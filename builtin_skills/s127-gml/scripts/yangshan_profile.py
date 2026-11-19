"""Production rules for MSA Announcement No. 16 of 2026 (Yangshan)."""
from __future__ import annotations

import re

from coords import circle_surface, extract
from s127_model import FEATURES, GEOGRAPHIC_FEATURES, allowed_roles, role_targets


PROFILE = "yangshan-announcement-16-2026"
TITLE = "上海洋山深水港区及其附近水域通航安全管理规定"
SOURCE = "Announcement No.16｜第16号公告"
PUBLISHED = "2026-08-21"
VALIDITY = {"dateStart": "2026-10-01", "dateEnd": "2031-10-01"}
REPORT_NAMES = (
    "LNG及低闪点气态燃料加注作业前报告", "经过洋山VTS报告线及报告点报告", "次日靠离泊及通过东海大桥计划报告",
    "抛锚、起锚、走锚及应急锚泊报告", "靠离码头动态报告", "事故及设备故障报告", "碍航物报告",
)
ORDINARY = (
    ("洋山港主航道", "Yangshan Port Main Fairway"), ("金山航道", "Jinshan Fairway"),
    ("金山内航道", "Jinshan Inner Fairway"), ("漕泾东航道", "Caojing East Fairway"),
    ("漕泾西航道", "Caojing West Fairway"), ("漕泾东航道支线航道", "Caojing East Branch Fairway"),
    ("漕泾东西航道连接通道", "Caojing East–West Connecting Fairway"),
    ("上海漕泾电厂码头连接航道", "Shanghai Caojing Power Plant Terminal Connecting Fairway"),
    ("临港主航道", "Lingang Main Fairway"), ("临港支航道", "Lingang Branch Fairway"),
    ("洋山港东支航道", "Yangshan Port East Branch Fairway"),
)
MAIN_SECTION = "洋山港主航道（外后门至小岩礁）"
CAUTION = "洋山警戒区"
A8_SECTION = "南支灯船至A8灯浮之间航道"
BRIDGE_SAFETY = "东海大桥桥区安全水域"
BRIDGE_SPAN = r"东海大桥[1-4]号通航孔航道"
GEOMETRY_EVIDENCE = {"enc", "source-boundary", "source-circle", "chart-trace", "user-gml"}
SOURCED_TYPES = {"RestrictedAreaNavigational", "Regulations", "VesselTrafficServiceArea", "WaterwayArea",
                 "UnderkeelClearanceAllowanceArea", "AnchorBerth", "AnchorageArea", "RadioCallingInPoint"}
# Closed boundaries published as ordered positions in Appendix 3. Digit groups
# are latitude / longitude in degrees, minutes and seconds (not decimal degrees).
ANCHORAGE_BOUNDARIES = {
    "洋山NO.1待泊、检疫锚地": (
        "30°28′00″N/122°32′48″E;30°28′00″N/122°28′31.50″E;30°30′00″N/122°28′31.50″E;"
        "30°30′00″N/122°30′24″E;30°29′33″N/122°32′48″E"),
    "洋山港内中小型船舶锚地": (
        "30°36′47.10″N/122°02′43.20″E;30°37′47.70″N/122°00′41.10″E;"
        "30°37′12.80″N/122°00′19.50″E;30°36′11.60″N/122°02′22.90″E"),
    "洋山NO.2应急锚地": (
        "30°38′11.40″N/122°00′37.20″E;30°38′36″N/121°59′54″E;"
        "30°38′12″N/121°59′34.20″E;30°37′46.80″N/122°00′18″E"),
    "临港锚地": (
        "30°48′59″N/121°47′12.90″E;30°49′00.40″N/121°49′43.30″E;"
        "30°48′15.80″N/121°49′43.90″E;30°48′14.40″N/121°47′13.40″E"),
    "金山NO.1锚地": (
        "30°38′50″N/121°19′28″E;30°40′54″N/121°18′22″E;"
        "30°40′45″N/121°19′47″E;30°39′16″N/121°20′31″E"),
    "金山NO.2锚地": (
        "30°37′54″N/121°20′00″E;30°35′50″N/121°21′06″E;"
        "30°36′20″N/121°22′40″E;30°38′24″N/121°21′36″E"),
    "金山NO.3锚地": (
        "30°42′42″N/121°24′36″E;30°41′00″N/121°27′12″E;30°42′30″N/121°27′36″E;"
        "30°43′30″N/121°25′54″E;30°43′36″N/121°25′06″E"),
    "金山NO.4锚地": (
        "30°39′30″N/121°21′48″E;30°39′12″N/121°20′36″E;"
        "30°40′42″N/121°19′48″E;30°41′00″N/121°21′00″E"),
}


def names(feature):
    value = feature.get("featureName") or {}
    return ({entry.get("language"): entry.get("name") for entry in value}
            if isinstance(value, list) else value)


def chinese(feature):
    return (names(feature).get("zho") or "").strip()


def ref(feature):
    return feature.get("ref") or feature.get("id")


def ordinary_name(name):
    if re.fullmatch(r"洋山港主航道\s*[（(]?\s*外后门至小岩礁(?:段|正横水域)?\s*[）)]?", name):
        return MAIN_SECTION
    if name in {"漕泾东航道支线", "漕泾东支线"}:
        return ORDINARY[5][0]
    for title, _ in ORDINARY:
        if name == title or re.fullmatch(re.escape(title) + r"(?:第?[一二三123]段|[（(].*段[）)])", name):
            return title
    return name if name in {CAUTION, A8_SECTION} else None


def applies(doc):
    dataset = doc.get("dataset") or {}
    title = str(dataset.get("title", ""))
    if doc.get("productionProfile") == PROFILE or TITLE in title or re.search(
        r"navigational safety administration.*yangshan deep[- ]water port", title, re.I
    ):
        return True
    sources = [(doc.get("common") or {}).get("sourceIndication")]
    sources.extend(feature.get("sourceIndication") for feature in doc.get("features", []))
    # A full regulation title or the official index identifies the case even
    # when the preliminary coding uses section names instead of chart names.
    if any(TITLE in re.sub(r"\s+", "", str(source)) or "2026-00146" in str(source) for source in sources):
        return True
    feature_names = {chinese(feature) for feature in doc.get("features", [])}
    if BRIDGE_SAFETY in feature_names and any(ordinary_name(name) == ORDINARY[0][0] for name in feature_names):
        return any(re.search(r"16号|No\.?\s*16|navigational safety administration.*yangshan deep[- ]water port", str(source), re.IGNORECASE)
                   or TITLE in str(source) for source in sources)
    return False


def fishing_allowed(feature, yangshan):
    return yangshan and bool(ordinary_name(chinese(feature)) or re.fullmatch(BRIDGE_SPAN, chinese(feature)))


def binding(role, target, **attrs):
    return {"role": role, "target": target, **attrs}


def text(eng, zho):
    return [{"categoryOfText": "abstract or summary", "information": [{"text": {"eng": eng, "zho": zho}}]}]


def measurement(characteristic, value, unit, comparison="greater than or equal to"):
    return {"comparisonOperator": comparison, "vesselsCharacteristics": characteristic,
            "vesselsCharacteristicsValue": value, "vesselsCharacteristicsUnit": unit}


def normalize(doc):
    doc["productionProfile"] = PROFILE
    common = doc.setdefault("common", {})
    common["sourceIndication"] = {
        "categoryOfAuthority": 15, "countryName": "China", "reportedDate": PUBLISHED,
        "source": SOURCE, "sourceType": "law or regulation",
        "featureName": {"eng": "Maritime Safety Administration of China", "zho": "中华人民共和国海事局"},
    }
    common["fixedDateRange"] = VALIDITY.copy()
    doc["dataset"]["referenceDate"] = PUBLISHED
    features = doc["features"]
    notes = {}
    used = {ref(feature) for feature in features}
    replaced = {}

    def upsert(key, kind, zho, eng, clause, attrs=None, match=None, reuse=True):
        candidates = [feature for feature in features if ref(feature) == key or
                      (reuse and (match(feature) if match else chinese(feature) == zho))]
        feature = next((item for item in candidates if ref(item) == key), candidates[0] if candidates else None)
        if feature is None:
            while key in used:
                key += "-new"
            used.add(key)
            feature = {"ref": key}
            features.append(feature)
        feature.update(type=kind, featureName={"zho": zho, "eng": eng}, clause=clause)
        if attrs is not None:
            feature["attributes"] = attrs
        return feature

    def attach(feature, role, target, **attrs):
        association = binding(role, ref(target), **attrs)
        values = feature.setdefault("associations", [])
        values[:] = [item for item in values if (item.get("role"), item.get("target")) != (role, ref(target))]
        values.append(association)

    groups = [*ORDINARY, (MAIN_SECTION, "Yangshan Port Main Fairway (Waihoumen to Xiaoyanjiao)"),
              (CAUTION, "Yangshan Precautionary Area"), (A8_SECTION, "Fairway between Nanzhi Lightship and A8 Light Buoy")]
    for index, (name, english) in enumerate(groups, 1):
        matching = [feature for feature in features if feature["type"] in GEOGRAPHIC_FEATURES
                    and ordinary_name(chinese(feature)) == name]
        if name in {ORDINARY[0][0], ORDINARY[1][0]} and matching:
            targets = matching
        else:
            target = upsert(f"ys-channel-{index}", "RestrictedAreaNavigational", name, english,
                            "第二十六条；附件1", match=lambda feature: feature in matching)
            targets = [target]
            for extra in matching:
                if extra is not target:
                    replaced[ref(extra)] = ref(target)
                    features.remove(extra)
        for target in targets:
            target["type"] = "RestrictedAreaNavigational"
            target.setdefault("clause", "第二十六条；附件1")
            target["attributes"] = {"restriction": [1, 3]}
        if name in {ORDINARY[0][0], ORDINARY[1][0]}:
            if len(targets) == 1:
                targets.append(upsert(f"ys-channel-{index}-pending-segment", "RestrictedAreaNavigational", name, english,
                                      "第二十六条；附件1", {"restriction": [1, 3]}, reuse=False))
            for target in targets:
                if chinese(target) == name:
                    target["featureName"] = {"zho": name, "eng": english}
                notes[ref(target)] = "对照表含同名分段，空间范围待逐段核对；不按行号或 FIN 对应"
        if name == CAUTION:
            caution = targets[0]
        if name == MAIN_SECTION:
            section = targets[0]

    spans = []
    for index, (tonnage, width, height) in enumerate(((500, "2×56", 17.5), (5000, "300", 40),
                                                   (1000, "2×100", 25), (500, "2×56", 17.5)), 1):
        span = upsert(f"ys-span-{index}", "RestrictedAreaNavigational", f"东海大桥{index}号通航孔航道",
                      f"Donghai Bridge Navigation Opening No. {index}", "第十九、二十、二十六、三十七条；附件1",
                      {"restriction": [30, 3, 8, 1]}, match=lambda f: re.sub(r"\s+", "", chinese(f)) in
                      {f"东海大桥{index}号通航孔航道", f"东海大桥{index}号通航孔"})
        direction = "单孔双向" if index == 2 else "双孔单向"
        span["textContent"] = text(f"{tonnage} DWT or less; clear width {width} m; clearance {height} m. "
                                   + ("One opening, two-way traffic." if index == 2 else "Two openings, one-way traffic."),
                                   f"{tonnage}载重吨及以下；{direction}航道；通航净宽{width}米，净空高度{height}米。")
        spans.append(span)
    safety = [feature for feature in features if chinese(feature) == BRIDGE_SAFETY]
    for index in range(1, 6):
        area = safety[index - 1] if index <= len(safety) else upsert(
            f"ys-bridge-safety-{index}", "RestrictedAreaNavigational", BRIDGE_SAFETY,
            "Donghai Bridge Safety Waters", "第二十、二十六、三十七条", reuse=False)
        area["type"] = "RestrictedAreaNavigational"
        area.setdefault("attributes", {})["restriction"] = [7, 1]
        area["textContent"] = text("Waters within 1,000 m on either side of the bridge axis, excluding the navigation channels. "
                                   "Entry is limited to bridge maintenance vessels; authorized emergency response is an exception per the production feedback.",
                                   "桥梁轴线两侧各1000米内、通航孔航道以外水域。除东海大桥维护作业船舶外不得驶入；按生产反馈保留经核准的应急处置例外。")
        notes[ref(area)] = "五处同名独立水域：逐面核对准确 GML，不按顺序或 FIN 猜配"
    for extra in safety[5:]:
        if not extra.get("geometry") or not extra.get("geometryEvidence"):
            features.remove(extra)

    scope = upsert("ys-scope", "WaterwayArea", "上海洋山深水港区及其附近水域", "Yangshan Deep-Water Port Area and Adjacent Waters",
                   "第二、三十七条；附件5", {"dynamicResource": 1}, match=lambda f: chinese(f) in
                   {"上海洋山深水港区及其附近水域", "洋山深水港区及其附近水域"})
    vts = upsert("ys-vts", "VesselTrafficServiceArea", "洋山船舶交通服务系统区域", "Yangshan VTS Area", "第三十、三十一条",
                 match=lambda f: f["type"] == "VesselTrafficServiceArea" and "洋山" in chinese(f))
    vts_attrs = vts.setdefault("attributes", {})
    vts_attrs.setdefault("categoryOfVesselTrafficService", [2])
    vts_attrs["requirementsForMaintenanceOfListeningWatch"] = [
        {"eng": "Use AIS and maintain a VHF listening watch as required.", "zho": "配备AIS和VHF的船舶按规定正确使用或守听。"}]
    authority = upsert("ys-authority", "Authority", "中华人民共和国上海海事局", "Shanghai Maritime Safety Administration", "第三条",
                       {"categoryOfAuthority": 15})
    attach(vts, "controlAuthority", authority)

    def applicable(key, name, english, attrs, clause="第三十七条"):
        return upsert(f"ys-app-{key}", "Applicability", name, english, clause, attrs)

    large = applicable("large", "大型船舶", "Large Vessels", {"logicalConnectives": 2, "vesselsMeasurements": [
        measurement("gross tonnage", 3000, "gross ton"), measurement("length overall", 100, "metre")],
        "information": [{"text": {"eng": "At least 3,000 GT or at least 100 metres in overall length.",
                                   "zho": "总吨3000及以上，或船长100米及以上。"}}]})
    ultra = applicable("ultra", "超大型集装箱船舶", "Ultra-Large Container Ships (at least 16,000 TEU)",
                       {"categoryOfVessel": 2, "categoryOfCargo": 2, "logicalConnectives": 1,
                        "information": [{"text": {"eng": "Maximum carrying capacity of at least 16,000 TEU.",
                                                  "zho": "最大载箱量16000TEU以上。"}}]})
    long_container = applicable("container-250", "船长大于250米的集装箱船舶", "Container Ships over 250 m",
                                {"categoryOfVessel": 2, "logicalConnectives": 1,
                                 "vesselsMeasurements": [measurement("length overall", 250, "metre", "greater than")],
                                 "information": [{"text": {"eng": "Container ships with overall length greater than 250 metres.",
                                                            "zho": "船长严格大于250米的集装箱船舶。"}}]}, "第二十二条")
    bulk = applicable("bulk-hazard", "载运散装危险货物的船舶", "Vessels Carrying Dangerous Cargo in Bulk",
                      {"categoryOfCargo": [1, 7], "logicalConnectives": 1,
                       "information": [{"text": {"eng": "Vessels carrying dangerous cargo in bulk.",
                                                  "zho": "适用于载运散装危险货物的船舶。"}}]}, "第二十一条")
    gas = applicable("low-flashpoint", "载运低闪点气态燃料的船舶", "Vessels Carrying Low Flashpoint Gaseous Fuel",
                     {"categoryOfCargo": 7, "categoryOfDangerousOrHazardousCargo": 7}, "第十六条")
    flammable = applicable("flammable", "载运易燃易爆危险货物的船舶", "Vessels Carrying Flammable or Explosive Dangerous Cargo",
                           {"categoryOfCargo": 7, "logicalConnectives": 1}, "第二十条")
    all_vessels = applicable("all", "适用的船舶、设施", "Vessels and Facilities Within the Scope of the Rules", {}, "第二条")
    bunker = applicable("bunker", "液化天然气及其他低闪点气态燃料加注船", "LNG and Low Flashpoint Gaseous Fuel Bunkering Vessels",
                        {"information": [{"text": {"eng": "Bunkering vessels covered by Article 16.", "zho": "第十六条所述气态燃料加注船。"}}]}, "第十六条")
    plans = applicable("plan", "第三十二条规定的船舶、设施", "Vessels and Facilities Listed in Article 32", {"logicalConnectives": 2}, "第三十二条")

    ukc = upsert("ys-ukc", "UnderkeelClearanceAllowanceArea", "洋山深水港区散装危险货物船舶富余水深限定区",
                 "Yangshan Underkeel Clearance Area for Vessels Carrying Dangerous Cargo in Bulk", "第二十一条",
                 {"underkeelAllowance": {"underkeelAllowanceVariableDraughtBased": 12, "underkeelAllowanceFixed": 1, "operation": 1}},
                 match=lambda f: f["type"] == "UnderkeelClearanceAllowanceArea" and (
                     "洋山" in chinese(f) or chinese(f) == "载运散装危险货物船舶富余水深区"))
    ukc["textContent"] = text("Clearance must exceed 12% of the vessel's draught and be at least 1 metre.",
                               "富余水深应当大于船舶吃水的12％，且不小于1米。")
    attach(ukc, "permission", bulk, permission="required")

    related_areas = []
    for key, name, english, restriction, article, aliases in (
        ("bridge-access", "洋山深水港区、临港水域及东海大桥相关水域", "Yangshan, Lingang and Donghai Bridge Waters", 8, "第二十二条；附件2", {"洋山深水港区、临港及东海大桥水域"}),
        ("main-access", "洋山港主航道水域", "Yangshan Port Main Fairway Waters", 8, "第二十二条；附件2", set()),
        ("jinshan-access", "金山水域", "Jinshan Waters", 8, "第十六、二十二条；附件2", set()),
        ("nanzhi", "南支灯船及附近水域", "Nanzhi Lightship and Adjacent Waters", 1, "第二十六条；附件4", set()),
        ("oil-terminal", "老人家门水道附近石油码头水域", "Oil Terminal Waters near Laorenjiamen Waterway", 8, "第十四条", {"老人家门水道附近石油码头"}),
    ):
        area = upsert(f"ys-area-{key}", "RestrictedAreaNavigational", name, english, article,
                      {"restriction": [restriction]}, match=lambda f: re.sub(
                          r"[（(]能见距离小于\s*500\s*米禁止航行[）)]$", "", chinese(f)) in {name, *aliases})
        related_areas.append(area)
    bridge_access, main_access, jinshan_access, nanzhi, oil_terminal = related_areas
    oil_terminal["textContent"] = text("Passenger vessels should avoid the waterway while a vessel berths at or departs from the nearby oil terminal.",
                                        "老人家门水道附近石油码头有船舶正在靠离泊作业时，客船应当避免从该水道通过。")
    nanzhi["textContent"] = text("Except in emergencies, vessels shall not anchor or drift in the designated waters.",
                                  "除紧急情况外，船舶不得在南支灯船及附近水域范围内锚泊、滞航。")

    geometries = doc.setdefault("geometries", {})

    def source_geometry(feature, geometry, kind, citation):
        evidence = feature.get("geometryEvidence") or {}
        if feature.get("geometry") and evidence.get("kind") in {"enc", "user-gml"} and evidence.get("reference"):
            return
        key = f"ys-source-{ref(feature)}"
        geometries[key] = geometry
        feature.update(geometry=key, geometryEvidence={"kind": kind, "reference": citation})
        feature.pop("geometryStatus", None)

    def source_ring(feature, coordinates, citation):
        points = extract(coordinates)
        source_geometry(feature, {"type": "surface", "exterior": [*points, points[0]]}, "source-boundary", citation)

    source_ring(bridge_access, "30°50′56.76″N/121°45′13.74″E;30°39′38.58″N/121°51′52.08″E;"
                "30°35′48″N/121°58′00″E;30°34′00″N/122°03′48″E;30°33′54″N/122°08′32.70″E;"
                "30°35′09″N/122°09′48″E;30°40′00″N/122°08′09″E;30°49′14.70″N/122°02′37.50″E;"
                "30°52′00″N/121°56′00″E", "第16号公告附件2第一项，九点法定边界")
    source_ring(nanzhi, "31°00′00″N/122°08′00″E;31°00′00″N/122°15′30″E;30°54′00″N/122°18′00″E;"
                "30°54′00″N/122°08′12″E;30°58′20.94″N/122°09′54.84″E;30°59′23.10″N/122°09′06.66″E",
                "第16号公告附件4，六点法定边界")
    for feature in features:
        name = re.sub(r"\s+", "", chinese(feature)).upper()
        is_anchorage = feature["type"] in {"AnchorageArea", "RestrictedAreaNavigational"}
        if is_anchorage and name in ANCHORAGE_BOUNDARIES:
            source_ring(feature, ANCHORAGE_BOUNDARIES[name], f"第16号公告附件3，{name}的法定坐标")
            continue
        if feature is caution:
            center, radius, citation = "30°33′33″N/122°11′26″E", 3704, "第16号公告附件1，法定半径 2 海里"
        elif is_anchorage and name in {"洋山NO.1应急锚地", "洋山1号应急锚地"}:
            center, radius, citation = "30°36′27″N/122°03′18″E", 600, "第16号公告附件3，法定半径 600 米"
        elif is_anchorage and name == "金山东锚地":
            center, radius, citation = "30°40′50″N/121°35′18″E", 1000, "第16号公告附件3，法定半径 1000 米"
        else:
            continue
        source_geometry(feature, circle_surface(extract(center)[0], radius), "source-circle", citation)

    def regulation(key, name, english, article, summary, applicable_to, areas, aliases=()):
        def same_rule(feature):
            return feature["type"] == "Regulations" and chinese(feature) in (name, *aliases)

        prior = [feature for feature in features if same_rule(feature)]
        rule = upsert(f"ys-rule-{key}", "Regulations", name, english, article,
                      {"categoryOfAuthority": 15}, match=same_rule)
        for duplicate in prior:
            if duplicate is not rule:
                replaced[ref(duplicate)] = ref(rule)
                features.remove(duplicate)
        rule["textContent"] = text(*summary)
        rule["associations"] = [binding("isApplicableTo", ref(item), inclusion="included") for item in applicable_to]
        attach(rule, "theOrganisation", authority)
        for area in areas:
            attach(area, "theRxN", rule, source=article)
        return rule

    regulation("precaution", "洋山警戒区航行要求", "Navigation Requirements in the Yangshan Precautionary Area", "第十条",
               ("Have engines ready, navigate with special caution under the master's direction, and do not impede vessels on the main fairway.",
                "船舶应当备车、由船长在驾驶台指挥并特别谨慎驾驶，不得妨碍沿主航道通行的船舶。"), [all_vessels], [caution],
               aliases=("洋山警戒区航行规定",))
    regulation("large-overtaking", "洋山警戒区大型船舶禁止相互追越", "No Overtaking Between Large Vessels in the Precautionary Area", "第十条",
               ("Large vessels shall not overtake one another.", "大型船舶间禁止追越。"), [large], [caution])
    regulation("lingang", "临港水域进港航道选择", "Inbound Fairway Selection for Lingang Waters", "第十二、十八条",
               ("Select a Donghai Bridge opening according to tonnage and cargo. Vessels unable to use an opening take the Yangshan Precautionary Area, Jinshan Fairway, Caojing East Branch Fairway and Lingang Main Fairway. Large vessels entering or leaving the latter must prepare engines and anchors.",
                "按船舶吨位和货物性质等选择通航孔；不能经通航孔时，经洋山警戒区、金山航道、漕泾东航道支线航道、临港主航道；大型船舶进出临港主航道须备车、备锚。"),
               [all_vessels], [caution, *spans])
    regulation("jinshan", "金山水域进港航道选择", "Inbound Fairway Selection for Jinshan Waters", "第十三条",
               ("Use Caojing West Fairway when arriving via Jinshan Inner Fairway. From the Yangshan Precautionary Area, Jinshan Fairway, Caojing East Branch and Caojing East Fairway may be used.",
                "经金山内航道驶往上海化学工业区沿岸码头应走漕泾西航道；自洋山警戒区可经金山航道、漕泾东航道支线航道、漕泾东航道。"), [all_vessels], [jinshan_access])
    regulation("crossing", "洋山港主航道外后门至小岩礁段交会限制", "Meeting Restrictions between Waihoumen and Xiaoyanjiao", "第十五条",
               ("Combined beam when meeting must not exceed 90 m. Avoid two-way traffic while an LNG vessel is berthed at, arriving at or departing from the nearby LNG terminal.",
                "两船交会宽度总和不大于90米；附近液化天然气码头有液化天然气船舶停泊或正在靠离泊时，应避免双向通航。"), [all_vessels], [section])
    regulation("dangerous", "危险船舶进出洋山及金山水域航行要求", "Navigation Requirements for Hazardous Vessels in Yangshan and Jinshan", "第十六条",
               ("LNG and other low flashpoint gas fuel carriers require a safety assessment, escort and safe separation, visibility of at least 2,000 m and wind no greater than Beaufort 7.",
                "液化天然气及其他低闪点气态燃料船须专题论证并确定护航、安全距离，进出港能见距离不小于2000米、风力不大于7级。"),
               [gas], [scope, main_access, jinshan_access], aliases=("液化天然气及低闪点气态燃料船舶安全保障",))
    liquid_bulk = applicable("liquid-bulk-hazard", "载运散装液体危险货物的船舶", "Vessels Carrying Dangerous Liquid Cargo in Bulk",
                             {"categoryOfCargo": [1, 7], "logicalConnectives": 1,
                              "information": [{"text": {"eng": "Limited to dangerous cargo in liquid bulk.",
                                                         "zho": "仅指散装液体危险货物。"}}]}, "第二十条")
    regulation("dangerous-bridge", "禁止危险货物船通过东海大桥", "Ban on Donghai Bridge Passage for Specified Dangerous Cargo", "第二十条",
               ("Vessels carrying flammable or explosive dangerous cargo, or dangerous cargo in liquid bulk, must not pass Donghai Bridge.",
                "禁止载运易燃易爆危险货物或散装液体危险货物的船舶通过东海大桥。"), [flammable, liquid_bulk], spans)
    regulation("ultra", "超大型集装箱船靠离泊作业条件", "Berthing and Unberthing Conditions for Ultra-Large Container Ships", "第十七条",
               ("Minimum visibility 1,000 m by day and 2,000 m by night; maximum Beaufort 8, or 7 in southerly winds.",
                "白天能见距离不小于1000米、夜间不小于2000米；风力不大于8级，偏南风时不大于7级。"), [ultra], [scope],
               aliases=("超大型集装箱船舶靠离泊条件",))
    regulation("openings", "东海大桥通航孔通过规定", "Donghai Bridge Opening Passage Requirements", "第十九、二十条",
               ("Choose a suitable opening, keep at least 4 m overhead clearance and prefer slack water. Prepare engines and anchors; the master commands from the bridge and the chief engineer stands watch below. Auxiliary openings close at Beaufort 8; the main opening closes at Beaufort 9.",
                "按船舶吨位及通航尺度选孔，保留4米以上富余高度并尽量选缓流时段；备车备锚，船长在驾驶台指挥、轮机长在机舱值班；8级及以上禁止通过辅孔，9级及以上禁止通过主孔。"), [all_vessels], spans)
    regulation("towing", "东海大桥拖轮船队和顶推组合体通行限制", "Donghai Bridge Restrictions on Towing and Pushing Formations", "第二十条",
               ("No stern towing formation or other towing formation over 120 m in total length or 22 m in total beam may use the main opening. No towing or push boat–barge formation may use an auxiliary opening.",
                "主通航孔禁止吊拖船队及总长大于120米或总宽大于22米的其他拖轮船队通过；辅孔禁止拖轮船队和顶推船—驳船组合体通过。"), [all_vessels], spans)
    regulation("visibility", "能见距离限制", "Visibility Restrictions", "第二十二条；附件2",
               ("No Donghai Bridge passage below 1,000 m. Below 500 m, no navigation in Appendix 2 waters except eligible container ships over 250 m after special safety measures.",
                "能见距离小于1000米不得通过东海大桥；小于500米不得在附件2水域航行，满足条文条件且采取特别安全措施的船长大于250米集装箱船舶可继续航行。"), [all_vessels], [bridge_access, main_access],
               aliases=("能见度不良时航行限制",))
    regulation("restricted-visibility-exception", "低能见度航行的集装箱船限定例外", "Limited Visibility Exception for Container Ships", "第二十二条；附件2",
               ("Below 500 m in Appendix 2 waters, a container ship over 250 m may continue with special safety measures if already in the precautionary area and bound for a Yangshan Deep-Water Port berth, or outbound after leaving such a berth. The local MSA must promptly notify adjacent MSAs.",
                "附件2水域能见距离小于500米时，已进入警戒区拟靠洋山深水港区码头、或已从该港区码头离泊出口的船长大于250米集装箱船，采取特别安全措施后方可继续航行；辖区海事管理机构须及时通报相邻海事管理机构。"),
               [long_container], [bridge_access, main_access])

    reporting = (
        ("bunkering", "Report Before LNG or Low Flashpoint Gas Fuel Bunkering", "第十六条",
         "Report the operation type, time, location, entity and vessel name to the competent authority before bunkering.", "加注船须在作业前向主管机关报告作业种类、时间、地点、单位和船舶名称等。", bunker, scope),
        ("vts", "Reports at Yangshan VTS Reporting Lines and Points", "第三十一条",
         "Report when passing a VTS reporting line or point; vessels meeting the one-time position reporting rules may be exempt.", "经过洋山VTS报告线及报告点时按规定报告；符合一次性船位报告要求的可免报。", all_vessels, vts),
        ("plan", "Next-Day Berthing, Unberthing and Bridge Passage Plans", "第三十二条",
         "Before 1600 daily, international vessels, passenger vessels, towing or pushing formations, oil, bulk chemical and liquefied gas carriers of ≥500 GT, and other domestic vessels of ≥1,000 GT must report next-day plans to the local MSA.",
         "国际航行船、客船、拖船船队或顶推组合体、500总吨及以上油船、散装化学品船和液化气船，以及1000总吨及以上其他国内航行船舶，每日1600时前向辖区海事管理机构报告次日靠离泊或过桥计划。", plans, scope),
        ("anchor", "Anchoring, Weighing Anchor, Dragging and Emergency Anchoring Reports", "第三十三条",
         "Report in advance before anchoring or weighing anchor. A vessel dragging anchor must take emergency measures, notify nearby vessels and report. Emergency anchoring outside an anchorage requires VHF notice, signals, clearing the fairway where possible and immediate reporting.",
         "抛起锚须提前报告；走锚须立即处置、通报附近船舶并报告。锚地外应急锚泊须VHF通报、显示信号、尽可能让出航道并立即报告。", all_vessels, scope),
        ("berthing", "Berthing and Unberthing Movement Reports", "第三十四条",
         "Vessels must announce their movements in advance and report to the local MSA before berthing or unberthing.", "靠离码头的船舶应提前发布动态，并向辖区海事管理机构报告。", all_vessels, scope),
        ("incident", "Casualty and Equipment Failure Reports", "第三十五条",
         "Take safety measures, leave the fairway where possible, broadcast vessel movements and report promptly to the local MSA.", "发生水上交通事故或影响安全航行的设备故障时，采取安全措施、尽可能驶离航道、通报动态并及时报告。", all_vessels, scope),
        ("obstruction", "Reports of Navigational Obstructions", "第三十六条",
         "Owners or operators must report the name, shape, size, position and depth of obstructions to the local MSA and mark the obstruction as required.", "碍航物及其他影响安全航行的沉没物、漂浮物未妥善解决前，所有人或经营人须报告名称、形状、尺寸、位置和深度，并设置规定标志。", all_vessels, scope),
    )
    for name, (key, english, article, eng_text, zho_text, applicability, area) in zip(REPORT_NAMES, reporting, strict=True):
        regulation(f"report-{key}", name, english, article, (eng_text, zho_text), [applicability], [area])

    features[:] = [feature for feature in features if feature["type"] != "ShipReport"]
    by_ref = {ref(feature): feature for feature in features}
    for feature in features:
        if feature["type"] in SOURCED_TYPES:
            feature.pop("sourceIndication", None)
            feature.pop("fixedDateRange", None)
            attrs = feature.get("attributes") or {}
            attrs.pop("sourceIndication", None)
            attrs.pop("fixedDateRange", None)
            feature["reusable"] = False
        cleaned = []
        for association in feature.get("associations", []):
            target_ref = replaced.get(association.get("target"), association.get("target"))
            target = by_ref.get(target_ref)
            role = association.get("role")
            if target and role in allowed_roles(feature["type"]):
                expected = set(role_targets(feature["type"], role))
                chain = set(FEATURES.get(target["type"], {}).get("chain", ())) | {target["type"]}
                if expected and not expected.intersection(chain):
                    continue
                cleaned.append({**association, "target": target_ref})
        feature["associations"] = cleaned
    associated = {association["target"] for feature in features for association in feature["associations"]}
    features[:] = [feature for feature in features if feature["type"] != "Applicability" or ref(feature) in associated]
    return notes
