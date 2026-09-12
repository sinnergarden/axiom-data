# Source response completeness admission

Raw storage retains the exact immutable observation. Before financial mapping,
the shared `source_completeness.validate_raw_completeness` gate applies the
effective endpoint policy. Collection, observed-Raw resume and the builder use
the same policy; a successful Raw transport status does not establish admission.

`tushare_fina_indicator.v1` already declares a response limit of 100. Its frozen
profile and digest remain unchanged. This endpoint authority also applies to
indicator observations using the earlier `tushare_pr6.v1` mapping profile:
changing the legacy binding does not avoid the limit. A response of 99 rows
passes the cap check; 100 or more fails. No indicator profile declares usable
pagination, so `offset`, invented page metadata, or an unverified terminal flag
cannot establish completeness. This check does not prove full historical coverage
or PIT suitability. No 100-row cap is inferred for income, balance sheet or
cash-flow endpoints.

The bootstrap source planner bounds indicator requests by report-end quarter.
When a response still reaches 100 rows, the collector retains its Raw identity
and raises `SourceCompletenessError` carrying `raw_batch_id`. The failed run
preserves that identity for deterministic revalidation. The explicit
`plan_truncated_raw_split(raw)` planner returns two adjacent date scopes and
`NEEDS_COLLECTION`. The formal `collect_bootstrap_sources` collection stage
executes these children using deterministic child run identities and request
checkpoints, recursively splitting capped responses. Resume revalidates the
same retained parent and completed children; it collects only unfinished children.
A single-day scope at the cap is blocked pending source qualification. Terminal
`completed_raw_batch_ids` contain admitted leaves, excluding truncated parents.
Every child must independently pass admission before a canonical build can use it.

The shared policy reads limits declared by the existing source profiles.
Unknown profiles and profiles without declared caps return `unestablished` and
`complete=false`; they must not be presented as completeness evidence. The
existing industry qualification profile declares limit/offset pagination. A
single page returns `page_series_required`, `complete=false`, leaving the
existing cross-Raw batch validator responsible for the page closure. When the
shared validator receives explicit verified Raw page evidence, it checks bound
scope/profile, contiguous offsets, duplicate rows and a terminal short page.

Admission returns a stable JSON projection containing source profile, effective
policy reference/digest, actual row count and completeness qualification. It
never includes payload bytes. Existing immutable snapshots are not rewritten;
new builder identities bind the completeness implementation.

## Per-page selector admission

Every industry page now uses `sw_source.validate_raw_scope`, also called by the
formal industry observation/mapper path. It binds the actual profile, endpoint,
domain, requested fields and Raw request, and reuses `validate_request` plus
`payload_issues` for row schema and selector semantics. A response with is_new=Y
cannot satisfy is_new=N, including a short terminal page. Security and industry
selectors are checked for every row. Real membership endpoints do not support
date request filters; invented start/end selectors are rejected.

Only validated pages can establish a contiguous, nonduplicated series with a
bound terminal page. A selector cannot change between pages. An invalid page
raises SourceCompletenessError with that page's Raw ID and cannot become a
complete request checkpoint. Collection still retains the original Raw bytes.
