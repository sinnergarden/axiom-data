# Financial View operation preparation

The operation retains the full financial history once, groups it by security,
and releases those groups when the operation ends. Each View still uses the
existing daily PIT selectors, independent publisher and semantic loader.
Full-scope financial admission precedes projected history consumption. Calendar,
security identities and taxonomy preparation are shared within the operation.

Reuse remains bound to the concrete Snapshot, domain commit objects/digests and
the existing validated file/path states. Encoded history groups prevent output
mutation from affecting subsequent Views. Memory grows with the input financial
domain, rather than with the number of materialized View payloads. Completed
artifacts still undergo individual validation before their builders are skipped.

The Design-approved comparison is:

- Old/new implementation: equal logical values, PIT, missing states and provenance.
- Batch/serial under the new implementation: equal artifact identity and contents.
- Code bundles retain their actual implementation digests; a new implementation
  can produce new View IDs.

## Evidence scope

The real input is concrete Snapshot
`snapshot-1d69dc236a358f1627ae91080c33cf993b9e2acb55bbd2c2cb7a37129ce6b5a5`.
The baseline source is main commit
`65e815350f5b28f8ec9d6ed381774a9a6dde0b16`.
The measurement uses 1, 10 and 50 real securities, each at its frozen plan's end
session. This isolates preparation scaling; it does not measure all historical
sessions of all 3,580 financial Views.

The baseline probe selects successful samples deterministically and records
existing valuation coverage rejections separately. Every recorded rejection
must remain rejected by the new implementation. Earlier attempts stopped at
`000005.SZ` (`2024-04-25`) and `000016.SZ` (`2026-09-11`); their partial results
are not successful 50-security runs.

`profile_real.py` compares complete projection payload digests (including
values, PIT metadata, missing states and provenance), publishes 50 separate
test artifacts and verifies completed resume invokes zero builders. It reuses
an explicitly dependency-validated Reader for the public operation and redirects
only publication into the evidence output directory. This separates measured
initialization from build/resume costs. It does not measure full 18-domain
operation startup again.

Partition byte counters count logical traversals; `/proc/self/io` counters
separately report physical reads. Page-cache state is uncontrolled. No supplier
requests, formal-root writes or full Views operation are permitted by the probe.

Run with the exact baseline result path and an unused output directory:

```sh
# BASELINE_SRC contains `src` extracted from the exact baseline commit above.
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$BASELINE_SRC" python3 \
  reports/financial-operation-batch/profile_baseline.py BASELINE_OUT
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 \
  reports/financial-operation-batch/profile_real.py OUT BASELINE_RESULT_JSON
```

## Results

Implementation: `eab4450f3dfaa9b3eb782c4d223ad37f324ec19b` (includes `de8e04b`).
[49 targeted tests](targeted-tests.log) passed. The separate
[terminal artifact check](terminal-check.json) verified 50 artifacts and 1,350
files, including manifest identity, declared file digests and actual code digests.

[Before](before.json) and [after](after.json) use the same 50 configurations.
Complete logical payloads match 50/50. All five baseline rejections match.
Synthetic multi-security publication and one real serial publication confirm
same-implementation batch/serial artifact identity; all 50 real batch artifacts
also match the old implementation's logical payload digest.

Projection only, excluding initialization and publication:

| Successful securities | Before seconds | Batch seconds | Before partition traversals | Batch traversals |
|---:|---:|---:|---:|---:|
| 1 | 128.9 | 149.3 | 784 | 784 |
| 10 | 301.0 | 206.5 | 2,413 | 793 |
| 50 | 1,061.4 | 460.7 | 9,653 | 833 |

The first security pays for grouping all histories. Subsequent securities average
19.0 seconds before versus 6.4 seconds after. At 50 securities the projection is
2.30 times faster. Financial partition traversals fall from 9,450 to 630;
securities 2–50 do not traverse financial partitions. Calendar preparation falls
from 50 calls to one. Full-scope financial selection remains one in both runs:
the prior admission reuse is preserved, rather than counted as a new improvement.
Each request still performs its own coverage checks and existing file-state guards.

Across the 50 projections, logical partition bytes fall from 87.12 GB to 15.12 GB.
Physical reads increase from 0.854 GB to 1.310 GB. These runs do **not** demonstrate
reduced physical disk traffic; OS page-cache state was uncontrolled. Retained
encoded history is 740,807,217 bytes for all 3,586 canonical securities (including
those outside the successful sample). Whole-process peak RSS is 19,666,872 KiB;
it includes initialization and cannot be attributed solely to batch preparation.
No before-run RSS comparison was recorded.

| Phase | Seconds | Domain loads | Partition traversals | Builder calls |
|---|---:|---:|---:|---:|
| Before dependency initialization | 1,864.8 | 6 | 729 | 0 |
| After dependency initialization | 1,779.6 | 6 | 729 | 0 |
| New serial publication, one View | 26.1 | 0 | 182 | 1 |
| New batch publication, 50 Views | 675.7 | 0 | 280 | 50 |
| Completed resume, 50 Views | 349.5 | 0 | 230 | **0** |

Initialization is a separate fixed cost; this ticket does not optimize it.
Publication and resume use the already validated Reader as described above;
resume still loads and checks every completed artifact. The sample does not
establish total runtime for 3,580 full-history Views or 17,900 full Views.

The successful baseline run took 2,936.7 seconds; the after run, including
publication and resume, took 3,302.0 seconds. Their totals contain different
phases and are not an end-to-end speed comparison. Two earlier baseline probes
stopped on existing valuation gaps; their partial logs remain in the workspace
review directory and are excluded from successful-run timings.

Real artifacts and original logs are retained at:
`/home/liuming/workspace/axiom/review/financial-operation-batch-20260922/`.
The baseline output is `before-complete`; test artifacts are in `after/outputs`.
Run `check_evidence.py before-complete/result.json after/result.json` against
those original directories to repeat the lightweight terminal artifact check.
Formal data remained read-only; no full Views, daily or bulk operation ran.
