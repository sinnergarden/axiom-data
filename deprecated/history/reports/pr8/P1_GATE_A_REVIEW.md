# PR8 coverage, public scope and payload admission review node

Review base: `cb20745d7ac9a6946757abf4b31986fff7f53240`, the approved I/O PR merge
on `phase1/v1-operational-release`. Review the subsequent P1/Gate A commit
identified in the delivery message. No PR8/main merge or bulk build is authorized.

## Changes and invariants

- `source_coverage.py` and `artifacts.py`: immutable observation projection and compact coverage lineage; NO_CHANGE requires observation coverage as well as canonical rows. Exact Raw replay is stable; a new observation gets a new identity even with identical rows. Loader independently recomputes coverage. Enabled builds bind admission/scope module digests.
- `public_source_scope.py`, `operations.py`, `artifacts.py`: explicit public allowlist for adjustment_factors/security_capital, own-exchange calendar and identity checks, Raw/request envelope and open-session coverage, shared direct/operation/loader checks. Daily includes explicitly supplied earlier Raw in its requested envelope. Existing D-M1 date-scope and conflicting reobservation rules remain.
- `source_completeness.py`, `pr6_source.py`, `bootstrap_sources.py`: profile-specific pre-mapping admission, including manually stored Raw and resume. Indicator 100-row cap applies to current and legacy bindings; 99 passes the profile cap check. Original Raw is retained before rejection. Bootstrap recursively executes deterministic split scopes using durable checkpoints; only admitted leaves enter the completed Raw set. Real industry pagination requires a bound, contiguous terminal page series; every page rejects partial/truncated metadata. Official SSE JSON/SZSE XLSX retain their existing authority parser.
- `gate_a.py`, `scope/pr8_gate_a.v1.json`, CLI/public exports: source-backed requirement and planner bindings, readiness checks, per-security historical anchors and future terminal evidence contracts. Missing policies or formal producer/validator entries explicitly BLOCK. No future baseline artifact is required for this readiness check and no Gate B result is claimed.

## Regressions for the independent reviewer

1. Empty COMPLETE extends PR7 request coverage while canonical holder rows remain equal. Old Snapshot remains insufficient for the new session. Exact replay, offline rebuild and rehashed false coverage are checked.
2. Public bootstrap/direct/daily/repair genesis builds produce identical domain IDs. Explicit-parent single-security replay through direct/daily/repair preserves the complete parent state and yields identical IDs. Existing one-day incremental tests remain. Wrong domain/key/date/security/exchange, request widening and a self-consistently rehashed out-of-scope manifest fail.
3. Manually published 100-row financial Raw is rejected by bootstrap, repair and daily observed-Raw input; no financial commit is created. Retained Raw bytes remain intact. Actual profile pagination and automatic split/resume/unsplittable-day paths are covered.
4. Gate A blocks after removal of a required profile/planner/completeness policy or a source-plan interval. The CLI uses the public services and leaves the data root untouched. Historical plans preserve the target and retain identity exclusions for disjoint security lifetimes.
5. The corporate-action reobservation regression now expects a distinct observation identity, unchanged canonical rows/first evidence, and exact-replay NO_CHANGE. The synthetic 2,501-node traversal fixture includes its required builder_config; traversal assertions are unchanged.

Primary tests: `test_source_coverage.py`, `test_public_source_scope.py`,
`test_source_completeness.py`, `test_bootstrap_sources.py`, `test_gate_a.py`,
`test_gate_a_cli.py`, `test_undated_actions.py`, `test_v1_lineage.py`.

## Scope of evidence

The checked-in Gate A probe is one security over 2014-01-01–2026-09-08 with
financial lookback from 2013-01-01. It checks all 56 code/source bindings and the
unchanged 469 registry, not 3,601-security payload admission. The source/profile
and missing formal-capability findings are real blockers, not requests to create
bulk artifacts during review. Full V1 baseline/admission/daily/recovery/Notebook
acceptance remains outstanding.

Internal review was split by code ownership: the Gate A author independently
reviewed root's coverage/public-scope/CLI changes and the shared-source boundary
fixes; root independently reviewed the shared admission and Gate A implementation.
A separate source author review covers the Gate A module. These bounded reviews
are not the user's final Gate A approval.

No full history acquisition, baseline promotion, production daily simulation,
Gate B or final Notebook acceptance was executed. Approved SW2021 authority,
anomaly mapping, delist boundary and prior recovery/I/O work were reused.

## Validated result — 2026-09-12

- Luna full suite: **291/291 PASS**, 93.744 seconds.
  Command: `PYTHONPATH=src python3 -m unittest discover -s tests -v`.
  Durable log: `reports/pr8/p1_gate_a_fullsuite.log`.
- Independent bounded Astra review of root/public/source fixes: APPROVE.
  Independent bounded Astra review of Gate A module by the source author: APPROVE.
  These reviewers did not approve their own implementation ownership.
- `git diff --check`: PASS. Final committed `git show --check` is checked after commit.
- Actual CLI: `PYTHONPATH=src python3 -m axiom_data.cli --data-root /home/liuming/workspace/axiom/data plan-gate-a --scope reports/pr8/gate_a_probe_scope.json`;
  feed its plan to the same CLI's `validate-gate-a --plan`.
- Gate A probe: **GATE_A_BLOCKED**, expected CLI exit 1; 56 requirement bindings.
  See `gate_a_probe_plan.json` and `gate_a_probe_result.json`.
  Thirteen source-policy checks lack an established completeness policy.
  Full-admission/daily-evidence/Notebook formal capabilities and sparse-history
  admission execution remain missing. These require code/source qualification,
  not baseline artifact generation to make Gate A appear ready.
- `bulk_authorized=false`, `ready_for_consumption=false`, Gate B `NOT_ASSESSED`.
  No baseline promotion or new collection/build was launched.
