# Reader security projection

Implementation base: `e7f450d9b97d685db8e329d0c7c3b175b32e0ca7`.
Only Reader consumption changes. No schema, artifact identity, PIT selector,
history prefetch, checkpoint or resume changes.

## Behavior and bounds

`market_daily()` and `facts()` share a Reader-local projection cache for
session-partition domains and requests of at most 64 securities. The key includes
domain, immutable partition object ID and the normalized security set. Cache
storage is capped at 128 entries and 8 MiB of encoded row payload per Reader.
Oversized projections use the complete read path without cache retention; larger
security sets and unfiltered reads use the original path.

`benchmark_daily` is excluded: its identity field is `benchmark`, not `symbol`.
Its existing public `facts(symbols=...)` behavior remains an empty result; this
optimization does not introduce benchmark-selector semantics.

On a miss, the complete month is read using existing digest, byte-count,
partition-key, ordering and row-count checks. The iterator is exhausted before
projected rows are exposed or cached. Initial full Snapshot validation is
unchanged. Financial and membership selection/admission remain untouched.

Device/inode/size/ctime are invalidation guards only, not artifact identity or
data selection. No mtime/current/latest lookup is used. A changed file is fully
reread and rejected if corrupt, including same-size edits with restored mtime.
Every new Reader starts empty. Returned nested rows are decoded afresh so caller
mutation cannot alter the cache. The cache is ephemeral and writes no files.

## Real query-phase comparison

See [real-query.json](real-query.json) for exact Snapshot, commit and object IDs.
The chosen real month contains 70,833 rows and 24,125,498 bytes.
Both implementations query `688981.SH` on ten sessions from the same explicit
candidate Snapshot; all returned rows compare equal.

| Metric | Previous path | Projection cache |
| --- | ---: | ---: |
| Complete partition reads | 10 | 1 |
| Partition bytes traversed | 241,254,980 | 24,125,498 |
| Digest plus parse logical bytes | 482,509,960 | 48,250,996 |
| Ten-query elapsed seconds | 2.9217 | 0.2864 |
| First-query seconds | 0.2889 | 0.2847 |
| Retained payload bytes | 0 | 7,375 |

Both paths start with an empty application cache. OS page cache was **not**
flushed; these are logical file reads, not physical device traffic. The first
query still reads the full month. Benefits apply to repeated queries reusing
retained security/month projections, not one-off reads or unbounded history.

The reproduction harness verifies Snapshot/commit identities and their binding,
then validates the complete selected partition. It invokes the actual
`Reader.market_daily` query path against that bounded composition; the baseline
uses the former session-read behavior. Full production Snapshot initialization
and closure validation are **NOT RUN** and excluded from timings. This does not
claim to accelerate startup, PIT selection, or end-to-end full Views.

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests timeout 120s python3 tests/reader_projection_probe.py \
  --root /var/lib/axiom-data \
  --snapshot snapshot-aba8944e426923db7a9f3e08a977c0e8a3a6fe12ba8b92561dfffd74ac285420 \
  --month 2026-08 --symbol 688981.SH
```

The script is REPRODUCTION_EVIDENCE, not a production entrypoint. It never
publishes or requests supplier data; the formal root remained read-only.

## Targeted regression

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests timeout 120s python3 -m unittest -v \
  test_session_partition_reads test_daily_pit_partition_reads test_pr6_coverage \
  test_pr6_final_probes test_partition_stream test_reader_validation_reuse
```

16 tests passed in 6.977 seconds. After adding the maximum-security cache-key
bound, the two session-partition tests passed again in 0.203 seconds, including
the 65-security fallback. `git diff --check` passed.

Coverage includes same concrete synthetic Snapshot with cached/uncached public
queries, field projection and ordering, missing security results, PIT revisions,
caller mutation isolation, complete-input count rejection, unrequested-security
corruption, same-size tampering with restored mtime, cache bounds, and new Reader
full validation. Full suite, production Snapshot startup and full Views were not
run under this bounded ticket.

## PR review correction

Excluded benchmark_daily from the symbol projection cache and added a public
Reader.facts regression covering bounded/unbounded symbol filtering and normal
benchmark reads. The original 16 tests plus this counterexample passed: 17 tests
in 6.916 seconds. The real probe was rerun after the fix; real-query.json and
the table above contain the updated measurements. Values remain equal and full
partition reads remain 10 versus 1. No other domain semantics changed.
