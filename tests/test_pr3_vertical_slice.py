from __future__ import annotations

import hashlib
import json
import shutil
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from axiom_data import (
    MARKET_VIEW_FIELDS,
    RECONCILIATION_CATEGORIES,
    BuildApplication,
    QlibViewReader,
    SnapshotReader,
    TushareCollector,
    TushareMarketBuilder,
    build_qlib_view,
    canonical_market_observations,
    compare_direct_and_qlib,
    create_snapshot,
    load_raw_batch,
    load_snapshot,
    load_tushare_source_profile,
    rebuild_catalog,
    reconcile_market,
    validate_domain_commit_closure,
)


FIXED_TIME = "2026-09-05T12:00:00+08:00"
FIXTURE = Path(__file__).parent / "fixtures/tushare_pr3_real_sample.json"
REPORTS = Path(__file__).parents[1] / "reports/pr3"


class FixtureTushareClient:
    def __init__(self, responses: dict[str, list[dict[str, object]]]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, object]]] = []

    def query(self, endpoint: str, *, fields: str, **params: object):
        self.calls.append((endpoint, dict(params)))
        requested_fields = fields.split(",")
        rows = self.responses[endpoint]
        selected = []
        codes = set(str(params.get("ts_code", "")).split(","))
        for row in rows:
            if endpoint == "trade_cal" and params.get("exchange") != row["exchange"]:
                continue
            if endpoint not in {"trade_cal", "stock_basic"} and codes != {""}:
                if row["ts_code"] not in codes:
                    continue
            selected.append({field: row.get(field) for field in requested_fields})
        return selected


def load_fixture(name: str) -> dict[str, object]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))[name]


def collect_fixture(
    root: Path, fixture: dict[str, object]
) -> tuple[dict[str, list[str]], FixtureTushareClient]:
    responses = fixture["responses"]
    assert isinstance(responses, dict)
    client = FixtureTushareClient(responses)
    collector = TushareCollector(root, client)
    symbols = fixture["symbols"]
    start = fixture["start_date"]
    end = fixture["end_date"]
    assert isinstance(symbols, list) and isinstance(start, str) and isinstance(end, str)
    exchanges = sorted({"SSE" if symbol.endswith(".SH") else "SZSE" for symbol in symbols})
    ids = {"calendar": [], "security": [], "market": []}
    for exchange in exchanges:
        ids["calendar"].append(
            collector.collect(
                "trade_cal",
                {"exchange": exchange, "start_date": start, "end_date": end},
                retrieved_at=FIXED_TIME,
            ).raw_batch_id
        )
    ids["security"].append(
        collector.collect(
            "stock_basic",
            {"exchange": "", "list_status": "L"},
            retrieved_at=FIXED_TIME,
        ).raw_batch_id
    )
    codes = ",".join(symbols)
    for endpoint in ("daily", "adj_factor"):
        ids["market"].append(
            collector.collect(
                endpoint,
                {"ts_code": codes, "start_date": start, "end_date": end},
                retrieved_at=FIXED_TIME,
            ).raw_batch_id
        )
    for endpoint in ("daily_basic", "stk_limit"):
        for symbol in symbols:
            ids["market"].append(
                collector.collect(
                    endpoint,
                    {"ts_code": symbol, "start_date": start, "end_date": end},
                    retrieved_at=FIXED_TIME,
                ).raw_batch_id
            )
    ids["market"].append(
        collector.collect(
            "suspend_d",
            {"ts_code": symbols[0], "suspend_date": start},
            retrieved_at=FIXED_TIME,
        ).raw_batch_id
    )
    return ids, client


def build_fixture(
    root: Path,
    fixture: dict[str, object],
    ids: dict[str, list[str]],
) -> dict[str, str]:
    symbols = fixture["symbols"]
    start = str(fixture["start_date"])
    end = str(fixture["end_date"])
    assert isinstance(symbols, list)
    config = {
        "source_profile_version": "tushare_phase1.v1",
        "symbols": symbols,
        "start_session": f"{start[:4]}-{start[4:6]}-{start[6:]}",
        "end_session": f"{end[:4]}-{end[4:6]}-{end[6:]}",
        "pit_classification": "current-observed-best-effort",
    }
    calendar = BuildApplication(
        "trading_calendar",
        TushareMarketBuilder(
            root, "trading_calendar", builder_config=config, created_at=FIXED_TIME
        ),
    ).build(None, ids["calendar"], [], "trading_calendar.v1")
    security = BuildApplication(
        "security_master",
        TushareMarketBuilder(
            root, "security_master", builder_config=config, created_at=FIXED_TIME
        ),
    ).build(None, ids["security"], [], "security_master.v1")
    market = BuildApplication(
        "market_daily",
        TushareMarketBuilder(
            root,
            "market_daily",
            builder_config=config,
            calendar_commit_id=calendar.commit_id,
            security_master_commit_id=security.commit_id,
            created_at=FIXED_TIME,
        ),
    ).build(None, ids["market"], [], "market_daily.v1")
    snapshot = create_snapshot(
        root,
        {
            "trading_calendar": calendar.commit_id,
            "security_master": security.commit_id,
            "market_daily": market.commit_id,
        },
        created_at=FIXED_TIME,
    )
    return {
        "trading_calendar": calendar.commit_id,
        "security_master": security.commit_id,
        "market_daily": market.commit_id,
        "snapshot": snapshot.snapshot_id,
    }


class Pr3VerticalSliceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "data"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_source_profile_freezes_endpoint_units_mapping_and_pit_limits(self) -> None:
        profile = load_tushare_source_profile()
        self.assertEqual(
            set(profile["endpoints"]),
            {
                "trade_cal",
                "stock_basic",
                "daily",
                "adj_factor",
                "daily_basic",
                "stk_limit",
                "suspend_d",
            },
        )
        self.assertEqual(profile["endpoints"]["daily"]["units"]["vol"], "hundred-share lots")
        self.assertEqual(
            profile["endpoints"]["daily_basic"]["units"]["total_mv"],
            "ten-thousand CNY",
        )
        self.assertIn("unknown", profile["pit_classification"]["historical_availability"])
        self.assertIn("not verified PIT", profile["pit_classification"]["revision_capability"])

    def test_real_source_raw_snapshot_reader_and_empty_staging_qlib_view(self) -> None:
        fixture = load_fixture("listing_slice")
        ids, client = collect_fixture(self.root, fixture)
        daily_raw = load_raw_batch(self.root, ids["market"][0])
        self.assertEqual(daily_raw.manifest["request"]["endpoint"], "daily")
        self.assertEqual(
            json.loads(daily_raw.payload), fixture["responses"]["daily"]
        )
        self.assertGreater(len(client.calls), 0)

        artifacts = build_fixture(self.root, fixture, ids)
        snapshot = load_snapshot(self.root, artifacts["snapshot"])
        for domain in ("trading_calendar", "security_master", "market_daily"):
            self.assertEqual(
                validate_domain_commit_closure(
                    self.root, domain, artifacts[domain]
                ).ref.commit_id,
                artifacts[domain],
            )
        self.assertEqual(snapshot.ref.snapshot_id, artifacts["snapshot"])

        self.assertFalse((self.root / "exports/qlib").exists())
        with patch.object(
            socket.socket, "connect", side_effect=AssertionError("network forbidden")
        ):
            reader = SnapshotReader(self.root, artifacts["snapshot"])
            view = build_qlib_view(
                self.root,
                artifacts["snapshot"],
                symbols=fixture["symbols"],
                start_session="2025-01-01",
                end_session="2025-01-06",
                created_at=FIXED_TIME,
            )
        self.assertEqual(reader.schema("market_daily")[:2], ("session", "symbol"))
        self.assertEqual(
            tuple(QlibViewReader(self.root, view.view_id).view.manifest["fields"]),
            MARKET_VIEW_FIELDS,
        )
        equivalence = compare_direct_and_qlib(
            self.root, artifacts["snapshot"], view.view_id
        )
        self.assertEqual(equivalence["status"], "PASS")
        self.assertEqual(equivalence["direct_keys"], 10)

        rows = reader.market_daily(
            fixture["symbols"], "2025-01-01", "2025-01-06"
        )
        keys = {(row["session"], row["symbol"]) for row in rows}
        for symbol in ("603072.SH", "301581.SZ"):
            self.assertNotIn(("2025-01-02", symbol), keys)
            self.assertIn(("2025-01-03", symbol), keys)

    def test_real_suspend_evidence_creates_rows_but_daily_absence_does_not(self) -> None:
        fixture = load_fixture("suspension_slice")
        ids, _ = collect_fixture(self.root, fixture)
        artifacts = build_fixture(self.root, fixture, ids)
        rows = SnapshotReader(self.root, artifacts["snapshot"]).market_daily(
            fixture["symbols"], "2025-08-29", "2025-09-09"
        )
        suspended = [row for row in rows if row["is_suspended"]]
        self.assertEqual(
            [row["session"] for row in suspended],
            [
                "2025-09-01",
                "2025-09-02",
                "2025-09-03",
                "2025-09-04",
                "2025-09-05",
                "2025-09-08",
            ],
        )
        self.assertTrue(all(row["open"] is None for row in suspended))
        self.assertTrue(all(row["volume_shares"] == 0 for row in suspended))

        without_evidence = json.loads(json.dumps(fixture))
        without_evidence["responses"]["suspend_d"] = []
        other_root = Path(self.temporary.name) / "without-suspend-evidence"
        other_ids, _ = collect_fixture(other_root, without_evidence)
        other = build_fixture(other_root, without_evidence, other_ids)
        other_rows = SnapshotReader(other_root, other["snapshot"]).market_daily(
            fixture["symbols"], "2025-08-29", "2025-09-09"
        )
        self.assertEqual([row["session"] for row in other_rows], ["2025-08-29", "2025-09-09"])

    def test_frozen_raw_only_clean_rebuild_is_identical_and_offline(self) -> None:
        fixture = load_fixture("listing_slice")
        ids, _ = collect_fixture(self.root, fixture)
        original = build_fixture(self.root, fixture, ids)
        root_b = Path(self.temporary.name) / "offline"
        shutil.copytree(self.root / "raw/batches", root_b / "raw/batches")
        self.assertFalse((root_b / "canonical").exists())
        self.assertFalse((root_b / "snapshots").exists())
        self.assertFalse((root_b / "catalog.sqlite").exists())

        with patch.object(
            socket.socket, "connect", side_effect=AssertionError("network forbidden")
        ):
            rebuilt = build_fixture(root_b, fixture, ids)
            view = build_qlib_view(
                root_b,
                rebuilt["snapshot"],
                symbols=fixture["symbols"],
                start_session="2025-01-01",
                end_session="2025-01-06",
                created_at=FIXED_TIME,
            )
            count = rebuild_catalog(root_b)
        self.assertEqual(rebuilt, original)
        self.assertEqual(count, sum(map(len, ids.values())) + 4)
        self.assertEqual(
            SnapshotReader(root_b, rebuilt["snapshot"]).commits["market_daily"].rows,
            SnapshotReader(self.root, original["snapshot"]).commits["market_daily"].rows,
        )
        self.assertEqual(
            compare_direct_and_qlib(root_b, rebuilt["snapshot"], view.view_id)["status"],
            "PASS",
        )

    def test_reconciliation_classifies_differences_without_trusting_qsys(self) -> None:
        fixture = load_fixture("listing_slice")
        ids, _ = collect_fixture(self.root, fixture)
        artifacts = build_fixture(self.root, fixture, ids)
        source = canonical_market_observations(
            self.root,
            ids["market"],
            symbols=fixture["symbols"],
            start_session="2025-01-02",
            end_session="2025-01-03",
        )
        qsys = []
        for row in source:
            copied = dict(row)
            copied.pop("pre_close")
            copied.pop("adj_factor")
            qsys.append(copied)
        qsys[0]["turnover_rate"] += 0.01
        report = reconcile_market(
            self.root,
            artifacts["snapshot"],
            source_rows=source,
            qsys_rows=qsys,
            symbols=fixture["symbols"],
            start_session="2025-01-02",
            end_session="2025-01-03",
        )
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(set(report["counts"]), set(RECONCILIATION_CATEGORIES))
        self.assertGreater(report["counts"]["all_equal"], 0)
        self.assertGreater(report["counts"]["qsys_missing"], 0)
        self.assertEqual(
            report["counts"]["axiom_tushare_equal_qsys_different"], 1
        )
        self.assertEqual(report["contract_or_build_bug_count"], 0)

    def test_committed_real_run_evidence_is_complete(self) -> None:
        run = json.loads((REPORTS / "run_manifest.json").read_text(encoding="utf-8"))
        equivalence = json.loads(
            (REPORTS / "direct_qlib_equivalence.json").read_text(encoding="utf-8")
        )
        reconciliation = json.loads(
            (REPORTS / "tushare_axiom_qsys_reconciliation.json").read_text(
                encoding="utf-8"
            )
        )
        offline = json.loads(
            (REPORTS / "offline_rebuild.json").read_text(encoding="utf-8")
        )
        self.assertEqual(run["scope"]["sse_symbols"], 10)
        self.assertEqual(run["scope"]["szse_symbols"], 10)
        self.assertEqual(len(run["raw_batches"]), 46)
        self.assertEqual(
            run["source_profile"]["profile_version"], "tushare_phase1.v1"
        )
        self.assertTrue(
            run["source_profile"]["normalized_content_digest"].startswith(
                "sha256:"
            )
        )
        profile_content = json.dumps(
            load_tushare_source_profile(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        self.assertEqual(
            run["source_profile"]["normalized_content_digest"],
            f"sha256:{hashlib.sha256(profile_content).hexdigest()}",
        )
        self.assertEqual(run["domain_commits"]["trading_calendar"]["rows"], 730)
        self.assertEqual(run["domain_commits"]["security_master"]["rows"], 20)
        self.assertEqual(run["domain_commits"]["market_daily"]["rows"], 4858)
        self.assertEqual(run["qlib_view"]["fields"], list(MARKET_VIEW_FIELDS))
        self.assertEqual(
            run["qlib_view"]["snapshot_ref"]["snapshot_id"],
            run["snapshot"]["snapshot_id"],
        )
        self.assertEqual(len(run["real_cases"]["confirmed_suspensions"]), 6)
        self.assertGreater(
            len(run["real_cases"]["pre_close_not_prior_close_examples"]), 0
        )
        self.assertEqual(equivalence["status"], "PASS")
        self.assertEqual(equivalence["mismatches"], [])
        self.assertEqual(reconciliation["status"], "PASS")
        self.assertEqual(reconciliation["contract_or_build_bug_count"], 0)
        self.assertEqual(offline["status"], "PASS")
        self.assertEqual(offline["network_calls"], 0)


if __name__ == "__main__":
    unittest.main()
