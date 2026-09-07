# PR6: PIT reference and revision-preserving financial facts

PR6 owns the 23 leaves resolved from PR4 r5 in
`src/axiom_data/scope/pr6_scope.v1.json`. Its four stable-derived requirements
are single-quarter revenue/cost and TTM revenue/net income. The 15 holder,
margin/lending, moneyflow and forecast leaves remain PR7. PR6 is not D-M2
acceptance. Market value and security-capital facts retain their PR5 owners.

## Canonical representation

Four v2 contracts extend the existing DomainCommit publisher and Snapshot v3:
`universe_membership`, `industry_membership`, `financial_events`, and
`valuation_daily`. Snapshot v1/v2 and PR1–PR5 contracts remain unchanged.

`symbol` is the existing exchange-qualified security identity, validated against
the explicitly bound security-master commit. `group_id` means universe ID in
universe membership and classification-system ID in industry membership.
Industry rows also carry the supplier industry label. A View records an explicit
label-to-integer encoding; codes from different Views cannot be compared directly.

The canonical key is `(logical_event_key, revision_id)`. Financial logical events
include endpoint, security, report period and exact report type. Revision IDs
fingerprint economic identity, values and missing semantics. Different content versions remain separate rows. Each content row has a small
`observations` array with `observation_id`, `observed_at`, `source_ref`,
`revision_id` and vendor time. Identical content retains its earliest
`first_observed_at`, while every retrieval retains its own observation. A→B→A
therefore stores two content rows and three ordered observations. The selector
returns the selected `observation_ref`; derived components bind that reference.
Every RawBatch remains in commit lineage. Raw
payloads retain vendor announcement dates and update flags for inspection.

All source references, including bootstrap rows, must resolve inside the commit's
transitive RawBatch closure. Loader validation independently repeats the source
mapping from those batches, in addition to file/hash/contract validation. Builds
require explicit parent/raw/patch/contract arguments. Nonempty patches remain
unsupported and fail closed as in the inherited publisher.

## Time and membership

Membership intervals are `[effective_from,effective_to)`. Revision knowledge time
is independent of effective time. A boundary inferred from a subsequent snapshot
retains `boundary_source_ref` and uses the maximum observation time of both
start and boundary inputs. The child replays parent observation raw refs plus new raw inputs, ordered by
actual retrieval time, and reconstructs complete interval states. Later bounded
responses replace effective snapshots inside their declared request interval;
exact-date empty snapshots express exit. Each state is formed from consecutive
effective snapshot dates with the explicit final bound. State observations bind
all input raw refs, state identity and coverage bounds. The selector chooses one
complete state, so old interval versions cannot overlap the child state.
Incremental replay preserves the prior state until the later observation is usable.
An explicitly open interval is allowed; it does not certify unlimited query coverage.
An entirely empty history cannot certify coverage and fails closed. Overlaps at an operational knowledge cutoff
fail the canonical contract. Enter/exit/reenter produces separate spans.

The bounded index adapter uses historical `index_weight` snapshot dates. It does
not claim monthly snapshot dates are the actual exchange rebalance dates. The
final interval is bounded by an explicit exclusive coverage end, never infinity
inferred from the current list. The source snapshot is retained in RawBatch.
Absence outside demonstrated coverage remains unknown. Historical union accepts
explicit target bounds and lookback start; per-session membership is a separate
Reader operation. Market reads accept explicitly requested securities even when
those securities are outside a universe. Account logic is outside Data.

`bak_basic` supplies one represented civil-day industry interval. Gaps remain
unknown; neither latest industry nor stock_basic metadata fills them. The bounded
real fixture has four dates for two securities. Synthetic integration tests also
exercise classification changes.

Frozen Qsys `csi1800_pit_v2` uses inclusive ends. Reconciliation explicitly adds one
civil day to convert these ends to Axiom's exclusive boundary. Its all-history
union contains 3,601 securities; the clipped registry contains 3,131. They are
not interchangeable with a daily 1,800-member cohort.

## Source and availability decisions

The versioned `tushare_pr6.v1` profile binds every request, endpoint, source key,
field mapping, scale, date meaning and missing policy. Endpoints are income,
balancesheet, cashflow, fina_indicator, daily_basic, index_weight and bak_basic.

`ann_date` and `f_ann_date` are vendor date assertions. `update_flag` identifies a
vendor update category; it cannot order simultaneous revisions or prove when a
particular revision became public. The source has terminal-history risk.
`source_available_at` remains null. Collected historical rows are `best_effort`
with actual `first_observed_at`, not retroactively observed in their report year.

Policies:

- `operational_pit_v1` and `market_pit_safe_v1`: choose the last actual source observation at or before cutoff, then resolve its content.
- `best_effort_vendor_v1`: explicitly non-strict use of vendor dates at civil-day end
  in Asia/Shanghai. Financial/daily facts order by vendor availability, then actual
  observation time. Equal ordering with incompatible content fails. Universe
  uses the latest observed complete reconstructed history whose earliest effective
  date is visible; effective intervals still constrain each membership result.
  This deliberately permits retrospective boundary correction under best_effort.
  No intraday or preopen publication claim is made.
- `verified`: unavailable until typed public-evidence closure exists; always rejected.
- Bootstrap-hybrid policy is not implemented; unsupported policies fail closed.

Selection first filters usable versions, then selects within each logical event.
A simultaneous conflict fails rather than using row/file order. This can make a
post-observation operational query unavailable for ambiguous terminal variants;
that limitation is preferable to inventing a revision order. Adding a revision
whose actual observation is after T cannot affect operational output before T.

## Financial and stable-derived decisions

Income/balance/cashflow source amounts map to CNY. Fina indicator current_ratio is
a dimensionless ratio; debt_to_assets, grossprofit_margin and roe percentages are
scaled by 0.01. The canonical indicator fields use supplier fields exclusively.
There is no silent accounting-ratio fallback, and `roe` is not `roe_waa`.

PR4 recorded legacy fallback paths for current ratio, debt ratio, gross margin and
ROE. `supplier_field_only.v1` deliberately gives each canonical name one source.
Deleting that source produces missing, not another formula under the same name.
In particular parent-attributable equity is not silently treated as total equity
when reconstructing debt, and simple net-income/equity is not substituted for ROE.

Report types are preserved. The first stable contract accepts calendar-year
consolidated cumulative type 1. Type 2/3 standalone quarters, adjusted-comparison
types and parent-company reports stay readable as reported but do not mix into
this cumulative derivation. Unsupported combinations yield explicit invalidity.

Q1 is the selected cumulative fact. Other quarters subtract the immediately
preceding cumulative quarter of the same year/type. Missing predecessor or null
source fields produce missing, including when a later revision removes a field.
TTM requires four consecutive valid quarters. Each result retains exact source
revision refs, component periods, policy, cutoff and maximum component usability.
Net-income single quarters are internal TTM components, not additional scope.
No YoY, growth score, rank, neutralization or Feature formula is introduced.

Valuation PE/PB/PS are distinct source ratios. PE uses the supplier net-profit
denominator, PB uses net assets less other equity instruments, and PS uses latest
annual revenue. No PE/PE-TTM or PS/PS-TTM substitution is allowed. Missing loss PE
stays null; the source does not identify every null's economic reason.

## Reader, materialization and recovery

`SnapshotReader.as_of`, `financial_derived`, `members` and `historical_union` read
only validated fixed commits. `FactView(..., pr6_fact_view_id=...).read('pr6')`
reads a prebuilt materialization. `build_pr6_fact_view` freezes the financial
projection, row-level derived lineage, membership/industry, 23 numeric fields and
Qlib float32 files. `QlibViewReader` reads that exact View ID. Event/revision tables
remain in the same artifact and in SnapshotReader, without a mutable sidecar.

The v2 View records `requested_scope`, `actual_available_scope` and
`validated_scope`. Admission checks IDs, exact symbol/endpoint coverage, calendar
bounds and holes, daily valuation keys, daily classification intervals and
universe state coverage. Missing required scope raises `INSUFFICIENT_SCOPE`;
unknown IDs raise `UNKNOWN_UNIVERSE`/`UNKNOWN_CLASSIFICATION`. There is no implicit
partial or date clamping. A covered source null or unavailable financial component
is represented as a typed missing fact rather than claimed as a valid numeric fact.
FactView subset reads repeat admission against the materialized scope.

`FactView.read('pr6')` returns numeric `rows` and parallel typed `facts`, including
value, unit, validity, missing_reason, qualification, usable_at, policy/cutoff,
source/revision/observation/derived refs, component refs and quality_state.
TTM adds expected quarters and quarter components; missing reasons distinguish
missing_quarter, source_value_missing, incompatible_report_type,
PIT_component_not_visible and invalid_component. Industry metadata binds its
classification system and a versioned code-to-identity mapping in the same View.
Universe facts carry universe IDs, domain version and policy;
`SnapshotReader.membership_facts` supplies the complete query envelope even for an
empty cohort. `QlibViewReader.fact_metadata` exposes the same typed facts and mapping.
Removing the mapping invalidates the View. Archived v1 PR6 Views are not admitted
as v2 formal Views; their original frozen code and bytes remain in the prior run.

The View binds snapshot/domain refs, scope, policy, a cutoff capped at each session
end, field mapping, industry encoding and frozen implementation bytes. Its loader
recomputes source projections and compares every file, not just hashes. The
existing catalog rebuild indexes both Fact and Qlib uses of this artifact.

The validation runner reconstructs dependencies and PR6 commits from copied raw,
then Snapshot, Fact/Qlib and catalog in a fresh root with Python socket access
blocked. Source and recovery identities, logical values, PIT results and union are
compared. Frozen Qsys references remain evidence only. Final gate validation lives
in the existing evidence module and is bound to both actual roots.

Supplier documentation checked during implementation:
[income](https://tushare.pro/document/2?doc_id=33),
[balancesheet](https://tushare.pro/document/2?doc_id=36),
[cashflow](https://tushare.pro/document/2?doc_id=44),
[indicators](https://tushare.pro/document/2?doc_id=79),
[daily basic](https://tushare.pro/document/2?doc_id=32),
[index weights](https://tushare.pro/document/2?doc_id=96),
[historical basic](https://tushare.pro/document/2?doc_id=262).
