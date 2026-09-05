"""Fixed-window value reconciliation for current source, Axiom, and frozen Qsys."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from axiom_data.artifacts import ArtifactError, load_raw_batch
from axiom_data.consumption import MARKET_VIEW_FIELDS, SnapshotReader
from axiom_data.tushare import (
    load_tushare_source_profile,
    tushare_source_profile_digest,
)


RECONCILIATION_CATEGORIES = {
    "all_equal": "一致",
    "axiom_tushare_equal_qsys_different": "Axiom 与 Tushare 一致、Qsys 不同",
    "axiom_qsys_equal_tushare_different": "Axiom 与 Qsys 一致、Tushare 不同",
    "all_different": "三方不同",
    "qsys_missing": "Qsys 缺失",
    "source_current_missing": "source 当前缺失",
}
_MISSING = object()
_INDEPENDENT_PROFILE_RULES = {
    "daily": {
        "primary_key": ["ts_code", "trade_date"],
        "represented_session_field": "trade_date",
        "units": {
            "vol": "hundred-share lots",
            "amount": "thousand CNY",
        },
        "canonical_mapping": {
            "volume_shares": "vol multiplied by 100",
            "amount_cny": "amount multiplied by 1000",
            "OHLC_pre_close": "copied as unadjusted CNY/share",
        },
    },
    "daily_basic": {
        "primary_key": ["ts_code", "trade_date"],
        "units": {
            "turnover_rate": "percent of circulating shares",
            "total_mv": "ten-thousand CNY",
            "circ_mv": "ten-thousand CNY",
        },
        "canonical_mapping": {
            "turnover_rate": "copied as percent",
            "total_market_cap_cny": "total_mv multiplied by 10000",
            "circulating_market_cap_cny": "circ_mv multiplied by 10000",
        },
    },
    "adj_factor": {
        "primary_key": ["ts_code", "trade_date"],
        "canonical_mapping": {
            "adj_factor": "copied; ratios are meaningful only within the same frozen series/anchor"
        },
    },
    "stk_limit": {"primary_key": ["ts_code", "trade_date"]},
}


def _check_independent_profile(profile: Mapping[str, Any]) -> None:
    endpoints = profile.get("endpoints")
    if not isinstance(endpoints, Mapping):
        raise ArtifactError("independent source checker requires endpoint profiles")
    for endpoint, expected_sections in _INDEPENDENT_PROFILE_RULES.items():
        definition = endpoints.get(endpoint)
        if not isinstance(definition, Mapping):
            raise ArtifactError(f"independent source checker has no {endpoint} profile")
        for section, expected in expected_sections.items():
            actual = definition.get(section)
            if isinstance(expected, dict):
                if not isinstance(actual, Mapping) or any(
                    actual.get(name) != value for name, value in expected.items()
                ):
                    raise ArtifactError(
                        f"independent source checker does not recognize {endpoint} {section}"
                    )
            elif actual != expected:
                raise ArtifactError(
                    f"independent source checker does not recognize {endpoint} {section}"
                )


def _source_session(value: object) -> str:
    if not isinstance(value, str) or len(value) != 8 or not value.isdigit():
        raise ArtifactError("independent source session must be YYYYMMDD text")
    try:
        return date.fromisoformat(
            f"{value[0:4]}-{value[4:6]}-{value[6:8]}"
        ).isoformat()
    except ValueError as exc:
        raise ArtifactError("independent source session is invalid") from exc


def _source_number(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ArtifactError("independent source numeric field is invalid")
    number = float(value)
    if not math.isfinite(number):
        raise ArtifactError("independent source numeric field is not finite")
    return number


def _source_scaled(value: object, multiplier: int) -> float | None:
    number = _source_number(value)
    return None if number is None else float(Decimal(str(number)) * multiplier)


def independent_tushare_market_expectations(
    data_root: str | Path,
    raw_batch_ids: Sequence[str],
    *,
    symbols: Sequence[str],
    start_session: str,
    end_session: str,
) -> tuple[dict[str, Any], ...]:
    """Map a small raw market slice without calling production builder code."""

    profile = load_tushare_source_profile()
    _check_independent_profile(profile)
    profile_digest = tushare_source_profile_digest(profile)
    endpoints = profile["endpoints"]
    tables: dict[str, dict[tuple[str, str], Mapping[str, Any]]] = {
        endpoint: {} for endpoint in _INDEPENDENT_PROFILE_RULES
    }
    seen_endpoints: set[str] = set()
    for raw_batch_id in raw_batch_ids:
        raw = load_raw_batch(data_root, raw_batch_id)
        if raw.manifest.get("schema_version") != "raw_batch.v2":
            raise ArtifactError(
                "independent source checker requires profile-bound raw_batch.v2 input"
            )
        request = raw.manifest.get("request")
        endpoint = request.get("endpoint") if isinstance(request, dict) else None
        if endpoint not in tables:
            continue
        seen_endpoints.add(endpoint)
        definition = endpoints[endpoint]
        if (
            raw.manifest.get("domain") != "market_daily"
            or raw.manifest.get("source_profile_ref")
            != definition["source_profile_ref"]
            or raw.manifest.get("source_profile_version")
            != profile["profile_version"]
            or raw.manifest.get("source_profile_digest") != profile_digest
        ):
            raise ArtifactError("independent source checker profile mismatch")
        try:
            rows = json.loads(raw.payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ArtifactError("independent source checker requires JSON rows") from exc
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ArtifactError("independent source checker requires row objects")
        for row in rows:
            symbol = row.get("ts_code")
            source_date = row.get("trade_date")
            if not isinstance(symbol, str) or not isinstance(source_date, str):
                raise ArtifactError("independent source join key must be text")
            key = (symbol, source_date)
            if key in tables[endpoint] and tables[endpoint][key] != row:
                raise ArtifactError(f"independent source rows conflict at {key!r}")
            tables[endpoint][key] = row
    if seen_endpoints != set(tables):
        raise ArtifactError("independent source checker is missing a required endpoint")

    selected = set(symbols)
    expected: list[dict[str, Any]] = []
    for (symbol, source_date), daily in tables["daily"].items():
        session = _source_session(source_date)
        if symbol not in selected or not start_session <= session <= end_session:
            continue
        basic = tables["daily_basic"].get((symbol, source_date), {})
        factor = tables["adj_factor"].get((symbol, source_date), {})
        limit = tables["stk_limit"].get((symbol, source_date), {})
        volume = _source_number(daily.get("vol"))
        scaled_volume = None if volume is None else Decimal(str(volume)) * 100
        if scaled_volume is None or scaled_volume != scaled_volume.to_integral_value():
            raise ArtifactError("independent source volume does not convert to whole shares")
        expected.append(
            {
                "session": session,
                "symbol": symbol,
                "open": _source_number(daily.get("open")),
                "high": _source_number(daily.get("high")),
                "low": _source_number(daily.get("low")),
                "close": _source_number(daily.get("close")),
                "pre_close": _source_number(daily.get("pre_close")),
                "volume_shares": int(scaled_volume),
                "amount_cny": _source_scaled(daily.get("amount"), 1000),
                "adj_factor": _source_number(factor.get("adj_factor")),
                "up_limit": _source_number(limit.get("up_limit")),
                "down_limit": _source_number(limit.get("down_limit")),
                "is_suspended": False,
                "turnover_rate": _source_number(basic.get("turnover_rate")),
                "total_market_cap_cny": _source_scaled(basic.get("total_mv"), 10000),
                "circulating_market_cap_cny": _source_scaled(
                    basic.get("circ_mv"), 10000
                ),
            }
        )
    return tuple(sorted(expected, key=lambda row: (row["session"], row["symbol"])))


def load_frozen_qsys_market(
    parquet_path: str | Path,
    *,
    symbols: Sequence[str],
    start_session: str,
    end_session: str,
) -> tuple[dict[str, Any], ...]:
    """Read and unit-normalize the frozen forensic Qsys panel without mutating it."""

    try:
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover - optional forensic dependency
        raise RuntimeError("Qsys parquet reconciliation requires pyarrow") from exc
    columns = [
        "ts_code",
        "trade_date",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "turnover_rate",
        "total_mv",
        "circ_mv",
        "up_limit",
        "down_limit",
        "paused",
    ]
    table = pq.read_table(
        parquet_path,
        columns=columns,
        filters=[
            ("ts_code", "in", list(symbols)),
            ("trade_date", ">=", start_session),
            ("trade_date", "<=", end_session),
        ],
    )
    rows: list[dict[str, Any]] = []
    for source in table.to_pylist():
        rows.append(
            {
                "session": source["trade_date"],
                "symbol": source["ts_code"],
                "open": source["open"],
                "high": source["high"],
                "low": source["low"],
                "close": source["close"],
                "volume_shares": _scaled(source["volume"], 100),
                "amount_cny": source["amount"],
                "up_limit": source["up_limit"],
                "down_limit": source["down_limit"],
                "is_suspended": (
                    None if source["paused"] is None else bool(source["paused"])
                ),
                "turnover_rate": source["turnover_rate"],
                "total_market_cap_cny": _scaled(source["total_mv"], 10000),
                "circulating_market_cap_cny": _scaled(source["circ_mv"], 10000),
            }
        )
    return tuple(sorted(rows, key=lambda row: (row["session"], row["symbol"])))


def _scaled(value: object, multiplier: int) -> float | None:
    if value is None:
        return None
    return float(Decimal(str(value)) * multiplier)


def _equal(left: object, right: object, tolerance: float) -> bool:
    if left is _MISSING or right is _MISSING:
        return left is right
    if left is None or right is None:
        return left is None and right is None
    if isinstance(left, bool) or isinstance(right, bool):
        return left is right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return math.isclose(float(left), float(right), rel_tol=tolerance, abs_tol=1e-6)
    return left == right


def _shown(value: object) -> object:
    return "<missing>" if value is _MISSING else value


def reconcile_market(
    data_root: str | Path,
    snapshot_id: str,
    *,
    source_rows: Sequence[Mapping[str, Any]],
    qsys_rows: Sequence[Mapping[str, Any]],
    symbols: Sequence[str],
    start_session: str,
    end_session: str,
    fields: Sequence[str] = MARKET_VIEW_FIELDS,
    relative_tolerance: float = 1e-9,
) -> dict[str, Any]:
    """Classify every fixed-window field comparison without choosing Qsys as truth."""

    reader = SnapshotReader(data_root, snapshot_id)
    axiom = {
        (row["session"], row["symbol"]): row
        for row in reader.market_daily(symbols, start_session, end_session)
    }
    selected = set(symbols)

    def index(values: Sequence[Mapping[str, Any]], name: str):
        result: dict[tuple[str, str], Mapping[str, Any]] = {}
        for row in values:
            try:
                key = (row["session"], row["symbol"])
            except KeyError as exc:
                raise ArtifactError(f"{name} reconciliation row has no key") from exc
            if not all(isinstance(value, str) for value in key):
                raise ArtifactError(f"{name} reconciliation key must be text")
            if key[1] not in selected or not start_session <= key[0] <= end_session:
                continue
            if key in result and result[key] != row:
                raise ArtifactError(f"{name} reconciliation rows conflict at {key!r}")
            result[key] = row
        return result

    source = index(source_rows, "Tushare")
    qsys = index(qsys_rows, "Qsys")
    keys = sorted(set(axiom) | set(source) | set(qsys))
    counts = {category: 0 for category in RECONCILIATION_CATEGORIES}
    differences: list[dict[str, Any]] = []
    build_bug_count = 0

    for key in keys:
        for field in fields:
            axiom_value = axiom.get(key, {}).get(field, _MISSING)
            source_value = source.get(key, {}).get(field, _MISSING)
            qsys_value = qsys.get(key, {}).get(field, _MISSING)
            axiom_source_equal = _equal(axiom_value, source_value, relative_tolerance)
            axiom_qsys_equal = _equal(axiom_value, qsys_value, relative_tolerance)
            if source_value is _MISSING:
                category = "source_current_missing"
            elif qsys_value is _MISSING:
                category = "qsys_missing"
            elif axiom_source_equal and axiom_qsys_equal:
                category = "all_equal"
            elif axiom_source_equal:
                category = "axiom_tushare_equal_qsys_different"
            elif axiom_qsys_equal:
                category = "axiom_qsys_equal_tushare_different"
            else:
                category = "all_different"
            counts[category] += 1
            if category == "all_equal":
                continue
            contract_build_bug = source_value is not _MISSING and not axiom_source_equal
            build_bug_count += int(contract_build_bug)
            differences.append(
                {
                    "session": key[0],
                    "symbol": key[1],
                    "field": field,
                    "category": category,
                    "category_label": RECONCILIATION_CATEGORIES[category],
                    "tushare": _shown(source_value),
                    "axiom": _shown(axiom_value),
                    "qsys": _shown(qsys_value),
                    "preliminary_cause": (
                        "contract_or_build_bug"
                        if contract_build_bug
                        else "legacy_qsys_missing_or_different"
                        if category
                        in {"qsys_missing", "axiom_tushare_equal_qsys_different"}
                        else "supplier_drift_or_revision"
                        if category == "axiom_qsys_equal_tushare_different"
                        else "source_current_missing"
                        if category == "source_current_missing"
                        else "unknown"
                    ),
                    "contract_or_build_bug": contract_build_bug,
                    "supplier_drift_or_revision": category
                    in {"axiom_qsys_equal_tushare_different", "source_current_missing"},
                    "legacy_qsys_issue": category
                    in {"qsys_missing", "axiom_tushare_equal_qsys_different"},
                    "unknown": category == "all_different",
                }
            )

    return {
        "status": "PASS" if build_bug_count == 0 else "FAIL",
        "snapshot_id": reader.snapshot.ref.snapshot_id,
        "scope": {
            "symbols": list(symbols),
            "start_session": start_session,
            "end_session": end_session,
            "fields": list(fields),
        },
        "source_pit_classification": "current-observed-best-effort",
        "source_expectation_path": "independent-frozen-raw-profile-checker.v1",
        "qsys_role": "frozen-forensic-reference-only",
        "relative_tolerance": relative_tolerance,
        "counts": counts,
        "contract_or_build_bug_count": build_bug_count,
        "differences": differences,
    }


__all__ = [
    "RECONCILIATION_CATEGORIES",
    "independent_tushare_market_expectations",
    "load_frozen_qsys_market",
    "reconcile_market",
]
