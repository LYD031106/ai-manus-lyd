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
        self.assertNotIn("restriction", first["attributes"])
        self.assertNotIn("restriction", second["attributes"])
        self.assertIn("限制编码缺少现行适用条款", self.report("bridge.md"))
        self.assertNotIn("textContent", second)
        self.assertIn("当地高程基面", self.report("bridge.draft.gml"))
        self.assertNotIn("90 米", self.report("bridge.draft.gml"))

    def test_restriction_evidence_flags_technical_width_exceptions_and_nested_areas(self):
        self.write_json("seed.json", [
            feature("bridge", "RestrictedAreaNavigational", "北湾桥水域",
                    sourceBasis={"kind": "bridge-information", "reference": "桥梁资料表"}),
            feature("channel", "RestrictedAreaNavigational", "北湾通航孔航道"),
            feature("approach", "RestrictedAreaNavigational", "北湾桥区进港水域"),
        ])
        doc = json.loads(self.report("seed.json"))
        doc["restrictionClauses"] = [
            {"reference": "桥梁资料表第2行「通航净空宽度」", "evidence": "通航净空宽度 75 米",
             "scope": "area-wide", "code": 37, "targets": ["bridge"], "evidenceKind": "regulatory-clause"},
            {"reference": "北湾通航规则第六条", "evidence": "船舶在北湾桥水域内禁止掉头",
             "scope": "area-wide", "code": 35, "targets": ["bridge"], "evidenceKind": "regulatory-clause"},
            {"reference": "北湾通航规则第七条", "evidence": "禁止在本航道内锚泊。",
             "scope": "area-wide", "code": 1, "targets": ["channel"]},
            {"reference": "北湾通航规则第八条", "evidence": "禁止在桥区水域范围内的通航孔航道追越。",
             "scope": "area-wide", "code": 28, "targets": ["channel"]},
            {"reference": "北湾通航规则第九条", "evidence": "除维护船外，禁止驶入进港水域。",
             "scope": "area-wide", "code": 7, "targets": ["approach"]},
        ]
        (self.directory / "seed.json").write_text(json.dumps(doc), encoding="utf-8")
        self.run_script("geometry_workflow.py", "draft", "seed.json", "-o", "guarded.gml",
                        "--featureset-out", "guarded.json", "--report", "guarded.md")
        bridge, channel, approach = json.loads(self.report("guarded.json"))["features"]
        self.assertEqual(bridge["attributes"]["restriction"], [35])
        self.assertEqual(channel["attributes"]["restriction"], [1])
        self.assertNotIn("restriction", approach["attributes"])
        report = self.report("guarded.md")
        self.assertIn("桥孔净空宽度属于技术尺度", report)
        self.assertIn("原文限定了局部范围", report)
        self.assertIn("原文包含例外或附加条件", report)
        self.run_script("geometry_workflow.py", "complete", "guarded.json", "-o", "completed.gml",
                        "--featureset-out", "rechecked.json", "--report", "completed.md")
        self.assertEqual(json.loads(self.report("rechecked.json"))["features"], [bridge, channel, approach])

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

    def test_area_restrictions_are_derived_from_scope_across_both_phases(self):
        self.write_json("seed.json", [
            feature("west", "WaterwayArea", "白沙西航道", geometry="a", attributes={"dynamicResource": 1},
                    geometryEvidence={"kind": "source-boundary", "reference": "附图一"},
                    associations=[{"role": "theRxN", "target": "operations"}]),
            feature("east", "CautionArea", "白沙东警戒区", geometry="b", attributes={"restriction": [30]},
                    geometryEvidence={"kind": "source-boundary", "reference": "附图二"},
                    associations=[{"role": "theRxN", "target": "operations"}]),
            feature("operations", "Restrictions", "捕捞及追越条件",
                    textContent={"information": {"text": {"zho": "禁止捕捞。能见度不良时禁止客船追越。"}}}),
        ], SURFACES)
        seed = json.loads(self.report("seed.json"))
        seed["restrictionClauses"] = [
            {"scope": "area-wide", "targets": ["east", "west"], "reference": "第八条",
             "evidence": "附图一和附图二范围内禁止锚泊。", "code": "anchoring prohibited"},
            {"scope": "area-wide", "targets": ["east", "west"], "reference": "第九条",
             "evidence": "附图一和附图二范围内禁止捕捞。", "code": 3, "regulation": "operations"},
            {"scope": "conditional", "targets": ["east"], "reference": "第十条", "regulation": "operations",
             "evidence": "能见度不良时禁止客船追越。"},
            {"scope": "partial", "targets": ["west"], "reference": "第十一条",
             "evidence": "仅对西航道入口的限制适用，尚缺保留范围的规则。"},
        ]
        (self.directory / "seed.json").write_text(json.dumps(seed), encoding="utf-8")
        self.run_script("geometry_workflow.py", "draft", "seed.json", "-o", "data.gml",
                        "--featureset-out", "source.json", "--report", "draft.md")
        result = json.loads(self.report("source.json"))
        west, east, _ = result["features"]
        for area in (west, east):
            self.assertEqual(area["type"], "RestrictedAreaNavigational")
            self.assertEqual(area["attributes"], {"restriction": [1]})
        self.assertEqual(west["sourceReview"][0]["reference"], "第十一条")
        self.assertIn("禁止捕捞", self.report("data.draft.gml"))
        self.assertIn("能见度不良时禁止客船追越", self.report("data.draft.gml"))

        self.run_script("geometry_workflow.py", "complete", "source.json", "-o", "promoted.gml",
                        "--featureset-out", "rechecked.json", "--report", "complete.md", "--allow-restriction-3")
        rechecked = json.loads(self.report("rechecked.json"))
        self.assertEqual(rechecked["features"][0]["attributes"]["restriction"], [1, 3])
        self.assertEqual(len(rechecked["features"][0]["sourceReview"]), 1)
        self.assertFalse((self.directory / "promoted.gml").exists())
        self.assertTrue((self.directory / "promoted.draft.gml").exists())

        result["restrictionClauses"].pop()
        (self.directory / "resolved.json").write_text(json.dumps(result), encoding="utf-8")
        self.run_script("geometry_workflow.py", "complete", "resolved.json", "-o", "final.gml",
                        "--featureset-out", "ready.json", "--report", "ready.md")
        final = self.report("final.gml")
        altered = final.replace('<restriction code="1">anchoring prohibited</restriction>',
                                '<restriction code="8">entry restricted</restriction>', 1)
        (self.directory / "changed.gml").write_text(altered, encoding="utf-8")
        self.run_script("geometry_workflow.py", "check", "changed.gml", "--source-featureset", "resolved.json",
                        "--report", "checked.md", code=1)
        self.assertIn("全区 restriction 应为 [1]，GML 实际为 [8]", self.report("checked.md"))

        seed["restrictionClauses"][2]["code"] = 28
        (self.directory / "seed.json").write_text(json.dumps(seed), encoding="utf-8")
        self.run_script("geometry_workflow.py", "draft", "seed.json", "-o", "wrong.gml",
                        "--report", "wrong.md", code=1)
        self.assertIn("不可填写全区 restriction 码", self.report("wrong.md"))

    def test_conditional_underkeel_preserves_applicability_and_operator(self):
        underkeel = feature("depth", "UnderkeelClearanceAllowanceArea", "东湾水域",
                            sourceBasis={"kind": "conditional-underkeel", "reference": "港区规则 第三条"},
                            attributes={"underkeelAllowance": {"underkeelAllowanceFixed": 0.6,
                                                               "underkeelAllowanceVariableDraughtBased": 9}})
        self.write_json("unscoped.json", [underkeel])
        self.run_script("geometry_workflow.py", "draft", "unscoped.json", "-o", "wrong.gml",
                        "--report", "wrong.md", code=1)
        self.assertIn("须以 theRxN 关联包含条文和适用对象", self.report("wrong.md"))
        self.assertIn("同时填写多种富余水深参数时须按原文明确 operation", self.report("wrong.md"))
        underkeel["attributes"]["underkeelAllowance"]["operation"] = "largest value"
        underkeel["associations"] = [{"role": "theRxN", "target": "rule"}]
        rule = feature("rule", "Regulations", "富余水深限定",
                       textContent={"information": {"text": {"zho": "客船富余水深须大于吃水比例阈值，且不小于固定阈值。"}}},
                       associations=[{"role": "isApplicableTo", "target": "vessels", "inclusion": "included"}])
        vessels = feature("vessels", "Applicability", "客船", attributes={"information": {"text": {"zho": "客船"}}})
        self.write_json("scoped.json", [underkeel, rule, vessels])
        self.run_script("geometry_workflow.py", "draft", "scoped.json", "-o", "depth.gml", "--report", "depth.md")
        result = self.report("depth.draft.gml")
        self.assertIn("客船富余水深须大于吃水比例阈值，且不小于固定阈值", result)
        self.assertIn('<operation code="1">largest value</operation>', result)

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
        self.assertFalse((self.directory / "data.gml").exists())
        self.assertIn("同名候选没有独立锚点", self.report("completed.md"))
        self.run_script("geometry_workflow.py", "complete", "initial.json", "geometry.gml",
                        "-o", "data.gml", "--featureset-out", "completed.json",
                        "--report", "completed.md", "--assoc-csv", "associations.csv", "--match", "vts=f1")
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

    def test_independent_source_anchor_rejects_same_name_wrong_location(self):
        ref = feature("sector", "RestrictedAreaNavigational", "水上作业区",
                      attributes={"restriction": [7]},
                      geometryAnchors=[{"position":[30.025,122.025],"reference":"原公告附件坐标表第 2 行"}])
        self.write_json("source.json", [ref])
        for label, geometry in (("wrong", "b"), ("correct", "a")):
            self.write_json(f"{label}.json", [{k: v for k, v in ref.items() if k != "geometryAnchors"} |
                                             {"geometry": geometry}], SURFACES)
            self.run_script("build_gml.py", f"{label}.json", "-o", f"{label}.gml")
        cmd = ("complete", "source.json", "wrong.gml", "-o", "out.gml",
               "--featureset-out", "out.json", "--report", "out.md")
        self.run_script("geometry_workflow.py", *cmd, "--match", "sector=f1")
        self.assertFalse((self.directory / "out.gml").exists())
        self.assertIn("超出 0 米容差，需复核空间位置", self.report("out.md"))
        self.run_script("geometry_workflow.py", "check", "wrong.gml", "--report", "structure.md")
        self.assertIn("不核对原文空间位置", self.report("structure.md"))
        self.run_script("geometry_workflow.py", "check", "wrong.gml", "--source-featureset", "source.json",
                        "--report", "location.md", code=1)
        self.assertIn("原公告附件坐标表第 2 行", self.report("location.md"))

        self.run_script("geometry_workflow.py", *cmd[:2], "correct.gml", *cmd[3:])
        self.assertIn("已通过原文锚点检查", self.report("out.md"))
        self.run_script("geometry_workflow.py", "check", "out.gml", "--source-featureset", "source.json",
                        "--report", "checked.md")

    def test_unresolved_citation_cannot_be_promoted_by_complete_geometry(self):
        sample = feature("sector", "WaterwayArea", "西滩水域", geometry="a",
                         geometryEvidence={"kind":"source-boundary", "reference":"通告附件坐标"},
                         sourceReview=[{"reference":"通告援引的上位文件", "reason":"没有核到续期文件"}])
        self.write_json("source.json", [sample], SURFACES)
        self.run_script("geometry_workflow.py", "complete", "source.json", "-o", "out.gml",
                        "--featureset-out", "out.json", "--report", "out.md")
        self.assertFalse((self.directory / "out.gml").exists())
        self.assertIn("没有核到续期文件", self.report("out.md"))
        self.assertIn("已具备", self.report("out.md"))

    def test_date_line_surface_does_not_wrap_across_a_distant_anchor(self):
        geometries = {"area": {"type": "surface", "exterior": [
            [-1, 179], [-1, -179], [1, -179], [1, 179], [-1, 179],
        ]}}
        region = feature("area", "RestrictedAreaNavigational", "经线测试区", geometry="area",
                         attributes={"restriction": [7]},
                         geometryEvidence={"kind": "source-boundary", "reference": "边界附件"})
        args = ("complete", "source.json", "--featureset-out", "out.json", "--report", "out.md")
        self.write_json("source.json", [region | {"geometryAnchors": [
            {"position": [0, 180], "reference": "经线东侧海图锚点"},
        ]}], geometries)
        self.run_script("geometry_workflow.py", *args, "-o", "out.gml")
        self.assertTrue((self.directory / "out.gml").exists())

        self.write_json("source.json", [region | {"geometryAnchors": [
            {"position": [0, 0], "reference": "远侧海图锚点"},
        ]}], geometries)
        self.run_script("geometry_workflow.py", *args, "-o", "rejected.gml")
        self.assertFalse((self.directory / "rejected.gml").exists())
        self.assertIn("远侧海图锚点", self.report("out.md"))

    def test_coordinate_plan_uses_explicit_indices_and_rejects_missing_rows(self):
        (self.directory / "positions.csv").write_text("lat,lon\n10,40\n11,40\n10,41\n", encoding="utf-8")
        plan = self.directory / "plan.json"
        plan.write_text(json.dumps({"area": {"type": "surface", "rows": [3, 1, 2]},
                                    "line": {"type": "curve", "rows": [2, 1]}}), encoding="utf-8")
        result = subprocess.check_output([sys.executable, str(SCRIPTS / "coords.py"), "--csv", "positions.csv",
                                          "--plan", "plan.json"], cwd=self.directory, encoding="utf-8")
        self.assertEqual(json.loads(result)["area"]["exterior"], [[10, 41], [10, 40], [11, 40]])
        plan.write_text(json.dumps({"bad": {"type": "curve", "rows": [1, 4]}}), encoding="utf-8")
        self.run_script("coords.py", "--csv", "positions.csv", "--plan", "plan.json", code=1)

    def test_official_coverage_respects_holes_and_can_be_required(self):
        region = feature("area", "WaterwayArea", "虚构作业范围", geometry="a",
                         geometryEvidence={"kind": "source-boundary", "reference": "通告附表"})
        shape = {"type": "surface", "exterior": [[29, 121], [32, 121], [32, 123], [29, 123]]}
        data = {"dataset": self.dataset, "features": [region], "geometries": {**SURFACES, "coverage": shape}}
        seed = self.directory / "initial.json"
        seed.write_text(json.dumps(data), encoding="utf-8")
        self.run_script("build_gml.py", "initial.json", "-o", "unchecked.gml")
        self.run_script("geometry_workflow.py", "check", "unchecked.gml", "--require-coverage", "--report", "audit.md", code=1)
        self.assertIn("最终发布检查需要正式 DataCoverage 面", self.report("audit.md"))
        data["coverageAreas"] = [{"geometry": "coverage", "reference": "独立图层说明"}]
        seed.write_text(json.dumps(data), encoding="utf-8")
        self.run_script("build_gml.py", "initial.json", "-o", "covered.gml")
        self.run_script("geometry_workflow.py", "check", "covered.gml", "--require-coverage", "--report", "audit.md")
        self.assertIn("确实落在 DataCoverage", self.report("audit.md"))
        shape["interiors"] = [[[30.01, 122.01], [30.04, 122.01], [30.04, 122.04], [30.01, 122.04]]]
        seed.write_text(json.dumps(data), encoding="utf-8")
        self.run_script("build_gml.py", "initial.json", "-o", "with-hole.gml")
        self.run_script("geometry_workflow.py", "check", "with-hole.gml", "--report", "audit.md", code=1)
        self.assertIn("落入其空洞", self.report("audit.md"))

    def test_sourced_name_and_calendar_end_conventions(self):
        data = {"dataset": self.dataset, "common": {"validityBasis": {
            "reference": "虚构公告附则", "effectiveDate": "2027-05-18", "durationYears": 2,
            "dateEndConvention": "expiry-boundary"}},
            "features": [feature("area", "WaterwayArea", "旧名称",
                                 sourceBasis={"kind": "bridge-information", "reference": "虚构桥梁附表",
                                              "originalName": "星桥水域"})]}
        seed = self.directory / "initial.json"
        seed.write_text(json.dumps(data), encoding="utf-8")
        self.run_script("geometry_workflow.py", "draft", "initial.json", "-o", "data.gml",
                        "--featureset-out", "draft.json", "--report", "draft.md")
        produced = json.loads(self.report("draft.json"))
        self.assertEqual(produced["features"][0]["featureName"]["zho"], "星桥水域")
        self.assertEqual(produced["common"]["fixedDateRange"]["dateEnd"], "2029-05-18")
        self.assertIn("最后有效日 2029-05-17", self.report("draft.md"))

        data["common"]["validityBasis"].pop("dateEndConvention")
        seed.write_text(json.dumps(data), encoding="utf-8")
        self.run_script("geometry_workflow.py", "draft", "initial.json", "-o", "data.gml",
                        "--featureset-out", "draft.json", "--report", "draft.md")
        produced = json.loads(self.report("draft.json"))
        self.assertNotIn("dateEnd", produced["common"]["fixedDateRange"])
        self.assertIn("尚需确认 GML dateEnd", self.report("draft.md"))

        produced["common"]["validityBasis"]["dateEndConvention"] = "last-valid-day"
        produced["common"]["sourceReview"].append({"reference": "原文附图", "reason": "缺海图"})
        seed.write_text(json.dumps(produced), encoding="utf-8")
        self.run_script("geometry_workflow.py", "draft", "initial.json", "-o", "data.gml",
                        "--featureset-out", "draft.json", "--report", "draft.md")
        self.assertNotIn("尚需确认 GML dateEnd", self.report("draft.md"))
        self.assertIn("缺海图", self.report("draft.md"))

    def test_replacing_candidate_keeps_geometry_also_used_as_coverage(self):
        flow = ("geometry_workflow.py", "complete", "initial.json", "donor.gml", "-o", "out.gml",
                "--featureset-out", "out.json", "--report", "report.md", "--match", "area=f1")
        geoms = {"covered": {"type": "surface", "exterior": [[29, 121], [32, 121], [32, 123], [29, 123]]},
                 **SURFACES}
        feat = feature("area", "WaterwayArea", "试验范围", geometry="covered", geometryStatus="review",
                       geometryEvidence={"kind": "chart-trace", "reference": "附图"})
        initial = {"dataset": self.dataset, "geometries": geoms, "features": [feat],
                   "coverageAreas": [{"geometry": "covered", "reference": "正式覆盖层"}]}
        (self.directory / "initial.json").write_text(json.dumps(initial), encoding="utf-8")
        self.write_json("donor.json", [{**feat, "geometry": "a"}], geoms)
        self.run_script("build_gml.py", "donor.json", "-o", "donor.gml")
        self.run_script(*flow)
        self.assertIn("<DataCoverage", self.report("out.gml"))
        self.assertIn("covered", json.loads(self.report("out.json"))["geometries"])


if __name__ == "__main__":
    unittest.main()
