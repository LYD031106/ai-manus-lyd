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
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)

    def report(self, name):
        return (self.directory / name).read_text(encoding="utf-8")

    def test_three_phases_preserve_vts_associations(self):
        vts = feature("vts", "VesselTrafficServiceArea", "洋山交管区",
                      associations=[{"role": "reptForTrafficServ", "target": "report"}])
        report = feature("report", "ShipReport", "过线报告")
        self.write_json("initial.json", [vts, report])
        self.run_script("geometry_workflow.py", "draft", "initial.json",
                        "-o", "data.gml", "--report", "draft.md")
        draft = ET.parse(self.directory / "data.draft.gml").getroot()
        self.assertIsNone(draft.find(f"{GML}boundedBy"))
        self.assertIn("洋山交管区", self.report("draft.md"))
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

    def test_same_name_geometry_requires_explicit_mapping_and_bridge_profile(self):
        restriction = ["entry restricted", "passing or overtaking prohibited", "fishing prohibited"]
        first = feature("one", "RestrictedAreaNavigational", "东海大桥桥区安全水域",
                        attributes={"restriction": restriction}, geometryStatus="review", geometry="b")
        second = {**first, "ref": "two", "geometry": None}
        self.write_json("initial.json", [first, second], SURFACES)
        self.write_json("donor.json", [{**first, "geometry": "a"}, {**second, "geometry": "b"}], SURFACES)
        self.run_script("build_gml.py", "donor.json", "-o", "geometry.gml")
        self.run_script("validate_featureset.py", "initial.json", code=1)
        self.run_script("validate_featureset.py", "initial.json", "--bridge-profile")

        command = ("complete", "initial.json", "geometry.gml", "-o", "data.gml",
                   "--featureset-out", "completed.json", "--report", "completed.md", "--bridge-profile")
        self.run_script("geometry_workflow.py", *command)
        self.assertFalse((self.directory / "data.gml").exists())
        self.assertIn("同名有歧义", self.report("completed.md"))
        self.assertIn("已勾绘，待确认", self.report("completed.md"))

        self.run_script("geometry_workflow.py", *command, "--match", "one=f1", "--match", "two=f2")
        final = self.report("data.gml")
        self.assertIn('code="30">passing or overtaking prohibited', final)
        self.assertIn('code="3">fishing prohibited', final)
        doc = json.loads(self.report("completed.json"))
        self.assertEqual([f["geometry"] for f in doc["features"]], ["补录-one", "补录-two"])
        self.assertNotIn("geometryStatus", doc["features"][0])
        self.assertNotEqual(doc["geometries"]["补录-one"], doc["geometries"]["补录-two"])

        self.run_script("geometry_workflow.py", "check", "data.gml",
                        "--report", "final.md", "--bridge-profile")
        self.assertIn("超出 S127FC", self.report("final.md"))
        corrupted = final.replace('code="30"', 'code="28"').replace('xlink:href="#s1"', 'xlink:href="#missing"')
        (self.directory / "broken.gml").write_text(corrupted, encoding="utf-8")
        self.run_script("geometry_workflow.py", "check", "broken.gml",
                        "--report", "broken.md", "--bridge-profile", code=1)
        self.assertIn("本地引用 '#missing' 不存在", self.report("broken.md"))
        self.assertIn("code 应为 30", self.report("broken.md"))
        self.assertIn("东海大桥桥区安全水域", self.report("broken.md"))

    def test_renamed_draft_and_unlinked_vts_report_remain_visible(self):
        vts = feature("vts", "VesselTrafficServiceArea", "洋山交管区", geometry="a",
                      geometryStatus="review", sourceIndication={
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


if __name__ == "__main__":
    unittest.main()
