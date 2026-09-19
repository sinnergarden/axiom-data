from __future__ import annotations

import hashlib
import json
import shutil
import socket
import tempfile
import unittest
from pathlib import Path
from fixture_locations import fixture_root
from unittest.mock import patch
from copy import deepcopy

from axiom_data import (
    ArtifactError,
    ArtifactNotFoundError,
    BuildApplication,
    FactView,
    MarketDomainBuilder,
    QlibViewReader,
    SnapshotReader,
    TushareDm1Builder,
    TushareDm1Collector,
    build_adjusted_price_view,
    build_market_replay_view,
    build_qlib_view,
    compare_direct_and_qlib,
    create_snapshot,
    dm1_source_profile_digest,
    load_adjusted_price_view,
    load_dm1_source_profile,
    load_market_replay_view,
    load_raw_batch,
    load_snapshot,
    list_catalog,
    lookup_catalog,
    rebuild_catalog,
    reconcile_dm1_raw_mapping,
    validate_domain_commit_closure,
    validate_pr5_evidence,
    write_raw_batch,
)
from axiom_data.contracts import load_contract
from axiom_data.evidence import validate_d01_snapshot_coexistence


FIXED_TIME = "2026-09-06T10:00:00+08:00"
START = "2026-01-02"
END = "2026-01-05"
SYMBOLS = ("600000.SH", "600001.SH", "600002.SH", "600003.SH")
ADJUSTED_SYMBOLS = SYMBOLS[:2]
BENCHMARKS = ("000300.SH",)


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _profile_digest(name: str) -> str:
    return f"sha256:{hashlib.sha256(name.encode()).hexdigest()}"


def _write_rows(root: Path, raw_id: str, domain: str, rows: list[dict[str, object]]) -> None:
    from test_artifacts import write_rows
    write_rows(root, raw_id, domain, rows, retrieved_at=FIXED_TIME)


def _base_rows() -> dict[str, list[dict[str, object]]]:
    calendar = [
        {"exchange": "SSE", "session": "2026-01-02", "is_open": True, "previous_open_session": None},
        {"exchange": "SSE", "session": "2026-01-03", "is_open": False, "previous_open_session": "2026-01-02"},
        {"exchange": "SSE", "session": "2026-01-04", "is_open": False, "previous_open_session": "2026-01-02"},
        {"exchange": "SSE", "session": "2026-01-05", "is_open": True, "previous_open_session": "2026-01-02"},
    ]
    security = [
        {"symbol": SYMBOLS[0], "exchange": "SSE", "list_session": "2000-01-01", "delist_session": None, "status": "source-current-L"},
        {"symbol": SYMBOLS[1], "exchange": "SSE", "list_session": "2026-01-05", "delist_session": None, "status": "source-current-L"},
        {"symbol": SYMBOLS[2], "exchange": "SSE", "list_session": "2000-01-01", "delist_session": None, "status": "source-current-L"},
        {"symbol": SYMBOLS[3], "exchange": "SSE", "list_session": "2000-01-01", "delist_session": "2026-01-05", "status": "source-current-D"},
    ]

    def normal(session: str, symbol: str, factor: float, upper: float, lower: float) -> dict[str, object]:
        return {
            "session": session, "symbol": symbol,
            "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5, "pre_close": 8.5,
            "volume_shares": 1000, "amount_cny": 10200.0,
            "adj_factor": factor, "up_limit": upper, "down_limit": lower,
            "is_suspended": False, "turnover_rate": 1.0,
            "total_market_cap_cny": 100000000.0, "circulating_market_cap_cny": 80000000.0,
        }

    market = [
        normal("2026-01-02", SYMBOLS[0], 1.0, 11.0, 9.0),
        {
            "session": "2026-01-05", "symbol": SYMBOLS[0],
            "open": None, "high": None, "low": None, "close": None, "pre_close": 10.5,
            "volume_shares": 0, "amount_cny": 0.0, "adj_factor": 1.2,
            "up_limit": None, "down_limit": None, "is_suspended": True,
            "turnover_rate": None, "total_market_cap_cny": None, "circulating_market_cap_cny": None,
        },
        normal("2026-01-05", SYMBOLS[1], 2.0, 22.0, 18.0),
        normal("2026-01-05", SYMBOLS[2], 1.5, 16.5, 13.5),
    ]
    return {"trading_calendar": calendar, "security_master": security, "market_daily": market}


RESPONSES = {
    "daily": [
        {"ts_code": SYMBOLS[0], "trade_date": "20260102", "open": 10, "high": 11, "low": 9, "close": 10.5, "pre_close": 8.5, "vol": 10, "amount": 10.2},
        {"ts_code": SYMBOLS[1], "trade_date": "20260105", "open": 10, "high": 11, "low": 9, "close": 10.5, "pre_close": 8.5, "vol": 10, "amount": 10.2},
        {"ts_code": SYMBOLS[2], "trade_date": "20260105", "open": 10, "high": 11, "low": 9, "close": 10.5, "pre_close": 8.5, "vol": 10, "amount": 10.2},
    ],
    "suspend_d": [{"ts_code": SYMBOLS[0], "trade_date": "20260105", "suspend_timing": None, "suspend_type": "S"}],
    "stk_limit": [
        {"ts_code": SYMBOLS[0], "trade_date": "20260102", "up_limit": 11, "down_limit": 9},
        {"ts_code": SYMBOLS[1], "trade_date": "20260105", "up_limit": 22, "down_limit": 18},
        {"ts_code": SYMBOLS[2], "trade_date": "20260105", "up_limit": 16.5, "down_limit": 13.5},
    ],
    "dividend": [{"ts_code": SYMBOLS[0], "ann_date": "20260102", "end_date": "20251231", "div_proc": "实施", "stk_div": 0, "stk_bo_rate": 0, "stk_co_rate": 0, "cash_div_tax": 0.5, "record_date": "20260104", "ex_date": "20260105", "pay_date": "20260105", "div_listdate": None}],
    "adj_factor": [
        {"ts_code": SYMBOLS[0], "trade_date": "20260102", "adj_factor": 1.0},
        {"ts_code": SYMBOLS[0], "trade_date": "20260105", "adj_factor": 1.2},
        {"ts_code": SYMBOLS[1], "trade_date": "20260105", "adj_factor": 2.0},
        {"ts_code": SYMBOLS[2], "trade_date": "20260105", "adj_factor": 1.5},
    ],
    "index_daily": [
        {"ts_code": BENCHMARKS[0], "trade_date": "20260102", "close": 4000.0},
        {"ts_code": BENCHMARKS[0], "trade_date": "20260105", "close": 4050.0},
    ],
    "daily_basic": [
        {"ts_code": SYMBOLS[0], "trade_date": "20260102", "total_share": 10000, "float_share": 8000},
        {"ts_code": SYMBOLS[0], "trade_date": "20260105", "total_share": 10000, "float_share": 8000},
        {"ts_code": SYMBOLS[1], "trade_date": "20260105", "total_share": 20000, "float_share": 15000},
        {"ts_code": SYMBOLS[2], "trade_date": "20260105", "total_share": 15000, "float_share": 12000},
    ],
}


class FixtureClient:
    def query(self, endpoint: str, *, fields: str, **params: object):
        selected = set(str(params.get("ts_code", "")).split(","))
        rows = [
            row for row in RESPONSES[endpoint]
            if not selected or "" in selected or row["ts_code"] in selected
        ]
        names = fields.split(",")
        return [{name: row.get(name) for name in names} for row in rows]


def collect_all(root: Path) -> dict[str, list[str]]:
    collector = TushareDm1Collector(root, FixtureClient())
    compact_start, compact_end = START.replace("-", ""), END.replace("-", "")
    code_scope = ",".join(SYMBOLS)
    ids = {}
    ids["security_status"] = [
        collector.collect("security_status", endpoint, {"ts_code": code_scope, "start_date": compact_start, "end_date": compact_end}, retrieved_at=FIXED_TIME).raw_batch_id
        for endpoint in ("daily", "suspend_d")
    ]
    for domain, endpoint in (
        ("price_limits", "stk_limit"),
        ("adjustment_factors", "adj_factor"),
        ("security_capital", "daily_basic"),
    ):
        ids[domain] = [collector.collect(domain, endpoint, {"ts_code": code_scope, "start_date": compact_start, "end_date": compact_end}, retrieved_at=FIXED_TIME).raw_batch_id]
    ids["corporate_actions"] = [collector.collect("corporate_actions", "dividend", {"ts_code": code_scope}, retrieved_at=FIXED_TIME).raw_batch_id]
    ids["benchmark_daily"] = [collector.collect("benchmark_daily", "index_daily", {"ts_code": BENCHMARKS[0], "start_date": compact_start, "end_date": compact_end}, retrieved_at=FIXED_TIME).raw_batch_id]
    return ids


def write_dm1_variant(
    root: Path,
    raw_id: str,
    domain: str,
    endpoint: str,
    params: dict[str, object],
    rows: list[dict[str, object]],
    *,
    fields: list[str] | None = None,
    collector: str = "axiom-data.tushare-dm1-collector.v1",
) -> str:
    profile = load_dm1_source_profile()
    definition = profile["endpoints"][endpoint]
    response_fields = list(definition["fields"])
    request_fields = list(fields if fields is not None else response_fields)
    write_raw_batch(
        root,
        raw_id,
        domain=domain,
        source_profile=definition["source_profile_ref"],
        source_profile_version=profile["profile_version"],
        source_profile_digest=dm1_source_profile_digest(profile),
        request={"endpoint": endpoint, "params": params, "fields": request_fields},
        retrieved_at=FIXED_TIME,
        payload=_json_bytes(rows),
        collector_code=collector,
        summary={
            "rows": len(rows),
            "response_fields": response_fields,
            "represented_session_field": definition["represented_session_field"],
        },
    )
    return raw_id


def build_all(root: Path, ids: dict[str, list[str]]) -> dict[str, str]:
    base = _base_rows()
    for domain, rows in base.items():
        raw_id = f"raw-{domain}"
        if not (root / "raw/batches" / raw_id).exists():
            _write_rows(root, raw_id, domain, rows)
    calendar = BuildApplication("trading_calendar", MarketDomainBuilder(root, "trading_calendar", created_at=FIXED_TIME)).build(None, ["raw-trading_calendar"], [], "trading_calendar.v1")
    security = BuildApplication("security_master", MarketDomainBuilder(root, "security_master", created_at=FIXED_TIME)).build(None, ["raw-security_master"], [], "security_master.v1")
    market = BuildApplication("market_daily", MarketDomainBuilder(root, "market_daily", calendar_commit_id=calendar.commit_id, security_master_commit_id=security.commit_id, created_at=FIXED_TIME)).build(None, ["raw-market_daily"], [], "market_daily.v1")
    commits = {"trading_calendar": calendar.commit_id, "security_master": security.commit_id, "market_daily": market.commit_id}
    dependency_map = {
        "security_status": {"trading_calendar": calendar.commit_id, "security_master": security.commit_id, "market_daily": market.commit_id},
        "price_limits": {"trading_calendar": calendar.commit_id, "security_master": security.commit_id},
        "corporate_actions": {"trading_calendar": calendar.commit_id, "security_master": security.commit_id},
        "adjustment_factors": {"trading_calendar": calendar.commit_id, "security_master": security.commit_id, "market_daily": market.commit_id},
        "benchmark_daily": {"trading_calendar": calendar.commit_id},
        "security_capital": {"trading_calendar": calendar.commit_id, "security_master": security.commit_id},
    }
    for domain in dependency_map:
        symbols = list(BENCHMARKS if domain == "benchmark_daily" else SYMBOLS)
        config = {"symbols": symbols, "start_session": START, "end_session": END, "pit_qualification": "best_effort"}
        version = f"{domain}.v1"
        if domain == "corporate_actions":
            version = "corporate_actions.v2"
            config["corporate_action_observations"] = "corporate_action_observations.v1"
        elif domain == "security_capital":
            version = "security_capital.v2"
            config["capital_qualification"] = "capital_conflict.v1"
        ref = BuildApplication(domain, TushareDm1Builder(root, domain, dependency_commit_ids=dependency_map[domain], builder_config=config, created_at=FIXED_TIME)).build(None, ids[domain], [], version)
        commits[domain] = ref.commit_id
    snapshot = create_snapshot(root, commits, created_at=FIXED_TIME)
    adjusted = build_adjusted_price_view(root, snapshot.snapshot_id, symbols=ADJUSTED_SYMBOLS, start_session=START, end_session=END, anchor_session=END, pit_policy="research_non_pit", decision_cutoff=END, created_at=FIXED_TIME)
    replay = build_market_replay_view(root, snapshot.snapshot_id, symbols=SYMBOLS, start_session=START, end_session=END, created_at=FIXED_TIME)
    qlib = build_qlib_view(root, snapshot.snapshot_id, symbols=ADJUSTED_SYMBOLS, start_session=START, end_session=END, adjusted_price_view_id=adjusted.view_id, price_basis="anchor_adjusted", pit_policy="research_non_pit", decision_cutoff=END, created_at=FIXED_TIME)
    return {**commits, "snapshot": snapshot.snapshot_id, "adjusted": adjusted.view_id, "replay": replay.view_id, "qlib": qlib.view_id}


from test_artifacts import synthetic_source_fixture


@synthetic_source_fixture
class Pr5Dm1Test(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "data"
        self.ids = collect_all(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_scope_manifest_freezes_pr4_partition_without_deferred_overlap(self) -> None:
        scope = json.loads((Path(__file__).parents[1] / "src/axiom_data/scope/pr5_d_m1_scope.v1.json").read_text())
        pr5 = set(scope["pr5"]["leaf_ids"])
        pr6 = set(scope["deferred"]["pr6"]["leaf_ids"])
        pr7 = set(scope["deferred"]["pr7"]["leaf_ids"])
        self.assertFalse(pr5 & pr6 or pr5 & pr7 or pr6 & pr7)
        self.assertEqual(len(pr5), 18)
        self.assertEqual(scope["pr5"]["ownership_corrections"]["market.paused"], "security_status")

    def test_source_profile_and_every_new_raw_are_semantically_bound_v2(self) -> None:
        profile = load_dm1_source_profile()
        self.assertEqual(profile["profile_version"], "tushare_dm1.v1")
        self.assertIn("terminal history", profile["endpoints"]["dividend"]["terminal_history_risk"])
        raw_ids = [value for values in self.ids.values() for value in values]
        for raw_id in raw_ids:
            raw = load_raw_batch(self.root, raw_id)
            self.assertEqual(raw.manifest["schema_version"], "raw_batch.v2")
            self.assertEqual(raw.manifest["source_profile_digest"], dm1_source_profile_digest())
        collector = TushareDm1Collector(self.root, FixtureClient())
        request = {"ts_code": ",".join(SYMBOLS), "start_date": START.replace("-", ""), "end_date": END.replace("-", "")}
        later = collector.collect("adjustment_factors", "adj_factor", request, retrieved_at="2026-09-06T10:00:01+08:00")
        self.assertNotEqual(later.raw_batch_id, self.ids["adjustment_factors"][0])

    def test_each_dm1_raw_request_is_validated_before_payload_aggregation(self) -> None:
        artifacts = build_all(self.root, self.ids)
        dependencies = {
            name: artifacts[name]
            for name in ("trading_calendar", "security_master", "market_daily")
        }
        params = {
            "ts_code": SYMBOLS[0],
            "start_date": START.replace("-", ""),
            "end_date": END.replace("-", ""),
        }
        good = dict(RESPONSES["adj_factor"][0])
        cases = (
            ("swapped", [dict(good, ts_code=SYMBOLS[1])], None, "axiom-data.tushare-dm1-collector.v1", "source (row outside request selector: ts_code|date outside request bounds)"),
            ("symbol", [dict(good, ts_code="999999.SH")], None, "axiom-data.tushare-dm1-collector.v1", "source (row outside request selector: ts_code|date outside request bounds)"),
            ("date", [dict(good, trade_date="20260106")], None, "axiom-data.tushare-dm1-collector.v1", "source (row outside request selector: ts_code|date outside request bounds)"),
            ("fields", [good], ["ts_code", "trade_date"], "axiom-data.tushare-dm1-collector.v1", "Raw source profile/fields binding mismatch"),
            ("collector", [good], None, "axiom-data.wrong-collector.v1", "collector revision"),
        )
        for name, rows, fields, collector, error in cases:
            with self.subTest(name=name):
                raw_id = write_dm1_variant(
                    self.root,
                    f"raw-invalid-{name}",
                    "adjustment_factors",
                    "adj_factor",
                    params,
                    rows,
                    fields=fields,
                    collector=collector,
                )
                builder = TushareDm1Builder(
                    self.root,
                    "adjustment_factors",
                    dependency_commit_ids=dependencies,
                    builder_config={
                        "symbols": [SYMBOLS[0]],
                        "start_session": START,
                        "end_session": END,
                    },
                )
                with self.assertRaisesRegex(ArtifactError, error):
                    BuildApplication("adjustment_factors", builder).build(
                        None, [raw_id], [], "adjustment_factors.v1"
                    )

    def test_dm1_contract_and_source_profile_content_is_golden(self) -> None:
        expected = {
            "security_status.v1": "1087b0f2bd9e28a7578da2b4a9a807d0323d7010bc6aca14db79377b515c9e8a",
            "price_limits.v1": "df88b568f1835e5df26dd8534279e67271f1d0d33b6135b22298674b9b939d15",
            "corporate_actions.v1": "db00fa9fc152f32faa028022b5cb956bc26c606e28a0d601aae4e508fe976d03",
            "adjustment_factors.v1": "66971791dc0d2b20f0b5d2593ed19001199ec5456b538b32ee5c068ff3927938",
            "benchmark_daily.v1": "c51644aed6b743f86d2d97685eb85b7dd3dcc17dc12bd4d0d74445a3c2538564",
            "security_capital.v1": "6e3d7cdb208e19e7fd6c26761783f59e7c57d9687f82c81fcb5d70aa42acd270",
        }
        for version, digest in expected.items():
            self.assertEqual(hashlib.sha256(_json_bytes(load_contract(version))).hexdigest(), digest)
        adjusted = json.loads((Path(__file__).parents[1] / "src/axiom_data/contracts/adjusted_price.v1.json").read_text())
        self.assertEqual(hashlib.sha256(_json_bytes(adjusted)).hexdigest(), "571d3bcaf217b981e6423a8ceacb309a1bef15e1c591950582502dc23ff094b5")
        self.assertEqual(dm1_source_profile_digest(), "sha256:07680d2cdb037ecdadd3ed3eaa714abe5d98b00f3ece924e190f489b7ee64315")
        self.assertIn(
            "tushare.dividend.dm1.v1",
            load_contract("corporate_actions.v1")["semantics"]["support_matrix"],
        )

    def test_intraday_suspension_and_unsupported_action_terms_block_build(self) -> None:
        artifacts = build_all(self.root, self.ids)
        dependencies = {name: artifacts[name] for name in ("trading_calendar", "security_master", "market_daily")}
        changed_suspend = [dict(RESPONSES["suspend_d"][0], suspend_timing="09:30")]
        with patch.dict(RESPONSES, {"suspend_d": changed_suspend}):
            collector = TushareDm1Collector(self.root, FixtureClient())
            daily = collector.collect("security_status", "daily", {"ts_code": ",".join(SYMBOLS), "start_date": START.replace("-", ""), "end_date": END.replace("-", "")}, retrieved_at="2026-09-06T12:00:00+08:00")
            suspend = collector.collect("security_status", "suspend_d", {"ts_code": ",".join(SYMBOLS), "start_date": START.replace("-", ""), "end_date": END.replace("-", "")}, retrieved_at="2026-09-06T12:00:00+08:00")
        with self.assertRaisesRegex(ArtifactError, "intraday"):
            BuildApplication("security_status", TushareDm1Builder(self.root, "security_status", dependency_commit_ids=dependencies, builder_config={"symbols": list(SYMBOLS), "start_session": START, "end_session": END})).build(None, [daily.raw_batch_id, suspend.raw_batch_id], [], "security_status.v1")

        unsupported = [dict(RESPONSES["dividend"][0], stk_div=0.1, cash_div_tax=0)]
        with patch.dict(RESPONSES, {"dividend": unsupported}):
            action_raw = TushareDm1Collector(self.root, FixtureClient()).collect("corporate_actions", "dividend", {"ts_code": ",".join(SYMBOLS)}, retrieved_at="2026-09-06T13:00:00+08:00")
        with self.assertRaisesRegex(ArtifactError, "unsupported stock terms"):
            BuildApplication("corporate_actions", TushareDm1Builder(self.root, "corporate_actions", dependency_commit_ids={name: artifacts[name] for name in ("trading_calendar", "security_master")}, builder_config={"symbols": list(SYMBOLS), "start_session": START, "end_session": END, "corporate_action_observations": "corporate_action_observations.v1"})).build(None, [action_raw.raw_batch_id], [], "corporate_actions.v2")

    def test_dm1_snapshot_views_and_classification(self) -> None:
        artifacts = build_all(self.root, self.ids)
        snapshot = load_snapshot(self.root, artifacts["snapshot"])
        self.assertEqual(snapshot.manifest["schema_version"], "data_snapshot.v2")
        status = SnapshotReader(self.root, artifacts["snapshot"]).facts("security_status")
        states = {(row["session"], row["symbol"]): row["status"] for row in status}
        self.assertEqual(states[(START, SYMBOLS[0])], "normal_active")
        self.assertEqual(states[(END, SYMBOLS[0])], "suspended")
        self.assertEqual(states[(START, SYMBOLS[1])], "not_yet_listed")
        self.assertEqual(states[(START, SYMBOLS[2])], "unknown_source_gap")
        self.assertEqual(states[(END, SYMBOLS[3])], "delisted")
        replay = load_market_replay_view(self.root, artifacts["replay"])
        suspended = next(row for row in replay.rows if row["session"] == END and row["symbol"] == SYMBOLS[0])
        self.assertEqual(suspended["missing_reason"], "explicit_full_day_suspension")
        self.assertEqual(replay.manifest["price_basis"], "unadjusted")
        self.assertIn("execution", replay.manifest["excluded_capabilities"])
        action_rows = SnapshotReader(self.root, artifacts["snapshot"]).facts("corporate_actions")
        self.assertEqual(action_rows[0]["action_type"], "cash_dividend")
        capital = SnapshotReader(self.root, artifacts["snapshot"]).facts("security_capital")
        self.assertEqual(capital[0]["total_shares"], 100_000_000.0)
        reconciliation = reconcile_dm1_raw_mapping(
            str(self.root),
            artifacts["snapshot"],
            self.ids,
            symbols=SYMBOLS,
            start_session=START,
            end_session=END,
        )
        self.assertEqual(reconciliation["status"], "PASS")

    def test_independent_benchmark_and_action_checkers_catch_builder_bugs(self) -> None:
        original_benchmark = TushareDm1Builder._benchmark_rows
        original_actions = TushareDm1Builder._action_rows

        def broken_benchmark(builder, *args):
            rows = original_benchmark(builder, *args)
            for row in rows:
                row["close"] *= 0.01
            return rows

        def broken_actions(builder, *args):
            rows = original_actions(builder, *args)
            for row in rows:
                if row["cash_per_share"] is not None:
                    row["cash_per_share"] *= 0.01
            return rows

        for name, method, replacement, expected_domain in (
            ("benchmark", "_benchmark_rows", broken_benchmark, "benchmark_daily"),
            ("actions", "_action_rows", broken_actions, "corporate_actions"),
        ):
            with self.subTest(name=name):
                root = Path(self.temporary.name) / f"mapping-{name}"
                ids = collect_all(root)
                with patch.object(TushareDm1Builder, method, replacement):
                    artifacts = build_all(root, ids)
                    faulted_reader = SnapshotReader(root, artifacts['snapshot'])
                if expected_domain == 'corporate_actions':
                    with self.assertRaisesRegex(ArtifactError, 'qualified D-M1 observations differ from RawBatch mapping'):
                        SnapshotReader(root, artifacts['snapshot'])
                # The independent row checker also detects the faulty values
                # already captured while the deliberately broken mapper ran.
                with patch('axiom_data.dm1_reconciliation.SnapshotReader', return_value=faulted_reader):
                    report = reconcile_dm1_raw_mapping(
                        str(root), artifacts["snapshot"], ids,
                        symbols=SYMBOLS, start_session=START, end_session=END,
                    )
                self.assertEqual(report["status"], "FAIL")
                self.assertIn(expected_domain, {row["domain"] for row in report["mismatches"]})

    def test_adjusted_identity_anchor_guard_factview_and_qlib_equivalence(self) -> None:
        artifacts = build_all(self.root, self.ids)
        adjusted = load_adjusted_price_view(self.root, artifacts["adjusted"])
        source = SnapshotReader(self.root, artifacts["snapshot"]).market_daily(ADJUSTED_SYMBOLS, START, END)
        self.assertEqual(next(row for row in source if row["session"] == START)["close"], 10.5)
        first = next(row for row in adjusted.rows if row["session"] == START)
        self.assertAlmostEqual(first["close"], 10.5 / 1.2)
        same = build_adjusted_price_view(self.root, artifacts["snapshot"], symbols=ADJUSTED_SYMBOLS, start_session=START, end_session=END, anchor_session=END, pit_policy="research_non_pit", decision_cutoff=END, created_at="2026-09-07T00:00:00+08:00")
        self.assertEqual(same.view_id, artifacts["adjusted"])
        end_one = build_adjusted_price_view(self.root, artifacts["snapshot"], symbols=ADJUSTED_SYMBOLS[:1], start_session=START, end_session=END, anchor_session=END, pit_policy="research_non_pit", decision_cutoff=END, created_at=FIXED_TIME)
        start_one = build_adjusted_price_view(self.root, artifacts["snapshot"], symbols=ADJUSTED_SYMBOLS[:1], start_session=START, end_session=END, anchor_session=START, pit_policy="research_non_pit", decision_cutoff=END, created_at=FIXED_TIME)
        self.assertNotEqual(start_one.view_id, end_one.view_id)
        with self.assertRaisesRegex(ArtifactError, "future anchor"):
            build_adjusted_price_view(self.root, artifacts["snapshot"], symbols=ADJUSTED_SYMBOLS, start_session=START, end_session=END, anchor_session=END, pit_policy="strict_decision_time", decision_cutoff=START)
        with self.assertRaisesRegex(ArtifactNotFoundError, "build-required"):
            FactView(self.root, artifacts["snapshot"]).read("adjusted_price", price_basis="anchor_adjusted")
        fact = FactView(self.root, artifacts["snapshot"], adjusted_price_view_id=artifacts["adjusted"]).read("adjusted_price", symbols=ADJUSTED_SYMBOLS, start_session=START, end_session=END, price_basis="anchor_adjusted", pit_policy="research_non_pit", cutoff_policy=f"decision_cutoff={END}")
        self.assertEqual(fact["anchor"], END)
        self.assertEqual(fact["pit_qualification"], "best_effort")
        self.assertEqual(compare_direct_and_qlib(self.root, artifacts["snapshot"], artifacts["qlib"])["status"], "PASS")
        self.assertEqual(QlibViewReader(self.root, artifacts["qlib"]).view.manifest["price_basis"], "anchor_adjusted")

    def test_pit_qualification_strict_cutoff_and_view_policy_cannot_be_forged(self) -> None:
        artifacts = build_all(self.root, self.ids)
        dependencies = {
            name: artifacts[name]
            for name in ("trading_calendar", "security_master", "market_daily")
        }
        market = SnapshotReader(self.root, artifacts["snapshot"]).market_daily(
            ADJUSTED_SYMBOLS, START, END
        )

        def publish_factor(
            raw_id: str,
            qualification: str,
            basis: str,
            source_available_at: str | None,
            first_observed_at: str,
            *,
            source_ref: str | None = None,
        ):
            rows = [
                {
                    "session": row["session"],
                    "symbol": row["symbol"],
                    "factor": row["adj_factor"],
                    "source_available_at": source_available_at,
                    "first_observed_at": first_observed_at,
                    "availability_basis": basis,
                    "pit_qualification": qualification,
                    "source_ref": source_ref or raw_id,
                }
                for row in market
            ]
            _write_rows(self.root, raw_id, "adjustment_factors", rows)
            return BuildApplication(
                "adjustment_factors",
                MarketDomainBuilder(
                    self.root,
                    "adjustment_factors",
                    dependency_commit_ids=dependencies,
                    created_at=FIXED_TIME,
                ),
            ).build(None, [raw_id], [], "adjustment_factors.v1")

        with self.assertRaisesRegex(ArtifactError, "VERIFIED_EVIDENCE_UNAVAILABLE"):
            publish_factor(
                "raw-verified-current-batch",
                "verified",
                "revision_specific_public_evidence",
                "2025-01-01T00:00:00+00:00",
                FIXED_TIME,
            )
        for name, source_ref in (
            ("other-raw", self.ids["adjustment_factors"][0]),
            ("domain-commit", artifacts["adjustment_factors"]),
            ("snapshot", artifacts["snapshot"]),
            ("fabricated", "evidence-random-artifact-id"),
        ):
            with self.subTest(verified_source=name), self.assertRaisesRegex(
                ArtifactError, "VERIFIED_EVIDENCE_UNAVAILABLE"
            ):
                publish_factor(
                    f"raw-verified-{name}",
                    "verified",
                    "revision_specific_public_evidence",
                    "2025-01-01T00:00:00+00:00",
                    FIXED_TIME,
                    source_ref=source_ref,
                )

        valid_observed = publish_factor(
            "raw-observed-valid",
            "observed",
            "first_observation",
            None,
            FIXED_TIME,
        )
        self.assertTrue(valid_observed.commit_id.startswith("adjustment_factors-"))
        with self.assertRaisesRegex(ArtifactError, "predates its RawBatch retrieval"):
            publish_factor(
                "raw-observed-too-early",
                "observed",
                "first_observation",
                None,
                "2026-09-06T01:00:00+00:00",
            )

        observed = publish_factor(
            "raw-observed-after-cutoff",
            "observed",
            "first_observation",
            None,
            FIXED_TIME,
        )
        snapshot_ids = {
            name: artifacts[name]
            for name in (
                "trading_calendar",
                "security_master",
                "market_daily",
                "security_status",
                "price_limits",
                "corporate_actions",
                "adjustment_factors",
                "benchmark_daily",
                "security_capital",
            )
        }
        snapshot_ids["adjustment_factors"] = observed.commit_id
        observed_snapshot = create_snapshot(self.root, snapshot_ids, created_at=FIXED_TIME)
        with self.assertRaisesRegex(ArtifactError, "first_observed_at is later"):
            build_adjusted_price_view(
                self.root,
                observed_snapshot.snapshot_id,
                symbols=ADJUSTED_SYMBOLS,
                start_session=START,
                end_session=END,
                anchor_session=END,
                pit_policy="strict_decision_time",
                decision_cutoff=END,
            )
        with self.assertRaisesRegex(ArtifactError, "best-effort"):
            build_adjusted_price_view(
                self.root,
                artifacts["snapshot"],
                symbols=ADJUSTED_SYMBOLS,
                start_session=START,
                end_session=END,
                anchor_session=END,
                pit_policy="strict_decision_time",
                decision_cutoff=END,
            )
        fact_view = FactView(
            self.root,
            artifacts["snapshot"],
            adjusted_price_view_id=artifacts["adjusted"],
        )
        with self.assertRaisesRegex(ArtifactError, "qualification is unavailable"):
            fact_view.read(
                "adjusted_price",
                price_basis="anchor_adjusted",
                pit_policy="verified",
            )
        with self.assertRaisesRegex(ArtifactError, "differs from its Derived"):
            build_qlib_view(
                self.root,
                artifacts["snapshot"],
                symbols=ADJUSTED_SYMBOLS,
                start_session=START,
                end_session=END,
                adjusted_price_view_id=artifacts["adjusted"],
                price_basis="anchor_adjusted",
                pit_policy="strict_decision_time",
                decision_cutoff=END,
            )

    def test_observed_source_ref_must_be_in_transitive_raw_lineage(self) -> None:
        artifacts = build_all(self.root, self.ids)
        dependencies = {
            name: artifacts[name]
            for name in ("trading_calendar", "security_master", "market_daily")
        }
        market = SnapshotReader(self.root, artifacts["snapshot"]).market_daily(
            ADJUSTED_SYMBOLS, START, END
        )
        parent_market = next(
            row
            for row in market
            if row["symbol"] == SYMBOLS[0] and row["session"] == START
        )
        child_market = next(
            row
            for row in market
            if row["symbol"] == SYMBOLS[1] and row["session"] == END
        )
        grandchild_market = next(
            row
            for row in market
            if row["symbol"] == SYMBOLS[0] and row["session"] == END
        )

        def observed_row(market_row: dict[str, object], source_ref: str) -> dict[str, object]:
            return {
                "session": market_row["session"],
                "symbol": market_row["symbol"],
                "factor": market_row["adj_factor"],
                "source_available_at": None,
                "first_observed_at": FIXED_TIME,
                "availability_basis": "first_observation",
                "pit_qualification": "observed",
                "source_ref": source_ref,
            }

        def publish(raw_id: str, row: dict[str, object], parent: str | None = None):
            _write_rows(self.root, raw_id, "adjustment_factors", [row])
            return BuildApplication(
                "adjustment_factors",
                MarketDomainBuilder(
                    self.root,
                    "adjustment_factors",
                    dependency_commit_ids=dependencies,
                    created_at=FIXED_TIME,
                ),
            ).build(parent, [raw_id], [], "adjustment_factors.v1")

        parent_raw = "raw-observed-parent-lineage"
        parent = publish(parent_raw, observed_row(parent_market, parent_raw))
        child_raw = "raw-observed-child-lineage"
        child = publish(child_raw, observed_row(child_market, parent_raw), parent.commit_id)
        self.assertEqual(
            validate_domain_commit_closure(
                self.root, "adjustment_factors", child.commit_id
            ).ref,
            child,
        )
        grandchild_raw = "raw-observed-grandchild-lineage"
        grandchild = publish(
            grandchild_raw,
            observed_row(grandchild_market, parent_raw),
            child.commit_id,
        )
        self.assertEqual(
            validate_domain_commit_closure(
                self.root, "adjustment_factors", grandchild.commit_id
            ).ref,
            grandchild,
        )

        unrelated_raw = "raw-observed-unrelated-lineage"
        unrelated = publish(
            unrelated_raw, observed_row(child_market, unrelated_raw)
        )
        self.assertTrue(unrelated.commit_id.startswith("adjustment_factors-"))

        commits = self.root / "canonical/adjustment_factors/commits"
        before = set(commits.iterdir())
        direct_raw = "raw-observed-direct-external"
        with self.assertRaisesRegex(ArtifactError, "transitive RawBatch closure"):
            publish(direct_raw, observed_row(parent_market, unrelated_raw))
        self.assertEqual(before, set(commits.iterdir()))

        child_external_raw = "raw-observed-child-external"
        with self.assertRaisesRegex(ArtifactError, "transitive RawBatch closure"):
            publish(
                child_external_raw,
                observed_row(child_market, unrelated_raw),
                parent.commit_id,
            )
        self.assertEqual(before, set(commits.iterdir()))

        injected_raw = "raw-observed-injected-external"
        with patch("axiom_data.artifacts._validate_dm1_observation_refs"):
            injected = publish(
                injected_raw, observed_row(parent_market, unrelated_raw)
            )
        with self.assertRaisesRegex(ArtifactError, "transitive RawBatch closure"):
            validate_domain_commit_closure(
                self.root, "adjustment_factors", injected.commit_id
            )
        snapshot_ids = {
            name: artifacts[name]
            for name in (
                "trading_calendar",
                "security_master",
                "market_daily",
                "security_status",
                "price_limits",
                "corporate_actions",
                "adjustment_factors",
                "benchmark_daily",
                "security_capital",
            )
        }
        snapshot_ids["adjustment_factors"] = injected.commit_id
        before_snapshots = set((self.root / "snapshots").iterdir())
        with self.assertRaisesRegex(ArtifactError, "transitive RawBatch closure"):
            create_snapshot(self.root, snapshot_ids, created_at=FIXED_TIME)
        self.assertEqual(before_snapshots, set((self.root / "snapshots").iterdir()))

    def test_cross_domain_conflict_and_unsupported_action_fail_before_snapshot(self) -> None:
        artifacts = build_all(self.root, self.ids)
        # A separately valid but conflicting formal factor cannot compose into D-M1.
        broken = [dict(row) for row in RESPONSES["adj_factor"]]
        broken[0]["adj_factor"] = 9.0
        client = FixtureClient()
        with patch.dict(RESPONSES, {"adj_factor": broken}):
            raw = TushareDm1Collector(self.root, client).collect("adjustment_factors", "adj_factor", {"ts_code": ",".join(SYMBOLS), "start_date": START.replace("-", ""), "end_date": END.replace("-", "")}, retrieved_at="2026-09-06T11:00:00+08:00")
        ref = BuildApplication("adjustment_factors", TushareDm1Builder(self.root, "adjustment_factors", dependency_commit_ids={name: artifacts[name] for name in ("trading_calendar", "security_master", "market_daily")}, builder_config={"symbols": list(SYMBOLS), "start_session": START, "end_session": END}, created_at=FIXED_TIME)).build(None, [raw.raw_batch_id], [], "adjustment_factors.v1")
        ids = {name: artifacts[name] for name in ("trading_calendar", "security_master", "market_daily", "security_status", "price_limits", "corporate_actions", "benchmark_daily", "security_capital")}
        ids["adjustment_factors"] = ref.commit_id
        before = set((self.root / "snapshots").iterdir())
        with self.assertRaisesRegex(ArtifactError, "cross-domain"):
            create_snapshot(self.root, ids)
        self.assertEqual(before, set((self.root / "snapshots").iterdir()))

    def test_raw_only_offline_recovery_catalog_corruption_and_old_snapshot_coexist(self) -> None:
        artifacts = build_all(self.root, self.ids)
        legacy = create_snapshot(self.root, {name: artifacts[name] for name in ("trading_calendar", "security_master", "market_daily")}, created_at=FIXED_TIME)
        self.assertEqual(load_snapshot(self.root, legacy.snapshot_id).manifest["schema_version"], "data_snapshot.v1")
        offline = Path(self.temporary.name) / "offline"
        shutil.copytree(self.root / "raw/batches", offline / "raw/batches")
        with patch.object(socket.socket, "connect", side_effect=AssertionError("network forbidden")):
            rebuilt = build_all(offline, self.ids)
            count = rebuild_catalog(offline)
        self.assertEqual(rebuilt, artifacts)
        self.assertGreater(count, 0)
        path = offline / "canonical/adjustment_factors/commits" / rebuilt["adjustment_factors"] / "rows.json"
        path.write_bytes(path.read_bytes() + b"\n")
        with self.assertRaisesRegex(ArtifactError, "digest"):
            load_snapshot(offline, rebuilt["snapshot"])

    def test_d01_requires_two_explicit_loadable_immutable_snapshots(self) -> None:
        artifacts = build_all(self.root, self.ids)
        old_ref = create_snapshot(
            self.root,
            {
                name: artifacts[name]
                for name in ("trading_calendar", "security_master", "market_daily")
            },
            created_at=FIXED_TIME,
        )
        old = load_snapshot(self.root, old_ref.snapshot_id)
        new = load_snapshot(self.root, artifacts["snapshot"])

        def ref(snapshot) -> dict[str, str]:
            return {
                "snapshot_id": snapshot.ref.snapshot_id,
                "manifest_digest": snapshot.ref.manifest_digest,
                "identity_digest": snapshot.manifest["identity_digest"],
            }

        evidence = {
            "schema_version": "axiom_data.pr5_d01_snapshot_coexistence.v1",
            "validation_root": str(self.root),
            "old_snapshot": ref(old),
            "new_snapshot": ref(new),
            "old_snapshot_manifest_digest_before_new": old.ref.manifest_digest,
            "old_domain_refs": old.manifest["domain_refs"],
            "new_domain_refs": new.manifest["domain_refs"],
        }
        self.assertTrue(validate_d01_snapshot_coexistence(evidence))

        only_new_root = Path(self.temporary.name) / "d01-only-new"
        shutil.copytree(self.root, only_new_root)
        shutil.rmtree(only_new_root / "snapshots" / old.ref.snapshot_id)
        only_new = deepcopy(evidence)
        only_new["validation_root"] = str(only_new_root)
        with self.assertRaises(ArtifactError):
            validate_d01_snapshot_coexistence(only_new)

        for name in ("old_snapshot", "new_snapshot"):
            with self.subTest(missing=name):
                missing = deepcopy(evidence)
                missing[name]["snapshot_id"] = "snapshot-" + "0" * 64
                with self.assertRaises(ArtifactError):
                    validate_d01_snapshot_coexistence(missing)

        same = deepcopy(evidence)
        same["old_snapshot"] = same["new_snapshot"]
        with self.assertRaisesRegex(ArtifactError, "identities must differ"):
            validate_d01_snapshot_coexistence(same)

        corrupt_root = Path(self.temporary.name) / "d01-corrupt-old"
        shutil.copytree(self.root, corrupt_root)
        old_manifest = corrupt_root / "snapshots" / old.ref.snapshot_id / "manifest.json"
        old_manifest.chmod(old_manifest.stat().st_mode | 0o200)
        old_manifest.write_bytes(old_manifest.read_bytes() + b"corrupt")
        corrupt = deepcopy(evidence)
        corrupt["validation_root"] = str(corrupt_root)
        with self.assertRaisesRegex(ArtifactError, "digest"):
            validate_d01_snapshot_coexistence(corrupt)

    def test_catalog_rebuild_loads_and_indexes_all_formal_views(self) -> None:
        artifacts = build_all(self.root, self.ids)
        count = rebuild_catalog(self.root)
        counts: dict[str, int] = {}
        for entry in list_catalog(self.root):
            counts[entry.artifact_type] = counts.get(entry.artifact_type, 0) + 1
        self.assertEqual(counts["adjusted_price_view"], 1)
        self.assertEqual(counts["market_replay_view"], 1)
        self.assertEqual(counts["qlib_view"], 1)
        for artifact_type, identity in (
            ("adjusted_price_view", artifacts["adjusted"]),
            ("market_replay_view", artifacts["replay"]),
            ("qlib_view", artifacts["qlib"]),
        ):
            self.assertEqual(
                lookup_catalog(self.root, artifact_type, identity).artifact_id,
                identity,
            )
        (self.root / "catalog.sqlite").unlink()
        self.assertEqual(load_adjusted_price_view(self.root, artifacts["adjusted"]).ref.view_id, artifacts["adjusted"])
        self.assertEqual(load_market_replay_view(self.root, artifacts["replay"]).ref.view_id, artifacts["replay"])
        self.assertEqual(QlibViewReader(self.root, artifacts["qlib"]).view.ref.view_id, artifacts["qlib"])
        self.assertEqual(rebuild_catalog(self.root), count)

        corruptions = {
            "adjusted": Path("derived/adjusted_price/commits") / artifacts["adjusted"] / "rows.json",
            "replay": Path("derived/market_replay/commits") / artifacts["replay"] / "rows.json",
        }
        qlib_manifest = QlibViewReader(self.root, artifacts["qlib"]).view.manifest
        corruptions["qlib"] = Path("exports/qlib") / artifacts["qlib"] / qlib_manifest["output_files"][-1]["path"]
        for name, relative in corruptions.items():
            with self.subTest(name=name):
                clone = Path(self.temporary.name) / f"catalog-corrupt-{name}"
                shutil.copytree(self.root, clone)
                target = clone / relative
                target.write_bytes(target.read_bytes() + b"broken")
                with self.assertRaisesRegex(ArtifactError, "digest"):
                    rebuild_catalog(clone)

    def test_committed_evidence_refs_and_pass_results_are_consistent(self) -> None:
        report_dir = Path(__file__).parents[1] / "reports/pr5"
        reports = {
            name: json.loads((report_dir / f"{name}.json").read_text())
            for name in (
                "run_manifest",
                "direct_qlib_equivalence",
                "dm1_raw_mapping_reconciliation",
                "qsys_reconciliation",
                "offline_recovery",
                "dm1_acceptance_matrix",
            )
        }
        data_root = fixture_root(reports["run_manifest"]["forensic_closure"]["path"])
        # Validators receive explicit relocated locations in an in-memory copy;
        # committed evidence continues to record its original publication paths.
        reports = deepcopy(reports)
        for evidence in (reports['run_manifest']['d01_snapshot_coexistence'],
                         reports['dm1_acceptance_matrix']['gates']['D01']['evidence']):
            evidence['validation_root'] = str(fixture_root(evidence['validation_root']))
        validate_pr5_evidence(data_root, *reports.values())

        def coordinated(old: str, new: str) -> dict[str, object]:
            return json.loads(json.dumps(reports).replace(old, new))

        refs = reports["run_manifest"]["artifact_refs"]
        coordinated_cases = (
            ("adjusted", refs["adjusted_price_view"]["view_id"], "adjusted-price-" + "0" * 64),
            ("replay", refs["market_replay_view"]["view_id"], "market-replay-" + "0" * 64),
            ("qlib", refs["qlib_view"]["view_id"], "qlib-" + "0" * 64),
            ("snapshot", refs["snapshot"]["snapshot_id"], "snapshot-" + "0" * 64),
            ("manifest-digest", refs["adjusted_price_view"]["manifest_digest"], "sha256:" + "0" * 64),
        )
        for name, old, new in coordinated_cases:
            with self.subTest(coordinated=name), self.assertRaises(ArtifactError):
                validate_pr5_evidence(data_root, *coordinated(old, new).values())

        gate_cases = []
        changed = deepcopy(reports)
        del changed["dm1_acceptance_matrix"]["gates"]["D01"]["checks"]["immutable_snapshot_coexists"]
        gate_cases.append(("missing", changed))
        changed = deepcopy(reports)
        check = changed["dm1_acceptance_matrix"]["gates"]["D01"]["checks"].pop("immutable_snapshot_coexists")
        changed["dm1_acceptance_matrix"]["gates"]["D01"]["checks"]["immutable_snapshot_ok"] = check
        gate_cases.append(("rename", changed))
        changed = deepcopy(reports)
        changed["dm1_acceptance_matrix"]["gates"]["D01"]["checks"] = {"unrelated_claim": True}
        gate_cases.append(("replacement", changed))
        changed = deepcopy(reports)
        changed["dm1_acceptance_matrix"]["gates"]["D01"]["checks"]["immutable_snapshot_coexists"] = False
        gate_cases.append(("false-pass", changed))
        changed = deepcopy(reports)
        changed["offline_recovery"]["rebuilt_refs"]["snapshot"]["snapshot_id"] = "snapshot-tampered"
        gate_cases.append(("rebuilt-ref", changed))
        changed = deepcopy(reports)
        del changed["run_manifest"]["d01_snapshot_coexistence"]["old_snapshot"]
        del changed["dm1_acceptance_matrix"]["gates"]["D01"]["evidence"]["old_snapshot"]
        gate_cases.append(("d01-only-new", changed))
        changed = deepcopy(reports)
        for evidence in (
            changed["run_manifest"]["d01_snapshot_coexistence"],
            changed["dm1_acceptance_matrix"]["gates"]["D01"]["evidence"],
        ):
            evidence["old_snapshot"] = deepcopy(evidence["new_snapshot"])
        gate_cases.append(("d01-same-snapshot", changed))
        for name, changed in gate_cases:
            with self.subTest(name=name), self.assertRaises(ArtifactError):
                validate_pr5_evidence(data_root, *changed.values())

        d01_corrupt_root = Path(self.temporary.name) / "corrupt-d01-root"
        d01 = reports["run_manifest"]["d01_snapshot_coexistence"]
        shutil.copytree(Path(d01["validation_root"]), d01_corrupt_root)
        changed = deepcopy(reports)
        for evidence in (
            changed["run_manifest"]["d01_snapshot_coexistence"],
            changed["dm1_acceptance_matrix"]["gates"]["D01"]["evidence"],
        ):
            evidence["validation_root"] = str(d01_corrupt_root)
        old_manifest = (
            d01_corrupt_root
            / "snapshots"
            / d01["old_snapshot"]["snapshot_id"]
            / "manifest.json"
        )
        old_manifest.chmod(old_manifest.stat().st_mode | 0o200)
        old_manifest.write_bytes(old_manifest.read_bytes() + b"corrupt")
        with self.assertRaisesRegex(ArtifactError, "digest"):
            validate_pr5_evidence(data_root, *changed.values())

        corrupt_root = Path(self.temporary.name) / "corrupt-evidence-root"
        shutil.copytree(data_root, corrupt_root)
        adjusted_rows = (
            corrupt_root
            / "derived/adjusted_price/commits"
            / refs["adjusted_price_view"]["view_id"]
            / "rows.json"
        )
        adjusted_rows.chmod(adjusted_rows.stat().st_mode | 0o200)
        adjusted_rows.write_bytes(adjusted_rows.read_bytes() + b"corrupt")
        with self.assertRaisesRegex(ArtifactError, "digest"):
            validate_pr5_evidence(corrupt_root, *reports.values())


if __name__ == "__main__":
    unittest.main()
