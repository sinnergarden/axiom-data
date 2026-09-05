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
            for domain in ("trading_calendar", "security_master", "market_daily")
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
        "schema_version": "qlib_view.v1",
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

    layout = _layout(data_root)
    view_id = _identity("view_id", view_id)
    target = layout.qlib_exports / view_id
    manifest, manifest_digest = _load_manifest(
        layout.root,
        target,
        artifact_type="qlib_view",
        schema_version="qlib_view.v1",
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
        self.path = _layout(self.data_root).qlib_exports / view_id

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
                    f"{item['storage_path']}/{field}.day.bin",
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
    return {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "scope": scope,
        "fields": list(fields),
        "direct_keys": len(direct_rows),
        "qlib_keys": len(view_rows),
        "relative_tolerance": relative_tolerance,
        "mismatches": mismatches,
    }


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
