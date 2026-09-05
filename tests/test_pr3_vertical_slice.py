from __future__ import annotations

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
    ArtifactError,
    BuildApplication,
    QlibViewReader,
    SnapshotReader,
    TushareCollector,
    TushareMarketBuilder,
    build_qlib_view,
    compare_direct_and_qlib,
    create_snapshot,
    independent_tushare_market_expectations,
    load_raw_batch,
    load_snapshot,
    load_tushare_source_profile,
    rebuild_catalog,
    reconcile_market,
    tushare_source_profile_digest,
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
            daily_raw.manifest["source_profile_digest"],
            tushare_source_profile_digest(),
        )
        self.assertEqual(
            json.loads(daily_raw.payload), fixture["responses"]["daily"]
        )
        self.assertGreater(len(client.calls), 0)

        artifacts = build_fixture(self.root, fixture, ids)
        snapshot = load_snapshot(self.root, artifacts["snapshot"])
        for domain in ("trading_calendar", "security_master", "market_daily"):
            commit = validate_domain_commit_closure(
                self.root, domain, artifacts[domain]
            )
            self.assertEqual(commit.ref.commit_id, artifacts[domain])
            self.assertEqual(
                commit.manifest["builder_config"]["source_profile_digest"],
                tushare_source_profile_digest(),
            )
            self.assertTrue(
                all(
                    ref["source_profile_digest"] == tushare_source_profile_digest()
                    for ref in commit.manifest["ordered_raw_batch_refs"]
                )
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
        source = independent_tushare_market_expectations(
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

    def test_independent_raw_expectations_freeze_market_mappings_and_join_key(self) -> None:
        fixture = load_fixture("listing_slice")
        ids, _ = collect_fixture(self.root, fixture)
        rows = independent_tushare_market_expectations(
            self.root,
            ids["market"],
            symbols=fixture["symbols"],
            start_session="2025-01-02",
            end_session="2025-01-03",
        )
        row = {(item["session"], item["symbol"]): item for item in rows}[
            ("2025-01-02", "000001.SZ")
        ]
        self.assertEqual(row["volume_shares"], 181_959_699)
        self.assertEqual(row["amount_cny"], 2_102_923_078.0)
        self.assertEqual(row["circulating_market_cap_cny"], 221_806_208_345.0)
        self.assertEqual(row["total_market_cap_cny"], 221_809_645_003.0)
        self.assertEqual(row["turnover_rate"], 0.9377)
        self.assertEqual(row["pre_close"], 11.7)
        self.assertEqual(row["adj_factor"], 127.7841)

    def test_independent_checker_catches_injected_production_volume_bug(self) -> None:
        fixture = load_fixture("listing_slice")
        ids, _ = collect_fixture(self.root, fixture)

        def broken_volume(value: object) -> int:
            return int(float(value) * 0.01)  # type: ignore[arg-type]

        with patch("axiom_data.tushare._volume_shares", side_effect=broken_volume):
            artifacts = build_fixture(self.root, fixture, ids)
        source = independent_tushare_market_expectations(
            self.root,
            ids["market"],
            symbols=fixture["symbols"],
            start_session="2025-01-02",
            end_session="2025-01-03",
        )
        report = reconcile_market(
            self.root,
            artifacts["snapshot"],
            source_rows=source,
            qsys_rows=source,
            symbols=fixture["symbols"],
            start_session="2025-01-02",
            end_session="2025-01-03",
        )
        volume_failures = [
            item
            for item in report["differences"]
            if item["field"] == "volume_shares"
        ]
        self.assertEqual(report["status"], "FAIL")
        self.assertGreater(report["contract_or_build_bug_count"], 0)
        self.assertTrue(volume_failures)
        self.assertTrue(all(item["contract_or_build_bug"] for item in volume_failures))
        self.assertTrue(
            all(item["preliminary_cause"] == "contract_or_build_bug" for item in volume_failures)
        )

    def test_source_profile_content_digest_changes_identity_and_rejects_old_raw(self) -> None:
        fixture = load_fixture("listing_slice")
        ids, _ = collect_fixture(self.root, fixture)
        baseline = build_fixture(self.root, fixture, ids)
        changed_profile = json.loads(json.dumps(load_tushare_source_profile()))
        commentary_only = json.loads(json.dumps(changed_profile))
        commentary_only["comment"] = "formatting-only note"
        commentary_only["endpoints"]["daily"]["comment"] = "non-semantic note"
        self.assertEqual(
            tushare_source_profile_digest(commentary_only),
            tushare_source_profile_digest(changed_profile),
        )
        changed_profile["endpoints"]["daily"]["units"]["vol"] = "shares"
        self.assertEqual(changed_profile["profile_version"], "tushare_phase1.v1")
        self.assertNotEqual(
            tushare_source_profile_digest(changed_profile),
            tushare_source_profile_digest(),
        )

        changed_root = Path(self.temporary.name) / "changed-profile"
        with patch(
            "axiom_data.tushare.load_tushare_source_profile",
            return_value=changed_profile,
        ):
            changed_ids, _ = collect_fixture(changed_root, fixture)
            changed = build_fixture(changed_root, fixture, changed_ids)
            with self.assertRaisesRegex(ArtifactError, "source profile mismatch"):
                build_fixture(self.root, fixture, ids)

        self.assertNotEqual(ids["market"][0], changed_ids["market"][0])
        self.assertNotEqual(baseline["market_daily"], changed["market_daily"])
        self.assertNotEqual(baseline["snapshot"], changed["snapshot"])
        changed_raw = load_raw_batch(changed_root, changed_ids["market"][0])
        self.assertEqual(
            changed_raw.manifest["source_profile_digest"],
            tushare_source_profile_digest(changed_profile),
        )

    def test_calendar_request_scope_requires_every_day_and_exchange(self) -> None:
        fixture = load_fixture("listing_slice")
        normal_ids, _ = collect_fixture(self.root, fixture)
        normal = build_fixture(self.root, fixture, normal_ids)
        self.assertEqual(
            len(SnapshotReader(self.root, normal["snapshot"]).trading_calendar()),
            12,
        )

        omissions = {
            "left-boundary": "20250101",
            "right-boundary": "20250106",
            "middle-closed-day": "20250104",
        }
        for label, missing_date in omissions.items():
            with self.subTest(label=label):
                changed = json.loads(json.dumps(fixture))
                changed["responses"]["trade_cal"] = [
                    row
                    for row in changed["responses"]["trade_cal"]
                    if not (
                        row["exchange"] == "SSE"
                        and row["cal_date"] == missing_date
                    )
                ]
                root = Path(self.temporary.name) / f"calendar-{label}"
                ids, _ = collect_fixture(root, changed)
                with self.assertRaisesRegex(ArtifactError, "completely cover"):
                    build_fixture(root, changed, ids)

        root = Path(self.temporary.name) / "calendar-missing-exchange"
        ids, _ = collect_fixture(root, fixture)
        ids["calendar"] = [
            raw_id
            for raw_id in ids["calendar"]
            if load_raw_batch(root, raw_id).manifest["request"]["params"]["exchange"]
            != "SSE"
        ]
        with self.assertRaisesRegex(ArtifactError, "every scoped exchange"):
            build_fixture(root, fixture, ids)

    def test_calendar_rejects_request_metadata_and_payload_scope_mismatch(self) -> None:
        fixture = load_fixture("listing_slice")
        root = Path(self.temporary.name) / "calendar-request-mismatch"
        ids, client = collect_fixture(root, fixture)
        wrong = TushareCollector(root, client).collect(
            "trade_cal",
            {"exchange": "SSE", "start_date": "20250102", "end_date": "20250106"},
            retrieved_at=FIXED_TIME,
        )
        ids["calendar"] = [
            wrong.raw_batch_id
            if load_raw_batch(root, raw_id).manifest["request"]["params"]["exchange"]
            == "SSE"
            else raw_id
            for raw_id in ids["calendar"]
        ]
        with self.assertRaisesRegex(ArtifactError, "request scope mismatch"):
            build_fixture(root, fixture, ids)

        outside = json.loads(json.dumps(fixture))
        extra = dict(outside["responses"]["trade_cal"][0])
        extra["cal_date"] = "20250107"
        outside["responses"]["trade_cal"].append(extra)
        other_root = Path(self.temporary.name) / "calendar-payload-mismatch"
        other_ids, _ = collect_fixture(other_root, outside)
        with self.assertRaisesRegex(ArtifactError, "outside the declared"):
            build_fixture(other_root, outside, other_ids)

    def test_unknown_nonempty_suspend_timing_is_rejected(self) -> None:
        fixture = load_fixture("suspension_slice")
        fixture["responses"]["suspend_d"][0]["suspend_timing"] = "10:00-11:00"
        ids, _ = collect_fixture(self.root, fixture)
        with self.assertRaisesRegex(ArtifactError, "suspend_timing"):
            build_fixture(self.root, fixture, ids)

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
        self.assertEqual(run["report_version"], "pr3-real-market-slice.v2")
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
        self.assertEqual(
            run["source_profile"]["normalized_content_digest"],
            tushare_source_profile_digest(),
        )
        self.assertTrue(
            all(
                item["source_profile_digest"]
                == run["source_profile"]["normalized_content_digest"]
                for item in run["raw_batches"]
            )
        )
        for commit in run["domain_commits"].values():
            self.assertTrue(
                all(
                    item["source_profile_digest"]
                    == run["source_profile"]["normalized_content_digest"]
                    for item in commit["ordered_source_profiles"]
                )
            )
        self.assertEqual(run["domain_commits"]["trading_calendar"]["rows"], 730)
        self.assertEqual(run["domain_commits"]["security_master"]["rows"], 20)
        self.assertEqual(run["domain_commits"]["market_daily"]["rows"], 4858)
        self.assertEqual(run["qlib_view"]["fields"], list(MARKET_VIEW_FIELDS))
        self.assertEqual(
            run["qlib_view"]["snapshot_ref"]["snapshot_id"],
            run["snapshot"]["snapshot_id"],
        )
        self.assertEqual(
            run["frozen_real_closure"]["path"],
            "/var/lib/axiom-data/forensic/"
            "pr3-market-slice-20260905-blocker-fix-v2",
        )
        self.assertEqual(
            run["frozen_real_closure"]["snapshot_id"],
            run["snapshot"]["snapshot_id"],
        )
        self.assertEqual(
            run["frozen_real_closure"]["qlib_view_id"],
            run["qlib_view"]["view_id"],
        )
        self.assertEqual(len(run["real_cases"]["confirmed_suspensions"]), 6)
        self.assertGreater(
            len(run["real_cases"]["pre_close_not_prior_close_examples"]), 0
        )
        self.assertEqual(equivalence["status"], "PASS")
        self.assertEqual(equivalence["mismatches"], [])
        self.assertEqual(reconciliation["status"], "PASS")
        self.assertEqual(
            reconciliation["source_expectation_path"],
            "independent-frozen-raw-profile-checker.v1",
        )
        self.assertEqual(reconciliation["contract_or_build_bug_count"], 0)
        self.assertEqual(offline["status"], "PASS")
        self.assertEqual(offline["network_calls"], 0)


if __name__ == "__main__":
    unittest.main()
