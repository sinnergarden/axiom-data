# ADR-0004: D-M1 market/reference completion

Status: accepted for PR5

## Scope authority

PR5 consumes the frozen PR4 r5 `data_leaf_manifest` and
`leaf_to_migration_slice` rather than re-running the Feature census.  The
packaged `pr5_d_m1_scope.v1.json` records both input digests, the six selected
slices, all 18 selected leaves, and the PR6/PR7 exclusions.  The Data design
adds the formal `security_status`, `price_limits`, and `corporate_actions`
domains even though PR4 had represented their current Feature dependencies as
fields of `market_daily`.

## Canonical facts

D-M1 Snapshot v2 contains the original three PR3 domains plus:

- `security_status.v1`: per-session status and reason.  A missing market row is
  unknown unless explicit lifecycle or full-day suspension evidence explains
  it; status is not a `tradable` decision.
- `price_limits.v1`: limited, explicit no-limit, or unknown daily rule facts.
  These facts do not decide execution.
- `corporate_actions.v1`: versioned economic action components with distinct
  announcement, record, ex/effective, payment, and share-available dates.
  The first Tushare mapper promotes implemented cash/bonus/transfer components;
  proposals remain in RawBatch and split/consolidation are declared unsupported.
- `adjustment_factors.v1`: the positive cumulative source factor.  Missing
  values remain missing and are never replaced with one.
- `benchmark_daily.v1`: canonical unadjusted benchmark close.
- `security_capital.v1`: total and circulating shares, normalized from units of
  ten-thousand shares to shares.

Every new observation follows the independent
`tushare_dm1.v1 -> raw_batch.v2 -> domain_commit.v1` path.  Its normalized
SourceProfile digest is part of RawBatch and DomainCommit identity.  A current
terminal-history response is `best_effort`; an economic date alone never
implies verified PIT availability.  A typed SourceEvidence artifact is not
implemented in PR5, so D-M1 canonical
builders reject every `verified` row with `VERIFIED_EVIDENCE_UNAVAILABLE`.
A row with only first-observation evidence is `observed`; its source ref must
resolve to a same-domain RawBatch in the publishing DomainCommit's transitive
parent RawBatch closure and cannot claim observation before that RawBatch was
retrieved.  Publication and closure loading enforce the same membership rule;
RawBatch order in each manifest remains unchanged.

Each RawBatch is validated before endpoint payloads are aggregated.  The
validator binds endpoint/profile and collector revisions, exact requested
fields, symbol/date scope, and every payload row to that one request.  The
dividend endpoint is intentionally security-scoped: it may return all
`div_proc` states, while the canonical mapper promotes only implemented
actions under `tushare.dividend.dm1.v1`.

Snapshot v1 remains readable and composable from exactly the original three
domains.  Snapshot v2 requires exactly all nine D-M1 domains and validates that
every dependency ref is the same commit selected by the Snapshot.

## Stable adjusted price

`adjusted_price_view.v1` applies only this formula:

```text
adjusted_ohlc(t, A) = unadjusted_ohlc(t) * factor(t) / factor(A)
```

The view identity binds the Snapshot, exact market and adjustment commits,
scope, derived contract, builder revision, explicit anchor, PIT policy, and
decision cutoff.  Missing factors remain missing.  A strict decision-time view
validates every factor it actually consumes: trusted source availability must
not exceed the cutoff, otherwise conservative first observation is used;
best-effort terminal history is never accepted as strict.  Unadjusted canonical
prices remain unchanged and MarketReplayView never consumes adjusted prices.

## Read views

FactView reads one explicit Snapshot and optional prebuilt Derived ref.  It
returns the actual proved qualification and executed policy together with the
requested fields, scope, price basis, anchor, cutoff, and source/quality refs;
callers cannot upgrade those claims.  It does not build an absent Derived view.

MarketReplayView materializes unadjusted OHLC/pre-close/volume/amount, calendar,
status and missing reason, price limits, action identities, and security/rule
refs.  Its identity records post-session replay and its replay cutoff.  It
explicitly excludes execution, cash, positions, and corporate-action accounting.

QlibView v2 binds an explicit adjusted-price Derived ref when its price basis is
anchor-adjusted, including the exact anchor, PIT policy, qualification, and
cutoff.  The caller cannot override that binding and the exporter cannot choose
an anchor.  Event-shaped facts remain available through SnapshotReader/FactView
rather than being forced into qlib binary fields.  QlibView v1 remains readable.

## Publication and recovery

All canonical commits, Snapshot v2, adjusted-price view, replay view, and Qlib
view use the existing staged immutable publisher.  Reads never collect, build,
or select `current`/`latest`.  A clean root containing only frozen RawBatch v2
can deterministically rebuild all canonical identities and view identities;
catalog deletion is handled by manifest scanning.  Catalog rebuilding validates
and indexes RawBatch, DomainCommit, Snapshot, adjusted-price, MarketReplay, and
Qlib manifests through their normal loaders; the catalog remains a disposable
index rather than provenance authority.

PR5 evidence validation takes an explicit immutable data root.  It loads every
reported RawBatch, DomainCommit, Snapshot, adjusted-price view,
MarketReplayView, and QlibView through the formal loaders before comparing
reports.  Its D01-D15 checks and required refs are a fixed PR5 map; report
`PASS` values are summaries derived from those loaded objects, not authority.
D01 separately binds an explicit validation root plus old/new Snapshot refs,
manifest digests, and domain refs.  Both Snapshots are loaded by identity and
closure-validated; the old manifest digest recorded before publishing the new
Snapshot must still match.  The verifier never discovers a substitute Snapshot
by scanning a directory.

## Exclusions

PR5 does not implement PIT universe/industry, financial/valuation, holder,
margin/lending, moneyflow, forecast, Research Features, Labels, Trade, UI, or a
production current switch.  Those remain assigned to PR6/PR7 by the frozen
scope manifest.
