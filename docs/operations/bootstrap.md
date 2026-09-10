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
