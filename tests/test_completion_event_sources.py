"""Injected Tushare event adapters through Raw, Snapshot and public reads."""

import json
import tempfile
import unittest
from datetime import datetime, timezone

from axiom_data.api import Data
from axiom_data.event_sources import collect_event_response
from axiom_data.financial import ttm
from axiom_data.protocols import CoverageError, EventQuery, QuerySpec
from axiom_data.storage import LocalStore
from axiom_data.updates import apply_saved_raw


IDS = {"000001.SZ": "sec-bank"}
OBS = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)


class Client:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def query(self, endpoint, **params):
        self.calls.append((endpoint, params))
        result = next(self.responses)
        if isinstance(result, Exception):
            raise result
        return result


def income(period, amount, announcement):
    return {"ts_code": "000001.SZ", "ann_date": announcement,
            "f_ann_date": announcement, "end_date": period, "report_type": "1",
            "total_revenue": amount, "n_income_attr_p": amount / 10,
            "vendor_extra": "kept in Raw"}


class EventSourceTests(unittest.TestCase):
    def test_income_adapter_to_events_and_ttm_with_native_keys(self):
        rows = [income("20190331", 10, "20190420"),
                income("20190630", 30, "20190820"),
                income("20190930", 60, "20191020"),
                income("20191231", 100, "20200420")]
        dates = {"2019-04-20": "2019-04-22", "2019-08-20": "2019-08-21",
                 "2019-10-20": "2019-10-21", "2020-04-20": "2020-04-21"}
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            store = LocalStore(directory)
            client = Client([[row] for row in rows])
            ids = []
            for index, row in enumerate(rows):
                saved = collect_event_response(store, client=client, endpoint="income",
                                               params={"ts_code": "000001.SZ", "period": row["end_date"],
                                                       "report_type": "1"}, identity_map=IDS,
                                               observed_at=OBS, operation_id="income_fetch",
                                               batch_index=index, next_open_session_by_date=dates)
                ids.append(saved["batch_id"])
            receipt = apply_saved_raw(store, base_snapshot=None, raw_batch_ids=ids,
                                      operation_id="income_publish", build_context={"test": "native income"})
            data = Data(directory)
            selected = data.events(snapshot=receipt.snapshot_id, query=EventQuery(
                "financial_events", ("total_revenue",), ("sec-bank",),
                "2019-01-01", "2019-12-31", "2020-05-01T00:00:00+08:00",
                "best_effort_vendor_v1", "report_period", {"report_type": "1"}))
            self.assertEqual(len(selected.frame), 4)
            self.assertEqual(set(selected.frame["endpoint"]), {"income"})
            self.assertEqual(set(selected.frame["report_type"]), {"1"})
            self.assertEqual(selected.field_meta["total_revenue"]["unit"],
                             "CNY")
            result = ttm(selected, field="total_revenue", endpoint="income",
                         report_type="1", basis="cumulative_ytd",
                         unit="CNY", periods=("2019-12-31",))
            self.assertEqual(result.frame.iloc[0]["total_revenue"], 100)
            self.assertEqual(result.field_meta["total_revenue"]["by_key"][0]["status"], "value")
            raw = store.get_raw(ids[0])
            self.assertEqual(json.loads(store.read_raw_record(raw))[0]["vendor_extra"], "kept in Raw")
            self.assertIsNone(store.read_partition(store.load_snapshot(receipt.snapshot_id)["domains"]
                                                   ["financial_events"]["partitions"][0]).to_pylist()[0]
                              ["source_available_at"])

    def test_action_holder_and_limit_remain_native_and_do_not_imply_executability(self):
        next_open = {"2019-04-20": "2019-04-22", "2019-05-20": "2019-05-21"}
        dividend = [
            {"ts_code": "000001.SZ", "end_date": "20181231", "ann_date": "20190420",
             "imp_ann_date": None,
             "div_proc": "预案", "cash_div_tax": 0.5, "stk_bo_rate": 0,
             "stk_co_rate": 0, "record_date": None, "ex_date": None},
            {"ts_code": "000001.SZ", "end_date": "20181231", "ann_date": "20190520",
             "imp_ann_date": "20190520",
             "div_proc": "实施", "cash_div_tax": 0.5, "stk_bo_rate": 0,
             "stk_co_rate": 0, "record_date": "20190601", "ex_date": "20190602"},
        ]
        holder = [{"ts_code": "000001.SZ", "ann_date": "20190420",
                   "end_date": "20181231", "holder_name": "holder A",
                   "hold_amount": 1000, "hold_ratio": 10.0}]
        limit = [{"ts_code": "000001.SZ", "trade_date": "20190603",
                  "up_limit": 11.0, "down_limit": 9.0}]
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            store = LocalStore(directory)
            client = Client([dividend, holder, limit])
            calls = [
                ("dividend", {"ts_code": "000001.SZ"}, next_open),
                ("top10_holders", {"ts_code": "000001.SZ", "period": "20181231"}, next_open),
                ("stk_limit", {"ts_code": "000001.SZ", "trade_date": "20190603"}, None),
            ]
            ids = [collect_event_response(store, client=client, endpoint=endpoint,
                                          params=params, identity_map=IDS,
                                          observed_at=OBS, operation_id="event_fetch",
                                          batch_index=i, next_open_session_by_date=calendar)["batch_id"]
                   for i, (endpoint, params, calendar) in enumerate(calls)]
            receipt = apply_saved_raw(store, base_snapshot=None, raw_batch_ids=ids,
                                      operation_id="event_publish", build_context={"test": "native event"})
            data = Data(directory)
            actions = data.events(snapshot=receipt.snapshot_id, query=EventQuery(
                "corporate_actions", ("cash_dividend_before_tax_per_share", "ex_date"),
                ("sec-bank",), "2018-12-31", "2018-12-31",
                "2019-06-01T00:00:00+08:00", "best_effort_vendor_v1", "report_period"))
            self.assertEqual(set(actions.frame["process_status"]), {"预案", "实施"})
            self.assertIsNone(actions.frame.loc[actions.frame["process_status"] == "预案", "ex_date"].iloc[0])
            holders = data.events(snapshot=receipt.snapshot_id, query=EventQuery(
                "top_holders_reports", ("holder_count", "group_completeness", "top10_ratio"), ("sec-bank",),
                "2018-12-31", "2018-12-31", "2019-05-01T00:00:00+08:00",
                "best_effort_vendor_v1", "report_period"))
            self.assertEqual(holders.frame.iloc[0]["holder_count"], 1)
            self.assertEqual(holders.frame.iloc[0]["group_completeness"], "partial_supplier_report")
            self.assertTrue(holders.frame["top10_ratio"].isna().all())
            price = data.read(snapshot=receipt.snapshot_id, query=QuerySpec(
                "price_limits", ("up_limit", "down_limit"), ("sec-bank",),
                ("2019-06-03",), "best_effort_vendor_v1",
                {"2019-06-03": "2019-06-03T11:00:00+08:00"}))
            self.assertEqual(price.frame.iloc[0]["up_limit"], 11)
            self.assertIn("price bounds only", store.load_snapshot(receipt.snapshot_id)
                          ["domains"]["price_limits"]["source_profile"]["execution_semantics"])

    def test_permission_failure_and_cap_are_logged_not_empty(self):
        next_open = {"2019-04-20": "2019-04-22"}
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            store = LocalStore(directory)
            with self.assertRaises(CoverageError):
                collect_event_response(store, client=Client([RuntimeError("token=secret")]),
                                       endpoint="income", params={"ts_code": "000001.SZ",
                                                                  "period": "20181231", "report_type": "1"},
                                       identity_map=IDS, observed_at=OBS,
                                       operation_id="failed_event", next_open_session_by_date=next_open)
            failure = store.find_raw_by_operation("failed_event")[0]
            self.assertEqual(failure["status"], "failed")
            self.assertNotIn(b"secret", store.read_raw_record(failure))
            capped_row = {"ts_code": "000001.SZ", "trade_date": "20190603",
                          "up_limit": 11, "down_limit": 9}
            with self.assertRaises(CoverageError):
                collect_event_response(store, client=Client([[capped_row] * 5800]),
                                       endpoint="stk_limit", params={"ts_code": "000001.SZ",
                                                                     "trade_date": "20190603"},
                                       identity_map=IDS, observed_at=OBS,
                                       operation_id="capped_event")
            self.assertEqual(store.find_raw_by_operation("capped_event")[0]["status"], "cap")
            self.assertFalse((store.root / "current.json").exists())


if __name__ == "__main__":
    unittest.main()
