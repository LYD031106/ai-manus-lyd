#!/usr/bin/env python3
"""要素清单 JSON 校验（构建 GML 之前跑）。

用法：
    python validate_featureset.py featureset.json
    python validate_featureset.py featureset.json --strict     # 告警也算失败

两级判定的依据是**证据强度**，不是"XSD 说了什么"：

  ERROR —— 违反官方要素目录 S127FC.xml，或会真出问题的：
           要素类/属性/角色不存在（含拼写纠正）、枚举值不在要素目录取值内、
           超出多重性上限、用了该要素类不允许的枚举子集、关联挂错要素类或指向错类型、
           几何图元类型不符、关联目标不存在、坐标越界、数据集命名违规、
           单段 text 超 300 字符。

  WARN  —— 指南/标注规范的约定，或需要人工确认的：中英未成对、displayName 约定、
           可复用要素填了来源、条款序号残留、命中不表达清单、经纬度疑似写反、
           要素目录标为必填但原文确实没有的字段。

  一句话：**原文有就填，没有就不填** —— 不会因为"某个可选属性没填"而卡住；
  但写了要素目录里不存在的东西一定报错。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from s127_model import (  # noqa: E402
    ALL_FEATURES,
    ASSOCIATION_CLASSES,
    ASSOC_ELEMENTS,
    CAN_HOST_CONTROL_AUTHORITY,
    COMPLEX,
    DATASET_MAX_LEN,
    DATASET_MIN_LEN,
    DATASET_PREFIX,
    DISCOURAGED_FEATURES,
    ENUMS,
    FC_VERSION,
    FEATURES,
    GEOGRAPHIC_FEATURES,
    INCLUSION_TYPES,
    LANGUAGES,
    PERMISSION_TYPES,
    REUSABLE_BY_DEFAULT,
    ROLE_TITLE,
    TEXT_MAX_CHARS,
    allowed_attributes,
    allowed_primitives,
    allowed_roles,
    assoc as catalogue_assoc,
    binding,
    geometry_allowed,
    geometry_kind,
    merges_bilingual,
    permitted_labels,
    required_attributes,
    resolve_enum,
    role_targets,
)

CJK = re.compile(r"[㐀-鿿　-〿＀-￯]")
CLAUSE_NO = re.compile(r"第[一二三四五六七八九十百零〇\d]+条|^\s*\d+\.\d+(\.\d+)?\s")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# 指南 6.2：明确不作 S-127 表达的内容
NOT_EXPRESSED = [
    (r"海底(电缆|管道)|水下管线", "指南 6.2(1)：《海底电缆管道保护规定》相关条文视作从业者常识，不作 S-127 表达"),
    (r"(过驳|水上|水下)作业.{0,12}(应|须|需).{0,10}(申报|核查)", "指南 6.2(2)：海上作业申报规定不属于 S-127 表达域"),
    (r"(进出港|入港|航行).{0,8}申报|报港手续", "指南 6.2(3)：申报类条文行为主体非船舶，不作表达"),
    (r"(视程|能见度).{0,12}(小于|低于).{0,20}(实施|禁止).{0,10}(单向通航|进出港)",
     "指南 6.2(4)：由指挥中心/VTS 研判下达的临时交通管制措施不作表达"),
]

errors: list[str] = []
warns: list[str] = []


def err(where: str, msg: str) -> None:
    errors.append(f"{where}: {msg}")


def warn(where: str, msg: str) -> None:
    warns.append(f"{where}: {msg}")


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

def iter_texts(node, path="", out=None):
    if out is None:
        out = []
    if isinstance(node, dict):
        for k, v in node.items():
            iter_texts(v, f"{path}/{k}", out)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            iter_texts(v, f"{path}[{i}]", out)
    elif isinstance(node, str):
        out.append((path, node))
    return out


def count_of(name: str, value) -> int:
    """这份 JSON 展开后会产生几个 XML 元素。

    要素清单里有两种"一处写法 → 一个元素"的速记，计数时必须还原：
      * 双语单值属性 ["English", "中文"] → CR 连接成 **1** 个元素
      * featureName {"eng":…, "zho":…}   → **2** 个元素（每语一条）
    """
    if value in (None, "", {}, []):
        return 0
    if merges_bilingual(name, value):
        return 1
    if name == "featureName":
        if isinstance(value, list):
            return len(value)
        if isinstance(value, dict) and set(value) & {"eng", "zho"}:
            return len([k for k in ("eng", "zho") if value.get(k)])
        return 1
    return len(value) if isinstance(value, list) else 1


def is_bilingual_shorthand(name: str, value) -> bool:
    """是否是双语/名称速记，不应按复合属性向下钻。"""
    if not isinstance(value, (dict, list, tuple)):
        return False
    if merges_bilingual(name, value):
        return True
    if name in ("featureName", "headline", "text"):
        return isinstance(value, dict) and bool(set(value) & {"eng", "zho"})
    return False


# ---------------------------------------------------------------------------
# 要素目录层校验（官方 S127FC.xml）
# ---------------------------------------------------------------------------

def check_against_catalogue(where: str, ftype: str, present: dict) -> None:
    """present: 子元素名 -> 实例个数。按官方要素目录校验存在性与多重性。"""
    allowed = set(allowed_attributes(ftype)) | set(allowed_roles(ftype)) | {
        "s100:featureObjectIdentifier", "geometry"}
    for name, n in present.items():
        if name.startswith("$"):
            continue
        if name not in allowed:
            near = [a for a in allowed if a.lower() == name.lower()]
            if near:
                err(where, f"{ftype} 没有子元素 {name!r}，是否想写 {near[0]!r}？")
            else:
                err(where, f"要素目录里 {ftype} 没有 {name!r}；"
                           f"该要素类允许的属性见 references/02-要素字典.md")
            continue
        # 属性查 BINDINGS，关联角色查 ASSOCS
        limit = None
        b = binding(ftype, name)
        if b:
            limit = b[1]
        else:
            a = catalogue_assoc(ftype, name)
            if a:
                limit = a[1]
        if limit is not None and limit != -1 and n > limit:
            err(where, f"{name} 出现 {n} 次，要素目录上限 {limit} 次")
    for name in required_attributes(ftype):
        if present.get(name, 0) < 1:
            warn(where, f"要素目录把 {ftype}/{name} 标为必填（lower>=1），当前缺失；"
                        f"原文确实没有对应内容时，按 DCEG 2.4 应写成 "
                        f'"{name}": {{"$nil": true}}（显式空值），而不是整个省略')


def check_enums(where: str, node, owner: str | None = None, ftype: str | None = None) -> None:
    """枚举取值合法性 + 该要素类允许的枚举子集（要素目录 permittedValues）。"""
    if isinstance(node, dict):
        for key, value in node.items():
            if key.startswith("$"):
                continue
            if key in ENUMS and not isinstance(value, dict):
                for item in (value if isinstance(value, list) else [value]):
                    try:
                        resolve_enum(key, item, ftype if owner is None else None)
                    except ValueError as exc:
                        err(f"{where}/{key}", str(exc))
            elif isinstance(value, (dict, list)):
                check_enums(f"{where}/{key}", value, key, ftype)
    elif isinstance(node, list):
        for item in node:
            check_enums(where, item, owner, ftype)


def check_composite(where: str, complex_attr: str | None, value: dict) -> None:
    """复合属性的子元素存在性与多重性（要素目录 subAttributeBinding）。"""
    if not complex_attr or complex_attr not in COMPLEX:
        return
    subs = {s[0]: (s[1], s[2]) for s in COMPLEX[complex_attr]}
    for key, sub in value.items():
        if key.startswith("$"):
            continue
        if key not in subs:
            near = [a for a in subs if a.lower() == key.lower()]
            hint = f"（是否想写 {near[0]!r}？）" if near else ""
            err(where, f"要素目录里 {complex_attr} 没有子属性 {key!r}{hint}")
            continue
        lo, up = subs[key]
        n = count_of(key, sub)
        if up != -1 and n > up:
            err(f"{where}/{key}", f"出现 {n} 次，要素目录上限 {up} 次")
        if is_bilingual_shorthand(key, sub):
            continue
        for item in (sub if isinstance(sub, list) else [sub]):
            if isinstance(item, dict) and not is_bilingual_shorthand(key, item):
                check_composite(f"{where}/{key}", key, item)
    for key, (lo, _up) in subs.items():
        if lo >= 1 and count_of(key, value.get(key)) < 1:
            warn(where, f"要素目录把 {complex_attr}/{key} 标为必填，当前缺失")


# ---------------------------------------------------------------------------
# 指南层校验
# ---------------------------------------------------------------------------

def check_dataset(ds: dict) -> None:
    where = "dataset"
    name = ds.get("name", "")
    if not name:
        err(where, "缺少 name")
        return
    if not name.startswith(DATASET_PREFIX):
        err(where, f"name 必须以 {DATASET_PREFIX} 开头（中国官方机构产品），现为 {name!r}")
    if not DATASET_MIN_LEN <= len(name) <= DATASET_MAX_LEN:
        err(where, f"name 长度须在 {DATASET_MIN_LEN}~{DATASET_MAX_LEN} 之间，现为 {len(name)}")
    if not re.fullmatch(r"[A-Za-z0-9_]+", name):
        err(where, f"name 只允许字母、数字、下划线（指南 3.3.1），现为 {name!r}")
    if ds.get("referenceDate") and not DATE.match(str(ds["referenceDate"])):
        err(where, "referenceDate 需为 YYYY-MM-DD")
    title = ds.get("title")
    if title and title != name:
        warn(where, f"datasetTitle({title!r}) 与 name({name!r}) 不一致 —— "
                    f"文件名、datasetFileIdentifier、datasetTitle 三者应一致，"
                    f"否则归档与系统核对时容易对不上")


def check_date_range(where: str, fdr) -> None:
    if not isinstance(fdr, dict):
        err(where, "fixedDateRange 需为对象")
        return
    check_composite(f"{where}/fixedDateRange", "fixedDateRangeType", fdr)
    start, end = fdr.get("dateStart"), fdr.get("dateEnd")
    for key, value in (("dateStart", start), ("dateEnd", end)):
        if value and not DATE.match(str(value)):
            err(where, f"{key} 需为 YYYY-MM-DD，现为 {value!r}")
    if start and end and str(start) > str(end):
        err(where, f"dateStart({start}) 晚于 dateEnd({end})")


def check_source_indication(where: str, si) -> None:
    if not isinstance(si, dict):
        err(where, "sourceIndication 需为对象")
        return
    check_composite(f"{where}/sourceIndication", "sourceIndicationType", si)
    for key in ("categoryOfAuthority", "countryName", "reportedDate", "source", "sourceType"):
        if not si.get(key):
            warn(where, f"sourceIndication 缺少 {key}（XSD 可选，但指南 4.4 要求填写）")
    if si.get("reportedDate") and not DATE.match(str(si["reportedDate"])):
        err(where, "sourceIndication.reportedDate 需为 YYYY-MM-DD")
    src = si.get("source")
    if isinstance(src, str) and CJK.search(src) and not re.search(r"[\r\n]", src):
        warn(where, "sourceIndication.source 似只填了中文；指南 4.4 要求中英文同时填入（先英后中）")
    fn = si.get("featureName")
    if isinstance(fn, dict):
        if not fn.get("eng") or not fn.get("zho"):
            warn(where, "sourceIndication.featureName 应有中英文两条（发文机构名）")
        if fn.get("displayName"):
            warn(where, "sourceIndication.featureName 的 displayName 约定为空（指南 4.4）")


def check_feature_name(where: str, ftype: str, fn, only_lang: str | None = None) -> None:
    if isinstance(fn, list):
        langs = [x.get("language") for x in fn]
        for lang in langs:
            if lang not in LANGUAGES:
                err(where, f"featureName.language 须为 eng/zho，现为 {lang!r}")
        for item in fn:
            if item.get("language") == "eng" and item.get("displayName"):
                warn(where, "英文 featureName 的 displayName 约定不选（指南 4.3）")
        if only_lang:
            if any(lang != only_lang for lang in langs):
                warn(where, f"ContactDetails 的属性只用 language={only_lang} 填写（指南 4.1、5.3）")
        elif "eng" not in langs or "zho" not in langs:
            warn(where, "featureName 约定中英文成对（指南 4.1）")
        return
    if not isinstance(fn, dict):
        err(where, "featureName 需为 {'eng':…, 'zho':…}")
        return
    if only_lang:
        other = "zho" if only_lang == "eng" else "eng"
        if not fn.get(only_lang):
            err(where, f"ContactDetails 的 language={only_lang}，featureName 必须有 {only_lang} 名称")
        if fn.get(other):
            warn(where, f"ContactDetails 的非枚举属性只用 language={only_lang} 填写；"
                        f"双语服务需分别创建 eng/zho 两个 ContactDetails（指南 5.3）")
    else:
        if not fn.get("eng"):
            warn(where, "featureName 缺少英文名（数据集默认语言为英文，指南 4.1）")
        if not fn.get("zho"):
            warn(where, "featureName 缺少中文名（指南 4.1 约定中英成对）")
    if ftype == "Applicability" and fn.get("displayName"):
        warn(where, "APPLIC 要素中英文 displayName 均不选（指南 4.3、5.4）")
    eng = fn.get("eng") or ""
    if CJK.search(eng):
        warn(where, f"英文 featureName 含中文字符：{eng!r}")
    if eng and eng[:1].islower():
        warn(where, f"英文 featureName 约定词首大写（指南 4.3）：{eng!r}")
    for num in re.findall(r"(?<![\d,.])\d{4,}(?![\d,])", eng):
        warn(where, f"英文名称中的数值应用千分位区隔（指南 4.3）：{num}")


def check_texts(where: str, feat: dict) -> None:
    text_keys = {"text", "information", "noticeTimeText", "serviceAccessProcedure"}
    for path, text in iter_texts(
        {"textContent": feat.get("textContent"), "attributes": feat.get("attributes")}
    ):
        if not {seg.split("[")[0] for seg in path.split("/")} & text_keys:
            continue
        longest = max((len(p) for p in re.split(r"[\r\n]", text)), default=0)
        if longest > TEXT_MAX_CHARS:
            err(f"{where}{path}", f"单段 text 长度 {longest} 超过 {TEXT_MAX_CHARS} 字符上限，"
                                  f"请拆成多个 information（指南 5.2）")
        elif len(text) > TEXT_MAX_CHARS:
            warn(f"{where}{path}", f"text 总长 {len(text)} 超过 {TEXT_MAX_CHARS} 字符，已按 CR 分行；"
                                   f"如导入系统被截断请拆成多个 information（指南 5.2）")
        if CLAUSE_NO.search(text):
            warn(f"{where}{path}", "text 中出现条款/段落序号，指南 5.2 约定不标示（应记入生产记录表）")
        for pattern, reason in NOT_EXPRESSED:
            if re.search(pattern, text):
                warn(f"{where}{path}", f"疑似不应表达的内容——{reason}")
        first = re.split(r"[\r\n]", text)[0]
        if CJK.search(first) and not CJK.search(text[:1]):
            warn(f"{where}{path}", "英文文本中混有中文字符，请核对英译")


def check_text_content(where: str, tcs) -> None:
    if tcs is None:
        return
    if isinstance(tcs, dict):
        tcs = [tcs]
    for i, tc in enumerate(tcs):
        tag = f"{where}/textContent[{i}]"
        check_composite(tag, "textContentType", tc)
        infos = tc.get("information")
        if infos is None:
            warn(tag, "textContent 没有 information，无内容可表达")
            continue
        if isinstance(infos, dict):
            infos = [infos]
        if not tc.get("categoryOfText"):
            warn(tag, "text 有内容时 categoryOfText 必选（指南 4.5）")
        for j, info in enumerate(infos):
            if "language" in info:
                if info["language"] not in LANGUAGES:
                    err(f"{tag}/information[{j}]", f"language 须为 eng/zho：{info['language']!r}")
                continue
            def has(lang):
                if isinstance(info.get(lang), str):
                    return True
                t = info.get("text")
                return isinstance(t, dict) and bool(t.get(lang))
            if not (has("eng") and has("zho")):
                warn(f"{tag}/information[{j}]", "information 约定中英文成对（指南 4.1）")


def check_geometry(name: str, spec: dict) -> str | None:
    where = f"geometries/{name}"
    try:
        kind = geometry_kind(spec)
    except ValueError as exc:
        err(where, str(exc))
        return None
    if kind == "point":
        rings = [[spec.get("coordinates") or spec.get("position")]]
    elif kind == "curve":
        rings = [spec.get("coordinates") or spec.get("exterior")]
    else:
        rings = [spec.get("exterior") or spec.get("coordinates")] + list(spec.get("interiors", []))
    for ring in rings:
        if not ring:
            err(where, "缺少坐标")
            continue
        for pos in ring:
            if not isinstance(pos, (list, tuple)) or len(pos) < 2:
                err(where, f"坐标点格式应为 [lat, lon]：{pos!r}")
                continue
            lat, lon = float(pos[0]), float(pos[1])
            if not -90 <= lat <= 90:
                err(where, f"纬度越界 {lat}；坐标顺序须为 [纬度, 经度]")
            if not -180 <= lon <= 180:
                err(where, f"经度越界 {lon}")
            if abs(lat) > 60 and abs(lon) < 60:
                warn(where, f"[{lat}, {lon}] 疑似经纬度写反（我国沿海纬度 3~54、经度 73~136）")
    if kind == "surface":
        uniq = {tuple(p) for p in (rings[0] or []) if isinstance(p, (list, tuple))}
        if len(uniq) < 3:
            err(where, f"面要素外环至少需要 3 个不同顶点，现有 {len(uniq)}")
    return kind


# ---------------------------------------------------------------------------

def validate(doc: dict) -> None:
    check_dataset(doc.get("dataset") or {})

    common = doc.get("common") or {}
    if "fixedDateRange" in common:
        check_date_range("common", common["fixedDateRange"])
    if "sourceIndication" in common:
        check_source_indication("common", common["sourceIndication"])
    check_enums("common", common)

    geoms = doc.get("geometries") or {}
    geom_kinds = {}
    for name, spec in geoms.items():
        geom_kinds[name] = check_geometry(name, spec)

    features = doc.get("features") or []
    if not features:
        err("features", "要素清单为空")
        return

    refs: dict[str, dict] = {}
    for feat in features:
        ref = feat.get("ref") or feat.get("id")
        if not ref:
            err("features", f"要素缺少 ref：{feat.get('type')}")
            continue
        if ref in refs:
            err(f"features/{ref}", "ref 重复")
        refs[ref] = feat

    has_authority = any(f.get("type") == "Authority" for f in features)
    used_geoms: set[str] = set()

    for ref, feat in refs.items():
        where = f"features/{ref}"
        ftype = feat.get("type")
        if ftype in ASSOCIATION_CLASSES:
            err(where, f"{ftype} 是关联类，不能当要素建；"
                       f"在 associations 里写 permission/inclusion，用 build_gml.py --assoc-objects 生成")
            continue
        if ftype not in ALL_FEATURES:
            err(where, f"S127.xsd 中没有要素类 {ftype!r}；见 references/02-要素字典.md")
            continue

        attrs = feat.get("attributes") or {}
        assocs = feat.get("associations") or []
        roles = [a.get("role") for a in assocs]

        # ---- 汇总实例，交给 XSD 层校验 ----
        present: dict[str, int] = {}
        reusable = feat.get("reusable", ftype in REUSABLE_BY_DEFAULT)
        if "featureName" in feat:
            fn = feat["featureName"]
            present["featureName"] = len(fn) if isinstance(fn, list) else \
                len([k for k in ("eng", "zho") if isinstance(fn, dict) and fn.get(k)])
        for key in ("fixedDateRange", "sourceIndication"):
            value = feat.get(key, None if reusable else common.get(key))
            if value:
                present[key] = 1
        if feat.get("textContent"):
            present["textContent"] = count_of("textContent", feat["textContent"])
        for key, value in attrs.items():
            present[key] = present.get(key, 0) + count_of(key, value)
        for role in roles:
            if role:
                present[role] = present.get(role, 0) + 1
        if feat.get("geometry"):
            present["geometry"] = 1
        check_against_catalogue(where, ftype, present)

        # ---- featureName ----
        if "featureName" not in feat:
            warn(where, "缺少 featureName（XSD 可选，但要素没有名称无法使用）")
        else:
            only_lang = None
            if ftype == "ContactDetails":
                lang = attrs.get("language")
                only_lang = lang if lang in LANGUAGES else None
            check_feature_name(where, ftype, feat["featureName"], only_lang)

        # ---- 日期与来源 ----
        if feat.get("fixedDateRange"):
            check_date_range(where, feat["fixedDateRange"])
        if feat.get("sourceIndication"):
            check_source_indication(where, feat["sourceIndication"])
            if reusable:
                warn(where, "可复用要素约定不填 sourceIndication，便于跨来源重复关联（指南 4.3）")
        elif not reusable and not common.get("sourceIndication"):
            warn(where, "专属要素应有 sourceIndication（可放在 common 里共用，指南 4.3/5.1）")

        # ---- 属性内部结构与枚举 ----
        check_enums(where, attrs, None, ftype)
        for key, value in attrs.items():
            if is_bilingual_shorthand(key, value):
                continue
            for item in (value if isinstance(value, list) else [value]):
                if isinstance(item, dict) and not is_bilingual_shorthand(key, item):
                    check_composite(f"{where}/{key}", key, item)
        check_text_content(where, feat.get("textContent"))
        check_texts(where, feat)

        # ---- 关联 ----
        for i, assoc in enumerate(assocs):
            tag = f"{where}/associations[{i}]"
            role = assoc.get("role")
            if role not in ASSOC_ELEMENTS:
                err(tag, f"S127.xsd 中没有关联元素 {role!r}；可选 {sorted(ASSOC_ELEMENTS)}")
                continue
            target = assoc.get("target")
            if target not in refs:
                err(tag, f"关联目标 {target!r} 不存在")
                continue
            ttype = refs[target].get("type")
            if role not in allowed_roles(ftype):
                err(tag, f"要素目录里 {ftype} 不能挂 {role!r}；"
                         f"允许的角色：{sorted(allowed_roles(ftype))}")
                continue
            expected = role_targets(ftype, role)
            if expected:
                chain = FEATURES.get(ttype, {}).get("chain", [])
                # 目标可以是声明类型本身，也可以是它的子类（声明常指向抽象父类）
                if ttype not in expected and not (set(expected) & set(chain)):
                    err(tag, f"要素目录规定 {ftype}.{role} 指向 {sorted(expected)}，现指向 {ttype}")
            title = ROLE_TITLE.get(role, role)
            ptype, itype = assoc.get("permission"), assoc.get("inclusion")
            if title == "PermissionType":
                if not ptype:
                    warn(tag, "许可类型未指定（required/prohibited/…）；"
                              "不指定就既写不进 GML 也进不了补录清单")
                elif ptype not in PERMISSION_TYPES:
                    err(tag, f"非法许可类型 {ptype!r}；XSD 允许 {sorted(PERMISSION_TYPES)}")
            if title == "InclusionType":
                if not itype:
                    warn(tag, "包含类型未指定（included/excluded）")
                elif itype not in INCLUSION_TYPES:
                    err(tag, f"非法包含类型 {itype!r}；XSD 允许 {sorted(INCLUSION_TYPES)}")

        # ---- 几何 ----
        if ftype in GEOGRAPHIC_FEATURES:
            gname = feat.get("geometry")
            if not gname:
                warn(where, f"{ftype} 没有 geometry —— 地理要素通常需要范围；"
                            f"确实无范围可画时（如仅有名称、几何取自 ENC）可留空")
            elif gname not in geoms:
                err(where, f"geometries 中找不到 {gname!r}")
            else:
                used_geoms.add(gname)
                kind = geom_kinds.get(gname)
                if kind and not geometry_allowed(ftype, kind):
                    err(where, f"要素目录规定 {ftype} 只允许 {allowed_primitives(ftype)} 几何，"
                               f"现给的是 {kind}")
                # DCEG 5.16：报告点/线的方向语义，两种几何要求相反
                if ftype == "RadioCallingInPoint":
                    has_ori = bool(attrs.get("orientationValue"))
                    if kind == "point" and not has_ori:
                        warn(where, "DCEG 5.16：point 型报告点必须至少填一个 orientationValue；"
                                    "反向也适用时用 trafficFlow=two-way 表达，不要加反向的 orientationValue")
                    elif kind == "curve":
                        warn(where, "DCEG 5.16：curve 型报告线的数字化方向必须使「须报告的交通流方向在线的右侧」"
                                    "—— 坐标顺序写反语义即相反且无法自动检出，请对照海图确认")
            # 指南 5.3 要求地理要素关联唯一 Authority，但 S127.xsd 里
            # 只有部分要素类有 controlAuthority；其余只能间接获得主管机构。
            if ftype in CAN_HOST_CONTROL_AUTHORITY and roles.count("controlAuthority") == 0:
                if has_authority:
                    warn(where, "未关联 Authority（指南 5.3 要求地理要素关联唯一主管机构；"
                                "若在系统内补录可忽略）")
                else:
                    warn(where, "数据集内没有 Authority 要素；指南 5.3 要求地理要素关联唯一 Authority")
            elif ftype not in CAN_HOST_CONTROL_AUTHORITY:
                indirect = {"componentOf", "theRxN"} & set(roles)
                if not indirect:
                    warn(where, f"S127.xsd 中 {ftype} 没有 controlAuthority，主管机构只能间接获得："
                                f"componentOf 聚合到 VTSA/SRSA，或 theRxN 关联 Regulations，"
                                f"或在系统内关联；当前两条间接路径都没有")

        # ---- 各要素类的指南约定 ----
        if ftype == "Applicability":
            conds = 0
            for key in ("categoryOfVessel", "categoryOfCargo", "categoryOfVesselRegistry",
                        "categoryOfDangerousOrHazardousCargo"):
                conds += count_of(key, attrs.get(key))
            conds += count_of("vesselsMeasurements", attrs.get("vesselsMeasurements"))
            lc = attrs.get("logicalConnectives")
            if conds > 1 and not lc:
                warn(where, "多个条件并存时应给出 logicalConnectives（指南 5.4）")
            if conds <= 1 and lc:
                warn(where, "仅单一属性描述一类船舶时 logicalConnectives 应留空（指南 5.4）")
            if not conds and not attrs.get("information"):
                warn(where, "APPLIC 既无枚举参数也无 information，无法界定船舶类型（指南 5.4）")
        if ftype == "UnderkeelClearanceAllowanceArea" and not attrs.get("underkeelAllowance"):
            warn(where, "UCAA 没有 underkeelAllowance，等于没表达富余水深要求（指南 5.10）")
        if ftype in ("RestrictedAreaNavigational", "RestrictedAreaRegulatory") \
                and not attrs.get("restriction"):
            warn(where, "限制区/监管区通常需要 restriction；否则考虑改用 WaterwayArea 或信息要素（指南 5.11、5.14）")
        if ftype == "RouteingMeasure" and attrs.get("categoryOfRouteingMeasure") == "recommended route":
            if not any(ROLE_TITLE.get(a.get("role")) == "PermissionType" for a in assocs):
                warn(where, "推荐航路应关联渔船 APPLIC（沿海公共航路 not recommended／"
                            "渔船推荐航路 recommended，指南 7.5、7.6）")
        if ftype in ("Regulations", "Restrictions") and not feat.get("textContent"):
            warn(where, "规则类信息要素没有 textContent，等于没有条文内容（指南 5.2）")
        st = (feat.get("sourceIndication") or common.get("sourceIndication") or {}).get("sourceType")
        if ftype == "Regulations" and st and st != "law or regulation":
            warn(where, "Regulations 用于直属海事局及更高级别机构发文（law or regulation）；"
                        "地方海事局发文应改用 Restrictions（指南 5.2）")
        if ftype == "Restrictions" and st and st != "official publication":
            warn(where, "Restrictions 用于地方海事局及同级机构发文（official publication）；"
                        "直属海事局及以上应改用 Regulations（指南 5.2）")
        if ftype == "ContactDetails" and attrs.get("language") not in LANGUAGES:
            warn(where, "ContactDetails 应有唯一 language（eng 或 zho，指南 4.1、5.3）")

    for name in geoms:
        if name not in used_geoms:
            warn(f"geometries/{name}", "定义了但没有要素引用")


def main() -> int:
    ap = argparse.ArgumentParser(description="S-127 要素清单校验")
    ap.add_argument("featureset")
    ap.add_argument("--strict", action="store_true", help="把 WARN 也当作失败")
    args = ap.parse_args()

    doc = json.loads(Path(args.featureset).read_text(encoding="utf-8"))
    validate(doc)

    for e in errors:
        print(f"[ERROR] {e}")
    for w in warns:
        print(f"[WARN ] {w}")
    n_feat = len(doc.get("features") or [])
    print(f"\n共 {n_feat} 个要素；{len(errors)} 个 ERROR（会真出问题，必须修），"
          f"{len(warns)} 条 WARN（指南约定 / 待人工确认）")
    if errors:
        print("→ 请先修掉 ERROR 再构建 GML")
        return 1
    if warns and args.strict:
        return 1
    print("→ 校验通过，可以构建 GML")
    return 0


if __name__ == "__main__":
    sys.exit(main())
