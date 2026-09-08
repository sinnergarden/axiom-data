# PR7 D-M2 completion

## Definition of done and stages

1. Verify approved PR6, merge/push, machine-resolve frozen PR4 scope. Complete:
   main/origin main at e23d8234dd110afd2972cc5647bfea14df4b019b; PR4 1518 files
   digest-validated; disjoint PR5 18 + PR6 23 + PR7 15 = 56, 469 frozen consumers.
2. Implement the five remaining canonical domains, source profiles, revision and
   completeness semantics, public Snapshot/Fact/Qlib access and counterexamples.
3. Collect bounded real supplier inputs, build and reconcile immutable artifacts.
4. Validate all 56 requirements and 469 dependencies, consumer conformance with
   legacy reads denied, and complete offline rebuild in a new root.
5. Independently validate terminal artifacts, D01–D15 evidence, full tests and
   diff checks; commit and deliver the PR7 review report with actual limitations.

Task remains active at stage 2/5. D-M2 acceptance is not yet established.

## Frozen scope

`python scripts/resolve_pr7_scope.py --package <explicit PR4 r5 directory>
--output src/axiom_data/scope/pr7_scope.v1.json` verifies the authoritative package
and resolves remaining leaves against PR5 public fields/artifact refs and PR6's
frozen required-leaf list. No Feature census is rerun. PR7 contains forecast (3),
holder (2), margin/lending (7), moneyflow (3). There is no additional standalone
stable-derived leaf; the holder ratio is a completeness-gated report aggregate.

## Design

Reuse RawBatch v2, DomainCommit publisher, observation selector and SnapshotReader.
Top-holder reports retain all holder rows inside one revision-bound group, so an
incomplete correction cannot inherit disappeared holders or pretend to decrease
a complete concentration. Public metrics retain null reasons and component refs.
Verified remains fail-closed. Historical terminal endpoints remain best_effort;
operational queries use actual retrieval observations.

## Implementation and real closure

Five canonical v1 contracts are registered in Snapshot v4. Snapshot v1–v3 and
previous Fact/Qlib contracts retain explicit dispatch. Source requests bind the
profile content digest and their own symbol/date ranges; the loader replays the
transitive raw mapping before admitting canonical rows. Reports retain grouped
holder rows, and aggregate only ten distinct valid holders. Missing/zero remain
distinct in all seven financing/lending fields. Moneyflow preserves ELG buy/sell
and supplier L2 net independently, converting ten-thousand CNY to CNY.

Public `SnapshotReader.leaf_fact` / `FactView.leaf_fact` expose 15 typed leaves;
`build_pr7_fact_view` exports the 12 numeric fields and carries forecast event
metadata in the same artifact. The existing Qlib reader resolves this version.
`extend_snapshot` uses the existing BuildApplication and reuses every other
explicit domain ref without recollection.

Supplier sample: 600036.SH, 688981.SH and 000401.SZ; daily window June 10–13,
2025, and bounded report/publication history January 2024–June 13, 2025. Fifteen
RawBatches produced 67 holder counts, 15 grouped Top10 reports (150 holder rows),
12 margin, 12 moneyflow and 3 forecast rows. Empty forecast responses from the
first two symbols remain source gaps. All historical rows are best_effort.

The real operational extension test exposed multiple announcements for one
report period in a single retrieval. PR7 first applies shared PIT visibility to
each content observation, then orders eligible candidates by usable time,
observation time and explicit publication time. Equal times with different
content still fail. PR6 selectors are unchanged. Targeted revision and real
extension checks passed (15 tests).

Bounded source and recovery admissions are rebuilt after the final selector
change under explicit `admission-r3`. Earlier admission directories are retained
as development evidence and are not selected by recency or aliases.

## Source semantics references

- Tushare margin_detail: https://tushare.pro/document/2?doc_id=59
- Tushare moneyflow: https://tushare.pro/document/2?doc_id=170
- Tushare holder counts: https://tushare.pro/document/2?doc_id=166
- Tushare Top10 holders: https://tushare.pro/document/2?doc_id=61
- Tushare company performance forecasts: https://tushare.pro/document/2?doc_id=45

Strict historical adjusted-price admission correctly rejects terminal bootstrap
factors. The real fixture uses the existing `research_non_pit` policy with an
explicit anchor, and does not upgrade the qualification.

## Final validation

Stages 1–4 complete. Stage 5 artifact validation passed: source and recovery in
`pr7-dm2-20260909-r1/admission-r3` were independently reopened, the actual 56/469
consumer outputs and independent reconciliation matched their reports, and every
executed frozen source file matches the final source bytes. Full suite passed
136/136 with zero skips in 100.969 seconds. The D01–D15 matrix binds passing tests.

Terminal artifacts and all 19 report items are in `reports/pr7/PR7_REPORT.md` and
its linked machine reports. The delivery handoff records the commit and verifies its remote branch identity.
PR7 is not merged; D-M2 release acceptance awaits independent review.
