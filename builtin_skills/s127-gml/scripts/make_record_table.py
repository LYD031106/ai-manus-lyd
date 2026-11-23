#!/usr/bin/env python3
"""从要素清单 JSON 生成《S-127要素生产与检查记录表》。

指南 5.2 要求：GML 的 text 里不写条款序号，但记录表必须记录条款来源以便溯源；
指南 3.3.2(4)(5) 要求：GML 无法承载的关联类型、以及一审/二审意见都在记录表里流转。

用法：
    python make_record_table.py featureset.json -o 记录表.xlsx
    python make_record_table.py featureset.json -o 记录表      # 无 openpyxl 时输出 CSV

产出文件解析 / gml校对 / gml审核三个工作表，每个要素一行，统一八列：
    要素类 / FIN / 中文名称 / 英文名称 / 编码信息 / 有效期起 / 有效期止 / 来源
条款出处与关联保存在来源单元格批注，审核意见可追加到批注。
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import Counter
from pathlib import Path
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_gml import (  # noqa: E402
    expand_feature_name,
    expand_information,
    expand_text_content,
    render_value,
)
from source_rules import source_review, validity_dates  # noqa: E402
from s127_model import (  # noqa: E402
    GEOGRAPHIC_FEATURES,
    NS_ATTRS,
    REUSABLE_BY_DEFAULT,
    ROLE_TITLE,
)

COLUMNS = [
    "要素类",
    "FIN",
    "中文名称",
    "英文名称",
    "编码信息",
    "有效期起",
    "有效期止",
    "来源",
]
SHEETS = ["文件解析", "gml校对", "gml审核"]
COMMON_COLUMNS = {"s100:featureObjectIdentifier", "featureName", "fixedDateRange", "sourceIndication"}
XSI_NIL = "{http://www.w3.org/2001/XMLSchema-instance}nil"


def label(feat: dict) -> str:
    fn = (feat.get("attributes") or {}).get("featureName", feat.get("featureName")) or {}
    if isinstance(fn, dict):
        if fn.get("language"):
            return str(fn.get("name", ""))
        return " ".join(x for x in (fn.get("zho"), fn.get("eng")) if x)
    if isinstance(fn, list):
        return " ".join(str(x.get("name", "")) for x in fn)
    return str(feat.get("ref", ""))


def effective_value(feat: dict, common: dict, key: str):
    """采用与 build_gml 相同的覆盖/继承顺序，包括显式 null。"""
    attrs = feat.get("attributes") or {}
    if key in attrs:
        return attrs[key]
    if key in feat:
        return feat[key]
    reusable = feat.get("reusable", feat.get("type") in REUSABLE_BY_DEFAULT)
    return None if reusable else common.get(key)


def property_nodes(name: str, value, feature_type: str) -> list[ET.Element]:
    """复用 GML 序列化，避免表格另行解释枚举、双语、nil 与复合属性。"""
    xml = "\n".join(render_value(name, value, 0, feature_type))
    return list(ET.fromstring(f"<record {NS_ATTRS}>{xml}</record>"))


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def node_text(node: ET.Element | None) -> str:
    if node is None or node.get(XSI_NIL) == "true":
        return ""
    return "".join(node.itertext()).strip().replace("\r\n", "\n").replace("\r", "\n")


def child_text(node: ET.Element, name: str) -> str:
    return next((node_text(child) for child in node if local_name(child.tag) == name), "")


def source_of(feat: dict, common: dict) -> str:
    sources = property_nodes("sourceIndication", effective_value(feat, common, "sourceIndication"), feat["type"])
    return "\n".join(" / ".join(text.splitlines()) for source in sources
                     if (text := child_text(source, "source")))


def names_of(feat: dict) -> tuple[str, str]:
    value = feat.get("featureName")
    expanded = expand_feature_name(feat["type"], value) if value else None
    value = (feat.get("attributes") or {}).get("featureName", expanded)
    names = {"zho": [], "eng": []}
    for node in property_nodes("featureName", value, feat["type"]):
        lang = child_text(node, "language")
        if lang in names and (name := child_text(node, "name")):
            names[lang].append(name)
    return "\n".join(names["zho"]), "\n".join(names["eng"])


def date_range_of(feat: dict, common: dict) -> tuple[str, str]:
    nodes = property_nodes("fixedDateRange", effective_value(feat, common, "fixedDateRange"), feat["type"])
    if not nodes:
        return "", ""

    def date(name):
        value = child_text(nodes[0], name)
        # 历史成果可能使用 YYYYMMDD；只格式化完整日期，不补齐部分日期。
        return f"{value[:4]}-{value[4:6]}-{value[6:]}" if len(value) == 8 and value.isdigit() else value

    basis = feat.get("validityBasis")
    attrs = feat.get("attributes") or {}
    if basis is None and "fixedDateRange" not in feat and "fixedDateRange" not in attrs:
        basis = common.get("validityBasis")
    if basis is not None:
        try:
            _, last, _ = validity_dates(basis)
        except ValueError:
            pass
        else:
            if last is not None:
                return date("dateStart"), last.isoformat()
    return date("dateStart"), date("dateEnd")


def fin_of(feat: dict, default: int) -> str:
    if feat["type"] not in GEOGRAPHIC_FEATURES:
        return "---"
    value = (feat.get("attributes") or {}).get("s100:featureObjectIdentifier", {
        "s100:featureIdentificationNumber": default,
        "s100:featureIdentificationSubdivision": 1,
    })
    nodes = property_nodes("s100:featureObjectIdentifier", value, feat["type"])
    if not nodes:
        return ""
    number = child_text(nodes[0], "featureIdentificationNumber")
    subdivision = child_text(nodes[0], "featureIdentificationSubdivision")
    return f"{number}-{subdivision}" if number and subdivision else number


def coding_info(feat: dict) -> str:
    def flatten(nodes: list[ET.Element], prefix: str = "") -> list[str]:
        counts = Counter(node.tag for node in nodes)
        seen = Counter()
        lines = []
        for node in nodes:
            seen[node.tag] += 1
            name = local_name(node.tag)
            # 单值枚举列表维持 restriction=...；重复的复合实例保留独立索引。
            if len(node) and counts[node.tag] > 1:
                name += f"[{seen[node.tag]}]"
            path = f"{prefix}.{name}" if prefix else name
            if len(node):
                lines.extend(flatten(list(node), path))
            else:
                text = node_text(node)
                if node.get(XSI_NIL) == "true":
                    text = "null"
                elif "code" in node.attrib:
                    text = f"{node.get('code')}({text})"
                lines.append(f"{path}={text}")
        return lines

    lines = []
    for key, value in (feat.get("attributes") or {}).items():
        if key.startswith("$") or key in COMMON_COLUMNS:
            continue
        if key == "information":
            value = expand_information(value)
        elif key == "textContent":
            value = expand_text_content(value)
        lines.extend(flatten(property_nodes(key, value, feat["type"])))
    return ";\n".join(lines)


def assoc_text(feat: dict, refs: dict) -> str:
    out = []
    for a in feat.get("associations") or []:
        role = a.get("role", "?")
        title = ROLE_TITLE.get(role, role)
        target = refs.get(a.get("target"), {})
        kind = a.get("permission") or a.get("inclusion")
        piece = f"{role}→{label(target) or a.get('target')}"
        if title in ("PermissionType", "InclusionType"):
            piece += f"（{kind or '待补录'}）"
        out.append(piece)
    return "\n".join(out)


def build_rows(doc: dict) -> list[list[str]]:
    common = doc.get("common") or {}
    features = doc.get("features") or []
    rows: list[list[str]] = []
    fin = int((doc.get("dataset") or {}).get("featureIdStart", 1))
    for feat in features:
        zho, eng = names_of(feat)
        start, end = date_range_of(feat, common)
        rows.append([feat["type"], fin_of(feat, fin), zho, eng, coding_info(feat),
                     start, end, source_of(feat, common)])
        if feat["type"] in GEOGRAPHIC_FEATURES:
            fin += 1
    return rows


def build_comments(doc: dict) -> list[str]:
    features = doc.get("features") or []
    refs = {(f.get("ref") or f.get("id")): f for f in features}
    comments = []
    for feat in features:
        parts = []
        if feat.get("clause"):
            parts.append(f"条款出处：{feat['clause']}")
        if associations := assoc_text(feat, refs):
            parts.append(f"关联要素及关联类型：\n{associations}")
        if reviews := source_review(feat):
            parts.append("待核对的原文依据\n" + "\n".join(reviews))
        comments.append("\n\n".join(parts))
    return comments


def write_xlsx(path: Path, rows: list[list[str]], meta: dict, comments: list[str] | None = None) -> bool:
    try:
        from openpyxl import Workbook
        from openpyxl.comments import Comment
        from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    except ImportError:
        return False

    wb = Workbook()
    for idx, sheet in enumerate(SHEETS):
        ws = wb.active if idx == 0 else wb.create_sheet()
        ws.title = sheet
        ws.append(COLUMNS)
        head_fill = PatternFill("solid", fgColor="4472C4")
        edge = Side(style="thin", color="BFBFBF")
        border = Border(left=edge, right=edge, top=edge, bottom=edge)
        for cell in ws[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = head_fill
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            cell.border = border
        widths = [30, 12, 44, 52, 58, 14, 14, 48]
        for col, width in zip("ABCDEFGH", widths):
            ws.column_dimensions[col].width = width
        for index, row in enumerate(rows, start=2):
            ws.append(row)
            for cell in ws[index]:
                cell.alignment = Alignment(vertical="top", wrap_text=True)
                cell.border = border
                # 名称、FIN、日期和来源均保留文本，不被 Excel 识别成数字或公式。
                cell.data_type = "s"
                cell.number_format = "@"
            if comments and index - 2 < len(comments) and comments[index - 2]:
                ws.cell(index, 8).comment = Comment(comments[index - 2], "S-127")
            lines = max(sum(max(1, math.ceil(len(line) / width))
                            for line in str(value).split("\n"))
                        for value, width in zip(row, widths))
            ws.row_dimensions[index].height = min(409, max(30, lines * 16))
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = f"A1:H{len(rows) + 1}"
        note = f"数据集：{meta.get('name', '')}    要素总数：{len(rows)}    阶段：{sheet}"
        ws.cell(row=len(rows) + 3, column=1, value=note)
    wb.save(path)
    return True


def write_csv(base: Path, rows: list[list[str]]) -> list[Path]:
    written = []
    for sheet in SHEETS:
        path = base.with_name(f"{base.stem}-{sheet}.csv")
        with open(path, "w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(COLUMNS)
            writer.writerows(rows)
        written.append(path)
    return written


def main() -> int:
    ap = argparse.ArgumentParser(description="生成 S-127 要素生产与检查记录表")
    ap.add_argument("featureset")
    ap.add_argument("-o", "--output", default="S-127要素生产与检查记录表.xlsx")
    args = ap.parse_args()

    doc = json.loads(Path(args.featureset).read_text(encoding="utf-8"))
    rows = build_rows(doc)
    out = Path(args.output)

    if out.suffix.lower() == ".xlsx" and write_xlsx(out, rows, doc.get("dataset") or {}, build_comments(doc)):
        print(f"[OK] 已生成 {out}（{len(rows)} 行 × 3 个工作表）")
    else:
        if out.suffix.lower() == ".xlsx":
            print("[提醒] 未安装 openpyxl，改为输出 CSV（pip install openpyxl 可直接出 xlsx）")
        for path in write_csv(out, rows):
            print(f"[OK] 已生成 {path}")
    print("[下一步] 核对八列结果；Excel 的条款出处及关联见「来源」格批注，审核意见可追加到批注。")
    print("[追溯] CSV 的条款及证据通过对应 featureset.json / evidence.json 核对。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
