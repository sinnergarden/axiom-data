"""Reference-scope planning and bounded historical member observations.

Planning and validation helpers are pure. ``run_reference_bootstrap`` makes only
its declared supplier calls and saves Raw before validation; it publishes no
Snapshot. A listing snapshot is today's terminal catalogue; index_weight is
monthly dated evidence, not an adjustment-event feed or continuous historical
membership authority.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
import re
import time
from typing import Any

from .protocols import ConflictError, CoverageError, DataError
from .provider_local import CAPS, response_bytes
from .sources import _rows
from .storage import LocalStore, _clean_name, _json_bytes


LISTING_STATUSES = ("L", "D", "P")
EXCHANGES = ("SSE", "SZSE")
LISTING_FIELDS = ("ts_code", "exchange", "list_status", "list_date", "delist_date")
WEIGHT_FIELDS = ("index_code", "con_code", "trade_date", "weight")
LISTING_CAP = 6000
CSI1800_SOURCE_INDEX_CODES = ("000300.SH", "000905.SH", "000852.SH")
CSI1800_NAMES = {"000300.SH": "沪深300", "000905.SH": "中证500",
                 "000852.SH": "中证1000"}
_SYMBOL = re.compile(r"^[0-9]{6}\.(SH|SZ)$")
_VENDOR_ALIAS = re.compile(r"^T[0-9]{6}\.(SH|SZ)$")
_INDEX = re.compile(r"^[0-9]{6}\.(SH|SZ|CSI)$")


def _day(value: str) -> date:
    try:
        if not isinstance(value, str):
            raise ValueError
        return date.fromisoformat(value)
    except ValueError as exc:
        raise DataError(f"invalid ISO date {value!r}") from exc


def _source_day(value: Any, field: str) -> date:
    try:
        return datetime.strptime(value, "%Y%m%d").date()
    except (TypeError, ValueError) as exc:
        raise DataError(f"invalid {field} source date") from exc


def plan_full_listing_requests() -> tuple[dict[str, Any], ...]:
    """Return six explicit current stock_basic selectors for SSE/SZSE L/D/P.

    Each request is a single response. The caller must store every Raw result,
    then call :func:`listing_identity_candidates` before using the result as a
    full terminal catalogue. No historical status at an earlier date is implied.
    """
    return tuple({"endpoint": "stock_basic", "params": {"exchange": exchange,
                    "list_status": status}, "fields": LISTING_FIELDS}
                 for exchange in EXCHANGES for status in LISTING_STATUSES)


def listing_identity_candidates(
    responses: Mapping[tuple[str, str], Sequence[Mapping[str, Any]]],
) -> dict[str, dict[str, Any]]:
    """Return ordinary six-digit source-code candidates from all six slices.

    Nonstandard historical supplier codes are retained separately by
    :func:`listing_catalogue_evidence`; use that result when scope closure
    matters. These candidates are source facts, not reviewed stable IDs.
    """
    return listing_catalogue_evidence(responses)["catalogue"]


def listing_catalogue_evidence(
    responses: Mapping[tuple[str, str], Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    """Validate six terminal Tushare listing slices and retain vendor dates.

    An observed `T`-prefixed historical code is kept verbatim, never stripped
    or merged into a later ordinary code. The returned catalogue only contains
    six-digit qualified codes; aliases need explicit stable identity mapping.
    Dates are supplier-reported history in this terminal observation, visible
    operationally only after Raw receipt; they are not independent public PIT.
    A response at the documented 6000-row cap cannot close this observation.
    """
    expected = {(exchange, status) for exchange in EXCHANGES for status in LISTING_STATUSES}
    if set(responses) != expected:
        raise DataError("full listing catalogue requires SSE/SZSE × L/D/P responses")
    candidates: dict[str, dict[str, Any]] = {}
    unresolved: list[dict[str, Any]] = []
    all_codes: set[str] = set()
    for exchange, status in sorted(expected):
        rows = responses[(exchange, status)]
        if len(rows) >= LISTING_CAP:
            raise DataError("stock_basic slice reached 6000-row cap")
        seen: set[str] = set()
        for row in rows:
            if any(field not in row for field in LISTING_FIELDS):
                raise DataError("stock_basic row lacks requested fields")
            code = row["ts_code"]
            match = _SYMBOL.fullmatch(code) if isinstance(code, str) else None
            alias = _VENDOR_ALIAS.fullmatch(code) if isinstance(code, str) else None
            suffix = (match or alias).group(1) if match or alias else None
            if suffix is None or ("SSE" if suffix == "SH" else "SZSE") != exchange:
                raise DataError("stock_basic row has invalid code/exchange")
            if row["exchange"] != exchange or row["list_status"] != status:
                raise DataError("stock_basic row differs from its selector")
            if code in seen or code in all_codes:
                raise DataError("stock_basic code duplicated across catalogue slices")
            seen.add(code)
            all_codes.add(code)
            listing = _source_day(row["list_date"], "list_date").isoformat()
            delist_raw = row["delist_date"]
            delist = None if delist_raw in (None, "") else _source_day(delist_raw, "delist_date").isoformat()
            if delist is not None and delist < listing:
                raise DataError("stock_basic delist date precedes listing")
            item = {"exchange": exchange, "list_status_at_observation": status,
                    "listing_date": listing, "vendor_delist_date": delist,
                    "delist_boundary": "supplier_reported_delist_date"}
            if alias:
                unresolved.append({"source_code": code, **item,
                                   "reason": "nonstandard_historical_vendor_alias",
                                   "identity_status": "requires_independent_evidence"})
            else:
                candidates[code] = item
    return {"catalogue": dict(sorted(candidates.items())),
            "unresolved_catalogue_rows": sorted(unresolved, key=lambda row: row["source_code"]),
            "source_scope": "SSE/SZSE stock_basic L/D/P terminal listing slices",
            "a_share_classification": "supplier_stock_basic_code_exchange_observation"}


def validate_identity_bindings(candidates: Mapping[str, Mapping[str, Any]],
                               identity_map: Mapping[str, str], *, version: str) -> dict[str, Any]:
    """Freeze externally reviewed, one-to-one stable IDs for this catalogue.

    The caller owns ID assignment and persists the returned version/map with
    the source plan. Empty or incomplete bindings fail before bulk acquisition.
    This does not resolve the exclusive delisting-session boundary.
    """
    if not isinstance(version, str) or not version.strip():
        raise DataError("identity binding version is required")
    if set(identity_map) != set(candidates):
        raise DataError("identity map must bind exactly the catalogue codes")
    values = tuple(identity_map.values())
    if any(not isinstance(value, str) or not value.strip() for value in values) or len(set(values)) != len(values):
        raise DataError("stable security IDs must be distinct and nonempty")
    return {"binding_version": version, "identity_map": dict(sorted(identity_map.items())),
            "source_codes": len(candidates), "basis": "reviewed_terminal_listing_catalogue",
            "historical_status": "unverified", "delist_boundary": "unresolved"}


def plan_weight_months(index_code: str, start_session: str,
                       end_session: str) -> tuple[dict[str, Any], ...]:
    """Plan one index_weight request per calendar month in an inclusive window.

    The returned scopes are bounded observations. They do not imply that a
    represented group remains a member set until the next monthly response.
    """
    if not isinstance(index_code, str) or not _INDEX.fullmatch(index_code):
        raise DataError("index_code must be exchange-qualified")
    start, end = _day(start_session), _day(end_session)
    if start > end:
        raise DataError("reversed index observation window")
    result: list[dict[str, Any]] = []
    month = date(start.year, start.month, 1)
    while month <= end:
        next_month = date(month.year + (month.month == 12), month.month % 12 + 1, 1)
        first, last = max(start, month), min(end, next_month - timedelta(days=1))
        result.append({"endpoint": "index_weight", "params": {"index_code": index_code,
                       "start_date": first.strftime("%Y%m%d"), "end_date": last.strftime("%Y%m%d")},
                       "fields": WEIGHT_FIELDS})
        month = next_month
    return tuple(result)


def resolve_index_preset(name: str) -> tuple[str, ...]:
    """Return the CSI 300/500/1000 Tushare selectors proposed for `csi1800`.

    The executable preparation plan queries Tushare ``index_basic`` for these
    exact qualified selectors. A static name difference is only a warning;
    the returned supplier name is retained as observed.
    """
    if name != "csi1800":
        raise DataError("unsupported index preset")
    return CSI1800_SOURCE_INDEX_CODES


def bounded_weight_observations(
    requests: Sequence[Mapping[str, Any]],
    responses: Mapping[tuple[str, str, str], Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    """Validate saved monthly responses and return dated groups and candidate union.

    ``responses`` keys are (index_code, start_date, end_date) from the plan.
    Empty months remain unobserved, never empty member sets. A nonempty dated
    group is retained only for its source trade_date, with an explicit one-day
    interval useful to the current Reader. Group completeness and all days
    between observations remain unverified. Raw receipts stay with the caller.
    """
    expected = {(spec["params"]["index_code"], spec["params"]["start_date"],
                 spec["params"]["end_date"]) for spec in requests}
    if set(responses) != expected or len(expected) != len(requests):
        raise DataError("monthly index responses must match every planned selector")
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    empty_months: list[dict[str, str]] = []
    candidate_codes: set[str] = set()
    unresolved_weight_rows: list[dict[str, Any]] = []
    for index_code, first, last in sorted(expected):
        rows = responses[(index_code, first, last)]
        if not rows:
            empty_months.append({"index_code": index_code, "start_date": first, "end_date": last})
            continue
        seen: set[tuple[str, str]] = set()
        for row in rows:
            if any(field not in row for field in WEIGHT_FIELDS):
                raise DataError("index_weight row lacks requested fields")
            code, source_date = row["con_code"], row["trade_date"]
            ordinary = _SYMBOL.fullmatch(code) if isinstance(code, str) else None
            alias = _VENDOR_ALIAS.fullmatch(code) if isinstance(code, str) else None
            if row["index_code"] != index_code or not (ordinary or alias):
                raise DataError("index_weight row has invalid index/constituent")
            day = _source_day(source_date, "trade_date")
            if not first <= source_date <= last:
                raise DataError("index_weight row lies outside month request")
            key = (source_date, code)
            if key in seen:
                raise DataError("duplicate member in one dated index response")
            seen.add(key)
            weight = row["weight"]
            if not isinstance(weight, (float, int)) or isinstance(weight, bool) or not 0 <= weight <= 100:
                raise DataError("invalid index weight percent")
            if alias:
                unresolved_weight_rows.append({"index_code": index_code, "source_code": code,
                                               "source_trade_date": day.isoformat(),
                                               "weight_percent": float(weight),
                                               "reason": "nonstandard_historical_vendor_alias",
                                               "identity_status": "requires_independent_evidence"})
                continue
            groups[day.isoformat()].append({"index_code": index_code, "con_code": code,
                                            "source_trade_date": day.isoformat(),
                                            "effective_from": day.isoformat(),
                                            "effective_to": (day + timedelta(days=1)).isoformat(),
                                            "weight_percent": float(weight),
                                            "observation_kind": "bounded_date_group"})
            candidate_codes.add(code)
    return {"mode": "bounded_monthly_observations_v1", "groups_by_source_date":
            {day: sorted(rows, key=lambda row: (row["index_code"], row["con_code"]))
             for day, rows in sorted(groups.items())},
            "candidate_union": sorted(candidate_codes), "empty_months_unknown": empty_months,
            "unresolved_weight_rows": unresolved_weight_rows,
            "candidate_union_requires_resolution": bool(unresolved_weight_rows),
            "continuous_membership": False, "historical_pit": "best_effort_vendor_v1"}


def plan_reference_bootstrap(*, index_codes: Sequence[str], start_session: str,
                             end_session: str, include_calendar: bool = False) -> dict[str, Any]:
    """Freeze six listing and explicit monthly index requests, without calling a source.

    Dates are inclusive civil dates. At least one index is required; each month
    is a bounded supplier observation, even when the plan is fully collected.
    The plan digest binds selectors and source fields for Raw replay.
    """
    indices = tuple(index_codes)
    if not indices or len(set(indices)) != len(indices):
        raise DataError("index_codes must be nonempty and unique")
    if type(include_calendar) is not bool:
        raise DataError("include_calendar must be bool")
    requests = [*plan_full_listing_requests()]
    if include_calendar:
        for code in sorted(indices):
            requests.append({"endpoint": "index_basic", "params": {"ts_code": code},
                             "fields": ("ts_code", "name", "market", "publisher")})
    for code in sorted(indices):
        requests.extend(plan_weight_months(code, start_session, end_session))
    if include_calendar:
        for exchange in EXCHANGES:
            requests.append({"endpoint": "trade_cal", "params": {"exchange": exchange,
                             "start_date": _day(start_session).strftime("%Y%m%d"),
                             "end_date": _day(end_session).strftime("%Y%m%d")},
                             "fields": ("exchange", "cal_date", "is_open")})
    # JSON-native shape allows the plan to be saved and replayed exactly.
    requests = [{**request, "fields": list(request["fields"])} for request in requests]
    body = {"version": "reference_bootstrap.v1", "index_codes": sorted(indices),
            "start_session": _day(start_session).isoformat(),
            "end_session": _day(end_session).isoformat(), "requests": requests,
            "membership_mode": "bounded_monthly_observations_v1"}
    if include_calendar:
        body["include_calendar"] = True
    digest = sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {**body, "plan_digest": digest}


def _reference_profile(endpoint: str) -> dict[str, Any]:
    scope = {"stock_basic": "terminal_listing_status_slice",
             "index_weight": "bounded_monthly_index_date_observation",
             "index_basic": "index_selector_verification",
             "trade_cal": "complete_civil_calendar"}[endpoint]
    return {"id": f"tushare.reference_bootstrap.{endpoint}.v1", "endpoint": endpoint,
            "raw_serialization": "json_source_records_or_sdk_table_v1",
            "scope": scope,
            "coverage_claim": "saved supplier response; historical PIT and completeness not certified",
            "response_limit": (8000 if endpoint == "index_basic" else CAPS[endpoint]),
            "response_limit_rule": ("documented stock_basic cap" if endpoint == "stock_basic" else
                                    "local index_weight row safeguard; no official numeric cap documented"
                                    if endpoint == "index_weight" else
                                    "documented index_basic cap" if endpoint == "index_basic" else
                                    "local trade_cal row safeguard")}


def _selector_evidence(code: str, rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if len(rows) != 1 or rows[0].get("ts_code") != code:
        raise DataError(f"index_basic did not verify exact selector {code}")
    row = rows[0]
    if not isinstance(row.get("name"), str) or not row["name"]:
        raise DataError("index_basic lacks index name")
    result = {"ts_code": code, "name": row["name"], "market": row.get("market"),
              "publisher": row.get("publisher"), "basis": "observed_tushare_index_basic"}
    expected_name = CSI1800_NAMES.get(code)
    if expected_name is not None and row["name"] != expected_name:
        result["name_crosscheck_warning"] = {"static_name": expected_name,
                                              "supplier_name": row["name"]}
    return result


def _calendar_evidence(requests: Sequence[Mapping[str, Any]],
                       responses: Mapping[str, Sequence[Mapping[str, Any]]]) -> dict[str, Any]:
    if len(requests) != 2 or set(responses) != set(EXCHANGES):
        raise DataError("complete SSE/SZSE calendar responses required")
    open_sessions: dict[str, list[str]] = {}
    for request in requests:
        params = request["params"]
        exchange = params["exchange"]
        start = _source_day(params["start_date"], "calendar start")
        end = _source_day(params["end_date"], "calendar end")
        cells: dict[date, bool] = {}
        for row in responses[exchange]:
            if row.get("exchange") != exchange:
                raise DataError("trade_cal row differs from requested exchange")
            day = _source_day(row.get("cal_date"), "cal_date")
            if not start <= day <= end or day in cells:
                raise DataError("trade_cal contains out-of-range or duplicate date")
            value = row.get("is_open")
            if value not in (0, 1, "0", "1", False, True):
                raise DataError("trade_cal is_open must be 0 or 1")
            cells[day] = str(value) in ("1", "True")
        expected_days = (end - start).days + 1
        if len(cells) != expected_days:
            raise DataError("trade_cal lacks a civil date in requested scope")
        open_sessions[exchange] = [day.isoformat() for day, opened in sorted(cells.items()) if opened]
    return {"start_session": start.isoformat(), "end_session": end.isoformat(),
            "open_sessions_by_exchange": open_sessions,
            "basis": "complete_saved_trade_cal_civil_date_responses"}


def run_reference_bootstrap(store: LocalStore, *, plan: Mapping[str, Any], client: Any,
                            operation_id: str, max_attempts: int = 3,
                            min_interval_seconds: float = 1.25,
                            reuse_raw_batch_ids: Sequence[str] = (),
                            clock: Any = None, monotonic: Any = None,
                            sleeper: Any = None) -> dict[str, Any]:
    """Collect Raw-first reference requests and return a validated scope seed.

    ``client.query(endpoint, fields=..., **params)`` is the only supplier call.
    Failures are retried up to ``max_attempts`` with short backoff. An interrupted
    run resumes at the next durable attempt slot with the same operation ID.
    Successful/empty Raw is never refetched. The operation checkpoint and Raw
    selectors bind the exact plan. This function writes only Raw and operation
    state: no canonical partition, Snapshot, or ``current`` pointer is changed.
    ``reuse_raw_batch_ids`` may name earlier saved reference responses with the
    exact same endpoint/params/fields and source profile. Those observations
    retain their original receipt time and Raw ID; reusing them makes no new
    claim of a later supplier observation. The IDs bind the new operation.
    Exceptions contain no supplier response text or credentials.
    """
    if not isinstance(store, LocalStore):
        raise DataError("LocalStore is required")
    if not isinstance(plan, Mapping):
        raise DataError("reference plan is required")
    try:
        expected_plan = plan_reference_bootstrap(index_codes=plan["index_codes"],
                                                 start_session=plan["start_session"],
                                                 end_session=plan["end_session"],
                                                 include_calendar=plan.get("include_calendar", False))
    except (KeyError, TypeError, ValueError) as exc:
        raise DataError("invalid reference plan") from exc
    if dict(plan) != expected_plan:
        raise ConflictError("reference plan differs from frozen public planner")
    if type(max_attempts) is not int or not 1 <= max_attempts <= 10:
        raise DataError("max_attempts must be in [1,10]")
    if not isinstance(min_interval_seconds, (int, float)) or not 0 <= min_interval_seconds <= 3600:
        raise DataError("min_interval_seconds must be in [0,3600]")
    query = getattr(client, "query", None)
    if not callable(query):
        raise DataError("injected client needs query(endpoint, fields, **params)")
    if isinstance(reuse_raw_batch_ids, (str, bytes)):
        raise DataError("reuse Raw IDs must be a sequence of batch IDs")
    reuse_ids = tuple(sorted(set(reuse_raw_batch_ids)))
    if len(reuse_ids) != len(tuple(reuse_raw_batch_ids)):
        raise DataError("reuse Raw IDs must be unique")
    reusable: dict[int, dict[str, Any]] = {}
    if reuse_ids:
        source_raws = store.get_raw_many(reuse_ids)
        for index, spec in enumerate(plan["requests"]):
            matches = [raw for raw in source_raws.values()
                       if raw.get("status") in ("success", "empty") and
                       raw.get("domain") == "reference_bootstrap" and
                       raw.get("source_profile") == _reference_profile(spec["endpoint"]) and
                       raw.get("request", {}).get("endpoint") == spec["endpoint"] and
                       raw["request"].get("params") == spec["params"] and
                       raw["request"].get("fields") == spec["fields"]]
            if matches:
                reusable[index] = max(matches, key=lambda raw: (raw["observed_at"], raw["batch_id"]))
        unused = set(reuse_ids) - {raw["batch_id"] for raw in reusable.values()}
        if unused:
            raise DataError("reused Raw does not match this reference plan")
    # LocalStore checks the safe operation-ID grammar before any network call.
    prior = store.read_operation(operation_id)
    operation_identity = {"kind": "reference_bootstrap", "plan_digest": plan["plan_digest"],
                          "max_attempts": max_attempts}
    if reuse_ids:
        operation_identity["reuse_raw_batch_ids"] = list(reuse_ids)
    if prior is not None and any(prior.get(key) != value for key, value in operation_identity.items()):
        raise ConflictError("operation ID is bound to another reference plan")
    state = prior or {**operation_identity, "status": "running"}
    if prior is None:
        store.write_operation(operation_id, state)
    recovered = store.find_raw_by_operation(operation_id)
    now = clock or (lambda: datetime.now(timezone.utc))
    ticks = monotonic or time.monotonic
    sleep = sleeper or time.sleep
    # Tushare stock_basic has a lower call allowance than the other reference
    # endpoints. Keep one serial global clock and a separate stock-basic clock.
    global_gap = max(0.2, min_interval_seconds)
    stock_gap = 60 / 50
    last_call: float | None = None
    last_stock_call: float | None = None
    chosen: list[dict[str, Any]] = []

    def collect(index: int) -> dict[str, Any]:
        nonlocal last_call, last_stock_call
        spec = plan["requests"][index]
        endpoint, params = spec["endpoint"], spec["params"]
        profile = _reference_profile(endpoint)
        if index in reusable:
            return reusable[index]
        for attempt in range(max_attempts):
            slot = index * max_attempts + attempt
            request = {"endpoint": endpoint, "params": params, "fields": list(spec["fields"]),
                       "plan_digest": plan["plan_digest"], "plan_request_index": index,
                       "attempt": attempt, "coverage_status": "observed_response_only"}
            raw = recovered.get(slot)
            fetched_new = raw is None
            if raw is None:
                current = ticks()
                due = current
                if last_call is not None:
                    due = max(due, last_call + global_gap)
                if endpoint == "stock_basic" and last_stock_call is not None:
                    due = max(due, last_stock_call + stock_gap)
                if due > current:
                    sleep(due - current)
                try:
                    last_call = ticks()
                    if endpoint == "stock_basic":
                        last_stock_call = last_call
                    response = query(endpoint, fields=",".join(spec["fields"]), **params)
                    payload, count = response_bytes(response, endpoint=endpoint)
                    status = ("failed" if count is None else
                              "cap" if count >= profile["response_limit"] else
                              "empty" if count == 0 else "success")
                except Exception as exc:
                    # Source exceptions can contain tokens; persist the type only.
                    payload = json.dumps({"phase": "fetch_or_serialize", "error_type":
                                          type(exc).__name__}, separators=(",", ":")).encode()
                    status = "failed"
                observed = now()
                if not isinstance(observed, datetime) or observed.tzinfo is None or observed.utcoffset() is None:
                    raise DataError("clock must return a timezone-aware datetime")
                raw = store.write_raw(payload, domain="reference_bootstrap", request=request,
                                      source_profile=profile, observed_at=observed,
                                      status=status, operation_id=operation_id,
                                      batch_index=slot)
                recovered[slot] = raw
                state.update(status="running", last_logged_index=slot)
                store.write_operation(operation_id, state)
            elif (raw.get("request") != request or raw.get("source_profile") != profile or
                  raw.get("domain") != "reference_bootstrap"):
                raise ConflictError("recovered reference Raw differs from plan")
            if raw["status"] in ("success", "empty"):
                return raw
            if raw["status"] == "cap":
                state.update(status="partial", error={"request_index": index, "reason": "cap"})
                store.write_operation(operation_id, state)
                raise CoverageError(f"{endpoint} response reached {profile['response_limit']}-row safeguard; saved Raw retained")
            if raw["status"] != "failed":
                raise DataError("unexpected reference Raw status")
            if attempt == max_attempts - 1:
                state.update(status="partial", error={"request_index": index,
                                                       "reason": "attempts_exhausted"})
                store.write_operation(operation_id, state)
                raise CoverageError("reference supplier request exhausted attempts; saved Raw retained")
            if fetched_new:
                sleep(min(2 ** attempt, 30))
        raise DataError("unreachable reference attempt state")

    for index in range(6):
        chosen.append(collect(index))
    listing_responses = {
        (plan["requests"][i]["params"]["exchange"], plan["requests"][i]["params"]["list_status"]):
        _rows(store.read_raw_record(chosen[i])) for i in range(6)}
    try:
        listing_evidence = listing_catalogue_evidence(listing_responses)
    except DataError:
        state.update(status="partial", error={"phase": "listing_validation"})
        store.write_operation(operation_id, state)
        raise

    weight_requests = [spec for spec in plan["requests"] if spec["endpoint"] == "index_weight"]
    calendar_requests = [spec for spec in plan["requests"] if spec["endpoint"] == "trade_cal"]
    weight_responses: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    calendar_responses: dict[str, list[dict[str, Any]]] = {}
    selector_evidence: dict[str, dict[str, Any]] = {}
    for index in range(6, len(plan["requests"])):
        raw = collect(index)
        chosen.append(raw)
        spec = plan["requests"][index]
        params = spec["params"]
        rows = _rows(store.read_raw_record(raw))
        if spec["endpoint"] == "index_basic":
            try:
                selector_evidence[params["ts_code"]] = _selector_evidence(params["ts_code"], rows)
            except DataError:
                state.update(status="partial", error={"phase": "index_selector_validation"})
                store.write_operation(operation_id, state)
                raise
        elif spec["endpoint"] == "index_weight":
            weight_responses[(params["index_code"], params["start_date"], params["end_date"])] = rows
        else:
            calendar_responses[params["exchange"]] = rows
    try:
        bounded = bounded_weight_observations(weight_requests, weight_responses)
        calendar = (_calendar_evidence(calendar_requests, calendar_responses)
                    if plan.get("include_calendar") else None)
    except DataError:
        state.update(status="partial", error={"phase": "reference_validation"})
        store.write_operation(operation_id, state)
        raise
    catalogue = listing_evidence["catalogue"]
    binding_codes = sorted(set(catalogue) | set(bounded["candidate_union"]))
    unresolved_codes = sorted({row["source_code"] for row in
                               (*listing_evidence["unresolved_catalogue_rows"],
                                *bounded["unresolved_weight_rows"])})
    result = {"operation_id": operation_id, "plan_digest": plan["plan_digest"],
              "index_codes": list(plan["index_codes"]),
              "start_session": plan["start_session"], "end_session": plan["end_session"],
              "index_selector_evidence": selector_evidence,
              "calendar": calendar,
              "status": "complete_observations", "raw_batch_ids": [r["batch_id"] for r in chosen],
              "raw_observations": [{"batch_id": raw["batch_id"],
                                    "observed_at": raw["observed_at"],
                                    "endpoint": spec["endpoint"],
                                    "params": dict(spec["params"])}
                                   for spec, raw in zip(plan["requests"], chosen)],
              "catalogue": catalogue,
              "unresolved_catalogue_rows": listing_evidence["unresolved_catalogue_rows"],
              "listing_source_scope": listing_evidence["source_scope"],
              "a_share_classification": listing_evidence["a_share_classification"],
              "bounded_observations": bounded,
              "candidate_union": bounded["candidate_union"],
              "identity_binding_template": {"binding_version": None,
                                            "identity_map": {code: None for code in binding_codes},
                                            "unresolved_source_codes": unresolved_codes,
                                            "missing_from_listing_catalogue":
                                                sorted(set(bounded["candidate_union"]) - set(catalogue))},
              "continuous_membership_ready": False, "strict_historical_pit_ready": False}
    state.update(status="complete_observations", raw_batch_ids=result["raw_batch_ids"])
    state.pop("error", None)
    store.write_operation(operation_id, state)
    return result


def prepare_universe_scope(seed: Mapping[str, Any], *, anchor_session: str,
                           scope_mode: str,
                           previous_bindings: Mapping[str, Any] | None = None,
                           preset: str = "csi1800") -> dict[str, Any]:
    """Build a bulk-ready scope from saved reference observations, without I/O.

    ``scope_mode`` is ``anchor_members`` (latest observed group in anchor month
    per index) or ``historical_union`` (all observed codes; never truncated).
    The former is an engineering acceptance cohort, not a survivor-safe
    historical decision universe. The latter is only a candidate read set.
    New stable IDs use source code plus listing date; old mappings are retained
    and may only be extended. An index candidate without listing identity stays
    unbound and makes this scope not ready for bulk.
    """
    if scope_mode not in ("anchor_members", "historical_union"):
        raise DataError("scope_mode must be anchor_members or historical_union")
    anchor = _day(anchor_session).isoformat()
    if seed.get("status") != "complete_observations" or not isinstance(seed.get("calendar"), Mapping):
        raise CoverageError("scope preparation requires completed Raw and calendar observations")
    codes = resolve_index_preset(preset)
    if set(seed.get("index_codes", ())) != set(codes):
        raise DataError("source indices differ from selected preset")
    selectors = seed.get("index_selector_evidence", {})
    if set(selectors) != set(codes):
        raise CoverageError("all CSI source selectors require index_basic verification")
    for code in codes:
        if not isinstance(selectors[code].get("name"), str) or not selectors[code]["name"]:
            raise CoverageError("Tushare source selector name is missing")
    if not seed["start_session"] <= anchor <= seed["end_session"]:
        raise DataError("anchor session lies outside prepared source scope")
    calendar = seed["calendar"]
    if (calendar.get("start_session") != seed["start_session"] or
            calendar.get("end_session") != seed["end_session"] or
            set(calendar.get("open_sessions_by_exchange", {})) != set(EXCHANGES)):
        raise CoverageError("calendar scope does not close requested range")
    catalogue = seed["catalogue"]
    bounded = seed["bounded_observations"]
    groups = bounded["groups_by_source_date"]
    anchor_dates: dict[str, str | None] = {}
    anchor_groups: dict[str, list[str]] = {}
    for code in codes:
        dated = [(day, [row["con_code"] for row in rows if row["index_code"] == code])
                 for day, rows in groups.items() if day[:7] == anchor[:7] and day <= anchor]
        dated = [(day, symbols) for day, symbols in dated if symbols]
        if dated:
            chosen_day, symbols = max(dated, key=lambda item: item[0])
            anchor_dates[code] = chosen_day
            anchor_groups[code] = sorted(set(symbols))
        else:
            anchor_dates[code] = None
            anchor_groups[code] = []
    anchor_members = sorted({symbol for symbols in anchor_groups.values() for symbol in symbols})
    historical_union = sorted(set(bounded["candidate_union"]))
    selected = anchor_members if scope_mode == "anchor_members" else historical_union

    previous = previous_bindings or {}
    old_map = dict(previous.get("identity_map", {}))
    old_dates = dict(previous.get("listing_dates", {}))
    if any(not isinstance(code, str) or not isinstance(identity, str) or not identity
           for code, identity in old_map.items()) or len(set(old_map.values())) != len(old_map):
        raise DataError("previous stable identity bindings are invalid")
    identity_map = dict(old_map)
    listing_dates = dict(old_dates)
    for code, item in sorted(catalogue.items()):
        listing = item["listing_date"]
        if code in old_dates and old_dates[code] != listing:
            raise ConflictError("previous binding listing date differs from new source observation")
        listing_dates[code] = listing
        identity_map.setdefault(code, f"cnstock.{code}.{listing.replace('-', '')}")
    if len(set(identity_map.values())) != len(identity_map):
        raise ConflictError("new deterministic identities collide with prior bindings")
    missing = sorted(set(selected) - set(identity_map))
    unresolved_weight = bounded.get("unresolved_weight_rows", [])
    selected_aliases = ([row for row in unresolved_weight
                         if scope_mode == "historical_union" or
                         row["source_trade_date"] in set(anchor_dates.values())])
    expected_counts = {"000300.SH": 300, "000905.SH": 500, "000852.SH": 1000}
    counts = {code: len(anchor_groups[code]) for code in codes}
    anchor_nominal = (all(counts[code] == expected_counts[code] for code in codes)
                      and len(anchor_members) == 1800)
    ready = bool(selected) and not missing and not selected_aliases
    return {"version": "prepared_universe_scope.v1", "preset": preset,
            "scope_mode": scope_mode, "start_session": seed["start_session"],
            "end_session": seed["end_session"], "anchor_session": anchor,
            "index_codes": list(codes), "symbols": selected,
            "symbol_count": len(selected), "anchor_members": anchor_members,
            "anchor_observation_dates": anchor_dates, "anchor_group_counts": counts,
            "anchor_nominal_1800": anchor_nominal,
            "historical_union": historical_union,
            "identity_binding_version": "source_code_listing_date.v1",
            "identity_map": dict(sorted(identity_map.items())),
            "listing_dates": dict(sorted(listing_dates.items())),
            "missing_identity_codes": missing,
            "unresolved_catalogue_rows": seed["unresolved_catalogue_rows"],
            "unresolved_weight_rows": selected_aliases,
            "calendar": calendar,
            "reference_plan_digest": seed["plan_digest"],
            "reference_operation_id": seed["operation_id"],
            "reference_raw_batch_ids": seed["raw_batch_ids"],
            "reference_raw_observations": seed.get("raw_observations", []),
            "index_selector_evidence": selectors,
            "ready_for_bulk": ready,
            "membership_basis": "bounded_monthly_vendor_observations_best_effort_not_strict_pit",
            "a_share_classification": seed["a_share_classification"]}


def exact_anchor_raw_ids(store: LocalStore, seed: Mapping[str, Any], *,
                         anchor_session: str) -> dict[str, str]:
    """Bind three saved, complete one-day Tushare index-weight observations.

    The monthly reference Raw may serve as the exact anchor only when every
    returned row belongs to the requested anchor date and the three original
    supplier responses have 300/500/1000 distinct, nonoverlapping codes.
    This selects existing Raw IDs and never synthesizes or refetches a row.
    """
    anchor = _day(anchor_session).strftime("%Y%m%d")
    expected = {"000300": 300, "000905": 500, "000852": 1000}
    observations = seed.get("raw_observations")
    if not isinstance(store, LocalStore) or not isinstance(observations, list):
        raise DataError("exact anchor needs saved reference Raw observations")
    result: dict[str, str] = {}
    all_members: set[str] = set()
    for short_code, count in expected.items():
        index_code = f"{short_code}.SH"
        candidates = [item for item in observations
                      if item.get("endpoint") == "index_weight" and
                      item.get("params", {}).get("index_code") == index_code and
                      item["params"].get("start_date", "") <= anchor <=
                      item["params"].get("end_date", "")]
        if len(candidates) != 1:
            raise CoverageError(f"{index_code} has no unique saved anchor-month Raw")
        raw = store.get_raw(candidates[0]["batch_id"])
        if raw.get("status") != "success" or raw.get("domain") != "reference_bootstrap":
            raise CoverageError(f"{index_code} anchor Raw is not a successful original response")
        rows = _rows(store.read_raw_record(raw))
        members = [row.get("con_code") for row in rows]
        if (len(rows) != count or len(set(members)) != count or
                any(row.get("index_code") != index_code or
                    row.get("trade_date") != anchor or
                    not isinstance(row.get("con_code"), str) or
                    not _SYMBOL.fullmatch(row["con_code"]) for row in rows)):
            raise CoverageError(f"{index_code} anchor Raw is not one complete {anchor} group")
        overlap = all_members.intersection(members)
        if overlap:
            raise CoverageError("CSI 300/500/1000 anchor Raw groups overlap")
        all_members.update(members)
        result[short_code] = raw["batch_id"]
    if len(all_members) != 1800:
        raise CoverageError("CSI anchor Raw union is not 1,800 distinct securities")
    return result


def save_prepared_scope(store: LocalStore, prepared: Mapping[str, Any], *,
                        preparation_id: str) -> str:
    """Persist an immutable reviewed scope under operations/preparation.

    Repeating identical content is idempotent. Different content needs a new
    preparation ID so a bulk job's scope reference cannot change in place.
    This writes no Snapshot or ``current`` pointer.
    """
    if not isinstance(store, LocalStore) or prepared.get("version") != "prepared_universe_scope.v1":
        raise DataError("LocalStore and prepared universe scope required")
    name = _clean_name(preparation_id, "preparation ID")
    uri = f"operations/preparation/{name}.json"
    with store.writer():
        store._atomic(store._path(uri), _json_bytes(dict(prepared)), immutable=True)
    return str(store._path(uri))
