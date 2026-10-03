# axiom-data：个人维护版设计与实施边界

> 文档编号：AX-DATA · 个人版修订 v0.4 · 2026-10-03。\
> 状态：目标合同；生产来源按用户最新决定统一为 Tushare，外部 cross-check 只产生 warning，不改写事实或阻断发布。约定的Data代码范围及两份真实教程已验收，证据见实现仓库交付说明；十二年全量采集尚未运行。\
> 上位边界：[总纲](01_axiom_overview.md)、[补丁 A](07_重要补丁_A.md)、[补丁 B](07_重要补丁_B.md)。本次个人版约定已同步相关文档；四仓分工与 Engine/Core、Runtime 的逻辑边界不变。\
> 阅读示例：[For Quant Researcher](../../notebooks/researcher_tutorial.html) · [For Quant Dev](../../notebooks/developer_tutorial.html) · [Notebook 与复运行说明](../../notebooks/README.md)。两种视角使用同一套设计。教程只证明其实际运行范围，不构成全部供应商能力或生产回测验收。

## 0. 本文与两份 Notebook 的关系

本文件是 Data 的存储、时间与公共读取合同。总纲及已同步补丁约束四个物理仓库和五个逻辑域；Core、Research、Runtime、UI 各自拥有计算 ABI、研究产物、运行/账户与组合展示协议。Researcher Notebook 讲研究使用，Dev Notebook 展开实现审核，二者用同一批留存的真实市场数据及当前产品 API 解释本文，不另行定义一套跨仓 schema。HTML 从同名 `.ipynb` 生成。

教程通过实际 Reader、来源映射、跨仓薄接口与离线重建展示本文，分别使用一年工程样本和完整来源小窗口。生产成员与生命周期来自 Tushare；旧版官网调样与独立公告的结果是外部检查记录，不作为新版数据或发布条件。真实演示不代替晚到、撤销等反例测试，示例省略不等于合同豁免。跨仓接口调整须同步总纲索引、owner 文档和两份 Notebook。

当前施工证据另见 [Researcher 教程](../../notebooks/researcher_tutorial.html)、[Developer 教程](../../notebooks/developer_tutorial.html) 与
[实现合同和支持边界](../local-implementation-contract.md)：已贯通 Raw、Parquet、Snapshot、
日频/成分/事件 Reader、单季/TTM、状态与范围规划、分段 PIT、跨仓薄 adapter 和 portable bundle。
逐条验收见 [D01–D18 清单](../demo-acceptance.md)。真实来源完整性和历史 vintage
并未因此得到证明；固定 1800 标的一年真实采集、核对、离线重建和搬移已完成；范围、未完成事项与教程证据见 [设计对照](../design-conformance.md)。本次来源政策调整来自用户明确决定，覆盖先前“供应商基线＋官网调样必须闭合”的要求。

## 1. 判断与范围：小而严谨的数据底座

单人维护、A 股日频、沪深300及 CSI1800 历史成员、退出成员与指数基准，以及明确启用的7ETF日线轮动；研究输出起点可选 2020 年或 2014-11-01。输出起点与 lookback 采集起点分开，结束日为来源可提供的已完成交易日。真实验收使用一年行情工程样本、双证券完整源和7ETF日线；独立安装、恢复与离线搬移已验证，十二年全量尚未实际采集。

值得保留的是：旧输入可恢复、单位和证券身份正确、使用供应商历史成员并集避免只选今天成员、缺数可解释、时间资格诚实。供应商快照可能遗漏月内临时进出股，这一来源限制明确报告，不据此停止初始化。把 DomainCommit、DerivedCommit、View、Validation、Admission、CodeBundle 都做成独立发布和登记系统，对当前规模不划算。

首版用户体验是：显式更新一次，固定一个 Snapshot，用几行 Python 读取，再在 Notebook 中研究和解释异常。不要求用户每天操作多个产物生命周期。

Data 保存事实、时间、来源和稳定变换；Research 定义 Feature/Label/样本/模型；Engine/Core 执行共享计算；Engine/Runtime 处理时间推进、成交与账户；UI 只读组合展示。数据检查通过不代表策略有效或可以实盘。

## 2. 首版只有三类持久对象

```text
SourceProfile + 请求
  → Raw 原响应 + fetch 日志
  → 版本化 Canonical Parquet
  → 一个 Snapshot manifest，内嵌按域的完整分区映射
  → Reader(snapshot, QuerySpec)
  → DataFrame + schema + refs + 时间/缺失元信息
```

| 概念 | 个人版实现 | 何时再扩展 |
|---|---|---|
| RawBatch | 一条请求/观察日志，引用原响应 | 无需独立注册服务 |
| DomainCommit | Snapshot 内的域段：contract、来源、实现和完整 partition map | 确有独立域发布/共享需要时，才抽成独立 manifest |
| Snapshot | 一份不可变 JSON 固定事实状态；是主要持久发布单位 | 不包含 Feature、模型或账户 |
| FactView / MarketReplayView | 固定 Snapshot 的 QuerySpec + Reader；可作为内嵌 ViewRef | 不为每次查询发布文件；物化只因真实性能或格式需求 |
| Derived | 版本化纯函数；必要时缓存，键包含 Snapshot/输入域、配方、scope、PIT/cutoff | 昂贵且跨任务复用后，才需要独立 DerivedCommit |
| QlibView | 显式导出固定数字日频Query；Research薄adapter使用实际Qlib读取 | 不成为日更或普通Reader查询的前置；新cutoff/字段生成新版本 |
| SourceEvidence | 可选原文/附件/引用与具体 revision 的绑定 | 按目标研究需要补证据，不建设全历史证据管理平台 |
| Coverage / quality | 请求与成功范围、异常列表、实际检查结果 | 不建立通用认证、资格撤销或 admission 平台 |
| Catalog | 首版读 manifest；索引可后加 | 查找成本确实出现时加可重建 SQLite |

Ref 表达可恢复的确定引用，不意味着独立目录、数据库记录或发布流程。域引用可以是 `snapshot_id + domain`；逻辑 ViewRef 可以在实验/run manifest 中内嵌 Snapshot 与 QuerySpec。保留语义版本轴，不人为增加持久对象。

建议目录：

```text
data/
  raw/objects/<digest>/payload.*
  raw/fetches.jsonl                   一行一次请求，按 batch_id 引用
  canonical/<domain>/<partition>/<version>.parquet
  snapshots/<snapshot_id>.json
  cache/                             可选 derived 缓存
  exports/qlib/                      显式导出的固定消费者格式，可存放于独立目录
  current.json                       可选默认 Snapshot 指针
```

实际数据不进代码 Git；数据根可配置，manifest 使用相对路径。相同内容与未变分区复用，不复制全历史。旧 Snapshot 和它引用的文件不可原地改写；不做自动 committed GC。单机单写者足够。

## 3. Raw、合同与规范事实

### 3.1 Raw 是低成本保险

保留原 JSON/CSV/附件字节；SDK 返回表格时保存原字段和单位，并声明序列化方式。不能换单位、丢字段、把空值变零后称为 Raw。

fetches.jsonl 每行按 batch_id 标识一次请求，记录：endpoint、参数/证券/日期范围、SourceProfile 版本、retrieved_at、成功/空结果/失败、分页是否完整、payload 路径及摘要。重复响应可复用内容对象，每次观察仍留日志。Raw 已成功而转换失败，不回滚 Raw。

今天重拉的终态响应不能替代以前保存的输入。摘要用来发现内容变化或损坏，不能替代文件与备份。

### 3.2 合同只定义当前需要的字段

每个来源/域的配置明确：经济键与 revision 身份、字段映射、单位/dtype、证券映射、空值/撤销含义、可用时间规则和分区。来源和字段合同可以是源码中的版本化配置，Snapshot 内嵌本次实际使用的内容；无需独立 contract 文件或通用 schema 注册平台。

- 日期/证券/单位只在一个转换入口处理；不按数值大小猜测手/股、千元/元或百分数。
- 同一经济键的内容变化保留 revision；同一内容重复观察保留最早 first_observed，不因重建而后移。
- 修订顺序由明确来源规则决定，不以文件顺序或“最后一次抓到”任意选赢家。
- `value / not_provided / retracted / source_missing / parse_error` 按实际源能力表达；不能统一前填。
- `pre_close` 保留供应商口径，不无条件替换为上一行 close。

### 3.3 从真实研究需求选域

所有默认生产数据来自 Tushare：行情、财务、日历、身份、上市/退市日期、停牌、成员与基准。接受供应商内容，不重新认证其每条历史事实。外部来源可单独 cross-check，结果是 warning，不替换 Canonical 数值，不成为 prepare/run/current 的必需门槛。

Axiom 仍负责自己的请求、单位、键、类型、时间与文件映射。请求失败、已知截断、文件损坏或本地错误不能冒称成功；供应商数值异常、外部来源分歧和来源精度不足分别记录为 warning/limitations。PIT 表达实际知识边界，不把信任供应商解释为今天的终态必然是当年的版本。

| 范围 | 内容与必要语义 |
|---|---|
| 市场研究首批 | security_master、trading_calendar、market_daily、历史 membership、指数基准；只启用当前研究用到的字段 |
| 账户回放所需 | security_status、price_limits、corporate_actions 和规则适用时间；进入该用途前补齐受影响范围 |
| 财务/股东（本轮已明确启用） | 财报按证券+endpoint+报告期+报表类型+revision 保存，股东报告保留报告组与完整性；接源单位、公告与修订时间、真实输出及单季/TTM 纳入完整 Data 验收 |
| 其他候选域 | 不预搬两融、资金流、全部公告等候选数据 |

Canonical 日频是 `(security_id, session, revision)` 的宽表；事件按经济身份保存，再按 cutoff 投影，不在每个交易日复制事件。起步按域/月分区，财报可按 endpoint/报告年；测过真实尺寸后再优化，不按每股每字段预建小文件。

ETF轮动新增范围使用基金身份、未复权日线、fund_adj、分红与涨跌停专用来源；复用相同存储和读取合同。用户已确认只用日线，消费者约定每周首个交易日使用上一交易日的21交易日窗口计算20D动量，在当日开盘价执行；Data仅采集与提供对应原始数据，动量/排名/信号归Research，成交/账户归Runtime，不称精确复刻09:35成交。详细采集与消费者合同见 [ETF日线轮动](../etf-rotation-data.md)。

## 4. 一个 Snapshot 足够固定事实

下面是结构示意，不表示真实数据或已实现 API：

```yaml
snapshot_id: s_example
parent_snapshot: s_parent
schema_version: personal_data_v1
build_context:
  code_ref: recoverable_git_commit
  dependency_lock_ref: recoverable_environment_lock
  config: {operation: update, market_partition: month}
checks: {usage: market_research, issues: []}
domains:
  market_daily:
    contract: {contract_id: market_v1, logical_key: [security_id, session], fields: declared_fields}
    source_profile: {id: supplier_daily_v1, units: declared_units, availability: declared_policy}
    build_context: {code_ref: market_builder_commit, config: actual_market_config}
    raw_batch_ids: [b1]  # 对应 raw/fetches.jsonl 中的请求，日志再引用原响应
    partitions:
      - partition: "2020-01"
        uri: canonical/market_daily/2020-01/p1.parquet
        file_sha256: "..."
        rows: 100
    coverage: {requested: requested_range, completed: completed_range, gaps: []}
  universe_membership:
    contract: {contract_id: membership_v1, interval: "[from,to)"}
    source_profile: {id: supplier_membership_v1}
    build_context: {code_ref: membership_builder_commit, config: actual_membership_config}
    raw_batch_ids: [b2]
    partitions:
      - partition: "history"
        uri: canonical/universe_membership/history/u1.parquet
        file_sha256: "..."
```

每个域段保存完整分区映射与该域实际构建来源。更新目标域时保留其他域段及其原 provenance，不能把旧域冒称用新代码重建。相同事实/coverage/时间语义未变时，新 fetch 日志不强迫创建新 Snapshot。

需要改变事实、coverage、单位/时间语义或 provenance 时生成新 Snapshot；仅补一个检查报告不改变事实版本，可在实验或操作记录中保存。新增派生配方或 Feature 不改变原 Snapshot。

build 的代码来源须可恢复：保存已提交源码的 Git 历史、依赖锁与实际配置，从确定版本构建。不能只记一个已经丢失的 SHA，也不能用未保存工作区修改假称该 commit 的结果。日常不建立 dirty 捕获/源码打包系统；重要发布或移机时可另存源码归档。运行中读取的合同和配置也固定。

当前写入在启动时验证干净 commit 或留存安装 wheel 的 origin/digest，并把真实 builder、依赖锁与环境绑定到操作和新建域；未变化域保留原来源。Snapshot 回放只选其引用的 Raw；历史字段扩展另用显式域/成功状态/receipt cutoff 选择预览。Snapshot bundle 是依赖闭包，完整 Raw 备份须显式选择截止接收时刻，见[恢复路径](../data-change-and-recovery.md)。

## 5. PIT：两个问题，不是三个晋升等级

经济时间回答事实属于何时；具体 revision 的公开时间回答市场何时可能知道；first_observed 回答本系统何时收到；cutoff 是本次允许使用的边界。公开证据并不证明数值正确。

| Policy | 回答的问题 | 可见性规则 |
|---|---|---|
| `operational_pit_v1` | 本系统当时实际拥有什么信息？ | 从实际 first_observed 起可见 |
| `market_pit_safe_v1` | 有何依据认为市场当时能知道？ | 有具体 revision 公开证据用 source_available；否则保守采用 first_observed |
| `best_effort_vendor_v1` | 来源终态历史在明示假设下可以怎样探索？ | 按声明的 vendor date/发布规则投影，承认缺少历史 vintage |
| `bootstrap_hybrid_v1` | 历史探索与上线后观察怎样组合？ | 明确分段 policy、范围与限制，不标成纯 strict |

`verified / observed / best_effort` 是本次输入范围的证据依据，不是 policy，也不是全库等级。上线积累 observed、选择性补公开证据是两种能力；verified 不替代 operational。补证据可以改变新 Snapshot 对市场历史的解释，不能伪造本系统更早收到数据。synthetic 是数据性质，另行标注。

2026 才采到 2020 终态历史不可能仅靠架构恢复 2020 信息集。允许用于明确的历史探索；严格结论不得借此成立。对需要的少数财务/成员事件补证据，胜过追求全历史认证。

读取顺序：按 policy 过滤可见 revision → 每个经济事件选择版本 → 再做 TTM/单季/日频投影。只有公告日期没有时刻时，使用明确保守规则，例如下一交易日开盘；日线的 OHLC/量额不能提前给当天开盘决策。UTC 表达绝对时刻，session 使用市场日历。

分红的完整实施行同时包含原公告日与实施公告日时，以较晚日期作为整行best-effort可见性的下界：股票按下一交易日09:30，ETF按当日20:00；只查询现金字段也遵循此边界。严格政策仍使用实际receipt或精确版本证据，不把该日期假设当作历史公开版本证明。

加入 usable_from 晚于 T 的数据不能改变 T 前 safe 输出。合法新证据证明更早公开时，新 Snapshot 可以改变历史解释，需说明影响；旧 Snapshot 不变。PIT 证据不足与单位错、日期错、错配证券等处理缺陷不是一回事，后者必须修复。

## 6. 范围、状态与派生的必要边界

三个集合分开：读取集合是历史成员 union 加 lookback；当天决策集合是当日可知 membership；账户跟踪集合还包括池外持仓/挂单。成员用半开区间 `[from,to)`，退出再进入不能合并成持续区间，且有效时间与可知时间分开。

成员生产来源为 Tushare `index_weight` 的实际 dated 名单。2026 年 6 月三个指数的实测中，6 月 12 日和 15 日均为空，整月查询只返回 6 月 30 日的 300、500、1000 行；每日调用不会凭空得到每日名单。历史初始化按月批量请求，保存供应商实际返回的所有 `trade_date`；空响应不清空已存在名单。CSI1800 按供应商三组名单的并集定义，不用官网调样改写。

名单观察日期、研究 session 和可知时间分别表达。两份快照间默认采用用户已确认的“截至该日最近一份供应商快照延续”：只选 trade_date 不晚于研究 session 且按所选 PIT policy 可见的完整快照，没有任何此前快照时为 unknown。不能把月底新名单回填月初，也不能称已恢复精确官方每日成分。原始快照是事实，日频投影是读取政策；两者使用相同 Raw 与 Snapshot。严格 PIT 仍受实际 receipt 约束，best-effort 才使用明示的供应商日期假设。

prepare 保留全部供应商历史快照候选并集、lookback 与池外跟踪证券，不能截成今天的 1800 只。月度观察不能证明未遗漏月内短暂成员；此项及外部比对差异以 warning/limitations 表达，不阻断生产初始化。先前必须闭合官网原始调样链的要求已撤销。

先看 Tushare 交易日历和身份，再看供应商停牌记录、请求覆盖。闭市不生成交易 session；未上市、退市、有记录停牌、未知缺数分别解释。上市/退市按供应商日期的声明语义处理；外部公告不覆盖供应商日期，分歧只记录 warning。有记录停牌可以零成交量，不能把缺行当停牌或补零价。Runtime 的最后有效价估值带时间与陈旧说明，不写回新的成交事实；退市存量及后续权益仍跟踪。

发现缺数时，修复、停止相关用途，或在研究政策下明确排除并报告影响；自动删掉缺数/停牌股票会改变样本，不能声称结果无偏。数据可追溯也不证明选样、成本和执行假设科学。

复权序列、TTM 等稳定派生先实现为纯函数，绑定输入 Snapshot、配方/实现版本、scope、PIT/cutoff。缺期、口径不兼容、撤销要显式缺失。Feature/Label 定义归 Research；缓存不是事实来源。

复权声明锚点与行动范围；严格 Feature 不静默用未来锚点。历史探索若采用来源终态复权，明确限制。账户回放使用未复权价格与公司行动，避免重复分红。复杂公司行动未支持时，只限制实际受影响用途，不能假装已支持。

一次决策的价格窗口内，各历史点使用同一个明确锚点，并仅使用该决策 cutoff 已知的行动/因子。不能把每行按本日锚点得到的价格拼起来算跨期收益。不同历史决策分别冻结各自窗口，后来的行动不能修改旧决策输出。例如假设 1 股拆成 2 股且无经济涨跌，价格由 10 变 5；以拆股后时点为共同锚点，窗口两端都应为 5，收益为 0，而不是 -50%。

统一事实字典入口为 `Data.dictionary(snapshot=固定ID, domains=...)`，由当前 adapter 合同/profile 与该 Snapshot 的实际合同生成字段、类型、单位/转换、时间和查询方法；中文含义只作展示注释。省略 Snapshot 看代码支持，指定版本看已声明字段，不将声明误作非空/PIT可见或完整覆盖。默认只读元数据；显式一个 Raw ID 才列出该响应未映射字段，不扫描全库。数据字典归 Data，Feature 配方归 Research，见[阅读入口](../overview.md)。

累计复权因子在没有新行动时可以保持非1值；价格变换使用 `factor(t)/factor(anchor)`，因子相同才使比值为1。合法行情空值、事件零行和季度不足分别保持结果与原因，不能统一填零；数据依赖、单位、身份和文件错误则修正实际受影响范围。

## 7. 一个 Reader，薄的消费者映射

默认同机 Python，不建微服务。以下是接口提案：

```python
data = Data(root=ROOT)
result = data.update(base_snapshot=BASE_OR_NONE, request=UPDATE_SPEC)
rebuilt = data.rebuild(base_snapshot=BASE, raw_batch_ids=RAW_IDS, domains=DOMAINS)
snapshot = data.resolve("current")  # 本次任务只解析一次
batch = data.read(snapshot=snapshot, query=QUERY)
members = data.members(snapshot=snapshot, query=MEMBERS_QUERY)
events = data.events(snapshot=snapshot, query=EVENT_QUERY)
replay = data.read_market(snapshot=snapshot, query=MARKET_QUERY)  # Runtime 执行侧
issues = data.inspect(snapshot, required_scope=SCOPE)
```

表格读取统一返回 `DataBatch(frame, field_meta, context)`。frame 是带键的 DataFrame；context 保存 schema、实际 Snapshot/查询 refs、query 参数、Reader/派生版本和 quality/limitations。UI JSON 仅把 frame 转为 records，返回 `records + field_meta + context`。field_meta 按键与字段关联来源/revision、usable_from、first_observed、依据和缺失原因；同源同时间字段可共用元信息，不逐单元格复制全套 manifest。update/rebuild 返回 Snapshot ID、changed、issues 与本次操作 ID；失败不返回成功的新版本，inspect 返回范围与问题摘要，无需强行塞成行情表。

QuerySpec 固定 Snapshot、字段、scope、PIT/cutoff、价格/复权口径和派生配方；结果 context 再记录实际 Reader/派生实现版本。实验保存这个结构即为逻辑 ViewRef，不需要为查询发布另一个 artifact。按用途保留 Fact/Market 的区别；首次只实现真实消费者需要的方法。

context 的 `contract_version` 标识 DataBatch 返回结构，独立于 Snapshot schema 与 Reader 实现版本；`generated_at` 是这次响应生成时间，不是行情新鲜度或历史可用时间。实际数据时间由 session、field_meta 和域覆盖表达。 本地 DataBatch 的 `to_json()` 保留可复现的语义内容；API 传输使用 `to_response()` 添加 context.generated_at，UI/P12 另有自己的响应生成时间。生成时间不进入逻辑 ViewRef 或缓存身份。UI 映射保留这些字段，同时使用自己的 P12 组合协议版本；未知必要字段/不兼容版本明确报错，不靠列位置或静默默认解释。

Research/Runtime adapter 按同一合同映射为 Core FactBatch；Core 不读 Data 磁盘。Runtime 给决策与成交模拟注入不同 Reader 权限：开盘决策不能提前看完整日线，simulator 只能随时钟推进读取随后行情。UI 后端将同一结果转为 JSON，保存 query context；不重算 Feature、不联网补数。

### 7.1 用途查询的共同语义

上述 QUERY 均为用途明确的 QuerySpec：固定具体 Snapshot、scope、字段/事件选择和时间规则。成员查询包含 universe、生效 session 与 knowledge cutoff；事件查询包含事件种类、经济时间范围与 knowledge cutoff；两者都显式选择 PIT policy，不能只传 cutoff 后让 Reader 猜政策。scope 可用显式 sessions；使用区间时声明端点含义。QuerySpec 内若也含 snapshot_id，必须与函数参数一致。

MARKET_QUERY 绑定执行用途、证券/区间、未复权价格与所需状态/行动/规则，以及事件时间和所选历史修订解释。Runtime 可以预读固定回放文件，但按执行时钟释放事件，不能把后来完整日线或事件反送给早期决策。记录回放近似与来源限制；仅有 session 范围不构成完整的执行时间合同。

Research 构建 Label 时可以显式查询未来结果范围，保存独立 QuerySpec/上下文，由 Research 定义收益口径、maturity 与训练 cutoff。它不是该样本决策时的事实输入，不能进入 Feature FactBatch。Data 不解释 Label 公式，Runtime 不用 Label 代替成交回放。

### 7.2 四仓之间的数据与协议

| 路径 | 调用和交换内容 | 协议与 owner |
|---|---|---|
| Data → Research | Python Reader 返回 DataBatch；Research adapter 映射事实供 Core 计算，保存 FeatureBuild | P02 Data；P05 Core；P06 Research |
| Data → Engine/Runtime | 同一 Reader 提供决策事实与执行回放；Runtime adapter 注入 Core | P02/P03 Data；P05/P07 Core；P11 Runtime |
| Research → Engine | 冻结 Feature/Model/Strategy 运行包；BacktestRequest；返回 BacktestRun/EvaluationReport | P06 Research；P09 与账户 P10 Runtime |
| Data → UI BFF | records + field_meta + context，作为事实图层输入 | P02/P03 Data，P12 组合归 UI |
| Research/Runtime → UI BFF | 已保存 Feature/Signal/trace/委托/成交/账户/评估的只读投影 | P06/P08/P10/P11 各 owner；P12 UI |

物理仓库为 axiom-data、axiom-engine、axiom-research、axiom-ui；Engine 内 Core 与 Runtime 逻辑分开。Data 不依赖 Core 或 Research；Research 与 Runtime 各自拥有消费 Data 的 adapter，Core 拥有 FactBatch ABI。DataBatch 的 schema 归 Data，FactBatch 的 schema 归 Core；adapter 按键映射并保留单位、cutoff、缺失和来源，不重做 PIT、复权或 Feature。

Research 负责研究编排与训练，Feature/推理/信号共享执行由 Core 完成；正式回测只调用 Runtime。生产 Runtime 加载冻结的轻量运行包，不 import 可变 Research workspace。FeatureBuild/SignalRun 的合同归 Research，研究时写 Research 产物空间，运行时生成的写对应 Trade run 空间，均不写入 Data Snapshot。

UI 浏览器访问只读 BFF；BFF 调用各 owner 公共查询包，不直接拼 Raw/Parquet/账本来重新解释业务。Data JSON 不是完整 P12：ChartContext 还固定 run/build/stage、价格/时间口径及实际 refs；ChartLayer 组合各来源、缺失/重建状态，账户部分带 committed watermark。具体格式由 [UI §5–7](06_axiom_ui.md) 定义。首版同机函数调用足够，不要求每仓提供 HTTP 服务。

固定版本的粒度是一项离线实验，或长服务中的一个 session/决策批次。下一批可重新解析并检查新版本；已开始批次不变。长 run 保存 batch/session → Snapshot/QuerySpec 的实际映射，UI 重放沿此映射读取，不能只用最终 Snapshot。尚未进入 Data 的实时输入以 Runtime feed log 为来源，不冒称来自事后 Snapshot。

read 不采集、不修复、不隐式物化。Qlib已纳入本轮交付：显式export_qlib保存文件清单、完整开放日历/身份映射、原始单位、scope/PIT cutoffs、float32容差与exporter版本；实际Qlib读取与Reader等价。价格仍未复权，不暗中套训练归一化口径；财务/分红保持原生事件，不自动前填为日频bin。Research拥有Qlib初始化和消费adapter，P02/P03不依赖Qlib。详见[Qlib接口](../qlib-interface.md)。

## 8. 日更、修复与最小检查

```text
固定 parent Snapshot、请求范围、代码/合同/配置
→ 请求并保存 Raw/fetch 日志
→ 转换与实际范围检查
→ 写受影响分区新对象，复用其他文件
→ 原子发布完整 Snapshot
→ 已启用的日更计划成功后更新 current
```

单写者和一个操作 checkpoint 足够。成功 Raw 可续用；必需范围失败不切默认版本。可选域沿用旧段并说明其数据时间/缺口，不阻断无关研究。开始读取时解析 current 一次；下一任务才使用新版本。无须每天人工审批。

来源分别定义发布时间、可请求日期、分页和重查策略；不能假设只扫近几天能发现所有历史修订。修复优先从 Raw 重建受影响分区；必要人工 patch 保存目标稳定键、旧值、新值、原因及来源，不改旧 Raw。所有入口走同一更新/重建函数，不预建通用 patch DSL 或证据工作流。

检查直接服务用途：

| 检查 | 最小证明 |
|---|---|
| 请求与转换 | 证券/日期/分页范围、键唯一性、单位映射、空值含义 |
| 时间与修订 | 晚到修订、旧公告日、重复观察、开盘不能看日线的反例 |
| 实际需求覆盖 | 请求与成功范围、必需字段、lookback、成员/持仓集合及缺口；freshness 单列 |
| 不变与恢复 | S2 发布后 S1 数值不变；换目录、无网络也能读旧 Snapshot 与来源 |
| 消费者 | 第一个真实消费者的键、单位、时间、缺失一致；其他消费者实际接入时再检 |

结果只需 `可用于指定用途 / 有明确限制 / 不可用于该用途` 与具体问题。小型合成反例加一个真实窗口验证语义；正式任务检查实际需要范围。日更只检查新增与受影响范围，文案/UI 小改不重扫全历史。抽样不能说明全范围完整，但不因此建设通用 certification/admission 平台。

数据损坏时从备份恢复，不能重新算 hash 洗白。备份 Raw/fetch、规范文件、Snapshot、合同/实际配置、可恢复源码与依赖锁；缓存可重建。做一次恢复演练比增加一套登记系统更有价值。

## 9. 从零实施顺序与工期

本节保留从零规划时的粗估，假设AI持续实现、人工及时确认来源语义、一个主要来源和权限可用。它描述最初行情闭环的实施顺序，不是当前剩余工期或交付范围。当前已约定并完成的Data范围包括财务/事件和Qlib，实际代码、运行时间与验收证据见[交付说明](../../DELIVERY.md)；完整Engine/UI产品和实盘由各owner负责。

| 累计里程碑 | 范围 | 估计 |
|---|---|---|
| 假设数据闭环 | 三层存储、Snapshot、Reader、关键语义夹具 | 1–3 天 |
| 真实小窗口 | 一个来源、实际字段合同、增量/失败续跑与关键验证 | 3–7 天 |
| 首版行情研究可用 | 扩到沪深300历史范围、必需状态/价格口径、缺口解释和一次恢复 | 约 1–2 周 |
| 财务、股东、Qlib | 先明确原生事件/PIT与数字日频导出合同，再真实接源和消费 | 当前已纳入完整Data交付并验收；无剩余工期承诺 |

原先 6–9 周混合了人工兼职项目推进、联调和运行观察，不再作为 AI 首版实施的等待时间。来源限流、下载量、历史能力缺失与授权等待可能改变实际工期；运行观察可以和研究并行。实施从真实小窗口开始，边接源边明确合同，通过后扩范围。

最大风险依次是历史成分/退出证券覆盖、公司行动与复权口径、财务 revision 可知时间，以及范围不断膨胀。AI 可加速接口包装和测试，不能补造供应商不存在的历史 vintage。真实数据正确性通常比性能框架更早成为瓶颈；以测量决定分区/缓存，首版不做多机 writer、消息总线、通用 Feature Store、全公告 OCR、自动 committed GC 或全历史证据补齐。

### 9.1 开始实现只需补齐的交接内容

设计已足够启动实现，内部类和中间函数由实现者组织，不再编写完整内部设计。接源时补齐四项：

1. 实际供应商合同：endpoint、请求证券/时间范围、主键、单位、空值、分页与修订/可用时间。未知能力明确限制，不由 AI 猜测。
2. §7 的少量公共接口：表格形状、查询上下文及读写副作用；不需要预建所有消费者。
3. 固定 Raw 样本和独立核对的预期：单位、晚到修订、停牌/缺数、历史成员与日线 cutoff；不能从实现输出反推所有测试答案。
4. 可重复运行的完成命令：真实小窗口从 Raw 构建、读取、增量更新、重读旧版本、换目录恢复；如某步未支持，结果明确指出，不以假设输出替代。

Agent harness 首版就是上述固定夹具、测试命令和 Notebook 端到端运行。实现仓库的 AGENTS.md 保持一页导航：职责边界、公共入口、运行命令、来源与时间规则；不复制整套设计，不增加审批流程，不预建专用 Agent 平台。

## 10. 小而必要的验收

这些编号是可执行语义场景，不要求独立认证报告服务；可记录在测试输出或操作/实验日志中。

| ID | 场景 | 应得到的结果 |
|---|---|---|
| D01 | 发布 S2 后读取 S1 | 旧文件和值不变，新旧同时可读 |
| D02 | 同 Raw/合同/代码/配置重建 | 键、单位、值及缺失语义一致 |
| D03 | 重复观察后供应商改值 | fetch 日志完整，旧 revision 保留，首次观察不后移 |
| D04 | 单位/证券/日期映射反例 | 确定转换；未知映射明确失败 |
| D05 | 变更合同或修复实现 | 从明确输入生成新 Snapshot，不覆盖旧文件 |
| D06 | 晚到修订与日线开盘读取 | safe/operational 不倒灌；best-effort 限制明确 |
| D07 | 财务/股东接入时的撤销、多期与缺行 | 不压扁事件、不盲目前填或聚合；接入该域时执行 |
| D08 | 入池/退出/再入与池外持仓 | 集合正确，lookback 与账户跟踪不丢失 |
| D09 | 单域修复/补证据 | 其他域文件、数据与 provenance 复用 |
| D10 | 必需范围更新失败 | Raw 可留用，current 不变 |
| D11 | 换目录且无网络恢复 | 旧 Snapshot、来源和实际查询可恢复，无需 catalog |
| D12 | 文件损坏 | 明确失败并恢复，不改摘要冒充正常 |
| D13 | 新鲜但用途覆盖不足 | 报具体缺口，不获得全库通过结论 |
| D14 | 本轮Qlib读取与搬移 | 需要字段的键/日历/单位/NaN/数值和成员区间按固定Query合同等价 |
| D15 | 停牌、缺数、闭市、未上市、退市 | 状态可区分，不统一填零或删行 |
| D16 | UI 实际接入后查询旧 run | 用原 Snapshot/QuerySpec，读取无写副作用 |
| D17 | 真实消费者接入 | 同键、同单位、同时间与缺失语义，薄 adapter 不另算事实 |
| D18 | 外部配置/current 在运行中变化 | 已开始任务使用已固定的版本和配置 |

用户已明确本轮交付完整Data，2026-10-03进一步要求Qlib纳入：财务接源、PIT与证据、退市边界、历史股票池、Qlib导出/实际读取等价及Researcher/Developer教程须完成。完整账户撮合和UI产品由各自owner负责，Data消费协议与薄接口必须真实执行。未完成的来源决策应讨论，不得通过缩小验收范围宣称完成。
