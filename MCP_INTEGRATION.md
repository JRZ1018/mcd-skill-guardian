# 麦当劳 MCP 接入与真实核验

官方文档：[M-China/mcd-mcp-server](https://github.com/M-China/mcd-mcp-server)。固定接入 `https://mcp.mcd.cn`，官方 Python MCP SDK 1.30.0，Streamable HTTP，`Authorization: Bearer`。凭证从环境或隐藏输入读取，不在公开文件中保存。

## 两条明确分开的路径

`capture`：初始化连接 → 分页 `tools/list` → 保存完整工具契约至指定本地文件。保留 `name`、`inputSchema`、`outputSchema`；工具总数来自实际发现。它不调用业务接口。

`probe`：先验证清单并拒绝非白名单工具 → 同会话初始化及发现 → 对**全部**声明参数作 schema 校验 → 串行调用三个白名单只读工具 → 保存私有完整响应。未解析/不支持 schema 不进入业务调用。不会从接口描述猜测实参，也不对数据作业务变更。

白名单固定为 `campaign-calendar`、`list-nutrition-foods`、`query-nearby-stores`。`query-nearby-stores` 的示例实参由实际发现的参数定义确定：`beType=1`、`searchType=2`、`city=上海`、`keyword=人民广场`，表示公开地标附近的到店取餐门店查询，不使用定位、不查询用户配送地址。

## 2026-10-23 真实验证

见 [evidence/live-verification.json](evidence/live-verification.json)。北京时间 17:43 完成，发现 35 个工具，三次业务调用均返回 `isError=false`、顶层 `structuredContent.success=true` 与整数 `code=200`，所依赖响应字段类型全部通过。

| 工具 | 参数 | 响应检查 |
| --- | --- | --- |
| campaign-calendar | `{}` | `/structuredContent/data/dailyList` array |
| list-nutrition-foods | `{}` | `/structuredContent/data` string |
| query-nearby-stores | 示例清单中的公开地标查询 | `/structuredContent/data` array |

已实际调用上述工具，不是仅列出接口名。业务信号缺失标为 unknown，显式失败标为 fail，不用“连接成功”替代“业务成功”。类型检查不证明响应语义、营养解码、门店营业承诺或价格正确。

## 离线影响分析

`compare` 不导入联网客户端、不读取 Token，只按清单中的实际参数，用本地 JSON Schema 验证器对照两份输入契约。外部 `$ref` 不联网解析；未知方言或无法解析的本地引用标为不可验证。未启用可选 format 断言。

报告包含具体调用的新增失败、原已失败、恢复通过、通过和无法验证。全局 `changes` 可包含输入和输出 schema 的结构差异，但调用通过/失败**仅**由 inputSchema 验证决定。`checks` 是另一路对实际响应的检查，不会冒充官方长期输出契约。

公开基线从真实发现中选取四项输入 schema，仅去除 description/title/examples/default/$comment 注解，保留实际校验约束；第四项 now-time-info 只用于“未被依赖工具变化”的合成演练，未调用它。公开基线不含其他 31 项契约，不能用其缺失推断官方服务没有其他工具。

## 真实与合成的边界

网页默认展示同一真实基线对照自身，以及实测摘要。其余四组候选由脚本复制该基线后显式修改，`mode=synthetic`，并附合成说明。新增必填、枚举收窄、未使用工具变化和工具缺失都不是本次发现的官方故障。

演练报告来自 Python 核心引擎，前端只安全展示结果。静态页面不收集 Token，不调用业务接口，不包含分析或广告追踪。

## 公开信息投影

`private/live-probe.json` 不提交。公开验证摘要只保留工具名、清单标识、检查规则数量、状态和时间，以及私有记录 SHA-256；不复制业务数据、调用参数、原始异常、请求头或会话 ID。

公开演示的实参仅来自随附、已检查的公开示例清单，包含公开地标；通用 compare 报告不自动附加用户实参。schema 差异可能包含 schema 自身的常量，清单标识、schema 和报告均须在分享前人工核对，不能保证任意输入的自动脱敏安全。

哈希可在拥有私有原始文件时复算，不是麦当劳官方签名、独立证明或资格审核结果。真实凭证未进入公开文件。配置示例的 `${MCD_MCP_TOKEN}` 是否展开取决于所用 MCP Client；本项目 Python 客户端直接读取该环境变量。

## 业务价值

单纯“schema 变了”不足以判断项目要不要改。将结构差异与显式调用清单连接，开发者能看到具体哪次输入不再满足契约，以及哪些变化暂未影响已声明样例，从而形成可复查的迁移核对记录。它支持开发排查，不代替实际场景回归、模型评测或最终参赛审核。
