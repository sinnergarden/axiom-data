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
- an implementation ref derived from the actual builder type plus a controlled
  implementation revision, normalized builder configuration, and its digest;
- fixed dependency commit refs used for cross-domain validation;
- relative output paths, row count, content digests, logical content digest,
  validation summary, and creation time.

PR2 supports only an empty `patch_ids` sequence. A non-empty sequence is
rejected explicitly because no immutable patch artifact or domain patch
semantics have been approved.

The Phase 1 canonical physical file is deterministic JSON. Reproducibility is
defined over the contract schema and logical rows; it does not introduce a
Parquet or Qlib byte-equivalence requirement.

The DomainCommit ID is the full SHA-256 digest of the deterministic manifest
projection, prefixed by its domain. That projection includes the domain,
contract version/digest, parent ref, ordered raw/patch refs, builder
implementation ref, normalized configuration, dependency refs, output metadata,
logical output digest, and validation summary. `created_at`, the ID itself, and
the identity digest field are excluded to avoid a circular or wall-clock-based
identity. A supplied `commit_id` is only an expected derived ID and cannot
override it. The controlled implementation revision must change whenever the
builder's output semantics change.

### Market cross-domain validation

Publishing `market_daily.v1` requires explicit immutable
`trading_calendar.v1` and `security_master.v1` commit IDs. For every market row:

- its canonical symbol suffix determines `SSE` or `SZSE` and must match a
  security identity in the fixed security commit;
- its session must fall in that security identity's frozen half-open interval
  `[list_session, delist_session)`; an unknown start, a pre-listing row, and a
  row on or after `delist_session` are rejected;
- `(exchange, session)` must exist in the fixed calendar commit and have
  `is_open=true`.

The resulting dependency refs include their derived identity, logical content,
and contract digests and are stored in the market commit. No current pointer,
catalog lookup, directory ordering, symlink, or mtime selects either dependency.
This check is identity and basic calendar consistency only; it is not a
historical tradability engine.

### DataSnapshot

A Phase 1 DataSnapshot is an immutable manifest composing exactly one commit
for each of `trading_calendar`, `security_master`, and `market_daily`. It does
not copy canonical rows. Creation verifies that all exact refs exist, use the
registered Phase 1 contracts, match the market commit's fixed dependency refs,
and pass cross-domain validation. Publishing a newer domain commit or snapshot
does not change an older snapshot.

The Snapshot ID is the full SHA-256 digest of its deterministic manifest
projection. Its own `created_at` is excluded. A supplied `snapshot_id` is only
an expected derived ID and cannot override the computed identity.

### Manifest truth and catalog index

Each artifact stores `manifest.json` and a sibling `manifest.sha256`. Payload,
contract, and canonical output digests are checked against the manifest when
loaded. Paths inside manifests are relative to their artifact and cannot escape
it. Consequently RawBatch, DomainCommit, and DataSnapshot resolution does not
depend on the catalog.

All read paths pass through one lexical safe-path check. The data root and its
ancestor chain, each artifact directory, each manifest-relative intermediate
component, and the final file must be real paths rather than symlinks.

`load_domain_commit()` is the local inspection loader. Formal operations use
`validate_domain_commit_closure()`, which recursively verifies parent lineage,
ordered RawBatch refs, empty PR2 patch refs, fixed market dependency commits,
and their local manifests/contracts/outputs. It detects cycles and caches each
validated commit. Domain builds, Snapshot creation/loading, recovery, and
catalog rebuilding use this full closure path.

`catalog.sqlite` is a disposable exact-ID inspection index for raw batches,
domain commits, and snapshots. `rebuild_catalog()` scans and validates artifact
manifests, creates a new database in staging, and atomically replaces only the
catalog. A broken lineage aborts rebuilding rather than being indexed as
verified. Deleting the catalog does not affect artifact interpretation.

### Publication protocol

Every artifact is assembled in a new directory under `staging`, checked and
fsynced, then atomically renamed to its final immutable identity. Existing
targets are never overwritten. RawBatch retries require complete byte
equivalence. DomainCommit and Snapshot retries compare the deterministic
identity projection digest plus content files, so a different retry-time
`created_at` returns the first artifact without changing its manifest. Any
decisive difference is a conflict. Symlink artifact targets and files are
rejected.

Recovery copies an already published immutable closure to a different data
root, removes the disposable catalog, rebuilds it from manifests, validates the
Snapshot's full lineage, and reads the original raw and canonical values. It
does not call the RawBatch writer or reconstruct supplier observations.

## Current boundary

PR2 does not implement a supplier client, non-empty patches, Qlib exports,
compatibility readers, legacy reconciliation, mutable current pointers,
production migration, derived domains, or research artifacts. Those decisions
and implementations require later reviewed work.
