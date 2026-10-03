"""Current Raw -> Parquet -> Snapshot CLI. Supplier and legacy imports stay lazy."""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any


_PLAN_SCHEMA = "axiom_data_bulk_job_cli_v1"
_PLAN_SCHEMA_V2 = "axiom_data_bulk_job_cli_v2"
_FULL_SCHEMA = "axiom_data_full_source_cli_v1"
_ETF_SCHEMA = "axiom_data_etf_job_cli_v1"
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_SCOPE_KEYS = frozenset({"mode", "symbols", "start_session", "end_session",
                         "identity_map", "benchmark_codes", "index_codes", "endpoints",
                         "max_requests_per_chunk", "request_strategy", "membership_source"})
_QUERY_KEYS = frozenset({"domain", "fields", "symbols", "sessions", "pit_policy",
                         "cutoff_by_session", "purpose", "price_basis", "adjustment_anchor",
                         "universe_id", "policy_by_session"})
_EVENT_KEYS = frozenset({"domain", "fields", "symbols", "start", "end", "cutoff",
                         "pit_policy", "time_field", "filters", "purpose"})
_REBUILD_KEYS = frozenset({"base_snapshot", "raw_batch_ids", "domains", "operation_id",
                           "build_context", "promote", "domain_overrides"})
_PREPARE_SCHEMA = "axiom_data_preparation_config_v1"
_ETF_PREPARE_SCHEMA = "axiom_data_etf_preparation_config_v1"
_ETF_PREPARE_KEYS = frozenset({"schema_version", "preparation_id", "symbols",
                               "start_session", "end_session", "benchmark_codes",
                               "mode", "base_snapshot", "max_requests_per_chunk",
                               "max_attempts", "max_workers", "global_calls_per_minute",
                               "stock_basic_calls_per_minute", "min_interval_seconds"})
_PREPARE_KEYS = frozenset({"schema_version", "preparation_id", "reference_operation_id",
                           "preset", "scope_mode", "anchor_session", "start_session",
                           "end_session", "mode", "endpoints", "benchmark_codes",
                           "max_requests_per_chunk", "request_strategy", "symbol_limit",
                           "required_symbol_count", "previous_preparation",
                           "max_attempts", "min_interval_seconds"})


class _CredentialRequired(BaseException):
    """Stop before a source attempt or Raw checkpoint when no credential exists."""


class _LazySourceClient:
    def __init__(self, token_file: Path | None):
        self.token_file = token_file
        self._client = None

    def query(self, endpoint: str, *, fields: str = "", **params):
        if self._client is None:
            if self.token_file is None and not (os.environ.get("TUSHARE_TOKEN") or os.environ.get("TS_TOKEN")):
                raise _CredentialRequired()
            from .live_client import TushareHttpClient
            try:
                self._client = TushareHttpClient(token_file=self.token_file)
            except Exception:
                raise _CredentialRequired() from None
        return self._client.query(endpoint, fields=fields, **params)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="axiom-data",
        description="Current local Raw, Parquet and Snapshot workflow.")
    parser.add_argument("--data-root", type=Path, help="Local data root")
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="Collect reference evidence and write a stable symbol scope")
    prepare.add_argument("--config", required=True, type=Path, help="Saved preparation configuration JSON")
    prepare.add_argument("--output-scope", required=True, type=Path, help="Generated source scope JSON")
    prepare.add_argument("--token-file", type=Path, help="Private plain-text token or SDK CSV")
    prepare.add_argument("--market-only", action="store_true",
                         help="Explicitly skip membership and listing source collection")
    plan = commands.add_parser("plan", help="Write an immutable, reviewable source job plan")
    plan.add_argument("--scope", required=True, type=Path, help="JSON source scope")
    plan.add_argument("--output", required=True, type=Path, help="New plan JSON path")
    plan.add_argument("--operation-id", required=True)
    plan.add_argument("--base-snapshot", help="Concrete ID or current; default captures current if present")
    plan.add_argument("--no-promote", action="store_true")
    plan.add_argument("--max-attempts", type=int, default=3)
    plan.add_argument("--min-interval-seconds", type=float,
                      help="Extra minimum gap; default 0 for batch v2, 1.25 for v1")
    plan.add_argument("--max-workers", type=int, default=8)
    plan.add_argument("--global-calls-per-minute", type=int, default=300)
    plan.add_argument("--stock-basic-calls-per-minute", type=int, default=50)
    plan.add_argument("--market-only", action="store_true",
                      help="Explicitly limit this job to market/reference data")
    plan.add_argument("--event-endpoint", action="append",
                      choices=("income", "balancesheet", "cashflow", "fina_indicator",
                               "dividend", "top10_holders", "stk_limit"),
                      help="Repeat to choose domains; default is all seven")
    plan.add_argument("--income-strategy", choices=("vip_month", "symbol_year"),
                      default="vip_month", help="VIP monthly all-market or ordinary per-symbol annual")
    plan.add_argument("--calendar-end", help="Frozen source-as-of calendar tail, ISO date")
    plan.add_argument("--event-announcement-lookback-days", type=int, default=31,
                      help="Daily full-source announcement refresh, inclusive calendar days (1-365)")
    run = commands.add_parser("run", help="Resume a planned job and publish after completion")
    run.add_argument("--plan", required=True, type=Path)
    run.add_argument("--token-file", type=Path, help="Private plain-text token or SDK CSV")
    status = commands.add_parser("status", help="Show durable job progress without source calls")
    status.add_argument("--plan", required=True, type=Path)
    read = commands.add_parser("read", help="Read a pinned Snapshot")
    read.add_argument("--snapshot", default="current")
    read.add_argument("--query", required=True, type=Path, help="QuerySpec or EventQuery JSON")
    read.add_argument("--kind", choices=("facts", "market", "members", "states", "events"),
                      default="facts")
    inspect = commands.add_parser("inspect", help="Inspect inventory or an explicit required scope")
    inspect.add_argument("--snapshot", default="current")
    inspect.add_argument("--query", type=Path, help="Optional QuerySpec JSON")
    audit = commands.add_parser("audit", help="Independently check saved source and Snapshot evidence")
    audit.add_argument("--plan", required=True, type=Path)
    audit.add_argument("--snapshot", default="current")
    audit.add_argument("--output", required=True, type=Path, help="Atomic JSON report path")
    rebuild = commands.add_parser("rebuild", help="Rebuild from retained Raw, without a supplier")
    rebuild.add_argument("--request", type=Path, help="Advanced explicit rebuild request JSON")
    rebuild.add_argument("--snapshot", default="current", help="Base Snapshot for automatic Raw selection")
    rebuild.add_argument("--operation-id", help="Required without --request")
    rebuild.add_argument("--domain", action="append", help="Domain to rebuild; repeat to select several")
    rebuild.add_argument("--no-promote", action="store_true")
    export = commands.add_parser("export", help="Create a portable, verified bundle")
    export.add_argument("--snapshot", default="current")
    export.add_argument("--destination", required=True, type=Path)
    export.add_argument("--code-root", type=Path)
    export.add_argument("--raw-backup-cutoff", help="Also back up all Raw receipts through this timezone-aware instant")
    import_cmd = commands.add_parser("import", help="Verify and import a bundle to a new root")
    import_cmd.add_argument("--bundle", required=True, type=Path)
    qlib_export = commands.add_parser("qlib-export", help="Explicit immutable Qlib daily projection, without source calls")
    qlib_export.add_argument("--snapshot", default="current")
    qlib_export.add_argument("--spec", required=True, type=Path)
    qlib_export.add_argument("--destination", required=True, type=Path)
    qlib_verify = commands.add_parser("qlib-verify", help="Verify Qlib files and optionally compare the source Reader")
    qlib_verify.add_argument("--view", required=True, type=Path)
    qlib_verify.add_argument("--against-reader", action="store_true")
    verify = commands.add_parser("verify", help="Verify a completed job or portable bundle without writes")
    verify_target = verify.add_mutually_exclusive_group(required=True)
    verify_target.add_argument("--bundle", type=Path)
    verify_target.add_argument("--plan", type=Path)
    return parser


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)


def _digest(value: Any) -> str:
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _read_object(path: Path, label: str) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _write_immutable_json(path: Path, value: Any) -> None:
    """Publish JSON once; an identical retry reuses the same artifact."""
    encoded = (_json(value) + "\n").encode("utf-8")
    if path.exists():
        if path.read_bytes() != encoded:
            raise ValueError(f"existing output differs: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(name, path)
    except FileExistsError:
        if path.read_bytes() != encoded:
            raise ValueError(f"existing output differs: {path}") from None
    finally:
        os.unlink(name)


def _write_atomic_json(path: Path, value: Any) -> None:
    encoded = (_json(value) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _only_keys(value: dict[str, Any], allowed: frozenset[str], label: str) -> None:
    extra = set(value) - allowed
    if extra:
        raise ValueError(f"{label} has unsupported fields: {', '.join(sorted(extra))}")


def _root(args: argparse.Namespace) -> Path:
    if args.data_root is None:
        raise ValueError("--data-root is required for this command")
    return args.data_root


def _base_for_plan(root: Path, requested: str | None) -> str | None:
    from .api import Data
    data = Data(root)
    if requested is not None:
        return data.resolve(requested)
    return data.resolve("current") if (root / "current.json").exists() else None


def _load_plan(path: Path):
    from .bulk_jobs import BulkJobPlan
    envelope = _read_object(path, "plan")
    required = {"schema_version", "job", "operation_id", "base_snapshot", "promote",
                "max_attempts", "min_interval_seconds", "plan_sha256"}
    if envelope.get("schema_version") in {_PLAN_SCHEMA_V2, _FULL_SCHEMA, _ETF_SCHEMA}:
        required |= {"max_workers", "global_calls_per_minute", "stock_basic_calls_per_minute"}
    if set(envelope) != required or envelope["schema_version"] not in {_PLAN_SCHEMA, _PLAN_SCHEMA_V2, _FULL_SCHEMA, _ETF_SCHEMA}:
        raise ValueError("unsupported or incomplete CLI plan")
    body = {key: value for key, value in envelope.items() if key != "plan_sha256"}
    if _digest(body) != envelope["plan_sha256"]:
        raise ValueError("CLI plan digest mismatch")
    if envelope["schema_version"] == _FULL_SCHEMA:
        from .full_sources import FullSourcePlan
        job = FullSourcePlan.from_dict(envelope["job"])
    elif envelope["schema_version"] == _ETF_SCHEMA:
        from .etf_jobs import EtfJobPlan
        job = EtfJobPlan.from_dict(envelope["job"])
    else:
        job = BulkJobPlan.from_dict(envelope["job"])
    if not isinstance(envelope["operation_id"], str) or not _ID.fullmatch(envelope["operation_id"]):
        raise ValueError("invalid plan operation ID")
    if envelope["base_snapshot"] is not None and not isinstance(envelope["base_snapshot"], str):
        raise ValueError("invalid plan base Snapshot")
    if type(envelope["promote"]) is not bool:
        raise ValueError("invalid plan promote flag")
    if type(envelope["max_attempts"]) is not int or not 1 <= envelope["max_attempts"] <= 10:
        raise ValueError("invalid plan attempt limit")
    interval = envelope["min_interval_seconds"]
    if type(interval) not in (float, int) or not 0 <= interval <= 3600:
        raise ValueError("invalid plan pacing interval")
    if envelope["schema_version"] in {_PLAN_SCHEMA_V2, _FULL_SCHEMA, _ETF_SCHEMA}:
        if (envelope["schema_version"] != _ETF_SCHEMA and
                (job.market if envelope["schema_version"] == _FULL_SCHEMA else job).request_strategy != "trading_day_market_v2"):
            raise ValueError("CLI v2 plan requires a batch v2 job")
        if type(envelope["max_workers"]) is not int or not 1 <= envelope["max_workers"] <= 32:
            raise ValueError("invalid plan worker count")
        for name in ("global_calls_per_minute", "stock_basic_calls_per_minute"):
            if type(envelope[name]) is not int or envelope[name] < 1:
                raise ValueError(f"invalid plan {name}")
    return envelope, job


def _runner_fingerprint(envelope: dict[str, Any], job: Any) -> str:
    # Match the durable run identity, including options that affect recovery.
    if envelope["schema_version"] == _ETF_SCHEMA:
        return _digest({"plan": job.to_dict(), "base": envelope["base_snapshot"],
                        "promote": envelope["promote"], "options": {
                            key: envelope[key] for key in ("max_attempts", "min_interval_seconds",
                            "max_workers", "global_calls_per_minute", "stock_basic_calls_per_minute")}})
    identity = {"plan": job.to_dict(), "base_snapshot": envelope["base_snapshot"],
                "promote": envelope["promote"], "max_attempts": envelope["max_attempts"],
                "min_interval_seconds": envelope["min_interval_seconds"]}
    if envelope["schema_version"] in {_PLAN_SCHEMA_V2, _FULL_SCHEMA}:
        identity.update(max_workers=envelope["max_workers"],
                        global_calls_per_minute=envelope["global_calls_per_minute"],
                        stock_basic_calls_per_minute=envelope["stock_basic_calls_per_minute"])
    return sha256(json.dumps(identity, sort_keys=True,
                             separators=(",", ":"),
                             ensure_ascii=envelope["schema_version"] != _FULL_SCHEMA,
                             allow_nan=False).encode()).hexdigest()


def _prepare_etf(args: argparse.Namespace, config: dict[str, Any], *, client: Any = None) -> dict[str, Any]:
    from .etf_jobs import prepare_etf_references
    from .provider_etf import ETF_SYMBOLS
    from .storage import LocalStore

    _only_keys(config, _ETF_PREPARE_KEYS, "ETF preparation config")
    preparation_id = config.get("preparation_id")
    if not isinstance(preparation_id, str) or not _ID.fullmatch(preparation_id):
        raise ValueError("ETF preparation_id must be a safe identifier")
    if args.market_only:
        raise ValueError("--market-only applies to stock jobs, not ETF preparation")
    if client is None:
        client = _LazySourceClient(args.token_file)
    if "base_snapshot" in config:
        base = (_base_for_plan(_root(args), config["base_snapshot"])
                if config["base_snapshot"] is not None else None)
    else:
        previous = LocalStore(_root(args)).read_operation(f"{preparation_id}.publish")
        base = (previous["base_snapshot"] if previous is not None
                else _base_for_plan(_root(args), None))
    options = {key: config[key] for key in ("max_attempts", "max_workers",
        "global_calls_per_minute", "stock_basic_calls_per_minute", "min_interval_seconds")
        if key in config}
    scope = prepare_etf_references(LocalStore(_root(args)), client=client,
        symbols=config.get("symbols", ETF_SYMBOLS), start_session=config["start_session"],
        end_session=config["end_session"], benchmark_codes=config.get("benchmark_codes", ["000300.SH"]),
        mode=config.get("mode", "bulk"), base_snapshot=base,
        max_requests_per_chunk=config.get("max_requests_per_chunk", 256),
        operation_id=preparation_id, **options)
    if args.output_scope.exists() and _read_object(args.output_scope, "existing ETF scope") == scope:
        pass
    else:
        _write_immutable_json(args.output_scope, scope)
    return {"status": "prepared", "preparation_id": preparation_id,
            "scope_file": str(args.output_scope), "scope_sha256": _digest(scope),
            "symbols": len(scope["symbols"]), "reference_snapshot": scope["reference_snapshot"],
            "warmup_start_session": scope["warmup_start_session"]}


def _plan_etf(args: argparse.Namespace, scope: dict[str, Any]) -> dict[str, Any]:
    from .etf_jobs import EtfJobPlan, estimate_etf_job
    from .storage import LocalStore

    if args.market_only or args.event_endpoint or args.calendar_end:
        raise ValueError("stock source options do not apply to an ETF plan")
    job = EtfJobPlan.from_dict(scope)
    reference = LocalStore(_root(args)).load_snapshot(job.reference_snapshot)
    base = reference["parent_snapshot"]
    if args.base_snapshot is not None and _base_for_plan(_root(args), args.base_snapshot) != base:
        raise ValueError("ETF base Snapshot differs from prepared reference parent")
    interval = 0 if args.min_interval_seconds is None or args.min_interval_seconds == 0 else args.min_interval_seconds
    if (type(args.max_attempts) is not int or not 1 <= args.max_attempts <= 10 or
            type(args.max_workers) is not int or not 1 <= args.max_workers <= 32 or
            type(args.global_calls_per_minute) is not int or args.global_calls_per_minute < 1 or
            type(args.stock_basic_calls_per_minute) is not int or args.stock_basic_calls_per_minute < 1 or
            type(interval) not in (int, float) or not 0 <= interval <= 3600):
        raise ValueError("ETF run limits must be positive and bounded")
    body = {"schema_version": _ETF_SCHEMA, "job": job.to_dict(),
            "operation_id": args.operation_id, "base_snapshot": base,
            "promote": not args.no_promote, "max_attempts": args.max_attempts,
            "min_interval_seconds": interval, "max_workers": args.max_workers,
            "global_calls_per_minute": args.global_calls_per_minute,
            "stock_basic_calls_per_minute": args.stock_basic_calls_per_minute}
    envelope = {**body, "plan_sha256": _digest(body)}
    _write_immutable_json(args.output, envelope)
    return {"plan": str(args.output), "operation_id": args.operation_id,
            "base_snapshot": base, "estimate": estimate_etf_job(job,
                min_interval_seconds=interval)}


def _query(path: Path, *, event: bool = False):
    from .protocols import EventQuery, QuerySpec
    value = _read_object(path, "query")
    _only_keys(value, _EVENT_KEYS if event else _QUERY_KEYS, "query")
    return EventQuery(**value) if event else QuerySpec(**value)


def _result(receipt: Any) -> dict[str, Any]:
    return {"operation_id": receipt.operation_id, "snapshot_id": receipt.snapshot_id,
            "changed": receipt.changed, "status": receipt.status}


def _reusable_reference_raw(store: Any, previous: dict[str, Any] | None,
                            plan: dict[str, Any]) -> tuple[str, ...]:
    """Select exact old reference responses that also exist in this data root.

    A longer reference window changes calendar selectors; those old calendar
    responses cannot be reused. A preparation file from another root can keep
    identity bindings without implying that its Raw bytes were transferred.
    """
    if previous is None:
        return ()
    from .protocols import DataError
    from .universe_sources import _reference_profile

    prior_ids = previous.get("reference_raw_batch_ids", ())
    if (not isinstance(prior_ids, list) or
            any(not isinstance(batch_id, str) for batch_id in prior_ids)):
        raise DataError("previous preparation has invalid Raw ID list")
    wanted = set(prior_ids)
    if not wanted:
        return ()
    log = store.root / "raw/fetches.jsonl"
    if not log.is_file():
        return ()
    present: set[str] = set()
    with log.open("rb") as stream:
        for line in stream:
            # A final incomplete append is not a committed receipt.
            if not line.endswith(b"\n"):
                break
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise DataError("current Raw fetch log is corrupt") from exc
            if not isinstance(record, dict):
                raise DataError("current Raw fetch log is invalid")
            batch_id = record.get("batch_id")
            if batch_id in wanted:
                present.add(batch_id)
    if not present:
        return ()
    records = store.get_raw_many(sorted(present))
    planned_endpoints = {spec["endpoint"] for spec in plan["requests"]}

    def selector(endpoint: str, params: Any, fields: Any) -> tuple[str, str, tuple[str, ...]]:
        return endpoint, _digest(params), tuple(fields)

    candidates: dict[tuple[str, str, tuple[str, ...]], list[str]] = {}
    for batch_id in prior_ids:
        raw = records.get(batch_id)
        if not raw or raw.get("status") not in {"success", "empty"} or raw.get("domain") != "reference_bootstrap":
            continue
        request = raw.get("request", {})
        if not isinstance(request, dict):
            continue
        endpoint = request.get("endpoint")
        params, fields = request.get("params"), request.get("fields")
        if (endpoint not in planned_endpoints or not isinstance(params, dict) or
                not isinstance(fields, list) or any(not isinstance(field, str) for field in fields)):
            continue
        if raw.get("source_profile") != _reference_profile(endpoint):
            continue
        candidates.setdefault(selector(endpoint, params, fields), []).append(batch_id)
    selected: list[str] = []
    used: set[str] = set()
    for spec in plan["requests"]:
        key = selector(spec["endpoint"], spec["params"], spec["fields"])
        for batch_id in candidates.get(key, ()):
            if batch_id not in used:
                selected.append(batch_id)
                used.add(batch_id)
                break
    return tuple(selected)


def _daily_prepared_from_previous(seed: dict[str, Any], previous: dict[str, Any], *,
                                  start: str, end: str, scope_mode: str) -> dict[str, Any]:
    """Carry forward the frozen anchor while refreshing today's listing IDs."""
    from .protocols import ConflictError, CoverageError

    if (previous.get("version") != "prepared_universe_scope.v1" or
            not previous.get("ready_for_bulk") or
            not isinstance(previous.get("membership_source"), dict)):
        raise CoverageError("daily exact membership needs a completed previous preparation")
    identity_map = dict(previous["identity_map"])
    listing_dates = dict(previous["listing_dates"])
    for code, item in sorted(seed["catalogue"].items()):
        listing = item["listing_date"]
        if code in listing_dates and listing_dates[code] != listing:
            raise ConflictError("daily listing date differs from frozen security identity")
        listing_dates[code] = listing
        identity_map.setdefault(code, f"cnstock.{code}.{listing.replace('-', '')}")
    if len(set(identity_map.values())) != len(identity_map):
        raise ConflictError("daily refreshed security identities collide")
    observed_codes = set(seed["bounded_observations"]["candidate_union"])
    missing = sorted(observed_codes - set(identity_map))
    if missing:
        raise CoverageError("Tushare index member lacks stock_basic identity: "
                            + ", ".join(missing[:8]))
    symbols = (sorted(set(previous["symbols"]) | observed_codes)
               if scope_mode == "historical_union" else list(previous["symbols"]))
    return {**previous, "scope_mode": scope_mode, "start_session": start,
            "end_session": end, "symbols": symbols,
            "symbol_count": len(symbols),
            "identity_map": dict(sorted(identity_map.items())),
            "listing_dates": dict(sorted(listing_dates.items())),
            "calendar": seed["calendar"],
            "reference_plan_digest": seed["plan_digest"],
            "reference_operation_id": seed["operation_id"],
            "reference_raw_batch_ids": seed["raw_batch_ids"],
            "reference_raw_observations": seed.get("raw_observations", []),
            "index_selector_evidence": seed["index_selector_evidence"],
            "a_share_classification": seed["a_share_classification"]}


def _prepare(args: argparse.Namespace, *, client: Any = None) -> dict[str, Any]:
    config = _read_object(args.config, "preparation config")
    if config.get("schema_version") == _ETF_PREPARE_SCHEMA:
        return _prepare_etf(args, config, client=client)
    from .bulk_jobs import plan_bulk_job
    from .storage import LocalStore
    from .universe_sources import (plan_reference_bootstrap, prepare_universe_scope,
                                   resolve_index_preset, run_reference_bootstrap,
                                   save_prepared_scope)

    _only_keys(config, _PREPARE_KEYS, "preparation config")
    if config.get("schema_version") != _PREPARE_SCHEMA:
        raise ValueError("unsupported preparation config schema")
    preparation_id = config.get("preparation_id")
    reference_id = config.get("reference_operation_id") or f"{preparation_id}.reference"
    if not isinstance(preparation_id, str) or not _ID.fullmatch(preparation_id) or not _ID.fullmatch(reference_id):
        raise ValueError("preparation and reference operation IDs must be safe identifiers")
    preset = config.get("preset", "csi1800")
    indices = resolve_index_preset(preset)
    scope_mode = config.get("scope_mode", "anchor_members")
    if scope_mode not in {"anchor_members", "historical_union"}:
        raise ValueError("scope_mode must be anchor_members or historical_union")
    start, end = config["start_session"], config["end_session"]
    anchor = config.get("anchor_session", end)
    limit = config.get("symbol_limit")
    required_count = config.get("required_symbol_count")
    if limit is not None and (type(limit) is not int or limit < 1):
        raise ValueError("symbol_limit must be a positive integer")
    if required_count is not None and (type(required_count) is not int or required_count < 1):
        raise ValueError("required_symbol_count must be a positive integer")
    endpoints = config.get("endpoints", ["trade_cal", "stock_basic", "daily",
                                         "adj_factor", "suspend_d", "index_daily"])
    if not isinstance(endpoints, list) or not endpoints or any(not isinstance(item, str)
            for item in endpoints) or len(set(endpoints)) != len(endpoints):
        raise ValueError("endpoints must be a nonempty unique list")
    if "index_weight" in endpoints and (scope_mode != "historical_union" or limit is not None):
        raise ValueError("index_weight needs an untruncated historical_union scope")
    if config.get("request_strategy", "trading_day_market_v2") != "trading_day_market_v2":
        raise ValueError("prepared delivery requires trading_day_market_v2")
    chunk_size = config.get("max_requests_per_chunk", 1000)
    if type(chunk_size) is not int or not 1 <= chunk_size <= 6000:
        raise ValueError("max_requests_per_chunk must be in [1,6000]")
    reference_start = start
    if not args.market_only:
        from datetime import date, timedelta
        month_start = date.fromisoformat(start).replace(day=1)
        reference_start = (month_start - timedelta(days=1)).replace(day=1).isoformat()
    reference_plan = plan_reference_bootstrap(index_codes=indices,
        start_session=reference_start, end_session=end, include_calendar=True)
    max_attempts = config.get("max_attempts", 3)
    interval = config.get("min_interval_seconds", 1.25)
    if type(max_attempts) is not int or not 1 <= max_attempts <= 10:
        raise ValueError("reference max_attempts must be in [1,10]")
    if type(interval) not in (int, float) or not 0 <= interval <= 3600:
        raise ValueError("reference min_interval_seconds must be in [0,3600]")
    previous = None
    if config.get("previous_preparation"):
        previous_path = Path(config["previous_preparation"])
        if not previous_path.is_absolute():
            previous_path = args.config.parent / previous_path
        previous = _read_object(previous_path, "previous preparation")
    if config.get("mode", "bulk") == "daily" and not args.market_only and (
            previous is None or not isinstance(previous.get("membership_source"), dict)):
        raise ValueError("daily Tushare membership requires previous_preparation")
    # A debug fact scope can be small while the membership source remains the
    # complete supplier snapshot. Limit only the later canonical selection.
    store = LocalStore(_root(args))
    reusable_ids = _reusable_reference_raw(store, previous, reference_plan)
    if config.get("mode", "bulk") == "daily" and not args.market_only:
        reusable_ids = tuple(batch_id for batch_id in reusable_ids
                             if store.get_raw(batch_id)["request"]["endpoint"] != "stock_basic")
    if client is None:
        client = _LazySourceClient(args.token_file)
    seed = run_reference_bootstrap(store, plan=reference_plan, client=client,
        operation_id=reference_id, max_attempts=max_attempts,
        min_interval_seconds=interval,
        reuse_raw_batch_ids=reusable_ids)
    if config.get("mode", "bulk") == "daily" and not args.market_only:
        prepared = _daily_prepared_from_previous(seed, previous, start=start,
                                                  end=end, scope_mode=scope_mode)
    else:
        prepared = prepare_universe_scope(seed, anchor_session=anchor,
            scope_mode=scope_mode, previous_bindings=previous, preset=preset)
        prepared = {**prepared, "start_session": start, "end_session": end}
    if not prepared["ready_for_bulk"]:
        raise ValueError("reference scope is not ready for bulk; inspect saved reference Raw")
    membership_source = None
    if not args.market_only:
        observations = seed.get("raw_observations", ())
        weight_ids = [item["batch_id"] for item in observations
                      if item.get("endpoint") == "index_weight"]
        listing_ids = [item["batch_id"] for item in observations
                       if item.get("endpoint") == "stock_basic"]
        if not weight_ids:
            raise ValueError("Tushare index_weight preparation has no saved Raw")
        if len(listing_ids) != 6:
            raise ValueError("Tushare stock_basic preparation needs six saved status slices")
        if config.get("mode", "bulk") == "daily":
            prior_source = previous["membership_source"]
            if prior_source.get("schema_version") != "tushare_csi1800_membership_source_v1":
                raise ValueError("daily Tushare membership needs a previous Tushare preparation")
            weight_ids = list(dict.fromkeys(
                [*prior_source["index_weight_raw_batch_ids"], *weight_ids]))
            listing_ids = list(dict.fromkeys(
                [*prior_source["stock_basic_raw_batch_ids"], *listing_ids]))
        calendar = seed["calendar"]["open_sessions_by_exchange"]
        common_open = sorted(set(calendar["SSE"]) & set(calendar["SZSE"]))
        through = [day for day in common_open if day <= end]
        if not through:
            raise ValueError("Tushare trade_cal has no common open session through preparation end")
        membership_source = {
            "schema_version": "tushare_csi1800_membership_source_v1",
            "index_weight_raw_batch_ids": weight_ids,
            "stock_basic_raw_batch_ids": listing_ids,
            "verified_through": through[-1],
            "between_snapshots": "carry_forward",
        }
        prepared = {**prepared,
                    "membership_basis": "tushare_dated_index_weight_snapshots",
                    "membership_source": membership_source}
    symbols = list(prepared["symbols"])
    if limit is not None:
        if type(limit) is not int or not 1 <= limit <= len(symbols):
            raise ValueError("symbol_limit must be in [1, prepared symbol count]")
        symbols = symbols[:limit]
    if required_count is not None and (type(required_count) is not int or
                                       (len(prepared["anchor_members"]) if membership_source else len(symbols))
                                       != required_count):
        raise ValueError("prepared anchor count differs from required_symbol_count")
    scope = {"mode": config.get("mode", "bulk"), "symbols": symbols,
             "start_session": start, "end_session": end,
             "identity_map": prepared["identity_map"],
             "benchmark_codes": config.get("benchmark_codes", [indices[0]]),
             "index_codes": list(indices) if "index_weight" in endpoints else [],
             "endpoints": endpoints,
             "max_requests_per_chunk": chunk_size,
             "request_strategy": config.get("request_strategy", "trading_day_market_v2")}
    if membership_source:
        scope["membership_source"] = membership_source
    # Reject an unusable scope before publishing its immutable review artifacts.
    plan_bulk_job(**{key: value for key, value in scope.items() if key != "membership_source"})
    saved = {**prepared, "delivery_selection": {"symbol_limit": limit,
        "selected_symbol_count": len(symbols), "required_symbol_count": required_count,
        "selection_basis": ("tushare_index_weight_candidate_union" if membership_source else
                            "source_anchor_or_union" if limit is None or limit == prepared["symbol_count"]
                            else "sorted_source_code_subset_for_debug")},
        "source_scope_sha256": _digest(scope)}
    preparation_file = save_prepared_scope(store, saved, preparation_id=preparation_id)
    _write_immutable_json(args.output_scope, scope)
    return {"status": "prepared", "preparation_id": preparation_id,
            "preparation_file": preparation_file, "scope_file": str(args.output_scope),
            "scope_sha256": _digest(scope), "source": preset,
            "scope_mode": scope_mode, "symbols": len(symbols),
            "anchor_nominal_1800": prepared["anchor_nominal_1800"],
            "historical_union_candidates": len(prepared["historical_union"]),
            "reference_requests": len(reference_plan["requests"]),
            "reference_raw_batches": len(seed["raw_batch_ids"]),
            "membership_raw_batches": (len(membership_source["index_weight_raw_batch_ids"])
                                       if membership_source else 0),
            "membership_basis": prepared["membership_basis"]}


def _execute(args: argparse.Namespace, *, client: Any = None) -> dict[str, Any]:
    command = args.command
    if command == "prepare":
        return _prepare(args, client=client)
    if command == "plan":
        scope = _read_object(args.scope, "scope")
        if not _ID.fullmatch(args.operation_id):
            raise ValueError("operation ID must use letters, numbers, _, . or -")
        if scope.get("schema_version") == "axiom_data_etf_scope_v1":
            return _plan_etf(args, scope)
        _only_keys(scope, _SCOPE_KEYS, "scope")
        from .bulk_jobs import estimate_bulk_job, plan_bulk_job
        membership_source = scope.pop("membership_source", None)
        job = plan_bulk_job(**scope)
        include_events = not args.market_only
        if include_events and membership_source is None:
            raise ValueError("complete source planning requires prepared Tushare membership; use --market-only for a market-only plan")
        if not 1 <= args.event_announcement_lookback_days <= 365:
            raise ValueError("event announcement lookback days must be in [1,365]")
        if include_events:
            from datetime import datetime, timedelta
            from zoneinfo import ZoneInfo
            from .full_sources import estimate_full_sources, plan_full_sources
            today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
            tail = max(today, datetime.fromisoformat(job.end_session).date()) + timedelta(days=31)
            job = plan_full_sources(
                market=job, event_endpoints=args.event_endpoint or
                ("income", "balancesheet", "cashflow", "fina_indicator",
                 "dividend", "top10_holders", "stk_limit"),
                income_strategy=args.income_strategy,
                calendar_end=args.calendar_end or tail.isoformat(),
                max_event_requests_per_chunk=job.max_requests_per_chunk,
                membership_source=membership_source,
                announcement_start=(
                    datetime.fromisoformat(job.end_session).date() -
                    timedelta(days=args.event_announcement_lookback_days - 1)
                ).isoformat() if job.mode == "daily" else None)
        elif args.event_endpoint or args.calendar_end:
            raise ValueError("event options require a full source plan")
        if type(args.max_attempts) is not int or not 1 <= args.max_attempts <= 10:
            raise ValueError("max attempts must be in [1,10]")
        strategy = getattr(job.market if include_events else job, "request_strategy", "trading_day_market_v2")
        interval = (0.0 if strategy == "trading_day_market_v2" else 1.25) if args.min_interval_seconds is None else args.min_interval_seconds
        if not 0 <= interval <= 3600:
            raise ValueError("pacing interval must be in [0,3600]")
        if type(args.max_workers) is not int or not 1 <= args.max_workers <= 32:
            raise ValueError("max_workers must be in [1,32]")
        if (type(args.global_calls_per_minute) is not int or args.global_calls_per_minute < 1 or
                type(args.stock_basic_calls_per_minute) is not int or args.stock_basic_calls_per_minute < 1):
            raise ValueError("rate limits must be positive integers")
        body = {"schema_version": (_FULL_SCHEMA if include_events else
                                   _PLAN_SCHEMA_V2 if strategy == "trading_day_market_v2" else _PLAN_SCHEMA),
                "job": job.to_dict(),
                "operation_id": args.operation_id,
                "base_snapshot": _base_for_plan(_root(args), args.base_snapshot),
                "promote": not args.no_promote, "max_attempts": args.max_attempts,
                "min_interval_seconds": interval}
        if strategy == "trading_day_market_v2":
            body.update(max_workers=args.max_workers,
                        global_calls_per_minute=args.global_calls_per_minute,
                        stock_basic_calls_per_minute=args.stock_basic_calls_per_minute)
        envelope = {**body, "plan_sha256": _digest(body)}
        estimate = (estimate_full_sources(job) if include_events else
                    estimate_bulk_job(job, min_interval_seconds=interval))
        encoded = _json(envelope) + "\n"
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(encoded)
        return {"plan": str(args.output), "operation_id": args.operation_id,
                "base_snapshot": body["base_snapshot"], "estimate": estimate}
    if command == "run":
        from .storage import LocalStore
        envelope, job = _load_plan(args.plan)
        if client is None:
            # A completed or fully checkpointed job can replay without a token.
            # The SDK's ~/tk.csv fallback is never used by this CLI.
            client = _LazySourceClient(args.token_file)
        if envelope["schema_version"] == _ETF_SCHEMA:
            from .etf_jobs import etf_job_status, run_etf_job, verify_etf_job
            runner, status_fn, verify_fn = run_etf_job, etf_job_status, verify_etf_job
        elif envelope["schema_version"] == _FULL_SCHEMA:
            from .full_sources import (full_source_status, run_full_sources,
                                       verify_full_sources)
            runner, status_fn, verify_fn = run_full_sources, full_source_status, verify_full_sources
        else:
            from .bulk_jobs import bulk_job_status, run_bulk_job, verify_bulk_job
            runner, status_fn, verify_fn = run_bulk_job, bulk_job_status, verify_bulk_job
        store = LocalStore(_root(args))
        receipt = runner(store, plan=job, client=client,
                               operation_id=envelope["operation_id"],
                               base_snapshot=envelope["base_snapshot"],
                               promote=envelope["promote"],
                               max_attempts=envelope["max_attempts"],
                               min_interval_seconds=envelope["min_interval_seconds"],
                               **({name: envelope[name] for name in
                                   ("max_workers", "global_calls_per_minute", "stock_basic_calls_per_minute")}
                                  if envelope["schema_version"] in {_PLAN_SCHEMA_V2, _FULL_SCHEMA, _ETF_SCHEMA} else {}))
        response = _result(receipt)
        state = store.read_operation(envelope["operation_id"])
        if state is not None and state.get("status") == "success":
            progress = status_fn(store, plan=job, operation_id=envelope["operation_id"])
            response["progress"] = ({key: progress[key] for key in
                ("status", "planned_requests", "completed_requests", "next_chunk", "total_chunks")
                if key in progress} if envelope["schema_version"] == _ETF_SCHEMA else progress)
            response["verification"] = verify_fn(store, plan=job,
                operation_id=envelope["operation_id"])
        return response
    if command == "status":
        from .storage import LocalStore
        envelope, job = _load_plan(args.plan)
        if envelope["schema_version"] == _ETF_SCHEMA:
            from .etf_jobs import etf_job_status as status_fn
        elif envelope["schema_version"] == _FULL_SCHEMA:
            from .full_sources import full_source_status as status_fn
        else:
            from .bulk_jobs import bulk_job_status as status_fn
        operation_id = envelope["operation_id"]
        store = LocalStore(_root(args))
        state = store.read_operation(operation_id)
        if state is None:
            return {"operation_id": operation_id, **status_fn(store, plan=job,
                                                       operation_id=operation_id)}
        expected_kind = ("etf_job_v1" if envelope["schema_version"] == _ETF_SCHEMA else
                         "full_source_job_v1" if envelope["schema_version"] == _FULL_SCHEMA
                         else "bulk_job_v2" if envelope["schema_version"] == _PLAN_SCHEMA_V2 else "bulk_job")
        if state.get("kind") != expected_kind:
            raise ValueError("operation ID belongs to a different job kind")
        if (state.get("plan_fingerprint") != job.fingerprint() or
                state.get("base_snapshot") != envelope["base_snapshot"] or
                state.get("fingerprint") != _runner_fingerprint(envelope, job)):
            raise ValueError("checkpoint belongs to a different plan")
        result = state.get("result") or {}
        progress = status_fn(store, plan=job, operation_id=operation_id)
        if envelope["schema_version"] == _ETF_SCHEMA:
            return {"operation_id": operation_id, "snapshot_id": state.get("candidate_snapshot"),
                    "status": progress["status"], "planned_requests": progress["planned_requests"],
                    "completed_requests": progress.get("completed_requests", 0),
                    "next_chunk": progress.get("next_chunk", 0),
                    "total_chunks": progress.get("total_chunks")}
        issue = progress.get("error")
        issue_type = issue.get("type") or issue.get("error_type") if isinstance(issue, dict) else None
        safe_issue = ({"type": issue_type if isinstance(issue_type, str) and _ID.fullmatch(issue_type)
                       else "unknown"} if issue is not None else None)
        progress["error"] = safe_issue
        return {"operation_id": operation_id, "snapshot_id": result.get("snapshot_id"),
                **progress}
    if command == "audit":
        from .api import Data
        from .protocols import DataError
        from .verification import audit_snapshot
        envelope, job = _load_plan(args.plan)
        data = Data(_root(args))
        try:
            snapshot = data.resolve(args.snapshot)
            state = data.store.read_operation(envelope["operation_id"])
            result_snapshot = (state.get("candidate_snapshot") if envelope["schema_version"] == _ETF_SCHEMA
                               else state.get("result", {}).get("snapshot_id")) if state else None
            if (state is None or state.get("status") != "success" or
                    result_snapshot != snapshot or
                    state.get("plan_fingerprint") != job.fingerprint()):
                raise DataError("audit Snapshot is not the completed result of the supplied plan")
            if envelope["schema_version"] == _ETF_SCHEMA:
                from .etf_jobs import audit_etf_snapshot
                report = audit_etf_snapshot(data.store, snapshot_id=snapshot, plan=job)
            else:
                report = audit_snapshot(data.store, snapshot_id=snapshot,
                                        plan=job.market if envelope["schema_version"] == _FULL_SCHEMA else job,
                                        base_snapshot=state.get("base_snapshot"))
        except DataError as exc:
            report = {"status": "failed", "snapshot": args.snapshot,
                      "plan_operation_id": envelope["operation_id"],
                      "issues": [{"type": type(exc).__name__, "message": str(exc)}]}
        _write_atomic_json(args.output, report)
        return {"status": report["status"], "report": str(args.output),
                "snapshot_id": report.get("snapshot_id", args.snapshot),
                "issues": len(report.get("issues", []))}
    if command in {"read", "inspect", "rebuild"}:
        from .api import Data
        data = Data(_root(args))
        if command == "rebuild":
            if args.request:
                if args.operation_id or args.domain or args.no_promote or args.snapshot != "current":
                    raise ValueError("--request cannot be combined with automatic rebuild options")
                request = _read_object(args.request, "rebuild request")
                _only_keys(request, _REBUILD_KEYS, "rebuild request")
                if request.get("base_snapshot") == "current":
                    request["base_snapshot"] = data.resolve("current")
            else:
                if not args.operation_id or not _ID.fullmatch(args.operation_id):
                    raise ValueError("rebuild needs a safe --operation-id or --request")
                snapshot = data.resolve(args.snapshot)
                manifest = data.store.load_snapshot(snapshot)
                domains = list(args.domain or manifest["domains"])
                if not domains or len(set(domains)) != len(domains):
                    raise ValueError("rebuild domains must be nonempty and unique")
                unknown = set(domains) - set(manifest["domains"])
                if unknown:
                    raise ValueError(f"Snapshot has no domain: {', '.join(sorted(unknown))}")
                raw_ids = list(dict.fromkeys(batch_id
                    for domain in domains
                    for batch_id in manifest["domains"][domain]["raw_batch_ids"]))
                if not raw_ids:
                    raise ValueError("selected domains have no saved Raw batches")
                request = {"base_snapshot": snapshot, "raw_batch_ids": raw_ids,
                           "domains": domains, "operation_id": args.operation_id,
                           "build_context": {"reason": "cli_rebuild_saved_raw"},
                           "promote": not args.no_promote}
            return _result(data.rebuild(**request))
        snapshot = data.resolve(args.snapshot)
        if command == "inspect":
            scope = _query(args.query) if args.query else None
            return data.inspect(snapshot, required_scope=scope)
        query = _query(args.query, event=args.kind == "events")
        method = {"facts": data.read, "market": data.read_market,
                  "members": data.members, "states": data.states,
                  "events": data.events}[args.kind]
        return method(snapshot=snapshot, query=query).to_json()
    if command in {"qlib-export", "qlib-verify"}:
        from .api import Data
        from .protocols import QuerySpec
        from .qlib_export import verify_qlib_export
        if command == "qlib-verify":
            manifest = verify_qlib_export(args.view, data=Data(_root(args)) if args.against_reader else None)
            return {"verified": True, "view_id": manifest["view_id"], "snapshot_id": manifest["snapshot_id"],
                    "compared_to_reader": args.against_reader}
        spec = _read_object(args.spec, "Qlib export spec")
        _only_keys(spec, {"queries", "instrument_map", "field_aliases", "universe_query",
                          "universe_name", "rtol", "atol"}, "Qlib export spec")
        values = spec.pop("queries")
        for query in values:
            _only_keys(query, _QUERY_KEYS, "Qlib query")
        universe = spec.pop("universe_query", None)
        if universe is not None:
            _only_keys(universe, _QUERY_KEYS, "Qlib membership query")
            spec["universe_query"] = QuerySpec(**universe)
        data = Data(_root(args))
        manifest = data.export_qlib(snapshot=data.resolve(args.snapshot),
                    queries=[QuerySpec(**q) for q in values], destination=args.destination, **spec)
        return {"view": str(args.destination), "view_id": manifest["view_id"],
                "snapshot_id": manifest["snapshot_id"], "fields": list(manifest["fields"]),
                "calendar_sessions": len(manifest["calendar"])}
    if command == "export":
        from .portable import export_bundle
        manifest = export_bundle(_root(args), args.destination,
                                 snapshot_id=args.snapshot, code_root=args.code_root,
                                 raw_backup_cutoff=args.raw_backup_cutoff)
        return {"bundle": str(args.destination), "bundle_id": manifest["bundle_id"],
                "snapshot_id": manifest["snapshot_id"]}
    if command == "import":
        from .portable import import_bundle
        snapshot_id = import_bundle(args.bundle, _root(args))
        return {"data_root": str(args.data_root), "snapshot_id": snapshot_id}
    if command == "verify":
        if args.plan:
            from .storage import LocalStore
            envelope, job = _load_plan(args.plan)
            store = LocalStore(_root(args))
            state = store.read_operation(envelope["operation_id"])
            if state is not None and state.get("fingerprint") != _runner_fingerprint(envelope, job):
                raise ValueError("checkpoint belongs to a different plan")
            if envelope["schema_version"] == _ETF_SCHEMA:
                from .etf_jobs import verify_etf_job
                return verify_etf_job(store, plan=job, operation_id=envelope["operation_id"])
            if envelope["schema_version"] == _FULL_SCHEMA:
                from .full_sources import verify_full_sources
                return verify_full_sources(store, plan=job, operation_id=envelope["operation_id"])
            from .bulk_jobs import verify_bulk_job
            return verify_bulk_job(store, plan=job, operation_id=envelope["operation_id"])
        from .portable import verify_bundle
        manifest = verify_bundle(args.bundle)
        return {"bundle": str(args.bundle), "bundle_id": manifest["bundle_id"],
                "snapshot_id": manifest["snapshot_id"], "status": "verified"}
    raise ValueError("unsupported command")


def main(argv: list[str] | None = None, *, client: Any = None) -> int:
    """Run one command; unexpected source failures print only the error type."""
    args = _parser().parse_args(argv)
    try:
        result = _execute(args, client=client)
        print(_json(result))
        if args.command == "audit" and result.get("status") == "failed":
            return 1
    except _CredentialRequired:
        print(_json({"status": "error", "error_type": "CredentialRequired",
                     "error": "run requires --token-file, TUSHARE_TOKEN or TS_TOKEN for source calls"}),
              file=sys.stderr)
        return 2
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        print(_json({"status": "error", "error_type": type(exc).__name__, "error": str(exc)}),
              file=sys.stderr)
        return 2
    except Exception as exc:
        from .protocols import DataError
        error = {"status": "error", "error_type": type(exc).__name__}
        if isinstance(exc, DataError):
            error["error"] = str(exc)
        print(_json(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
