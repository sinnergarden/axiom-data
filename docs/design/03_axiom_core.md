# Engine/Core 初版设计与最小验收标准

> 文档编号：AX-CORE · 专项初版 v0.1 · 所属设计包 v0.2 · 2026-09-05。\
> 状态：实施参考草案；模块名与接口为本版实现约定，不代表现有代码已经具备。\
> 上位边界：[总体设计](01_axiom_overview.md)。关联：[Research](05_axiom_research.md)、[Trade](04_axiom_trade.md)、[Data](02_axiom_data.md)。

本专项描述 axiom-engine 内的逻辑模块，物理仓库按[补丁 A](07_重要补丁_A.md)执行；Data 输入与首版范围已同步 2026-09-27 个人版，不改变本模块的计算或交易职责。

2026-09-28 Data 接口实证：Research 所有的 adapter 已将真实月末 DataBatch/成员结果映射为 FactBatch、ExecutionContext 和 FeaturePlan，并执行 identity/lag/return；这不是全部策略或决策算子的真实验收。
见 [真实 Developer 教程](../../notebooks/developer_tutorial.html#section-14) 与 [设计对照](../design-conformance.md)。正文继续定义目标合同。

## 1. 一句话定义

Core 是一套可被 Research 和 Trade 调用的**共享计算与投资决策库**。同样的已知事实、信号、账户、策略状态、参数与时间，应该得到同样的目标仓位和订单意图。

Core 不是一个总调度进程，不拥有账户数据库，不读取“最新模型”，不撮合订单。它通常不单独启动，也不因每个新 Feature 或策略点子而修改。

### 1.1 范围

| Core 内 | Core 外 |
|---|---|
| 中立 Frame/Context/Result 类型与插件执行 ABI | Data Reader、Qlib 初始化、路径发现 |
| 通用 Feature 算子、固定计划执行 | 具体 Feature 配方、候选研究、历史物化与缓存 |
| 模型推理接口与已拟合变换的执行 | 模型训练、搜索、OOS 切分、模型发布审批 |
| 信号组合、标准化和候选约束协议 | SQL 文件读取、signal registry/default 解析 |
| 目标仓位、换仓、策略风控、订单意图规划 | Broker、撮合、SQLite、现金与股份入账 |
| 策略状态转换与解释 trace | 系统时钟、event loop、调度、恢复和对账 |

Data 的个人版输入合同见 [Data §7](02_axiom_data.md#7-一个-reader薄的消费者映射)：ViewRef 可以内嵌 Snapshot + QuerySpec，不要求独立 Data View/DomainCommit 发布服务。Core 仍只消费中立 Frame；只为真实接入的消费者做薄 adapter，不以 Data 的通用认证系统作为 Core 前置依赖。

P02 的 Reader 返回 Data 所有的 DataBatch；P05 的 FactBatch 是 Core 所有的中立计算 ABI。Research 和 Runtime 各自在调用侧映射，两者不能因都叫“事实表”而合并 owner。映射保留键、字段顺序、单位、可见性、缺失与来源；不重复执行 Data 的 PIT/复权，也不从 Label 结果补 Feature。共享列缓冲可以避免复制，不要求建立额外服务或落盘格式。

稳定边界不代表不许改 Core。新增通用语义才升级 Core；某策略“60/180 如何加权、何时减仓”应由冻结插件或配置表达，不能堆成很多 `if strategy_name`。

## 2. 与其他模块交互（P05、P07、P08）

```text
Research：FeaturePlan / FactBatch / frozen transform / ModelHandle
          → Core → FeatureFrame / PredictionFrame / SignalFrame

Trade：SignalFrame / AccountState / StrategyState / DecisionContext
       → Core → TargetPortfolio / OrderIntent[] / NewStrategyState / DecisionTrace

Trade：Fill/Account notification + StrategyState
       → Core → NewStrategyState / trace
```

Data 不直接调用 Core；Data 的固定事实由调用方 adapter 转成 FactBatch。Core 不 import Research/Trade 项目。生产执行的是 Research 发布的轻量策略包，不是 mutable Research workspace。

UI 不提交交易计算请求；它读取 Research/Trade 已保存的 trace。原因码与 schema 可随 Core release 提供，不需要 Core Web 服务。

## 3. 主要类型与最小语义

| 类型 | 最少包含 | 约束 |
|---|---|---|
| FactBatch | `(security_id, session)` 键、typed columns、单位、可见时间、来源 Snapshot + QuerySpec（逻辑 ViewRef）、missing metadata | 由调用方按 cutoff 裁剪；不能含未声明未来数据 |
| FeatureFrame | 行键、有序 schema、stage、FeatureRelease、input refs、validity | 禁止位置 `.values` 静默拼接 |
| TransformState | 拟合参数、fit scope/ref、实现版本、输入输出 schema | 推理不重新 fit |
| ModelHandle | 已加载推理对象、ModelRelease、expected schema、backend version | 不能在 predict 内寻找模型文件/指针 |
| PredictionFrame | 行键、数值、目标语义、model/fold refs、validity | 回归值、排名分数、概率不同义 |
| SignalFrame | 行键、score/stage、cutoff、模拟可用时点、source refs、reference universe、invalid reason | 已组合信号不能再次无意归一化 |
| TargetPortfolio | 证券目标权重/数量、剩余现金目标、intent kind、约束结果 | 区分 no-op、keep、liquidate、明确 target=0 |
| OrderIntent | security、side、quantity、价格约束、有效期、reason、稳定 logical key | 不是券商订单，不代表已成交 |
| StrategyState | contract/version、策略记忆、last processed logical event | 不保存权威现金/持仓 |
| DecisionTrace | 原始分数/目标、约束后结果、舍入/拒绝原因、输入 refs | 对“未买/未卖/未换”也有说明 |

唯一证券键使用 security_id；显示代码来自有有效期的映射。时间/session/数量精度、列顺序、空值与 tie-break 都属于 contract。

### 3.1 DecisionContext

```yaml
decision_id: dec_example
decision_time: "2026-09-04T00:55:00Z"
knowledge_cutoff: "2026-09-03T15:59:59Z"
feature_session: "2026-09-03"
trade_session: "2026-09-04"
event_sequence: 42
strategy_release_ref: sr_example
universe_ref: universe_example
market_rule_ref: rule_example
account_state_version: 17
execution_constraints_ref: constraints_example
rng_seed: null
```

示例时间不是默认交易时刻；实际 cutoff 必须由版本化日历和运行协议决定。Context 中没有 `mode=backtest/shadow/real`。

账户、成交和行情会随事件变化；“固定输入”指这一次决策的显式输入。运行中接到新成交时，可以基于新的 AccountState 做下一次决策，不是把开盘账户锁死一整天。

## 4. Feature 与推理的共享实现

### 4.1 FeaturePlan

Research 发布 FeaturePlan，包括输入字段/稳定派生、lookback、算子图、lag、输出列、缺失/异常处理、参考截面、执行版本和插件引用。Core 执行同一个计划，批量研究与逐日推理不能分别实现两个近似公式。

```text
连续历史 FactBatch
→ 每证券时序算子
→ 按当期 PIT membership 选择横截面
→ rank / zscore / neutralize 等声明变换
→ base / cross_sectional FeatureFrame
→ 已拟合 TransformState
→ model_input FeatureFrame
→ ModelHandle.predict
```

跨证券 rank 的参考集合必须显式，即使只请求一个 UI/持仓证券也不能用这一个证券重新归一化。UI 本身不走此计算链。

### 4.2 Fit 与 transform

拟合逻辑由 Research 在训练期调用并生成 TransformState；Core 只负责通用算子或已冻结插件的可重复执行。若加入新学习型预处理，应明确 fit 接口与结果归属，不把隐式 fit 藏进 transform。

同一 base FeatureBuild 可以被不同训练 fold 的 scaler 转成不同 model_input；因此后者必须带 TransformState/ModelRelease/fold 身份，不能共享一个不分 fit 的 cache。

### 4.3 插件包

```text
Strategy runtime package
  manifest / dependencies.lock
  feature_plugins/       纯计算实现
  inference_plugins/     保存模型的推理适配
  signal_plugins/        纯信号变换
  portfolio_plugins/     组合/换仓/风控
```

发布包不得 import 训练工作区、联网、读取系统 current/latest、使用未固定全局随机状态或直接写文件。禁止 I/O 是合同、依赖检查和隔离测试要求，不声称普通 Python 类型系统能自动提供安全沙箱。仅加载受信任、已审查的发布包，拒绝外部不可信模型序列化文件。

可后端下推的 Qlib/向量化计算由调用方 adapter 执行，需与基准算子 golden case 等价。性能优化必须报告主键、NaN、dtype、误差和排名边界影响，不只比较相关系数。预测数值在容差内但 TopK、意图或约束结果翻转，仍属于行为变化，不能用数值容差掩盖；需要固定 tie-break/稳定计算或单独批准语义变更。

## 5. 信号与策略协议

### 5.1 SignalPlan

允许单模型、规则信号、多个模型或已保存 SignalRun 的组合。固定输入 alias、source stage、horizon、键对齐、缺失/过期规则、变换版本与 reference universe。

支持基础算术、rank、zscore、条件组合即可。Research 可用受控 SQL/表达式编译计划；Core 执行已验证表示，不直接 `eval()` 任意字符串，也不读取数据库。

每条 signal 有 `score_semantics`，例如 rank_score、standardized_return_prediction、calibrated_probability。没有校准证据不生成“80% 会涨”的解释字段。

### 5.2 Portfolio/Rebalance/Risk 三类职责

| 插件 | 回答问题 | 例子，仅为可配研究策略 |
|---|---|---|
| PortfolioPolicy | 想持有什么、各占多少 | TopK 等权、持仓漂移、现金缓冲、行业/个股上限 |
| RebalancePolicy | 今天是否调整、何时保持不动 | 周频/20 session、分相位、持仓保留/替换规则 |
| RiskPolicy | 投资组合允许哪些风险 | 集中度/暴露限制、回撤门控、持仓退出 |

第一版提供一个简单 TopK 等权与明确的 rebalance policy 作为贯通基线；Top5、60/180 混合、止损/止盈等是 StrategyRelease 参数/插件，不是 Core 全局规则。

用户的“最多五只、允许空仓、错了快撤/对了多拿”作为研究意图，由策略与实验评价决定具体实现。Core 不把它变成未经验证的收益承诺。

## 6. 订单规划与边界案例

规划以**真实 AccountState**为基础，包含现金总额/可用/冻结、总持仓/可卖/冻结、未完成订单及其状态。不能把目标或前次 OrderIntent 当作真实持仓。

建议固定次序：

```text
validate inputs/context
→ candidate/held-symbol coverage check
→ portfolio proposed target
→ rebalance decision
→ strategy risk constraints
→ account-aware delta including outstanding orders
→ lot/tick/available inventory/estimated cash checks
→ deterministic priority/rounding
→ OrderIntent + revised target + trace
```

仍有效的未成交买单/卖单要计入投影暴露和现金占用；状态不明的外部订单不能当零。是否撤旧单/修改目标由策略与运行协议明确，不能在重跑时重复发同一需求。

同一账户批次内由 Trade 串行或采用 account version compare-and-apply；Core 输出附 expected account version。状态变了应明确重新决策，不把基于旧现金的两个计划直接都提交。

### 6.1 不可混淆的情况

| 情景 | 行为要求 |
|---|---|
| 当前无 signal | invalid/no decision 或固定保守策略；不能默认 score=0=卖出 |
| 股票退出选股池但还持有 | 保持账户跟踪；依冻结的 exit/hold policy 处理 |
| 目标为零但无法卖出 | 记录未完成退出与原因，不能假装清仓 |
| 有未完成委托 | 计入冻结与投影数量，去重/撤改规则明确 |
| 无可用成交价格 | 可返回无可执行意图；不得用未来 close 补开盘价 |
| 规则/数量不适用 | 显式 invalid 或拒绝，不套统一 100 股/10% 规则 |
| 预算不足 | 固定优先级和舍入规则；现金缓冲保留，可少买/空仓 |
| 相同 score | 稳定 tie-break，例如 secondary metric 后 security_id |

Core 使用 Data 提供的有效规则和 Trade 提供的账户约束。交易所/券商规则资料需实施时核对；这里不硬编码跨板块和跨时期都相同的规则。

## 7. 状态转换：意图和成交分开

StrategyState 保存上次目标、冷却、已知追踪高点、策略阶段等。生成订单不应增加真实持仓；只有 Trade 处理确认成交后提供新 AccountState。

Core 可接收 Fill/Account notification 来更新持仓 episode 的策略记忆。重复事件处理的水位与持久化归 Trade；Core reducer 仍应对 event_id/sequence 有明确前置条件，不依赖隐含“肯定只会调用一次”。

StrategyState contract 改版时必须提供显式 migration/reset policy；不能用新代码随意解释旧状态。状态切换在批次/发布边界执行并记录，而不是在半个决策过程中加载新插件。

同一逻辑事件回放允许 envelope 中 run_id/墙钟日志不同，但业务目标、意图 logical key、状态与 trace 语义必须一致。用于去重的稳定逻辑键与用于区分实际执行尝试的 attempt_id 是两件事。

## 8. 纯接口与依赖规则

```python
# 初版协议示意；不要求每个接口单独建类。
execute_feature_plan(plan, facts, transform_state, context) -> FeatureResult
predict(model_handle, feature_frame, context) -> PredictionFrame
combine_signals(signal_plan, frames, context) -> SignalFrame
make_decision(strategy, signal_frame, facts, account, state, context) -> DecisionResult
reduce_strategy_state(strategy, state, event, context) -> StateTransition
```

```text
src/axiom_core/
  contracts/         neutral frames, states, context, intent
  operators/         tested general transforms
  execution/         plan/inference/plugin executors
  policies/          interfaces and minimal generic policies
  planning/          account-aware intent compilation
  validation/        schema/time/rule preconditions
  tracing/           typed reasons and decision output
  tests/             fixtures and conformance
  docs/              Core-specific implementation and tests
# Shared design: axiom-data/docs/design/ (one authoritative cross-repo index)
```

不得依赖 `axiom_research` 或 `axiom_trade` 主包；不可 import 券商 SDK、数据库或 scheduler。Data 的 domain schema 仍归 Data；中立 Frame 是 adapter 层映射结果，不强迫 Data 引入整个 Core。

## 9. 错误、解释与可观察性

建议原因码：`MISSING_REQUIRED_FEATURE`、`SCHEMA_MISMATCH`、`FUTURE_INPUT`、`UNKNOWN_RULE`、`STALE_SIGNAL`、`INSUFFICIENT_CASH`、`UNSELLABLE_POSITION`、`PENDING_ORDER_CONFLICT`、`STATE_VERSION_MISMATCH`。

错误返回必须区分：计算无法成立的 hard error、合法无交易、策略风险阻断、输入质量允许的显式降级。禁止 catch-all 后返回空表并宣称成功。

DecisionTrace 至少可解释：候选分数与 rank、入选/排除、原目标与约束后目标、数量变化、舍入、缺数据、风险限制、未完成订单影响。解释是计算路径事实；模型 Feature attribution 可作为独立分析，不能冒充因果原因或替代实际规则 trace。

Trace 由调用方写入 run artifacts，UI 按 P12 读取。只要保存的 refs 与输入仍可恢复，不能因为看图而执行新一轮投资决策。

## 10. 最小实现顺序

| 阶段 | 交付 | 约束 |
|---|---|---|
| C-M1 | Frame/Context/Intent/State schema 与验证、通用 Feature/信号算子 | 先用纯夹具，不接券商或真实数据根 |
| C-M2 | 保存模型推理 + 一个 FeaturePlan + TopK/Rebalance/Risk + intent planner | 跑通一条冻结策略，不迁全部旧策略 |
| C-M3 | Research 批量与 Trade 逐日一致性，状态 reducer 与 trace | 通过相同输入的语义回放 |
| C-M4 | 插件发布 ABI/兼容拒绝、依赖/I/O 门禁 | 接受新 Feature 不要求改 Core |

## 11. 最小验收标准

| ID | 夹具/操作 | 必须结果 |
|---|---|---|
| C01 | 相同显式输入、不同进程运行 | 目标/意图/状态/trace 语义相同；无墙钟或目录依赖 |
| C02 | 改系统时间、latest/model 指针、工作区 | 已加载计划和 handle 的计算不变 |
| C03 | 输入行被打乱、列错序、重复 key | 只按明确 key/schema 安全对齐或拒绝，绝不静默位置拼接 |
| C04 | Research 批量与 Trade 按日执行同 FeaturePlan | 各日 Feature、NaN、rank 和推理输入一致 |
| C05 | 加入 cutoff 后事实或未来标签列 | 因果检查拒绝或不可见，过去决策不变 |
| C06 | fit state 不同但 Feature 名相同 | 输出与 provenance 区分，推理不重新 fit |
| C07 | 当前持仓退出 universe 或缺信号 | 按冻结政策处理并有解释，不丢账户状态 |
| C08 | 部分成交/挂单冻结后再次规划 | 不重复买卖；AccountState 版本冲突可发现 |
| C09 | 最小单位、限价、不可卖、现金不足 | 计划满足声明规则；剩余现金/未完成目标可解释 |
| C10 | 无交易 vs 清仓 vs risk block | 三者输出和 trace 可区分 |
| C11 | 收到同一成交通知/乱序通知 | reducer 与 Trade 去重/水位合同一致，不重复状态作用 |
| C12 | cached 与 model source 提供同一最终 SignalFrame | Core 决策完全相同，无重复组合/标准化 |
| C13 | 新增冻结 Feature/策略插件 | Core 无需修改；发布包不依赖 mutable Research |
| C14 | 插件试图联网/读 latest/写 DB | 静态依赖与隔离 conformance 测试失败；不把普通执行宣称安全沙箱 |
| C15 | 保存 trace 后供 UI 查询 | 图上目标/原因与真实运行一致，UI 不调用决策引擎 |

性能验收单独记录 batch/逐日耗时与内存，不通过删校验、改 dtype 或改变算子来达标。具体预算在固定硬件和代表性数据上确认后冻结。

## 12. 依据与未决项

继承上传的 Axiom 总纲 v0.1 §5–7，以及 signal-centric 文档关于模型与策略解耦、组合表达式和 target-based plan 的设计。本版进一步明确 Feature stage、插件 ABI、AccountState 版本及 pending order 的职责。

尚待专项确认：首个模型 backend、插件打包方式、具体 portfolio 基线、state migration 规则、日级事件时间表和实际交易规则覆盖。不得因这些未决项引入多个 mode-specific 决策实现；缺支持时明确拒绝。
