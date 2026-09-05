# ADR-0003: Real Tushare market vertical slice

- Status: accepted for Phase 1 PR3
- Date: 2026-09-05
- Scope: one fixed market slice, Snapshot Reader, and Qlib-compatible view

## Decision

PR3 proves the existing immutable artifact model with a bounded real-data run:

```text
Tushare response -> RawBatch -> DomainCommit -> DataSnapshot
                 -> SnapshotReader -> QlibView -> equivalence/reconciliation
```

The fixed scope is 20 securities (10 SSE and 10 SZSE) over the inclusive
2025-01-01 through 2025-12-31 interval. It includes two identities listed on
2025-01-03, six explicitly confirmed suspended sessions for `688981.SH`, and
multiple observed cases where `pre_close` differs from the prior canonical
row's close.

## Source boundary and PIT status

Only `trade_cal`, `stock_basic`, `daily`, `adj_factor`, `daily_basic`,
`stk_limit`, and `suspend_d` are supported. Their primary keys, units, missing
semantics, date fields, and canonical mappings are frozen in
`tushare_phase1.v1`. Every response is written first as a RawBatch containing
the original returned fields and source units. Canonical builders consume only
those frozen payloads through the PR2 publication path.

PR3 profile-bound collection publishes `raw_batch.v2`. Its normalized
SourceProfile content is SHA-256 bound to the RawBatch identity and manifest.
It includes endpoint keys, mappings, units, session and missing semantics, plus
revision/PIT declarations, while excluding formatting and unrecognized
commentary fields. DomainCommit ordered raw provenance and the Tushare builder
configuration both carry the consumed profile digest. Rebuild rejects a frozen
v2 RawBatch when the currently supplied profile has the same version text but
different semantic content.

The loader explicitly dispatches both `raw_batch.v1` and `raw_batch.v2`.
PR2-format v1 artifacts have no profile version/digest and remain readable by
closure validation and catalog rebuild. They are not rewritten. The PR3
Tushare builder and independent checker require profile-bound v2 inputs and
reject a v1 input with an explicit error.

For `trade_cal`, the builder verifies each RawBatch before aggregation: its
request names one valid exchange and the configured start/end scope, every
payload row names that same exchange, and its untrimmed date set contains
exactly one row for every natural day in the closed interval. Only validated
batches are combined, and one batch is required for every scoped exchange.
Missing boundaries, interior dates, whole exchanges, exchanged payloads, mixed
exchanges, and out-of-scope rows fail the build.

The run is `current-observed-best-effort`. `retrieved_at` records when Axiom saw
the response. A historical `trade_date`, `cal_date`, or `list_date` is a
represented/effective session, not proof that the currently returned revision
was available at that historical time. Tushare terminal-history revision and
historical availability capabilities remain unknown unless separate evidence
is later supplied. `stock_basic.list_status` remains a current observation, not
a historical security-status domain. The active-security slice has null
`delist_date`; the adapter rejects a non-null Tushare value until its exact
compatibility with the contract's exclusive boundary is evidenced.

## Real missing/suspension clarification

The real `daily` response has no rows for `688981.SH` on the six open sessions
from 2025-09-01 through 2025-09-08. Absence alone remains unknown. The frozen
`suspend_d` response explicitly marks those dates with `suspend_type=S`, so the
builder emits confirmed-suspended rows with null OHLC, zero volume/amount, and
no forward fill. Removing that evidence leaves the dates absent rather than
guessing suspension. The real fixture and regression test freeze this rule; no
market v1 contract change was required. A null or empty `suspend_timing` is the
only supported full-day form; any non-empty intraday timing is rejected rather
than promoted to a full-day suspension.

## Reader

`SnapshotReader(data_root, snapshot_id)` accepts only an explicit Snapshot ID,
validates its complete closure, and exposes:

- `trading_calendar(exchange=..., start_session=..., end_session=...)`;
- `security_master(symbols=...)`;
- `market_daily(symbols, start_session, end_session)`;
- `schema(domain)`.

Intervals are closed and returned dictionaries follow the frozen contract field
order. The Reader has no network, build, current/latest, catalog-selection, or
mtime path.

## QlibView

`build_qlib_view()` starts from a fully validated explicit Snapshot and builds
from a new staging directory. It atomically publishes a Qlib day-frequency
layout containing `calendars/day.txt`, `instruments/all.txt`, and little-endian
float32 `features/<instrument>/<field>.day.bin` files. Missing values are NaN;
the leading float is the Qlib calendar offset. Canonical symbols map explicitly
to `SHxxxxxx`/`SZxxxxxx` and lowercase storage directories.

The full-SHA view identity binds the Snapshot identity, ordered fields, ordered
symbols and closed session scope, exporter implementation/revision/config,
instrument identity-interval storage scope, and every output content digest.
There is no incremental overwrite and no dependency on a mutable universe or
installed Qlib runtime.

## Evidence

The real run published 46 RawBatches, three DomainCommits containing 730
calendar rows, 20 security rows, and 4,858 market rows, plus one Snapshot and
one QlibView. Direct/Qlib comparison covered all 4,858 keys and all 14 exported
fields, including symbols, calendar, ordering, nulls, OHLCV, `pre_close`, and
the remaining market values. It passed with zero mismatches at relative
tolerance `1e-6`.

The four-security 2025-06-10 through 2025-06-13 reconciliation produced 179
three-way equal field values, 13 Axiom/Tushare-equal values differing from Qsys,
and 32 Qsys-missing values. The 13 differences are Tushare's four-decimal
`turnover_rate` observations versus Qsys's more precise derived values. Qsys
does not store `pre_close` or `adj_factor` in the frozen panel, accounting for
the 32 missing values. Its Tushare expectations are independently derived from
frozen raw fields and the SourceProfile without calling the production builder
or its normalization/join helpers. A regression test injects a production
`volume` scaling bug and requires reconciliation to report a contract/build
failure. No contract/build bug or source drift was observed in the real run.

The complete real closure is retained read-only at
`/var/lib/axiom-data/forensic/pr3-market-slice-20260905-blocker-fix-v2`.
It contains Snapshot
`snapshot-843117ad2d44fdbb4f2ec5cdaa8aa1e324358a344e0da91e464aac7396ce5974`
and QlibView
`qlib-4bdf486c3df5f9298598218b549f061b285a5d722103e13ba6d21107e3ddfb90`;
it is forensic validation evidence, not a production current selection.

The direct/Qlib and offline rebuild reports carry the exact Snapshot and
QlibView IDs plus manifest/identity digests. The offline report also records
source and rebuilt root maps, DomainCommit refs, ordered RawBatch refs, logical
equality, and catalog rebuild status. A PR3-specific consistency check rejects
a PASS report whose refs do not match the run manifest or each other.

For recovery, only the already-published `raw/batches` closure was copied to a
new empty root. With no collector/network call, the three DomainCommits,
Snapshot, QlibView, and catalog were rebuilt. Artifact identities, canonical
rows, and direct/view equivalence matched the source root exactly.

## Boundary

The frozen Qsys parquet is read-only forensic input to reconciliation and is
never a canonical source. PR3 does not migrate Qsys, switch a production
pointer, or implement financial, holder, margin, moneyflow, industry, PIT
universe, corporate-action history, research Feature, Label, Trade, or UI work.
