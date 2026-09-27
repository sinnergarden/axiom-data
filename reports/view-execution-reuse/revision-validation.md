# PR32 blocking-finding fixes

Role: Worker. Repository: axiom-data. Branch: codex/view-execution-reuse.
PR: https://github.com/sinnergarden/axiom-data/pull/32.
Starting reviewed head: `2d4676673fc0bf908d06a792d7f5f1c41461d309`.
Base: `feb97f8d7bae7b166db3f31d20fa6e7722c9dbb8`.

Finite outcome: fix the two reported omissions, validate them, append a commit
on the same branch, publish evidence to the same PR and deliver a Worker handoff.
Stages: inspect retained changes; fix and validate affected boundaries; validate
the final diff, remote head and evidence, then deliver and stop.
Definition of Done: both reviewer reproductions have the corrected result;
affected tests pass; source replay remains once per operation; no changes to
`artifacts.py` or `operations.py`; the added commit and evidence appear on PR32;
the final handoff identifies the implementation head and remaining review state.

## Implementation

The operation opts its target SnapshotReader into the existing
`_load_snapshot_with_commits` source-closure validation. Its returned checked
commits and observed validation paths stay with that shared Reader. Candidates
continue to use the bounded reference-only Reader slot; the candidate's consumed
commit identities must match the fully validated target before rebinding. This
shares the authoritative validation rather than replaying history per candidate.
Existing consumed-input checks reject later changes to admitted Raw files.
Ordinary Reader construction keeps its existing deferred replay behavior.

One `view_batches` helper defines both preflight and execution groups, preserving
plan order, family bounds and unique securities in each group. Financial groups
share date range, universe/industry and PIT/cutoff; event groups share date range
and PIT/cutoff. Market/adjusted source preparation permits several windows and
uses its existing bounded union read, then validates and projects each original
request separately. Repeated-security requests move to a subsequent group.
Neither the output scopes nor the prerequisite checks are widened or removed.

Product source changes are confined to admission_plan, consumption and
view_operation. Targeted tests and minimum evidence accompany them. No new
dependency, artifact schema, source authority, projection path or official data
mutation is introduced. Historical artifact loaders and PIT semantics remain.
This Data View operation does not touch research, backtest, daily or ledger state.

## Validation

**PASS:**

```text
PYTHONPATH=src:tests python3 -m unittest test_view_execution_reuse test_view_operation test_view_operation_batch test_admission_plan test_full_admission test_frozen_execution test_event_request_index test_adjusted_price_batch -q
Ran 47 tests in 59.865s
OK
```

Output: `fix-targeted-tests.log`. The source-closure test observes exactly one
complete Snapshot load plus one reference-only candidate load for all five family
labels, and no repeated `_validate_domain_commit_node` key. Before-admission Raw
damage rejects the Snapshot with no Views; damage injected between admission and
reuse also rejects the operation. Five families' repeated-security/window plans
produce the same refs as individual builds. Family grouping boundaries, actual
frozen subprocess invocation and existing resume/batch tests also pass.

**PASS:** the independent review's `reproduce.py` is executed from the fixed code
using temporary fixture copies, changing only its two expected-result assertions
in memory. The original review files stay intact. Output: `fix-reproduction.log`.
Event windows June 10–11 and June 12–13 now build together and match the individual
View refs. Referenced market Raw corruption now gives `FAILED`, with the original
`RawBatch payload content does not match its manifest` reason and no publication.

The exact reproduction command is:

```python
from pathlib import Path
source = Path('../../view-execution-reuse-review/reproduce.py').read_text()
source = source.replace("assert results['together']['status'] == 'FAILED'",
                        "assert results['together']['status'] == 'VIEWS_BUILT'")
source = source.replace("assert r['status'] == 'VIEWS_BUILT' and r['reused_views'] == ['q']",
                        "assert r['status'] == 'FAILED' and not r.get('reused_views') and not r['published_views']")
exec(compile(source, 'reviewer reproduction with corrected outcome assertions', 'exec'))
```

Run with `PYTHONPATH=src:tests python3` from the repository root.

**PASS:** `PYTHONPATH=src:tests python3 reports/view-execution-reuse/benchmark.py`.
Output: `fix-benchmark.json`, four sessions and one/two securities on isolated real
fixture copies plus an explicitly synthetic benchmark-domain parent-link change.

| Views | Mode | Operation seconds | Full source-closure seconds | Projection calls |
| --- | --- | ---: | ---: | ---: |
| 5 | reuse | 0.581 | 0.234 | 0 |
| 5 | fresh | 0.659 | 0.223 | 5 |
| 10 | reuse | 0.818 | 0.225 | 0 |
| 10 | fresh | 0.940 | 0.227 | 10 |

Each operation has one complete target validation. Reuse has one additional
reference-only candidate Snapshot load. These are small wall-time samples;
physical I/O and peak memory are **NOT MEASURED**. Zero projections saves value
calculation but retains the fixed operation-level source validation and subsequent
metadata-state checks. Initial benchmark.json timings predate this correction.
Full-history/production measurement is **NOT RUN**, and earlier sampled production
closure cost remains relevant. No production ETA or readiness is inferred here.

**PASS:** final diff whitespace check and ownership check. Remote delivery and
implementation SHA are recorded in the Worker handoff and PR follow-up.
Status: IMPLEMENTED / TESTED. Independent remote re-review remains required;
this Worker does not approve or merge.
