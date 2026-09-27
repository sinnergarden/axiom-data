"""Independent raw-to-canonical checks for the bounded reference evidence run."""

from __future__ import annotations

import json
import hashlib
import math
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal
from typing import Any

from axiom_data.artifacts import ArtifactError, load_raw_batch
from axiom_data.consumption import SnapshotReader
from axiom_data.reference_source import load_reference_source_profile, reference_source_profile_digest


def _date(value: object) -> str:
    if not isinstance(value, str) or len(value) != 8 or not value.isdigit():
        raise ArtifactError("independent reference source date is invalid")
    try:
        return date(int(value[:4]), int(value[4:6]), int(value[6:])).isoformat()
    except ValueError as exc:
        raise ArtifactError("independent reference source date is invalid") from exc


def _number(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ArtifactError("independent reference numeric value is invalid")
    number = float(value)
    if not math.isfinite(number):
        raise ArtifactError("independent reference numeric value is not finite")
    return number


def _identity(value: object) -> str:
    content = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(content).hexdigest()


def _rows(data_root: str, raw_ids: Sequence[str], domain: str) -> list[dict[str, Any]]:
    result = []
    for raw_id in raw_ids:
        raw = load_raw_batch(data_root, raw_id)
        if (
            raw.manifest.get("schema_version") != "raw_batch.v2"
            or raw.manifest.get("domain") != domain
            or raw.manifest.get("source_profile_digest") != reference_source_profile_digest(load_reference_source_profile(raw.manifest.get("source_profile_version")))
        ):
            raise ArtifactError("independent reference checker source binding mismatch")
        values = json.loads(raw.payload)
        if not isinstance(values, list) or any(not isinstance(row, dict) for row in values):
            raise ArtifactError("independent reference checker requires raw JSON row arrays")
        result.extend(values)
    return result


def reconcile_reference_raw_mapping(
    data_root: str,
    snapshot_id: str,
    raw_ids: Mapping[str, Sequence[str]],
    *,
    symbols: Sequence[str],
    start_session: str,
    end_session: str,
) -> dict[str, Any]:
    """Check critical mappings without calling a production builder helper."""

    reader = SnapshotReader(data_root, snapshot_id)
    selected = set(symbols)
    factors = {
        (row["session"], row["symbol"]): row
        for row in reader.facts("adjustment_factors", symbols=symbols, start_session=start_session, end_session=end_session)
    }
    limits = {
        (row["session"], row["symbol"]): row
        for row in reader.facts("price_limits", symbols=symbols, start_session=start_session, end_session=end_session)
    }
    capital = {
        (row["session"], row["symbol"]): row
        for row in reader.facts("security_capital", symbols=symbols, start_session=start_session, end_session=end_session)
    }
    status = {
        (row["session"], row["symbol"]): row
        for row in reader.facts("security_status", symbols=symbols, start_session=start_session, end_session=end_session)
    }
    benchmarks = {
        (row["session"], row["benchmark"]): row
        for row in reader.facts(
            "benchmark_daily", start_session=start_session, end_session=end_session
        )
    }
    actions = {
        (row["symbol"], row["action_id"], row["action_version"]): row
        for row in reader.facts(
            "corporate_actions",
            symbols=symbols,
            start_session=start_session,
            end_session=end_session,
        )
    }
    mismatches: list[dict[str, Any]] = []

    for source in _rows(data_root, raw_ids["adjustment_factors"], "adjustment_factors"):
        key = (_date(source.get("trade_date")), source.get("ts_code"))
        if key[1] in selected and start_session <= key[0] <= end_session:
            actual = factors.get(key, {}).get("factor")
            expected = _number(source.get("adj_factor"))
            if actual != expected:
                mismatches.append({"domain": "adjustment_factors", "key": list(key), "field": "factor", "expected": expected, "actual": actual})

    for source in _rows(data_root, raw_ids["price_limits"], "price_limits"):
        key = (_date(source.get("trade_date")), source.get("ts_code"))
        if key[1] in selected and start_session <= key[0] <= end_session:
            expected = (_number(source.get("up_limit")), _number(source.get("down_limit")))
            actual = (limits.get(key, {}).get("upper_limit"), limits.get(key, {}).get("lower_limit"))
            if actual != expected:
                mismatches.append({"domain": "price_limits", "key": list(key), "field": "bounds", "expected": list(expected), "actual": list(actual)})

    for source in _rows(data_root, raw_ids["security_capital"], "security_capital"):
        key = (_date(source.get("trade_date")), source.get("ts_code"))
        if key[1] in selected and start_session <= key[0] <= end_session:
            expected = (
                float(Decimal(str(_number(source.get("total_share")))) * 10000),
                float(Decimal(str(_number(source.get("float_share")))) * 10000),
            )
            actual = (capital.get(key, {}).get("total_shares"), capital.get(key, {}).get("circulating_shares"))
            if actual != expected:
                mismatches.append({"domain": "security_capital", "key": list(key), "field": "shares", "expected": list(expected), "actual": list(actual)})

    daily_keys = set()
    for source in _rows(data_root, raw_ids["security_status"], "security_status"):
        if "open" not in source:
            continue
        key = (_date(source.get("trade_date")), source.get("ts_code"))
        if key[1] in selected and start_session <= key[0] <= end_session:
            daily_keys.add(key)
            if status.get(key, {}).get("status") != "normal_active":
                mismatches.append({"domain": "security_status", "key": list(key), "field": "status", "expected": "normal_active", "actual": status.get(key, {}).get("status")})

    suspend_keys = set()
    for source in _rows(data_root, raw_ids["security_status"], "security_status"):
        if "suspend_type" not in source or source.get("suspend_type") != "S":
            continue
        if source.get("suspend_timing") not in (None, ""):
            raise ArtifactError("independent reference checker rejects intraday suspension")
        key = (_date(source.get("trade_date")), source.get("ts_code"))
        if key[1] in selected and start_session <= key[0] <= end_session:
            suspend_keys.add(key)
            if status.get(key, {}).get("status") != "suspended":
                mismatches.append({"domain": "security_status", "key": list(key), "field": "status", "expected": "suspended", "actual": status.get(key, {}).get("status")})
    if daily_keys & suspend_keys:
        mismatches.append({"domain": "security_status", "key": None, "field": "source_conflict", "expected": "disjoint", "actual": "overlap"})

    for source in _rows(data_root, raw_ids["benchmark_daily"], "benchmark_daily"):
        key = (_date(source.get("trade_date")), source.get("ts_code"))
        if start_session <= key[0] <= end_session:
            expected = _number(source.get("close"))
            actual = benchmarks.get(key, {}).get("close")
            if actual != expected:
                mismatches.append({"domain": "benchmark_daily", "key": list(key), "field": "close", "expected": expected, "actual": actual})

    for source in _rows(data_root, raw_ids["corporate_actions"], "corporate_actions"):
        symbol = source.get("ts_code")
        if symbol not in selected or source.get("div_proc") != "实施":
            continue
        effective = _date(source.get("ex_date"))
        if not start_session <= effective <= end_session:
            continue
        terms = {
            "cash_dividend": ("cash_per_share", _number(source.get("cash_div_tax"))),
            "stock_dividend": ("stock_ratio", _number(source.get("stk_bo_rate"))),
            "capital_transfer": ("transfer_ratio", _number(source.get("stk_co_rate"))),
        }
        natural = [symbol, source.get("end_date"), source.get("ann_date"), source.get("div_proc")]
        for action_type, (term, value) in terms.items():
            if value in (None, 0, 0.0):
                continue
            key = (
                symbol,
                f"ca-{_identity([natural, action_type])}",
                f"obs-{_identity(source)}",
            )
            actual_row = actions.get(key)
            expected = {
                "action_type": action_type,
                "announcement_date": _date(source["ann_date"]) if source.get("ann_date") else None,
                "record_date": _date(source["record_date"]) if source.get("record_date") else None,
                "effective_date": effective,
                "ex_date": effective,
                "payment_date": _date(source["pay_date"]) if source.get("pay_date") else None,
                "share_available_date": _date(source["div_listdate"]) if source.get("div_listdate") else None,
                term: value,
            }
            actual = None if actual_row is None else {
                name: actual_row.get(name) for name in expected
            }
            if actual != expected:
                mismatches.append({"domain": "corporate_actions", "key": list(key), "field": "mapping", "expected": expected, "actual": actual})

    checks = {
        "factor_passthrough": not any(row["domain"] == "adjustment_factors" for row in mismatches),
        "price_limit_passthrough": not any(row["domain"] == "price_limits" for row in mismatches),
        "capital_x10000": not any(row["domain"] == "security_capital" for row in mismatches),
        "status_evidence": not any(row["domain"] == "security_status" for row in mismatches),
        "benchmark_passthrough": not any(row["domain"] == "benchmark_daily" for row in mismatches),
        "corporate_action_mapping": not any(row["domain"] == "corporate_actions" for row in mismatches),
    }
    raw_refs = []
    for domain, identities in raw_ids.items():
        for raw_id in identities:
            raw = load_raw_batch(data_root, raw_id)
            raw_refs.append({
                "domain": domain,
                "raw_batch_id": raw.ref.raw_batch_id,
                "manifest_digest": raw.ref.manifest_digest,
                "payload_digest": raw.manifest["payload_files"][0]["content_digest"],
                "schema_version": raw.manifest["schema_version"],
                "source_profile_ref": raw.manifest["source_profile_ref"],
                "source_profile_version": raw.manifest["source_profile_version"],
                "source_profile_digest": raw.manifest["source_profile_digest"],
            })
    return {
        "status": "PASS" if all(checks.values()) and not mismatches else "FAIL",
        "snapshot_id": snapshot_id,
        "source_profile_digest": reference_source_profile_digest(load_reference_source_profile(raw.manifest.get("source_profile_version"))),
        "raw_batch_refs": raw_refs,
        "scope": {"symbols": list(symbols), "start_session": start_session, "end_session": end_session},
        "checks": checks,
        "mismatch_count": len(mismatches),
        "mismatches": mismatches,
    }


__all__ = ["reconcile_reference_raw_mapping"]


# Compatibility exports for historical callers.
