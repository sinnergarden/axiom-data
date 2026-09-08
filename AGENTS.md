# axiom-data working agreement

## Mission

`axiom-data` publishes immutable, versioned data artifacts and snapshot-bound
views. It does not own research Features, Labels, models, strategies, or
backtest semantics.

## Current delivery boundary

PR1–PR6 are merged. PR7 completes the remaining 15 PR4 Data requirements and
bounded D-M2 admission. Follow `src/axiom_data/scope/pr7_scope.v1.json`; preserve
PR5/6 contracts and published artifact compatibility. Deliver a PR7 branch and
commit for review; do not merge PR7. Do not enter D-M3 or modify SysQ, Research,
Trade, Core, accounts or UI. Data root is `/home/liuming/workspace/axiom/data`.

## Invariants

- A build accepts only explicit identities. `current`, `latest`, and `live` are
  not valid build inputs.
- The public domain-builder shape remains
  `build(parent_commit, raw_batch_ids, patch_ids, contract_version)`.
- Published artifacts are immutable. Publication uses staging and atomic rename; never update committed bytes in place.
- Qlib is a snapshot-bound export view, never the source of truth.
- No symlink or mtime may select an artifact identity.
- Keep dependencies minimal; reuse the existing publisher, Reader and evidence validators.

## Validation

Run from the repository root:

```text
PYTHONPATH=src python3 -m unittest discover -s tests -v
```
