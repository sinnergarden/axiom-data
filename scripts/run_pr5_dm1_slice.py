#!/usr/bin/env python3
"""Collect, publish, reconcile, and offline-rebuild the bounded PR5 D-M1 slice."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from axiom_data import (
    ArtifactError,
    BuildApplication,
    FactView,
    SnapshotReader,
    TushareCollector,
    TushareDm1Builder,
    TushareDm1Collector,
    TushareMarketBuilder,
    build_adjusted_price_view,
    build_market_replay_view,
    build_qlib_view,
    compare_direct_and_qlib,
    create_snapshot,
    independent_tushare_market_expectations,
    load_adjusted_price_view,
    load_dm1_source_profile,
    load_frozen_qsys_market,
    load_market_replay_view,
    load_qlib_view,
    load_raw_batch,
    load_snapshot,
    list_catalog,
    lookup_catalog,
    rebuild_catalog,
    reconcile_dm1_raw_mapping,
    reconcile_market,
    validate_domain_commit_closure,
    validate_pr5_evidence,
)


SYMBOLS = (
    "600000.SH",
    "600036.SH",
    "688981.SH",
    "603072.SH",
    "000001.SZ",
    "301581.SZ",
)
BENCHMARKS = ("000300.SH",)
START = "2025-06-01"
END = "2025-09-05"
ANCHOR = END
RECON_START = "2025-06-10"
RECON_END = "2025-06-13"
BASE_DOMAINS = ("trading_calendar", "security_master", "market_daily")
REFERENCE_DOMAINS = (
    "security_status",
    "price_limits",
    "corporate_actions",
    "adjustment_factors",
    "benchmark_daily",
    "security_capital",
)


def compact(value: str) -> str:
    return value.replace("-", "")


def collect(root: Path) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    base_collector = TushareCollector(root)
    dm1_collector = TushareDm1Collector(root)
    start, end = compact(START), compact(END)
    codes = ",".join(SYMBOLS)
    base: dict[str, list[str]] = {name: [] for name in BASE_DOMAINS}
    for exchange in ("SSE", "SZSE"):
        base["trading_calendar"].append(
            base_collector.collect(
                "trade_cal", {"exchange": exchange, "start_date": start, "end_date": end}
            ).raw_batch_id
        )
    base["security_master"].append(
        base_collector.collect("stock_basic", {"exchange": "", "list_status": "L"}).raw_batch_id
    )
    for endpoint in ("daily", "adj_factor"):
        base["market_daily"].append(
            base_collector.collect(endpoint, {"ts_code": codes, "start_date": start, "end_date": end}).raw_batch_id
        )
    for endpoint in ("daily_basic", "stk_limit", "suspend_d"):
        for symbol in SYMBOLS:
            base["market_daily"].append(
                base_collector.collect(
                    endpoint,
                    {"ts_code": symbol, "start_date": start, "end_date": end},
                ).raw_batch_id
            )

    dm1: dict[str, list[str]] = {name: [] for name in REFERENCE_DOMAINS}
    dm1["security_status"].append(
        dm1_collector.collect("security_status", "daily", {"ts_code": codes, "start_date": start, "end_date": end}).raw_batch_id
    )
    for symbol in SYMBOLS:
        dm1["security_status"].append(
            dm1_collector.collect("security_status", "suspend_d", {"ts_code": symbol, "start_date": start, "end_date": end}).raw_batch_id
        )
        dm1["price_limits"].append(
            dm1_collector.collect("price_limits", "stk_limit", {"ts_code": symbol, "start_date": start, "end_date": end}).raw_batch_id
        )
        dm1["corporate_actions"].append(
            dm1_collector.collect("corporate_actions", "dividend", {"ts_code": symbol}).raw_batch_id
        )
        dm1["security_capital"].append(
            dm1_collector.collect("security_capital", "daily_basic", {"ts_code": symbol, "start_date": start, "end_date": end}).raw_batch_id
        )
    dm1["adjustment_factors"].append(
        dm1_collector.collect("adjustment_factors", "adj_factor", {"ts_code": codes, "start_date": start, "end_date": end}).raw_batch_id
    )
    dm1["benchmark_daily"].append(
        dm1_collector.collect("benchmark_daily", "index_daily", {"ts_code": BENCHMARKS[0], "start_date": start, "end_date": end}).raw_batch_id
    )
    return base, dm1


def build(
    root: Path,
    base_raw: dict[str, list[str]],
    dm1_raw: dict[str, list[str]],
) -> dict[str, str]:
    base_config = {
        "source_profile_version": "tushare_phase1.v1",
        "symbols": list(SYMBOLS),
        "start_session": START,
        "end_session": END,
        "pit_classification": "current-observed-best-effort",
    }
    calendar = BuildApplication(
        "trading_calendar",
        TushareMarketBuilder(root, "trading_calendar", builder_config=base_config),
    ).build(None, base_raw["trading_calendar"], [], "trading_calendar.v1")
    security = BuildApplication(
        "security_master",
        TushareMarketBuilder(root, "security_master", builder_config=base_config),
    ).build(None, base_raw["security_master"], [], "security_master.v1")
    market = BuildApplication(
        "market_daily",
        TushareMarketBuilder(
            root,
            "market_daily",
            builder_config=base_config,
            calendar_commit_id=calendar.commit_id,
            security_master_commit_id=security.commit_id,
        ),
    ).build(None, base_raw["market_daily"], [], "market_daily.v1")
    commits = {
        "trading_calendar": calendar.commit_id,
        "security_master": security.commit_id,
        "market_daily": market.commit_id,
    }
    dependencies = {
        "security_status": {"trading_calendar": calendar.commit_id, "security_master": security.commit_id, "market_daily": market.commit_id},
        "price_limits": {"trading_calendar": calendar.commit_id, "security_master": security.commit_id},
        "corporate_actions": {"trading_calendar": calendar.commit_id, "security_master": security.commit_id},
        "adjustment_factors": {"trading_calendar": calendar.commit_id, "security_master": security.commit_id, "market_daily": market.commit_id},
        "benchmark_daily": {"trading_calendar": calendar.commit_id},
        "security_capital": {"trading_calendar": calendar.commit_id, "security_master": security.commit_id},
    }
    for domain in REFERENCE_DOMAINS:
        config = {
            "symbols": list(BENCHMARKS if domain == "benchmark_daily" else SYMBOLS),
            "start_session": START,
            "end_session": END,
            "pit_qualification": "best_effort",
        }
        ref = BuildApplication(
            domain,
            TushareDm1Builder(
                root,
                domain,
                dependency_commit_ids=dependencies[domain],
                builder_config=config,
            ),
        ).build(None, dm1_raw[domain], [], f"{domain}.v1")
        commits[domain] = ref.commit_id
    snapshot = create_snapshot(root, commits)
    adjusted = build_adjusted_price_view(
        root,
        snapshot.snapshot_id,
        symbols=SYMBOLS,
        start_session=START,
        end_session=END,
        anchor_session=ANCHOR,
        pit_policy="research_non_pit",
        decision_cutoff=END,
    )
    replay = build_market_replay_view(
        root,
        snapshot.snapshot_id,
        symbols=SYMBOLS,
        start_session=START,
        end_session=END,
    )
    qlib = build_qlib_view(
        root,
        snapshot.snapshot_id,
        symbols=SYMBOLS,
        start_session=START,
        end_session=END,
        adjusted_price_view_id=adjusted.view_id,
        price_basis="anchor_adjusted",
        pit_policy="research_non_pit",
        decision_cutoff=END,
    )
    return {
        **commits,
        "snapshot": snapshot.snapshot_id,
        "adjusted_price_view": adjusted.view_id,
        "market_replay_view": replay.view_id,
        "qlib_view": qlib.view_id,
    }


def artifact_refs(root: Path, artifacts: dict[str, str]) -> dict[str, Any]:
    commits = {}
    raw_batches = []
    for domain in (*BASE_DOMAINS, *REFERENCE_DOMAINS):
        commit = validate_domain_commit_closure(root, domain, artifacts[domain])
        commits[domain] = {
            "domain_commit_id": commit.ref.commit_id,
            "manifest_digest": commit.manifest_digest,
            "identity_digest": commit.manifest["identity_digest"],
            "contract_digest": commit.manifest["contract_digest"],
            "logical_content_digest": commit.manifest["logical_content_digest"],
            "rows": len(commit.rows),
        }
        raw_batches.extend(
            {"domain": domain, **raw_ref}
            for raw_ref in commit.manifest["ordered_raw_batch_refs"]
        )
    snapshot = load_snapshot(root, artifacts["snapshot"])
    adjusted = load_adjusted_price_view(root, artifacts["adjusted_price_view"])
    replay = load_market_replay_view(root, artifacts["market_replay_view"])
    qlib = load_qlib_view(root, artifacts["qlib_view"])
    qlib_content = [
        [entry["path"], entry["content_digest"]]
        for entry in qlib.manifest["output_files"]
    ]
    return {
        "raw_batches": raw_batches,
        "domain_commits": commits,
        "snapshot": {"snapshot_id": snapshot.ref.snapshot_id, "manifest_digest": snapshot.ref.manifest_digest, "identity_digest": snapshot.manifest["identity_digest"]},
        "adjusted_price_view": {
            "view_id": adjusted.ref.view_id,
            "manifest_digest": adjusted.ref.manifest_digest,
            "identity_digest": adjusted.manifest["identity_digest"],
            "content_digest": adjusted.manifest["output"]["content_digest"],
            "anchor_session": adjusted.manifest["anchor_session"],
            "pit_policy": adjusted.manifest["pit_policy"],
            "pit_qualification": adjusted.manifest["pit_qualification"],
            "decision_cutoff": adjusted.manifest["decision_cutoff"],
        },
        "market_replay_view": {
            "view_id": replay.ref.view_id,
            "manifest_digest": replay.ref.manifest_digest,
            "identity_digest": replay.manifest["identity_digest"],
            "content_digest": replay.manifest["output"]["content_digest"],
            "temporal_policy": replay.manifest["temporal_policy"],
        },
        "qlib_view": {
            "view_id": qlib.ref.view_id,
            "manifest_digest": qlib.ref.manifest_digest,
            "identity_digest": qlib.manifest["identity_digest"],
            "content_digest": f"sha256:{hashlib.sha256(json.dumps(qlib_content, sort_keys=True, separators=(',', ':')).encode()).hexdigest()}",
            "anchor_session": qlib.manifest["anchor_session"],
            "pit_policy": qlib.manifest["pit_policy"],
            "pit_qualification": qlib.manifest["pit_qualification"],
            "decision_cutoff": qlib.manifest["decision_cutoff"],
            "derived_view_id": qlib.manifest["derived_refs"][0]["view_id"],
        },
    }


def raw_refs(root: Path, groups: dict[str, list[str]]) -> list[dict[str, Any]]:
    result = []
    for domain, identities in groups.items():
        for identity in identities:
            raw = load_raw_batch(root, identity)
            result.append({
                "domain": domain,
                "raw_batch_id": identity,
                "schema_version": raw.manifest["schema_version"],
                "manifest_digest": raw.ref.manifest_digest,
                "payload_digest": raw.manifest["payload_files"][0]["content_digest"],
                "source_profile_ref": raw.manifest["source_profile_ref"],
                "source_profile_version": raw.manifest["source_profile_version"],
                "source_profile_digest": raw.manifest["source_profile_digest"],
                "request": raw.manifest["request"],
                "retrieved_at": raw.manifest["retrieved_at"],
                "rows": raw.manifest["summary"]["rows"],
            })
    return result


def real_cases(root: Path, artifacts: dict[str, str]) -> dict[str, Any]:
    reader = SnapshotReader(root, artifacts["snapshot"])
    market = reader.market_daily(SYMBOLS, START, END)
    status = reader.facts("security_status", symbols=SYMBOLS, start_session=START, end_session=END)
    limits = reader.facts("price_limits", symbols=SYMBOLS, start_session=START, end_session=END)
    actions = reader.facts("corporate_actions", symbols=SYMBOLS, start_session=START, end_session=END)
    factors = reader.facts("adjustment_factors", symbols=SYMBOLS, start_session=START, end_session=END)
    by_symbol: dict[str, list[dict[str, Any]]] = {}
    for row in factors:
        by_symbol.setdefault(row["symbol"], []).append(row)
    changes = [
        {"symbol": symbol, "first": values[0]["factor"], "last": values[-1]["factor"]}
        for symbol, values in by_symbol.items()
        if values[0]["factor"] != values[-1]["factor"]
    ]
    prior: dict[str, float] = {}
    preclose = []
    for row in market:
        symbol = row["symbol"]
        if row["close"] is not None and symbol in prior and row["pre_close"] != prior[symbol]:
            preclose.append({"session": row["session"], "symbol": symbol, "prior_row_close": prior[symbol], "pre_close": row["pre_close"]})
        if row["close"] is not None:
            prior[symbol] = row["close"]
    return {
        "confirmed_suspensions": [[row["session"], row["symbol"]] for row in status if row["status"] == "suspended"],
        "unknown_source_gaps": [[row["session"], row["symbol"]] for row in status if row["status"] == "unknown_source_gap"][:50],
        "limited_rows": sum(row["limit_state"] == "limited" for row in limits),
        "corporate_actions": actions,
        "factor_changes": changes,
        "pre_close_not_prior_row_close": preclose[:20],
    }


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return f"sha256:{digest.hexdigest()}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--offline-root", type=Path, required=True)
    parser.add_argument("--qsys-parquet", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.data_root.exists() or args.offline_root.exists():
        raise SystemExit("data roots must not exist")
    base_raw, dm1_raw = collect(args.data_root)
    artifacts = build(args.data_root, base_raw, dm1_raw)
    source_catalog = rebuild_catalog(args.data_root)
    source_catalog_types = Counter(entry.artifact_type for entry in list_catalog(args.data_root))
    refs = artifact_refs(args.data_root, artifacts)
    raw = raw_refs(args.data_root, {**base_raw, **dm1_raw})
    if any(item["schema_version"] != "raw_batch.v2" for item in raw):
        raise SystemExit("PR5 real closure contains non-v2 RawBatch")

    direct_qlib = compare_direct_and_qlib(args.data_root, artifacts["snapshot"], artifacts["qlib_view"])
    dm1_recon = reconcile_dm1_raw_mapping(str(args.data_root), artifacts["snapshot"], dm1_raw, symbols=SYMBOLS, start_session=START, end_session=END)
    source_market = independent_tushare_market_expectations(args.data_root, base_raw["market_daily"], symbols=SYMBOLS, start_session=RECON_START, end_session=RECON_END)
    qsys_market = load_frozen_qsys_market(args.qsys_parquet, symbols=SYMBOLS, start_session=RECON_START, end_session=RECON_END)
    qsys_recon = reconcile_market(args.data_root, artifacts["snapshot"], source_rows=source_market, qsys_rows=qsys_market, symbols=SYMBOLS, start_session=RECON_START, end_session=RECON_END)
    qsys_recon["snapshot_ref"] = refs["snapshot"]
    qsys_recon["raw_batch_refs"] = [
        ref for ref in refs["raw_batches"] if ref["domain"] == "market_daily"
    ]

    fact = FactView(args.data_root, artifacts["snapshot"], adjusted_price_view_id=artifacts["adjusted_price_view"]).read("adjusted_price", symbols=SYMBOLS, start_session=START, end_session=END, price_basis="anchor_adjusted", pit_policy="research_non_pit", cutoff_policy=f"decision_cutoff={END}")
    replay = load_market_replay_view(args.data_root, artifacts["market_replay_view"])
    strict_blocked = False
    try:
        build_adjusted_price_view(
            args.data_root,
            artifacts["snapshot"],
            symbols=SYMBOLS,
            start_session=START,
            end_session=END,
            anchor_session=ANCHOR,
            pit_policy="strict_decision_time",
            decision_cutoff=END,
        )
    except ArtifactError:
        strict_blocked = True

    shutil.copytree(args.data_root / "raw/batches", args.offline_root / "raw/batches")
    offline_start = {name: (args.offline_root / name).exists() for name in ("canonical", "derived", "snapshots", "exports", "catalog.sqlite")}
    offline_artifacts = build(args.offline_root, base_raw, dm1_raw)
    offline_catalog = rebuild_catalog(args.offline_root)
    offline_catalog_types = Counter(entry.artifact_type for entry in list_catalog(args.offline_root))
    offline_view_lookups = {
        artifact_type: lookup_catalog(args.offline_root, artifact_type, offline_artifacts[key]).artifact_id
        == offline_artifacts[key]
        for artifact_type, key in (
            ("adjusted_price_view", "adjusted_price_view"),
            ("market_replay_view", "market_replay_view"),
            ("qlib_view", "qlib_view"),
        )
    }
    offline_catalog_ok = (
        sum(offline_catalog_types.values()) == offline_catalog
        and all(offline_view_lookups.values())
        and all(offline_catalog_types[name] == 1 for name in offline_view_lookups)
    )
    offline_refs = artifact_refs(args.offline_root, offline_artifacts)
    offline_direct = compare_direct_and_qlib(args.offline_root, offline_artifacts["snapshot"], offline_artifacts["qlib_view"])
    identity_equal = artifacts == offline_artifacts and all(
        refs["domain_commits"][domain]["identity_digest"]
        == offline_refs["domain_commits"][domain]["identity_digest"]
        for domain in (*BASE_DOMAINS, *REFERENCE_DOMAINS)
    ) and all(
        refs[name]["identity_digest"] == offline_refs[name]["identity_digest"]
        for name in ("snapshot", "adjusted_price_view", "market_replay_view", "qlib_view")
    )
    logical_equal = (
        tuple(SnapshotReader(args.data_root, artifacts["snapshot"]).commits[domain].rows)
        == tuple(SnapshotReader(args.offline_root, offline_artifacts["snapshot"]).commits[domain].rows)
        for domain in (*BASE_DOMAINS, *REFERENCE_DOMAINS)
    )
    logical_checks = list(logical_equal)
    recovery = {
        "status": "PASS"
        if identity_equal
        and all(logical_checks)
        and offline_direct["status"] == "PASS"
        and offline_catalog_ok
        else "FAIL",
        "network_calls": 0,
        "starting_state": offline_start,
        "source_artifacts": artifacts,
        "rebuilt_artifacts": offline_artifacts,
        "source_refs": refs,
        "rebuilt_refs": offline_refs,
        "identity_equality": identity_equal,
        "domain_logical_equality": dict(zip((*BASE_DOMAINS, *REFERENCE_DOMAINS), logical_checks)),
        "catalog_rebuild": {
            "status": "PASS" if offline_catalog_ok else "FAIL",
            "entries": offline_catalog,
            "artifact_type_counts": dict(sorted(offline_catalog_types.items())),
            "exact_view_lookups": offline_view_lookups,
        },
        "direct_qlib_equivalence": offline_direct,
        "anchor_semantics_equal": refs["adjusted_price_view"]["anchor_session"] == offline_refs["adjusted_price_view"]["anchor_session"] == ANCHOR,
        "pit_binding_equal": all(
            refs["adjusted_price_view"][name]
            == offline_refs["adjusted_price_view"][name]
            for name in ("pit_policy", "pit_qualification", "decision_cutoff")
        ),
    }

    cases = real_cases(args.data_root, artifacts)
    reference_rows = [
        row
        for domain in REFERENCE_DOMAINS
        for row in SnapshotReader(args.data_root, artifacts["snapshot"]).commits[domain].rows
    ]
    view_lookups = {
        "adjusted_price_view": lookup_catalog(
            args.data_root, "adjusted_price_view", artifacts["adjusted_price_view"]
        ).artifact_id == artifacts["adjusted_price_view"],
        "market_replay_view": lookup_catalog(
            args.data_root, "market_replay_view", artifacts["market_replay_view"]
        ).artifact_id == artifacts["market_replay_view"],
        "qlib_view": lookup_catalog(
            args.data_root, "qlib_view", artifacts["qlib_view"]
        ).artifact_id == artifacts["qlib_view"],
    }

    def gate(checks: dict[str, bool], required_refs: list[str]) -> dict[str, Any]:
        failures = [name for name, result in checks.items() if result is not True]
        return {
            "status": "PASS" if not failures else "FAIL",
            "checks": checks,
            "required_refs": required_refs,
            "mismatch_count": len(failures),
            "mismatches": failures,
        }

    acceptance = {
        "D01": gate({"immutable_snapshot_coexists": load_snapshot(args.data_root, artifacts["snapshot"]).manifest["schema_version"] == "data_snapshot.v2"}, ["snapshot"]),
        "D02": gate({"identity_equality": identity_equal, "logical_equality": all(logical_checks), "pit_binding_equal": recovery["pit_binding_equal"]}, ["raw_batches", "domain_commits", "snapshot", "adjusted_price_view", "market_replay_view", "qlib_view"]),
        "D03": gate({"all_raw_v2": all(item["schema_version"] == "raw_batch.v2" for item in raw), "raw_ids_unique": len(raw) == len({item["raw_batch_id"] for item in raw})}, ["raw_batches"]),
        "D04": gate(dict(dm1_recon["checks"]), ["raw_batches", "domain_commits", "snapshot"]),
        "D05": gate({"all_contract_refs_present": all(ref["contract_digest"].startswith("sha256:") for ref in refs["domain_commits"].values())}, ["domain_commits"]),
        "D10": gate({"snapshot_cross_domain_validation": load_snapshot(args.data_root, artifacts["snapshot"]).manifest["validation_summary"]["cross_domain"] == "PASS"}, ["domain_commits", "snapshot"]),
        "D11": gate({"offline_recovery": recovery["status"] == "PASS", "catalog_views_exact": all(view_lookups.values())}, ["raw_batches", "domain_commits", "snapshot", "adjusted_price_view", "market_replay_view", "qlib_view"]),
        "D12": gate({"catalog_loader_validation": sum(source_catalog_types.values()) == source_catalog, "all_formal_views_indexed": all(view_lookups.values())}, ["snapshot", "adjusted_price_view", "market_replay_view", "qlib_view"]),
        "D13": gate({"terminal_history_not_verified": all(row["pit_qualification"] != "verified" for row in reference_rows), "adjusted_is_best_effort": refs["adjusted_price_view"]["pit_qualification"] == "best_effort", "false_strict_blocked": strict_blocked}, ["raw_batches", "domain_commits", "snapshot", "adjusted_price_view"]),
        "D14": gate(dict(direct_qlib["checks"]), ["snapshot", "adjusted_price_view", "qlib_view"]),
        "D15": gate({"real_suspension_present": bool(cases["confirmed_suspensions"]), "replay_is_post_session": replay.manifest["temporal_policy"]["mode"] == "post_session_replay"}, ["domain_commits", "snapshot", "market_replay_view"]),
    }
    status = "PASS" if all(item["status"] == "PASS" for item in acceptance.values()) and dm1_recon["status"] == qsys_recon["status"] == direct_qlib["status"] == recovery["status"] == "PASS" else "FAIL"
    repository = Path(__file__).resolve().parents[1]
    code_files = (
        "src/axiom_data/artifacts.py",
        "src/axiom_data/dm1_source.py",
        "src/axiom_data/domains/dm1.py",
        "src/axiom_data/views.py",
        "src/axiom_data/consumption.py",
        "src/axiom_data/evidence.py",
        "src/axiom_data/dm1_reconciliation.py",
        "src/axiom_data/contracts/corporate_actions.v1.json",
        "src/axiom_data/contracts/adjusted_price.v1.json",
        "scripts/run_pr5_dm1_slice.py",
    )
    scope_manifest = repository / "src/axiom_data/scope/pr5_d_m1_scope.v1.json"
    run_manifest = {
        "schema_version": "axiom_data.pr5_dm1_run.v1",
        "status": status,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "code_branch": "phase1/pr5-market-reference-completion",
        "code_content_digests": {
            name: file_digest(repository / name) for name in code_files
        },
        "scope_manifest": {
            "path": "src/axiom_data/scope/pr5_d_m1_scope.v1.json",
            "content_digest": file_digest(scope_manifest),
        },
        "forensic_closure": {
            "path": str(args.data_root.resolve()),
            "role": "immutable validation evidence; not production current",
        },
        "qsys_reference": {
            "path": str(args.qsys_parquet.resolve()),
            "content_digest": file_digest(args.qsys_parquet),
            "bytes": args.qsys_parquet.stat().st_size,
            "role": "frozen reconciliation evidence only; not truth authority",
        },
        "scope": {"symbols": list(SYMBOLS), "benchmarks": list(BENCHMARKS), "start_session": START, "end_session": END},
        "source_profile": {"version": load_dm1_source_profile()["profile_version"], "digest": dm1_recon["source_profile_digest"]},
        "raw_batches": raw,
        "artifacts": artifacts,
        "artifact_refs": refs,
        "catalog_rebuild": {"status": "PASS", "entries": source_catalog, "artifact_type_counts": dict(sorted(source_catalog_types.items())), "exact_view_lookups": view_lookups},
        "fact_view": {"snapshot_ref": fact["snapshot_ref"], "derived_refs": fact["derived_refs"], "price_basis": fact["price_basis"], "anchor": fact["anchor"], "pit_policy": fact["pit_policy"], "pit_qualification": fact["pit_qualification"], "cutoff_policy": fact["cutoff_policy"], "rows": len(fact["rows"])},
        "market_replay_view": {"view_id": replay.ref.view_id, "rows": len(replay.rows), "temporal_policy": replay.manifest["temporal_policy"], "unsupported_events": replay.manifest["unsupported_events"]},
        "real_cases": cases,
        "reports": ["direct_qlib_equivalence.json", "dm1_raw_mapping_reconciliation.json", "qsys_reconciliation.json", "offline_recovery.json", "dm1_acceptance_matrix.json"],
    }
    acceptance_report = {"status": "PASS" if all(item["status"] == "PASS" for item in acceptance.values()) else "FAIL", "artifact_refs": refs, "gates": acceptance}
    validate_pr5_evidence(
        args.data_root,
        run_manifest,
        direct_qlib,
        dm1_recon,
        qsys_recon,
        recovery,
        acceptance_report,
    )
    for name, report in {
        "run_manifest.json": run_manifest,
        "direct_qlib_equivalence.json": direct_qlib,
        "dm1_raw_mapping_reconciliation.json": dm1_recon,
        "qsys_reconciliation.json": qsys_recon,
        "offline_recovery.json": recovery,
        "dm1_acceptance_matrix.json": acceptance_report,
    }.items():
        write_json(args.report_dir / name, report)
    if status != "PASS":
        raise SystemExit("PR5 D-M1 verification failed; inspect generated reports")
    print(json.dumps({"status": status, "artifacts": artifacts, "raw_batches": len(raw)}, sort_keys=True))


if __name__ == "__main__":
    main()
