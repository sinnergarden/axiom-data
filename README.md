# axiom-data

`axiom-data` is the data publication boundary for the Axiom project family. It
turns frozen supplier inputs into immutable market domain commits and composes
fixed data snapshots. Snapshot-bound export views are a later phase.

This branch contains Phase 1 PR2:

- explicit `trading_calendar.v1`, `security_master.v1`, and
  `market_daily.v1` schemas;
- append-only RawBatch artifacts under an explicit data root;
- a public build application port with the stable argument shape
  `build(parent_commit, raw_batch_ids, patch_ids, contract_version)`;
- immutable market DomainCommits with contract and input provenance;
- immutable three-domain DataSnapshots;
- a disposable SQLite catalog rebuilt from manifests;
- standard-library contract and artifact tests.

There is no supplier network client, non-empty patch executor, Qlib exporter,
compatibility reader, production pointer switch, or legacy dual-write in PR2.

## Data root

The logical layout is:

```text
/var/lib/axiom-data/
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
