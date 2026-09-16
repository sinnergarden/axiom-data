# axiom-data

`axiom-data` is the data publication boundary for the Axiom project family. It
turns frozen supplier inputs into immutable market domain commits and composes
fixed data snapshots. Phase 1 PR3 adds one bounded Snapshot-bound consumption
and export proof.

This branch contains the Phase 1 PR3 market vertical slice:

- explicit `trading_calendar.v1`, `security_master.v1`, and
  `market_daily.v1` schemas;
- append-only RawBatch artifacts under an explicit data root;
- a public build application port with the stable argument shape
  `build(parent_commit, raw_batch_ids, patch_ids, contract_version)`;
- immutable market DomainCommits with contract and input provenance;
- immutable three-domain DataSnapshots;
- full parent/raw/dependency closure validation for formal resolution;
- a disposable SQLite catalog rebuilt from manifests;
- an allow-listed Tushare adapter and frozen endpoint SourceProfile;
- a Snapshot-bound read-only market Reader;
- an immutable Qlib-compatible day-frequency binary view;
- direct/view equivalence, production-independent frozen-raw/Qsys
  reconciliation, and RawBatch-only offline rebuild evidence;
- SourceProfile semantic digests bound through RawBatch and DomainCommit
  identities, plus exact calendar request-scope coverage validation;
- standard-library contract and artifact tests.

PR3 remains a fixed 20-security, one-year proof. It does not add a general
supplier framework, non-empty patches, mutable pointers, legacy dual-write,
research Features, or any post-market data domain.

The committed reports under `reports/pr3/` record the reviewed real run without
committing its full RawBatch payloads. `tests/fixtures/` contains only the small
real listing/suspension samples needed for offline tests. Live collection reads
the Tushare credential from `TUSHARE_TOKEN` or Tushare's existing local secure
configuration; no credential is stored in this repository.

The full real closure named in the run report is retained under the external
read-only `/home/liuming/workspace/axiom/data/forensic/` area. It is validation
evidence only; no production `current` pointer is created or changed.

The reviewed run is reproducible with `scripts/run_pr3_market_slice.py`; it
requires two nonexistent temporary data-root paths, the frozen Qsys parquet
path, and a report output directory. Collection is the only networked stage.
The script copies only the resulting `raw/batches` closure into the second root
before rebuilding every downstream artifact offline.

## Data root

The logical layout is:

```text
/home/liuming/workspace/axiom/data/
  raw/batches/
  raw/objects/
  canonical/<domain>/commits/
  canonical/<domain>/objects/
  derived/<name>/commits/
  derived/<name>/objects/
  snapshots/
  exports/qlib/
  patches/
  build_provenance/
  staging/
  reports/
  current.json
  catalog.sqlite
```

`DataRootLayout` only derives these paths; it does not create or mutate them.

## Validate Phase 1

```text
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

## PR6 PIT and financial facts

PR6 scope and contracts are documented in [ADR 0005](docs/adr/0005-pr6-pit-financial.md).
The Snapshot Reader exposes as-of revisions, membership, historical union and financial
derivations. PR6 Fact/Qlib materialization is explicit and snapshot-bound.
PR7 leaves and D-M2 total acceptance remain deferred.
# Holder-count source admission

For `tushare_pr7_holder.v3` / `stk_holdernumber`, `end_date` is required
for the canonical holder-count logical event identity. A null `end_date`
is never inferred from `ann_date`, surrounding records, or a quarter end.
The complete supplier response remains in RawBatch, including non-null
`holder_num` values on rows with a null report date.

Such rows are `unmaterializable` with reason `MISSING_REPORT_DATE` and
affected field `end_date`. Raw summary `canonical_admission` records counts,
row indexes and fingerprints; `pr7_source.canonicalization_report(raw)`
returns that admission result with its immutable `raw_ref`. They produce no
canonical holder-count event. This is source-to-canonical admission, not PIT
qualification. Existing null-date/null-count exclusion remains compatible.
Invalid non-null dates and conflicting canonical revisions remain errors.

Supplier request completeness is evaluated separately: a complete response
may contain unusable rows. Downstream requirement coverage must assess the
reported gap; collection completion alone does not admit a requirement or
declare the entire endpoint history incomplete. Existing RawBatches and
checkpoints are preserved on resume.

This admission clarification supersedes the older v3 profile prose that
rejected missing report dates with non-null counts. Frozen profile bytes and
digests are retained for existing Raw compatibility. Existing valid and
null-date/null-count responses keep their original summary shape; their
admission report is computed from immutable Raw when requested. New summary
metadata is added only for the newly admitted response shape.
