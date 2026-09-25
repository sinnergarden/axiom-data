# Production execution and contract census

Source base: `b8fe7e0664e24c6c598d193fa404c20249139973`. Locations below refer to
that commit. AST navigation was generated over 134 code/JSON files: 959 nodes,
3,628 edges, zero LLM extraction tokens. Dynamic dispatch edges were verified
against source. The graph stays in local audit evidence; it is not a runtime
dependency. Qsys-specific trading entrypoints are not applicable to this Data
repository; its existing public Data entrypoints are authoritative.

## Actual chain

```text
bootstrap / repair / assemble_candidate -> immutable Snapshot (not admitted)
gate_a.plan_historical_views -> per-security geometry + source/PIT work PENDING
public materialize_views / CLI materialize-views
  -> resolve aliases, bind signatures, freeze config + installed code digest
  -> require identical run plan on resume
  -> SnapshotReader: validate immutable domain closure, cross-domain contracts
  -> financial_batch: operation-scoped shared preparation cache
  -> group single-security requests by family and compatible bounds
  -> validate completed artifact and declared inputs; skip valid exact-run work
  -> family batch preparation
  -> per-security builder -> output/serialization -> immutable publication
  -> load/validate newly published artifact -> save completed ref
  -> all labels complete -> VIEWS_BUILT, FULL_ADMISSION still pending
  -> gate_a.validate_terminal_evidence + full admission verifier + Reviewer Gate B
```

`validate_admission_plan` is a separate public geometry checker, not called by
`materialize_views`. Its single-anchor semantics differ from historical
per-security planning; passing one does not establish the other's buildability.
Gate A is capability/contract/request-plan evidence; Gate B is terminal evidence
and independent acceptance. Neither a plan status nor VIEWS_BUILT equals DATA_ACCEPTED.

| Family | Shared preparation | Per-security authoritative computation | Publication / reuse |
|---|---|---|---|
| adjusted_price | `views.adjusted_price_batch` reads market/factor rows for bounded symbols | `_build_adjusted_price_view`: exact anchor factor, factor PIT qualification, OHLC ratio | Ordinary Derived manifest + rows; strict Snapshot and domain refs |
| market_replay | `consumption.market_view_batch`: market/status/limits/actions | `views._build_market_replay_view`: market scope and daily replay facts | Replay rows and closure; exact manifest/input reuse |
| market_qlib | same bounded market batch | `_validate_qlib_inputs`, `_build_qlib_view`: fields, identity intervals, explicit adjusted ref if requested, float32 features | Immutable export; no dynamically resolved adjusted reference |
| financial | `financial_batch`, `financial_view_batch`, `prepared_input`: calendar, admission, financial histories | `financial_views.project`: `admit_view`, membership/industry/valuation, revision selection and derived metrics | sparse states, numeric export, bundled code, structural/source validation |
| event | `event_view_batch`: bounded request coverage, source-specific session bounds, histories | `event_views._project`: per-day cutoff, event revision selection, latest report and missing states | sparse states, numeric export, bundled code, structural/source validation |

A singleton can bypass the batch context but calls the same per-security builder.
Family dependencies are resolved from the explicit Snapshot. Qlib's adjusted
dependency, when used, must be an existing explicit View ID; this frozen plan is
unadjusted Qlib. No dependency is auto-discovered from a previous plan label.

## Failure timing census

A = known before freeze; B = operation-start metadata/artifact checks;
C = actual input reads needed; D = computed output or execution-time mutation.
The raw [failure-sites.csv](failure-sites.csv) includes all package raise/assert
sites and enclosing conditions, including ingestion and historical loaders.
The reviewed grouping below covers the five execution paths; the inventory is
not falsely labeled a completed site-by-site proof after the stop line.

| Failure family / source owner | Earliest determination | Current location / disposition |
|---|---|---|
| Unknown kind, malformed config, signature, reversed scope, unknown fields, bad PIT policy, future requested anchor | A | `_config` validates in geometry planning; materialization itself only signature-binds before freeze. Share existing request admission; do not copy its rules. |
| Identity mapping absent/ambiguous, unknown exchange, list unknown, scope outside applicability, no open sessions | A/B | planner and `exchange_sessions`; financial/Qlib/replay repeat during builder. Use authoritative identity/calendar resolution. |
| Wrong registry/schema/version, required domain absent, invalid Snapshot/domain refs, contract mismatch | B | SnapshotReader at operation start already handles broad closure. Avoid another full-closure validator. |
| Resume changed plan/code; artifact kind/ref/scope/PIT/cutoff mismatch; damaged completed artifact | B for existing outputs | `view_operation._completed_view` and loaders, currently visited in execution order. Retain immutable compatibility validation. |
| Exact factor anchor unavailable | C, one concrete key per security | `views.py:176–183`, late inside builder. All 3,579 requested keys checked; 15 missing. Resolving a different anchor is a semantic decision, not a validator fix. |
| Strict factor availability invalid; revision policy cannot prove PIT | C | `weakest_pit_qualification`, `validate_strict_decision_time`; current plan uses research_non_pit. Existing source facts must remain authoritative. |
| Qlib explicit Derived missing/wrong Snapshot, scope, policy or cutoff | B/C | `_validate_qlib_inputs`; same function already shared by build/reuse. This plan has no adjusted Derived dependency. |
| Calendar coverage differs by exchange | A/B | `exchange_sessions` is strict by exchange; Qlib/financial projection constructs union calendar. Current row-level consequences require further audit; do not assume all exchanges always agree. |
| Financial unknown universe/industry, invisible observation, invalid interval, absent classification coverage | B/C | `financial_coverage.membership_coverage` called from `admit_view` during family work. Can admit earlier through this owner. |
| Valuation exact session/security gap; financial endpoint absent | C, potentially large without index | `_require_valuation` and `_admission_inputs`. Request intervals are weaker evidence; not certified by this census. |
| Financial same-time revisions, fiscal-period/type ambiguity, non-finite derived value | C/D | PIT selectors and financial derivation. Use selected-history owner; output-derived failures stay in computation. No duplicate financial calculator in preflight. |
| Event request interval absent, lineage cycle/ref conflict, missing Raw metadata | B | `request_coverage`: lineage metadata, no Raw payload required. Current-plan interval unions checked for all event requests; full lineage not revalidated. |
| Source-specific start/end asymmetry | B | `dependency_session` caps each domain to its frozen bound. Margin ends 2026-09-10, others can end 09-11. Request coverage remains required; out-of-bound target facts remain unavailable. |
| Event simultaneous revision ambiguity, no permitted PIT evidence | C | `select_event_revisions` / `visibility_times`, currently in per-day projection; existing selectors can admit histories without materializing every View. Not globally certified here. |
| Duplicate projected day, missing sparse day, cutoff mismatch, unordered sessions, non-closing intervals | D | `view_states.encode_states` / `SparseDailyRows`. Output assertions must remain; duplicating projection to preflight them is prohibited. |
| Numeric type / float32 serialization overflow | C for direct values, D for derived values | `feature_bytes`: finite float64 need not fit float32. Real full-plan representability was not scanned; retained UNKNOWN, not claimed as a found data error. |
| Existing target conflict, publication I/O, mutation of consumed source during batch/use | B for existing conflict; D for new I/O/mutation | immutable publisher and `file_state` guards. A preflight cannot guarantee future disk space, atomic publish, or absence of concurrent mutation. |
| Gate B missing artifacts, full-admission target mismatch, daily/recovery/Notebook evidence absent | B once terminal inputs exist, after execution | `gate_a.validate_terminal_evidence`; independent Gate B remains separate and not executed. |

## Date and authority census

[date-fields.json](date-fields.json) enumerates 111 date-related field
declarations across current and historical contracts. The important distinctions
are below; old contracts are retained, not rewritten to match new names.

| Meaning | Existing field / owner | Boundary / audit conclusion |
|---|---|---|
| Civil date | calendar `session`, validation `validate_session` | ISO formatting alone does not prove a trading day |
| Exchange session | `trading_calendar.is_open`, `exchange_sessions` | Exchange-specific; validates all civil days, then selects open ones |
| Security identity interval | `list_session`, `delist_session`, `domains.market._checked_security_identity_state` | `[list, delist)`; calendar dates despite the `_session` name |
| Supplier list/delist observations | Tushare `list_date`, `delist_date` | Retained in Raw; cannot independently authorize market-row existence |
| Termination effective boundary | SSE/SZSE official termination tables, `ExchangeSecurityBuilder` | Official exclusive identity end wins; not an announcement timestamp or last tradable date |
| First/last tradable session | No separate authoritative field/policy found | First/last identity-eligible open date does not establish actual tradability |
| Security trading/suspension state | `security_status`, session suspension profiles | Explicit suspended/source-gap states; identity-active is weaker than tradable |
| Suspend/resume date | `suspend_d` evidence and `session_suspension` | Status evidence; does not require a daily trade observation |
| Observation key | daily/factor `session` | Exact recorded date; not synthesized from lifecycle end |
| Revision/economic dates | `report_period`, `announcement_date`, event effective dates | Period ranking and knowledge ranking are separate |
| PIT availability | `vendor_available_at`, `first_observed_at`, `observed_at`, `pit.instant` | Timezone-qualified instants; actual selected revision evidence matters |
| Requested View range | `start_session`, `end_session` | Closed inclusive range in View contracts; differs from exclusive identity/membership ends |
| Requested adjustment anchor | `anchor_session` | Explicit exact factor observation in current builder contract |
| Effective adjustment anchor | Not separately represented | Cannot silently replace requested anchor with previous observation |
| Source availability bound | DomainCommit `builder_config.end_session` | A source-scope bound, not observed last row or last tradable session |
| Membership interval | `coverage_from/to`, `effective_from/to` | Exclusive upper bound; admission checks `end >= coverage_to` as insufficient |

`exchange_security.v1` is the active boundary owner in this Snapshot. Its source
set is two official termination documents plus six Tushare stock-basic
exchange/status requests. It rejects missing official delist boundaries, listing
date disagreement, duplicate identities and live/termination contradictions.
It deliberately preserves differing supplier delist dates in Raw. The source
census found 17 such differences and zero unresolved identity contradictions.
No universal source reconciliation mechanism is warranted.

## Ingestion invariant

`source_client.PacedSourceClient` retries bounded transport failures and then
raises. `collect_requests` tracks attempted versus VALID_COMPLETE inputs;
`bootstrap_sources.collect_bounded` revalidates completeness before recording
accepted batch IDs and recursively handles required split scopes. Failed or
unsplittable requests fail the operation. `source_completeness._validate_raw_result`
rejects non-success and partial/truncated flags even with a success envelope.
Domain builders retain Raw-completeness admission.

Across all 18 frozen DomainCommit metadata records, no source observation was
marked non-success/incomplete by the checks performed. This is not a promise
that every accepted supplier response contains every economically expected row.
No ingestion modification is justified by this evidence. Tests exercise partial
responses, cap rejection, official-page completeness and public-builder guards.

## Incremental / restart boundary

`operations.assemble_candidate` starts from parent domain IDs and rebuilds only
explicit domain inputs (while normal dependency closure must remain valid).
`partitions.publish_partitions` deduplicates canonical object bytes; it still
groups/serializes supplied rows, so physical reuse is not zero compute.

At the View boundary, Snapshot identity is part of every manifest identity,
and financial/event Views additionally bind installed module content. Exact-run
resume can skip validated completed artifacts. A new Snapshot or implementation
requires a new plan/run; there is no operation path to rebind/reuse an unchanged
View payload. This is the bounded execution-model decision needed before
extending implementation. Old identities must not be forged or rewritten.
