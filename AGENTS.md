# axiom-data working agreement

## Mission

`axiom-data` publishes immutable, versioned data artifacts and snapshot-bound
views. It does not own research Features, Labels, models, strategies, or
backtest semantics.

## Current delivery boundary

The current branch is Phase 1 PR1 only. It may define:

- the repository and Python package skeleton;
- the `market.v1` contract;
- the `/var/lib/axiom-data` logical layout;
- the public domain-build port.

It must not collect supplier data, write RawBatch or DomainCommit artifacts,
compose DataSnapshots, export Qlib, maintain a catalog, switch `current.json`,
or read/write legacy Qsys data. Those operations require separately reviewed
follow-up work.

## Invariants

- A build accepts only explicit identities. `current`, `latest`, and `live` are
  not valid build inputs.
- The public domain-builder shape remains
  `build(parent_commit, raw_batch_ids, patch_ids, contract_version)`.
- Published artifacts are immutable. Staging and atomic publication are future
  implementation requirements, not permission for in-place updates.
- Qlib is a snapshot-bound export view, never the source of truth.
- No symlink or mtime may select an artifact identity.
- Keep dependencies minimal; the PR1 package and tests use the standard library.

## Validation

Run from the repository root:

```text
PYTHONPATH=src python3 -m unittest discover -s tests -v
```
