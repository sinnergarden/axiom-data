"""Explicit continuation of a stopped VIP financial bulk under a new builder.

The original operation remains immutable. Start from its verified reference
Snapshot, replay retained event responses under new operation IDs, then collect
only missing selectors. This is an explicit branch, not checkpoint migration.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import time
from typing import Any, Callable

from .batch_fetch import BatchRateLimiter, run_batch_chunk
from .builder import operation_context
from .event_sources import _FIELDS, _CAPS, _response_issue, event_source_profile
from .full_sources import (FullSourcePlan, _event_chunks, _hash,
                           _next_open_map, verify_full_sources)
from .protocols import ConflictError, CoverageError, DataError, OperationResult
from .sources import _rows
from .storage import LocalStore
from .updates import apply_saved_raw


KIND = "full_source_financial_continuation_v1"


def _checkpoint_hash(store: LocalStore, operation_id: str) -> str:
    # read_operation validates the ID before resolving the file path.
    if store.read_operation(operation_id) is None:
        raise CoverageError(f"source checkpoint is absent: {operation_id}")
    return sha256((store.root / "operations" / f"{operation_id}.json").read_bytes()).hexdigest()


def _chunks(plan, calendar, market):
    chunks = list(_event_chunks(plan, _next_open_map(plan, calendar["trading_sessions"]),
                                market["trading_sessions"]))
    # Retain the old core request fields throughout this continuation. Adding
    # optional context to future plans never makes old responses full captures
    # of those additional fields, and never requires historical refetching.
    for _, _, specs, _ in chunks:
        for spec in specs:
            spec["fields"] = list(_FIELDS[spec["endpoint"]])
    return chunks


def prepare_financial_continuation(store: LocalStore, *, plan: FullSourcePlan,
                                    source_operation_id: str,
                                    source_checkpoint_sha256: str) -> dict[str, Any]:
    """Read and bind a stopped source operation and reusable Raw; zero writes/calls.

    Scope is the VIP financial phase, before any capped event split. Completed
    market/calendar and reference publication checkpoints are referenced by
    their original IDs; completed event publications must form their old chain.
    A pending selector can reuse well-formed retained bytes after correction.
    Different retained payload versions require an explicit choice of scope,
    rather than silently choosing one response.
    """
    source = store.read_operation(source_operation_id)
    if (not source or source.get("kind") != "full_source_job_v1" or
            source.get("status") not in {"failed", "interrupted"} or
            source.get("plan_fingerprint") != plan.fingerprint() or
            source.get("phase") not in {"income", "balancesheet", "cashflow", "fina_indicator"} or
            plan.income_strategy != "vip_month"):
        raise CoverageError("continuation requires the matching stopped VIP financial operation")
    if _checkpoint_hash(store, source_operation_id) != source_checkpoint_sha256:
        raise ConflictError("source checkpoint differs from the reviewed continuation binding")
    market = store.read_operation(source_operation_id + ".market")
    calendar = store.read_operation(source_operation_id + ".calendar")
    if not market or not calendar or market.get("status") != "success" or calendar.get("status") != "success":
        raise CoverageError("continuation requires completed original market and calendar")
    prior_id = source_operation_id + (".membership.publish" if plan.membership_source else ".calendar")
    prior = store.read_operation(prior_id)
    if not prior or prior.get("status") != "success":
        raise CoverageError("original reference Snapshot is incomplete")
    event_base = prior["result"]["snapshot_id"]
    hashes = {op: _checkpoint_hash(store, op) for op in
              {source_operation_id, source_operation_id + ".market", source_operation_id + ".calendar", prior_id}}
    if plan.membership_source:
        listing_id = source_operation_id + ".listing.publish"
        hashes[listing_id] = _checkpoint_hash(store, listing_id)
    chunks = _chunks(plan, calendar, market)
    boundary = source["next_event_chunk"]
    if not 0 <= boundary < len(chunks):
        raise CoverageError("source financial chunk is outside its frozen plan")
    expected_base = event_base
    reuse: dict[str, dict[str, str]] = {}
    for index in range(boundary + 1):
        endpoint, _, specs, calendar_map = chunks[index]
        child_id = source_operation_id + f".e.c{index:06d}"
        child = store.read_operation(child_id)
        if not child or len(child["tasks"]) != len(specs):
            raise CoverageError("source event chunk is missing or contains unsupported cap splits")
        hashes[child_id] = _checkpoint_hash(store, child_id)
        if index < boundary:
            publish_id = child_id + ".publish"
            published = store.read_operation(publish_id)
            if (child.get("status") != "success" or not published or
                    published.get("status") != "success" or
                    published.get("base_snapshot") != expected_base or
                    published.get("raw_batch_ids") != child.get("selected_raw_batch_ids")):
                raise CoverageError("original financial publication chain is incomplete")
            hashes[publish_id] = _checkpoint_hash(store, publish_id)
            expected_base = published["result"]["snapshot_id"]
        records = None
        selected = {}
        for task_index, (task, spec) in enumerate(zip(child["tasks"], specs)):
            symbols = child["canonical_symbol_sets"].get(task.get("canonical_symbols_ref"))
            if (any(task.get(field) != spec.get(field) for field in ("endpoint", "params", "fields")) or
                    symbols != spec.get("canonical_symbols")):
                raise ConflictError("old source selector differs from the continuation field policy")
            if task["status"] == "done":
                selected[str(task_index)] = task["selected_raw_batch_id"]
                continue
            if task["status"] != "pending" or index < boundary:
                raise CoverageError("source event task cannot be reused by this continuation")
            if records is None:
                records = store.find_raw_by_operation(child_id,
                    since_offset=source.get("event_chunk_raw_log_offset", 0), shared_profiles=True)
            valid = []
            profile = event_source_profile(spec["endpoint"], identity_map=dict(plan.market.identity_map),
                                           next_open_session_by_date=calendar_map)
            chosen = set(spec["canonical_symbols"])
            for raw in records.values():
                if raw.get("request", {}).get("plan_request_index") != task_index:
                    continue
                try:
                    rows = _rows(store.read_raw_record(raw))
                    if not _response_issue(spec["endpoint"], [row for row in rows
                            if row.get("ts_code") in chosen], spec["params"], profile):
                        valid.append(raw)
                except DataError:
                    continue
            if len({raw["payload_sha256"] for raw in valid}) > 1:
                raise CoverageError("pending source selector has different retained response versions")
            if valid:
                selected[str(task_index)] = min(valid, key=lambda raw: raw["observed_at"])["batch_id"]
        reuse[str(index)] = selected
    if expected_base != source["candidate_snapshot"]:
        raise CoverageError("source publication chain does not reach its stopped candidate")
    return {"source_operation_id": source_operation_id, "source_checkpoint_hashes": hashes,
            "source_builder": source.get("builder"), "base_snapshot": source.get("base_snapshot"),
            "event_base_snapshot": event_base, "reused_raw_batch_ids": reuse,
            "total_event_chunks": len(chunks), "planned_event_requests": sum(len(c[2]) for c in chunks),
            "reused_initial_requests": sum(len(items) for items in reuse.values()),
            "field_policy": "frozen_source_core_fields_v1"}


def verify_continuation_binding(store: LocalStore, state: dict[str, Any], plan: FullSourcePlan) -> None:
    """Check unchanged source checkpoints and the explicit original reference base."""
    if state.get("kind") != KIND or state.get("plan_fingerprint") != plan.fingerprint():
        raise ConflictError("checkpoint is not the matching financial continuation")
    binding = state["source_binding"]
    for operation, digest in binding["source_checkpoint_hashes"].items():
        if _checkpoint_hash(store, operation) != digest:
            raise ConflictError("original operation changed after the continuation was bound")
    if state.get("source_operation_id") != binding["source_operation_id"]:
        raise ConflictError("continuation original operation reference differs")
    expected = prepare_financial_continuation(store, plan=plan,
        source_operation_id=binding["source_operation_id"],
        source_checkpoint_sha256=binding["source_checkpoint_hashes"][binding["source_operation_id"]])
    if expected != binding:
        raise ConflictError("continuation source Raw binding differs from the original checkpoints")


def verify_continuation_selectors(store: LocalStore, state: dict[str, Any],
                                  plan: FullSourcePlan, operation_id: str) -> dict[str, Any]:
    """Verify every planned selector, cap split and reused receipt independently.

    Check the durable task graph and Raw selectors, not just aggregate progress
    counters. Publication chaining and canonical/Raw value auditing are checked
    separately by verify_full_sources and audit_snapshot.
    """
    from .batch_fetch import _children, _expand_task

    verify_continuation_binding(store, state, plan)
    source_id = state["source_operation_id"]
    chunks = _chunks(plan, store.read_operation(source_id + ".calendar"),
                     store.read_operation(source_id + ".market"))
    if state["next_event_chunk"] != len(chunks) or state["total_event_chunks"] != len(chunks):
        raise CoverageError("continuation does not cover every frozen event chunk")
    reused_count = 0
    attempts = 0
    for index, (_, _, specs, _) in enumerate(chunks):
        child_id = operation_id + f".e.c{index:06d}"
        child = store.read_operation(child_id)
        published = store.read_operation(child_id + ".publish")
        if (not child or child.get("status") != "success" or not published or
                published.get("builder") != state.get("builder")):
            raise CoverageError("continuation event publication lacks its bound builder")
        tasks = child["tasks"]
        sets = child["canonical_symbol_sets"]
        if len(tasks) < len(specs):
            raise CoverageError("continuation dropped a planned event request")
        for task, spec in zip(tasks, specs):
            expanded = _expand_task(task, sets)
            if any(expanded.get(field) != spec.get(field) for field in
                   ("endpoint", "params", "fields", "canonical_symbols")):
                raise CoverageError("continuation task differs from a frozen original selector")
        expected_reuse = state["source_binding"]["reused_raw_batch_ids"].get(str(index), {})
        pending = list(range(len(specs)))
        reachable = set()
        while pending:
            task_index = pending.pop()
            if task_index in reachable or not 0 <= task_index < len(tasks):
                raise CoverageError("continuation cap graph has repeated or invalid child indexes")
            reachable.add(task_index)
            task = tasks[task_index]
            expanded = _expand_task(task, sets)
            if task["status"] == "split":
                expected_children = _children(expanded)
                child_indexes = task["child_indexes"]
                if len(child_indexes) != len(expected_children):
                    raise CoverageError("continuation cap split omits part of its scope")
                for child_index, expected in zip(child_indexes, expected_children):
                    if not 0 <= child_index < len(tasks) or any(
                            _expand_task(tasks[child_index], sets).get(field) != expected.get(field)
                            for field in ("endpoint", "params", "fields", "canonical_symbols")):
                        raise CoverageError("continuation cap child differs from its narrowed selector")
                raw = store.get_raw(task["cap_raw_batch_id"])
                limit = _CAPS[expanded["endpoint"]]
                if raw["status"] != "cap" or limit is None or len(_rows(store.read_raw_record(raw))) < limit:
                    raise CoverageError("continuation split lacks a retained capped response")
                pending.extend(child_indexes)
            elif task["status"] == "done":
                raw = store.get_raw(task["selected_raw_batch_id"])
                if raw["status"] not in {"success", "empty"}:
                    raise CoverageError("continuation selected an unsuccessful event response")
                original_id = expected_reuse.get(str(task_index))
                if original_id:
                    original = store.get_raw(original_id)
                    if (raw["request"].get("revalidated_from_batch_id") != original_id or any(
                            raw[field] != original[field] for field in
                            ("observed_at", "payload_sha256", "payload_uri"))):
                        raise CoverageError("continuation reused receipt changed its source or clock")
                    reused_count += 1
            else:
                raise CoverageError("continuation has an unfinished event request")
            if any(raw["request"].get(field) != expanded.get(field) for field in
                   ("endpoint", "params", "fields", "canonical_symbols")):
                raise CoverageError("continuation Raw differs from its recorded task selector")
        if len(reachable) != len(tasks):
            raise CoverageError("continuation contains unplanned event tasks")
        if child.get("reused_requests", 0) != len(expected_reuse):
            raise CoverageError("continuation repeated or omitted a reusable original request")
        attempts += child["raw_attempts"]
    if (reused_count != state["source_binding"]["reused_initial_requests"] or
            reused_count != state["reused_event_requests"] or attempts != state["event_raw_attempts"]):
        raise CoverageError("continuation request accounting differs from its receipts")
    before = store.load_snapshot(state["source_binding"]["event_base_snapshot"])
    after = store.load_snapshot(state["result"]["snapshot_id"])
    if any(after["domains"].get(name) != domain for name, domain in before["domains"].items()):
        raise CoverageError("continuation changed an inherited reference/market domain")
    return {"source_operation_id": source_id, "reused_event_requests": reused_count,
            "new_supplier_attempts": attempts, "initial_event_selectors": sum(len(c[2]) for c in chunks)}


def continue_financial_bulk(store: LocalStore, *, plan: FullSourcePlan,
                            source_operation_id: str, source_checkpoint_sha256: str,
                            operation_id: str, client: Any, promote: bool = True,
                            max_attempts: int = 3, max_workers: int = 8,
                            min_interval_seconds: float = 0,
                            global_calls_per_minute: int = 300,
                            stock_basic_calls_per_minute: int = 50,
                            clock: Callable | None = None) -> OperationResult:
    """Replay retained financial Raw and resume missing event selectors explicitly.

    Bind the actual clean commit/retained wheel to the new operation before
    writes. Original operations and snapshots are not changed. Each reused
    receipt gets a linked interpretation record with its unchanged timestamp.
    Verify complete selector/publication coverage and independently audit the
    candidate before promotion. Interrupted calls resume with the same builder,
    options and operation ID; a changed builder is rejected before any call.
    """
    if operation_id == source_operation_id:
        raise ConflictError("financial continuation needs a new operation ID")
    options = {"plan": plan.to_dict(), "source_operation_id": source_operation_id,
               "source_checkpoint_sha256": source_checkpoint_sha256, "promote": promote,
               "max_attempts": max_attempts, "max_workers": max_workers,
               "min_interval_seconds": min_interval_seconds,
               "global_calls_per_minute": global_calls_per_minute,
               "stock_basic_calls_per_minute": stock_basic_calls_per_minute}
    fingerprint = _hash(options)
    state = store.read_operation(operation_id)
    if state is not None and (state.get("kind") != KIND or state.get("fingerprint") != fingerprint):
        raise ConflictError("operation ID is bound to a different financial continuation")
    if state is None:
        binding = prepare_financial_continuation(store, plan=plan,
                    source_operation_id=source_operation_id, source_checkpoint_sha256=source_checkpoint_sha256)
        state = {"kind": KIND, "fingerprint": fingerprint, "plan_fingerprint": plan.fingerprint(),
                 "source_operation_id": source_operation_id, "source_binding": binding,
                 "base_snapshot": binding["base_snapshot"], "candidate_snapshot": binding["event_base_snapshot"],
                 "next_event_chunk": 0, "completed_event_requests": 0, "event_raw_rows": 0,
                 "event_raw_attempts": 0, "reused_event_requests": 0,
                 "total_event_chunks": binding["total_event_chunks"],
                 "planned_event_requests": binding["planned_event_requests"],
                 "status": "running", "phase": "events", "started_at": datetime.now(timezone.utc).isoformat()}
    verify_continuation_binding(store, state, plan)
    if state.get("status") == "success":
        return OperationResult(state["result"]["snapshot_id"], state["result"]["changed"], operation_id)
    context = operation_context(store, state, operation_id, options)
    source = state["source_binding"]
    chunks = _chunks(plan, store.read_operation(source_operation_id + ".calendar"),
                     store.read_operation(source_operation_id + ".market"))
    pacer = BatchRateLimiter(global_per_minute=global_calls_per_minute,
                            stock_per_minute=stock_basic_calls_per_minute, extra_gap=min_interval_seconds,
                            ticks=time.monotonic, sleep=time.sleep)
    try:
        for index, (endpoint, label, specs, calendar_map) in enumerate(chunks):
            if index < state["next_event_chunk"]:
                continue
            child_id = operation_id + f".e.c{index:06d}"
            if "event_chunk_raw_log_offset" not in state:
                state.update(event_chunk_raw_log_offset=store.raw_log_size(), current_event_chunk=index,
                             status="running", phase=endpoint)
                store.write_operation(operation_id, state)
            budget = plan.max_event_requests_per_chunk
            if len(plan.market.symbols) > budget and endpoint in {"dividend", "stk_limit"}:
                budget = min(6000, len(specs) + 64 + 3 * len(plan.market.symbols))
            fetched = run_batch_chunk(store, specs=specs, client=client, operation_id=child_id,
                plan_fingerprint=plan.fingerprint(), identity_map=dict(plan.market.identity_map),
                raw_log_offset=state["event_chunk_raw_log_offset"], event_calendar_map=calendar_map,
                reused_raw_batch_ids={int(i): raw_id for i, raw_id in
                                     source["reused_raw_batch_ids"].get(str(index), {}).items()},
                max_attempts=max_attempts, max_workers=max_workers, max_total_requests_per_chunk=budget,
                min_interval_seconds=min_interval_seconds, global_calls_per_minute=global_calls_per_minute,
                stock_basic_calls_per_minute=stock_basic_calls_per_minute, rate_limiter=pacer, clock=clock)
            receipt = apply_saved_raw(store, base_snapshot=state["candidate_snapshot"],
                raw_batch_ids=fetched["raw_batch_ids"], operation_id=child_id + ".publish", promote=False,
                build_context={"builder": context["builder"], "run_operation_id": operation_id,
                               "source_plan": plan.fingerprint(), "source_operation_id": source_operation_id,
                               "event_endpoint": endpoint, "selector_window": label,
                               "field_policy": source["field_policy"]})
            state.update(candidate_snapshot=receipt.snapshot_id, next_event_chunk=index + 1,
                         completed_event_requests=state["completed_event_requests"] + fetched["completed_requests"],
                         event_raw_rows=state["event_raw_rows"] + fetched["raw_rows"],
                         event_raw_attempts=state["event_raw_attempts"] + fetched["raw_attempts"],
                         reused_event_requests=state["reused_event_requests"] + fetched["reused_requests"])
            state.pop("event_chunk_raw_log_offset", None)
            state.pop("current_event_chunk", None)
            store.write_operation(operation_id, state)
        result = OperationResult(state["candidate_snapshot"], True, operation_id)
        state.update(status="collected", phase="validating", result={"snapshot_id": result.snapshot_id, "changed": True})
        store.write_operation(operation_id, state)
        state["verification"] = verify_full_sources(store, plan=plan, operation_id=operation_id)
        from .verification import audit_snapshot
        state["audit"] = audit_snapshot(store, snapshot_id=result.snapshot_id, plan=plan.market,
                                         base_snapshot=state["base_snapshot"])
        if state["audit"]["status"] not in {"passed", "limited"}:
            raise CoverageError("financial continuation candidate did not pass source audit")
        if promote:
            store.promote_existing_snapshot(result.snapshot_id, expected_current=state["base_snapshot"])
        state.update(status="success", phase="complete", completed_at=datetime.now(timezone.utc).isoformat())
        state.pop("error", None)
        store.write_operation(operation_id, state)
        return result
    except BaseException as exc:
        state.update(status="failed" if isinstance(exc, Exception) else "interrupted",
                     error={"type": type(exc).__name__})
        store.write_operation(operation_id, state)
        raise
