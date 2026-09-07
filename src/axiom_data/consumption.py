"""Snapshot-bound market Reader and minimal Qlib-compatible binary view."""

from __future__ import annotations

import math
import struct
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from axiom_data.artifacts import (
    ArtifactError,
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


MARKET_VIEW_FIELDS = (
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "volume_shares",
    "amount_cny",
    "adj_factor",
    "up_limit",
    "down_limit",
    "is_suspended",
    "turnover_rate",
    "total_market_cap_cny",
    "circulating_market_cap_cny",
)
_QLIB_EXPORTER_REVISION = "qlib-binary-market.v1"
_ADJUSTED_PRICE_FIELDS = ("open", "high", "low", "close")


def _session(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise ArtifactError(f"{name} must be an ISO date")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ArtifactError(f"{name} must be an ISO date") from exc
    return parsed.isoformat()


def _symbols(values: object) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise ArtifactError("symbols must be an ordered sequence")
    result = tuple(_identity("symbol", value) for value in values)
    if not result or len(result) != len(set(result)):
        raise ArtifactError("symbols must be non-empty and unique")
    for symbol in result:
        _qlib_symbol(symbol)
    return result


def _ordered_row(row: Mapping[str, Any], fields: Sequence[str]) -> dict[str, Any]:
    try:
        return {field: row[field] for field in fields}
    except KeyError as exc:
        raise ArtifactError(f"canonical row has no field {exc.args[0]!r}") from exc


class SnapshotReader:
    """Read only rows reachable from one explicit, fully validated Snapshot."""

    def __init__(self, data_root: str | Path, snapshot_id: str) -> None:
        self.data_root = Path(data_root)
        self.snapshot = load_snapshot(self.data_root, snapshot_id)
        self.commits = {
            domain: validate_domain_commit_closure(
                self.data_root,
                domain,
                self.snapshot.manifest["domain_refs"][domain]["domain_commit_id"],
            )
            for domain in self.snapshot.manifest["domain_refs"]
        }

    def schema(self, domain: str) -> tuple[str, ...]:
        try:
            fields = self.commits[domain].contract["fields"]
        except KeyError as exc:
            raise ArtifactError(f"snapshot has no readable domain {domain!r}") from exc
        return tuple(field["name"] for field in fields)

    def trading_calendar(
        self,
        *,
        exchange: str | None = None,
        start_session: str | None = None,
        end_session: str | None = None,
    ) -> tuple[dict[str, Any], ...]:
        start = _session(start_session, "start_session") if start_session else None
        end = _session(end_session, "end_session") if end_session else None
        fields = self.schema("trading_calendar")
        return tuple(
            _ordered_row(row, fields)
            for row in self.commits["trading_calendar"].rows
            if (exchange is None or row["exchange"] == exchange)
            and (start is None or row["session"] >= start)
            and (end is None or row["session"] <= end)
        )

    def security_master(
        self, symbols: Sequence[str] | None = None
    ) -> tuple[dict[str, Any], ...]:
        selected = set(_symbols(symbols)) if symbols is not None else None
        fields = self.schema("security_master")
        return tuple(
            _ordered_row(row, fields)
            for row in self.commits["security_master"].rows
            if selected is None or row["symbol"] in selected
        )

    def market_daily(
        self,
        symbols: Sequence[str],
        start_session: str,
        end_session: str,
    ) -> tuple[dict[str, Any], ...]:
        selected = set(_symbols(symbols))
        start = _session(start_session, "start_session")
        end = _session(end_session, "end_session")
        if start > end:
            raise ArtifactError("market session interval is reversed")
        fields = self.schema("market_daily")
        return tuple(
            _ordered_row(row, fields)
            for row in self.commits["market_daily"].rows
            if row["symbol"] in selected and start <= row["session"] <= end
        )

    def as_of(self, domain: str, *, knowledge_cutoff: str, pit_policy: str,
              symbols: Sequence[str] | None = None) -> tuple[dict[str, Any], ...]:
        from axiom_data.pit import select_revisions
        return select_revisions(self.facts(domain, symbols=symbols),
                                policy=pit_policy, knowledge_cutoff=knowledge_cutoff)

    def financial_derived(self, *, knowledge_cutoff: str, pit_policy: str,
                          symbols: Sequence[str] | None = None) -> tuple[dict[str, Any], ...]:
        from axiom_data.pit import financial_derived
        return financial_derived(self.facts("financial_events", symbols=symbols),
                                 policy=pit_policy, knowledge_cutoff=knowledge_cutoff)

    def members(self, group_id: str, target_session: str, *, knowledge_cutoff: str,
                pit_policy: str, domain: str = "universe_membership", symbols: Sequence[str] | None = None) -> tuple[dict[str, Any], ...]:
        from axiom_data.pit import members
        if domain not in {"universe_membership", "industry_membership"}:
            raise ArtifactError("membership domain required")
        from axiom_data.pr6_coverage import membership_coverage
        membership_coverage(self,domain,group_id,target_session,target_session,pit_policy,knowledge_cutoff,symbols)
        return members(self.facts(domain, symbols=symbols), group_id=group_id,
                       target_session=_session(target_session, "target_session"),
                       knowledge_cutoff=knowledge_cutoff, policy=pit_policy)

    def membership_facts(self, group_id, target_session, *, knowledge_cutoff, pit_policy,
                         domain='universe_membership'):
        rows=self.members(group_id,target_session,knowledge_cutoff=knowledge_cutoff,
                          pit_policy=pit_policy,domain=domain)
        return {'group_id':group_id,'version':self.commits[domain].ref.commit_id,
                'snapshot_id':self.snapshot.ref.snapshot_id,'pit_policy':pit_policy,
                'knowledge_cutoff':knowledge_cutoff,'target_session':target_session,'rows':rows}

    def historical_union(self, group_id: str, start_session: str, end_session: str,
                         lookback_start: str, *, knowledge_cutoff: str,
                         pit_policy: str) -> tuple[str, ...]:
        from axiom_data.pit import historical_union
        from axiom_data.pr6_coverage import membership_coverage
        membership_coverage(self,'universe_membership',group_id,lookback_start,end_session,pit_policy,knowledge_cutoff)
        return historical_union(self.facts("universe_membership"), group_id=group_id,
            start_session=_session(start_session, "start_session"),
            end_session=_session(end_session, "end_session"),
            lookback_start=_session(lookback_start, "lookback_start"),
            knowledge_cutoff=knowledge_cutoff, policy=pit_policy)

    def facts(
        self,
        domain: str,
        *,
        symbols: Sequence[str] | None = None,
        start_session: str | None = None,
        end_session: str | None = None,
        fields: Sequence[str] | None = None,
    ) -> tuple[dict[str, Any], ...]:
        """Read one canonical fact domain without resolving or building anything."""

        if domain not in self.commits:
            raise ArtifactError(f"snapshot has no readable domain {domain!r}")
        from axiom_data.domains import PR6_DOMAINS
        if domain in PR6_DOMAINS:
            from axiom_data.pr6_coverage import require_symbols
            require_symbols(self.commits[domain].rows,symbols)
            if start_session is not None or end_session is not None:
                if domain != 'valuation_daily':
                    raise ArtifactError('INSUFFICIENT_SCOPE: use explicit PIT membership/financial Reader')
                candidates=[r for r in self.commits[domain].rows if symbols is None or r['symbol'] in symbols]
                from axiom_data.pr6_coverage import require_range
                days=[r['session'] for r in candidates]
                if not days:raise ArtifactError('INSUFFICIENT_SCOPE: no valuation coverage')
                require_range(start_session or min(days),end_session or max(days),min(days),max(days))
                expected={(r['symbol'],c['session']) for r in candidates for c in self.trading_calendar(
                    start_session=start_session or min(days),end_session=end_session or max(days)) if c['is_open']}
                if not expected<={(r['symbol'],r['session']) for r in candidates}:
                    raise ArtifactError('INSUFFICIENT_SCOPE: valuation gap')
        available = self.schema(domain)
        selected_fields = tuple(fields or available)
        if not selected_fields or len(selected_fields) != len(set(selected_fields)) or any(
            field not in available for field in selected_fields
        ):
            raise ArtifactError("Fact fields must be a non-empty ordered schema subset")
        selected_symbols = set(_symbols(symbols)) if symbols is not None else None
        start = _session(start_session, "start_session") if start_session else None
        end = _session(end_session, "end_session") if end_session else None
        if start is not None and end is not None and start > end:
            raise ArtifactError("fact session interval is reversed")
        rows = []
        for row in self.commits[domain].rows:
            row_symbol = row.get("symbol")
            row_session = row.get("session", row.get("effective_date"))
            if selected_symbols is not None and row_symbol not in selected_symbols:
                continue
            if start is not None and (not isinstance(row_session, str) or row_session < start):
                continue
            if end is not None and (not isinstance(row_session, str) or row_session > end):
                continue
            rows.append(_ordered_row(row, selected_fields))
        return tuple(rows)


@dataclass(frozen=True, slots=True)
class QlibViewRef:
    view_id: str
    manifest_digest: str


@dataclass(frozen=True, slots=True)
class QlibView:
    ref: QlibViewRef
    manifest: dict[str, Any]


def _qlib_symbol(symbol: object) -> str:
    if (
        not isinstance(symbol, str)
        or len(symbol) != 9
        or symbol[6] != "."
        or not symbol[:6].isdigit()
        or symbol[7:] not in {"SH", "SZ"}
    ):
        raise ArtifactError("QlibView symbol is not canonical")
    code, suffix = symbol.split(".")
    return f"{suffix}{code}"


def _feature_bytes(start_index: int, values: Sequence[object]) -> bytes:
    encoded = [float(start_index)]
    for value in values:
        if value is None:
            encoded.append(float("nan"))
        elif isinstance(value, bool):
            encoded.append(1.0 if value else 0.0)
        elif isinstance(value, (int, float)) and math.isfinite(float(value)):
            encoded.append(float(value))
        else:
            raise ArtifactError("Qlib market fields must be finite numeric values or null")
    return struct.pack(f"<{len(encoded)}f", *encoded)


def build_qlib_view(
    data_root: str | Path,
    snapshot_id: str,
    *,
    symbols: Sequence[str],
    start_session: str,
    end_session: str,
    fields: Sequence[str] = MARKET_VIEW_FIELDS,
    adjusted_price_view_id: str | None = None,
    price_basis: str = "unadjusted",
    pit_policy: str = "best_effort",
    decision_cutoff: str | None = None,
    created_at: str | None = None,
) -> QlibViewRef:
    """Atomically create one immutable Qlib binary view from an explicit Snapshot."""

    reader = SnapshotReader(data_root, snapshot_id)
    selected = _symbols(symbols)
    start = _session(start_session, "start_session")
    end = _session(end_session, "end_session")
    if start > end:
        raise ArtifactError("QlibView session interval is reversed")
    view_fields = tuple(fields)
    if (
        not view_fields
        or len(view_fields) != len(set(view_fields))
        or any(field not in MARKET_VIEW_FIELDS for field in view_fields)
    ):
        raise ArtifactError("QlibView fields must be an ordered unique market field subset")
    if price_basis not in {"unadjusted", "anchor_adjusted"}:
        raise ArtifactError("QlibView price basis is invalid")
    if pit_policy not in {"strict_decision_time", "research_non_pit", "best_effort"}:
        raise ArtifactError("QlibView PIT policy is invalid")
    adjusted = None
    if price_basis == "anchor_adjusted":
        if pit_policy not in {"strict_decision_time", "research_non_pit"}:
            raise ArtifactError("adjusted QlibView requires an explicit PIT policy")
        if adjusted_price_view_id is None:
            raise ArtifactError("adjusted QlibView requires an explicit Derived ref")
        from axiom_data.views import load_adjusted_price_view

        adjusted = load_adjusted_price_view(data_root, adjusted_price_view_id)
        if adjusted.manifest["snapshot_ref"]["snapshot_id"] != snapshot_id:
            raise ArtifactError("QlibView Derived ref belongs to another Snapshot")
        if adjusted.manifest["scope"] != {
            "symbols": list(selected),
            "start_session": start,
            "end_session": end,
            "interval": "closed",
        }:
            raise ArtifactError("QlibView Derived scope mismatch")
        if decision_cutoff is None:
            raise ArtifactError("adjusted QlibView requires an explicit decision cutoff")
        cutoff = _session(decision_cutoff, "decision_cutoff")
        if (
            pit_policy != adjusted.manifest["pit_policy"]
            or cutoff != adjusted.manifest["decision_cutoff"]
        ):
            raise ArtifactError("QlibView PIT policy/cutoff differs from its Derived view")
    elif adjusted_price_view_id is not None:
        raise ArtifactError("unadjusted QlibView must not carry an adjusted Derived ref")

    calendar = sorted(
        {
            row["session"]
            for row in reader.trading_calendar(
                start_session=start, end_session=end
            )
            if row["is_open"] is True
        }
    )
    if not calendar:
        raise ArtifactError("QlibView scope has no open calendar sessions")
    security = {row["symbol"]: row for row in reader.security_master(selected)}
    if set(security) != set(selected):
        raise ArtifactError("QlibView scope includes an unknown security identity")
    market = {
        (row["session"], row["symbol"]): row
        for row in reader.market_daily(selected, start, end)
    }
    if adjusted is not None:
        adjusted_rows = {
            (row["session"], row["symbol"]): row for row in adjusted.rows
        }
        for key, row in market.items():
            derived = adjusted_rows.get(key)
            if derived is not None:
                for field in _ADJUSTED_PRICE_FIELDS:
                    row[field] = derived[field]

    instrument_scope: list[dict[str, str]] = []
    for symbol in selected:
        identity = security[symbol]
        listed = identity["list_session"]
        if listed is None:
            raise ArtifactError("QlibView requires a known list_session")
        eligible = [
            session
            for session in calendar
            if session >= listed
            and (
                identity["delist_session"] is None
                or session < identity["delist_session"]
            )
        ]
        if not eligible:
            raise ArtifactError(f"QlibView has no identity interval for {symbol!r}")
        instrument_scope.append(
            {
                "symbol": symbol,
                "qlib_symbol": _qlib_symbol(symbol),
                "start_session": eligible[0],
                "end_session": eligible[-1],
                "storage_path": f"features/{_qlib_symbol(symbol).lower()}",
            }
        )

    outputs: dict[str, bytes] = {
        "calendars/day.txt": ("\n".join(calendar) + "\n").encode("ascii"),
        "instruments/all.txt": (
            "".join(
                f"{item['qlib_symbol']}\t{item['start_session']}\t{item['end_session']}\n"
                for item in instrument_scope
            )
        ).encode("ascii"),
    }
    calendar_index = {session: index for index, session in enumerate(calendar)}
    for item in instrument_scope:
        symbol = item["symbol"]
        first = calendar_index[item["start_session"]]
        last = calendar_index[item["end_session"]]
        sessions = calendar[first : last + 1]
        for field in view_fields:
            values = [market.get((session, symbol), {}).get(field) for session in sessions]
            outputs[f"{item['storage_path']}/{field}.day.bin"] = _feature_bytes(
                first, values
            )

    output_files = [
        {"path": path, "content_digest": _digest(content), "bytes": len(content)}
        for path, content in sorted(outputs.items())
    ]
    exporter = {
        "implementation": "axiom_data.consumption.build_qlib_view",
        "revision": _QLIB_EXPORTER_REVISION,
    }
    exporter["digest"] = _digest(_json_bytes(exporter))
    manifest: dict[str, Any] = {
        "artifact_type": "qlib_view",
        "schema_version": "qlib_view.v2" if adjusted is not None else "qlib_view.v1",
        "snapshot_ref": {
            "snapshot_id": reader.snapshot.ref.snapshot_id,
            "identity_digest": reader.snapshot.manifest["identity_digest"],
        },
        "fields": list(view_fields),
        "scope": {
            "symbols": list(selected),
            "start_session": start,
            "end_session": end,
            "interval": "closed",
        },
        "exporter_ref": exporter,
        "exporter_config": {
            "calendar": "union-of-open-SSE-SZSE-sessions",
            "feature_dtype": "little-endian-float32",
            "missing_value": "IEEE-754 NaN",
        },
        "instrument_storage_scope": instrument_scope,
        "output_files": output_files,
        "validation_summary": {
            "status": "PASS",
            "calendar_sessions": len(calendar),
            "instruments": len(instrument_scope),
            "fields": len(view_fields),
        },
    }
    if adjusted is not None:
        manifest.update(
            {
                "derived_refs": [
                    {
                        "view_id": adjusted.ref.view_id,
                        "identity_digest": adjusted.manifest["identity_digest"],
                    }
                ],
                "price_basis": price_basis,
                "anchor_session": adjusted.manifest["anchor_session"],
                "pit_policy": pit_policy,
                "pit_qualification": adjusted.manifest["pit_qualification"],
                "decision_cutoff": cutoff,
                "source_quality_refs": adjusted.manifest["domain_refs"],
            }
        )
    identity_digest = _identity_digest(manifest, "view_id")
    view_id = _derived_identity("qlib", identity_digest)
    manifest["view_id"] = view_id
    manifest["identity_digest"] = identity_digest
    manifest["created_at"] = _timestamp(created_at)

    layout = _layout(data_root)
    target = layout.qlib_exports / view_id

    def prepare(candidate: Path) -> None:
        for relative, content in outputs.items():
            path = candidate.joinpath(*relative.split("/"))
            path.parent.mkdir(parents=True, exist_ok=True)
            _write_file(path, content)
        _write_manifest(candidate, manifest)

    _publish_directory(layout, target, prepare, identity_digest=identity_digest)
    return load_qlib_view(layout.root, view_id).ref


def load_qlib_view(data_root: str | Path, view_id: str) -> QlibView:
    """Verify one exact view, all files, and its source Snapshot closure."""

    if isinstance(view_id, str) and view_id.startswith("pr6-fact-"):
        from axiom_data.pr6_views import load_pr6_fact_view
        return load_pr6_fact_view(data_root, view_id)
    layout = _layout(data_root)
    view_id = _identity("view_id", view_id)
    target = layout.qlib_exports / view_id
    manifest, manifest_digest = _load_manifest(
        layout.root,
        target,
        artifact_type="qlib_view",
        schema_version=("qlib_view.v1", "qlib_view.v2"),
        identity_field="view_id",
        identity=view_id,
    )
    _validate_manifest_identity(manifest, "view_id", "qlib", view_id)
    snapshot_ref = manifest.get("snapshot_ref")
    if not isinstance(snapshot_ref, dict) or not isinstance(
        snapshot_ref.get("snapshot_id"), str
    ):
        raise ArtifactError("QlibView snapshot ref is invalid")
    snapshot = load_snapshot(layout.root, snapshot_ref["snapshot_id"])
    if snapshot_ref != {
        "snapshot_id": snapshot.ref.snapshot_id,
        "identity_digest": snapshot.manifest["identity_digest"],
    }:
        raise ArtifactError("QlibView snapshot ref does not match its artifact")
    if manifest.get("schema_version") == "qlib_view.v2":
        from axiom_data.views import load_adjusted_price_view

        derived_refs = manifest.get("derived_refs")
        if not isinstance(derived_refs, list) or len(derived_refs) != 1:
            raise ArtifactError("adjusted QlibView Derived refs are invalid")
        ref = derived_refs[0]
        if not isinstance(ref, dict) or not isinstance(ref.get("view_id"), str):
            raise ArtifactError("adjusted QlibView Derived ref is invalid")
        adjusted = load_adjusted_price_view(layout.root, ref["view_id"])
        if ref != {
            "view_id": adjusted.ref.view_id,
            "identity_digest": adjusted.manifest["identity_digest"],
        }:
            raise ArtifactError("adjusted QlibView Derived ref mismatch")
        if (
            manifest.get("price_basis") != "anchor_adjusted"
            or manifest.get("anchor_session") != adjusted.manifest["anchor_session"]
            or manifest.get("decision_cutoff") != adjusted.manifest["decision_cutoff"]
            or manifest.get("pit_policy") != adjusted.manifest["pit_policy"]
            or manifest.get("pit_qualification")
            != adjusted.manifest["pit_qualification"]
        ):
            raise ArtifactError("adjusted QlibView anchor/PIT metadata is invalid")
        if manifest.get("source_quality_refs") != adjusted.manifest["domain_refs"]:
            raise ArtifactError("adjusted QlibView source/quality refs are invalid")
        if manifest.get("scope") != adjusted.manifest["scope"]:
            raise ArtifactError("adjusted QlibView scope differs from its Derived view")
        _session(manifest["decision_cutoff"], "QlibView decision cutoff")
    fields = manifest.get("fields")
    scope = manifest.get("scope")
    instruments = manifest.get("instrument_storage_scope")
    if (
        not isinstance(fields, list)
        or not fields
        or len(fields) != len(set(fields))
        or any(field not in MARKET_VIEW_FIELDS for field in fields)
        or not isinstance(scope, dict)
        or scope.get("interval") != "closed"
        or not isinstance(scope.get("symbols"), list)
        or not isinstance(instruments, list)
        or len(instruments) != len(scope["symbols"])
    ):
        raise ArtifactError("QlibView field or scope metadata is invalid")
    _session(scope.get("start_session"), "QlibView scope start")
    _session(scope.get("end_session"), "QlibView scope end")
    if (
        _symbols(scope["symbols"]) != tuple(scope["symbols"])
        or scope["start_session"] > scope["end_session"]
    ):
        raise ArtifactError("QlibView scope is invalid")
    if [item.get("symbol") for item in instruments if isinstance(item, dict)] != scope[
        "symbols"
    ]:
        raise ArtifactError("QlibView instrument order does not match its scope")
    for item in instruments:
        if (
            not isinstance(item, dict)
            or item.get("qlib_symbol") != _qlib_symbol(item.get("symbol", ""))
            or item.get("storage_path")
            != f"features/{item['qlib_symbol'].lower()}"
        ):
            raise ArtifactError("QlibView instrument mapping is invalid")
        _session(item.get("start_session"), "QlibView instrument start")
        _session(item.get("end_session"), "QlibView instrument end")
        if not (
            scope["start_session"]
            <= item["start_session"]
            <= item["end_session"]
            <= scope["end_session"]
        ):
            raise ArtifactError("QlibView instrument scope is invalid")
    exporter = manifest.get("exporter_ref")
    if not isinstance(exporter, dict) or set(exporter) != {
        "implementation",
        "revision",
        "digest",
    }:
        raise ArtifactError("QlibView exporter ref is invalid")
    descriptor = {
        "implementation": exporter["implementation"],
        "revision": exporter["revision"],
    }
    if descriptor != {
        "implementation": "axiom_data.consumption.build_qlib_view",
        "revision": _QLIB_EXPORTER_REVISION,
    } or exporter["digest"] != _digest(_json_bytes(descriptor)):
        raise ArtifactError("QlibView exporter ref digest mismatch")
    if manifest.get("exporter_config") != {
        "calendar": "union-of-open-SSE-SZSE-sessions",
        "feature_dtype": "little-endian-float32",
        "missing_value": "IEEE-754 NaN",
    }:
        raise ArtifactError("QlibView exporter config is invalid")
    output_files = manifest.get("output_files")
    if not isinstance(output_files, list):
        raise ArtifactError("QlibView output manifest is invalid")
    required_paths = {"calendars/day.txt", "instruments/all.txt"}
    required_paths.update(
        f"{item['storage_path']}/{field}.day.bin"
        for item in instruments
        for field in fields
    )
    declared_paths = [
        entry.get("path") for entry in output_files if isinstance(entry, dict)
    ]
    if (
        len(declared_paths) != len(output_files)
        or len(declared_paths) != len(required_paths)
        or set(declared_paths) != required_paths
    ):
        raise ArtifactError("QlibView output file set is invalid")
    expected: dict[str, str] = {}
    for entry in output_files:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise ArtifactError("QlibView output entry is invalid")
        path = _relative_file(layout.root, target, entry["path"])
        if not path.is_file():
            raise ArtifactError("QlibView output file is missing")
        content = path.read_bytes()
        if entry.get("content_digest") != _digest(content) or entry.get("bytes") != len(
            content
        ):
            raise ArtifactError("QlibView output file digest mismatch")
        if entry["path"].endswith(".day.bin") and (
            len(content) < 8 or len(content) % 4
        ):
            raise ArtifactError("QlibView feature file framing is invalid")
        expected[entry["path"]] = entry["content_digest"]
    if _content_tree_digests(target) != expected:
        raise ArtifactError("QlibView contains undeclared output content")
    if not isinstance(manifest.get("created_at"), str):
        raise ArtifactError("QlibView created_at is missing")
    _timestamp(manifest["created_at"])
    calendar_path = _relative_file(
        layout.root, target, "calendars/day.txt"
    )
    calendar = tuple(calendar_path.read_text(encoding="ascii").splitlines())
    if not calendar or calendar != tuple(sorted(set(calendar))):
        raise ArtifactError("QlibView calendar is not ordered and unique")
    for value in calendar:
        _session(value, "QlibView calendar session")
    instruments_path = _relative_file(
        layout.root, target, "instruments/all.txt"
    )
    expected_instruments = "".join(
        f"{item['qlib_symbol']}\t{item['start_session']}\t{item['end_session']}\n"
        for item in instruments
    )
    if instruments_path.read_text(encoding="ascii") != expected_instruments:
        raise ArtifactError("QlibView instruments file does not match its manifest")
    if manifest.get("validation_summary") != {
        "status": "PASS",
        "calendar_sessions": len(calendar),
        "instruments": len(instruments),
        "fields": len(fields),
    }:
        raise ArtifactError("QlibView validation summary is invalid")
    return QlibView(QlibViewRef(view_id, manifest_digest), manifest)


class QlibViewReader:
    """Read the exact binary field layout emitted by build_qlib_view()."""

    def __init__(self, data_root: str | Path, view_id: str) -> None:
        self.data_root = Path(data_root)
        self.view = load_qlib_view(self.data_root, view_id)
        self.path = (_layout(self.data_root).derived_commits("pr6_fact") / view_id
                     if view_id.startswith("pr6-fact-") else _layout(self.data_root).qlib_exports / view_id)

    def fact_metadata(self):
        if self.view.manifest.get('artifact_type') != 'pr6_fact_view':
            raise ArtifactError('PR6 FactView metadata required')
        return {'view_id':self.view.ref.view_id,'snapshot_ref':self.view.manifest['snapshot_ref'],
                'industry_mapping':self.view.manifest['industry_mapping'],
                'validated_scope':self.view.manifest['validated_scope'],
                'rows':tuple({'symbol':r['symbol'],'session':r['session'],'fields':r['facts']} for r in self.view.rows)}

    def calendar(self) -> tuple[str, ...]:
        layout = _layout(self.data_root)
        path = _relative_file(
            layout.root, self.path, "calendars/day.txt"
        )
        content = path.read_text(encoding="ascii")
        return tuple(line for line in content.splitlines() if line)

    def instrument_mapping(self) -> tuple[tuple[str, str], ...]:
        return tuple(
            (item["symbol"], item["qlib_symbol"])
            for item in self.view.manifest["instrument_storage_scope"]
        )

    def market_daily(self, *, include_missing: bool = False) -> tuple[dict[str, Any], ...]:
        calendar = self.calendar()
        fields = tuple(self.view.manifest["fields"])
        rows: list[dict[str, Any]] = []
        for item in self.view.manifest["instrument_storage_scope"]:
            values_by_field: dict[str, tuple[float, ...]] = {}
            first_index: int | None = None
            for field in fields:
                layout = _layout(self.data_root)
                path = _relative_file(
                    layout.root,
                    self.path,
                    f"{item['storage_path']}/{self.view.manifest.get('qlib_field_mapping', {}).get(field, field)}.day.bin",
                )
                content = path.read_bytes()
                if len(content) < 8 or len(content) % 4:
                    raise ArtifactError("Qlib feature file has invalid float32 framing")
                values = struct.unpack(f"<{len(content) // 4}f", content)
                current_first = int(values[0])
                if values[0] != current_first or not 0 <= current_first < len(calendar):
                    raise ArtifactError("Qlib feature file has an invalid calendar offset")
                if first_index is None:
                    first_index = current_first
                elif first_index != current_first:
                    raise ArtifactError("Qlib feature fields have inconsistent offsets")
                values_by_field[field] = values[1:]
            assert first_index is not None
            lengths = {len(values) for values in values_by_field.values()}
            if len(lengths) != 1:
                raise ArtifactError("Qlib feature fields have inconsistent lengths")
            length = lengths.pop()
            if first_index + length > len(calendar):
                raise ArtifactError("Qlib feature values exceed their calendar")
            if (
                calendar[first_index] != item["start_session"]
                or calendar[first_index + length - 1] != item["end_session"]
            ):
                raise ArtifactError("Qlib feature range does not match instrument scope")
            for offset in range(length):
                values: dict[str, Any] = {}
                for field in fields:
                    value = values_by_field[field][offset]
                    if math.isnan(value):
                        values[field] = None
                    elif field == "is_suspended":
                        values[field] = bool(value)
                    else:
                        values[field] = value
                if include_missing or any(value is not None for value in values.values()):
                    rows.append(
                        {
                            "session": calendar[first_index + offset],
                            "symbol": item["symbol"],
                            **values,
                        }
                    )
        return tuple(sorted(rows, key=lambda row: (row["session"], row["symbol"])))


def compare_direct_and_qlib(
    data_root: str | Path,
    snapshot_id: str,
    view_id: str,
    *,
    relative_tolerance: float = 1e-6,
) -> dict[str, Any]:
    """Compare exact keys/schema/missingness and tolerant float values."""

    direct = SnapshotReader(data_root, snapshot_id)
    view = QlibViewReader(data_root, view_id)
    manifest = view.view.manifest
    if manifest["snapshot_ref"]["snapshot_id"] != direct.snapshot.ref.snapshot_id:
        raise ArtifactError("QlibView belongs to another Snapshot")
    scope = manifest["scope"]
    fields = tuple(manifest["fields"])
    direct_rows = {
        (row["session"], row["symbol"]): row
        for row in direct.market_daily(
            scope["symbols"], scope["start_session"], scope["end_session"]
        )
    }
    if manifest.get("schema_version") == "qlib_view.v2":
        from axiom_data.views import load_adjusted_price_view

        adjusted = load_adjusted_price_view(
            data_root, manifest["derived_refs"][0]["view_id"]
        )
        adjusted_rows = {
            (row["session"], row["symbol"]): row for row in adjusted.rows
        }
        for key, row in direct_rows.items():
            derived = adjusted_rows.get(key)
            if derived is not None:
                for field in _ADJUSTED_PRICE_FIELDS:
                    row[field] = derived[field]
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
        pit_binding = {
            "pit_policy": adjusted.manifest["pit_policy"],
            "pit_qualification": adjusted.manifest["pit_qualification"],
            "decision_cutoff": adjusted.manifest["decision_cutoff"],
            "anchor_session": adjusted.manifest["anchor_session"],
        }
    else:
        adjusted_ref = None
        pit_binding = None
    view_rows = {
        (row["session"], row["symbol"]): row for row in view.market_daily()
    }
    expected_calendar = tuple(
        sorted(
            {
                row["session"]
                for row in direct.trading_calendar(
                    start_session=scope["start_session"],
                    end_session=scope["end_session"],
                )
                if row["is_open"] is True
            }
        )
    )
    expected_mapping = tuple((symbol, _qlib_symbol(symbol)) for symbol in scope["symbols"])
    mismatches: list[dict[str, Any]] = []
    for key in sorted(set(direct_rows) | set(view_rows)):
        if key not in direct_rows or key not in view_rows:
            mismatches.append({"key": list(key), "reason": "key_presence"})
            continue
        for field in fields:
            left = direct_rows[key][field]
            right = view_rows[key][field]
            if left is None or right is None:
                equal = left is None and right is None
            elif isinstance(left, bool):
                equal = left is right
            else:
                equal = math.isclose(
                    float(left), float(right), rel_tol=relative_tolerance, abs_tol=1e-6
                )
            if not equal:
                mismatches.append(
                    {
                        "key": list(key),
                        "field": field,
                        "direct": left,
                        "qlib": right,
                        "reason": "value_or_null",
                    }
                )
    checks = {
        "snapshot": manifest["snapshot_ref"]["snapshot_id"]
        == direct.snapshot.ref.snapshot_id,
        "field_order": fields
        == tuple(field for field in direct.schema("market_daily") if field in fields),
        "calendar": view.calendar() == expected_calendar,
        "instrument_mapping": view.instrument_mapping() == expected_mapping,
        "keys": set(direct_rows) == set(view_rows),
        "values_and_nulls": not mismatches,
    }
    qlib_report_ref = {
        "view_id": view.view.ref.view_id,
        "manifest_digest": view.view.ref.manifest_digest,
        "identity_digest": view.view.manifest["identity_digest"],
    }
    if manifest.get("schema_version") == "qlib_view.v2":
        qlib_report_ref.update(
            {
                "content_digest": _digest(
                    _json_bytes(
                        [
                            [entry["path"], entry["content_digest"]]
                            for entry in view.view.manifest["output_files"]
                        ]
                    )
                ),
                "anchor_session": manifest["anchor_session"],
                "pit_policy": manifest["pit_policy"],
                "pit_qualification": manifest["pit_qualification"],
                "decision_cutoff": manifest["decision_cutoff"],
                "derived_view_id": manifest["derived_refs"][0]["view_id"],
            }
        )
    return {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "source_snapshot_ref": {
            "snapshot_id": direct.snapshot.ref.snapshot_id,
            "manifest_digest": direct.snapshot.ref.manifest_digest,
            "identity_digest": direct.snapshot.manifest["identity_digest"],
        },
        "qlib_view_ref": qlib_report_ref,
        "adjusted_price_view_ref": adjusted_ref,
        "pit_binding": pit_binding,
        "checks": checks,
        "scope": scope,
        "fields": list(fields),
        "direct_keys": len(direct_rows),
        "qlib_keys": len(view_rows),
        "relative_tolerance": relative_tolerance,
        "mismatches": mismatches,
    }


def _report_mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ArtifactError(f"PR3 report {name} must be an object")
    return value


def _report_ref(
    value: object, fields: Sequence[str], name: str
) -> dict[str, Any]:
    mapping = _report_mapping(value, name)
    missing = [field for field in fields if field not in mapping]
    if missing:
        raise ArtifactError(f"PR3 report {name} is missing immutable refs")
    return {field: mapping[field] for field in fields}


def _validate_pass_comparison(report: Mapping[str, Any], name: str) -> None:
    if report.get("status") != "PASS":
        return
    checks = _report_mapping(report.get("checks"), f"{name} checks")
    if (
        not checks
        or any(value is not True for value in checks.values())
        or report.get("mismatches") != []
    ):
        raise ArtifactError(f"PR3 {name} PASS is inconsistent with its results")


def validate_pr3_report_refs(
    run_manifest: Mapping[str, Any],
    direct_qlib_report: Mapping[str, Any],
    offline_rebuild_report: Mapping[str, Any],
) -> None:
    """Cross-check the immutable refs in the three fixed PR3 evidence reports."""

    run = _report_mapping(run_manifest, "run manifest")
    direct = _report_mapping(direct_qlib_report, "direct/Qlib report")
    offline = _report_mapping(offline_rebuild_report, "offline rebuild report")
    _validate_pass_comparison(direct, "direct/Qlib report")
    run_snapshot_value = _report_mapping(run.get("snapshot"), "run snapshot ref")
    run_view_value = _report_mapping(run.get("qlib_view"), "run QlibView ref")
    run_snapshot = _report_ref(
        run_snapshot_value,
        ("snapshot_id", "manifest_digest", "identity_digest"),
        "run snapshot ref",
    )
    run_view = _report_ref(
        run_view_value,
        ("view_id", "manifest_digest", "identity_digest"),
        "run QlibView ref",
    )
    direct_snapshot = _report_ref(
        direct.get("source_snapshot_ref"),
        tuple(run_snapshot),
        "direct source Snapshot ref",
    )
    direct_view = _report_ref(
        direct.get("qlib_view_ref"), tuple(run_view), "direct QlibView ref"
    )
    if direct_snapshot != run_snapshot or direct_view != run_view:
        raise ArtifactError("PR3 direct/Qlib report refs do not match the run manifest")
    if direct.get("scope") != run_view_value.get("scope"):
        raise ArtifactError("PR3 direct/Qlib report scope does not match the run manifest")
    if direct.get("fields") != run_view_value.get("fields"):
        raise ArtifactError("PR3 direct/Qlib report fields do not match the run manifest")

    run_commits_value = _report_mapping(run.get("domain_commits"), "run DomainCommit refs")
    run_commits = {
        domain: _report_ref(
            run_commits_value.get(domain),
            (
                "domain_commit_id",
                "manifest_digest",
                "identity_digest",
                "logical_content_digest",
                "contract_digest",
            ),
            f"run {domain} DomainCommit ref",
        )
        for domain in ("trading_calendar", "security_master", "market_daily")
    }
    raw_fields = (
        "role",
        "raw_batch_id",
        "manifest_digest",
        "payload_digest",
    )
    run_raw_value = run.get("raw_batches")
    if not isinstance(run_raw_value, list):
        raise ArtifactError("PR3 run RawBatch refs must be a list")
    run_raw = [
        _report_ref(value, raw_fields, "run RawBatch ref") for value in run_raw_value
    ]
    run_raw_summary = {
        "ordered_raw_batch_ids": [ref["raw_batch_id"] for ref in run_raw],
        "ordered_refs_digest": _digest(_json_bytes(run_raw)),
    }
    expected_root_map = {
        "domain_commits": {
            domain: ref["domain_commit_id"] for domain, ref in run_commits.items()
        },
        "snapshot": run_snapshot["snapshot_id"],
        "qlib_view": run_view["view_id"],
    }
    if offline.get("source_root_artifact_map") != expected_root_map:
        raise ArtifactError("PR3 offline source root artifact map is inconsistent")

    source_refs = _report_mapping(
        offline.get("source_artifact_refs"), "offline source artifact refs"
    )
    rebuilt_refs = _report_mapping(
        offline.get("rebuilt_artifact_refs"), "offline rebuilt artifact refs"
    )

    def checked_artifact_refs(
        refs: Mapping[str, Any], name: str
    ) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, Any], dict[str, Any]]:
        snapshot_ref = _report_ref(
            refs.get("snapshot"), tuple(run_snapshot), f"{name} Snapshot ref"
        )
        view_ref = _report_ref(
            refs.get("qlib_view"), tuple(run_view), f"{name} QlibView ref"
        )
        commits_value = _report_mapping(
            refs.get("domain_commits"), f"{name} DomainCommit refs"
        )
        commits = {
            domain: _report_ref(
                commits_value.get(domain), tuple(run_commits[domain]), f"{name} {domain} ref"
            )
            for domain in run_commits
        }
        raw = _report_ref(
            refs.get("raw_batches"),
            ("ordered_raw_batch_ids", "ordered_refs_digest"),
            f"{name} RawBatch refs",
        )
        raw_ids = raw["ordered_raw_batch_ids"]
        if not isinstance(raw_ids, list) or any(
            not isinstance(raw_id, str) for raw_id in raw_ids
        ):
            raise ArtifactError(f"PR3 report {name} RawBatch IDs must be a list")
        return snapshot_ref, commits, raw, view_ref

    source_snapshot, source_commits, source_raw, source_view = checked_artifact_refs(
        source_refs, "offline source"
    )
    rebuilt_snapshot, rebuilt_commits, rebuilt_raw, rebuilt_view = checked_artifact_refs(
        rebuilt_refs, "offline rebuilt"
    )
    if (
        source_snapshot != run_snapshot
        or source_commits != run_commits
        or source_raw != run_raw_summary
        or source_view != run_view
    ):
        raise ArtifactError("PR3 offline source refs do not match the run manifest")

    rebuilt_map = offline.get("rebuilt_root_artifact_map")
    if not isinstance(rebuilt_map, Mapping) or rebuilt_map != {
        "domain_commits": {
            domain: ref["domain_commit_id"]
            for domain, ref in rebuilt_commits.items()
        },
        "snapshot": rebuilt_snapshot["snapshot_id"],
        "qlib_view": rebuilt_view["view_id"],
    }:
        raise ArtifactError("PR3 offline rebuilt root artifact map is inconsistent")

    offline_direct = _report_mapping(
        offline.get("direct_qlib_equivalence"), "offline direct/Qlib report"
    )
    _validate_pass_comparison(offline_direct, "offline direct/Qlib report")
    if (
        _report_ref(
            offline_direct.get("source_snapshot_ref"),
            tuple(run_snapshot),
            "offline direct Snapshot ref",
        )
        != rebuilt_snapshot
        or _report_ref(
            offline_direct.get("qlib_view_ref"),
            tuple(run_view),
            "offline direct QlibView ref",
        )
        != rebuilt_view
    ):
        raise ArtifactError("PR3 offline direct/Qlib refs do not match rebuilt refs")

    expected_identity = {
        "root_artifact_maps": offline.get("source_root_artifact_map")
        == offline.get("rebuilt_root_artifact_map"),
        "snapshot": source_snapshot["snapshot_id"]
        == rebuilt_snapshot["snapshot_id"]
        and source_snapshot["identity_digest"]
        == rebuilt_snapshot["identity_digest"],
        "domain_commits": all(
            source_commits[domain]["domain_commit_id"]
            == rebuilt_commits[domain]["domain_commit_id"]
            and source_commits[domain]["identity_digest"]
            == rebuilt_commits[domain]["identity_digest"]
            for domain in run_commits
        ),
        "raw_batches": source_raw == rebuilt_raw,
        "qlib_view": source_view["view_id"] == rebuilt_view["view_id"]
        and source_view["identity_digest"] == rebuilt_view["identity_digest"],
    }
    if offline.get("identity_equality") != expected_identity:
        raise ArtifactError("PR3 offline identity equality summary is inconsistent")
    logical = _report_mapping(offline.get("logical_equality"), "offline logical equality")
    catalog = _report_mapping(offline.get("catalog_rebuild"), "offline catalog rebuild")
    if offline.get("status") == "PASS" and (
        not all(expected_identity.values())
        or logical.get("canonical_rows") is not True
        or catalog.get("status") != "PASS"
        or offline_direct.get("status") != "PASS"
    ):
        raise ArtifactError("PR3 offline PASS is not supported by its artifact refs")


__all__ = [
    "MARKET_VIEW_FIELDS",
    "QlibView",
    "QlibViewReader",
    "QlibViewRef",
    "SnapshotReader",
    "build_qlib_view",
    "compare_direct_and_qlib",
    "load_qlib_view",
]
