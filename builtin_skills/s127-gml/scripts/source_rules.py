"""Apply S-127 production rules to evidence extracted from a supplied source."""
from __future__ import annotations

from datetime import date, timedelta
import re
import unicodedata

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
CLEARANCE_HEIGHT = re.compile(r"净空高(?:度)?|垂直净空|\b(?:vertical clearance|headroom)\b", re.I)
OTHER_BRIDGE_FIELDS = re.compile(
    r"净空宽|设计(?:通航)?船型|(?:通航)?(?:桥)?孔数量|通航孔\s*\d+\s*个"
    r"|\b(?:clearance width|design vessel|\d+\s+navigable openings?)\b", re.I
)
BARE_HEIGHT = re.compile(r"^\s*\d+(?:\.\d+)?\s*(?:米|m\b)(?:\s*[（(][^）)]+[）)])?\s*$", re.I)
PROHIBITION = re.compile(r"禁止|不得|严禁|\b(?:prohibited|shall not|must not|may not)\b", re.I)
RESTRICTION_ACTIONS = {
    1: re.compile(r"锚泊|抛锚|\banchor(?:ing)?\b", re.I),
    7: re.compile(r"驶入|进入|入区|\b(?:entry|enter(?:ing)?)\b", re.I),
    25: re.compile(r"滞航|停船|\b(?:stop(?:ping)?|drift(?:ing)?)\b", re.I),
    28: re.compile(r"追越|\bovertak(?:e|ing)\b", re.I),
    30: re.compile(r"追越|并列|并行|\b(?:overtak(?:e|ing)|abreast)\b", re.I),
}


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


def information_texts(feature):
    content = feature.get("textContent") or (feature.get("attributes") or {}).get("textContent")
    for block in content if isinstance(content, list) else [content]:
        if not isinstance(block, dict):
            continue
        items = block.get("information")
        for item in items if isinstance(items, list) else [items]:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            for value in text.values() if isinstance(text, dict) else [text]:
                if has_text(value):
                    yield value


def normalized_quote(value):
    text = unicodedata.normalize("NFKC", value).casefold()
    text = re.sub(r"(?<!\d)\.|\.(?!\d)", "", text)
    return re.sub(r"[^\w.<>≤≥=±%+-]|_", "", text)


def contains_quote(feature, quote):
    key = normalized_quote(quote)
    return bool(key) and any(key in normalized_quote(text) for text in information_texts(feature))


def restriction_items(feature):
    raw = (feature.get("attributes") or {}).get("restriction")
    return raw if isinstance(raw, list) else [raw] if raw is not None else []


def restriction_code(value):
    if type(value) is int or has_text(value):
        try:
            return resolve_enum("restriction", value)[0]
        except ValueError:  # Leave invalid enums for the catalogue validator.
            pass
    return None


def contradictory_restrictions(feature):
    """Check prohibitions against exceptions in the same operative sentence on that object."""
    codes = {restriction_code(value) for value in restriction_items(feature)} & RESTRICTION_ACTIONS.keys()
    if not codes:
        return {}
    result = {}
    for text in information_texts(feature):
        for sentence in re.split(r"[。；;!?！？\n]|\.(?=\s|$)", text):
            exception, prohibition = CLAUSE_EXCEPTION.search(sentence), PROHIBITION.search(sentence)
            if not exception or not prohibition:
                continue
            if exception.start() < prohibition.start():
                separator = re.search(r"[,，:：]", sentence[exception.end():prohibition.start()])
                start = exception.end() + separator.end() if separator else exception.end()
                operative = sentence[start:]
            else:
                operative = sentence[:exception.start()]
            for code in sorted(codes):
                if RESTRICTION_ACTIONS[code].search(operative):
                    result.setdefault(code, sentence.strip())
    return result


def sanitize_bridge_clearance(records):
    clean = []
    for row in records if isinstance(records, list) else []:
        if not isinstance(row, dict):
            continue
        block = {}
        for language in ("eng", "zho"):
            value = row.get(language)
            if not has_text(value):
                continue
            segments = re.split(r"[；;\n]", value)
            heights = [part.strip() for part in segments if not OTHER_BRIDGE_FIELDS.search(part) and
                       (CLEARANCE_HEIGHT.search(part) or (len(segments) == 1 and BARE_HEIGHT.fullmatch(part)))]
            if heights:
                block[language] = "；".join(heights) if language == "zho" else "; ".join(heights)
        if block:
            clean.append(block)
    return clean


def prepare_bridge_clearances(doc):
    notes = {}
    for feature in doc.get("features", []):
        info = basis(feature)
        if info.get("kind") != "bridge-information" or not has_text(info.get("reference")):
            continue
        raw = info.get("verticalClearance") or []
        records = sanitize_bridge_clearance(raw)
        attrs = feature.get("attributes") or {}
        attrs.pop("information", None)
        attrs.pop("textContent", None)
        feature.pop("textContent", None)
        if records:
            feature["textContent"] = [{"categoryOfText": "extract", "information": [
                {"text": item} for item in records
            ]}]
        if not records:
            notes[ref(feature)] = "未提供可明确识别的通航净空高度，待核对"
        elif records != raw:
            notes[ref(feature)] = "净空字段已保留可识别的高度、基面及适用桥孔；其他技术参数按原始资料查阅"
    return notes


def area_wide_review(feature, clause, code):
    evidence_kind = clause.get("evidenceKind")
    if evidence_kind == "technical-data":
        return "技术资料须与法规义务分开记录；全区限制需要现行适用的条款"
    if basis(feature).get("kind") == "bridge-information":
        if evidence_kind != "regulatory-clause" or (code == 37 and CLEARANCE_WIDTH_FIELD.search(clause["reference"])):
            return "桥梁技术资料需另有现行适用条款才能设置全区限制；桥孔净空宽度属于技术尺度"
    _, label = resolve_enum("restriction", code)
    operative = clause.get("sourceQuote") or clause["evidence"]
    if label.endswith("prohibited") and CLAUSE_EXCEPTION.search(operative):
        return "原文包含例外或附加条件，须在关联规则中保留，复核后再编码"
    if NESTED_SCOPE.search(operative) and not has_text(clause.get("extentBasis")):
        return "原文限定了局部范围，请核对目标范围，或以局部条款关联规则"
    return None


def refresh_restriction_reviews(features, clauses):
    """Keep local codes consistent with their text and surface missing targets of quoted rules."""
    rule_links = {}
    for key, feature in features.items():
        if feature.get("type") in GEOGRAPHIC_FEATURES:
            for link in feature.get("associations") or []:
                if link.get("role") == "theRxN":
                    rule_links.setdefault(link.get("target"), set()).add(key)

    missing_targets = {}
    supported = {}
    for clause in clauses:
        quote = clause.get("sourceQuote") or clause["evidence"]
        for rule_key, linked in rule_links.items():
            rule = features.get(rule_key, {})
            if clause.get("regulation") == rule_key:
                supported.setdefault(rule_key, set()).update(clause["targets"])
            if rule.get("type") not in {"Regulations", "Restrictions"} or not contains_quote(rule, quote):
                continue
            supported.setdefault(rule_key, set()).update(clause["targets"])
            if clause["scope"] == "area-wide":
                for key in linked.difference(clause["targets"]):
                    missing_targets.setdefault(key, []).append(clause["reference"])
                supported[rule_key].update(linked)

    for key, feature in features.items():
        linked = rule_links.get(key, set())
        reviews = feature.get("sourceReview", [])
        if not isinstance(reviews, list):
            raise ValueError(f"{key} 的 sourceReview 须为数组")
        # A removed code must remain visible on subsequent geometry completion.
        review = [row for row in reviews if not (isinstance(row, dict) and row.get("rule") == "restriction-target")]
        conflicts = contradictory_restrictions(feature)
        if conflicts:
            attrs = feature["attributes"]
            kept = [item for item in restriction_items(feature) if restriction_code(item) not in conflicts]
            attrs["restriction"] = kept or {"$nil": True}
            reference = feature.get("clause") or basis(feature).get("reference") or str(key)
            for code, text in conflicts.items():
                item = {"reference": reference, "reason": f"已撤下与同一条文例外冲突的全区码 {code}。{text}",
                        "rule": "restriction-text"}
                if item not in review:
                    review.append(item)
        for reference in dict.fromkeys(missing_targets.get(key, [])):
            review.append({"reference": reference,
                           "reason": "已关联包含该原文的规则，适用清单未列此要素，须核对范围和 targets",
                           "rule": "restriction-target"})
        if clauses and feature.get("type") in {"Regulations", "Restrictions"} and feature.get("clause") and len(linked) > 1:
            uncovered = linked - supported.get(key, set())
            if uncovered and any(PROHIBITION.search(text) and any(
                    action.search(text) for action in RESTRICTION_ACTIONS.values()) for text in information_texts(feature)):
                review.append({"reference": feature["clause"],
                               "reason": f"多处水域共用此禁限规则，以下目标缺少可核对的原文范围：{', '.join(sorted(uncovered))}",
                               "rule": "restriction-target"})
        if review:
            feature["sourceReview"] = review
        else:
            feature.pop("sourceReview", None)


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
        if "sourceQuote" in clause and not has_text(clause["sourceQuote"]):
            raise ValueError(f"第 {index} 条的 sourceQuote 须为可核对的原文摘录")
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
        review = [item for item in prior_review if not (isinstance(item, dict)
                  and item.get("rule") in {"restriction-scope", "restriction-text"})]
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
                elif has_text(clause.get("sourceQuote")) and not contains_quote(rule, clause["sourceQuote"]):
                    reason = f"关联规则遗漏可核对的原文摘录，须保留其中的范围或条件。{clause['sourceQuote']}"
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
    refresh_restriction_reviews(features, clauses)
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
