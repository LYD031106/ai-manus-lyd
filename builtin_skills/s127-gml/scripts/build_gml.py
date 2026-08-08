#!/usr/bin/env python3
"""要素清单 JSON → S-127 GML 数据集。

用法：
    python build_gml.py featureset.json -o 127CN00XXX001.gml
    python build_gml.py featureset.json -o out.gml --assoc-csv 关联补录.csv
    python build_gml.py featureset.json -o out.gml --assoc-objects     # 关联类型写进 GML

设计原则：
  * 大模型只产**要素清单 JSON**（属性 + 关联 + 坐标）；命名空间、几何图元链、
    gml:id 串联、boundedBy、xlink arcrole、枚举 code、子元素顺序全部由本脚本
    确定性生成。
  * 要素类、属性、枚举 code、多重性、几何约束、关联目标全部来自**官方要素目录**
    S127FC.xml（见 scripts/s127_catalogue.py）。子元素顺序取要素目录的属性绑定顺序
    —— 实测与 13 个已入库生产数据集在 16/16 个要素类上一致。

仅依赖 Python 标准库。
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from s127_model import (  # noqa: E402
    ARCROLE_BASE,
    ASSOC_ELEMENTS,
    COMPLEX,
    DATASET_IDENTIFICATION,
    DATE_WRAPPED,
    ENUMS,
    GEOGRAPHIC_FEATURES,
    INVERSE_ROLES,
    NS_ATTRS,
    REUSABLE_BY_DEFAULT,
    ROLE_TITLE,
    SRS,
    FEATURES,
    child_order,
    composite_order,
    geometry_kind,
    merges_bilingual,
    permitted_labels,
    resolve_enum,
)

IND = "  "
warnings: list[str] = []


def warn(msg: str) -> None:
    if msg not in warnings:
        warnings.append(msg)


# ---------------------------------------------------------------------------
# XML 基础
# ---------------------------------------------------------------------------

def esc(text: str) -> str:
    """XML 文本转义。CR 必须写成 &#13;，否则解析时会被规范化掉——
    真实数据集正是用 CR 分隔「先英文后中文」的双语文本。"""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\r\n", "\r")
        .replace("\n", "\r")
        .replace("\r", "&#13;\n")
    )


def fmt_num(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.10f}".rstrip("0").rstrip(".") or "0"
    return str(value)


# ---------------------------------------------------------------------------
# 双语展开
# ---------------------------------------------------------------------------

def bilingual(value) -> str:
    """['English', '中文'] 或 {'eng':…, 'zho':…} → 'English\\r中文'（先英后中）。"""
    if isinstance(value, dict):
        parts = [value[k] for k in ("eng", "zho") if value.get(k) not in (None, "")]
    elif isinstance(value, (list, tuple)):
        parts = [p for p in value if p not in (None, "")]
    else:
        return str(value)
    return "\r".join(str(p) for p in parts)


def expand_feature_name(feature_type: str, value) -> list[dict]:
    """featureName 展开：先英文（不选 displayName），后中文（displayName=true）。
    APPLIC 中英文 displayName 均不选；复合属性内的 featureName 一律留空（指南 4.3/4.4）。"""
    if isinstance(value, list):
        return value
    if not isinstance(value, dict):
        raise ValueError(f"featureName 必须是 dict 或 list，得到 {type(value).__name__}")
    if "language" in value or ("name" in value and set(value) <= {"language", "name", "displayName"}):
        return [value]
    show_zho = feature_type not in ("Applicability", "_composite")
    out = []
    if value.get("eng"):
        out.append({"language": "eng", "name": value["eng"]})
    if value.get("zho"):
        item = {"language": "zho", "name": value["zho"]}
        if value.get("displayName", show_zho):
            item["displayName"] = True
        out.append(item)
    if not out:
        raise ValueError(f"featureName 至少需要 eng 或 zho：{value!r}")
    return out


def expand_information(items) -> list[dict]:
    """{'headline': {...}, 'text': {...}} → 两个 <information>（eng 在前）。"""
    if isinstance(items, dict):
        items = [items]
    out: list[dict] = []
    for item in items:
        if "language" in item:
            out.append(item)
            continue
        for lang in ("eng", "zho"):
            text = item.get(lang) if isinstance(item.get(lang), str) else None
            if text is None and isinstance(item.get("text"), dict):
                text = item["text"].get(lang)
            headline = item.get("headline")
            if isinstance(headline, dict):
                headline = headline.get(lang)
            elif isinstance(headline, (list, tuple)):
                headline = headline[0 if lang == "eng" else 1] if len(headline) > 1 else headline[0]
            if text is None and headline is None:
                continue
            block: dict = {}
            if headline:
                block["headline"] = headline
            block["language"] = lang
            if text is not None:
                block["text"] = text
            out.append(block)
    return out


def expand_text_content(items) -> list[dict]:
    if isinstance(items, dict):
        items = [items]
    out = []
    for tc in items:
        tc = dict(tc)
        if "information" in tc:
            tc["information"] = expand_information(tc["information"])
        out.append(tc)
    return out


# ---------------------------------------------------------------------------
# 属性渲染
# ---------------------------------------------------------------------------

def render_value(name: str, value, depth: int, owner_type: str | None = None) -> list[str]:
    pad = IND * depth
    lines: list[str] = []
    if value is None or value == "" or value == [] or value == {}:
        return lines

    # 显式空值：可写成 <x xsi:nil="true"/>，
    # 表示「该属性适用但取值未知」，与「不填」语义不同（语料里有 2 处）。
    # 要素清单里用 {"$nil": true} 表达。
    if isinstance(value, dict) and value.get("$nil") is True:
        lines.append(f'{pad}<{name} xsi:nil="true"/>')
        return lines

    # 双语合并（指南 4.1）：{"eng":…,"zho":…} 一律合并成一格；
    # ["英","中"] 只在该属性 upper=1 时合并 —— 可重复属性（如
    # communicationChannel）的列表必须输出多个元素，否则会毁掉多重性。
    if merges_bilingual(name, value):
        value = bilingual(value)

    if isinstance(value, (list, tuple)):
        for item in value:
            lines += render_value(name, item, depth, owner_type)
        return lines

    # 日期型：包一层 <s100:date>
    if name in DATE_WRAPPED and not isinstance(value, dict):
        lines.append(f"{pad}<{name}>")
        lines.append(f"{pad}{IND}<s100:date>{esc(value)}</s100:date>")
        lines.append(f"{pad}</{name}>")
        return lines

    # 复合属性：按要素目录的 subAttributeBinding 顺序输出
    if isinstance(value, dict):
        order = composite_order(name)
        if not order:
            order = list(value)
            warn(f"复合属性 {name} 不在要素目录的复合属性表里，按输入顺序输出，请核对属性名")
        lines.append(f"{pad}<{name}>")
        for key in order + [k for k in value if k not in order]:
            if key not in value or key.startswith("$"):
                continue
            sub = value[key]
            if key == "featureName":
                sub = expand_feature_name("_composite", sub)
            if key == "information":
                sub = expand_information(sub)
            lines += render_value(key, sub, depth + 1, name)
        lines.append(f"{pad}</{name}>")
        return lines

    if name in ENUMS:
        # 枚举值本身必须存在（错了直接抛错）；但"该要素类允许的枚举子集"只告警——
        # 已入库的生产数据里就有越出子集的用法，重建归档时不能因此拒绝出文件。
        code, label = resolve_enum(name, value)
        if owner_type in FEATURES:
            allow = permitted_labels(owner_type, name)
            if allow is not None and label not in allow:
                warn(f"{owner_type}.{name}={label!r} 不在要素目录允许的取值子集内"
                     f"（只允许 {sorted(allow)}）—— 已按原样输出，请核对是否该换要素类")
        lines.append(f'{pad}<{name} code="{code}">{esc(label)}</{name}>')
        return lines

    if isinstance(value, bool):
        lines.append(f"{pad}<{name}>{'true' if value else 'false'}</{name}>")
    elif isinstance(value, (int, float)):
        lines.append(f"{pad}<{name}>{fmt_num(value)}</{name}>")
    else:
        lines.append(f"{pad}<{name}>{esc(value)}</{name}>")
    return lines


# ---------------------------------------------------------------------------
# 几何
# ---------------------------------------------------------------------------

class GeometryPool:
    """把要素清单里的坐标编译成 S-100 几何图元链。

    面：Curve → OrientableCurve → CompositeCurve → Surface(PolygonPatch/Ring)
    线：Curve → OrientableCurve → CompositeCurve
    点：Point
    """

    def __init__(self):
        self.curves: list[tuple[str, list]] = []
        self.orientables: list[tuple[str, str]] = []
        self.composites: list[tuple[str, list[str]]] = []
        self.surfaces: list[tuple[str, str, list[str]]] = []
        self.points: list[tuple[str, list[float]]] = []
        self.refs: dict[str, tuple[str, str]] = {}
        self._n = {"c": 0, "oc": 0, "cc": 0, "s": 0, "pt": 0}
        self.coords: list[list[float]] = []

    def _next(self, kind: str) -> str:
        self._n[kind] += 1
        return f"{kind}{self._n[kind]}"

    def _curve(self, coords: list[list[float]]) -> str:
        cid = self._next("c")
        self.curves.append((cid, coords))
        self.coords.extend(coords)
        ocid = self._next("oc")
        self.orientables.append((ocid, cid))
        ccid = self._next("cc")
        self.composites.append((ccid, [ocid]))
        return ccid

    def add(self, name: str, spec: dict) -> tuple[str, str]:
        if name in self.refs:
            return self.refs[name]
        kind = geometry_kind(spec)
        if kind == "point":
            pid = self._next("pt")
            pos = spec.get("coordinates") or spec.get("position")
            self.points.append((pid, pos))
            self.coords.append(pos)
            ref = ("s100:pointProperty", pid)
        elif kind == "curve":
            coords = spec.get("coordinates") or spec.get("exterior")
            ref = ("s100:curveProperty", self._curve(coords))
        else:
            exterior = spec.get("exterior") or spec.get("coordinates")
            if exterior is None:
                raise ValueError(f"几何 {name}: 面要素缺少 exterior 坐标")
            ext_cc = self._curve(close_ring(exterior))
            int_ccs = [self._curve(close_ring(r)) for r in spec.get("interiors", [])]
            sid = self._next("s")
            self.surfaces.append((sid, ext_cc, int_ccs))
            ref = ("s100:surfaceProperty", sid)
        self.refs[name] = ref
        return ref

    def render(self) -> list[str]:
        lines: list[str] = []
        for pid, pos in self.points:
            lines.append(f'{IND}<s100:Point srsName="{SRS}" gml:id="{pid}">')
            lines.append(f"{IND*2}<gml:pos>{fmt_pos(pos)}</gml:pos>")
            lines.append(f"{IND}</s100:Point>")
        for cid, coords in self.curves:
            lines.append(f'{IND}<s100:Curve srsName="{SRS}" gml:id="{cid}">')
            lines.append(f"{IND*2}<gml:segments>")
            lines.append(f"{IND*3}<gml:LineStringSegment>")
            lines.append(f"{IND*4}<gml:posList>{' '.join(fmt_pos(p) for p in coords)}</gml:posList>")
            lines.append(f"{IND*3}</gml:LineStringSegment>")
            lines.append(f"{IND*2}</gml:segments>")
            lines.append(f"{IND}</s100:Curve>")
        for ocid, cid in self.orientables:
            lines.append(f'{IND}<s100:OrientableCurve srsName="{SRS}" gml:id="{ocid}" orientation="+">')
            lines.append(f'{IND*2}<gml:baseCurve xlink:href="#{cid}"/>')
            lines.append(f"{IND}</s100:OrientableCurve>")
        for ccid, members in self.composites:
            lines.append(f'{IND}<s100:CompositeCurve srsName="{SRS}" gml:id="{ccid}">')
            for m in members:
                lines.append(f'{IND*2}<gml:curveMember xlink:href="#{m}"/>')
            lines.append(f"{IND}</s100:CompositeCurve>")
        for sid, ext, ints in self.surfaces:
            lines.append(f'{IND}<s100:Surface srsName="{SRS}" gml:id="{sid}">')
            lines.append(f"{IND*2}<gml:patches>")
            lines.append(f"{IND*3}<gml:PolygonPatch>")
            lines.append(f"{IND*4}<gml:exterior>")
            lines.append(f"{IND*5}<gml:Ring>")
            lines.append(f'{IND*6}<gml:curveMember xlink:href="#{ext}"/>')
            lines.append(f"{IND*5}</gml:Ring>")
            lines.append(f"{IND*4}</gml:exterior>")
            for icc in ints:
                lines.append(f"{IND*4}<gml:interior>")
                lines.append(f"{IND*5}<gml:Ring>")
                lines.append(f'{IND*6}<gml:curveMember xlink:href="#{icc}"/>')
                lines.append(f"{IND*5}</gml:Ring>")
                lines.append(f"{IND*4}</gml:interior>")
            lines.append(f"{IND*3}</gml:PolygonPatch>")
            lines.append(f"{IND*2}</gml:patches>")
            lines.append(f"{IND}</s100:Surface>")
        return lines

    def envelope(self):
        lats = [c[0] for c in self.coords]
        lons = [c[1] for c in self.coords]
        return min(lats), min(lons), max(lats), max(lons)


def close_ring(ring):
    if ring and ring[0] != ring[-1]:
        ring = list(ring) + [ring[0]]
    return ring


def fmt_pos(pos) -> str:
    """输出 'lat lon'（EPSG:4326 轴序，与真实数据集一致）。"""
    return f"{fmt_num(pos[0])} {fmt_num(pos[1])}"


# ---------------------------------------------------------------------------
# 主构建流程
# ---------------------------------------------------------------------------

def build(doc: dict, assoc_objects: bool = False):
    ds = doc.get("dataset") or {}
    name = ds.get("name")
    if not name:
        raise ValueError("dataset.name 必填（如 127CN00PTWRVEW001）")
    common = doc.get("common") or {}
    features = doc.get("features") or []
    if not features:
        raise ValueError("features 为空")

    pool = GeometryPool()
    geoms = doc.get("geometries") or {}

    ids: dict[str, str] = {}
    nf = ni = 0
    for feat in features:
        ref = feat.get("ref") or feat.get("id")
        ftype = feat["type"]
        if not ref:
            raise ValueError(f"要素缺少 ref：{feat}")
        if ref in ids:
            raise ValueError(f"ref 重复：{ref}")
        if ftype in GEOGRAPHIC_FEATURES:
            nf += 1
            ids[ref] = f"f{nf}"
        else:
            ni += 1
            ids[ref] = f"i{ni}"

    by_ref = {(f.get("ref") or f.get("id")): f for f in features}

    # 自动补齐互为反向的聚合关联（consistsOf ↔ componentOf）
    for feat in features:
        src = feat.get("ref") or feat.get("id")
        for assoc in list(feat.get("associations") or []):
            inv = INVERSE_ROLES.get(assoc.get("role"))
            target = by_ref.get(assoc.get("target"))
            if not inv or target is None:
                continue
            existing = target.setdefault("associations", [])
            if not any(a.get("role") == inv and a.get("target") == src for a in existing):
                existing.append({"role": inv, "target": src, "_auto": True})

    # --assoc-objects：把关联类型物化成 PermissionType / InclusionType 成员对象
    assoc_object_lines: list[str] = []
    assoc_object_for: dict[tuple, str] = {}
    if assoc_objects:
        for feat in features:
            for assoc in feat.get("associations") or []:
                role = assoc.get("role")
                title = ROLE_TITLE.get(role)
                kind = assoc.get("permission") or assoc.get("inclusion")
                if title not in ("PermissionType", "InclusionType") or not kind:
                    continue
                target_id = ids.get(assoc.get("target"))
                key = (title, kind, target_id)
                if key in assoc_object_for:
                    continue
                ni += 1
                oid = f"i{ni}"
                assoc_object_for[key] = oid
                assoc_object_lines.append(f'{IND*2}<{title} gml:id="{oid}">')
                if title == "PermissionType":
                    code, label = resolve_enum("categoryOfRelationship", kind)
                    assoc_object_lines.append(
                        f'{IND*3}<categoryOfRelationship code="{code}">{label}</categoryOfRelationship>')
                    assoc_object_lines.append(
                        f'{IND*3}<permission xlink:href="#{target_id}"'
                        f' xlink:arcrole="{ARCROLE_BASE}permission" xlink:title="PermissionType"/>')
                else:
                    code, label = resolve_enum("membership", kind)
                    assoc_object_lines.append(f'{IND*3}<membership code="{code}">{label}</membership>')
                    assoc_object_lines.append(
                        f'{IND*3}<isApplicableTo xlink:href="#{target_id}"'
                        f' xlink:arcrole="{ARCROLE_BASE}isApplicableTo" xlink:title="InclusionType"/>')
                assoc_object_lines.append(f"{IND*2}</{title}>")

    fin = int(ds.get("featureIdStart", 1))
    agency = ds.get("agency", "CN")
    assoc_rows: list[dict] = []
    member_lines: list[str] = []

    for feat in features:
        ref = feat.get("ref") or feat.get("id")
        ftype = feat["type"]
        gid = ids[ref]
        is_geo = ftype in GEOGRAPHIC_FEATURES

        attrs: dict = {}
        if is_geo:
            attrs["s100:featureObjectIdentifier"] = {
                "s100:agency": agency,
                "s100:featureIdentificationNumber": fin,
                "s100:featureIdentificationSubdivision": 1,
            }
            fin += 1

        reusable = feat.get("reusable", ftype in REUSABLE_BY_DEFAULT)
        for key in ("fixedDateRange", "sourceIndication"):
            if key in feat:
                if feat[key] is not None:
                    attrs[key] = feat[key]
            elif not reusable and key in common:
                attrs[key] = common[key]

        attrs["featureName"] = expand_feature_name(ftype, feat["featureName"])
        if "textContent" in feat:
            attrs["textContent"] = expand_text_content(feat["textContent"])
        for key, value in (feat.get("attributes") or {}).items():
            if key.startswith("$"):
                continue
            if key == "information":
                value = expand_information(value)
            if key == "textContent":
                value = expand_text_content(value)
            attrs[key] = value

        assoc_elems: dict[str, list[str]] = {}
        for assoc in feat.get("associations") or []:
            role = assoc["role"]
            if role not in ASSOC_ELEMENTS:
                raise ValueError(f"{ref}: 未知关联角色 {role!r}，可选 {sorted(ASSOC_ELEMENTS)}")
            target = assoc["target"]
            if target not in ids:
                raise ValueError(f"{ref}: 关联目标 {target!r} 不存在")
            title = ROLE_TITLE.get(role, role)
            href = ids[target]
            kind = assoc.get("permission") or assoc.get("inclusion")
            if assoc_objects and title in ("PermissionType", "InclusionType") and kind:
                href = assoc_object_for[(title, kind, ids[target])]
            assoc_elems.setdefault(role, []).append(
                f'{IND*3}<{role} xlink:href="#{href}"'
                f' xlink:arcrole="{ARCROLE_BASE}{role}" xlink:title="{title}"/>'
            )
            if not assoc.get("_auto"):
                assoc_rows.append({
                    "源要素": feature_label(feat),
                    "源要素类型": ftype,
                    "关联角色": role,
                    "关联类": title,
                    "目标要素": feature_label(by_ref[target]),
                    "目标要素类型": by_ref[target]["type"],
                    "关联类型": kind or ("—" if title not in ("PermissionType", "InclusionType") else ""),
                    "GML 已承载": "是" if (assoc_objects and kind and
                                           title in ("PermissionType", "InclusionType")) else
                                  ("—" if title not in ("PermissionType", "InclusionType") else "否，需系统内补录"),
                    "条文出处": assoc.get("source", ""),
                    "备注": assoc.get("note", ""),
                })

        geom_lines: list[str] = []
        gname = feat.get("geometry")
        if gname:
            spec = geoms.get(gname)
            if spec is None:
                raise ValueError(f"{ref}: geometries 中找不到 {gname!r}")
            prop, target_id = pool.add(gname, spec)
            geom_lines = [
                f"{IND*3}<geometry>",
                f'{IND*4}<{prop} xlink:href="#{target_id}"/>',
                f"{IND*3}</geometry>",
            ]
        elif is_geo:
            warn(f"{ref}（{ftype}）是地理要素但没有 geometry")

        order = child_order(ftype)
        unknown = [k for k in attrs if k not in order]
        if unknown:
            warn(f"{ftype} 出现要素目录未定义的子元素 {unknown}，已排在已知元素之后（请核对属性名）")
        member_lines.append(f'{IND*2}<{ftype} gml:id="{gid}">')
        for key in order:
            if key == "geometry":
                member_lines += geom_lines
            elif key in ASSOC_ELEMENTS:
                member_lines += assoc_elems.get(key, [])
            elif key in attrs:
                member_lines += render_value(key, attrs[key], 3, ftype)
        for key in unknown:
            member_lines += render_value(key, attrs[key], 3, ftype)
        member_lines.append(f"{IND*2}</{ftype}>")

    if not pool.coords:
        raise ValueError("数据集不含任何坐标，无法计算 gml:boundedBy")
    lo_lat, lo_lon, hi_lat, hi_lon = pool.envelope()

    out = ['<?xml version="1.0" encoding="UTF-8"?>']
    out.append(f'<S127:Dataset {NS_ATTRS} gml:id="ds">')
    out.append(f"{IND}<gml:boundedBy>")
    out.append(f'{IND*2}<gml:Envelope srsName="{SRS}">')
    out.append(f"{IND*3}<gml:lowerCorner>{fmt_num(lo_lat)} {fmt_num(lo_lon)}</gml:lowerCorner>")
    out.append(f"{IND*3}<gml:upperCorner>{fmt_num(hi_lat)} {fmt_num(hi_lon)}</gml:upperCorner>")
    out.append(f"{IND*2}</gml:Envelope>")
    out.append(f"{IND}</gml:boundedBy>")
    out.append(f"{IND}<s100:DatasetIdentificationInformation>")
    for tag, value in DATASET_IDENTIFICATION:
        out.append(f"{IND*2}<s100:{tag}>{value}</s100:{tag}>")
        if tag == "applicationProfile":
            out.append(f"{IND*2}<s100:datasetFileIdentifier>{name}.gml</s100:datasetFileIdentifier>")
            out.append(f"{IND*2}<s100:datasetTitle>{esc(ds.get('title', name))}</s100:datasetTitle>")
            if ds.get("referenceDate"):
                out.append(f"{IND*2}<s100:datasetReferenceDate>{ds['referenceDate']}</s100:datasetReferenceDate>")
    out.append(f"{IND*2}<s100:updateNumber>{ds.get('updateNumber', 0)}</s100:updateNumber>")
    out.append(f"{IND}</s100:DatasetIdentificationInformation>")
    out += pool.render()
    out.append(f"{IND}<members>")
    out += assoc_object_lines
    out += member_lines
    out.append(f"{IND}</members>")
    out.append("</S127:Dataset>")
    return "\n".join(out) + "\n", assoc_rows


def feature_label(feat: dict) -> str:
    fn = feat.get("featureName") or {}
    if isinstance(fn, dict):
        return " / ".join(x for x in (fn.get("zho"), fn.get("eng")) if x)
    if isinstance(fn, list):
        return " / ".join(str(x.get("name", "")) for x in fn)
    return str(feat.get("ref", ""))


def main() -> int:
    ap = argparse.ArgumentParser(description="要素清单 JSON → S-127 GML")
    ap.add_argument("featureset", help="要素清单 JSON 路径")
    ap.add_argument("-o", "--output", help="输出 GML 路径（默认 <dataset.name>.gml）")
    ap.add_argument("--assoc-csv", help="关联类型补录清单 CSV 输出路径")
    ap.add_argument("--assoc-objects", action="store_true",
                    help="把许可/包含类型物化为 PermissionType/InclusionType 成员对象"
                         "（schema 支持，但现有生产数据未使用，导入前请先小样验证）")
    args = ap.parse_args()

    doc = json.loads(Path(args.featureset).read_text(encoding="utf-8"))
    gml, assoc_rows = build(doc, assoc_objects=args.assoc_objects)

    out_path = Path(args.output or f"{doc['dataset']['name']}.gml")
    out_path.write_text(gml, encoding="utf-8", newline="\n")
    print(f"[OK] 已生成 {out_path}（{len(doc['features'])} 个要素"
          f"{'，关联类型已写入 GML' if args.assoc_objects else ''}）")

    if args.assoc_csv and assoc_rows:
        with open(args.assoc_csv, "w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(assoc_rows[0].keys()))
            writer.writeheader()
            writer.writerows(assoc_rows)
        print(f"[OK] 已生成关联清单 {args.assoc_csv}（{len(assoc_rows)} 条）")

    todo = [r for r in assoc_rows if r["GML 已承载"] == "否，需系统内补录"]
    missing = [r for r in assoc_rows if r["关联类型"] == ""]
    if missing:
        print(f"[提醒] {len(missing)} 条 Permission/Inclusion 关联未指定类型")
    if todo:
        print(f"[提醒] {len(todo)} 条关联类型未写入 GML，须在值班子系统内补选"
              f"（或改用 --assoc-objects）")
    for w in warnings:
        print(f"[警告] {w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
