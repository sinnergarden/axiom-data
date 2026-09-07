# axiom-data working agreement

## Mission

`axiom-data` publishes immutable, versioned data artifacts and snapshot-bound
views. It does not own research Features, Labels, models, strategies, or
backtest semantics.

## Current delivery boundary

PR1–PR5 are merged. PR6 implements PIT universe/industry, financial observations,
valuation and the four PR4-required single-quarter/TTM leaves, with frozen source,
reconciliation and recovery evidence. Follow the exact PR4-derived scope in
`src/axiom_data/scope/pr6_scope.v1.json`. Deliver one PR6 branch/commit; do not merge.
PR7 and total D-M2 acceptance remain deferred. Do not modify SysQ, Research,
Trade, accounts or UI. Data root is `/home/liuming/workspace/axiom/data`.

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
