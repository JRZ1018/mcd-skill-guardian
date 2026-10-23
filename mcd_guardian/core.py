"""Offline checks of explicitly declared MCP calls, never universal compatibility.

Schema resolution uses an empty ``referencing.Registry``. No comparison or
response check opens a connection, and diagnostics never include argument or
response values.
"""

from __future__ import annotations

from copy import deepcopy
import json
import math
import re
from typing import Any

from jsonschema import Draft202012Validator, SchemaError, validators
from referencing import Registry


SNAPSHOT_FORMAT = "mcd-guardian.snapshot.v1"
MANIFEST_FORMAT = "mcd-guardian.manifest.v1"
REPORT_FORMAT = "mcd-guardian.report.v1"
_TYPES = {"string", "array", "object", "number", "integer", "boolean", "null"}
_BAD_POINTER_ESCAPE = re.compile(r"~(?![01])")
_ARRAY_INDEX = re.compile(r"(?:0|[1-9][0-9]*)\Z")
_MISSING = object()


class ContractError(ValueError):
    """A malformed local contract, with a message safe for public diagnostics."""


def _is_json(value: Any, depth: int = 0) -> bool:
    if depth > 150:
        return False
    if value is None or isinstance(value, (str, bool, int)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_is_json(item, depth + 1) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _is_json(item, depth + 1)
                   for key, item in value.items())
    return False


def _text(value: Any, location: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{location} 必须是非空字符串。")
    return value


def _pointer(pointer: Any) -> str:
    if (not isinstance(pointer, str)
            or (pointer != "" and not pointer.startswith("/"))
            or _BAD_POINTER_ESCAPE.search(pointer)):
        raise ContractError("响应检查必须使用有效的 JSON Pointer。")
    return pointer


def _validate_checks(checks: Any) -> list[dict[str, str]]:
    if not isinstance(checks, list):
        raise ContractError("checks 必须是列表。")
    for check in checks:
        if (not isinstance(check, dict) or "pointer" not in check
                or set(check) - {"pointer", "type"}):
            raise ContractError("每项响应检查只能包含 pointer 和可选 type。")
        _pointer(check["pointer"])
        if "type" in check and (not isinstance(check["type"], str)
                                or check["type"] not in _TYPES):
            raise ContractError("响应检查 type 不受支持。")
    return checks


def validate_manifest(manifest: Any) -> dict[str, Any]:
    """Validate and copy a manifest; add absent ``checks`` as empty lists.

    Empty manifests/call lists are allowed and produce a zero-call report.
    Call IDs are unique within a skill; skill IDs are unique in the manifest.
    """
    if not isinstance(manifest, dict) or manifest.get("format") != MANIFEST_FORMAT:
        raise ContractError("manifest format 必须是 mcd-guardian.manifest.v1。")
    if not _is_json(manifest):
        raise ContractError("manifest 必须只包含有限、有效的 JSON 值。")
    skills = manifest.get("skills")
    if not isinstance(skills, list):
        raise ContractError("manifest.skills 必须是列表。")
    skill_ids: set[str] = set()
    for skill in skills:
        if not isinstance(skill, dict):
            raise ContractError("skills 中的每项必须是对象。")
        skill_id = _text(skill.get("id"), "skill.id")
        _text(skill.get("name"), "skill.name")
        if skill_id in skill_ids:
            raise ContractError("skill.id 不得重复。")
        skill_ids.add(skill_id)
        if "source" in skill:
            _text(skill["source"], "skill.source")
        calls = skill.get("calls")
        if not isinstance(calls, list):
            raise ContractError("skill.calls 必须是列表。")
        call_ids: set[str] = set()
        for call in calls:
            if not isinstance(call, dict):
                raise ContractError("calls 中的每项必须是对象。")
            call_id = _text(call.get("id"), "call.id")
            _text(call.get("tool"), "call.tool")
            if call_id in call_ids:
                raise ContractError("同一 skill 中的 call.id 不得重复。")
            call_ids.add(call_id)
            if not isinstance(call.get("arguments"), dict):
                raise ContractError("call.arguments 必须是 JSON 对象。")
            _validate_checks(call.get("checks", []))
    clean = deepcopy(manifest)
    for skill in clean["skills"]:
        for call in skill["calls"]:
            call.setdefault("checks", [])
    return clean


def _escape(token: Any) -> str:
    return str(token).replace("~", "~0").replace("/", "~1")


def _lookup(document: Any, pointer: str) -> Any:
    current = document
    if pointer == "":
        return current
    for encoded in pointer[1:].split("/"):
        token = encoded.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            current = current.get(token, _MISSING)
        elif isinstance(current, list) and _ARRAY_INDEX.fullmatch(token):
            # Avoid converting attacker-controlled, extremely long integers.
            if len(token) > len(str(len(current))):
                return _MISSING
            index = int(token)
            current = current[index] if index < len(current) else _MISSING
        else:
            return _MISSING
        if current is _MISSING:
            return _MISSING
    return current


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "string":
        return isinstance(value, str)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    if isinstance(value, bool):
        return False
    if expected == "number":
        return isinstance(value, (int, float)) and (
            not isinstance(value, float) or math.isfinite(value))
    # JSON Schema integer semantics include finite floats with no fraction.
    return (isinstance(value, int)
            or (isinstance(value, float) and math.isfinite(value)
                and value.is_integer()))


def check_response(result: Any, checks: Any) -> list[dict[str, str]]:
    """Check only pointer existence and optional primitive JSON type.

    A present JSON null is different from a missing field. These checks do not
    verify business truth, freshness, semantic units, or official guarantees.
    """
    _validate_checks(checks)
    issues: list[dict[str, str]] = []
    for check in checks:
        pointer = check["pointer"]
        value = _lookup(result, pointer)
        if value is _MISSING:
            issues.append({"path": pointer, "rule": "presence",
                           "message": "声明依赖的响应字段不存在。"})
        elif "type" in check and not _matches_type(value, check["type"]):
            issues.append({"path": pointer, "rule": "type",
                           "message": "响应字段类型不符合声明。"})
    return issues


def _index_snapshot(snapshot: Any) -> dict[str, dict[str, Any]]:
    if (not isinstance(snapshot, dict)
            or snapshot.get("format") != SNAPSHOT_FORMAT):
        raise ContractError("snapshot format 必须是 mcd-guardian.snapshot.v1。")
    if not _is_json(snapshot):
        raise ContractError("snapshot 必须只包含有限、有效的 JSON 值。")
    _text(snapshot.get("captured_at"), "snapshot.captured_at")
    _text(snapshot.get("server"), "snapshot.server")
    if (not isinstance(snapshot.get("mode"), str)
            or snapshot["mode"] not in {"live", "synthetic"}):
        raise ContractError("snapshot.mode 必须为 live 或 synthetic。")
    count = snapshot.get("discovered_tool_count")
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        raise ContractError("snapshot.discovered_tool_count 必须为非负整数。")
    tools = snapshot.get("tools")
    if not isinstance(tools, list):
        raise ContractError("snapshot.tools 必须是列表。")
    if count < len(tools):
        raise ContractError("工具总数不得小于快照内的工具数量。")
    indexed: dict[str, dict[str, Any]] = {}
    for tool in tools:
        if not isinstance(tool, dict):
            raise ContractError("snapshot.tools 中的每项必须是对象。")
        name = _text(tool.get("name"), "tool.name")
        if name in indexed:
            raise ContractError("同一快照内的工具名称不得重复。")
        indexed[name] = tool
    return indexed


_MESSAGES = {
    "required": "缺少必填字段。",
    "type": "参数类型不符合 Schema。",
    "enum": "参数不在 Schema 允许的枚举范围内。",
    "const": "参数不符合 Schema 固定值约束。",
    "additionalProperties": "包含 Schema 不允许的额外字段。",
    "unevaluatedProperties": "包含 Schema 不允许的未评估字段。",
    "minimum": "参数低于 Schema 数值下限。",
    "maximum": "参数超过 Schema 数值上限。",
    "pattern": "参数不符合 Schema 模式约束。",
}


def _verify_arguments(tool: dict[str, Any] | None,
                      arguments: dict[str, Any]) -> dict[str, Any]:
    if tool is None:
        return {"valid": False, "issues": [{"path": "", "rule": "missing-tool",
                "message": "快照中缺少此调用依赖的工具。"}]}
    schema = tool.get("inputSchema", _MISSING)
    if not isinstance(schema, (dict, bool)):
        return {"valid": None, "issues": [{"path": "", "rule": "invalid-schema",
                "message": "工具缺少可验证的 inputSchema。"}]}
    try:
        if isinstance(schema, dict) and "$schema" in schema:
            validator_type = validators.validator_for(schema, default=None)
            if validator_type is None:
                return {"valid": None, "issues": [{"path": "", "rule": "schema-dialect",
                        "message": "当前验证器不支持此 Schema 方言。"}]}
        else:
            validator_type = Draft202012Validator
        validator_type.check_schema(schema)
        validator = validator_type(schema, registry=Registry())
        errors = list(validator.iter_errors(arguments))
    except SchemaError:
        return {"valid": None, "issues": [{"path": "", "rule": "invalid-schema",
                "message": "inputSchema 不符合其声明的 Schema 规范。"}]}
    except Exception:
        # Never expose exception strings: refs, regexes, and validators can
        # embed arguments or remotely supplied text in their error messages.
        return {"valid": None, "issues": [{"path": "", "rule": "unresolvable-schema",
                "message": "Schema 无法在本地完整验证；未请求外部引用。"}]}
    issues = []
    seen_issues: set[tuple[str, str, str]] = set()
    for error in errors:
        rule = error.validator if isinstance(error.validator, str) else "schema"
        path = "".join("/" + _escape(part) for part in error.absolute_path)
        paths = [path]
        if rule == "required" and isinstance(error.instance, dict):
            # Each jsonschema required error repeats the complete required
            # list. Derive names from the schema, never parse its message,
            # and deduplicate when multiple missing fields yield errors.
            paths = [path + "/" + _escape(name)
                     for name in error.validator_value if name not in error.instance]
        message = _MESSAGES.get(rule, "参数不符合此项 Schema 约束。")
        for issue_path in paths:
            signature = (issue_path, rule, message)
            if signature not in seen_issues:
                seen_issues.add(signature)
                issues.append({"path": issue_path, "rule": rule, "message": message})
    issues.sort(key=lambda item: (item["path"], item["rule"], item["message"]))
    return {"valid": not issues, "issues": issues}


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=True,
                      separators=(",", ":"), allow_nan=False)


def _changes(before: Any, after: Any, path: str = "") -> list[dict[str, Any]]:
    if (isinstance(before, list) and isinstance(after, list)
            and path.rsplit("/", 1)[-1] in {"enum", "required", "type"}
            and sorted(map(_canonical, before)) == sorted(map(_canonical, after))):
        return []
    # Python considers True == 1 even in nested lists/dicts, while JSON Schema
    # enum treats booleans and numbers as different values.
    if _canonical(before) == _canonical(after):
        return []
    if isinstance(before, dict) and isinstance(after, dict):
        result = []
        for key in sorted(before.keys() | after.keys()):
            child = path + "/" + _escape(key)
            if key not in before:
                result.append({"path": child, "kind": "added", "after": deepcopy(after[key])})
            elif key not in after:
                result.append({"path": child, "kind": "removed", "before": deepcopy(before[key])})
            else:
                result.extend(_changes(before[key], after[key], child))
        return result
    if isinstance(before, list) and isinstance(after, list):
        result = []
        for index in range(max(len(before), len(after))):
            child = path + "/" + str(index)
            if index >= len(before):
                result.append({"path": child, "kind": "added", "after": deepcopy(after[index])})
            elif index >= len(after):
                result.append({"path": child, "kind": "removed", "before": deepcopy(before[index])})
            else:
                result.extend(_changes(before[index], after[index], child))
        return result
    return [{"path": path, "kind": "changed", "before": deepcopy(before),
             "after": deepcopy(after)}]


def _contract(tool: dict[str, Any]) -> dict[str, Any]:
    return {key: tool[key] for key in ("inputSchema", "outputSchema") if key in tool}


def _status(before: bool | None, after: bool | None) -> str:
    if before is None or after is None:
        return "unverifiable"
    if before and not after:
        return "regression"
    if not before and after:
        return "improved"
    return "pass" if after else "preexisting-failure"


def compare_snapshots(before: Any, after: Any, manifest: Any,
                      mode: str = "comparison") -> dict[str, Any]:
    """Validate each concrete declared arguments object against two snapshots.

    All tool schema changes are reported; only declared calls affect call
    statuses. Response checks are not executed without an actual response.
    """
    _text(mode, "report.mode")
    clean = validate_manifest(manifest)
    old_tools = _index_snapshot(before)
    new_tools = _index_snapshot(after)
    changes: list[dict[str, Any]] = []
    per_tool: dict[str, list[dict[str, Any]]] = {}
    for name in sorted(old_tools.keys() | new_tools.keys()):
        if name not in old_tools:
            differences = [{"path": "", "kind": "added", "after": deepcopy(_contract(new_tools[name]))}]
        elif name not in new_tools:
            differences = [{"path": "", "kind": "removed", "before": deepcopy(_contract(old_tools[name]))}]
        else:
            differences = _changes(_contract(old_tools[name]), _contract(new_tools[name]))
        per_tool[name] = differences
        changes.extend({"tool": name, **deepcopy(change)} for change in differences)
    calls = []
    for skill in clean["skills"]:
        for call in skill["calls"]:
            old = _verify_arguments(old_tools.get(call["tool"]), call["arguments"])
            new = _verify_arguments(new_tools.get(call["tool"]), call["arguments"])
            calls.append({"skill_id": skill["id"], "skill_name": skill["name"],
                          "id": call["id"], "tool": call["tool"],
                          "before": old, "after": new,
                          "status": _status(old["valid"], new["valid"]),
                          "changes": deepcopy(per_tool.get(call["tool"], []))})
    limitations = [
        "只验证 manifest 显式列出的具体参数样例，不证明所有可能输入兼容，也不证明整个 Skill 正确。",
        "比较过程完全离线，缺少工具属于明确失败；无法本地解析的 Schema 标为不可验证。",
        "Schema 差异是结构变化记录，变化数量不等于破坏性变更数量。",
        "未启用可选 format 断言；未覆盖未知 Schema 方言、全部自定义关键字或业务隐含约束。",
        "本报告不执行响应检查；check_response 仅检查实际响应中的字段存在性与 JSON 类型。",
        "一次响应的字段检查不代表官方长期输出契约、业务真实性、金额单位或未来服务状态。",
    ]
    if before["server"] != after["server"]:
        limitations.append("两份快照声明的服务器不同，不能将差异直接解释为同一服务升级。")
    summary = {"skills": len(clean["skills"]), "calls": len(calls),
               "regressions": sum(call["status"] == "regression" for call in calls),
               "preexisting_failures": sum(call["status"] == "preexisting-failure" for call in calls),
               "improved": sum(call["status"] == "improved" for call in calls),
               "unverifiable": sum(call["status"] == "unverifiable" for call in calls),
               "schema_changes": len(changes)}
    return {"format": REPORT_FORMAT, "mode": mode,
            "before": {"captured_at": before["captured_at"], "mode": before["mode"]},
            "after": {"captured_at": after["captured_at"], "mode": after["mode"]},
            "summary": summary, "calls": calls, "changes": changes,
            "limitations": limitations}
