"""Apply S-127 production rules to evidence extracted from a supplied source."""
from __future__ import annotations

from coords import circle_surface
from s127_model import GEOGRAPHIC_FEATURES, geometry_allowed

AREA_KINDS = {"regulated-fairway", "bridge-waters", "bridge-information"}
KINDS = AREA_KINDS | {"routeing-measure", "reporting-obligation"}
GEOMETRY_KINDS = {"enc", "source-boundary", "source-circle", "chart-trace", "user-gml"}


def ref(feature):
    return feature.get("ref") or feature.get("id")


def has_text(value):
    return isinstance(value, str) and bool(value.strip())


def basis(feature):
    value = feature.get("sourceBasis")
    return value if isinstance(value, dict) else {}


def geometry_evidence(feature):
    value = feature.get("geometryEvidence")
    return value if isinstance(value, dict) else {}


def partition(feature):
    info = basis(feature)
    value = info.get("partitionGroup")
    return value.strip() if has_text(value) and has_text(info.get("reference")) else None


def source_review(owner):
    items = owner.get("sourceReview", [])
    if not isinstance(items, list) or any(
        not isinstance(item, dict) or not has_text(item.get("reference")) or not has_text(item.get("reason"))
        for item in items
    ):
        raise ValueError("sourceReview 须逐项给出待核对事项的 reference 和 reason")
    return [f"{item['reference']}：{item['reason']}" for item in items]


def normalize_draft(doc, *, allow_restriction_3=False):
    notes = {}

    def note(key, message):
        notes[key] = "；".join(filter(None, (notes.get(key), message)))

    def check_source_date(owner, key, evidence):
        source = owner.get("sourceIndication")
        if isinstance(source, dict) and source.get("reportedDate") and not has_text(evidence):
            source.pop("reportedDate")
            note(key, "发布日期无明确出处，已留空；请核对通告日期")

    common = doc.get("common") or {}
    check_source_date(common, "common", common.get("reportedDateBasis"))
    for feature in doc.get("features", []):
        key, ftype = ref(feature), feature.get("type")
        info = basis(feature)
        kind = info.get("kind")
        attrs = feature.get("attributes") or {}
        for owner in (feature, attrs):
            check_source_date(owner, key, feature.get("reportedDateBasis"))
        if kind in AREA_KINDS and has_text(info.get("reference")):
            if ftype in {"RouteingMeasure", "WaterwayArea", "RestrictedAreaNavigational"}:
                if ftype != "RestrictedAreaNavigational":
                    feature["type"] = "RestrictedAreaNavigational"
                    # These fields described the previous classification only.
                    for field in ("categoryOfRouteingMeasure", "categoryOfTrafficSeparationScheme",
                                  "categoryOfNavigationLine", "dynamicResource"):
                        attrs.pop(field, None)
                    note(key, "按有出处的范围用途改为航行限制区")
                feature["attributes"] = attrs
                if kind == "bridge-information":
                    # In a bridge information notice only carry clearance extracted
                    # as such; quoted datum and distinct spans stay in the value.
                    records = [item for item in (info.get("verticalClearance") or []) if
                               isinstance(item, dict) and all(has_text(v) for v in item.values())
                               and set(item) <= {"eng", "zho"} and item]
                    attrs.pop("information", None)
                    attrs.pop("textContent", None)
                    feature.pop("textContent", None)
                    if records:
                        feature["textContent"] = [{"categoryOfText": "extract", "information": [
                            {"text": item} for item in records
                        ]}]
                    else:
                        note(key, "未提供有出处的通航净空高度，不推定数值")

        if feature.get("type") != "RestrictedAreaNavigational" or allow_restriction_3:
            continue
        values = attrs.get("restriction")
        values = values if isinstance(values, list) else [values]
        is_fishing = lambda value: str(value).strip().lower() in {"3", "fishing prohibited"}
        if any(is_fishing(value) for value in values):
            attrs["restriction"] = [item for item in values if not is_fishing(item)] or {"$nil": True}
            text = feature.pop("textContent", None)
            if "textContent" in attrs:
                text = attrs.pop("textContent")
            text = text or [{"categoryOfText": "abstract or summary"}]
            text = text if isinstance(text, list) else [text]
            items = text[0].get("information") or []
            items = items if isinstance(items, list) else [items]
            items.append({"text": {"eng": "Fishing operations are prohibited.", "zho": "禁止捕捞作业。"}})
            text[0]["information"] = items
            feature["textContent"] = text
            note(key, "要素目录不允许此类限制区使用码 3，保留禁捕捞条文文字")
    return notes


def prepare_geometries(doc):
    """Keep only spatial evidence that can be traced; create circles from source parameters."""
    pool = doc.setdefault("geometries", {})
    removed, notes = set(), {}
    for feature in doc.get("features", []):
        ftype = feature.get("type")
        if ftype not in GEOGRAPHIC_FEATURES:
            continue
        key, existing = ref(feature), feature.get("geometry")
        evidence = geometry_evidence(feature)
        kind = evidence.get("kind")
        if kind == "source-circle" and has_text(evidence.get("reference")):
            try:
                center, radius = evidence["center"], evidence["radiusMetres"]
                if (not isinstance(center, list) or len(center) != 2 or
                    not -90 < center[0] < 90 or not -180 <= center[1] <= 180 or
                    not geometry_allowed(ftype, "surface")):
                    raise ValueError("不支持该圆心或要素类型")
                geometry = circle_surface(center, radius)
            except (KeyError, TypeError, ValueError):
                notes[key] = "圆形范围缺少有效的原文圆心或米制半径，几何待补"
            else:
                # Stable key across draft/complete, never overwrite another geometry.
                name = existing if existing in pool and pool[existing] == geometry else f"source-circle-{key}"
                while name in pool and pool[name] != geometry:
                    name += "-new"
                pool[name] = geometry
                feature["geometry"] = name
                if existing and existing != name:
                    removed.add(existing)
                continue
        elif kind in GEOMETRY_KINDS and has_text(evidence.get("reference")):
            if existing and kind == "chart-trace" and not has_text(evidence.get("reviewReference")):
                feature["geometryStatus"] = "review"
            continue
        if existing:
            removed.add(feature.pop("geometry"))
            if key not in notes:
                notes[key] = "已移除无可核对依据的几何；请上传准确 GML 或提供来源"
        feature.pop("geometryStatus", None)
    used = {feature.get("geometry") for feature in doc.get("features", [])}
    for name in removed - used:
        pool.pop(name, None)
    return notes
