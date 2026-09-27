# Financial leaf ambiguity

Implementation base: `21382552bfa9531ad2c93c55a7beeeb7d6e66b33` (PR #10).
PR #11 was OPEN when checked; its evidence branch is unchanged. This report belongs to a separate implementation PR.

Unorderable financial revisions now yield a consumption result containing every tied source reference. Only differing fields become null with `AMBIGUOUS_SOURCE_REVISION`; equal fields remain available. Quarter and TTM computations propagate the reason from affected inputs. No canonical observation is removed and no winner is invented.

Full-scope View admission records visible ambiguity intervals before security projection. Future observations do not close intervals at historical cutoffs. Generic strict selection and all publication/observation ordering rules remain unchanged.

New consumption contracts: `financial_leaf_resolution.v1`, `financial_stable.v3`, `pr6_fact_view.v3`, `typed_fact.v2`. Canonical financial v1–v4 loaders are unchanged. Published View v1/v2 replay uses its frozen semantics; new semantics do not rewrite old artifacts. Recovery/catalog changes only dispatch the new View version.

## Evidence

- `targeted-tests.log`: 60 tests PASS in 16.653 seconds.
- `public-tests.log`: 12 tests PASS in 13.879 seconds, including public Reader, FactView/Qlib, recovery and resume. Two integration tests overlap the first run: **70 distinct tests**.
- `census-result.json` / `census-intervals.json`: complete financial v4 partition scan, 694,151 rows, 692,205 logical keys, 90 partitions; 12.761 seconds.
- Operational and market-safe policy: 1,912 conflicting keys/intervals, 5,387 unavailable leaves each. Best-effort: 16 keys/intervals, 23 unavailable leaves.
- Every strict-selector ambiguity was accounted for; every nonconflicting selection at tested revision transitions was identical. Equal fields, complete competing revision references, derived propagation and recorded interval boundaries were checked. Key sets match the preceding complete v4 census.
- `legacy-read.json`: existing v3/v4 real sample DomainCommit closure checks. Published v1/v2 View compatibility is covered by targeted tests.

Reproduce the census from the repository with `PYTHONPATH=src python3 reports/financial-leaf-ambiguity/census.py`. It requires the exact immutable financial commit recorded in the JSON under `/var/lib/axiom-data`; it reads formal artifacts and writes only adjacent report files. This is REPRODUCTION_EVIDENCE, not a product operation.

The tests use isolated roots. No supplier requests, formal publications, full Snapshot validation or full Views were run. These results establish this ticket's targeted behavior, not full production acceptance. Independent review is pending.
