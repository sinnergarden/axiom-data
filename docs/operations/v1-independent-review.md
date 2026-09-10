# V1 Independent Review Checklist

Status: **NOT_READY**. This file defines the future review entry point; it does not record an independent review or an approval.

The independent Astra reviewer makes the final decision. Luna high may execute the approved reruns and evidence collection. The review has two gates with different inputs.

## Review gates and build lock

The required execution order is:

`code changes → independent code review → small-real/lean-full source preflight → bulk build → independent artifact review`

**Gate A — code review, before any full collection or bulk build.** Its inputs are the frozen code commit; formal configurations and CLI; executable end-to-end entrypoint, recovery, admission, and terminal-validator implementations; existing immutable Raw and minimal real fixtures; the 56-requirements and 469-dependency-matrix scope authority; and the tests and source-preflight plan. A full baseline snapshot is not required yet. Mapper unit tests alone do not pass this gate. The current code gate is **NOT_READY**, so the status is **`NO_BULK_BUILD`**: do not start new full-history collection or a new large build. Existing immutable Raw and checkpoints remain preserved. Existing Raw may be inspected read-only, and a small real probe or an already-authorized in-flight task may continue to its explicit checkpoint; do not repeat or silently start collection.

The small-real/lean-full preflight must check domain composition, identity, and calendar relationships before an expensive closure. If the preflight finds a mapper defect, reuse immutable Raw and rebuild only the affected domain. If it finds a view defect, rebuild only the view. Fetch only the corresponding requests when Raw acquisition is insufficient, truncated, or scoped incorrectly; never re-fetch all history by default.

**Gate B — artifact review, after the bulk build.** Its inputs are final snapshot IDs and schema versions; published v1 and current v2 view IDs and schema versions; and the actual 56-requirements and 469-dependency-matrix evidence for scope, admission, performance, recovery, and Notebook results. It independently checks lineage, scope, readers, views, recovery, and report reconciliation. A preflight or builder result cannot substitute for this gate. If any prerequisite is missing, retain `NOT_READY`; do not infer readiness from builder `PASS` records.

## Review checks

1. **Rebuild on a new server**

   Follow repository documentation with manual commands and no Agent dependency. Record the exact commit, inputs, commands, outputs, and environment. Confirm that the procedure does not silently fetch history or recreate `first_observed` evidence.

2. **Scope and report completeness**

   Verify the complete 56-requirements and 469-dependency-matrix scopes. A probe, sample, or representative subset cannot stand in for either scope. Reconcile each evidence report to its run plan, artifact, digest, and terminal validator.

3. **PIT and observation policy**

   Independently rerun key counterexamples for PIT prefix handling, ABA, late observations, and best-effort observations. Check observation-time policy, late-data treatment, and source qualification. Fail closed on an unrequested missing member; reader closure must not accept a partial request.

4. **Factor exclusions and Raw invariants**

   Verify that the 16,749 factor/calendar exclusions are the exact frozen set, with a reproducible difference against the inventory and source/object bindings. Confirm Raw is unchanged, legal factor values keep their original provenance, and calendar and identity rules remain strict. Do not broaden an exclusion by symbol, date, or error type.

5. **Snapshot and view compatibility**

   Reload the immutable old snapshot and both published v1 and current v2 views independently. Check schema, lineage, manifests, view contracts, and reader behavior. Do not require derived IDs to match across snapshots when the immutable inputs or lineage differ.

6. **Fresh-root and daily recovery**

   In a fresh copied root, run the full-root recovery path with network disabled and exercise multiple daily cases: T+1, no-change, late data, and recovery after interruption. Validate outputs and continuation state. Run corruption probes only in the copied root; never mutate the publish root or old Raw.

7. **Direct Qlib performance and accounting**

   Run the direct-Qlib Notebook against the full root. Label synthetic and real-data measurements separately, reconcile them to the actual full-root rows and timings, and list any missing accounting or performance evidence. Do not present synthetic performance as production evidence.

## Decision record

The reviewer must publish `Approve` or `Reject`, with P0/P1/P2 findings, tested scope, evidence references, and limitations. `NOT_READY` remains the outcome until all prerequisites and checks are independently verified. A builder PASS, a partial rerun, or a report digest alone cannot produce approval.
