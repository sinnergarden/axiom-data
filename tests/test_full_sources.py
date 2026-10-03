"""Source-relative finance/event bulk behavior without provider network calls."""

import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from axiom_data.api import Data
from axiom_data.batch_fetch import run_batch_chunk
from axiom_data.bulk_jobs import plan_bulk_job
from axiom_data.event_sources import collect_event_response, event_source_profile, _FIELDS
from axiom_data.full_sources import (FullSourcePlan, full_source_status,
                                     _event_chunks, estimate_full_sources,
                                     plan_full_sources, run_full_sources,
                                     verify_full_sources)
from axiom_data.protocols import CoverageError, EventQuery
from axiom_data.storage import LocalStore
from axiom_data.updates import apply_saved_raw


IDENTITY = {"000001.SZ": "sec-one"}
CALENDAR = {"2025-03-29": "2025-03-31", "2025-04-01": "2025-04-02"}


class SourceClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def query(self, endpoint, *, fields, **params):
        self.calls.append((endpoint, params))
        return self.response


class CalendarLimitClient:
    def __init__(self):
        self.calls = []

    def query(self, endpoint, *, fields, **params):
        self.calls.append(endpoint)
        if endpoint == "trade_cal":
            start = datetime.strptime(params["start_date"], "%Y%m%d").date()
            end = datetime.strptime(params["end_date"], "%Y%m%d").date()
            rows = []
            while start <= end:
                rows.append({"exchange": params["exchange"],
                             "cal_date": start.strftime("%Y%m%d"),
                             "is_open": "1" if start.weekday() < 5 else "0"})
                start += timedelta(days=1)
            return rows
        if endpoint == "stk_limit":
            return [{"ts_code": "000001.SZ", "trade_date": params["trade_date"],
                     "up_limit": 11.0, "down_limit": 9.0}]
        raise AssertionError(endpoint)


class FullSourceTests(unittest.TestCase):
    def test_vip_financial_response_above_old_local_threshold_is_observed(self):
        row = {"ts_code": "000002.SZ", "ann_date": "20260828",
               "end_date": "20260630", "roe": 3.75, "roe_waa": 3.96,
               "debt_to_assets": 91.92}
        spec = {"endpoint": "fina_indicator_vip",
                "params": {"period": "20260630"},
                "fields": list(_FIELDS["fina_indicator_vip"]),
                "canonical_symbols": ["000001.SZ"]}
        with tempfile.TemporaryDirectory() as path:
            store, client = LocalStore(path), SourceClient([row] * 10001)
            fetched = run_batch_chunk(
                store, specs=[spec], client=client, operation_id="vip-large",
                plan_fingerprint="vip-large-plan", identity_map=IDENTITY,
                raw_log_offset=0, max_total_requests_per_chunk=8,
                event_calendar_map=CALENDAR,
                global_calls_per_minute=0, stock_basic_calls_per_minute=0)
            self.assertEqual(len(client.calls), 1)
            self.assertEqual(len(fetched["raw_batch_ids"]), 1)
            self.assertEqual(store.get_raw(fetched["raw_batch_ids"][0])["status"], "success")
            self.assertIsNone(event_source_profile("fina_indicator_vip",
                              identity_map=IDENTITY,
                              next_open_session_by_date=CALENDAR)["response_limit"])

    def test_daily_plan_uses_short_announcement_window_and_report_refresh(self):
        market = plan_bulk_job(mode="daily", symbols=["000001.SZ"],
                               identity_map=IDENTITY, start_session="2026-09-30",
                               end_session="2026-09-30", endpoints=("trade_cal",))
        plan = plan_full_sources(market=market, calendar_end="2026-10-31")
        self.assertEqual(plan.event_start, "2025-04-01")
        self.assertEqual(plan.announcement_start, "2026-08-31")
        self.assertEqual(FullSourcePlan.from_dict(plan.to_dict()), plan)
        self.assertEqual(plan.to_dict()["schema_version"], "local_full_source_job_v4")
        chunks = list(_event_chunks(plan, {"2026-09-30": "2026-10-01"}, ["20260930"]))
        specs = [spec for _, _, group, _ in chunks for spec in group]
        self.assertEqual({tuple(sorted(spec["params"].items())) for spec in specs
                          if spec["endpoint"] == "income_vip"}, {
            (("end_date", "20260831"), ("report_type", "1"), ("start_date", "20260831")),
            (("end_date", "20260930"), ("report_type", "1"), ("start_date", "20260901"))})
        self.assertEqual(len([s for s in specs if s["endpoint"] == "dividend"]), 31)
        self.assertEqual(len([s for s in specs if s["endpoint"] == "top10_holders"]), 1)
        self.assertEqual(estimate_full_sources(plan)["event_by_endpoint"]["dividend"], 31)

    def test_vendor_membership_plan_freezes_tushare_raw(self):
        market = plan_bulk_job(mode="bulk", symbols=["000001.SZ"],
                               identity_map=IDENTITY, start_session="2026-06-01",
                               end_session="2026-06-30", endpoints=("trade_cal",))
        membership = {"schema_version": "tushare_csi1800_membership_source_v1",
                      "index_weight_raw_batch_ids": ["b_300", "b_500", "b_1000"],
                      "stock_basic_raw_batch_ids": [f"b_stock_{i}" for i in range(6)],
                      "verified_through": "2026-06-30",
                      "between_snapshots": "carry_forward"}
        plan = plan_full_sources(market=market, calendar_end="2026-07-31",
                                 membership_source=membership)
        self.assertEqual(FullSourcePlan.from_dict(plan.to_dict()), plan)
        self.assertEqual(plan.to_dict()["schema_version"], "local_full_source_job_v4")
        self.assertEqual(plan.to_dict()["membership_source"], membership)

    def test_failed_chunk_resumes_in_new_bounded_attempt_round(self):
        class Flaky:
            def __init__(self):
                self.calls = []

            def query(self, endpoint, *, fields, **params):
                self.calls.append(params["ann_date"])
                if params["ann_date"] == "20260402" and self.calls.count("20260402") == 1:
                    raise ConnectionError("transient transport failure")
                return []

        specs = [{"endpoint": "dividend", "params": {"ann_date": day},
                  "fields": list(_FIELDS["dividend"]), "canonical_symbols": ["000001.SZ"]}
                 for day in ("20260401", "20260402")]
        with tempfile.TemporaryDirectory() as path:
            store, client = LocalStore(path), Flaky()
            options = {"specs": specs, "client": client, "operation_id": "retry-transport",
                       "plan_fingerprint": "frozen", "identity_map": IDENTITY,
                       "raw_log_offset": 0, "max_attempts": 1, "max_workers": 1,
                       "max_total_requests_per_chunk": 8,
                       "event_calendar_map": {"2026-04-01": "2026-04-02"},
                       "global_calls_per_minute": 0, "stock_basic_calls_per_minute": 0}
            with self.assertRaises(CoverageError):
                run_batch_chunk(store, **options)
            failed = [r for r in store.find_raw_by_operation("retry-transport").values()
                      if r["status"] == "failed"]
            self.assertEqual(len(failed), 1)
            result = run_batch_chunk(store, **options)
            self.assertEqual(client.calls, ["20260401", "20260402", "20260402"])
            self.assertEqual(len(result["raw_batch_ids"]), 2)
            self.assertEqual(store.get_raw(failed[0]["batch_id"])["status"], "failed")
            self.assertEqual(store.read_operation("retry-transport")["retry_round"], 1)

    def test_validator_fix_reclassifies_saved_response_without_network(self):
        row = {"ts_code": "000001.SZ", "ann_date": "20250329",
               "f_ann_date": "20250329", "end_date": "20241231",
               "report_type": "1", "total_revenue": 100.0,
               "n_income_attr_p": 10.0}
        spec = {"endpoint": "income_vip",
                "params": {"period": "20241231", "report_type": "1"},
                "fields": list(_FIELDS["income_vip"]),
                "canonical_symbols": ["000001.SZ"]}
        with tempfile.TemporaryDirectory() as path:
            store, client = LocalStore(path), SourceClient([row])
            options = {"specs": [spec], "client": client, "operation_id": "validator-fix",
                       "plan_fingerprint": "plan-fix", "identity_map": IDENTITY,
                       "raw_log_offset": 0, "max_attempts": 1,
                       "max_total_requests_per_chunk": 8,
                       "event_calendar_map": CALENDAR,
                       "global_calls_per_minute": 0, "stock_basic_calls_per_minute": 0}
            with patch("axiom_data.event_sources._response_issue", return_value="old rule"):
                with self.assertRaises(CoverageError):
                    run_batch_chunk(store, **options)
            failed = store.find_raw_by_operation("validator-fix")[0]
            self.assertEqual(failed["status"], "failed")
            result = run_batch_chunk(store, **options)
            self.assertEqual(len(client.calls), 1)
            corrected = store.get_raw(result["raw_batch_ids"][0])
            self.assertEqual(corrected["request"]["revalidated_from_batch_id"], failed["batch_id"])
            self.assertEqual(corrected["observed_at"], failed["observed_at"])
            self.assertEqual(corrected["payload_sha256"], failed["payload_sha256"])

    def test_same_disclosure_selects_returned_dominating_row(self):
        from axiom_data.event_sources import _financial_unique
        partial = {"ts_code": "600000.SH", "ann_date": "20260828",
                   "end_date": "20260630", "roe": 3.7501,
                   "roe_waa": None, "debt_to_assets": 91.9207}
        complete = {**partial, "roe_waa": 3.96}
        self.assertEqual(_financial_unique("fina_indicator", [partial, complete]), [complete])
        with self.assertRaisesRegex(Exception, "conflicting or complementary"):
            _financial_unique("fina_indicator", [
                {**partial, "roe_waa": 3.96, "debt_to_assets": None},
                {**partial, "roe_waa": None, "debt_to_assets": 91.9207}])

    def test_plan_freezes_warmup_and_rejects_changed_calendar(self):
        market = plan_bulk_job(mode="bulk", symbols=["000001.SZ"],
                               identity_map=IDENTITY, start_session="2026-09-01",
                               end_session="2026-09-01", endpoints=("trade_cal",))
        plan = plan_full_sources(market=market, calendar_end="2026-10-01")
        self.assertEqual(plan.event_start, "2025-04-01")
        self.assertEqual(len(plan.event_endpoints), 7)
        self.assertEqual(FullSourcePlan.from_dict(plan.to_dict()), plan)
        bad = plan.to_dict()
        bad["event_start"] = "2026-01-01"
        with self.assertRaisesRegex(Exception, "lookback"):
            FullSourcePlan.from_dict(bad)

    def test_vip_all_market_raw_selects_scope_and_orders_announced_versions(self):
        rows = [{"ts_code": "000001.SZ", "ann_date": "20250329",
                 "f_ann_date": "20250329", "end_date": "20241231",
                 "report_type": "1", "total_revenue": 100.0,
                 "n_income_attr_p": 10.0},
                {"ts_code": "000001.SZ", "ann_date": "20250329",
                 "f_ann_date": "20250401", "end_date": "20241231",
                 "report_type": "1", "total_revenue": 120.0,
                 "n_income_attr_p": 12.0},
                {"ts_code": "000002.SZ", "ann_date": "20250329",
                 "f_ann_date": "20250329", "end_date": "20241231",
                 "report_type": "1", "total_revenue": 999.0,
                 "n_income_attr_p": 99.0}]
        spec = {"endpoint": "income_vip",
                "params": {"period": "20241231", "report_type": "1"},
                "fields": list(_FIELDS["income_vip"]),
                "canonical_symbols": ["000001.SZ"]}
        with tempfile.TemporaryDirectory() as path:
            store = LocalStore(path)
            fetched = run_batch_chunk(store, specs=[spec], client=SourceClient(rows),
                                      operation_id="vip-fetch", plan_fingerprint="job-1",
                                      identity_map=IDENTITY, raw_log_offset=0,
                                      max_total_requests_per_chunk=8,
                                      global_calls_per_minute=100000,
                                      stock_basic_calls_per_minute=100000,
                                      event_calendar_map=CALENDAR)
            raw = store.get_raw(fetched["raw_batch_ids"][0])
            self.assertEqual(len(json.loads(store.read_raw_record(raw))), 3)
            receipt = apply_saved_raw(store, base_snapshot=None,
                                      raw_batch_ids=fetched["raw_batch_ids"],
                                      operation_id="vip-publish", build_context={"test": "vip"})
            query = lambda cutoff: EventQuery("financial_events", ("total_revenue",),
                ("sec-one",), "2024-12-31", "2024-12-31", cutoff,
                "best_effort_vendor_v1", "report_period")
            data = Data(path)
            earlier = data.events(snapshot=receipt.snapshot_id,
                                  query=query("2025-03-31T10:00:00+08:00"))
            later = data.events(snapshot=receipt.snapshot_id,
                                query=query("2025-04-02T10:00:00+08:00"))
            self.assertEqual(earlier.frame.iloc[0]["total_revenue"], 100)
            self.assertEqual(later.frame.iloc[0]["total_revenue"], 120)
            self.assertEqual(later.field_meta["total_revenue"]["unit"], "CNY")

    def test_holder_report_replaces_whole_group_without_old_member_leak(self):
        def report(names):
            return [{"ts_code": "000001.SZ", "ann_date": "20250329",
                     "end_date": "20241231", "holder_name": name,
                     "hold_amount": i + 100, "hold_ratio": 1.0}
                    for i, name in enumerate(names)]
        first_names = [f"old-{i}" for i in range(10)]
        second_names = ["new-0", *first_names[1:]]
        with tempfile.TemporaryDirectory() as path:
            store = LocalStore(path)
            params = {"ts_code": "000001.SZ", "period": "20241231"}
            a = collect_event_response(store, client=SourceClient(report(first_names)),
                endpoint="top10_holders", params=params, identity_map=IDENTITY,
                operation_id="holders-old", observed_at=datetime(2025, 4, 1, tzinfo=timezone.utc),
                next_open_session_by_date=CALENDAR)
            s1 = apply_saved_raw(store, base_snapshot=None, raw_batch_ids=[a["batch_id"]],
                                 operation_id="holders-pub-1", build_context={"test": "group"})
            b = collect_event_response(store, client=SourceClient(report(second_names)),
                endpoint="top10_holders", params=params, identity_map=IDENTITY,
                operation_id="holders-new", observed_at=datetime(2025, 4, 2, tzinfo=timezone.utc),
                next_open_session_by_date=CALENDAR)
            s2 = apply_saved_raw(store, base_snapshot=s1.snapshot_id, raw_batch_ids=[b["batch_id"]],
                                 operation_id="holders-pub-2", build_context={"test": "group"})
            query = EventQuery("top_holders_reports", ("holders", "holder_count", "top10_ratio"),
                               ("sec-one",), "2024-12-31", "2024-12-31",
                               "2025-04-03T00:00:00+08:00", "operational_pit_v1",
                               "report_period")
            old = Data(path).events(snapshot=s1.snapshot_id, query=query)
            new = Data(path).events(snapshot=s2.snapshot_id, query=query)
            self.assertEqual(old.frame.iloc[0]["group_completeness"], "supplier_report_complete")
            self.assertEqual(new.frame.iloc[0]["holder_count"], 10)
            self.assertEqual(new.frame.iloc[0]["top10_ratio"], 10.0)
            self.assertIn("old-0", old.frame.iloc[0]["holders"])
            self.assertNotIn("old-0", new.frame.iloc[0]["holders"])
            self.assertIn("new-0", new.frame.iloc[0]["holders"])

    def test_full_job_market_and_event_share_one_promotion_and_replay(self):
        market = plan_bulk_job(mode="bulk", symbols=["000001.SZ"],
                               identity_map=IDENTITY, start_session="2026-09-01",
                               end_session="2026-09-01", endpoints=("trade_cal",))
        plan = plan_full_sources(market=market, event_endpoints=("stk_limit",),
                                 calendar_end="2026-10-01")
        with tempfile.TemporaryDirectory() as path:
            store = LocalStore(path)
            client = CalendarLimitClient()
            options = {"plan": plan, "client": client, "operation_id": "full-small",
                       "base_snapshot": None, "min_interval_seconds": 0,
                       "global_calls_per_minute": 100000,
                       "stock_basic_calls_per_minute": 100000,
                       "sleeper": lambda _: None}
            receipt = run_full_sources(store, **options)
            self.assertEqual(store.resolve("current"), receipt.snapshot_id)
            self.assertTrue(verify_full_sources(store, plan=plan,
                                                operation_id="full-small")["verified"])
            before = len(client.calls)
            replay = run_full_sources(store, **options)
            self.assertEqual(replay.snapshot_id, receipt.snapshot_id)
            self.assertEqual(len(client.calls), before)
            self.assertEqual(full_source_status(store, plan=plan,
                                                operation_id="full-small")["status"], "success")



if __name__ == "__main__":
    unittest.main()
