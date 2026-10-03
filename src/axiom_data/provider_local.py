"""Declared Tushare-to-local adapters. No network call or credential lives here.

The vendor's terminal history is an observation made *now*: none of these
profiles manufacture a revision-bound source publication timestamp. A different
content revision is ordered only by ``terminal_observation_v1`` at read time.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from typing import Any

from .protocols import DataError, IngestBatch
from .sources import _response_table, _rows


_META = {
    "revision_id": {"dtype": "string", "nullable": False},
    "revision_sequence": {"dtype": "int64", "nullable": True},
    "first_observed_at": {"dtype": "timestamp", "nullable": False},
    "raw_batch_id": {"dtype": "string", "nullable": False},
    "source_available_at": {"dtype": "timestamp", "nullable": True},
    "evidence_ref": {"dtype": "string", "nullable": True},
}


def _contract(domain: str, key: tuple[str, ...], fields: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {"contract_id": f"local.{domain}.terminal_observation.v1", "logical_key": list(key),
            "fields": {**fields, **_META}}


def _f(dtype: str, nullable: bool = True, unit: str | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {"dtype": dtype, "nullable": nullable}
    if unit is not None:
        item["unit"] = unit
    return item


CONTRACTS = {
    "daily": _contract("market_daily", ("security_id", "session"), {
        "security_id": _f("string", False), "session": _f("date", False),
        **{name: _f("float64", unit="CNY/share") for name in
           ("open", "high", "low", "close", "pre_close")},
        "volume_shares": _f("int64", unit="shares"),
        "amount_cny": _f("float64", unit="CNY"),
    }),
    "adj_factor": _contract("adjustment_factors", ("security_id", "session"), {
        "security_id": _f("string", False), "session": _f("date", False),
        "factor": _f("float64", False, "dimensionless"),
    }),
    "index_daily": _contract("benchmark_daily", ("security_id", "session"), {
        "security_id": _f("string", False), "session": _f("date", False),
        "close": _f("float64", False, "index points"),
    }),
    "trade_cal": _contract("trading_calendar", ("exchange", "session"), {
        "exchange": _f("string", False), "session": _f("date", False),
        "is_open": _f("bool", False),
    }),
    "stock_basic": _contract("security_master", ("security_id", "listing_date"), {
        "security_id": _f("string", False), "listing_date": _f("date", False),
        "delisting_date": _f("date"), "vendor_delist_date": _f("date"),
        "exchange": _f("string", False), "list_status": _f("string", False),
    }),
    "suspend_d": _contract("security_status", ("security_id", "session"), {
        "security_id": _f("string", False), "session": _f("date", False),
        "is_suspended": _f("bool"), "suspend_timing": _f("string"),
        "status_reason": _f("string", False),
    }),
    "index_weight": _contract("universe_membership", ("universe_id", "security_id", "effective_from"), {
        "universe_id": _f("string", False), "security_id": _f("string", False),
        "effective_from": _f("date", False), "effective_to": _f("date", False),
        "weight_percent": _f("float64", unit="percent"),
    }),
}


FIELDS = {
    "daily": ("ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "vol", "amount"),
    "adj_factor": ("ts_code", "trade_date", "adj_factor"),
    "index_daily": ("ts_code", "trade_date", "close"),
    "trade_cal": ("exchange", "cal_date", "is_open"),
    "stock_basic": ("ts_code", "exchange", "list_status", "list_date", "delist_date"),
    "suspend_d": ("ts_code", "trade_date", "suspend_timing", "suspend_type"),
    "index_weight": ("index_code", "con_code", "trade_date", "weight"),
}


# A row at the cap might be silently truncated. Below the cap is still only a
# bounded response observation, not independent proof of vendor completeness.
CAPS = {"daily": 6000, "adj_factor": 6000, "index_daily": 6000,
        "trade_cal": 6000, "stock_basic": 6000, "suspend_d": 5000,
        "index_weight": 6000}
DOMAINS = {endpoint: contract["contract_id"].split(".")[1] for endpoint, contract in CONTRACTS.items()}


def profile_for(endpoint: str, *, identity_map: Mapping[str, str]) -> dict[str, Any]:
    """Build frozen source assumptions for an explicitly named endpoint.

    ``identity_map`` contains only caller-confirmed stable security IDs; source
    tickers are never silently used as identity. Index IDs remain qualified
    vendor index codes. Stock delist_date is retained here; the vendor_listing
    adapter supplies the accepted supplier-date exclusive economic boundary.
    """
    if endpoint not in CONTRACTS:
        raise DataError(f"unsupported local Tushare endpoint: {endpoint}")
    profile: dict[str, Any] = {
        "id": f"tushare.local.{endpoint}.v1", "endpoint": endpoint,
        "raw_serialization": "json_source_records_or_sdk_table_v1",
        "revision_order": "terminal_observation_v1",
        "revision_capability": "terminal history; source revision sequence/publication time unknown",
        "availability": {
            "timezone": "Asia/Shanghai", "session_release_time": "20:00:00",
            "basis": "best_effort exploration assumption; not revision-bound public evidence",
        },
        "missing_row": "unknown; never infer an absence from an empty result",
        "response_limit": CAPS[endpoint],
        "response_limit_rule": "at cap is ambiguous and blocks publication; split plan",
        "coverage_claim": "observed response only; completeness unverified",
        "field_map": {}, "date_formats": {},
    }
    fm = profile["field_map"]
    dates = profile["date_formats"]
    if endpoint in ("daily", "adj_factor", "suspend_d", "index_weight", "stock_basic"):
        profile["identity_map"] = dict(identity_map)
    if endpoint == "daily":
        fm.update(security_id="ts_code", session="trade_date",
                  open="open", high="high", low="low", close="close",
                  pre_close="pre_close", volume_shares="vol", amount_cny="amount")
        dates["session"] = "YYYYMMDD"
        profile["source_units"] = {**{name: "CNY/share" for name in
                                      ("open", "high", "low", "close", "pre_close")},
                                   "vol": "hundred-share lots", "amount": "thousand CNY"}
        profile["unit_conversions"] = {
            "volume_shares": {"from": "hundred-share lots", "to": "shares", "factor": "100"},
            "amount_cny": {"from": "thousand CNY", "to": "CNY", "factor": "1000"}}
    elif endpoint == "adj_factor":
        fm.update(security_id="ts_code", session="trade_date", factor="adj_factor")
        dates["session"] = "YYYYMMDD"
        profile["source_units"] = {"adj_factor": "dimensionless"}
    elif endpoint == "index_daily":
        fm.update(security_id="ts_code", session="trade_date", close="close")
        dates["session"] = "YYYYMMDD"
        profile["source_units"] = {"close": "index points"}
        profile["identity_policy"] = "qualified_index_code"
    elif endpoint == "trade_cal":
        fm.update(exchange="exchange", session="cal_date", is_open="is_open")
        dates["session"] = "YYYYMMDD"
        profile["value_maps"] = {"is_open": {"0": False, "1": True}}
    elif endpoint == "stock_basic":
        fm.update(security_id="ts_code", listing_date="list_date",
                  vendor_delist_date="delist_date", exchange="exchange",
                  list_status="list_status")
        dates.update(listing_date="YYYYMMDD", vendor_delist_date="YYYYMMDD")
        profile["null_values"] = {"vendor_delist_date": [""]}
        profile["delist_boundary"] = "vendor field retained; exclusive canonical boundary unknown"
    elif endpoint == "suspend_d":
        fm.update(security_id="ts_code", session="trade_date",
                  is_suspended="__is_suspended", suspend_timing="suspend_timing",
                  status_reason="__status_reason")
        dates["session"] = "YYYYMMDD"
        profile["normalizer"] = "tushare_suspend_d_v1"
    else:
        fm.update(universe_id="index_code", security_id="con_code",
                  effective_from="trade_date", effective_to="__effective_to",
                  weight_percent="weight")
        dates.update(effective_from="YYYYMMDD", effective_to="YYYYMMDD")
        profile["source_units"] = {"weight": "percent"}
        profile["normalizer"] = "tushare_index_weight_v1"
        profile["membership_semantics"] = "bounded dated source group; group completeness unverified; validity [date,next calendar day); no inferred interval to next monthly observation"
    return profile


def response_bytes(response: Any, *, endpoint: str) -> tuple[bytes, int | None]:
    """Serialize every returned source column and value, without conversion."""
    if isinstance(response, (list, Mapping)):
        source_document = response
    else:
        fields, rows = _response_table(response)
        source_document = {"fields": fields, "rows": rows}
    try:
        payload = json.dumps(source_document, ensure_ascii=False, separators=(",", ":"),
                             allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise DataError("Tushare response is not strict JSON data") from exc
    try:
        _, rows = _response_table(response)
    except DataError:
        return payload, None
    return payload, len(rows)


def select_source_rows(batch: IngestBatch, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Check a decoded supplier response once and select explicit canonical scope.

    Batch calls may return the whole market. Raw keeps all source rows; only
    ``request.canonical_symbols`` are normalized, and every selected code must
    have a stable binding. Request bounds still apply to every returned row.
    Omitting canonical_symbols retains the original exact-scope behavior.
    """
    endpoint, params = batch.request["endpoint"], batch.request["params"]
    selected = batch.request.get("canonical_symbols")
    if selected is not None:
        identities = batch.source_profile.get("identity_map", {})
        if (not isinstance(selected, (list, tuple)) or
                any(not isinstance(code, str) or code not in identities for code in selected)):
            raise DataError("canonical_symbols must contain explicitly bound source codes")
        selected = set(selected)
    requested_codes = set(params["ts_code"].split(",")) if params.get("ts_code") else None
    result = []
    for row in rows:
        if any(key not in row for key in FIELDS[endpoint]):
            raise DataError(f"{endpoint} response lacks required source fields")
        code = row.get("con_code") if endpoint == "index_weight" else row.get("ts_code")
        if requested_codes is not None and code not in requested_codes:
            raise DataError("response lies outside requested security")
        if endpoint == "index_weight" and row["index_code"] != params["index_code"]:
            raise DataError("response lies outside requested index")
        if endpoint in ("trade_cal", "stock_basic") and params.get("exchange") and row["exchange"] != params["exchange"]:
            raise DataError("response lies outside requested exchange")
        date_field = "cal_date" if endpoint == "trade_cal" else "list_date" if endpoint == "stock_basic" else "trade_date"
        if date_field != "list_date":
            day = row[date_field]
            if (not isinstance(day, str) or
                    (params.get("start_date") and day < params["start_date"]) or
                    (params.get("end_date") and day > params["end_date"]) or
                    (params.get("trade_date") and day != params["trade_date"])):
                raise DataError("response lies outside requested dates")
        if endpoint == "stock_basic":
            if row["exchange"] not in ("SSE", "SZSE"):
                raise DataError("unsupported exchange for stock_basic")
            if row["list_status"] != params["list_status"]:
                raise DataError("stock_basic list_status differs from request")
        if selected is not None and code not in selected:
            if endpoint == "index_weight":
                raise DataError("membership source grew outside the frozen universe; prepare an expanded scope and new plan")
            continue
        if endpoint in ("daily", "adj_factor", "suspend_d", "index_weight", "stock_basic"):
            if code not in batch.source_profile["identity_map"]:
                raise DataError(f"unmapped source security identity {code!r}")
        result.append(row)
    return result


def validate_saved_response(batch: IngestBatch) -> None:
    """Validate retained source bounds; canonical scope never changes Raw bytes."""
    select_source_rows(batch, _rows(batch.payload))


def prepare_source_rows(batch: IngestBatch) -> list[dict[str, Any]]:
    """Decode, validate, filter and add adapter columns without a JSON roundtrip."""
    rows = select_source_rows(batch, _rows(batch.payload))
    if batch.normalizer == "tushare_index_weight_v1":
        _membership_columns(rows)
    elif batch.normalizer == "tushare_suspend_d_v1":
        _suspension_columns(rows)
    return rows


def normalize_membership_payload(payload: bytes) -> bytes:
    """Add the next-day end to an in-memory decoded copy of saved vendor Raw."""
    rows = _rows(payload)
    _membership_columns(rows)
    return json.dumps(rows, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _membership_columns(rows):
    for row in rows:
        value = row.get("trade_date")
        try:
            day = datetime.strptime(value, "%Y%m%d").date()
        except (TypeError, ValueError) as exc:
            raise DataError("index_weight trade_date is invalid") from exc
        row["__effective_to"] = (day + timedelta(days=1)).strftime("%Y%m%d")


def normalize_suspension_payload(payload: bytes) -> bytes:
    """Classify full versus partial day without altering saved supplier Raw."""
    rows = _rows(payload)
    _suspension_columns(rows)
    return json.dumps(rows, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _suspension_columns(rows):
    """Reduce source events to one honest daily summary; original Raw is intact.

    Multiple intraday S/R records are events, not competing terminal revisions.
    They cannot establish all-day execution availability. Timings are retained
    deterministically; a contradictory full-day S/R group remains unknown.
    """
    grouped = {}
    for row in rows:
        kind, timing = row.get("suspend_type"), row.get("suspend_timing")
        if kind not in ("S", "R"):
            raise DataError("unsupported suspend_d type")
        if timing is not None and not isinstance(timing, str):
            raise DataError("invalid intraday suspension timing")
        grouped.setdefault((row.get("ts_code"), row.get("trade_date")), []).append(row)
    output = []
    for _, events in grouped.items():
        row = dict(events[0])
        kinds = {event["suspend_type"] for event in events}
        timings = sorted({event["suspend_timing"] for event in events if event.get("suspend_timing")})
        row["suspend_timing"] = ";".join(timings) if timings else None
        row["suspend_type"] = "+".join(sorted(kinds))
        if timings or len(kinds) > 1:
            row["__is_suspended"] = None
            row["__status_reason"] = ("partial_session_suspension" if timings else "conflicting_daily_status_events")
        else:
            row["__is_suspended"] = kinds == {"S"}
            row["__status_reason"] = "full_day_suspension" if kinds == {"S"} else "explicit_resumption"
        output.append(row)
    rows[:] = output
