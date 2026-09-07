"""Fixed PR5 evidence consistency checks; this is not a reporting platform."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from axiom_data.artifacts import (
    ArtifactError,
    list_catalog,
    load_raw_batch,
    load_snapshot,
    lookup_catalog,
    validate_domain_commit_closure,
)
from axiom_data.domains import DM1_REFERENCE_DOMAINS, DM1_SNAPSHOT_DOMAINS


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
_GATE_SCHEMA = {
    "D01": ({"immutable_snapshot_coexists"}, ("old_snapshot", "new_snapshot")),
    "D02": (
        {"identity_equality", "logical_equality", "pit_binding_equal"},
        _REF_NAMES,
    ),
    "D03": ({"all_raw_v2", "raw_ids_unique"}, ("raw_batches",)),
    "D04": (
        {
            "benchmark_passthrough",
            "capital_x10000",
            "corporate_action_mapping",
            "factor_passthrough",
            "price_limit_passthrough",
            "status_evidence",
        },
        ("raw_batches", "domain_commits", "snapshot"),
    ),
    "D05": ({"all_contract_refs_present"}, ("domain_commits",)),
    "D10": (
        {"snapshot_cross_domain_validation"},
        ("domain_commits", "snapshot"),
    ),
    "D11": ({"catalog_views_exact", "offline_recovery"}, _REF_NAMES),
    "D12": (
        {"all_formal_views_indexed", "catalog_loader_validation"},
        ("snapshot", "adjusted_price_view", "market_replay_view", "qlib_view"),
    ),
    "D13": (
        {"adjusted_is_best_effort", "false_strict_blocked", "terminal_history_not_verified"},
        ("raw_batches", "domain_commits", "snapshot", "adjusted_price_view"),
    ),
    "D14": (
        {"calendar", "field_order", "instrument_mapping", "keys", "snapshot", "values_and_nulls"},
        ("snapshot", "adjusted_price_view", "qlib_view"),
    ),
    "D15": (
        {"real_suspension_present", "replay_is_post_session"},
        ("domain_commits", "snapshot", "market_replay_view"),
    ),
}

_D01_EVIDENCE_FIELDS = {
    "schema_version",
    "validation_root",
    "old_snapshot",
    "new_snapshot",
    "old_snapshot_manifest_digest_before_new",
    "old_domain_refs",
    "new_domain_refs",
}


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


def _content_digest(value: object) -> str:
    content = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def _load_actual_refs(
    data_root: str | Path, claimed_refs: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load the reported closure and project refs from validated artifacts."""

    from axiom_data.consumption import load_qlib_view
    from axiom_data.views import load_adjusted_price_view, load_market_replay_view

    root = Path(data_root)
    claimed_domains = _mapping(claimed_refs.get("domain_commits"), "DomainCommit refs")
    if set(claimed_domains) != set(DM1_SNAPSHOT_DOMAINS):
        raise ArtifactError("PR5 evidence DomainCommit set is incomplete")

    commits: dict[str, Any] = {}
    domain_refs: dict[str, Any] = {}
    raw_refs: list[dict[str, Any]] = []
    for domain in DM1_SNAPSHOT_DOMAINS:
        claimed = _mapping(claimed_domains[domain], f"{domain} DomainCommit ref")
        commit_id = claimed.get("domain_commit_id")
        if not isinstance(commit_id, str):
            raise ArtifactError("PR5 evidence DomainCommit ID is invalid")
        commit = validate_domain_commit_closure(root, domain, commit_id)
        commits[domain] = commit
        domain_refs[domain] = {
            "domain_commit_id": commit.ref.commit_id,
            "contract_digest": commit.manifest["contract_digest"],
            "manifest_digest": commit.manifest_digest,
            "identity_digest": commit.manifest["identity_digest"],
            "logical_content_digest": commit.manifest["logical_content_digest"],
            "rows": len(commit.rows),
        }
        for raw_ref in commit.manifest["ordered_raw_batch_refs"]:
            raw = load_raw_batch(root, raw_ref["raw_batch_id"])
            manifest = raw.manifest
            raw_refs.append(
                {
                    "domain": domain,
                    "raw_batch_id": raw.ref.raw_batch_id,
                    "manifest_digest": raw.ref.manifest_digest,
                    "payload_digest": manifest["payload_files"][0]["content_digest"],
                    "schema_version": manifest["schema_version"],
                    "source_profile_ref": manifest.get("source_profile_ref"),
                    "source_profile_version": manifest.get("source_profile_version"),
                    "source_profile_digest": manifest.get("source_profile_digest"),
                }
            )

    claimed_snapshot = _mapping(claimed_refs.get("snapshot"), "Snapshot ref")
    snapshot = load_snapshot(root, claimed_snapshot.get("snapshot_id"))  # type: ignore[arg-type]
    snapshot_ref = {
        "snapshot_id": snapshot.ref.snapshot_id,
        "manifest_digest": snapshot.ref.manifest_digest,
        "identity_digest": snapshot.manifest["identity_digest"],
    }
    for domain, ref in domain_refs.items():
        commit = commits[domain]
        projected = {
            "domain_commit_id": ref["domain_commit_id"],
            "domain": domain,
            "contract_version": commit.ref.contract_version,
            "contract_digest": ref["contract_digest"],
            "identity_digest": ref["identity_digest"],
            "logical_content_digest": ref["logical_content_digest"],
        }
        if snapshot.manifest["domain_refs"].get(domain) != projected:
            raise ArtifactError("PR5 DomainCommit refs differ from Snapshot composition")

    claimed_adjusted = _mapping(
        claimed_refs.get("adjusted_price_view"), "adjusted view ref"
    )
    adjusted = load_adjusted_price_view(root, claimed_adjusted.get("view_id"))  # type: ignore[arg-type]
    adjusted_ref = {
        "view_id": adjusted.ref.view_id,
        "manifest_digest": adjusted.ref.manifest_digest,
        "identity_digest": adjusted.manifest["identity_digest"],
        "content_digest": adjusted.manifest["output"]["content_digest"],
        "anchor_session": adjusted.manifest["anchor_session"],
        "pit_policy": adjusted.manifest["pit_policy"],
        "pit_qualification": adjusted.manifest["pit_qualification"],
        "decision_cutoff": adjusted.manifest["decision_cutoff"],
    }

    claimed_replay = _mapping(
        claimed_refs.get("market_replay_view"), "MarketReplayView ref"
    )
    replay = load_market_replay_view(root, claimed_replay.get("view_id"))  # type: ignore[arg-type]
    replay_ref = {
        "view_id": replay.ref.view_id,
        "manifest_digest": replay.ref.manifest_digest,
        "identity_digest": replay.manifest["identity_digest"],
        "content_digest": replay.manifest["output"]["content_digest"],
        "temporal_policy": replay.manifest["temporal_policy"],
    }

    claimed_qlib = _mapping(claimed_refs.get("qlib_view"), "QlibView ref")
    qlib = load_qlib_view(root, claimed_qlib.get("view_id"))  # type: ignore[arg-type]
    qlib_ref = {
        "view_id": qlib.ref.view_id,
        "manifest_digest": qlib.ref.manifest_digest,
        "identity_digest": qlib.manifest["identity_digest"],
        "content_digest": _content_digest(
            [
                [entry["path"], entry["content_digest"]]
                for entry in qlib.manifest["output_files"]
            ]
        ),
        "anchor_session": qlib.manifest["anchor_session"],
        "pit_policy": qlib.manifest["pit_policy"],
        "pit_qualification": qlib.manifest["pit_qualification"],
        "decision_cutoff": qlib.manifest["decision_cutoff"],
        "derived_view_id": qlib.manifest["derived_refs"][0]["view_id"],
    }
    return (
        {
            "raw_batches": raw_refs,
            "domain_commits": domain_refs,
            "snapshot": snapshot_ref,
            "adjusted_price_view": adjusted_ref,
            "market_replay_view": replay_ref,
            "qlib_view": qlib_ref,
        },
        {
            "domain_commits": commits,
            "snapshot": snapshot,
            "adjusted_price_view": adjusted,
            "market_replay_view": replay,
            "qlib_view": qlib,
        },
    )


def _pass_report(report: Mapping[str, Any], name: str) -> None:
    checks = _mapping(report.get("checks"), f"{name} checks")
    if (
        report.get("status") != "PASS"
        or not checks
        or any(value is not True for value in checks.values())
        or report.get("mismatches") != []
    ):
        raise ArtifactError(f"PR5 {name} PASS is inconsistent")


def validate_d01_snapshot_coexistence(value: object) -> bool:
    evidence = _mapping(value, "D01 snapshot coexistence evidence")
    if (
        set(evidence) != _D01_EVIDENCE_FIELDS
        or evidence.get("schema_version")
        != "axiom_data.pr5_d01_snapshot_coexistence.v1"
        or not isinstance(evidence.get("validation_root"), str)
    ):
        raise ArtifactError("PR5 D01 snapshot coexistence evidence is incomplete")

    root = Path(evidence["validation_root"])
    claims = {
        name: _mapping(evidence.get(name), f"D01 {name} ref")
        for name in ("old_snapshot", "new_snapshot")
    }
    if claims["old_snapshot"].get("snapshot_id") == claims["new_snapshot"].get(
        "snapshot_id"
    ):
        raise ArtifactError("PR5 D01 old/new Snapshot identities must differ")

    snapshots = {
        name: load_snapshot(root, claim.get("snapshot_id"))  # type: ignore[arg-type]
        for name, claim in claims.items()
    }
    actual_refs = {
        name: {
            "snapshot_id": snapshot.ref.snapshot_id,
            "manifest_digest": snapshot.ref.manifest_digest,
            "identity_digest": snapshot.manifest["identity_digest"],
        }
        for name, snapshot in snapshots.items()
    }
    if any(dict(claims[name]) != actual_refs[name] for name in snapshots):
        raise ArtifactError("PR5 D01 Snapshot ref differs from its loaded artifact")
    for name in snapshots:
        _digest(actual_refs[name]["manifest_digest"], f"D01 {name} manifest")
        _digest(actual_refs[name]["identity_digest"], f"D01 {name} identity")

    old_domain_refs = _mapping(evidence.get("old_domain_refs"), "D01 old domain refs")
    new_domain_refs = _mapping(evidence.get("new_domain_refs"), "D01 new domain refs")
    if (
        dict(old_domain_refs) != snapshots["old_snapshot"].manifest["domain_refs"]
        or dict(new_domain_refs)
        != snapshots["new_snapshot"].manifest["domain_refs"]
        or dict(old_domain_refs) == dict(new_domain_refs)
    ):
        raise ArtifactError("PR5 D01 Snapshot domain refs are inconsistent")
    if (
        evidence.get("old_snapshot_manifest_digest_before_new")
        != actual_refs["old_snapshot"]["manifest_digest"]
    ):
        raise ArtifactError("PR5 D01 old Snapshot changed after new publication")
    return True


def validate_pr5_evidence(
    data_root: str | Path,
    run_manifest: Mapping[str, Any],
    direct_qlib: Mapping[str, Any],
    dm1_reconciliation: Mapping[str, Any],
    qsys_reconciliation: Mapping[str, Any],
    offline_recovery: Mapping[str, Any],
    acceptance_matrix: Mapping[str, Any],
) -> None:
    """Load one immutable D-M1 closure and derive every claimed PR5 PASS."""

    from axiom_data.consumption import compare_direct_and_qlib
    from axiom_data.dm1_reconciliation import reconcile_dm1_raw_mapping
    from axiom_data.domains import MarketContractError
    from axiom_data.domains.dm1 import validate_strict_decision_time

    run = _mapping(run_manifest, "run manifest")
    artifacts = _mapping(run.get("artifacts"), "artifact map")
    refs = _mapping(run.get("artifact_refs"), "artifact refs")
    if set(refs) != set(_REF_NAMES):
        raise ArtifactError("PR5 run artifact refs are incomplete")
    actual_refs, loaded = _load_actual_refs(data_root, refs)
    if dict(refs) != actual_refs:
        raise ArtifactError("PR5 report refs differ from the loaded artifacts")

    raw_refs = _sequence(actual_refs["raw_batches"], "RawBatch refs")
    domain_refs = _mapping(actual_refs["domain_commits"], "DomainCommit refs")
    snapshot_ref = _mapping(actual_refs["snapshot"], "Snapshot ref")
    adjusted_ref = _mapping(actual_refs["adjusted_price_view"], "adjusted view ref")
    replay_ref = _mapping(actual_refs["market_replay_view"], "replay view ref")
    qlib_ref = _mapping(actual_refs["qlib_view"], "QlibView ref")
    if not raw_refs or not domain_refs:
        raise ArtifactError("PR5 run RawBatch/DomainCommit refs are empty")
    for raw in raw_refs:
        raw = _mapping(raw, "RawBatch ref")
        if any(field not in raw for field in _RAW_REF_FIELDS) or raw.get("schema_version") != "raw_batch.v2":
            raise ArtifactError("PR5 run RawBatch ref is incomplete or non-v2")
        _digest(raw["manifest_digest"], "RawBatch manifest")
        _digest(raw["payload_digest"], "RawBatch payload")
        _digest(raw["source_profile_digest"], "RawBatch SourceProfile")
    actual_artifacts = {
        **{
            domain: ref["domain_commit_id"]
            for domain, ref in domain_refs.items()
        },
        "snapshot": snapshot_ref["snapshot_id"],
        "adjusted_price_view": adjusted_ref["view_id"],
        "market_replay_view": replay_ref["view_id"],
        "qlib_view": qlib_ref["view_id"],
    }
    if dict(artifacts) != actual_artifacts:
        raise ArtifactError("PR5 artifact map differs from the loaded artifacts")
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

    root = Path(data_root)
    catalog_entries = list_catalog(root)
    actual_catalog_counts = Counter(entry.artifact_type for entry in catalog_entries)
    actual_lookups = {
        name: lookup_catalog(root, name, actual_refs[key]["view_id"]).artifact_id
        == actual_refs[key]["view_id"]
        for name, key in (
            ("adjusted_price_view", "adjusted_price_view"),
            ("market_replay_view", "market_replay_view"),
            ("qlib_view", "qlib_view"),
        )
    }
    catalog = _mapping(run.get("catalog_rebuild"), "catalog rebuild")
    catalog_counts = _mapping(catalog.get("artifact_type_counts"), "catalog counts")
    lookups = _mapping(catalog.get("exact_view_lookups"), "catalog view lookups")
    if (
        catalog.get("status") != "PASS"
        or catalog.get("entries") != len(catalog_entries)
        or dict(catalog_counts) != dict(sorted(actual_catalog_counts.items()))
        or dict(lookups) != actual_lookups
        or any(actual_catalog_counts.get(name) != 1 for name in actual_lookups)
    ):
        raise ArtifactError("PR5 catalog evidence is inconsistent")

    actual_direct = compare_direct_and_qlib(
        root, snapshot_ref["snapshot_id"], qlib_ref["view_id"]
    )
    if dict(direct_qlib) != actual_direct:
        raise ArtifactError("PR5 direct/Qlib report differs from loaded artifacts")
    _pass_report(actual_direct, "direct/Qlib")

    scope = _mapping(run.get("scope"), "run scope")
    symbols = _sequence(scope.get("symbols"), "run symbols")
    start = scope.get("start_session")
    end = scope.get("end_session")
    if not isinstance(start, str) or not isinstance(end, str):
        raise ArtifactError("PR5 run session scope is invalid")
    dm1_raw_ids = {
        domain: [
            raw["raw_batch_id"] for raw in raw_refs if raw["domain"] == domain
        ]
        for domain in DM1_REFERENCE_DOMAINS
    }
    actual_dm1 = reconcile_dm1_raw_mapping(
        str(root),
        snapshot_ref["snapshot_id"],
        dm1_raw_ids,
        symbols=symbols,
        start_session=start,
        end_session=end,
    )
    if dict(dm1_reconciliation) != actual_dm1:
        raise ArtifactError("PR5 D-M1 reconciliation differs from loaded artifacts")
    _pass_report(actual_dm1, "D-M1 reconciliation")

    expected_market_raw = [
        raw for raw in raw_refs if _mapping(raw, "RawBatch ref")["domain"] == "market_daily"
    ]
    if (
        qsys_reconciliation.get("status") != "PASS"
        or qsys_reconciliation.get("contract_or_build_bug_count") != 0
        or qsys_reconciliation.get("snapshot_id") != snapshot_ref.get("snapshot_id")
        or qsys_reconciliation.get("snapshot_ref") != dict(snapshot_ref)
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
    offline_ok = (
        offline_recovery.get("network_calls") == 0
        and offline_recovery.get("identity_equality") is True
        and offline_recovery.get("anchor_semantics_equal") is True
        and offline_recovery.get("pit_binding_equal") is True
        and bool(logical)
        and all(value is True for value in logical.values())
        and dict(source_artifacts) == actual_artifacts
        and dict(rebuilt_artifacts) == actual_artifacts
        and dict(source_refs) == actual_refs
        and _projection(rebuilt_refs) == _projection(actual_refs)
        and _projection(offline_direct) == _projection(actual_direct)
        and offline_catalog.get("status") == "PASS"
        and offline_catalog.get("entries") == len(catalog_entries)
        and dict(offline_catalog_counts) == dict(sorted(actual_catalog_counts.items()))
        and dict(offline_lookups) == actual_lookups
    )
    if not offline_ok or offline_recovery.get("status") != "PASS":
        raise ArtifactError("PR5 offline recovery refs/results are inconsistent")

    gates = _mapping(acceptance_matrix.get("gates"), "acceptance gates")
    acceptance_refs = _mapping(acceptance_matrix.get("artifact_refs"), "acceptance refs")
    if set(gates) != set(_GATE_SCHEMA) or dict(acceptance_refs) != actual_refs:
        raise ArtifactError("PR5 acceptance refs/gates are incomplete")
    d01_gate = _mapping(gates["D01"], "acceptance gate D01")
    run_d01 = _mapping(
        run.get("d01_snapshot_coexistence"), "run D01 snapshot coexistence"
    )
    gate_d01 = _mapping(
        d01_gate.get("evidence"), "acceptance D01 snapshot coexistence"
    )
    if dict(run_d01) != dict(gate_d01):
        raise ArtifactError("PR5 D01 evidence differs between run and acceptance reports")
    d01_coexists = validate_d01_snapshot_coexistence(run_d01)

    adjusted = loaded["adjusted_price_view"]
    replay = loaded["market_replay_view"]
    snapshot = loaded["snapshot"]
    factor_rows = loaded["domain_commits"]["adjustment_factors"].rows
    try:
        validate_strict_decision_time(factor_rows, adjusted.manifest["decision_cutoff"])
        strict_blocked = False
    except MarketContractError:
        strict_blocked = True
    terminal_rows = [
        row
        for domain in DM1_REFERENCE_DOMAINS
        for row in loaded["domain_commits"][domain].rows
    ]
    actual_checks = {
        "D01": {"immutable_snapshot_coexists": d01_coexists},
        "D02": {
            "identity_equality": offline_recovery.get("identity_equality") is True,
            "logical_equality": bool(logical) and all(value is True for value in logical.values()),
            "pit_binding_equal": offline_recovery.get("pit_binding_equal") is True,
        },
        "D03": {
            "all_raw_v2": all(raw["schema_version"] == "raw_batch.v2" for raw in raw_refs),
            "raw_ids_unique": len(raw_refs) == len({raw["raw_batch_id"] for raw in raw_refs}),
        },
        "D04": dict(actual_dm1["checks"]),
        "D05": {"all_contract_refs_present": all(ref["contract_digest"].startswith("sha256:") for ref in domain_refs.values())},
        "D10": {"snapshot_cross_domain_validation": snapshot.manifest["validation_summary"]["cross_domain"] == "PASS"},
        "D11": {"offline_recovery": offline_ok, "catalog_views_exact": all(actual_lookups.values())},
        "D12": {"catalog_loader_validation": len(catalog_entries) == sum(actual_catalog_counts.values()), "all_formal_views_indexed": all(actual_lookups.values())},
        "D13": {
            "terminal_history_not_verified": all(row["pit_qualification"] != "verified" for row in terminal_rows),
            "adjusted_is_best_effort": adjusted.manifest["pit_qualification"] == "best_effort",
            "false_strict_blocked": strict_blocked,
        },
        "D14": dict(actual_direct["checks"]),
        "D15": {
            "real_suspension_present": any(row["status"] == "suspended" for row in replay.rows),
            "replay_is_post_session": replay.manifest["temporal_policy"]["mode"] == "post_session_replay",
        },
    }
    view_scope = loaded["qlib_view"].manifest["scope"]
    if (
        actual_direct.get("scope") != view_scope
        or actual_direct.get("fields") != loaded["qlib_view"].manifest["fields"]
        or adjusted.manifest["scope"] != view_scope
        or replay.manifest["scope"] != view_scope
        or list(symbols) != view_scope["symbols"]
        or start != view_scope["start_session"]
        or end != view_scope["end_session"]
    ):
        raise ArtifactError("PR5 scope/field evidence differs from loaded Views")

    for name, (required_checks, required_refs) in _GATE_SCHEMA.items():
        value = gates[name]
        gate = _mapping(value, f"acceptance gate {name}")
        checks = _mapping(gate.get("checks"), f"acceptance gate {name} checks")
        claimed_required_refs = _sequence(
            gate.get("required_refs"), f"acceptance gate {name} refs"
        )
        derived_pass = (
            set(checks) == required_checks
            and dict(checks) == actual_checks[name]
            and tuple(claimed_required_refs) == tuple(required_refs)
            and all(result is True for result in actual_checks[name].values())
            and gate.get("mismatch_count") == 0
            and gate.get("mismatches") == []
        )
        if (
            not derived_pass
            or gate.get("status") != "PASS"
        ):
            raise ArtifactError(f"PR5 acceptance gate {name} PASS is inconsistent")
    if acceptance_matrix.get("status") != "PASS" or run.get("status") != "PASS":
        raise ArtifactError("PR5 terminal PASS is inconsistent")


__all__ = ["validate_d01_snapshot_coexistence", "validate_pr5_evidence"]


def pr6_actual_refs(data_root: str | Path, snapshot_id: str, view_id: str) -> dict[str, Any]:
    """Resolve PR6 evidence through the same manifest-truth loaders used by readers."""
    from axiom_data.consumption import SnapshotReader
    from axiom_data.pr6_views import load_pr6_fact_view
    reader = SnapshotReader(data_root, snapshot_id)
    view = load_pr6_fact_view(data_root, view_id)
    if view.manifest['snapshot_ref']['snapshot_id'] != snapshot_id:
        raise ArtifactError('PR6 evidence View/Snapshot mismatch')
    return {
        'snapshot': {'snapshot_id': snapshot_id, 'identity_digest': reader.snapshot.manifest['identity_digest']},
        'view': {'view_id': view_id, 'identity_digest': view.manifest['identity_digest']},
        'domain_commits': {d: {'domain_commit_id': c.ref.commit_id,
                              'logical_content_digest': c.manifest['logical_content_digest'],
                              'rows': len(c.rows)} for d, c in reader.commits.items()},
    }


def validate_pr6_evidence(run_manifest: Mapping[str, Any], *, data_root: str | Path,
                          offline_root: str | Path) -> bool:
    """Fixed PR6 gate schema; caller booleans and coordinated fake references cannot pass."""
    from axiom_data.pr6_reconciliation import reconcile
    expected_fields = {'schema_version', 'data_root', 'offline_root', 'artifact_refs',
                       'offline_artifact_refs', 'reconciliation', 'gates'}
    if set(run_manifest) != expected_fields or run_manifest['schema_version'] != 'pr6_evidence.v1':
        raise ArtifactError('PR6 evidence schema mismatch')
    if str(Path(data_root)) != run_manifest['data_root'] or str(Path(offline_root)) != run_manifest['offline_root']:
        raise ArtifactError('PR6 verifier must bind actual validation roots')
    if Path(data_root).resolve() == Path(offline_root).resolve():
        raise ArtifactError('PR6 recovery requires a different root')
    refs = run_manifest['artifact_refs']
    actual = pr6_actual_refs(data_root, refs['snapshot']['snapshot_id'], refs['view']['view_id'])
    offline = pr6_actual_refs(offline_root, refs['snapshot']['snapshot_id'], refs['view']['view_id'])
    if refs != actual or run_manifest['offline_artifact_refs'] != offline or offline != actual:
        raise ArtifactError('PR6 actual artifact refs or recovery identity mismatch')
    report = reconcile(data_root, refs['snapshot']['snapshot_id'], refs['view']['view_id'])
    if run_manifest['reconciliation'] != report:
        raise ArtifactError('PR6 reconciliation does not match actual inputs')
    for root in (data_root, offline_root):
        for kind, identity in [('data_snapshot', refs['snapshot']['snapshot_id']),
                               ('pr6_fact_view', refs['view']['view_id']),
                               ('qlib_view', refs['view']['view_id'])]:
            lookup_catalog(root, kind, identity)
    from axiom_data.consumption import SnapshotReader
    financial = SnapshotReader(data_root, refs['snapshot']['snapshot_id']).commits['financial_events'].rows
    revision_counts = Counter(r['logical_event_key'] for r in financial)
    gates = {
        'artifact_closure': True,
        'offline_identity_and_values': actual == offline,
        'historical_union_exact': report['historical_union']['count'] == 3601,
        'direct_qlib_values': report['direct_qlib_comparisons'] > 0,
        'bounded_membership_reconciliation': all(r['result'] == 'MATCH' for r in report['membership']),
        'source_revisions_retained': max(revision_counts.values(), default=0) > 1,
    }
    if run_manifest['gates'] != gates or not all(gates.values()):
        raise ArtifactError('PR6 gate result differs from actual validation')
    return True
