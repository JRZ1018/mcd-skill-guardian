# 麦麦契约哨兵 · McD Skill Guardian

[在线演示](https://jrz1018.github.io/mcd-skill-guardian/) · [Skill](skills/mcd-skill-guardian/SKILL.md) · [真实查询摘要](evidence/live-verification.json) · [官方成功参赛回执](https://github.com/M-China/mcd-developer-innovation-challenge/issues/1141#issuecomment-6362341015)

**接口变了，究竟影响了我的哪一次 Skill 调用？**

声明项目使用的工具与具体参数，读取麦当劳官方 MCP 契约，离线对照前后版本。新增必填字段、收窄枚举或工具缺失时，报告会定位到受影响的调用；未被清单依赖的工具变化，则不会把所有 Skill 都标红。

例如：活动接口在**合成演练**中新增必填 `locale`，`麦麦不忘` 的空参数调用会出现 `/locale · required`；门店样例和营养清单样例仍通过。网页的四个变化场景全部标记为合成，**不是官方接口真实故障**。

它是一个调用依赖验证工具，不是录放代理。通过仅表示清单中这组参数满足该 `inputSchema`，不证明全部输入向后兼容、服务可用或整个 Skill 正确。实际业务响应检查单独记录。

这是麦当劳程序员创意开发大赛的独立参赛作品，非麦当劳官方产品。使用 Codex 辅助开发，未申报 WorkBuddy 专项奖励。

2026-10-23 北京时间 17:59:24，官方账号 M-China-Official 已回复确认成功参赛。状态见 [参赛记录](evidence/contest-registration.json)；参赛确认不代表入榜、获奖或最终资格审核通过。

## 30 秒体验

1. 打开网页，查看真实查询基线与三个声明调用。
2. 切换“新增必填参数”“枚举收窄”“未使用接口变化”“依赖工具缺失”。
3. 点选依赖链，查看具体实参、前后契约和失败规则；可下载当前报告。

网页直接加载已发布数据，不需要 Token，也不在浏览器连接私人账户。新增调用清单和最新契约对比在本地命令行执行。

2026-10-23 北京时间 17:43 已实际发现 **35 个工具**，并完成 **3/3 个只读业务调用及响应字段检查**。公开基线仅选取四个工具的输入契约；完整响应与完整发现记录存于本地忽略目录。页面显示这次快照时间，不承诺自动刷新。

| 真实调用 | 具体检查 | 结果 |
| --- | --- | --- |
| `campaign-calendar({})` | `/structuredContent/data/dailyList` 是数组 | 通过 |
| `list-nutrition-foods({})` | `/structuredContent/data` 是字符串 | 通过 |
| `query-nearby-stores` | 公开地标“上海／人民广场”，返回 data 数组 | 通过 |

营养数据本次实际为字符串，不能因为想要数组而把它解释为“零个餐品”。这里仅检查类型，不实现营养格式解码或语义正确性验证。门店示例使用公开地标，不代表用户位置。

## 本地运行

需要 Python 3.12 或更新版本，无需模型 API。PowerShell 示例：

```powershell
git clone https://github.com/JRZ1018/mcd-skill-guardian.git
cd mcd-skill-guardian
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

先用公开基线与合成候选离线体验：

```powershell
.\.venv\Scripts\python.exe -m mcd_guardian compare --before examples/baseline.public.json --after examples/candidate-required.demo.json --out private/impact.json
```

生成 `private/impact.json` 与可直接打开的 `private/impact.html`。HTML 是无脚本、无远程资源的单文件报告，动态文本经过转义。

退出码：`0` 表示声明调用在候选中都通过；`2` 表示候选中有新增失败、原已失败或无法验证；`1` 表示文件、配置或执行错误。这让命令可以接入自己的回归流程。

### 换成自己的 Skill

复制 [examples/manifest.json](examples/manifest.json)，按自己真正调用的接口填写：

```json
{
  "format": "mcd-guardian.manifest.v1",
  "skills": [{
    "id": "my-skill",
    "name": "我的活动工具",
    "calls": [{
      "id": "default-calendar",
      "tool": "campaign-calendar",
      "arguments": {},
      "checks": [{"pointer": "/structuredContent/data/dailyList", "type": "array"}]
    }]
  }]
}
```

`checks` 只用于实际响应的 JSON Pointer 存在性和类型检查，不参与静态输入契约验证。样例清单中“麦麦不忘”对应作者的已公开项目，另两项明确是附带的示例调用；本工具不会自动推断其他仓库的完整依赖。

### 获取当前官方契约

从 [麦当劳 MCP 平台](https://open.mcd.cn/mcp) 激活自己的 Token。交互终端会隐藏输入，也可通过 `MCD_MCP_TOKEN` 提供；不要写进公开配置。

```powershell
.\.venv\Scripts\python.exe -m mcd_guardian capture --out private/current.json
.\.venv\Scripts\python.exe -m mcd_guardian compare --before examples/baseline.public.json --after private/current.json --manifest private/my-skill.json --out private/current-impact.json
```

`capture` 只初始化并分页读取 `tools/list`。示例公开基线只保留四项契约，比较完整新快照时会出现其他工具“新增”的结构记录；这不是服务器新增工具的证据。持续对比时请保留自己的前后**完整**快照，或使用相同选取范围。

### 验证真实响应

```powershell
.\.venv\Scripts\python.exe -m mcd_guardian probe --manifest examples/manifest.json --out private/live-probe.json
.\.venv\Scripts\python.exe -m mcd_guardian receipt --input private/live-probe.json --out private/receipt.json
```

`probe` 仅允许表中的三个只读工具，在同一会话发现契约并校验全部声明参数后才调用。不允许领券、下单、取消、抽奖或积分兑换。其他工具仍可离线比较输入契约。

完整实测记录可能包含账户相关内容，应保留在 `private/`。`receipt` 不复制调用参数、业务响应和原始异常，但会保留清单标识与字段位置；分享前仍需核对。它不提供通用自动脱敏保证。

## 验证、设计与边界

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe scripts/build_demo.py --check
```

测试覆盖新增必填、枚举、类型、工具缺失、未依赖变化、已有错误、外部引用、JSON Pointer、错误隐私与 CLI 退出码等。比较过程不请求外部 schema 引用；不支持的方言和不可解析引用标记为无法验证。没有开启可选 `format` 断言，也不验证所有业务隐含约束。契约差异数量不是破坏性变化数量。

交互演练数据由同一 Python 引擎生成，浏览器负责展示，不复制一套校验算法。重新生成演练：`python scripts/build_demo.py`。初次生成真实基线需显式指定私有实测记录：`python scripts/build_demo.py --from-probe private/live-probe.json`。

[MCP 接入与核验细节](MCP_INTEGRATION.md) · [工作计划和竞品定位](PLAN.md) · [官方比赛规则](https://github.com/M-China/mcd-developer-innovation-challenge/blob/main/activityGuidelines.md)

同一个 GitHub 账号仅 Star 最高的一个项目计榜，本作品不代表额外独立入榜名额。欢迎基于实际使用反馈具体的兼容性问题；觉得有用可自愿 Star，不进行互动交换或刷量。

## 许可

原创代码和文档采用 [MIT](LICENSE)。麦当劳商标、官方内容及原样保留的 [参赛声明](CONTEST_DECLARATION.md) 不在本授权范围内，适用各权利人的条款。
