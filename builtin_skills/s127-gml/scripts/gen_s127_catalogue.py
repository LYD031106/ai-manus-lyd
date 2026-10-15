#!/usr/bin/env python3
"""从官方要素目录 S127FC.xml 生成 s127_catalogue.py（权威表）。

    python scripts/gen_s127_catalogue.py                      # 生成
    python scripts/gen_s127_catalogue.py --verify '示例/**/*.gml'   # 用真实 GML 复核枚举 code

**要素目录（S-100 Part 5 Feature Catalogue）是 S-127 的权威定义**：
要素类全集、属性全集、枚举 label→code 的官方数值、每个要素类允许的属性与多重性、
每个绑定允许的枚举子集、允许的几何图元、关联角色与目标类型、继承链。

要素目录**不定义 XML 元素顺序**（它不是序列化格式）。顺序按生产实践处理，
见 s127_model.py 的 child_order()。

生成内容：
  FC_VERSION      要素目录版本
  FEATURES        要素类 → 种类/继承/允许几何/定义
  ATTRIBUTES      属性 → 名称/值类型
  ENUMS           枚举属性 → {label: code}（**官方数值**）
  COMPLEX         复合属性 → [(子属性, lower, upper, 允许枚举子集)]
  BINDINGS        要素类 → {属性: (lower, upper, 允许枚举子集)}（继承已展开）
  ASSOCS          要素类 → {角色: (lower, upper, [目标类型…], 关联类, roleType)}（继承已展开）
  ROLES           角色 → 名称/定义
"""
from __future__ import annotations

import argparse
import glob
import re
import sys
import xml.etree.ElementTree as ET
from collections import OrderedDict
from pathlib import Path

FC = "{http://www.iho.int/S100FC}"
BASE = "{http://www.iho.int/S100Base}"
HERE = Path(__file__).resolve().parent
DEFAULT_FC = HERE.parent / "schemas" / "S127FC.xml"
OUT = HERE / "s127_catalogue.py"


def text(elem, tag, default=None):
    node = elem.find(FC + tag)
    return (node.text or "").strip() if node is not None and node.text else default


def multiplicity(node):
    m = node.find(FC + "multiplicity")
    if m is None:
        return (0, -1)
    lower = int((m.findtext(BASE + "lower") or "0").strip())
    upper_node = m.find(BASE + "upper")
    if upper_node is None or upper_node.get("infinite") == "true":
        upper = -1
    else:
        upper = int((upper_node.text or "-1").strip())
    return (lower, upper)


def permitted(node):
    pv = node.find(FC + "permittedValues")
    if pv is None:
        return None
    return [int((v.text or "0").strip()) for v in pv.findall(FC + "value")]


def parse(fc_path: Path):
    root = ET.parse(fc_path).getroot()

    # ---- 简单属性与枚举 ----
    attributes, enums = {}, {}
    for sa in root.iter(FC + "S100_FC_SimpleAttribute"):
        code = text(sa, "code")
        if not code:
            continue
        vt = text(sa, "valueType", "text")
        attributes[code] = {"name": text(sa, "name"), "valueType": vt}
        lv = sa.find(FC + "listedValues")
        if lv is not None:
            table = OrderedDict()
            for v in lv.findall(FC + "listedValue"):
                label, num = text(v, "label"), text(v, "code")
                if label and num:
                    table[label] = int(num)
            if table:
                enums[code] = table

    # ---- 复合属性 ----
    complex_attrs = {}
    for ca in root.iter(FC + "S100_FC_ComplexAttribute"):
        code = text(ca, "code")
        if not code:
            continue
        subs = []
        for b in ca.findall(FC + "subAttributeBinding"):
            ref = b.find(FC + "attribute")
            if ref is None:
                continue
            lo, up = multiplicity(b)
            subs.append((ref.get("ref"), lo, up, permitted(b)))
        complex_attrs[code] = subs
        attributes.setdefault(code, {"name": text(ca, "name"), "valueType": "complex"})

    # ---- 角色与关联类 ----
    roles = {}
    for ro in root.iter(FC + "S100_FC_Role"):
        code = text(ro, "code")
        if code:
            roles[code] = {"name": text(ro, "name"), "definition": text(ro, "definition")}

    # ---- 要素类与信息类 ----
    raw = {}
    for kind_tag, kind in (("S100_FC_FeatureType", "feature"),
                           ("S100_FC_InformationType", "information")):
        for ft in root.iter(FC + kind_tag):
            code = text(ft, "code")
            if not code:
                continue
            binds = OrderedDict()
            for b in ft.findall(FC + "attributeBinding"):
                ref = b.find(FC + "attribute")
                if ref is None:
                    continue
                lo, up = multiplicity(b)
                binds[ref.get("ref")] = (lo, up, permitted(b))
            assocs = OrderedDict()
            for tag in ("informationBinding", "featureBinding"):
                for b in ft.findall(FC + tag):
                    role = b.find(FC + "role")
                    if role is None:
                        continue
                    code_ = role.get("ref")
                    lo, up = multiplicity(b)
                    target = b.find(FC + "informationType")
                    if target is None:
                        target = b.find(FC + "featureType")
                    assoc = b.find(FC + "association")
                    tgt = target.get("ref") if target is not None else None
                    # 同一角色可以有多条绑定、指向不同目标类型
                    # （如 VTSA.consistsOf → 报告线 / 信号站 / 雷达范围）
                    if code_ in assocs:
                        lo0, up0, tgts, ac0, rt0 = assocs[code_]
                        if tgt and tgt not in tgts:
                            tgts.append(tgt)
                        assocs[code_] = (min(lo0, lo), -1 if -1 in (up0, up) else max(up0, up),
                                         tgts, ac0 or (assoc.get("ref") if assoc is not None else None),
                                         rt0 or b.get("roleType"))
                    else:
                        assocs[code_] = (
                            lo, up, [tgt] if tgt else [],
                            assoc.get("ref") if assoc is not None else None,
                            b.get("roleType"),
                        )
            raw[code] = {
                "name": text(ft, "name"),
                "definition": (text(ft, "definition") or "")[:400],
                "kind": kind,
                "abstract": ft.get("isAbstract") == "true",
                "superType": text(ft, "superType"),
                "useType": text(ft, "featureUseType"),
                "primitives": [p.text.strip() for p in ft.findall(FC + "permittedPrimitives")
                               if p.text],
                "_binds": binds,
                "_assocs": assocs,
            }

    # ---- 展开继承（父在前、子在后）----
    def resolve(code, key, depth=0):
        if depth > 12 or code not in raw:
            return OrderedDict()
        out = OrderedDict()
        parent = raw[code]["superType"]
        if parent:
            out.update(resolve(parent, key, depth + 1))
        for name, val in raw[code][key].items():
            if key == "_assocs" and name in out:
                # 子类补充的目标类型要并进父类那条，别覆盖
                lo0, up0, tgts0, ac0, rt0 = out[name]
                lo1, up1, tgts1, ac1, rt1 = val
                merged = list(tgts0) + [t for t in tgts1 if t not in tgts0]
                out[name] = (min(lo0, lo1), -1 if -1 in (up0, up1) else max(up0, up1),
                             merged, ac0 or ac1, rt0 or rt1)
            else:
                out[name] = val
        return out

    features, bindings, associations = {}, {}, {}
    for code, info in raw.items():
        if info["abstract"]:
            continue                                   # 抽象类不能实例化
        chain = [code]
        while raw.get(chain[-1], {}).get("superType"):
            chain.append(raw[chain[-1]]["superType"])
        prims = info["primitives"]
        if not prims:                                  # 几何约束可能定义在父类上
            for anc in chain[1:]:
                if raw.get(anc, {}).get("primitives"):
                    prims = raw[anc]["primitives"]
                    break
        use = info["useType"]
        if not use:
            for anc in chain[1:]:
                if raw.get(anc, {}).get("useType"):
                    use = raw[anc]["useType"]
                    break
        features[code] = {
            "name": info["name"],
            "kind": "geographic" if use == "geographic" else (
                "meta" if use == "meta" else info["kind"] if info["kind"] == "information"
                else "geographic"),
            "superType": info["superType"],
            "chain": list(reversed(chain)),
            "primitives": prims,
            "definition": info["definition"],
        }
        bindings[code] = dict(resolve(code, "_binds"))
        associations[code] = dict(resolve(code, "_assocs"))

    # 关联类（PermissionType / InclusionType 等）单列出来
    assoc_classes = {}
    for tag in ("S100_FC_InformationAssociation", "S100_FC_FeatureAssociation"):
        for a in root.iter(FC + tag):
            code = text(a, "code")
            if code:
                assoc_classes[code] = {
                    "name": text(a, "name"),
                    "roles": [r.get("ref") for r in a.findall(FC + "role")],
                    "kind": "information" if "Information" in tag else "feature",
                }

    return {
        "version": text(root, "versionNumber"),
        "date": text(root, "versionDate"),
        "features": features,
        "bindings": bindings,
        "associations": associations,
        "attributes": attributes,
        "enums": enums,
        "complex": complex_attrs,
        "roles": roles,
        "assoc_classes": assoc_classes,
        "abstract": sorted(c for c, i in raw.items() if i["abstract"]),
    }


def fmt(obj, indent=0):
    pad = " " * indent
    if isinstance(obj, dict):
        if not obj:
            return "{}"
        lines = ["{"]
        for k, v in obj.items():
            lines.append(f"{pad}    {k!r}: {fmt(v, indent + 4)},")
        lines.append(pad + "}")
        return "\n".join(lines)
    if isinstance(obj, (list, tuple)):
        body = ", ".join(repr(x) if not isinstance(x, (dict, list, tuple)) else fmt(x, indent + 4)
                         for x in obj)
        opener, closer = ("[", "]") if isinstance(obj, list) else ("(", ")")
        if len(body) < 100:
            return opener + body + (",)" if isinstance(obj, tuple) and len(obj) == 1 else closer)
        lines = [opener]
        for x in obj:
            lines.append(f"{pad}    {fmt(x, indent + 4) if isinstance(x, (dict, list, tuple)) else repr(x)},")
        lines.append(pad + closer)
        return "\n".join(lines)
    return repr(obj)


def render(t) -> str:
    n_enum = sum(len(v) for v in t["enums"].values())
    geo = sum(1 for f in t["features"].values() if f["kind"] == "geographic")
    info = sum(1 for f in t["features"].values() if f["kind"] == "information")
    head = f'''"""S-127 权威表 —— 由 gen_s127_catalogue.py 从官方要素目录 S127FC.xml 生成。

**不要手工编辑。** 要素目录换版就重跑：python scripts/gen_s127_catalogue.py

来源：S127FC.xml（S-100 Part 5 Feature Catalogue for S-127）
版本：{t["version"]}（{t["date"]}）
规模：{geo} 个地理要素类、{info} 个信息要素类、{len(t["abstract"])} 个抽象类、
      {len(t["attributes"])} 个属性、{len(t["enums"])} 个枚举属性 / {n_enum} 个枚举值、
      {len(t["complex"])} 个复合属性、{len(t["roles"])} 个关联角色、
      {len(t["assoc_classes"])} 个关联类。

枚举 code 是要素目录里的官方数值（listedValue/code），不是推导值。
注意官方 code **不连续**：如 status 是 1~9、12、14~18、28；
restriction 到 39 但缺 38；categoryOfMilitaryPracticeArea 从 2 起。
"""

FC_VERSION = {t["version"]!r}
FC_DATE = {t["date"]!r}

'''
    body = []
    body.append("# 抽象类（不可实例化，只贡献继承属性）\n")
    body.append("ABSTRACT_TYPES = " + fmt(t["abstract"]) + "\n")
    body.append("\n# 要素类 → 种类 / 继承链 / 允许几何图元 / 定义\n")
    body.append("FEATURES = " + fmt(t["features"]) + "\n")
    body.append("\n# 要素类 → {属性: (lower, upper, 允许的枚举 code 子集或 None)}；upper=-1 表示不限\n")
    body.append("BINDINGS = " + fmt(t["bindings"]) + "\n")
    body.append("\n# 要素类 → {角色: (lower, upper, 目标类型, 关联类, roleType)}\n")
    body.append("ASSOCS = " + fmt(t["associations"]) + "\n")
    body.append("\n# 属性 → 名称 / 值类型\n")
    body.append("ATTRIBUTES = " + fmt(t["attributes"]) + "\n")
    body.append("\n# 枚举属性 → {label: 官方 code}\n")
    body.append("ENUMS = " + fmt(t["enums"]) + "\n")
    body.append("\n# 复合属性 → [(子属性, lower, upper, 允许枚举子集)]\n")
    body.append("COMPLEX = " + fmt(t["complex"]) + "\n")
    body.append("\n# 关联角色 → 名称 / 定义\n")
    body.append("ROLES = " + fmt(t["roles"]) + "\n")
    body.append("\n# 关联类 → 名称 / 两端角色 / 类型\n")
    body.append("ASSOC_CLASSES = " + fmt(t["assoc_classes"]) + "\n")
    tail = '''

# ---------------------------------------------------------------------------
# 查询辅助
# ---------------------------------------------------------------------------

def binding(feature_type, attr):
    """(lower, upper, 允许枚举子集)；该要素类不允许此属性时返回 None。"""
    return BINDINGS.get(feature_type, {}).get(attr)


def assoc(feature_type, role):
    """(lower, upper, [目标类型…], 关联类, roleType)；不允许时返回 None。"""
    return ASSOCS.get(feature_type, {}).get(role)


def allowed_attributes(feature_type):
    return list(BINDINGS.get(feature_type, {}))


def allowed_roles(feature_type):
    return list(ASSOCS.get(feature_type, {}))


def required_attributes(feature_type):
    """要素目录规定 lower>=1 的属性。"""
    return [a for a, (lo, _, _) in BINDINGS.get(feature_type, {}).items() if lo >= 1]


def is_repeatable(feature_type, attr):
    b = binding(feature_type, attr)
    return bool(b) and (b[1] == -1 or b[1] > 1)


def max_occurs(attr):
    """该属性在所有绑定与复合属性里的最宽松上限（-1 = 不限）。"""
    best = 1
    for binds in BINDINGS.values():
        if attr in binds:
            up = binds[attr][1]
            if up == -1:
                return -1
            best = max(best, up)
    for subs in COMPLEX.values():
        for name, _lo, up, _pv in subs:
            if name == attr:
                if up == -1:
                    return -1
                best = max(best, up)
    return best


def sub_attributes(complex_attr):
    return [s[0] for s in COMPLEX.get(complex_attr, [])]


def permitted_labels(feature_type, attr):
    """该要素类在此属性上允许的枚举 label（要素目录可能只允许全集的子集）。"""
    table = ENUMS.get(attr)
    if not table:
        return None
    b = binding(feature_type, attr)
    if not b or b[2] is None:
        return list(table)
    allow = set(b[2])
    return [label for label, code in table.items() if code in allow]
'''
    return head + "".join(body) + tail


def verify(fc_path: Path, pattern: str) -> int:
    t = parse(fc_path)
    enums = t["enums"]
    ok = bad = 0
    for path in glob.glob(pattern, recursive=True):
        raw = Path(path).read_text(encoding="utf-8")
        for attr, code, label in re.findall(r'<([A-Za-z0-9_]+) code="(\d+)">([^<]*)</\1>', raw):
            table = enums.get(attr)
            if table is None:
                bad += 1
                print(f"[不符] {Path(path).name}: 要素目录里没有枚举属性 {attr}", file=sys.stderr)
            elif label not in table:
                bad += 1
                print(f"[不符] {Path(path).name}: {attr}={label!r} 不在要素目录取值内", file=sys.stderr)
            elif table[label] != int(code):
                bad += 1
                print(f"[不符] {Path(path).name}: {attr}.{label} 文件 code={code}，"
                      f"要素目录 code={table[label]}", file=sys.stderr)
            else:
                ok += 1
    print(f"[复核] 吻合 {ok}，不符 {bad}")
    return bad


def main() -> int:
    ap = argparse.ArgumentParser(description="从 S127FC.xml 生成 s127_catalogue.py")
    ap.add_argument("--fc", default=str(DEFAULT_FC))
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--verify", help="用真实 GML 复核枚举 code，如 '示例/**/*.gml'")
    args = ap.parse_args()

    fc_path = Path(args.fc)
    if args.verify:
        return 1 if verify(fc_path, args.verify) else 0

    t = parse(fc_path)
    Path(args.out).write_text(render(t), encoding="utf-8", newline="\n")
    geo = sum(1 for f in t["features"].values() if f["kind"] == "geographic")
    info = sum(1 for f in t["features"].values() if f["kind"] == "information")
    print(f"[OK] 已生成 {args.out}")
    print(f"     要素目录 {t['version']}；地理要素 {geo}、信息要素 {info}、抽象类 {len(t['abstract'])}")
    print(f"     属性 {len(t['attributes'])}、枚举属性 {len(t['enums'])} / 取值 "
          f"{sum(len(v) for v in t['enums'].values())}、复合属性 {len(t['complex'])}、"
          f"角色 {len(t['roles'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
