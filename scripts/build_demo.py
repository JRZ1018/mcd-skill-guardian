"""Build explicit synthetic drills from a selected real input-contract baseline."""
from __future__ import annotations
import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mcd_guardian.__main__ import read_json, receipt, write_json
from mcd_guardian.core import compare_snapshots


def project_schema(value):
    if not isinstance(value, dict):
        return deepcopy(value)
    annotations = {"description", "title", "examples", "default", "$comment"}
    named_schemas = {"properties", "patternProperties", "$defs", "definitions", "dependentSchemas"}
    single_schemas = {"additionalProperties", "unevaluatedProperties", "propertyNames", "contains", "not", "if", "then", "else", "additionalItems", "unevaluatedItems", "contentSchema"}
    schema_arrays = {"allOf", "anyOf", "oneOf", "prefixItems"}
    projected = {}
    for key, child in value.items():
        if key in annotations:
            continue
        if key in named_schemas and isinstance(child, dict):
            projected[key] = {name: project_schema(schema) for name, schema in child.items()}
        elif key in single_schemas:
            projected[key] = project_schema(child)
        elif (key in schema_arrays or key == "items") and isinstance(child, list):
            projected[key] = [project_schema(schema) for schema in child]
        elif key == "items":
            projected[key] = project_schema(child)
        elif key == "dependencies" and isinstance(child, dict):
            projected[key] = {name: project_schema(schema) if isinstance(schema, dict) else deepcopy(schema) for name, schema in child.items()}
        else:
            # enum/const and unknown extension keywords can contain literal
            # objects whose keys happen to be "title" or "default".
            projected[key] = deepcopy(child)
    return projected


def decorate(report, before, after, manifest):
    calls = {(s["id"], c["id"]): c for s in manifest["skills"] for c in s["calls"]}
    old_tools = {t["name"]: t["inputSchema"] for t in before["tools"]}
    new_tools = {t["name"]: t["inputSchema"] for t in after["tools"]}
    for call in report["calls"]:
        # This fixed, reviewed sample manifest uses a public landmark only.
        call["arguments"] = deepcopy(calls[(call["skill_id"], call["id"])]["arguments"])
        call["before_schema"] = old_tools.get(call["tool"])
        call["after_schema"] = new_tools.get(call["tool"])
    return report


def generate(baseline, manifest, verification):
    base_report = decorate(compare_snapshots(baseline, baseline, manifest, mode="real-baseline"), baseline, baseline, manifest)
    scenarios = []
    candidates = {}
    definitions = [
        ("required", "新增必填参数", "合成演练：活动接口要求 locale，空参数调用会怎样？"),
        ("enum", "枚举收窄", "合成演练：门店接口只接受 beType=5，现有 beType=1 调用受到影响。"),
        ("unrelated", "未使用接口变化", "合成演练：当前清单未使用的时间接口改变，不应被称为这三个调用发生退化。"),
        ("removed", "依赖工具缺失", "合成演练：移除活动接口，定位依赖它的调用，而非把所有 Skill 都标红。"),
    ]
    for scenario_id, title, description in definitions:
        candidate = deepcopy(baseline)
        candidate["mode"] = "synthetic"
        candidate["synthetic_note"] = "由真实基线合成；不是官方接口真实变动或故障。captured_at沿用基线来源时间。"
        mapping = {t["name"]: t for t in candidate["tools"]}
        if scenario_id == "required":
            schema = mapping["campaign-calendar"]["inputSchema"]
            schema.setdefault("properties", {})["locale"] = {"type": "string", "enum": ["zh-CN"]}
            schema["required"] = sorted(set(schema.get("required", [])) | {"locale"})
        elif scenario_id == "enum":
            mapping["query-nearby-stores"]["inputSchema"]["properties"]["beType"]["enum"] = [5]
        elif scenario_id == "unrelated":
            schema = mapping["now-time-info"]["inputSchema"]
            schema.setdefault("properties", {})["timezone"] = {"type": "string"}
            schema["required"] = sorted(set(schema.get("required", [])) | {"timezone"})
        elif scenario_id == "removed":
            candidate["tools"] = [t for t in candidate["tools"] if t["name"] != "campaign-calendar"]
            candidate["discovered_tool_count"] -= 1
        report = decorate(compare_snapshots(baseline, candidate, manifest, mode="synthetic-drill"), baseline, candidate, manifest)
        scenarios.append({"id": scenario_id, "title": title, "description": description, "report": report})
        candidates[f"examples/candidate-{scenario_id}.demo.json"] = candidate
    demo = {
        "format": "mcd-guardian.demo.v1",
        "baseline": {
            "snapshot_summary": {"captured_at": baseline["captured_at"], "tool_count": baseline["discovered_tool_count"], "selected_tools": len(baseline["tools"]), "probe_passed": verification["passed"], "probe_total": verification["total"]},
            "report": base_report,
        },
        "scenarios": scenarios,
    }
    return {"docs/demo-data.json": demo, **candidates}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-probe", help="显式从私有实测生成选取后的公开基线和摘要")
    parser.add_argument("--check", action="store_true", help="只核对已提交的演练数据是否可复现")
    args = parser.parse_args()
    if args.from_probe and args.check:
        parser.error("--from-probe 与 --check 不能同时使用")
    manifest = read_json(ROOT / "examples/manifest.json")
    if args.from_probe:
        probe_path = Path(args.from_probe)
        probe = read_json(probe_path)
        verified = receipt(probe, manifest)
        if verified["passed"] != verified["total"] or verified["total"] != 3:
            raise SystemExit("三项实测与响应字段检查未全部通过，停止公开生成。")
        import hashlib
        verified["private_record_sha256"] = hashlib.sha256(probe_path.read_bytes()).hexdigest()
        snapshot = probe["snapshot"]
        selected = {"campaign-calendar", "list-nutrition-foods", "query-nearby-stores", "now-time-info"}
        tools = [{"name": t["name"], "inputSchema": project_schema(t["inputSchema"])} for t in snapshot["tools"] if t["name"] in selected]
        if {t["name"] for t in tools} != selected:
            raise SystemExit("真实快照缺少演练需要的工具，不能用模拟工具替代。")
        baseline = {"format": "mcd-guardian.snapshot.v1", "mode": "live", "server": "https://mcp.mcd.cn", "captured_at": snapshot["captured_at"], "discovered_tool_count": snapshot["discovered_tool_count"], "published_tool_count": len(tools), "projection_note": "从实际发现工具中选取四项输入契约，仅移除描述、标题、示例和默认值等注解；未公开业务响应或账户数据。", "tools": tools}
        write_json(ROOT / "examples/baseline.public.json", baseline)
        write_json(ROOT / "evidence/live-verification.json", verified)
    else:
        baseline = read_json(ROOT / "examples/baseline.public.json")
        verified = read_json(ROOT / "evidence/live-verification.json")
    generated = generate(baseline, manifest, verified)
    for relative, value in generated.items():
        target = ROOT / relative
        if args.check:
            if not target.exists() or read_json(target) != value:
                raise SystemExit(f"演练文件与当前引擎不一致：{relative}")
        else:
            write_json(target, value)
    print("四组合成演练与真实基线已核对。" if args.check else "已生成真实基线与四组合成演练；未把合成变化当作官方故障。")


if __name__ == "__main__":
    main()
