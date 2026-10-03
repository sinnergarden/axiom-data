"""Small standalone ETF jobs using the same Raw/Parquet/Snapshot machinery.

Prepare saves supplier catalogue/calendar facts. A frozen job then collects
bounded multi-year ranges for a small fixed ETF pool. Checkpoints, retries,
rate limits and offline rebuild are shared with stock jobs, not duplicated.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
import math
from typing import Any, Mapping, Sequence

from .batch_fetch import BatchRateLimiter, run_batch_chunk
from .bulk_jobs import _day, _v2_spec
from .protocols import ConflictError, DataError, OperationResult
from .provider_etf import ETF_SYMBOLS, FIELDS, fund_identity
from .sources import _rows
from .storage import LocalStore
from .updates import apply_saved_raw


def _hash(value: Any) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _spec(endpoint: str, params: Mapping[str, str], symbols=None) -> dict:
    value = {"endpoint": endpoint, "params": dict(params), "fields": list(FIELDS[endpoint])}
    if symbols is not None:
        value["canonical_symbols"] = list(symbols)
    return value


def prepare_etf_references(store: LocalStore, *, client: Any, start_session: str,
                           end_session: str, operation_id: str,
                           symbols: Sequence[str] = ETF_SYMBOLS,
                           benchmark_codes: Sequence[str] = ("000300.SH",),
                           mode: str = "bulk", base_snapshot: str | None = None,
                           max_requests_per_chunk: int = 256, **run_options) -> dict:
    """Save a fund catalogue and a 75-day calendar warmup, without promotion.

    Returns a frozen scope for plan_etf_job. All fund identities use the source
    code/list-date policy, never today's ordinal position. Actual Raw receipts
    are retained. Failed requests leave resumable Raw and current unchanged.
    The caller supplies the same operation and options to resume preparation.
    """
    first, last = _day(start_session), _day(end_session)
    selected = tuple(sorted(symbols))
    if first > last or not selected or len(set(selected)) != len(selected):
        raise DataError("ETF preparation requires a unique pool and ordered dates")
    warmup = (first - timedelta(days=75)).isoformat()
    specs = [_spec("fund_basic", {"market": "E", "status": status}, selected)
             for status in ("L", "D", "I")]
    specs.extend(_v2_spec("trade_cal", {"exchange": exchange,
                     "start_date": warmup.replace('-', ''),
                     "end_date": (last + timedelta(days=14)).strftime("%Y%m%d")})
                 for exchange in ("SSE", "SZSE"))
    identity = {"symbols": selected, "start": start_session, "end": end_session,
                "base": base_snapshot, "mode": mode, "benchmarks": list(benchmark_codes)}
    fetched = run_batch_chunk(store, specs=specs, client=client,
                              operation_id=f"{operation_id}.reference",
                              plan_fingerprint=_hash(identity), identity_map={},
                              raw_log_offset=0,
                              max_total_requests_per_chunk=max_requests_per_chunk,
                              **run_options)
    published = apply_saved_raw(store, base_snapshot=base_snapshot,
                     raw_batch_ids=fetched["raw_batch_ids"],
                     operation_id=f"{operation_id}.publish",
                     build_context={"purpose": "ETF supplier reference preparation",
                                    "identity_policy": "qualified_fund_code_and_listing_date_v1"},
                     promote=False)
    manifest = store.load_snapshot(published.snapshot_id)
    masters = [row for part in manifest["domains"]["security_master"]["partitions"]
               for row in store.read_partition(part).to_pylist()]
    bindings = {row["source_code"]: row["security_id"] for row in masters
                if row["source_code"] in selected}
    if set(bindings) != set(selected):
        raise DataError("fund_basic lacks a listing-date identity for a configured ETF")
    calendar = [row for part in manifest["domains"]["trading_calendar"]["partitions"]
                for row in store.read_partition(part).to_pylist()]
    for exchange in ("SSE", "SZSE"):
        previous = {str(row["session"]) for row in calendar if row["exchange"] == exchange
                    and row["is_open"] and str(row["session"]) < start_session}
        if len(previous) < 21:
            raise DataError("saved ETF calendar has fewer than 21 warmup sessions")
    return {"schema_version": "axiom_data_etf_scope_v1", "mode": mode,
            "symbols": list(selected), "start_session": start_session,
            "end_session": end_session, "warmup_start_session": warmup,
            "identity_map": bindings, "benchmark_codes": sorted(benchmark_codes),
            "reference_snapshot": published.snapshot_id,
            "max_requests_per_chunk": max_requests_per_chunk}


@dataclass(frozen=True)
class EtfJobPlan:
    """Frozen small-pool scope; inclusive dates and concrete reference Snapshot."""
    mode: str
    symbols: tuple[str, ...]
    start_session: str
    end_session: str
    warmup_start_session: str
    identity_map: tuple[tuple[str, str], ...]
    benchmark_codes: tuple[str, ...]
    reference_snapshot: str
    max_requests_per_chunk: int = 256

    def to_dict(self) -> dict:
        return {"schema_version": "axiom_data_etf_scope_v1", "mode": self.mode,
                "symbols": list(self.symbols), "start_session": self.start_session,
                "end_session": self.end_session, "warmup_start_session": self.warmup_start_session,
                "identity_map": dict(self.identity_map), "benchmark_codes": list(self.benchmark_codes),
                "reference_snapshot": self.reference_snapshot,
                "max_requests_per_chunk": self.max_requests_per_chunk}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EtfJobPlan":
        body = dict(value)
        if body.pop("schema_version", None) != "axiom_data_etf_scope_v1":
            raise DataError("unsupported ETF scope schema")
        return plan_etf_job(**body)

    def fingerprint(self) -> str:
        return _hash(self.to_dict())


def plan_etf_job(*, mode: str, symbols: Sequence[str], start_session: str,
                 end_session: str, warmup_start_session: str,
                 identity_map: Mapping[str, str], reference_snapshot: str,
                 benchmark_codes: Sequence[str] = ("000300.SH",),
                 max_requests_per_chunk: int = 256) -> EtfJobPlan:
    """Freeze a source-only ETF job; planning performs no reads or writes."""
    if mode not in {"init", "bulk", "daily"}:
        raise DataError("unsupported ETF mode")
    first, last, warmup = map(_day, (start_session, end_session, warmup_start_session))
    if warmup > first or first > last or (mode == "daily" and first != last):
        raise DataError("ETF dates are reversed or daily spans multiple dates")
    selected = tuple(sorted(symbols))
    if (not selected or len(set(selected)) != len(selected) or set(identity_map) != set(selected)
            or len(set(identity_map.values())) != len(selected)):
        raise DataError("ETF scope needs exact unique stable bindings")
    for code, identity in identity_map.items():
        if not isinstance(identity, str) or not identity.startswith("cn.etf."):
            raise DataError("ETF identity does not follow the frozen fund listing policy")
        if fund_identity(code, identity.rsplit('.', 1)[-1]) != identity:
            raise DataError("ETF identity/code/listing-date binding differs")
    if (not isinstance(reference_snapshot, str) or not reference_snapshot.startswith("s_") or
            type(max_requests_per_chunk) is not int or not 1 <= max_requests_per_chunk <= 6000):
        raise DataError("ETF plan needs a concrete reference Snapshot and bounded chunks")
    return EtfJobPlan(mode, selected, start_session, end_session, warmup_start_session,
                      tuple(sorted(identity_map.items())), tuple(sorted(benchmark_codes)),
                      reference_snapshot, max_requests_per_chunk)


def _specs(plan: EtfJobPlan) -> list[dict]:
    # Five calendar years fit even fund_adj's 2000-row cap for one code.
    first, last = _day(plan.warmup_start_session), _day(plan.end_session)
    spans = []
    while first <= last:
        end = min(last, first + timedelta(days=1825))
        spans.append((first.strftime("%Y%m%d"), end.strftime("%Y%m%d")))
        first = end + timedelta(days=1)
    specs = [_spec(endpoint, {"ts_code": code, "start_date": start, "end_date": end}, [code])
             for start, end in spans for code in plan.symbols
             for endpoint in ("fund_daily", "fund_adj", "etf_limit")]
    specs.extend(_v2_spec("suspend_d", {"ts_code": code, "start_date": start, "end_date": end}, [code])
                 for start, end in spans for code in plan.symbols)
    # fund_div has code/date selectors, not start_date/end_date. Fetch each
    # code's supplier history once; preserve its native announcement keys.
    specs.extend(_spec("fund_div", {"ts_code": code}, [code]) for code in plan.symbols)
    specs.extend(_v2_spec("index_daily", {"ts_code": code, "start_date": start, "end_date": end})
                 for start, end in spans for code in plan.benchmark_codes)
    return specs


def estimate_etf_job(plan: EtfJobPlan, *, min_interval_seconds: float = 0) -> dict:
    count = len(_specs(plan))
    return {"planned_requests": count, "minimum_pacing_seconds": max(0, count - 1) * max(.2, min_interval_seconds),
            "request_strategy": "small ETF pool; per-code multi-year ranges; source history dividends",
            "scope": "source calls only; retries and processing excluded"}


def run_etf_job(store: LocalStore, *, plan: EtfJobPlan, client: Any, operation_id: str,
                base_snapshot: str | None, promote: bool = True, **run_options) -> OperationResult:
    """Resume bounded source batches and promote once after the entire job.

    A completed operation makes no network calls. Unchanged content keeps its
    original canonical receipt and partitions. Daily jobs reread the small pool's
    warmup and native dividend histories to capture terminal supplier revisions.
    """
    fingerprint = _hash({"plan": plan.to_dict(), "base": base_snapshot, "promote": promote,
                         "options": {k: v for k, v in run_options.items() if not callable(v)}})
    state = store.read_operation(operation_id)
    if state is not None and state.get("fingerprint") != fingerprint:
        raise ConflictError("operation_id is bound to a different ETF job")
    if state is not None and state.get("status") == "success":
        return OperationResult(state["candidate_snapshot"], state["changed"], operation_id)
    if state is None:
        reference = store.load_snapshot(plan.reference_snapshot)
        if plan.reference_snapshot != base_snapshot and reference["parent_snapshot"] != base_snapshot:
            raise ConflictError("ETF prepared reference was built from a different base")
        state = {"kind": "etf_job_v1", "fingerprint": fingerprint,
                 "plan_fingerprint": plan.fingerprint(), "status": "running",
                 "base_snapshot": base_snapshot, "candidate_snapshot": plan.reference_snapshot,
                 "next_chunk": 0, "started_at": datetime.now(timezone.utc).isoformat(),
                 "raw_batch_ids": [], "completed_requests": 0, "raw_rows": 0, "promote": promote}
        store.write_operation(operation_id, state)
    from .builder import operation_context
    runtime = operation_context(store, state, operation_id,
                                {"plan": plan.to_dict(), "options": {k: v for k, v in run_options.items() if not callable(v)}})
    specs = _specs(plan)
    chunks = [specs[i:i + plan.max_requests_per_chunk] for i in range(0, len(specs), plan.max_requests_per_chunk)]
    import time
    limiter = BatchRateLimiter(global_per_minute=run_options.get("global_calls_per_minute", 300),
                              stock_per_minute=run_options.get("stock_basic_calls_per_minute", 50),
                              extra_gap=run_options.get("min_interval_seconds", 0),
                              ticks=run_options.get("monotonic", time.monotonic),
                              sleep=run_options.get("sleeper", time.sleep))
    try:
        for index in range(state["next_chunk"], len(chunks)):
            chunk_op = f"{operation_id}.c{index:06d}"
            fetched = run_batch_chunk(store, specs=chunks[index], client=client, operation_id=chunk_op,
                        plan_fingerprint=plan.fingerprint(), identity_map=dict(plan.identity_map),
                        raw_log_offset=0,
                        max_total_requests_per_chunk=max(64, plan.max_requests_per_chunk * 2),
                        rate_limiter=limiter, **run_options)
            result = apply_saved_raw(store, base_snapshot=state["candidate_snapshot"],
                        raw_batch_ids=fetched["raw_batch_ids"], operation_id=f"{chunk_op}.publish",
                        build_context={"source_plan": plan.fingerprint(), "asset_type": "exchange_traded_fund",
                                       "builder": runtime["builder"], "run_operation_id": operation_id,
                                       "execution_options": {k: v for k, v in run_options.items() if not callable(v)},
                                       "scope": {"start": plan.warmup_start_session, "end": plan.end_session},
                                       "revision_policy": "terminal_observation_v1"}, promote=False)
            state.update(candidate_snapshot=result.snapshot_id, next_chunk=index + 1,
                         status="running", total_chunks=len(chunks))
            state["raw_batch_ids"].extend(fetched["raw_batch_ids"])
            state["completed_requests"] += fetched["completed_requests"]
            state["raw_rows"] += fetched["raw_rows"]
            store.write_operation(operation_id, state)
        if promote:
            store.promote_existing_snapshot(state["candidate_snapshot"], expected_current=base_snapshot)
        state.update(status="success", changed=state["candidate_snapshot"] != base_snapshot,
                     completed_at=datetime.now(timezone.utc).isoformat())
        state.pop("error", None)
        store.write_operation(operation_id, state)
        return OperationResult(state["candidate_snapshot"], state["changed"], operation_id)
    except Exception as exc:
        state.update(status="failed", error={"type": type(exc).__name__, "phase": "source_or_publish"})
        store.write_operation(operation_id, state)
        raise


def etf_job_status(store: LocalStore, *, plan: EtfJobPlan, operation_id: str) -> dict:
    """Return saved progress without source calls or mutations."""
    state = store.read_operation(operation_id)
    if state is None:
        return {"status": "not_started", "planned_requests": len(_specs(plan))}
    if state.get("plan_fingerprint") != plan.fingerprint():
        raise ConflictError("ETF checkpoint differs from supplied plan")
    return dict(state, planned_requests=len(_specs(plan)))


def verify_etf_job(store: LocalStore, *, plan: EtfJobPlan, operation_id: str) -> dict:
    """Verify completed request checkpoints and all retained object hashes offline."""
    state = etf_job_status(store, plan=plan, operation_id=operation_id)
    if state["status"] != "success" or state["next_chunk"] != state["total_chunks"]:
        raise DataError("ETF job is incomplete")
    for index in range(state["total_chunks"]):
        chunk = store.read_operation(f"{operation_id}.c{index:06d}")
        if not chunk or chunk.get("status") != "success" or any(task["status"] not in {"done", "split"} for task in chunk["tasks"]):
            raise DataError("ETF job has an unresolved source request")
    manifest = store.load_snapshot(state["candidate_snapshot"])
    ids = list(dict.fromkeys(raw_id for domain in manifest["domains"].values() for raw_id in domain["raw_batch_ids"]))
    for raw in store.get_raw_many(ids).values():
        if raw["status"] not in {"success", "empty"}:
            raise DataError("ETF Snapshot references failed or capped Raw")
        store.read_raw_record(raw)
    parts = [part for domain in manifest["domains"].values() for part in domain["partitions"]]
    for part in parts:
        store.verify_partition(part)
    if state["promote"] and store.resolve("current") != state["candidate_snapshot"]:
        raise DataError("ETF current differs from completed job")
    return {"verified": True, "snapshot_id": state["candidate_snapshot"], "raw_batches": len(ids),
            "canonical_partitions": len(parts), "canonical_rows": sum(p["rows"] for p in parts)}


def audit_etf_snapshot(store: LocalStore, *, snapshot_id: str, plan: EtfJobPlan, **_) -> dict:
    """Compare fund prices/units/factors/limits/dividends to original supplier rows.

    Missing market dates relative to saved listing/calendar facts are reported
    as warnings. They never acquire synthetic prices or suspension labels.
    This is source-relative acceptance, not independent supplier certification.
    """
    manifest = store.load_snapshot(snapshot_id)
    mappings = {"market_daily": {**{field: (field, 1) for field in ("open", "high", "low", "close", "pre_close")},
                                  "volume_units": ("vol", 100), "amount_cny": ("amount", 1000)},
                "adjustment_factors": {"factor": ("adj_factor", 1)},
                "price_limits": {"up_limit": ("up_limit", 1), "down_limit": ("down_limit", 1)},
                "corporate_actions": {"cash_dividend_per_unit": ("div_cash", 1)}}
    inverse = {identity: code for code, identity in plan.identity_map}
    report = {"snapshot_id": snapshot_id, "status": "passed", "source_mapping_checks": {},
              "row_counts": {}, "issues": [], "missing_market_cells": 0,
              "limitations": ["Tushare source-relative checks; terminal public vintages unavailable.",
                              "Empty suspension responses and missing bars do not prove tradeability at the open."]}
    latest_daily = {}
    for name, domain in manifest["domains"].items():
        report["row_counts"][name] = sum(part["rows"] for part in domain["partitions"])
        if name not in mappings:
            continue
        raw_cache = {}
        checked = 0
        for part in domain["partitions"]:
            for row in store.read_partition(part).to_pylist():
                if name == "market_daily":
                    key = row["security_id"], str(row["session"])
                    previous = latest_daily.get(key)
                    if previous is None or row["first_observed_at"] > previous["first_observed_at"]:
                        latest_daily[key] = row
                raw_id = row["raw_batch_id"]
                if raw_id not in raw_cache:
                    raw = store.get_raw(raw_id)
                    raw_cache[raw_id] = (raw, _rows(store.read_raw_record(raw)))
                raw, source_rows = raw_cache[raw_id]
                code = inverse.get(row["security_id"])
                if code is None:
                    raise DataError("ETF Snapshot contains a fund outside the frozen pool")
                candidates = [r for r in source_rows if r["ts_code"] == code and (
                    (r.get("ann_date") == str(row["announcement_date"]).replace('-', '') and r.get("div_proc") == row["process_status"])
                    if name == "corporate_actions" else r.get("trade_date") == str(row["session"]).replace('-', ''))]
                matches = []
                for original in candidates:
                    equal = True
                    for field, (source, multiplier) in mappings[name].items():
                        value = original[source]
                        expected = None if value is None else Decimal(str(value)) * multiplier
                        actual = row[field]
                        equal &= (actual is None if expected is None else actual is not None and
                                  math.isclose(float(actual), float(expected), rel_tol=1e-12, abs_tol=1e-8))
                    if name == "corporate_actions":
                        for canonical, source in (("implementation_announcement_date", "imp_anndate"),
                                                  ("record_date", "record_date"), ("ex_date", "ex_date"), ("pay_date", "pay_date")):
                            expected = original[source] or None
                            actual = str(row[canonical]).replace('-', '') if row[canonical] is not None else None
                            equal &= actual == expected
                    if equal:
                        matches.append(original)
                if not matches or str(row["first_observed_at"]) != str(datetime.fromisoformat(raw["observed_at"])):
                    raise DataError(f"{name} differs from referenced ETF Raw or original receipt")
                checked += 1
        report["source_mapping_checks"][name] = checked
    masters = {row["security_id"]: row for part in manifest["domains"]["security_master"]["partitions"]
               for row in store.read_partition(part).to_pylist()}
    calendar = [row for part in manifest["domains"]["trading_calendar"]["partitions"]
                for row in store.read_partition(part).to_pylist()]
    samples = []
    for identity in inverse:
        master = masters.get(identity)
        if master is None:
            raise DataError("ETF source catalogue identity is absent from Snapshot")
        if fund_identity(master["source_code"], str(master["listing_date"]).replace('-', '')) != identity:
            raise DataError("ETF stable identity differs from supplier listing date")
        for row in calendar:
            day = str(row["session"])
            if (row["exchange"] != master["exchange"] or not row["is_open"] or
                    not plan.warmup_start_session <= day <= plan.end_session or
                    day < str(master["listing_date"]) or
                    master["delisting_date"] is not None and day >= str(master["delisting_date"])):
                continue
            observed = latest_daily.get((identity, day))
            if observed is None or observed["close"] is None:
                report["missing_market_cells"] += 1
                if len(samples) < 20:
                    samples.append({"security_id": identity, "session": day, "reason": "supplier_bar_missing"})
    if report["missing_market_cells"]:
        report["status"] = "limited"
        report["issues"].append({"kind": "warning", "reason": "supplier_market_gaps", "sample": samples})
    return report
