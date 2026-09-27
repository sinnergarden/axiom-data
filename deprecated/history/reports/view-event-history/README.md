# View history preparation evidence

Base: `a95239eeee08ace3f6e9845363442d8aea0c7eed`.

Financial and event Views prepare each requested domain's history once per
projection. Every session still uses the existing PIT selector. Full-scope
financial admission runs before preparation; daily domains keep their existing
Reader path. Memory holds requested securities' histories for this call only:
O(requested history), with no fixed byte ceiling or persistent cache.

## Validation

27 targeted tests passed in 5.402s, without skips. They cover frozen financial
View payload equality, daily direct/derived equivalence, event metadata and
policy/cutoff equivalence, ambiguity, and financial conflict rejection outside
the requested security before preparation.

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests timeout 120s python3 -m unittest -v \
  test_view_event_history test_pr7_projection_batching test_pr7_projection \
  test_pr6_pit test_pr6_coverage test_pr6_final_probes
```

## Real small sample

Both JSON reports identify the exact immutable forensic Snapshot and query:
`688981.SH`, four sessions, 2025-06-10 through 2025-06-13. Each run validates the
complete small Snapshot. Baseline View implementations come from the exact base
commit, using the same unchanged Reader. Entire old/new projection outputs match.

| Measurement | Base | Prepared |
|---|---:|---:|
| History facts calls | 20 | 4 |
| Canonical sequence scans, including admission | 31 | 8 |
| Original resident fixture projection | 24.7ms | 25.0ms |
| Isolated partition replay traversals | 188 | 55 |
| Replay bytes traversed | 1,127,144 | 312,740 |
| Replay projection time | 58.8ms | 32.6ms |

The original fixture stores legacy resident rows; its timings show no speedup.
The second probe writes those same validated real rows into temporary partitions
using the existing partition implementation. It changes only the in-memory
Reader's storage representation, not formal artifacts. Counts include fixed
admission and schema work, so they need not equal one physical read per domain.
Bytes are object traversals, not device traffic or cold-cache measurements.
Times exclude Reader initialization and replay preparation. This small sample
does not establish full-production latency or memory use.

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests timeout 120s python3 \
  tests/view_event_history_probe.py --base a95239eeee08ace3f6e9845363442d8aea0c7eed
# Add --partition-replay for the isolated storage replay.
```

Formal data and bulk state remain unchanged. No full production Snapshot
initialization or integration suite was run. Independent review is pending.
