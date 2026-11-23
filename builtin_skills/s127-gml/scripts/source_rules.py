"""Apply S-127 production rules to evidence extracted from a supplied source."""
from __future__ import annotations

from datetime import date, timedelta
import re

from coords import circle_surface
from s127_model import GEOGRAPHIC_FEATURES, binding, geometry_allowed, resolve_enum

AREA_KINDS = {"regulated-fairway", "bridge-waters", "bridge-information"}
KINDS = AREA_KINDS | {"routeing-measure", "reporting-obligation", "conditional-underkeel"}
GEOMETRY_KINDS = {"enc", "source-boundary", "source-circle", "chart-trace", "user-gml"}
RESTRICTION_SCOPES = {"area-wide", "conditional", "partial", "unverified"}
EVIDENCE_KINDS = {"regulatory-clause", "technical-data"}
CLAUSE_EXCEPTION = re.compile(r"除[^，。；;]{1,100}外(?=[，、,：:]|$)|\b(?:except|unless|exempt(?:ed)?)\b", re.I)
NESTED_SCOPE = re.compile(r"范围内的|\b(?:within the portion|only within)\b", re.I)
CLEARANCE_WIDTH_FIELD = re.compile(r"[「“\"](?:通航)?净空宽(?:度)?[」”\"]|\b(?:column|field)\s+(?:horizontal\s+)?clearance width\b", re.I)


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


def validity_dates(evidence):
    """Return the sourced effective, inclusive last-valid and exclusive expiry dates."""
    if not isinstance(evidence, dict) or not has_text(evidence.get("reference")):
        raise ValueError("validityBasis 需要明确的条款或公告出处")

    def get_date(field):
        if field not in evidence:
            return None
        value = evidence[field]
        try:
            parsed = date.fromisoformat(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field} 须为真实的 YYYY-MM-DD 日期") from exc
        if value != parsed.isoformat():
            raise ValueError(f"{field} 须为 YYYY-MM-DD 日期")
        return parsed

    start = get_date("effectiveDate")
    last = get_date("lastValidDate")
    expiry = get_date("expiryDate")
    if not (start or last or expiry):
        raise ValueError("有效期出处没有可核实的生效或结束日期")
    if "durationYears" in evidence:
        years = evidence["durationYears"]
        if start is None or type(years) is not int or years <= 0:
            raise ValueError("以整年推导期限须有明确生效日及正整数年数")
        try:
            anniversary = start.replace(year=start.year + years)
        except (OverflowError, ValueError) as exc:
            raise ValueError("无法直接确定周年届满日，请核实后填写明确终止日期") from exc
        if expiry is not None and expiry != anniversary:
            raise ValueError("明示的届满日与生效日及年数不符，待核对")
        expiry = anniversary
    one_day = timedelta(days=1)
    try:
        inconsistent_end = last and expiry and last + one_day != expiry
        inverted_range = start and ((last and start > last) or (expiry and start >= expiry))
        if inconsistent_end or inverted_range:
            raise ValueError("生效日、最后有效日或届满界点互相冲突，待核对")
        return start, last or (expiry - one_day if expiry else None), expiry or (last + one_day if last else None)
    except OverflowError as exc:
        raise ValueError("有效期超出可表达的日期范围") from exc


def apply_validity(owner, key):
    if "validityBasis" not in owner:
        return None
    evidence = owner["validityBasis"]
    review = owner.get("sourceReview")
    if isinstance(review, list):
        review[:] = [entry for entry in review if not (isinstance(entry, dict) and entry.get("rule") == "validity")]
    owner["fixedDateRange"] = None
    try:
        start, last, expiry = validity_dates(evidence)
        convention = evidence.get("dateEndConvention")
        if convention not in (None, "last-valid-day", "expiry-boundary"):
            raise ValueError("dateEndConvention 须为 last-valid-day 或 expiry-boundary")
        owner["fixedDateRange"] = {"dateStart": start.isoformat()} if start else {}
        if last and convention is None:
            raise ValueError("已有期限依据，但尚需确认 GML dateEnd 采用最后有效日还是届满界点")
        end = last if convention == "last-valid-day" else expiry
        if end:
            owner["fixedDateRange"]["dateEnd"] = end.isoformat()
        if not owner["fixedDateRange"]:
            owner["fixedDateRange"] = None
    except ValueError as exc:
        reference = evidence.get("reference") if isinstance(evidence, dict) else None
        entry = {"reference": reference if has_text(reference) else f"{key} 有效期", "reason": str(exc), "rule": "validity"}
        review = owner.setdefault("sourceReview", [])
        if isinstance(review, list) and entry not in review:
            review.append(entry)
        return str(exc)
    return f"最后有效日 {last.isoformat()}；届满界点 {expiry.isoformat()}" if last else None


def select_restricted_area(feature, attrs):
    feature["type"] = "RestrictedAreaNavigational"
    for field in ("categoryOfRouteingMeasure", "categoryOfTrafficSeparationScheme",
                  "categoryOfNavigationLine", "dynamicResource"):
        attrs.pop(field, None)


def area_wide_review(feature, clause, code):
    evidence_kind = clause.get("evidenceKind")
    if evidence_kind == "technical-data":
        return "技术资料须与法规义务分开记录；全区限制需要现行适用的条款"
    if basis(feature).get("kind") == "bridge-information":
        if evidence_kind != "regulatory-clause" or (code == 37 and CLEARANCE_WIDTH_FIELD.search(clause["reference"])):
            return "桥梁技术资料需另有现行适用条款才能设置全区限制；桥孔净空宽度属于技术尺度"
    _, label = resolve_enum("restriction", code)
    if label.endswith("prohibited") and CLAUSE_EXCEPTION.search(clause["evidence"]):
        return "原文包含例外或附加条件，须在关联规则中保留，复核后再编码"
    if NESTED_SCOPE.search(clause["evidence"]) and not has_text(clause.get("extentBasis")):
        return "原文限定了局部范围，请核对目标范围，或以局部条款关联规则"
    return None


def apply_restriction_clauses(doc, *, allow_restriction_3=False):
    """Compile restrictions for explicit feature refs and retain limitations in linked rules."""
    clauses = doc.get("restrictionClauses", [])
    if not isinstance(clauses, list):
        raise ValueError("restrictionClauses 须为条款数组")
    features = {ref(feature): feature for feature in doc.get("features", [])}
    by_target = {}
    for index, clause in enumerate(clauses, start=1):
        if not isinstance(clause, dict) or not all(has_text(clause.get(field)) for field in ("reference", "evidence")):
            raise ValueError(f"第 {index} 条须填写原文出处 reference 及证据 evidence")
        scope = clause.get("scope")
        if not has_text(scope) or scope not in RESTRICTION_SCOPES:
            raise ValueError(f"第 {index} 条 scope 无效")
        targets = clause.get("targets")
        if not isinstance(targets, list) or not targets:
            raise ValueError(f"第 {index} 条须指定至少一个地理要素 ref")
        if any(not has_text(key) or features.get(key, {}).get("type") not in GEOGRAPHIC_FEATURES for key in targets):
            raise ValueError(f"第 {index} 条的 targets 须指向已有地理要素 ref")
        if len(set(targets)) != len(targets):
            raise ValueError(f"第 {index} 条的 targets 有重复 ref")
        code = clause.get("code")
        if scope == "area-wide":
            if type(code) is not int and not has_text(code):
                raise ValueError(f"第 {index} 条全区适用限制须填写 restriction 码或标签")
            code, _ = resolve_enum("restriction", code)
        elif "code" in clause:
            raise ValueError(f"第 {index} 条有条件、局部适用或未核实，不可填写全区 restriction 码")
        if "regulation" in clause and not has_text(clause["regulation"]):
            raise ValueError(f"第 {index} 条的 regulation 须为规则要素 ref")
        if "evidenceKind" in clause and (not has_text(clause["evidenceKind"]) or clause["evidenceKind"] not in EVIDENCE_KINDS):
            raise ValueError(f"第 {index} 条的 evidenceKind 无效")
        if "extentBasis" in clause and not has_text(clause["extentBasis"]):
            raise ValueError(f"第 {index} 条的 extentBasis 须描述完整覆盖目标的独立依据")
        for key in targets:
            by_target.setdefault(key, []).append((clause, code))

    notes = {}
    for key, feature in features.items():
        if key in by_target or basis(feature).get("kind") != "bridge-information":
            continue
        attrs = feature.get("attributes") or {}
        restriction = attrs.get("restriction")
        if restriction and not (isinstance(restriction, dict) and restriction.get("$nil")):
            attrs.pop("restriction")
            review = feature.setdefault("sourceReview", [])
            if not isinstance(review, list):
                raise ValueError(f"{key} 的 sourceReview 须为数组")
            item = {"reference": basis(feature).get("reference") or str(key),
                    "reason": "桥梁通告的限制编码缺少现行适用条款，待核实",
                    "rule": "restriction-scope"}
            if item not in review:
                review.append(item)
            notes[key] = item["reason"]

    for key, entries in by_target.items():
        feature = features[key]
        attrs = feature.get("attributes") or {}
        feature["attributes"] = attrs
        attrs.pop("restriction", None)
        prior_review = feature.get("sourceReview", [])
        if not isinstance(prior_review, list):
            raise ValueError(f"{key} 的 sourceReview 须为数组")
        review = [item for item in prior_review if not (isinstance(item, dict) and item.get("rule") == "restriction-scope")]
        codes = []
        for clause, code in entries:
            scope = clause["scope"]
            reason = area_wide_review(feature, clause, code) if scope == "area-wide" else None
            if scope == "unverified":
                reason = clause["evidence"]
            elif scope == "area-wide" and reason is None:
                ftype = feature["type"]
                candidate = "RestrictedAreaNavigational" if ftype in {"WaterwayArea", "CautionArea"} else ftype
                restriction = binding(candidate, "restriction")
                compatible = allow_restriction_3 and candidate == "RestrictedAreaNavigational" and code == 3
                if restriction and (compatible or restriction[2] is None or code in restriction[2]):
                    shape = (doc.get("geometries") or {}).get(feature.get("geometry"), {})
                    if candidate != ftype and shape and not geometry_allowed(candidate, shape.get("type")):
                        reason = "全域限制需要明确的范围几何，当前几何类型不适用"
                    else:
                        if candidate != ftype:
                            select_restricted_area(feature, attrs)
                        if code not in codes:
                            codes.append(code)
                        continue
            if reason is None:
                rule = features.get(clause.get("regulation"), {})
                text = rule.get("textContent") or (rule.get("attributes") or {}).get("textContent")
                linked = any(a.get("role") == "theRxN" and a.get("target") == ref(rule)
                             for a in (feature.get("associations") or []))
                if rule.get("type") not in {"Regulations", "Restrictions"} or not (text and linked):
                    reason = f"{clause['evidence'].rstrip('。')}。须保留条款正文并以 theRxN 关联相应的 Regulations 或 Restrictions"
            if reason is not None:
                review.append({"reference": clause["reference"], "reason": reason, "rule": "restriction-scope"})
        if codes:
            attrs["restriction"] = codes
        if review:
            feature["sourceReview"] = review
        else:
            feature.pop("sourceReview", None)
        references = "、".join(dict.fromkeys(clause["reference"] for clause, _ in entries))
        notes[key] = f"已按条款适用范围核对限制编码（{references}）"
    return notes


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
    detail = apply_validity(common, "common")
    if detail:
        note("common", detail)
    for feature in doc.get("features", []):
        key, ftype = ref(feature), feature.get("type")
        info = basis(feature)
        kind = info.get("kind")
        attrs = feature.get("attributes") or {}
        if "validityBasis" in feature:
            attrs.pop("fixedDateRange", None)
        detail = apply_validity(feature, key)
        if detail:
            note(key, detail)
        for owner in (feature, attrs):
            check_source_date(owner, key, feature.get("reportedDateBasis"))
        original_name = info.get("originalName")
        if has_text(info.get("reference")) and has_text(original_name):
            given = attrs.pop("featureName", feature.get("featureName"))
            if isinstance(given, list):
                names = [item for item in given if isinstance(item, dict) and item.get("language") != "zho"]
                feature["featureName"] = [*names, {"language": "zho", "name": original_name.strip()}]
            else:
                feature["featureName"] = {**(given if isinstance(given, dict) else {}), "zho": original_name.strip()}
            note(key, "中文名称按已核对的原文名称写入")
        if kind in AREA_KINDS and has_text(info.get("reference")):
            if ftype in {"RouteingMeasure", "WaterwayArea", "RestrictedAreaNavigational"}:
                if ftype != "RestrictedAreaNavigational":
                    select_restricted_area(feature, attrs)
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
    coverage = doc.get("coverageAreas", [])
    if isinstance(coverage, list):
        used.update(item["geometry"] for item in coverage if isinstance(item, dict) and isinstance(item.get("geometry"), str))
    for name in removed - used:
        pool.pop(name, None)
    return notes
