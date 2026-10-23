"""Private, read-only MCP contract capture and explicitly declared probes."""

from __future__ import annotations

from contextlib import asynccontextmanager, contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import getpass
import json
import logging
import os
import sys
from time import perf_counter
from typing import Any, AsyncIterator, Iterator

from jsonschema import SchemaError, ValidationError, validators
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from referencing import Registry


SERVER = "https://mcp.mcd.cn"
READ_ONLY_TOOLS = frozenset({
    "campaign-calendar", "list-nutrition-foods", "query-nearby-stores",
})
SNAPSHOT_FORMAT = "mcd-guardian.snapshot.v1"
PROBE_FORMAT = "mcd-guardian.probe.v1"
MANIFEST_FORMAT = "mcd-guardian.manifest.v1"


class GuardianClientError(RuntimeError):
    """A safe message without credentials, arguments, headers, or payloads."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_token() -> str:
    token = os.environ.get("MCD_MCP_TOKEN", "").strip()
    if not token:
        if not sys.stdin.isatty():
            raise GuardianClientError(
                "MCD_MCP_TOKEN is missing. Set it or use an interactive "
                "terminal for hidden token entry."
            )
        try:
            token = getpass.getpass("McDonald's MCP token (hidden): ").strip()
        except (EOFError, KeyboardInterrupt):
            raise GuardianClientError("Token entry was cancelled.") from None
    if not token:
        raise GuardianClientError("A McDonald's MCP token is required.")
    if any(character.isspace() for character in token):
        raise GuardianClientError("The MCP token must not contain whitespace.")
    return token


@contextmanager
def _quiet_transport_logs() -> Iterator[None]:
    # Keep SDK and HTTP diagnostics out of application/root log handlers even
    # when the caller enables debug logging. Restore configuration afterwards.
    previous = []
    for name in ("mcp", "httpx", "httpcore"):
        logger = logging.getLogger(name)
        previous.append((logger, logger.handlers[:], logger.propagate))
        logger.handlers = [logging.NullHandler()]
        logger.propagate = False
    try:
        yield
    finally:
        for logger, handlers, propagate in previous:
            logger.handlers = handlers
            logger.propagate = propagate


def _safe_error(error: BaseException) -> GuardianClientError | None:
    if isinstance(error, GuardianClientError):
        return error
    if isinstance(error, BaseExceptionGroup):
        for child in error.exceptions:
            found = _safe_error(child)
            if found is not None:
                return found
    return None


@asynccontextmanager
async def _session() -> AsyncIterator[ClientSession]:
    token = _get_token()
    try:
        with _quiet_transport_logs():
            async with streamablehttp_client(
                SERVER,
                headers={"Authorization": f"Bearer {token}"},
                timeout=30,
                sse_read_timeout=60,
            ) as (read_stream, write_stream, _):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    yield session
    except GuardianClientError:
        raise
    except Exception as error:
        known = _safe_error(error)
        if known is not None:
            raise known from None
        # Transport and SDK errors may embed request headers or raw responses.
        raise GuardianClientError(
            "The official MCP connection or request failed. "
            "Check the network and token, then retry."
        ) from None


async def _discover(session: ClientSession) -> dict[str, Any]:
    contracts: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    seen_cursors: set[str] = set()
    cursor: str | None = None
    while True:
        response = await session.list_tools(cursor=cursor)
        for tool in response.tools:
            if tool.name in seen_names:
                raise GuardianClientError("The server returned a duplicate tool name.")
            if (not isinstance(tool.inputSchema, dict)
                    or (tool.outputSchema is not None
                        and not isinstance(tool.outputSchema, dict))):
                raise GuardianClientError("The server returned an invalid tool schema object.")
            contracts.append({
                "name": tool.name,
                "inputSchema": deepcopy(tool.inputSchema),
                "outputSchema": deepcopy(tool.outputSchema),
            })
            seen_names.add(tool.name)
        cursor = response.nextCursor
        if not cursor:
            break
        if cursor in seen_cursors:
            raise GuardianClientError("The server repeated a tools/list cursor.")
        seen_cursors.add(cursor)
    return {
        "format": SNAPSHOT_FORMAT,
        "captured_at": _now(),
        "mode": "live",
        "server": SERVER,
        "discovered_tool_count": len(contracts),
        "tools": sorted(contracts, key=lambda tool: tool["name"]),
    }


def _identifier(value: Any) -> bool:
    return (isinstance(value, str) and bool(value.strip())
            and value == value.strip()
            and not any(ord(character) < 32 or ord(character) == 127
                        for character in value))


def _json_keys_are_strings(value: Any) -> bool:
    if isinstance(value, dict):
        return all(isinstance(key, str) and _json_keys_are_strings(item)
                   for key, item in value.items())
    if isinstance(value, list):
        return all(_json_keys_are_strings(item) for item in value)
    return value is None or isinstance(value, (str, bool, int, float))


def _declared_calls(manifest: Any) -> list[dict[str, Any]]:
    """Validate local declarations before acquiring a token or opening a session."""
    if not isinstance(manifest, dict) or manifest.get("format") != MANIFEST_FORMAT:
        raise GuardianClientError("The manifest format is missing or unsupported.")
    skills = manifest.get("skills")
    if not isinstance(skills, list):
        raise GuardianClientError("The manifest must contain a skills list.")
    declared = []
    skill_ids: set[str] = set()
    for skill in skills:
        if not isinstance(skill, dict) or not _identifier(skill.get("id")):
            raise GuardianClientError("Every skill must have a valid id.")
        skill_id = skill["id"]
        if skill_id in skill_ids:
            raise GuardianClientError("The manifest contains duplicate skill ids.")
        skill_ids.add(skill_id)
        calls = skill.get("calls")
        if not isinstance(calls, list):
            raise GuardianClientError("Every skill must contain an explicit calls list.")
        call_ids: set[str] = set()
        for call in calls:
            if not isinstance(call, dict) or not _identifier(call.get("id")):
                raise GuardianClientError("Every declared call must have a valid id.")
            if call["id"] in call_ids:
                raise GuardianClientError("A skill contains duplicate call ids.")
            call_ids.add(call["id"])
            tool = call.get("tool")
            if not isinstance(tool, str) or tool not in READ_ONLY_TOOLS:
                raise GuardianClientError(
                    "A declared tool is outside the three-tool read-only allowlist."
                )
            arguments = call.get("arguments")
            if not isinstance(arguments, dict):
                raise GuardianClientError("Every call must declare a JSON arguments object.")
            try:
                if not _json_keys_are_strings(arguments):
                    raise ValueError("Non-JSON value")
                json.dumps(arguments, allow_nan=False)
                copied = deepcopy(arguments)
            except (TypeError, ValueError, OverflowError, RecursionError):
                raise GuardianClientError(
                    "Declared arguments must contain valid JSON values and string keys."
                ) from None
            declared.append({
                "skill_id": skill_id,
                "id": call["id"],
                "tool": tool,
                "arguments": copied,
            })
    return declared


def _validate_arguments(arguments: dict[str, Any], schema: dict[str, Any]) -> None:
    try:
        validator_type = (validators.validator_for(schema, default=None)
                          if "$schema" in schema else validators.validator_for(schema))
        if validator_type is None:
            raise GuardianClientError("A discovered input schema uses an unsupported dialect.")
        validator_type.check_schema(schema)
        # Do not resolve external schema references over HTTP or other schemes.
        validator_type(schema, registry=Registry()).validate(arguments)
    except GuardianClientError:
        raise
    except SchemaError:
        raise GuardianClientError("A discovered input schema is invalid.") from None
    except ValidationError as error:
        safe_rules = {"required", "type", "enum", "const", "additionalProperties",
                      "oneOf", "anyOf", "allOf", "pattern", "minimum", "maximum",
                      "minLength", "maxLength", "minItems", "maxItems"}
        rule = f" ({error.validator})" if error.validator in safe_rules else ""
        raise GuardianClientError(
            f"A declared call does not match its discovered input schema{rule}."
        ) from None
    except Exception:
        raise GuardianClientError(
            "A discovered input schema could not be validated locally."
        ) from None


def _business_status(result: dict[str, Any]) -> str:
    if result.get("isError") is True:
        return "fail"
    structured = result.get("structuredContent")
    if not isinstance(structured, dict):
        return "unknown"
    # Only the known top-level success/code envelope is interpreted. Nested
    # status, text-only responses, or an unpublished envelope remain unknown.
    if "success" in structured and structured["success"] is not True:
        return "fail"
    if "code" in structured and (type(structured["code"]) is not int
                                  or structured["code"] != 200):
        return "fail"
    if structured.get("success") is True and type(structured.get("code")) is int:
        return "pass"
    return "unknown"


async def capture() -> dict[str, Any]:
    """Capture all discovered contracts without calling any business tool."""
    async with _session() as session:
        return await _discover(session)


async def probe(manifest: dict[str, Any]) -> dict[str, Any]:
    """Discover, validate every declared input, then probe sequentially once.

    Returned results can contain private data. This module never prints or saves
    them; the caller must keep them private and derive a safe public summary.
    Manifest response checks are intentionally left to the caller's core module.
    """
    declared = _declared_calls(manifest)
    async with _session() as session:
        snapshot = await _discover(session)
        by_name = {tool["name"]: tool for tool in snapshot["tools"]}
        for call in declared:
            contract = by_name.get(call["tool"])
            if contract is None:
                raise GuardianClientError("A declared read-only tool was not discovered.")
            _validate_arguments(call["arguments"], contract["inputSchema"])
        results = []
        for call in declared:
            started = perf_counter()
            response = await session.call_tool(call["tool"], arguments=call["arguments"])
            elapsed_ms = round((perf_counter() - started) * 1000, 3)
            result = response.model_dump(mode="json", by_alias=True, exclude_none=True)
            results.append({
                "skill_id": call["skill_id"],
                "id": call["id"],
                "tool": call["tool"],
                "captured_at": _now(),
                "elapsed_ms": elapsed_ms,
                "result": result,
                "business_status": _business_status(result),
            })
        return {
            "format": PROBE_FORMAT,
            "captured_at": _now(),
            "server": SERVER,
            "mode": "live",
            "snapshot": snapshot,
            "calls": results,
        }
