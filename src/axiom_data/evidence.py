"""Fixed PR5 evidence consistency checks; this is not a reporting platform."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from axiom_data.artifacts import ArtifactError


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ArtifactError(f"PR5 evidence {name} must be an object")
    return value


def validate_pr5_evidence(
    run_manifest: Mapping[str, Any],
    direct_qlib: Mapping[str, Any],
    dm1_reconciliation: Mapping[str, Any],
    qsys_reconciliation: Mapping[str, Any],
    offline_recovery: Mapping[str, Any],
    acceptance_matrix: Mapping[str, Any],
) -> None:
    """Require PASS results to agree with the immutable identities they describe."""

    run = _mapping(run_manifest, "run manifest")
    artifacts = _mapping(run.get("artifacts"), "artifact map")
    refs = _mapping(run.get("artifact_refs"), "artifact refs")
    snapshot_ref = _mapping(refs.get("snapshot"), "Snapshot ref")
    qlib_ref = _mapping(refs.get("qlib_view"), "QlibView ref")
    if snapshot_ref.get("snapshot_id") != artifacts.get("snapshot"):
        raise ArtifactError("PR5 run Snapshot ref is inconsistent")
    if qlib_ref.get("view_id") != artifacts.get("qlib_view"):
        raise ArtifactError("PR5 run QlibView ref is inconsistent")
    raw_batches = run.get("raw_batches")
    if (
        not isinstance(raw_batches, list)
        or not raw_batches
        or any(not isinstance(raw, Mapping) for raw in raw_batches)
        or any(raw.get("schema_version") != "raw_batch.v2" for raw in raw_batches)
    ):
        raise ArtifactError("PR5 run contains a non-v2 RawBatch")

    checks = _mapping(direct_qlib.get("checks"), "direct/Qlib checks")
    direct_snapshot = _mapping(direct_qlib.get("source_snapshot_ref"), "direct Snapshot ref")
    direct_view = _mapping(direct_qlib.get("qlib_view_ref"), "direct QlibView ref")
    if (
        direct_qlib.get("status") != "PASS"
        or not checks
        or any(value is not True for value in checks.values())
        or direct_qlib.get("mismatches") != []
        or direct_snapshot.get("snapshot_id") != snapshot_ref.get("snapshot_id")
        or direct_snapshot.get("identity_digest") != snapshot_ref.get("identity_digest")
        or direct_view.get("view_id") != qlib_ref.get("view_id")
        or direct_view.get("identity_digest") != qlib_ref.get("identity_digest")
    ):
        raise ArtifactError("PR5 direct/Qlib PASS is inconsistent")

    for report, name in (
        (dm1_reconciliation, "D-M1 reconciliation"),
        (qsys_reconciliation, "Qsys reconciliation"),
    ):
        if report.get("status") != "PASS" or report.get("snapshot_id") != artifacts.get("snapshot"):
            raise ArtifactError(f"PR5 {name} is not bound to the run Snapshot")
    if dm1_reconciliation.get("mismatch_count") != 0 or dm1_reconciliation.get("mismatches") != []:
        raise ArtifactError("PR5 D-M1 reconciliation PASS contains mismatches")

    logical = _mapping(offline_recovery.get("domain_logical_equality"), "offline logical equality")
    rebuilt = _mapping(offline_recovery.get("rebuilt_artifacts"), "offline artifact map")
    if (
        offline_recovery.get("status") != "PASS"
        or offline_recovery.get("identity_equality") is not True
        or offline_recovery.get("anchor_semantics_equal") is not True
        or not logical
        or any(value is not True for value in logical.values())
        or dict(rebuilt) != dict(artifacts)
    ):
        raise ArtifactError("PR5 offline recovery PASS is inconsistent")

    gates = _mapping(acceptance_matrix.get("gates"), "acceptance gates")
    if (
        acceptance_matrix.get("status") != "PASS"
        or not gates
        or any(_mapping(value, "acceptance gate").get("status") != "PASS" for value in gates.values())
        or run.get("status") != "PASS"
    ):
        raise ArtifactError("PR5 acceptance PASS is inconsistent")


__all__ = ["validate_pr5_evidence"]
