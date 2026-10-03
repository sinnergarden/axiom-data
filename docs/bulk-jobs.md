# Bounded local-source bulk jobs

`axiom_data.bulk_jobs` provides the durable job path for `init`, `bulk`, and
one-session `daily` scopes. A plan freezes the inclusive dates, selected source
codes, caller-reviewed stable `identity_map`, endpoint set, benchmark/index
codes, and maximum calls per chunk. Save `plan.to_dict()` as JSON; restore it
with `BulkJobPlan.from_dict()`. A changed plan needs a new operation ID.

## Default v2 trading-day batches

`plan_bulk_job` defaults to `request_strategy="trading_day_market_v2"` and
serializes as `local_bulk_job_v2`. The runner first fetches both exchange
calendars over windows of at most 144 months, six exchange × `L`/`D`/`P`
`stock_basic` slices, and each benchmark over the same long windows. It
requires explicit calendar rows for **every date and both exchanges**; a
missing row stops the job rather than being inferred closed. It freezes the
union of open exchange dates in the job checkpoint, then requests `daily`,
`adj_factor`, and `suspend_d` once per open date for the whole market.
`index_weight` remains one request per index and month. The request choices
follow the supplier's documented [daily by-date], [factor by-date],
[suspension by-date], [stock-basic exchange/status], and
[monthly index-weight] selectors.

[daily by-date]: https://tushare.pro/document/2?doc_id=27
[factor by-date]: https://tushare.pro/document/2?doc_id=28
[suspension by-date]: https://tushare.pro/document/2?doc_id=214
[stock-basic exchange/status]: https://tushare.pro/document/2?doc_id=25
[monthly index-weight]: https://tushare.pro/document/2?doc_id=96

```python
from axiom_data.bulk_jobs import (plan_bulk_job, estimate_bulk_job,
                                   run_bulk_job, bulk_job_status, verify_bulk_job)
from axiom_data.storage import LocalStore

plan = plan_bulk_job(
    mode="bulk", symbols=reviewed_codes,
    start_session="2025-01-01", end_session="2025-12-31",
    identity_map=reviewed_code_to_security_id,
    benchmark_codes=["000300.SH"], index_codes=["000300.SH"],
)
store = LocalStore(data_root)
estimate = estimate_bulk_job(plan)
result = run_bulk_job(
    store, plan=plan, client=injected_tushare_client,
    operation_id="reviewed-2025-v2", base_snapshot=pinned_current,
    max_workers=8, global_calls_per_minute=300,
    stock_basic_calls_per_minute=50,
)
progress = bulk_job_status(store, plan=plan, operation_id="reviewed-2025-v2")
report = verify_bulk_job(store, plan=plan, operation_id="reviewed-2025-v2")
```

The v2 Raw request records `request_strategy`, exact `endpoint`/`params`/
`fields`, `plan_fingerprint`, request index, attempt, and
`canonical_symbols` for whole-market equity and monthly constituent calls.
Raw retains all supplier rows. Canonical normalization selects only the frozen
reviewed codes, then checks their stable identities. Calendar and benchmark
responses have no symbol filter. The publisher validates selected Raw once
while normalizing. Capped responses remain Raw and are never published as
complete: date windows are bisected, while a capped one-day equity or
stock-basic response is narrowed to deterministic per-symbol calls. A
one-day `index_weight` response at its cap fails closed because the documented
interface has no constituent selector.

One process has at most eight source calls in flight. A shared limiter spaces
all endpoints to at most 300 calls/minute and `stock_basic` to at most 50/minute.
Workers stamp each response at actual receipt after serialization; the caller
alone appends Raw. Already completed in-flight responses are logged before a
sibling failure is raised. Successful Raw and split child requests resume from
the same operation ID. Each month is one canonical application in the normal
case; `max_requests_per_chunk` splits a month only if explicitly set below its
request count. `current` changes only after all reference and monthly chunks
complete and the pinned base is still current.

Before calendar collection, `estimate_bulk_job` reports a request range with
one call per *possible* calendar date as the upper bound. After the calendar
is saved, `bulk_job_status` reports exact planned calls, completed calls,
received Raw rows, elapsed time and phase. For 1,800 codes, one benchmark and
one index, a 2025 plan has at most 1,116 initial calls (calendar-day upper
bound), versus hundreds of thousands of v1 symbol-month calls. A 12-year
2014–2025 plan has at most 13,302 initial calls, before retries or cap splits;
the exact open-day count is frozen by the acquired calendar. These counts are
planning bounds, not supplier throughput or historical completeness claims.
`verify_bulk_job` independently checks the successful chunk checkpoints,
selected Raw hashes, final Parquet references, Snapshot ancestry and current
pointer without a source call. It reports observed-response coverage, not
independent vendor completeness.

## Complete financial and event jobs

The CLI creates a `FullSourcePlan` by default. It composes the market job with
calendar warmup, financial statements and indicators, corporate actions, holder
reports, price limits and listing-boundary inputs. Use `--market-only` only for
an explicitly limited market job. `BulkJobPlan` remains the market component;
it does not claim to collect the complete Data scope by itself.

Financial VIP sources use announcement windows (or report-period selectors
where that is the documented interface); their normalizers retain native report
keys and actual announcement dates. Provider caps trigger narrower requests.
Report-period facts are stored as events in annual Parquet partitions, never
expanded into one copy per trading day. See [event policies](local-event-sources.md)
and [performance and storage](performance-and-storage.md).

A one-session `mode=daily` full-source plan freezes a 31-calendar-day inclusive
original-announcement selector window by default (`--event-announcement-lookback-days`
accepts 1–365). The three statement VIP endpoints use at most the overlapping
monthly announcement ranges, and dividend uses one whole-market announcement-date
request per day in that window. Financial indicators retain their documented
quarter-period VIP selectors; top-ten holders require one report-period range
per selected security, because that supplier endpoint requires `ts_code`.
The latter two refresh the five-quarter report-period window without multiplying
each security by every lookback day. Price limits use the new trading session.
The frozen plan records both bounds and the exact selectors; one daily plan can
be resumed without redownloading successful requests. Corrections to an older
original announcement date, or to a report period outside the report window,
require an explicit new bulk history-refresh plan and operation ID. A short
daily window makes no claim to detect every historical vendor change. These
selectors follow the supplier's [statement](https://tushare.pro/document/2?doc_id=44),
[indicator](https://tushare.pro/document/2?doc_id=79),
[dividend](https://tushare.pro/document/2?doc_id=103), and
[holder](https://tushare.pro/document/2?doc_id=61) pages.

The complete runner stages candidates and changes `current` once after all
required phases succeed. Reuse the frozen plan and operation ID after an
interruption. Successful Raw survives normalization failure and must be reused;
a failed run opens another bounded attempt round in fresh Raw slots while
preserving prior failed receipts. A local validator correction can reclassify
saved supplier bytes at their original receipt time without another request.
Source retries and offline normalization retries are different operations.
The complete runnable small sample is
[the two-security, three-month scope](../examples/full_scope_debug_202606_202608.md).

Raw contains exact request parameters and original bytes; candidate manifests
summarize bounded request chunks rather than repeating every full request.
Large inline source profiles are compressed in the append log. The store
indexes new log lines incrementally, and repeated identical payload bytes
share one content-addressed object. Unchanged canonical facts preserve their
original first-observed time and reuse existing partition references.

Current plans accept the trading-day batch strategy only. Superseded serial
symbol-month runners and their command-specific examples are outside this
repository; old data remains readable with its saved source bundle.
