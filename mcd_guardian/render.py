"""A self-contained, escaped HTML report; no scripts or remote resources."""
from html import escape
import json


def render_report(report: dict) -> str:
    def safe(value):
        return escape(str(value), quote=True)

    labels = {
        "regression": "新增失败", "pass": "声明调用通过",
        "preexisting-failure": "原已失败", "improved": "恢复通过",
        "unverifiable": "无法验证",
    }
    rows = []
    for call in report.get("calls", []):
        issues = call.get("after", {}).get("issues", [])
        evidence = "<br>".join(safe(f"{i.get('path', '/')} · {i.get('rule', '')} · {i.get('message', '')}") for i in issues) or "当前具体输入通过校验"
        status = call.get("status", "unverifiable")
        rows.append(f"<tr><td>{safe(call.get('skill_name', ''))}<small>{safe(call.get('id', ''))}</small></td><td><code>{safe(call.get('tool', ''))}</code></td><td class='{safe(status)}'>{safe(labels.get(status, status))}</td><td>{evidence}</td></tr>")
    changes = "".join(f"<li><code>{safe(c.get('tool', ''))}{safe(c.get('path', ''))}</code> · {safe(c.get('kind', ''))}</li>" for c in report.get("changes", [])) or "<li>未发现契约结构差异</li>"
    limits = "".join(f"<li>{safe(x)}</li>" for x in report.get("limitations", []))
    summary = safe(json.dumps(report.get("summary", {}), ensure_ascii=False))
    source_modes = f"基线模式：{safe(report.get('before', {}).get('mode', 'unknown'))}；候选模式：{safe(report.get('after', {}).get('mode', 'unknown'))}"
    drill = any(report.get(side, {}).get("mode") == "synthetic" for side in ("before", "after"))
    source_banner = "<p class='regression'>合成演练：本报告包含模拟契约变化，不代表官方接口发生真实升级或故障。</p>" if drill else ""
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'"><title>麦麦契约哨兵 · 调用影响报告</title><style>
    :root{{color-scheme:light}}body{{font:16px/1.7 'Microsoft YaHei',sans-serif;background:#f5f3e8;color:#153831;margin:0}}main{{max-width:1120px;margin:auto;padding:48px 24px}}h1{{font-size:40px;line-height:1.25}}.eyebrow{{letter-spacing:.12em;color:#675548}}.summary{{background:#173b34;color:#f5f3e8;padding:18px;overflow:auto}}table{{width:100%;border-collapse:collapse;background:#fffdf5}}th,td{{text-align:left;vertical-align:top;padding:18px 12px;border-bottom:1px solid #cdcfc3}}th{{font-size:13px;color:#53685e}}small{{display:block;color:#607569}}code{{overflow-wrap:anywhere;font-size:13px}}.regression{{color:#ae3d13;font-weight:bold}}.unverifiable{{color:#8a6513}}.pass{{color:#11684e}}.table-wrap{{overflow-x:auto}}footer{{border-top:1px solid #c4c9bc;padding-top:24px;margin-top:40px;color:#53685e}}@media(max-width:600px){{h1{{font-size:30px}}main{{padding:24px 16px}}table{{min-width:650px}}}}
    </style></head><body><main><p class="eyebrow">MCD SKILL GUARDIAN / LOCAL REPORT</p><h1>哪一次调用，受到了影响。</h1><p>报告模式：{safe(report.get('mode', ''))}。仅验证清单中的具体输入；不连接 MCP、不执行订单操作。</p><p>{source_modes}</p>{source_banner}<p>基线：{safe(report.get('before', {}).get('captured_at', ''))} → 候选：{safe(report.get('after', {}).get('captured_at', ''))}</p><pre class="summary">{summary}</pre><div class="table-wrap"><table><thead><tr><th>Skill / 调用</th><th>工具依赖</th><th>结果</th><th>候选契约证据</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div><h2>契约结构变化</h2><p>调用通过与否仅检验 inputSchema。</p><ul>{changes}</ul><h2>结果边界</h2><ul>{limits}</ul><p>通过不保证服务可用、业务成功、响应语义正确或所有可能输入兼容。实际响应检查属于另一项实测。</p><footer>麦麦契约哨兵 · 独立参赛作品，非麦当劳官方产品。Codex 辅助开发。该文件不包含脚本、外部字体、请求头或业务响应。</footer></main></body></html>"""
