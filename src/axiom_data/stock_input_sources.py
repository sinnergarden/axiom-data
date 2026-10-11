"""Explicit Tushare input-domain adapters; no feature computation or scheduling.

One call retains one original response in Raw. Normalization validates the full
response, then selects frozen securities. Financial v3 requires an explicit
selected-domain rebuild; legacy collectors/contracts remain unchanged.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, time, timezone
import re
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from .event_sources import (CONTRACTS as EVENT_CONTRACTS, _FIELDS, _FINANCIAL,
                           _STATEMENTS, _financial_unique, _response_issue,
                           _source_rows, _validate_params, event_source_profile,
                           request_fields)
from .protocols import ConflictError, CoverageError, DataError, IngestBatch
from .live_client import SourceResponseError
from .source_failure import safe_failure
from .sources import _rows
from .storage import LocalStore

NORMALIZER = "tushare_stock_inputs_v1"
FIELD_SET = "stock_feature_inputs_v1"
_EXTRA = {"income": {"revenue": ("revenue", "CNY"), "net_income": ("n_income", "CNY"),
                     "oper_cost": ("oper_cost", "CNY")},
          "balancesheet": {"inventory": ("inventories", "CNY"),
                           "accounts_receiv": ("accounts_receiv", "CNY")},
          "fina_indicator": {"grossprofit_margin": ("grossprofit_margin", "percent"),
                             "current_ratio": ("current_ratio", "ratio")}}
_DAILY = {
    "daily_basic": ("valuation_daily", {
        **{f: (f, "ratio", "ratio", 1) for f in ("pe", "pe_ttm", "pb", "ps", "ps_ttm", "volume_ratio")},
        **{f: (f, "percent", "percent", 1) for f in ("turnover_rate", "turnover_rate_f")},
        "total_market_value_cny": ("total_mv", "ten_thousand_CNY", "CNY", 10000),
        "float_market_value_cny": ("circ_mv", "ten_thousand_CNY", "CNY", 10000),
        **{f: (src, "ten_thousand_shares", "shares", 10000) for f, src in
           (("total_shares", "total_share"), ("float_shares", "float_share"), ("free_shares", "free_share"))}}),
    "moneyflow": ("money_flow_daily", {
        "net_inflow_cny": ("net_mf_amount", "ten_thousand_CNY", "CNY", 10000),
        "extra_large_buy_cny": ("buy_elg_amount", "ten_thousand_CNY", "CNY", 10000),
        "extra_large_sell_cny": ("sell_elg_amount", "ten_thousand_CNY", "CNY", 10000)}),
    "margin_detail": ("margin_daily", {
        **{f: (src, "CNY", "CNY", 1) for f, src in
           (("margin_balance", "rzye"), ("margin_buy_amount", "rzmre"),
            ("margin_repay_amount", "rzche"), ("margin_total_balance", "rzrqye"))},
        "lend_volume": ("rqyl", "shares", "shares", 1),
        "lend_repay_volume": ("rqchl", "shares", "shares", 1),
        "lend_sell_native": ("rqmcl", "supplier_quantity", "supplier_quantity", 1)})}
_EVENT_FIELDS = {
    "stk_holdernumber": ("ts_code", "ann_date", "end_date", "holder_num"),
    "forecast": ("ts_code", "ann_date", "first_ann_date", "end_date", "type"),
    "index_member_all": ("ts_code", "l1_code", "l2_code", "l3_code", "in_date", "out_date", "is_new"),
    "index_classify": ("index_code", "industry_name", "parent_code", "level", "industry_code", "is_pub", "src")}
_CAPS = {"daily_basic": 6000, "moneyflow": 6000, "margin_detail": 6000,
         "stk_holdernumber": 3000, "forecast": 3500, "forecast_vip": None,
         "index_member_all": 2000, "index_classify": None,
         "income": 6000, "income_vip": None, "balancesheet": 6000,
         "balancesheet_vip": None, "fina_indicator": 100, "fina_indicator_vip": None}


def _field(dtype="string", *, required=False, unit=None, basis=None):
    result = {"dtype": dtype, "nullable": not required}
    if unit:
        result["unit"] = unit
    if basis:
        result["basis"] = basis
    return result


def _contract(domain, key, fields):
    meta = deepcopy(EVENT_CONTRACTS["income"]["fields"])
    meta = {f: meta[f] for f in ("revision_id", "revision_sequence", "first_observed_at",
                                "raw_batch_id", "source_available_at", "evidence_ref")}
    return {"contract_id": f"local.{domain}.tushare.input.v1", "logical_key": list(key),
            "fields": {**fields, **meta}}


def _iso(value, name):
    if not isinstance(value, str) or not re.fullmatch(r"\d{8}", value):
        raise DataError(f"{name} requires YYYYMMDD")
    try:
        return datetime.strptime(value, "%Y%m%d").date().isoformat()
    except ValueError as exc:
        raise DataError(f"invalid {name}") from exc


def stock_input_source_spec(endpoint: str, *, identity_map: Mapping[str, str],
                            next_open_session_by_date=None,
                            lending_sell_unit_by_security=None) -> dict[str, Any]:
    """Return isolated fields/contract/profile; no I/O, cache or supplier calls.

    Actual public timestamps are unsupported without revision-bound evidence.
    Industry interval/publication/taxonomy precision is not inferred. Margin's
    declared next-open clock is usable through events, while the ordinary daily
    best-effort reader intentionally rejects its unsupported same-day clock.
    lending_sell_unit_by_security may explicitly bind stable IDs to 'shares';
    otherwise only native rqmcl quantity is exposed, with shares not_provided.
    """
    if endpoint not in _CAPS:
        raise DataError("unsupported stock input endpoint")
    # Reuse current stock identity/calendar validation, without mutating defaults.
    if not isinstance(identity_map, Mapping):
        raise DataError("stock inputs require an explicit identity mapping")
    identities = dict(identity_map)
    if not identities or any(not re.fullmatch(r"\d{6}\.(SH|SZ)", str(k)) or
                             not isinstance(v, str) or not v for k, v in identities.items()) or len(set(identities.values())) != len(identities):
        raise DataError("stock inputs require unique explicit stock identity bindings")
    base = endpoint.removesuffix("_vip")
    if endpoint in _FINANCIAL:
        profile = event_source_profile(endpoint, identity_map=identities,
                                       next_open_session_by_date=next_open_session_by_date)
        contract = deepcopy(EVENT_CONTRACTS[endpoint])
        contract["contract_id"] = contract["contract_id"].removesuffix("v2") + "v3"
        fields = list(request_fields(endpoint))
        for canonical, (native, unit) in _EXTRA[base].items():
            spec = _field("float64", unit=unit, basis="cumulative_ytd" if base == "income" else "period_end_stock" if base == "balancesheet" else None)
            spec["status_field"] = canonical + "__status"
            contract["fields"][canonical] = spec
            contract["fields"][canonical + "__status"] = _field()
            profile["field_map"].update({canonical: native, canonical + "__status": "__status__" + native})
            profile["source_units"][native] = unit
            fields.append(native)
        domain = {"income": "financial_events", "balancesheet": "balance_sheet_events",
                  "fina_indicator": "financial_indicator_events"}[base]
    else:
        profile = {"endpoint": base, "identity_map": identities, "field_map": {},
                   "date_formats": {}, "source_units": {}, "unit_conversions": {},
                   "revision_order": "terminal_observation_v1", "missing_row": "unknown",
                   "coverage_claim": "observed_response_only; source completeness unverified"}
        fm = profile["field_map"]
        if base in _DAILY:
            domain, values = _DAILY[base]
            declared = {"security_id": _field(required=True), "session": _field("date", required=True)}
            fm.update(security_id="ts_code", session="trade_date")
            fields = ["ts_code", "trade_date"]
            profile["date_formats"]["session"] = "YYYYMMDD"
            for canonical, (native, src_unit, unit, factor) in values.items():
                declared[canonical] = _field("float64", unit=unit)
                fm[canonical] = native
                profile["source_units"][native] = src_unit
                if factor != 1:
                    profile["unit_conversions"][canonical] = {"from": src_unit, "to": unit, "factor": factor}
                fields.append(native)
            if base == "margin_detail":
                calendar = dict(next_open_session_by_date or {})
                if not calendar:
                    raise DataError("margin inputs require explicit next-open calendar")
                units = dict(lending_sell_unit_by_security or {})
                if any(k not in identities.values() or v != "shares" for k, v in units.items()):
                    raise DataError("lending sell units require explicit stable-stock shares bindings")
                profile.update(next_open_session_by_date=calendar, lending_sell_unit_by_security=units)
                profile["availability"] = {"basis": "declared_next_open_08:30_assumption; not publication evidence",
                                           "declared_available_at_field": "declared_available_at"}
                declared.update(declared_available_at=_field("timestamp", required=True),
                                lend_sell_volume=_field("float64", unit="shares"), lend_sell_volume__status=_field())
                declared["lend_sell_volume"]["status_field"] = "lend_sell_volume__status"
                fm.update(declared_available_at="__release", lend_sell_volume="__sell_shares",
                          lend_sell_volume__status="__sell_status")
                profile["source_units"]["__sell_shares"] = "shares"
            else:
                profile["availability"] = {"basis": "best_effort_same_session_20:00_assumption; not publication evidence",
                                           "timezone": "Asia/Shanghai", "session_release_time": "20:00:00"}
            contract = _contract(domain, ("security_id", "session"), declared)
        elif base in {"forecast", "stk_holdernumber"}:
            domain = "forecast_events" if base == "forecast" else "shareholder_count_events"
            fields = list(_EVENT_FIELDS[base])
            declared = {"security_id": _field(required=True), "report_period": _field("date", required=True),
                        "announcement_date": _field("date", required=True), "actual_announcement_date": _field("date", required=True)}
            fm.update(security_id="ts_code", report_period="end_date", announcement_date="ann_date", actual_announcement_date="ann_date")
            profile["date_formats"].update({f: "YYYYMMDD" for f in ("report_period", "announcement_date", "actual_announcement_date")})
            if base == "forecast":
                declared.update(forecast_type=_field(), first_announcement_date=_field("date"))
                fm.update(forecast_type="type", first_announcement_date="first_ann_date")
                profile["date_formats"]["first_announcement_date"] = "YYYYMMDD"
                profile["null_values"] = {"first_announcement_date": [""]}
            else:
                declared["holder_num"] = _field("int64", unit="holders")
                fm["holder_num"] = "holder_num"
                profile["source_units"]["holder_num"] = "holders"
            # Existing financial availability/revision policy, current ann_date.
            clock_profile = event_source_profile("fina_indicator", identity_map=identities,
                                                  next_open_session_by_date=next_open_session_by_date)
            profile["availability"] = clock_profile["availability"]
            profile["revision_order"] = "announcement_day_then_terminal_v1"
            contract = _contract(domain, ("security_id", "report_period"), declared)
        elif base == "index_member_all":
            domain = "industry_membership_events"
            fields = list(_EVENT_FIELDS[base])
            declared = {"security_id": _field(required=True),
                        **{f: _field(required=True) for f in ("l1_code", "l2_code", "l3_code")},
                        "effective_from": _field("date", required=True), "effective_to": _field("date"), "is_new": _field(required=True)}
            fm.update(security_id="ts_code", effective_from="in_date", effective_to="out_date", is_new="is_new",
                      **{f: f for f in ("l1_code", "l2_code", "l3_code")})
            profile["date_formats"].update(effective_from="YYYYMMDD", effective_to="YYYYMMDD")
            profile["null_values"] = {"effective_to": [""]}
            profile["historical_interval_boundary"] = "supplier dates retained; out_date inclusivity unverified"
            profile["historical_taxonomy_version"] = "not_provided; catalog selector cannot prove member-row vintage"
            contract = _contract(domain, ("security_id", "l1_code", "l2_code", "l3_code", "effective_from"), declared)
        else:
            domain = "industry_catalogue"
            fields = list(_EVENT_FIELDS[base])
            declared = {f: _field(required=f in {"index_code", "level", "requested_src"}) for f in (*fields, "requested_src")}
            fm.update({f: f for f in fields}, requested_src="__requested_src")
            contract = _contract(domain, ("requested_src", "index_code"), declared)
    profile.update(id=f"tushare.local.{base}.{FIELD_SET}", input_field_set=FIELD_SET,
                   response_limit=_CAPS[endpoint], historical_publication_evidence="not_supported by terminal history")
    return {"domain": domain, "fields": list(dict.fromkeys(fields)), "contract": contract,
            "source_profile": profile, "normalizer": NORMALIZER}


def _params(endpoint, params, identities):
    base = endpoint.removesuffix("_vip")
    if endpoint in _FINANCIAL:
        return _validate_params(endpoint, params, identities)
    if not isinstance(params, Mapping) or any(not isinstance(v, str) for v in params.values()):
        raise DataError("stock source params must be bounded strings")
    p = dict(params)
    allowed = {"ts_code", "trade_date", "start_date", "end_date"} if base in _DAILY else {"ts_code", "ann_date", "start_date", "end_date", "period"}
    if base == "index_member_all":
        allowed = {"ts_code", "l1_code", "l2_code", "l3_code", "is_new"}
        if p.get("is_new") not in {"Y", "N"} or not set(p) & {"ts_code", "l1_code", "l2_code", "l3_code"}:
            raise DataError("historical industry requests need an explicit classification/security and is_new Y/N")
    elif base == "index_classify":
        allowed = {"src", "level", "index_code", "parent_code"}
        if p.get("src") not in {"SW2014", "SW2021"} or p.get("level") not in {"L1", "L2", "L3"}:
            raise DataError("industry catalog requires explicit src and level")
    elif base in _DAILY:
        if "trade_date" not in p and not ("ts_code" in p and "start_date" in p and "end_date" in p):
            raise DataError("daily input needs market trade_date or bounded security history")
    elif not ("ann_date" in p or "start_date" in p and "end_date" in p or endpoint.endswith("_vip") and "period" in p):
        raise DataError("event input requires explicit announcement date/window or VIP period")
    if set(p) - allowed or any(len(v) > 64 or not v for v in p.values()):
        raise DataError("unsupported/unbounded stock source params")
    if "ts_code" in p and p["ts_code"] not in identities:
        raise DataError("unmapped requested security")
    for name in ("trade_date", "start_date", "end_date", "ann_date", "period"):
        if name in p:
            _iso(p[name], name)
    if p.get("start_date", "") > p.get("end_date", "99999999"):
        raise DataError("reversed stock source range")
    return p


def prepare_stock_input_rows(batch: IngestBatch) -> list[dict[str, Any]]:
    """Validate retained source scope and normalize copies; never alter Raw.

    Expanded financial fields absent from legacy Raw remain not_provided.
    Same-disclosure conflicts compare the complete expanded field set; no
    cross-row or cross-receipt complementary merge creates a new report.
    """
    endpoint = batch.request.get("endpoint")
    if endpoint not in _CAPS or batch.source_profile.get("input_field_set") != FIELD_SET:
        raise DataError("unsupported retained stock input source/field set")
    base = endpoint.removesuffix("_vip")
    identities = batch.source_profile["identity_map"]
    params = _params(endpoint, batch.request.get("params", {}), identities)
    selected = batch.request.get("canonical_symbols")
    if base != "index_classify" and (not isinstance(selected, list) or not selected or
                                     len(set(selected)) != len(selected) or any(s not in identities for s in selected)):
        raise DataError("stock input needs explicit frozen canonical_symbols")
    rows = _rows(batch.payload)
    cap = batch.source_profile.get("response_limit")
    if cap is not None and len(rows) >= cap:
        raise CoverageError("stock input response reached row cap; split the selector")
    required = list(batch.request.get("fields") or ())
    if endpoint in _FINANCIAL and batch.request.get("input_field_set") != FIELD_SET:
        # Legacy event adapters permitted absent optional statement context.
        # Rebuild preserves that interpretation; new explicit requests remain
        # strict about every requested expanded field and context column.
        required = list(_FIELDS[endpoint])
    if not required:
        raise DataError("retained input lacks explicit source field request")
    for row in rows:
        if any(f not in row for f in required):
            raise DataError("stock input response lacks a requested field")
        if params.get("ts_code") and row.get("ts_code") != params["ts_code"]:
            raise DataError("stock input response contains another requested security")
        if base in _DAILY or base in {"forecast", "stk_holdernumber"}:
            axis = "trade_date" if base in _DAILY else "ann_date"
            _iso(row.get(axis), axis)
            if not params.get("start_date", "00000000") <= row[axis] <= params.get("end_date", "99999999") or any(
                    name in params and row.get(axis if name != "period" else "end_date") != params[name]
                    for name in ("trade_date", "ann_date", "period")):
                raise DataError("stock input response lies outside requested date scope")
        if base == "index_member_all":
            if any(row.get(f) != params[f] for f in ("l1_code", "l2_code", "l3_code", "is_new") if f in params):
                raise DataError("industry response lies outside requested classification/state")
            _iso(row.get("in_date"), "industry in_date")
            if row.get("out_date") not in (None, ""):
                _iso(row["out_date"], "industry out_date")
                if row["out_date"] < row["in_date"]:
                    raise DataError("reversed supplier industry interval")
        if base == "index_classify" and any(row.get(f) != params[f] for f in ("level", "index_code", "parent_code") if f in params):
            raise DataError("catalog response lies outside requested classification")
    chosen = [dict(row) for row in rows if base == "index_classify" or row["ts_code"] in selected]
    if endpoint in _FINANCIAL:
        issue = _response_issue(endpoint, rows, params, batch.source_profile)
        if issue:
            raise DataError(issue)
        fields = list(dict.fromkeys((*request_fields(endpoint), *(n for n, _ in _EXTRA[base].values()))))
        numeric = [batch.source_profile["field_map"][f] for f, s in batch.contract["fields"].items()
                   if s.get("dtype") == "float64"]
        chosen = _financial_unique(endpoint, chosen, fields=fields, numeric_fields=numeric)
        for row in chosen:
            row["__endpoint"] = base
            for native, _ in _EXTRA[base].values():
                row.setdefault("__status__" + native, "value" if row.get(native) is not None else "source_missing" if native in row else "not_provided")
    elif base == "margin_detail":
        for row in chosen:
            day = _iso(row["trade_date"], "trade_date")
            next_day = batch.source_profile["next_open_session_by_date"].get(day)
            try:
                opening = datetime.fromisoformat(next_day).date()
                if opening.isoformat() <= day:
                    raise ValueError()
            except (TypeError, ValueError) as exc:
                raise DataError("margin day lacks a later next-open calendar entry") from exc
            row["__release"] = datetime.combine(opening, time(8, 30), ZoneInfo("Asia/Shanghai")).isoformat()
            bound = identities[row["ts_code"]] in batch.source_profile["lending_sell_unit_by_security"]
            row["__sell_shares"] = row["rqmcl"] if bound else None
            row["__sell_status"] = "value" if bound and row["rqmcl"] is not None else "not_provided"
    elif base in {"forecast", "stk_holdernumber"}:
        for row in chosen:
            day = _iso(row["ann_date"], "ann_date")
            _iso(row["end_date"], "end_date")
            if day not in batch.source_profile["availability"]["next_open_session_by_date"]:
                raise DataError("stock event lacks explicit next-open calendar rule")
    elif base == "index_classify":
        for row in chosen:
            row["__requested_src"] = params["src"]
    return chosen


def collect_stock_input_response(store: LocalStore, *, client: Any, endpoint: str,
                                 params: Mapping[str, str], identity_map: Mapping[str, str],
                                 canonical_symbols=None, operation_id: str, batch_index=0,
                                 next_open_session_by_date=None, lending_sell_unit_by_security=None,
                                 clock=None, observed_at=None) -> dict[str, Any]:
    """Make exactly one explicit injected call and save success/empty/cap/failure.

    The saved slot binds endpoint, fields, selectors, identity and source policy.
    Retries reuse successful bytes; failed/capped slots require a new index.
    Receipt is sampled after the response. Failures retain safe error codes and
    never become empty/zero data. No quota change, pagination or extra call is
    inferred; the caller probes then splits capped selectors under its budget.
    """
    if not isinstance(store, LocalStore):
        raise DataError("stock inputs require LocalStore")
    spec = stock_input_source_spec(endpoint, identity_map=identity_map,
                                  next_open_session_by_date=next_open_session_by_date,
                                  lending_sell_unit_by_security=lending_sell_unit_by_security)
    p = _params(endpoint, params, identity_map)
    symbols = list(canonical_symbols if canonical_symbols is not None else sorted(identity_map))
    if not symbols or len(set(symbols)) != len(symbols) or any(s not in identity_map for s in symbols):
        raise DataError("stock inputs require a nonempty frozen candidate scope")
    request = {"endpoint": endpoint, "params": p, "fields": spec["fields"],
               "canonical_symbols": symbols, "input_field_set": FIELD_SET,
               "coverage_status": "observed_response_only"}
    previous = store.find_raw_by_operation(operation_id).get(batch_index)
    if previous:
        if previous["request"] != request or previous["source_profile"] != spec["source_profile"] or previous["contract"] != spec["contract"]:
            raise ConflictError("stock input saved slot differs from this request/profile/contract")
        if previous["status"] in {"success", "empty"}:
            return previous
        raise CoverageError("stock input slot is failed/capped; use a new batch index")
    query = getattr(client, "query", None)
    if not callable(query):
        raise DataError("stock source client needs query(endpoint, fields, **params)")
    receipt = None
    try:
        response = query(endpoint, fields=",".join(spec["fields"]), **p)
        receipt = observed_at if observed_at is not None else (clock or (lambda: datetime.now(timezone.utc)))()
        payload, rows = _source_rows(response)
        cap = _CAPS[endpoint]
        if rows is None:
            raise DataError("malformed stock supplier table")
        status = "cap" if cap is not None and len(rows) >= cap else "empty" if not rows else "success"
        if status != "cap":
            batch = IngestBatch(spec["domain"], payload, request, spec["contract"], spec["source_profile"], receipt, NORMALIZER)
            prepare_stock_input_rows(batch)
        issue = "source response reached row cap; split selector" if status == "cap" else None
    except Exception as exc:
        if "payload" not in locals():
            payload = safe_failure(exc, "stock_input_fetch_or_serialize")
        issue = (f"supplier request refused/unavailable (code={exc.code})" if isinstance(exc, SourceResponseError)
                 else str(exc) if isinstance(exc, DataError)
                 else f"stock supplier fetch/serialize failed ({type(exc).__name__})")
        status = "failed"
    if receipt is None:
        receipt = observed_at if observed_at is not None else (clock or (lambda: datetime.now(timezone.utc)))()
    if not isinstance(receipt, datetime) or receipt.tzinfo is None or receipt.utcoffset() is None:
        raise DataError("stock input receipt must be timezone aware")
    raw = store.write_raw(payload, domain=spec["domain"], request=request, source_profile=spec["source_profile"],
                          contract=spec["contract"], normalizer=NORMALIZER, observed_at=receipt,
                          status=status, operation_id=operation_id, batch_index=batch_index)
    if issue:
        raise CoverageError(f"{endpoint} Raw {raw['batch_id']}: {issue}")
    return raw
