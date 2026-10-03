"""Tushare stock_basic listing and delisting events from saved source bytes."""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import date, datetime
from hashlib import sha256
from typing import Any, Mapping, Sequence

from .protocols import DataError
from .sources import _rows
from .storage import LocalStore, _json_bytes


SCHEMA = "tushare_stock_basic_listing_source_v1"
CONTRACT = {
    "contract_id": "local.tushare_stock_basic_listing_events.v1",
    "logical_key": ["security_id", "event_type"],
    "fields": {
        "security_id": {"dtype": "string", "nullable": False},
        "source_code": {"dtype": "string", "nullable": False},
        "exchange": {"dtype": "string", "nullable": False},
        "event_type": {"dtype": "string", "nullable": False},
        "event_date": {"dtype": "date", "nullable": False},
        "listing_date": {"dtype": "date", "nullable": False},
        "delisting_date": {"dtype": "date", "nullable": True},
        "vendor_list_status": {"dtype": "string", "nullable": False},
        "event_state": {"dtype": "string", "nullable": False},
        "revision_id": {"dtype": "string", "nullable": False},
        "revision_sequence": {"dtype": "int64", "nullable": False},
        "first_observed_at": {"dtype": "timestamp", "nullable": False},
        "raw_batch_id": {"dtype": "string", "nullable": False},
        "source_available_at": {"dtype": "timestamp", "nullable": True},
        "evidence_ref": {"dtype": "string", "nullable": True},
    },
}
PROFILE = {
    "id": "tushare.stock_basic.listing_events.v1",
    "endpoint": "stock_basic",
    "revision_order": "source_sequence_only",
    "availability": {"timezone": "Asia/Shanghai", "session_release_time": "00:00:00",
                     "date_field": "event_date", "date_rule": "same_day_release",
                     "basis": "best-effort supplier event-date assumption; no historical public vintage"},
    "field_map": {"source_code": "ts_code", "exchange": "exchange",
                  "listing_date": "list_date", "delisting_date": "delist_date",
                  "vendor_list_status": "list_status"},
    "coverage_claim": "six Tushare SSE/SZSE L/D/P terminal stock_basic slices; supplier dates accepted",
}
_SELECTORS = {(exchange, status) for exchange in ("SSE", "SZSE") for status in ("L", "D", "P")}


def _source_day(value: Any, field: str) -> str:
    try:
        if not isinstance(value, str):
            raise ValueError
        return datetime.strptime(value, "%Y%m%d").date().isoformat()
    except ValueError as exc:
        raise DataError(f"stock_basic has invalid {field}") from exc


def _plain(row: Mapping[str, Any]) -> dict[str, Any]:
    return {key: (value.isoformat() if isinstance(value, (date, datetime)) else value)
            for key, value in row.items()}


def _digest(value: Any) -> str:
    return sha256(_json_bytes(value)).hexdigest()


def _source_rows(store: LocalStore, raw_ids: Sequence[str],
                 identity_map: Mapping[str, str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    records = store.get_raw_many(raw_ids)
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for raw_id in raw_ids:
        raw = records[raw_id]
        request = raw.get("request") or {}
        params = request.get("params") or {}
        selector = (params.get("exchange"), params.get("list_status"))
        if (raw.get("domain") != "reference_bootstrap" or request.get("endpoint") != "stock_basic"
                or selector not in _SELECTORS or raw.get("status") not in {"success", "empty"}):
            raise DataError(f"{raw_id} is not a successful uncapped Tushare stock_basic Raw")
        rows = _rows(store.read_raw_record(raw))
        if raw["status"] == "empty" and rows:
            raise DataError("empty stock_basic Raw contains rows")
        if raw["status"] == "success" and not rows:
            raise DataError("successful stock_basic Raw has no rows")
        item = {"raw_batch_id": raw_id, "observed_at": raw["observed_at"],
                "selector": selector, "rows": rows}
        old = latest.get(selector)
        if old is None or (old["observed_at"], old["raw_batch_id"]) < (item["observed_at"], raw_id):
            latest[selector] = item
    if set(latest) != _SELECTORS:
        raise DataError("vendor listing needs all six stock_basic selectors")
    by_code: dict[str, dict[str, Any]] = {}
    warnings = []
    for selector in sorted(latest):
        source = latest[selector]
        for original in source["rows"]:
            if any(name not in original for name in
                   ("ts_code", "exchange", "list_status", "list_date", "delist_date")):
                raise DataError("stock_basic row lacks a requested source field")
            if (original["exchange"], original["list_status"]) != selector:
                raise DataError("stock_basic row differs from its request selector")
            code = original["ts_code"]
            if not isinstance(code, str) or not code:
                raise DataError("stock_basic row has invalid source code")
            prior = by_code.get(code)
            if prior is not None:
                warnings.append({"kind": "supplier_duplicate_source_code", "source_code": code})
                if prior["observed_at"] >= source["observed_at"]:
                    continue
            by_code[code] = {"original": original, "raw_batch_id": source["raw_batch_id"],
                             "observed_at": source["observed_at"]}
    events = []
    for code, source in sorted(by_code.items()):
        if code not in identity_map:
            continue
        original = source["original"]
        listed = _source_day(original["list_date"], "list_date")
        delisted = (None if original["delist_date"] in (None, "") else
                    _source_day(original["delist_date"], "delist_date"))
        for kind, event_date in (("listing", listed), ("delisting", delisted)):
            if event_date is None:
                continue
            security = identity_map[code]
            event = {"security_id": security, "source_code": code,
                     "exchange": original["exchange"], "event_type": kind,
                     "event_date": event_date, "listing_date": listed,
                     "delisting_date": (delisted if kind == "delisting" else None),
                     "vendor_list_status": original["list_status"],
                     "event_state": "value", "revision_sequence": 1,
                     "first_observed_at": source["observed_at"],
                     "raw_batch_id": source["raw_batch_id"],
                     "source_available_at": None, "evidence_ref": None}
            event["revision_id"] = "vendor:" + _digest([security, kind, event_date,
                                                          source["observed_at"]])
            events.append(event)
    return events, warnings


def _versions(store: LocalStore, old: Mapping[str, Any] | None,
              current: list[dict[str, Any]], change_time: str) -> tuple[list[dict[str, Any]], bool]:
    if old is None or old.get("source_profile", {}).get("id") != PROFILE["id"]:
        return current, True
    prior = [_plain(row) for part in old["partitions"] for row in store.read_partition(part).to_pylist()]
    latest = {(row["security_id"], row["event_type"]): row
              for row in sorted(prior, key=lambda item: item["revision_sequence"])}
    result = list(prior)
    current_keys = set()
    changed = False
    material = lambda row: (row["source_code"], row["exchange"], row["event_date"],
                            row["listing_date"], row["delisting_date"],
                            row["vendor_list_status"], row["event_state"])
    for row in current:
        key = row["security_id"], row["event_type"]
        current_keys.add(key)
        previous = latest.get(key)
        if previous is not None and material(previous) == material(row):
            continue
        if previous is not None:
            row["revision_sequence"] = previous["revision_sequence"] + 1
            row["first_observed_at"] = max(row["first_observed_at"], previous["first_observed_at"])
            row["revision_id"] = "vendor:" + _digest([key, material(row), row["first_observed_at"]])
        result.append(row)
        changed = True
    for key, previous in latest.items():
        if key in current_keys or previous["event_state"] == "source_missing":
            continue
        row = dict(previous)
        row["event_state"] = "source_missing"
        row["revision_sequence"] = previous["revision_sequence"] + 1
        row["first_observed_at"] = max(change_time, previous["first_observed_at"])
        row["revision_id"] = "vendor:" + _digest([key, "source_missing", row["first_observed_at"]])
        result.append(row)
        changed = True
    return result, changed


def build_vendor_listing_domain(store: LocalStore, *, stock_basic_raw_batch_ids: Sequence[str],
                                identity_map: Mapping[str, str] | None,
                                operation_id: str,
                                old_domain: Mapping[str, Any] | None = None,
                                old_snapshot_id: str | None = None,
                                prior_source_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Build listing/delisting events solely from six saved Tushare slices."""
    ids = list(dict.fromkeys(stock_basic_raw_batch_ids))
    identity = dict(identity_map or {})
    old = old_domain if old_domain and old_domain.get("source_profile", {}).get("id") == PROFILE["id"] else None
    previous = (dict(prior_source_config) if prior_source_config is not None else
                vendor_listing_source_chain(store, old)[-1] if old is not None else None)
    if previous is not None:
        if any(identity.get(code) != stable for code, stable in previous["identity_map"].items()):
            raise DataError("vendor listing cannot remap existing security identities")
        if not set(previous["stock_basic_raw_batch_ids"]) <= set(ids):
            raise DataError("vendor listing update omitted prior stock_basic Raw")
    current, warnings = _source_rows(store, ids, identity)
    change_time = max(store.get_raw(raw_id)["observed_at"] for raw_id in ids)
    rows, changed = _versions(store, old, current, change_time)
    if old is not None and not changed:
        return deepcopy(dict(old))
    if old is not None and not old_snapshot_id:
        raise DataError("vendor listing update needs parent Snapshot ID")
    partition = store.write_partition("listing_events", "history", rows, CONTRACT)
    config = {"schema_version": SCHEMA, "stock_basic_raw_batch_ids": ids,
              "identity_map": identity}
    context = ({"vendor_listing_source_base": config} if old is None else {
        "vendor_listing_source_parent_snapshot": old_snapshot_id,
        "vendor_listing_source_delta": {
            "stock_basic_raw_batch_ids": [raw_id for raw_id in ids if raw_id not in previous["stock_basic_raw_batch_ids"]],
            "identity_map": {code: stable for code, stable in identity.items()
                             if code not in previous["identity_map"]},
        }})
    return {"contract": CONTRACT, "source_profile": PROFILE,
            "partitions": [partition], "raw_batch_ids": ids,
            "coverage": {"basis": "tushare_terminal_stock_basic_snapshot_v1",
                         "selected_slices": [list(selector) for selector in sorted(_SELECTORS)],
                         "warnings": warnings,
                         "limitations": [
                             "The current supplier snapshot reports historical list_date and delist_date; it does not prove historical publication time.",
                             "Best-effort event dates are supplier-date assumptions; strict policies use original Raw receipt.",
                         ]},
            "build_context": {"operation_id": operation_id, **context}}


def vendor_listing_source_chain(store: LocalStore,
                                domain: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Recover vendor listing source configs through immutable parent Snapshots."""
    context = domain.get("build_context") or {}
    base = context.get("vendor_listing_source_base")
    if isinstance(base, Mapping):
        if base.get("schema_version") != SCHEMA:
            raise DataError("unsupported vendor listing source schema")
        return [deepcopy(dict(base))]
    parent_id = context.get("vendor_listing_source_parent_snapshot")
    delta = context.get("vendor_listing_source_delta")
    if not isinstance(parent_id, str) or not isinstance(delta, Mapping):
        return []
    parent = store.load_snapshot(parent_id)
    previous = parent["domains"].get("listing_events")
    if previous is None:
        raise DataError("vendor listing parent Snapshot lacks listing_events")
    chain = vendor_listing_source_chain(store, previous)
    config = deepcopy(chain[-1])
    config["stock_basic_raw_batch_ids"].extend(delta.get("stock_basic_raw_batch_ids") or [])
    config["identity_map"].update(delta.get("identity_map") or {})
    chain.append(config)
    return chain


def publish_vendor_listing(store: LocalStore, *, stock_basic_raw_batch_ids: Sequence[str],
                           identity_map: Mapping[str, str] | None,
                           operation_id: str, base_snapshot: str | None,
                           promote: bool = False) -> dict[str, Any]:
    """Publish source-relative Tushare listing events; preserve unchanged data."""
    parent = store.load_snapshot(base_snapshot) if base_snapshot else None
    old = parent["domains"].get("listing_events") if parent else None
    domain = build_vendor_listing_domain(
        store, stock_basic_raw_batch_ids=stock_basic_raw_batch_ids,
        identity_map=identity_map, operation_id=operation_id,
        old_domain=old, old_snapshot_id=base_snapshot)
    if old == domain:
        return {"snapshot_id": base_snapshot, "changed": False}
    domains = deepcopy(parent["domains"]) if parent else {}
    domains["listing_events"] = domain
    snapshot = store.publish_snapshot(domains, parent_snapshot=base_snapshot,
                                      build_context={"source": SCHEMA, "operation_id": operation_id},
                                      promote=promote)
    return {"snapshot_id": snapshot["snapshot_id"], "changed": True}
