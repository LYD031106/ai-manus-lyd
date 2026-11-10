#!/usr/bin/env python3
"""S-127 草稿、按名称补几何和最终 GML 检查。"""
from __future__ import annotations

import argparse
import json
import sys
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

import validate_featureset as checks
from build_gml import DRAFT_MARKER, build, feature_label, warnings as build_warnings, write_assoc_csv
from gml_to_featureset import (
    GML, XLINK, XSI_NIL, convert, dataset_members, local, parse_geometries, reference,
)
from s127_model import ENUMS, GEOGRAPHIC_FEATURES, SRS, geometry_allowed

S127_DATASET = "{http://www.iho.int/S127/gml/cs0/1.0}Dataset"
WGS84 = {SRS, "urn:ogc:def:crs:EPSG::4326", "urn:ogc:def:crs:EPSG:0:4326"}
PRIMITIVES = {"surfaceProperty": "surface", "curveProperty": "curve", "pointProperty": "point"}


def feature_ref(feature):
    return feature.get("ref") or feature.get("id")


def name_keys(value):
    if isinstance(value, dict):
        items = ((language, value.get(language)) for language in ("zho", "eng"))
    else:
        items = ((item.get("language"), item.get("name")) for item in value or [])
    return {
        (language, " ".join(unicodedata.normalize("NFC", name).split()).casefold())
        for language, name in items if language in {"zho", "eng"} and name and name.strip()
    }


def read_dataset(path):
    parser = ET.iterparse(path, events=("pi",))
    draft = False
    for _, instruction in parser:
        if instruction.text == DRAFT_MARKER:
            draft = True
    root = parser.root
    if root.tag != S127_DATASET:
        raise ValueError("文件根元素不是本项目支持的 S-127 Dataset")
    ids = Counter(elem.get(GML + "id") for elem in root.iter() if GML + "id" in elem.attrib)
    duplicates = [gid for gid, count in ids.items() if not gid or count > 1]
    if duplicates:
        raise ValueError(f"空或重复的 gml:id：{duplicates}")
    return root, ids, draft


def read_geometries(root):
    for elem in root.iter():
        srs = elem.get("srsName")
        if srs and srs not in WGS84:
            raise ValueError(f"不支持的坐标系 {srs!r}；请提供 EPSG:4326（纬度、经度）")
        if elem.get("srsDimension", "2") != "2":
            raise ValueError("补录和检查只支持二维几何")
        if elem.get("axisLabels", "").lower().split() in (["lon", "lat"], ["long", "lat"]):
            raise ValueError("坐标轴须按 EPSG:4326 的纬度、经度顺序")
    return parse_geometries(root)


def geometry_for(member, geometries):
    props = member.findall("{*}geometry")
    if not props:
        return None
    if len(props) != 1 or len(props[0]) != 1:
        raise ValueError("geometry 必须包含一个几何属性")
    prop = props[0][0]
    gid = reference(prop)
    geometry = geometries.get(gid)
    if geometry is None or geometry["type"] != PRIMITIVES.get(local(prop.tag)):
        raise ValueError(f"几何引用 #{gid} 缺失、类型不匹配或暂不支持")
    if len(prop):
        raise ValueError("暂不支持复制带 maskReference 等子元素的几何")
    return geometry


def supplement(doc, path, matches):
    root, _, draft = read_dataset(path)
    if draft:
        raise ValueError("上传的是流程草稿，请提供含准确几何的 GML")
    geometries = read_geometries(root)
    donors = {}
    for member in dataset_members(root):
        gid = member.get(GML + "id")
        names = name_keys([
            {"language": item.findtext("{*}language"), "name": item.findtext("{*}name")}
            for item in member.findall("{*}featureName")
        ])
        if gid and names:
            donors[gid] = (names, member)

    geographic = [f for f in doc["features"] if f["type"] in GEOGRAPHIC_FEATURES]
    names_by_ref = {feature_ref(f): name_keys(f.get("featureName")) for f in geographic}
    candidates_by_ref = {
        ref: [gid for gid, (donor_names, _) in donors.items() if names & donor_names]
        for ref, names in names_by_ref.items()
    }
    donor_uses = Counter(gid for candidates in candidates_by_ref.values() for gid in candidates)
    pending = {feature_ref(f): f for f in geographic
               if not f.get("geometry") or f.get("geometryStatus") == "review"}
    unknown = set(matches) - pending.keys()
    if unknown:
        raise ValueError(f"--match 指向了不存在或无需补录的要素：{sorted(unknown)}")

    notes = {}
    for ref, feature in pending.items():
        candidates = candidates_by_ref[ref]
        if ref in matches:
            gid = matches[ref]
            if gid not in candidates:
                raise ValueError(f"{ref} 与 #{gid} 必须具有相同名称")
        elif len(candidates) == 1 and donor_uses[candidates[0]] == 1:
            gid = candidates[0]
        else:
            notes[ref] = "未找到同名要素" if not candidates else f"同名有歧义，请确认对应关系：{', '.join(candidates)}"
            continue

        try:
            geometry = geometry_for(donors[gid][1], geometries)
            if geometry is None or not geometry_allowed(feature["type"], geometry["type"]):
                raise ValueError("未提供该要素类可用的几何")
            checks.errors.clear()
            checks.warns.clear()
            checks.check_geometry(gid, geometry)
            if checks.errors:
                raise ValueError("；".join(checks.errors))
        except ValueError as exc:
            notes[ref] = f"#{gid}：{exc}"
            continue

        pool = doc.setdefault("geometries", {})
        key = f"补录-{ref}"
        while key in pool:
            key += "-新"
        old = feature.get("geometry")
        pool[key] = geometry
        feature["geometry"] = key
        feature.pop("geometryStatus", None)
        if old and all(item.get("geometry") != old for item in doc["features"]):
            pool.pop(old, None)
        notes[ref] = f"已从 {path.name} #{gid} 补录"
    return notes


def inventory(doc, notes):
    rows = []
    pending = set()
    for feat in doc["features"]:
        ref = feature_ref(feat)
        if feat["type"] not in GEOGRAPHIC_FEATURES:
            status, action = "不适用（信息要素）", "否"
        elif not feat.get("geometry"):
            status, action = "缺失", "是"
        elif feat.get("geometryStatus") == "review":
            status, action = "已勾绘，待确认", "待确认或提供准确 GML"
        else:
            status, action = "已具备", "否"
        if action != "否":
            pending.add(ref)
        rows.append((ref, feature_label(feat) or ref, feat["type"], status, action, notes.get(ref, "")))

    blocked = []
    for feat in doc["features"]:
        for assoc in feat.get("associations") or []:
            ref, target = feature_ref(feat), assoc.get("target")
            if ref in pending or target in pending:
                blocked.append(f"{ref} → {assoc.get('role')} → {target}（涉及待补录或待确认的几何）")
    return rows, pending, blocked


def report_section(title, items, empty="未发现问题。"):
    return [f"## {title}", "", *([f"- {item}" for item in items] or [empty]), ""]


def write_report(path, sections):
    path.write_text("\n".join(sections) + "\n", encoding="utf-8")


def format_inventory(rows):
    def cell(value):
        return " ".join(str(value).split()).replace("|", r"\|")

    return [
        "## 全部要素的几何状态", "",
        "| 要素标识 | 名称 | 要素类 | 几何完成程度 | 需用户提供几何 | 补录说明 |",
        "| --- | --- | --- | --- | --- | --- |",
        *["| " + " | ".join(map(cell, row)) + " |" for row in rows],
        "",
    ]


def produce(args):
    doc = json.loads(args.input.read_text(encoding="utf-8-sig"))
    checks.validate(doc, bridge_profile=args.bridge_profile)
    notes = {}
    if args.command == "complete" and args.geometry_gml and not checks.errors:
        matches = dict(args.match or [])
        if len(matches) != len(args.match or []):
            raise ValueError("同一个要素不能指定多个 --match")
        try:
            notes = supplement(doc, args.geometry_gml, matches)
        except (OSError, ET.ParseError, KeyError, ValueError) as exc:
            write_report(args.report, ["# S-127 几何补录报告", "", f"上传的几何未能补录：{exc}", "",
                                       *format_inventory(inventory(doc, {})[0])])
            return 1
        checks.validate(doc, bridge_profile=args.bridge_profile)

    rows, pending, blocked = inventory(doc, notes)
    draft = args.command == "draft" or bool(pending)
    label = "草稿" if draft else "几何补录完成"
    report = [f"# S-127 {label}报告", "", *format_inventory(rows)]
    report += report_section("涉及待确认几何的关联", blocked)
    report += report_section("要素目录及编码错误", checks.errors)
    report += report_section("编码告警与人工核对项", checks.warns)

    if checks.errors:
        write_report(args.report, report)
        return 1

    try:
        gml, associations = build(doc, draft=draft)
    except (KeyError, TypeError, ValueError) as exc:
        write_report(args.report, report + report_section("GML 构建错误", [str(exc)]))
        return 1
    path = draft_path(args.output) if draft else args.output
    if args.featureset_out:
        args.featureset_out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.assoc_csv:
        write_assoc_csv(args.assoc_csv, associations)
    path.write_text(gml, encoding="utf-8")
    report += report_section("GML 构建告警", build_warnings)
    write_report(args.report, report)
    print(f"{label}：{path}；待补录或确认几何 {len(pending)} 项；报告：{args.report}")
    return 0


def check_xsd(path, schema):
    if schema is None:
        return "未执行完整 XSD 校验（需提供含依赖的本地产品 XSD）；已执行要素目录和编码检查。", False
    try:
        from lxml import etree
    except ImportError as exc:
        return f"XSD 校验无法执行：{exc}", True
    try:
        parser = etree.XMLParser(no_network=True, resolve_entities=False)
        validator = etree.XMLSchema(etree.parse(str(schema), parser))
        validator.assertValid(etree.parse(str(path), parser))
    except (OSError, ValueError, etree.LxmlError) as exc:
        return f"XSD 校验失败或无法执行：{exc}", True
    return "通过所提供的 XSD 校验。", False


def audit(args):
    structure_errors, structure_warnings = [], []
    rows, blocked = [], []
    try:
        root, ids, draft = read_dataset(args.input)
        if draft:
            structure_errors.append("该文件标记为流程草稿，须补录或确认几何并重新生成最终 GML")
        for elem in root.iter():
            tag = local(elem.tag)
            href = elem.get(XLINK + "href", "")
            if href.startswith("#") and href[1:] not in ids:
                structure_errors.append(f"{tag} 的本地引用 {href!r} 不存在")
            if tag in ENUMS and elem.get(XSI_NIL) not in ("true", "1"):
                label = (elem.text or "").strip()
                expected = ENUMS[tag].get(label)
                if expected is None:
                    structure_errors.append(f"{tag} 的枚举标签 {label!r} 不在目录中")
                elif elem.get("code") is None:
                    structure_warnings.append(f"{tag}={label} 缺少生产编码 code={expected}")
                elif elem.get("code") != str(expected):
                    structure_errors.append(f"{tag}={label} 的 code 应为 {expected}，实为 {elem.get('code')!r}")

        geometries = read_geometries(root)
        for member in dataset_members(root):
            ref = member.get(GML + "id")
            if not ref:
                structure_errors.append(f"{local(member.tag)} 缺少 gml:id")
            if local(member.tag) in GEOGRAPHIC_FEATURES:
                name = member.findtext("{*}featureName/{*}name")
                label = " / ".join(item for item in (ref, name) if item) or local(member.tag)
                try:
                    if geometry_for(member, geometries) is None:
                        structure_errors.append(f"{label} 缺少几何，无法作为最终成果")
                except ValueError as exc:
                    structure_errors.append(f"{label}：{exc}")
                    # 几何错误已记录。继续检查名称、属性及依赖这个要素的关联。
                    for geometry in member.findall("{*}geometry"):
                        member.remove(geometry)
        doc = convert(args.input, root=root, geoms_by_id=geometries)
        checks.validate(doc, bridge_profile=args.bridge_profile)
        rows, _, blocked = inventory(doc, {})
    except (OSError, ET.ParseError, KeyError, AttributeError, IndexError, ValueError) as exc:
        structure_errors.append(f"无法完整解析 GML：{exc}")

    xsd_result, xsd_failed = check_xsd(args.input, args.xsd)
    failed = bool(structure_errors or checks.errors or xsd_failed)
    verdict = "发现阻断问题。" if failed else "已执行的自动检查未发现阻断问题。"
    report = ["# S-127 最终 GML 检查报告", "", verdict, ""]
    report += report_section("数据标准与 GML 结构", structure_errors + checks.errors)
    report += report_section("XSD 校验", [xsd_result])
    if rows:
        report += format_inventory(rows)
    report += report_section("受几何影响的关联", blocked)
    report += report_section("编码告警与人工核对项", structure_warnings + checks.warns)
    report += ["仍需结合原文、海图及审核口径核对要素选型、名称、发布日期和几何范围。"]
    write_report(args.report, report)
    print(f"{verdict} 检查报告：{args.report}")
    return int(failed)


def parse_match(value):
    ref, separator, gid = value.partition("=")
    if not separator or not ref or not gid:
        raise argparse.ArgumentTypeError("使用 --match 要素ref=上传GML的gml:id")
    return ref, gid


def draft_path(path):
    return path.with_name(path.stem + ".draft.gml")


def check_paths(args):
    donor = getattr(args, "geometry_gml", None)
    if getattr(args, "match", None) and donor is None:
        raise ValueError("--match 需要提供几何 GML")
    inputs = [args.input, donor, getattr(args, "xsd", None)]
    output = getattr(args, "output", None)
    outputs = [args.report, output, draft_path(output) if output else None,
               getattr(args, "featureset_out", None), getattr(args, "assoc_csv", None)]
    paths = [path.resolve() for path in outputs if path]
    if len(paths) != len(set(paths)) or set(paths) & {path.resolve() for path in inputs if path}:
        raise ValueError("输入与各输出必须使用不同文件名")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    phases = parser.add_subparsers(dest="command", required=True)
    for command in ("draft", "complete", "check"):
        phase = phases.add_parser(command)
        phase.add_argument("input", type=Path)
        phase.add_argument("--report", type=Path, required=True)
        phase.add_argument("--bridge-profile", action="store_true", help="标注桥区项目的代码 3 兼容项")
        if command != "check":
            phase.add_argument("-o", "--output", type=Path, required=True)
            phase.add_argument("--featureset-out", type=Path, required=command == "complete")
            phase.add_argument("--assoc-csv", type=Path)
        if command == "complete":
            phase.add_argument("geometry_gml", type=Path, nargs="?", help="同名几何 GML；已有确认几何时可省略")
            phase.add_argument("--match", action="append", type=parse_match)
        if command == "check":
            phase.add_argument("--xsd", type=Path, help="完整的本地产品 XSD，可选")
    args = parser.parse_args()
    try:
        check_paths(args)
        return audit(args) if args.command == "check" else produce(args)
    except (OSError, ET.ParseError, KeyError, AttributeError, IndexError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
