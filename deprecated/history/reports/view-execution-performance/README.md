# View execution performance

Status: IMPLEMENTED, TARGETED_TESTS_PASS, REAL_REPRESENTATIVE_PASS.
Independent review remains required. This is not full-Views acceptance.

Base: `0110bc9a900fe1fd1135f7714f4170337ba65db3`.
Implementation: `e3090942fe1f932829d105ca393d32e278425ae6`.
Input: `snapshot-1d69dc236a358f1627ae91080c33cf993b9e2acb55bbd2c2cb7a37129ce6b5a5`,
`688981.SH`, 2026-08-24 through 2026-09-11 (15 sessions), frozen best-effort cutoff.
Formal `/var/lib/axiom-data` stayed read-only. Test publication used an isolated
workspace root. No supplier request, candidate rebuild, full Views or daily run.

## Implementation

- Complete-scope valuation coverage and financial summaries are prepared once
  for unchanged inputs/policy/cutoff. Coverage retains individual sessions and
  holes; full-scope ambiguity remains visible.
- Admission and current-security history occupy two Reader-local encoded slots,
  at most 32 MiB each. File/ancestor and implementation changes reject reuse.
  This bounds preparation payloads, not total Reader memory or closure metadata.
- Existing per-session PIT selectors and derived calculations still execute.
  No contract, schema, persisted identity algorithm or historical artifact changes.
- Dependency validation reuses its complete closure across security/date scopes.
  Policy and Derived inputs still bind reuse; Qlib request admission runs each time.
- Daily dependencies use one complete traversal. Snapshot dependency refs must
  match the already-validated DomainCommit dependencies before reusing that check.
- `_safe_path` is unchanged. Ancestor inode/mode detect replacement; regular-file
  change stamps detect edits. Unrelated output creation does not invalidate inputs.

## Controlled real comparison

`real-before.json` and `real-after.json` record phase counters and inclusive timings.
Both runs used the same script, Snapshot and request. All 119 archived baseline
source files were checked against the base commit. OS caches were not normalized;
these are process-first measurements, not guaranteed cold disks.

| Phase | Before seconds | After seconds | Partition traversals before → after | Logical partition GB before → after |
|---|---:|---:|---:|---:|
| Dependency initialization | 2034.157 | 2006.819 | 1125 → 972 | 45.217 → 36.762 |
| First projection, both profiled | 613.099 | 322.759 | 1104 → 798 | 31.175 → 14.263 |

Initialization improved only 1.3%; this is not a substantial startup speedup.
Logical partition volume fell 18.7%. Physical reads were 31.332 GB before and
28.767 GB after. Sampled RSS approached 18–19 GiB and swap was used; initialization
memory pressure remains. These samples are not a measured peak bound.

The profiled first projection improved 47.4%. `profile-before.txt` and
`profile-after.txt` identify repeated partition parsing/merging in admission:
admission took 519.608s before and 226.597s after under the profiler. Timings are
inclusive and must not be summed. Raw loader calls are not physical disk reads.

## Reuse and public operation

These after phases are unprofiled and use the already-validated Reader. The
public operation's Reader constructor is supplied that Reader to separate the
measured initialization cost. Public builder, artifact loader, request checks,
checkpoint and resume logic execute normally, with isolated output publication.
These are not fresh-process end-to-end operation timings.

| After phase | Seconds | Domain loads | Partition traversals | Logical partition GB | Physical read GB | Builder calls |
|---|---:|---:|---:|---:|---:|---:|
| Warm projection | 55.371 | 0 | 15 | 0.665 | 0 | n/a |
| Changed-window closure reuse | 2.905 | 0 | 0 | 0 | 0.0043 | n/a |
| Prepared public build | 114.244 | 0 | 30 | 1.329 | 0.0322 | 1 |
| Completed resume | 55.229 | 0 | 15 | 0.665 | 0 | 0 |

Warm admission took 3.088s. Warm/resume no longer traversed financial partitions;
the remaining 15 traversals were valuation partitions. Public build wrote
59,904,000 process-accounted bytes; resume wrote 12,288 bytes of isolated operation
progress. Zero builder calls does not mean zero validation or zero elapsed time.

Historical same-input evidence records warm public building at 642.354s and
completed resume at 323.174s (`historical-context.json`). This is context, not a
controlled timing ratio: execution dates, OS caches and harness boundaries differ.
Do not compare profiled first projection with unprofiled warm timing as a speedup.

## Correctness and terminal checks

`terminal-verification.json` records a separate check after both processes exited:

- Both results are PASS with identical Snapshot and request.
- Before, after, warm and published payloads are exactly equal. Before also matches
  the historical frozen sample. This includes values, PIT, missing states,
  ambiguity metadata, ordering, provenance and input references.
- Payload: 57,470,907 bytes,
  `sha256:a0b9e9bff2e3dee4bd3ab019c6a598e88585b080f8e194b1af77e408d40e84de`.
- Test View identity, manifest digest and all 27 declared file sizes/digests pass;
  its operation checkpoint is `VIEWS_BUILT`.
- Completed resume has zero builder calls; changed-window reuse has zero domain loads.

`targeted-tests.txt`: 45/45 PASS in 16.056s. Coverage includes frozen payload
identity, unrequested-security ambiguity, valuation holes/error order, legacy
resume, exact Snapshot composition, changed input/implementation, ancestor symlink
replacement and full-validation rejection of unrelated damage. Product code did
not change during the real runs. No full suite was requested.

## Execution incident

Earlier incomplete logs predate the host boot at 2026-09-22 09:00:30. A 30-second
cross-call probe passed after restart. The first recovery added periodic Python
traceback dumps and exited 139 at the second dump. The kernel reported SIGSEGV;
the address resolves to `_Py_DumpTracebackThreads`. This supports failure in the
added diagnostic, not a product deadlock, and does not establish the cause of every
earlier lost session handle.

Removing that diagnostic and using external resource sampling allowed both runs
to finish: before 2649.247s, after 2561.156s. The totals cover different phase sets
and are not an overall speedup ratio. Failed logs remain in the workspace review
directory; `execution-diagnosis.json` preserves the finding.

## Publication and full-Views limits

The prior publication log totals 8938.381s: 4650.703s in 19 closure spans and
4287.512s between markers without function-level attribution (`publication-profile.json`).
Two financial spans reference different commits, not duplicate checks of one commit.
Further publication work needs a separate ticket with detailed profiling. No new
candidate publication occurred here.

The frozen plan contains 17,900 Views, 3,580 of each kind (`cost-shape.json`). Costs
separate into initial validation per Reader/operation, global financial admission
per policy/cutoff, history preparation when security changes, and per-session
PIT/membership/valuation/output work. The two slots can evict alternating inputs;
a new process still pays validation costs.

The sample is only 15 sessions; full financial windows span years. Membership and
derived JSON alone occupy about 35.9 MB and 16.6 MB. Output volume and per-session
work remain significant. No precise 17,900-View ETA is justified. Fresh Event/Market
startup timings and full-scale throughput were not measured.

## Reproduction

Use `profile_real.py` in an isolated review directory with archived base `src/`
under `baseline/`. Run before and after sequentially with their respective
`PYTHONPATH` and `PYTHONDONTWRITEBYTECODE=1`. Validate existing terminal results
before considering a rerun. The script denies formal-root writes and supplier
connections, compares exact bytes and exercises public build/resume.
