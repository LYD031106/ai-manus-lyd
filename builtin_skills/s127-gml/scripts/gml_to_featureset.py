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


def parse_geometries(root):
    """还原几何图元链 → {gml:id: 坐标}，以及 property 引用 → 几何定义。"""
    curves, orient, comp, surf, points = {}, {}, {}, {}, {}
    for e in root:
        gid = e.get(GML + "id")
        tag = local(e.tag)
        if tag == "Curve":
            pos = e.find(f"{GML}segments/{GML}LineStringSegment/{GML}posList")
            nums = [float(x) for x in (pos.text or "").split()]
            curves[gid] = [[nums[i], nums[i + 1]] for i in range(0, len(nums), 2)]
        elif tag == "OrientableCurve":
            orient[gid] = (e.find(f"{GML}baseCurve").get(XLINK + "href")[1:],
                           e.get("orientation", "+"))
        elif tag == "CompositeCurve":
            comp[gid] = [m.get(XLINK + "href")[1:] for m in e.findall(f"{GML}curveMember")]
        elif tag == "Surface":
            patch = e.find(f"{GML}patches/{GML}PolygonPatch")
            ext = patch.find(f"{GML}exterior/{GML}Ring/{GML}curveMember")
            ints = patch.findall(f"{GML}interior/{GML}Ring/{GML}curveMember")
            surf[gid] = (ext.get(XLINK + "href")[1:],
                         [i.get(XLINK + "href")[1:] for i in ints])
        elif tag == "Point":
            pos = e.find(f"{GML}pos")
            nums = [float(x) for x in (pos.text or "").split()]
            points[gid] = nums[:2]

    def ring_of(cc_id):
        """CompositeCurve → 坐标序列（按 orientation 决定是否反向）。

        多段拓扑曲线在 junction 处去除重复端点；使用 epsilon 容差
        以防不同 Curve 段对同一 junction 坐标的文本精度不一致。
        """
        out = []
        for oc_id in comp.get(cc_id, []):
            c_id, sign = orient.get(oc_id, (None, "+"))
            pts = list(curves.get(c_id, []))
            if sign == "-":
                pts.reverse()
            if not out:
                out.extend(pts)
            elif out[-1] == pts[0] or (
                abs(out[-1][0] - pts[0][0]) < 1e-9
                and abs(out[-1][1] - pts[0][1]) < 1e-9
            ):
                out.extend(pts[1:])
            else:
                out.extend(pts)
        return out

    geoms = {}
    for sid, (ext, ints) in surf.items():
        spec = {"type": "surface", "exterior": ring_of(ext)}
        if ints:
            spec["interiors"] = [ring_of(i) for i in ints]
        geoms[sid] = spec
    for ccid in comp:
        if ccid not in {ext for ext, _ in surf.values()} and \
           not any(ccid in ints for _, ints in surf.values()):
            geoms[ccid] = {"type": "curve", "coordinates": ring_of(ccid)}
    for pid, pos in points.items():
        geoms[pid] = {"type": "point", "coordinates": pos}
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


def parse_feature(elem, geom_ids, ref_of):
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
            item = {"language": None, "name": None}
            d = parse_value(child)
            item = {k: v for k, v in d.items()}
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


def convert(path: Path) -> dict:
    root = ET.parse(path).getroot()
    geoms_by_id = parse_geometries(root)

    ident = root.find(f"{S100}DatasetIdentificationInformation")
    def ident_text(tag, default=None):
        e = ident.find(f"{S100}{tag}") if ident is not None else None
        return (e.text or "").strip() if e is not None else default

    members = root.find("{http://www.iho.int/S127/gml/cs0/1.0}members") or root.find("members")
    if members is None:
        for e in root:
            if local(e.tag) == "members":
                members = e
                break

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

    features = [parse_feature(e, geom_ids, ref_of) for e in members]

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

    name = ident_text("datasetTitle") or path.stem
    doc = {
        "dataset": {
            "name": name,
            "referenceDate": ident_text("datasetReferenceDate"),
            "updateNumber": int(ident_text("updateNumber", "0") or 0),
            "agency": "CN",
            "featureIdStart": 1,
        },
    }
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
