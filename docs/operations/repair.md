# Frozen-input repair

`axiom_data.repair(data_root, run_id=..., snapshot_id=..., domain_inputs=...)`
uses the same builder and candidate validation path as `assemble_candidate`.
The Snapshot must be an explicit immutable identity. Each domain input supplies
`raw_batch_ids`, `contract_version`, `config`, and the explicit `new_lineage`
boolean. A clean root uses the listed complete frozen Raw inputs; incremental
repair retains the declared parent. Source collection is separate.

The CLI is `axiom-data --data-root ROOT repair --snapshot ID --run-id RUN
--plan domain-inputs.json`. The JSON file has the same domain_inputs shape.
The installed builder implementation and all source/config/dependency identities
are recorded by the normal immutable publisher. Resume verifies the frozen plan
and published artifacts. A changed plan requires another run identity.

A successful result is `CANDIDATE_BUILT`, with
`stage=REQUIRED_VIEWS_AND_FULL_ADMISSION` and `ready_for_consumption=false`.
Required Views and admission must still accept that candidate before publication
as a consumable release. Failure retains successful Raw and the old Snapshot.
The current contracts reject nonempty patch IDs; this repair path uses frozen
Raw with the installed corrected builder or an explicit clean lineage.

Validation: the real PR7 fixture repair preserves other domain IDs and the old
Snapshot, resumes identically, rejects a substituted valid commit, retains Raw
on failure, and supports no-change reuse. Floating repair Snapshot IDs fail
before writing. Full suite: 211 tests PASS (64.350s).
