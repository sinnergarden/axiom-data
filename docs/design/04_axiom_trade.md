# Engine/Runtime 初版设计：统一回测、运行时与事务账本

> 文档编号：AX-TRADE · 专项初版 v0.1 · 所属设计包 v0.2 · 2026-09-05。\
> 状态：实施参考草案，不授权联网下单、账户迁移或生产切换。\
> 上位边界：[总体设计](01_axiom_overview.md)。关联：[Core](03_axiom_core.md)、[Data](02_axiom_data.md)、[Research](05_axiom_research.md)、[UI](06_axiom_ui.md)。\
> 继承既有 SQLite ledger 的账户主键、流水与投影分离；发送恢复、乱序回报、资金精度等是本版补充约定。准确交易规则、券商能力和费率须专项取证。

本专项描述 axiom-engine 内的逻辑模块，物理仓库按[补丁 A](07_重要补丁_A.md)执行；Data 输入与首版范围已同步 2026-09-27 个人版，不改变本模块的计算或交易职责。

2026-09-28 Data 接口实证：DecisionBatchGate 已用真实输入验证批次 Snapshot 固定及 decision/market_replay 分离；本次没有实现或验收委托、成交、账户与完整 BacktestRun。
见 [真实 Developer 教程](../../notebooks/developer_tutorial.html#section-14) 与 [设计对照](../design-conformance.md)。正文继续定义目标合同。

## 1. 定位：策略运行与执行系统，不只是实盘下单

Trade 内包含 runtime、唯一正式回测、shadow/real、行情/Broker adapter、统一账户核算、SQLite ledger、恢复/对账与账户评估。Runtime 不再单拆 repo。

```text
Request
→ resolve / validate / freeze ResolvedRunPlan
→ Clock / Feed / SignalSource
→ Core decision
→ persist OrderIntent
→ SimBroker or RealBroker
→ fills / account events
→ accounting + ledger
→ run artifacts / evaluation / UI projections
```

Trade 不重新实现 Feature、推理、选股与换仓；Research 不另写一套账户回测；UI 不直改数据库。第一版一账户对应一个策略部署/虚拟实验，暂不做多策略共享实盘资金的复杂分摊。

## 2. 外部交互与模式

| 模式 | 市场与时钟 | 信号来源 | 成交与账户 |
|---|---|---|---|
| backtest | HistoricalClock + 固定 MarketReplayView | CachedSignalSource 或 ModelSignalSource | SimBroker + 独立临时账本/状态；完成后不可变 BacktestRun |
| shadow | 实际时钟 + 已登记 Feed | 发布包同一推理链 | SimBroker + shadow SQLite ledger |
| real | 实际时钟 + 已登记 Feed | 发布包同一推理链 | RealBroker + real SQLite ledger + 对账 |

mode 只在 Runtime 组装 adapters；Core 的 Context 不接 mode。SimBroker 与 RealBroker 实现相同执行事件协议，但不能承诺成交或净值相同。

Research 用 P09 提交回测请求，Trade 返回 BacktestRun/P10 评估；Data 提供 P02/P03，实际使用 Qlib 时才需要 P04；Core 消费 P07 并输出 P08 Intent；UI 经 P12 查询运行、图层和账户投影。

Data 的逻辑 FactView/MarketReplayView 是固定 Snapshot + QuerySpec，可内嵌本次 Plan；不要求预先发布独立 View artifact 或逐域 commit。Runtime 检查本次实际输入、持仓、lookback 与执行范围；不依赖通用 Data admission 平台。market-public 与 system-observed 是不同时间问题，运行明确选择 policy，不能用后补公开证据伪造系统当时已收到。

Runtime adapter 消费 Data 的 P02 DataBatch，并映射为 Core 的 P05 FactBatch；成员/事件查询同时固定 PIT policy 和 cutoff。执行侧 P03 查询固定事件范围、价格和历史修订解释，可预读文件但只能随执行时钟释放事件。映射不重新解释单位/复权/PIT；决策输入、执行回放与 Research Label 结果保持用途边界。长 run 保存实际 batch/session→data/query/model refs，供 P12 重放；当前默认 Snapshot 不能替代历史输入。

离线回测必须可在不安装/登录券商、无 real 凭证的环境运行。Live/Broker SDK 按需加载，不能 import Trade 就连接券商。

## 3. 一套回测，两种信号输入

### 3.1 日常研究

```text
SignalRun + portfolio/rebalance/risk policy + MarketReplayView
→ unified runtime → Core → SimBroker → Accounting → Evaluation
```

只改换仓/持仓/风控，不重训、不重新预测。Research 的 IC、Recall@K 等信号评估是另一个分析步骤，不是第二个 fast backtest。

### 3.2 发布前与问题复验

```text
StrategyRelease + FactView + frozen model schedule + MarketReplayView
→ 同一 runtime 的 ModelSignalSource
→ 同一 Core/SimBroker/Accounting/Evaluation
```

model source 只执行保存模型，不在回测里重训。滚动历史有明确 fold→ModelRelease→允许预测日期映射，不能用最新模型回推全历史。

两种 source 必须在相同 `signal_stage` 接合，避免 cached 已组合分数被再组合一次。依赖账户/成交路径的信号不能作为无条件跨策略复用的缓存，必须随运行重算或绑定对应状态轨迹。

## 4. Request、ResolvedRunPlan 与运行身份

### 4.1 请求最低字段

```yaml
request_id: request_example
mode: backtest
input_mode: cached_signal
signal_run_ref: sig_example
strategy_release_ref: sr_example
market_view_ref: market_example
fact_view_ref: null
initial_account_ref: seed_example
scope: {start_session: "2021-01-04", end_session: "2026-07-31"}
execution_profile_ref: execution_example
evaluation_spec_ref: evaluation_example
```

null 只用于当前模式不适用项，附原因；必要项缺失应拒绝，不用 `NOT_AVAILABLE` 假装绑定完成。

### 4.2 一次解析

Runtime 解析 release/model/snapshot/view/universe/default，并固定具体 refs；代码、配置、依赖包和 adapter 配置也固定。解析后所有子组件复用同一 `ResolvedRunPlan` 和加载对象，不再次访问指针、mtime 或 mutable checkout。

```text
run_id                    一次逻辑运行
attempt_id                一次实际执行/恢复尝试
decision_id               一次策略决策
order_intent_id           一项持久意图
client_order_id           外部发送稳定身份
broker_order_id           券商返回的订单身份
account_id                现金和持仓的主键
strategy_release_ref      本次决策策略来源
```

一个失败 attempt 不生成第二套相同意图。显式“新研究重跑”产生新 run；同 run 的恢复保持 logical IDs 和账户处理水位。

Plan 记录实际使用的代码来源/发布包、配置、依赖、data roots 定位信息、refs、cutoff、initial state、execution/evaluation profiles。Data 侧允许可恢复 commit + 依赖锁 + 实际配置，不强制代码归档；未保存 dirty 修改不能冒称对应 commit。Runtime 的生产发布包要求不变；不能只记主 repo commit，也不要求所有组件位于同一 checkout。

### 4.3 长服务与实时输入

按 session/决策批次固定计划，下一批次可采用新数据/策略并重新 preflight。已启动的 D1/M1 不会因 current 指向 D2/M2 而变。

尚未到来的 Feed/Broker 输入无法提前 hash。启动时固定连接/来源/事件合同，运行中按序保存实际收到的输入及接收时间。账户变化正常流入下一决策；冻结身份不等于冻结未来市场和账户。

## 5. 时间推进与事件协议

共同事件 envelope：schema/version、event_id、source/source_event_id、account_id、run_id、causation/correlation IDs、event_time、received_at、logical sequence、payload digest。历史同时间事件固定排序；真实回报按接收序持久化，并保留业务发生时间。

| 事件 | 生产者 | 作用 |
|---|---|---|
| MarketEvent / CorporateActionFact | Data/Feed adapter | 市场和权益事实 |
| SignalEvent | SignalSource | 当前允许使用的最终投资分数 |
| TargetEvent / OrderIntent | Core | 目标、交易意图，不直接改持仓 |
| OrderEvent / FillEvent | Broker adapter | 已发送/确认/拒单/撤单/部分或全部成交事实 |
| AccountEvent | Accounting / reconciliation | 费用、股息、送转、入出金、结算/调整 |
| AccountProjection / PositionSnapshot | Trade | 带 committed sequence 的账户视图 |

建议日级顺序：结转可用状态/权益事件→取得到 cutoff 的事实和账户→生成决策→登记/发送意图→按市场时钟处理回报→记账→估值/快照→报告。实际事件阶段由 ExecutionProfile 明确，不把所有公司行动统一塞到同一个日终。

Decision Reader 与 Simulator Reader 隔离。Simulator 可以处理之后发生的价格区间，但不能把全天 high/low/close/volume 提前交给开盘 Core。日线不足以证明精确开盘可成交量；使用日成交量约束的 profile 必须标明估计成交窗口、可用信息和日级近似，不能据此宣称精确开盘撮合，也不能用尚未确认的日终成交余额进行更早的日内再决策。

## 6. ExecutionProfile：最低回测真实性

| 项目 | 初版要求 | 不允许 |
|---|---|---|
| 信息/价格时序 | 决策 cutoff、最早委托/成交、参考价和成交窗口明确 | 未发生收盘信息却按同一收盘价买入 |
| 停牌/缺价 | 不成交；估值价格和陈旧状态单列 | 填价后当真实可成交 |
| 可用现金/可卖数量 | 接受账户冻结、结算与规则约束 | 目标仓位当实际仓位，超可卖数量 |
| 涨跌停 | 按规则/方向、日级可观测性做保守模拟 | 只要碰价就认为能成交 |
| 成交量 | 事前规划用已知流动性，模拟按明确窗口执行 cap/partial fill | 先用未来成交量选股或无限容量 |
| 费用/滑点 | 佣金/税费/最低费用/价格滑点各自明确 | price 已含滑点又从现金重复扣 |
| 公司行动 | 股息、送转等进入权益/现金/数量与成本基准 | 复权收益与分红现金重复计入 |
| 未成交 | 保留、到期、撤销及再次规划规则 | 每天丢掉挂单并重发 |
| 不支持事件 | profile 的 capability check 阻断受影响正式范围 | 默默忽略配股、退市/代码变化 |

细致冲击模型与订单簿不是首版前提；报告必须保留可执行性限制，并做成本和容量敏感性。不要先假定 1000 万规模一定没有影响，也不写一个通用资金阈值替代标的/换手分析。

初版规则字段随市场、证券类别和日期版本化；实际费率、涨跌幅、最小交易单位和结算细节在上线前用当前权威规则与 Broker 样本确认，不在总纲硬编码。

## 7. 账户与 Ledger 数据模型

账户权威分两层：Trade ledger 是系统内部流水与状态；RealBroker 是外部订单/成交/账户的对账依据。差异产生显式记录和调整事件，不用券商快照直接覆盖旧账。

账户查询以 account_id 为主键；strategy 仅用于归因，不能拿 strategy_id 找钱。backtest、shadow、real 使用不同环境/账户空间，real 数据库与模拟数据库物理隔离。

### 7.1 建议表

| 表 | 主键 / 关键约束 | 内容 |
|---|---|---|
| accounts | account_id | environment、币种、Broker/account 映射、状态 |
| runs / attempts | run_id / attempt_id | frozen plan、执行状态与 checkpoint |
| order_intents | intent_id；account+logical intent key 唯一 | Core 意图、expected account version、原因 |
| orders | order_id；account+client_order_id 唯一 | Broker 身份、请求、状态、累计成交/撤量 |
| outbox | command_id / 幂等键 | 待发送、已尝试、待确认命令及关联意图 |
| inbound_events | account+source+source_event_key 唯一 | 标准化回报、原始回报引用、应用状态 |
| fills | account+broker+trade_key 唯一 | 每笔增量成交，而非累计成交量重复入账 |
| cash_ledger | cash_event_id | 现金、冻结/应收/费用变化及关联事实 |
| position_ledger | position_event_id | 总量、可用/冻结、成本/公司行动变化 |
| cash_state / positions | account；account+security | 由事务维护的当前投影及 committed sequence |
| portfolio_snapshots | account+valuation point+version | 净值、估值来源、现金流、stale 标记 |
| corporate_action_applications | account+event+entitlement version 唯一 | 权益基准、应收/到账/股份变化的应用记录 |
| reconciliation_runs / differences | reconciliation_id / diff_id | 委托、成交、现金、持仓核对与处理 |
| checkpoints / migrations | account+sequence / schema version | 恢复点和可重复迁移 |

Broker 的 trade_key/source_event_key 必须按真实唯一范围构造：若原 ID 只在交易日或 venue 内唯一，则把 account、source、session/venue 一并纳入。不能假定券商 ID 永不复用。

可以按实际需求合并相近表，但不能删除意图、外部发送、回报去重、资金与股份流水之间的逻辑区别。首版不强制复杂双重记账或基金会计，必须让每个状态变化都有可追溯原因。

### 7.2 金额、数量和费用

现金流水采用明确币种最小单位的整数（CNY 可按分），运算使用 Decimal/定点而非二进制浮点累加。价格与中间费用保留足够精度，最终按 Fee/RoundingContract 入账；数量为对应证券规则允许的精度。序列化保留 scale。

滑点若已反映在 fill price，仅作为诊断成本拆分，不再现金扣一次。佣金若有按单最低额，不能对每笔 partial fill 重复收最低额；费用晚到或更正生成费用调整事件，不能修改既有 fill 的经济历史。gross/net cash、税费、应收/实收分别表达。

## 8. 事务与幂等：外部发送不是数据库事务

### 8.1 写入原则

本地短事务使用单写者协调、外键与唯一键。SQLite 的 WAL 允许读写并发但仍非多写者数据库；初版应避免跨网络文件系统放活跃账本。相关行为以 SQLite 官方文档为依据。[E1]

Real/shadow 账本采用 WAL、`foreign_keys=ON`、`synchronous=FULL` 和显式 busy/重试策略作为本版保守默认。WAL+NORMAL 的已提交事务在系统崩溃/掉电后可能丢失，不能只因旧规格采用 NORMAL 就不区分 durability；FULL 也不替代可靠存储与备份。[E2]

### 8.2 准备发送：事务 A

在一个数据库事务中：验证 account_state_version→记录 decision/intent→校验 logical key→创建 order/outbox→按预算冻结现金/数量→提交。

Broker 网络调用发生在事务外，不能长时间持有写锁。并发两次相同逻辑请求只产生一个 outbox 任务。Core 版本检查冲突则重新决策或拒绝，不盲目复用旧计划。

### 8.3 发送与回包

```text
PREPARED → SEND_ATTEMPTED → ACKNOWLEDGED / REJECTED / UNKNOWN
```

提交给 Broker 后进程可能在记录 ACK 前崩溃。恢复时查询稳定 client_order_id、Broker 委托/成交列表；无法判断是否已经发送成功则保留 UNKNOWN 并阻断相关重发。**不能声称可以跨 SQLite 与 Broker 实现一个原子 exactly-once 事务。**

稳定客户端 ID 是否由具体 Broker 原样支持是能力项；不支持时制定匹配/人工处理策略，并在实盘门禁前验证。不能只凭请求超时就重下同一订单。

### 8.4 接受回报与记账：事务 B

原始回报可先保存 inbox；小型原始 payload 可随 inbox 事务保存，较大 payload 则先原子发布不可变对象再登记引用，禁止账本指向尚未落盘的回报文件。账户应用事务必须包含：去重检查→标准事件应用→新增 fill/费用/资金与持仓流水→更新订单累计状态→更新冻结和当前投影→更新 strategy state/checkpoint 与已处理水位→提交。

任何中途异常整体回滚，不出现成交已记但持仓未记。保存到 inbox 但未应用的事件可恢复重试。同 key 同 payload 是幂等重复；同 key 不同 payload 是冲突/更正，不能简单丢弃或覆盖。

### 8.5 乱序与部分成交

成交可能先于委托确认、撤单请求后仍有有效成交、费用稍后才到。事件应用以累计事实和合法状态约束处理，不能只凭枚举状态“已经 canceled”就丢成交。

Broker 同时提供累计数量和增量回报时，adapter 明确归一化，避免把 100→150 当两笔 100+150。撤单只有确认的剩余量可释放冻结；成交更正以反向/调整事件表达，保留旧流水。

### 8.6 完成状态

一个运行结束不代表所有订单已成交。区分 `DECISION_COMPLETE`、`EXECUTION_PENDING`、`RECONCILIATION_REQUIRED`、`COMPLETE`、`FAILED`；完成时必须有一致账户快照/事件水位与产物状态。

## 9. 公司行动、估值与权益

Data 提供事件事实及日期；Trade 按账户实际权益登记处理。至少区分登记基准、除权、到账、股份上市/可卖日期，不把所有权益在公告日立即加到账户。

现金股息可先记 receivable，到账再转现金，避免除息日净值虚降又在到账日重复获利；送转增股同时处理成本基准和可用数量。税费或后续追缴按实际/模拟 profile 写明确事件。

账户净值基本关系为：现金 + 持仓市值 + 应收 − 应付；冻结现金仍在现金总额内，不能加两次。可用现金与总现金不是同一个字段。

停牌可按固定估值政策使用上次有效价，但必须标 stale 与持续时长；退市/停牌不等于自动清零或永远按最后价计算良好绩效。缺失的退出/清算事实在支持范围内明确限制或阻断。

### 9.1 初版核算 golden 样例

以下仅为合成核算测试参数，数量粒度设为 1 股，不是实际费率或市场委托合法性样例。初始现金 10,000 元，无持仓；成交价每股 10 元买入 100 股、费用 5 元，期望现金 8,995、持仓 100，10 元估值净资产 9,995。重复应用同 fill 不变化；下个可卖 session 以 11 元卖出 40 股、费用 5 元，现金 9,430、剩余 60 股，11 元估值净资产 10,090。

另设持有 100 股、每股 1 元现金股息：确认权益时应收 100，到账时现金加 100/应收减 100，不得净资产再增加一次。送股/转增夹具核对数量、成本基准、可卖日期；未知税费或精度不能用这些简化示例替代真实 profile。

## 10. 恢复、对账、备份与部署

### 10.1 恢复次序

```text
阻断新发单
→ 读取冻结 Plan / checkpoint / 未应用 inbox / 未确认 outbox
→ 重放可确定的内部事件
→ 查询 Broker 委托、成交、资金和持仓
→ 去重并应用差异/确认 UNKNOWN
→ 一致性检查
→ 明确允许继续发送
```

系统 restart 不重新选择最新 StrategyRelease，也不自动新建同账户同日意图。Broker 对账前不能把本地“没 ACK”认定为外部“没订单”。人工调整通过 owner service 产生记录，禁止 SQL 直接改余额。

### 10.2 备份

活动账本采用 SQLite backup API 或已验证的安全备份流程，不只复制一个正在写的 `.db` 文件而忽略 WAL。Online Backup API 提供一致的数据库副本；实现应校验复制结果和恢复流程。[E3]

备份覆盖 ledger、必要策略状态/Plan、原始回报、发布包引用和恢复凭证定位（不泄露 secrets）。Catalog 可重建不等于 ledger 可删除。迁移前隔离备份，迁移执行走正式 service/schema migration，异常回滚或恢复。

### 10.3 部署

保留多个 systemd timer/service：Data sync、Research retrain、Trade preopen/postclose 等，各调用本 owner 公共 API。统一的是合同和冻结步骤，不是巨型脚本。

Trade 实际发布包、代码、配置、account/environment、run profile 与依赖在 DeploymentManifest 中固定。生产从冻结发布包运行，不能混读主工作区配置；文件位置可不同但身份必须可证明。

执行安全闸包括账户不符、real 未批准、数据/信号失效、对账差异、Broker 连通性、未知订单、重复任务、人工停机。它可以拒绝发送，不得悄悄改变投资策略；下一步由显式新决策处理。

## 11. 统一账户评估（P10）

Research 和 UI 消费本仓标准结果，不另实现 CAGR/DD。EvaluationSpec 固定 benchmark、price/total-return 口径、净值/入出金调整、频率、年化、费用、窗口、risk-free 及缺失处理。

| 指标 | 最小定义与边界 |
|---|---|
| CAGR | 以现金流调整后的财富/净值指数计算；样本不足、终值无效不编造年化 |
| MaxDD | 同一净值路径对历史前高的最坏回撤 |
| Calmar | 同区间 CAGR / abs(MaxDD)；0 分母返回明确状态 |
| Sharpe | 明确 r−rf、标准差、年化因子；不设跨策略通用优秀阈值 |
| Recovery time | 谷底→恢复前高；并列出前高→恢复的 underwater duration；期末未恢复标 censored |
| Rolling 1Y/2Y excess | 固定基准、1Y/2Y 实际窗口定义；默认几何相对收益 `(1+Rs)/(1+Rb)-1`，差值另命名 |
| Worst-year / worst rolling 1Y | 区分完整日历年和滚动窗口，首尾不完整年单列 |
| 执行与风险 | 换手、费用、滑点、成交率、未成交原因、容量/行业/单票集中度 |
| Episode | 实际入场至退出、持有时长、MFE/MAE、capture/giveback 与贡献；公式版本化 |

这些是本系统拟采用的口径约定，具体 annualization/window profile 在首次正式比较前冻结。重叠滚动窗口的胜率是样本描述，不写成独立入场的获胜概率。

报告同时列 Data/PIT、样本/OOS、执行 profile、未支持事件和未检查范围。收益高不能覆盖正确性 blocker；费用或 benchmark 口径不同的回测不可直接混在排行榜上。

## 12. 公共入口与只读接口

```python
run_backtest(request: BacktestRequest) -> BacktestRunRef
run_session(request: SessionRequest) -> RunRef
resume_run(run_id, approved_recovery_policy) -> RecoveryReport
reconcile(account_id, scope) -> ReconciliationReport
query_account(account_id, as_of, committed_sequence=None) -> AccountProjection
query_run(run_ref) -> RunSummary
query_chart_layers(run_ref, security_id, interval) -> RuntimeChartLayers
```

CLI 只做解析与调用，Notebook/Agent/systemd 使用相同 service。只读 inspect 不发送委托、不调用会写生产目录的初始化。UI 查询返回 committed watermark，避免同一屏拼接不同事务阶段的 cash/positions。

```text
src/axiom_trade/
  runtime/ backtest/ adapters/{feed,signal,broker,state}/
  accounting/ ledger/ reconciliation/ reporting/
  services/ query/ cli/ deploy/ tests/
```

## 13. 分阶段验收

| 阶段 | 最小交付 | 不能提前宣称 |
|---|---|---|
| T-M1 | 离线单回测、cached signal、Core 接入、SimBroker、核算/标准报告 | 真实 Broker 可用 |
| T-M2 | SQLite ledger、intent/outbox/inbox、部分成交、恢复夹具、只读查询 | 已经获得真钱授权 |
| T-M3 | shadow 持续运行、model source 一致性、对账与部署冻结 | 不同实盘成交环境收益等同回测 |
| T-M4 | 明确 Broker adapter/权限/规则/账户验证后受控 real | 通用支持所有券商和证券事件 |

## 14. 最小验收标准

| ID | 操作/失败点 | 必须结果 |
|---|---|---|
| T01 | Research 同 SignalRun 多策略回测 | 不训练/推理，使用同一 engine，独立账户/结果 |
| T02 | 同信号的 cached/model source | Target/Intent/模拟账户轨迹一致，无重复标准化 |
| T03 | 运行中改 model/data/default/配置 | 已启动 Plan 不变，下批次才采用新版本 |
| T04 | 开盘决策尝试读未来 close/volume | 不可见/拒绝；执行侧信息不泄入 Core |
| T05 | 停牌、缺价、一字限价、低成交量 | 保守成交/不成交/部分成交，明确原因和估值 |
| T06 | 分红、送转、应收到账及重复事件 | 权益正确、不双算收益、不重复入账 |
| T07 | 两个并发相同下单请求 | 一份逻辑意图/outbox，状态版本检查生效 |
| T08 | 事务 A 后、发送前 crash | 可以安全继续发送同一已持久意图 |
| T09 | Broker 已接收但 ACK 丢失 / 进程 crash | 查委托/成交后确认；未知则阻断，不盲目重发 |
| T10 | apply fill 中途故障 | fills/cash/positions/order/checkpoint 一起回滚 |
| T11 | 同 fill 重放 / 同 key 不同 payload | 前者幂等，后者冲突/更正可追踪 |
| T12 | 成交先于 ACK、撤单后迟到成交、累计回报 | 不漏记/双记，冻结与剩余量正确 |
| T13 | partial fills + 按单最低费用 + 费用晚到 | 费用正确累积/调整，滑点不双扣 |
| T14 | 买入未可卖、已冻结数量再次卖出 | 拒绝超可卖/超现金，规则按 profile |
| T15 | 多账户/环境同 strategy 名 | 查询以 account_id 隔离，backtest 不写 real |
| T16 | Broker 与本地现金/持仓不一致 | 暂停相关发送、显式 reconciliation，不覆盖流水 |
| T17 | 备份恢复、checkpoint 重放、换发布目录 | 可恢复账户与原计划；事件与投影一致 |
| T18 | 已发委托但报告/UI 生成失败 | 成交不回滚；报告可重建，运行状态不伪成功 |
| T19 | 已知净值/现金流 fixture 计算指标 | CAGR/DD/Calmar/Sharpe/恢复/滚动超额/Worst-year 口径正确 |
| T20 | UI 同屏现金/持仓/图层查询 | 同一 committed watermark；意图/委托/成交有区别 |
| T21 | 不安装 Broker SDK，禁网跑历史回测 | 正常完成，无环境写入/凭证依赖 |
| T22 | state contract 或 execution rule 不兼容 | preflight 拒绝；不得忽略后继续真钱运行 |

交易所/券商特定实盘能力必须追加真实 sandbox/只读对账证据；隔离 fake Broker 通过不等于真钱已验收。

## 15. 来源、补充决策与待确认

源材料：`qsys_sqlite_ledger_prd.md` §3–4、§9–16；Axiom 总纲 v0.1 §7、§10–13；本对话关于停牌/公司行动/容量、评估指标、scheduler 和 pointer 的输入。保留账户 ID 主键、流水/投影分离和事务性；现金精度、outbox/inbox、迟到回报、FULL 默认与只读一致查询为本版明确补充，不声称原文已完整规定。

外部技术核对（2026-09-05；只用于对应 SQLite 行为）：

- [E1] SQLite WAL：`https://www.sqlite.org/wal.html`。
- [E2] SQLite synchronous：`https://www.sqlite.org/pragma.html#pragma_synchronous`。
- [E3] SQLite Online Backup API：`https://www.sqlite.org/backup.html`。

待确认：Broker client_order_id/query 能力；准确费用/税务/交易规则与公司行动范围；最小执行窗口和量限制近似；现金四舍五入；真实账户启动/迁移基准。未完成前只交付相应级别，不以示例参数直接实盘。
