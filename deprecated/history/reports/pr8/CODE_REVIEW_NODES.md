# PR8 code review nodes

Overall Gate A: **NOT_READY / NO_BULK_BUILD**. This index identifies bounded code
increments for review; it does not approve the V1 baseline or full data admission.

## A. Restored artifact validation

Commit: `6b48cc9`.

Review `src/axiom_data/recovery.py`, CLI/export integration,
`tests/test_recovery_operation.py`, and `docs/operations/recovery.md`.
The public operation binds explicit Snapshot/View IDs and manifest digests,
checks their offline closure using existing version-dispatched loaders,
revalidates on resume, and rebuilds the catalog. PR6/PR7 restore their published
bytes; exact rebuild is limited to creation-time-pinnable market/adjusted Views.
It cannot mark a dataset ready for consumption.

Independent Astra bounded review passed after the unsupported exact-rebuild
finding was fixed. Luna ran 246/246 tests, 75.398 seconds; log:
`/tmp/pr8-recovery-fullsuite-20260911.log`. Full-root recovery remains unvalidated.

## B. Required-View plan geometry

Review the increment after `6b48cc9`: `src/axiom_data/admission_plan.py`,
CLI/export integration, `tests/test_admission_plan.py`, and
`docs/operations/admission-plan.md`.

The public preflight validates the packaged 56/469 registry digest, explicit
Snapshot v4 manifest identity/composition, and two complete reference-domain
closures. It checks planned coverage separately for all five View kinds using
each security's exchange sessions and identity interval. Missing securities,
interior holes, mismatched policies/cutoffs/fields, duplicate overlapping work,
and invalid anchors cannot pass as a complete plan. Forecast fields remain part
of the fixed PR7 Fact exporter, not just its numeric Qlib subset.

Independent Astra bounded code review passed after Raw closure, manifest shape,
date normalization and anchor checks were fixed. Targeted tests: 9/9 PASS in
0.250 seconds; log `/tmp/pr8-admission-plan-targeted-20260912-r2.log`.
Full suite: 255/255 PASS in 75.394 seconds; `git diff --check` PASS.
Log: `/tmp/pr8-admission-plan-fullsuite-20260912.log`.

The result is `PLAN_VALIDATED` or `INCOMPLETE`, never full admission. Required
shards still need real construction and payload validation. Source-backed gaps,
benchmark availability, universe observations and historical PIT results are
not established by this geometric check. Full targets whose securities have no
common valid adjusted anchor cannot use the current common-anchor plan shape;
the full-history execution plan must address that explicitly without reducing
the requested target.

## Remaining overall code gate

- Full-scope source-backed availability/admission and exact 56/469 result binding.
- A full-history View execution plan, including explicit adjusted-anchor scope.
- Terminal validator tying the baseline, required Views, daily/T+1/revision/
  no-change results, offline recovery and executed Notebook to frozen identities.
- Independent Gate A review of that complete execution path, followed by lean
  source preflight. Full-root artifacts then require independent Gate B review.

Previously captured immutable Raw/checkpoints are preserved. No new bulk build,
merge or baseline pointer update is part of these review nodes.
