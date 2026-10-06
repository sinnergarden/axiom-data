"""Bounded concurrent fetch for one v2 job chunk; the caller alone writes Raw."""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
import threading
import time
from typing import Any, Callable, Mapping, Sequence

from .source_failure import safe_failure as _safe_failure
from .protocols import ConflictError, CoverageError, DataError
from .provider_local import CAPS, CONTRACTS, DOMAINS, profile_for, response_bytes
from .provider_etf import (CAPS as ETF_CAPS, CONTRACTS as ETF_CONTRACTS,
                           DOMAINS as ETF_DOMAINS, profile_for as etf_profile_for)
from .sources import _rows
from .storage import LocalStore


def _digest(value: Any) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False).encode()).hexdigest()


def _task(endpoint: str, params: Mapping[str, str], fields: Sequence[str],
          canonical_symbols: Sequence[str] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"endpoint": endpoint, "params": dict(sorted(params.items())),
                              "fields": list(fields), "status": "pending", "attempt": 0}
    if canonical_symbols is not None:
        result["canonical_symbols"] = list(canonical_symbols)
    return result


def _compact_task(item: dict[str, Any], symbol_sets: dict[str, list[str]]) -> dict[str, Any]:
    symbols = item.pop("canonical_symbols", None)
    if symbols is not None:
        key = _digest(symbols)
        symbol_sets.setdefault(key, symbols)
        item["canonical_symbols_ref"] = key
    return item


def _expand_task(item: Mapping[str, Any], symbol_sets: Mapping[str, list[str]]) -> dict[str, Any]:
    result = dict(item)
    ref = result.pop("canonical_symbols_ref", None)
    if ref is not None:
        result["canonical_symbols"] = symbol_sets[ref]
    return result


def _children(task: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Deterministically narrow a capped request; never accept a truncated page."""
    endpoint = task["endpoint"]
    params = task["params"]
    symbols = task.get("canonical_symbols")
    first, last = params.get("start_date"), params.get("end_date")
    if first and last and first < last:
        start = date.fromisoformat(f"{first[:4]}-{first[4:6]}-{first[6:]}")
        end = date.fromisoformat(f"{last[:4]}-{last[4:6]}-{last[6:]}")
        middle = start + (end - start) // 2
        next_day = middle + timedelta(days=1)
        left = {**params, "end_date": middle.strftime("%Y%m%d")}
        right = {**params, "start_date": next_day.strftime("%Y%m%d")}
        return [_task(endpoint, left, task["fields"], symbols),
                _task(endpoint, right, task["fields"], symbols)]
    if endpoint in {"daily", "adj_factor", "suspend_d", "stock_basic", "fund_basic"} and "ts_code" not in params:
        if endpoint == "stock_basic":
            exchange = params.get("exchange")
            selected = [s for s in (symbols or ())
                        if s.endswith(".SH" if exchange == "SSE" else ".SZ")]
        else:
            selected = list(symbols or ())
        if not selected:
            raise CoverageError(f"{endpoint} reached its row cap with no selected symbols to split")
        return [_task(endpoint, {**params, "ts_code": symbol}, task["fields"], [symbol])
                for symbol in selected]
    if endpoint == "index_weight":
        raise CoverageError("index_weight reached its row cap on one date; supplier has no constituent selector")
    if endpoint in {"income_vip", "balancesheet_vip", "cashflow_vip",
                    "fina_indicator_vip", "dividend", "stk_limit"} and "ts_code" not in params:
        if not symbols:
            raise CoverageError(f"{endpoint} cap has no selected securities to split")
        child_endpoint = endpoint.removesuffix("_vip")
        return [_task(child_endpoint, {**params, "ts_code": symbol}, task["fields"], [symbol])
                for symbol in symbols]
    raise CoverageError(f"{endpoint} reached its row cap at an unsplittable request")


class BatchRateLimiter:
    def __init__(self, *, global_per_minute: int, stock_per_minute: int,
                 extra_gap: float, ticks: Callable[[], float], sleep: Callable[[float], None]):
        self._lock = threading.Lock()
        self._global_gap = max(60 / global_per_minute if global_per_minute else 0, extra_gap)
        self._stock_gap = 60 / stock_per_minute if stock_per_minute else 0
        self._next_global = 0.0
        self._next_stock = 0.0
        self._ticks, self._sleep = ticks, sleep

    def wait(self, endpoint: str) -> None:
        with self._lock:
            now = self._ticks()
            due = max(now, self._next_global,
                      self._next_stock if endpoint == "stock_basic" else 0)
            self._next_global = due + self._global_gap
            if endpoint == "stock_basic":
                self._next_stock = due + self._stock_gap
        if due > now:
            self._sleep(due - now)


def verify_batch_selectors(store: LocalStore, *, operation_id: str,
                           specs: Sequence[Mapping[str, Any]],
                           plan_fingerprint: str, identity_map: Mapping[str, str]) -> list[str]:
    """Read-only check of frozen initial selectors and their complete split tree.

    Each capped parent must have every deterministic child, including date and
    symbol splits. A leaf binds its own Raw selector, rather than requiring the
    full job universe in every child. Empty successful leaves count as observed
    responses, never as proof that source facts exist.
    """
    state = store.read_operation(operation_id)
    if not state or state.get("kind") != "batch_fetch_v2" or state.get("status") != "success":
        raise DataError("batch selector checkpoint is incomplete")
    tasks = state["tasks"]
    symbol_sets = state["canonical_symbol_sets"]
    if any(_digest(symbols) != ref for ref, symbols in symbol_sets.items()):
        raise DataError("batch canonical symbol binding differs from its digest")
    visited = set()

    def visit(index, expected):
        if type(index) is not int or not 0 <= index < len(tasks) or index in visited:
            raise DataError("batch selector tree has missing or repeated children")
        visited.add(index)
        task = _expand_task(tasks[index], symbol_sets)
        fields = ("endpoint", "params", "fields", "canonical_symbols")
        if any(task.get(field) != expected.get(field) for field in fields):
            raise DataError("batch child selector differs from the frozen plan")
        split = task.get("status") == "split"
        if not split and task.get("status") != "done":
            raise DataError("batch selector has no completed response")
        raw_id = task.get("cap_raw_batch_id" if split else "selected_raw_batch_id")
        raw = store.get_raw(raw_id)
        request = raw.get("request", {})
        if (raw.get("status") not in ({"cap"} if split else {"success", "empty"}) or
                request.get("plan_fingerprint") != plan_fingerprint or
                request.get("plan_request_index") != index or
                any(request.get(field) != task.get(field) for field in fields)):
            raise DataError("batch Raw selector differs from its completed child")
        frozen_identity = raw.get("source_profile", {}).get("identity_map")
        if frozen_identity is not None and frozen_identity != dict(identity_map):
            raise DataError("batch Raw identity differs from the frozen plan")
        store.read_raw_record(raw)
        if split:
            children = _children(task)
            indexes = task.get("child_indexes", [])
            if len(indexes) != len(children):
                raise DataError("batch split does not cover its complete parent scope")
            for child_index, child in zip(indexes, children):
                visit(child_index, child)

    for index, spec in enumerate(specs):
        visit(index, spec)
    if visited != set(range(len(tasks))):
        raise DataError("batch has selectors outside the frozen plan")
    # Selected receipts are saved in task order, independent of completion order.
    selected = [task["selected_raw_batch_id"] for task in tasks if task["status"] == "done"]
    if selected != state.get("selected_raw_batch_ids") or len(set(selected)) != len(selected):
        raise DataError("batch selected Raw differs from its completed leaves")
    return selected


def run_batch_chunk(store: LocalStore, *, specs: Sequence[Mapping[str, Any]],
                    client: Any, operation_id: str, plan_fingerprint: str,
                    identity_map: Mapping[str, str], raw_log_offset: int,
                    max_attempts: int = 3, max_workers: int = 8,
                    global_calls_per_minute: int = 300,
                    stock_basic_calls_per_minute: int = 50,
                    max_total_requests_per_chunk: int = 6000,
                    min_interval_seconds: float = 0,
                    rate_limiter: BatchRateLimiter | None = None,
                    event_calendar_map: Mapping[str, str] | None = None,
                    reused_raw_batch_ids: Mapping[int, str] | None = None,
                    clock: Callable[[], datetime] | None = None,
                    monotonic: Callable[[], float] | None = None,
                    sleeper: Callable[[float], None] | None = None) -> dict[str, Any]:
    """Fetch a bounded chunk with crash-safe Raw, cap splitting and no publish.

    A worker returns serialized original bytes and its actual aware receipt
    time. The calling thread alone appends Raw. Successful selected IDs are
    returned in deterministic task order; capped parent Raw is never selected.
    Explicit ``reused_raw_batch_ids`` binds initial event request indexes to
    retained responses. Revalidation appends an alias with the original bytes
    and receipt time; it makes no supplier call for those requests.
    """
    if not isinstance(store, LocalStore) or not 1 <= max_attempts <= 10:
        raise DataError("invalid batch store or retry limit")
    if type(max_workers) is not int or not 1 <= max_workers <= 32:
        raise DataError("max_workers must be in [1,32]")
    if type(max_total_requests_per_chunk) is not int or max_total_requests_per_chunk < len(specs):
        raise DataError("chunk request limit is smaller than initial scope")
    if any(type(v) is not int or v < 0 for v in
           (global_calls_per_minute, stock_basic_calls_per_minute)):
        raise DataError("rate limits must be non-negative integers")
    if not isinstance(min_interval_seconds, (int, float)) or not 0 <= min_interval_seconds <= 3600:
        raise DataError("min_interval_seconds must be between 0 and 3600")
    query = getattr(client, "query", None)
    if not callable(query):
        raise DataError("injected client needs query(endpoint, fields, **params)")
    symbol_sets: dict[str, list[str]] = {}
    initial = [_compact_task(_task(s["endpoint"], s["params"], s["fields"],
                                   s.get("canonical_symbols")), symbol_sets) for s in specs]
    fingerprint_payload = {"initial": initial, "canonical_symbol_sets": symbol_sets,
                           "plan_fingerprint": plan_fingerprint,
                           "identity_map": dict(identity_map), "max_attempts": max_attempts,
                           "max_total_requests_per_chunk": max_total_requests_per_chunk}
    reused = dict(reused_raw_batch_ids or {})
    if any(type(index) is not int or not 0 <= index < len(specs) or
           not isinstance(raw_id, str) or not raw_id for index, raw_id in reused.items()):
        raise DataError("reused Raw needs valid initial request indexes and batch IDs")
    if reused:
        fingerprint_payload["reused_raw_batch_ids"] = {str(index): raw_id for index, raw_id in reused.items()}
    if event_calendar_map is not None:
        fingerprint_payload["event_calendar_map"] = dict(event_calendar_map)
    fingerprint = _digest(fingerprint_payload)
    state = store.read_operation(operation_id)
    if state is not None and (state.get("kind") != "batch_fetch_v2" or
                              state.get("fingerprint") != fingerprint):
        raise ConflictError("batch operation is bound to different requests")
    if state is None:
        state = {"kind": "batch_fetch_v2", "fingerprint": fingerprint,
                 "status": "running", "tasks": initial,
                 "canonical_symbol_sets": symbol_sets,
                 "raw_rows": 0, "raw_attempts": 0, "completed_requests": 0}
        store.write_operation(operation_id, state)
    if state.get("status") == "success":
        return {"raw_batch_ids": state["selected_raw_batch_ids"],
                "raw_rows": state["raw_rows"], "raw_attempts": state["raw_attempts"],
                "completed_requests": state["completed_requests"],
                "total_requests": len(state["tasks"]),
                "last_observed_at": state.get("last_observed_at"),
                "last_stock_observed_at": state.get("last_stock_observed_at"),
                "reused_requests": state.get("reused_requests", 0)}
    retrying_failed_run = state.get("status") == "failed"
    tasks = state["tasks"]
    symbol_sets = state["canonical_symbol_sets"]
    recovered = store.find_raw_by_operation(operation_id, since_offset=raw_log_offset,
                                            shared_profiles=True)
    slot_span = (max_total_requests_per_chunk + 1) * max_attempts

    def slot(index: int, attempt: int) -> int:
        # Each explicit rerun gets a fresh bounded set of slots. The adjacent
        # span is reserved for offline Raw revalidation, so neither can
        # overwrite an earlier receipt or another task's attempt.
        return 2 * state.get("retry_round", 0) * slot_span + index * max_attempts + attempt
    from .event_sources import (_CAPS as EVENT_CAPS, _DOMAINS as EVENT_DOMAINS,
                                _FIELDS as EVENT_FIELDS, CONTRACTS as EVENT_CONTRACTS,
                                _source_rows as event_source_rows,
                                _response_issue as event_response_issue,
                                event_source_profile)
    def is_event(endpoint: str) -> bool:
        return endpoint in EVENT_DOMAINS
    profiles = {}
    def profile(endpoint: str):
        if endpoint not in profiles:
            profiles[endpoint] = (etf_profile_for(endpoint, identity_map=identity_map)
                                  if endpoint in ETF_DOMAINS else
                                  event_source_profile(endpoint, identity_map=identity_map,
                                                       next_open_session_by_date=event_calendar_map)
                                  if is_event(endpoint) else
                                  profile_for(endpoint, identity_map=identity_map))
        return profiles[endpoint]
    for spec in specs:
        profile(spec["endpoint"])
    now = clock or (lambda: datetime.now(timezone.utc))
    ticks, sleep = monotonic or time.monotonic, sleeper or time.sleep
    pacer = rate_limiter or BatchRateLimiter(global_per_minute=global_calls_per_minute,
                                             stock_per_minute=stock_basic_calls_per_minute,
                                             extra_gap=min_interval_seconds, ticks=ticks, sleep=sleep)

    def request(index: int, attempt: int) -> dict[str, Any]:
        item = tasks[index]
        result = {"endpoint": item["endpoint"], "params": item["params"],
                  "fields": item["fields"], "plan_request_index": index,
                  "attempt": state.get("retry_round", 0) * max_attempts + attempt,
                  "plan_fingerprint": plan_fingerprint,
                  "request_strategy": ("etf_batch_v1" if item["endpoint"] in ETF_DOMAINS else
                                       "event_bulk_v1" if is_event(item["endpoint"])
                                       else "trading_day_market_v2"),
                  "coverage_status": "observed_response_only"}
        if "canonical_symbols_ref" in item:
            result["canonical_symbols"] = symbol_sets[item["canonical_symbols_ref"]]
        return result

    def work(index: int, attempt: int):
        item = tasks[index]
        pacer.wait(item["endpoint"])
        try:
            response = query(item["endpoint"], fields=",".join(item["fields"]),
                             **item["params"])
            if is_event(item["endpoint"]):
                payload, rows = event_source_rows(response)
                count = len(rows) if rows is not None else None
                event_cap = EVENT_CAPS[item["endpoint"]]
                if rows is not None and (event_cap is None or count < event_cap):
                    selected = item.get("canonical_symbols_ref")
                    symbols_for_issue = set(symbol_sets[selected]) if selected else set(identity_map)
                    chosen = [row for row in rows if row.get("ts_code") in symbols_for_issue]
                    issue = ("supplier response lacks requested fields" if any(
                        any(field not in row for field in EVENT_FIELDS[item["endpoint"]])
                        for row in rows) else
                        event_response_issue(item["endpoint"], chosen, item["params"],
                                             profile(item["endpoint"])))
                    if issue:
                        count = None
            else:
                payload, count = response_bytes(response, endpoint=item["endpoint"])
            response_cap = (EVENT_CAPS[item["endpoint"]] if is_event(item["endpoint"])
                            else ETF_CAPS[item["endpoint"]] if item["endpoint"] in ETF_DOMAINS
                            else CAPS[item["endpoint"]])
            status = ("failed" if count is None else
                      "cap" if response_cap is not None and count >= response_cap else
                      "empty" if count == 0 else "success")
        except Exception as exc:
            payload, count, status = _safe_failure(exc, "fetch_or_serialize"), None, "failed"
        observed = now()
        if not isinstance(observed, datetime) or observed.tzinfo is None or observed.utcoffset() is None:
            raise DataError("clock must return a timezone-aware datetime")
        return payload, count, status, observed

    dirty = 0
    last_flush = ticks()

    def flush(*, force: bool = False) -> None:
        nonlocal dirty, last_flush
        if force or dirty >= 16 or ticks() - last_flush >= 1:
            store.write_operation(operation_id, state)
            dirty = 0
            last_flush = ticks()

    def accept(index: int, attempt: int, raw: Mapping[str, Any], count: int | None) -> None:
        nonlocal dirty
        item = tasks[index]
        expected = request(index, attempt)
        actual_request = dict(raw.get("request", {}))
        revalidated_from = actual_request.pop("revalidated_from_batch_id", None)
        if revalidated_from is not None:
            original = store.get_raw(revalidated_from)
            external_reuse = reused.get(index) == revalidated_from
            saved_original = recovered.get(slot(index, attempt))
            if ((not external_reuse and (saved_original is None or
                     saved_original["batch_id"] != revalidated_from or original["status"] != "failed")) or
                    raw["observed_at"] != original["observed_at"] or
                    raw["payload_sha256"] != original["payload_sha256"]):
                raise ConflictError("revalidated Raw does not match its saved source response")
        endpoint = item["endpoint"]
        domain = (ETF_DOMAINS[endpoint] if endpoint in ETF_DOMAINS else
                  EVENT_DOMAINS[endpoint] if is_event(endpoint) else DOMAINS[endpoint])
        contract = (ETF_CONTRACTS[endpoint] if endpoint in ETF_DOMAINS else
                    EVENT_CONTRACTS[endpoint] if is_event(endpoint) else CONTRACTS[endpoint])
        if (actual_request != expected or raw.get("domain") != domain or
                raw.get("contract") != contract or
                raw.get("source_profile") != profile(endpoint)):
            raise ConflictError("recovered batch Raw differs from frozen request")
        if state.get("last_observed_at", "") < raw["observed_at"]:
            state["last_observed_at"] = raw["observed_at"]
        if endpoint == "stock_basic" and state.get("last_stock_observed_at", "") < raw["observed_at"]:
            state["last_stock_observed_at"] = raw["observed_at"]
        if raw["status"] in {"success", "empty"}:
            item.update(status="done", selected_raw_batch_id=raw["batch_id"])
            state["completed_requests"] += 1
        elif raw["status"] == "cap":
            children = [_compact_task(child, symbol_sets)
                        for child in _children(_expand_task(item, symbol_sets))]
            if len(tasks) + len(children) > max_total_requests_per_chunk:
                raise CoverageError("cap split exceeds fixed chunk request limit; use a narrower new plan")
            item.update(status="split", cap_raw_batch_id=raw["batch_id"],
                        child_indexes=list(range(len(tasks), len(tasks) + len(children))))
            tasks.extend(children)
            state["completed_requests"] += 1
        elif raw["status"] == "failed":
            if attempt + 1 >= max_attempts:
                raise CoverageError(f"{endpoint} exhausted {max_attempts} attempts")
            item["attempt"] = attempt + 1
        else:
            raise DataError("unexpected Raw fetch status")
        if revalidated_from is None:
            state["raw_attempts"] += 1
        else:
            state["raw_reclassifications"] = state.get("raw_reclassifications", 0) + 1
            if reused.get(index) == revalidated_from:
                state["reused_requests"] = state.get("reused_requests", 0) + 1
        state["raw_rows"] += count or 0
        dirty += 1
        flush(force=item["status"] == "split")

    def append_result(index: int, attempt: int, result):
        payload, count, status, observed = result
        item = tasks[index]
        endpoint = item["endpoint"]
        raw = store.write_raw(payload, domain=(ETF_DOMAINS[endpoint] if endpoint in ETF_DOMAINS else
                                               EVENT_DOMAINS[endpoint] if is_event(endpoint)
                                               else DOMAINS[endpoint]),
                              request=request(index, attempt),
                              source_profile=profile(endpoint),
                              contract=(ETF_CONTRACTS[endpoint] if endpoint in ETF_DOMAINS else
                                        EVENT_CONTRACTS[endpoint] if is_event(endpoint)
                                        else CONTRACTS[endpoint]),
                              observed_at=observed,
                              normalizer=("event_records_v1" if is_event(endpoint)
                                          else profile(endpoint).get("normalizer", "records_v1")),
                              status=status, operation_id=operation_id,
                              batch_index=slot(index, attempt))
        recovered[slot(index, attempt)] = raw
        if state.get("last_observed_at", "") < raw["observed_at"]:
            state["last_observed_at"] = raw["observed_at"]
        if item["endpoint"] == "stock_basic" and state.get("last_stock_observed_at", "") < raw["observed_at"]:
            state["last_stock_observed_at"] = raw["observed_at"]
        return raw, count

    def revalidate_saved_response(index: int, attempt: int,
                                  failed: Mapping[str, Any]) -> tuple[dict[str, Any], int] | None:
        """Reclassify valid saved bytes after a local validator correction.

        This appends a provenance-linked Raw record with the original bytes and
        original receipt timestamp. It never queries the supplier or changes the
        failed observation; a true transport error is not reclassified.
        """
        item = tasks[index]
        endpoint = item["endpoint"]
        if not is_event(endpoint) or failed["status"] != "failed":
            return None
        source_slot = slot(index, attempt)
        alias_slot = source_slot + slot_span
        existing = recovered.get(alias_slot)
        if existing is not None:
            count = len(_rows(store.read_raw_record(existing)))
            return existing, count
        payload = store.read_raw_record(failed)
        try:
            rows = _rows(payload)
        except DataError:
            return None
        event_cap = EVENT_CAPS[endpoint]
        if rows is None or (event_cap is not None and len(rows) >= event_cap) or any(
                any(field not in row for field in EVENT_FIELDS[endpoint]) for row in rows):
            return None
        selected = item.get("canonical_symbols_ref")
        symbols_for_issue = set(symbol_sets[selected]) if selected else set(identity_map)
        chosen = [row for row in rows if row.get("ts_code") in symbols_for_issue]
        if event_response_issue(endpoint, chosen, item["params"], profile(endpoint)):
            return None
        alias = store.write_raw(
            payload, domain=EVENT_DOMAINS[endpoint],
            request={**request(index, attempt),
                     "revalidated_from_batch_id": failed["batch_id"]},
            source_profile=profile(endpoint), contract=EVENT_CONTRACTS[endpoint],
            normalizer="event_records_v1", observed_at=failed["observed_at"],
            status="empty" if not rows else "success", operation_id=operation_id,
            batch_index=alias_slot)
        recovered[alias_slot] = alias
        return alias, len(rows)

    def reuse_response(index: int, attempt: int) -> tuple[dict[str, Any], int]:
        item = tasks[index]
        endpoint = item["endpoint"]
        original = store.get_raw(reused[index])
        expected = request(index, attempt)
        if (not is_event(endpoint) or original.get("status") not in {"success", "empty", "failed"} or
                original.get("domain") != EVENT_DOMAINS[endpoint] or
                original.get("normalizer") != "event_records_v1" or any(
                    original.get("request", {}).get(field) != expected.get(field)
                    for field in ("endpoint", "params", "fields", "canonical_symbols"))):
            raise ConflictError("reused Raw differs from its frozen event selector")
        payload = store.read_raw_record(original)
        rows = _rows(payload)
        if EVENT_CAPS[endpoint] is not None and len(rows) >= EVENT_CAPS[endpoint]:
            raise CoverageError("capped retained event response cannot be reclassified as complete")
        chosen = set(expected.get("canonical_symbols") or ())
        selected = [row for row in rows if row.get("ts_code") in chosen]
        if any(any(field not in row for field in EVENT_FIELDS[endpoint]) for row in rows) or (
                event_response_issue(endpoint, selected, item["params"], profile(endpoint))):
            raise CoverageError("retained event response is not valid under the corrected adapter")
        current_profile = profile(endpoint)
        old_profile = original.get("source_profile") or {}
        if (old_profile.get("identity_map") != current_profile["identity_map"] or
                old_profile.get("source_units") != current_profile.get("source_units") or
                old_profile.get("availability") != current_profile.get("availability")):
            raise ConflictError("reused Raw has different identity, units or availability")
        alias = store.write_raw(payload, domain=EVENT_DOMAINS[endpoint],
                                request={**expected, "revalidated_from_batch_id": original["batch_id"]},
                                source_profile=current_profile, contract=EVENT_CONTRACTS[endpoint],
                                observed_at=original["observed_at"], normalizer="event_records_v1",
                                status="success" if rows else "empty", operation_id=operation_id,
                                batch_index=slot(index, attempt))
        recovered[slot(index, attempt)] = alias
        return alias, len(rows)

    if retrying_failed_run:
        # Responses from in-flight workers are saved before a failure is
        # surfaced. Admit any such successes before opening a new retry round.
        for index, item in enumerate(tasks):
            if item["status"] != "pending":
                continue
            for attempt in range(item["attempt"], max_attempts):
                saved = recovered.get(slot(index, attempt))
                if saved is not None:
                    corrected = (revalidate_saved_response(index, attempt, saved)
                                 if saved["status"] == "failed" else None)
                    if corrected is not None:
                        saved, count = corrected
                    elif saved["status"] in {"success", "empty"}:
                        count = len(_rows(store.read_raw_record(saved)))
                    else:
                        continue
                    accept(index, attempt, saved, count)
                    break
        if any(item["status"] == "pending" for item in tasks):
            state["retry_round"] = state.get("retry_round", 0) + 1
            for item in tasks:
                if item["status"] == "pending":
                    item["attempt"] = 0
            state["status"] = "running"
            state.pop("error", None)
            flush(force=True)
    from collections import deque
    pending = deque(index for index, item in enumerate(tasks) if item["status"] == "pending")
    active = {}
    try:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            while pending or active:
                while pending and len(active) < max_workers:
                    index = pending.popleft()
                    item = tasks[index]
                    if item["status"] != "pending":
                        continue
                    attempt = item["attempt"]
                    source_slot = slot(index, attempt)
                    raw = recovered.get(source_slot)
                    if raw is None and index in reused:
                        raw, _ = reuse_response(index, attempt)
                    if raw is not None:
                        count = (len(_rows(store.read_raw_record(raw)))
                                 if raw["status"] != "failed" else None)
                        if raw["status"] == "failed":
                            corrected = revalidate_saved_response(index, attempt, raw)
                            if corrected is not None:
                                raw, count = corrected
                        before = len(tasks)
                        accept(index, attempt, raw, count)
                        pending.extend(range(before, len(tasks)))
                        if item["status"] == "pending":
                            pending.append(index)
                    else:
                        future = pool.submit(work, index, attempt)
                        active[future] = (index, attempt)
                if not active:
                    continue
                done, _ = wait(active, return_when=FIRST_COMPLETED)
                for future in done:
                    index, attempt = active.pop(future)
                    raw, count = append_result(index, attempt, future.result())
                    item = tasks[index]
                    before = len(tasks)
                    accept(index, attempt, raw, count)
                    pending.extend(range(before, len(tasks)))
                    if item["status"] == "pending":
                        pending.append(index)
        selected = [item["selected_raw_batch_id"] for item in tasks if item["status"] == "done"]
        if not selected:
            raise CoverageError("batch chunk supplied no publishable Raw")
        state.update(status="success", selected_raw_batch_ids=selected)
        flush(force=True)
        return {"raw_batch_ids": selected, "raw_rows": state["raw_rows"],
                "raw_attempts": state["raw_attempts"],
                "completed_requests": state["completed_requests"],
                "total_requests": len(tasks),
                "last_observed_at": state.get("last_observed_at"),
                "last_stock_observed_at": state.get("last_stock_observed_at"),
                "reused_requests": state.get("reused_requests", 0)}
    except BaseException as exc:
        # Futures already sent to the supplier may complete after one task
        # blocks. Preserve every returned response before surfacing failure.
        for future, (index, attempt) in list(active.items()):
            try:
                append_result(index, attempt, future.result())
            except BaseException:
                pass
        state.update(status="failed" if isinstance(exc, Exception) else "interrupted",
                     error={"type": type(exc).__name__})
        flush(force=True)
        raise
