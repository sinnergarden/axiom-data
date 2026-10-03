"""Executable event, retraction, holder completeness and financial recipes."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from axiom_data.api import Data
from axiom_data.event_reader import read_events
from axiom_data.financial import single_quarter, ttm
from axiom_data.protocols import EventQuery, IngestBatch, QueryError, UpdateRequest


def stamp(day: int) -> datetime:
    return datetime(2025, 5, day, tzinfo=timezone.utc)


def contract(domain: str) -> dict:
    if domain == "financial_events":
        return {"contract_id": "test.finance.v1",
                "logical_key": ["security_id", "endpoint", "report_type", "report_period"],
                "fields": {
                    "security_id": {"dtype": "string", "nullable": False},
                    "endpoint": {"dtype": "string", "nullable": False},
                    "report_type": {"dtype": "string", "nullable": False},
                    "report_period": {"dtype": "date", "nullable": False},
                    "vendor_ann_date": {"dtype": "date"},
                    "revenue": {"dtype": "int64", "unit": "CNY", "basis": "cumulative_ytd",
                                "status_field": "revenue__status"},
                    "revenue__status": {"dtype": "string"},
                }}
    if domain == "corporate_actions":
        return {"contract_id": "test.actions.v1",
                "logical_key": ["security_id", "logical_event_key"],
                "fields": {"security_id": {"dtype": "string", "nullable": False},
                           "logical_event_key": {"dtype": "string", "nullable": False},
                           "effective_date": {"dtype": "date", "nullable": False},
                           "vendor_ann_date": {"dtype": "date"},
                           "cash": {"dtype": "int64", "unit": "CNY/share"},
                           "cash__status": {"dtype": "string"}}}
    return {"contract_id": "test.holder.v1",
            "logical_key": ["security_id", "report_period"],
            "fields": {"security_id": {"dtype": "string", "nullable": False},
                       "report_period": {"dtype": "date", "nullable": False},
                       "group_completeness": {"dtype": "string", "nullable": False},
                       "vendor_ann_date": {"dtype": "date"},
                       "top10_ratio": {"dtype": "float64", "unit": "percent"}}}


def profile(domain: str) -> dict:
    fields = contract(domain)["fields"]
    return {"id": "test.vendor.v1", "field_map": {**{name: name for name in fields},
                                                    "revision_id": "revision_id",
                                                    "revision_sequence": "revision_sequence"},
            "source_units": {name: spec["unit"] for name, spec in fields.items() if "unit" in spec},
            "availability": {"date_field": "vendor_ann_date", "date_rule": "same_day_release",
                             "timezone": "Asia/Shanghai", "session_release_time": "17:00:00"},
            "revision_order": "source_sequence_only"}


def batch(domain: str, rows: list[dict], observed: datetime) -> IngestBatch:
    return IngestBatch(domain=domain, payload=json.dumps(rows).encode(), request={"bounded_test": True},
                       contract=contract(domain), source_profile=profile(domain), observed_at=observed)


def financial_row(period: str, amount: int | None, revision: str, sequence: int,
                  status: str = "value") -> dict:
    return {"security_id": "A", "endpoint": "income", "report_type": "consolidated",
            "report_period": period, "vendor_ann_date": "2025-04-30", "revenue": amount,
            "revenue__status": status, "revision_id": revision, "revision_sequence": sequence}


def eq(domain: str, field: str, start: str, end: str, cutoff: datetime,
       *, policy: str = "operational_pit_v1", time_field: str = "report_period",
       filters: dict | None = None) -> EventQuery:
    return EventQuery(domain=domain, fields=(field,), symbols=("A",), start=start, end=end,
                      cutoff=cutoff, pit_policy=policy, time_field=time_field, filters=filters or {})


class CompletionEventTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir="/private/tmp")
        self.addCleanup(self.temp.cleanup)
        self.data = Data(Path(self.temp.name) / "local")

    def update(self, base, operation, *batches):
        return self.data.update(base_snapshot=base, request=UpdateRequest(
            batches=tuple(batches), operation_id=operation, build_context={"test": operation}))

    def test_report_period_prunes_years_but_announcement_query_keeps_them(self):
        rows = [financial_row(f"{year}-12-31", year, f"r{year}", 1)
                for year in range(2014, 2026)]
        snapshot = self.update(None, "many_years", batch("financial_events", rows, stamp(2))).snapshot_id
        store = self.data.store
        with patch.object(store, "read_partition", wraps=store.read_partition) as reads:
            selected = read_events(store, snapshot, eq(
                "financial_events", "revenue", "2024-01-01", "2024-12-31", stamp(3)))
        self.assertEqual(selected.frame.revenue.tolist(), [2024])
        self.assertEqual([call.args[0]["partition"] for call in reads.call_args_list], ["period-2024"])
        # All reports share an announcement date: report-year pruning here
        # would lose older reports and incorrectly change PIT results.
        with patch.object(store, "read_partition", wraps=store.read_partition) as reads:
            selected = read_events(store, snapshot, eq(
                "financial_events", "revenue", "2025-04-01", "2025-04-30", stamp(3),
                time_field="vendor_ann_date"))
        self.assertEqual(len(selected.frame), 12)
        self.assertEqual(reads.call_count, 12)

    def test_correction_selected_before_range_and_old_snapshot_immutable(self):
        first = {"security_id": "A", "logical_event_key": "dividend-1", "effective_date": "2025-05-10",
                 "vendor_ann_date": "2025-05-01", "cash": 1, "cash__status": "value",
                 "revision_id": "action-r1", "revision_sequence": 1}
        old = self.update(None, "action_1", batch("corporate_actions", [first], stamp(2))).snapshot_id
        corrected = dict(first, effective_date="2025-06-10", revision_id="action-r2", revision_sequence=2)
        new = self.update(old, "action_2", batch("corporate_actions", [corrected], stamp(3))).snapshot_id
        may = eq("corporate_actions", "cash", "2025-05-01", "2025-05-31", stamp(4),
                 time_field="effective_date")
        assert read_events(self.data.store, old, may).frame["cash"].tolist() == [1]
        assert read_events(self.data.store, new, may).frame.empty
        june = eq("corporate_actions", "cash", "2025-06-01", "2025-06-30", stamp(4),
                  time_field="effective_date")
        selected = read_events(self.data.store, new, june)
        assert selected.frame["effective_date"].tolist() == ["2025-06-10"]
        assert selected.field_meta["cash"]["by_key"][0]["revision_id"] == "action-r2"
        # The actual normalized Parquet and saved Raw payload remain verifiable.
        manifest = self.data.store.load_snapshot(new)
        self.data.store.verify_partition(manifest["domains"]["corporate_actions"]["partitions"][0])
        assert len(manifest["domains"]["corporate_actions"]["raw_batch_ids"]) == 2

    def test_retraction_missing_quarter_ttm_and_provenance(self):
        periods = ["2024-03-31", "2024-06-30", "2024-09-30", "2024-12-31", "2025-03-31"]
        values = [100, 200, 300, 400, 120]
        initial = [financial_row(p, v, f"r{i}", 1) for i, (p, v) in enumerate(zip(periods, values))]
        old = self.update(None, "finance_1", batch("financial_events", initial, stamp(2))).snapshot_id
        query = eq("financial_events", "revenue", "2024-01-01", "2025-03-31", stamp(3),
                   filters={"endpoint": "income", "report_type": "consolidated"})
        source = read_events(self.data.store, old, query)
        args = {"field": "revenue", "endpoint": "income", "report_type": "consolidated",
                "basis": "cumulative_ytd", "unit": "CNY"}
        one = single_quarter(source, **args)
        assert one.frame["revenue"].tolist() == [100, 100, 100, 100, 120]
        assert one.field_meta["revenue"]["basis"] == "single_quarter"
        assert one.context["derivation"]["source_basis"] == "cumulative_ytd"
        trailing = ttm(source, **args)
        assert trailing.frame["revenue"].iloc[3:].tolist() == [400, 420]
        assert trailing.field_meta["revenue"]["basis"] == "trailing_twelve_months"
        assert trailing.context["query"]["value_basis"] == "trailing_twelve_months"
        assert trailing.context["derivation"]["input_snapshot_id"] == old
        assert len(trailing.field_meta["revenue"]["by_key"][-1]["components"]) >= 4
        revised = financial_row("2024-09-30", None, "r2_retract", 2, "retracted")
        new = self.update(old, "finance_2", batch("financial_events", [revised], stamp(4))).snapshot_id
        after = read_events(self.data.store, new, eq("financial_events", "revenue", "2024-01-01",
                                                     "2025-03-31", stamp(5)))
        assert after.field_meta["revenue"]["by_key"][2]["status"] == "retracted"
        assert pd.isna(after.frame["revenue"].iloc[2])
        invalid = ttm(after, **args)
        assert pd.isna(invalid.frame["revenue"].iloc[-1])
        assert invalid.field_meta["revenue"]["by_key"][-1]["missing_reason"] == "retracted"
        assert ttm(source, **args).frame["revenue"].iloc[-1] == 420
        incomplete = read_events(self.data.store, old, eq("financial_events", "revenue", "2024-06-01",
                                                          "2025-03-31", stamp(3)))
        missing = ttm(incomplete, **args)
        assert pd.isna(missing.frame["revenue"].iloc[-1])
        assert missing.field_meta["revenue"]["by_key"][-1]["missing_reason"] == "missing_quarter"
        with self.assertRaisesRegex(QueryError, "unit or basis"):
            ttm(source, **dict(args, unit="thousand CNY"))

    def test_best_effort_requires_rule_and_holder_group_incomplete(self):
        holder_rows = [
            {"security_id": "A", "report_period": "2024-12-31", "group_completeness": "complete",
             "vendor_ann_date": "2025-05-01", "top10_ratio": 58.0,
             "revision_id": "h1", "revision_sequence": 1},
            {"security_id": "A", "report_period": "2025-03-31", "group_completeness": "incomplete",
             "vendor_ann_date": "2025-05-01", "top10_ratio": 62.0,
             "revision_id": "h2", "revision_sequence": 1},
        ]
        snap = self.update(None, "holders_1", batch("top_holders_reports", holder_rows, stamp(4))).snapshot_id
        strict = read_events(self.data.store, snap, eq("top_holders_reports", "top10_ratio",
                                                     "2024-01-01", "2025-12-31", stamp(2)))
        assert strict.frame.empty
        vendor = read_events(self.data.store, snap, eq("top_holders_reports", "top10_ratio",
                                                     "2024-01-01", "2025-12-31", stamp(2),
                                                     policy="best_effort_vendor_v1"))
        assert vendor.frame["top10_ratio"].tolist()[0] == 58.0
        assert vendor.frame["top10_ratio"].isna().tolist() == [False, True]
        assert vendor.field_meta["top10_ratio"]["by_key"][1]["status"] == "source_missing"
        assert vendor.context["limitations"]

    def test_explicit_field_states_and_unknown_state(self):
        rows = [financial_row("2024-03-31", None, "missing", 1, "not_provided"),
                financial_row("2024-06-30", None, "parsed", 1, "parse_error"),
                financial_row("2024-09-30", None, "source", 1, "source_missing"),
                financial_row("2024-12-31", None, "withdrawn", 1, "retracted")]
        snap = self.update(None, "states_1", batch("financial_events", rows, stamp(2))).snapshot_id
        selected = read_events(self.data.store, snap, eq("financial_events", "revenue",
                                                        "2024-01-01", "2024-12-31", stamp(3)))
        assert [item["status"] for item in selected.field_meta["revenue"]["by_key"]] == [
            "not_provided", "parse_error", "source_missing", "retracted"]
        assert selected.frame["revenue"].isna().all()
        bad = financial_row("2025-03-31", None, "unknown", 1, "unrecognized")
        later = self.update(snap, "states_2", batch("financial_events", [bad], stamp(4))).snapshot_id
        with self.assertRaisesRegex(QueryError, "unknown field status"):
            read_events(self.data.store, later, eq("financial_events", "revenue",
                                                   "2025-01-01", "2025-03-31", stamp(5)))

    def test_next_open_date_rule_is_explicit_assumption(self):
        action = {"security_id": "A", "logical_event_key": "dividend-2", "effective_date": "2025-05-10",
                  "vendor_ann_date": "2025-05-01", "cash": 2, "cash__status": "value",
                  "revision_id": "next-r1", "revision_sequence": 1}
        source_profile = profile("corporate_actions")
        source_profile["availability"] = {
            "date_field": "vendor_ann_date", "date_rule": "next_open",
            "next_open_session_by_date": {"2025-05-01": "2025-05-05"},
            "timezone": "Asia/Shanghai", "session_release_time": "09:30:00"}
        input_batch = IngestBatch(domain="corporate_actions", payload=json.dumps([action]).encode(),
                                  request={"bounded_test": True}, contract=contract("corporate_actions"),
                                  source_profile=source_profile, observed_at=stamp(6))
        snap = self.update(None, "calendar_1", input_batch).snapshot_id
        before = eq("corporate_actions", "cash", "2025-05-01", "2025-05-31", stamp(2),
                    policy="best_effort_vendor_v1", time_field="effective_date")
        after = eq("corporate_actions", "cash", "2025-05-01", "2025-05-31",
                   datetime(2025, 5, 5, 2, tzinfo=timezone.utc),
                   policy="best_effort_vendor_v1", time_field="effective_date")
        assert read_events(self.data.store, snap, before).frame.empty
        chosen = read_events(self.data.store, snap, after)
        assert chosen.frame["cash"].tolist() == [2]
        assert chosen.field_meta["cash"]["by_key"][0]["availability_basis"] == "declared_vendor_assumption"

    def test_nullable_int64_preserves_large_fact_and_derived_value(self):
        huge = 9_007_199_254_740_993  # first integer not exactly representable as float64
        rows = [financial_row("2024-03-31", huge, "huge", 1),
                financial_row("2024-06-30", None, "null", 1, "not_provided")]
        snap = self.update(None, "large_1", batch("financial_events", rows, stamp(2))).snapshot_id
        selected = read_events(self.data.store, snap, eq("financial_events", "revenue",
                                                        "2024-01-01", "2024-06-30", stamp(3)))
        assert selected.frame["revenue"].dtype.name == "Int64"
        assert selected.frame["revenue"].iloc[0] == huge
        derived = single_quarter(selected, field="revenue", endpoint="income",
                                 report_type="consolidated", basis="cumulative_ytd", unit="CNY")
        assert derived.frame["revenue"].dtype.name == "Int64"
        assert derived.frame["revenue"].iloc[0] == huge
        assert pd.isna(derived.frame["revenue"].iloc[1])
        encoded = json.dumps(derived.to_json(), allow_nan=False)
        assert str(huge) in encoded

    def test_terminal_observation_order_declares_limit(self):
        source_profile = profile("financial_events")
        source_profile["revision_order"] = "terminal_observation_v1"
        old = financial_row("2024-03-31", 10, "old", 1)
        new = financial_row("2024-03-31", 20, "new", 2)
        def input_batch(row, observed):
            return IngestBatch(domain="financial_events", payload=json.dumps([row]).encode(),
                               request={"bounded_test": True}, contract=contract("financial_events"),
                               source_profile=source_profile, observed_at=observed)
        first = self.update(None, "terminal_1", input_batch(old, stamp(2))).snapshot_id
        second = self.update(first, "terminal_2", input_batch(new, stamp(4))).snapshot_id
        result = read_events(self.data.store, second, eq("financial_events", "revenue",
                                                         "2024-03-31", "2024-03-31", stamp(5)))
        assert result.frame["revenue"].tolist() == [20]
        assert any("terminal states ordered by system observation" in text
                   for text in result.context["limitations"])


if __name__ == "__main__":
    unittest.main()
