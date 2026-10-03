# Axiom 量化系统总体设计 v0.2

> 文档编号：AX-OVERVIEW · 设计包 v0.2 · 日期：2026-09-05。
> 状态：待评审的实施总纲，供专项设计和分阶段实现使用；不代表现有代码已经满足本文要求。\
> 适用范围：个人 A 股日级投研、回测、shadow 与真实交易。\
> 核心结构：四个物理仓库，按[补丁 A](07_重要补丁_A.md)将 Core 与 Runtime 合并于 `axiom-engine`；逻辑职责分离，只有一套正式回测。\
> Data 个人版同步：2026-10-03；版本/查询引用可内嵌，本轮完整交付范围见专项 02。生产来源统一为 Tushare；外部 cross-check 仅 warning，不改写数据或阻断发布。\
> 阅读约定：`Ref` 是指向确定版本的引用；`View` 是按用途提供的数据读取合同；`Release` 是冻结的定义与实现；`Run` 是一次实际执行。

本文把历史讨论中的共识整理为目标架构。标为“本版细化”的内容是为打通实现补充的建议默认，需要在相关专项设计中确认；字段和接口展示最小语义，不要求逐字照搬为数据库表。总体设计不等于采集、迁移、运行或实盘授权。

## 0. 六份文档如何使用

本包是一份总纲和五份初版专项设计，不是六套平行架构。先读本文的边界、5×5 交互表和系统验收，再由专项对话确认字段与实现。Worker 只执行已明确授权的阶段，本文不授权直接迁移或真实交易。

| 文档 | 回答的问题 | 权威范围 |
|---|---|---|
| [01 总纲](01_axiom_overview.md) | 为什么拆、谁负责、交换什么、如何集成和演进 | 跨仓边界、协议索引、系统级验收 |
| [02 Data](02_axiom_data.md) | 事实怎样保存、修订、PIT、读取和恢复 | Source/Domain/Snapshot/View/Quality 合同 |
| [03 Core](03_axiom_core.md) | 同样输入如何产生同样的决策 | 计算协议、插件 ABI、决策与策略状态 |
| [04 Trade](04_axiom_trade.md) | 一套回测如何走向 shadow/real，钱和订单如何可靠记录 | Runtime/Broker/Accounting/Ledger/账户评估 |
| [05 Research](05_axiom_research.md) | 如何提升信号和策略、控制试验、复用产物和发布 | Feature/Label/Dataset/Model/Signal/研究证据 |
| [06 UI](06_axiom_ui.md) | 怎样看懂事实、Feature、决策、账户和异常 | 只读组合查询、图表联动、交互与展示验收 |

文档切分不按页面或每个类单拆。Runtime、统一回测、模拟成交和事务账本都留在 Trade；Feature 与模型专项思考留在 Research；跨仓设计统一存放于 `axiom-data/docs/design/`，不增加独立治理 repo；物理存放位置不改变各仓的逻辑职责。

**冲突处理：**模块细节由 owner 文档决定；涉及边界和数据协议的变更，先形成小型变更提案，同步总纲、受影响专项与契约测试，再执行。总纲本身可以被反例推翻，不是让 agent 机械服从的不可修改宪法。

**Notebook 与正式设计：**[For Quant Researcher](../../notebooks/researcher_tutorial.html) 解释研究使用过程，[For Quant Dev](../../notebooks/developer_tutorial.html) 展开存储、接口与实现审核；两者是本设计包的教学展开，不是另外两份协议 authority。总纲与已同步补丁决定四仓/五逻辑域边界，各专项决定本域合同；Notebook 的产品接口提案须与这些合同同步。`.ipynb` 是教学内容源，HTML 是生成的阅读版；`demo_*` 及 synthetic 输出不定义生产 ABI，也不证明实现完成。修订一个跨仓合同要同时更新 owner、协议索引与受影响示例，不采用“最后编辑的文件自动覆盖其他设计”的规则。

**来源与新增约定：**五个逻辑领域、四个物理仓库、单回测、PIT、信号复用、统一评估、只读 UI 和持续演进来自既有讨论。各专项的表结构、接口示意、错误码和验收夹具是“本版实现约定”，不冒充代码现状。供应商历史能力、实际券商行为、准确费率与硬件耗时仍须实施取证，不默认已有结论。资料及与 Qsys 的差异统一见最后一节。

## 1. 系统目标与基本原则

Axiom 同时承担研究平台、投资辅助和自动交易系统三个角色。推进顺序是先建立可信研究闭环，再完善监控和 shadow，最后进入受控实盘；自动研究不是第一阶段前提。

系统要让人能够回答：这次结果用了什么数据、当时为何可见、执行了什么策略、为什么下单、实际成交了什么，以及如何再次验证。研究中的收益与回撤目标属于实验验收配置，不是架构承诺。

必须遵守六项原则：

1. **事实、假设、执行分开。** Data 管事实，Research 管假设和研究证据，Core 管共享计算语义，Trade 管运行、成交与账户，UI 管展示。
2. **一个概念一个权威定义。** 可以有多个物化格式，不能有多个互不一致的 universe、Feature 公式、label 或账户解释。
3. **数据正确、可复现、预测有效、可交易分别验证。** 哈希、测试通过、IC 高或回测收益好，不能互相代替。
4. **固定边界，不固定研究答案。** 字段、时间、版本、验证和交换协议要严格；Feature、模型、信号组合与持仓策略允许变化。
5. **正式产物不可原地修改。** 修改事实、公式、模型或规则均产生新版本；一次执行固定实际依赖，不再自行寻找“最新”。
6. **按独立版本轴拆仓，不按类的数量拆仓。** 优先单机、文件 artifact、Python 公共 API 和少量 CLI，不建设微服务、消息队列或通用工作流平台。

## 2. 四个物理仓库与五个逻辑领域

| 仓库/逻辑模块 | 主要负责 | 主要交付 | 不负责 |
|---|---|---|---|
| `axiom-data` | 采集、标准事实、PIT、稳定派生、snapshot、事实视图、数据验证 | `DataSnapshotRef`、`FactViewRef`、`MarketReplayViewRef`、`QlibViewRef`、`UniverseRef` | 预测 Feature 选择、label 定义、训练、策略、成交、Feature 有效性裁决 |
| `axiom-engine/core` | 通用 Feature 算子、推理协议、信号变换、组合/风险/订单规划协议、策略状态转换 | 版本化执行库、输入输出类型、确定性决策结果 | 采集、找最新版本、具体研究配方、训练编排、撮合、数据库、调度 |
| `axiom-research` | Feature/label/模型/策略研究、训练、信号评估、实验比较、研究发布 | `FeatureRelease`、`FeatureBuild`、`TrainingDataset`、`ModelRelease`、`SignalRun`、`StrategyRelease` | 另写回测引擎、券商接口、真实账户账本、修改 Data 内部文件 |
| `axiom-engine/runtime` | runtime、统一回测、shadow/real、Broker、账户核算、ledger、恢复和对账 | `RunManifest`、`BacktestRun`、订单/成交/持仓事件、账户快照、部署记录 | 另写 Feature/预测/选股规则、改变上游事实、自动证明 alpha |
| `axiom-ui` | 研究、数据、运行、订单与账户的只读总看板 | 页面、查询接口、可重建展示索引 | 修数据、改持仓、下单、隐式发布策略、重新定义指标 |

```text
供应商 → axiom-data → 固定的事实视图 ─────────────┐
                      ↓                         │
               axiom-research                   │
               Feature / Label / 训练           │
                      ↓                         │
             SignalRun / StrategyRelease        │
                      ↓                         ↓
                 axiom-engine / runtime
                 解析版本、准备输入、驱动事件
                      ↓                 ↑
                  Engine/Core           │
                  共享决策语义           │
                      ↓                 │
                  OrderIntent           │
                      ↓                 │
           SimBroker / RealBroker → 成交与账户状态
                      ↓
             BacktestRun / Trade ledger

axiom-ui 只读各模块公开的产物与查询接口。
```

Core 是库，不是必须独立启动的服务。Research 与 Trade 都可以调用它。`runtime`、模拟 Broker、真实 Broker、账户核算和部署代码放在 Trade 内，与 Core 同属 axiom-engine，不另建 Runtime 仓库。

独立仓库不要求每次都经网络通信。Research 调用 Trade 的回测公共 API，可以在同一进程执行；券商依赖必须按需加载，离线回测不需要实盘配置或登录。

## 3. 共同语言：版本、产物与数据合同

### 3.1 主要对象

下表是逻辑边界，不要求为每行建设独立服务或登记系统。属于同一发布包的对象可共享一个 manifest，通过子对象引用区分。

| 对象 | 表达什么 | 权威 owner |
|---|---|---|
| `DataSnapshot` | 固定事实状态；个人版 manifest 内嵌各域完整分区与来源，不包含模型或 Feature | Data |
| `Derived / 可选 DerivedCommit` | 固定 Snapshot 上的稳定变换；首版为函数/缓存，重复昂贵共享后再独立发布 | Data |
| `FactView / MarketReplayView / QlibView` | 同一事实状态的用途型读取视图 | Data |
| `UniverseRef` | 明确版本的 membership、有效区间、可知时间和查询范围 | Data |
| `FeatureRelease` | Feature 定义、实现、顺序、窗口、缺失处理和变换合同 | Research |
| `FeatureBuild` | 某 FeatureRelease 在具体数据、范围和 cutoff 上的构建结果 | 合同归 Research；产物由实际运行方发布 |
| `LabelSpec / LabelBuild` | 预测目标定义，以及固定数据上的标签结果 | Research |
| `DatasetSpec / TrainingDataset` | Feature、label、样本选择、切分和预处理组成的训练输入 | Research |
| `ModelRelease` | 训练好的模型、预处理状态、有序 schema、训练来源与推理实现 | Research |
| `SignalSpec / SignalRun` | 信号定义，以及一段时间内实际产生的分数 | 定义归 Research；结果由实际运行方发布 |
| `StrategyRelease` | 可执行的信号生产计划、模型引用、组合/风险/换仓配置与兼容要求 | Research |
| `ResolvedRunPlan` | 一次执行实际采用的输入版本、代码、配置、模式和时间合同 | 实际运行方；回测/交易由 Trade runtime 负责 |
| `RunManifest / BacktestRun` | 执行事实、输出引用、验证结果与完成状态 | 实际运行方 |
| `AccountState / TradeLedger` | 账户现金、持仓、冻结量、委托、成交及其变化原因 | Trade |

**不要再合成一个包揽事实、训练、回测与账户的万能 Dataset。** 训练需要 label/split；预测不需要 label；回测账户核算需要公司行动和成交事实，不能只拿训练 Feature 矩阵代替。

### 3.2 Ref 与 manifest 的最小要求

一个持久产物 Ref 至少能解析到：对象类型、schema/contract 版本、具体 ID、manifest、内容校验和。manifest 再引用实际数据文件、输入对象和构建代码。Ref 也可定位父 manifest 内的域段或内嵌 QuerySpec，不要求每种概念都有独立文件、摘要与登记流程。逻辑 View 保存查询定义及其实际输入引用即可。

```yaml
artifact_type: feature_build
artifact_id: fb_example
contract_version: feature_build_v1
manifest_uri: artifacts/features/fb_example/manifest.json
content_digest: "sha256:..."
```

URI 是定位方式，不是身份本身。文件移到另一台机器后，只要引用闭包完整，就应仍可解释和重放；记录一个哈希却不保留文件，不算可复现。

大表使用 Parquet；manifest/config 使用 JSON/YAML；Qlib binary 是受控读取格式；SQLite 用于索引或交易事务。基础设施复用 manifest 和构建 provenance，不做逐单元格哈希、逐 Feature 收据或重复打包整个项目。

生产运行生成的 FeatureBuild/SignalRun 写入本次 Trade run 的产物空间，引用 Research 发布合同；不要求 Trade 写 Research 数据库。两者的身份与校验规则相同。

### 3.3 行级语义

跨模块表至少固定证券标识、session/时间、主键、字段单位、排序、空值语义和 schema。Feature 与 label 按键连接，禁止仅靠 `.values` 或行号对齐。

分清 `Prediction`、`Signal`、`Target`、`OrderIntent`、`Fill`：分别是模型输出、投资分数、目标仓位、交易意图、实际成交。排名分数不自动具有概率或预期收益的含义。

## 4. axiom-data：个人维护的事实底座

### 4.1 最小持久结构

面向单人维护的 A 股日频、沪深300历史成员与基准，首版采用 [Data 个人版设计](02_axiom_data.md)：

```text
Raw 原响应 + 每次 fetch 日志
→ 版本化 Canonical Parquet
→ 一个 Snapshot manifest（按域内嵌完整 partition map、合同/来源/代码引用）
→ Reader(snapshot, QuerySpec)
```

DomainCommit是域事实版本的逻辑边界，无需独立发布；域引用用Snapshot + 域名。稳定派生为版本化纯函数/可重建缓存，不先建设DerivedCommit管理。Qlib显式导出与实际消费者薄adapter已纳入当前Data交付；Catalog仍在实际需要时加入。

Raw 保留原字段、原单位与观察记录。Canonical 固定经济键、revision、单位/证券映射、缺失与时间合同；重复观察不后移首次观察，旧版本不覆盖。首批以身份、日历、行情、历史成员和基准形成真实研究闭环；账户回放用到的状态/限价/公司行动在进入该用途前补齐，财务/股东按策略需要接入。

DataSnapshot 只固定事实状态。派生配方和 Feature 各有版本，不因新增计算而修改原 Snapshot。预测 Feature 归 Research，计算执行归 Engine/Core。

### 4.2 View 是查询合同

| View | 核心内容 | 何时实现 |
|---|---|---|
| FactView / ResearchFactView | 行情、所需 PIT 事件/成员、稳定派生、字段时间与缺失 | 第一条真实研究读取路径 |
| MarketReplayView | 不复权行情/pre_close、状态、限价、公司行动及规则 | 接入账户回放时 |
| QlibView | 选定原生数字日频字段、开放日历、稳定编码/成员区间和固定导出文件 | 当前已实现并验收；任务需要Qlib时显式生成 |

逻辑 ViewRef 是 Snapshot + QuerySpec + Reader 版本，可内嵌在实验/run manifest；不要求独立 View 文件或每次查询发布。QuerySpec 固定字段、scope/lookback、PIT/cutoff、价格/复权口径、实际派生配方。统一返回 DataBatch(frame, field_meta, context)，context 含 schema、Snapshot/QuerySpec、实现版本与限制；UI JSON 为 records + field_meta + context。UI/Runtime 采用薄 adapter，不能另造数据语义。

读取不采集、不修复、不悄悄物化。需要 Qlib 时显式导出，检查实际依赖字段；exporter 不联网刷新成员，也不自行定义财务/Feature 语义。

### 4.3 PIT 与范围

区分经济时间、具体 revision 的公开时间、系统首次观察时间与查询 cutoff。operational 回答系统当时实际拥有的信息；market-safe 回答有依据的市场可知信息，证据不足时保守采用观察时间。两者是不同问题，verified 不取代 operational。`verified / observed / best_effort` 表示具体范围的证据依据，不是全库晋升等级；synthetic 另标。

历史终态数据允许明示 best-effort 探索，不冒充历史信息集；公开证据按目标需求补齐，不追求全历史认证。新证据可以改变新 Snapshot 的历史解释，不能修改旧 Snapshot 或伪造更早观察。

读取集合是历史 union 加 lookback；当天选股/横截面集合是当日可知 membership；账户跟踪还包括池外持仓/挂单。闭市、停牌、缺数、未上市、退市分别解释。排除缺数或停牌股票会改变样本，数据可追溯不等于选样无偏；Data 不用万能 tradable 保证成交。

### 4.4 更新与用途检查

```text
固定 parent Snapshot、请求范围、合同/代码/实际配置
→ 保存 Raw/fetch → 转换并检查实际所需范围
→ 写受影响分区，复用其余文件与域来源
→ 原子发布完整 Snapshot → 已启用计划成功后更新 current
```

单写者、简单 checkpoint 和 update/read/inspect/rebuild 公共函数足够。单域修复不重跑无关域；源码由可恢复 commit、依赖锁与实际配置固定，重要发布可另存代码归档。无需通用代码捕获、认证/admission 或证据管理平台。

检查字段/单位/键、请求覆盖、修订与时间反例和实际用途依赖，包括 lookback/label 与持仓范围。日更只检新增及受影响历史。返回可用、有明确限制或不可用于该用途及原因；freshness 单列，可选域缺口不阻断无关用途。备份真实旧输入并做恢复演练，不能只保留摘要。

## 5. Engine/Core：共享计算与决策语义

### 5.1 包含什么

Core 提供通用 Feature 算子和执行协议、模型推理接口、信号变换、组合与风险接口、目标仓位和订单规划，以及策略状态转换。具体 Feature、模型权重和策略配方由 Release 注入。

```text
FeaturePlan + FactBatch + TransformState → FeatureFrame
ModelHandle + FeatureFrame → PredictionFrame
SignalPlan + PredictionFrame/外部固定信号 → SignalFrame
SignalFrame + AccountState + StrategyState + DecisionContext
    → TargetPortfolio + OrderIntent[] + NewStrategyState + DecisionTrace
```

`DecisionContext` 显式提供决策时间、可用数据截止点、有效交易规则、约束和事件序号。Core 不从系统时钟自行读“现在”。

通用排序、归一化、目标权重转数量、限仓检查等可在 Core；“60/180 日如何混合”“何时换仓”“什么 Feature 有效”等属于研究配置或冻结插件，不能变成 Core 中不断增加的策略特例。

### 5.2 不包含什么

Core 不包含 CLI/systemd、供应商采集、数据库连接、模型指针发现、真实下单、模拟撮合、持久化 cache 或训练搜索编排。输入加载与 artifact 写入由外围负责。

Core 不需要知道 backtest/shadow/real。它需要知道的是当时的账户、证券规则和信息边界，这些由 runtime 显式传入。

### 5.3 Feature 实现怎样复用

Feature 的定义和专用代码在 Research 编写，发布为不可变 FeatureRelease。Core 执行该发布包中的纯计算计划，训练和推理调用同一实现与版本。

通用算子可以批量执行，也可通过受控后端下推 Qlib expression；下推是优化，不得改变 PIT、顺序、缺失和字段语义。物理文件加载及 Qlib 初始化在 adapter，不得渗透到模型或策略代码。

生产只加载发布包，不 import 可变 Research workspace。新增自定义 Feature 不要求修改 Core；真正新增通用能力才升级 Core release。

### 5.4 状态归属

策略状态如冷却期、追踪高点、上次目标由 Core 转换；账户状态由 Trade 的统一核算模块提供。Core 可以消费成交事件更新策略状态，但不能因生成 OrderIntent 就假设已成交。

**本版细化：**现金、持仓、冻结数量、费用和公司行动的账户计算统一放在 `axiom-engine/accounting`；数据库持久化与这些计算分开。回测、shadow、real 复用这套核算，不在 Core 或 Research 各做一份。

## 6. axiom-research：研究生产与版本化发布

### 6.1 内部组成与日常闭环

```text
features/       定义、选择、组合、发布与构建缓存
labels/         目标公式、horizon、收益口径、maturity
training/       样本、切分、预处理拟合、训练和滚动窗口
signals/        模型预测、规则信号、表达式组合、SignalRun
evaluation/     信号评估、消融、稳定性与研究证据
experiments/    recipe、尝试记录、对照与报告
releases/       Feature/Model/Strategy 发布包与研究决策
```

```text
提出假设 → 固定数据和研究配置 → Feature/Label → 训练
→ OOS SignalRun → 信号评估 → 调用 Trade 统一回测 → 比较与发布
```

信号评估不是第二套回测。修改仓位、换仓、退出规则时，优先复用 SignalRun，不重复训练；更换数据或 Feature 时则构建对应新版本，不能偷换缓存。

### 6.2 Feature、样本与训练合同

FeatureRelease 固定公式/实现、窗口和 lag、有序列名、缺失和异常值政策、横截面定义、依赖字段及兼容执行版本。FeatureBuild 固定 data/view refs、FeatureRelease、scope、cutoff、实际输入与输出。

默认流水顺序：

```text
读取连续历史并补足 lookback
→ 单证券时序计算
→ 关联当日 PIT membership/industry
→ 按声明的当日参考截面做 rank/zscore/neutralize
→ 形成候选样本
```

已有持仓的管理不能因为它退出选股截面而中断。需要给池外持仓评分时，Feature/策略合同明确其参考截面与缺信号行为，不能静默填零。

LabelSpec 必须明确实际公式、交易 session horizon、起止价格、延迟、基准、公司行动和成熟条件。参数写 60 日而实际执行 5 日必须失败。标签可读取目标区间的未来结果，但该能力只能给 label/evaluation 路径，不能给 Feature 与推理路径。

TrainingDataset 至少绑定 FeatureBuild、LabelBuild、sample keys、universe、训练/验证/OOS 区间、purge/maturity、预处理 fit 范围及状态。标准化的拟合与变换分开；模型输入按列名和顺序校验。

每个正式 FeatureBuild 输出缺失率、常数列、异常值、覆盖和来源检查；这些是构建正确性，不是 alpha 证据。

时间 OOS、模型选择和最终 holdout 分开；记录同族试验而不只保留赢家。滚动训练记录 fold→模型→训练输入→允许预测区间的映射，不能用后来训练出的一个模型覆盖整段历史。

### 6.3 ModelRelease 与 SignalRun

ModelRelease 携带模型字节、推理实现、FeatureRelease、有序输入 schema、拟合后的预处理状态、TrainingDataset 引用、训练 recipe 和环境要求。推理不需要重新加载 label，更不得重新拟合 scaler。

SignalRun 保存实际分数及其来源。必要行语义包括：证券、决策 session、信息 cutoff、模拟/实际可用时点、score、有效性、失效原因、来源模型/fold 或组合节点。

必须区分“今天生成了一段历史 OOS 信号的文件时间”和“这条历史信号在实验中允许何时决策”。后者由训练截止、数据可见性和信号延迟证明，不能靠改文件时间证明。

多信号组合使用明确的 SignalSpec：输入 SignalRun refs、键对齐、score 语义、缺失/过期处理、参考截面和变换版本。原始与组合信号使用同一评估和回测接口，不复制模型或成交代码。

### 6.4 StrategyRelease 的内容

```yaml
strategy_release_id: sr_example
feature_release_ref: fr_example
model_refs: [mr_example]
signal_plan: {definition_ref: signal_plan_example}
portfolio_policy: {definition_ref: portfolio_example}
rebalance_policy: {definition_ref: rebalance_example}
risk_policy: {definition_ref: risk_example}
state_contract_version: strategy_state_v1
data_requirements_ref: requirements_example
compatible_core: core_api_v1
runtime_package_ref: frozen_strategy_package
research_evidence_refs: [signal_eval_example, backtest_eval_example]
```

这是示意。无模型的规则策略不必伪造模型引用；多模型和滚动模型使用明确映射。账户凭证、实盘资金和实际执行费率不写入 StrategyRelease，由运行计划单独绑定。

Research 发布候选策略与证据；进入 shadow/real 是独立部署决定。重训产生新 ModelRelease 和新 StrategyRelease，不能覆盖原模型目录或自动切换真钱账户。

## 7. Engine/Runtime：runtime、统一回测与账户执行

### 7.1 内部组成

```text
runtime/       版本解析、preflight、运行计划、事件循环、模式组装
backtest/      统一历史回测入口与运行产物
adapters/      历史/实时行情、缓存/模型信号、模拟/真实 Broker
accounting/    统一现金、持仓、冻结量、费用、公司行动计算
ledger/        SQLite 事务、幂等、checkpoint、恢复、对账
reporting/     账户收益、回测指标、订单和 episode 统计
cli/           公共入口的薄封装
deploy/        systemd、运行配置和发布锁定
```

### 7.2 Runtime 与 Core 的分工

Runtime 选择运行模式和 adapter，冻结依赖并驱动事件；Core 只接收明确的可用输入、状态和规则。

| 模式 | 时钟/行情 | 信号入口 | 成交来源 | 状态与产物 |
|---|---|---|---|---|
| backtest | 历史时钟 + MarketReplayView | CachedSignalSource 或 ModelSignalSource | SimBroker | 独立运行状态、不可变 BacktestRun |
| shadow | 实际时钟 + 受控行情 feed | 同一发布信号链 | SimBroker | 隔离 shadow ledger、运行日志 |
| real | 实际时钟 + 受控行情 feed | 同一发布信号链 | RealBroker | 真实账户 ledger、Broker 对账 |

两种 SignalSource 的输出必须停在同一语义位置：缓存的已组合信号不能被再次组合或标准化。模式分支在外层 adapter/profile，不散落在策略中。

**只有一套回测。** 日常 Research 传 SignalRun；端到端复验传 StrategyRelease。二者复用同一个 runtime、Core 决策、SimBroker、账户核算和评估实现。

账户相关的信号不能无条件跨回测复用：若某信号依赖实际持仓、历史成交或路径状态，必须在 Core 中随运行计算，或严格绑定对应状态轨迹。

### 7.3 ResolvedRunPlan

一个运行计划至少包含：

```yaml
run_id: run_example
mode: backtest
input_mode: cached_signal
strategy_release_ref: sr_example
signal_run_ref: sig_example
research_fact_view_ref: facts_example
market_replay_view_ref: market_example
universe_ref: universe_example
benchmark_ref: benchmark_example
scope: {start_session: "2021-01-04", end_session: "2026-07-31"}
cutoff_policy_ref: cutoff_example
initial_state_ref: account_seed_example
execution_profile_ref: execution_example
evaluation_spec_ref: evaluation_example
core_release_ref: core_example
runtime_release_ref: trade_example
resolved_code_and_config_ref: provenance_example
```

未使用的字段显式标为不适用，而非用 `NOT_AVAILABLE` 冒充已绑定。模型回放额外绑定模型或固定的 fold/model schedule。

运行流程为：

```text
Request → resolve → compatibility/quality preflight → freeze plan
→ load explicit inputs → run events → persist → evaluate → finalize
```

只有入口能把别名解析为具体 ID；执行中不能再查 current/latest/mtime。正式离线重放禁止网络。real 的行情和 Broker 网络 I/O 只能经已选择的 adapter 进入，不能误把“禁止动态身份发现”写成“实盘禁止所有网络”。

### 7.4 事件、成交与状态

共同事件至少覆盖：

| 事件 | 表达什么 | 主要生产者 |
|---|---|---|
| `MarketEvent` | 带时间与来源的市场输入 | Feed adapter |
| `SignalEvent` | 在当前 cutoff 允许使用的信号 | 缓存或模型信号链 |
| `TargetEvent` | 策略希望达到的仓位 | Core |
| `OrderIntent` | 买卖数量、约束、有效期和原因 | Core |
| `OrderEvent` | 发送、确认、拒单、撤单和状态变化 | Trade/Broker |
| `FillEvent` | 真实或模拟成交事实 | Broker adapter |
| `AccountEvent` | 费用、分红、送转、入出金、结算等变化 | Trade 核算/对账 |
| `PositionSnapshot` | 某时点的现金、持仓和净值 | Trade |

事件带 `run_id/account_id/event_id`、时间、序号、来源和关联 ID；相同时间的事件有固定排序。决策使用账户的实际状态，包括未完成委托、冻结现金、冻结/可卖数量，不把目标仓位当真实仓位。

`OrderIntent` 先持久化，再提交 Broker。外部发送与数据库不能假设属于一个可回滚事务；失败后用稳定客户订单 ID、委托查询和对账确认结果，不能盲目重发。

### 7.5 账户权威与运行安全

Trade ledger 是系统内部唯一账户记录；真实 Broker 是外部成交和账户对账来源。差异形成显式 reconciliation，不直接覆盖旧流水。shadow 与 real 按账户和环境隔离，回测不能写生产数据库。

策略风险约束由 Core 统一计算；Trade 另有执行安全闸，如连接失效、账户不符、委托重复、对账异常、人工停机。闸门可以拒绝发送，但不能悄悄改成另一套选股策略；拒绝原因必须记录。

## 8. axiom-ui：只读证据总看板

UI 首页先展示运行是否可信，再展示收益。主要页面包括：

| 页面 | 内容 | 来源 |
|---|---|---|
| Overview | 数据用途/缺口范围、运行状态、阻断、部署版本、对账异常 | Data/Research/Trade 公共摘要 |
| Data | snapshot、PIT 依据、缺口分类、drift、实际检查范围；原始事实下钻 | Data manifest/Reader；catalog 可选 |
| Chart Explorer | K 线/量额、Feature/预测叠加、公告/公司行动、订单与成交；点击某日还原输入 | Data + Research + Trade 的固定 refs |
| Research | 实验、Feature/模型、信号评估、对照与回测 | Research + BacktestRun |
| Releases | 发布包依赖、研究证据、shadow/real 部署状态 | Research release + Trade deployment |
| Runs | 解析后的输入、时间线、输出、错误与恢复情况 | RunManifest/事件日志 |
| Portfolio | 订单、成交、现金、持仓、净值、风险 | Trade 只读查询 |
| Decision detail | 为什么选中/未买/未卖、缺信号、约束、成交差异 | DecisionTrace + Order/Fill |

UI 可以建立可删除重建的查询索引，但不得扫描 mtime 猜“最新正式结果”，不得自行从 raw 重算业务口径。查询当前部署或最新运行状态是允许的展示行为；不能用展示查询替代一次执行的版本冻结。

第一版不设写入接口。未来若增加批准或停机按钮，应提交有权限、有记录的业务请求，由 owner service 执行，不能直接改 ledger 或指针。


### 8.1 图表联动是正式跨模块用例

用户选证券与日期，查看原始价格及按声明口径复权的 K 线；叠加 Feature、模型分数、持仓区间与买卖点。点击一个点，应看到原始事实、变换后 Feature、实际模型输入、信息 cutoff、来源和质量状态。

支持两个互不混淆的上下文：

- **浏览上下文**：用户选择 snapshot；“当前”仅在打开查询时解析一次，返回具体 ID。
- **运行还原上下文**：选定 ResearchRun/BacktestRun/TradeRun，优先读取当时真实保存的 Feature/Signal/成交，按运行绑定的分阶段 snapshot/model 和账户水位展示，不使用当前文件覆盖。

Data 提供事实和展示所需元数据；Research 提供 FeatureBuild、原始/变换/模型输入阶段和信号；Trade 提供决策、委托、成交、账户与核算；UI 的只读 BFF 组合响应，不重算业务公式。Core 不单开 UI 服务，其 trace/schema 由运行产物携带。

`ChartContext` 至少包含数据 refs、Feature/Signal refs、run/account refs、symbol、区间、时间坐标、复权锚点与交易日历；`ChartLayer` 返回 layer_role、单位、stage、时间语义、缺失原因、source_ref、source_digest、is_reconstructed。跨版本叠加必须显式确认并标识。

Feature 图默认按**可用于决策的时间**对齐；报告期可以做第二轴但不能画成当时已经可知。原始分数与模型输入不能共用一个模糊的“feature”字段。买卖标记区分意图、委托和真实/模拟成交，订单价格与调整后图轴之间的显示映射不得改写账本价格。

查询不得触发采集、训练、修数、Feature 构建或历史回测。没有保存且不能只读还原的层返回“缺产物/需单独重建”；UI 不假造历史。查询缓存属于展示缓存，不成为业务真相。详情见 [UI §5–7](06_axiom_ui.md)。

## 9. 模块之间具体交换什么

### 9.1 五乘五交互矩阵：列为生产者，行为消费者

每格按“用例 → 数据/请求 → 协议编号”阅读。对角线是自身职责。矩阵描述信息生产与消费，不等于 import 关系或网络调用方向；请求也是一种信息，但不授予写入权。没有直接关系的格子保留“无”。

| 消费者 ↓ / 生产者 → | **Data** | **Core** | **Research** | **Trade** | **UI** |
|---|---|---|---|---|---|
| **Data** | **事实采集、raw/canonical、PIT、snapshot、稳定派生、View、质量/恢复；不管 alpha** | 无运行期依赖；共享协议索引不构成 Data 对 Core 执行库的依赖 | 研究事实/新域需求 → `FactQuery / DataRequirements` → **P01/P02**；写操作另行授权 | 回放/持仓/订单证券及 freshness 需求 → `MarketQuery` → **P01/P03** | 浏览行情/公告/审计 → `FactQuery / ChartQuery` → **P01/P12**；只读 |
| **Core** | 无直接存储调用；固定 `FactBatch` 经 Research/Trade adapter 注入 → **P02/P05** | **纯计算、推理 ABI、信号变换、组合/风险/订单规划、策略状态转换；无 I/O 和账本** | 训练/推理复用算子、策略插件 → `FeaturePlan / ModelHandle / SignalPlan / policy package` → **P05/P06** | 每次决策上下文 → `FactBatch / SignalFrame / AccountState / StrategyState / DecisionContext` → **P07** | 无直接业务请求；禁止看图时触发决策 |
| **Research** | 样本/Feature/label/诊断 → `FactView / QlibView / UniverseRef / QualityReport` → **P01/P02/P04** | 批量构建/推理/信号组合 → `FeatureFrame / PredictionFrame / SignalFrame / trace` → **P05/P07** | **Feature/Label/Dataset、训练、OOS 信号与研究评估、发布包和研究索引** | 回测与实盘复盘 → `BacktestRun / EvaluationReport / runtime FeatureBuild / SignalRun / account events` → **P09/P10/P13** | 查研究/叠加 Feature/对照 → `ResearchQuery / ChartQuery` → **P12**；不自动训练 |
| **Trade** | 回放/估值/在线事实 → `MarketReplayView / FactView / rule facts / QualityReport` → **P01/P02/P03** | 投资决策 → `DecisionResult / Target / OrderIntent / NewStrategyState` → **P07/P08** | 统一回测/受控部署 → `BacktestRequest / SignalRun / StrategyRelease / model package` → **P06/P09** | **Runtime、单回测、Broker、账户核算、SQLite ledger、恢复/对账、标准收益评估** | 只读账户/运行/图层请求 → `RunQuery / AccountQuery / ChartQuery` → **P12**；不能发单/切指针 |
| **UI** | K 线与事实下钻 → `FactSeries / events / metadata / quality` → **P02/P03/P12** | 解释类型、原因码与 trace schema → **P07/P08**；随产物/版本包读取，不启动 Core | Feature/预测叠加、研究结果与发布证据 → `FeatureBuild / SignalRun / ResearchEvaluation / release graph` → **P06/P10/P12** | 意图/成交叠加、持仓、净值、流水、运行状态 → `RunManifest / AccountProjection / BacktestEvaluation` → **P08/P10/P11/P12** | **只读 Evidence Console、Chart Explorer、查询组合与可重建缓存；无业务写入权** |

#### 9.1.1 协议索引与 owner

协议号是共同索引，不要求产生同等数量的库。JSON/表 schema 放在 owner repo，总纲只保存导航；避免各文档复制出略有不同的权威版本。

| ID | 协议族 | 权威 owner / 专项细化 | 最低交换语义 |
|---|---|---|---|
| P01 | ArtifactRef / manifest envelope | 各产物 owner 实现，共同 envelope 在本总纲 §3 | 持久对象的 type/ID/contract/manifest/digest；域或逻辑 View 可内嵌父 manifest，保留实际输入与语义 |
| P02 | FactQuery / FactView / DataBatch | [Data](02_axiom_data.md) | snapshot、fields、scope/lookback、PIT/cutoff、membership、单位/缺失/证据；DataBatch 是 Reader 输出 |
| P03 | MarketQuery / MarketReplayView | [Data](02_axiom_data.md)；执行解释由 Trade | 价格/状态/公司行动/规则事实、证券和 session 范围、事件时间 |
| P04 | 显式 QlibView / backend equivalence | [Data](02_axiom_data.md)；当前已接入，按任务选择 | snapshot、配方/原生字段口径、映射、calendar/membership、dtype/tolerance、文件闭包；无网络刷新 |
| P05 | FactBatch / FeaturePlan / FeatureFrame / inference ABI | [Core](03_axiom_core.md) 定义 ABI；Research 发布实现 | adapter 将 P02 DataBatch 映射为中立 FactBatch；行键、列顺序、stage、fit state、所需历史、计算/推理版本 |
| P06 | Feature/Model/Signal/Strategy Release 与 Build | [Research](05_axiom_research.md) | 冻结定义、执行包、数据要求、依赖兼容、研究证据；实际运行方保存输出 |
| P07 | DecisionContext / DecisionResult | [Core](03_axiom_core.md) | 信息 cutoff、账户版本、策略状态、规则、Target/Intent/trace；不含 mode |
| P08 | OrderIntent / Order/Fill/AccountEvent | Core 定义 Intent；[Trade](04_axiom_trade.md) 定义执行/核算事件 | event/source/causation ID、账户、序号、时间、金额/数量、幂等键 |
| P09 | BacktestRequest / BacktestRun | [Trade](04_axiom_trade.md) | signal 或 model 输入、views、初始账户、execution/evaluation refs、结果 |
| P10 | EvaluationSpec / EvaluationReport | [Research](05_axiom_research.md) 信号评估；[Trade](04_axiom_trade.md) 账户评估 | scope、基准、metric 定义、结果表、未检查项、证据；UI 不重算 |
| P11 | ResolvedRunPlan / AccountProjection / Deployment | [Trade](04_axiom_trade.md) | 一次解析的版本、事件边界、mode、安全授权、账户水位、恢复状态 |
| P12 | Query / ChartContext / ChartLayer | [UI](06_axiom_ui.md) 定义组合协议；各 owner 提供只读投影 | 固定 refs、symbol/time、stage、单位/坐标、来源、重建/缺失标记 |
| P13 | ValidationResult / ImpactProposal | 各 owner 的语义验证；总纲 §12 定义演进流程 | 检查范围/规则、已确认反例、状态、依赖影响、验收证据与建议 |

Qlib是按需使用的P04后端格式，已纳入当前Data交付并在两份教程实际执行；普通采集、日更及P02/P03 Reader不依赖它。P02/P03是用途合同，可表示为Snapshot + QuerySpec；P04只显式物化冻结的数字日频查询。

DataBatch 与 FactBatch 不要求采用两份复制的数据，但其 owner 和合同不同：前者属于 Data Reader，后者属于 Core ABI，由 Research/Runtime adapter 映射并保留键、单位、缺失、时间与来源。UI 的 Data JSON 只是 P12 中事实图层的输入；完整 ChartContext/ChartLayer 还组合 Research 产物及 Trade 运行/账户投影，不能把所有图层统称 DataBatch。

Research 请求运行回测是 Research→Trade；BacktestRun 返回是 Trade→Research，两者是不同格子。UI 发查询也是 UI→owner，但结果的语义 authority 仍属于 owner。Core 的业务结果由调用方落盘，UI 不向 Core 提交交易决策请求。

### 9.2 Data daily

```text
Data collector 抓取并保存 raw
→ 在明确 parent 上构建/校验 canonical
→ 发布新 snapshot D2
→ 由后续任务解析 D2，构建所需 View
```

已运行中的 D1 研究不受影响。Data 发布成功但 Feature 构建失败，只阻断下游，不回滚已有效的数据 snapshot。

### 9.3 一次研究与多次回测

```text
D1 + FeatureRelease F1 + LabelSpec L1 + DatasetSpec
→ FeatureBuild / LabelBuild / TrainingDataset
→ 训练 ModelRelease M1
→ 时间 OOS SignalRun S1
→ SignalEvaluationReport
→ S1 + 不同组合/换仓配置 + 固定 MarketReplayView
→ 多个 BacktestRun 与可比较的标准报告
```

多次回测只重新执行发生变化的下游。固定 S1 比较组合规则，不应顺手重训或更新数据。

### 9.4 Shadow / Real

```text
明确接受的 StrategyRelease + 部署配置
→ runtime 解析本次数据、模型、代码、账户和 cutoff
→ Feature / Model / Signal（同一发布实现）
→ Core 决策
→ 发送安全闸
→ SimBroker / RealBroker
→ 成交、账户事务、快照、对账、报告
```

定时重训由 Research 使用固定 recipe 执行，发布新版本；Trade 在明确的运行边界采用它，不在决策过程中自行改模型。

### 9.5 离线复验

恢复 RunManifest 引用的数据、发布包、环境和事件；禁止联网重拉。先复验 Feature/保存模型推理，再复验 Signal→订单→账户轨迹。重训复现是另外一项测试，需要固定训练环境、随机种子和声明的容差。

## 10. 回测真实性与标准评估

### 10.1 一个引擎，两种输入

CachedSignalSource 用于日常策略研究，ModelSignalSource 用于完整推理回放。共享的后半段是：

```text
Signal → Target → Risk → OrderIntent → SimBroker → Accounting → Evaluation
```

发布前用同一小型事件集验证两种输入在相同信号、账户与规则下得到同一决策轨迹。回测和实盘只保证**相同输入下决策一致**，不承诺不同真实成交环境产生相同净值。

### 10.2 最低执行合同

| 项目 | 第一版要求 |
|---|---|
| 决策与成交时序 | 声明使用什么时间的信息、最早何时成交；禁止用未发生的收盘信息按同一收盘价成交 |
| 停牌与缺价 | 不可交易时不能成交；缺价原因明确；估值价格及陈旧状态单独记录 |
| 现金/持仓/结算 | 不超可用现金，不超可卖数量；订单未完成状态与结算规则显式处理 |
| 分红送转等公司行动 | 按事件影响现金、应收、股份和可用状态，登记日/除权日/支付或上市日按合同处理 |
| 涨跌停 | 明确买卖方向、价格限制和保守成交假设；触价不等于能成交 |
| 成交量与成本 | 声明成交量参与上限、费用、滑点与未成交处理；正式评估提供成本/容量敏感性 |
| 完整逐笔撮合 | 非第一版目标；所用日级近似必须可解释并进入 execution profile |

**复权研究价格和真实成交价格分开。** 账户回测使用不复权价格加公司行动核算，不能既用含分红效果的复权收益，又重复把分红计入现金。

Simulator 可以为成交判断读取随后发生的行情区间，但这些内容不能传入当时的 Core 决策。日终成交量用于事后模拟参与上限，与策略在开盘提前知道全天成交量，是两种不同语义。

冲击成本可先用简单、保守的参数与敏感性测试，不要求建设完整订单簿模型；不能把“不做复杂模型”解释成“成本不会影响策略结论”。

### 10.3 信号评估

Research 的统一评估至少包括 IC/Rank IC、ICIR、分位组收益、覆盖、分行业/年份/阶段稳定性、top-k 表现和冗余/消融；排序任务按需要补充 Recall@K、Precision@K、NDCG@K。

排序指标必须指定候选池、horizon、K、相关标的/收益等级定义、并列处理和无正例日政策；它们不是某个训练算法的专属输出，也不能替代成本后回测。

### 10.4 回测与账户评估

统一 `EvaluationSpec` 绑定净值口径、基准、时间范围、费用、年化方法、无风险利率和窗口定义。基准的价格/全收益口径明确绑定；账户入出金不能冒充投资收益，使用与现金流处理一致的收益序列。

| 指标组 | 必须输出 | 口径要求 |
|---|---|---|
| 收益与回撤 | CAGR、MaxDD、Calmar | Calmar 使用同一区间 CAGR/绝对 MaxDD；分母为零时不伪造有限值 |
| 波动效率 | Sharpe | 明确收益频率、年化与无风险利率，不硬编码通用“优秀阈值” |
| 恢复体验 | Recovery time、underwater duration | 区分谷底到恢复、前高到恢复；期末未恢复明确标记并报告已持续时长 |
| 跨周期 | Rolling 1Y/2Y excess | 固定基准及滚动窗口；明确几何相对收益或差值口径 |
| 尾部年份 | Worst-year、worst rolling 1Y | 区分完整日历年与滚动区间，标识不完整首尾年 |
| 可执行性 | 换手、成本、成交率、未成交原因、仓位与容量 | 区分计划与实际成交 |
| 贡献与路径 | 单票/行业/阶段贡献、持仓 episode、capture/giveback、集中度 | 防止收益被少数标的或短阶段主导 |

**本版细化：**信号评估实现归 Research；账户净值、回测与 shadow/real 共用的指标实现归 Trade 的 reporting。Research 引用标准 BacktestEvaluation，不另写一套 CAGR/DD；UI 只展示。共同结果 envelope 统一对象引用、metric spec、scope、数据表和警告。

### 10.5 门禁分开，不设万能绿色勾

正式结果分别报告数据资格、Feature 正确性、训练/标签时序、OOS 有效性、执行可信度、部署准备度。每项包括已检查范围、规则版本、结果、未检查事项和证据。

已完成检查不等于不存在未知问题。关键输入错误必须阻断；可接受近似只能按预先声明的研究政策使用并标记，不能通过“降级”绕过已确认的语义错误。

## 11. 时间、版本冻结、存储与恢复

### 11.1 冻结什么，不冻结什么

代码、配置、snapshot/view/release 引用和评估规则在逻辑运行开始时固定；账户与行情在运行中按事件变化。

长运行服务不能把首次 DataSnapshot 永远锁死。日级系统以一个 session 或一次决策批次建立计划；下一批次可以采用新 snapshot/release，但必须重新 preflight。历史滚动模型也可以变化，前提是计划中已有明确、可验证的时间映射。

对于尚未到来的 live 数据，启动时固定 feed、连接配置和事件合同，之后逐批记录实际收到的消息、来源和时间；不能声称在启动时已哈希未来行情。策略计算仍不自行寻找“最新文件”。

因果不变量包括：加入可用时间晚于 T 的事件，不改变 T 之前的严格 as-of 输出；Feature/Signal 在 cutoff 之后的输入不可见；同一运行中的对象引用不随外部 pointer 改动而变化。

### 11.2 独立版本轴与代码依赖

```text
DataSnapshot        数据事实变化
Derived/View        稳定变换或导出变化
FeatureRelease      特征定义变化
ModelRelease        训练结果变化
StrategyRelease     信号/组合/风险/换仓定义变化
CoreRelease         通用决策实现变化
RuntimeRelease      编排、成交、核算、部署实现变化
```

修改 UI 或 Research 文档，不应重编号数据 snapshot。改变数据也不应覆盖旧 FeatureRelease。

研究工具原生支持受控对照：

| 对照 | 用途 | 仍须固定的条件 |
|---|---|---|
| D1/F1 与 D1/F1 | 检查重建一致性 | 实现、参数、实际输入范围和环境 |
| D1/F1 与 D1/F2 | 判断 Feature 变化的增量价值 | 数据与派生口径、样本/标签/切分、训练 recipe、组合与成本 |
| D1/F1 与 D2/F1 | 判断数据/PIT 变化的影响 | Feature、派生算法、样本协议、训练 recipe 和执行假设 |
| D1/F1 与 D2/F2 | 描述整体升级 | 不能宣称是单一因素造成的变化 |

训练输入改变时，新训练出的模型可以不同，但训练方法和切分应受控；复用保存模型只验证输入变化对该模型的影响，两种结论不能混为一谈。

Repo commit 记录真实代码来源；语义 contract 版本记录解释变化；artifact ID 记录具体产物。三者不能互相代替。个人 Data 构建从可恢复的已提交源码、依赖锁与实际配置执行，不要求通用 dirty 捕获或每次打源码包；重要发布可另存归档。未保存工作区改动不能冒称所记录 commit 的结果。策略生产仍从冻结发布包执行，其部署边界见 Runtime 专项。

**本版细化：**Data 拥有自己的发布 schema/Reader，Core 拥有中立计算接口与事件类型；Research/Trade 的 adapter 完成映射。Core 不反向 import Research 或 Trade，Data 不依赖策略包。Trade 只加载冻结的策略运行包，不依赖整个 Research 工作区。Research 可调用 Trade 的无券商副作用回测 API，避免循环依赖。

### 11.3 存储与写入权

```text
/var/lib/axiom-data/       raw、canonical、derived、snapshot、views、数据索引
/var/lib/axiom-research/   Feature/Label/Model/Signal、实验、发布包、研究索引
/var/lib/axiom-engine/     backtests、runs、shadow/real ledger、部署记录
axiom-ui 自有目录          可重建查询缓存，不是业务事实
```

目录可配置，不能拼接别的 repo 根目录猜位置。Data/Research 的 catalog 可由 manifest 重建；Trade 的 ledger 是事务性记录，不能当普通缓存删除。跨仓默认只读产物，通过 owner 的公共 service 请求改变状态。

### 11.4 失败与恢复

Data 构建失败保留 raw/staging，不切换 current；研究构建或训练失败不发布成功状态、不自动换模型。UI/报告失败不回滚已发生的成交。

Trade 对同一 FillEvent 幂等应用；成交、现金变化、持仓变化和处理水位必须在同一账户事务内一致。恢复先加载 checkpoint、重放未处理事件、查询 Broker 对账，再决定能否继续发单。

备份必须保留 raw/fetch、合同/实际配置、可恢复源码与依赖锁、snapshot/release 引用的真实文件、模型、重要运行与交易账本；Data 源码包可选，不能只记无法恢复的 commit/hash。恢复验收在隔离目录执行，验证删除索引后可重建、断网后可回放；供应商重新拉取产生新观察，不是旧结果复现。

## 12. Harness 与持续演进机制

### 12.1 执行与反思分开，但都需要证据

普通开发按已接受合同执行。重大正确性反例、新领域、反复绕路、入口能力缺失或人工新认知，触发有目的的架构 review。

review 不是只问“代码符合文档吗”，还要问“当前合同是否覆盖真实业务”。先给出问题/威胁模型和参考情景，再对照代码、数据、文档、入口与测试。harness、skill、UC 自身也是被审查对象，不能成为不可质疑的正确前提。

新发现应形成：反例与影响范围 → 合同修订 → 最小回归测试 → 运行前闸门 → 必要文档/ADR 更新。证据不足标未知，不能自动补拉、修数或放松门禁。

### 12.2 轻量跨仓治理

**本版细化：**总体边界及跨仓契约索引统一存放于 `axiom-data/docs/design/`，不增加治理 repo；domain 细节与测试由各 owner 仓库维护。其他仓引用这套文档的明确版本，不复制维护整份总纲。

每个仓库的 `AGENTS.md` 只做简短导航，指向模块合同、UC、公用命令和适用测试。skills 描述何时使用/不使用，不充当第二份业务真相。

合同改变时更新受影响消费者与跨仓集成测试。重要默认变更或破坏性语义变化记 ADR；普通局部修复不要求制造大量文档。审计报告必须区分事实、推断、设计选择和未知项，不能把“范围内检查完成”写成“系统已无问题”。

### 12.3 合法入口必须够用

canonical 指一类操作使用统一 service 和验证，不代表全系统只能有一个脚本。Data build、Research train、Trade run 可以各有入口；CLI、systemd、Notebook 和 Agent 调同一公共 API。

脚本只解析参数、组装依赖、调用入口，不定义 Feature/策略或直接更新账本。检查覆盖子目录、实际 scheduler 与 API，而不只扫描顶层文件名。必要门禁的 `TBD/SKIP/UNKNOWN` 不能被当作通过。

缺少合法入口时，报 framework gap，先设计最小入口能力。执行授权可区分只读取证、临时夹具、正式构建和生产变更，避免一禁到底，也避免审计顺手变修复。

## 13. 实施顺序与系统验收

### 13.1 实施顺序

| 阶段 | 交付 | 阶段门禁 |
|---|---|---|
| A：合同与锚点 | 确定 owner、关键 Ref/Frame/事件协议；小型 golden pack 和一个可恢复真实锚点 | 能解释每个输入、时间与现有证据缺口 |
| B：Data 个人市场闭环 | raw/fetch→canonical→内嵌域段 Snapshot→Reader；一条真实研究路径 | 同Snapshot可读回、单位/时间/范围正确、可恢复；当前完整Data交付另包含财务/事件与Qlib实际消费 |
| C：研究与 Core 接口 | Feature/Label/Dataset/保存模型推理/SignalRun | 样本键、列顺序、maturity、训练/推理一致性通过 |
| D：统一回测 | Trade runtime、缓存/模型信号入口、SimBroker、核算、标准报告 | 同信号同轨迹；停牌/公司行动/限制与失败恢复夹具通过 |
| E：监控与 shadow | 只读 UI、部署锁定、连续运行、对账和停机 | 运行内身份稳定，失败可解释、不重复委托 |
| F：受控 real | Broker adapter、显式部署批准、资金和执行安全约束 | 实盘启用条件、对账、幂等与恢复另行验收 |

这是依赖顺序，不要求已经完成的有效工作重做，也不授权一次性实施全部阶段。先做一条真实策略的纵向闭环，不批量搬迁所有历史实验、不同时更改数据解释与交易算法来追求更好收益。

### 13.2 按实际启用能力演示的验收场景

| ID | 场景 | 可接受结果 |
|---|---|---|
| A01 | D1 上新增数据得到 D2，同时旧研究继续跑 | D1 文件和值不变，运行不读取 D2 |
| A02 | 在同一数据上新增 Feature F2 | F1 与旧模型仍可读；数据 snapshot 不被改号 |
| A03 | 加入晚到 revision 或撤销/缺失反例 | as-of、单位和 revision 语义符合合同，不能倒灌或静默丢事件 |
| A04 | 成员进入、退出、再进入且存在历史 lookback | rolling 连续；当日截面正确；池外存量持仓仍可管理 |
| A05 | 实际启用 Qlib 后，Qlib/direct 对同一查询读取 | 主键/schema/映射一致；数值在声明容差内；无未知 sidecar |
| A06 | 更换 label horizon 或打乱 Feature 行/列 | 实际标签随合同变化；错位、列不符和不成熟样本被拦截 |
| A07 | 同输入重建 Feature、保存模型推理 | 样本、列顺序、标签一致，预测在容差内；不能只比相关性 |
| A08 | 同信号走 cached 与 model replay | 信号接口一致时目标、订单与账户轨迹一致；模型回放不暗中重训 |
| A09 | run 中途修改 model/data/default 指针 | 已开始批次继续使用原计划；下一批次才重新解析 |
| A10 | 停牌、缺价、涨跌停、分红送转、部分成交 | 不虚构成交、不双算分红；cash/position/订单状态可解释 |
| A11 | 同一成交回报重复到达，或发送后进程崩溃 | 不重复记账；先查询/对账，不盲目重发订单 |
| A12 | 单域修复；接入股东域后也可仅补其证据 | 无关域文件与来源不变，走同一公共更新/重建入口 |
| A13 | 数据 freshness 充足但关键验证缺失 | 显式阻断或按既定近似政策标记，不获得伪认证 |
| A14 | 删除 catalog、换目录、断网恢复 | manifest 闭包可恢复；重要运行可重放，交易账本有独立恢复验证 |
| A15 | UI 和部署检查 | 显示实际 code/config/strategy/data 版本、失败与未检查项；无业务写权限 |
| A16 | 注入已知错误但旧 harness 原先会绿 | 新 gate 能拒绝；TBD、范围遗漏或文档过期不会假通过 |
| A17 | 在 UI 打开固定 run，叠加 Feature、预测、意图和成交 | 点击点值与所选 build/run 一致；阶段和可知时间不混，跨版本显式标注 |
| A18 | 改变 UI 代码、Research 文档或 Trade 调度配置 | Data snapshot/Feature 语义身份不被无关重编号；运行部署 provenance 如实记录 |
| A19 | 同一委托分多次成交、费用后到或撤单后迟到成交 | 现金/冻结/持仓与券商事实一致，事件去重但不丢合法迟到事实 |
| A20 | 冻结同一信号比较多个 portfolio/exit 配置 | 训练和推理执行次数为 0；统一回测输出可比，来源与账本独立 |

验收证据采用小型 golden pack、一个真实完整窗口和必要的全范围数据验证；不要求每次修改都重跑多年训练。算法近似、浮点容差和未实现能力事前声明，不在结果出来后调门槛。

验收区分合成语义反例、固定真实窗口和任务实际需求范围检查。个人Data不建设通用admission/certification平台；检查记录可内嵌操作或实验日志。抽样不替代范围完整性，也不为每次UI/文案小改重跑全历史。当前Data完整交付包含财务/事件和Qlib，不能用行情MVP边界延期这些能力；完整回测/UI产品仍由各owner验收。

各专项场景编号为 Dxx/Cxx/Txx/Rxx/Uxx；按当前里程碑实际启用能力提供具体操作和结果，不把全部后续场景作为个人 Data 首版门槛。

各专项交付时回传：实际边界、输入输出合同、版本兼容、验证结果、未决问题、对其他仓库的影响及切换条件。完整字段 schema、CLI 拼写、分区粒度和具体数值阈值在专项设计中确定。

## 14. 与 Qsys 的不同、进化点与资料依据

### 14.1 变化不只是拆仓

| Qsys 已有基础或审计暴露的问题 | Axiom 的继承或改进 |
|---|---|
| 已有信号中心研究、SignalStore、label 与模型产物 | 保留“信号评估→回测评估”和缓存复用；正式接入同一 Trade 回测引擎，不新建 fast backtest |
| Protected Core 将数据、runner、交易等一起保护，后续可能锁住旧假设 | 分为 Data/Core/Research/Trade/UI，各有 owner；保护业务合同，不永久保护旧实现 |
| 可变 Feather、Qlib、sidecar 与模糊 data_version | 不可变事实状态与用途型 View；格式可保留适配，但身份必须绑定真实保存的内容 |
| 多套 Feature registry、YAML、硬编码与模型 bundle 各自解释 | FeatureRelease 统一正式定义；训练与推理使用同一冻结实现 |
| current universe 曾被历史入口误用 | 读取 union、当日 PIT 样本和存量持仓范围明确分开 |
| 不同入口的验收强弱不同 | 相同语义统一 service/preflight；入口可以多个，但不能各说各话 |
| freeze/hash/lineage 容易被误当作数据已正确 | provenance、结构、语义、PIT、研究有效性与执行真实性分别报告范围和证据 |
| label horizon 与公式可能不一致、工作日近似 maturity、位置赋值错位 | Label/Dataset 合同与键对齐成为前置门禁，使用明确交易日历和目标区间 |
| 模型与 signal 指针在同一对象中可 A→B | 逻辑运行一次解析、跨组件传递同一冻结计划；后续更新以新批次/显式计划进入 |
| clean runtime checkout 与 dirty main/config 混合执行 | 各仓代码、发布包、实际配置和依赖独立绑定；不强求一个 checkout，但禁止未知混合 |
| 只有数据/Qlib 基础读取，回测还需自行拼市场条件 | Data 提供 MarketReplayView；Trade 统一处理成交、公司行动和账户核算 |
| 已有 SQLite ledger、candidate 校验和 episode analytics | 继承有效的事务、追踪和分析语义，补充外部订单恢复、环境隔离和统一报告 |
| harness 限制 ad-hoc，但合法的单 domain 操作或只读核验入口不足 | 把能力缺口当框架问题处理；反例沉淀到合同、测试、闸门和必要文档 |

本轮静态与运行核验是上述风险判断的输入，不是对所有历史结果的判决。运行核验中的 9,664 个可比值只证明抽样一致；1,206 个 current 成员回扩与 model A→B 则是已报告的具体反例。本文没有重新检查这些主机文件，也不将报告路径当作本环境中的可访问文件。

早期 `SysQ-Data v2` 把正式 Feature 生命周期放在 Data；更早的口头草案曾把模拟成交和回测统计放入 Core，上传的 Axiom 总纲 v0.1 已收窄这一边界。本包沿用已确认决议并补充专项细节：**预测 Feature/训练与研究发布归 Research；Core 收窄为共享计算和决策；runtime、统一回测、模拟成交与账户核算归 Trade；Data 扩展事实视图但不扩展预测职责。** 这些是目标边界的演进，不是声称旧规格已经如此设计。

### 14.2 资料依据与证据边界

本次在原总纲 v0.1 上保持既有边界与有效内容，重点新增六文档导航、真正的 5×5 矩阵、协议索引、Chart Explorer 以及相关验收。

本总纲继承五个逻辑领域与统一回测，物理仓库按补丁 A 收敛为四个；Data 按个人版 §4 与专项 02 简化为事实底座和用途检查。文中“本版细化”是新增的实现建议，不冒充已有代码事实。

已参考的项目资料：

- `init.md`：项目定位、真实价格与复权价格、信号/策略/执行分层。
- `qsys_phase_1_5_framework_stabilization.md`，§2、§4–6：稳定框架、研究区、策略生命周期和 artifact 合同。
- `qsys_signal_centric_research_architecture.md`，§2–4、§6–8：Feature/Label/Model/Signal/Strategy/Backtest 实体和信号复用。
- `qsys_sqlite_ledger_prd.md`，§3–4、§9、§13：账户主键、账户与策略隔离、流水与快照、交易事务。
- `qsys_ui_prd_v0_1.md`，§1–5：Evidence Console、运行优先、信号和账本下钻。本文采用后续讨论的“第一版只读”边界，不继承其中可能存在的写入交互。
- `sysq_data_warehouse_development_spec_v2.md`，§2–3、§8–17、§20–25、§31–32：双版本轴、不可变事实、PIT、source revision、验证、引用闭包和恢复；其 Feature owner 归属按本文更新。
- 旧总纲还提到 `axiom_data_architecture_and_agent_plan_v0_1.md`，但本轮未提供全文，不把它作为已重新核对的依据。相关决定以本对话已确认内容及可读文件为准。
- 本对话粘贴的《Qsys 当前架构只读审计报告》与《Qsys 三项运行时事实核验报告》：作为用户提供的静态/运行证据。未取得仓库 `docs/ARCHITECTURE.md` 全文，相关现状不超出审计报告的证据范围。

**最终目标：研究可以频繁改变，已发布事实和策略不会被悄悄改变；每个模块交付可理解的合同，每个结果有可验证的输入，每次新发现都能进入下一次运行前的防线。**
