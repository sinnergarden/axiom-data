# 当前数据命令行

安装 `axiom-data` 后，默认命令只使用 Raw → 类型化 Parquet → 不可变 Snapshot 路径。历史 V1 命令在隔离归档中。除了 `prepare` 和 `run`，下列命令均不请求供应商。

```sh
cd /absolute/path/axiom-data
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
axiom-data --help
```

一年完整来源配置使用 [配置样例](../examples/delivery_csi1800_one_year.json)：2025-09-01 至 2026-08-31。生产数据只取 Tushare：`prepare` 保存六个 `stock_basic` 上市状态切片、三个 `index_basic` 选择器、三指数组逐月 `index_weight` 原响应及沪深 `trade_cal`。成员采用每个供应商实际 `trade_date` 的整组快照，并从该日延续至下一次快照；月底名单不倒填月初。准备窗口额外覆盖起始日前一完整月，供起始日选择最近已有快照。`required_symbol_count: 1800` 是固定样本的锚点断言；历史并集范围可以超过 1800。供应商内容与外部来源不同只记警告，不改变生产成员或退市字段。

```sh
mkdir -p delivery
axiom-data --data-root /absolute/data prepare \
  --config examples/delivery_csi1800_one_year.json \
  --output-scope delivery/csi1800-one-year.scope.json \
  --token-file /absolute/private/tushare-token.txt
axiom-data --data-root /absolute/data plan \
  --scope delivery/csi1800-one-year.scope.json \
  --operation-id csi1800-202509-202608-bulk \
  --output delivery/csi1800-one-year.plan.json \
  --max-workers 8 --global-calls-per-minute 300 \
  --stock-basic-calls-per-minute 50
axiom-data --data-root /absolute/data run \
  --plan delivery/csi1800-one-year.plan.json \
  --token-file /absolute/private/tushare-token.txt
axiom-data --data-root /absolute/data status \
  --plan delivery/csi1800-one-year.plan.json
axiom-data --data-root /absolute/data verify \
  --plan delivery/csi1800-one-year.plan.json
axiom-data --data-root /absolute/data audit \
  --plan delivery/csi1800-one-year.plan.json --snapshot current \
  --output delivery/csi1800-one-year.audit.json
```

把示例中的绝对数据根、token 文件改成自己的路径。`prepare` 输出 `scope.json`，并将 Tushare 原 Raw 引用、供应商快照日期、稳定身份和日历固定在数据根 `operations/preparation/`。再次使用同一操作 ID 会恢复已有成功 Raw。`plan` 冻结成员 Raw、标的、日期、端点、基础 Snapshot、并发/限速/重试参数与摘要；`run` 先发布行情与基于供应商快照的成员区间，再发布七类财务/事件，最后一次性推进 `current`。Tushare 非快照日使用最近一份已观察的供应商快照；这是明确的研究口径，不是独立认证的逐日成分。执行途中可用 `status` 查看请求进度。要明确只做行情，可同时使用 `prepare --market-only` 与 `plan --market-only`。

供应商凭据只从 `--token-file`、`TUSHARE_TOKEN` 或 `TS_TOKEN` 读取。token 文件可为纯文本或含 `token` 列的 SDK CSV。CLI 不使用 SDK 主目录默认文件，不把凭据写入配置、计划、Raw 元数据或输出。`prepare` 和 `run` 是仅有的网络步骤；不要把 token 值放入 JSON 或命令参数。

`verify --plan` 逐字节验证选中 Raw、块发布链、成员来源配置和请求账目。重复或空响应若没有改变 Canonical，Raw 仍在请求日志与块检查点中；验证器重算未新增 Snapshot 引用的响应，确认没有漏发布的事实。`audit` 独立核对来源字段映射、日期、单位、范围及日历开市日缺价，结果写入 JSON。`passed`、`limited`、`failed` 是本地来源相对状态；供应商月度快照的日间延续和接收前的严格历史可见性另见成员域的来源说明。字节、映射或响应截断错误返回非零退出码。

读数时，`current` 在每条命令开始时解析一次。下面的 `query.json` 是 `QuerySpec` 参数 JSON：

```json
{
  "domain": "market_daily",
  "fields": ["close"],
  "symbols": ["cnstock.000001.SZ.19910403"],
  "sessions": ["2026-06-01"],
  "pit_policy": "best_effort_vendor_v1",
  "cutoff_by_session": {"2026-06-01": "2026-06-01T20:30:00+08:00"}
}
```

查询中的身份仅为格式示例；真实 ID 从生成的 scope 取。`best_effort_vendor_v1` 使用声明的供应商发布时间假设，不能当作严格历史 PIT 证明。

```sh
axiom-data --data-root /absolute/data inspect --snapshot current
axiom-data --data-root /absolute/data inspect --snapshot current --query query.json
axiom-data --data-root /absolute/data read --snapshot current --query query.json
axiom-data --data-root /absolute/data read --kind states --snapshot current --query query.json
```

`read --kind market`、`members`、`events` 分别调用当前 Data API；`events` 的查询文件使用 `EventQuery` 字段（如 `start`、`end`、`cutoff`、`time_field`）。

## 显式Qlib导出与读取验证

`qlib-export`仅读取已固定的本地Snapshot和查询，不请求供应商，也不推进current。配置包含同证券顺序、同开放日历的`queries`列表，以及可选`instrument_map`、`field_aliases`、`universe_query`、`universe_name`、`rtol`、`atol`。股票/ETF真实原型分别有[股票样例](../examples/qlib_stock_debug.spec.json)与[ETF样例](../examples/qlib_etf_debug.spec.json)，替换范围时一并调整固定身份、完整开放日历、预热与cutoff。

```sh
axiom-data --data-root /absolute/data qlib-export --snapshot current \
  --spec examples/qlib_stock_debug.spec.json --destination /absolute/qlib-view
axiom-data qlib-verify --view /absolute/qlib-view
axiom-data --data-root /absolute/data qlib-verify \
  --view /absolute/qlib-view --against-reader
```

默认验证manifest与完整文件闭包；`--against-reader`另逐月比较固定Query的数字、NaN和成员区间。同查询复用已验证导出，不重新读取Data；不同查询使用新目录。实际消费由Research的`QlibView`显式激活Qlib并读取原生字段，见[完整接口与教学](qlib-interface.md)。导出无需安装Qlib，实际消费需可选环境。财务/分红继续使用事件接口，不隐式前填为数字日频。

## 离线重建与搬移

离线重建可直接从选定 Snapshot 清单汇总保留的 Raw，不需手填成千上万个 batch ID。重建从已有 Raw 创建新候选，不下载、不覆写旧 Snapshot：

```sh
axiom-data --data-root /absolute/data rebuild --snapshot current \
  --operation-id offline-rebuild-001 --domain market_daily --no-promote
axiom-data --data-root /absolute/data export --snapshot current \
  --destination /absolute/transfer/bundle --code-root /absolute/path/axiom-data
axiom-data verify --bundle /absolute/transfer/bundle
axiom-data --data-root /absolute/restored-data import --bundle /absolute/transfer/bundle
```

`rebuild --request rebuild.json` 保留给需要指定 Raw 列表、domain override 或构建上下文的高级操作。`export` 和 `import` 的目标必须尚不存在；包包含所选 Snapshot 的祖先、引用的 Raw 及源码副本。`verify` 对包做逐字节离线检查。

一年验收通过后，可用 [12 年历史候选并集配置](../examples/delivery_csi1800_twelve_year.json)在独立数据根开始 2014-11-01 至 2026-09-28 的作业。该配置有新的 preparation/operation ID，不截断候选并集，也不要求它恰为 1800：

```sh
axiom-data --data-root /absolute/data-12y prepare \
  --config examples/delivery_csi1800_twelve_year.json \
  --output-scope delivery/csi1800-twelve-year.scope.json \
  --token-file /absolute/private/tushare-token.txt
axiom-data --data-root /absolute/data-12y plan \
  --scope delivery/csi1800-twelve-year.scope.json \
  --operation-id csi1800-201411-202609-bulk \
  --output delivery/csi1800-twelve-year.plan.json
axiom-data --data-root /absolute/data-12y run \
  --plan delivery/csi1800-twelve-year.plan.json \
  --token-file /absolute/private/tushare-token.txt
axiom-data --data-root /absolute/data-12y status \
  --plan delivery/csi1800-twelve-year.plan.json
axiom-data --data-root /absolute/data-12y verify \
  --plan delivery/csi1800-twelve-year.plan.json
axiom-data --data-root /absolute/data-12y audit \
  --plan delivery/csi1800-twelve-year.plan.json --snapshot current \
  --output delivery/csi1800-twelve-year.audit.json
```

这是后续自助流程，尚未执行十二年网络作业。历史并集配置按 Tushare 月度 `index_weight` 实际返回的候选代码准备行情范围；月中逐日请求在真实频率探测中为空，故不做每天三指数重复拉取。每个供应商 `trade_date` 的名单从该日起延续到下次快照，空响应不清空成员；上市与退市日期使用 `stock_basic` 的供应商字段。`previous_preparation` 可沿用旧稳定身份与已存成员 Raw，日常仅刷新上月和当月月度请求。扩大范围时仅精确匹配的本地 Raw 可复用；跨根未复制的 Raw 不会被假装存在。`symbol_limit` 只允许 `prepare --market-only` 调试。


## 大范围读取与重复回测

Query 不要求先生成磁盘 View。读取一次固定 Snapshot 得到的 DataBatch 可以在同一研究任务中复用；Data 不负责每个回测 bar 再次读取全历史。
默认每个 Reader 的缓存预算为 64 MiB，包含逐字段来源元数据；超过预算的查询仍正常返回，但不会强行留在缓存。
可显式使用 `Data(root, cache_bytes=512 * 1024 * 1024)` 增大预算，或 `cache_bytes=0` 关闭；预算按机器内存选择，不影响语义。
分月/分字段读取可控制峰值内存。调用方持有的结果可修改，不能污染 Reader 缓存中的结果。

历史成员准备之后若 Tushare 新快照出现新代码，重新 `prepare` 扩展稳定身份与候选行情范围，并以新的操作 ID 固定新计划；旧 Raw、计划和 Snapshot 保持原样。


固定 1800 的十二年工程样本见 [配置](../examples/delivery_csi1800_twelve_year_fixed1800.json)，只用于容量排期；真实历史成员与行情范围应采用历史并集配置。当前 Tushare-only 容量/时间模型（本地记录：`delivery/csi1800_twelve_year_tushare_v4.capacity.json`）估算：2014-11-01 至 2026-09-28 固定样本的行情、日历和七类事件预日历上界为 24,582 次；准备阶段另有 144 个月×3 指数的 432 次月度成员请求和 11 次基础请求，共约 25,025 次基础请求。历史并集规模在真实 `prepare` 后才知道；每多一个标的增加一次基础前十股东请求，实际开市日可降低行情与限价次数，限额拆分和重试会增加请求。300 次/分钟纯节流下界约 83 分钟，预留 2–6 小时作为工程排期而非保证。建议单根预留 5–9 GiB，含搬移与恢复副本预留 15–27 GiB。该模型未执行十二年网络请求，也没有伪造可运行计划。

旧工作根保存的中证指数官网历史档案仍是独立的交叉核对材料，不进入 Tushare-only 生产计划、成员 Canonical、退市字段或发布门槛。真实频率探测见 Tushare 月度成员响应（本地记录：`docs/tushare-membership-cadence.json`）：2026-06-12 和 06-15 三指数组为空，整月请求与 06-30 单日请求给出同一份 300/500/1000 快照。

财务三表在这个计划中各有 159 个按原公告日的 VIP 月度全市场 selector；财务指标有 52 个按报告期的 VIP 季度 selector，含 2013-07-01 起的报告期预热。普通 `fina_indicator` 文档写每次 100 行；VIP 页没有给数值行上限，因此系统保存其全部实际返回行，不自行以 10,000 行为界触发 1800 股逐一补拉。这是对观测响应的处理，不是独立完整性证明。分红按公告日每天一次全市场请求，12 年估算 4,838 次；只有实际命中供应商声明的限额才按该日期拆分。`top10_holders` 需要单股参数，故为 1800 次报告期请求。限价按实际开市日全市场请求，估算文件先用 4350 个日历日作上界。供应商参数和限制见 [财务指标](https://tushare.pro/document/2?doc_id=79)、[分红](https://tushare.pro/document/2?doc_id=103)、[前十股东](https://tushare.pro/document/2?doc_id=61)。

日常完整增量默认只回看过去 31 个原公告日；固定 1800 标的 2026-09-28 的离线选择器模型为事件 1843 个基础请求，其中三表月度共 6、指标五个季度、分红 31 个全市场日、股东 1800 个单股报告期、限价 1 个开市日。公告日更久或旧报告期修订，需要明确另建 bulk 历史刷新计划。可用 `plan --event-announcement-lookback-days N` 把日常窗口固定为 1–365 日；这不会声称覆盖任意旧公告日修改。

日常成员刷新把上一份 preparation JSON 指给 `previous_preparation`。`prepare` 请求上月和当月的三指数组月度范围，沿用旧成员 Raw 与稳定身份；收到新 `trade_date` 才更新成员区间，空响应不清空。新成员若缺 `stock_basic` 稳定身份，会在本地映射校验中被明确指出。

生产成员来源只有 Tushare `index_weight`，生产上市与退市字段只有 Tushare `stock_basic`。官网公告、交易所表和发行人 PDF 可另作交叉核对报告；差异为 warning，不改写供应商事实，也不阻断 `current`。旧官方来源 Snapshot 保留在原独立数据根，不自动迁移。

新的 Tushare-only 主入口已在统一双标的小样本报告（本地记录：`delivery/csi1800_tushare_only_v4_202606_202608.report.json`）中完成 14 域 `verify`、`audit`、重复执行不新增 Raw，以及搬移后离线重建。该次使用早先实际取得的 Tushare 原响应离线回放；新的 Raw 接收时间属于回放，不是新的实时供应商观测。真实双教程见 [Notebook说明](../notebooks/README.md)。

## 七只 ETF 日线数据

ETF 使用独立数据根与 [12 年配置](../examples/etf_rotation_daily_twelve_year.json)，不附加股票财务事件或中证成员计划。`prepare` 保存 Tushare `fund_basic`、沪深交易日历并保证起点前至少 21 个开市日；`plan` 冻结七只基金、基准、身份和预热窗口；`run` 获取 `fund_daily`、`fund_adj`、`etf_limit`、`suspend_d`、`fund_div` 与 `index_daily`。按每只基金不超过五年的区间请求，基金分红按代码获取供应商原始历史。Data 只提供数据，不计算轮动、动量、交易信号或佣金。

```sh
axiom-data --data-root /absolute/etf-data prepare \
  --config examples/etf_rotation_daily_twelve_year.json \
  --output-scope delivery/etf-twelve-year.scope.json \
  --token-file /absolute/private/tushare-token.txt
axiom-data --data-root /absolute/etf-data plan \
  --scope delivery/etf-twelve-year.scope.json \
  --operation-id etf-rotation-daily-201411-202609 \
  --output delivery/etf-twelve-year.plan.json
axiom-data --data-root /absolute/etf-data run \
  --plan delivery/etf-twelve-year.plan.json \
  --token-file /absolute/private/tushare-token.txt
axiom-data --data-root /absolute/etf-data verify --plan delivery/etf-twelve-year.plan.json
axiom-data --data-root /absolute/etf-data audit --plan delivery/etf-twelve-year.plan.json \
  --snapshot current --output delivery/etf-twelve-year.audit.json
```

此 12 年 ETF 配置尚未执行。真实 2026-06 至 08 的七基金小样本见 报告（本地记录：`delivery/etf_rotation_daily_202606_202608.report.json`）：41 份 Raw、2,921 行 Canonical，来源映射审计通过；可用[相应配置](../examples/etf_rotation_daily_202606_202608.json)、范围（本地记录：`delivery/etf_rotation_daily_202606_202608.scope.json`）和计划（本地记录：`delivery/etf_rotation_daily_202606_202608.plan.json`）在原数据根上无凭据重复执行验证。基金日线、因子、涨跌停用 `Data.read`，分红用 `Data.events`；真实稳定身份从 scope 的 `identity_map` 读取。


新的0.3.1独立安装包已用同入口完成近期和2014年股票完整源及7ETF采集、中断恢复、重复执行和来源映射审计，见[当前preflight](bulk-preflight.md)。`symbol_limit`可用于小事实样本，准备的供应商成员/上市Raw和稳定身份仍保留完整来源。
