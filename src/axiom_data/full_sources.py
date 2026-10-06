"""Thin resumable composition of market and native financial/event sources.

The market runner owns its own checkpoint. This layer freezes the extra source
scope, collects event Raw in bounded chunks, builds candidate Snapshots, and
swaps ``current`` once only after all required work has completed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
import time
from typing import Any, Callable, Mapping, Sequence

from .batch_fetch import BatchRateLimiter, run_batch_chunk, verify_batch_selectors
from .bulk_jobs import BulkJobPlan, plan_bulk_job, run_bulk_job, verify_bulk_job
from .event_sources import request_fields
from .protocols import ConflictError, CoverageError, DataError, OperationResult
from .storage import LocalStore
from .updates import apply_saved_raw


_EVENTS = ("income", "balancesheet", "cashflow", "fina_indicator",
           "dividend", "top10_holders", "stk_limit")
_STRATEGIES = {"vip_month", "symbol_year"}


def _hash(value: Any) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _date(value: str) -> date:
    if not isinstance(value, str):
        raise DataError("full source date must be ISO text")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise DataError("full source date must be ISO YYYY-MM-DD") from exc


def _quarter_lookback(first: date) -> date:
    quarter = first.year * 4 + (first.month - 1) // 3 - 5
    year, index = divmod(quarter, 4)
    return date(year, index * 3 + 1, 1)


def _months(first: date, last: date):
    year, month = first.year, first.month
    while (year, month) <= (last.year, last.month):
        start = max(first, date(year, month, 1))
        next_month = date(year + (month == 12), month % 12 + 1, 1)
        end = min(last, next_month - timedelta(days=1))
        yield start, end
        year, month = next_month.year, next_month.month


def _quarter_ends(first: date, last: date):
    year, index = first.year, (first.month - 1) // 3
    while (year, index) <= (last.year, (last.month - 1) // 3):
        month = (index + 1) * 3
        next_month = date(year + (month == 12), month % 12 + 1, 1)
        end = next_month - timedelta(days=1)
        if first <= end <= last:
            yield end
        index += 1
        if index == 4:
            year, index = year + 1, 0


def _yyyymmdd(day: date) -> str:
    return day.strftime("%Y%m%d")


@dataclass(frozen=True)
class FullSourcePlan:
    market: BulkJobPlan
    event_endpoints: tuple[str, ...]
    income_strategy: str
    event_start: str
    calendar_end: str
    max_event_requests_per_chunk: int = 6000
    announcement_start: str | None = None
    membership_source: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        body = {"schema_version": "local_full_source_job_v4",
                "market": self.market.to_dict(),
                "event_endpoints": list(self.event_endpoints),
                "income_strategy": self.income_strategy,
                "event_start": self.event_start, "calendar_end": self.calendar_end,
                "max_event_requests_per_chunk": self.max_event_requests_per_chunk}
        if self.announcement_start:
            body["announcement_start"] = self.announcement_start
            body["historical_refresh_policy"] = "explicit_new_bulk_plan_for_older_corrections"
        if self.membership_source:
            body["membership_source"] = self.membership_source
        return body

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "FullSourcePlan":
        if not isinstance(value, Mapping):
            raise DataError("unsupported full source plan")
        expected = {"schema_version", "market", "event_endpoints", "income_strategy",
                    "event_start", "calendar_end",
                    "max_event_requests_per_chunk"}
        version = value.get("schema_version") if isinstance(value, Mapping) else None
        if "announcement_start" in value:
            expected |= {"announcement_start", "historical_refresh_policy"}
        if "membership_source" in value:
            expected.add("membership_source")
        if (not isinstance(value, Mapping) or set(value) != expected or
                version != "local_full_source_job_v4" or
                "announcement_start" in expected and
                value.get("historical_refresh_policy") != "explicit_new_bulk_plan_for_older_corrections"):
            raise DataError("unsupported full source plan")
        market = BulkJobPlan.from_dict(value["market"])
        result = plan_full_sources(market=market, event_endpoints=value["event_endpoints"],
                                   income_strategy=value["income_strategy"],
                                   calendar_end=value["calendar_end"],
                                   max_event_requests_per_chunk=value["max_event_requests_per_chunk"],
                                   announcement_start=value.get("announcement_start"),
                                   membership_source=value.get("membership_source"))
        if result.to_dict()["schema_version"] != version:
            raise DataError("full source plan mode and schema version differ")
        if result.event_start != value["event_start"]:
            raise DataError("full source financial lookback differs from declared range")
        return result

    def fingerprint(self) -> str:
        return _hash(self.to_dict())


def plan_full_sources(*, market: BulkJobPlan, event_endpoints: Sequence[str] = _EVENTS,
                      income_strategy: str = "vip_month", calendar_end: str,
                      max_event_requests_per_chunk: int = 6000,
                      announcement_start: str | None = None,
                      membership_source: Mapping[str, Any] | None = None) -> FullSourcePlan:
    """Freeze report lookback and a bounded daily announcement refresh.

    ``calendar_end`` is explicit because a historic report can be corrected long
    after its report period. The CLI freezes today's date plus a calendar tail
    when it creates the plan; replay never recomputes it from wall time.
    """
    if not isinstance(market, BulkJobPlan) or market.request_strategy != "trading_day_market_v2":
        raise DataError("full sources require the current market batch plan")
    events = tuple(dict.fromkeys(event_endpoints))
    if not events or any(name not in _EVENTS for name in events) or len(events) != len(tuple(event_endpoints)):
        raise DataError("full source event endpoints must be unique supported names")
    if income_strategy not in _STRATEGIES:
        raise DataError("unsupported income request strategy")
    if type(max_event_requests_per_chunk) is not int or not 1 <= max_event_requests_per_chunk <= 6000:
        raise DataError("event chunk request limit must be in [1,6000]")
    start = _quarter_lookback(_date(market.start_session))
    if market.mode == "daily":
        announced = _date(announcement_start) if announcement_start else (
            _date(market.end_session) - timedelta(days=30))
        if not start <= announced <= _date(market.end_session):
            raise DataError("daily announcement start must lie within report warmup and session")
    elif announcement_start is not None:
        raise DataError("announcement_start applies only to daily plans")
    else:
        announced = None
    frozen_membership = None
    if membership_source is not None:
        if (not isinstance(membership_source, Mapping) or
                set(membership_source) != {"schema_version", "index_weight_raw_batch_ids",
                                           "stock_basic_raw_batch_ids", "verified_through",
                                           "between_snapshots"} or
                membership_source.get("schema_version") != "tushare_csi1800_membership_source_v1" or
                not isinstance(membership_source["index_weight_raw_batch_ids"], list) or
                not membership_source["index_weight_raw_batch_ids"] or
                not isinstance(membership_source["stock_basic_raw_batch_ids"], list) or
                not membership_source["stock_basic_raw_batch_ids"] or
                any(not isinstance(raw_id, str) or not raw_id.startswith("b_")
                    for raw_id in (*membership_source["index_weight_raw_batch_ids"],
                                   *membership_source["stock_basic_raw_batch_ids"])) or
                len(set(membership_source["index_weight_raw_batch_ids"])) !=
                len(membership_source["index_weight_raw_batch_ids"]) or
                len(set(membership_source["stock_basic_raw_batch_ids"])) !=
                len(membership_source["stock_basic_raw_batch_ids"]) or
                membership_source["between_snapshots"] != "carry_forward" or
                _date(membership_source["verified_through"]) > _date(market.end_session)):
            raise DataError("Tushare membership needs frozen monthly weight and listing Raw")
        frozen_membership = json.loads(json.dumps(membership_source, sort_keys=True))
    through = _date(calendar_end)
    if through < _date(market.end_session) + timedelta(days=14):
        raise DataError("calendar_end needs at least a 14-day next-open tail")
    return FullSourcePlan(market, events, income_strategy, start.isoformat(),
                          through.isoformat(), max_event_requests_per_chunk,
                          announced.isoformat() if announced else None,
                          frozen_membership)


def _calendar_job(plan: FullSourcePlan) -> BulkJobPlan:
    return plan_bulk_job(mode="bulk", symbols=plan.market.symbols,
                         start_session=plan.event_start, end_session=plan.calendar_end,
                         identity_map=dict(plan.market.identity_map), endpoints=("trade_cal",),
                         max_requests_per_chunk=plan.market.max_requests_per_chunk,
                         request_strategy="trading_day_market_v2")


def _next_open_map(plan: FullSourcePlan, sessions: Sequence[str]) -> dict[str, str]:
    opened = [date(int(s[:4]), int(s[4:6]), int(s[6:])) for s in sessions]
    opened.sort()
    if not opened:
        raise CoverageError("full source calendar has no open sessions")
    from bisect import bisect_right
    current, last = _date(plan.event_start), _date(plan.calendar_end)
    result = {}
    while current <= last:
        index = bisect_right(opened, current)
        if index < len(opened):
            result[current.isoformat()] = opened[index].isoformat()
        current += timedelta(days=1)
    return result


def _spec(endpoint: str, params: Mapping[str, str], symbols: Sequence[str]) -> dict[str, Any]:
    return {"endpoint": endpoint, "params": dict(params), "fields": list(request_fields(endpoint)),
            "canonical_symbols": list(symbols)}


def _event_chunks(plan: FullSourcePlan, next_open: Mapping[str, str],
                  trading_sessions: Sequence[str]):
    """Yield deterministic bounded chunks and only the dates their profile needs."""
    symbols = plan.market.symbols
    first, last = _date(plan.event_start), _date(plan.market.end_session)
    announcement_first = (_date(plan.announcement_start) if plan.announcement_start
                          else first)
    limit = plan.max_event_requests_per_chunk
    for endpoint in plan.event_endpoints:
        if endpoint in {"income", "balancesheet", "cashflow"} and plan.income_strategy == "vip_month":
            for year in range(announcement_first.year, last.year + 1):
                ranges = [(a, b) for a, b in _months(announcement_first, last) if a.year == year]
                specs = [_spec(f"{endpoint}_vip", {"start_date": _yyyymmdd(a),
                                             "end_date": _yyyymmdd(b),
                                             "report_type": "1"}, symbols)
                         for a, b in ranges]
                # A later correction keeps its original ann_date selector but
                # may carry a much later f_ann_date. The frozen as-of calendar
                # must cover that actual announcement day as well.
                calendar = next_open
                for offset in range(0, len(specs), limit):
                    yield endpoint, f"{year}:{offset // limit}", specs[offset:offset + limit], calendar
        elif endpoint in {"income", "balancesheet", "cashflow"}:
            for year in range(announcement_first.year, last.year + 1):
                a, b = max(announcement_first, date(year, 1, 1)), min(last, date(year, 12, 31))
                specs = [_spec(endpoint, {"ts_code": code, "start_date": _yyyymmdd(a),
                                          "end_date": _yyyymmdd(b), "report_type": "1"}, (code,))
                         for code in symbols]
                calendar = next_open
                for offset in range(0, len(specs), limit):
                    yield endpoint, f"{year}:{offset // limit}", specs[offset:offset + limit], calendar
        elif endpoint == "fina_indicator" and plan.income_strategy == "vip_month":
            periods = list(_quarter_ends(first, last))
            for year in range(first.year, last.year + 1):
                specs = [_spec("fina_indicator_vip", {"period": _yyyymmdd(day)}, symbols)
                         for day in periods if day.year == year]
                for offset in range(0, len(specs), limit):
                    yield endpoint, f"{year}:{offset // limit}", specs[offset:offset + limit], next_open
        elif endpoint == "fina_indicator":
            for year in range(first.year, last.year + 1):
                a, b = max(first, date(year, 1, 1)), min(last, date(year, 12, 31))
                specs = [_spec(endpoint, {"ts_code": code, "start_date": _yyyymmdd(a),
                                          "end_date": _yyyymmdd(b)}, (code,))
                         for code in symbols]
                for offset in range(0, len(specs), limit):
                    yield endpoint, f"{year}:{offset // limit}", specs[offset:offset + limit], next_open
        elif endpoint == "top10_holders":
            specs = [_spec(endpoint, {"ts_code": code, "start_date": _yyyymmdd(first),
                                      "end_date": _yyyymmdd(last)}, (code,)) for code in symbols]
            # Holder selector is report period, so a correction may have any
            # later announcement date. Keep the full frozen calendar here.
            for offset in range(0, len(specs), limit):
                yield endpoint, f"symbols:{offset // limit}", specs[offset:offset + limit], next_open
        elif endpoint == "dividend":
            for a, b in _months(announcement_first, last):
                specs = [_spec(endpoint, {"ann_date": _yyyymmdd(a + timedelta(days=step))}, symbols)
                         for step in range((b - a).days + 1)]
                calendar = {day: nxt for day, nxt in next_open.items()
                            if a.year <= int(day[:4]) <= a.year + 1}
                for offset in range(0, len(specs), limit):
                    yield endpoint, f"{a:%Y-%m}:{offset // limit}", specs[offset:offset + limit], calendar
        else:
            sessions = [s for s in trading_sessions
                        if plan.market.start_session.replace("-", "") <= s <= plan.market.end_session.replace("-", "")]
            for a, b in _months(_date(plan.market.start_session), _date(plan.market.end_session)):
                specs = [_spec(endpoint, {"trade_date": s}, symbols) for s in sessions
                         if _yyyymmdd(a) <= s <= _yyyymmdd(b)]
                for offset in range(0, len(specs), limit):
                    yield endpoint, f"{a:%Y-%m}:{offset // limit}", specs[offset:offset + limit], {}


def estimate_full_sources(plan: FullSourcePlan) -> dict[str, Any]:
    from .bulk_jobs import estimate_bulk_job
    first, last = _date(plan.event_start), _date(plan.market.end_session)
    announcement_first = (_date(plan.announcement_start) if plan.announcement_start
                          else first)
    months = list(_months(announcement_first, last))
    report_months = list(_months(first, last))
    by_endpoint = {}
    for financial in ("income", "balancesheet", "cashflow"):
        if financial not in plan.event_endpoints:
            continue
        by_endpoint[f"{financial}_vip" if plan.income_strategy == "vip_month" else financial] = (
            len(months) if plan.income_strategy == "vip_month" else
            len({a.year for a, _ in months}) * len(plan.market.symbols))
    if "fina_indicator" in plan.event_endpoints:
        by_endpoint["fina_indicator_vip" if plan.income_strategy == "vip_month" else "fina_indicator"] = (
            len(list(_quarter_ends(first, last))) if plan.income_strategy == "vip_month" else
            len({a.year for a, _ in report_months}) * len(plan.market.symbols))
    if "top10_holders" in plan.event_endpoints:
        by_endpoint["top10_holders"] = len(plan.market.symbols)
    if "dividend" in plan.event_endpoints:
        by_endpoint["dividend"] = (last - announcement_first).days + 1
    if "stk_limit" in plan.event_endpoints:
        by_endpoint["stk_limit_upper_bound"] = (_date(plan.market.end_session) - _date(plan.market.start_session)).days + 1
    return {"market": estimate_bulk_job(plan.market), "calendar": estimate_bulk_job(_calendar_job(plan)),
            "event_requests_pre_calendar_upper_bound": sum(by_endpoint.values()),
            "event_by_endpoint": by_endpoint,
            "membership_source": (
                {"mode": "tushare_dated_index_weight_snapshots",
                 "saved_weight_raw_batches": len(plan.membership_source["index_weight_raw_batch_ids"]),
                 "saved_listing_raw_batches": len(plan.membership_source["stock_basic_raw_batch_ids"]),
                 "between_snapshots": plan.membership_source["between_snapshots"],
                 "additional_run_network_requests": 0}
                if plan.membership_source else None),
            "cap_split_policy": {
                "vip_financial": "no documented numeric VIP cap; retain each observed response without inferred per-symbol fallback",
                "documented_capped_endpoints": ["dividend", "stk_limit"],
                "basis": "selector counts exclude bounded cap splits and retries; observed response is not an independent supplier census"},
            "announcement_refresh_start": announcement_first.isoformat(),
            "historical_refresh_policy": ("explicit new bulk plan for corrections older than the daily window"
                                           if plan.announcement_start else "initial bulk history"),
            "coverage_basis": "planned selectors only; empty supplier responses do not prove absence"}


def run_full_sources(store: LocalStore, *, plan: FullSourcePlan, client: Any,
                     operation_id: str, base_snapshot: str | None, promote: bool = True,
                     max_attempts: int = 3, min_interval_seconds: float = 0,
                     max_workers: int = 8, global_calls_per_minute: int = 300,
                     stock_basic_calls_per_minute: int = 50,
                     clock: Callable[[], datetime] | None = None,
                     monotonic: Callable[[], float] | None = None,
                     sleeper: Callable[[float], None] | None = None) -> OperationResult:
    if not isinstance(store, LocalStore) or not isinstance(plan, FullSourcePlan):
        raise DataError("full source job needs LocalStore and FullSourcePlan")
    options = {"plan": plan.to_dict(), "base_snapshot": base_snapshot, "promote": promote,
               "max_attempts": max_attempts, "min_interval_seconds": min_interval_seconds,
               "max_workers": max_workers, "global_calls_per_minute": global_calls_per_minute,
               "stock_basic_calls_per_minute": stock_basic_calls_per_minute}
    fingerprint = _hash(options)
    state = store.read_operation(operation_id)
    if state is not None and (state.get("kind") != "full_source_job_v1" or state.get("fingerprint") != fingerprint):
        raise ConflictError("operation ID is bound to another full source job")
    if state is None:
        state = {"kind": "full_source_job_v1", "fingerprint": fingerprint,
                 "plan_fingerprint": plan.fingerprint(), "base_snapshot": base_snapshot,
                 "candidate_snapshot": base_snapshot, "next_event_chunk": 0,
                 "completed_event_requests": 0, "event_raw_rows": 0,
                 "event_raw_attempts": 0, "status": "running", "phase": "market",
                 "started_at": datetime.now(timezone.utc).isoformat()}
        store.write_operation(operation_id, state)
    if state["status"] == "success":
        result = state["result"]
        return OperationResult(result["snapshot_id"], result["changed"], operation_id)
    from .builder import operation_context
    runtime = operation_context(store, state, operation_id, options)
    common = {"client": client, "max_attempts": max_attempts,
              "min_interval_seconds": min_interval_seconds, "max_workers": max_workers,
              "global_calls_per_minute": global_calls_per_minute,
              "stock_basic_calls_per_minute": stock_basic_calls_per_minute,
              "clock": clock, "monotonic": monotonic, "sleeper": sleeper}
    try:
        market = run_bulk_job(store, plan=plan.market,
                              operation_id=f"{operation_id}.market", base_snapshot=base_snapshot,
                              promote=False, **common)
        if state["phase"] == "market":
            state.update(candidate_snapshot=market.snapshot_id, phase="calendar")
            store.write_operation(operation_id, state)
        calendar_plan = _calendar_job(plan)
        calendar = run_bulk_job(store, plan=calendar_plan,
                                operation_id=f"{operation_id}.calendar",
                                base_snapshot=market.snapshot_id, promote=False, **common)
        if state["phase"] == "calendar":
            state.update(candidate_snapshot=calendar.snapshot_id,
                         phase="vendor_listing" if plan.membership_source else "events")
            store.write_operation(operation_id, state)
        if plan.membership_source and state["phase"] == "vendor_listing":
            from .vendor_listing import publish_vendor_listing
            listing_op = f"{operation_id}.listing.publish"
            base = state["candidate_snapshot"]
            listing = publish_vendor_listing(
                store,
                stock_basic_raw_batch_ids=plan.membership_source["stock_basic_raw_batch_ids"],
                identity_map=dict(plan.market.identity_map),
                base_snapshot=base, operation_id=listing_op, promote=False)
            store.write_operation(listing_op, {
                "kind": "full_source_vendor_listing_publish_v1", "status": "success",
                "plan_fingerprint": plan.fingerprint(), "base_snapshot": base,
                "result": listing})
            state.update(candidate_snapshot=listing["snapshot_id"], phase="membership",
                         listing_raw_batches=len(plan.membership_source["stock_basic_raw_batch_ids"]))
            store.write_operation(operation_id, state)
        if plan.membership_source and state["phase"] == "membership":
            from .vendor_membership import publish_vendor_membership
            member_op = f"{operation_id}.membership.publish"
            base = state["candidate_snapshot"]
            published = publish_vendor_membership(
                store,
                index_weight_raw_batch_ids=plan.membership_source["index_weight_raw_batch_ids"],
                identity_map=dict(plan.market.identity_map),
                verified_through=plan.membership_source["verified_through"],
                between_snapshots=plan.membership_source["between_snapshots"],
                base_snapshot=base, operation_id=member_op, promote=False)
            store.write_operation(member_op, {
                "kind": "full_source_vendor_membership_publish_v1", "status": "success",
                "plan_fingerprint": plan.fingerprint(), "base_snapshot": base,
                "result": published})
            state.update(candidate_snapshot=published["snapshot_id"], phase="events",
                         membership_complete_states=published["complete_state_count"])
            store.write_operation(operation_id, state)
        calendar_state = store.read_operation(f"{operation_id}.calendar")
        market_state = store.read_operation(f"{operation_id}.market")
        if not calendar_state or not market_state or not isinstance(calendar_state.get("trading_sessions"), list):
            raise CoverageError("completed calendar checkpoint lacks open sessions")
        next_open = _next_open_map(plan, calendar_state["trading_sessions"])
        chunks = list(_event_chunks(plan, next_open, market_state.get("trading_sessions") or ()))
        state["total_event_chunks"] = len(chunks)
        state["planned_event_requests"] = sum(len(specs) for _, _, specs, _ in chunks)
        store.write_operation(operation_id, state)
        pacer = BatchRateLimiter(global_per_minute=global_calls_per_minute,
                                 stock_per_minute=stock_basic_calls_per_minute,
                                 extra_gap=min_interval_seconds,
                                 ticks=monotonic or time.monotonic,
                                 sleep=sleeper or time.sleep)
        for index, (endpoint, label, specs, calendar_map) in enumerate(chunks):
            if index < state["next_event_chunk"]:
                continue
            if state.get("event_chunk_raw_log_offset") is None:
                state["event_chunk_raw_log_offset"] = store.raw_log_size()
                state.update(phase=endpoint, current_event_chunk=index, status="running")
                store.write_operation(operation_id, state)
            chunk_op = f"{operation_id}.e.c{index:06d}"
            split_budget = plan.max_event_requests_per_chunk
            if len(plan.market.symbols) > split_budget and endpoint in {"dividend", "stk_limit"}:
                # A documented all-market cap may split to the selected
                # securities. Reserve at most three such dates per chunk.
                split_budget = min(6000, len(specs) + 64 + 3 * len(plan.market.symbols))
            fetched = run_batch_chunk(store, specs=specs, client=client,
                                      operation_id=chunk_op, plan_fingerprint=plan.fingerprint(),
                                      identity_map=dict(plan.market.identity_map),
                                      raw_log_offset=state["event_chunk_raw_log_offset"],
                                      max_attempts=max_attempts, max_workers=max_workers,
                                      global_calls_per_minute=global_calls_per_minute,
                                      stock_basic_calls_per_minute=stock_basic_calls_per_minute,
                                      max_total_requests_per_chunk=split_budget,
                                      min_interval_seconds=min_interval_seconds,
                                      rate_limiter=pacer, event_calendar_map=calendar_map,
                                      clock=clock, monotonic=monotonic, sleeper=sleeper)
            published = apply_saved_raw(store, base_snapshot=state["candidate_snapshot"],
                                        raw_batch_ids=fetched["raw_batch_ids"],
                                        operation_id=f"{chunk_op}.publish", promote=False,
                                        build_context={"source_plan": plan.fingerprint(),
                                                       "builder": runtime["builder"], "run_operation_id": operation_id,
                                                       "execution_options": {k: v for k, v in options.items() if k != "plan"},
                                                       "event_endpoint": endpoint,
                                                       "selector_window": label,
                                                       "coverage": "observed supplier responses, not complete vendor vintage",
                                                       "bulk_chunk": {"job_fingerprint": plan.fingerprint(),
                                                                      "chunk_index": index}})
            state["candidate_snapshot"] = published.snapshot_id
            state["next_event_chunk"] = index + 1
            state["completed_event_requests"] += fetched["completed_requests"]
            state["event_raw_rows"] += fetched["raw_rows"]
            state["event_raw_attempts"] += fetched["raw_attempts"]
            state.pop("event_chunk_raw_log_offset", None)
            state.pop("current_event_chunk", None)
            state.pop("error", None)
            store.write_operation(operation_id, state)
        candidate = state["candidate_snapshot"]
        if candidate is None:
            raise DataError("full source job produced no Snapshot")
        if promote:
            store.promote_existing_snapshot(candidate, expected_current=base_snapshot)
        result = OperationResult(candidate, candidate != base_snapshot, operation_id)
        state.update(status="success", phase="complete",
                     completed_at=datetime.now(timezone.utc).isoformat(),
                     result={"snapshot_id": candidate, "changed": result.changed})
        store.write_operation(operation_id, state)
        return result
    except Exception as exc:
        state.update(status="failed", error={"type": type(exc).__name__})
        store.write_operation(operation_id, state)
        raise


def full_source_status(store: LocalStore, *, plan: FullSourcePlan,
                       operation_id: str) -> dict[str, Any]:
    state = store.read_operation(operation_id)
    if state is None:
        return {"status": "not_started", "phase": "market", "operation_id": operation_id}
    if state.get("kind") not in {"full_source_job_v1", "full_source_financial_continuation_v1"} or state.get("plan_fingerprint") != plan.fingerprint():
        raise ConflictError("checkpoint does not match full source plan")
    started = state.get("started_at")
    elapsed = (((datetime.fromisoformat(state["completed_at"]) if state.get("completed_at")
                 else datetime.now(timezone.utc)) - datetime.fromisoformat(started)).total_seconds()
               if started else None)
    result = {key: state.get(key) for key in
            ("status", "phase", "candidate_snapshot", "next_event_chunk",
             "total_event_chunks", "completed_event_requests", "planned_event_requests",
             "event_raw_rows", "event_raw_attempts", "listing_raw_batches",
             "membership_complete_states", "reused_event_requests")} | {
             "operation_id": operation_id, "elapsed_seconds": elapsed}
    from .bulk_jobs import bulk_job_status
    reference_operation = state.get("reference_operation_id", state.get("source_operation_id", operation_id))
    for phase, child_plan in (("market", plan.market), ("calendar", _calendar_job(plan))):
        child = store.read_operation(f"{reference_operation}.{phase}")
        if child is not None:
            result[phase] = bulk_job_status(store, plan=child_plan,
                                             operation_id=f"{reference_operation}.{phase}")
    from .sources import _rows
    active = state.get("current_event_chunk")
    if active is not None:
        chunk_op = f"{operation_id}.e.c{active:06d}"
        child = store.read_operation(chunk_op)
        if child:
            records = store.find_raw_by_operation(
                chunk_op, since_offset=state.get("event_chunk_raw_log_offset", 0),
                shared_profiles=True)
            result["active_event_raw_attempts"] = len(records)
            result["active_event_completed_requests"] = sum(
                item["status"] in {"done", "split"} for item in child["tasks"])
            result["active_event_raw_rows"] = sum(
                len(_rows(store.read_raw_record(raw)))
                for raw in records.values() if raw["status"] in {"success", "empty", "cap"})
    return result


def verify_full_sources(store: LocalStore, *, plan: FullSourcePlan,
                        operation_id: str) -> dict[str, Any]:
    state = store.read_operation(operation_id)
    continuation = state and state.get("kind") == "full_source_financial_continuation_v1"
    if (not state or state.get("status") not in ({"success", "collected"} if continuation else {"success"}) or
            state.get("plan_fingerprint") != plan.fingerprint()):
        raise CoverageError("full source job has no matching completed checkpoint")
    if continuation:
        from .financial_continuation import verify_continuation_selectors
        reuse_report = verify_continuation_selectors(store, state, plan, operation_id)
    reference_operation = state.get("reference_operation_id", state.get("source_operation_id", operation_id)) if continuation else operation_id
    market = verify_bulk_job(store, plan=plan.market, operation_id=f"{reference_operation}.market")
    calendar = verify_bulk_job(store, plan=_calendar_job(plan), operation_id=f"{reference_operation}.calendar")
    snapshot = store.load_snapshot(state["result"]["snapshot_id"])
    selected = 0
    referenced_raw = {batch_id for domain in snapshot["domains"].values()
                      for batch_id in domain["raw_batch_ids"]}
    prior_operation = store.read_operation(
        f"{reference_operation}.membership.publish" if plan.membership_source else
        f"{reference_operation}.calendar")
    if not prior_operation or prior_operation.get("status") != "success":
        raise CoverageError("full source event base is not published")
    expected_base = prior_operation["result"]["snapshot_id"]
    if plan.membership_source:
        from .vendor_listing import (PROFILE as LISTING_PROFILE, _source_rows,
                                     vendor_listing_source_chain)
        from .vendor_membership import (PROFILE as MEMBERSHIP_PROFILE, _selected_groups,
                                        vendor_membership_source_chain)
        listing_op = store.read_operation(f"{reference_operation}.listing.publish")
        if (not listing_op or listing_op.get("status") != "success" or
                listing_op.get("kind") != "full_source_vendor_listing_publish_v1" or
                listing_op.get("plan_fingerprint") != plan.fingerprint() or
                prior_operation.get("kind") != "full_source_vendor_membership_publish_v1" or
                prior_operation.get("plan_fingerprint") != plan.fingerprint() or
                prior_operation.get("base_snapshot") != listing_op["result"]["snapshot_id"] or
                not {"listing_events", "universe_membership"} <= set(snapshot["domains"])):
            raise CoverageError("Tushare listing or membership publication is incomplete")
        identity = dict(plan.market.identity_map)
        listing_domain = snapshot["domains"]["listing_events"]
        listing_chain = vendor_listing_source_chain(store, listing_domain)
        if listing_domain.get("source_profile", {}).get("id") != LISTING_PROFILE["id"] or not listing_chain:
            raise CoverageError("listing_events is not sourced from Tushare stock_basic")
        listing_frozen = listing_chain[-1]
        listed_ids = listing_frozen["stock_basic_raw_batch_ids"]
        planned_listed_ids = plan.membership_source["stock_basic_raw_batch_ids"]
        if (not set(listed_ids) <= set(planned_listed_ids) or
                any(identity.get(code) != stable
                    for code, stable in listing_frozen["identity_map"].items())):
            raise CoverageError("Tushare listing source configuration changed")
        def listing_values(ids):
            rows, _ = _source_rows(store, ids, identity)
            return sorted((row["source_code"], row["security_id"], row["event_type"],
                           row["event_date"], row["listing_date"], row["delisting_date"],
                           row["vendor_list_status"]) for row in rows)
        if listing_values(planned_listed_ids) != listing_values(listed_ids):
            raise CoverageError("unpublished stock_basic Raw changes listing facts")
        member_domain = snapshot["domains"]["universe_membership"]
        member_chain = vendor_membership_source_chain(store, member_domain)
        if member_domain.get("source_profile", {}).get("id") != MEMBERSHIP_PROFILE["id"] or not member_chain:
            raise CoverageError("universe_membership is not sourced from Tushare index_weight")
        member_frozen = member_chain[-1]
        frozen_ids = member_frozen["index_weight_raw_batch_ids"]
        planned_ids = plan.membership_source["index_weight_raw_batch_ids"]
        if (member_frozen["verified_through"] != plan.membership_source["verified_through"] or
                member_frozen["between_snapshots"] != plan.membership_source["between_snapshots"] or
                not set(frozen_ids) <= set(planned_ids) or
                any(identity.get(code) != stable
                    for code, stable in member_frozen["identity_map"].items()) or
                _selected_groups(store, planned_ids, identity) !=
                _selected_groups(store, frozen_ids, identity)):
            raise CoverageError("Tushare membership Raw differs from published economic states")
        for batch_id in set(planned_ids) | set(planned_listed_ids):
            store.read_raw_record(store.get_raw(batch_id))
    unchanged_raw = 0
    calendar_state = store.read_operation(f"{reference_operation}.calendar")
    market_state = store.read_operation(f"{reference_operation}.market")
    event_chunks = list(_event_chunks(plan, _next_open_map(plan, calendar_state["trading_sessions"]),
                                     market_state["trading_sessions"]))
    if len(event_chunks) != state["total_event_chunks"]:
        raise CoverageError("event chunks do not cover the frozen plan")
    for index in range(state["total_event_chunks"]):
        chunk_op = f"{operation_id}.e.c{index:06d}"
        child = store.read_operation(chunk_op)
        if not child or child.get("status") != "success":
            raise CoverageError(f"event chunk {index} is incomplete")
        verify_batch_selectors(store, operation_id=chunk_op, specs=event_chunks[index][2],
                               plan_fingerprint=plan.fingerprint(), identity_map=dict(plan.market.identity_map))
        published = store.read_operation(f"{chunk_op}.publish")
        if (not published or published.get("status") != "success" or
                published.get("base_snapshot") != expected_base or
                published.get("raw_batch_ids") != child["selected_raw_batch_ids"]):
            raise CoverageError(f"event chunk {index} has no matching canonical publication")
        candidate = published["result"]["snapshot_id"]
        omitted: dict[str, list[dict[str, Any]]] = {}
        for batch_id in child["selected_raw_batch_ids"]:
            raw = store.get_raw(batch_id)
            if raw["status"] not in {"success", "empty"}:
                raise CoverageError("selected event Raw is not successful")
            store.read_raw_record(raw)  # independent byte-hash verification
            if batch_id not in referenced_raw:
                omitted.setdefault(raw["domain"], []).append(raw)
            selected += 1
        if omitted:
            before = store.load_snapshot(expected_base)
            after = store.load_snapshot(candidate)
            for domain, raws in omitted.items():
                if before["domains"].get(domain) != after["domains"].get(domain):
                    raise CoverageError("event Raw absent after a changed domain publication")
                _verify_unchanged_event_rows(store, before["domains"].get(domain), raws)
                unchanged_raw += len(raws)
        expected_base = candidate
    if expected_base != state["result"]["snapshot_id"]:
        raise CoverageError("event publication chain does not reach final Snapshot")
    domains = snapshot["domains"]
    needed = {"income": "financial_events", "balancesheet": "balance_sheet_events",
              "cashflow": "cash_flow_events", "fina_indicator": "financial_indicator_events",
              "dividend": "corporate_actions",
              "top10_holders": "top_holders_reports", "stk_limit": "price_limits"}
    missing = [needed[name] for name in plan.event_endpoints if needed[name] not in domains]
    if missing:
        raise CoverageError(f"completed Snapshot lacks event domains: {missing}")
    return {"verified": True, "snapshot_id": state["result"]["snapshot_id"],
            "market_verified": market.get("verified"),
            "calendar_verified": calendar.get("verified"),
            "selected_event_raw_batches": selected,
            "unchanged_event_raw_batches": unchanged_raw,
            "event_raw_rows": state["event_raw_rows"],
            **(reuse_report if continuation else {}),
            "coverage_basis": "selected Raw requests, published facts and verified unchanged receipts; supplier completeness unverified"}


def _verify_unchanged_event_rows(store: LocalStore, domain: Mapping[str, Any] | None,
                                 raws: Sequence[Mapping[str, Any]]) -> None:
    """Independently recompute whether omitted successful receipts add facts."""
    from .sources import normalize_batch
    from .updates import _SavedBatch, _merge_rows, _merge_terminal, _partition

    if domain is None:
        raise CoverageError("unchanged event domain is absent")
    contract = domain["contract"]
    incoming: dict[str, list[dict[str, Any]]] = {}
    for raw in raws:
        for row in normalize_batch(_SavedBatch(store, raw), raw):
            incoming.setdefault(_partition(row, contract), []).append(row)
    parts = {part["partition"]: part for part in domain["partitions"]}
    for key, rows in incoming.items():
        previous = parts.get(key)
        if previous is None:
            raise CoverageError("unchanged event receipt contains a new partition")
        existing = store.read_partition(previous).to_pylist()
        order = domain["source_profile"].get("revision_order")
        if order == "announcement_day_then_terminal_v1":
            merge_contract = {**contract, "logical_key": [
                *contract["logical_key"], "actual_announcement_date"]}
            _, changed = _merge_terminal(existing, rows, merge_contract)
        elif order == "terminal_observation_v1":
            _, changed = _merge_terminal(existing, rows, contract)
        else:
            _, changed = _merge_rows(existing, rows, contract)
        if changed:
            raise CoverageError("unchanged event receipt contains unpublished canonical facts")
