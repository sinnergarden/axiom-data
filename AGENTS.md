# axiom-data working agreement

## Mission

`axiom-data` publishes immutable, versioned data artifacts and snapshot-bound
views. It does not own research Features, Labels, models, strategies, or
backtest semantics.

## Current delivery boundary

PR1–PR7 have passed independent review. The current delivery is the final V1
operational release: public bootstrap/daily/repair/inspect, immutable partition
reuse, full 2014-to-source-available history admission, recovery, performance and
a read-only acceptance Notebook. Preserve the accepted 56 Data requirements and
469 Feature dependency scope; add no Data domains or Research Features.
Do not merge this release, change SysQ, or enter Research/Core/Trade/UI.
Data root is `/home/liuming/workspace/axiom/data`. See `V1_WORK_LOG.md` for the
terminal gates. Resolve a default pointer once at operation entry; builders
continue to receive only explicit immutable identities.

PR8 must qualify SW2021 and CITIC across the actual 3,601-security historical
union before choosing one industry authority. Preserve each taxonomy separately;
stock_basic/bak_basic are comparison evidence only. Verified small source gaps
are classification_unavailable/source_coverage_gap; uncertain out-date sessions
may be boundary_session_ambiguous. Never synthesize an industry sentinel or fill
across taxonomies. Use a new contract and lineage root, best_effort history, and
close the delist boundary decision before full bootstrap. After qualification
passes, continue the complete V1 gates without another approval pause.

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
