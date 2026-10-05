# Bulk readiness audit — DESIGN_DECISION_REQUIRED

Worker evidence against `b8fe7e0664e24c6c598d193fa404c20249139973`.
Snapshot: `snapshot-1d69dc236a358f1627ae91080c33cf993b9e2acb55bbd2c2cb7a37129ce6b5a5`.
Frozen operation: `v1-full-views-20260925-b8fe7e0-1d69dc-r1`.
The operation remains stopped at 2,002 publications. Do not resume or merge this PR.

This is a stop-line audit deliverable, **not execution-model closure**. No product
semantics were changed. The requested zero-BLOCKED/zero-UNKNOWN acceptance is NOT
MET. An independent Reviewer has not reviewed this change.

## A. ROOT CAUSE

The production planner `gate_a.plan_historical_views` computes the intersection
of exchange-open dates and `[list_session, delist_session)`, then uses its last
element as both requested end and adjustment anchor (`gate_a.py:736–748`). The
registered policy explicitly specifies
`last_eligible_target_session_per_security.v1` (`scope/gate_a.v3.json:935`).
Identity eligibility is therefore being promoted into a requirement for a
factor observation, although `security_master.v1` expressly distinguishes the
exclusive identity boundary from the last market row.

The failure survives preflight because the checks prove different things:

* Historical planning returns `GEOMETRY_DEFINED`, with source availability PENDING.
* `validate_admission_plan` checks geometry and reference closures. It requires a
  common semantic anchor across shards; it is not a readiness verifier for this
  historical plan's per-security anchors.
* `materialize_views` freezes kind/config/signature and code identity, validates
  the Snapshot, and immediately enters batches. It never admits every request's
  deterministic prerequisites first.
* The last E2E preflight exercised ten securities per family, all with the common
  2026-09-11 end. Its 50/50 success cannot prove 17,895-plan applicability.
* Anchor existence is finally required inside `_build_adjusted_price_view`.
  Financial admission and event request coverage likewise run during family work.

This is a contract/admission gap, not evidence that network timeout lost a row.

## B. EXECUTION MODEL

[Execution and date map](execution-model.md) traces the public entry, five
families, publication, resume and Gate B. Its A/B/C/D table distinguishes checks
that could move earlier from output assertions that must stay in builders.
**No check was moved in this PR**, because the findings below cross the supplied
stop line. The attached scripts are labeled reproduction evidence and do not
create an alternative production planner or readiness authority.

## C. GLOBAL CENSUS

All frozen labels appear exactly once in [plans.csv](plans.csv).
[summary.json](summary.json) binds the original plan SHA-256 and Snapshot.

| Result | Count |
|---|---:|
| TOTAL | 17,895 |
| READY, certified | 0 |
| NOT_APPLICABLE, certified within this plan | 0 |
| BLOCKED | 15 |
| UNKNOWN / prerequisites not fully assessed | 17,880 |

Each family contains 3,579 plans. Adjusted price has 15 BLOCKED and 3,564 UNKNOWN;
the other four have 3,579 UNKNOWN each. This does **not** assert those UNKNOWN
plans will fail. The prior 2,002 publications were not revalidated here and are
not silently promoted into readiness certificates.

The 15 confirmed impossible anchor requirements are `PLAN_SEMANTICS_ERROR`:
600069, 600074, 600086, 600175, 600190, 600240, 600555, 600614, 600634, 600687,
600695, 600701, 600747, 601268 and 601558, all `.SH`.
All lack both daily price and factor on their requested anchor. Their frozen
status is `unknown_source_gap`; that stored label is **not** a new audit judgment
that the observation should exist. See [anchor-evidence.json](anchor-evidence.json).

No COVERAGE_GAP was found in the event accepted-request interval check. No
ACTUAL_CORRUPTION was found in the bytes actually checked. LEGITIMATE_ABSENCE,
NOT_APPLICABLE and economic causes of missing observations are not inferred from
missing rows. SOURCE_CONFLICT/AMBIGUOUS is not automatically assigned to the 17
source disagreements: their identity authority is already resolved. Remaining
row/PIT/revision/financial coverage checks retain UNKNOWN after the stop line.
This is a full-label census with explicitly incomplete prerequisite coverage,
not a completed exhaustive failure-condition certification.

## D. FINDINGS

**BLOCKING — B1, anchor contract requires a Design decision.** Fifteen concrete
plans require nonexistent observations. There is a unique identity owner and a
unique existing geometry policy; there is no separate requested/effective anchor
resolution contract. Replacing the declared anchor with the previous observed
factor would change its economic meaning and could hide a real coverage gap.
Changing it is not a coverage-label correction.

**BLOCKING — B2, cross-Snapshot execution reuse is absent.** `repair`/
`assemble_candidate` can preserve unaffected DomainCommit IDs; Canonical
content-addressed partitions reuse equal bytes. Thus “all 18 domains must always
rebuild” is false. However every View binds the whole Snapshot in its identity.
Changing even an unrelated domain creates new View IDs for the same requests.
`materialize_views` rejects the old run because its plan changed, and a new run
has no completed records to reuse. Builders compute payload before finding the
publication target. There is no cross-Snapshot binding/reuse path for unchanged
View results. This is the requested independent systemic finding and activates
the explicit stop line; no generic DAG or new artifact was introduced.

**BLOCKING — B3, readiness coverage remains incomplete.** Exact factor keys can
be checked from targeted monthly partitions. Financial admission currently
derives valuation coverage from row scans and selects financial revisions;
there is no exact per-security coverage index in the inspected manifests. The
valuation partitions alone total about 8.46 GB. Request success and partition
existence cannot certify financial row coverage. Copying projection into a new
validator, or calling all builders as a dry-run, is not an acceptable shortcut.

**NON_BLOCKING for the semantic decision; required diagnostic follow-up — N1.**
Shared-preparation failures are attributed to the last pending label and lose
the exception message. See the independent reproduction below. This can hide
the actual affected scope in an operational report.

**NON_BLOCKING — N2.** Source authority works as specified: 17 termination-date
differences across 3,601 scoped securities, zero unresolved listing/status
contradictions under the existing policy. Only 14 of the 15 missing-anchor
securities are among those differences: `601268.SH` shows why disagreement alone
is neither necessary nor sufficient to identify an impossible anchor.

**NON_BLOCKING — N3, input versus executed plan binding.** The source-plan
envelope retains implementation digest `sha256:decdcff3e9bbc649f8b771e95198a1e73c689509bf80f749f632604ba1623a41`,
while the actual operation froze
`sha256:3f2fcc4d94ce87e852e0276be6d62c312653c8cc8a72e217bfd2751a43276ffe`.
Snapshot and all requests are exactly equal. The public operation accepts only
`views`, and independently binds installed code; it does not validate a caller's
outer implementation digest. This is not an executed-plan mutation, but input
and execution envelopes must not be cited interchangeably. Both bindings are
recorded in summary/terminal validation.

**OUT_OF_SCOPE after stop line.** Implementing a new anchor policy, changing
persisted View binding/reuse semantics, repairing production data, rerunning
full closure/full bulk, Gate B acceptance, and promotion require the next
explicitly bounded decision/ticket. No source reconciliation framework is needed.

## E. NEW PROBLEM FAMILIES DISCOVERED

**Shared preparation can falsify per-security failure attribution.** In
`view_operation.py:164–190`, the completed-record scan leaves `active_view` set
to the last member. If entering the batch context fails, the exception handler
records that last member even when the failure comes from the first member.
Only `error_type` survives. The isolated [evidence.py](evidence.py) probe injects
a first-member preparation failure and asserts that the public operation reports
`last`; [batch-attribution.json](batch-attribution.json) records the result.
No production builder or artifact is used by this probe. This is distinct from
date semantics and can misdirect the next incident investigation.

**Input-plan provenance and executed-plan provenance diverge.** The final
artifact equality check found N3, rather than assuming the runner's source-plan
copy was byte-identical to the officially frozen plan. The requests remain
identical; code provenance must come from the latter. This was discovered during
the audit, not inferred from the known 600069 failure.

## F. CHANGES

Only audit evidence, bounded read-only reproduction commands, the execution/date
map, and the Designer handoff were added under `reports/bulk-readiness/`.
Runtime source, contracts, public APIs, dependencies and persistent formats are
unchanged. Historical plan, Snapshot and all 2,002 published refs are retained.
No lookahead policy, ledger or account state was changed.

## G. COST / VALIDATION

Wall time from first inspection through terminal artifact validation: **867.5 s
(14 min 27.5 s)**; Git delivery follows this measurement. The final artifact
check is [artifact-validation.json](artifact-validation.json): exact label set,
classification totals, anchor evidence, actual frozen requests, installed-code
binding, retained 2,002 refs, and stopped checkpoint all agree. Free space was
620,522,782,720 bytes and free inodes 62,240,057 at that check.

The timed all-plan census took **27.90 seconds**, with **849,841,456 bytes of
metadata**, **2,128,601,766 bytes of Canonical payload**, and **4,549,108 plan
bytes** read. Peak RSS was **2,832,668 KiB**. These are logical bytes measured by
the census; they are not physical disk-I/O counters or total session read volume.
Exploratory metadata reads occurred separately and were not fully metered.
The separate source comparison read **868,784 bytes** from eight security-master
Raw batches (twice while adding static inventory output). No market/factor Raw
payload was read, no remote source was queried, and no data was rebuilt.

Existing source-completeness, exchange-security and admission-plan tests:
**PASS, 24 tests in 0.738 s**, see [tests.log](tests.log). Isolated batch diagnostic:
**PASS (bug reproduced)**. Selected manifest/partition content checks: PASS.
Full closure, full financial/event row readiness, runtime bulk and Gate B:
**NOT RUN**. Runtime code was not changed, so unrelated expensive tests were not
repeated. Static inventory contains 1,352 raise/assert sites across the package
and 111 date-related contract field declarations; it is navigation evidence,
not proof that every site is reachable or independently reviewed.

The earlier 31.60 h / 41.34 GB estimate remains a sampled scenario, not a renewed
budget guarantee. The known failed run already spent roughly 49 minutes before
the anchor failure. This audit cannot certify a revised full-bulk budget while
the executable-plan and restart model remain unresolved.

## H. RECOMMENDATION — DESIGN_DECISION_REQUIRED

Designer needs to decide two bounded contracts before Worker implementation:

1. Define requested versus effective adjustment anchor and evidence sufficient
   to distinguish legitimate end-of-history absence from a true gap. Preserve
   the existing exchange-owned identity interval. State whether the requested
   View end remains intact when the effective factor anchor differs, and how
   unresolved cases are represented before freeze. A previous-factor lookup
   alone is insufficient evidence of legitimate absence.
2. Define how a new Snapshot can bind unchanged View computation without
   recomputing it, while keeping historical Snapshot/View identities immutable.
   Canonical object reuse already exists; target the missing View layer only.

After that decision: extend the existing planner/admission owners, admit all
deterministic prerequisites before publication, retain builder-only assertions,
rerun this full-label census with the remaining checks, and require BLOCKED=0,
UNKNOWN=0 before recommending a separate Reviewer and eventual bulk resume.

No request to relax correctness, waive unknowns, or resume bulk is made here.

## Reproduce bounded evidence

From this repository checkout, with an output directory outside the formal root:

```sh
PYTHONPATH=src python3 reports/bulk-readiness/census.py \
  /var/lib/axiom-data \
  /home/liuming/workspace/axiom/review/v1-full-views-20260925-b8fe7e0-r1/source-plan.json \
  /tmp/axiom-bulk-readiness-reproduction \
  /var/lib/axiom-data/operations/v1-full-views-20260925-b8fe7e0-1d69dc-r1/views-plan.json
PYTHONPATH=src python3 reports/bulk-readiness/evidence.py \
  /var/lib/axiom-data /tmp/axiom-bulk-readiness-reproduction
PYTHONPATH=src:tests python3 -m unittest \
  test_source_completeness test_exchange_security test_admission_plan
```

The missing-anchor context output retains surrounding rows for inspection;
`anchor-evidence.json` in this PR is its compact exact-anchor extraction.
Initial measurement counted the source plan; the later final binding check also
reads the separate frozen plan. Counts/timing on rerun can therefore differ.
