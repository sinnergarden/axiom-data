# PR6 implementation and validation plan

Objective: one PR6 branch/commit for PIT universe/industry and financial facts,
valuation, single-quarter and TTM, with real-source reconciliation and offline
recovery. Do not merge. Data root: `/home/liuming/workspace/axiom/data`.

Branch: `phase1/pr6-pit-financial`; baseline: `81e3048cc0c11b0826609db66575f44c069ad07d`.

## Stages / Definition of Done

1. Resolve PR4 r5 machine mapping, validate authority hashes, preserve exact PR6
   and deferred PR7 leaves. Complete: all 1,518 package manifest files verified;
   23 PR6 leaves, four explicitly stable-derived leaves and 15 deferred PR7 leaves
   resolved into `src/axiom_data/scope/pr6_scope.v1.json`.
2. SourceProfiles, canonical contracts and revisions: exact request/payload
   validation, immutable raw closure, half-open membership, revision-specific
   availability, fail-closed verified, explicit conflict and null handling.
3. Snapshot-bound financial as-of, single-quarter/TTM, PIT projections, Fact Reader
   and Qlib consumption: exact components/policy/cutoff and missingness validated.
4. Bounded real source collection, frozen Qsys reconciliation, integration fixture,
   fresh-root network-disabled reconstruction through catalog and View validation.
5. Independently validate every terminal artifact, run full tests and adversarial
   cases, inspect Git diff, commit one PR6 deliverable and report all 16 requested
   categories. Only then mark the persistent goal complete.

## Authority

User-provided design attachments are the design authority. The task's old repo
path is superseded by the explicitly fixed migrated environment. Repository
AGENTS.md now records the explicitly authorized PR6 boundary.
The PR4 package is `/tmp/axiom-qsys-pr4-audit.c969c74-20260906-r5`.
Its immutable manifests are bound by digest in the resolved scope. Frozen Qsys
artifacts are forensic inputs only, never a mutable runtime dependency.

## Validation tracking

Stages 1–4 complete. Stage 5 artifact validation: PASS.
- Baseline full suite: 80/80 PASS.
- Final full suite: 96/96 PASS; `reports/pr6/tests.txt`.
- Frozen-code recovery attempt: `pr6-pit-financial-20260908-r1-validation-v3`.
- Source and recovery roots, fixed identities and all six gates:
  `reports/pr6/run_manifest.json`.
- Scope, source/PIT limitations and deferred leaves: `reports/pr6/DELIVERY.md`.
- Working implementation matches the frozen validation code bundle byte-for-byte.

The delivery consists of this single PR6 changeset on the named branch. The final
Git check must show exactly one commit relative to main and a clean working tree.
The commit identity and branch publication outcome are recorded in the task's
final delivery response. No merge or PR7 implementation is part of this run.
