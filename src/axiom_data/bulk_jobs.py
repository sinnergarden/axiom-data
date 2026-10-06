"""Bounded, restartable local-source jobs with one final current-pointer swap.

The saved plan freezes the exact security IDs, endpoint set and date scope.
Each candidate Snapshot is immutable and inspectable, but `current` advances
only after every required chunk has a usable Raw response and a candidate.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
import re
from typing import Any, Callable, Mapping, Sequence

from .protocols import ConflictError, DataError, OperationResult
from .provider_local import CONTRACTS, FIELDS
from .batch_fetch import BatchRateLimiter, run_batch_chunk, verify_batch_selectors
from .sources import _rows
from .storage import LocalStore
from .updates import apply_saved_raw
from .builder import operation_context


_SYMBOL = re.compile(r"^[0-9]{6}\.(?:SH|SZ)$")
_INDEX = re.compile(r"^[0-9]{6}\.(?:SH|SZ|CSI)$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def _day(value: str) -> date:
    try:
        if not isinstance(value, str):
            raise ValueError
        return date.fromisoformat(value)
    except ValueError as exc:
        raise DataError(f"invalid ISO session: {value!r}") from exc


def _months(start: date, end: date) -> list[tuple[str, str]]:
    result = []
    current = date(start.year, start.month, 1)
    while current <= end:
        next_month = date(current.year + (current.month == 12), current.month % 12 + 1, 1)
        first, last = max(start, current), min(end, next_month - timedelta(days=1))
        if first <= last:
            result.append((first.strftime("%Y%m%d"), last.strftime("%Y%m%d")))
        current = next_month
    return result


@dataclass(frozen=True)
class BulkJobPlan:
    """Frozen job scope; date bounds are inclusive exchange-local ISO sessions."""

    mode: str
    symbols: tuple[str, ...]
    start_session: str
    end_session: str
    identity_map: tuple[tuple[str, str], ...]
    benchmark_codes: tuple[str, ...]
    index_codes: tuple[str, ...]
    endpoints: tuple[str, ...]
    max_requests_per_chunk: int = 6000
    request_strategy: str = "trading_day_market_v2"

    def to_dict(self) -> dict[str, Any]:
        """Return a stable, strict-JSON scope for saving outside the data root."""
        body = {"schema_version": "local_bulk_job_v2", "mode": self.mode,
                "symbols": list(self.symbols), "start_session": self.start_session,
                "end_session": self.end_session,
                "identity_map": dict(self.identity_map),
                "benchmark_codes": list(self.benchmark_codes),
                "index_codes": list(self.index_codes), "endpoints": list(self.endpoints),
                "max_requests_per_chunk": self.max_requests_per_chunk,
                "request_strategy": self.request_strategy}
        return body

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "BulkJobPlan":
        """Validate and restore a saved plan; unknown schema/keys fail closed."""
        if not isinstance(value, Mapping) or value.get("schema_version") != "local_bulk_job_v2":
            raise DataError("unsupported bulk job plan schema")
        keys = {"schema_version", "mode", "symbols", "start_session",
                "end_session", "identity_map", "benchmark_codes",
                "index_codes", "endpoints", "max_requests_per_chunk", "request_strategy"}
        if set(value) != keys:
            raise DataError("bulk job plan has missing or unknown fields")
        return plan_bulk_job(mode=value["mode"], symbols=value["symbols"],
                             start_session=value["start_session"], end_session=value["end_session"],
                             identity_map=value["identity_map"],
                             benchmark_codes=value["benchmark_codes"],
                             index_codes=value["index_codes"], endpoints=value["endpoints"],
                             max_requests_per_chunk=value["max_requests_per_chunk"],
                             request_strategy=value["request_strategy"])

    def fingerprint(self) -> str:
        return sha256(json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"),
                                 ensure_ascii=False).encode()).hexdigest()


def plan_bulk_job(*, mode: str, symbols: Sequence[str], start_session: str,
                  end_session: str, identity_map: Mapping[str, str],
                  benchmark_codes: Sequence[str] = (), index_codes: Sequence[str] = (),
                  endpoints: Sequence[str] | None = None,
                  max_requests_per_chunk: int = 6000,
                  request_strategy: str = "trading_day_market_v2") -> BulkJobPlan:
    """Freeze init/bulk/daily collection scope without fetching or writing.

    `identity_map` includes caller-reviewed IDs for returned index members too;
    changing it requires a different job and potentially a domain rebuild.
    """
    if type(max_requests_per_chunk) is not int or not 1 <= max_requests_per_chunk <= 6000:
        raise DataError("max_requests_per_chunk must be an integer in [1,6000]")
    if request_strategy != "trading_day_market_v2":
        raise DataError("unsupported bulk job request strategy")
    if mode not in {"init", "bulk", "daily"}:
        raise DataError("mode must be init, bulk, or daily")
    if _day(start_session) > _day(end_session) or (mode == "daily" and start_session != end_session):
        raise DataError("plan dates are reversed or daily spans multiple dates")
    selected = tuple(sorted(symbols))
    if len(set(selected)) != len(selected) or any(not _SYMBOL.fullmatch(s) for s in selected):
        raise DataError("symbols must be unique exchange-qualified A-share codes")
    if not isinstance(identity_map, Mapping) or not set(selected).issubset(identity_map):
        raise DataError("identity_map must bind every selected source symbol")
    identities = dict(identity_map)
    if (any(not isinstance(k, str) or not _SYMBOL.fullmatch(k) for k in identities) or
            any(not isinstance(v, str) or not v.strip() for v in identities.values()) or
            len(set(identities.values())) != len(identities)):
        raise DataError("identity_map requires distinct nonempty stable IDs")
    benchmarks, indices = tuple(benchmark_codes), tuple(index_codes)
    if (len(set(benchmarks)) != len(benchmarks) or len(set(indices)) != len(indices) or
            any(not isinstance(v, str) or not _INDEX.fullmatch(v) for v in (*benchmarks, *indices))):
        raise DataError("benchmark and index codes must be unique qualified codes")
    default = (("trade_cal", "stock_basic") if mode == "init" else
               ("trade_cal", "stock_basic", "daily", "adj_factor", "suspend_d", "index_daily", "index_weight"))
    wanted = tuple(default if endpoints is None else endpoints)
    if len(set(wanted)) != len(wanted) or any(v not in CONTRACTS for v in wanted):
        raise DataError("endpoints must be unique declared local Tushare adapters")
    effective = tuple(v for v in wanted if not (v == "index_daily" and not benchmarks) and
                      not (v == "index_weight" and not indices))
    if not effective:
        raise DataError("plan needs at least one endpoint")
    if (set(effective).intersection({"daily", "adj_factor", "suspend_d"}) and
            "trade_cal" not in effective):
        raise DataError("trading-day batch requests require trade_cal in the same frozen plan")
    return BulkJobPlan(mode, selected, start_session, end_session,
                       tuple(sorted(identities.items())),
                       tuple(sorted(benchmarks)), tuple(sorted(indices)),
                       effective, max_requests_per_chunk, request_strategy)


def _span_windows(plan: BulkJobPlan, *, max_months: int = 144) -> list[tuple[str, str]]:
    spans = _months(_day(plan.start_session), _day(plan.end_session))
    return [(spans[i][0], spans[min(i + max_months, len(spans)) - 1][1])
            for i in range(0, len(spans), max_months)]


def _v2_spec(endpoint: str, params: Mapping[str, str],
             canonical_symbols: Sequence[str] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"endpoint": endpoint, "params": dict(sorted(params.items())),
                              "fields": list(FIELDS[endpoint])}
    if canonical_symbols is not None:
        result["canonical_symbols"] = list(canonical_symbols)
    return result


def _v2_reference_specs(plan: BulkJobPlan) -> list[dict[str, Any]]:
    wanted = set(plan.endpoints)
    specs: list[dict[str, Any]] = []
    windows = _span_windows(plan)
    if "trade_cal" in wanted:
        for exchange in ("SSE", "SZSE"):
            specs.extend(_v2_spec("trade_cal", {"exchange": exchange,
                                                 "start_date": start, "end_date": end})
                         for start, end in windows)
    if "stock_basic" in wanted:
        for exchange in ("SSE", "SZSE"):
            selected = [s for s in plan.symbols
                        if s.endswith(".SH" if exchange == "SSE" else ".SZ")]
            for status in ("L", "D", "P"):
                specs.append(_v2_spec("stock_basic", {"exchange": exchange,
                                                       "list_status": status}, selected))
    if "index_daily" in wanted:
        for code in plan.benchmark_codes:
            specs.extend(_v2_spec("index_daily", {"ts_code": code,
                                                   "start_date": start, "end_date": end})
                         for start, end in windows)
    return specs


def _v2_month_specs(plan: BulkJobPlan, sessions: Sequence[str],
                    first: str, last: str) -> list[dict[str, Any]]:
    wanted = set(plan.endpoints)
    specs = [_v2_spec(endpoint, {"trade_date": session}, plan.symbols)
             for session in sessions if first <= session <= last
             for endpoint in ("daily", "adj_factor", "suspend_d") if endpoint in wanted]
    if "index_weight" in wanted:
        specs.extend(_v2_spec("index_weight", {"index_code": code,
                                               "start_date": first, "end_date": last},
                              plan.symbols)
                     for code in plan.index_codes)
    return specs


def _v2_session_upper_bound(plan: BulkJobPlan) -> int:
    return (_day(plan.end_session) - _day(plan.start_session)).days + 1


def _v2_calendar_sessions(store: LocalStore, raw_ids: Sequence[str],
                          plan: BulkJobPlan) -> list[str]:
    """Require explicit SSE/SZSE rows for every date; freeze their open union."""
    from .protocols import CoverageError

    seen: dict[tuple[str, str], str] = {}
    records = store.get_raw_many(raw_ids)
    for batch_id in raw_ids:
        raw = records[batch_id]
        if raw["request"]["endpoint"] != "trade_cal":
            continue
        for row in _rows(store.read_raw_record(raw)):
            exchange, day, flag = row.get("exchange"), row.get("cal_date"), str(row.get("is_open"))
            if exchange not in {"SSE", "SZSE"} or flag not in {"0", "1"}:
                raise DataError("saved trading calendar has invalid exchange or open flag")
            key = exchange, day
            if key in seen and seen[key] != flag:
                raise ConflictError("saved trading calendar disagrees across requests")
            seen[key] = flag
    current, end = _day(plan.start_session), _day(plan.end_session)
    opened: list[str] = []
    while current <= end:
        day = current.strftime("%Y%m%d")
        if any((exchange, day) not in seen for exchange in ("SSE", "SZSE")):
            raise CoverageError(f"trading calendar lacks explicit exchange rows for {day}")
        if any(seen[(exchange, day)] == "1" for exchange in ("SSE", "SZSE")):
            opened.append(day)
        current += timedelta(days=1)
    return opened


def estimate_bulk_job(plan: BulkJobPlan, *, min_interval_seconds: float | None = None) -> dict[str, Any]:
    """Pure call/chunk count, rate floor, and selected-response memory bound."""
    if not isinstance(plan, BulkJobPlan):
        raise DataError("explicit BulkJobPlan is required")
    gap = 0.0 if min_interval_seconds is None else min_interval_seconds
    if not isinstance(gap, (int, float)) or not 0 <= gap <= 3600:
        raise DataError("min_interval_seconds must be between 0 and 3600")
    refs = _v2_reference_specs(plan)
    months = _months(_day(plan.start_session), _day(plan.end_session))
    per_day = sum(endpoint in plan.endpoints for endpoint in
                  ("daily", "adj_factor", "suspend_d"))
    monthly = len(months) * len(plan.index_codes) if "index_weight" in plan.endpoints else 0
    low = len(refs) + monthly
    upper = low + _v2_session_upper_bound(plan) * per_day
    month_max = 31 * per_day + (len(plan.index_codes) if "index_weight" in plan.endpoints else 0)
    by_endpoint = {endpoint: sum(s["endpoint"] == endpoint for s in refs)
                   for endpoint in plan.endpoints}
    for endpoint in ("daily", "adj_factor", "suspend_d"):
        if endpoint in plan.endpoints:
            by_endpoint[endpoint] = _v2_session_upper_bound(plan)
    if "index_weight" in plan.endpoints:
        by_endpoint["index_weight"] = monthly
    chunks = ((len(refs) + plan.max_requests_per_chunk - 1) // plan.max_requests_per_chunk +
              sum((len(_v2_month_specs(plan, (), first, last)) +
                   31 * per_day + plan.max_requests_per_chunk - 1) //
                  plan.max_requests_per_chunk for first, last in months))
    pacing = max(60 / 300, gap)
    return {"requests": upper, "requests_lower_bound": low,
            "request_count_basis": "calendar_day_upper_bound_before_calendar_fetch",
            "chunks": chunks, "max_requests_in_chunk": min(plan.max_requests_per_chunk,
                                                        max(len(refs), month_max)),
            "max_selected_raw_responses_in_memory": min(plan.max_requests_per_chunk,
                                                          max(len(refs), month_max)),
            "by_endpoint": by_endpoint,
            "minimum_pacing_seconds": max(0, low - 1) * pacing,
            "minimum_pacing_days": max(0, low - 1) * pacing / 86400,
            "pacing_seconds_at_request_upper_bound": max(0, upper - 1) * pacing,
            "pacing_note": "upper-bound dates; exact open-day count follows saved calendar; retries, cap splits and I/O excluded",
            "max_attempts_multiplier_excluded": True}

def run_bulk_job(store: LocalStore, *, plan: BulkJobPlan, client: Any,
                 operation_id: str, base_snapshot: str | None,
                 promote: bool = True, max_attempts: int = 3,
                 min_interval_seconds: float | None = None,
                 max_workers: int = 8,
                 global_calls_per_minute: int = 300,
                 stock_basic_calls_per_minute: int = 50,
                 clock: Callable[[], datetime] | None = None,
                 monotonic: Callable[[], float] | None = None,
                 sleeper: Callable[[float], None] | None = None) -> OperationResult:
    """Resume bounded chunks and atomically promote only the complete job.

    A failed/capped chunk leaves `current` unchanged. Saved successful Raw and
    candidate Snapshots are reused by the same operation ID. A changed current
    pointer conflicts at final promotion. No source call occurs on a completed
    job. `promote=False` leaves its final candidate inspectable by ID.
    """
    if not isinstance(store, LocalStore) or not isinstance(plan, BulkJobPlan):
        raise DataError("store and explicit BulkJobPlan are required")
    if not isinstance(operation_id, str) or not _ID.fullmatch(operation_id):
        raise DataError("operation_id must be a safe durable identifier")
    return _run_bulk_job_v2(store, plan=plan, client=client,
                            operation_id=operation_id, base_snapshot=base_snapshot,
                            promote=promote, max_attempts=max_attempts,
                            min_interval_seconds=0 if min_interval_seconds is None else min_interval_seconds,
                            max_workers=max_workers,
                            global_calls_per_minute=global_calls_per_minute,
                            stock_basic_calls_per_minute=stock_basic_calls_per_minute,
                            clock=clock, monotonic=monotonic, sleeper=sleeper)


def _run_bulk_job_v2(store: LocalStore, *, plan: BulkJobPlan, client: Any,
                     operation_id: str, base_snapshot: str | None, promote: bool,
                     max_attempts: int, min_interval_seconds: float,
                     max_workers: int, global_calls_per_minute: int,
                     stock_basic_calls_per_minute: int,
                     clock: Callable[[], datetime] | None,
                     monotonic: Callable[[], float] | None,
                     sleeper: Callable[[float], None] | None) -> OperationResult:
    """Reference batch, frozen open-day set, then month-sized candidates."""
    from .protocols import CoverageError

    identity = {"plan": plan.to_dict(), "base_snapshot": base_snapshot,
                "promote": promote, "max_attempts": max_attempts,
                "min_interval_seconds": min_interval_seconds, "max_workers": max_workers,
                "global_calls_per_minute": global_calls_per_minute,
                "stock_basic_calls_per_minute": stock_basic_calls_per_minute}
    fingerprint = sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    state = store.read_operation(operation_id)
    if state is not None and (state.get("kind") != "bulk_job_v2" or state.get("fingerprint") != fingerprint):
        raise ConflictError("operation_id is bound to a different bulk job")
    if state is None:
        reference_chunk_count = (len(_v2_reference_specs(plan)) + plan.max_requests_per_chunk - 1) // plan.max_requests_per_chunk
        state = {"kind": "bulk_job_v2", "fingerprint": fingerprint,
                 "plan_fingerprint": plan.fingerprint(), "base_snapshot": base_snapshot,
                 "candidate_snapshot": base_snapshot, "promote": promote,
                 "next_chunk": 0, "total_chunks": None,
                 "reference_chunk_count": reference_chunk_count, "phase": "reference",
                 "status": "running", "started_at": datetime.now(timezone.utc).isoformat(),
                 "completed_requests": 0, "planned_requests": None,
                 "raw_rows": 0, "raw_attempts": 0}
        store.write_operation(operation_id, state)
    if state.get("status") == "success":
        value = state["result"]
        return OperationResult(value["snapshot_id"], value["changed"], operation_id)
    ticks, sleep = monotonic, sleeper
    runtime = operation_context(store, state, operation_id, identity)
    import time
    if state.get("current_chunk_index") is not None:
        chunk_op = f"{operation_id}.v2.c{state['current_chunk_index']:06d}"
        for raw in store.find_raw_by_operation(chunk_op,
                                                since_offset=state.get("chunk_raw_log_offset", 0),
                                                shared_profiles=True).values():
            if state.get("last_observed_at", "") < raw["observed_at"]:
                state["last_observed_at"] = raw["observed_at"]
            if raw["request"]["endpoint"] == "stock_basic" and state.get("last_stock_observed_at", "") < raw["observed_at"]:
                state["last_stock_observed_at"] = raw["observed_at"]
    global_gap = max(60 / global_calls_per_minute if global_calls_per_minute else 0,
                     min_interval_seconds)
    stock_gap = 60 / stock_basic_calls_per_minute if stock_basic_calls_per_minute else 0
    now_utc = datetime.now(timezone.utc)
    due = now_utc
    for name, gap in (("last_observed_at", global_gap),
                      ("last_stock_observed_at", stock_gap)):
        if state.get(name) and gap:
            due = max(due, datetime.fromisoformat(state[name]) + timedelta(seconds=gap))
    if due > now_utc:
        (sleep or time.sleep)((due - now_utc).total_seconds())
    limiter = BatchRateLimiter(global_per_minute=global_calls_per_minute,
                               stock_per_minute=stock_basic_calls_per_minute,
                               extra_gap=min_interval_seconds,
                               ticks=ticks or time.monotonic, sleep=sleep or time.sleep)

    def execute(index: int, specs: Sequence[Mapping[str, Any]], phase: str) -> str:
        if not specs:
            return state["candidate_snapshot"]
        if state.get("chunk_raw_log_offset") is None:
            state["chunk_raw_log_offset"] = store.raw_log_size()
            state.update(phase=phase, status="running", current_chunk_index=index,
                         chunk_request_count=len(specs))
            store.write_operation(operation_id, state)
        chunk_op = f"{operation_id}.v2.c{index:06d}"
        fetched = run_batch_chunk(store, specs=specs, client=client,
                                  operation_id=chunk_op, plan_fingerprint=plan.fingerprint(),
                                  identity_map=dict(plan.identity_map),
                                  raw_log_offset=state["chunk_raw_log_offset"],
                                  max_attempts=max_attempts, max_workers=max_workers,
                                  global_calls_per_minute=global_calls_per_minute,
                                  stock_basic_calls_per_minute=stock_basic_calls_per_minute,
                                  max_total_requests_per_chunk=plan.max_requests_per_chunk,
                                  min_interval_seconds=min_interval_seconds,
                                  rate_limiter=limiter, clock=clock, monotonic=monotonic,
                                  sleeper=sleeper)
        context = {"source_plan": plan.fingerprint(), "mode": plan.mode,
                   "builder": runtime["builder"], "run_operation_id": operation_id,
                   "execution_options": {k: v for k, v in identity.items() if k != "plan"},
                   "scope": {"start": plan.start_session, "end": plan.end_session},
                   "request_strategy": plan.request_strategy,
                   "coverage": "observed responses only; missing facts remain unknown",
                   "revision_policy": "terminal_observation_v1",
                   "bulk_chunk": {"job_fingerprint": plan.fingerprint(),
                                  "chunk_index": index}}
        published = apply_saved_raw(store, base_snapshot=state["candidate_snapshot"],
                                    raw_batch_ids=fetched["raw_batch_ids"],
                                    operation_id=f"{chunk_op}.publish",
                                    build_context=context, promote=False)
        state["candidate_snapshot"] = published.snapshot_id
        state["completed_requests"] += fetched["completed_requests"]
        state["raw_rows"] += fetched["raw_rows"]
        state["raw_attempts"] += fetched["raw_attempts"]
        for name in ("last_observed_at", "last_stock_observed_at"):
            if fetched.get(name) and state.get(name, "") < fetched[name]:
                state[name] = fetched[name]
        if state["planned_requests"] is not None:
            state["planned_requests"] += fetched["total_requests"] - len(specs)
        state["next_chunk"] = index + 1
        state.pop("chunk_raw_log_offset", None)
        state.pop("current_chunk_index", None)
        state.pop("chunk_request_count", None)
        state.pop("error", None)
        store.write_operation(operation_id, state)
        return published.snapshot_id

    try:
        reference_specs = _v2_reference_specs(plan)
        reference_chunks = [reference_specs[i:i + plan.max_requests_per_chunk]
                            for i in range(0, len(reference_specs), plan.max_requests_per_chunk)]
        for index, specs in enumerate(reference_chunks):
            if index >= state["next_chunk"]:
                execute(index, specs, "reference")
        if "trading_sessions" not in state:
            if "trade_cal" in plan.endpoints:
                reference_ids: list[str] = []
                for index in range(len(reference_chunks)):
                    ref_state = store.read_operation(f"{operation_id}.v2.c{index:06d}")
                    if not ref_state or ref_state.get("status") != "success":
                        raise CoverageError("reference calendar fetch is incomplete")
                    reference_ids.extend(ref_state["selected_raw_batch_ids"])
                state["trading_sessions"] = _v2_calendar_sessions(
                    store, reference_ids, plan)
            else:
                state["trading_sessions"] = []
            month_specs = [_v2_month_specs(plan, state["trading_sessions"], first, last)
                           for first, last in _months(_day(plan.start_session), _day(plan.end_session))]
            chunks = [specs[i:i + plan.max_requests_per_chunk]
                      for specs in month_specs for i in range(0, len(specs), plan.max_requests_per_chunk)]
            state["total_chunks"] = len(reference_chunks) + len(chunks)
            reference_extra = sum(
                len(store.read_operation(f"{operation_id}.v2.c{index:06d}")["tasks"]) - len(specs)
                for index, specs in enumerate(reference_chunks))
            state["planned_requests"] = len(reference_specs) + reference_extra + sum(len(chunk) for chunk in chunks)
            state["phase"] = "monthly"
            store.write_operation(operation_id, state)
        else:
            month_specs = [_v2_month_specs(plan, state["trading_sessions"], first, last)
                           for first, last in _months(_day(plan.start_session), _day(plan.end_session))]
            chunks = [specs[i:i + plan.max_requests_per_chunk]
                      for specs in month_specs for i in range(0, len(specs), plan.max_requests_per_chunk)]
        for index, specs in enumerate(chunks, start=len(reference_chunks)):
            if index < state["next_chunk"]:
                continue
            execute(index, specs, "monthly")
        candidate = state["candidate_snapshot"]
        if candidate is None:
            raise DataError("bulk job produced no candidate Snapshot")
        if promote:
            store.promote_existing_snapshot(candidate, expected_current=base_snapshot)
        result = OperationResult(candidate, candidate != base_snapshot, operation_id)
        state.update(status="success", phase="complete",
                     completed_at=datetime.now(timezone.utc).isoformat(),
                     result={"snapshot_id": candidate, "changed": result.changed})
        store.write_operation(operation_id, state)
        return result
    except Exception as exc:
        state.update(status="failed", error={"type": type(exc).__name__,
                                             "phase": state.get("phase")})
        store.write_operation(operation_id, state)
        raise


def bulk_job_status(store: LocalStore, *, plan: BulkJobPlan, operation_id: str) -> dict[str, Any]:
    """Read current durable progress without contacting the supplier or writing.

    The active chunk's Raw log is included, so status remains accurate between
    periodic batch checkpoint flushes and after an unclean interruption.
    """
    if not isinstance(store, LocalStore) or not isinstance(plan, BulkJobPlan):
        raise DataError("store and explicit BulkJobPlan are required")
    state = store.read_operation(operation_id)
    if state is None:
        return {"status": "not_started", "phase": "not_started",
                "plan_fingerprint": plan.fingerprint(), "completed_requests": 0,
                "planned_requests": None, "raw_rows": 0, "raw_attempts": 0,
                "next_chunk": 0, "total_chunks": None, "elapsed_seconds": 0}
    if state.get("plan_fingerprint") != plan.fingerprint():
        raise ConflictError("job checkpoint does not match supplied plan")
    started = state.get("started_at")
    ended = state.get("completed_at")
    elapsed = None
    if started:
        start_time = datetime.fromisoformat(started)
        end_time = datetime.fromisoformat(ended) if ended else datetime.now(timezone.utc)
        elapsed = max(0.0, (end_time - start_time).total_seconds())
    completed = state.get("completed_requests", 0)
    raw_rows = state.get("raw_rows", 0)
    raw_attempts = state.get("raw_attempts", 0)
    planned = state.get("planned_requests")
    index = state.get("current_chunk_index")
    if index is not None:
        chunk_op = f"{operation_id}.v2.c{index:06d}"
        fetch = store.read_operation(chunk_op)
        if fetch is not None:
            records = store.find_raw_by_operation(chunk_op,
                                                  since_offset=state.get("chunk_raw_log_offset", 0),
                                                  shared_profiles=True)
            raw_attempts += len(records)
            completed_indexes = {i for i, task in enumerate(fetch["tasks"])
                                 if task["status"] in {"done", "split"}}
            completed_indexes.update(raw["request"]["plan_request_index"]
                                     for raw in records.values()
                                     if raw["status"] in {"success", "empty", "cap"})
            completed += len(completed_indexes)
            raw_rows += sum(len(_rows(store.read_raw_record(raw)))
                            for raw in records.values() if raw["status"] != "failed")
            if planned is not None:
                planned += len(fetch["tasks"]) - state.get("chunk_request_count", 0)
    return {"status": state.get("status"), "phase": state.get("phase", "monthly"),
            "plan_fingerprint": plan.fingerprint(), "started_at": started,
            "completed_at": ended, "elapsed_seconds": elapsed,
            "completed_requests": completed, "planned_requests": planned,
            "raw_rows": raw_rows, "raw_attempts": raw_attempts,
            "next_chunk": state.get("next_chunk"), "total_chunks": state.get("total_chunks"),
            "candidate_snapshot": state.get("candidate_snapshot"),
            "trading_sessions": len(state.get("trading_sessions", ())),
            "error": state.get("error")}


def verify_bulk_job(store: LocalStore, *, plan: BulkJobPlan, operation_id: str) -> dict[str, Any]:
    """Independently verify saved Raw, complete job checkpoint and Snapshot closure.

    This is offline and read-only. It checks every selected Raw hash and every
    final canonical Parquet object; it does not certify supplier completeness.
    """
    status = bulk_job_status(store, plan=plan, operation_id=operation_id)
    if status["status"] != "success":
        raise DataError("bulk job has not completed successfully")
    state = store.read_operation(operation_id)
    total = state["total_chunks"]
    if total is None or state["next_chunk"] != total:
        raise DataError("bulk job checkpoint has incomplete chunks")
    if state["completed_requests"] != state["planned_requests"]:
        raise DataError("bulk job request accounting is incomplete")
    raw_count = 0
    selected_rows = 0
    reference_specs = _v2_reference_specs(plan)
    expected_chunks = [reference_specs[i:i + plan.max_requests_per_chunk]
                       for i in range(0, len(reference_specs), plan.max_requests_per_chunk)]
    reference_ids = [raw_id for index in range(len(expected_chunks))
                     for raw_id in (store.read_operation(f"{operation_id}.v2.c{index:06d}") or {}).get(
                         "selected_raw_batch_ids", [])]
    sessions = _v2_calendar_sessions(store, reference_ids, plan) if "trade_cal" in plan.endpoints else []
    if sessions != state.get("trading_sessions"):
        raise DataError("bulk open sessions differ from the frozen source calendar")
    for first, last in _months(_day(plan.start_session), _day(plan.end_session)):
        specs = _v2_month_specs(plan, sessions, first, last)
        expected_chunks.extend(specs[i:i + plan.max_requests_per_chunk]
                               for i in range(0, len(specs), plan.max_requests_per_chunk))
    if len(expected_chunks) != total:
        raise DataError("bulk chunks do not cover the frozen plan")
    for index in range(total):
        chunk_op = f"{operation_id}.v2.c{index:06d}"
        chunk = store.read_operation(chunk_op)
        if not chunk or chunk.get("status") != "success":
            raise DataError(f"bulk job chunk {index} is incomplete")
        if any(
                task["status"] not in {"done", "split"} for task in chunk["tasks"]):
            raise DataError(f"bulk job chunk {index} has unresolved requests")
        ids = verify_batch_selectors(store, operation_id=chunk_op,
                                     specs=expected_chunks[index], plan_fingerprint=plan.fingerprint(),
                                     identity_map=dict(plan.identity_map))
        records = store.get_raw_many(ids)
        for batch_id in ids:
            raw = records[batch_id]
            if raw["status"] not in {"success", "empty"} or raw["request"].get("plan_fingerprint") != plan.fingerprint():
                raise DataError("bulk job selected an invalid Raw observation")
            selected_rows += len(_rows(store.read_raw_record(raw)))
            raw_count += 1
    candidate = state["candidate_snapshot"]
    if candidate != state["result"]["snapshot_id"]:
        raise DataError("bulk job result differs from candidate Snapshot")
    snapshot = store.load_snapshot(candidate)
    partition_count = canonical_rows = 0
    for domain in snapshot["domains"].values():
        for part in domain["partitions"]:
            store.verify_partition(part)
            partition_count += 1
            canonical_rows += part["rows"]
    ancestor = candidate
    seen = set()
    while ancestor != state["base_snapshot"]:
        if ancestor is None or ancestor in seen:
            raise DataError("candidate Snapshot does not descend from the job base")
        seen.add(ancestor)
        ancestor = store.load_snapshot(ancestor)["parent_snapshot"]
    if state.get("promote", True) and store.resolve("current") != candidate:
        raise DataError("current does not point to the completed bulk candidate")
    return {"verified": True, "snapshot_id": candidate,
            "plan_fingerprint": plan.fingerprint(),
            "selected_raw_batches": raw_count, "selected_raw_rows": selected_rows,
            "canonical_partitions": partition_count, "canonical_rows": canonical_rows,
            "coverage_basis": "observed_responses_only"}
