"""Synthetic v2 whole-market batching; no supplier credentials or network."""

import tempfile
import time
import unittest

from axiom_data.bulk_jobs import (BulkJobPlan, bulk_job_status, estimate_bulk_job,
                                   plan_bulk_job, run_bulk_job, verify_bulk_job)
from axiom_data.protocols import CoverageError
from axiom_data.storage import LocalStore


IDS = {"000001.SZ": "sec-1", "600000.SH": "sec-2"}


def daily(code, day="20200102"):
    return {"ts_code": code, "trade_date": day, "open": 10, "high": 11,
            "low": 9, "close": 10, "pre_close": 9, "vol": 1, "amount": 1}


class WholeMarket:
    def __init__(self, *, omit_calendar_day=None, cap_daily=False,
                 fail_child=None, interrupt_daily=False):
        self.calls = []
        self.omit_calendar_day = omit_calendar_day
        self.cap_daily = cap_daily
        self.fail_child = fail_child
        self.interrupt_daily = interrupt_daily

    def query(self, endpoint, **params):
        self.calls.append((endpoint, dict(params)))
        if endpoint == "trade_cal":
            days = ("20200101", "20200102", "20200103")
            return [{"exchange": params["exchange"], "cal_date": day,
                     "is_open": "1" if day == "20200102" else "0"}
                    for day in days if day != self.omit_calendar_day]
        if endpoint == "stock_basic":
            if params["list_status"] != "L":
                return []
            code = "600000.SH" if params["exchange"] == "SSE" else "000001.SZ"
            return [{"ts_code": code, "exchange": params["exchange"],
                     "list_status": "L", "list_date": "19900101", "delist_date": None}]
        if endpoint == "index_daily":
            return [{"ts_code": params["ts_code"], "trade_date": "20200102", "close": 4000}]
        if endpoint == "index_weight":
            return [{"index_code": params["index_code"], "con_code": "000001.SZ",
                     "trade_date": "20200102", "weight": 1.5}]
        if endpoint == "daily":
            if self.interrupt_daily:
                self.interrupt_daily = False
                raise KeyboardInterrupt()
            if self.cap_daily and "ts_code" not in params:
                return [{}] * 6000
            if self.fail_child is not None and params.get("ts_code") == self.fail_child:
                raise RuntimeError("synthetic supplier refusal")
            if "ts_code" in params:
                if params["ts_code"] == "600000.SH":
                    time.sleep(0.03)
                return [daily(params["ts_code"])]
            return [daily("000001.SZ"), daily("600000.SH"), daily("999999.SZ")]
        if endpoint == "adj_factor":
            return [{"ts_code": code, "trade_date": "20200102", "adj_factor": 1.0}
                    for code in ("000001.SZ", "600000.SH", "999999.SZ")]
        if endpoint == "suspend_d":
            return [{"ts_code": "000001.SZ", "trade_date": "20200102",
                     "suspend_timing": "", "suspend_type": "S"}]
        raise AssertionError(endpoint)


def v2_plan(endpoints):
    return plan_bulk_job(mode="bulk", symbols=list(IDS), identity_map=IDS,
                         start_session="2020-01-01", end_session="2020-01-03",
                         benchmark_codes=["000300.SH"], index_codes=["000300.SH"],
                         endpoints=endpoints)


def run(store, plan, client, operation_id="batch-v2", **kwargs):
    return run_bulk_job(store, plan=plan, client=client, operation_id=operation_id,
                        base_snapshot=None, max_workers=kwargs.pop("max_workers", 4),
                        max_attempts=kwargs.pop("max_attempts", 1),
                        global_calls_per_minute=0, stock_basic_calls_per_minute=0,
                        min_interval_seconds=0, **kwargs)


class BatchJobsV2Test(unittest.TestCase):
    def test_default_v2_scope_filters_only_canonical_rows_and_verifies(self):
        plan = v2_plan(["trade_cal", "stock_basic", "daily", "adj_factor",
                        "suspend_d", "index_daily", "index_weight"])
        self.assertEqual(plan.to_dict()["schema_version"], "local_bulk_job_v2")
        self.assertEqual(BulkJobPlan.from_dict(plan.to_dict()), plan)
        estimate = estimate_bulk_job(plan)
        self.assertEqual(estimate["request_count_basis"],
                         "calendar_day_upper_bound_before_calendar_fetch")
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            store = LocalStore(directory)
            client = WholeMarket()
            result = run(store, plan, client)
            self.assertEqual(len(client.calls), 13)
            status = bulk_job_status(store, plan=plan, operation_id="batch-v2")
            self.assertEqual((status["completed_requests"], status["planned_requests"]), (13, 13))
            self.assertEqual(status["trading_sessions"], 1)
            self.assertEqual(store.resolve("current"), result.snapshot_id)
            market = store.load_snapshot(result.snapshot_id)["domains"]["market_daily"]
            rows = [row for part in market["partitions"] for row in store.read_partition(part).to_pylist()]
            self.assertEqual({row["security_id"] for row in rows}, {"sec-1", "sec-2"})
            raw = store.find_raw_by_operation("batch-v2.v2.c000001")
            daily_raw = [record for record in raw.values() if record["request"]["endpoint"] == "daily"][0]
            self.assertIn(b"999999.SZ", store.read_raw_record(daily_raw))
            self.assertEqual(daily_raw["request"]["canonical_symbols"], sorted(IDS))
            report = verify_bulk_job(store, plan=plan, operation_id="batch-v2")
            self.assertTrue(report["verified"])
            self.assertEqual(report["snapshot_id"], result.snapshot_id)

    def test_missing_calendar_date_fails_closed_before_day_requests(self):
        plan = v2_plan(["trade_cal", "daily"])
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            store = LocalStore(directory)
            client = WholeMarket(omit_calendar_day="20200103")
            with self.assertRaisesRegex(CoverageError, "calendar lacks"):
                run(store, plan, client)
            self.assertFalse((store.root / "current.json").exists())
            self.assertFalse(any(endpoint == "daily" for endpoint, _ in client.calls))

    def test_capped_day_splits_to_selected_symbols_and_accounts_all_calls(self):
        plan = v2_plan(["trade_cal", "daily"])
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            store = LocalStore(directory)
            client = WholeMarket(cap_daily=True)
            result = run(store, plan, client)
            calls = [params for endpoint, params in client.calls if endpoint == "daily"]
            self.assertEqual(len(calls), 3)
            self.assertEqual({p.get("ts_code") for p in calls[1:]}, set(IDS))
            status = bulk_job_status(store, plan=plan, operation_id="batch-v2")
            self.assertEqual((status["completed_requests"], status["planned_requests"]), (5, 5))
            raw = store.find_raw_by_operation("batch-v2.v2.c000001")
            self.assertEqual(sum(record["status"] == "cap" for record in raw.values()), 1)
            self.assertEqual(store.resolve("current"), result.snapshot_id)

    def test_inflight_response_is_logged_when_sibling_request_fails(self):
        plan = v2_plan(["trade_cal", "daily"])
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            store = LocalStore(directory)
            client = WholeMarket(cap_daily=True, fail_child="000001.SZ")
            with self.assertRaises(CoverageError):
                run(store, plan, client, max_workers=2)
            records = store.find_raw_by_operation("batch-v2.v2.c000001")
            self.assertEqual(sorted(raw["status"] for raw in records.values()),
                             ["cap", "failed", "success"])
            self.assertFalse((store.root / "current.json").exists())

    def test_cap_split_respects_fixed_request_bound(self):
        plan = plan_bulk_job(mode="bulk", symbols=list(IDS), identity_map=IDS,
                             start_session="2020-01-01", end_session="2020-01-03",
                             endpoints=["trade_cal", "daily"],
                             max_requests_per_chunk=2)
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            store = LocalStore(directory)
            with self.assertRaisesRegex(CoverageError, "fixed chunk request limit"):
                run(store, plan, WholeMarket(cap_daily=True))
            self.assertFalse((store.root / "current.json").exists())

    def test_interrupt_resumes_without_repeating_reference(self):
        plan = v2_plan(["trade_cal", "daily"])
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            store = LocalStore(directory)
            client = WholeMarket(interrupt_daily=True)
            with self.assertRaises(KeyboardInterrupt):
                run(store, plan, client, max_workers=1)
            self.assertFalse((store.root / "current.json").exists())
            resume = WholeMarket()
            result = run(store, plan, resume, max_workers=1)
            self.assertEqual([endpoint for endpoint, _ in resume.calls], ["daily"])
            self.assertEqual(store.resolve("current"), result.snapshot_id)


if __name__ == "__main__":
    unittest.main()
