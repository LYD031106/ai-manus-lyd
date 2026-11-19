"""Production corrections for Zhangzhou bridge notice 闽航通〔2026〕0052号."""
from __future__ import annotations

import re
import unicodedata
from datetime import date

from s127_model import GEOGRAPHIC_FEATURES
from yangshan_profile import chinese, ref


NOTICE = "Notice No.0052/2026｜闽航通〔2026〕0052号"
NOTICE_PATTERN = r"闽航通\s*[〔\[（(]?\s*2026\s*[〕\]）)]?\s*0052\s*号"
BRIDGE_NAMES = {
    "九龙江口龙江大桥桥区水域": ("龙江大桥桥区水域", "Longjiang Bridge Waters"),
    "九龙江口南港特大桥桥区水域": ("南港特大桥桥区水域", "Nangang Grand Bridge Waters"),
    "九龙江口北港特大桥桥区水域": ("北港特大桥桥区水域", "Beigang Grand Bridge Waters"),
}
CLEARANCE = (
    r"(?:通航)?净空(?:高度|高)?\s*(?:为|是|[:：])?\s*(\d+(?:\.\d+)?\s*(?:米|m)(?:\s*\([^()]+\))?)",
    r"vertical\s+clearance(?:\s+height)?\s*(?:is|[:：])?\s*(\d+(?:\.\d+)?\s*(?:metres?|meters?|m)\b(?:\s*\([^()]+\))?)",
)
CLEARANCE_LABEL = re.compile(r"^(?:通航)?净空(?:高度|高)|^vertical clearance", re.I)


def clearance_information(feature, attributes):
    """Preserve clearance values with their stated datum and span distinctions."""
    records = []
    for owner in (feature, attributes):
        nested = owner.get("textContent") or []
        entries = [owner, *(nested if isinstance(nested, list) else [nested])]
        for entry in entries:
            values = entry.get("information") or []
            for info in values if isinstance(values, list) else [values]:
                label = info.get("headline") or {}
                bodies = info.get("text") or {}
                if not isinstance(bodies, dict):
                    continue
                if isinstance(label, dict) and any(CLEARANCE_LABEL.search(item) for item in label.values() if isinstance(item, str)):
                    selected = {language: body for language, body in bodies.items()
                                if isinstance(body, str) and re.search(r"\d", body)}
                else:
                    selected = {}
                    for language, pattern in zip(("zho", "eng"), CLEARANCE, strict=True):
                        normalized = unicodedata.normalize("NFKC", bodies.get(language) or "")
                        found = list(dict.fromkeys(re.findall(pattern, normalized, flags=re.I)))
                        if found:
                            prefix = "通航净空高度：" if language == "zho" else "Vertical clearance: "
                            selected[language] = prefix + ("；" if language == "zho" else "; ").join(found) + ("。" if language == "zho" else ".")
                if selected and {"text": selected} not in records:
                    records.append({"text": selected})
    return records


def normalize(doc):
    common = doc.get("common") or {}
    features = doc.get("features", [])
    sources = [common.get("sourceIndication"), *[item.get("sourceIndication") for item in features]]
    evidence = unicodedata.normalize("NFKC", str([doc.get("dataset"), *sources]))
    if not re.search(NOTICE_PATTERN, evidence):
        return {}

    dates = set()
    dataset = doc.get("dataset") or {}
    dataset_dates = {dataset.get("issueDate"), dataset.get("referenceDate")}
    for source in sources:
        for entry in source if isinstance(source, list) else [source]:
            if not isinstance(entry, dict):
                continue
            identity = unicodedata.normalize("NFKC", str(entry.get("source", "")))
            reported = entry.get("reportedDate")
            # Only carry the date explicitly supplied for this announcement. Reject a date
            # identical to the dataset generation date, which was a known source of errors.
            if re.search(NOTICE_PATTERN, identity) and isinstance(reported, str) and reported not in dataset_dates:
                try:
                    dates.add(date.fromisoformat(reported).isoformat())
                except ValueError:
                    pass
    source = {"source": NOTICE, "categoryOfAuthority": 15, "countryName": "China",
              "sourceType": "official publication",
              "featureName": {"eng": "Zhangzhou Maritime Safety Administration", "zho": "漳州海事局"}}
    if len(dates) == 1:
        source["reportedDate"] = dates.pop()

    notes = {}
    for feature in features:
        name = chinese(feature)
        if feature.get("type") not in GEOGRAPHIC_FEATURES or "桥区水域" not in name:
            continue
        notes[ref(feature)] = "按闽航通〔2026〕0052号整理桥区限制。"
        old_attrs = feature.get("attributes") or {}
        feature.update(type="RestrictedAreaNavigational", attributes={"restriction": [35, 3, 1]},
                       sourceIndication=source.copy(), reusable=False)
        if name in BRIDGE_NAMES:
            zho, eng = BRIDGE_NAMES[name]
            feature["featureName"] = {"zho": zho, "eng": eng}
        values = clearance_information(feature, old_attrs)
        for key in ("textContent", "information"):
            feature.pop(key, None)
        if values:
            feature["textContent"] = [{"categoryOfText": "abstract or summary", "information": values}]
        else:
            notes[ref(feature)] = "资料未注明可提取的通航净空高度；未推定数值。"
        if "reportedDate" not in source:
            notes[ref(feature)] = "；".join(filter(None, (notes.get(ref(feature)), "未核实通告发布日期，已留空。")))
    return notes
