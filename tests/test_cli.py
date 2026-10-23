"""Observable CLI outcomes and the public/private evidence boundary."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from mcd_guardian.__main__ import receipt
from mcd_guardian.render import render_report
from scripts.build_demo import project_schema

ROOT = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def test_required_drill_has_nonzero_exit_and_standalone_report(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "impact.json"
            run = subprocess.run([sys.executable, "-m", "mcd_guardian", "compare", "--before", "examples/baseline.public.json", "--after", "examples/candidate-required.demo.json", "--out", str(output)], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", env={**__import__('os').environ, "PYTHONIOENCODING": "utf-8"})
            self.assertEqual(run.returncode, 2, run.stderr)
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(report["summary"]["regressions"], 1)
            self.assertTrue(output.with_suffix(".html").is_file())
            self.assertEqual([c["tool"] for c in report["calls"] if c["status"] == "regression"], ["campaign-calendar"])

    def test_unrelated_change_keeps_declared_calls_passing(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "impact.json"
            run = subprocess.run([sys.executable, "-m", "mcd_guardian", "compare", "--before", "examples/baseline.public.json", "--after", "examples/candidate-unrelated.demo.json", "--out", str(output)], cwd=ROOT, capture_output=True)
            self.assertEqual(run.returncode, 0)
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertGreater(report["summary"]["schema_changes"], 0)
            self.assertTrue(all(c["status"] == "pass" for c in report["calls"]))

    def test_static_html_escapes_untrusted_names(self):
        report = {"mode": "synthetic-drill", "calls": [{"skill_name": "<script>alert(1)</script>", "id": "unsafe", "tool": "test", "status": "pass", "after": {"issues": []}}]}
        html = render_report(report)
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("default-src 'none'", html)

    def test_default_compare_html_must_disclose_synthetic_candidate(self):
        html = render_report({"mode": "comparison", "before": {"mode": "live"}, "after": {"mode": "synthetic"}})
        self.assertIn("合成演练", html)
        self.assertIn("不代表官方", html)

    def test_schema_annotation_projection_preserves_named_parameters_and_literals(self):
        schema = {"title": "Annotation", "properties": {"title": {"type": "string", "description": "Annotation"}, "default": {"const": {"title": "literal", "default": 3}}}, "enum": [{"description": "literal"}]}
        projected = project_schema(schema)
        self.assertNotIn("title", projected)
        self.assertIn("title", projected["properties"])
        self.assertNotIn("description", projected["properties"]["title"])
        self.assertEqual(projected["properties"]["default"]["const"], {"title": "literal", "default": 3})
        self.assertEqual(projected["enum"], [{"description": "literal"}])


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.manifest = {"format": "mcd-guardian.manifest.v1", "skills": [{"id": "fixture", "name": "合成测试", "calls": [{"id": "call", "tool": "campaign-calendar", "arguments": {"private-note": "DO-NOT-PUBLISH"}, "checks": [{"pointer": "/structuredContent/data", "type": "array"}]}]}]}
        self.probe = {"format": "mcd-guardian.probe.v1", "mode": "live", "server": "https://mcp.mcd.cn", "captured_at": "2026-10-23T00:00:00Z", "calls": [{"skill_id": "fixture", "id": "call", "tool": "campaign-calendar", "business_status": "pass", "result": {"isError": False, "structuredContent": {"success": True, "code": 200, "data": ["SECRET-RESPONSE-VALUE"]}}}]}

    def test_projection_excludes_arguments_and_response_values(self):
        result = receipt(self.probe, self.manifest)
        encoded = json.dumps(result)
        self.assertEqual(result["passed"], 1)
        self.assertNotIn("DO-NOT-PUBLISH", encoded)
        self.assertNotIn("SECRET-RESPONSE-VALUE", encoded)

    def test_error_cannot_be_marked_pass_by_stale_status(self):
        self.probe["calls"][0]["result"]["isError"] = True
        self.assertEqual(receipt(self.probe, self.manifest)["passed"], 0)

    def test_missing_field_cannot_pass(self):
        del self.probe["calls"][0]["result"]["structuredContent"]["data"]
        self.assertEqual(receipt(self.probe, self.manifest)["passed"], 0)

    def test_other_server_is_not_a_live_receipt(self):
        self.probe["server"] = "https://example.invalid"
        with self.assertRaises(ValueError):
            receipt(self.probe, self.manifest)

    def test_duplicate_call_is_rejected(self):
        self.probe["calls"].append(deepcopy(self.probe["calls"][0]))
        with self.assertRaises(ValueError):
            receipt(self.probe, self.manifest)

    def test_payload_cannot_hide_in_scalar_metadata(self):
        for field in ("captured_at", "business_status"):
            probe = deepcopy(self.probe)
            target = probe if field == "captured_at" else probe["calls"][0]
            target[field] = {"PRIVATE": "do not copy"}
            with self.assertRaises(ValueError):
                receipt(probe, self.manifest)


if __name__ == "__main__":
    unittest.main()
