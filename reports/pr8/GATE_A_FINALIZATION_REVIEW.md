# PR8 Gate A finalization

Base: `88128558dc4305cc3bfe904a476c654a8b8ec393`.
Scope: source completeness contracts, historical sparse coverage, and read-only
Gate A acceptance. No full bootstrap or new domain is part of this change.

## Source contracts

The machine matrix enumerates the original 13 failing source bindings from the
previous Gate A report, with their frozen 56-requirement links. It expands every
endpoint into request/payload selectors, pagination or split rules, empty-response
semantics, truncation, coverage and historical sparsity. Rules are explicit in
`src/axiom_data/source_profiles/source_completeness.v1.json`; absence or unknown
historical completeness blocks required sources.

Completeness extends the original immutable mapping profile. New Raw manifests
bind the effective profile and extension digest without changing supplier bytes.
Build config and coverage v2 bind current admission. Published coverage v1 retains
its frozen validation and identity. Current bootstrap, repair and direct v2 build
requalify inherited source inputs; an old successful validation cannot promote
an input that fails today's contract. Paginated observations remain grouped by
one commit's ordered inputs, including on historical parent chains.

## Historical sparse coverage

`historical_sparse.py` reuses the existing collector checkpoints and deterministic
split graph. Plans use source-supported security/range or snapshot requests;
dividend is security-history-only and cannot invent date-window splitting.
The requested source interval is distinct from the economic target interval.
Stock listing snapshots do not substitute for historical delist boundaries.

Complete parent coverage requires exact child interval closure and current
admission of every leaf. Missing, failed, unsupported or truncated leaves prevent
promotion. Complete empty events retain their declared source semantics and
enter DomainCommit coverage identity. Repeated resume revalidates checkpoints
and does not collect already successful leaves again.

## Readiness and acceptance boundary

Public CLI: `plan-gate-a`, `validate-gate-a`, `gate-a-completeness-matrix`.
Python API: `make_gate_a_plan`, `validate_gate_a`, `validate_gate_a_report`.
The report binds code revision and installed implementation bytes, SourceProfile
and policy digests, planner/config identity and validator version. Revalidation
recomputes the current result. Reviewed executable route digests supplement the
call inventory, so a dead-code validator name is insufficient.

Gate A validates bootstrap readiness; Gate B validates actual produced artifacts.
Future evidence schemas are defined now. Missing full-admission, daily-evidence
and Notebook execution capabilities remain explicit `CAPABILITY_BLOCKED` and
cannot pass final V1 acceptance. No future baseline ID or executed Notebook is
required to demonstrate Gate A readiness. Best-effort historical PIT and qualified
SW gaps remain visible; this report does not assert verified historical knowledge.

## Independent code review

Independent Astra review approved the final direct-build requalification guard,
invocation-local verification reuse and regression coverage. Final artifact
validation and publication evidence are recorded below when complete.

## Final tests

Luna executed the frozen r6 implementation: focused tests 23/23 PASS in 12.913
seconds; full unittest suite 348/348 PASS in 131.631 seconds. Logs are retained as
`gate_a_finalization_focused.log` and `gate_a_finalization_fullsuite.log` in this
directory. The suite includes missing-policy, missing-terminal-page,
missing-sparse-executor, public-bypass and unknown-history destructive tests,
legacy recovery, pagination selectors, v1 compatibility and direct v2 inherited
source requalification.

## Validated planning and sparse artifacts

The full frozen target is 3,601 securities over 2014-01-01 through 2026-09-08.
The planner produced 299,037 requests across 15 source-domain groups, bound to
56 Data requirements and 469 Feature dependencies. These are planning bindings,
not executed full-data admission. The single-security sparse readiness plan has
11 requests, rather than a daily scan.

`gate_a_completeness_matrix.json` is the complete machine-generated 13/13 PASS
matrix; `gate_a_finalization_plan.json` freezes the target and contract.

| Source/domain | Endpoints | Result |
| --- | --- | --- |
| market_daily | daily, daily_basic, adj_factor, stk_limit, suspend_d | PASS |
| security_status | daily, suspend_d | PASS |
| price_limits | stk_limit | PASS |
| adjustment_factors | adj_factor | PASS |
| security_capital | daily_basic | PASS |
| corporate_actions | dividend | PASS |
| benchmark_daily | index_daily | PASS |
| financial_events | income, balancesheet, cashflow | PASS |
| valuation_daily | daily_basic | PASS |
| universe_membership | index_weight | PASS |
| industry_membership | index_classify, index_member_all | PASS |
| security_master | SSE, SZSE | PASS |
| trading_calendar | trade_cal | PASS |

`gate_a_sparse_empty.json` retains the deterministic forecast fixture result:
one source call, unchanged empty Raw payload, complete sparse coverage,
idempotent resume and zero domain rows. Its actual immutable DomainCommit is
`forecast_observations-47fe397750cadfc65026abe8af3f138996c6dde808d0017ff000e277bf04d19e`.
This is a bounded executor proof, not a live supplier history claim.

The precommit Gate A run returned `GATE_A_READY_FOR_BULK_BUILD`, no findings,
with independent Astra approval of the code and artifact bindings. Its report
is retained outside the repository under
`../review/pr8-gate-a-precommit-r6-20260912/`; it binds the base Git revision and
the exact tested new implementation bytes, and must not be used as evidence
for a later commit. Final evidence is generated after commit under
`../review/pr8-gate-a-<full-commit-sha>/` and independently replayed through
`validate_gate_a_report`. The final delivery identifies that exact directory.

Gate B still explicitly lists five missing capabilities: full-admission
producer and validator, daily-evidence validator, Notebook smoke producer and
validator. This does not assert V1 acceptance. No full bootstrap, baseline,
full admission, broad daily execution or final Notebook was run in this task.
