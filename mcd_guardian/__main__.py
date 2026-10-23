"""CLI with explicit online capture/probe and fully offline comparison."""
from __future__ import annotations
import argparse
import asyncio
from datetime import datetime, timedelta
import hashlib
import json
import re
from pathlib import Path
import sys

from . import __version__
from .render import render_report


def read_json(path: str | Path):
    source = Path(path)
    if source.stat().st_size > 8_000_000:
        raise ValueError("JSON 文件超过 8 MB 限制")
    return json.loads(source.read_text(encoding="utf-8"))


def write_json(path: str | Path, value):
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    temp = dest.with_name(dest.name + ".tmp")
    temp.write_text(data, encoding="utf-8")
    temp.replace(dest)


def receipt(probe, manifest):
    from .core import check_response, validate_manifest
    validate_manifest(manifest)
    if probe.get("format") != "mcd-guardian.probe.v1" or probe.get("mode") != "live" or probe.get("server") != "https://mcp.mcd.cn":
        raise ValueError("不是本项目的官方地址实测记录")
    captured_at = probe.get("captured_at")
    if not isinstance(captured_at, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)", captured_at):
        raise ValueError("实测时间必须为 UTC ISO 时间文本")
    if datetime.fromisoformat(captured_at.replace("Z", "+00:00")).utcoffset() != timedelta(0):
        raise ValueError("实测时间必须使用 UTC")
    expected = {(s["id"], c["id"]): c for s in manifest["skills"] for c in s["calls"]}
    actual = probe.get("calls", [])
    if len(actual) != len(expected):
        raise ValueError("实测记录与清单的调用数量不一致")
    rows = []
    seen = set()
    for call in actual:
        status = call.get("business_status")
        if not isinstance(status, str) or status not in {"pass", "fail", "unknown"}:
            raise ValueError("实测状态不是受支持的标量")
        key = (call.get("skill_id"), call.get("id"))
        if key not in expected or key in seen:
            raise ValueError("实测记录存在未知或重复调用")
        seen.add(key)
        spec = expected[key]
        if call.get("tool") != spec["tool"]:
            raise ValueError("实测工具与依赖清单不一致")
        issues = check_response(call["result"], spec.get("checks", []))
        result = call["result"]
        business = result.get("structuredContent", {})
        verified_success = isinstance(business, dict) and business.get("success") is True and type(business.get("code")) is int and business["code"] == 200 and result.get("isError", False) is False
        passed = call.get("business_status") == "pass" and verified_success and not issues
        rows.append({"skill_id": key[0], "id": key[1], "tool": spec["tool"], "business_status": status, "checks": len(spec.get("checks", [])), "check_issues": issues, "passed": passed})
    return {"format": "mcd-guardian.receipt.v1", "mode": probe.get("mode"), "captured_at": probe.get("captured_at"), "server": "https://mcp.mcd.cn", "calls": rows, "passed": sum(x["passed"] for x in rows), "total": len(rows), "note": "本地生成的实测摘要，不是官方签名或资格证明；不复制参数、业务响应与原始异常。保留清单标识和检查位置，发布前请核对。"}


def main(argv=None):
    parser = argparse.ArgumentParser(description="麦麦契约哨兵：只验证声明的具体调用")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    cap = sub.add_parser("capture", help="在线发现契约，不调用业务工具")
    cap.add_argument("--out", default="private/current.json")
    pr = sub.add_parser("probe", help="在线验证三个白名单只读工具")
    pr.add_argument("--manifest", default="examples/manifest.json")
    pr.add_argument("--out", default="private/live-probe.json")
    comp = sub.add_parser("compare", help="离线比较前后契约对具体调用的影响")
    comp.add_argument("--before", required=True)
    comp.add_argument("--after", required=True)
    comp.add_argument("--manifest", default="examples/manifest.json")
    comp.add_argument("--out", default="private/report.json")
    comp.add_argument("--html", default=None)
    rec = sub.add_parser("receipt", help="从私有实测记录生成不含业务数据的摘要")
    rec.add_argument("--input", required=True)
    rec.add_argument("--manifest", default="examples/manifest.json")
    rec.add_argument("--out", default="private/receipt.json")
    args = parser.parse_args(argv)
    try:
        if args.command == "capture":
            from .client import capture
            output = asyncio.run(capture())
            write_json(args.out, output)
            print(f"已发现 {output['discovered_tool_count']} 个工具，契约保存至 {args.out}；未调用业务工具。")
        elif args.command == "probe":
            from .client import probe
            from .core import validate_manifest
            manifest = read_json(args.manifest)
            validate_manifest(manifest)
            output = asyncio.run(probe(manifest))
            write_json(args.out, output)
            summary = receipt(output, manifest)
            print(f"只读实测通过 {summary['passed']}/{summary['total']}；完整记录仅写入 {args.out}。")
            if summary["passed"] != summary["total"]:
                return 2
        elif args.command == "compare":
            from .core import compare_snapshots
            output = compare_snapshots(read_json(args.before), read_json(args.after), read_json(args.manifest))
            write_json(args.out, output)
            html_path = Path(args.html) if args.html else Path(args.out).with_suffix(".html")
            if html_path.resolve() == Path(args.out).resolve():
                raise ValueError("HTML 与 JSON 输出路径不能相同")
            html_path.parent.mkdir(parents=True, exist_ok=True)
            html_path.write_text(render_report(output), encoding="utf-8")
            summary = output["summary"]
            print(f"离线报告已生成：{args.out} / {html_path}；新增失败 {summary['regressions']}；无法验证 {summary['unverifiable']}。")
            if summary["regressions"] or summary["preexisting_failures"] or summary["unverifiable"]:
                return 2
        elif args.command == "receipt":
            input_path = Path(args.input)
            output = receipt(read_json(input_path), read_json(args.manifest))
            output["private_record_sha256"] = hashlib.sha256(input_path.read_bytes()).hexdigest()
            write_json(args.out, output)
            print(f"摘要已保存；通过 {output['passed']}/{output['total']}。发布前仍请核对保留字段。")
        return 0
    except (KeyboardInterrupt, EOFError):
        print("操作已取消。", file=sys.stderr)
        return 1
    except Exception as error:
        # Only our own safe exception classes may expose a message.
        if error.__class__.__name__ in {"ContractError", "GuardianClientError"}:
            print(str(error), file=sys.stderr)
        else:
            print("操作失败。请检查本地文件格式、路径与配置；没有打印原始异常或业务数据。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
