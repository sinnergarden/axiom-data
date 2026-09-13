# axiom-data working agreement

## Model allocation

Follow the workspace-wide model workflow in `../AGENTS.md`. **GPT-6 Astra**
(`gpt-6-astra`) writes implementation, scripts and tests, and owns direction,
contract/semantic decisions and blocker assessment. **GPT-5.6 Luna**
(`gpt-5.6-luna`, reasoning `high`) runs reviewed scripts/tests, performs bounded
read-only inspections, and handles logs, evidence and existing-run monitoring.
Luna does not write or modify code. Independent Astra reviewers own code review
and final acceptance. Give execution tasks explicit artifact/run identities and
only the context needed. Report unavailable models rather than silently changing
this allocation. These rules preserve all scope, immutable-artifact and terminal
outcome requirements; they do not authorize a merge or broaden PR8.

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
Formal Data root is `/var/lib/axiom-data`. Legacy workspace data is retained
forensic/unknown evidence only; it must not receive production bootstrap writes. See `V1_WORK_LOG.md` for the
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

## Review before bulk build

Follow the [V1 independent review checklist](docs/operations/v1-independent-review.md): Gate A must independently approve the formal entrypoint, configs/CLI, recovery, admission, terminal validator, tests, and source-preflight plan before any new full-history collection or large build. Until then, keep `NO_BULK_BUILD`; read-only Raw inspection, small real probes, and already-authorized in-flight work may continue only to an explicit checkpoint, without duplicate starts. After Gate A, run the lightweight domain/identity/calendar preflight, then build, then require Gate B artifact review against final snapshot/view schema, 56-requirements, and 469-dependency-matrix evidence.
