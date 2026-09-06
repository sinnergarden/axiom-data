"""Snapshot-bound D-M1 derived, FactView, and MarketReplayView artifacts."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

from axiom_data.artifacts import (
    ArtifactError,
    ArtifactNotFoundError,
    _content_tree_digests,
    _derived_identity,
    _digest,
    _identity,
    _identity_digest,
    _json_bytes,
    _layout,
    _load_manifest,
    _publish_directory,
    _relative_file,
    _timestamp,
    _validate_manifest_identity,
    _write_file,
    _write_manifest,
    load_snapshot,
    validate_domain_commit_closure,
)
from axiom_data.consumption import SnapshotReader, _session, _symbols


_ADJUSTED_REVISION = "anchor-bound-adjusted-price.v1"
_REPLAY_REVISION = "market-replay-facts.v1"
_ADJUSTED_FIELDS = ("open", "high", "low", "close")


@dataclass(frozen=True, slots=True)
class DerivedViewRef:
    name: str
    view_id: str
    manifest_digest: str


@dataclass(frozen=True, slots=True)
class DerivedView:
    ref: DerivedViewRef
    manifest: dict[str, Any]
    rows: tuple[dict[str, Any], ...]


def _snapshot_domain_ref(snapshot: Any, domain: str) -> dict[str, Any]:
    try:
        ref = snapshot.manifest["domain_refs"][domain]
    except KeyError as exc:
        raise ArtifactError(f"Snapshot lacks required D-M1 domain {domain!r}") from exc
    return dict(ref)


def _view_target(data_root: str | Path, name: str, view_id: str) -> Path:
    return _layout(data_root).derived_commits(name) / view_id


def build_adjusted_price_view(
    data_root: str | Path,
    snapshot_id: str,
    *,
    symbols: Sequence[str],
    start_session: str,
    end_session: str,
    anchor_session: str,
    pit_policy: str,
    decision_cutoff: str,
    created_at: str | None = None,
) -> DerivedViewRef:
    """Materialize the reviewed factor-ratio formula with one explicit anchor."""

    reader = SnapshotReader(data_root, snapshot_id)
    snapshot = reader.snapshot
    selected = _symbols(symbols)
    start = _session(start_session, "start_session")
    end = _session(end_session, "end_session")
    anchor = _session(anchor_session, "anchor_session")
    cutoff = _session(decision_cutoff, "decision_cutoff")
    if start > end or not start <= anchor <= end:
        raise ArtifactError("adjusted-price anchor must be inside the requested scope")
    if pit_policy not in {"strict_decision_time", "research_non_pit"}:
        raise ArtifactError("adjusted-price PIT policy is invalid")
    if pit_policy == "strict_decision_time" and anchor > cutoff:
        raise ArtifactError("strict decision-time view cannot use a future anchor")
    market_ref = _snapshot_domain_ref(snapshot, "market_daily")
    factor_ref = _snapshot_domain_ref(snapshot, "adjustment_factors")
    market = reader.market_daily(selected, start, end)
    factors = {
        (row["session"], row["symbol"]): row
        for row in reader.facts(
            "adjustment_factors",
            symbols=selected,
            start_session=start,
            end_session=end,
        )
    }
    anchors = {symbol: factors.get((anchor, symbol)) for symbol in selected}
    if any(value is None for value in anchors.values()):
        missing = [symbol for symbol, value in anchors.items() if value is None]
        raise ArtifactError(f"adjusted-price anchor factor is unavailable for {missing!r}")
    rows: list[dict[str, Any]] = []
    for source in market:
        key = (source["session"], source["symbol"])
        factor = factors.get(key)
        anchor_factor = anchors[source["symbol"]]
        assert anchor_factor is not None
        if factor is None:
            values = {field: None for field in _ADJUSTED_FIELDS}
            state = "missing_factor"
        elif any(source[field] is None for field in _ADJUSTED_FIELDS):
            values = {field: None for field in _ADJUSTED_FIELDS}
            state = "no_market_price"
        else:
            ratio = factor["factor"] / anchor_factor["factor"]
            values = {field: source[field] * ratio for field in _ADJUSTED_FIELDS}
            state = "ok"
        rows.append(
            {
                "session": source["session"],
                "symbol": source["symbol"],
                **values,
                "adjustment_state": state,
            }
        )
    rows.sort(key=lambda row: (row["session"], row["symbol"]))
    contract = json.loads(
        files("axiom_data.contracts").joinpath("adjusted_price.v1.json").read_bytes()
    )
    contract_content = _json_bytes(contract)
    rows_content = _json_bytes(rows)
    builder_ref = {
        "implementation": "axiom_data.views.build_adjusted_price_view",
        "revision": _ADJUSTED_REVISION,
    }
    builder_ref["digest"] = _digest(_json_bytes(builder_ref))
    manifest: dict[str, Any] = {
        "artifact_type": "adjusted_price_view",
        "schema_version": "adjusted_price_view.v1",
        "snapshot_ref": {
            "snapshot_id": snapshot.ref.snapshot_id,
            "identity_digest": snapshot.manifest["identity_digest"],
        },
        "domain_refs": {"market_daily": market_ref, "adjustment_factors": factor_ref},
        "derived_contract": {
            "contract_version": "adjusted_price.v1",
            "content_digest": _digest(contract_content),
            "path": "contract.json",
        },
        "builder_ref": builder_ref,
        "scope": {
            "symbols": list(selected),
            "start_session": start,
            "end_session": end,
            "interval": "closed",
        },
        "price_basis": "anchor_adjusted",
        "anchor_session": anchor,
        "pit_policy": pit_policy,
        "decision_cutoff": cutoff,
        "formula": contract["formula"],
        "output": {
            "path": "rows.json",
            "content_digest": _digest(rows_content),
            "rows": len(rows),
        },
        "validation_summary": {
            "status": "PASS",
            "anchor_factors_present": True,
            "future_anchor_blocked": pit_policy != "strict_decision_time" or anchor <= cutoff,
        },
    }
    identity_digest = _identity_digest(manifest, "view_id")
    view_id = _derived_identity("adjusted-price", identity_digest)
    manifest["view_id"] = view_id
    manifest["identity_digest"] = identity_digest
    manifest["created_at"] = _timestamp(created_at)
    target = _view_target(data_root, "adjusted_price", view_id)

    def prepare(candidate: Path) -> None:
        _write_file(candidate / "contract.json", contract_content)
        _write_file(candidate / "rows.json", rows_content)
        _write_manifest(candidate, manifest)

    _publish_directory(_layout(data_root), target, prepare, identity_digest=identity_digest)
    return load_adjusted_price_view(data_root, view_id).ref


def load_adjusted_price_view(data_root: str | Path, view_id: str) -> DerivedView:
    layout = _layout(data_root)
    view_id = _identity("view_id", view_id)
    target = _view_target(data_root, "adjusted_price", view_id)
    manifest, manifest_digest = _load_manifest(
        layout.root,
        target,
        artifact_type="adjusted_price_view",
        schema_version="adjusted_price_view.v1",
        identity_field="view_id",
        identity=view_id,
    )
    _validate_manifest_identity(manifest, "view_id", "adjusted-price", view_id)
    snapshot_ref = manifest.get("snapshot_ref")
    if not isinstance(snapshot_ref, dict) or not isinstance(snapshot_ref.get("snapshot_id"), str):
        raise ArtifactError("adjusted-price Snapshot ref is invalid")
    snapshot = load_snapshot(layout.root, snapshot_ref["snapshot_id"])
    if snapshot_ref != {
        "snapshot_id": snapshot.ref.snapshot_id,
        "identity_digest": snapshot.manifest["identity_digest"],
    }:
        raise ArtifactError("adjusted-price Snapshot ref mismatch")
    for domain in ("market_daily", "adjustment_factors"):
        expected = _snapshot_domain_ref(snapshot, domain)
        if manifest.get("domain_refs", {}).get(domain) != expected:
            raise ArtifactError("adjusted-price domain ref mismatch")
        validate_domain_commit_closure(layout.root, domain, expected["domain_commit_id"])
    contract_ref = manifest.get("derived_contract")
    output = manifest.get("output")
    if not isinstance(contract_ref, dict) or not isinstance(output, dict):
        raise ArtifactError("adjusted-price content refs are invalid")
    contract_path = _relative_file(layout.root, target, contract_ref.get("path"))
    rows_path = _relative_file(layout.root, target, output.get("path"))
    if (
        not contract_path.is_file()
        or _digest(contract_path.read_bytes()) != contract_ref.get("content_digest")
        or not rows_path.is_file()
        or _digest(rows_path.read_bytes()) != output.get("content_digest")
    ):
        raise ArtifactError("adjusted-price content digest mismatch")
    packaged_contract = files("axiom_data.contracts").joinpath("adjusted_price.v1.json").read_bytes()
    if json.loads(contract_path.read_bytes()) != json.loads(packaged_contract):
        raise ArtifactError("adjusted-price contract is not the registered v1 content")
    expected_builder = {
        "implementation": "axiom_data.views.build_adjusted_price_view",
        "revision": _ADJUSTED_REVISION,
    }
    expected_builder["digest"] = _digest(_json_bytes(expected_builder))
    if manifest.get("builder_ref") != expected_builder:
        raise ArtifactError("adjusted-price builder ref is invalid")
    rows = json.loads(rows_path.read_bytes())
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ArtifactError("adjusted-price rows are invalid")
    if output.get("rows") != len(rows):
        raise ArtifactError("adjusted-price row count mismatch")
    expected_paths = {
        "contract.json": contract_ref["content_digest"],
        "rows.json": output["content_digest"],
    }
    if _content_tree_digests(target) != expected_paths:
        raise ArtifactError("adjusted-price view has undeclared content")
    scope = manifest.get("scope")
    if not isinstance(scope, dict) or scope.get("interval") != "closed":
        raise ArtifactError("adjusted-price scope is invalid")
    _symbols(scope.get("symbols"))
    start = _session(scope.get("start_session"), "adjusted start")
    end = _session(scope.get("end_session"), "adjusted end")
    anchor = _session(manifest.get("anchor_session"), "adjusted anchor")
    cutoff = _session(manifest.get("decision_cutoff"), "adjusted cutoff")
    if not start <= anchor <= end or (
        manifest.get("pit_policy") == "strict_decision_time" and anchor > cutoff
    ):
        raise ArtifactError("adjusted-price anchor policy is invalid")
    if manifest.get("validation_summary", {}).get("status") != "PASS":
        raise ArtifactError("adjusted-price validation did not pass")
    return DerivedView(DerivedViewRef("adjusted_price", view_id, manifest_digest), manifest, tuple(rows))


class FactView:
    """Typed reads over explicit Snapshot/Derived refs; never builds missing views."""

    def __init__(
        self,
        data_root: str | Path,
        snapshot_id: str,
        *,
        adjusted_price_view_id: str | None = None,
    ) -> None:
        self.reader = SnapshotReader(data_root, snapshot_id)
        self.adjusted = (
            load_adjusted_price_view(data_root, adjusted_price_view_id)
            if adjusted_price_view_id is not None
            else None
        )
        if self.adjusted is not None and self.adjusted.manifest["snapshot_ref"]["snapshot_id"] != snapshot_id:
            raise ArtifactError("FactView Derived ref belongs to another Snapshot")

    def read(
        self,
        domain: str,
        *,
        symbols: Sequence[str] | None = None,
        start_session: str | None = None,
        end_session: str | None = None,
        fields: Sequence[str] | None = None,
        price_basis: str = "canonical",
        pit_policy: str = "best_effort",
        cutoff_policy: str = "snapshot_bound",
    ) -> dict[str, Any]:
        if domain == "adjusted_price":
            if self.adjusted is None:
                raise ArtifactNotFoundError("adjusted-price view unavailable; build-required")
            rows = self.adjusted.rows
            available = ("session", "symbol", *_ADJUSTED_FIELDS, "adjustment_state")
            selected_fields = tuple(fields or available)
            if not selected_fields or len(selected_fields) != len(set(selected_fields)) or any(
                field not in available for field in selected_fields
            ):
                raise ArtifactError("adjusted FactView fields are invalid")
            if price_basis != "anchor_adjusted":
                raise ArtifactError("adjusted price requires anchor_adjusted price_basis")
            selected_symbols = set(_symbols(symbols)) if symbols is not None else None
            start = _session(start_session, "start_session") if start_session else None
            end = _session(end_session, "end_session") if end_session else None
            values = tuple(
                {field: row[field] for field in selected_fields}
                for row in rows
                if (selected_symbols is None or row["symbol"] in selected_symbols)
                and (start is None or row["session"] >= start)
                and (end is None or row["session"] <= end)
            )
            derived_refs = [dict(self.adjusted.manifest["domain_refs"], view_id=self.adjusted.ref.view_id)]
            anchor = self.adjusted.manifest["anchor_session"]
            response_fields = selected_fields
        else:
            if price_basis not in {"canonical", "unadjusted"}:
                raise ArtifactError("canonical fact domain does not support this price basis")
            values = self.reader.facts(
                domain,
                symbols=symbols,
                start_session=start_session,
                end_session=end_session,
                fields=fields,
            )
            derived_refs = []
            anchor = None
            response_fields = tuple(fields or self.reader.schema(domain))
        commit = self.reader.commits.get(domain)
        source_quality = [] if commit is None else [{
            "domain_commit_id": commit.ref.commit_id,
            "contract_version": commit.ref.contract_version,
            "contract_digest": commit.manifest["contract_digest"],
            "validation": commit.manifest["validation_summary"],
            "raw_refs": commit.manifest["ordered_raw_batch_refs"],
        }]
        return {
            "snapshot_ref": {
                "snapshot_id": self.reader.snapshot.ref.snapshot_id,
                "manifest_digest": self.reader.snapshot.ref.manifest_digest,
            },
            "derived_refs": derived_refs,
            "domain": domain,
            "fields": list(response_fields),
            "scope": {"symbols": list(symbols) if symbols is not None else None, "start_session": start_session, "end_session": end_session},
            "price_basis": price_basis,
            "anchor": anchor,
            "pit_policy": pit_policy,
            "cutoff_policy": cutoff_policy,
            "source_quality_refs": source_quality,
            "rows": values,
        }


def build_market_replay_view(
    data_root: str | Path,
    snapshot_id: str,
    *,
    symbols: Sequence[str],
    start_session: str,
    end_session: str,
    created_at: str | None = None,
) -> DerivedViewRef:
    """Materialize replay facts only; execution, cash, and positions remain absent."""

    reader = SnapshotReader(data_root, snapshot_id)
    selected = _symbols(symbols)
    start = _session(start_session, "start_session")
    end = _session(end_session, "end_session")
    if start > end:
        raise ArtifactError("MarketReplayView scope is reversed")
    required = ("trading_calendar", "security_master", "market_daily", "security_status", "price_limits", "corporate_actions")
    for domain in required:
        _snapshot_domain_ref(reader.snapshot, domain)
    security = {row["symbol"]: row for row in reader.security_master(selected)}
    market = {(row["session"], row["symbol"]): row for row in reader.market_daily(selected, start, end)}
    status = {(row["session"], row["symbol"]): row for row in reader.facts("security_status", symbols=selected, start_session=start, end_session=end)}
    limits = {(row["session"], row["symbol"]): row for row in reader.facts("price_limits", symbols=selected, start_session=start, end_session=end)}
    actions: dict[tuple[str, str], list[str]] = {}
    for row in reader.facts("corporate_actions", symbols=selected, start_session=start, end_session=end):
        actions.setdefault((row["effective_date"], row["symbol"]), []).append(row["action_id"])
    calendars = reader.trading_calendar(start_session=start, end_session=end)
    rows = []
    for cal in calendars:
        if cal["is_open"] is not True:
            continue
        for symbol in selected:
            identity = security[symbol]
            exchange = "SSE" if symbol.endswith(".SH") else "SZSE"
            if cal["exchange"] != exchange:
                continue
            key = (cal["session"], symbol)
            state = status.get(key)
            if state is None:
                continue
            observed = market.get(key)
            limit = limits.get(key)
            rows.append({
                "session": cal["session"],
                "symbol": symbol,
                "exchange": exchange,
                "calendar_is_open": True,
                "status": state["status"],
                "missing_reason": None if observed is not None and not observed["is_suspended"] else state["reason"],
                "open": None if observed is None else observed["open"],
                "high": None if observed is None else observed["high"],
                "low": None if observed is None else observed["low"],
                "close": None if observed is None else observed["close"],
                "pre_close": None if observed is None else observed["pre_close"],
                "volume_shares": None if observed is None else observed["volume_shares"],
                "amount_cny": None if observed is None else observed["amount_cny"],
                "limit_state": None if limit is None else limit["limit_state"],
                "upper_limit": None if limit is None else limit["upper_limit"],
                "lower_limit": None if limit is None else limit["lower_limit"],
                "rule_ref": None if limit is None else limit["rule_ref"],
                "corporate_action_ids": sorted(actions.get(key, [])),
                "security_identity_ref": _snapshot_domain_ref(reader.snapshot, "security_master")["domain_commit_id"],
            })
    rows.sort(key=lambda row: (row["session"], row["symbol"]))
    rows_content = _json_bytes(rows)
    builder_ref = {"implementation": "axiom_data.views.build_market_replay_view", "revision": _REPLAY_REVISION}
    builder_ref["digest"] = _digest(_json_bytes(builder_ref))
    manifest: dict[str, Any] = {
        "artifact_type": "market_replay_view",
        "schema_version": "market_replay_view.v1",
        "snapshot_ref": {"snapshot_id": reader.snapshot.ref.snapshot_id, "identity_digest": reader.snapshot.manifest["identity_digest"]},
        "domain_refs": {domain: _snapshot_domain_ref(reader.snapshot, domain) for domain in required},
        "scope": {"symbols": list(selected), "start_session": start, "end_session": end, "interval": "closed"},
        "price_basis": "unadjusted",
        "builder_ref": builder_ref,
        "unsupported_events": ["rights_issue", "intraday_suspension", "split", "consolidation"],
        "excluded_capabilities": ["execution", "cash", "positions", "corporate_action_accounting"],
        "output": {"path": "rows.json", "content_digest": _digest(rows_content), "rows": len(rows)},
        "validation_summary": {"status": "PASS", "fact_only": True, "rows": len(rows)},
    }
    identity_digest = _identity_digest(manifest, "view_id")
    view_id = _derived_identity("market-replay", identity_digest)
    manifest["view_id"] = view_id
    manifest["identity_digest"] = identity_digest
    manifest["created_at"] = _timestamp(created_at)
    target = _view_target(data_root, "market_replay", view_id)

    def prepare(candidate: Path) -> None:
        _write_file(candidate / "rows.json", rows_content)
        _write_manifest(candidate, manifest)

    _publish_directory(_layout(data_root), target, prepare, identity_digest=identity_digest)
    return load_market_replay_view(data_root, view_id).ref


def load_market_replay_view(data_root: str | Path, view_id: str) -> DerivedView:
    layout = _layout(data_root)
    view_id = _identity("view_id", view_id)
    target = _view_target(data_root, "market_replay", view_id)
    manifest, manifest_digest = _load_manifest(layout.root, target, artifact_type="market_replay_view", schema_version="market_replay_view.v1", identity_field="view_id", identity=view_id)
    _validate_manifest_identity(manifest, "view_id", "market-replay", view_id)
    snapshot_ref = manifest.get("snapshot_ref")
    if not isinstance(snapshot_ref, dict) or not isinstance(snapshot_ref.get("snapshot_id"), str):
        raise ArtifactError("MarketReplayView Snapshot ref is invalid")
    snapshot = load_snapshot(layout.root, snapshot_ref["snapshot_id"])
    if snapshot_ref != {"snapshot_id": snapshot.ref.snapshot_id, "identity_digest": snapshot.manifest["identity_digest"]}:
        raise ArtifactError("MarketReplayView Snapshot ref mismatch")
    domain_refs = manifest.get("domain_refs")
    if not isinstance(domain_refs, dict):
        raise ArtifactError("MarketReplayView domain refs are invalid")
    for domain, ref in domain_refs.items():
        if ref != _snapshot_domain_ref(snapshot, domain):
            raise ArtifactError("MarketReplayView domain ref mismatch")
    output = manifest.get("output")
    if not isinstance(output, dict):
        raise ArtifactError("MarketReplayView output ref is invalid")
    expected_builder = {
        "implementation": "axiom_data.views.build_market_replay_view",
        "revision": _REPLAY_REVISION,
    }
    expected_builder["digest"] = _digest(_json_bytes(expected_builder))
    if manifest.get("builder_ref") != expected_builder:
        raise ArtifactError("MarketReplayView builder ref is invalid")
    path = _relative_file(layout.root, target, output.get("path"))
    if not path.is_file() or _digest(path.read_bytes()) != output.get("content_digest"):
        raise ArtifactError("MarketReplayView output digest mismatch")
    rows = json.loads(path.read_bytes())
    if not isinstance(rows, list) or output.get("rows") != len(rows):
        raise ArtifactError("MarketReplayView rows are invalid")
    if _content_tree_digests(target) != {"rows.json": output["content_digest"]}:
        raise ArtifactError("MarketReplayView contains undeclared content")
    if manifest.get("price_basis") != "unadjusted" or manifest.get("validation_summary", {}).get("status") != "PASS":
        raise ArtifactError("MarketReplayView semantic validation failed")
    return DerivedView(DerivedViewRef("market_replay", view_id, manifest_digest), manifest, tuple(rows))


__all__ = [
    "DerivedView",
    "DerivedViewRef",
    "FactView",
    "build_adjusted_price_view",
    "build_market_replay_view",
    "load_adjusted_price_view",
    "load_market_replay_view",
]
