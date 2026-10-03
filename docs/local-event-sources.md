# 财务与事件接源

常规财务直接读取 Tushare。完整初始化的默认端点为 income、balancesheet、cashflow、fina_indicator、dividend、top10_holders、stk_limit；供应商支持时使用 VIP 批量选择器，原返回字节先进入 Raw，随后仅为冻结证券集合构建 Canonical。

Axiom 接受供应商返回的财务内容，不建设独立的财报认证系统。接源验收核对字段映射、单位、报告键、修订选择和响应是否触顶；不会逐份查 PDF、重新计算供应商全部指标，或要求供应商历史数值绝无错误。来源数值正确性与本地转换正确性是两个问题。

历史 PIT 的限制也不等于拒绝信任供应商：今天取得的历史终态可以在声明假设的 best-effort 模式使用，但只有公告日期不能证明每次历史修订何时公开。严格模式按已有证据或实际收到时间选择；缺少历史版本时明确返回不可用，不为补满数据虚构版本。

collect_event_response 接受显式 client/endpoint/params/identity_map/next-open 日历；网络、权限和截断失败保留 Raw，不转换为空响应。实际 observed_at 在收到响应后采样。完整作业按冻结计划批量执行和恢复，调用者通常使用 [统一 CLI](cli.md)，无需自行逐股循环。

## 原生结构与单位

| 来源 | Canonical 域 / 经济键 | 口径 |
|---|---|---|
| income | financial_events / security_id + endpoint + report_type + report_period | total_revenue、parent_net_income 为 CNY；累计年内值 |
| balancesheet | balance_sheet_events / 同上 | total_assets、total_liabilities、parent_equity 为 CNY；报告期末存量 |
| cashflow | cash_flow_events / 同上 | operating/investing/financing_cash_flow 为 CNY；累计年内值 |
| fina_indicator | financial_indicator_events / security_id + endpoint + report_period | roe、weighted_roe、debt_to_assets 为百分数，不再乘 100 |
| dividend | corporate_actions / security_id + report_period + announcement_date + process_status | 现金分红 CNY/原股；送股、转增为新股/原股；实施日、登记日、除权日分别保留 |
| top10_holders | top_holders_reports / security_id + report_period | 持有人、股数和比例按整份报告组保存；修订整组替换 |
| stk_limit | price_limits / security_id + session | 未复权 CNY/share 上下限；不证明流动性或可成交 |

财务单位经过 600000.SH/2024 年报供应商值与发行人报告独立抽样：收入 170,748,000,000 CNY、归母净利 45,257,000,000 CNY，与原报 RMB million 数一致。资产负债与现金流也核对对应值。PDF 用于这一次单位核实，正常初始化/日更不下载每份财报。

财务保留 announcement_date 与 actual_announcement_date；报告期不是公告时间。每个报告年一个 Parquet 分区，修订仍属于原经济报告年，不向每个交易日复制报表。报表类型须符合字段的累计/存量合同，不能将母公司/单季/合并累计类型任意混算。

## 修订与 PIT

财务及股东来源采用 announcement_day_then_terminal_v1：先按查询 policy/cutoff 筛可见版本，再选供应商公告日；同日终态更改按实际观察顺序保留。一次响应同公告日多行，仅当其中一个实际返回行完整支配其它缺字段行时选择该行；相互冲突或互补行不拼接成供应商从未返回的记录。

供应商公告日不等于精确历史公开证据。best-effort 用固定日历的下一交易日 09:30 假设；operational 用实际第一次收到；market-safe 没有精确 revision 的原文证明时也回退 receipt。修订不会抹掉旧 Snapshot 或把 first_observed 改成公告日。

分红保留提案/批准/实施等 process_status；只选择“实施”才可解释为已实施行动，Data 不自动写入账户或调整因子。股东 supplier_report_complete 描述供应商响应所声明的完整报告组；partial/duplicate/缺公告等问题保留，不能仅因有十行就获得外部认证。top10_ratio 只有来源组语义允许时才派生，旧报告股东不混入新组。

完整分红行若有晚于原公告日的 implementation_announcement_date，整行的 best-effort 可见时间按两者较晚日期计算：股票为下一交易日09:30，ETF为当日20:00。即使只查询现金字段也应用这个边界，避免提前读到后来确定的实施细节。真实000001.SZ的一条记录原公告为2026-05-23、实施公告为06-05，06-01不可读，06-08开盘后才在此假设下可读；严格模式仍按实际receipt或精确版本证据。

## 派生与缺失

EventQuery 返回原生事件 DataBatch。single_quarter 和 ttm 是选定事件之上的纯函数，参数显式包含 endpoint、report_type、basis、unit 和目标报告期，并携带实际输入 provenance。

```python
from axiom_data import Data, EventQuery, ttm

events = Data(DATA_ROOT).events(snapshot=SNAPSHOT_ID, query=EventQuery(
    domain='financial_events', fields=('total_revenue',), symbols=(SECURITY_ID,),
    start='2025-01-01', end='2026-06-30', time_field='report_period',
    cutoff='2026-09-01T09:30:00+08:00', pit_policy='best_effort_vendor_v1',
    filters={'report_type': '1'},
))
result = ttm(events, field='total_revenue', endpoint='income', report_type='1',
             basis='cumulative_ytd', unit='CNY', periods=('2026-06-30',))
```

收入/现金流累计值可转换单季和 TTM；资产负债期末存量不能做同样的加减。缺期、撤销、不可见或不兼容 basis 返回明确不可用状态，不盲目前填或把 null 变零。查公告日期时必须先选择经济键的版本，日期修正不能复活旧记录。

## 初始化与日更

bulk 含五季度报告 warmup，获取研究区间需要的可见历史。daily 默认回看 31 天公告窗口，并保存实际公告/报告期选择器；fina_indicator 的 start/end 是报告期，不能误当公告窗口。无法按公告窗查询的来源按其真实支持方式刷新。旧公告不改日期的历史修订需显式新 bulk refresh，短窗口日更不保证发现全部旧值修改。

cap 只基于确认的来源限制或真实截断信号；不得用自设阈值制造大量逐股补拉。支持日期拆分的来源先拆日期，真正无法批量的端点才逐股，并使用共享限速与有界并发。重复响应对象复用，不变事实不写新 Canonical/Snapshot。详见 [性能与存储](performance-and-storage.md)。

真实全部七端点核对见 完整窗口验收（本地记录：`delivery/full_scope_debug_202606_202608.live.acceptance.json`），教程给出实际表格和返回结构。来源相对 passed 不证明供应商未遗漏报告或恢复了历史 vintage。
