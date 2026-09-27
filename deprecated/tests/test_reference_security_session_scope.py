from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from axiom_data import ArtifactError, BuildApplication, TushareReferenceCollector
from axiom_data.artifacts import _DOMAIN_DEPENDENCIES
from axiom_data.reference_source import TushareReferenceBuilder
from test_reference_evidence_reference import END as FULL_END
from test_reference_evidence_reference import FixtureClient, START as FULL_START, SYMBOLS, build_all, collect_all
from test_artifacts import synthetic_source_fixture


POLICY = "exchange_security.v1"
SYMBOL = "600832.SH"
OTHER_SYMBOL = "600001.SH"
START = "2014-12-31"
END = "2015-05-22"
PATCH_PARENT_END = "2026-01-04"


def _builder(*, policy: str | None = POLICY, calendar_rows=None, security_rows=None):
    builder = object.__new__(TushareReferenceBuilder)
    builder.builder_config = {} if policy is None else {"security_session_scope": policy}
    deps = {
        "trading_calendar": SimpleNamespace(rows=list(calendar_rows or [])),
        "security_master": SimpleNamespace(rows=list(security_rows or [])),
    }
    builder._dependency = lambda domain: deps[domain]
    return builder


def _identity(symbol=SYMBOL, *, list_session="2015-01-01", delist_session="2015-05-20"):
    return {
        "symbol": symbol,
        "exchange": "SSE",
        "list_session": list_session,
        "delist_session": delist_session,
        "status": "source-current",
    }


def _calendar():
    return [
        {"exchange": "SSE", "session": session, "is_open": True}
        for session in (
            "2014-12-31", "2015-05-18", "2015-05-19", "2015-05-20", "2015-05-21", "2015-05-22"
        )
    ]


def _raw():
    return SimpleNamespace(
        manifest={"retrieved_at": "2026-09-11T00:00:00Z"},
        ref=SimpleNamespace(raw_batch_id="raw-fixture"),
    )


def _source_rows():
    return [
        {"ts_code": SYMBOL, "trade_date": "20141231", "adj_factor": 0.9},
        {"ts_code": SYMBOL, "trade_date": "20150518", "adj_factor": 1.0},
        {"ts_code": SYMBOL, "trade_date": "20150519", "adj_factor": 1.1},
        {"ts_code": SYMBOL, "trade_date": "20150520", "adj_factor": 1.2},
        {"ts_code": SYMBOL, "trade_date": "20150521", "adj_factor": 1.3},
        {"ts_code": SYMBOL, "trade_date": "20150522", "adj_factor": 1.4},
    ]


@synthetic_source_fixture
class Dm1SecuritySessionScopeTest(unittest.TestCase):
    def test_factor_and_capital_filter_identity_bounds_and_match_single_symbol_mapping(self):
        factor_sources = _source_rows()
        capital_sources = [
            {"ts_code": row["ts_code"], "trade_date": row["trade_date"], "total_share": 10000, "float_share": 8000}
            for row in factor_sources
        ]
        builder = _builder(calendar_rows=_calendar(), security_rows=[_identity()])
        raw = _raw()
        factors = builder._factor_rows(
            {"adj_factor": [(row, raw) for row in factor_sources]},
            {SYMBOL}, START, END,
        )
        capital = builder._capital_rows(
            {"daily_basic": [(row, raw) for row in capital_sources]},
            {SYMBOL}, START, END,
        )
        expected_sessions = ["2015-05-18", "2015-05-19"]
        self.assertEqual([row["session"] for row in factors], expected_sessions)
        self.assertEqual([row["session"] for row in capital], expected_sessions)
        self.assertEqual(
            factors,
            builder._factor_rows(
                {"adj_factor": [(row, raw) for row in factor_sources if row["ts_code"] == SYMBOL]},
                {SYMBOL}, START, END,
            ),
        )
        self.assertEqual(
            capital,
            builder._capital_rows(
                {"daily_basic": [(row, raw) for row in capital_sources if row["ts_code"] == SYMBOL]},
                {SYMBOL}, START, END,
            ),
        )
        self.assertEqual(factor_sources, _source_rows())

    def test_missing_identity_calendar_and_unknown_identity_state_fail_closed(self):
        raw = _raw()
        source = _source_rows()[:1]
        with self.assertRaisesRegex(ArtifactError, "frozen security identity"):
            _builder(calendar_rows=_calendar(), security_rows=[])._factor_rows(
                {"adj_factor": [(source[0], raw)]}, {SYMBOL}, START, END,
            )
        with self.assertRaisesRegex(ArtifactError, "exchange calendar"):
            _builder(calendar_rows=[], security_rows=[_identity()])._factor_rows(
                {"adj_factor": [(source[0], raw)]}, {SYMBOL}, START, END,
            )
        with self.assertRaisesRegex(ArtifactError, "unknown identity"):
            _builder(
                calendar_rows=_calendar(),
                security_rows=[_identity(list_session=None, delist_session=None)],
            )._factor_rows({"adj_factor": [(source[0], raw)]}, {SYMBOL}, START, END)

    def test_closed_supplier_session_is_excluded_and_invalid_parent_fails_closed(self):
        raw = _raw()
        source = _source_rows()[1]
        closed = [dict(row) for row in _calendar()]
        closed[1]["is_open"] = False
        rows = _builder(calendar_rows=closed, security_rows=[_identity()])._factor_rows(
            {"adj_factor": [(source, raw)]}, {SYMBOL}, START, END,
        )
        self.assertEqual(rows, [])
        capital = {"ts_code": SYMBOL, "trade_date": source["trade_date"], "total_share": 10000, "float_share": 8000}
        capital_rows = _builder(calendar_rows=closed, security_rows=[_identity()])._capital_rows(
            {"daily_basic": [(capital, raw)]}, {SYMBOL}, START, END,
        )
        self.assertEqual(capital_rows, [])
        self.assertEqual(source, _source_rows()[1])
        with self.assertRaisesRegex(ArtifactError, "invalid parent"):
            _builder(calendar_rows=_calendar(), security_rows=[_identity()])._validate_parent_security_sessions(
                [{"symbol": SYMBOL, "session": "2015-05-20"}],
            )

    def test_incremental_parent_validation_covers_full_history_and_unrequested_symbols(self):
        builder = _builder(
            calendar_rows=_calendar(),
            security_rows=[_identity(), _identity(OTHER_SYMBOL)],
        )
        builder._validate_parent_security_sessions([
            {"symbol": SYMBOL, "session": "2015-05-18"},
            {"symbol": OTHER_SYMBOL, "session": "2015-05-19"},
        ])

    def test_new_policy_full_parent_accepts_one_day_subset_incremental_patch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ids = collect_all(root)
            commits = build_all(root, ids)
            dependencies = {
                domain: commits[domain]
                for domain in _DOMAIN_DEPENDENCIES["adjustment_factors"]
            }
            class ThroughParentEndClient:
                def query(self, endpoint, *, fields, **params):
                    rows = FixtureClient().query(endpoint, fields=fields, **params)
                    return [row for row in rows if row.get("trade_date", "") <= PATCH_PARENT_END.replace("-", "")]

            collector = TushareReferenceCollector(root, ThroughParentEndClient())
            parent_raw = collector.collect(
                "adjustment_factors", "adj_factor",
                {
                    "ts_code": ",".join(SYMBOLS),
                    "start_date": FULL_START.replace("-", ""),
                    "end_date": PATCH_PARENT_END.replace("-", ""),
                },
                retrieved_at="2026-09-10T00:00:00Z",
            )
            config = {
                "symbols": list(SYMBOLS),
                "start_session": FULL_START,
                "end_session": PATCH_PARENT_END,
                "pit_qualification": "best_effort",
                "security_session_scope": POLICY,
            }
            parent = BuildApplication(
                "adjustment_factors",
                TushareReferenceBuilder(
                    root, "adjustment_factors",
                    dependency_commit_ids=dependencies,
                    builder_config=config,
                ),
            ).build(None, [parent_raw.raw_batch_id], [], "adjustment_factors.v1")
            class OneDayClient:
                def query(self, endpoint, *, fields, **params):
                    rows = FixtureClient().query(endpoint, fields=fields, **params)
                    return [row for row in rows if row.get("trade_date") == FULL_END.replace("-", "")]

            patch_raw = TushareReferenceCollector(root, OneDayClient()).collect(
                "adjustment_factors", "adj_factor",
                {
                    "ts_code": SYMBOLS[0],
                    "start_date": FULL_END.replace("-", ""),
                    "end_date": FULL_END.replace("-", ""),
                },
                retrieved_at="2026-09-11T00:00:00Z",
            )
            child_config = dict(
                config,
                symbols=[SYMBOLS[0]],
                start_session=FULL_END,
                end_session=FULL_END,
            )
            child = BuildApplication(
                "adjustment_factors",
                TushareReferenceBuilder(
                    root, "adjustment_factors",
                    dependency_commit_ids=dependencies,
                    builder_config=child_config,
                ),
            ).build(parent.commit_id, [patch_raw.raw_batch_id], [], "adjustment_factors.v1")
            self.assertEqual(child.commit_id != parent.commit_id, True)

    def test_legacy_mapping_keeps_previous_behavior(self):
        raw = _raw()
        builder = _builder(policy=None, calendar_rows=[], security_rows=[])
        rows = builder._factor_rows(
            {"adj_factor": [(_source_rows()[3], raw)]}, {SYMBOL}, START, END,
        )
        self.assertEqual([row["session"] for row in rows], ["2015-05-20"])

    def test_unknown_policy_is_rejected_at_builder_construction(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ArtifactError, "unsupported security session scope"):
                TushareReferenceBuilder(
                    Path(directory), "adjustment_factors",
                    dependency_commit_ids={},
                    builder_config={"security_session_scope": "future.v1"},
                )


if __name__ == "__main__":
    unittest.main()
