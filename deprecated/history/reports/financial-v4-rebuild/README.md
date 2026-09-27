# Financial v4 rebuild evidence

Status: IMPLEMENTED, TESTED, RUNTIME_VERIFIED. Awaiting independent PR review.

Implementation: `a7d8571c3adb9a6fb1421d4cf5acf575c3cec22c`.
Base: `577a018e1094ce04c658d945dab00594a0f2d547` (PR #9 merged).
Run: `financial-v4-rebuild-20260921-r1`.

## Semantics

For income, balancesheet, cashflow and indicator, flag 1 supersedes 0 only
inside one RawBatch, one logical record, and equal ann_date **and** f_ann_date.
Different publication dates remain separate observations under existing PIT.
Conflicting preferred-flag content remains ambiguity. Unknown flags fail.
V1/v2/v3 remain readable; v4 publication starts a new lineage.

## Measured optimization

A 531-Raw public v3 build was profiled before the change. Raw load and path
validation dominated. The change removes two traversals used only to recover
IDs/refs and streams financial Raw normalization instead of retaining every
payload. It retains source admission, mapping replay and full closure checks.

| Same real v3 input | Before | Optimized |
| --- | ---: | ---: |
| Raw loads | 4,773 | 3,711 |
| Profiled elapsed | 14.385 s | 12.815 s |

Raw loads fell 22.3%. Logical digest, partitions, source coverage, Raw refs and
dependency refs match exactly (performance-equivalence.json). These elapsed
measurements include profiler overhead and local filesystem cache effects; they
are not a controlled cold-cache benchmark or a promised full-scale speedup.
The optimization is bounded in live Raw payloads; canonical rows and metadata
still use memory proportional to the output.

## Validation

64 targeted tests passed (60 in 32.325 s, four integration fixtures in 5.218 s).
Logs are included. They cover all endpoints, both publication dates, separate
Raw observations, equal-flag ambiguity, duplicates, source admission, ABA,
historical prefix, full-scope rejection, downgrade rejection and bounded Raw
lifetime. The existing published v1 closure was read; v2 replay tests retain its
old ambiguity; a real v3 sample closure also passed. Using a v3 parent for v4
publication was rejected as a different contract lineage.

The public v4 sample build used 531 existing Raw in an isolated root. All 1,952
previously identified keys were checked from 1,034 existing Raw. Then the same
keys were checked in the full published v4 partitions:

| Across visibility transitions of the known keys | Orderable | Ambiguous |
| --- | ---: | ---: |
| operational_pit_v1 | 40 | 1,912 |
| best_effort_vendor_v1 | 1,936 | 16 |

The 16 remaining best-effort cases comprise nine equal-flag conflicts and seven
mixed 0/1 keys whose preferred flag-1 rows themselves conflict. Different-date
operational ties retain their existing behavior. These counts cover the 1,952
known keys, not a new assertion about every possible quality issue.

Actual `002010.SZ / 2026-06-30 / fina_indicator` yields `roe=0.024916` in both
policies. Its exact revision and Raw reference are in publication-validation.json.

## Full financial publication

The authorized rebuild reused **all 230,464 Raw refs**, with no old-contract
parent. Scope remained 3,601 securities, 2014-01-01 through 2026-09-13.

- New commit: `financial_events-7fe86eee9a5e0b64e9fb8cc47c0446a6677c4d4f0de59bef6979244e4d15c04a`
- Contract: `financial_events.v4`
- Rows: **694,151**; partitions: **90**
- Public build plus complete closure and output checks: **1,822.512 s (30m 22.5s)**
- Actual Raw loads: **1,556,668** (includes validation/replay)
- Last sampled process peak RSS: **5.29 GiB**; this is a sampled lower bound,
  not a continuous peak-memory benchmark.
- Published partition/known-key regression: **7.356 s**, PASS
- Old financial manifest unchanged; other commit refs, Snapshot/View refs,
  catalog and current/default pointer checks unchanged.

The public builder returned only after full financial contract/Raw closure
validation. publication-run.log records closure entry and PASS. Inputs, explicit
security-master dependency, input-list digest and manifest digest are recorded
in inputs.json and publication-result.json. The existing replacement candidate
continues to reference v3; this ticket published no replacement Snapshot or Views.

## Reproduction

From the repository root, run the targeted suite:

```sh
PYTHONPATH=src:tests python3 -m unittest test_financial_v4 test_financial_source_revision test_financial_rebuild test_pr6_artifacts test_pr6_pit test_pr6_coverage test_source_completeness test_public_source_scope test_writable_contracts test_view_event_history test_pr6_integration test_v1_candidate -v
```

Some historical tests use the repository's existing external fixture locations.
On the evidence host, the following is read-only and checks the exact published
financial partitions and known-key behavior without Snapshot initialization:

```sh
PYTHONPATH=src python3 reports/financial-v4-rebuild/validate_publication.py
```

It writes its result only to this report directory. `publish_v4.py` records the
one-time authorized public BuildApplication invocation; it is not a new product
entrypoint and refuses to run when the included publication result exists.
No supplier request, Snapshot publication, View build or promotion was performed.
