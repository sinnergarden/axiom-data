# Axiom Data V1 operational checkpoint

Decision: **Axiom Data V1 baseline rejected**. The requested terminal outcome is
incomplete. Stage 2/5 has a tested code checkpoint; stage 3 source work exposed
two unsupported source-to-contract cases. Under request section 1, construction
stops at those blockers. This report records partial delivery, not V1 acceptance.

## Source blockers and continuation gates

The exact source refs, scope hash, affected rows and examples are in
[source_blockers.json](source_blockers.json). These are fresh supplier
observations, not Qsys canonical truth. Scope uses the frozen PR4 historical
union (3,601 securities), as stated during execution.

1. **Delisted security identity boundary:** 228 required securities have non-null
   Tushare `delist_date`. For example, `000005.SZ` has `20240426`. The public
   BuildApplication with security_master.v1 raises
   `PR3 cannot promote an unverified Tushare delist_date boundary`.
   The accepted contract requires an exclusive delisting-effective date;
   SourceProfile tushare_phase1.v1 and ADR 0003 explicitly leave source boundary
   compatibility unverified. This demonstrates unsupported expanded source
   coverage, not proof that the supplier date is wrong. Continuation requires
   boundary evidence, a versioned source mapping decision, fixture and validation.
2. **Null industry observation:** 568 required-symbol supplier rows have no
   classification. Raw diagnostic comparison with stock_basic gives 158 before
   list_date, 375 for currently listed securities on/after list_date, and 35
   requiring delisted-boundary evidence. In particular `001289.SZ`, listed
   `20220124`, has `industry=null` on that date. The accepted mapper raises
   `industry classification missing`; industry_membership.v2 requires a non-null
   industry_id. Continuation requires an explicit missing-observation contract
   or additional source evidence, with versioned fixtures and validation. These
   diagnostic categories do not claim canonical lifetime eligibility.

The repository fixture contains exact projected source rows and original raw
manifest refs. Two offline regression tests preserve both rejection gates.
No business semantics were relaxed. Saved RawBatches remain available for reuse.

## Requested 24-item delivery inventory

1. **Branch / commit:** `phase1/v1-operational-release`, based on approved PR7
   `95efe78b734ebf9783c7a59d3b81c19a8abb445e`. The checkpoint commit containing
   this report is identified in the accompanying response. Local main and
   origin/main were equal at `e23d8234dd110afd2972cc5647bfea14df4b019b`.
2. **Bootstrap API / CLI:** public `collect_requests` and `assemble_candidate`
   provide resumable request collection and explicit-ref candidate construction.
   Thin CLI `collect --run-id --plan` exists. The complete bootstrap operation
   through required Views, full admission and promotion is not implemented.
3. **Daily API / CLI:** complete daily planner, dry-run and publication lifecycle
   are not implemented. Source request schemas distinguish economic scope and
   availability policy, but do not yet implement a daily scheduling policy.
4. **Repair / inspect:** public `inspect_snapshot`, `resolve_snapshot`, and
   `compare_pr7_projection` exist; CLI supports inspect and rebuild-catalog.
   Repair is not implemented. Candidate clean-lineage assembly is a building
   block, not a completed repair operation.
5. **Actual bootstrap scope:** intended storage 2014-01-01 through 2026-09-08,
   PR4 union 3,601 securities. Actual preflight: supplier L 5,558 / D 339 / P 0
   security rows; SSE and SZSE each 4,634 calendar rows; one-security market
   probe for 600036.SH. Bulk industry raw collection spans 2,596 planned open
   dates in 2016-01-01 through 2026-09-08. Supplier documentation begins in 2016;
   actual first nonempty date is 2016-08-09. This is not complete-domain history.
6. **Baseline Snapshot:** none. No full-union security master or baseline
   candidate was published. The Notebook's explicit PR7 fixture Snapshot is
   not a V1 baseline.
7. **56/56 admission:** V1 full-scope admission has not run. Previous bounded
   PR7 admission is not extended by this checkpoint.
8. **PIT qualification:** source observations retain actual retrieval times and
   profile bindings. Historical source data remains best-effort/bootstrap;
   canonical full-scope qualification statistics are unavailable.
9. **Partitions:** opt-in domain_commit.v2 complete immutable maps; session
   facts/calendar by month, financial by endpoint/report year, holder/forecast
   by report year, membership/industry by effective year, security by exchange.
   Unchanged object bytes are reused. v1 loader semantics remain supported.
   Policy is preliminary: eager full-state reads/builds remain; real full-root
   daily memory and I/O validation is still required.
10. **Bootstrap performance:** industry source phase: 2,596 RawBatches,
    10,712,417 rows, zero collection failures, 778,297,070 raw tree bytes.
    First/last observation span 559.599 seconds; independent raw validation
    took 12.681 seconds. There are 2,424 nonempty dates and 172 empty responses.
    Peak memory and complete bootstrap wall time were not measured. One-security
    market probe produced 3,085 rows and 153 monthly objects; SSE calendar
    probe produced 4,634 rows and 153 monthly objects.
11. **Daily performance:** not measured on a completed real baseline. Small
    partition fixtures verify unchanged-object reuse; they do not establish
    production daily scaling, touched-partition counts or bytes read/written.
12. **T+1 margin:** no full-root daily example executed.
13. **Late revision:** accepted PR6/PR7 regression suite remains passing;
    requested full-root V1 daily revision run has not executed.
14. **No-change:** opt-in equal-state parent reuse has a passing fixture.
    Full daily NO_CHANGE outcome has not been implemented or accepted.
15. **Lineage:** iterative postorder closure passes a 2,501-node synthetic
    stress test, repeated-ref cache check, cycle detection and parent-integrity
    failures. The synthetic test mocks artifact loads. Exact Snapshot/catalog
    performance across a year of real commits remains unmeasured.
16. **Filesystem readonly:** public writer uses one root lock and seals new
    published files 0444/directories 0555. All 2,596 industry RawBatches were
    independently checked read-only. Loader digest/closure checks remain active.
    Complete baseline closure permission validation remains pending. POSIX mode
    bits reduce accidental writes; they do not establish distinct user isolation.
17. **Catalog recovery:** existing public rebuild_catalog and thin CLI remain.
    Full V1 baseline recovery has not run.
18. **Offline full recovery:** not run; no baseline closure exists to restore.
19. **Notebook:** `notebooks/data_acceptance.ipynb`; public read-only APIs for
    market, PIT, financial components, other facts, direct/Qlib and inspection.
    Six code cells passed a network-denied Python smoke against concrete PR7
    fixture refs. This was not a Jupyter kernel run. Full-baseline smoke and
    actual reconciliation panel remain pending.
20. **Reconciliation:** full current-source/Axiom/frozen-Qsys comparison has not
    run. The source counterexample report is not a reconciliation certification.
21. **Disk usage:** industry raw tree 778,297,070 bytes; frozen execution bundle,
    plan/log/validation tree 2,245,459 bytes; preflight tree 4,889,700 bytes at
    checkpoint measurement. These are separate measured trees, not a forecast
    for the missing full bootstrap.
22. **Source limitations:** delisted boundary and null industry blockers above;
    industry has unavailable early history and interior empty responses.
    Terminal-history observations do not prove revision-specific historical
    availability. Full requested source scope remains unvalidated.
23. **V1 acceptance:** **rejected / incomplete**. Full bootstrap, 56 requirements
    admission, daily, offline recovery and final Notebook acceptance are required
    before a baseline can be accepted. No current pointer was created or moved.
24. **Scope:** work is confined to axiom-data. No Research/Core/Trade/UI,
    production systemd deployment or old SysQ modifications. No merge or push.

## Reproducibility and remaining engineering work

Industry run registry:
`/home/liuming/workspace/axiom/data/operations/v1-industry-bootstrap-20260909-r1/collection.json`.
Frozen source, request plan, code manifest, collection log and independent
validation report:
`/home/liuming/workspace/axiom/data/operations/v1-source-20260909-r1/`.
Run COMPLETE means collection stage completion only. All 2,596 exact raw refs
were independently loaded and checked against their bound requests/profiles.

The complete operation lifecycle, candidate resume plan-binding hardening,
complete configuration validation, full-scale reading/building costs, golden
certification and reconciliation services still require work. This commit is
an implementation and source-evidence checkpoint, not production readiness.

Checkpoint test command:
`PYTHONPATH=src python3 -m unittest discover -s tests -v`.
Full-suite result and Git whitespace verification are recorded after the run in
the accompanying checkpoint validation record.
