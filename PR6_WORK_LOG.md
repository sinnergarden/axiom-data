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

## Review correction run (2026-09-08)

Objective: repair financial ABA observation sequencing, incremental membership state,
coverage admission and typed FactView metadata. PR7 remains deferred; no merge.

Stages: (1) reproduce and trace blockers; (2) implement and test all counterexamples;
(3) real frozen supplier slice and new-root offline recovery; (4) independently
validate terminal artifacts, full suite, code bundle, commit and remote branch.
DoD: ABA resolves A/B/A at the three cutoffs; immutable child closes/corrects
intervals and matches all-raw replay; date/symbol/ID/field coverage gaps reject;
TTM null has missing reason and component refs; missing industry mapping rejects;
actual source/recovery identities and six gates pass; full tests pass; delivery
report contains all 13 requested items and commit SHA is verified remotely.

All four correction stages completed. The 100-test suite passed in 65.769 seconds.
New source/recovery roots under `pr6-review-20260908-r1` passed all six gates;
repo implementation bytes match the frozen code bundle. The 13-item correction
report is `reports/pr6-review/DELIVERY.md`. Remote verification follows commit.

## Three-blocker correction (2026-09-08)

DoD/stages: (1) commit runnable probes before implementation and record failures;
(2) repair group-state empty membership, world-at-cutoff derivation, and explicit
v1/v2 loaders; (3) validate empty/isolation/re-entry/source-gap/raw-only recovery,
old/new Snapshot logical prefix and future visibility, actual published v1/v2 and
malformed artifacts; (4) independently validate real slice/new-root recovery,
full suite, immutable artifact bytes, frozen implementation, commit and remote.
Final deliverable: 10-item report plus corrective commit on the existing branch.
PR7 remains deferred; no merge.

Terminal validation: frozen implementation matches repo bytes; 107/107 tests PASS
(93.196 seconds), all six actual-root gates PASS. Original v1, prior v2 and new
View payloads match stored logical rows and identities. Final roots are
`pr6-empty-prefix-compat-20260908-r4/source` and `/recovery`; the corresponding
report is `reports/pr6-empty-prefix-compat/DELIVERY.md`.

## Universe security projection correction

DoD/stages: reproduce the real fixed Snapshot (12 selected rows; 688981.SH has
2), correct Reader validation-before-projection ordering, run the complete suite
and diff/show whitespace checks, then append one corrective commit and verify it.
The selector and group-state completeness rules remain unchanged. as_of selects
from complete universe rows; as_of and members share the final symbol projection
and existing symbol admission. Six focused regressions cover real/subset reads,
known nonmember/exit, unknown symbols, missing unrequested member rows, and empty
correction/return. Focused tests: 6/6 PASS. PR7 remains deferred; no merge.

Full suite: 113/113 PASS (93.631 seconds), including existing empty-state,
enter/exit/re-enter, financial ABA/PIT prefix, v1/v2 compatibility and offline
artifact evidence checks. `git diff --check`: PASS.
