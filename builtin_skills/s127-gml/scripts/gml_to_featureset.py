#!/usr/bin/env python3
"""把已有的 S-127 GML 反解成要素清单 JSON。

两个用途：
  1. **回归测试**：GML → JSON → GML，与原件比对，验证构建器的保真度
  2. **存档同步**（指南 3.3.2(5)）：系统里改过的数据导出 GML 后反解成 JSON，
     修改后重新构建，保证 JSON / GML / 系统三者一致

用法：
    python gml_to_featureset.py 127CN00XXX001.gml -o featureset.json

局限：GML 里没有的信息反解不出来 —— `clause`（条款出处）、
关联的许可/包含类型（除非用了 --assoc-objects）都会丢，需要人工补回。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from s127_model import (  # noqa: E402
    ASSOC_ELEMENTS,
    DATE_WRAPPED,
    ENUMS,
    GEOGRAPHIC_FEATURES,
    REUSABLE_BY_DEFAULT,
    is_single_valued,
)

GML = "{http://www.opengis.net/gml/3.2}"
S100 = "{http://www.iho.int/s100gml/1.0}"
XLINK = "{http://www.w3.org/1999/xlink}"


def local(tag: str) -> str:
    return tag.split("}")[-1]


def qname(elem) -> str:
    """带 s100: 前缀的名字（与要素清单里的写法一致）。"""
    if elem.tag.startswith(S100):
        return "s100:" + local(elem.tag)
    return local(elem.tag)


def reference(elem):
    href = elem.get(XLINK + "href", "") if elem is not None else ""
    if not href.startswith("#") or len(href) < 2:
        raise ValueError(f"缺少有效的本地几何引用：{href!r}")
    return href[1:]


def parse_geometries(root):
    """还原 S-100 的二维点、直线分段和单面拓扑几何。"""
    curves, orient, comp, surf, points = {}, {}, {}, {}, {}

    def positions(elem, gid):
        values = [float(value) for value in (elem.text or "").split()]
        if len(values) < 2 or len(values) % 2 or elem.get("srsDimension", "2") != "2":
            raise ValueError(f"{gid}: 坐标必须是二维经纬度对")
        return [values[i:i + 2] for i in range(0, len(values), 2)]

    def join(parts, gid):
        out = []
        for pts in parts:
            if not out:
                out.extend(pts)
            elif all(abs(a - b) < 1e-9 for a, b in zip(out[-1], pts[0])):
                out.extend(pts[1:])
            else:
                raise ValueError(f"{gid}: 曲线分段端点不连续")
        if len(out) < 2:
            raise ValueError(f"{gid}: 曲线至少需要两个点")
        return out

    for e in root:
        gid = e.get(GML + "id")
        tag = local(e.tag)
        if tag == "Curve":
            segments = e.find(f"{GML}segments")
            parts = []
            for segment in segments if segments is not None else ():
                pos = segment.find(f"{GML}posList")
                if segment.tag != f"{GML}LineStringSegment" or pos is None:
                    raise ValueError(f"{gid}: 仅支持带 posList 的 LineStringSegment")
                parts.append(positions(pos, gid))
            curves[gid] = join(parts, gid)
        elif tag == "OrientableCurve":
            sign = e.get("orientation", "+")
            if sign not in ("+", "-"):
                raise ValueError(f"{gid}: 无效的曲线方向 {sign!r}")
            orient[gid] = (reference(e.find(f"{GML}baseCurve")), sign)
        elif tag == "CompositeCurve":
            comp[gid] = [reference(member) for member in e.findall(f"{GML}curveMember")]
        elif tag == "Surface":
            patches = e.findall(f"{GML}patches/*")
            if len(patches) != 1 or patches[0].tag != f"{GML}PolygonPatch":
                raise ValueError(f"{gid}: 仅支持单个 PolygonPatch")
            ext = patches[0].find(f"{GML}exterior/{GML}Ring")
            interiors = patches[0].findall(f"{GML}interior")
            rings = [ext] + [item.find(f"{GML}Ring") for item in interiors]
            surf[gid] = [
                [reference(member) for member in ring.findall(f"{GML}curveMember")]
                if ring is not None else []
                for ring in rings
            ]
        elif tag == "Point":
            pos = e.find(f"{GML}pos")
            coords = positions(pos, gid) if pos is not None else []
            if len(coords) != 1:
                raise ValueError(f"{gid}: 点几何需要一个坐标对")
            points[gid] = coords[0]

    def curve_of(gid):
        if gid in curves:
            return curves[gid]
        if gid in orient:
            cid, sign = orient[gid]
            return curves[cid] if sign == "+" else curves[cid][::-1]
        return composites[gid]

    composites = {}
    for gid, members in comp.items():
        composites[gid] = join((curve_of(member) for member in members), gid)

    geoms = {gid: {"type": "curve", "coordinates": curve_of(gid)}
             for gid in (*curves, *orient, *composites)}
    for sid, rings in surf.items():
        coordinates = [join((curve_of(member) for member in ring), sid) for ring in rings]
        for ring in coordinates:
            if any(abs(a - b) >= 1e-9 for a, b in zip(ring[0], ring[-1])):
                raise ValueError(f"{sid}: 面环未闭合")
            ring[-1] = ring[0]
        geoms[sid] = {"type": "surface", "exterior": coordinates[0]}
        if len(coordinates) > 1:
            geoms[sid]["interiors"] = coordinates[1:]
    geoms.update({gid: {"type": "point", "coordinates": pos} for gid, pos in points.items()})
    return geoms


XSI_NIL = "{http://www.w3.org/2001/XMLSchema-instance}nil"


def parse_value(elem, type_name=None):
    """一个 XML 元素 → JSON 值（标量 / 复合 dict）。"""
    if elem.get(XSI_NIL) == "true":
        return {"$nil": True}
    kids = [k for k in elem]
    if not kids:
        text = (elem.text or "").replace("\r\n", "\r").replace("\n", "\r")
        name = qname(elem)
        if name in ENUMS:
            return text.strip()
        if is_single_valued(name) and "\r" in text:
            return text.split("\r", 1)
        if text.strip() in ("true", "false"):
            return text.strip() == "true"
        try:
            num = float(text)
            return int(num) if num == int(num) and "." not in text else num
        except (ValueError, TypeError):
            return text
    # 日期包装
    if qname(elem) in DATE_WRAPPED and len(kids) == 1 and local(kids[0].tag) == "date":
        return (kids[0].text or "").strip()
    out: dict = {}
    for k in kids:
        name = qname(k)
        val = parse_value(k)
        if name in out:
            if not isinstance(out[name], list):
                out[name] = [out[name]]
            out[name].append(val)
        else:
            out[name] = val
    return out


def parse_feature(elem, geom_ids, ref_of, assoc_objects):
    ftype = local(elem.tag)
    feat: dict = {"ref": ref_of[elem.get(GML + "id")], "type": ftype}
    attrs: dict = {}
    names: list = []
    assocs: list = []
    for child in elem:
        name = qname(child)
        if name == "s100:featureObjectIdentifier":
            continue                              # 由构建器重新生成
        if name == "featureName":
            item = parse_value(child)
            name_elem = child.find(f"{{*}}name")
            if isinstance(item, dict) and name_elem is not None:
                item["name"] = name_elem.text or ""
                names.append(item)
        elif name == "geometry":
            prop = list(child)[0]
            feat["geometry"] = geom_ids[prop.get(XLINK + "href")[1:]]
            if len(prop):
                feat["$geometryNote"] = (
                    "原件在 geometry 上带了 s100:maskReference（部分边界抑制显示），"
                    "本工具不支持，重建后会丢失")
        elif name in ASSOC_ELEMENTS:
            target = child.get(XLINK + "href")[1:]
            category = assoc_objects.get(target)
            if category:
                target, key, value = category
                assocs.append({"role": name, "target": ref_of.get(target, target), key: value})
            else:
                assocs.append({"role": name, "target": ref_of.get(target, target)})
        elif name in ("fixedDateRange", "sourceIndication", "textContent"):
            val = parse_value(child)
            if name == "textContent":
                feat.setdefault("textContent", []).append(val)
            else:
                feat[name] = val
        else:
            val = parse_value(child)
            if name in attrs:
                if not isinstance(attrs[name], list):
                    attrs[name] = [attrs[name]]
                attrs[name].append(val)
            else:
                attrs[name] = val
    # featureName 归一成 {eng, zho} 速记（能归就归，归不了保留列表）
    simple = {}
    ok = True
    for n in names:
        lang, nm = n.get("language"), n.get("name")
        if lang in ("eng", "zho") and lang not in simple:
            simple[lang] = nm
            if lang == "eng" and n.get("displayName"):
                ok = False
            if lang == "zho" and not n.get("displayName") and ftype != "Applicability":
                ok = False
            if lang == "zho" and n.get("displayName") and ftype == "Applicability":
                ok = False
        else:
            ok = False
    feat["featureName"] = simple if (ok and simple) else names
    if attrs:
        feat["attributes"] = attrs
    if assocs:
        feat["associations"] = assocs
    return feat


def dataset_members(root):
    members = root.find("{*}members")
    if members is None:
        raise ValueError("未找到 S-127 Dataset/members")
    return members


def convert(path: Path, *, root=None, geoms_by_id=None) -> dict:
    if root is None:
        root = ET.parse(path).getroot()
    if geoms_by_id is None:
        geoms_by_id = parse_geometries(root)

    ident = root.find(f"{S100}DatasetIdentificationInformation")
    def ident_text(tag, default=None):
        e = ident.find(f"{S100}{tag}") if ident is not None else None
        return (e.text or "").strip() if e is not None else default

    members = []
    assoc_objects = {}
    for elem in dataset_members(root):
        ftype = local(elem.tag)
        if ftype == "PermissionType":
            assoc_objects[elem.get(GML + "id")] = (
                reference(elem.find("{*}permission")), "permission",
                elem.findtext("{*}categoryOfRelationship"),
            )
        elif ftype == "InclusionType":
            assoc_objects[elem.get(GML + "id")] = (
                reference(elem.find("{*}isApplicableTo")), "inclusion",
                elem.findtext("{*}membership"),
            )
        else:
            members.append(elem)

    # gml:id → 可读 ref
    ref_of, seen = {}, {}
    for e in members:
        ftype = local(e.tag)
        seen[ftype] = seen.get(ftype, 0) + 1
        ref_of[e.get(GML + "id")] = f"{ftype[:1].lower()}{ftype[1:]}-{seen[ftype]}"

    # 几何 id → 可读键名
    geom_ids, geoms = {}, {}
    n = 0
    for e in members:
        g = None
        for child in e:
            if local(child.tag) == "geometry":
                g = list(child)[0].get(XLINK + "href")[1:]
        if g and g not in geom_ids:
            n += 1
            key = f"g-{n}"
            geom_ids[g] = key
            geoms[key] = geoms_by_id[g]

    features = [parse_feature(e, geom_ids, ref_of, assoc_objects) for e in members]

    # 先记录「原件里本来就没有来源」的要素 —— 这才是可复用要素的判据。
    # 必须在上提 common 之前记录，否则上提会把所有要素都变成"没有来源"。
    had_source = {id(f): "sourceIndication" in f for f in features}

    # 抽公共属性：若多个要素的 sourceIndication / fixedDateRange 完全相同则上提
    common = {}
    for key in ("fixedDateRange", "sourceIndication"):
        owners = [f for f in features if key in f]
        vals = {json.dumps(f[key], sort_keys=True, ensure_ascii=False) for f in owners}
        if len(vals) == 1 and len(owners) > 1:
            common[key] = owners[0][key]
            for f in owners:
                f.pop(key)

    # 标注可复用要素（只在与默认值不同时写出 reusable，保持 JSON 精简）
    for f in features:
        default_reusable = f["type"] in REUSABLE_BY_DEFAULT
        actual_reusable = not had_source[id(f)]
        if actual_reusable != default_reusable:
            f["reusable"] = actual_reusable

    identifier = ident_text("datasetFileIdentifier")
    title = ident_text("datasetTitle")
    name = Path(identifier).stem if identifier else title or path.stem
    doc = {
        "dataset": {
            "name": name,
            "referenceDate": ident_text("datasetReferenceDate"),
            "updateNumber": int(ident_text("updateNumber", "0") or 0),
            "agency": "CN",
            "featureIdStart": 1,
        },
    }
    if title and title != name:
        doc["dataset"]["title"] = title
    # 还原 featureIdStart
    fins = []
    for e in members:
        foi = e.find(f"{S100}featureObjectIdentifier/{S100}featureIdentificationNumber")
        if foi is not None and (foi.text or "").strip().isdigit():
            fins.append(int(foi.text))
    if fins:
        doc["dataset"]["featureIdStart"] = min(fins)
    if common:
        doc["common"] = common
    doc["geometries"] = geoms
    doc["features"] = features
    return doc


def main() -> int:
    ap = argparse.ArgumentParser(description="S-127 GML → 要素清单 JSON")
    ap.add_argument("gml")
    ap.add_argument("-o", "--output")
    args = ap.parse_args()
    doc = convert(Path(args.gml))
    text = json.dumps(doc, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"[OK] 已生成 {args.output}（{len(doc['features'])} 个要素）")
        print("[提醒] GML 里没有的信息（条款出处 clause、关联的许可/包含类型）需人工补回")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
