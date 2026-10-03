# Tushare 来源与观察语义

统一入口是 `prepare → plan → run → verify → audit`。`plan` 默认包含已启用的行情、财务与事件；`--market-only` 明确限制为行情。请求按来源支持的批次执行，共享限速，Raw 先于规范化落地。完整小样本命令见 [三个月调试](../examples/full_scope_debug_202606_202608.md)，调度与恢复见 [批量作业](bulk-jobs.md)。

每次请求保留 endpoint、参数、字段和实际接收时间。相同字节只保存一份内容，观察日志追加；同值事实不产生无意义的 Canonical 修订。恢复先复用已成功 Raw，再继续未完成请求。成功空响应只表示供应商此次没有返回数据，不能直接解释为停牌、非成员或财报为零。

## Vendor interpretation

- [Tushare A-share daily](https://tushare.pro/document/2?doc_id=27) is
  unadjusted, excludes suspended sessions, and documents a 6,000-row call cap.
  `vol` is in hundred-share lots and `amount` in thousand CNY, converted by
  declared factors 100 and 1,000. Whole-market daily requests are the default; a capped response is retained and split before publication.
- [Stock basic](https://tushare.pro/document/2?doc_id=25) documents a 6,000-row
  cap, 2,000-point permission, and up to 50 calls per minute. The market plan requests exchange × `L`/`D`/`P` slices. Its `delist_date` remains the supplier value. The listing-event
  domain uses it as the exclusive end of the declared listing interval;
  this is a supplier-date policy, not independent legal certification.
  Outside comparisons are warnings and do not replace or gate vendor facts.
- [Trading calendar](https://tushare.pro/document/2?doc_id=26) gives explicit
  `is_open` 0/1 rows for SSE and SZSE. Missing dates are unknown, not closed.
- [Adjustment factor](https://tushare.pro/document/2?doc_id=28) is a supplier
  cumulative factor. Ratios require a pinned anchor; absent factors are not 1.
- [Daily suspension](https://tushare.pro/document/2?doc_id=214) documents a
  5,000-row cap and S/R types. Only a full-day row (empty
  `suspend_timing`) becomes a definite session status. Intraday values are
  retained with `is_suspended=null` and a partial-session reason. Multiple S/R
  events on one security/date are reduced to a single daily summary, with sorted
  timings retained; mixed full-day S/R without timing remains unknown. These are
  events, not competing revisions. Original rows stay intact in Raw. Missing rows
  make no status assertion.
- [Index daily](https://tushare.pro/document/1?doc_id=95) supplies index-point
  close under a qualified index code and requires at least 2,000 points.
- [Index weight](https://tushare.pro/document/2?doc_id=96) supplies dated monthly
  groups. Daily research carries the latest visible group with trade_date on/before
  session. No month-end backfill; no prior group means unknown. Empty responses
  retain the previous group. Actual snapshot date, research session and receipt
  are separately preserved. Count differences and possible intramonth omissions
  are warnings, not official-source gates. Stable-ID mapping and known capped
  requests remain Axiom's responsibility.

The Tushare pages describe current interface permissions and update schedules,
not revision-bound historical public timestamps. The market profiles declare
`terminal_observation_v1`: distinct content observations are ordered by
*Axiom's actual receipt times* for best-effort terminal history. A repeated
value after an intervening change is a new observation. The vendor does not
provide a revision sequence in these responses. `source_available_at` remains
null, and no release-time assumption is written into it. Strict historical visibility cannot be inferred from terminal responses.
Optional revision-bound evidence can establish earlier market visibility; its
absence does not block normal supplier collection or best-effort research.
For `best_effort_vendor_v1` market reads, adapters use 20:00
Asia/Shanghai on the represented event/session date as an exploration-only release
assumption. Membership uses effective_from and identity uses listing_date;
calendar/status daily rows use their session. It does not change the system
observation timestamp or claim historical vintage proof.

Keep a stable, caller-reviewed ticker-to-security-ID map for updates to the
same domain. Appending previously unbound codes is allowed when every existing
binding and all other profile semantics stay identical; it does not require
rebuilding historical partitions. Reassigning or removing existing bindings
requires an explicit rebuild/migration. Permission errors are checkpointed by exception type and a constrained numeric/transport
code; arbitrary supplier error text and credentials are not logged.


## 财务与历史公开时间

财务等事件的合同见 [事件来源](local-event-sources.md)。其公告日修订政策独立于日行情的终态观察政策。财务采集以 Tushare 为来源；一次接源单位抽样可参考发行人报表，但普通更新不下载 PDF，也不要求逐份公告认证。供应商日期假设与精确版本公开证据分开，见 [公开证据](public-evidence.md)。

凭据位于代码和数据包之外。`TushareHttpClient(token_file=...)` 或 CLI `--token-file` 指定本地文件，优先于环境变量；不记录 token 或任意供应商异常正文。默认共享速率上限为每分钟 300 次，stock_basic 另有更低限额；实际权限、响应时延与源限制可能进一步降低吞吐。

新端点依 [接源演进](source-evolution.md) 处理。只取已明确启用的研究字段，不自动抓取全部新增接口。旧行情报告是其当时范围的证据，不等于当前完整域验收。

ETF日线轮动的基金源、21交易日预热与开盘执行约定见 [ETF合同](etf-rotation-data.md)。正常作业不调用模型，恢复使用同一冻结计划，不依赖agent。
