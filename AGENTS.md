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

V1 industry authority is SW2021 via Tushare index_classify/index_member_all.
Apply only the explicitly authorized, versioned 850401.SI → 850412.SI taxonomy
anomaly after all four automatic checks in docs/decisions/pr8-sw2021-authority.md.
Preserve Raw and mapping provenance; no general name-based aliases. CITIC remains
qualification evidence only. Verified supplier gaps are classification_unavailable;
out-date uncertainty is boundary_session_ambiguous. Use a new contract/root,
honest best_effort history, and continue all original V1 gates after validation.
The full-union comparison, official delist mapping and resume hardening are done.

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
