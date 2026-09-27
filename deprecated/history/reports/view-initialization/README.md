# View initialization validation reuse

The first financial View initialization repeats Raw reads for canonical provenance
and separately parses canonical partitions before parsing them again for mapping
comparison. This change retains verified scalar Raw provenance within one domain
validation and checks canonical provenance during the existing full mapping
comparison. It retains no Raw payload cache and does not skip source admission,
mapping replay, dependency validation or artifact/path checks.

Unchanged domains and parent Raw references still use their existing closure
rules. A reference absent from the current node's verified evidence is loaded and
verified normally. Every canonical row is still checked before validation can
succeed; no security projection occurs here.

## Baseline and reproduction

Base: `4617ad88c91ee15e58fcadbe5a8cda624e641a38`.
Its `src` tree (`947b1682a4b670a3b7a45efd598a3fdf7c91158f`) exactly matches
implementation `e3090942fe1f932829d105ca393d32e278425ae6` used by the previous
completed measurement in `../view-execution-performance/real-after.json`.
That measurement is reused rather than repeating an unchanged 33-minute run.

The bounded real Raw profile (`profile_sample.py`, `raw-sample.json`,
`raw-sample-profile.txt`) supplements its full initialization counters. These are
inclusive timings, not additive phases. The profile covers at most 100 Raw per
domain, so it does not by itself predict full-scale speedup.

Run `PYTHONPATH=src python3 reports/view-initialization/measure.py OUTPUT_DIRECTORY`
for the new measurement. It uses the same concrete Snapshot, six complete
financial View dependency domains, security `688981.SH`, and 15 sessions
(2026-08-24 through 2026-09-11). Formal-root writes and network connections are
rejected. It compares the entire serialized projection digest, including values,
PIT, missing and provenance, against the baseline, then verifies repeated
initialization loads zero domains.

This measures a dependency-scoped first initialization, not a fresh all-18-domain
Reader or all 17,900 Views. No filesystem cache drop is performed; logical reads
and traversal counts distinguish work reduction from OS cache effects.

## Results

| First initialization | Before | After |
| --- | ---: | ---: |
| Elapsed | 2006.819 s | 1783.550 s |
| Domain loads | 6 | 6 |
| Raw loads | 674,850 | 468,812 |
| Partition traversals | 972 | 729 |
| Logical partition bytes | 36,761,516,144 | 27,571,137,108 |
| Process read characters (`rchar`) | 101,813,167,104 | 82,141,830,178 |
| OS-attributed physical reads (`read_bytes`) | 28,766,957,568 | 32,510,754,816 |
| Process writes (`write_bytes`) | 0 | 0 |

Elapsed falls 11.13%, Raw loads 30.53%, and partition traversal/bytes 25%.
Physical reads increased 13.01%; this run does **not** demonstrate a reduction in
physical disk traffic. Filesystem cache/read-ahead conditions differ between runs.
The deterministic work reduction is fewer Raw loads and complete partition passes.
First validation still takes nearly 30 minutes; this is a bounded reduction in
repeated work, not elimination of the remaining fixed cost.

The complete output digest matches the baseline:
`sha256:a0b9e9bff2e3dee4bd3ab019c6a598e88585b080f8e194b1af77e408d40e84de`.
That comparison includes values, PIT, missing reasons, provenance and ordering.
The unchanged projection was executed only to verify equality; its 213.996 s is
not an initialization metric or a per-View optimization claim.

Repeated initialization takes 2.908 s with zero domain loads and zero partition
traversals. Context exit takes 1.430 s. Total measurement is 2003.000 s, with zero
process writes in every measured phase. `real-after.json` holds the measurements;
`terminal-verification.json` independently checks identities, output equality,
reuse counters and evidence digests.

Targeted tests: **49/49 PASS** (`targeted-tests.txt`). This includes complete
artifact closure tests, financial ambiguity behavior, cached-input corruption,
ancestor symlink replacement on reuse/context exit, zero-load normal reuse, and
a new mutation-during-initialization regression. The new provenance regression
also verifies that already checked Raw is not reloaded for that check.

Reproduce with:

```sh
PYTHONPATH=src:tests python3 -m unittest test_view_validation test_observation_validation_cache test_reader_validation_reuse test_fact_publication_validation test_financial_leaf_ambiguity test_artifacts -v
```

Full suite and full Views were not run. No formal artifact was written or rebuilt.
PIT, source contracts, schemas, persisted identities and per-View algorithms are
unchanged. Independent review is pending.
