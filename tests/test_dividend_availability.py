"""A terminal dividend row cannot precede its implementation announcement."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from axiom_data.api import Data
from axiom_data.event_sources import collect_event_response
from axiom_data.protocols import EventQuery, IngestBatch, UpdateRequest
from axiom_data.provider_etf import CONTRACTS, FIELDS, fund_identity, profile_for
from axiom_data.storage import LocalStore
from axiom_data.updates import apply_saved_raw


RECEIPT = datetime(2026, 10, 3, 6, tzinfo=timezone.utc)
STOCK_ID = "cnstock.000001.SZ.19910403"
ETF_ID = fund_identity("510300.SH", "20120528")
SAVED_STOCK_ROOT = (Path(__file__).resolve().parents[2] / "data" /
                    "csi1800_tushare_only_v4_202606_202608")


def _event_count(root, snapshot, *, symbol, field, policy, cutoff):
    query = EventQuery("corporate_actions", (field,), (symbol,),
                       "2026-05-23", "2026-05-23", cutoff, policy,
                       "announcement_date")
    return len(Data(root).events(snapshot=snapshot, query=query).frame)


class DividendAvailabilityTests(unittest.TestCase):
    def test_stock_final_implementation_uses_later_announcement_then_next_open(self):
        source = {"ts_code": "000001.SZ", "end_date": "20251231",
                  "ann_date": "20260523", "imp_ann_date": "20260605",
                  "div_proc": "实施", "cash_div_tax": 0.5,
                  "stk_bo_rate": 0.0, "stk_co_rate": 0.0,
                  "record_date": "20260611", "ex_date": "20260612"}

        class Client:
            def query(self, endpoint, *, fields, **params):
                assert endpoint == "dividend"
                return [source]

        with tempfile.TemporaryDirectory() as root:
            store = LocalStore(root)
            raw = collect_event_response(
                store, client=Client(), endpoint="dividend",
                params={"ts_code": "000001.SZ", "ann_date": "20260523"},
                identity_map={"000001.SZ": STOCK_ID},
                operation_id="late-stock-implementation", observed_at=RECEIPT,
                next_open_session_by_date={"2026-05-23": "2026-05-26",
                                           "2026-06-05": "2026-06-08"},
            )
            snapshot = apply_saved_raw(
                store, base_snapshot=None, raw_batch_ids=[raw["batch_id"]],
                operation_id="stock-dividend-publish", build_context={"test": "late implementation"},
                promote=False,
            ).snapshot_id
            # Only request the cash field. The implementation date still has
            # to be loaded internally to determine the whole row's visibility.
            for cutoff, expected in (("2026-06-01T12:00:00Z", 0),
                                     ("2026-06-08T01:29:00Z", 0),
                                     ("2026-06-08T01:30:01Z", 1)):
                self.assertEqual(_event_count(root, snapshot, symbol=STOCK_ID,
                                              field="cash_dividend_before_tax_per_share",
                                              policy="best_effort_vendor_v1", cutoff=cutoff), expected)
            self.assertEqual(_event_count(root, snapshot, symbol=STOCK_ID,
                                          field="cash_dividend_before_tax_per_share",
                                          policy="operational_pit_v1",
                                          cutoff="2026-06-08T01:30:01Z"), 0)

    def test_etf_final_implementation_uses_later_date_at_same_day_release(self):
        source = {"ts_code": "510300.SH", "ann_date": "20260523",
                  "imp_anndate": "20260605", "div_proc": "实施",
                  "record_date": "20260611", "ex_date": "20260612",
                  "pay_date": "20260615", "div_cash": 0.024}
        batch = IngestBatch(
            "corporate_actions", json.dumps([source], ensure_ascii=False).encode(),
            {"endpoint": "fund_div", "params": {"ts_code": "510300.SH"},
             "fields": list(FIELDS["fund_div"]), "canonical_symbols": ["510300.SH"]},
            CONTRACTS["fund_div"],
            profile_for("fund_div", identity_map={"510300.SH": ETF_ID}),
            RECEIPT, "etf_records_v1",
        )
        with tempfile.TemporaryDirectory() as root:
            snapshot = Data(root).update(
                base_snapshot=None,
                request=UpdateRequest((batch,), "etf-late-implementation",
                                      {"test": "late implementation"}, False),
            ).snapshot_id
            for cutoff, expected in (("2026-06-01T12:00:00Z", 0),
                                     ("2026-06-05T11:59:59Z", 0),
                                     ("2026-06-05T12:00:01Z", 1)):
                self.assertEqual(_event_count(root, snapshot, symbol=ETF_ID,
                                              field="cash_dividend_per_unit",
                                              policy="best_effort_vendor_v1", cutoff=cutoff), expected)
            self.assertEqual(_event_count(root, snapshot, symbol=ETF_ID,
                                          field="cash_dividend_per_unit",
                                          policy="operational_pit_v1",
                                          cutoff="2026-06-05T12:00:01Z"), 0)

    @unittest.skipUnless(SAVED_STOCK_ROOT.exists(), "saved stock v4 Raw is unavailable")
    def test_saved_supplier_implementation_does_not_reveal_future_dates(self):
        snapshot = Data(SAVED_STOCK_ROOT).resolve("current")
        self.assertEqual(_event_count(SAVED_STOCK_ROOT, snapshot, symbol=STOCK_ID,
                                      field="record_date", policy="best_effort_vendor_v1",
                                      cutoff="2026-06-01T12:00:00Z"), 0)
        self.assertEqual(_event_count(SAVED_STOCK_ROOT, snapshot, symbol=STOCK_ID,
                                      field="record_date", policy="best_effort_vendor_v1",
                                      cutoff="2026-06-08T01:30:01Z"), 1)


if __name__ == "__main__":
    unittest.main()
