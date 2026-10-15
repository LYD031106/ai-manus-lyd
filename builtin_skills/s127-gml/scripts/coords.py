#!/usr/bin/env python3
"""坐标提取与换算：法规原文里的度分秒 → 十进制 [纬度, 经度]。

用法：
    # 从文本文件/标准输入里把所有坐标对抓出来，按出现顺序组成一个环
    python coords.py --text 通告正文.txt --close
    # 从 CSV 抓（常见于「XY.csv」「area.csv」这类附件）
    python coords.py --csv XY.csv --lat-col 纬度 --lon-col 经度
    # 直接换算几个坐标
    python coords.py "37°27′04″N 122°08′49″E" "37°29′40″N 122°12′56″E"

输出：可直接粘进要素清单 geometries 的 JSON 坐标数组。
注意：S-127 GML 的 posList 轴序是 **纬度 经度**（EPSG:4326），
本脚本统一输出 [lat, lon]，请勿手工调换。
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys

# 三类写法，按优先级依次匹配（后者只在前者未覆盖的位置上生效）：
#   1) 带度分秒符号：37°27′04″N、37°27'04"N、37°27.5′N
#   2) 用空格或短横分隔：39 07 00.28N、38-57-53.59N（附件 CSV 常见）
#   3) 十进制带半球字母：37.4511°N、117.9031E
DMS_SYMBOL = re.compile(
    r"""(?P<deg>\d{1,3})\s*[°度º]\s*
        (?:(?P<min>\d{1,2}(?:\.\d+)?)\s*[′'’分]?\s*)?
        (?:(?P<sec>\d{1,2}(?:\.\d+)?)\s*[″"”秒〞]?\s*)?
        (?P<hemi>[NSEWnsew北南东西])""",
    re.X,
)
DMS_PLAIN = re.compile(
    r"""(?P<deg>\d{1,3})[-\s]+
        (?P<min>\d{1,2}(?:\.\d+)?)
        (?:[-\s]+(?P<sec>\d{1,2}(?:\.\d+)?))?
        \s*(?P<hemi>[NSEWnsew北南东西])""",
    re.X,
)
DECIMAL = re.compile(r"(?P<val>\d{1,3}\.\d{3,})\s*[°度]?\s*(?P<hemi>[NSEWnsew北南东西])")

HEMI_SIGN = {"N": 1, "S": -1, "E": 1, "W": -1, "北": 1, "南": -1, "东": 1, "西": -1}
IS_LAT = set("NSns北南")


def to_decimal(deg: float, minute: float, second: float, hemi: str) -> float:
    value = deg + minute / 60.0 + second / 3600.0
    sign = HEMI_SIGN.get(hemi.upper(), HEMI_SIGN.get(hemi, 1))
    return round(sign * value, 7)


def parse_one(token: str) -> list[float]:
    """解析一个『纬度 经度』字符串 → [lat, lon]。"""
    found = extract(token)
    if len(found) != 1:
        raise ValueError(f"无法从 {token!r} 解析出一组经纬度（解析到 {len(found)} 组）")
    return found[0]


def extract(text: str) -> list[list[float]]:
    """从任意文本中按出现顺序抓取坐标对，返回 [[lat, lon], …]。"""
    items: list[tuple[int, bool, float]] = []  # (位置, 是否纬度, 十进制值)
    covered: list[tuple[int, int]] = []

    def overlaps(match) -> bool:
        return any(s < match.end() and match.start() < e for s, e in covered)

    for pattern in (DMS_SYMBOL, DMS_PLAIN):
        for m in pattern.finditer(text):
            if overlaps(m):
                continue
            hemi = m.group("hemi")
            items.append(
                (
                    m.start(),
                    hemi in IS_LAT,
                    to_decimal(
                        float(m.group("deg")),
                        float(m.group("min") or 0),
                        float(m.group("sec") or 0),
                        hemi,
                    ),
                )
            )
            covered.append((m.start(), m.end()))
    for m in DECIMAL.finditer(text):
        if overlaps(m):
            continue
        hemi = m.group("hemi")
        sign = HEMI_SIGN.get(hemi.upper(), HEMI_SIGN.get(hemi, 1))
        items.append((m.start(), hemi in IS_LAT, round(sign * float(m.group("val")), 7)))
        covered.append((m.start(), m.end()))

    items.sort(key=lambda x: x[0])
    pairs: list[list[float]] = []
    pending_lat = pending_lon = None
    for _, is_lat, value in items:
        if is_lat:
            pending_lat = value
        else:
            pending_lon = value
        if pending_lat is not None and pending_lon is not None:
            pairs.append([pending_lat, pending_lon])
            pending_lat = pending_lon = None
    return pairs


def from_csv(
    path: str,
    lat_col: str | None,
    lon_col: str | None,
    group_col: str | None = None,
) -> dict[str, list[list[float]]]:
    """读 CSV → {分组名: [[lat, lon], …]}。无分组时键为 ''。"""
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh))
    if not rows:
        return {}
    header = rows[0]
    idx_lat = idx_lon = None
    if lat_col and lat_col in header:
        idx_lat = header.index(lat_col)
    if lon_col and lon_col in header:
        idx_lon = header.index(lon_col)
    if idx_lat is None or idx_lon is None:
        for i, cell in enumerate(header):
            low = cell.strip().lower()
            if idx_lat is None and (low in ("lat", "latitude", "y") or "纬" in cell):
                idx_lat = i
            if idx_lon is None and (low in ("lon", "lng", "longitude", "x") or "经" in cell):
                idx_lon = i
    body = rows[1:] if (idx_lat is not None and idx_lon is not None) else rows
    if idx_lat is None or idx_lon is None:
        idx_lat, idx_lon = 0, 1
        print("[提醒] 未识别到表头，默认第 1 列为纬度、第 2 列为经度", file=sys.stderr)

    idx_group = None
    if group_col:
        if group_col in header:
            idx_group = header.index(group_col)
        elif group_col.isdigit():
            idx_group = int(group_col)
        else:
            print(f"[提醒] 表头里没有分组列 {group_col!r}，忽略分组", file=sys.stderr)

    out: dict[str, list[list[float]]] = {}
    for row in body:
        if len(row) <= max(idx_lat, idx_lon):
            continue
        raw_lat, raw_lon = row[idx_lat].strip(), row[idx_lon].strip()
        if not raw_lat or not raw_lon:
            continue
        try:
            lat = float(raw_lat)
            lon = float(raw_lon)
        except ValueError:
            got = extract(f"{raw_lat} {raw_lon}")
            if not got:
                continue
            lat, lon = got[0]
        key = row[idx_group].strip() if idx_group is not None and len(row) > idx_group else ""
        out.setdefault(key, []).append([round(lat, 7), round(lon, 7)])
    return out


def sanity(points: list[list[float]]) -> None:
    for lat, lon in points:
        if not -90 <= lat <= 90:
            print(f"[警告] 纬度 {lat} 越界，可能经纬度写反", file=sys.stderr)
        elif abs(lat) > 60 and abs(lon) < 60:
            print(f"[警告] [{lat}, {lon}] 疑似经纬度写反", file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser(description="度分秒 → 十进制 [纬度, 经度]")
    ap.add_argument("tokens", nargs="*", help="直接给出的坐标串，如 \"37°27′04″N 122°08′49″E\"")
    ap.add_argument("--text", help="从文本文件抓取（- 表示标准输入）")
    ap.add_argument("--csv", help="从 CSV 抓取")
    ap.add_argument("--lat-col", help="CSV 纬度列名")
    ap.add_argument("--lon-col", help="CSV 经度列名")
    ap.add_argument("--group-col", help="CSV 分组列名（一份附件含多个区域/航路时按此列拆分）")
    ap.add_argument("--close", action="store_true", help="首尾闭合（面要素外环）")
    ap.add_argument("--name", default="g-1", help="输出 geometries 键名（无分组时使用）")
    ap.add_argument("--type", default="surface", choices=["surface", "curve", "point"])
    args = ap.parse_args()

    groups: dict[str, list[list[float]]] = {}
    if args.text:
        raw = sys.stdin.read() if args.text == "-" else open(args.text, encoding="utf-8").read()
        groups[""] = extract(raw)
    elif args.csv:
        groups = from_csv(args.csv, args.lat_col, args.lon_col, args.group_col)
    elif args.tokens:
        groups[""] = extract(" ".join(args.tokens))
    else:
        ap.error("请给出 --text / --csv 或直接的坐标串")

    groups = {k: v for k, v in groups.items() if v}
    if not groups:
        print("[错误] 没有解析到任何坐标", file=sys.stderr)
        return 1

    out: dict[str, dict] = {}
    total = 0
    for i, (key, points) in enumerate(groups.items(), 1):
        sanity(points)
        if args.close and args.type == "surface" and points[0] != points[-1]:
            points = points + [points[0]]
        if args.type == "point":
            spec: dict = {"type": "point", "coordinates": points[0]}
        elif args.type == "curve":
            spec = {"type": "curve", "coordinates": points}
        else:
            spec = {"type": "surface", "exterior": points}
        name = args.name if not key else f"g-{i}"
        out[name] = spec
        if key:
            spec["$name"] = key
        total += len(points)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"[OK] 解析到 {len(out)} 组几何、共 {total} 个坐标点", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
