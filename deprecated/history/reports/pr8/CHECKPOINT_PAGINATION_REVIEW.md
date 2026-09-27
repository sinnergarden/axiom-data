# PR8 checkpoint recovery and pagination selectors

Base: aa190103245b205b89bbf9f9be4723d33919ef42. NO_BULK_BUILD.

## Implementation

operations.py normalizes legacy state using current Raw admission. Authoritative states are PENDING, VALID_COMPLETE, NEEDS_RETRY, NEEDS_SPLIT, FAILED_TERMINAL and SUPERSEDED_BY_SPLIT. Completed and failed are disjoint projections. Upgrade is persisted before recovery; explicitly bound observed Raw cannot be replaced by new collection.

bootstrap_sources.py persists deterministic child references before execution. Successful children are reused, pending requests retain their original batch, and invalid parents are excluded from canonical inputs.

sw_source.validate_payload_scope and validate_raw_scope reuse existing request/payload rules for pagination and canonical industry admission. Each page validates profile, domain, fields and selectors before completeness. Offsets, duplicates, selector stability and terminal identity remain checked. Industry endpoints do not acquire unsupported date filters.

## Evidence

legacy_checkpoint_recovery.json records actual production recovery against on-disk legacy checkpoints and a deterministic fixture supplier, not live collection. A completed 100-row parent becomes SUPERSEDED_BY_SPLIT with child_request_count=2. Calls: 20250101–20250401 succeeds; 20250402–20250630 fails once; resume retries only the latter. FAILED → COMPLETE. Another resume makes no calls; idempotent_resume=true. Only valid child Raw IDs are admitted. Tests cover persisted reload, crash recovery, stable graph/checkpoint bytes, bound Raw zero-call rejection and invalid-parent build rejection.

Middle and terminal pages returning Y for is_new=N fail. Mapper rejects the same bad payload. Symbol/code mismatch, unsupported date parameters, invalid fields and profile bindings fail. Bad pages cannot produce COMPLETE checkpoints.

Independent Astra bounded code review: approve after fixes to durable pending recovery and explicit Raw binding. This is not bulk authorization.

Focused tests: 47/47 PASS; final recovery-focused: 18/18 PASS. Full suite: 314/314 PASS in 94.748 seconds; checkpoint_pagination_fullsuite.log.

## Gate A

Unchanged frozen probe rerun: GATE_A_BLOCKED, exit 1, bulk_authorized=false, ready_for_consumption=false. Exact evidence: checkpoint_pagination_gate_a.json. The probe covers one security, not full-market source availability.

15 failed checks remain: 13 source completeness policies, missing terminal entrypoints, and missing full-range sparse-history qualification executor. Source gaps: market_daily/daily; security_status/daily; price_limits/stk_limit; adjustment_factors/adj_factor; security_capital/daily_basic; corporate_actions/dividend; benchmark_daily/index_daily; financial_events/income; valuation_daily/daily_basic; universe_membership/index_weight; industry_membership/index_classify; security_master/SSE; trading_calendar/trade_cal.

Missing terminal entrypoints under axiom_data: full_admission.full_admission; full_admission.validate_full_admission; daily_acceptance.validate_daily_evidence; notebook_acceptance.execute_notebook_smoke; notebook_acceptance.validate_notebook_smoke.

No bulk bootstrap, baseline Snapshot, full admission, broad real daily run, final Notebook acceptance or new domain was started. V1 remains incomplete.
