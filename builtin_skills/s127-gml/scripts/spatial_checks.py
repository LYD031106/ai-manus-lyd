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
