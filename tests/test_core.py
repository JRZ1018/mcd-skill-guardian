"""Meaningful offline regression cases for declared-call impact checks."""

from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from mcd_guardian.core import (ContractError, check_response, compare_snapshots,
                               validate_manifest)


def snapshot(schema=None, *, name="campaign-calendar"):
    return {"format": "mcd-guardian.snapshot.v1",
            "captured_at": "2026-10-23T10:00:00Z", "mode": "synthetic",
            "server": "https://mcp.mcd.cn", "discovered_tool_count": 1,
            "tools": [{"name": name, "inputSchema": schema if schema is not None else {
                "type": "object", "properties": {}, "additionalProperties": False}}]}


def manifest(arguments=None, *, tool="campaign-calendar", checks=None):
    return {"format": "mcd-guardian.manifest.v1", "skills": [{
        "id": "calendar", "name": "合成日历调用方", "calls": [{
            "id": "fetch", "tool": tool,
            "arguments": arguments if arguments is not None else {},
            "checks": checks if checks is not None else []}]}]}


class DeclaredCallImpactTests(unittest.TestCase):
    def test_new_required_field_regresses_only_the_concrete_call(self):
        before = snapshot({"type": "object", "properties": {"city": {"type": "string"}}})
        after = deepcopy(before)
        after["tools"][0]["inputSchema"]["required"] = ["city"]
        report = compare_snapshots(before, after, manifest())
        self.assertEqual(report["calls"][0]["status"], "regression")
        self.assertEqual(report["summary"]["regressions"], 1)
        self.assertEqual(report["calls"][0]["after"]["issues"][0]["rule"], "required")
        passing = compare_snapshots(before, after, manifest({"city": "合成地点"}))
        self.assertEqual(passing["calls"][0]["status"], "pass")

    def test_enum_narrowing_is_not_always_a_regression(self):
        before = snapshot({"type": "object", "properties": {
            "channel": {"enum": ["pickup", "delivery"]}}})
        after = deepcopy(before)
        after["tools"][0]["inputSchema"]["properties"]["channel"]["enum"] = ["pickup"]
        rejected = compare_snapshots(before, after, manifest({"channel": "delivery"}))
        self.assertEqual(rejected["calls"][0]["status"], "regression")
        accepted = compare_snapshots(before, after, manifest({"channel": "pickup"}))
        self.assertEqual(accepted["calls"][0]["status"], "pass")

    def test_nested_required_paths_identify_all_missing_fields_once(self):
        before = snapshot({"type": "object", "properties": {
            "profile": {"type": "object", "properties": {
                "locale": {"type": "string"}, "a/b~c": {"type": "string"}}}}})
        after = deepcopy(before)
        after["tools"][0]["inputSchema"]["properties"]["profile"]["required"] = ["locale", "a/b~c"]
        report = compare_snapshots(before, after, manifest({"profile": {}}))
        issues = report["calls"][0]["after"]["issues"]
        self.assertEqual(report["calls"][0]["status"], "regression")
        self.assertEqual([issue["path"] for issue in issues],
                         ["/profile/a~1b~0c", "/profile/locale"])
        self.assertEqual([issue["rule"] for issue in issues], ["required", "required"])

    def test_unused_tool_change_is_visible_without_breaking_declared_call(self):
        before = snapshot()
        before["tools"].append({"name": "unused", "inputSchema": {"type": "object"}})
        before["discovered_tool_count"] = 2
        after = deepcopy(before)
        after["tools"][1]["inputSchema"]["required"] = ["new"]
        report = compare_snapshots(before, after, manifest())
        self.assertEqual(report["calls"][0]["status"], "pass")
        self.assertEqual(report["calls"][0]["changes"], [])
        self.assertEqual(report["summary"]["schema_changes"], 1)
        self.assertEqual(report["changes"][0]["tool"], "unused")

    def test_removed_tool_is_a_definite_failure(self):
        before = snapshot()
        after = deepcopy(before)
        after["tools"] = []
        after["discovered_tool_count"] = 0
        report = compare_snapshots(before, after, manifest())
        self.assertEqual(report["calls"][0]["status"], "regression")
        self.assertIs(report["calls"][0]["after"]["valid"], False)
        self.assertEqual(report["calls"][0]["after"]["issues"][0]["rule"], "missing-tool")
        self.assertEqual(report["changes"][0]["path"], "")

    def test_missing_tool_on_both_sides_is_preexisting(self):
        report = compare_snapshots(snapshot(), snapshot(), manifest(tool="absent"))
        self.assertEqual(report["summary"]["preexisting_failures"], 1)
        self.assertEqual(report["summary"]["regressions"], 0)

    def test_existing_invalid_arguments_do_not_become_a_new_regression(self):
        before = snapshot({"type": "object", "required": ["city"]})
        report = compare_snapshots(before, deepcopy(before), manifest())
        self.assertEqual(report["calls"][0]["status"], "preexisting-failure")
        self.assertEqual(report["summary"]["schema_changes"], 0)

    def test_added_tool_or_relaxed_requirement_can_improve(self):
        before = snapshot({"type": "object", "required": ["city"]})
        after = snapshot({"type": "object"})
        report = compare_snapshots(before, after, manifest())
        self.assertEqual(report["summary"]["improved"], 1)
        before["tools"] = []
        before["discovered_tool_count"] = 0
        self.assertEqual(compare_snapshots(before, after, manifest())["calls"][0]["status"], "improved")

    def test_unresolvable_ref_is_unknown_without_network(self):
        ref = snapshot({"$ref": "https://example.invalid/private-schema.json"})
        with patch("urllib.request.urlopen", side_effect=AssertionError("network")) as request:
            report = compare_snapshots(snapshot(), ref, manifest())
        request.assert_not_called()
        self.assertIsNone(report["calls"][0]["after"]["valid"])
        self.assertEqual(report["calls"][0]["status"], "unverifiable")
        self.assertEqual(report["summary"]["unverifiable"], 1)

    def test_local_refs_are_supported(self):
        data = snapshot({"$defs": {"args": {"type": "object", "required": ["city"]}},
                         "$ref": "#/$defs/args"})
        report = compare_snapshots(data, data, manifest({"city": "合成地点"}))
        self.assertEqual(report["calls"][0]["status"], "pass")

    def test_unknown_dialect_or_invalid_schema_is_unverifiable(self):
        for schema in ({"$schema": "https://example.invalid/draft-unknown"},
                       {"type": "made-up"}, "not-a-schema"):
            with self.subTest(schema=schema):
                data = snapshot(schema)
                report = compare_snapshots(data, data, manifest())
                self.assertEqual(report["calls"][0]["status"], "unverifiable")

    def test_boolean_schemas(self):
        report = compare_snapshots(snapshot(True), snapshot(False), manifest())
        self.assertEqual(report["calls"][0]["status"], "regression")

    def test_enum_reordering_is_ignored(self):
        before = snapshot({"type": "object", "properties": {"choice": {"enum": [1, "a", None]}}})
        after = deepcopy(before)
        after["tools"][0]["inputSchema"]["properties"]["choice"]["enum"] = [None, 1, "a"]
        report = compare_snapshots(before, after, manifest({"choice": 1}))
        self.assertEqual(report["changes"], [])
        self.assertEqual(report["calls"][0]["status"], "pass")

    def test_boolean_and_numeric_enum_values_remain_different(self):
        before = snapshot({"type": "object", "properties": {"choice": {"enum": [True]}}})
        after = snapshot({"type": "object", "properties": {"choice": {"enum": [1]}}})
        report = compare_snapshots(before, after, manifest({"choice": True}))
        self.assertEqual(report["calls"][0]["status"], "regression")
        self.assertTrue(report["changes"])

    def test_difference_pointers_escape_keys_and_output_schema_does_not_validate_input(self):
        before = snapshot({"type": "object", "properties": {"a/b~c": {"type": "string"}}})
        after = deepcopy(before)
        after["tools"][0]["inputSchema"]["properties"]["a/b~c"]["type"] = "number"
        after["tools"][0]["outputSchema"] = {"type": "object", "required": ["missing"]}
        report = compare_snapshots(before, after, manifest({"a/b~c": "private-value"}))
        self.assertIn("/inputSchema/properties/a~1b~0c/type", [x["path"] for x in report["changes"]])
        self.assertEqual(report["calls"][0]["after"]["issues"][0]["path"], "/a~1b~0c")
        self.assertEqual(report["calls"][0]["status"], "regression")

    def test_diagnostics_do_not_include_actual_argument_values(self):
        data = snapshot({"type": "object", "properties": {"choice": {"enum": ["allowed"]}},
                         "additionalProperties": False})
        report = compare_snapshots(data, data, manifest({"choice": "secret-token-value"}))
        self.assertNotIn("secret-token-value", json.dumps(report))
        self.assertNotIn("arguments", report["calls"][0])

    def test_no_input_mutation_and_report_changes_are_detached(self):
        before = snapshot()
        after = snapshot({"type": "object", "required": ["city"]})
        declared = manifest()
        originals = deepcopy((before, after, declared))
        report = compare_snapshots(before, after, declared)
        added_required = next(change for change in report["changes"]
                              if change["path"] == "/inputSchema/required")
        added_required["after"].append("other")
        self.assertEqual((before, after, declared), originals)

    def test_empty_manifest_and_metadata(self):
        report = compare_snapshots(snapshot(), snapshot(), {"format": "mcd-guardian.manifest.v1", "skills": []}, mode="synthetic-demo")
        self.assertEqual(report["summary"]["calls"], 0)
        self.assertEqual(report["summary"]["skills"], 0)
        self.assertEqual(report["before"], {"captured_at": "2026-10-23T10:00:00Z", "mode": "synthetic"})
        self.assertEqual(report["mode"], "synthetic-demo")


class ResponsePointerTests(unittest.TestCase):
    def test_missing_and_null_are_distinct(self):
        result = {"present": None}
        self.assertEqual(check_response(result, [{"pointer": "/present", "type": "null"}]), [])
        self.assertEqual(check_response(result, [{"pointer": "/present"}]), [])
        issues = check_response(result, [{"pointer": "/missing", "type": "null"}])
        self.assertEqual(issues[0]["rule"], "presence")
        self.assertEqual(check_response(result, [{"pointer": "/present", "type": "array"}])[0]["rule"], "type")

    def test_root_escape_empty_key_and_array_index(self):
        result = {"a/b": {"~": [{"": "ok"}]} }
        self.assertEqual(check_response(result, [{"pointer": "/a~1b/~0/0/", "type": "string"},
                                                {"pointer": "", "type": "object"}]), [])
        self.assertEqual(check_response([10], [{"pointer": "/0", "type": "number"}]), [])
        for pointer in ("/01", "/-", "/-1", "/1", "/" + "9" * 5000):
            self.assertEqual(check_response([10], [{"pointer": pointer}])[0]["rule"], "presence")

    def test_booleans_are_not_numbers_or_integers(self):
        for expected in ("number", "integer"):
            self.assertEqual(check_response({"v": True}, [{"pointer": "/v", "type": expected}])[0]["rule"], "type")
        self.assertEqual(check_response({"v": False}, [{"pointer": "/v", "type": "boolean"}]), [])

    def test_all_supported_types(self):
        cases = [("string", "x"), ("array", []), ("object", {}),
                 ("number", 1.5), ("integer", 2), ("integer", 2.0),
                 ("boolean", True), ("null", None)]
        for kind, value in cases:
            with self.subTest(kind=kind, value=value):
                self.assertEqual(check_response({"v": value}, [{"pointer": "/v", "type": kind}]), [])
        for value in (1.5, "2", True, float("nan"), float("inf")):
            self.assertTrue(check_response({"v": value}, [{"pointer": "/v", "type": "integer"}]))

    def test_response_diagnostics_do_not_expose_values(self):
        issues = check_response({"v": "private-value"}, [{"pointer": "/v", "type": "array"}])
        self.assertNotIn("private-value", json.dumps(issues))

    def test_invalid_checks_raise_safe_contract_errors(self):
        for checks in (None, {}, [None], [{"pointer": "relative"}],
                       [{"pointer": "/bad~"}], [{"pointer": "/bad~2"}],
                       [{"pointer": "/v", "type": "secret-unsupported-type"}]):
            with self.subTest(checks=checks):
                with self.assertRaises(ContractError) as caught:
                    check_response({}, checks)
                self.assertNotIn("secret-unsupported-type", str(caught.exception))


class ManifestValidationTests(unittest.TestCase):
    def test_checks_default_and_copy(self):
        data = manifest()
        del data["skills"][0]["calls"][0]["checks"]
        clean = validate_manifest(data)
        self.assertEqual(clean["skills"][0]["calls"][0]["checks"], [])
        self.assertNotIn("checks", data["skills"][0]["calls"][0])

    def test_duplicate_skill_and_call_ids(self):
        for field in ("skill", "call"):
            data = manifest()
            if field == "skill":
                data["skills"].append(deepcopy(data["skills"][0]))
            else:
                calls = data["skills"][0]["calls"]
                calls.append(deepcopy(calls[0]))
            with self.assertRaises(ContractError):
                validate_manifest(data)

    def test_invalid_shape_and_non_json_values(self):
        bad = [None, {}, {"format": "wrong", "skills": []}]
        for arguments in ([], None, {"secret": float("nan")}, {1: "not-json-key"}):
            data = manifest()
            data["skills"][0]["calls"][0]["arguments"] = arguments
            bad.append(data)
        for data in bad:
            with self.subTest(data=repr(data)):
                with self.assertRaises(ContractError):
                    validate_manifest(data)

    def test_duplicate_tools_and_bad_snapshot_counts_fail(self):
        data = snapshot()
        data["tools"].append(deepcopy(data["tools"][0]))
        data["discovered_tool_count"] = 2
        with self.assertRaises(ContractError):
            compare_snapshots(data, snapshot(), manifest())
        data = snapshot()
        data["discovered_tool_count"] = 0
        with self.assertRaises(ContractError):
            compare_snapshots(data, snapshot(), manifest())


if __name__ == "__main__":
    unittest.main()
