# ADR-0002: Phase 1 market artifact publication

- Status: Accepted
- Date: 2026-09-05
- Scope: Phase 1 PR2

## Context

PR1 froze the three market v1 contracts and the four-argument public build
boundary. PR2 must make that boundary produce a small, reproducible artifact
chain without relying on a mutable pointer or on SQLite as the source of truth.

## Decision

### RawBatch

A RawBatch is an append-only directory under `raw/batches/<raw_batch_id>`.
Its manifest records the domain, source profile, request metadata, retrieval
time, collector code identity, status/summary, and the relative path, byte
length, and SHA-256 digest of the saved payload. The writer persists the exact
payload bytes it receives; canonicalization happens only when a domain build
reads those bytes.

Publishing the same ID is idempotent only when the complete candidate artifact
is byte-for-byte equivalent. Different content at an existing ID is a hard
conflict. A failed downstream build never removes its RawBatch.

### DomainCommit

`MarketDomainBuilder` implements the unchanged PR1 build executor contract:

```text
build(parent_commit, raw_batch_ids, patch_ids, contract_version)
```

It starts with the complete logical state of an explicit parent, applies raw
batches in caller order, writes a fresh staged result, runs the PR1 domain
validator and any required cross-domain validator, then publishes a new
immutable commit. Equal duplicate key/value rows are idempotent. A conflicting
value for an existing key is rejected instead of using last-write-wins.

The DomainCommit manifest contains:

- the exact domain and contract version, plus the digest and relative path of
  the contract content copied into the artifact;
- an optional parent ref and ordered raw and patch ref arrays;
- builder identity, normalized builder configuration, and its digest;
- fixed dependency commit refs used for cross-domain validation;
- relative output paths, row count, content digests, logical content digest,
  validation summary, and creation time.

PR2 supports only an empty `patch_ids` sequence. A non-empty sequence is
rejected explicitly because no immutable patch artifact or domain patch
semantics have been approved.

The Phase 1 canonical physical file is deterministic JSON. Reproducibility is
defined over the contract schema and logical rows; it does not introduce a
Parquet or Qlib byte-equivalence requirement.

### Market cross-domain validation

Publishing `market_daily.v1` requires explicit immutable
`trading_calendar.v1` and `security_master.v1` commit IDs. For every market row:

- its canonical symbol suffix determines `SSE` or `SZSE` and must match a
  security identity in the fixed security commit;
- `(exchange, session)` must exist in the fixed calendar commit and have
  `is_open=true`.

The resulting dependency refs, including manifest and contract digests, are
stored in the market commit. No current pointer, catalog lookup, directory
ordering, symlink, or mtime selects either dependency. This check is identity
and basic calendar consistency only; it is not a historical tradability engine.

### DataSnapshot

A Phase 1 DataSnapshot is an immutable manifest composing exactly one commit
for each of `trading_calendar`, `security_master`, and `market_daily`. It does
not copy canonical rows. Creation verifies that all exact refs exist, use the
registered Phase 1 contracts, match the market commit's fixed dependency refs,
and pass cross-domain validation. Publishing a newer domain commit or snapshot
does not change an older snapshot.

### Manifest truth and catalog index

Each artifact stores `manifest.json` and a sibling `manifest.sha256`. Payload,
contract, and canonical output digests are checked against the manifest when
loaded. Paths inside manifests are relative to their artifact and cannot escape
it. Consequently RawBatch, DomainCommit, and DataSnapshot resolution does not
depend on the catalog.

`catalog.sqlite` is a disposable exact-ID inspection index for raw batches,
domain commits, and snapshots. `rebuild_catalog()` scans and validates artifact
manifests, creates a new database in staging, and atomically replaces only the
catalog. Deleting the catalog does not affect artifact interpretation.

### Publication protocol

Every artifact is assembled in a new directory under `staging`, checked and
fsynced, then atomically renamed to its final immutable identity. Existing
targets are never overwritten. A completely equivalent candidate is an
idempotent success; any difference is a conflict. Symlink artifact targets and
symlink files are rejected.

## Current boundary

PR2 does not implement a supplier client, non-empty patches, Qlib exports,
compatibility readers, legacy reconciliation, mutable current pointers,
production migration, derived domains, or research artifacts. Those decisions
and implementations require later reviewed work.
