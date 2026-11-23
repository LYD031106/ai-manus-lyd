"""Result tables retain the identifiers and values emitted by the GML builder."""
import copy
import csv
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from build_gml import build  # noqa: E402
from make_record_table import (  # noqa: E402
    COLUMNS, SHEETS, build_comments, build_rows, write_csv, write_xlsx,
)
from source_rules import normalize_draft  # noqa: E402


def feature(ref, kind, **kwargs):
    return {"ref": ref, "type": kind,
            "featureName": {"eng": f"English {ref}", "zho": f"中文{ref}"}, **kwargs}


def dataset(features):
    return {
        "dataset": {"name": "127CN00TEST001", "featureIdStart": 2195},
        "common": {
            "fixedDateRange": {"dateStart": "2026-10-01", "dateEnd": "2031-09-30"},
            "sourceIndication": {"source": {"zho": "第16号公告", "eng": "Announcement No. 16"}},
        },
        "features": features,
    }


class RecordTableTest(unittest.TestCase):
    def test_fin_names_and_order_match_gml_including_explicit_override(self):
        doc = dataset([
            feature("ship", "Applicability"),
            feature("area", "RestrictedAreaNavigational"),
            feature("rule", "Regulations"),
            feature("explicit", "WaterwayArea", attributes={
                "s100:featureObjectIdentifier": {"s100:agency": "CN",
                    "s100:featureIdentificationNumber": 9000,
                    "s100:featureIdentificationSubdivision": 4},
                "featureName": [{"language": "zho", "name": "覆盖名称"}],
            }),
            feature("last", "WaterwayArea"),
        ])
        rows = build_rows(doc)
        self.assertEqual([r[0] for r in rows], [f["type"] for f in doc["features"]])
        self.assertEqual([r[1] for r in rows], ["---", "2195-1", "---", "9000-4", "2197-1"])
        self.assertEqual(rows[3][2:4], ["覆盖名称", ""])
        xml, *_ = build(copy.deepcopy(doc), draft=True)
        members = ET.fromstring(xml).find("{*}members")
        for row, node in zip(rows, members):
            identifier = node.find("{*}featureObjectIdentifier")
            if identifier is not None:
                number = identifier.findtext("{*}featureIdentificationNumber")
                subdivision = identifier.findtext("{*}featureIdentificationSubdivision")
                self.assertEqual(row[1], f"{number}-{subdivision}")
            for lang, column in (("zho", 2), ("eng", 3)):
                names = [n.findtext("{*}name") for n in node.findall("{*}featureName")
                         if n.findtext("{*}language") == lang]
                self.assertEqual(row[column], "\n".join(names))

    def test_dates_and_sources_follow_inheritance_null_and_attribute_precedence(self):
        doc = dataset([
            feature("reusable", "Applicability"),
            feature("inherited", "Regulations"),
            feature("forced", "Authority", reusable=False),
            feature("suppressed", "Regulations", fixedDateRange=None, sourceIndication=None),
            feature("override", "Regulations", fixedDateRange={"dateStart": "2020-01-01"},
                    sourceIndication={"source": "old"}, attributes={
                        "fixedDateRange": {"dateStart": "2027-02-03"},
                        "sourceIndication": {"source": ["Override", "覆盖来源"]},
                    }),
            feature("attr-null", "Regulations", attributes={"sourceIndication": None,
                                                             "fixedDateRange": None}),
        ])
        rows = build_rows(doc)
        self.assertEqual(rows[0][5:], ["", "", ""])
        for index in (1, 2):
            self.assertEqual(rows[index][5:], ["2026-10-01", "2031-09-30",
                                               "Announcement No. 16 / 第16号公告"])
        for index in (3, 5):
            self.assertEqual(rows[index][5:], ["", "", ""])
        self.assertEqual(rows[4][5:], ["2027-02-03", "", "Override / 覆盖来源"])
        xml, *_ = build(copy.deepcopy(doc), draft=True)
        for row, node in zip(rows, ET.fromstring(xml).find("{*}members")):
            for column, name in ((5, "dateStart"), (6, "dateEnd")):
                date = node.find(f"{{*}}fixedDateRange/{{*}}{name}/{{*}}date")
                self.assertEqual(row[column], date.text if date is not None else "")
            source = node.findtext("{*}sourceIndication/{*}source", "")
            self.assertEqual(row[7], " / ".join(source.splitlines()))

    def test_inclusive_table_end_and_exclusive_gml_end_use_same_source_period(self):
        doc = dataset([feature("inherited", "Regulations")])
        doc["common"]["validityBasis"] = {"reference": "示意附则", "effectiveDate": "2028-03-01",
                                           "durationYears": 1, "dateEndConvention": "expiry-boundary"}
        normalize_draft(doc)
        self.assertEqual(build_rows(doc)[0][5:7], ["2028-03-01", "2029-02-28"])
        gml, _ = build(doc, draft=True)
        node = ET.fromstring(gml).find(".//{*}fixedDateRange/{*}dateEnd/{*}date")
        self.assertEqual(node.text, "2029-03-01")

    def test_coding_values_match_gml_enums_and_keep_composite_instances(self):
        doc = dataset([
            feature("area", "RestrictedAreaNavigational", attributes={
                "restriction": [3, "anchoring prohibited"],
                "$comment": "not an encoded attribute",
            }),
            feature("rule", "Regulations", attributes={
                "categoryOfAuthority": "maritime",
                "rxnCode": [{"categoryOfRxN": "navigation", "headline": "First"},
                            {"categoryOfRxN": 2, "headline": "Second"}],
            }),
            feature("ship", "Applicability", attributes={
                "logicalConnectives": "logical conjunction",
                "inBallast": False,
            }),
        ])
        rows = build_rows(doc)
        self.assertEqual(rows[0][4], "restriction=3(fishing prohibited);\nrestriction=1(anchoring prohibited)")
        self.assertIn("rxnCode[1].categoryOfRxN=1(navigation)", rows[1][4])
        self.assertIn("rxnCode[2].categoryOfRxN=2(communication)", rows[1][4])
        self.assertIn("inBallast=false", rows[2][4])
        xml, *_ = build(copy.deepcopy(doc), draft=True)
        for row, node in zip(rows, ET.fromstring(xml).find("{*}members")):
            for enum in node.iter():
                if "code" in enum.attrib:
                    self.assertIn(f"={enum.get('code')}({enum.text})", row[4])
        self.assertNotIn("$comment", rows[0][4])

    def test_csv_keeps_all_eight_columns_and_sources_on_every_sheet(self):
        doc = dataset([feature("rule", "Regulations"), feature("ship", "Applicability")])
        rows = build_rows(doc)
        with tempfile.TemporaryDirectory() as folder:
            paths = write_csv(Path(folder) / "results.csv", rows)
            self.assertEqual(len(paths), 3)
            for path in paths:
                with path.open(encoding="utf-8-sig", newline="") as stream:
                    exported = list(csv.reader(stream))
                self.assertEqual(exported, [COLUMNS, *rows])

    def test_xlsx_preserves_values_comments_and_requested_layout(self):
        try:
            from openpyxl import load_workbook
        except ImportError:
            self.skipTest("openpyxl is optional")
        doc = dataset([
            feature("ship", "Applicability"),
            feature("area", "RestrictedAreaNavigational", clause="第八条", associations=[
                {"role": "permission", "target": "ship", "permission": "required"},
            ], attributes={"restriction": ["fishing prohibited", "anchoring prohibited"]},
               sourceReview=[{"reference": "附图二", "reason": "水域边界待核对"}]),
        ])
        rows = build_rows(doc)
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "results.xlsx"
            self.assertTrue(write_xlsx(output, rows, doc["dataset"], build_comments(doc)))
            workbook = load_workbook(output)
            self.assertEqual(workbook.sheetnames, SHEETS)
            for sheet in workbook:
                self.assertEqual([c.value for c in sheet[1]], COLUMNS)
                values = [[cell.value or "" for cell in row]
                          for row in sheet.iter_rows(min_row=2, max_row=len(rows) + 1)]
                self.assertEqual(values, rows)
                self.assertEqual(sheet.freeze_panes, "A2")
                self.assertEqual(sheet["A1"].fill.fgColor.rgb, "004472C4")
                self.assertEqual(sheet["A1"].font.color.rgb, "00FFFFFF")
                self.assertTrue(sheet["E3"].alignment.wrap_text)
                self.assertEqual(sheet["E3"].border.bottom.style, "thin")
                comment = sheet["H3"].comment.text
                self.assertIn("第八条", comment)
                self.assertIn("附图二：水域边界待核对", comment)
                self.assertIn("permission→中文ship English ship（required）", comment)
                self.assertEqual(sheet["B3"].data_type, "s")
            workbook.close()


if __name__ == "__main__":
    unittest.main()
