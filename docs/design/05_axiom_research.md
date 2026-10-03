# axiom-research 初版设计：信号研究、性能提升与发布验收

> 文档编号：AX-RESEARCH · 专项初版 v0.1 · 所属设计包 v0.2 · 2026-09-05。\
> 状态：实施参考草案，研究方向是待检验假设，不是已验证收益结论。\
> 上位边界：[总体设计](01_axiom_overview.md)。关联：[Data](02_axiom_data.md)、[Core](03_axiom_core.md)、[Trade](04_axiom_trade.md)、[UI](06_axiom_ui.md)。\
> 来源以 signal-centric、Feature inventory、PIT/LLM 专项及历史讨论为主。历史文档中的“已存在”“可用”“有效”仅代表当时记载，未重新核对的内容不能直接 promotion。

2026-09-28 Data 接口实证：ViewRef 的真实 Query 重放和 Research→Core 薄 adapter 已执行；连续成员、完整 FeatureBuild/模型/OOS/策略回测仍不是本次 Data 教程的验收结论。
见 [真实 Developer 教程](../../notebooks/developer_tutorial.html#section-14) 与 [设计对照](../design-conformance.md)。正文继续定义目标合同。

## 1. 目标与最小闭环

Research 让一个假设能够低成本地变成**固定输入、明确标签、时间 OOS 信号、可复用评估与统一回测**，并说明为什么值得或不值得进入 shadow。

用户希望争取 20–30% 年化、回撤控制在约 20% 至最多 30%，属于研究愿望和策略选择约束，不是软件交付的收益保证。收益目标、持仓 Top5/允许空仓等由 ExperimentSpec/StrategyRelease 配置；架构验收不能靠某条曲线达到目标来代替。

```text
Hypothesis / ExperimentSpec
→ Data requirements + fixed Snapshot/View
→ FeatureRelease / FeatureBuild + LabelSpec / LabelBuild
→ TrainingDataset + split / fit state
→ ModelRelease / OOS SignalRun
→ SignalEvaluation
→ Trade 统一 BacktestRun
→ 对照、失败归因、Evidence、候选 Release
```

Research 经常调用同一个 Trade 回测，不另建 fast backtest。改组合/退出只重跑下游回测；模型/数据变了才重算实际受影响阶段。

## 2. 内部模块与数据归属

```text
src/axiom_research/
  experiments/     假设、范围、尝试记录、对照
  features/        定义、选择、发布、构建、诊断
  labels/          目标公式、maturity、构建
  datasets/        sample keys、split、fit state
  training/        train、rolling、模型接口
  signals/         原始/组合 SignalRun、lineage
  evaluation/      IC/ranking/消融/稳健性/研究解释
  releases/        冻结运行包、证据与候选决策
  cache/           内容与依赖绑定的阶段缓存
  queries/         UI 只读研究/图层接口
  services/ cli/ tests/
```

Data 只提供可信事实与稳定派生；Research 不直接读 raw 或未登记 sidecar 进入正式模型。Trade 可以执行发布的 FeaturePlan 并保存 FeatureBuild/SignalRun；合同归 Research，实际产物归该运行空间，不要求 Trade 写 Research 数据库。

Model/Feature 插件发布为独立、冻结的轻量运行包，生产不 import mutable Research 源码树。Research 可调用 Trade 的离线公共 API，Trade 不依赖 Research 训练主包，避免循环依赖。

当前发布与本地实现分开：GitHub Research/main `2699fbc` 只有 R0 合同/API；本地 Data/Qlib/ViewRef adapter 尚未提交，验收仅证明这份本地工作树。Engine/main `c8a506b` 已有 Core（包括 asof 与逐session cutoff），本地 Runtime 尚未提交。行情＋财务联合输入、FeatureBuild面板保存/读取和跨实验复用仍待 Research 实现；后文为目标职责，不能当作已交付能力。新增路径应复用 Core 执行器，不另写 PIT Reader 或 Feature 执行器。

## 3. 核心产物合同（P01、P05、P06）

| 产物 | 必需内容 | 不能混淆 |
|---|---|---|
| Hypothesis / ExperimentSpec | 假设、经济机制、对照、范围、主次指标、尝试族、预算 | 写了假设不等于有证据 |
| FeatureRelease | 公式/代码、窗口/lag、字段顺序、单位、missing/outlier、参考截面、依赖/ABI | 定义不是物化结果 |
| FeatureBuild | D/V refs、F release、scope/lookback、PIT/cutoff、stage/fit ref、实际输入和输出 | build 成功不等于有 alpha |
| LabelSpec/Build | 公式/horizon、起止时点/价格、基准、收益口径、成熟条件、结果 refs | 标签可以未来，Feature 不可以 |
| TrainingDataset | Feature/label refs、sample keys、过滤原因、split、purge/maturity、fit state | 同 Feature 不等于同样本或 split |
| ModelRelease | 模型文件、推理实现、Feature/schema、scaler、训练 dataset/recipe、环境 | 模型不是策略 |
| SignalRun | 原始/组合 score、keys、stage、model/fold/source、信息/可用时间、validity | 分数不是目标仓位或成交 |
| StrategyRelease | signal/model/feature 计划、portfolio/rebalance/risk、state schema、兼容要求 | 研究发布不等于真钱授权 |
| Evidence | 被比较对象、协议/基准/成本、指标/稳健性、PIT 暴露、全部变体数量、结论 | 结论仅在所绑定上下文成立 |

Data输入依[个人版设计](02_axiom_data.md)固定Snapshot + QuerySpec，不要求独立DomainCommit/DerivedCommit/View发布系统。FeatureRelease固定配方；FeatureBuild才绑定具体数据与查询。稳定派生可为固定配方函数/缓存；Qlib路径已接入，Research的QlibView薄adapter显式激活Data导出，并保存P04引用。Qlib不重做PIT或自动归一化单位；训练processor和Feature语义由Research明确。

调用链是 `Research 编排 → Data Reader/P02 DataBatch → Research adapter → Core/P05 FactBatch + FeaturePlan → FeatureFrame → Research 保存 P06 FeatureBuild`。Research 的 adapter 按键保留单位、时间、缺失与来源，不另写 Feature 执行器或 PIT Reader。Label 读取使用独立的结果范围与查询上下文，可以包含目标窗口未来事实；Research 检查 maturity/训练 cutoff，禁止这些结果流回决策 FactBatch。账户回测经 P09 调 Runtime，账户指标读取 Runtime 的 P10 输出；UI 经 P12 读取已保存研究产物。

历史 best-effort、系统观察时间和有证据的市场公开时间分别回答不同问题，不是资格晋升链。研究应记录所用 policy、范围和限制；数据检查通过不证明样本池或停牌/缺数排除无偏。财务/股东域按真实 Feature 需求接入，不把全域认证当作市场研究前提。

### 3.1 FeatureRelease 示意

```yaml
feature_release_id: fr_example
business_hypothesis: "示例：质量变化可能改善中期排序"
expected_horizons: [60, 180]
input_requirements_ref: req_example
feature_plan_ref: plan_example
runtime_package_ref: pkg_example
ordered_schema_ref: schema_example
missing_policy_ref: missing_example
reference_universe_policy_ref: cs_example
compatible_core_api: core_api_v1
```

同名 Feature 若公式/lag/输入/revision policy 不同，必须有不同定义身份；旧 registry、Qlib expression、模型 features.json 最终指向同一正式定义，不能各自成为 authority。

### 3.2 Feature stage

至少区分 `base`、`cross_sectional`、`model_input`。UI 请求哪一阶段就返回哪一阶段；模型输入绑定 fitted TransformState/ModelRelease/fold。查看原值不等于查看训练输入。

Column 级 provenance 保存一次公式/输入关系；UI 可追到 source refs 和选中样本，不给每个 cell 都建立昂贵独立收据。

## 4. Dataset、Label 与时序

### 4.1 数据准备顺序

```text
读取足够的连续历史（含历史 union/lookback）
→ 单证券时间特征
→ 当日 PIT membership/industry
→ 当日参考截面变换
→ sample filter（原因显式记录）
→ 按 security/session 键对齐 label
→ 分训练/验证/OOS
→ 仅在训练范围 fit 的预处理
```

不能先只保留在池日期，再计算丢失历史的 rolling。已有持仓若不在候选池，仍由 Trade 跟踪；给池外持仓评分需要明确参考截面和缺数据政策。

### 4.2 LabelSpec

```text
label_id / semantic_version
horizon_sessions
feature_session / decision_time / earliest_trade_time
return_start_rule / return_end_rule
price_basis / corporate_action_policy
absolute_or_excess / benchmark_ref
normalization_policy
maturity_rule / missing_or_delisting_policy
```

改变 horizon 必须改变实际公式与 maturity，不能只改变 manifest。使用真实交易日历，不用工作日近似。前瞻目标起点/终点含不含首日由具体规则固定，不用字符串“未来 20d”隐含定义。

训练 cutoff 为 T 时，只使用 target_end 及必要结果在 T 前已经可用的标签；验证早停和模型选择也需遵守模拟训练时可知性。长期标签重叠时按目标区间做 purge/隔离，不仅比较 feature 日期。

缺价、退市等不能事后删除全部亏损样本使训练/评价偏好幸存者。无有效价格或退出事实时按 label contract 标记并报告覆盖偏差；不能把所有 invalid 都填 0。

### 4.3 Rolling 与 OOS

保存每个 fold 的 train/validation bounds、label maturity cutoff、fit state、ModelRelease、允许预测区间和实际样本。模型“文件今天生成”与“模拟当时允许预测”不是一个时间；后者必须由数据/标签/训练时序证明。

最终 holdout 在研究选择前锁定；不把不断用于选择参数的验证集称作 untouched OOS。Meta/stacking 只能读 base model 的 OOS predictions，禁止把训练内拟合分数当新 Feature。

### 4.4 Baseline 资格

Legacy baseline 可封存并用于 forensic 数值对照，但不因冻结就认定正确。新 baseline 绑定实际消费范围的 Snapshot/QuerySpec、PIT 限制、Feature、Label/Dataset 和执行检查；可接受的历史 best-effort 事前声明，不要求 Data 先建通用认证平台，也不能据探索结果宣称严格历史可见。

不能同时改变 universe、PIT、Feature、模型和执行口径，再把收益变化全部归因模型。CSI800 与 CSI1800 的结果分开记录；source history 不足以支持 strict 长历史时，显式限制范围或 best-effort，不能为了训练 8 年而伪造历史版本。

## 5. SignalRun、表达式与评估（P06/P10）

Raw 和 Derived SignalRun 使用同一读取/评估/回测协议。必要字段：security_id、feature/decision session、knowledge_cutoff、simulated_available_at 或实际可用时点、score、score semantics、signal_stage、valid/invalid_reason、model/fold/source refs。

SignalExpression 固定 inputs、join keys、算术/条件操作、归一化截面和缺失处理；用受控表达式或 SQL 编译计划，不允许任意 Python eval、动态网络或隐藏数据库输入。

### 5.1 信号评估

| 组别 | 输出 | 使用条件 |
|---|---|---|
| 全截面 | IC、Rank IC、ICIR、有效样本数 | 日横截面、horizon、权重、NaN/tie 规则固定；ICIR 年化与非年化分开 |
| 排序头部 | Precision@K、Recall@K、NDCG@K、TopK forward excess | 固定候选池、正例/等级、K、基准、无正例日处理 |
| 分布 | 分位组收益、单调性、coverage、missing | 这是标签条件统计，不伪装成可交易净值 |
| 时序稳健 | 分年/阶段/行业/市值、delayed vintage、跨 horizon | 区分未成熟标签，不能只看最终总均值 |
| 增量 | 消融、相关/冗余、TopK 错误、组合贡献 | 与已固定 baseline 做受控对照 |

Recall@K 的正例集合必须单独定义。例如预测 Top5 对真实未来 Top20，recall 分母是 20，precision 分母是 5；若预测与真实集合都固定 K，则两者数值相同，不能当两项独立证据。TopK 盈利比例、进入真实 TopM 比例、跑赢基准比例要不同命名。

NDCG 的 relevance/gain 映射在协议中固定；不能直接把含负数的原始回报当任意 gain 后仍宣称是标准 0–1 指标。长周期重叠样本不按独立日观测夸大显著性；至少报告时间分段稳健性，并在需要时采用保留时间依赖的区块重采样。

## 6. 策略/模型效果提升：首批研究路线

以下为**实验路线，不是承诺有效的默认实现**。先完成可信 baseline 与统一评估，再逐项对照，不一次开启全部项目。

### 6.1 先把“看起来强”拆开

历史讨论中，当前股票池/PIT 改正后，收益大幅回落且回撤仍高；某个 phase 比其他 phase 好很多。把这些作为待解释现象，不保留“phase0 天然更优”结论，也不为恢复原 CAGR 放松正确性。

拆开三种可变性：训练随机性/数据窗口；再训练起点 phase；换仓起点 phase。分别固定其余变量，在相同股票池、成本、日期和真实标签下比较。输出平均、最差阶段、rank/TopK 重叠、组合/单票贡献，不只选择最好曲线。

### 6.2 60/180 天与调仓周期

历史方案包含 60/180 日信号混合；inventory 还提出“180d 候选池、60d timing/rebalance”。把两者做成两个清楚的 Signal/Strategy 配方比较，不把后者当已证明优于前者。

预测 horizon、重训频率、调仓频率、最短持仓和退出规则是独立参数。短 horizon 配短周期、长 horizon 配长周期只是研究假设；不因预测 180 日就写死必须持有 180 日。

先比较：固定混合基线、长周期候选+中期排序、信号保留/换仓滞回。相同 SignalRun 可比较持仓政策；不同 label/model 则生成各自 OOS SignalRun。最终仍以成本后、回撤和恢复体验验收。

### 6.3 LambdaRank / 头部排序

目的：检验优化头部排序能否改善 Top5/候选池，而非只提高总体 IC。以固定回归 baseline 对照。

初版 RankExperimentSpec：同一 session 的截面作为 query group；标签按事前规定的等级映射为非负整数；明确 gain、K、训练/验证 group 和 head focus。LightGBM 的 LambdaRank 接受整数 relevance，并通过 label_gain 定义等级增益；group 长度与数据排序必须匹配官方接口。[E4][E5]

它优化排序目标，不保证 Recall@K、净收益或回撤一定改善。固定 Feature/Dataset/训练预算，比较总体 IC、头部 precision/recall/NDCG、False Positive、跨 phase 稳健性及同一回测。

历史交流曾报告 top-weighted sample weighting 没有形成预期增量；本版不复述未复核的精确数字，也不把它默认 promote。将其作为需关联旧 artifact 的负面结果，只有提出不同假设才重试。

### 6.4 新信息优先，不盲目加重复变换

既有 inventory 已列大量量价、流动性、相对强度、财务/股东和候选 Feature。先核对真实实现、公式和已尝试结果，避免把旧 Feature 改名当新增。

首批候选按证据条件排序：

| 方向 | 要检验什么 | 依赖与归属 |
|---|---|---|
| 量价/相对强度整理 | 完整既有价量集合与有结构组合是否有增量，而非只用少量旧列 | Research Feature；Data 提供行情事实 |
| 三表质量与风险 | accrual/cashflow-profit、应收/存货、短债/现金、资本开支等减少头部误选 | Data 提供 as-of 三表/稳定 TTM；Research 做预测组合 |
| 财报事件 | 公告 surprise/后续漂移是否只在公告后窗口有效 | event age、source-specific PIT；分别评估事件/非事件样本 |
| 股东与资金数据 | 新鲜度、内容变化是否有增量，是否仅来自不可信 vintage | Data 先证明时间/单位/分组；Research 才做衰减/预测分 |
| 入场与持仓管理 | 长期高分标的的过热/失败突破是否减少大回撤 | 不将三个简单 heat 指标等同完整拥挤度；保留否证实验 |

缺 raw/vintage 时停止对应研究，返回 DataRequirements，而不是 Research 自动补拉或绕过 contract。

### 6.5 稳定性、组合与退出

Seed/fold/phase ensemble、多 horizon blend 可作为候选，不默认“集成一定更稳”。它们要证明 head selection、回撤/恢复、成本和单票依赖的改善；权重不能用最终测试期优化。

“错了快撤、对了多拿”拆成保留/替换、trailing、stale、score change 等可解释政策。用保存的 SignalRun 做成对回测，观察 exit 与 replacement 的增量，而不是只看变动后的总 CAGR；已有仓与新开仓都必须有明确政策。

风险门控、行业/个股上限和现金使用通过 Core policy 发布；研究里不另写交易引擎。单票大牛与单阶段贡献、回撤恢复、最差滚动年度必须保留。

### 6.6 DNN/Transformer 与训练稳定性

作为可插拔后续候选保留，不是完成平台或第一版 alpha 的前提。必须证明相对于固定 LightGBM baseline 的 OOS 增量，记录 seed、重训 phase、输入序列长度、算力与推理成本。

Stock-ID embedding、fine-tune、序列状态要处理新上市/未见证券、warm start 来源与隐藏状态重置。不能借模型更复杂跳过 Feature/PIT/Label 合同，也不预设一定超过树模型。

## 7. LLM 财报因子：独立候选通道

继承原专项方向：候选 Top50/Top100 的财报文本，提取经营解释、需求/订单、库存与回款、风险变化、文本与三表一致性；不先全市场每日扫描，也不让 LLM 直接替代 Trade 决策。

```text
Data 保存文档原文/版本与公开证据
→ Research 固定候选名单、文档 refs、提示词/模型/抽取 schema
→ 保存原始响应、字段、引用文本位置与不确定性
→ FeatureRelease/Build 或冻结外部 SignalRun
→ delayed OOS evaluation
→ 同一回测 / 前向 shadow
```

保存 provider/model identifier、prompt/schema/version、参数、generated_at、文档 hash/来源/可见时间、输出内容 digest、候选池版本。外部模型再调用不保证返回同样文本，复验首先依赖已保存输出，不以重新请求等同重放。

历史 LLM 回填还可能受模型已有知识影响；固定历史文档不足以自动证明无未来信息。限制为有出处的字段抽取，不允许无来源自由预测；历史结果标明限制，优先以前向采集、标签成熟后的 delayed evaluation 验证。文档由 Data 管，LLM 预测性结果由 Research 管，不能标成供应商原始事实。

原专项的 API 权限与 ann_date-only 早期设计不是当前 PIT authority；按 Data 的 revision-bound policy 重新验证。没有证据的文本/财务版本不能因是 LLM 输出就绕过。

## 8. 工程性能：复用，而非第二套快速语义

### 8.1 阶段缓存身份

| 阶段 | 最低 cache/build key |
|---|---|
| 基础 Feature | data/view/derived + FeatureRelease + scope/lookback + universe/reference CS + PIT/cutoff + implementation |
| 模型输入 | base/cross-section build + fitted state/model/fold + sample/schema |
| Label | snapshot + LabelSpec + benchmark/calendar/action policy + scope |
| Dataset | Feature/Label builds + sample/filter/split + fit protocol |
| Model | Dataset + training recipe + seed/environment + actual source |
| Prediction/Signal | Model(s) + FeatureBuild + allowed dates + SignalPlan/stage |
| Evaluation | Signal/Label refs + EvalSpec/scope/benchmark |
| Backtest | Signal/Strategy + MarketView + initial state + execution/evaluation + Core/Runtime |

Cache hit 必须校验 manifest/身份与输出支持范围；prefix/superset reuse 需显式裁剪合同，不能只因文件名相同就命中。研究产物被正式引用后不允许被“同 idea 重跑覆盖”；scratch 可清理，但正式 rerun 用新 attempt/run。

### 8.2 运行效率方案

按列/日期读取，复用固定输入与 Feature panel；已使用 Qlib 时复用其 exports；避免每 fold 重建全历史；label/信号落盘一次供多种分析；SignalRun 变换使用向量化/受控查询；优先 profile 实际 CPU/I/O/内存再优化。

并行层级只选择合适的一层：fold、model 或库线程；显式配置总 CPU/内存/并发预算，防止 worker 与 LightGBM 线程叠乘。checkpoint 从已完成窗口继续，不能把不同数据/配置的旧窗口拼起来。

训练可复现不只固定一个 seed；锁定依赖/编译环境并记录 backend。LightGBM 官方说明 deterministic 不意味着不同版本/编译器/系统结果完全相同，验收需声明环境和容差。[E4]

### 8.3 性能验收方法

固定硬件、样本规模、冷/热 cache 和参数，记录每阶段 wall time、peak memory、bytes read、cache hit/miss、训练/推理次数。首轮测量后确定预算，不在本设计凭空承诺分钟数或倍数。

最低硬断言：只改 portfolio 时 train/predict 为 0；只看图或评估不触发训练；同 FeatureBuild 可被多实验并发只读；恢复不重复完成 fold；优化前后 keys/NaN/Feature/prediction 及关键排名边界满足声明一致性。

### 8.4 变更影响与重跑边界

| 变更 | 最先失效的阶段 | 可复用部分 |
|---|---|---|
| 只改 UI/报告排版 | 展示缓存 | 数据、Feature、模型、信号、账户评估原值 |
| 只改 EvaluationSpec | 对应信号或账户评估 | 已保存 Signal/Label 或 Backtest 事件/净值 |
| 只改 portfolio/exit/risk | Trade 回测决策后半段 | state-independent SignalRun 及上游 |
| 只改 SignalExpression | 组合信号 | 子 SignalRun、保存模型与 Feature |
| 改模型损失/超参/seed | 模型训练 | 相同 Dataset/Feature/Label |
| 改 Feature 公式/fit state | 对应 Feature/model-input 构建 | 不受影响的事实/基础 Feature/Label |
| 改 Data 单域/contract/PIT | 依赖该域的 derived/view 及下游 | 可证明无关的输入对象；不能只凭同名复用 |

依赖判断由 manifest 中的实际读取集合决定。全局 ruleset 增加新检查时可以复用旧产物字节并补验证，但不继承未经检查的可信状态。

## 9. 受控对照、证据与发布

| 比较 | 固定什么 | 回答什么 |
|---|---|---|
| D1/F1 vs D1/F1 | 输入、代码、参数、环境 | 可复验性 |
| D1/F1 vs D1/F2 | 数据/派生、标签/split、训练与执行协议 | Feature 增量 |
| D1/F1 vs D2/F1 | Feature/模型训练协议、派生规则、执行口径 | 数据/PIT 修订影响 |
| 同 SignalRun，policy A vs B | 信号/市场/初始账户/成本 | 换仓/退出/风控增量 |
| 同 features，model A vs B | 样本/标签/OOS/预算/执行 | 模型增量 |

某个比较需要重训时，模型产物不同是预期；另一类“固定保存模型只改输入”只是该模型的输入敏感性，不能混为一谈。

Evidence 绑定主次指标、scope、baseline、数据资格、变体总数、时间 OOS、头部表现、分阶段/行业稳定性、成本/容量、单票/阶段依赖与限制。保留失败与负结果，不只注册赢家。

生命周期建议 `scratch → candidate → accepted-for-shadow → deprecated`。Feature/Model/Strategy artifact 本体不可变，decision append-only；Trade 另做 deployment admission/real approval。状态描述可调整，但研究通过不得直接切真钱指针。

## 10. StrategyRelease 与公共 API

```yaml
strategy_release_id: sr_example
feature_release_ref: fr_example
model_refs_or_schedule_ref: schedule_example
signal_plan_ref: signal_plan_example
portfolio_policy_ref: portfolio_example
rebalance_policy_ref: rebalance_example
risk_policy_ref: risk_example
state_contract_ref: state_example
data_requirements_ref: data_req_example
runtime_package_ref: package_example
compatible_core_api: core_api_v1
research_evidence_refs: [evidence_example]
```

单纯回测组合政策不必新训模型；规则信号不必伪造 ModelRelease。部署账户、实际资金和券商凭证不在策略包里。

```python
build_features(snapshot_ref, feature_release_ref, scope, policy) -> FeatureBuildRef
build_labels(snapshot_ref, label_spec_ref, scope) -> LabelBuildRef
build_dataset(feature_ref, label_ref, sample_split_spec) -> DatasetRef
train(dataset_ref, train_spec) -> ModelReleaseRef
generate_signal(model_or_schedule_ref, feature_refs, signal_spec) -> SignalRunRef
evaluate_signal(signal_ref, label_ref, eval_spec) -> EvaluationRef
backtest(backtest_request) -> BacktestRunRef  # 调 Trade 公共 API
publish_strategy(strategy_spec, evidence_refs) -> StrategyReleaseRef
query_feature_layers(build_or_run_ref, security, interval, stage) -> ResearchChartLayers
```

View/build 不存在时报告明确缺口；不自动触发 Data collect/repair。查询只读，缺历史 Feature 层可提供独立重建任务规格，但用户单击图表不执行。

## 11. 最小实施路径

R-M1：固定一份合格 Data scope、一个 FeatureRelease、60/180 中实际可用的明确 label、保存模型与 OOS SignalRun，接入信号评估和 Trade 回测。

R-M2：补组合信号、受控对照、缓存/checkpoint、Feature/Signal 图层查询和发布包。

R-M3：在固定 baseline 上做一个头部排序试验和一个新信息试验；phase/成本/集中度报告随之完成。复杂模型、LLM 与自动研究逐项启用，不作为 R-M1 前提。

## 12. 最小验收标准

| ID | 情景 | 必须结果 |
|---|---|---|
| R01 | D1/F1 重建 | keys、schema、Feature、label 一致，保存模型推理在容差内 |
| R02 | 更换 horizon 参数 | 真实标签起止/公式/maturity 随之变，错误 manifest 被拒绝 |
| R03 | label 未成熟、purge 区间重叠 | 不进入模拟训练/验证；保留排除原因 |
| R04 | 把 in-sample prediction 用作 stacking 输入 | preflight 拒绝或标非正式，正式只接 OOS base signals |
| R05 | 同名 Feature 公式或 fit state 改变 | 新身份/cache miss，旧产物仍可读取 |
| R06 | 加入未来事实、shuffle 行列、改 PIT 截面 | 无穿越/错位；违规输入失败而非 silent fill |
| R07 | cached signal 做两次 policy 回测 | train/predict 次数为 0，结果来自同一 Trade 引擎 |
| R08 | ranker group 错误、负/不支持 relevance、K/正例定义缺失 | 训练/评估拒绝；Precision/Recall 含义清晰 |
| R09 | 只一个 phase 极好 | 报全 phase/seed/window 分布与最差值，不只保留赢家 |
| R10 | D/F/model/policy 多因素同时变化 | 报整体升级，不宣称单因素因果 |
| R11 | Feature 无数据/已知单位或 vintage 错误 | 请求 Data 补能力，不能自动补拉或通过 metrics 洗白 |
| R12 | cache hit、断点恢复、并行读取 | 只复用兼容闭包；无跨版本拼接/覆盖；记录实际执行次数 |
| R13 | 保存发布包移走 Research workspace | Trade 可加载并推理，无训练/源码目录依赖 |
| R14 | UI 查看 base/model_input/score | 与选择的 build/model/fold 一致，stage/source 可回查 |
| R15 | LLM 抽取/复验（启用该能力时） | 记录文档/输出/提示词与时点，重放不重新请求，历史限制明确 |
| R16 | 候选研究完成 | 只生成候选/证据，real deployment 未自动改变 |
| R17 | 性能优化或量化/dtype 变化 | 先测值/键/NaN/排名边界，再报告耗时/内存；无偷改语义 |
| R18 | 失败/负面试验 | 可查询、属于 attempt family，并进入证据变体计数 |

收益未达到用户愿望不代表软件合同没完成；软件通过也不证明策略有 alpha。两类结论必须分别交付。

## 13. 资料依据、证据限制与待确认

主要依据：`qsys_signal_centric_research_architecture.md` §2–8、§12；`qsys_feature_signal_mining_inventory.md` 的 Feature 表和“使用口径”；`qsys_pit_financial_llm_factor_plan.md` §6–10；`qsys_roadmap_v0_3_post_stable_framework.md` 的 Research/Candidate/Shadow 路线；本对话关于 phase、收益/回撤、LambdaRank、IC/Recall、标准回测评估和缓存复用的决定。

本版新增明确协议：Feature stage/fit-state 区分、排名指标正例定义、试验与工程性能分离、LLM 历史知识限制、包依赖与缓存闭包。旧文档的 ann_date-only PIT 与固定目录 artifact 命名以 Data/新版本合同为准；inventory 不作为“所有列当前已实现”的证明。

外部接口核对（2026-09-05；并非指定升级依赖版本）：

- [E4] LightGBM Parameters：`https://lightgbm.readthedocs.io/en/stable/Parameters.html`，ranking objective/label_gain 与 deterministic。
- [E5] LightGBM LGBMRanker：`https://lightgbm.readthedocs.io/en/stable/pythonapi/lightgbm.LGBMRanker.html`，query group、评估与 fit 接口。

仍需专项确认：当前真实可复验 baseline refs、数据/PIT 可用范围、首个标签公式、phase 定义、相关性/正例等级与主指标、冻结 holdout、资源预算、首批 Feature/model 插件。不要凭本设计重新解释旧结果为已验证结论。
