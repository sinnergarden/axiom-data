# axiom-data

`axiom-data` is the data publication boundary for the Axiom project family. It
will turn frozen supplier inputs into immutable domain commits, compose data
snapshots, and export snapshot-bound Qlib views.

This branch contains Phase 1 PR1 only:

- explicit `trading_calendar.v1`, `security_master.v1`, and
  `market_daily.v1` schemas;
- a pure path model for `/var/lib/axiom-data`;
- a public build application port with the stable argument shape
  `build(parent_commit, raw_batch_ids, patch_ids, contract_version)`;
- standard-library contract tests.

There is deliberately no supplier client, data writer, catalog, Qlib exporter,
compatibility reader, production pointer switch, or legacy dual-write in PR1.
A concrete executor for the build port belongs to PR2.

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

## Validate PR1

```text
PYTHONPATH=src python3 -m unittest discover -s tests -v
```
