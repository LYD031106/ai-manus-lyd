"""Cross-check geometry against independent spatial evidence, never against fixture names."""
from __future__ import annotations

import math

from s127_model import geometry_kind

EARTH_RADIUS = 6_371_008.8
# Only rounding of the seven-decimal-degree GML representation, not a source uncertainty.
ROUNDING_METRES = 0.02


def number(value):
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)


def anchors(feature):
    items = feature.get("geometryAnchors", [])
    if not isinstance(items, list):
        raise ValueError("geometryAnchors 应为数组")
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("每个几何锚点须包含坐标与原文出处")
        pos, reference = item.get("position"), item.get("reference")
        if (not isinstance(pos, (list, tuple)) or len(pos) != 2
                or not all(number(x) for x in pos) or not -90 <= pos[0] <= 90
                or not -180 <= pos[1] <= 180):
            raise ValueError("几何锚点坐标须为有效的 [纬度, 经度]")
        if not isinstance(reference, str) or not reference.strip():
            raise ValueError("几何锚点缺少独立的来源出处")
        tolerance = item.get("toleranceMetres", 0)
        if not number(tolerance) or tolerance < 0:
            raise ValueError("锚点容差须为原文或海图注明的非负米制不确定度")
    return items


def _segment_distance(a, b):
    x, y = b[0] - a[0], b[1] - a[1]
    square = x * x + y * y
    t = max(0, min(1, -(a[0] * x + a[1] * y) / square)) if square else 0
    return math.hypot(a[0] + t * x, a[1] + t * y)


def _lines(points, close=False):
    pairs = list(zip(points, points[1:]))
    if close and points[0] != points[-1]:
        pairs.append((points[-1], points[0]))
    return pairs


def _inside(pairs):
    hits = 0
    for a, b in pairs:
        if (a[1] > 0) != (b[1] > 0) and a[0] - (b[0] - a[0]) * a[1] / (b[1] - a[1]) > 0:
            hits += 1
    return bool(hits % 2)


def distance_to_geometry(position, shape):
    """Use local ground distances for the chart area; allow interiors/holes."""
    lat, lon = position
    metres_per_degree = EARTH_RADIUS * math.pi / 180
    longitude_scale = metres_per_degree * math.cos(math.radians(lat))

    def project(points):
        # Keep each segment continuous across the date line and the opposite meridian.
        previous_lon = lon
        for point_lat, point_lon in points:
            previous_lon += (float(point_lon) - previous_lon + 180) % 360 - 180
            yield ((previous_lon - lon) * longitude_scale, (float(point_lat) - lat) * metres_per_degree)

    kind = geometry_kind(shape)
    if kind == "point":
        point = shape.get("coordinates") or shape["position"]
        return math.hypot(*next(project([point])))
    if kind == "curve":
        points = shape.get("coordinates") or shape["exterior"]
        return min(_segment_distance(a, b) for a, b in _lines(list(project(points))))
    rings = [shape.get("exterior") or shape.get("coordinates"), *shape.get("interiors", [])]
    rings = [_lines(list(project(ring)), close=True) for ring in rings]
    if _inside(rings[0]) and not any(_inside(hole) for hole in rings[1:]):
        return 0
    return min(_segment_distance(a, b) for ring in rings for a, b in ring)


def conflicts(feature, shape):
    issues = []
    for item in anchors(feature):
        distance = distance_to_geometry(item["position"], shape)
        tolerance = item.get("toleranceMetres", 0)
        if distance > tolerance + ROUNDING_METRES:
            issues.append(f"与原文锚点「{item['reference']}」相距约 {distance:.1f} 米，"
                          f"超出 {tolerance:g} 米容差，需复核空间位置")
    return issues


def _exterior(shape):
    return shape.get("exterior") or shape.get("coordinates")


def coverage_entries(doc):
    entries = doc.get("coverageAreas", [])
    if not isinstance(entries, list):
        raise ValueError("coverageAreas 应为数组")
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("reference"), str) or not entry["reference"].strip():
            raise ValueError("每个 DataCoverage 须记录独立的覆盖范围出处")
        key = entry.get("geometry")
        shape = doc.get("geometries", {}).get(key) if isinstance(key, str) else None
        if not shape or geometry_kind(shape) != "surface":
            raise ValueError("DataCoverage 须引用 geometries 中已给出的面")
        for scale in ("maximumDisplayScale", "minimumDisplayScale"):
            if scale in entry and (type(entry[scale]) is not int or entry[scale] < 1):
                raise ValueError(f"DataCoverage/{scale} 须为有出处的正整数")
    return entries


def coverage_issues(areas, members):
    """Check full shape containment in the union of official coverage polygons."""
    try:
        from shapely import LineString, Point, Polygon, union_all
        from shapely.errors import GEOSException
    except ImportError as exc:
        raise ValueError("缺少 Shapely，未能检查 DataCoverage 几何覆盖") from exc

    meridian = float(_exterior(areas[0])[0][1])

    def project(points):
        projected = [((float(lon) - meridian + 180) % 360 - 180, float(lat))
                     for lat, lon in points]
        longitude = [p[0] for p in projected]
        if max(longitude) - min(longitude) >= 180:
            raise ValueError("几何跨越核验投影的分界或覆盖跨度过大，须在生产系统核对")
        return projected

    def topology(shape):
        kind = geometry_kind(shape)
        if kind == "point":
            return Point(*project([shape.get("coordinates") or shape["position"]])[0])
        if kind == "curve":
            return LineString(project(_exterior(shape)))
        polygon = Polygon(project(_exterior(shape)), [project(r) for r in shape.get("interiors", [])])
        if polygon.is_empty or not polygon.is_valid or polygon.area == 0:
            raise ValueError("含无效的面、内环或自交边界")
        return polygon

    try:
        coverage = union_all([topology(shape) for shape in areas])
    except GEOSException as exc:
        raise ValueError("无法合并 DataCoverage 面") from exc

    issues = []
    for label, shape in members:
        try:
            if not coverage.covers(topology(shape)):
                issues.append(f"{label} 有部分几何超出 DataCoverage 或落入其空洞")
        except (ValueError, GEOSException) as exc:
            issues.append(f"{label}：{exc}")
    return issues
