"""ETF jobs retain supplier Raw across failures, repeats and offline replay."""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta, timezone
from threading import Lock
import tempfile
import unittest

from axiom_data.etf_jobs import (audit_etf_snapshot, etf_job_status,
                                 plan_etf_job, prepare_etf_references,
                                 run_etf_job, verify_etf_job)
from axiom_data.protocols import DataError
from axiom_data.storage import LocalStore
from axiom_data.updates import rebuild_from_raw


CODE = "510300.SH"
RECEIPT = datetime(2026, 10, 3, 6, tzinfo=timezone.utc)
OPTIONS = {"max_attempts": 1, "max_workers": 2,
           "global_calls_per_minute": 0, "stock_basic_calls_per_minute": 0}


class FakeClient:
    """Supplier-shaped responses; one configured endpoint can fail once."""

    def __init__(self):
        self.calls = Counter()
        self.fail_once_endpoint = None
        self.lock = Lock()

    def query(self, endpoint, *, fields, **params):
        with self.lock:
            self.calls[endpoint] += 1
            fail = self.fail_once_endpoint == endpoint
            if fail:
                self.fail_once_endpoint = None
        if fail:
            raise RuntimeError("temporary source outage")
        if endpoint == "fund_basic":
            return ([{"ts_code": CODE, "name": "沪深300ETF", "list_date": "20120528",
                      "delist_date": None, "status": "L", "market": "E"}]
                    if params["status"] == "L" else [])
        if endpoint == "trade_cal":
            first = date.fromisoformat(params["start_date"][:4] + "-" +
                                      params["start_date"][4:6] + "-" +
                                      params["start_date"][6:])
            last = date.fromisoformat(params["end_date"][:4] + "-" +
                                     params["end_date"][4:6] + "-" +
                                     params["end_date"][6:])
            return [{"exchange": params["exchange"], "cal_date": day.strftime("%Y%m%d"),
                     "is_open": "1" if day.weekday() < 5 else "0"}
                    for day in (first + timedelta(days=offset)
                                for offset in range((last - first).days + 1))]
        if endpoint == "fund_daily":
            return [{"ts_code": CODE, "trade_date": "20260831", "open": 4.64,
                     "high": 4.692, "low": 4.618, "close": 4.685,
                     "pre_close": 4.679, "vol": 6731264.94, "amount": 3134489.338}]
        if endpoint == "fund_adj":
            return [{"ts_code": CODE, "trade_date": "20260831", "adj_factor": 1.2671}]
        if endpoint == "etf_limit":
            return [{"ts_code": CODE, "trade_date": "20260831",
                     "up_limit": 5.419, "down_limit": 4.433}]
        if endpoint == "fund_div":
            row = {"ts_code": CODE, "ann_date": "20260828", "imp_anndate": "20260828",
                   "div_proc": "实施", "record_date": "20260831", "ex_date": "20260901",
                   "pay_date": "20260902", "div_cash": 0.024}
            return [row, row]
        if endpoint == "suspend_d":
            return []
        if endpoint == "index_daily":
            return [{"ts_code": params["ts_code"], "trade_date": "20260831",
                     "close": 4500.0}]
        raise AssertionError(f"unexpected ETF endpoint {endpoint}")


def _prepared(store, client, operation_id, *, base_snapshot=None, receipt=RECEIPT):
    scope = prepare_etf_references(
        store, client=client, start_session="2026-08-31", end_session="2026-08-31",
        operation_id=operation_id, symbols=(CODE,), benchmark_codes=("000300.SH",),
        mode="bulk", base_snapshot=base_snapshot, max_requests_per_chunk=8,
        clock=lambda: receipt, **OPTIONS,
    )
    scope["max_requests_per_chunk"] = 2  # Exercise a committed chunk before failure.
    return plan_etf_job(**{key: value for key, value in scope.items()
                           if key != "schema_version"})


def _run(store, client, plan, operation_id, *, base_snapshot=None, receipt=RECEIPT):
    return run_etf_job(store, plan=plan, client=client, operation_id=operation_id,
                       base_snapshot=base_snapshot, promote=True,
                       clock=lambda: receipt, **OPTIONS)


def _domain_fingerprint(store, snapshot_id):
    domains = store.load_snapshot(snapshot_id)["domains"]
    return {name: (domain["contract"],
                   [(part["partition"], part["file_sha256"], part["rows"])
                    for part in domain["partitions"]])
            for name, domain in domains.items()}


class ETFJobTests(unittest.TestCase):
    def test_failure_resume_repeat_nochange_and_offline_rebuild(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalStore(root)
            client = FakeClient()
            plan = _prepared(store, client, "etf-prepare")
            self.assertEqual(set(dict(plan.identity_map)), {CODE})
            self.assertGreaterEqual(client.calls["trade_cal"], 2)
            self.assertIsNone(store.read_operation("etf-run"))
            first = dict(client.calls)
            client.fail_once_endpoint = "etf_limit"
            with self.assertRaises(DataError):
                _run(store, client, plan, "etf-run")
            failed = etf_job_status(store, plan=plan, operation_id="etf-run")
            self.assertEqual(failed["status"], "failed")
            self.assertGreater(failed["next_chunk"], 0)
            self.assertFalse((store.root / "current.json").exists())
            self.assertGreater(client.calls["fund_daily"], first.get("fund_daily", 0))

            before_resume = dict(client.calls)
            published = _run(store, client, plan, "etf-run")
            self.assertEqual(store.resolve("current"), published.snapshot_id)
            self.assertEqual(etf_job_status(store, plan=plan,
                                             operation_id="etf-run")["status"], "success")
            self.assertEqual(client.calls["fund_daily"], before_resume["fund_daily"])
            self.assertEqual(client.calls["fund_adj"], before_resume["fund_adj"])
            self.assertTrue(verify_etf_job(store, plan=plan,
                                           operation_id="etf-run")["verified"])
            audit = audit_etf_snapshot(store, snapshot_id=published.snapshot_id, plan=plan)
            # Sparse fake bars legitimately leave open calendar dates unknown.
            self.assertEqual(audit["status"], "limited")
            self.assertGreater(audit["missing_market_cells"], 0)
            self.assertEqual(audit["source_mapping_checks"]["market_daily"], 1)
            self.assertEqual(audit["source_mapping_checks"]["corporate_actions"], 1)

            completed_calls = dict(client.calls)
            completed = _run(store, client, plan, "etf-run")
            self.assertEqual(completed.snapshot_id, published.snapshot_id)
            self.assertEqual(dict(client.calls), completed_calls)

            prior_fingerprint = _domain_fingerprint(store, published.snapshot_id)
            prior_raw_count = len((store.root / "raw" / "fetches.jsonl").read_text().splitlines())
            next_plan = _prepared(store, client, "etf-prepare-repeat",
                                  base_snapshot=published.snapshot_id,
                                  receipt=RECEIPT + timedelta(days=1))
            repeated = _run(store, client, next_plan, "etf-run-repeat",
                            base_snapshot=published.snapshot_id,
                            receipt=RECEIPT + timedelta(days=1))
            self.assertFalse(repeated.changed)
            self.assertEqual(repeated.snapshot_id, published.snapshot_id)
            self.assertEqual(_domain_fingerprint(store, repeated.snapshot_id), prior_fingerprint)
            self.assertGreater(len((store.root / "raw" / "fetches.jsonl").read_text().splitlines()),
                               prior_raw_count)

            manifest = store.load_snapshot(published.snapshot_id)
            ids = list(dict.fromkeys(raw for domain in manifest["domains"].values()
                                     for raw in domain["raw_batch_ids"]))
            replay = rebuild_from_raw(
                store, base_snapshot=published.snapshot_id, raw_batch_ids=ids,
                domains=list(manifest["domains"]), operation_id="etf-rebuild",
                build_context={"test": "ETF offline replay"}, promote=False)
            self.assertEqual(_domain_fingerprint(store, replay.snapshot_id), prior_fingerprint)


if __name__ == "__main__":
    unittest.main()
