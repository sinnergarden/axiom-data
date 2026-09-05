#!/usr/bin/env python3
"""Collect and verify the fixed Phase 1 PR3 real-market slice."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from axiom_data import (
    BuildApplication,
    SnapshotReader,
    TushareCollector,
    TushareMarketBuilder,
    build_qlib_view,
    compare_direct_and_qlib,
    create_snapshot,
    independent_tushare_market_expectations,
    load_frozen_qsys_market,
    load_qlib_view,
    load_raw_batch,
    load_snapshot,
    load_tushare_source_profile,
    rebuild_catalog,
    reconcile_market,
    tushare_source_profile_digest,
    validate_domain_commit_closure,
)
from axiom_data.consumption import validate_pr3_report_refs


SYMBOLS = (
    "600000.SH",
    "600036.SH",
    "600519.SH",
    "600900.SH",
    "601318.SH",
    "601398.SH",
    "601857.SH",
    "603501.SH",
    "688981.SH",
    "603072.SH",
    "000001.SZ",
    "000002.SZ",
    "000333.SZ",
    "000651.SZ",
    "000858.SZ",
    "002415.SZ",
    "002594.SZ",
    "300059.SZ",
    "300750.SZ",
    "301581.SZ",
)
START = "2025-01-01"
END = "2025-12-31"
RECON_SYMBOLS = ("600000.SH", "600036.SH", "000001.SZ", "000333.SZ")
RECON_START = "2025-06-10"
RECON_END = "2025-06-13"
SUPERSEDED_FORENSIC_ROOT = (
    "/var/lib/axiom-data/forensic/pr3-market-slice-20260905-blocker-fix-v2"
)


def _tushare_date(value: str) -> str:
    return value.replace("-", "")


def collect(root: Path) -> dict[str, list[str]]:
    collector = TushareCollector(root)
    start = _tushare_date(START)
    end = _tushare_date(END)
    raw_ids: dict[str, list[str]] = {"calendar": [], "security": [], "market": []}
    for exchange in ("SSE", "SZSE"):
        ref = collector.collect(
            "trade_cal",
            {"exchange": exchange, "start_date": start, "end_date": end},
        )
        raw_ids["calendar"].append(ref.raw_batch_id)
    raw_ids["security"].append(
        collector.collect(
            "stock_basic", {"exchange": "", "list_status": "L"}
        ).raw_batch_id
    )
    codes = ",".join(SYMBOLS)
    for endpoint in ("daily", "adj_factor"):
        raw_ids["market"].append(
            collector.collect(
                endpoint,
                {"ts_code": codes, "start_date": start, "end_date": end},
            ).raw_batch_id
        )
    for endpoint in ("daily_basic", "stk_limit"):
        for symbol in SYMBOLS:
            raw_ids["market"].append(
                collector.collect(
                    endpoint,
                    {"ts_code": symbol, "start_date": start, "end_date": end},
                ).raw_batch_id
            )
    raw_ids["market"].append(
        collector.collect(
            "suspend_d", {"ts_code": "688981.SH", "suspend_date": "20250901"}
        ).raw_batch_id
    )
    return raw_ids


def build(root: Path, raw_ids: dict[str, list[str]]) -> dict[str, str]:
    config = {
        "source_profile_version": "tushare_phase1.v1",
        "symbols": list(SYMBOLS),
        "start_session": START,
        "end_session": END,
        "pit_classification": "current-observed-best-effort",
    }
    calendar = BuildApplication(
        "trading_calendar",
        TushareMarketBuilder(root, "trading_calendar", builder_config=config),
    ).build(None, raw_ids["calendar"], [], "trading_calendar.v1")
    security = BuildApplication(
        "security_master",
        TushareMarketBuilder(root, "security_master", builder_config=config),
    ).build(None, raw_ids["security"], [], "security_master.v1")
    market = BuildApplication(
        "market_daily",
        TushareMarketBuilder(
            root,
            "market_daily",
            builder_config=config,
            calendar_commit_id=calendar.commit_id,
            security_master_commit_id=security.commit_id,
        ),
    ).build(None, raw_ids["market"], [], "market_daily.v1")
    snapshot = create_snapshot(
        root,
        {
            "trading_calendar": calendar.commit_id,
            "security_master": security.commit_id,
            "market_daily": market.commit_id,
        },
    )
    view = build_qlib_view(
        root,
        snapshot.snapshot_id,
        symbols=SYMBOLS,
        start_session=START,
        end_session=END,
    )
    return {
        "trading_calendar": calendar.commit_id,
        "security_master": security.commit_id,
        "market_daily": market.commit_id,
        "snapshot": snapshot.snapshot_id,
        "qlib_view": view.view_id,
    }


def _raw_evidence(root: Path, raw_ids: dict[str, list[str]]) -> list[dict[str, Any]]:
    evidence = []
    for role, identities in raw_ids.items():
        for identity in identities:
            raw = load_raw_batch(root, identity)
            evidence.append(
                {
                    "role": role,
                    "raw_batch_id": identity,
                    "schema_version": raw.manifest["schema_version"],
                    "manifest_digest": raw.ref.manifest_digest,
                    "payload_digest": raw.manifest["payload_files"][0]["content_digest"],
                    "source_profile_ref": raw.manifest["source_profile_ref"],
                    "source_profile_version": raw.manifest["source_profile_version"],
                    "source_profile_digest": raw.manifest["source_profile_digest"],
                    "endpoint": raw.manifest["request"]["endpoint"],
                    "request_params": raw.manifest["request"]["params"],
                    "retrieved_at": raw.manifest["retrieved_at"],
                    "rows": raw.manifest["summary"]["rows"],
                }
            )
    return evidence


def _root_artifact_map(artifacts: dict[str, str]) -> dict[str, Any]:
    return {
        "domain_commits": {
            domain: artifacts[domain]
            for domain in ("trading_calendar", "security_master", "market_daily")
        },
        "snapshot": artifacts["snapshot"],
        "qlib_view": artifacts["qlib_view"],
    }


def _artifact_refs(
    root: Path,
    raw_ids: dict[str, list[str]],
    artifacts: dict[str, str],
) -> dict[str, Any]:
    raw_refs = []
    for role, identities in raw_ids.items():
        for identity in identities:
            raw = load_raw_batch(root, identity)
            raw_refs.append(
                {
                    "role": role,
                    "raw_batch_id": identity,
                    "manifest_digest": raw.ref.manifest_digest,
                    "payload_digest": raw.manifest["payload_files"][0][
                        "content_digest"
                    ],
                }
            )
    commits = {}
    for domain in ("trading_calendar", "security_master", "market_daily"):
        commit = validate_domain_commit_closure(root, domain, artifacts[domain])
        commits[domain] = {
            "domain_commit_id": commit.ref.commit_id,
            "manifest_digest": commit.manifest_digest,
            "identity_digest": commit.manifest["identity_digest"],
            "logical_content_digest": commit.manifest["logical_content_digest"],
            "contract_digest": commit.manifest["contract_digest"],
        }
    snapshot = load_snapshot(root, artifacts["snapshot"])
    view = load_qlib_view(root, artifacts["qlib_view"])
    raw_content = json.dumps(
        raw_refs,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return {
        "snapshot": {
            "snapshot_id": snapshot.ref.snapshot_id,
            "manifest_digest": snapshot.ref.manifest_digest,
            "identity_digest": snapshot.manifest["identity_digest"],
        },
        "domain_commits": commits,
        "raw_batches": {
            "ordered_raw_batch_ids": [
                ref["raw_batch_id"] for ref in raw_refs
            ],
            "ordered_refs_digest": (
                f"sha256:{hashlib.sha256(raw_content).hexdigest()}"
            ),
        },
        "qlib_view": {
            "view_id": view.ref.view_id,
            "manifest_digest": view.ref.manifest_digest,
            "identity_digest": view.manifest["identity_digest"],
        },
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return f"sha256:{digest.hexdigest()}"


def _real_cases(reader: SnapshotReader) -> dict[str, Any]:
    securities = {row["symbol"]: row for row in reader.security_master()}
    rows = reader.market_daily(SYMBOLS, START, END)
    suspended = [
        [row["session"], row["symbol"]] for row in rows if row["is_suspended"]
    ]
    first_market = {}
    prior_close: dict[str, float] = {}
    corporate_action_examples = []
    for row in rows:
        symbol = row["symbol"]
        first_market.setdefault(symbol, row["session"])
        if (
            row["close"] is not None
            and symbol in prior_close
            and row["pre_close"] != prior_close[symbol]
            and len(corporate_action_examples) < 12
        ):
            corporate_action_examples.append(
                {
                    "session": row["session"],
                    "symbol": symbol,
                    "prior_row_close": prior_close[symbol],
                    "supplier_pre_close": row["pre_close"],
                }
            )
        if row["close"] is not None:
            prior_close[symbol] = row["close"]
    listing = {
        symbol: {
            "list_session": securities[symbol]["list_session"],
            "first_market_session": first_market[symbol],
            "prior_scope_session_state": "not_yet_listed",
        }
        for symbol in ("603072.SH", "301581.SZ")
    }
    return {
        "confirmed_suspensions": suspended,
        "listing_boundaries": listing,
        "pre_close_not_prior_close_examples": corporate_action_examples,
    }


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--offline-root", type=Path, required=True)
    parser.add_argument("--qsys-parquet", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.data_root.exists() or args.offline_root.exists():
        raise SystemExit("data roots must not exist; this run requires empty roots")

    raw_ids = collect(args.data_root)
    artifacts = build(args.data_root, raw_ids)
    source_catalog_entries = rebuild_catalog(args.data_root)
    raw_evidence = _raw_evidence(args.data_root, raw_ids)
    if any(
        item["schema_version"] != "raw_batch.v2" for item in raw_evidence
    ):
        raise SystemExit("PR3 real closure contains a non-v2 RawBatch")
    reader = SnapshotReader(args.data_root, artifacts["snapshot"])
    equivalence = compare_direct_and_qlib(
        args.data_root, artifacts["snapshot"], artifacts["qlib_view"]
    )
    source_rows = independent_tushare_market_expectations(
        args.data_root,
        raw_ids["market"],
        symbols=RECON_SYMBOLS,
        start_session=RECON_START,
        end_session=RECON_END,
    )
    qsys_rows = load_frozen_qsys_market(
        args.qsys_parquet,
        symbols=RECON_SYMBOLS,
        start_session=RECON_START,
        end_session=RECON_END,
    )
    reconciliation = reconcile_market(
        args.data_root,
        artifacts["snapshot"],
        source_rows=source_rows,
        qsys_rows=qsys_rows,
        symbols=RECON_SYMBOLS,
        start_session=RECON_START,
        end_session=RECON_END,
    )

    shutil.copytree(args.data_root / "raw/batches", args.offline_root / "raw/batches")
    before = {
        "canonical_exists": (args.offline_root / "canonical").exists(),
        "snapshots_exists": (args.offline_root / "snapshots").exists(),
        "qlib_exists": (args.offline_root / "exports/qlib").exists(),
        "catalog_exists": (args.offline_root / "catalog.sqlite").exists(),
    }
    offline_artifacts = build(args.offline_root, raw_ids)
    catalog_entries = rebuild_catalog(args.offline_root)
    offline_reader = SnapshotReader(args.offline_root, offline_artifacts["snapshot"])
    offline_equivalence = compare_direct_and_qlib(
        args.offline_root,
        offline_artifacts["snapshot"],
        offline_artifacts["qlib_view"],
    )
    source_refs = _artifact_refs(args.data_root, raw_ids, artifacts)
    rebuilt_refs = _artifact_refs(args.offline_root, raw_ids, offline_artifacts)
    source_root_map = _root_artifact_map(artifacts)
    rebuilt_root_map = _root_artifact_map(offline_artifacts)
    identity_equality = {
        "root_artifact_maps": source_root_map == rebuilt_root_map,
        "snapshot": source_refs["snapshot"]["snapshot_id"]
        == rebuilt_refs["snapshot"]["snapshot_id"]
        and source_refs["snapshot"]["identity_digest"]
        == rebuilt_refs["snapshot"]["identity_digest"],
        "domain_commits": all(
            source_refs["domain_commits"][domain]["domain_commit_id"]
            == rebuilt_refs["domain_commits"][domain]["domain_commit_id"]
            and source_refs["domain_commits"][domain]["identity_digest"]
            == rebuilt_refs["domain_commits"][domain]["identity_digest"]
            for domain in ("trading_calendar", "security_master", "market_daily")
        ),
        "raw_batches": source_refs["raw_batches"] == rebuilt_refs["raw_batches"],
        "qlib_view": source_refs["qlib_view"]["view_id"]
        == rebuilt_refs["qlib_view"]["view_id"]
        and source_refs["qlib_view"]["identity_digest"]
        == rebuilt_refs["qlib_view"]["identity_digest"],
    }
    canonical_rows_equal = tuple(reader.commits["market_daily"].rows) == tuple(
        offline_reader.commits["market_daily"].rows
    )
    offline = {
        "status": "PASS"
        if all(identity_equality.values())
        and canonical_rows_equal
        and offline_equivalence["status"] == "PASS"
        else "FAIL",
        "starting_state": before,
        "copied_input": "published raw/batches closure only",
        "network_calls": 0,
        "source_root_artifact_map": source_root_map,
        "rebuilt_root_artifact_map": rebuilt_root_map,
        "source_artifact_refs": source_refs,
        "rebuilt_artifact_refs": rebuilt_refs,
        "identity_equality": identity_equality,
        "logical_equality": {"canonical_rows": canonical_rows_equal},
        "catalog_rebuild": {"status": "PASS", "entries": catalog_entries},
        "direct_qlib_equivalence": offline_equivalence,
    }

    commit_evidence = {}
    for domain in ("trading_calendar", "security_master", "market_daily"):
        commit = validate_domain_commit_closure(
            args.data_root, domain, artifacts[domain]
        )
        commit_evidence[domain] = {
            "domain_commit_id": commit.ref.commit_id,
            "manifest_digest": commit.manifest_digest,
            "identity_digest": commit.manifest["identity_digest"],
            "contract_version": commit.ref.contract_version,
            "contract_digest": commit.manifest["contract_digest"],
            "logical_content_digest": commit.manifest["logical_content_digest"],
            "rows": len(commit.rows),
            "ordered_raw_batch_ids": [
                ref["raw_batch_id"]
                for ref in commit.manifest["ordered_raw_batch_refs"]
            ],
            "ordered_source_profiles": [
                {
                    "raw_batch_id": ref["raw_batch_id"],
                    "source_profile_ref": ref["source_profile_ref"],
                    "source_profile_version": ref["source_profile_version"],
                    "source_profile_digest": ref["source_profile_digest"],
                }
                for ref in commit.manifest["ordered_raw_batch_refs"]
            ],
        }
    snapshot = load_snapshot(args.data_root, artifacts["snapshot"])
    qlib_view = load_qlib_view(args.data_root, artifacts["qlib_view"])
    source_profile = load_tushare_source_profile()
    source_profile_digest = tushare_source_profile_digest(source_profile)
    run_manifest = {
        "report_version": "pr3-real-market-slice.v4",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": {
            "symbols": list(SYMBOLS),
            "sse_symbols": sum(symbol.endswith(".SH") for symbol in SYMBOLS),
            "szse_symbols": sum(symbol.endswith(".SZ") for symbol in SYMBOLS),
            "start_session": START,
            "end_session": END,
        },
        "endpoints": [
            "trade_cal",
            "stock_basic",
            "daily",
            "adj_factor",
            "daily_basic",
            "stk_limit",
            "suspend_d",
        ],
        "pit": {
            "classification": "current-observed-best-effort",
            "historical_revision_availability": "unknown",
        },
        "source_profile": {
            "profile_version": source_profile["profile_version"],
            "normalized_content_digest": source_profile_digest,
        },
        "frozen_real_closure": {
            "path": str(args.data_root.resolve()),
            "snapshot_id": snapshot.ref.snapshot_id,
            "qlib_view_id": qlib_view.ref.view_id,
            "role": "immutable-validation-forensic-closure; not production current",
        },
        "superseded_forensic_closure": {
            "path": SUPERSEDED_FORENSIC_ROOT,
            "status": "superseded",
            "reason": "pre-schema-bound RawBatch envelope evidence; retained read-only",
        },
        "catalog_rebuild": {
            "status": "PASS",
            "entries": source_catalog_entries,
        },
        "raw_batches": raw_evidence,
        "domain_commits": commit_evidence,
        "snapshot": {
            "snapshot_id": snapshot.ref.snapshot_id,
            "manifest_digest": snapshot.ref.manifest_digest,
            "identity_digest": snapshot.manifest["identity_digest"],
            "domain_refs": snapshot.manifest["domain_refs"],
        },
        "qlib_view": {
            "view_id": qlib_view.ref.view_id,
            "manifest_digest": qlib_view.ref.manifest_digest,
            "identity_digest": qlib_view.manifest["identity_digest"],
            "snapshot_ref": qlib_view.manifest["snapshot_ref"],
            "fields": qlib_view.manifest["fields"],
            "scope": qlib_view.manifest["scope"],
            "exporter_ref": qlib_view.manifest["exporter_ref"],
            "instrument_storage_scope": qlib_view.manifest[
                "instrument_storage_scope"
            ],
        },
        "real_cases": _real_cases(reader),
        "qsys_reference": {
            "path": "SysQ/data/raw_panels/raw_panel_csi800_20200101_20251231.parquet",
            "bytes": args.qsys_parquet.stat().st_size,
            "content_digest": _sha256(args.qsys_parquet),
            "role": "frozen-forensic-reference-only",
        },
    }
    validate_pr3_report_refs(run_manifest, equivalence, offline)
    _write_json(args.report_dir / "run_manifest.json", run_manifest)
    _write_json(args.report_dir / "direct_qlib_equivalence.json", equivalence)
    _write_json(args.report_dir / "tushare_axiom_qsys_reconciliation.json", reconciliation)
    _write_json(args.report_dir / "offline_rebuild.json", offline)
    if not all(
        report["status"] == "PASS"
        for report in (equivalence, reconciliation, offline)
    ):
        raise SystemExit("PR3 verification failed; inspect the generated reports")
    print(
        json.dumps(
            {
                "artifacts": artifacts,
                "equivalence": equivalence["status"],
                "reconciliation": reconciliation["status"],
                "offline": offline["status"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
