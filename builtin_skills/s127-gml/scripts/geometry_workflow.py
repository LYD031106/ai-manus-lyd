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
import source_rules
import spatial_checks
from build_gml import DRAFT_MARKER, build, feature_label, warnings as build_warnings, write_assoc_csv
from gml_to_featureset import (
    GML, XLINK, XSI_NIL, convert, dataset_members, local, parse_geometries, reference,
)
from make_record_table import build_comments, build_rows, write_csv, write_xlsx
from s127_model import ENUMS, GEOGRAPHIC_FEATURES, SRS, geometry_allowed, resolve_enum

S127_DATASET = "{http://www.iho.int/S127/gml/cs0/1.0}Dataset"
WGS84 = {SRS, "urn:ogc:def:crs:EPSG::4326", "urn:ogc:def:crs:EPSG:0:4326"}
PRIMITIVES = {"surfaceProperty": "surface", "curveProperty": "curve", "pointProperty": "point"}
GEOMETRY_NIL_REASONS = {"unknown", "notApplicable", "unpopulated", "missing", "withheld", "inapplicable", "template"}


def feature_ref(feature):
    return feature.get("ref") or feature.get("id")


def surface_signature(geometry):
    # Compare the same boundary even if the ring starts elsewhere or is reversed.
    def edges(ring):
        points = [tuple(map(float, item)) for item in ring]
        if points and points[0] == points[-1]:
            points.pop()
        return tuple(sorted(tuple(sorted((a, b))) for a, b in zip(points, points[1:] + points[:1])))

    return edges(geometry.get("exterior") or geometry.get("coordinates") or []), tuple(sorted(
        edges(ring) for ring in geometry.get("interiors", [])
    ))


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
    kind = PRIMITIVES.get(local(prop.tag))
    if not kind or not geometry_allowed(local(member.tag), kind):
        raise ValueError("几何属性与要素允许的几何类型不符")
    if not prop.get(XLINK + "href"):
        if prop.get("nilReason") in GEOMETRY_NIL_REASONS and not len(prop) and not (prop.text or "").strip():
            return None
        raise ValueError("空几何属性缺少有效 nilReason 或 xlink:href")
    if "nilReason" in prop.attrib:
        raise ValueError("几何引用和 nilReason 不可同时填写")
    gid = reference(prop)
    geometry = geometries.get(gid)
    if geometry is None or geometry["type"] != kind:
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
    if len(set(matches.values())) != len(matches):
        raise ValueError("同一个上传要素不能分配给多个目标；请提供各目标独立的真实几何")
    reserved = set(matches.values())
    pool = doc.setdefault("geometries", {})
    coverage_keys = {entry["geometry"] for entry in spatial_checks.coverage_entries(doc)}
    used_partitions = {}
    for feature in geographic:
        ref = feature_ref(feature)
        group = source_rules.partition(feature)
        shape = pool.get(feature.get("geometry"), {})
        if group and ref not in pending and shape.get("type") == "surface":
            used_partitions[group, surface_signature(shape)] = ref

    notes = {}
    for ref, feature in pending.items():
        candidates = candidates_by_ref[ref]
        if ref in matches:
            gid = matches[ref]
            if gid not in donors:
                raise ValueError(f"上传的 GML 未找到有名称的要素 #{gid}")
        elif len(candidates) == 1 and donor_uses[candidates[0]] == 1 and candidates[0] not in reserved:
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
            conflicts = spatial_checks.conflicts(feature, geometry)
            if conflicts:
                raise ValueError("；".join(conflicts))
            group = source_rules.partition(feature)
            if group and geometry["type"] == "surface":
                signature = group, surface_signature(geometry)
                if signature in used_partitions:
                    raise ValueError(f"边界与 {used_partitions[signature]} 重复；独立分区须各提供不同几何")
                used_partitions[signature] = ref
        except ValueError as exc:
            notes[ref] = f"#{gid}：{exc}"
            continue

        key = f"补录-{ref}"
        while key in pool:
            key += "-新"
        old = feature.get("geometry")
        pool[key] = geometry
        feature["geometry"] = key
        anchors = spatial_checks.anchors(feature)
        if anchors or ref in matches:
            feature.pop("geometryStatus", None)
        else:
            feature["geometryStatus"] = "review"
        feature["geometryEvidence"] = {"kind": "user-gml", "reference": f"{path.name} #{gid}"}
        if old and old not in coverage_keys and all(item.get("geometry") != old for item in doc["features"]):
            pool.pop(old, None)
        notes[ref] = f"已从 {path.name} #{gid} 补录" + ("（显式指定异名对应）" if gid not in candidates else "")
        if anchors:
            notes[ref] += "；已通过原文锚点检查"
        elif ref in matches:
            notes[ref] += "；无独立锚点，按用户明确指定的边界补录"
        else:
            notes[ref] += "；同名候选没有独立锚点，核对原文或海图后用 --match 明确确认"
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
        evidence = source_rules.geometry_evidence(feat)
        detail = "；".join(filter(None, (notes.get(ref), evidence.get("reference"),
                                       evidence.get("reviewReference"),
                                       source_rules.basis(feat).get("reference"))))
        rows.append((ref, feature_label(feat) or ref, feat["type"], status, action, detail))

    blocked = []
    for feat in doc["features"]:
        for assoc in feat.get("associations") or []:
            ref, target = feature_ref(feat), assoc.get("target")
            if ref in pending or target in pending:
                blocked.append(f"{ref} → {assoc.get('role')} → {target}（涉及待补录或待确认的几何）")
    return rows, pending, blocked


def check_partitions(doc):
    seen, problems = {}, []
    for feature in doc["features"]:
        group = source_rules.partition(feature)
        shape = doc.get("geometries", {}).get(feature.get("geometry"), {})
        if not group or shape.get("type") != "surface" or feature.get("geometryStatus") == "review":
            continue
        key = group, surface_signature(shape)
        ref = feature_ref(feature)
        if key in seen:
            problems.append(f"独立分区 {group} 中 {ref} 与 {seen[key]} 的边界重复")
        seen[key] = ref
    return problems


def source_review_items(doc):
    return [
        *source_rules.source_review(doc),
        *source_rules.source_review(doc.get("common") or {}),
        *(f"{feature_label(f) or feature_ref(f)}：{item}" for f in doc["features"]
          for item in source_rules.source_review(f)),
    ]


def check_coverage(doc, areas):
    geoms = doc.get("geometries", {})
    members = [(feature_label(f) or feature_ref(f), geoms[f["geometry"]])
               for f in doc["features"] if f.get("geometry") and f["type"] in GEOGRAPHIC_FEATURES]
    try:
        return spatial_checks.coverage_issues(areas, members)
    except ValueError as exc:
        return [f"DataCoverage 未通过覆盖检查：{exc}"]


def restriction_codes(feature):
    values = (feature.get("attributes") or {}).get("restriction")
    values = values if isinstance(values, list) else [values]
    return {resolve_enum("restriction", value)[0] for value in values if isinstance(value, (int, str))}


def audit_source_context(doc, source, *, allow_restriction_3=False):
    if not isinstance(source, dict) or not isinstance(source.get("features"), list) or not all(
        isinstance(feature, dict) for feature in source["features"]
    ):
        raise ValueError("来源清单须含有 features 要素对象数组")
    managed = source_rules.apply_restriction_clauses(source, allow_restriction_3=allow_restriction_3)
    problems = source_review_items(source)
    located = {feature_ref(f) for f in source["features"] if spatial_checks.anchors(f)}
    to_check = [f for f in source["features"] if feature_ref(f) in located or feature_ref(f) in managed]
    have_geometry = [f for f in doc["features"] if f.get("geometry")]
    source_by_ref = {feature_ref(f): f for f in source["features"]}
    output_by_ref = {feature_ref(f): f for f in doc["features"]}
    matches = {}
    for f in to_check:
        names = name_keys(f.get("featureName"))
        found = [g for g in have_geometry if g["type"] == f["type"] and names & name_keys(g.get("featureName"))]
        if len(found) > 1:
            found = [g for g in found if not spatial_checks.conflicts(f, doc["geometries"][g["geometry"]])]
        matches[feature_ref(f)] = found
    uses = Counter(feature_ref(g) for found in matches.values() for g in found)
    checked = 0
    checked_restrictions = 0
    for feature in to_check:
        label = feature_label(feature) or feature_ref(feature)
        found = matches[feature_ref(feature)]
        if len(found) != 1 or uses[feature_ref(found[0])] != 1:
            problems.append(f"{label}：最终 GML 找不到唯一的同名同类要素，需按原文范围核对对应关系")
            continue
        shape = doc["geometries"][found[0]["geometry"]]
        if feature_ref(feature) in located:
            problems.extend(f"{label}：{issue}" for issue in spatial_checks.conflicts(feature, shape))
            checked += 1
        if feature_ref(feature) in managed:
            expected, actual = restriction_codes(feature), restriction_codes(found[0])
            if expected != actual:
                problems.append(f"{label}：全区 restriction 应为 {sorted(expected)}，GML 实际为 {sorted(actual)}")
            output_rules = list(regulations_for(found[0], output_by_ref))
            for rule in regulations_for(feature, source_by_ref):
                names = name_keys(rule.get("featureName"))
                if not any(rule["type"] == item["type"] and names & name_keys(item.get("featureName"))
                           and (item.get("textContent") or (item.get("attributes") or {}).get("textContent"))
                           for item in output_rules):
                    problems.append(f"{label}：GML 缺少带条文的关联 {feature_label(rule) or feature_ref(rule)}")
            checked_restrictions += 1
    coverage = (f"来源清单有 {len(located)} 个带原文锚点的要素，实际对照 {checked} 个；其余位置须另外核对。"
                if located else "来源清单未提供几何锚点；未校验要素与原文的空间位置。")
    if managed:
        coverage += f" 原文限制编码适用 {len(managed)} 个要素，已对照 {checked_restrictions} 个。"
    return problems, coverage


def regulations_for(feature, features):
    for assoc in feature.get("associations") or []:
        if assoc.get("role") == "theRxN":
            target = features.get(assoc.get("target"), {})
            if target.get("type") in {"Regulations", "Restrictions"}:
                yield target


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
    try:
        notes = source_rules.apply_restriction_clauses(doc, allow_restriction_3=args.allow_restriction_3)
    except ValueError as exc:
        write_report(args.report, report_section("限制条款的原文依据", [str(exc)]))
        return 1
    if args.command == "draft":
        for key, detail in source_rules.normalize_draft(doc, allow_restriction_3=args.allow_restriction_3).items():
            notes[key] = "；".join(filter(None, (notes.get(key), detail)))
    for key, detail in source_rules.prepare_geometries(doc).items():
        notes[key] = "；".join(filter(None, (notes.get(key), detail)))
    checks.validate(doc, allow_restriction_3=args.allow_restriction_3)
    if not checks.errors:
        for feature in doc["features"]:
            shape = doc.get("geometries", {}).get(feature.get("geometry"))
            if shape is None:
                continue
            conflicts = spatial_checks.conflicts(feature, shape)
            if conflicts:
                key = feature_ref(feature)
                feature["geometryStatus"] = "review"
                notes[key] = "；".join(filter(None, (notes.get(key), *conflicts)))
    if args.command == "complete" and args.geometry_gml and not checks.errors:
        matches = dict(args.match or [])
        if len(matches) != len(args.match or []):
            raise ValueError("同一个要素不能指定多个 --match")
        try:
            for key, detail in supplement(doc, args.geometry_gml, matches).items():
                notes[key] = "；".join(filter(None, (notes.get(key), detail)))
        except (OSError, ET.ParseError, KeyError, ValueError) as exc:
            write_report(args.report, ["# S-127 几何补录报告", "", f"上传的几何未能补录：{exc}", "",
                                       *format_inventory(inventory(doc, {})[0])])
            return 1
        checks.validate(doc, allow_restriction_3=args.allow_restriction_3)

    rows, pending, blocked = inventory(doc, notes)
    source_review = source_review_items(doc) if not checks.errors else []
    partition_errors = check_partitions(doc) if not checks.errors else []
    coverages = spatial_checks.coverage_entries(doc) if not checks.errors else []
    coverage_problems = check_coverage(doc, [doc["geometries"][c["geometry"]] for c in coverages]) if coverages else []
    draft = args.command == "draft" or bool(pending or source_review or coverage_problems)
    label = "草稿" if draft else "几何补录完成"
    report = [f"# S-127 {label}报告", "", *format_inventory(rows)]
    if pending:
        report += ["请上传含准确几何的同名 GML。不同范围的同名面须逐一指定要素标识与供体 gml:id；报告中的编号不是空间配对依据。", ""]
    if notes.get("common"):
        report += [notes["common"], ""]
    report += report_section("涉及待确认几何的关联", blocked)
    report += report_section("待核对的原文依据", source_review)
    report += report_section("DataCoverage 空间覆盖", coverage_problems,
                             "现有几何已由所提供覆盖面覆盖。" if coverages else "未提供正式覆盖面，待核验。")
    report += report_section("要素目录及编码错误", checks.errors + partition_errors)
    report += report_section("编码告警与人工核对项", checks.warns)

    if checks.errors or partition_errors:
        write_report(args.report, report)
        return 1

    try:
        gml, associations = build(doc, draft=draft, assoc_objects=True)
    except (KeyError, TypeError, ValueError) as exc:
        write_report(args.report, report + report_section("GML 构建错误", [str(exc)]))
        return 1
    path = draft_path(args.output) if draft else args.output
    if args.featureset_out:
        args.featureset_out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.assoc_csv:
        write_assoc_csv(args.assoc_csv, associations)
    if args.record:
        record_rows = build_rows(doc)
        if not write_xlsx(args.record, record_rows, doc.get("dataset") or {}, build_comments(doc)):
            write_csv(args.record, record_rows)
    path.write_text(gml, encoding="utf-8")
    report += report_section("GML 构建告警", build_warnings)
    write_report(args.report, report)
    print(f"{label}：{path}；待补录或确认几何 {len(pending)} 项，待核对原文依据 {len(source_review)} 项；报告：{args.report}")
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
    checks.errors.clear()
    checks.warns.clear()
    rows, blocked = [], []
    context_problems = []
    coverage_shapes = []
    coverage_errors = []
    coverage_summary = "未提供 DataCoverage，App_E 100 的空间覆盖未核验。"
    context_coverage = "未提供来源清单；本次只检查 GML 结构、码值和内部几何，不核对原文空间位置。"
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
        members = dataset_members(root)
        for member in list(members):
            ref = member.get(GML + "id")
            if not ref:
                structure_errors.append(f"{local(member.tag)} 缺少 gml:id")
            if local(member.tag) == "DataCoverage":
                try:
                    coverage = geometry_for(member, geometries)
                    if coverage is None or coverage["type"] != "surface":
                        raise ValueError("缺少覆盖面几何")
                    checks.check_geometry(ref or "DataCoverage", coverage)
                    coverage_errors.extend(checks.errors)
                    if not checks.errors:
                        coverage_shapes.append(coverage)
                    structure_warnings.extend(checks.warns)
                    checks.errors.clear()
                    checks.warns.clear()
                except ValueError as exc:
                    coverage_errors.append(f"DataCoverage：{exc}")
                # DataCoverage is a meta feature, not one of the business objects converted to JSON.
                members.remove(member)
                continue
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
            elif member.findall("{*}geometry"):
                structure_errors.append(f"{ref or local(member.tag)} 是信息要素，不应有 geometry")
                for geometry in member.findall("{*}geometry"):
                    member.remove(geometry)
        doc = convert(args.input, root=root, geoms_by_id=geometries)
        checks.validate(doc, allow_restriction_3=args.allow_restriction_3)
        rows, _, blocked = inventory(doc, {})
        if coverage_shapes:
            coverage_errors.extend(check_coverage(doc, coverage_shapes))
            coverage_summary = ("发现 DataCoverage 几何问题。" if coverage_errors else
                                "已核对全部现有地理要素确实落在 DataCoverage 范围内。")
        if args.source_featureset is not None and not checks.errors:
            try:
                source = json.loads(args.source_featureset.read_text(encoding="utf-8-sig"))
                context_problems, context_coverage = audit_source_context(
                    doc, source, allow_restriction_3=args.allow_restriction_3,
                )
            except (OSError, KeyError, TypeError, ValueError) as exc:
                context_problems = [f"来源清单无法完成独立复核：{exc}"]
    except (OSError, ET.ParseError, KeyError, AttributeError, IndexError, ValueError) as exc:
        structure_errors.append(f"无法完整解析 GML：{exc}")

    xsd_result, xsd_failed = check_xsd(args.input, args.xsd)
    if args.require_coverage and not coverage_shapes:
        coverage_errors.append("最终发布检查需要正式 DataCoverage 面。")
    failed = bool(structure_errors or checks.errors or xsd_failed or context_problems or coverage_errors)
    verdict = "发现阻断问题。" if failed else "已执行的自动检查未发现阻断问题。"
    report = ["# S-127 最终 GML 检查报告", "", verdict, ""]
    report += report_section("数据标准与 GML 结构", structure_errors + checks.errors)
    report += report_section("XSD 校验", [xsd_result])
    report += report_section("DataCoverage 空间覆盖", [coverage_summary, *coverage_errors])
    report += report_section("原文依据与空间位置", [context_coverage, *context_problems])
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
    inputs = [args.input, donor, getattr(args, "xsd", None), getattr(args, "source_featureset", None)]
    output = getattr(args, "output", None)
    outputs = [args.report, output, draft_path(output) if output else None,
               getattr(args, "featureset_out", None), getattr(args, "assoc_csv", None), getattr(args, "record", None)]
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
        phase.add_argument("--allow-restriction-3", "--bridge-profile", action="store_true",
                           help="显式采用项目兼容口径：航行限制区的限制码 3 作为告警")
        if command != "check":
            phase.add_argument("-o", "--output", type=Path, required=True)
            phase.add_argument("--featureset-out", type=Path, required=command == "complete")
            phase.add_argument("--assoc-csv", type=Path)
            phase.add_argument("--record", type=Path, help="同时生成与实际输出一致的 XLSX 记录表")
        if command == "complete":
            phase.add_argument("geometry_gml", type=Path, nargs="?", help="同名几何 GML；已有确认几何时可省略")
            phase.add_argument("--match", action="append", type=parse_match)
        if command == "check":
            phase.add_argument("--xsd", type=Path, help="完整的本地产品 XSD，可选")
            phase.add_argument("--source-featureset", type=Path, help="阶段二 JSON，可选；复核最终几何与原文锚点")
            phase.add_argument("--require-coverage", action="store_true", help="最终发布检查，缺正式覆盖面也判定为阻断")
    args = parser.parse_args()
    try:
        check_paths(args)
        return audit(args) if args.command == "check" else produce(args)
    except (OSError, ET.ParseError, KeyError, AttributeError, IndexError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
