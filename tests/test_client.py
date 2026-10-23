"""No-network tests for official contract discovery and declared read-only calls."""

from contextlib import asynccontextmanager, redirect_stderr, redirect_stdout
from copy import deepcopy
from datetime import datetime
import io
import logging
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from mcp.types import CallToolResult, TextContent, Tool

from mcd_guardian import client


SCHEMA = {"type": "object", "properties": {"query": {"type": "string"}},
          "required": ["query"], "additionalProperties": False}


def manifest(*tools):
    return {"format": "mcd-guardian.manifest.v1", "skills": [{
        "id": "synthetic-skill", "calls": [
            {"id": f"call-{index}", "tool": tool, "arguments": {"query": "example"},
             "checks": [{"pointer": "/success", "equals": True}]}
            for index, tool in enumerate(tools or ("campaign-calendar",))
        ],
    }]}


def session(*tools, response=None):
    tools = tools or tuple(sorted(client.READ_ONLY_TOOLS))
    contracts = [Tool(name=name, description="Not part of the captured contract",
                      inputSchema=deepcopy(SCHEMA)) for name in tools]
    if response is None:
        response = CallToolResult(
            content=[TextContent(type="text", text="Synthetic private result")],
            structuredContent={"success": True, "code": 200, "data": []},
        )
    return SimpleNamespace(
        list_tools=AsyncMock(return_value=SimpleNamespace(tools=contracts, nextCursor=None)),
        call_tool=AsyncMock(return_value=response),
    )


@asynccontextmanager
async def context(value):
    yield value


class GuardianClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_token_does_not_connect(self):
        with (patch.dict(client.os.environ, {}, clear=True),
              patch.object(client.sys.stdin, "isatty", return_value=False),
              patch.object(client, "streamablehttp_client") as transport):
            with self.assertRaisesRegex(client.GuardianClientError, "missing"):
                await client.capture()
        transport.assert_not_called()

    async def test_capture_follows_pages_and_retains_only_contract_fields(self):
        fake = session()
        fake.list_tools.side_effect = [
            SimpleNamespace(tools=[Tool(name=f"tool-{index:02}", inputSchema={"type": "object"})
                                  for index in range(20)], nextCursor="page-2"),
            SimpleNamespace(tools=[Tool(name=f"tool-{index:02}", inputSchema={"type": "object"},
                                       outputSchema={"type": "object"}, description="omit me")
                                  for index in range(20, 35)], nextCursor=None),
        ]
        with patch.object(client, "_session", lambda: context(fake)):
            captured = await client.capture()
        self.assertEqual(captured["format"], "mcd-guardian.snapshot.v1")
        self.assertEqual(captured["mode"], "live")
        self.assertEqual(captured["server"], "https://mcp.mcd.cn")
        self.assertEqual(captured["discovered_tool_count"], 35)
        self.assertEqual(len(captured["tools"]), 35)
        self.assertEqual(set(captured["tools"][0]), {"name", "inputSchema", "outputSchema"})
        self.assertIsNone(captured["tools"][0]["outputSchema"])
        self.assertEqual(captured["tools"][-1]["outputSchema"], {"type": "object"})
        self.assertIsNotNone(datetime.fromisoformat(captured["captured_at"]).utcoffset())
        fake.list_tools.assert_any_await(cursor="page-2")
        fake.call_tool.assert_not_awaited()

    async def test_repeated_cursor_fails_without_business_call(self):
        fake = session()
        fake.list_tools.return_value = SimpleNamespace(tools=[], nextCursor="same-page")
        with patch.object(client, "_session", lambda: context(fake)):
            with self.assertRaisesRegex(client.GuardianClientError, "repeated"):
                await client.capture()
        self.assertEqual(fake.list_tools.await_count, 2)
        fake.call_tool.assert_not_awaited()

    async def test_duplicate_discovered_name_is_rejected(self):
        fake = session("campaign-calendar", "campaign-calendar")
        with patch.object(client, "_session", lambda: context(fake)):
            with self.assertRaisesRegex(client.GuardianClientError, "duplicate"):
                await client.capture()

    async def test_disallowed_tool_rejected_before_session_or_token(self):
        for tool in ("cancel-order", "create-order", "unknown-tool"):
            with self.subTest(tool=tool), patch.object(client, "_session") as transport:
                with self.assertRaisesRegex(client.GuardianClientError, "allowlist"):
                    await client.probe(manifest(tool))
                transport.assert_not_called()

    async def test_every_input_is_validated_before_first_call(self):
        fake = session()
        declared = manifest("campaign-calendar", "list-nutrition-foods")
        declared["skills"][0]["calls"][1]["arguments"] = {}
        with patch.object(client, "_session", lambda: context(fake)):
            with self.assertRaisesRegex(client.GuardianClientError, "required"):
                await client.probe(declared)
        fake.call_tool.assert_not_awaited()

    async def test_missing_later_tool_prevents_all_calls(self):
        fake = session("campaign-calendar")
        with patch.object(client, "_session", lambda: context(fake)):
            with self.assertRaisesRegex(client.GuardianClientError, "not discovered"):
                await client.probe(manifest("campaign-calendar", "query-nearby-stores"))
        fake.call_tool.assert_not_awaited()

    async def test_external_schema_reference_is_not_fetched_or_called(self):
        fake = session("campaign-calendar")
        fake.list_tools.return_value.tools[0].inputSchema = {
            "$ref": "https://example.invalid/private-schema"
        }
        with patch.object(client, "_session", lambda: context(fake)):
            with self.assertRaisesRegex(client.GuardianClientError, "locally"):
                await client.probe(manifest())
        fake.call_tool.assert_not_awaited()

    async def test_unknown_schema_dialect_prevents_every_business_call(self):
        fake = session()
        fake.list_tools.return_value.tools[1].inputSchema = {
            **deepcopy(SCHEMA), "$schema": "https://example.invalid/unknown-dialect"
        }
        declared = manifest("campaign-calendar", fake.list_tools.return_value.tools[1].name)
        with patch.object(client, "_session", lambda: context(fake)):
            with self.assertRaisesRegex(client.GuardianClientError, "unsupported dialect"):
                await client.probe(declared)
        fake.call_tool.assert_not_awaited()

    async def test_arguments_and_manifest_shape_are_checked_before_connection(self):
        invalid = []
        missing = manifest()
        del missing["skills"][0]["calls"][0]["arguments"]
        invalid.append(missing)
        non_json = manifest()
        non_json["skills"][0]["calls"][0]["arguments"] = {1: "value"}
        invalid.append(non_json)
        duplicate = manifest()
        duplicate["skills"][0]["calls"] *= 2
        invalid.append(duplicate)
        invalid.extend([None, {}, {"format": client.MANIFEST_FORMAT, "skills": {}}])
        for declared in invalid:
            with self.subTest(declared=declared), patch.object(client, "_session") as transport:
                with self.assertRaises(client.GuardianClientError):
                    await client.probe(declared)
                transport.assert_not_called()

    async def test_probe_calls_serially_in_one_session_and_never_prints_result(self):
        fake = session()
        events = []

        async def record_call(name, arguments):
            events.append(name)
            return CallToolResult(content=[TextContent(type="text", text="private-account-marker")],
                                  structuredContent={"success": True, "code": 200})

        fake.call_tool.side_effect = record_call
        declared = manifest("campaign-calendar", "list-nutrition-foods", "query-nearby-stores")
        original = deepcopy(declared)
        stdout, stderr = io.StringIO(), io.StringIO()
        with (patch.object(client, "_session", lambda: context(fake)) as opened,
              redirect_stdout(stdout), redirect_stderr(stderr)):
            result = await client.probe(declared)
        self.assertEqual(declared, original)
        self.assertEqual(events, [call["tool"] for call in declared["skills"][0]["calls"]])
        self.assertEqual(result["format"], "mcd-guardian.probe.v1")
        self.assertEqual(result["snapshot"]["discovered_tool_count"], 3)
        self.assertEqual(len(result["calls"]), 3)
        for index, call in enumerate(result["calls"]):
            self.assertEqual(call["skill_id"], "synthetic-skill")
            self.assertEqual(call["id"], f"call-{index}")
            self.assertEqual(call["business_status"], "pass")
            self.assertGreaterEqual(call["elapsed_ms"], 0)
            self.assertIsNotNone(datetime.fromisoformat(call["captured_at"]).utcoffset())
            self.assertIn("private-account-marker", call["result"]["content"][0]["text"])
        self.assertEqual(stdout.getvalue() + stderr.getvalue(), "")
        fake.list_tools.assert_awaited_once()

    async def test_tool_and_business_failure_are_failed_not_empty_success(self):
        variants = [
            CallToolResult(content=[], isError=True),
            CallToolResult(content=[], structuredContent={"success": False, "code": 200}),
            CallToolResult(content=[], structuredContent={"success": True, "code": 500}),
            CallToolResult(content=[], structuredContent={"success": True, "code": "200"}),
        ]
        for response in variants:
            with self.subTest(response=response):
                fake = session(response=response)
                with patch.object(client, "_session", lambda: context(fake)):
                    result = await client.probe(manifest())
                self.assertEqual(result["calls"][0]["business_status"], "fail")

    async def test_unknown_or_nested_business_status_is_explicitly_unknown(self):
        variants = [None, {"data": {"success": True, "code": 200}},
                    {"success": True}, {"code": 200}, {"items": []}]
        for structured in variants:
            with self.subTest(structured=structured):
                fake = session(response=CallToolResult(content=[], structuredContent=structured))
                with patch.object(client, "_session", lambda: context(fake)):
                    result = await client.probe(manifest())
                self.assertEqual(result["calls"][0]["business_status"], "unknown")

    async def test_validation_error_does_not_echo_raw_argument_value(self):
        fake = session()
        declared = manifest()
        declared["skills"][0]["calls"][0]["arguments"] = {"query": {"secret": "PRIVATE-VALUE"}}
        with patch.object(client, "_session", lambda: context(fake)):
            with self.assertRaises(client.GuardianClientError) as caught:
                await client.probe(declared)
        self.assertNotIn("PRIVATE-VALUE", str(caught.exception))
        fake.call_tool.assert_not_awaited()

    async def test_sdk_error_and_logging_hide_payloads_and_headers(self):
        @asynccontextmanager
        async def broken_transport(*args, **kwargs):
            logging.getLogger("httpx").warning("Authorization: Bearer PRIVATE-TOKEN")
            logging.getLogger("mcp.client").warning("PRIVATE-PAYLOAD")
            raise RuntimeError("Authorization: Bearer PRIVATE-TOKEN PRIVATE-PAYLOAD")
            yield

        stderr, stdout = io.StringIO(), io.StringIO()
        with (patch.dict(client.os.environ, {"MCD_MCP_TOKEN": "PRIVATE-TOKEN"}),
              patch.object(client, "streamablehttp_client", broken_transport),
              redirect_stdout(stdout), redirect_stderr(stderr)):
            with self.assertRaises(client.GuardianClientError) as caught:
                await client.capture()
        self.assertNotIn("PRIVATE-", str(caught.exception) + stderr.getvalue() + stdout.getvalue())
        self.assertTrue(caught.exception.__suppress_context__)

    async def test_safe_error_survives_sdk_exception_group(self):
        @asynccontextmanager
        async def grouped_transport(*args, **kwargs):
            raise ExceptionGroup("private SDK error", [
                client.GuardianClientError("Safe discovery error.")
            ])
            yield

        with (patch.dict(client.os.environ, {"MCD_MCP_TOKEN": "PRIVATE-TOKEN"}),
              patch.object(client, "streamablehttp_client", grouped_transport)):
            with self.assertRaisesRegex(client.GuardianClientError, "Safe discovery error"):
                await client.capture()


if __name__ == "__main__":
    unittest.main()
