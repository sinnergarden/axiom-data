# Public bootstrap and canonical checkpoint resume

Collection uses `plan_bootstrap_sources` and `collect_bootstrap_sources` with a
frozen source plan. `bootstrap` then accepts exactly one input mode:

- `domain_inputs`: complete per-domain frozen Raw IDs, contract, config and lineage
  decisions; uses the shared candidate builder with no parent Snapshot.
- `domain_commit_ids`: the complete set of 18 explicit canonical checkpoints;
  independently validates their closure and composition before creating the
  ordinary Snapshot. It avoids repeating successful source collection/builds.

API: `axiom_data.bootstrap(root, run_id=..., domain_inputs=...)`, or use the
`domain_commit_ids` keyword. CLI: `axiom-data --data-root ROOT bootstrap
--run-id RUN --plan plan.json`. The JSON contains exactly one of those keys.

The result is a candidate with `ready_for_consumption=false` and
`stage=REQUIRED_VIEWS_AND_FULL_ADMISSION`. A baseline decision still requires
actual coverage, required Views and admission. The default pointer remains under
that separate acceptance gate. Execution records never substitute for artifact
validation: resuming a checkpoint validates it again and rejects later corruption.

Validation on the real PR7 closure reuses the exact ordinary Snapshot ID, checks
resume, rejects incomplete input modes and detects corrupted canonical bytes.
Full suite: 216 tests PASS (68.138s).

## Physical storage

Use the explicit absolute root `/var/lib/axiom-data` for this V1 deployment,
including bootstrap collection, canonical builds, daily operations and recovery.
The CLI requires `--data-root`; the shared Layout rejects relative paths and
symlink roots. No environment or current-directory fallback selects a root.
The caller may provide another explicit absolute isolated root for a test or
recovery; that does not make it the production root.

Published artifacts are sealed read-only through the existing writer boundary.
Staging and operations remain writer-owned. Legacy workspace Raw, canonical and
operation records are retained evidence pending individual qualification; they
are not a production baseline or an implicit resume source. Any reuse must name
immutable IDs and pass current source admission.
