"""S-127 生产约定层（指南规则），与要素目录权威层 s127_catalogue.py 配套。

分工：
  s127_catalogue.py  ——  **官方要素目录 S127FC.xml 生成**，不可手改。
                          要素类全集、属性全集、枚举 label→官方 code、
                          每个绑定的多重性与允许枚举子集、允许的几何图元、
                          关联角色与目标类型、继承链。回答「标准允许什么」。
  s127_model.py      ——  **手工维护**。《S-127数据生产与发布操作指南（修订版）》
                          与《S-127字段抽取标注规范》的中国生产约定，
                          外加 GML 序列化细节。回答「我们约定怎么填、怎么写出去」。

要素目录不定义 XML 元素顺序（它不是序列化格式）。实测：**要素目录的属性绑定顺序
与 13 个已入库生产数据集的元素顺序在 16/16 个要素类上完全一致**，因此直接采用。
"""

from s127_catalogue import (  # noqa: F401  (转出给使用方)
    ABSTRACT_TYPES,
    ASSOC_CLASSES,
    ASSOCS,
    ATTRIBUTES,
    BINDINGS,
    COMPLEX,
    ENUMS,
    FC_DATE,
    FC_VERSION,
    FEATURES,
    ROLES as FC_ROLES,
    allowed_attributes,
    allowed_roles,
    assoc,
    binding,
    is_repeatable,
    max_occurs,
    permitted_labels,
    required_attributes,
    sub_attributes,
)

# ---------------------------------------------------------------------------
# 命名空间与数据集头（S-100 Part 10b 交换集）
# ---------------------------------------------------------------------------

NS_ATTRS = (
    'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
    ' xmlns:xlink="http://www.w3.org/1999/xlink"'
    ' xmlns:gml="http://www.opengis.net/gml/3.2"'
    ' xmlns:s100="http://www.iho.int/s100gml/1.0"'
    ' xmlns:S127="http://www.iho.int/S127/gml/cs0/1.0"'
    ' xmlns="http://www.iho.int/S127/gml/cs0/1.0"'
)
SRS = "http://www.opengis.net/def/crs/EPSG/0/4326"

DATASET_IDENTIFICATION = [
    ("encodingSpecification", "S-100 Part 10b"),
    ("encodingSpecificationEdition", "1.0"),
    ("productIdentifier", "INT.IHO.S-127.1.0"),
    ("productEdition", "1.0.0"),
    ("applicationProfile", "1"),
    # datasetFileIdentifier / datasetTitle / datasetReferenceDate 由脚本插入
    ("datasetLanguage", "EN"),
    ("datasetTopicCategory", "transportation"),
    ("datasetPurpose", "base"),
]

# ---------------------------------------------------------------------------
# 要素分类
# ---------------------------------------------------------------------------

GEOGRAPHIC_FEATURES = {k for k, v in FEATURES.items() if v["kind"] == "geographic"}
INFORMATION_FEATURES = {k for k, v in FEATURES.items() if v["kind"] == "information"}
META_FEATURES = {k for k, v in FEATURES.items() if v["kind"] == "meta"}
ALL_FEATURES = GEOGRAPHIC_FEATURES | INFORMATION_FEATURES

# 关联类：不是要素，不能手工建成 feature（用 associations 的 permission/inclusion 表达）
ASSOCIATION_CLASSES = set(ASSOC_CLASSES)

# 可复用要素（指南 4.3）：不填 sourceIndication，便于跨来源重复关联
REUSABLE_BY_DEFAULT = {
    "Authority",
    "ContactDetails",
    "ServiceHours",
    "Applicability",
    "NonStandardWorkingDay",
}

# ---------------------------------------------------------------------------
# 抽取级别（《S-127字段抽取标注规范》）
# ---------------------------------------------------------------------------
# 必抽   —— 建了该要素就必须抽
# 条件抽 —— 原文出现相关信息才抽，未出现不填
# 按需   —— 可选，仅在能提高可用性时填
# 慎用   —— 现行生产规则明确不表达，或一般不建议创建
MUST = "必抽"
CONDITIONAL = "条件抽"
OPTIONAL = "按需"
DISCOURAGED = "慎用"

# 公共字段的抽取级别
EXTRACTION_LEVEL = {
    "featureName": MUST,
    "geometry": MUST,          # 仅地理要素
    "language": MUST,          # 仅 ContactDetails
    "fixedDateRange": CONDITIONAL,
    "sourceIndication": CONDITIONAL,
    "textContent": CONDITIONAL,
    "rxnCode": CONDITIONAL,
    "graphic": OPTIONAL,
    "onlineResource": OPTIONAL,
    "fileLocator": OPTIONAL,
    "fileReference": OPTIONAL,
    "categoryOfCommPref": DISCOURAGED,   # 除无线电通信外一般不填
}

# 规范明确"慎用/不抽"的要素类
DISCOURAGED_FEATURES = {
    "RadarRange": "无线电、雷达等通信设施覆盖范围不予表达（指南第 2 章）",
}

# ---------------------------------------------------------------------------
# 元素顺序
# ---------------------------------------------------------------------------
# 属性顺序 = 要素目录绑定顺序（实测与生产数据 16/16 一致）。
# 关联元素排在全部专有属性之后、geometry 之前 —— 这是生产数据的一致做法。
_HEAD_GEO = "s100:featureObjectIdentifier"

# 关联元素的输出顺序（生产数据观测顺序，未观测到的按要素目录顺序补在后面）
_ASSOC_ORDER_HINT = [
    "permission",
    "vslLocation",
    "isApplicableTo",
    "theApplicableRxN",
    "theRxN",
    "providesInformation",
    "theContactDetails",
    "controlAuthority",
    "theAuthority",
    "theOrganisation",
    "theServiceHours",
    "theShipReport",
    "theInformation",
    "reportTo",
    "mustBeFiledBy",
    "reptForTrafficServ",
    "consistsOf",
    "componentOf",
    "serviceProvider",
    "serviceArea",
    "positions",
]


def child_order(feature_type):
    """要素类的子元素输出顺序。"""
    attrs = [a for a in allowed_attributes(feature_type)]
    roles = allowed_roles(feature_type)
    ordered_roles = [r for r in _ASSOC_ORDER_HINT if r in roles]
    ordered_roles += [r for r in roles if r not in ordered_roles]
    out = []
    if feature_type in GEOGRAPHIC_FEATURES:
        out.append(_HEAD_GEO)
    out += attrs + ordered_roles
    if FEATURES.get(feature_type, {}).get("primitives"):
        out.append("geometry")
    return out


def composite_order(complex_attr):
    """复合属性的子元素顺序（要素目录 subAttributeBinding 顺序）。"""
    if complex_attr in _S100_COMPOSITE_ORDER:
        return list(_S100_COMPOSITE_ORDER[complex_attr])
    return sub_attributes(complex_attr)


# s100/ISO 命名空间的结构不在要素目录里，按 S-100 Part 10b 与真实数据补登记
_S100_COMPOSITE_ORDER = {
    "s100:featureObjectIdentifier": [
        "s100:agency",
        "s100:featureIdentificationNumber",
        "s100:featureIdentificationSubdivision",
    ],
}

# ---------------------------------------------------------------------------
# 日期与双语
# ---------------------------------------------------------------------------

# 需要包一层 <s100:date> 的日期型子属性（S100_TruncatedDate）
DATE_WRAPPED = {"dateStart", "dateEnd", "reportedDate", "dateFixed", "date", "sourceDate"}

# 指南 4.1：属性不具多重性时，中英双语填进同一格（CR 分隔）。
# 这组仅用于文档与提示；**是否合并由要素目录的多重性决定**，见 merges_bilingual()。
BILINGUAL_SCALARS = {
    "source",
    "noticeTimeText",
    "serviceAccessProcedure",
    "requirementsForMaintenanceOfListeningWatch",
    "contactInstructions",
    "deliveryPoint",
    "cityName",
    "administrativeDivision",
    "countryName",
    "postalCode",
    "nameOfResource",
    "onlineResourceDescription",
    "destination",
    "pilotQualification",
    "vesselPerformance",
    "siltationRate",
    "transmissionContent",
    "pilotRequest",
}


def is_single_valued(name: str) -> bool:
    """该属性在要素目录里是否处处 upper=1（未登记的按单值处理）。"""
    return max_occurs(name) == 1


def merges_bilingual(name, value) -> bool:
    """这个值是否应合并成「英文\\r中文」的单个元素。

    * `{"eng": …, "zho": …}` —— 明确的双语写法，无论多重性都合并
    * `["English", "中文"]`  —— 只在该属性 **upper=1** 时合并；
      可重复属性（communicationChannel、deliveryPoint 等）的列表按多个元素输出
    """
    if isinstance(value, dict):
        return bool(set(value) & {"eng", "zho"}) and all(
            isinstance(v, str) for v in value.values()
        )
    if isinstance(value, (list, tuple)):
        return (
            1 <= len(value) <= 2
            and all(isinstance(v, str) for v in value)
            and is_single_valued(name)
        )
    return False


# ---------------------------------------------------------------------------
# 关联：arcrole / xlink:title
# ---------------------------------------------------------------------------
# arcrole = http://www.iho.net/S-127/roles/<role>；
# xlink:title = 该角色所属的关联类名（要素目录的 association ref）。

ARCROLE_BASE = "http://www.iho.net/S-127/roles/"

# 角色 → 关联类名。优先用要素目录里绑定声明的 association；下表只补要素目录没给的。
_ROLE_TITLE_FALLBACK = {
    "theRxN": "AssociatedRxN",
    "theContactDetails": "SrvContact",
    "controlAuthority": "SrvControl",
    "reptForTrafficServ": "TrafficServRept",
    "consistsOf": "TrafficControlServiceAggregation",
    "componentOf": "TrafficControlServiceAggregation",
    "providesInformation": "AdditionalInformation",
    "positions": "SpatialAssociation",
}


def _build_role_title():
    out = {}
    for binds in ASSOCS.values():
        for role, (_lo, _up, _targets, assoc_class, _rt) in binds.items():
            if assoc_class and role not in out:
                out[role] = assoc_class
    for role, title in _ROLE_TITLE_FALLBACK.items():
        out.setdefault(role, title)
    for role in FC_ROLES:
        out.setdefault(role, role)
    return out


ROLE_TITLE = _build_role_title()
ASSOC_ELEMENTS = set(FC_ROLES)

# 互为反向的关联，构建器据此自动补齐对端
INVERSE_ROLES = {
    "consistsOf": "componentOf",
    "componentOf": "consistsOf",
    "providesInformation": "informationProvidedFor",
    "serviceProvider": "serviceArea",
}


def role_targets(feature_type, role):
    """要素目录声明的允许目标要素类清单；未声明返回 []。"""
    a = assoc(feature_type, role)
    return list(a[2]) if a else []


def can_host_role(feature_type, role) -> bool:
    return assoc(feature_type, role) is not None


# 有 controlAuthority 的要素类（要素目录决定）
CAN_HOST_CONTROL_AUTHORITY = {
    ft for ft in FEATURES if can_host_role(ft, "controlAuthority")
}

# 关联类型取值（要素目录枚举）
PERMISSION_TYPES = set(ENUMS.get("categoryOfRelationship", {}))
INCLUSION_TYPES = set(ENUMS.get("membership", {}))

# ---------------------------------------------------------------------------
# 几何
# ---------------------------------------------------------------------------

GEOMETRY_ALIASES = {
    "surface": "surface", "polygon": "surface", "area": "surface",
    "curve": "curve", "line": "curve", "linestring": "curve",
    "point": "point", "pt": "point",
}


def geometry_kind(spec):
    """几何定义 → 规范化种类（surface/curve/point）。"""
    raw = (spec.get("type") or "surface").lower()
    kind = GEOMETRY_ALIASES.get(raw)
    if kind is None:
        raise ValueError(f"未知几何类型 {raw!r}；可选 {sorted(set(GEOMETRY_ALIASES))}")
    return kind


def allowed_primitives(feature_type):
    return FEATURES.get(feature_type, {}).get("primitives") or []


def geometry_allowed(feature_type, kind) -> bool:
    prims = allowed_primitives(feature_type)
    return True if not prims else kind in prims


# ---------------------------------------------------------------------------
# 其他约定
# ---------------------------------------------------------------------------

LANGUAGES = {"eng", "zho"}      # ISO 639-3；数据集默认语言英文

TEXT_MAX_CHARS = 300            # 指南 5.2 / 标注规范：text 上限
DATASET_PREFIX = "127CN00"      # 中国官方机构产品前缀（指南 3.3.1）
DATASET_MIN_LEN = 8
DATASET_MAX_LEN = 17


def resolve_enum(attr, value, feature_type=None):
    """枚举取值 → (code, label)。value 可以是 label，也可以是官方 code 数值。

    给出 feature_type 时，会额外校验该取值是否在该要素类允许的枚举子集内
    （要素目录的 permittedValues 常常只允许全集的一部分）。
    """
    table = ENUMS.get(attr)
    if table is None:
        return None, value
    if isinstance(value, bool):
        return None, "true" if value else "false"

    label = None
    if isinstance(value, int):
        for lab, code in table.items():
            if code == value:
                label = lab
                break
        if label is None:
            raise ValueError(f"{attr}: 要素目录里没有 code {value}")
    else:
        key = str(value).strip()
        if key in table:
            label = key
        else:
            low = {k.lower(): k for k in table}
            if key.lower() in low:
                label = low[key.lower()]
            elif key.isdigit():
                return resolve_enum(attr, int(key), feature_type)
            else:
                raise ValueError(
                    f"{attr}: 非法枚举值 {value!r}；要素目录允许 {sorted(table)}"
                )

    if feature_type:
        allow = permitted_labels(feature_type, attr)
        if allow is not None and label not in allow:
            raise ValueError(
                f"{attr}={label!r} 不在 {feature_type} 允许的取值内；"
                f"要素目录只允许 {sorted(allow)}"
            )
    return table[label], label
