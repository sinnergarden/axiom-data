"""Thin Tushare adapters for exchange-traded funds, with fund units explicit.

Only supplier facts are normalized here. Raw retains duplicate rows and every
source column. Identical fund-dividend rows collapse once in canonical storage;
distinct source contents remain distinct revisions. There are no strategy rules.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import json
import re
from typing import Any

from .protocols import DataError, IngestBatch
from .provider_local import _contract, _f
from .sources import _date, _rows


ETF_SYMBOLS = ("518880.SH", "513100.SH", "159915.SZ", "510880.SH",
               "511010.SH", "510300.SH", "510500.SH")
_CODE = re.compile(r"^[0-9]{6}\.(SH|SZ)$")
FIELDS = {
    "fund_basic": ("ts_code", "name", "list_date", "delist_date", "status", "market"),
    "fund_daily": ("ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "vol", "amount"),
    "fund_adj": ("ts_code", "trade_date", "adj_factor"),
    "fund_div": ("ts_code", "ann_date", "imp_anndate", "div_proc", "record_date", "ex_date", "pay_date", "div_cash"),
    "etf_limit": ("ts_code", "trade_date", "up_limit", "down_limit"),
}
CAPS = {"fund_basic": 15000, "fund_daily": 5000, "fund_adj": 2000,
        "fund_div": None, "etf_limit": 3000}
DOMAINS = {"fund_basic": "security_master", "fund_daily": "market_daily",
           "fund_adj": "adjustment_factors", "fund_div": "corporate_actions",
           "etf_limit": "price_limits"}
CONTRACTS = {
    "fund_basic": _contract("security_master", ("security_id", "listing_date"), {
        "security_id": _f("string", False), "source_code": _f("string", False),
        "name": _f("string"), "exchange": _f("string", False),
        "listing_date": _f("date", False), "delisting_date": _f("date"),
        "list_status": _f("string", False), "asset_type": _f("string", False),
    }),
    "fund_daily": _contract("market_daily", ("security_id", "session"), {
        "security_id": _f("string", False), "session": _f("date", False),
        **{field: _f("float64", unit="CNY/fund unit") for field in
           ("open", "high", "low", "close", "pre_close")},
        "volume_units": _f("int64", unit="fund units"),
        "amount_cny": _f("float64", unit="CNY"),
    }),
    "fund_adj": _contract("adjustment_factors", ("security_id", "session"), {
        "security_id": _f("string", False), "session": _f("date", False),
        "factor": _f("float64", False, "dimensionless"),
    }),
    "fund_div": _contract("corporate_actions", ("security_id", "announcement_date", "process_status"), {
        "security_id": _f("string", False), "source_code": _f("string", False),
        "announcement_date": _f("date", False), "process_status": _f("string", False),
        "implementation_announcement_date": _f("date"), "record_date": _f("date"),
        "ex_date": _f("date"), "pay_date": _f("date"),
        "cash_dividend_per_unit": _f("float64", unit="CNY/fund unit"),
    }),
    "etf_limit": _contract("price_limits", ("security_id", "session"), {
        "security_id": _f("string", False), "session": _f("date", False),
        "up_limit": _f("float64", unit="CNY/fund unit"),
        "down_limit": _f("float64", unit="CNY/fund unit"),
    }),
}
for endpoint, contract in CONTRACTS.items():
    contract["contract_id"] = f"local.{DOMAINS[endpoint]}.etf_terminal_observation.v1"


def fund_identity(code: str, listing_date: str) -> str:
    """Stable identity policy: exchange-qualified supplier code plus listing date."""
    match = _CODE.fullmatch(code) if isinstance(code, str) else None
    if not match:
        raise DataError("ETF identity requires an exchange-qualified SH/SZ code")
    day = _date(listing_date, "YYYYMMDD", "list_date")
    exchange = "SSE" if match.group(1) == "SH" else "SZSE"
    return f"cn.etf.{exchange}.{code[:6]}.{day.replace('-', '')}"


def profile_for(endpoint: str, *, identity_map: Mapping[str, str]) -> dict[str, Any]:
    """Freeze native keys, units and declared availability; no source I/O.

    fund_basic assigns identities using its explicit code/list-date policy.
    Other endpoints use the frozen catalogue bindings. Historical supplier
    dates are assumptions only; operational PIT always uses actual receipts.
    """
    if endpoint not in CONTRACTS:
        raise DataError("unsupported ETF source endpoint")
    mappings = {
        "fund_basic": {"security_id": "security_id", "source_code": "ts_code", "name": "name",
                       "exchange": "__exchange", "listing_date": "list_date",
                       "delisting_date": "delist_date", "list_status": "status", "asset_type": "__asset_type"},
        "fund_daily": {"security_id": "ts_code", "session": "trade_date", "open": "open", "high": "high",
                       "low": "low", "close": "close", "pre_close": "pre_close", "volume_units": "vol", "amount_cny": "amount"},
        "fund_adj": {"security_id": "ts_code", "session": "trade_date", "factor": "adj_factor"},
        "fund_div": {"security_id": "ts_code", "source_code": "ts_code", "announcement_date": "ann_date",
                     "process_status": "div_proc", "implementation_announcement_date": "imp_anndate",
                     "record_date": "record_date", "ex_date": "ex_date", "pay_date": "pay_date", "cash_dividend_per_unit": "div_cash"},
        "etf_limit": {"security_id": "ts_code", "session": "trade_date", "up_limit": "up_limit", "down_limit": "down_limit"},
    }
    profile: dict[str, Any] = {
        "id": f"tushare.local.{endpoint}.v1", "endpoint": endpoint,
        "normalizer": "etf_records_v1", "field_map": mappings[endpoint],
        "raw_serialization": "json_source_records_or_sdk_table_v1",
        "revision_order": "terminal_observation_v1", "response_limit": CAPS[endpoint],
        "response_limit_rule": "split capped ranges; never normalize a capped parent",
        "revision_capability": "terminal observation; historical public vintages unavailable",
        "missing_row": "unknown; an absent daily bar is not a suspension",
        "coverage_claim": "retained Tushare response; no external approval gate",
        "availability": {"timezone": "Asia/Shanghai", "session_release_time": "20:00:00",
                         "basis": "best-effort supplier-date assumption; not public evidence"},
        "date_formats": {name: "YYYYMMDD" for name, spec in CONTRACTS[endpoint]["fields"].items()
                         if spec["dtype"] == "date"},
        "null_values": {name: [""] for name, spec in CONTRACTS[endpoint]["fields"].items()
                        if spec["dtype"] == "date" and spec.get("nullable", True)},
    }
    if endpoint != "fund_basic":
        profile["identity_map"] = dict(identity_map)
    else:
        profile["identity_policy"] = "qualified_fund_code_and_listing_date_v1"
        profile["availability"]["session_release_time"] = "00:00:00"
        profile["delist_boundary"] = "supplier delist_date is the exclusive economic boundary"
    if endpoint == "fund_daily":
        profile["source_units"] = {**{name: "CNY/fund unit" for name in ("open", "high", "low", "close", "pre_close")},
                                   "vol": "hundred-fund-unit lots", "amount": "thousand CNY"}
        profile["unit_conversions"] = {"volume_units": {"from": "hundred-fund-unit lots", "to": "fund units", "factor": "100"},
                                       "amount_cny": {"from": "thousand CNY", "to": "CNY", "factor": "1000"}}
    elif endpoint == "fund_adj":
        profile["source_units"] = {"adj_factor": "dimensionless"}
    elif endpoint == "fund_div":
        profile["source_units"] = {"div_cash": "CNY/fund unit"}
        profile["availability"].update(date_field="announcement_date", date_rule="same_day_release")
        profile["duplicate_policy"] = "identical selected source rows collapse; Raw remains unchanged"
    elif endpoint == "etf_limit":
        profile["source_units"] = {"up_limit": "CNY/fund unit", "down_limit": "CNY/fund unit"}
        profile["availability"]["session_release_time"] = "08:45:00"
    return deepcopy(profile)


def prepare_source_rows(batch: IngestBatch) -> list[dict[str, Any]]:
    """Validate selected supplier scope once and add only adapter metadata.

    Whole catalogue responses remain intact in Raw. Rows outside configured
    codes are ignored only for canonical storage. Supplier nulls stay null.
    """
    endpoint = batch.request["endpoint"]
    params = batch.request["params"]
    selected = batch.request.get("canonical_symbols")
    selected = set(selected) if selected is not None else None
    rows = []
    seen = set()
    for row in _rows(batch.payload):
        if any(field not in row for field in FIELDS[endpoint]):
            raise DataError(f"{endpoint} lacks a requested source column")
        code = row["ts_code"]
        if params.get("ts_code") and code != params["ts_code"]:
            raise DataError("ETF response lies outside requested source code")
        if endpoint == "fund_basic" and (row["market"] != params.get("market", "E") or
                                          row["status"] != params.get("status")):
            raise DataError("fund_basic response differs from its market/status selector")
        if endpoint in {"fund_daily", "fund_adj", "etf_limit"}:
            day = row["trade_date"]
            _date(day, "YYYYMMDD", "trade_date")
            if ((params.get("start_date") and day < params["start_date"]) or
                    (params.get("end_date") and day > params["end_date"]) or
                    (params.get("trade_date") and day != params["trade_date"])):
                raise DataError("ETF response lies outside requested dates")
        if selected is not None and code not in selected:
            continue
        if endpoint == "fund_basic":
            row["security_id"] = fund_identity(code, row["list_date"])
            row["__exchange"] = "SSE" if code.endswith(".SH") else "SZSE"
            row["__asset_type"] = "exchange_traded_fund"
        elif code not in batch.source_profile["identity_map"]:
            raise DataError("ETF source code has no frozen stable identity")
        if endpoint == "fund_div":
            signature = json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
            if signature in seen:
                continue
            seen.add(signature)
        rows.append(row)
    return rows
