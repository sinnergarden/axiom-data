# View runtime execution map (candidate branch, 2026-09-25)

Scope: five required View families in `materialize_views`. `ONCE` means one
operation; `PER_BATCH` means at most 50 securities; `PER_SECURITY` means one
immutable View; `PER_SESSION` means one projected trading day. This maps the
current experimental branch before runtime changes are accepted.

| Stage | adjusted_price | market_replay | market_qlib | financial | event |
|---|---|---|---|---|---|
| Snapshot/domain closure | ONCE (`SnapshotReader`) | ONCE | ONCE | ONCE | ONCE |
| Full-scope admission | PER_SECURITY factor/PIT checks | PER_SECURITY source quality | PER_SECURITY request and optional adjusted ref | ONCE cached financial admission, then PER_SECURITY coverage checks | PER_BATCH coverage checks, then PER_SECURITY PIT |
| Shared input preparation | PER_BATCH market/factors | PER_BATCH market/status/limits/actions | PER_BATCH market/calendar | ONCE financial history; PER_BATCH valuation | PER_BATCH event history/daily rows |
| Business projection | PER_SECURITY date join | PER_SECURITY date join | PER_SECURITY feature encoding | PER_SECURITY × PER_SESSION full-history revision, derived, universe and industry selection | PER_SECURITY × PER_SESSION history selection |
| Publication | PER_SECURITY immutable artifact | PER_SECURITY | PER_SECURITY | PER_SECURITY | PER_SECURITY |
| Post-publication loader | PER_SECURITY source qualification traversal | PER_SECURITY source qualification traversal | PER_SECURITY structural/file check | **PER_SECURITY full business projection again** | **PER_SECURITY full business projection again** |
| Completed resume | PER_SECURITY loader | PER_SECURITY loader | PER_SECURITY loader | PER_SECURITY full business projection | PER_SECURITY full business projection |

Findings:

1. The operation creates one checked Reader, but the financial/event loaders
   reproject each newly published or completed View. This repeats the same
   business computation for integrity checking; the other three loaders do
   structural/file checks plus some source qualification reads.
2. The bounded adjusted-price path already shares complete market/factor
   traversals. Current branch experiments add analogous market/event input
   grouping and financial valuation grouping. These must be tested before the
   branch becomes formal implementation.
3. Financial `project` invokes revision selection and `financial_derived` for
   every session; it also selects full universe membership and industry state
   for every session and every security. This is the remaining dominant
   complexity shape. The current `financial_batch` saves admission/history
   preparation but does not reuse those daily selections.
4. Event historical revisions are prepared by batch but selected again for
   every session/security. The selector itself remains authoritative; an
   ordered visibility sweep can reuse prepared state without changing its
   policy.
5. Financial View through v3 copied full index membership into every security
   output. The branch's v4 shape references the same immutable Universe
   Membership commit and stores only security-specific dated rows. Other
   families contain small repeated calendars/implementation bundle, not full
   market/index fact arrays.

The 2014-10-31–2026-09-11 isolated financial sample spent 1,634 seconds on
dependency validation, then ran more than 66 minutes in build before any
artifact file appeared. It was stopped under the new single-security stop line;
the source data root and paused bulk checkpoints were unchanged. The run did
not establish a full-history output size or a per-function profile.
