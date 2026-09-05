"""Fixed-window value reconciliation for current source, Axiom, and frozen Qsys."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

from axiom_data.artifacts import ArtifactError
from axiom_data.consumption import MARKET_VIEW_FIELDS, SnapshotReader


RECONCILIATION_CATEGORIES = {
    "all_equal": "一致",
    "axiom_tushare_equal_qsys_different": "Axiom 与 Tushare 一致、Qsys 不同",
    "axiom_qsys_equal_tushare_different": "Axiom 与 Qsys 一致、Tushare 不同",
    "all_different": "三方不同",
    "qsys_missing": "Qsys 缺失",
    "source_current_missing": "source 当前缺失",
}
_MISSING = object()


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
                        "legacy_qsys_missing_or_different"
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
        "qsys_role": "frozen-forensic-reference-only",
        "relative_tolerance": relative_tolerance,
        "counts": counts,
        "contract_or_build_bug_count": build_bug_count,
        "differences": differences,
    }


__all__ = [
    "RECONCILIATION_CATEGORIES",
    "load_frozen_qsys_market",
    "reconcile_market",
]
