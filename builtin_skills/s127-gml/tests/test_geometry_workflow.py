"""Regression coverage for draft, geometry supplementation and final GML checks."""
import json
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
GML = "{http://www.opengis.net/gml/3.2}"
XLINK = "{http://www.w3.org/1999/xlink}"
SURFACES = {
    "a": {"type": "surface", "exterior": [[30, 122], [30.1, 122], [30, 122.1]]},
    "b": {"type": "surface", "exterior": [[31, 122], [31.1, 122], [31, 122.1]]},
}


def feature(ref, kind, name, **kwargs):
    return {"ref": ref, "type": kind, "featureName": {"zho": name}, **kwargs}


class GeometryWorkflowTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        self.dataset = {"name": "127CN00TEST001"}

    def write_json(self, name, features, geometries=None):
        (self.directory / name).write_text(json.dumps({
            "dataset": self.dataset, "features": features, "geometries": geometries or {},
        }, ensure_ascii=False), encoding="utf-8")

    def run_script(self, script, *args, code=0):
        result = subprocess.run([sys.executable, "-X", "utf8", str(SCRIPTS / script), *args],
                                cwd=self.directory, capture_output=True, text=True, encoding="utf-8")
        message = result.stdout + result.stderr
        if result.returncode != code and "--report" in args:
            path = self.directory / args[args.index("--report") + 1]
            if path.is_file():
                message += path.read_text(encoding="utf-8")
        self.assertEqual(result.returncode, code, message)

    def report(self, name):
        return (self.directory / name).read_text(encoding="utf-8")

    def test_source_basis_drives_selection_dates_and_circle_without_named_profile(self):
        self.dataset["referenceDate"] = "2025-08-12"
        date = {"source": "航道通告乙", "reportedDate": "2025-08-12"}
        self.write_json("seed.json", [
            feature("fairway", "RouteingMeasure", "白沙东航段", geometry="a", sourceIndication=date,
                    sourceBasis={"kind": "regulated-fairway", "reference": "航道通告乙 第八条"},
                    attributes={"categoryOfRouteingMeasure": 3, "restriction": [1, 3]}),
            feature("route", "RouteingMeasure", "外湾推荐航路", sourceIndication=date,
                    reportedDateBasis="航道通告乙 首页落款",
                    sourceBasis={"kind": "routeing-measure", "reference": "航道通告乙 附件 4"},
                    attributes={"categoryOfRouteingMeasure": 4}),
            feature("anchorage", "RestrictedAreaNavigational", "石滩锚地", attributes={"restriction": [2]},
                    geometryEvidence={"kind": "source-circle", "reference": "附件 2 圆形边界",
                                      "center": [32, 121], "radiusMetres": 640}),
        ], SURFACES)
        self.run_script("geometry_workflow.py", "draft", "seed.json", "-o", "review.gml",
                        "--featureset-out", "draft.json", "--report", "report.md")
        result = json.loads(self.report("draft.json"))
        fairway, route, anchorage = result["features"]
        self.assertEqual(len(result["features"]), 3)
        self.assertEqual(fairway["type"], "RestrictedAreaNavigational")
        self.assertEqual(fairway["attributes"], {"restriction": [1]})
        self.assertNotIn("geometry", fairway)
        self.assertNotIn("reportedDate", fairway["sourceIndication"])
        self.assertEqual(route["type"], "RouteingMeasure")
        self.assertEqual(route["sourceIndication"]["reportedDate"], "2025-08-12")
        self.assertEqual(len(result["geometries"][anchorage["geometry"]]["exterior"]), 145)
        self.assertIn("禁止捕捞作业", self.report("review.draft.gml"))
        self.assertIn('nilReason="unknown"', self.report("review.draft.gml"))

    def test_bridge_notice_keeps_only_sourced_clearance_and_distinct_names(self):
        self.write_json("seed.json", [
            feature("first", "WaterwayArea", "新港桥桥区水域", attributes={"restriction": [35, 1]},
                    sourceBasis={"kind": "bridge-information", "reference": "桥梁通告附表第 1 行",
                                 "verticalClearance": [{"zho": "通航净空高度 18 米（当地高程基面）"}]},
                    textContent=[{"information": [{"text": {"zho": "通航宽度 90 米。"}}]}]),
            feature("second", "WaterwayArea", "新港北桥桥区水域", attributes={"restriction": [35]},
                    sourceBasis={"kind": "bridge-information", "reference": "桥梁通告附表第 2 行"}),
        ])
        self.run_script("geometry_workflow.py", "draft", "seed.json", "-o", "bridge.gml",
                        "--featureset-out", "draft.json", "--report", "bridge.md")
        first, second = json.loads(self.report("draft.json"))["features"]
        self.assertEqual(first["type"], "RestrictedAreaNavigational")
        self.assertEqual(first["featureName"]["zho"], "新港桥桥区水域")
        self.assertEqual(second["featureName"]["zho"], "新港北桥桥区水域")
        self.assertEqual(second["attributes"], {"restriction": [35]})
        self.assertNotIn("textContent", second)
        self.assertIn("当地高程基面", self.report("bridge.draft.gml"))
        self.assertNotIn("90 米", self.report("bridge.draft.gml"))

    def test_reporting_obligation_requires_rule_with_spatial_and_vessel_links(self):
        basis = {"kind": "reporting-obligation", "reference": "北湾通告 第十九条"}
        ship_report = feature("rule", "ShipReport", "次日报告", sourceBasis=basis)
        self.write_json("incorrect.json", [ship_report])
        self.run_script("geometry_workflow.py", "draft", "incorrect.json", "-o", "wrong.gml",
                        "--report", "wrong.md", code=1)
        self.assertIn("不能直接当作 ShipReport", self.report("wrong.md"))
        regulation = feature("rule", "Regulations", "次日报告", sourceBasis=basis, textContent=[
            {"categoryOfText":"extract","information":[{"text":{"zho":"客船于每日十二时前报告次日计划。"}}]}])
        self.write_json("unlinked.json", [regulation])
        self.run_script("geometry_workflow.py", "draft", "unlinked.json", "-o", "unlinked.gml",
                        "--report", "unlinked.md", code=1)
        self.assertIn("缺少相应地理要素", self.report("unlinked.md"))
        regulation["associations"] = [{"role":"isApplicableTo","target":"vessels","inclusion":"included"}]
        area = feature("area", "VesselTrafficServiceArea", "北湾交管区",
                       associations=[{"role":"theRxN","target":"rule"}])
        applicable = feature("vessels", "Applicability", "客船",
                             attributes={"information": {"text":{"zho":"客船"}}})
        self.write_json("linked.json", [area, regulation, applicable])
        self.run_script("geometry_workflow.py", "draft", "linked.json", "-o", "linked.gml", "--report", "linked.md")
        self.assertIn("area → theRxN → rule", self.report("linked.md"))
        self.assertIn("<InclusionType", self.report("linked.draft.gml"))
        self.assertIsNone(ET.parse(self.directory / "linked.draft.gml").getroot().find(".//{*}Regulations/{*}geometry"))

    def test_three_phases_preserve_vts_associations(self):
        vts = feature("vts", "VesselTrafficServiceArea", "沙湾交管区",
                      associations=[{"role": "reptForTrafficServ", "target": "report"}])
        report = feature("report", "ShipReport", "过线报告")
        self.write_json("initial.json", [vts, report])
        self.run_script("geometry_workflow.py", "draft", "initial.json",
                        "-o", "data.gml", "--report", "draft.md")
        draft = ET.parse(self.directory / "data.draft.gml").getroot()
        self.assertIsNone(draft.find(f"{GML}boundedBy"))
        self.assertIn("沙湾交管区", self.report("draft.md"))
        self.assertIn("过线报告", self.report("draft.md"))
        self.assertIn("vts → reptForTrafficServ → report", self.report("draft.md"))
        self.run_script("geometry_workflow.py", "check", "data.draft.gml",
                        "--report", "draft-check.md", code=1)
        self.assertIn("缺少几何", self.report("draft-check.md"))

        self.write_json("donor.json", [{**vts, "geometry": "a", "associations": []}], SURFACES)
        self.run_script("build_gml.py", "donor.json", "-o", "geometry.gml")
        self.run_script("geometry_workflow.py", "complete", "initial.json", "geometry.gml",
                        "-o", "data.gml", "--featureset-out", "completed.json",
                        "--report", "completed.md", "--assoc-csv", "associations.csv")
        final = ET.parse(self.directory / "data.gml").getroot()
        link = final.find(".//{*}VesselTrafficServiceArea/{*}reptForTrafficServ")
        ship_report = final.find(".//{*}ShipReport")
        self.assertEqual(link.get(XLINK + "href"), "#" + ship_report.get(GML + "id"))
        self.assertIsNotNone(final.find(".//{*}VesselTrafficServiceArea/{*}geometry"))
        self.assertIn("已从 geometry.gml #f1 补录", self.report("completed.md"))
        self.run_script("geometry_workflow.py", "check", "data.gml", "--report", "final.md")
        self.assertIn("未执行完整 XSD 校验", self.report("final.md"))

    def test_same_name_geometry_requires_explicit_mapping_and_compatibility_flag(self):
        restriction = ["entry restricted", "passing or overtaking prohibited", "fishing prohibited"]
        first = feature("one", "RestrictedAreaNavigational", "试验桥安全水域",
                        attributes={"restriction": restriction}, geometryStatus="review", geometry="b",
                        geometryEvidence={"kind":"chart-trace", "reference":"附图二"},
                        sourceBasis={"kind":"bridge-waters","reference":"试验通告附件 2", "partitionGroup":"桥区独立水域"})
        second = {**first, "ref": "two", "geometry": None}
        self.write_json("initial.json", [first, second], SURFACES)
        self.write_json("donor.json", [{**first, "geometry": "a"}, {**second, "geometry": "b"}], SURFACES)
        self.run_script("build_gml.py", "donor.json", "-o", "geometry.gml")
        self.run_script("validate_featureset.py", "initial.json", code=1)
        self.run_script("validate_featureset.py", "initial.json", "--allow-restriction-3")

        command = ("complete", "initial.json", "geometry.gml", "-o", "data.gml",
                   "--featureset-out", "completed.json", "--report", "completed.md", "--allow-restriction-3")
        self.run_script("geometry_workflow.py", *command)
        self.assertFalse((self.directory / "data.gml").exists())
        self.assertIn("同名有歧义", self.report("completed.md"))
        self.assertIn("已勾绘，待确认", self.report("completed.md"))

        duplicate = {"a": SURFACES["a"], "b": {"type": "surface",
                                             "exterior": SURFACES["a"]["exterior"][::-1]}}
        self.write_json("duplicates.json", [{**first, "geometry": "a"}, {**second, "geometry": "b"}], duplicate)
        self.run_script("build_gml.py", "duplicates.json", "-o", "duplicate.gml")
        duplicate_command = (*command[:2], "duplicate.gml", *command[3:])
        self.run_script("geometry_workflow.py", *duplicate_command, "--match", "one=f1", "--match", "two=f2")
        self.assertIn("独立分区须各提供不同几何", self.report("completed.md"))
        self.assertFalse((self.directory / "data.gml").exists())

        self.run_script("geometry_workflow.py", *command, "--match", "one=f1", "--match", "two=f2")
        final = self.report("data.gml")
        self.assertIn('code="30">passing or overtaking prohibited', final)
        self.assertIn('code="3">fishing prohibited', final)
        doc = json.loads(self.report("completed.json"))
        self.assertEqual([f["geometry"] for f in doc["features"]], ["补录-one", "补录-two"])
        self.assertNotIn("geometryStatus", doc["features"][0])
        self.assertNotEqual(doc["geometries"]["补录-one"], doc["geometries"]["补录-two"])

        self.run_script("geometry_workflow.py", "check", "data.gml",
                        "--report", "final.md", "--allow-restriction-3")
        self.assertIn("超出 S127FC", self.report("final.md"))
        corrupted = final.replace('code="30"', 'code="28"').replace('xlink:href="#s1"', 'xlink:href="#missing"')
        (self.directory / "broken.gml").write_text(corrupted, encoding="utf-8")
        self.run_script("geometry_workflow.py", "check", "broken.gml",
                        "--report", "broken.md", "--allow-restriction-3", code=1)
        self.assertIn("本地引用 '#missing' 不存在", self.report("broken.md"))
        self.assertIn("code 应为 30", self.report("broken.md"))
        self.assertIn("试验桥安全水域", self.report("broken.md"))

    def test_renamed_draft_and_unlinked_vts_report_remain_visible(self):
        vts = feature("vts", "VesselTrafficServiceArea", "沙湾交管区", geometry="a",
                      geometryStatus="review", geometryEvidence={"kind":"chart-trace","reference":"附图一"}, sourceIndication={
                          "source": ["Announcement No.16", "第16号公告"],
                      })
        report = feature("report", "ShipReport", "过线报告")
        self.write_json("initial.json", [vts, report], {"a": SURFACES["a"]})
        self.run_script("geometry_workflow.py", "draft", "initial.json",
                        "-o", "data.gml", "--report", "draft.md")
        (self.directory / "data.draft.gml").rename(self.directory / "renamed.gml")
        self.run_script("geometry_workflow.py", "check", "renamed.gml",
                        "--report", "check.md", code=1)
        self.assertIn("标记为流程草稿", self.report("check.md"))
        self.assertIn("该报告未与这些区域关联", self.report("check.md"))
        self.assertIn("未提供文件发布日期", self.report("check.md"))
        gml = self.report("renamed.gml")
        self.assertIn("Announcement No.16", gml)
        self.assertNotIn("<reportedDate>", gml)

    def test_candidate_cut_and_stitch_remain_under_review(self):
        geometry = {
            "first": {"type":"surface","exterior":[[30,120],[30.2,120],[30.2,120.2],[30,120.2]]},
            "second": {"type":"surface","exterior":[[30,120.2],[30.2,120.2],[30.2,120.4],[30,120.4]]},
        }
        self.write_json("donors.json", [
            feature("a", "WaterwayArea", "主港航段", geometry="first"),
            feature("b", "WaterwayArea", "主港航段东段", geometry="second"),
        ], geometry)
        self.run_script("build_gml.py", "donors.json", "-o", "donors.gml")
        self.write_json("input.json", [feature(ref, "WaterwayArea", ref) for ref in ("one", "two", "whole")])
        plan = [
            {"type":"split", "donor":"f1", "section":[[30.01,120.1],[30.18,120.1]],
             "left":"one", "right":"two", "reference":"通告附件 1 断面"},
            {"type":"stitch", "donors":["f1","f2"], "target":"whole", "reference":"通告第 2 条"},
        ]
        (self.directory / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
        self.run_script("review_geometry.py", "input.json", "donors.gml", "plan.json", "-o", "candidate.json")
        doc = json.loads(self.report("candidate.json"))
        self.assertTrue(all(f["geometryStatus"] == "review" for f in doc["features"]))
        self.assertIn("约差 1111.0、2222.0 米", doc["features"][0]["geometryEvidence"]["reference"])
        self.assertEqual(len(doc["geometries"][doc["features"][2]["geometry"]]["exterior"]), 7)
        self.run_script("geometry_workflow.py", "complete", "candidate.json",
                        "-o", "candidate.gml", "--featureset-out", "confirmed.json", "--report", "report.md")
        self.assertFalse((self.directory / "candidate.gml").exists())
        self.assertIn("已勾绘，待确认", self.report("report.md"))
        for f in doc["features"]:
            f.pop("geometryStatus")
        (self.directory / "unconfirmed.json").write_text(json.dumps(doc), encoding="utf-8")
        self.run_script("geometry_workflow.py", "complete", "unconfirmed.json", "-o", "unconfirmed.gml",
                        "--featureset-out", "unconfirmed-out.json", "--report", "unconfirmed.md")
        self.assertFalse((self.directory / "unconfirmed.gml").exists())
        for f in doc["features"]:
            f["geometryEvidence"]["reviewReference"] = "人工复核记录 TEST-8"
        (self.directory / "confirmed-input.json").write_text(json.dumps(doc), encoding="utf-8")
        self.run_script("geometry_workflow.py", "complete", "confirmed-input.json", "-o", "confirmed.gml",
                        "--featureset-out", "confirmed-out.json", "--report", "confirmed.md")
        self.assertIn("人工复核记录 TEST-8", self.report("confirmed.md"))
        self.run_script("geometry_workflow.py", "check", "confirmed.gml", "--report", "confirmed-check.md")
        plan[0]["section"] = [[30, 120], [30.3, 119.9]]
        (self.directory / "bad-plan.json").write_text(json.dumps(plan), encoding="utf-8")
        self.run_script("review_geometry.py", "input.json", "donors.gml", "bad-plan.json", "-o", "invalid.json", code=1)
        self.assertFalse((self.directory / "invalid.json").exists())


if __name__ == "__main__":
    unittest.main()
