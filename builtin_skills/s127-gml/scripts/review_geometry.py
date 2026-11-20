#!/usr/bin/env python3
"""Use source-referenced cuts or exact shared edges to prepare review surfaces."""
from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

from geometry_workflow import geometry_for, read_dataset, read_geometries
from gml_to_featureset import GML, dataset_members
from s127_model import GEOGRAPHIC_FEATURES, geometry_allowed


def planar_area(ring):
    y, x = ring[0]
    return abs(sum((a[1]-x)*(b[0]-y) - (a[0]-y)*(b[1]-x)
                   for a, b in zip(ring, ring[1:]))) / 2


def check_area(parts, whole):
    expected = planar_area(whole)
    if expected <= 0 or any(planar_area(ring) <= 0 for ring in parts) or not math.isclose(
        sum(map(planar_area, parts)), expected, rel_tol=1e-8, abs_tol=1e-14
    ):
        raise ValueError("候选边界闭合或面积核对未通过")


def exterior(surface):
    if not surface or surface.get("type") != "surface" or surface.get("interiors"):
        raise ValueError("复核工具只处理不含内环的单面")
    points = list(map(tuple, surface["exterior"]))
    if (len(points) < 4 or points[0] != points[-1] or len(set(points[:-1])) != len(points)-1
            or max(p[1] for p in points) - min(p[1] for p in points) >= 180):
        raise ValueError("供体外环未闭合、含重复顶点或跨越日期变更线")
    return points


def stitch(a, b):
    """Join two closed exteriors along exactly one whole common edge."""
    counts = Counter(tuple(sorted((x, y))) for ring in (a, b) for x, y in zip(ring, ring[1:]))
    if max(counts.values()) != 2 or sum(n == 2 for n in counts.values()) != 1:
        raise ValueError("供体须存在一条完全重合的共边；不吸附或延长边界")
    graph = defaultdict(list)
    for (x, y), count in counts.items():
        if count == 1:
            graph[x].append(y)
            graph[y].append(x)
    if any(len(neighbours) != 2 for neighbours in graph.values()):
        raise ValueError("拼接结果不是一个独立外环")
    start = min(graph)
    ring = [start, graph[start][0]]
    while ring[-1] != start and len(ring) <= len(graph):
        ring.append(next(point for point in graph[ring[-1]] if point != ring[-2]))
    if ring[-1] != start or len(ring) != len(graph) + 1:
        raise ValueError("拼接结果包含不连通边界")
    check_area((a, b), ring)
    return ring


def distance(a, b):
    # Approximate local offset for review, not a navigation measurement.
    return 111100 * math.hypot(a[0]-b[0], (a[1]-b[1]) * math.cos(math.radians((a[0]+b[0])/2)))


def split(ring, section):
    """Cut by the straight line through two sourced [latitude, longitude] points."""
    if len(section) != 2 or any(len(p) != 2 or not -90 <= p[0] <= 90 or not -180 <= p[1] <= 180 for p in section):
        raise ValueError("断面需要两个有效的 [纬度, 经度] 坐标")
    a, b = section
    dy, dx = b[0]-a[0], b[1]-a[1]
    if not (dx or dy):
        raise ValueError("断面的两个坐标不可相同")

    def side(point):
        return (dx * (point[0]-a[0]) - dy * (point[1]-a[1])) / math.hypot(dx, dy)

    left, right, hits = [], [], []
    for p, q in zip(ring, ring[1:]):
        sp, sq = side(p), side(q)
        if abs(sp) <= 1e-12:
            raise ValueError("断面通过供体顶点，需人工确认切分")
        (left if sp > 0 else right).append(p)
        if (sp > 0) != (sq > 0):
            f = sp / (sp - sq)
            hit = tuple(p[i] + f*(q[i]-p[i]) for i in (0, 1))
            left.append(hit)
            right.append(hit)
            hits.append(hit)
    if len(hits) != 2 or min(len(left), len(right)) < 3:
        raise ValueError("断面须与外环有且只有两个交点并形成两个面")
    for part in (left, right):
        part.append(part[0])
    check_area((left, right), ring)
    match = min((hits, hits[::-1]), key=lambda values: sum(distance(p, q) for p, q in zip(section, values)))
    return left, right, tuple(distance(p, q) for p, q in zip(section, match))


def prepare(doc, donor, operations):
    root, _, is_draft = read_dataset(donor)
    if is_draft:
        raise ValueError("供体不能是未确认的流程草稿")
    geometries = read_geometries(root)
    members = {m.get(GML+"id"): m for m in dataset_members(root)}
    targets = {f.get("ref") or f.get("id"): f for f in doc["features"] if f["type"] in GEOGRAPHIC_FEATURES}
    pool = doc.setdefault("geometries", {})
    claimed = set()

    def shape(gid):
        return exterior(geometry_for(members[gid], geometries))

    def assign(target, ring, reference):
        f = targets[target]
        if target in claimed or not geometry_allowed(f["type"], "surface"):
            raise ValueError(f"{target} 重复分配或不接受面")
        if f.get("geometry") and f.get("geometryStatus") != "review":
            raise ValueError(f"{target} 已有确认的边界，不用复核面覆盖")
        claimed.add(target)
        key = f"review-{target}"
        while key in pool:
            key += "-new"
        pool[key] = {"type": "surface", "exterior": ring}
        f.update(geometry=key, geometryStatus="review", geometryEvidence={
            "kind": "chart-trace", "reference": reference,
        })

    for op in operations:
        src = op["reference"].strip()
        if not src:
            raise ValueError("操作必须注明可核对的来源")
        if op["type"] == "stitch":
            first, second = op["donors"]
            candidate = stitch(shape(first), shape(second))
            assign(op["target"], candidate, f"{src}；{donor.name} #{first} + #{second} 共边拼接，待复核")
        elif op["type"] == "split":
            gid = op["donor"]
            left, right, offsets = split(shape(gid), op["section"])
            citation = (f"{src}；{donor.name} #{gid} 按断面试切；"
                        f"断面两端与供体交点分别约差 {offsets[0]:.1f}、{offsets[1]:.1f} 米，待复核")
            assign(op["left"], left, citation)
            assign(op["right"], right, citation)
        else:
            raise ValueError(f"不支持的操作类型 {op['type']!r}")
    return len(claimed)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for argument in ("input", "donor", "plan"):
        parser.add_argument(argument, type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.output.resolve() in {path.resolve() for path in (args.input, args.donor, args.plan)}:
            raise ValueError("输出不能覆盖输入")
        doc = json.loads(args.input.read_text(encoding="utf-8-sig"))
        operations = json.loads(args.plan.read_text(encoding="utf-8-sig"))
        count = prepare(doc, args.donor, operations)
        args.output.write_text(json.dumps(doc, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
        print(f"生成 {count} 项待复核候选几何：{args.output}")
    except (OSError, KeyError, TypeError, ValueError) as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
