---
name: mcd-skill-guardian
description: 比较麦当劳官方 MCP 前后输入契约，验证项目显式声明的具体调用并定位受影响的 Skill；也可只读实测三个白名单工具的响应字段。用于接口升级排查、调用依赖核对和离线回归检查。
---

在完整的 mcd-skill-guardian 仓库根目录使用，依赖和命令见 README.md，已验证接口与结果边界见 MCP_INTEGRATION.md。

用户想知道“接口变了会不会影响我的 Skill”时，先确定其实际调用的工具、具体参数与依赖的响应 JSON Pointer。将用户已有的这些信息写入清单，结构参考 examples/manifest.json；未知参数不要用猜测补全。不要替用户把所有工具都列为依赖。

有前后快照时直接离线比较：

```powershell
python -m mcd_guardian compare --before private/before.json --after private/after.json --manifest private/my-skill.json --out private/impact.json
```

需要当前官方契约时运行 `python -m mcd_guardian capture --out private/current.json`。这只执行 MCP 初始化和分页 tools/list，不调用业务工具；Token 从 MCD_MCP_TOKEN 或终端隐藏输入读取。固定连接 https://mcp.mcd.cn，不接受其他地址。

解释报告时，区分新增失败、原已失败、恢复通过和无法验证。通过只代表清单中这组具体参数满足该 inputSchema，不等于所有输入向后兼容、服务实际可用或业务语义正确。未被清单依赖的工具变化，不要称为该 Skill 被破坏。无法解析的 schema 引用应报告无法验证，不尝试外部下载或自动修改请求。

只有用户需要实际响应检查时，运行 `python -m mcd_guardian probe --manifest private/my-skill.json --out private/live-probe.json`。它仅允许 campaign-calendar、list-nutrition-foods、query-nearby-stores 三个只读工具；其余依赖只能做离线输入契约检查。公开地标示例不代表用户位置。完整响应保留在 private/，不直接粘贴到公共 Issue。

需要公开复现时，优先提供具体失败规则与最小合成输入；示例 candidate 文件和网页演练均是明确标注的合成变化，不能称为官方真实故障。`receipt` 命令可从私有记录生成有限字段摘要，但保留清单标识和检查位置，发布前仍需核对。除非用户要求发布，不推送快照、参数或报告。

返回生成的 JSON、HTML 路径和受影响调用的规则位置；不能仅凭一次样例检查宣布整个 Skill 已修好。
