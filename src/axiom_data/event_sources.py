"""Small Raw-first adapters for Tushare financial, action and holder events.

Calls are explicit and injected. ``collect_event_response`` makes exactly one
supplier request, logs even failed/capped/empty responses, and returns a saved
Raw record for ``apply_saved_raw``. Canonical conversion happens only later.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from datetime import date, datetime, timezone
from hashlib import sha256
import json
import math
import re
from typing import Any, Callable

from .protocols import ConflictError, CoverageError, DataError
from .source_failure import safe_failure as _safe_failure
from .sources import _response_table
from .storage import LocalStore


_SYMBOL = re.compile(r"^[0-9]{6}\.(?:SH|SZ)$")
_ENDPOINTS = {"income", "income_vip", "balancesheet", "balancesheet_vip",
              "cashflow", "cashflow_vip", "fina_indicator", "fina_indicator_vip",
              "dividend", "top10_holders", "stk_limit"}
_CAPS = {"income": 6000, "income_vip": None,
         "balancesheet": 6000, "balancesheet_vip": None,
         "cashflow": 6000, "cashflow_vip": None,
         "fina_indicator": 100, "fina_indicator_vip": None, "dividend": 2000,
         "top10_holders": 5000, "stk_limit": 5800}
# None means the VIP page gives no numeric cap: retain the whole observed
# response without inventing a truncation threshold or an inferred census.
# Ordinary income/holder caps remain conservative local safeguards, not proof
# of source completeness.
_FIELDS = {
    "income": ("ts_code", "ann_date", "f_ann_date", "end_date", "report_type",
               "total_revenue", "n_income_attr_p"),
    "dividend": ("ts_code", "end_date", "ann_date", "imp_ann_date", "div_proc",
                 "cash_div_tax", "stk_bo_rate", "stk_co_rate", "record_date", "ex_date"),
    "top10_holders": ("ts_code", "ann_date", "end_date", "holder_name",
                      "hold_amount", "hold_ratio"),
    "stk_limit": ("ts_code", "trade_date", "up_limit", "down_limit"),
}
_FIELDS["income_vip"] = _FIELDS["income"]
_FIELDS["balancesheet"] = ("ts_code", "ann_date", "f_ann_date", "end_date", "report_type",
                           "total_assets", "total_liab", "total_hldr_eqy_exc_min_int")
_FIELDS["balancesheet_vip"] = _FIELDS["balancesheet"]
_FIELDS["cashflow"] = ("ts_code", "ann_date", "f_ann_date", "end_date", "report_type",
                      "n_cashflow_act", "n_cashflow_inv_act", "n_cash_flows_fnc_act")
_FIELDS["cashflow_vip"] = _FIELDS["cashflow"]
_FIELDS["fina_indicator"] = ("ts_code", "ann_date", "end_date", "roe", "roe_waa", "debt_to_assets")
_FIELDS["fina_indicator_vip"] = _FIELDS["fina_indicator"]
_DOMAINS = {"income": "financial_events", "dividend": "corporate_actions",
            "top10_holders": "top_holders_reports", "stk_limit": "price_limits"}
_DOMAINS["income_vip"] = "financial_events"
_DOMAINS.update({"balancesheet": "balance_sheet_events", "balancesheet_vip": "balance_sheet_events",
                 "cashflow": "cash_flow_events", "cashflow_vip": "cash_flow_events",
                 "fina_indicator": "financial_indicator_events",
                 "fina_indicator_vip": "financial_indicator_events"})
_STATEMENTS = {"income", "income_vip", "balancesheet", "balancesheet_vip",
               "cashflow", "cashflow_vip"}
_INDICATORS = {"fina_indicator", "fina_indicator_vip"}
_FINANCIAL = _STATEMENTS | _INDICATORS
_STATEMENT_CONTEXT = ("comp_type", "end_type", "update_flag")
_DIVIDEND_KEY = ("ts_code", "end_date", "ann_date", "div_proc")
_DIVIDEND_VALUES = tuple(field for field in _FIELDS["dividend"] if field not in _DIVIDEND_KEY)
_DIVIDEND_V3_FIELDS = _FIELDS["dividend"] + ("stk_div", "pay_date", "div_listdate")
_DIVIDEND_DATES = {"imp_ann_date", "record_date", "ex_date", "pay_date", "div_listdate"}


def request_fields(endpoint: str, *, corporate_action_rules=None) -> tuple[str, ...]:
    """Request statement context as evidence, without requiring it in older Raw.

    update_flag has no documented public revision order and never selects a
    winner. comp_type/end_type describe statement context, not chronology.
    Explicit corporate_action_rules adds native total/payment/listing fields;
    it does not request an undocumented vendor action-ID column.
    """
    if corporate_action_rules is not None:
        if endpoint != "dividend":
            raise DataError("economic action rules apply only to dividend")
        return _DIVIDEND_V3_FIELDS
    return _FIELDS[endpoint] + (_STATEMENT_CONTEXT if endpoint in _STATEMENTS else ())


def _f(dtype: str, *, required: bool = False, unit: str | None = None,
       basis: str | None = None) -> dict[str, Any]:
    spec: dict[str, Any] = {"dtype": dtype, "nullable": not required}
    if unit:
        spec["unit"] = unit
    if basis:
        spec["basis"] = basis
    return spec


_META = {"revision_id": _f("string", required=True),
         "revision_sequence": _f("int64"),
         "first_observed_at": _f("timestamp", required=True),
         "raw_batch_id": _f("string", required=True),
         "source_available_at": _f("timestamp"),
         "evidence_ref": _f("string")}


def _contract(domain: str, key: tuple[str, ...], fields: dict[str, Any]) -> dict[str, Any]:
    return {"contract_id": f"local.{domain}.tushare.v1", "logical_key": list(key),
            "fields": {**fields, **_META}}


CONTRACTS = {
    "income": _contract("financial_events", ("security_id", "endpoint", "report_type", "report_period"), {
        "security_id": _f("string", required=True), "endpoint": _f("string", required=True),
        "report_type": _f("string", required=True), "report_period": _f("date", required=True),
        "announcement_date": _f("date", required=True), "actual_announcement_date": _f("date", required=True),
        "basis": _f("string", required=True),
        "total_revenue": _f("float64", unit="CNY", basis="cumulative_ytd"),
        "parent_net_income": _f("float64", unit="CNY", basis="cumulative_ytd"),
    }),
    "dividend": _contract("corporate_actions", ("security_id", "report_period", "announcement_date", "process_status"), {
        "security_id": _f("string", required=True), "report_period": _f("date", required=True),
        "announcement_date": _f("date", required=True), "process_status": _f("string", required=True),
        "cash_dividend_before_tax_per_share": _f("float64", unit="CNY/share"),
        "bonus_shares_per_share": _f("float64", unit="shares/share"),
        "capital_transfer_shares_per_share": _f("float64", unit="shares/share"),
        "implementation_announcement_date": _f("date"),
        "record_date": _f("date"), "ex_date": _f("date"),
    }),
    "top10_holders": _contract("top_holders_reports", ("security_id", "report_period"), {
        "security_id": _f("string", required=True), "report_period": _f("date", required=True),
        "announcement_date": _f("date", required=True),
        "actual_announcement_date": _f("date", required=True),
        "holders": _f("string", required=True),
        "holder_count": _f("int64", required=True),
        "group_completeness": _f("string", required=True),
        "group_issue": _f("string"),
        "top10_ratio": _f("float64", unit="percent of total shares"),
    }),
    "stk_limit": _contract("price_limits", ("security_id", "session"), {
        "security_id": _f("string", required=True), "session": _f("date", required=True),
        "up_limit": _f("float64", unit="CNY/share"),
        "down_limit": _f("float64", unit="CNY/share"),
    }),
}
CONTRACTS["income_vip"] = CONTRACTS["income"]
CONTRACTS["balancesheet"] = _contract(
    "balance_sheet_events", ("security_id", "endpoint", "report_type", "report_period"), {
        "security_id": _f("string", required=True), "endpoint": _f("string", required=True),
        "report_type": _f("string", required=True), "report_period": _f("date", required=True),
        "announcement_date": _f("date", required=True),
        "actual_announcement_date": _f("date", required=True),
        "total_assets": _f("float64", unit="CNY", basis="period_end_stock"),
        "total_liabilities": _f("float64", unit="CNY", basis="period_end_stock"),
        "parent_equity": _f("float64", unit="CNY", basis="period_end_stock"),
    })
CONTRACTS["balancesheet_vip"] = CONTRACTS["balancesheet"]
CONTRACTS["cashflow"] = _contract(
    "cash_flow_events", ("security_id", "endpoint", "report_type", "report_period"), {
        "security_id": _f("string", required=True), "endpoint": _f("string", required=True),
        "report_type": _f("string", required=True), "report_period": _f("date", required=True),
        "announcement_date": _f("date", required=True),
        "actual_announcement_date": _f("date", required=True),
        "operating_cash_flow": _f("float64", unit="CNY", basis="cumulative_ytd"),
        "investing_cash_flow": _f("float64", unit="CNY", basis="cumulative_ytd"),
        "financing_cash_flow": _f("float64", unit="CNY", basis="cumulative_ytd"),
    })
CONTRACTS["cashflow_vip"] = CONTRACTS["cashflow"]
CONTRACTS["fina_indicator"] = _contract(
    "financial_indicator_events", ("security_id", "endpoint", "report_period"), {
        "security_id": _f("string", required=True), "endpoint": _f("string", required=True),
        "report_period": _f("date", required=True),
        "announcement_date": _f("date", required=True),
        "actual_announcement_date": _f("date", required=True),
        "roe": _f("float64", unit="percent"),
        "weighted_roe": _f("float64", unit="percent"),
        "debt_to_assets": _f("float64", unit="percent"),
    })
CONTRACTS["fina_indicator_vip"] = CONTRACTS["fina_indicator"]

# A changed financial contract requires explicit rebuilding, never migration
# of old snapshots. The existing Reader statuses cover ambiguous source cells.
for _endpoint in ("income", "balancesheet", "cashflow", "fina_indicator"):
    _financial_contract = CONTRACTS[_endpoint]
    _financial_contract["contract_id"] = _financial_contract["contract_id"].removesuffix("v1") + "v2"
    _financial_fields = _financial_contract["fields"]
    _financial_fields["announcement_date"]["nullable"] = True
    _financial_fields["source_issue"] = _f("string")
    if _endpoint != "fina_indicator":
        _financial_fields.update({name: _f("string") for name in
                                  ("company_type", "report_end_type", "update_flag")})
    for _name, _spec in list(_financial_fields.items()):
        if _spec.get("dtype") == "float64" or _name in {
                "announcement_date", "company_type", "report_end_type", "update_flag"}:
            _spec["status_field"] = f"{_name}__status"
            _financial_fields[f"{_name}__status"] = _f("string")

# Dividend ambiguity concerns the identity of the entire economic action.
# Keep its native key; implementation/ex dates can themselves be corrected.
_dividend_contract = CONTRACTS["dividend"]
_dividend_contract["contract_id"] = "local.corporate_actions.tushare.v2"
_dividend_contract["fields"].update(source_issue=_f("string"), source_candidate_count=_f("int64"),
                                    candidate_economic_dates=_f("string"))
for _name, _spec in list(_dividend_contract["fields"].items()):
    if _name not in set(_dividend_contract["logical_key"]) | set(_META) | {
            "source_issue", "source_candidate_count", "candidate_economic_dates"}:
        _spec["status_field"] = f"{_name}__status"
        _dividend_contract["fields"][f"{_name}__status"] = _f("string")


DIVIDEND_ECONOMIC_CONTRACT = deepcopy(CONTRACTS["dividend"])
DIVIDEND_ECONOMIC_CONTRACT["contract_id"] = "local.corporate_actions.tushare.v3"
for _name, _spec in {
        "stock_distribution_shares_per_share": _f("float64", unit="shares/share"),
        "payment_date": _f("date"), "stock_listing_date": _f("date"),
        "economic_event_id": _f("string")}.items():
    _spec["status_field"] = f"{_name}__status"
    DIVIDEND_ECONOMIC_CONTRACT["fields"][_name] = _spec
    DIVIDEND_ECONOMIC_CONTRACT["fields"][f"{_name}__status"] = _f("string")


def _action_rules(value, identities):
    """Freeze explicit source-relative rounds; dates/amounts never generate IDs."""
    if not isinstance(value, Mapping) or value.get("rule") != "declared_distribution_round_v1" or (
            value.get("alias_rule") != "identical_complete_terms_v1"):
        raise DataError("economic actions require declared round and strict alias rules")
    native = value.get("native_key_field")
    if native is not None and (not isinstance(native, str) or not native or
                               native in (*_DIVIDEND_V3_FIELDS, "cash_div", "base_date", "base_share")):
        raise DataError("native economic key must be an explicitly declared opaque source field")
    rounds = value.get("rounds")
    if not isinstance(rounds, (list, tuple)):
        raise DataError("economic action rounds must be explicit native disclosure bindings")
    frozen, seen = [], set()
    for item in rounds:
        if not isinstance(item, Mapping) or set(item) != set(_DIVIDEND_KEY) | {"round"}:
            raise DataError("each round binds the complete native dividend key")
        key = tuple(item[field] for field in _DIVIDEND_KEY)
        if any(not isinstance(v, str) for v in key) or key[0] not in identities or key[3] != "实施" or key in seen:
            raise DataError("economic round needs one bound implemented native key")
        try:
            for day in key[1:3]:
                if not isinstance(day, str) or len(day) != 8:
                    raise ValueError
                datetime.strptime(day, "%Y%m%d")
        except (TypeError, ValueError) as exc:
            raise DataError("economic round keys require YYYYMMDD dates") from exc
        if not isinstance(item["round"], str) or not item["round"].strip():
            raise DataError("distribution round must be a stable nonempty source-relative name")
        seen.add(key)
        frozen.append(dict(item))
    return {"rule": value["rule"], "alias_rule": value["alias_rule"], "native_key_field": native,
            "rounds": sorted(frozen, key=lambda item: tuple(item[f] for f in _DIVIDEND_KEY))}


def event_source_profile(endpoint: str, *, identity_map: Mapping[str, str],
                         next_open_session_by_date: Mapping[str, str] | None = None,
                         corporate_action_rules: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Freeze mappings, availability assumptions and optional dividend v3 rules.

    corporate_action_rules declares a versioned native-key-to-round map and
    strict alias rule. No rounds are inferred from report periods, amounts or
    mutable dates. An opaque native key already present in Raw takes priority.
    Caller mappings are copied; this helper performs no I/O or source calls.
    Omitting the rules retains the existing dividend v2 contract and profile.
    """
    if endpoint not in _ENDPOINTS:
        raise DataError("unsupported event source endpoint")
    if (not isinstance(identity_map, Mapping) or not identity_map or
            any(not isinstance(k, str) or not _SYMBOL.fullmatch(k) or
                not isinstance(v, str) or not v for k, v in identity_map.items()) or
            len(set(identity_map.values())) != len(identity_map)):
        raise DataError("event source needs stable explicit identity_map")
    canonical_endpoint = endpoint.removesuffix("_vip")
    profile: dict[str, Any] = {
        "id": f"tushare.local.{canonical_endpoint}.{'v2' if endpoint in _FINANCIAL or endpoint == 'dividend' else 'v1'}", "endpoint": canonical_endpoint,
        "identity_map": dict(identity_map), "revision_order": (
            "announcement_day_then_terminal_v1" if canonical_endpoint in {
                "income", "balancesheet", "cashflow", "fina_indicator", "top10_holders"}
            else "terminal_observation_v1"),
        "revision_capability": ("supplier announcement day distinguishes disclosed versions; day is a best-effort availability assumption, not a historical public timestamp"
                                if canonical_endpoint in {"income", "balancesheet", "cashflow", "fina_indicator", "top10_holders"} else
                                "terminal observation; no vendor revision order or public timestamp"),
        "raw_serialization": "json_source_records_or_sdk_table_v1",
        "response_limit": _CAPS[endpoint],
        "coverage_claim": "observed response only; supplier completeness unverified",
        "missing_row": "unknown",
        "field_map": {}, "date_formats": {},
    }
    fm = profile["field_map"]
    date_fields = profile["date_formats"]
    if corporate_action_rules is not None:
        if endpoint != "dividend":
            raise DataError("economic action rules apply only to dividend")
        profile["economic_identity"] = _action_rules(corporate_action_rules, identity_map)
        profile["id"] = "tushare.local.dividend.v3"
    if endpoint != "stk_limit":
        if not isinstance(next_open_session_by_date, Mapping) or not next_open_session_by_date:
            raise DataError("event best-effort policy needs an explicit next-open calendar map")
        calendar = dict(next_open_session_by_date)
        for source_day, next_day in calendar.items():
            try:
                first, second = date.fromisoformat(source_day), date.fromisoformat(next_day)
            except (TypeError, ValueError) as exc:
                raise DataError("next-open calendar requires ISO dates") from exc
            if second <= first:
                raise DataError("next-open session must follow source date")
        profile["availability"] = {
            "basis": "best_effort_next_open_assumption; not publication evidence",
            "date_field": ("actual_announcement_date" if canonical_endpoint in {
                "income", "balancesheet", "cashflow", "top10_holders"} else "announcement_date"),
            "date_rule": "next_open", "timezone": "Asia/Shanghai",
            "session_release_time": "09:30:00",
            "next_open_session_by_date": calendar,
        }
    else:
        profile["availability"] = {
            "basis": "best_effort_same_session_assumption; not publication evidence",
            "timezone": "Asia/Shanghai", "session_release_time": "10:00:00"}
    if endpoint in {"income", "income_vip"}:
        fm.update(security_id="ts_code", endpoint="__endpoint", report_type="report_type",
                  report_period="end_date", announcement_date="ann_date",
                  actual_announcement_date="f_ann_date", basis="report_type",
                  total_revenue="total_revenue", parent_net_income="n_income_attr_p")
        profile["value_maps"] = {"basis": {"1": "cumulative_ytd"}}
        profile["source_units"] = {"total_revenue": "CNY", "n_income_attr_p": "CNY"}
        profile["unit_evidence"] = {
            "issuer_report": "https://news.spdb.com.cn/investor_relation/periodic_report/202503/P020250328684047385359.pdf",
            "supplier_document": "https://tushare.pro/document/2?doc_id=33",
            "checked_pair": "600000.SH/20241231: supplier 170748000000 CNY revenue, 45257000000 CNY parent net income; issuer 170748/45257 RMB million"}
        date_fields.update(report_period="YYYYMMDD", announcement_date="YYYYMMDD",
                           actual_announcement_date="YYYYMMDD")
    elif canonical_endpoint == "balancesheet":
        fm.update(security_id="ts_code", endpoint="__endpoint", report_type="report_type",
                  report_period="end_date", announcement_date="ann_date",
                  actual_announcement_date="f_ann_date", total_assets="total_assets",
                  total_liabilities="total_liab", parent_equity="total_hldr_eqy_exc_min_int")
        profile["source_units"] = {name: "CNY" for name in
                                   ("total_assets", "total_liab", "total_hldr_eqy_exc_min_int")}
        profile["unit_evidence"] = {
            "issuer_report": "https://news.spdb.com.cn/investor_relation/periodic_report/202503/P020250328684047385359.pdf",
            "supplier_document": "https://tushare.pro/document/2?doc_id=36",
            "checked_pair": "600000.SH/20241231: supplier total_assets 9461880000000 CNY; issuer 9461880 RMB million"}
        date_fields.update(report_period="YYYYMMDD", announcement_date="YYYYMMDD",
                            actual_announcement_date="YYYYMMDD")
    elif canonical_endpoint == "cashflow":
        fm.update(security_id="ts_code", endpoint="__endpoint", report_type="report_type",
                  report_period="end_date", announcement_date="ann_date",
                  actual_announcement_date="f_ann_date", operating_cash_flow="n_cashflow_act",
                  investing_cash_flow="n_cashflow_inv_act",
                  financing_cash_flow="n_cash_flows_fnc_act")
        profile["source_units"] = {name: "CNY" for name in
                                   ("n_cashflow_act", "n_cashflow_inv_act", "n_cash_flows_fnc_act")}
        profile["unit_evidence"] = {
            "issuer_report": "https://news.spdb.com.cn/investor_relation/periodic_report/202503/P020250328684047385359.pdf",
            "supplier_document": "https://tushare.pro/document/2?doc_id=44",
            "checked_pair": "600000.SH/20241231: supplier operating cash flow -333654000000 CNY; issuer -333654 RMB million"}
        date_fields.update(report_period="YYYYMMDD", announcement_date="YYYYMMDD",
                            actual_announcement_date="YYYYMMDD")
    elif canonical_endpoint == "fina_indicator":
        fm.update(security_id="ts_code", endpoint="__endpoint", report_period="end_date",
                  announcement_date="ann_date", actual_announcement_date="ann_date",
                  roe="roe", weighted_roe="roe_waa", debt_to_assets="debt_to_assets")
        profile["source_units"] = {name: "percent" for name in
                                   ("roe", "roe_waa", "debt_to_assets")}
        profile["unit_evidence"] = {
            "supplier_document": "https://tushare.pro/document/2?doc_id=79",
            "checked_pair": "600000.SH/20241231: roe_waa 6.28 percent; liabilities/assets 92.1286 percent"}
        date_fields.update(report_period="YYYYMMDD", announcement_date="YYYYMMDD",
                            actual_announcement_date="YYYYMMDD")
    elif endpoint == "dividend":
        fm.update(security_id="ts_code", report_period="end_date",
                  announcement_date="ann_date", process_status="div_proc",
                  cash_dividend_before_tax_per_share="cash_div_tax",
                  bonus_shares_per_share="stk_bo_rate",
                  capital_transfer_shares_per_share="stk_co_rate",
                  implementation_announcement_date="imp_ann_date",
                  record_date="record_date", ex_date="ex_date")
        profile["source_units"] = {"cash_div_tax": "CNY/share",
                                   "stk_bo_rate": "shares/share", "stk_co_rate": "shares/share"}
        date_fields.update(report_period="YYYYMMDD", announcement_date="YYYYMMDD",
                           implementation_announcement_date="YYYYMMDD",
                           record_date="YYYYMMDD", ex_date="YYYYMMDD")
        profile["null_values"] = {"implementation_announcement_date": [""],
                                  "record_date": [""], "ex_date": [""]}
        profile["action_semantics"] = "process_status is native; only explicit 实施 means implemented"
        fm.update(source_issue="__source_issue", source_candidate_count="__source_candidate_count",
                  candidate_economic_dates="__candidate_economic_dates")
        for name, spec in CONTRACTS[endpoint]["fields"].items():
            if status_field := spec.get("status_field"):
                fm[status_field] = f"__status__{fm[name]}"
        profile["same_observation_policy"] = (
            "distinct returned rows under one native action key make the entire action "
            "unavailable; no stable supplier action ID or revision order was captured; "
            "dates and amounts are source_missing, including unanimous zero amounts")
        if corporate_action_rules is not None:
            fm.update(stock_distribution_shares_per_share="stk_div", payment_date="pay_date",
                      stock_listing_date="div_listdate", economic_event_id="__economic_event_id")
            profile["source_units"]["stk_div"] = "shares/share"
            date_fields.update(payment_date="YYYYMMDD", stock_listing_date="YYYYMMDD")
            profile["null_values"].update(payment_date=[""], stock_listing_date=[""])
            for name in ("stock_distribution_shares_per_share", "payment_date", "stock_listing_date", "economic_event_id"):
                fm[f"{name}__status"] = f"__status__{fm[name]}"
            profile["availability"]["assumption_id"] = "tushare.current_implemented_terms_from_later_notice_next_open.v1"
            profile["economic_identity"]["native_key_limitation"] = (
                "Tushare dividend publishes no stable economic action ID; native_key_field is used only when an opaque key is explicitly retained in Raw."
            )
    elif endpoint == "top10_holders":
        fm.update(security_id="ts_code", report_period="end_date",
                  announcement_date="ann_date", actual_announcement_date="ann_date",
                  holders="__holders_json", holder_count="__holder_count",
                  group_completeness="__group_completeness")
        fm.update(group_issue="__group_issue", top10_ratio="__top10_ratio")
        profile["source_units"] = {"hold_amount": "shares",
                                   "hold_ratio": "percent of total shares",
                                   "__top10_ratio": "percent of total shares"}
        date_fields.update(report_period="YYYYMMDD", announcement_date="YYYYMMDD",
                            actual_announcement_date="YYYYMMDD")
        profile["group_completeness"] = (
            "complete only when one untruncated supplier response has exactly ten distinct "
            "holders with amount and ratio; source-relative, not independently verified")
    else:
        fm.update(security_id="ts_code", session="trade_date",
                  up_limit="up_limit", down_limit="down_limit")
        profile["source_units"] = {"up_limit": "CNY/share", "down_limit": "CNY/share"}
        date_fields["session"] = "YYYYMMDD"
        profile["execution_semantics"] = "price bounds only; not proof of executable liquidity"
    if endpoint in _FINANCIAL:
        fm["source_issue"] = "__source_issue"
        if endpoint in _STATEMENTS:
            fm.update(company_type="comp_type", report_end_type="end_type", update_flag="update_flag")
        for name, spec in CONTRACTS[endpoint]["fields"].items():
            if status_field := spec.get("status_field"):
                fm[status_field] = f"__status__{fm[name]}"
        profile["same_disclosure_policy"] = (
            "returned dominating row, otherwise consensus cells only; differing cells are "
            "source_missing/ambiguous_same_disclosure; differing company/period context "
            "makes numeric cells unavailable; update_flag never orders revisions")
    return profile


def _validate_params(endpoint: str, params: Mapping[str, str], identity_map: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(params, Mapping):
        raise DataError("event source request requires parameter mapping")
    request = dict(params)
    if "ts_code" in request and request["ts_code"] not in identity_map:
        raise DataError("event source request has unmapped security")
    if "ts_code" not in request and not (
        endpoint.endswith("_vip") and endpoint in _FINANCIAL and
        ("period" in request or "start_date" in request and "end_date" in request) or
        endpoint == "dividend" and "ann_date" in request or
        endpoint == "stk_limit" and "trade_date" in request):
        raise DataError("event request needs a security or an exact all-market date")
    allowed = ({"ts_code", "start_date", "end_date", "period", "report_type"} if endpoint in _STATEMENTS else
               {"ts_code", "start_date", "end_date", "period"} if endpoint in _INDICATORS else
               {"ts_code", "ann_date", "record_date", "ex_date", "imp_ann_date"} if endpoint == "dividend" else
               {"ts_code", "start_date", "end_date", "period", "ann_date"} if endpoint == "top10_holders" else
               {"ts_code", "trade_date", "start_date", "end_date"})
    if set(request) - allowed or any(not isinstance(v, str) or len(v) > 64 for v in request.values()):
        raise DataError("unsupported or unbounded event source params")
    if endpoint in _STATEMENTS and request.get("report_type") != "1":
        raise DataError("statement adapter only supports report_type=1 consolidated reports")
    if endpoint in _FINANCIAL and not ("period" in request or "start_date" in request and "end_date" in request):
        raise DataError("financial source requires period or bounded date range")
    if endpoint == "top10_holders" and not ("period" in request or "start_date" in request and "end_date" in request):
        raise DataError("holders require period or bounded report-period range")
    if endpoint == "stk_limit" and not ("trade_date" in request or "start_date" in request and "end_date" in request):
        raise DataError("stk_limit requires trade_date or bounded session range")
    for key, value in request.items():
        if key in {"start_date", "end_date", "period", "ann_date", "record_date", "ex_date", "imp_ann_date", "trade_date"}:
            if not re.fullmatch(r"[0-9]{8}", value):
                raise DataError(f"{key} requires YYYYMMDD")
            try:
                datetime.strptime(value, "%Y%m%d")
            except ValueError as exc:
                raise DataError(f"{key} is not a date") from exc
    if request.get("start_date", "") > request.get("end_date", "99999999"):
        raise DataError("reversed event source range")
    return request


def _source_rows(response: Any) -> tuple[bytes, list[dict[str, Any]] | None]:
    if isinstance(response, (list, Mapping)):
        source = response
    else:
        fields, rows = _response_table(response)
        source = {"fields": fields, "rows": rows}
    try:
        payload = json.dumps(source, ensure_ascii=False, separators=(",", ":"),
                             allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise DataError("supplier response is not strict JSON") from exc
    try:
        fields, rows = _response_table(response)
    except DataError:
        return payload, None
    return payload, [dict(zip(fields, row)) for row in rows]


def _financial_unique(endpoint: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Resolve one disclosure without imposing an order on its returned rows.

    Keep a returned row covering all partial duplicates when one exists.
    Otherwise retain only consensus cells, mark differing cells unavailable,
    and leave all original rows in Raw. Complementary partial rows are never
    combined into a complete invented report. No dates or flags order rows.
    """
    by_key: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    fields = request_fields(endpoint)
    for row in rows:
        actual = row["f_ann_date"] if endpoint in _STATEMENTS else row["ann_date"]
        key = row["ts_code"], row.get("report_type", ""), row["end_date"], actual
        by_key.setdefault(key, []).append(row)
    resolved = []
    numeric_fields = _FIELDS[endpoint][5:] if endpoint in _STATEMENTS else _FIELDS[endpoint][3:]
    for group in by_key.values():
        # The richest row is the only possible dominating candidate, up to
        # equal duplicates. Checking the complete group avoids input-order bugs.
        candidate = max(group, key=lambda item: sum(item.get(f) is not None for f in fields))
        if all(item.get(f) is None or item.get(f) == candidate.get(f)
               for item in group for f in fields):
            resolved.append(dict(candidate))
            continue
        row = {f: group[0].get(f) if all(item.get(f) == group[0].get(f) for item in group) else None
               for f in fields}
        ambiguous = {f for f in fields if any(item.get(f) != group[0].get(f) for item in group)}
        if endpoint in _STATEMENTS and ambiguous & {"comp_type", "end_type"}:
            ambiguous.update(numeric_fields)
        for field in ambiguous:
            row[field] = None
            row[f"__status__{field}"] = "source_missing"
        row["__source_issue"] = "ambiguous_same_disclosure"
        resolved.append(row)
    return resolved


def _response_issue(endpoint: str, rows: list[dict[str, Any]] | None,
                    params: Mapping[str, str], profile: Mapping[str, Any]) -> str | None:
    if rows is None:
        return "malformed supplier table"
    if _CAPS[endpoint] is not None and len(rows) >= _CAPS[endpoint]:
        return "supplier response reached declared/local row safeguard"
    for row in rows:
        required = _DIVIDEND_V3_FIELDS if endpoint == "dividend" and profile.get("economic_identity") else _FIELDS[endpoint]
        if any(field not in row for field in required):
            return "supplier response lacks a requested field"
        if params.get("ts_code") and row["ts_code"] != params["ts_code"]:
            return "supplier response contains another security"
        if endpoint in _STATEMENTS:
            if row["report_type"] != "1" or not row["f_ann_date"] or not row["ann_date"]:
                return "statement report type or actual announcement date is unsupported"
            # Income/balance/cashflow start/end select announcement dates.
            axis = row["ann_date"]
        elif endpoint in _INDICATORS:
            if not row["ann_date"]:
                return "indicator lacks announcement date"
            # fina_indicator start/end select report periods, not announcements.
            axis = row["end_date"]
        elif endpoint == "top10_holders":
            axis = row["end_date"]
        elif endpoint == "stk_limit":
            axis = row["trade_date"]
        else:
            axis = None
        if axis is not None and not isinstance(axis, str):
            return "supplier response has invalid date scope field"
        if axis is not None and (axis < params.get("start_date", "00000000") or
                                 axis > params.get("end_date", "99999999") or
                                 ("period" in params and row.get("end_date") != params["period"]) or
                                 ("trade_date" in params and axis != params["trade_date"])):
            return "supplier response lies outside requested date scope"
        if endpoint == "dividend" and any(row.get(field) != value for field, value in params.items()
                                            if field in {"ann_date", "record_date", "ex_date", "imp_ann_date"}):
            return "dividend response lies outside selected date"
        if endpoint in _FINANCIAL | {"dividend", "top10_holders"}:
            date_field = profile["availability"]["date_field"]
            source_field = ("f_ann_date" if endpoint in _STATEMENTS else "ann_date")
            source_day = row.get(source_field)
            try:
                iso = datetime.strptime(source_day, "%Y%m%d").date().isoformat()
            except (TypeError, ValueError):
                return "event lacks a valid announcement date"
            if iso not in profile["availability"]["next_open_session_by_date"]:
                return "event date lacks explicit next-open calendar rule"
    if endpoint in _FINANCIAL:
        try:
            _financial_unique(endpoint, rows)
        except DataError as exc:
            return str(exc)
    return None


def _dividend_unique(rows: list[dict[str, Any]], *, source_fields=None) -> list[dict[str, Any]]:
    """Deduplicate exact source actions; never compose or order ambiguous ones.

    A differing implementation date, ex date or amount does not establish a
    different action or a later revision. Preserve the native key and Raw group,
    but mark every economic date/amount unavailable for that observation.
    """
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    source_fields = _FIELDS["dividend"] if source_fields is None else source_fields
    values = tuple(field for field in source_fields if field not in _DIVIDEND_KEY)
    dates_to_keep = tuple(field for field in source_fields if field in _DIVIDEND_DATES)
    for row in rows:
        native = {field: row.get(field) for field in source_fields}
        for field in dates_to_keep:
            if native[field] == "":
                native[field] = None
        group = groups.setdefault(tuple(native[field] for field in _DIVIDEND_KEY), [])
        if native not in group:
            group.append(native)
    result = []
    for group in groups.values():
        row = dict(group[0])
        row["__source_candidate_count"] = len(group)
        row["__source_issue"] = "ambiguous_action_identity_or_revision" if len(group) > 1 else None
        row["__candidate_economic_dates"] = None
        if len(group) > 1:
            from .sources import _typed
            for item in group:
                for field in values:
                    # Whole-action ambiguity does not erase conversion errors
                    # that ordinary source normalization would reject.
                    _typed(item[field], 'date' if field in _DIVIDEND_DATES
                           else 'float64', field, 'YYYYMMDD')
            # This summary is part of terminal content. A changed candidate
            # date set must create a new observation even when all action
            # values are unavailable and the candidate count stays the same.
            dates = {field: sorted({item[field] for item in group}, key=lambda value: value or '')
                     for field in dates_to_keep}
            row["__candidate_economic_dates"] = json.dumps(dates, sort_keys=True, separators=(',', ':'))
            for field in values:
                row[field] = None
                row[f"__status__{field}"] = "source_missing"
        result.append(row)
    return result


def _action_identity_id(material):
    prefix = "ca:native:" if material[2] == "native" else "ca:round:"
    return prefix + sha256(json.dumps(material, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def _dividend_identity(rows, originals, profile):
    """Assign IDs from opaque vendor keys or frozen rounds, retaining conflicts."""
    rules = profile["economic_identity"]
    bindings = {tuple(item[f] for f in _DIVIDEND_KEY): item["round"] for item in rules["rounds"]}
    original_keys = {}
    for item in originals:
        original_keys.setdefault(tuple(item.get(f) for f in _DIVIDEND_KEY), []).append(item)
    groups = {}
    for row in rows:
        row["__economic_event_id"] = None
        row["__status____economic_event_id"] = "not_provided"
        if row["div_proc"] != "实施":
            continue
        key = tuple(row[f] for f in _DIVIDEND_KEY)
        native_field = rules["native_key_field"]
        if native_field and any(item.get(native_field) is not None and type(item.get(native_field)) not in (str, int)
                                for item in original_keys[key]):
            raise DataError("native economic key must be an opaque string or integer")
        native_values = {item.get(native_field) for item in original_keys[key]} if native_field else set()
        native_value = next(iter(native_values)) if len(native_values) == 1 else None
        if native_field and len(native_values) > 1:
            material = None
        elif isinstance(native_value, (str, int)) and type(native_value) is not bool and str(native_value):
            material = ["tushare.dividend", profile["identity_map"][key[0]], "native", str(native_value)]
        elif key in bindings:
            material = ["tushare.dividend", profile["identity_map"][key[0]], key[1], "round", bindings[key]]
        else:
            material = None
        if material is None or row.get("__source_issue"):
            row["__status____economic_event_id"] = "source_missing"
            row["__source_issue"] = row.get("__source_issue") or "economic_round_not_declared_or_native_key_ambiguous"
            continue
        row["__economic_event_id"] = _action_identity_id(material)
        row["__status____economic_event_id"] = "value"
        groups.setdefault(row["__economic_event_id"], []).append(row)
    terms = tuple(f for f in _DIVIDEND_V3_FIELDS if f not in {"ts_code", "ann_date"})
    for group in groups.values():
        if len(group) == 1:
            continue
        complete = all(item["stk_div"] is not None and item["cash_div_tax"] is not None
                       and all(item[f] is not None for f in ("imp_ann_date", "record_date", "ex_date"))
                       and (item["cash_div_tax"] == 0 or item["pay_date"] is not None)
                       and (item["stk_div"] == 0 or item["div_listdate"] is not None) for item in group)
        if group[0]["__economic_event_id"].startswith("ca:native:"):
            # The vendor key establishes identity, but the phase Reader must
            # still check all cutoff-visible terms before applying any action.
            continue
        if not complete or any(any(item[f] != group[0][f] for f in terms) for item in group):
            for item in group:
                item["__economic_event_id"] = None
                item["__status____economic_event_id"] = "source_missing"
                item["__source_issue"] = "ambiguous_economic_alias_terms_or_dates"
    return rows


def _native_action_scope(value, identities, selected):
    """Bind an explicit complete Native-key list to exact source identities."""
    fields = ("security_id", "report_period", "announcement_date", "process_status")
    codes = {identities[code]: code for code in selected}
    if not isinstance(value, list) or not value:
        raise DataError("canonical_event_keys needs a nonempty complete Native-key list")
    result = set()
    for item in value:
        if not isinstance(item, Mapping) or set(item) != set(fields):
            raise DataError("canonical_event_keys requires exactly the complete Native identity")
        key = tuple(item[f] for f in fields)
        if any(not isinstance(v, str) or not v for v in key) or key[0] not in codes:
            raise DataError("canonical_event_keys requires a selected stable security identity")
        try:
            if any(date.fromisoformat(day).isoformat() != day for day in key[1:3]):
                raise ValueError
        except ValueError as exc:
            raise DataError("canonical_event_keys requires canonical ISO dates") from exc
        source = (codes[key[0]], key[1].replace("-", ""), key[2].replace("-", ""), key[3])
        if source in result:
            raise DataError("canonical_event_keys contains a duplicate complete identity")
        result.add(source)
    return frozenset(result)


def prepare_event_rows(batch: Any) -> list[dict[str, Any]]:
    """Validate full saved supplier rows and select the frozen canonical scope.

    The original all-market response stays byte-for-byte in Raw. Only rows for
    explicitly bound securities reach Canonical. Dividend v3 requests may
    additionally freeze canonical_event_keys as dictionaries containing exactly
    security_id/report_period/announcement_date/process_status, using ISO dates.
    This static complete identity selection retains every candidate of a chosen
    key, performs no receipt/cutoff/account filtering, and never rewrites Raw.
    Omitting it retains the original scope. Actual announcement dates identify
    financial disclosure days; they are not public timestamps.
    """
    from .sources import _rows

    request = batch.request
    endpoint = request.get("endpoint")
    if endpoint not in _ENDPOINTS:
        raise DataError("unsupported saved event endpoint")
    params = _validate_params(endpoint, request.get("params", {}),
                              batch.source_profile.get("identity_map", {}))
    selected = request.get("canonical_symbols")
    identities = batch.source_profile.get("identity_map", {})
    if selected is None and endpoint == "dividend" and batch.source_profile.get("economic_identity"):
        # An explicit v3 rebuild can interpret an existing one-security Raw
        # receipt without rewriting its original request or source profile.
        selected = [params["ts_code"]] if "ts_code" in params else None
    if (not isinstance(selected, list) or not selected or
            any(code not in identities for code in selected)):
        raise DataError("event Raw needs explicit canonical_symbols")
    chosen = set(selected)
    decoded = _rows(batch.payload)
    required = _DIVIDEND_V3_FIELDS if endpoint == "dividend" and batch.source_profile.get("economic_identity") else _FIELDS[endpoint]
    if any(any(field not in row for field in required) for row in decoded):
        raise DataError("event Raw lacks requested source fields")
    if params.get("ts_code") and any(row["ts_code"] != params["ts_code"] for row in decoded):
        raise DataError("event Raw contains another requested security")
    rows = [dict(row) for row in decoded if row["ts_code"] in chosen]
    issue = _response_issue(endpoint, rows, params, batch.source_profile)
    if issue:
        raise DataError(issue)
    if "canonical_event_keys" in request:
        if endpoint != "dividend" or not batch.source_profile.get("economic_identity"):
            raise DataError("complete Native-key selection requires explicit dividend v3 rules")
        scope = _native_action_scope(request["canonical_event_keys"], identities, selected)
        rows = [row for row in rows if tuple(row[f] for f in _DIVIDEND_KEY) in scope]
    if endpoint in _FINANCIAL:
        unique = _financial_unique(endpoint, rows)
        for row in unique:
            row["__endpoint"] = endpoint.removesuffix("_vip")
        return unique
    if endpoint == "dividend":
        actions = _dividend_unique(rows, source_fields=required)
        return _dividend_identity(actions, rows, batch.source_profile) if batch.source_profile.get("economic_identity") else actions
    if endpoint == "top10_holders":
        groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        for row in rows:
            groups.setdefault((row["ts_code"], row["end_date"], row["ann_date"]), []).append(row)
        reports: list[dict[str, Any]] = []
        for group in groups.values():
            names = [row["holder_name"] for row in group]
            if len(set(names)) != len(names):
                flag = "ambiguous_duplicate_holder"
                issue = "duplicate_holder"
            elif len(group) == 10 and all(
                    isinstance(row["holder_name"], str) and row["holder_name"] and
                    type(row["hold_amount"]) in (int, float) and
                    math.isfinite(row["hold_amount"]) and
                    type(row["hold_ratio"]) in (int, float) and
                    math.isfinite(row["hold_ratio"]) for row in group):
                flag = "supplier_report_complete"
                issue = None
            else:
                flag = "partial_supplier_report"
                issue = "row_count_or_missing_fields"
            listed = [{"holder_name": row["holder_name"],
                       "hold_amount_shares": row["hold_amount"],
                       "hold_ratio_percent": row["hold_ratio"]}
                      for row in sorted(group, key=lambda item: str(item["holder_name"]))]
            first = group[0]
            reports.append({"ts_code": first["ts_code"], "end_date": first["end_date"],
                            "ann_date": first["ann_date"],
                            "__holders_json": json.dumps(listed, ensure_ascii=False,
                                                        sort_keys=True, separators=(",", ":"),
                                                        allow_nan=False),
                            "__holder_count": len(group),
                            "__group_completeness": flag,
                            "__group_issue": issue,
                            "__top10_ratio": (sum(row["hold_ratio"] for row in group)
                                              if issue is None else None)})
        return reports
    return rows


def collect_event_response(store: LocalStore, *, client: Any, endpoint: str,
                           params: Mapping[str, str], identity_map: Mapping[str, str],
                           operation_id: str, observed_at: datetime | None = None,
                           batch_index: int = 0,
                           next_open_session_by_date: Mapping[str, str] | None = None,
                           corporate_action_rules: Mapping[str, Any] | None = None,
                           clock: Callable[[], datetime] | None = None) -> dict[str, Any]:
    """Fetch one bounded response, append Raw, and return its successful record.

    The request is idempotent by operation ID and batch index: a saved success
    or empty response is reused without another network call. Supplier errors,
    malformed responses and cap hits are saved as failed/cap Raw and raised,
    never recast as empty. A failed slot needs a new batch index to retry.
    ``clock`` is sampled after the response; ``observed_at`` is an explicit
    replay/test override and must not be a pre-request timestamp in live runs.
    """
    if not isinstance(store, LocalStore) or endpoint not in _ENDPOINTS:
        raise DataError("LocalStore and supported endpoint required")
    profile = event_source_profile(endpoint, identity_map=identity_map,
                                   next_open_session_by_date=next_open_session_by_date,
                                   corporate_action_rules=corporate_action_rules)
    selected = _validate_params(endpoint, params, identity_map)
    fields = request_fields(endpoint, corporate_action_rules=corporate_action_rules)
    request = {"endpoint": endpoint, "params": selected, "fields": list(fields),
               "coverage_status": "observed_response_only",
               "canonical_symbols": [selected["ts_code"]] if "ts_code" in selected else sorted(identity_map)}
    previous = store.find_raw_by_operation(operation_id).get(batch_index)
    if previous is not None:
        if previous.get("request") != request or previous.get("source_profile") != profile:
            raise ConflictError("saved event source request differs from retry")
        if previous.get("status") in {"success", "empty"}:
            return previous
        raise CoverageError("saved event source attempt is failed or capped; use a new batch index")
    query = getattr(client, "query", None)
    if not callable(query):
        raise DataError("injected client requires query(endpoint, fields, **params)")
    try:
        response = query(endpoint, fields=",".join(fields), **selected)
        payload, rows = _source_rows(response)
        issue = _response_issue(endpoint, rows, selected, profile)
        status = "cap" if rows is not None and _CAPS[endpoint] is not None and len(rows) >= _CAPS[endpoint] else (
            "failed" if issue else "empty" if not rows else "success")
    except Exception as exc:
        # Vendor errors may contain credentials. The type/phase is enough to
        # retain evidence without persisting secret-bearing exception text.
        payload = _safe_failure(exc, "fetch_or_serialize")
        issue, status = "supplier query or serialization failed", "failed"
    receipt_time = observed_at if observed_at is not None else (clock or (lambda: datetime.now(timezone.utc)))()
    if not isinstance(receipt_time, datetime) or receipt_time.tzinfo is None or receipt_time.utcoffset() is None:
        raise DataError("receipt clock must return a timezone-aware datetime")
    raw = store.write_raw(payload, domain=_DOMAINS[endpoint], request=request,
                          source_profile=profile, contract=DIVIDEND_ECONOMIC_CONTRACT if corporate_action_rules is not None else CONTRACTS[endpoint],
                          observed_at=receipt_time, normalizer="event_records_v1", status=status,
                          operation_id=operation_id, batch_index=batch_index)
    if issue:
        raise CoverageError(f"{endpoint}: {issue}; Raw {raw['batch_id']} retained")
    return raw
