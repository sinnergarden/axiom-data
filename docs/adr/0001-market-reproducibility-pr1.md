# ADR-0001: Market reproducibility PR1 boundary

- Status: Accepted
- Date: 2026-09-04
- Scope: Phase 1 PR1

## Context

- **Fact:** The frozen legacy scene is identified by Qsys commit
  `c969c74a66b148fa66396c3896e48760004683d4` and forensic archive
  `sysq-c969c74-20260904`.
- **Fact:** The Phase 0 audit found mutable canonical data, a globally updated
  Qlib directory, and legacy inputs that cannot all be replayed from a single
  existing identity.
- **Inference:** Adding another data-version string without changing the
  publication boundary would not make those inputs reproducible.
- **Existing-rule judgment:** Qsys skill, UC, and harness material informs
  compatibility risks but is not treated as proof of correct data semantics.

## Decision

- **Independent recommendation:** Start with the market domain and retain a
  generic builder shape:

  ```text
  build(parent_commit, raw_batch_ids, patch_ids, contract_version)
  ```

  `BuildApplication` validates and freezes this call before handing it to one
  explicit executor. PR1 defines the port; PR2 will supply the first persistence
  implementation.
- **Independent recommendation:** `trading_calendar.v1`,
  `security_master.v1`, and `market_daily.v1` independently fix primary keys,
  order, nullability, dtypes, and physical units for their DomainCommits.
- **Independent recommendation:** The path model follows the Axiom baseline:
  immutable raw batches, domain commits/objects, snapshots, Qlib exports,
  patches, one build-provenance area, staging, reports, a rebuildable catalog,
  and an operator convenience pointer.
- **Independent recommendation:** Dynamic names (`current`, `latest`, `live`)
  are rejected by the build boundary. `current.json` is not an input identity.

## PR1 acceptance boundary

PR1 contains only the repository skeleton, market contract, pure path model,
public build port, and unit tests. It performs no filesystem publication and
does not create a CLI that could be mistaken for a working data builder.

The following require separate review:

- PR2: append-only RawBatch, immutable market DomainCommit, and DataSnapshot;
- PR3: clean-staging, exact-schema, atomically published Qlib View;
- PR4: read-only compatibility Reader and legacy/new comparison.

No production pointer, Qsys canonical path, daily flow, Feature, Label, model,
ledger, strategy, or backtest behavior changes in PR1.
