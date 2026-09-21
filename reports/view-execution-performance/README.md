# View execution performance

Status: IMPLEMENTED, TARGETED_TESTS_PASS, REAL_PROFILE_BLOCKED. PR remains draft.

Base: `0110bc9a900fe1fd1135f7714f4170337ba65db3`.
Implementation: `e3090942fe1f932829d105ca393d32e278425ae6`.
Concrete input: `snapshot-1d69dc236a358f1627ae91080c33cf993b9e2acb55bbd2c2cb7a37129ce6b5a5`.
Formal `/var/lib/axiom-data` remains read-only. No full Views or daily run occurred.

## Changes and invariants

- Financial admission computes complete-scope valuation coverage and financial
  summaries once for unchanged inputs/policy/cutoff. The coverage bitmap retains
  individual sessions, including holes. Full-scope ambiguity remains visible.
- Admission and current-security history occupy two Reader-local encoded slots,
  at most 32 MiB each. File/ancestor state and implementation changes reject reuse.
  This limit describes preparation payloads, not total Reader memory or closure metadata.
- Every daily PIT selector and derived calculation remains unchanged. Returned
  metadata, missing states, source observations and artifact identity algorithms
  remain unchanged. No canonical data rebuild or protocol migration is required.
- View validation reuses a complete closure across security/date projections.
  Policy and Derived inputs still bind reuse; Qlib request admission executes each time.
- Daily dependency checks traverse all rows once. Snapshot dependency refs must
  match the already-validated DomainCommit dependencies before their check is reused.
- `_safe_path` is unchanged. Directory inode/mode detect ancestor replacement;
  regular-file change stamps still detect in-place changes. Creating unrelated
  output directories does not invalidate immutable source files.

## Evidence available

`targeted-tests.txt`: 45/45 targeted tests pass in 16.056 seconds. Coverage includes
frozen full-payload equality, unrequested-security ambiguity, sparse valuation
holes, legacy resume, exact Snapshot composition, changed input/implementation,
ancestor symlink replacement, and full-validation rejection of unrelated damage.

`path-observation-counts.json`: observing a five-component path 100 times reduced
additional `lstat` calls from 500 to 5. This is an observation counter, not an
end-to-end speedup. Original path safety checks are still executed.

The before run stopped during initialization without a terminal artifact.
The Runner handle disappeared. A managed retry returned session ID `21384`,
but the next poll returned `Unknown process id 21384`. An independent five-second
execution-cell probe likewise returned `exec cell 1 not found` on continuation.
Neither attempt produced `before/result.json`; no after timing is claimed.

Required real evidence remains incomplete: before/after profile, exact real
payload equality, actual new warm build/resume costs, and real zero-builder resume.
The small-fixture tests do not replace these requirements.

## Publication profile and disposition

`publication-profile.json` reconstructs the prior real public repair log:
8,938.381 seconds total, with 4,650.703 seconds in 19 DomainCommit closure spans.
The remaining 4,287.512 seconds between markers are not function-level attribution.
They include loading, building, validation and orchestration; they cannot all be
called serialization or disk writes. The two financial closure spans reference
different commits, so they are not evidence of redundant validation of one commit.

This PR removes shared repeated scans. Further publication changes need a separate
ticket with detailed profiling of those unallocated spans. No candidate was republished.

## Full-Views cost structure

The frozen plan has 17,900 Views: 3,580 of each kind (`cost-shape.json`). All financial
requests share one policy/cutoff pair. Expected execution cost separates into:

1. Initial complete input validation per operation/session.
2. Global financial admission preparation for the current policy/cutoff.
3. Current-security history preparation when the security changes.
4. Per-session PIT selection, membership projection, serialization and file comparison.

The bounded cache retains one preparation per slot; alternating inputs may evict it.
Per-session work and output bytes remain. The prior 15-session financial sample
contains about 35.9 MB of membership JSON and 16.6 MB of derived JSON. Its window
does not represent full listing history. No exact full-Views ETA is established.
Fresh Event/Market startup speedups have not been measured for this implementation.

## Resume the measurement

Use a stable execution host with this concrete Snapshot available. Copy
`profile_real.py` to an isolated workspace review directory. Archive base `src/`
to that directory's `baseline/`. Run from the repository root, sequentially:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/absolute/review-run/baseline/src python3 -u /absolute/review-run/profile_real.py before
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/absolute/axiom-data/src python3 -u /absolute/review-run/profile_real.py after
```

These are authorized long representative measurements, not a bulk run. The script
denies formal-root writes and supplier connections, saves phase counters and a
cProfile, compares complete payload bytes, and publishes only isolated test Views.
It rejects existing terminal results instead of silently overwriting them.

Before making the PR ready, verify both result files, exact payload equality,
public artifact payload equality, `completed_resume.builder_calls == 0`, and
`changed_scope_validation.domain_loads == 0`. Report profiler overhead separately
from unprofiled build/resume elapsed. Preserve failed logs; do not infer completion
from a stale progress file or a disappeared process.
