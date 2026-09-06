"""Fixed PR5 evidence consistency checks; this is not a reporting platform."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from axiom_data.artifacts import ArtifactError


_REF_NAMES = (
    "raw_batches",
    "domain_commits",
    "snapshot",
    "adjusted_price_view",
    "market_replay_view",
    "qlib_view",
)
_RAW_REF_FIELDS = (
    "domain",
    "raw_batch_id",
    "manifest_digest",
    "payload_digest",
    "schema_version",
    "source_profile_ref",
    "source_profile_version",
    "source_profile_digest",
)
_GATES = frozenset(
    {"D01", "D02", "D03", "D04", "D05", "D10", "D11", "D12", "D13", "D14", "D15"}
)


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ArtifactError(f"PR5 evidence {name} must be an object")
    return value


def _sequence(value: object, name: str) -> Sequence[Any]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ArtifactError(f"PR5 evidence {name} must be a list")
    return value


def _digest(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.startswith("sha256:") or len(value) != 71:
        raise ArtifactError(f"PR5 evidence {name} digest is invalid")
    return value


def _projection(value: object) -> object:
    """Compare identity/content while allowing regenerated manifest timestamps."""

    if isinstance(value, Mapping):
        return {
            key: _projection(item)
            for key, item in value.items()
            if key != "manifest_digest"
        }
    if isinstance(value, list):
        return [_projection(item) for item in value]
    return value


def _pass_report(report: Mapping[str, Any], name: str) -> None:
    checks = _mapping(report.get("checks"), f"{name} checks")
    if (
        report.get("status") != "PASS"
        or not checks
        or any(value is not True for value in checks.values())
        or report.get("mismatches") != []
    ):
        raise ArtifactError(f"PR5 {name} PASS is inconsistent")


def validate_pr5_evidence(
    run_manifest: Mapping[str, Any],
    direct_qlib: Mapping[str, Any],
    dm1_reconciliation: Mapping[str, Any],
    qsys_reconciliation: Mapping[str, Any],
    offline_recovery: Mapping[str, Any],
    acceptance_matrix: Mapping[str, Any],
) -> None:
    """Require every PR5 PASS to bind the same immutable D-M1 closure."""

    run = _mapping(run_manifest, "run manifest")
    artifacts = _mapping(run.get("artifacts"), "artifact map")
    refs = _mapping(run.get("artifact_refs"), "artifact refs")
    if set(refs) != set(_REF_NAMES):
        raise ArtifactError("PR5 run artifact refs are incomplete")
    raw_refs = _sequence(refs["raw_batches"], "RawBatch refs")
    domain_refs = _mapping(refs["domain_commits"], "DomainCommit refs")
    snapshot_ref = _mapping(refs["snapshot"], "Snapshot ref")
    adjusted_ref = _mapping(refs["adjusted_price_view"], "adjusted view ref")
    replay_ref = _mapping(refs["market_replay_view"], "replay view ref")
    qlib_ref = _mapping(refs["qlib_view"], "QlibView ref")
    if not raw_refs or not domain_refs:
        raise ArtifactError("PR5 run RawBatch/DomainCommit refs are empty")
    for raw in raw_refs:
        raw = _mapping(raw, "RawBatch ref")
        if any(field not in raw for field in _RAW_REF_FIELDS) or raw.get("schema_version") != "raw_batch.v2":
            raise ArtifactError("PR5 run RawBatch ref is incomplete or non-v2")
        _digest(raw["manifest_digest"], "RawBatch manifest")
        _digest(raw["payload_digest"], "RawBatch payload")
        _digest(raw["source_profile_digest"], "RawBatch SourceProfile")
    for domain, ref_value in domain_refs.items():
        ref = _mapping(ref_value, f"{domain} DomainCommit ref")
        if ref.get("domain_commit_id") != artifacts.get(domain):
            raise ArtifactError("PR5 DomainCommit artifact map is inconsistent")
        for name in ("manifest_digest", "identity_digest", "contract_digest", "logical_content_digest"):
            _digest(ref.get(name), f"{domain} {name}")
    for name, ref, id_field in (
        ("snapshot", snapshot_ref, "snapshot_id"),
        ("adjusted_price_view", adjusted_ref, "view_id"),
        ("market_replay_view", replay_ref, "view_id"),
        ("qlib_view", qlib_ref, "view_id"),
    ):
        if ref.get(id_field) != artifacts.get(name):
            raise ArtifactError(f"PR5 run {name} ref is inconsistent")
        _digest(ref.get("manifest_digest"), f"{name} manifest")
        _digest(ref.get("identity_digest"), f"{name} identity")
    for name, ref in (("adjusted", adjusted_ref), ("replay", replay_ref), ("Qlib", qlib_ref)):
        _digest(ref.get("content_digest"), f"{name} content")
    if (
        qlib_ref.get("derived_view_id") != adjusted_ref.get("view_id")
        or any(
            qlib_ref.get(name) != adjusted_ref.get(name)
            for name in ("anchor_session", "pit_policy", "pit_qualification", "decision_cutoff")
        )
    ):
        raise ArtifactError("PR5 Qlib/Derived PIT binding is inconsistent")
    run_raw = _sequence(run.get("raw_batches"), "run RawBatch list")
    projected_run_raw = [
        {field: _mapping(raw, "run RawBatch").get(field) for field in _RAW_REF_FIELDS}
        for raw in run_raw
    ]
    if projected_run_raw != list(raw_refs):
        raise ArtifactError("PR5 run RawBatch list differs from its closure refs")

    catalog = _mapping(run.get("catalog_rebuild"), "catalog rebuild")
    catalog_counts = _mapping(catalog.get("artifact_type_counts"), "catalog counts")
    lookups = _mapping(catalog.get("exact_view_lookups"), "catalog view lookups")
    if (
        catalog.get("status") != "PASS"
        or catalog.get("entries") != sum(catalog_counts.values())
        or any(value is not True for value in lookups.values())
        or set(lookups) != {"adjusted_price_view", "market_replay_view", "qlib_view"}
        or any(catalog_counts.get(name) != 1 for name in lookups)
    ):
        raise ArtifactError("PR5 catalog evidence is inconsistent")

    _pass_report(direct_qlib, "direct/Qlib")
    if (
        direct_qlib.get("source_snapshot_ref") != snapshot_ref
        or direct_qlib.get("adjusted_price_view_ref") != adjusted_ref
        or direct_qlib.get("qlib_view_ref") != qlib_ref
        or direct_qlib.get("pit_binding")
        != {name: adjusted_ref[name] for name in ("pit_policy", "pit_qualification", "decision_cutoff", "anchor_session")}
    ):
        raise ArtifactError("PR5 direct/Qlib refs are inconsistent")

    _pass_report(dm1_reconciliation, "D-M1 reconciliation")
    expected_dm1_raw = [
        raw for raw in raw_refs if _mapping(raw, "RawBatch ref")["domain"] not in {"trading_calendar", "security_master", "market_daily"}
    ]
    if (
        dm1_reconciliation.get("snapshot_id") != snapshot_ref.get("snapshot_id")
        or dm1_reconciliation.get("raw_batch_refs") != expected_dm1_raw
        or dm1_reconciliation.get("mismatch_count") != 0
    ):
        raise ArtifactError("PR5 D-M1 reconciliation refs are inconsistent")

    expected_market_raw = [
        raw for raw in raw_refs if _mapping(raw, "RawBatch ref")["domain"] == "market_daily"
    ]
    if (
        qsys_reconciliation.get("status") != "PASS"
        or qsys_reconciliation.get("contract_or_build_bug_count") != 0
        or qsys_reconciliation.get("snapshot_id") != snapshot_ref.get("snapshot_id")
        or qsys_reconciliation.get("snapshot_ref") != snapshot_ref
        or qsys_reconciliation.get("raw_batch_refs") != expected_market_raw
    ):
        raise ArtifactError("PR5 Qsys reconciliation refs are inconsistent")

    logical = _mapping(offline_recovery.get("domain_logical_equality"), "offline logical equality")
    source_artifacts = _mapping(offline_recovery.get("source_artifacts"), "offline source artifacts")
    rebuilt_artifacts = _mapping(offline_recovery.get("rebuilt_artifacts"), "offline rebuilt artifacts")
    source_refs = _mapping(offline_recovery.get("source_refs"), "offline source refs")
    rebuilt_refs = _mapping(offline_recovery.get("rebuilt_refs"), "offline rebuilt refs")
    offline_direct = _mapping(offline_recovery.get("direct_qlib_equivalence"), "offline direct/Qlib")
    offline_catalog = _mapping(offline_recovery.get("catalog_rebuild"), "offline catalog")
    offline_catalog_counts = _mapping(
        offline_catalog.get("artifact_type_counts"), "offline catalog counts"
    )
    offline_lookups = _mapping(
        offline_catalog.get("exact_view_lookups"), "offline catalog view lookups"
    )
    _pass_report(offline_direct, "offline direct/Qlib")
    if (
        offline_recovery.get("status") != "PASS"
        or offline_recovery.get("network_calls") != 0
        or offline_recovery.get("identity_equality") is not True
        or offline_recovery.get("anchor_semantics_equal") is not True
        or offline_recovery.get("pit_binding_equal") is not True
        or not logical
        or any(value is not True for value in logical.values())
        or dict(source_artifacts) != dict(artifacts)
        or dict(rebuilt_artifacts) != dict(artifacts)
        or dict(source_refs) != dict(refs)
        or _projection(rebuilt_refs) != _projection(refs)
        or offline_direct.get("source_snapshot_ref") != rebuilt_refs.get("snapshot")
        or offline_direct.get("adjusted_price_view_ref") != rebuilt_refs.get("adjusted_price_view")
        or offline_direct.get("qlib_view_ref") != rebuilt_refs.get("qlib_view")
        or offline_catalog.get("status") != "PASS"
        or offline_catalog.get("entries") != sum(offline_catalog_counts.values())
        or set(offline_lookups)
        != {"adjusted_price_view", "market_replay_view", "qlib_view"}
        or any(value is not True for value in offline_lookups.values())
        or any(offline_catalog_counts.get(name) != 1 for name in offline_lookups)
    ):
        raise ArtifactError("PR5 offline recovery refs/results are inconsistent")

    gates = _mapping(acceptance_matrix.get("gates"), "acceptance gates")
    acceptance_refs = _mapping(acceptance_matrix.get("artifact_refs"), "acceptance refs")
    if set(gates) != _GATES or dict(acceptance_refs) != dict(refs):
        raise ArtifactError("PR5 acceptance refs/gates are incomplete")
    for name, value in gates.items():
        gate = _mapping(value, f"acceptance gate {name}")
        checks = _mapping(gate.get("checks"), f"acceptance gate {name} checks")
        required_refs = _sequence(gate.get("required_refs"), f"acceptance gate {name} refs")
        if (
            gate.get("status") != "PASS"
            or not checks
            or any(result is not True for result in checks.values())
            or not required_refs
            or len(required_refs) != len(set(required_refs))
            or any(ref_name not in acceptance_refs for ref_name in required_refs)
            or gate.get("mismatch_count") != 0
            or gate.get("mismatches") != []
        ):
            raise ArtifactError(f"PR5 acceptance gate {name} PASS is inconsistent")
    if acceptance_matrix.get("status") != "PASS" or run.get("status") != "PASS":
        raise ArtifactError("PR5 terminal PASS is inconsistent")


__all__ = ["validate_pr5_evidence"]
