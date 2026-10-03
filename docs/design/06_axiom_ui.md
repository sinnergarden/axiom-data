# axiom-ui 初版设计：只读证据控制台与图表联动

> 文档编号：AX-UI · 专项初版 v0.1 · 所属设计包 v0.2 · 2026-09-05。\
> 状态：实施参考草案；页面、查询和交互为目标设计，不代表现有 UI 已实现。\
> 上位边界：[总体设计](01_axiom_overview.md)。关联：[Data](02_axiom_data.md)、[Research](05_axiom_research.md)、[Trade](04_axiom_trade.md)、[Core](03_axiom_core.md)。\
> 延续 Evidence Console、Operation First、下钻而不堆信息；新增明确的跨模块 ChartContext/ChartLayer 合同。

2026-09-28 Data 接口实证：真实 DataBatch 已投影为 P12 ChartContext/ChartLayer，保留来源、单位和查询 refs；本次没有部署 BFF/浏览器 UI，也没有虚构成交、账户图层。
见 [真实 Developer 教程](../../notebooks/developer_tutorial.html#section-14) 与 [设计对照](../design-conformance.md)。正文继续定义目标合同。

## 1. 定位与三个用户问题

UI 是个人研究与运营的只读观察窗口，帮助回答：

1. 今天的数据、运行、账户是否可信，哪里有问题？
2. 某只股票的行情、Feature、预测与买卖行为之间是什么关系？
3. 两个研究结果为什么不同，是数据、Feature、模型、策略还是成交造成？

不做修数据、Feature 训练、重跑任务、切模型指针、真实下单或直接改持仓。未来审批/停机等写操作必须另立权限与 owner service 合同，不能在本版暗中加入。

UI 的只读不是“不能记住页面偏好”：主题、筛选条件、图层布局和可重建展示缓存可以写 UI 自己目录，但不能成为业务来源，也不能修改被查看的 artifact。

## 2. 结构与数据来源

```text
Browser
  → UI read-only BFF / Query Service
       → Data 公共 Reader / query projection
       → Research 公共 artifact/query projection
       → Trade 只读 ledger/run query
       → Core release 的 schema/reason code 元数据（非执行服务）
  ← 带具体 refs / committed watermark 的统一展示响应
```

不要求四个物理 repo 各自启动 HTTP 服务。单机可由 UI BFF 调用 owner 的无副作用 Reader 包；后续远程化再包装 HTTP。不得 import 会启动 Broker、创建业务目录、自动构建 Feature 的 owner application。

| 生产方 | UI 消费内容 | 用例 |
|---|---|---|
| Data | snapshot/quality/drift、行情/量额、财务/股东原值、公告/公司行动、PIT membership | 数据质量、K 线、事实与可知时间下钻 |
| Research | FeatureRelease/Build、各 stage、模型/SignalRun、研究评估、发布证据 | Feature/预测叠加、对照、候选复盘 |
| Trade | Plan/Run、DecisionTrace、意图/委托/成交、流水/持仓/净值、标准账户评估 | 运行监控、买卖原因、账户与恢复 |
| Core | 与所选版本匹配的 reason/schema 定义 | 解释 trace，不在 UI 实时调用 Core 生成另一份答案 |

某次 Trade 运行自身生成的 FeatureBuild/SignalRun 从 Trade artifact 空间读，其合同仍是 Research 定义；不能只搜索 Research 文件夹导致“生产没有 Feature”。

## 3. 信息架构与最小页面

| 页面 | 第一屏内容 | 下钻 |
|---|---|---|
| Overview | 数据最新与可信范围、失败/阻断、部署版本、对账差异 | 对应 Data/Run/Account 原因 |
| Data Explorer | Snapshot 内的域段、实际覆盖、PIT 依据、缺口/漂移 | 字段原值、revision、已有 source evidence、图表 |
| Research | 实验/假设、signal 评估、统一回测、对照 | Feature/Model/Dataset、失败试验、phase/分期 |
| Releases | 策略/模型/Feature 依赖与证据、候选和部署状态 | 发布包与兼容/质量限制；只读 |
| Runs | run/attempt、Plan、阶段状态、输入/输出、错误 | 事件序列、恢复点、准确版本 |
| Portfolio | account 现金/冻结/应收、持仓/可卖、订单成交、净值 | 流水、费用、公司行动、对账与 snapshot |
| Chart Explorer / Signal Debugger | K 线 + Feature/信号 + 决策/成交 | 点值、阶段、时间与来源、横截面 rank |

初版可把 Releases 合并进 Research、Data Explorer 合并 Data Quality；页面数量不是架构边界。重点是关键查询可达，不是做满菜单。

## 4. 使用场景与上下文

### U-UC01：日常检查

进入 Overview，先看今天哪些阶段成功/失败，数据日期和可信范围是否一致，账户是否有 UNKNOWN 订单或 reconciliation 差异，再看收益。不能用一个绿色“正常”覆盖某域失败。

### U-UC02：浏览一只股票

选择固定 snapshot、symbol、日期区间、价格口径，展示 K 线/量额、公告/公司行动/入出池事件。可以随后选择 FeatureBuild/SignalRun 叠加。默认不会因点击“看 Feature”开始训练或补数据。

### U-UC03：还原一次真实运行

从 Run/Backtest 进入 Chart Explorer，自动绑定这次运行实际消费的 View、Feature/模型、Signal、策略、账户与事件。点击决策日看到当时模型输入，点击成交日看到实际/模拟成交；两者不能被一个“买入点”混淆。

### U-UC04：解释未买、未卖与错过

查看候选分数/排名、目标、风险/数量约束、现金与可卖数量、挂单、意图与实际成交差异；缺少 trace 就显示未保存，不让 UI/LLM 编造“模型看好所以买了”。

### U-UC05：比较两个版本

选 A/B run 或 FeatureBuild，显示数据/Feature/Label/Dataset/model/policy/execution/benchmark 变化清单，再展示数值和结果差异。多个轴同时变化时标为整体对照，不能显示成单因素因果结论。

### U-UC06：检查数据反例

接入财务/股东域后，点击点位查看原值、canonical 单位、报告期、revision、source available、first observed、policy 与实际范围检查。未保存证据明确缺失；verified/observed 表示依据，不做等级晋升条，公开时间不能替代系统观察时间。原始值与变换值应能清楚区分。

## 5. ChartContext：先固定所看的世界（P12）

Data 首版采用 [Snapshot + QuerySpec + Reader](02_axiom_data.md#7-一个-reader薄的消费者映射)。图层的 view_ref 可以内嵌该定义，不要求每次浏览发布独立 View 或 Derived artifact。后端只做薄的传输/展示组合，源数据元信息来自 Reader；尚未接入财务、Qlib 或证据的页面明确未提供，不驱动 Data 预建全域。

Data 的 `records + field_meta + context` 是 P02/P03 的事实响应，只提供 P12 的 Data 图层输入，不是整个图表响应。BFF 将它与 Research/Trade 的既有产物投影组成 ChartContext/ChartLayer；保留各自实际 refs、stage 和时间含义，账户投影另带 committed watermark。单一 Data Snapshot 不拥有 Feature、Signal、DecisionTrace 或账户值。协议 owner 仍按本节及总纲 P12，不能在 Data Reader 中另建一份全系统 UI schema。

### 5.1 两种模式

- `browse`：选择 snapshot 和 artifacts。允许选择“当前”，但服务端在本次查询开始解析一次并返回具体 refs；刷新是另一次查询。
- `run_replay`：绑定 run/attempt 的实际输入/输出。不能把 current 替代 run 中的旧数据，也不把最新模型解释为当时模型。

长区间 run 可跨多个 batch/model/snapshot，ChartContext 保存明确的 session→refs 映射，不强行用最后一个 snapshot 代表整个历史。live 原始输入若仅在 Trade 的 feed log 中存在，应显示该批记录来源，不能伪称来自后来生成的 Data snapshot。

### 5.2 最小请求

```yaml
context_mode: run_replay
run_ref: run_example
attempt_ref: attempt_example
account_ref: account_example
security_id: security_example
interval: {start_session: "2025-01-02", end_session: "2026-07-31"}
price_basis: unadjusted
adjustment_anchor: null
time_axis: decision_available
layers:
  - {role: candles, view_ref: market_view_example}
  - {role: feature, build_ref: fb_example, field: quality_x, stage: base}
  - {role: feature, build_ref: fb_model_example, field: quality_x, stage: model_input}
  - {role: signal, signal_ref: sig_example, stage: final}
  - {role: decision, run_ref: run_example}
  - {role: fill, run_ref: run_example}
comparison_mode: false
```

所有 alias 解析后生成 `chart_context_digest`，返回实际 refs、scope、calendar、价格与时间含义、质量状态和 query version。前端后续点查携带同一 context，避免图更新后侧栏仍展示旧版本。

## 6. ChartLayer：每层都可解释

### 6.1 返回结构

```text
layer_id / role / name
source_ref(s) / source_digest(s)
security_id / keys / points_or_events
field / unit / value_semantics
stage: raw_fact | canonical | base | cross_sectional | model_input | prediction | signal | ...
time_semantics / effective_time / source_available_time / observed_time
price_basis / adjustment_anchor / display_mapping_ref
validity / missing_reason / freshness / quality_state
is_reconstructed / reconstruction_ref
aggregation_method / original_resolution
```

`stage` 的枚举按 owner 合同映射；不能把 model_input 标准化数值与原始 ROE 都叫同一个“Feature 值”。

### 6.2 图层与来源

| 图层 | 谁提供 | 展示约束 |
|---|---|---|
| 未复权 K 线、成交量/额 | Data Market/Fact View | 单位和缺价原因固定；不要把停牌画成虚构交易 |
| 截止指定锚点的复权图 | Data Derived/View | 明确锚点/版本，只用于所声明的显示与研究语义 |
| 财报/股东/公告/行业/入出池 | Data events/FactSeries | 报告期、公开时点、首次观察不同；事件线不误前移 |
| Feature 原值/截面值 | Research 或 Trade 保存的 FeatureBuild | 读取已存在产物，不用当前数据重算 |
| 模型输入/预测/最终信号 | 对应 Model/Feature/Signal artifact | 显示 fold/fit state/score semantics |
| Target/OrderIntent | Trade DecisionTrace | 是意图，不是成交 |
| 委托/成交/持仓区间 | Trade ledger/run projection | 区分 real/simulated、状态、成交价量及费用 |
| 净值/回撤/恢复期 | Trade 标准评估 | 引用 metric spec，不在前端另算版本 |

### 6.3 时间与价格对齐

Feature 默认在**可用于决策的时点**显示；按报告期展示要有显著“经济归属期，不代表当时可知”标记。模型预测 horizon 与显示区间分别标识，不把未来标签当实时信号。

订单/成交保留未复权原始价格；若叠加在调整后价格图上，由 Data 的固定映射转成 display coordinate，并显示原成交价与展示坐标。映射只影响图轴，不能改金额/数量或计算账户收益。

跨版本或不同基准的叠加必须显式进入 comparison mode，显示 A/B refs 和变动轴；不做静默自动对齐。按日期能 join 不等于可比较。

### 6.4 缺失、重建和只读

缺 Feature/trace 返回 `ARTIFACT_NOT_FOUND` 或 `LAYER_UNAVAILABLE` 与原因。只读查询不能隐式 build、train、fetch 或 replay 决策。另行授权的重建任务完成后可读取，标记 reconstructed、固定输入和 reconstruction ref，不冒充当时实际保存值。

第一版不要求实现“点击后自动重建”。读取已登记的 artifact 或无写入的安全投影足够。

## 7. 查询 API 与一致性

初版优先 Python 公共 Reader + UI BFF，可选 HTTP：

```text
GET  /overview
GET  /data/snapshots/{id}/quality
GET  /data/series?view_ref=...&security_id=...
GET  /research/runs/{id}
GET  /research/features/{build_id}/series?stage=...
GET  /trade/runs/{id}
GET  /accounts/{account_id}/summary?as_of=...&sequence=...
GET  /accounts/{account_id}/events?cursor=...
POST /charts/query               仅复杂只读查询，不创建业务任务
GET  /charts/{context_digest}/point?security_id=...&session=...
```

POST 的 HTTP 动词不代表业务写；服务必须明确它只计算查询投影/可重建展示缓存，不修改任何 owner artifact。路径只是建议，CLI/API 实际拼写由实现确认。

所有结果含 contract version、实际 refs、查询上下文（Data 返回字段为 context）、数据生成时间、account committed sequence（适用时）、warnings/unknowns。分页使用稳定排序与游标，不按目录 mtime 猜最近正式产物。

响应 generated_at 与来源数据的 session/observed/coverage 时间分开，不能把刚执行的查询显示成数据刚更新。P12 组合版本与嵌入的 DataBatch/Research/Trade 合同版本各自保留；未知 stage 或不兼容合同明确报告，不能靠位置或猜测解析。

Portfolio 同屏的现金、持仓、冻结和净值使用同一只读事务/水位；若行情估值时点不同，单独展示，不伪装成同一时刻。统计数字来自 owner 结果，UI 只做格式化/筛选。

### 7.1 错误合同

`UNRESOLVED_REFERENCE`、`CONTEXT_MISMATCH`、`LAYER_UNAVAILABLE`、`INSUFFICIENT_SCOPE`、`QUALITY_BLOCKED`、`UNKNOWN_STAGE`、`READ_LIMIT_EXCEEDED`。不能把无法读取返回成 0 收益或空仓。

只读服务异常不回滚成交，不使数据发布失效。Owner 不可用时展示明确缓存截止点，不把昨天成功页面冒充今天已运行。

## 8. 最小交互与视觉规范

保留深色优先、高信息密度、弱边框、状态 badge、图表/表格与下钻，少动画。红绿市场涨跌配色与系统健康色含义分开，避免用户把数据错误当股票下跌。

```text
顶部：上下文 browse/run、snapshot/run/strategy、日期、symbol、价格口径
中部：K 线与事件；Feature/score 使用独立 y 轴/子图，同步十字线
右栏：点值、stage、模型/版本、cutoff、PIT/缺失、source evidence
底部：同日候选排名、Target/Intent/Order/Fill、账户影响与原因
```

默认不把不同单位的十条曲线挤在一根轴上；用户可开关图层、锁定上下文、导出当前查询数据及其 refs。切证券/日期不会改变已锁定 run 的版本。

原 UI 规格列过 React/TypeScript、表格/查询和图表组件候选；本版不要求同时安装全部库。优先复用现有可靠依赖，至少支持 K 线、事件点、同步坐标与表格。具体技术版本另行核对，不以换框架作为验收。

## 9. 安全与性能

BFF 对数据根只读、账户查询使用只读连接；不提供任意 SQL、文件路径、命令或 import 接口。使用 allowlist 字段/Ref 解析、路径边界校验、分页和查询范围限制。错误与日志脱敏，不展示 token、券商凭证、完整环境变量。

本机个人服务默认仅绑定可信本地访问；需要远程访问时添加鉴权和明确网络边界。只读交易信息仍有隐私风险，不因无下单能力就裸露公网。

UI 可缓存 query-context-bound 结果，不能用 security+date 两个字段缓存所有版本。使用固定 refs+stage+scope+price/time policy+account watermark+query version 作为缓存键。

大图可做**显示降采样**，必须声明聚合方法并支持点查原值。OHLC 保留 open/高/低/close 语义，量额按规则汇总；Feature 和 signal 不默认平均成新的业务值。降采样不得回写训练/回测数据。

性能验收先固定“一股数年、多个 Feature 图层、一个运行的订单/成交”“数百 runs 筛选”等代表性规模，测冷/热查询延迟、payload 与内存，再冻结预算。不能以省时为由触发后台训练或省略版本核对。

## 10. 最小实施顺序

以下是 UI 产品自身的阶段，不是个人 Data 市场闭环的前置要求；尚未接入的领域不要求 Data 提前建设。

| 阶段 | 交付 | 验收重点 |
|---|---|---|
| U-M1 | BFF/Reader、Overview、Runs、Account 只读投影 | 版本/状态/账户主键正确，不依赖业务写入 |
| U-M2 | ChartContext + K 线/事件 + Feature/Signal 基础叠加 | run 锁定、stage/单位/时间清晰 |
| U-M3 | Target/Order/Fill/trace、同日候选、数据 provenance 下钻 | 真正看懂为什么买/没买/没成交 |
| U-M4 | A/B 对照、质量/phase/标准回测报告、缓存/分页 | 不混口径，不重新实现指标 |

## 11. 最小验收标准

| ID | 情景 | 必须结果 |
|---|---|---|
| U01 | Owner 数据目录只读、无生产写权限 | 浏览所有首版页面，业务数据/指针/账本不变 |
| U02 | 选择 run A 后默认已变为 B | 图、点查、侧栏始终使用 A；只在显式刷新/切换后改变 |
| U03 | K 线+base Feature+model_input+Signal 叠加 | 值与各 build/run 原值逐键一致，stage/单位可辨 |
| U04 | 长 run 跨 snapshot/model/fold | 各 session 使用实际 refs 映射，不全用最后一个模型/数据 |
| U05 | 公告晚于报告期、revision 晚到 | 默认按可知时间展示；报告期轴明确非 PIT 视图 |
| U06 | 复权图上显示未复权成交 | 原成交价保留、显示映射可追溯、不修改 ledger |
| U07 | 有意图但拒单/未成交/部分成交 | 图标、状态和原因有区别，不伪造成交点 |
| U08 | 缺 Feature/trace 或未保存模型输入 | 返回 unavailable/reconstruction 提示，不自动构建/伪造 |
| U09 | 混入另 snapshot/release 的层 | 默认拒绝或显式 comparison，A/B 身份可见 |
| U10 | 账户在查询中发生新成交 | 同屏 committed watermark 一致，或明确标识分时点 |
| U11 | Data 新鲜但证据不足、run 部分失败 | 首屏显示限制/阻断，而非一个绿色完成 |
| U12 | 某次失败/负实验、phase 最差结果 | 可被查询到，不只展示被选中的赢家 |
| U13 | UI 展示 CAGR/Calmar/Recovery 等 | 等于 Trade 的 EvaluationReport；benchmark/spec/未恢复标记完整 |
| U14 | 非法 ref、路径、SQL/命令、敏感日志 | 拒绝/脱敏，无越权读取或执行 |
| U15 | 图表降采样与点查 | 显示规则有标识；点查恢复原始精度和值，不回写业务 |
| U16 | Owner 暂时不可用、读取旧缓存 | 显示缓存截止点/失败，不冒充最新成功 |
| U17 | 查看 UI 后比对业务文件/服务调用 | 无 Data collect/build、Research train、Trade send/promote 调用 |

验收需至少一个包含公司行动、缺价、Feature/信号、未成交和实际成交的跨仓 fixture；纯 mock 页面不能证明上下文和 lineage 正确。

## 12. 来源与待确认

继承 `qsys_ui_prd_v0_1.md` §1–6 的 Evidence Console、运行/策略/信号/账本/数据质量页面，以及总纲 v0.1 §8；本对话新增 Data→UI 正式消费者、价格/Feature 叠加、5×5 交互与 run 还原要求。旧 UI 文档中的 stage transition/rerun 写接口不纳入当前只读版。

本版细化了 ChartContext、stage、长 run 多 refs、坐标映射、缺失/重建标志和 committed watermark。仍待确认现有前端依赖、真实 owner 查询接口、数据规模、远程访问需求与性能预算；缺少某层应推动 owner 补公共读取合同，而不是 UI 私自拼 sidecar。
