#!/usr/bin/env python3
"""S-127 schema 查询工具 —— 确定性地从要素目录读取属性格式。

这是给 Agent 或人类用的 CLI 查询接口。所有输出来自 s127_catalogue.py（由
gen_s127_catalogue.py 从官方 S127FC.xml 生成），不依赖模型记忆。

用法：
    # 查看某要素类有哪些属性、多重性、允许的枚举子集
    python lookup_schema.py attrs VesselTrafficServiceArea

    # 查看某要素类允许的关联角色与目标
    python lookup_schema.py roles VesselTrafficServiceArea

    # 查看某枚举属性的全部合法取值（label → code）
    python lookup_schema.py enum restriction

    # 查看某要素类在某枚举属性上允许的子集
    python lookup_schema.py enum restriction --feature RestrictedAreaNavigational

    # 查看某复合属性的子属性顺序与多重性
    python lookup_schema.py complex underkeelAllowance

    # 列出所有要素类
    python lookup_schema.py features

    # 查看某要素类的完整信息（继承链、几何、必填）
    python lookup_schema.py info PlaceOfRefuge

输出是确定性的：同一查询永远返回相同结果（除非重跑 gen_s127_catalogue.py 换了版本）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from s127_catalogue import (  # noqa: E402
    ASSOCS,
    BINDINGS,
    COMPLEX,
    ENUMS,
    FC_VERSION,
    FEATURES,
    allowed_attributes,
    allowed_roles,
    permitted_labels,
    required_attributes,
    sub_attributes,
)


def cmd_features(args):
    """列出所有要素类。"""
    geo = sorted(k for k, v in FEATURES.items() if v["kind"] == "geographic")
    info = sorted(k for k, v in FEATURES.items() if v["kind"] == "information")
    print(f"S-127 要素目录 {FC_VERSION}\n")
    print(f"地理要素（{len(geo)} 个）：")
    for f in geo:
        prims = ", ".join(FEATURES[f]["primitives"]) or "—"
        print(f"  {f:40s} 几何={prims}")
    print(f"\n信息要素（{len(info)} 个）：")
    for f in info:
        print(f"  {f}")


def cmd_info(args):
    """某要素类的完整信息。"""
    ft = args.feature_type
    if ft not in FEATURES:
        print(f"[错误] 未知要素类 {ft!r}；用 `lookup_schema.py features` 查看全部", file=sys.stderr)
        return 1
    f = FEATURES[ft]
    print(f"要素类：{ft}")
    print(f"  种类：{f['kind']}")
    print(f"  继承链：{' → '.join(f['chain'])}")
    print(f"  几何：{', '.join(f['primitives']) or '无（信息要素）'}")
    print(f"  定义：{f['definition'][:200]}")
    req = required_attributes(ft)
    print(f"  必填属性（lower>=1）：{req or '无'}")


def cmd_attrs(args):
    """某要素类的属性列表。"""
    ft = args.feature_type
    if ft not in FEATURES:
        print(f"[错误] 未知要素类 {ft!r}", file=sys.stderr)
        return 1
    binds = BINDINGS.get(ft, {})
    print(f"{ft} 的属性（要素目录绑定顺序，即 GML 元素顺序）：\n")
    print(f"{'属性名':36s} {'下限':>4s} {'上限':>6s} 允许枚举子集")
    print("-" * 80)
    for attr, (lo, up, pv) in binds.items():
        up_s = "∞" if up == -1 else str(up)
        pv_s = ""
        if pv is not None:
            # 找到该属性在 ENUMS 里的标签
            table = ENUMS.get(attr, {})
            labels = [lab for lab, code in table.items() if code in pv]
            pv_s = f"({len(labels)}/{len(table)})" if labels else f"codes={pv}"
        star = "★" if lo >= 1 else " "
        print(f"{star} {attr:34s} [{lo:>2}..{up_s:>3}] {pv_s}")
    print(f"\n★ = 必填（lower>=1）；上限 ∞ = 可重复")


def cmd_roles(args):
    """某要素类允许的关联角色。"""
    ft = args.feature_type
    if ft not in FEATURES:
        print(f"[错误] 未知要素类 {ft!r}", file=sys.stderr)
        return 1
    assocs = ASSOCS.get(ft, {})
    if not assocs:
        print(f"{ft} 没有关联角色")
        return
    print(f"{ft} 的关联角色：\n")
    print(f"{'角色':24s} {'下限':>4s} {'上限':>6s} {'关联类':28s} 目标")
    print("-" * 100)
    for role, (lo, up, targets, assoc_cls, rt) in assocs.items():
        up_s = "∞" if up == -1 else str(up)
        tgt = ", ".join(targets) if targets else "—"
        print(f"  {role:22s} [{lo:>2}..{up_s:>3}] {(assoc_cls or '—'):28s} {tgt}")


def cmd_enum(args):
    """枚举属性的取值。"""
    attr = args.attribute
    if attr not in ENUMS:
        # 模糊匹配
        candidates = [k for k in ENUMS if attr.lower() in k.lower()]
        if candidates:
            print(f"[提示] 找不到 {attr!r}，你可能是指：{candidates}", file=sys.stderr)
        else:
            print(f"[错误] {attr!r} 不是枚举属性；共 {len(ENUMS)} 个枚举属性：\n"
                  f"  {sorted(ENUMS)[:20]}...", file=sys.stderr)
        return 1
    table = ENUMS[attr]
    ft = args.feature
    if ft:
        labels = permitted_labels(ft, attr)
        if labels is None:
            print(f"{ft} 没有绑定属性 {attr}")
            return 1
        print(f"{attr} 在 {ft} 上允许的取值（{len(labels)}/{len(table)}）：\n")
        for lab in labels:
            print(f"  code={table[lab]:<4d} {lab}")
        excluded = [lab for lab in table if lab not in labels]
        if excluded:
            print(f"\n该要素类不允许的取值（{len(excluded)} 个）：")
            for lab in excluded:
                print(f"  code={table[lab]:<4d} {lab}")
    else:
        print(f"{attr} 的全部取值（{len(table)} 个）：\n")
        for lab, code in sorted(table.items(), key=lambda kv: kv[1]):
            print(f"  code={code:<4d} {lab}")


def cmd_complex(args):
    """复合属性的子属性。"""
    attr = args.attribute
    if attr not in COMPLEX:
        candidates = [k for k in COMPLEX if attr.lower() in k.lower()]
        if candidates:
            print(f"[提示] 找不到 {attr!r}，你可能是指：{candidates}", file=sys.stderr)
        else:
            print(f"[错误] {attr!r} 不是复合属性；共 {len(COMPLEX)} 个：\n"
                  f"  {sorted(COMPLEX)}", file=sys.stderr)
        return 1
    subs = COMPLEX[attr]
    print(f"{attr} 的子属性（按 GML 输出顺序）：\n")
    print(f"{'子属性':32s} {'下限':>4s} {'上限':>6s} 允许枚举子集")
    print("-" * 70)
    for name, lo, up, pv in subs:
        up_s = "∞" if up == -1 else str(up)
        pv_s = ""
        if pv is not None:
            table = ENUMS.get(name, {})
            labels = [lab for lab, code in table.items() if code in pv]
            pv_s = f"({len(labels)}/{len(table)})" if labels else f"codes={pv}"
        star = "★" if lo >= 1 else " "
        print(f"{star} {name:30s} [{lo:>2}..{up_s:>3}] {pv_s}")


def cmd_json(args):
    """以 JSON 输出某要素类的完整 schema（供程序消费）。"""
    ft = args.feature_type
    if ft not in FEATURES:
        print(f"[错误] 未知要素类 {ft!r}", file=sys.stderr)
        return 1
    out = {
        "feature_type": ft,
        "fc_version": FC_VERSION,
        "kind": FEATURES[ft]["kind"],
        "chain": FEATURES[ft]["chain"],
        "primitives": FEATURES[ft]["primitives"],
        "required": required_attributes(ft),
        "attributes": {
            attr: {"lower": lo, "upper": up, "permitted_values": pv}
            for attr, (lo, up, pv) in BINDINGS.get(ft, {}).items()
        },
        "roles": {
            role: {"lower": lo, "upper": up, "targets": tgts, "association_class": ac}
            for role, (lo, up, tgts, ac, rt) in ASSOCS.get(ft, {}).items()
        },
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))


def main() -> int:
    ap = argparse.ArgumentParser(description="S-127 schema 查询（从要素目录确定性读取）")
    sub = ap.add_subparsers(dest="command")

    p = sub.add_parser("features", help="列出所有要素类")
    p.set_defaults(func=cmd_features)

    p = sub.add_parser("info", help="要素类完整信息")
    p.add_argument("feature_type")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("attrs", help="要素类属性列表")
    p.add_argument("feature_type")
    p.set_defaults(func=cmd_attrs)

    p = sub.add_parser("roles", help="要素类关联角色")
    p.add_argument("feature_type")
    p.set_defaults(func=cmd_roles)

    p = sub.add_parser("enum", help="枚举属性取值")
    p.add_argument("attribute")
    p.add_argument("--feature", help="限定到某要素类允许的子集")
    p.set_defaults(func=cmd_enum)

    p = sub.add_parser("complex", help="复合属性子属性")
    p.add_argument("attribute")
    p.set_defaults(func=cmd_complex)

    p = sub.add_parser("json", help="要素类完整 schema（JSON 输出）")
    p.add_argument("feature_type")
    p.set_defaults(func=cmd_json)

    args = ap.parse_args()
    if not args.command:
        ap.print_help()
        return 0
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
