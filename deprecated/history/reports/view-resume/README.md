# Completed View resume

Base: `11484f08c24a2ca274076d7a864e26fd10043d42`.

The public operation now checks a completed View with its existing strict loader
before calling a builder. Manifest identity/digest, Snapshot closure, requested
scope and inputs, and financial/event implementation digests must match.
The loaders retain their semantic projection checks; this change does not make
validation free or bypass PIT admission.

Missing or invalid results are removed from completion state and sent to the
existing builder. Missing artifacts can be rebuilt. An occupied corrupt artifact
still causes the immutable publisher to fail closed: this operation does not
delete, quarantine or replace published bytes. A substituted valid artifact with
the wrong scope is rejected and the requested artifact rebuilt/reused by its builder.

`views-plan.json` stores the frozen plan once; `views.json` v2 stores progress
without the plan. Legacy v1 inline records upgrade after plan comparison, retaining
run ID and completed references. The plan is persisted before changing the progress
record, so interruption between those writes leaves a recoverable inline record.
The public return still includes the plan. Changed plan or implementation remains
a resume error under the existing operation contract; this ticket adds no code
upgrade/rebind mechanism. View schemas and identity algorithms are unchanged.

Progress still rewrites its smaller completed-reference summary. This bounded
change removes repeated large plan writes, not all possible quadratic metadata
growth. No journal, database or new workflow framework is introduced.

## Validation

The initial revision passed 13 tests in 11.810s. The review correction adds two
public-entrypoint Qlib regressions to the same targeted suite:

Review correction: **15/15 PASS**, 14.219s, no skips.

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests timeout 120s python3 -m unittest -v \
  test_view_operation test_view_resume test_recovery_operation
```

Coverage includes all five View kinds with zero completed builder calls, strict
Snapshot corruption rejection, missing/corrupt output, wrong input artifact,
implementation mismatch, interruption with one remaining item, legacy checkpoint
upgrade, plan write counts, and existing offline recovery tests. Formal data was
not modified; tests use disposable copies of the small real forensic fixture.

Qlib reuse and first build now share input admission. Qlib v1's intrinsic price
basis is unadjusted even though its manifest omits that field. An adjusted request
without a valid Derived input stays FAILED and enters the builder rather than
being skipped. A valid adjusted request rejects a substituted v1 artifact;
exact-compatible adjusted and unadjusted completion still skip the builder.

## Small real probe

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests timeout 120s python3 tests/view_resume_probe.py
```

The JSON report identifies the immutable Snapshot, 688981.SH and four sessions.
24 labels deliberately request the same small replay artifact to exercise
checkpoint persistence; they are not 24 distinct scopes.

| Measurement | Initial | Resume |
|---|---:|---:|
| Builder calls | 24 | 0 |
| Frozen plan writes | 1 | 0 |
| Operation bytes serialized | 166,997 | 162,576 |
| Repeated inline-plan bytes avoided at those writes | 160,650 | 83,538 |
| Elapsed | 0.461s | 0.290s |

References are identical on resume. Avoided bytes are calculated from the exact
plan size and observed progress writes, not a measured old-code benchmark.
Timing includes small Snapshot validation, with warm OS caches. Counts exclude
artifact writes and filesystem metadata; they do not establish production-scale
performance. Bulk and Gate B were not run. Independent review is pending.
