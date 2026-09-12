# I/O amplification fix review

Scope: the two high-priority paths in the supplied diagnosis. The PR is stacked
on the PR8 development branch; its diff is independent of earlier V1 work.

- SnapshotReader retains only verified PR7 ancestry IDs. First request-coverage
  construction loads each distinct Raw once; subsequent queries use its own
  interval index. No parent triggers another full ancestor traversal.
- Collection persists one small atomic/fsynced request result at a time. The
  compatible aggregate summary is written at operation entry and exit, rather
  than after each request. Resume validates checkpoint plan/request bindings and
  rechecks the actual immutable Raw and source request before reuse.
- The catalog's internal Reader follows the same metadata initialization path.
  Only ancestors reachable from that Snapshot's PR7 heads are retained.

Independent Astra review approved the bounded change after the catalog Reader
integration finding was fixed. Luna executed the checks; implementation and test
code were written by Astra.

Full suite: **259/259 PASS**, 76.779 seconds. `git diff --check` passed.
Log: `/tmp/axiom-io-fullsuite-20260912.log`.

Validation probes:

- 8 and 16 requests: exactly N+2 state writes, only two aggregate writes; doubling
  the requests grows serialized checkpoint bytes by less than 2.1 times.
- 3 and 6 appended real history nodes: every ancestor commit loads once during
  Reader initialization; the larger chain adds exactly 3 holder-domain loads.
  Request-coverage queries add no DomainCommit loads, and repeated queries add
  no Raw loads after the first interval-index construction.
- Termination immediately after a durable per-request checkpoint resumes without
  repeating that supplier request, despite an older aggregate summary.
- Existing v1 summaries resume; wrong checkpoint plan bindings fail. Corrupted
  completed Raw produces FAILED without silently recollecting a replacement.
- Existing observed-Raw binding tests and real v1/v2 recovery/catalog tests pass.

Targeted logs: `/tmp/axiom-io-targeted-20260912-collection.log`,
`/tmp/axiom-io-targeted-20260912-pr7-index-r2.log`,
`/tmp/axiom-io-targeted-20260912-v1-operations.log`,
`/tmp/axiom-io-targeted-20260912-observed-raw.log`,
`/tmp/axiom-io-recovery-20260912.log`.

Counters cover these code paths, not physical disk traffic or the total cost of
all mapper history replay. Raw publication retries and repeated Raw metadata
reads inside validation remain separate medium-priority opportunities. The
partition integrity-before-return pass remains intact. No bulk build or
production data rewrite is part of this PR; overall V1 Gate A remains pending.
