"""Current bulk runner invariants; all supplier replies are synthetic."""

import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

from axiom_data.bulk_jobs import (BulkJobPlan, estimate_bulk_job, plan_bulk_job,
                                   run_bulk_job, verify_bulk_job)
from axiom_data.protocols import ConflictError, DataError
from axiom_data.storage import LocalStore

OBS = datetime(2026, 9, 28, 12, tzinfo=timezone.utc)
IDS = {"000001.SZ": "sec-1", "000002.SZ": "sec-2"}


class Client:
    def __init__(self, *, interrupt_daily=False, close=10):
        self.calls = []
        self.interrupt_daily = interrupt_daily
        self.close = close

    def query(self, endpoint, **params):
        self.calls.append((endpoint, dict(params)))
        if endpoint == "trade_cal":
            first = date.fromisoformat(params["start_date"][:4] + "-" + params["start_date"][4:6] + "-" + params["start_date"][6:])
            last = date.fromisoformat(params["end_date"][:4] + "-" + params["end_date"][4:6] + "-" + params["end_date"][6:])
            days = [(first + timedelta(days=offset)).strftime("%Y%m%d")
                    for offset in range((last - first).days + 1)]
            return [{"exchange": params["exchange"], "cal_date": day,
                     "is_open": "1" if day in {"20200102", "20200203"} else "0"}
                    for day in days]
        if endpoint == "daily":
            if self.interrupt_daily:
                self.interrupt_daily = False
                raise KeyboardInterrupt()
            return [{"ts_code": code, "trade_date": params["trade_date"],
                     "open": 10, "high": 11, "low": 9, "close": self.close,
                     "pre_close": 9, "vol": 1, "amount": 1}
                    for code in IDS]
        raise AssertionError(endpoint)


def job(symbols=("000001.SZ",), *, start="2020-01-02", end="2020-01-02", ids=IDS):
    return plan_bulk_job(mode="bulk", symbols=symbols, identity_map=ids,
                         start_session=start, end_session=end,
                         endpoints=["trade_cal", "daily"], max_requests_per_chunk=4)


def run(store, plan, client, operation_id, base_snapshot=None):
    return run_bulk_job(store, plan=plan, client=client, operation_id=operation_id,
                        base_snapshot=base_snapshot, max_attempts=1, max_workers=1,
                        min_interval_seconds=0, global_calls_per_minute=0,
                        stock_basic_calls_per_minute=0,
                        clock=lambda: OBS + timedelta(seconds=client.close - 10))


class BulkJobTests(unittest.TestCase):
    def test_strict_v2_plan_and_count(self):
        plan = job()
        self.assertEqual(BulkJobPlan.from_dict(json.loads(json.dumps(plan.to_dict()))), plan)
        self.assertEqual(plan.to_dict()["schema_version"], "local_bulk_job_v2")
        self.assertEqual(estimate_bulk_job(plan)["requests"], 3)
        with self.assertRaises(DataError):
            BulkJobPlan.from_dict({**plan.to_dict(), "unreviewed": True})
        with self.assertRaises(DataError):
            BulkJobPlan.from_dict({**plan.to_dict(), "schema_version": "local_bulk_job_v1"})
        with self.assertRaises(DataError):
            plan_bulk_job(mode="bulk", request_strategy="symbol_month_v1",
                          symbols=["000001.SZ"], identity_map=IDS,
                          start_session="2020-01-02", end_session="2020-01-02")

    def test_interruption_reuses_reference_raw_and_promotes_once(self):
        plan = job(end="2020-02-03")
        with tempfile.TemporaryDirectory() as directory:
            store = LocalStore(directory)
            with self.assertRaises(KeyboardInterrupt):
                run(store, plan, Client(interrupt_daily=True), "resume")
            self.assertFalse((store.root / "current.json").exists())
            self.assertEqual(store.read_operation("resume")["next_chunk"], 1)
            resume = Client()
            result = run(store, plan, resume, "resume")
            self.assertEqual([name for name, _ in resume.calls], ["daily", "daily"])
            self.assertEqual(store.resolve("current"), result.snapshot_id)
            replay = Client()
            self.assertEqual(run(store, plan, replay, "resume").snapshot_id, result.snapshot_id)
            self.assertEqual(replay.calls, [])
            self.assertTrue(verify_bulk_job(store, plan=plan, operation_id="resume")["verified"])

    def test_current_conflict_does_not_publish_stale_candidate(self):
        plan = job()
        with tempfile.TemporaryDirectory() as directory:
            store = LocalStore(directory)
            first = run(store, plan, Client(), "first")
            other = run(store, plan, Client(close=12), "other", first.snapshot_id)
            with self.assertRaises(ConflictError):
                run(store, plan, Client(close=13), "stale", first.snapshot_id)
            self.assertEqual(store.resolve("current"), other.snapshot_id)
            self.assertEqual(store.read_operation("stale")["status"], "failed")

    def test_identity_map_append_only_and_remap_rejected(self):
        first_plan = job(ids={"000001.SZ": "sec-1"})
        second_plan = job(symbols=("000002.SZ",), start="2020-02-03", end="2020-02-03")
        with tempfile.TemporaryDirectory() as directory:
            store = LocalStore(directory)
            first = run(store, first_plan, Client(), "initial")
            second = run(store, second_plan, Client(), "extended", first.snapshot_id)
            previous = store.load_snapshot(first.snapshot_id)["domains"]["market_daily"]
            current = store.load_snapshot(second.snapshot_id)["domains"]["market_daily"]
            self.assertEqual(previous["source_profile"]["identity_map"], {"000001.SZ": "sec-1"})
            self.assertEqual(current["source_profile"]["identity_map"], IDS)
            remap = job(ids={"000001.SZ": "changed", "000002.SZ": "sec-2"})
            with self.assertRaises(ConflictError):
                run(store, remap, Client(), "remap", second.snapshot_id)
            self.assertEqual(store.resolve("current"), second.snapshot_id)


if __name__ == "__main__":
    unittest.main()
