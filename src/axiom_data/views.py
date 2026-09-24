"""Snapshot-bound derived, FactView, and MarketReplayView artifacts."""

from __future__ import annotations

import json
from contextlib import contextmanager
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
)
from axiom_data.consumption import SnapshotReader, prepared_market_view_rows, validate_session, validate_symbols
from axiom_data.consumption import ordered_row
from axiom_data.domains import MarketContractError
from axiom_data.domains.reference import (
    validate_strict_decision_time,
    weakest_pit_qualification,
)


_ADJUSTED_REVISION = "anchor-bound-adjusted-price.v2"
_REPLAY_REVISION = "market-replay-facts.v2"
_ADJUSTED_FIELDS = ("open", "high", "low", "close")
_PIT_STRENGTH = {"unknown": 0, "best_effort": 1, "observed": 2, "verified": 3}
ADJUSTED_BATCH_SIZE = 50


def _stored_view_kind(kind: str) -> str:
    """Resolve public names to the immutable View kinds used in published plans."""
    return {"financial_fact": "pr6_fact", "event_fact": "pr7_fact"}.get(kind, kind)


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
        raise ArtifactError(f"Snapshot lacks required domain {domain!r}") from exc
    return dict(ref)


def _view_target(data_root: str | Path, name: str, view_id: str) -> Path:
    return _layout(data_root).derived_commits(name) / view_id


@contextmanager
def adjusted_price_batch(reader, configs):
    """Share complete source traversals for one bounded group of security Views."""
    from axiom_data.verification_cache import file_state, validation_paths

    if len(configs) > ADJUSTED_BATCH_SIZE:
        raise ArtifactError("adjusted-price batch exceeds its security bound")
    symbols = set()
    for config in configs:
        selected = validate_symbols(config["symbols"])
        if len(selected) != 1:
            raise ArtifactError("adjusted-price batch requires one symbol per View")
        symbols.add(selected[0])
    start = min(validate_session(c["start_session"], "start_session") for c in configs)
    end = max(validate_session(c["end_session"], "end_session") for c in configs)
    sources = {}
    with validation_paths() as observed:
        for domain in ("market_daily", "adjustment_factors"):
            grouped = {symbol: [] for symbol in symbols}
            # The complete monthly objects are exhausted and validated before
            # any security projection becomes a builder input.
            for row in reader.session_rows(domain, start, end):
                if row["symbol"] in grouped and start <= row["session"] <= end:
                    grouped[row["symbol"]].append(row)
            sources[domain] = grouped
    prior = getattr(reader, "_adjusted_price_batch", None)
    reader._adjusted_price_batch = (start, end, symbols, sources)
    try:
        yield
    finally:
        reader._adjusted_price_batch = prior
        if any(file_state(path) != state for path, state in observed.items()):
            raise ArtifactError("adjusted-price source changed during batch")


def _adjusted_batch_rows(reader, domain, selected, start, end):
    batch = getattr(reader, "_adjusted_price_batch", None)
    if batch is None or len(selected) != 1:
        return None
    lower, upper, symbols, sources = batch
    if start < lower or end > upper or selected[0] not in symbols:
        return None
    return tuple(row for row in sources[domain][selected[0]]
                 if start <= row["session"] <= end)


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

    return _build_adjusted_price_view(SnapshotReader(data_root, snapshot_id),
        symbols=symbols,start_session=start_session,end_session=end_session,anchor_session=anchor_session,
        pit_policy=pit_policy,decision_cutoff=decision_cutoff,created_at=created_at)


def _build_adjusted_price_view(reader, *, symbols, start_session, end_session,
                               anchor_session, pit_policy, decision_cutoff, created_at=None):
    data_root = reader.data_root
    snapshot = reader.snapshot
    selected = validate_symbols(symbols)
    start = validate_session(start_session, "start_session")
    end = validate_session(end_session, "end_session")
    anchor = validate_session(anchor_session, "anchor_session")
    cutoff = validate_session(decision_cutoff, "decision_cutoff")
    if start > end or not start <= anchor <= end:
        raise ArtifactError("adjusted-price anchor must be inside the requested scope")
    if pit_policy not in {"strict_decision_time", "research_non_pit"}:
        raise ArtifactError("adjusted-price PIT policy is invalid")
    if pit_policy == "strict_decision_time" and anchor > cutoff:
        raise ArtifactError("strict decision-time view cannot use a future anchor")
    market_ref = _snapshot_domain_ref(snapshot, "market_daily")
    factor_ref = _snapshot_domain_ref(snapshot, "adjustment_factors")
    batched_market = _adjusted_batch_rows(reader, "market_daily", selected, start, end)
    if batched_market is None:
        market = reader.market_daily(selected, start, end)
    else:
        market_fields = reader.schema("market_daily")
        market = tuple(ordered_row(row, market_fields) for row in batched_market)
    batched_factors = _adjusted_batch_rows(reader, "adjustment_factors", selected, start, end)
    if batched_factors is None:
        factor_source = reader.facts("adjustment_factors", symbols=selected,
                                     start_session=start, end_session=end)
    else:
        factor_fields = reader.schema("adjustment_factors")
        factor_source = tuple(ordered_row(row, factor_fields) for row in batched_factors)
    factors = {
        (row["session"], row["symbol"]): row
        for row in factor_source
    }
    anchors = {symbol: factors.get((anchor, symbol)) for symbol in selected}
    if any(value is None for value in anchors.values()):
        missing = [symbol for symbol, value in anchors.items() if value is None]
        raise ArtifactError(f"adjusted-price anchor factor is unavailable for {missing!r}")
    consumed_factor_keys = {
        (anchor, symbol) for symbol in selected
    } | {
        (row["session"], row["symbol"])
        for row in market
        if (row["session"], row["symbol"]) in factors
    }
    consumed_factors = [factors[key] for key in sorted(consumed_factor_keys)]
    try:
        qualification = weakest_pit_qualification(consumed_factors)
        if pit_policy == "strict_decision_time":
            qualification = validate_strict_decision_time(consumed_factors, cutoff)
    except MarketContractError as exc:
        raise ArtifactError(
            f"adjusted-price PIT qualification is insufficient: {exc}"
        ) from exc
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
        "pit_qualification": qualification,
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
            "availability_qualification": qualification,
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
    return _load_adjusted_price_view(data_root, view_id, checked_reader=reader, structural_only=True).ref


def load_adjusted_price_view(data_root: str | Path, view_id: str) -> DerivedView:
    return _load_adjusted_price_view(data_root, view_id)


def _view_reader(data_root, snapshot_id, checked_reader):
    reader = checked_reader if checked_reader is not None else SnapshotReader(data_root, snapshot_id)
    if reader.data_root != _layout(data_root).root or reader.snapshot.ref.snapshot_id != snapshot_id:
        raise ArtifactError('checked Reader does not match View root/Snapshot')
    return reader


def _load_adjusted_price_view(data_root, view_id, *, checked_reader=None, structural_only=False):
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
    reader = _view_reader(layout.root, snapshot_ref["snapshot_id"], checked_reader)
    snapshot = reader.snapshot
    if snapshot_ref != {
        "snapshot_id": snapshot.ref.snapshot_id,
        "identity_digest": snapshot.manifest["identity_digest"],
    }:
        raise ArtifactError("adjusted-price Snapshot ref mismatch")
    for domain in ("market_daily", "adjustment_factors"):
        expected = _snapshot_domain_ref(snapshot, domain)
        if manifest.get("domain_refs", {}).get(domain) != expected:
            raise ArtifactError("adjusted-price domain ref mismatch")
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
    validate_symbols(scope.get("symbols"))
    start = validate_session(scope.get("start_session"), "adjusted start")
    end = validate_session(scope.get("end_session"), "adjusted end")
    anchor = validate_session(manifest.get("anchor_session"), "adjusted anchor")
    cutoff = validate_session(manifest.get("decision_cutoff"), "adjusted cutoff")
    policy = manifest.get("pit_policy")
    if not start <= anchor <= end or policy not in {
        "strict_decision_time",
        "research_non_pit",
    } or (policy == "strict_decision_time" and anchor > cutoff):
        raise ArtifactError("adjusted-price anchor policy is invalid")
    qualification = manifest.get("pit_qualification")
    if structural_only:
        if qualification not in {"verified", "observed", "best_effort", "unknown"}:
            raise ArtifactError("adjusted-price PIT qualification is invalid")
    else:
        selected = set(validate_symbols(scope["symbols"]))
        selected_symbols = tuple(sorted(selected))
        batched_factors = _adjusted_batch_rows(reader, "adjustment_factors", selected_symbols, start, end)
        batched_market = _adjusted_batch_rows(reader, "market_daily", selected_symbols, start, end)
        factor_rows = {
            (row["session"], row["symbol"]): row
            for row in (reader.session_rows("adjustment_factors", start, end)
                        if batched_factors is None else batched_factors)
            if row["symbol"] in selected and start <= row["session"] <= end
        }
        consumed_keys = {(anchor, symbol) for symbol in selected} | {
            (row["session"], row["symbol"])
            for row in (reader.session_rows("market_daily", start, end)
                        if batched_market is None else batched_market)
            if row["symbol"] in selected
            and start <= row["session"] <= end
            and (row["session"], row["symbol"]) in factor_rows
        }
        consumed_factors = [factor_rows[key] for key in sorted(consumed_keys)]
        try:
            qualification = weakest_pit_qualification(consumed_factors)
            if policy == "strict_decision_time":
                qualification = validate_strict_decision_time(consumed_factors, cutoff)
        except (KeyError, MarketContractError) as exc:
            raise ArtifactError("adjusted-price PIT evidence is invalid") from exc
    if manifest.get("pit_qualification") != qualification:
        raise ArtifactError("adjusted-price PIT qualification mismatch")
    if manifest.get("validation_summary") != {
        "status": "PASS",
        "anchor_factors_present": True,
        "future_anchor_blocked": policy != "strict_decision_time" or anchor <= cutoff,
        "availability_qualification": qualification,
    }:
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
        pr6_fact_view_id: str | None = None,
        financial_fact_view_id: str | None = None,
    ) -> None:
        if pr6_fact_view_id is not None and financial_fact_view_id is not None:
            raise ArtifactError("provide one financial FactView ID")
        financial_fact_view_id = financial_fact_view_id or pr6_fact_view_id
        self.reader = SnapshotReader(data_root, snapshot_id)
        self.financial = None
        if financial_fact_view_id is not None:
            from axiom_data.financial_views import load_financial_fact_view_with_reader
            layout = _layout(data_root)
            identity = _identity('view_id', financial_fact_view_id)
            manifest, _ = _load_manifest(layout.root, layout.derived_commits('pr6_fact') / identity,
                artifact_type='pr6_fact_view', schema_version=('pr6_fact_view.v1', 'pr6_fact_view.v2', 'pr6_fact_view.v3', 'pr6_fact_view.v4'),
                identity_field='view_id', identity=identity)
            # v1 keeps its frozen LegacyReader projection. v2 can share the
            # complete closure checked in this constructor invocation.
            self.financial = load_financial_fact_view_with_reader(data_root, identity,
                checked_reader=self.reader if manifest['schema_version'] in {'pr6_fact_view.v2','pr6_fact_view.v3','pr6_fact_view.v4'} else None)
            if self.financial.manifest["snapshot_ref"]["snapshot_id"] != snapshot_id:
                raise ArtifactError("financial FactView belongs to another Snapshot")
        self.pr6 = self.financial  # Historical attribute alias.
        self.adjusted = (
            _load_adjusted_price_view(data_root, adjusted_price_view_id, checked_reader=self.reader)
            if adjusted_price_view_id is not None
            else None
        )
        if self.adjusted is not None and self.adjusted.manifest["snapshot_ref"]["snapshot_id"] != snapshot_id:
            raise ArtifactError("FactView Derived ref belongs to another Snapshot")

    def leaf_fact(self, leaf, **query):
        return self.reader.leaf_fact(leaf, **query)

    def financial_members(self, group_id, target_session, *, symbols=None):
        """Resolve a validated financial View's immutable universe reference."""
        if self.financial is None or 'membership_ref' not in self.financial.manifest:
            raise ArtifactNotFoundError('financial membership ref unavailable')
        return self.reader.members_from_view_ref(
            self.financial.manifest['membership_ref'],group_id,target_session,symbols=symbols)

    def read(
        self,
        domain: str,
        *,
        symbols: Sequence[str] | None = None,
        start_session: str | None = None,
        end_session: str | None = None,
        fields: Sequence[str] | None = None,
        price_basis: str = "canonical",
        pit_policy: str = "actual",
        cutoff_policy: str | None = None,
    ) -> dict[str, Any]:
        if domain in {"pr6", "financial"}:
            if self.financial is None:
                raise ArtifactNotFoundError("VIEW_NOT_BUILT: explicit financial FactView required")
            manifest = self.financial.manifest
            if pit_policy not in {"actual", manifest["pit_policy"]}:
                raise ArtifactError("financial FactView policy differs from materialization")
            if cutoff_policy is not None and cutoff_policy != manifest["cutoff_policy"]:
                raise ArtifactError("financial FactView cutoff differs from materialization")
            if manifest['schema_version']=='pr6_fact_view.v1':
                selected=set(symbols) if symbols is not None else None
                selected_fields=tuple(fields or manifest['fields'])
                if not selected_fields or set(selected_fields)-set(manifest['fields']):
                    raise ArtifactError('invalid v1 FactView fields')
                return {'snapshot_ref':manifest['snapshot_ref'],'view_id':self.financial.ref.view_id,
                    'rows':tuple({'symbol':r['symbol'],'session':r['session'],**{f:r['values'][f] for f in selected_fields}}
                        for r in self.financial.rows if (selected is None or r['symbol'] in selected)
                        and (start_session is None or r['session']>=start_session)
                        and (end_session is None or r['session']<=end_session)),
                    'fields':selected_fields,'pit_policy':manifest['pit_policy'],
                    'cutoff_policy':manifest['cutoff_policy'],'knowledge_cutoff':manifest['knowledge_cutoff'],
                    'pit_qualification':manifest['pit_qualification'],'domain_refs':manifest['domain_refs']}
            from axiom_data.financial_coverage import admit_materialized
            selected = set(symbols) if symbols is not None else set(manifest['validated_scope']['symbols'])
            selected_fields = tuple(fields) if fields is not None else tuple(manifest["fields"])
            admit_materialized(manifest,selected,start_session or manifest['validated_scope']['start_session'],
                               end_session or manifest['validated_scope']['end_session'],selected_fields)
            if not selected_fields or len(selected_fields)!=len(set(selected_fields)) or set(selected_fields)-set(manifest["fields"]):
                raise ArtifactError("invalid financial FactView fields")
            rows = tuple({"symbol":r["symbol"],"session":r["session"],
                          **{f:r["values"][f] for f in selected_fields}} for r in self.financial.rows
                         if (selected is None or r["symbol"] in selected)
                         and (start_session is None or r["session"]>=start_session)
                         and (end_session is None or r["session"]<=end_session))
            return {"snapshot_ref":manifest["snapshot_ref"],"view_id":self.financial.ref.view_id,
                    "rows":rows,"fields":selected_fields,"pit_policy":manifest["pit_policy"],
                    "cutoff_policy":manifest["cutoff_policy"],"knowledge_cutoff":manifest["knowledge_cutoff"],
                    "pit_qualification":manifest["pit_qualification"],"domain_refs":manifest["domain_refs"],
                    "industry_mapping":manifest['industry_mapping'],
                    "requested_scope":{"symbols":sorted(selected),"start_session":start_session or manifest['scope']['start_session'],
                        "end_session":end_session or manifest['scope']['end_session'],"fields":list(selected_fields)},
                    "actual_available_scope":manifest['actual_available_scope'],"validated_scope":manifest['validated_scope'],
                    **({'membership_ref':manifest['membership_ref']} if manifest['schema_version']=='pr6_fact_view.v4' else {}),
                    "facts":tuple({'symbol':r['symbol'],'session':r['session'],
                        'fields':{f:r['facts'][f] for f in selected_fields}} for r in self.financial.rows
                        if r['symbol'] in selected and (start_session is None or r['session']>=start_session)
                        and (end_session is None or r['session']<=end_session))}
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
            selected_symbols = set(validate_symbols(symbols)) if symbols is not None else None
            start = validate_session(start_session, "start_session") if start_session else None
            end = validate_session(end_session, "end_session") if end_session else None
            values = tuple(
                {field: row[field] for field in selected_fields}
                for row in rows
                if (selected_symbols is None or row["symbol"] in selected_symbols)
                and (start is None or row["session"] >= start)
                and (end is None or row["session"] <= end)
            )
            derived_refs = [dict(self.adjusted.manifest["domain_refs"], view_id=self.adjusted.ref.view_id)]
            anchor = self.adjusted.manifest["anchor_session"]
            actual_policy = self.adjusted.manifest["pit_policy"]
            qualification = self.adjusted.manifest["pit_qualification"]
            actual_cutoff = f"decision_cutoff={self.adjusted.manifest['decision_cutoff']}"
            response_fields = selected_fields
        else:
            if price_basis not in {"canonical", "unadjusted"}:
                raise ArtifactError("canonical fact domain does not support this price basis")
            full_values = self.reader.facts(
                domain,
                symbols=symbols,
                start_session=start_session,
                end_session=end_session,
            )
            response_fields = tuple(fields or self.reader.schema(domain))
            values = tuple(ordered_row(row, response_fields) for row in full_values)
            derived_refs = []
            anchor = None
            qualification = (
                weakest_pit_qualification(full_values)
                if full_values and "pit_qualification" in full_values[0]
                else "unknown"
            )
            actual_policy = qualification
            actual_cutoff = "snapshot_bound"
        if pit_policy != "actual":
            if pit_policy in _PIT_STRENGTH:
                if _PIT_STRENGTH[qualification] < _PIT_STRENGTH[pit_policy]:
                    raise ArtifactError("FactView requested PIT qualification is unavailable")
            elif pit_policy != actual_policy:
                raise ArtifactError("FactView PIT policy differs from its underlying facts")
        if cutoff_policy is not None and cutoff_policy != actual_cutoff:
            raise ArtifactError("FactView cutoff policy differs from its underlying facts")
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
            "pit_policy": actual_policy,
            "pit_qualification": qualification,
            "cutoff_policy": actual_cutoff,
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

    return _build_market_replay_view(SnapshotReader(data_root, snapshot_id),
        symbols=symbols,start_session=start_session,end_session=end_session,created_at=created_at)


def _build_market_replay_view(reader, *, symbols, start_session, end_session, created_at=None):
    data_root = reader.data_root
    selected = validate_symbols(symbols)
    start = validate_session(start_session, "start_session")
    end = validate_session(end_session, "end_session")
    if start > end:
        raise ArtifactError("MarketReplayView scope is reversed")
    required = ("trading_calendar", "security_master", "market_daily", "security_status", "price_limits", "corporate_actions")
    for domain in required:
        _snapshot_domain_ref(reader.snapshot, domain)
    def source(domain, read):
        prepared = prepared_market_view_rows(reader, 'market_replay', domain, selected, start, end)
        return prepared if prepared is not None else read()
    security = {row["symbol"]: row for row in source('security_master', lambda: reader.security_master(selected))}
    market = {(row["session"], row["symbol"]): row for row in source('market_daily', lambda: reader.market_daily(selected, start, end))}
    status = {(row["session"], row["symbol"]): row for row in source('security_status', lambda: reader.facts("security_status", symbols=selected, start_session=start, end_session=end))}
    limits = {(row["session"], row["symbol"]): row for row in source('price_limits', lambda: reader.facts("price_limits", symbols=selected, start_session=start, end_session=end))}
    actions: dict[tuple[str, str], list[str]] = {}
    action_rows = source('corporate_actions', lambda: reader.facts("corporate_actions", symbols=selected, start_session=start, end_session=end))
    for row in action_rows:
        actions.setdefault((row["effective_date"], row["symbol"]), []).append(row["action_id"])
    replay_qualification = weakest_pit_qualification(
        [*status.values(), *limits.values(), *action_rows]
    )
    calendars = source('trading_calendar', lambda: reader.trading_calendar(start_session=start, end_session=end))
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
        "temporal_policy": {
            "mode": "post_session_replay",
            "replay_cutoff_session": end,
            "pit_policy": "research_non_pit",
            "pit_qualification": replay_qualification,
        },
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
    return _load_market_replay_view(data_root, view_id, checked_reader=reader, structural_only=True).ref


def load_market_replay_view(data_root: str | Path, view_id: str) -> DerivedView:
    return _load_market_replay_view(data_root, view_id)


def _load_market_replay_view(data_root, view_id, *, checked_reader=None, structural_only=False):
    layout = _layout(data_root)
    view_id = _identity("view_id", view_id)
    target = _view_target(data_root, "market_replay", view_id)
    manifest, manifest_digest = _load_manifest(layout.root, target, artifact_type="market_replay_view", schema_version="market_replay_view.v1", identity_field="view_id", identity=view_id)
    _validate_manifest_identity(manifest, "view_id", "market-replay", view_id)
    snapshot_ref = manifest.get("snapshot_ref")
    if not isinstance(snapshot_ref, dict) or not isinstance(snapshot_ref.get("snapshot_id"), str):
        raise ArtifactError("MarketReplayView Snapshot ref is invalid")
    reader = _view_reader(layout.root, snapshot_ref["snapshot_id"], checked_reader)
    snapshot = reader.snapshot
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
    scope = manifest.get("scope")
    policy = manifest.get("temporal_policy")
    if (
        not isinstance(scope, dict)
        or not isinstance(policy, dict)
        or policy.get("mode") != "post_session_replay"
        or policy.get("replay_cutoff_session") != scope.get("end_session")
        or policy.get("pit_policy") != "research_non_pit"
    ):
        raise ArtifactError("MarketReplayView temporal policy is invalid")
    selected = validate_symbols(scope.get("symbols"))
    if structural_only:
        if policy.get("pit_qualification") not in {"verified", "observed", "best_effort", "unknown"}:
            raise ArtifactError("MarketReplayView PIT qualification is invalid")
    else:
        qualification_rows = []
        for domain in ("security_status", "price_limits", "corporate_actions"):
            prepared = prepared_market_view_rows(reader, 'market_replay', domain, selected,
                                                 scope['start_session'], scope['end_session'])
            qualification_rows.extend(prepared if prepared is not None else reader.facts(
                domain, symbols=selected, start_session=scope['start_session'], end_session=scope['end_session']))
        if policy.get("pit_qualification") != weakest_pit_qualification(qualification_rows):
            raise ArtifactError("MarketReplayView PIT qualification mismatch")
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
