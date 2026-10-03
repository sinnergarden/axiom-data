"""ETF source units, supplier identity and actual-receipt visibility."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from axiom_data.api import Data
from axiom_data.protocols import DataError, EventQuery, IngestBatch, QuerySpec, UpdateRequest
from axiom_data.provider_etf import CONTRACTS, DOMAINS, ETF_SYMBOLS, FIELDS, fund_identity, profile_for
from axiom_data.sources import _rows, normalize_batch
from axiom_data.storage import LocalStore


RECEIPT = datetime(2026, 10, 3, 6, tzinfo=timezone.utc)
CODE = "510300.SH"
IDENTITY = fund_identity(CODE, "20120528")
IDENTITIES = {CODE: IDENTITY, "510880.SH": fund_identity("510880.SH", "20070118")}
PROBE_ROOT = Path(__file__).resolve().parents[2] / "data" / "etf_rotation_source_probe"
PROBE_IDS = {
    "fund_basic": "b_ceac6473cc654e8bbe9062d3fbb74742",
    "fund_daily": "b_8bdc47b7733d48cfa1eb3a94da76c267",
    "fund_adj": "b_a98797ca632043539f96b3acfa8951af",
    "fund_div": "b_0458c14fe2b8449ab7dc7b6a4b398d23",
    "etf_limit": "b_4e5a27f704814f238d89661dc30a5edb",
}


def _batch(endpoint, rows, *, codes=(CODE,), params=None, receipt=RECEIPT):
    params = params or ({"market": "E", "status": "L"} if endpoint == "fund_basic"
                        else {"ts_code": codes[0]})
    return IngestBatch(
        DOMAINS[endpoint], json.dumps(rows, ensure_ascii=False).encode(),
        {"endpoint": endpoint, "params": params, "fields": list(FIELDS[endpoint]),
         "canonical_symbols": list(codes)},
        CONTRACTS[endpoint], profile_for(endpoint, identity_map=IDENTITIES),
        receipt, "etf_records_v1",
    )


class ETFSourceTests(unittest.TestCase):
    def test_identity_uses_supplier_listing_date_and_selected_catalogue(self):
        row = {"ts_code": CODE, "name": "沪深300ETF", "list_date": "20120528",
               "delist_date": "", "status": "L", "market": "E"}
        other = {**row, "ts_code": "999999.SH", "list_date": None}
        result = normalize_batch(_batch("fund_basic", [row, other]),
                                 {"batch_id": "b-basic", "observed_at": RECEIPT})
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["security_id"], IDENTITY)
        self.assertEqual(result[0]["listing_date"], "2012-05-28")
        self.assertIsNone(result[0]["delisting_date"])
        self.assertIsNone(result[0]["source_available_at"])
        self.assertNotEqual(fund_identity(CODE, "20120529"), IDENTITY)
        with self.assertRaises(DataError):
            normalize_batch(_batch("fund_basic", [{**row, "list_date": None}]),
                            {"batch_id": "b-missing-date"})

    def test_daily_units_nullable_fields_and_receipt_cutoff(self):
        source = {"ts_code": CODE, "trade_date": "20260831", "open": 4.64,
                  "high": 4.692, "low": 4.618, "close": None,
                  "pre_close": 4.679, "vol": 6731264.94, "amount": 3134489.338}
        batch = _batch("fund_daily", [source],
                       params={"ts_code": CODE, "start_date": "20260831",
                               "end_date": "20260831"})
        row, = normalize_batch(batch, {"batch_id": "b-daily", "observed_at": RECEIPT})
        self.assertIsNone(row["close"])
        self.assertEqual(row["volume_units"], 673126494)
        self.assertEqual(row["amount_cny"], 3134489338.0)
        self.assertIsNone(row["source_available_at"])
        with tempfile.TemporaryDirectory() as root:
            result = Data(root).update(
                base_snapshot=None,
                request=UpdateRequest((batch,), "etf-daily", {"scope": "ETF test"}, False),
            )
            for cutoff, expected in (("2026-10-03T05:59:59Z", False),
                                     ("2026-10-03T06:00:01Z", True)):
                query = QuerySpec("market_daily", ("open", "volume_units"),
                                  (IDENTITY,), ("2026-08-31",), "operational_pit_v1",
                                  {"2026-08-31": cutoff})
                answer = Data(root).read(snapshot=result.snapshot_id, query=query).frame
                value = answer.iloc[0]["open"]
                if expected:
                    self.assertEqual(float(value), 4.64)
                else:
                    self.assertTrue(pd.isna(value))

    def test_dividend_duplicate_collapse_preserves_distinct_source_content(self):
        source = {"ts_code": "510880.SH", "ann_date": "20260831",
                  "imp_anndate": "", "div_proc": "实施", "record_date": "20260902",
                  "ex_date": "20260903", "pay_date": "", "div_cash": 0.024}
        batch = _batch("fund_div", [source, source], codes=("510880.SH",),
                       params={"ts_code": "510880.SH"})
        rows = normalize_batch(batch, {"batch_id": "b-div", "observed_at": RECEIPT})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["cash_dividend_per_unit"], 0.024)
        self.assertIsNone(rows[0]["implementation_announcement_date"])
        self.assertIsNone(rows[0]["pay_date"])
        self.assertIsNone(rows[0]["source_available_at"])
        revised = normalize_batch(
            _batch("fund_div", [source, {**source, "pay_date": "20260910"}],
                   codes=("510880.SH",), params={"ts_code": "510880.SH"}),
            {"batch_id": "b-div-revised", "observed_at": RECEIPT},
        )
        self.assertEqual(len(revised), 2)
        self.assertNotEqual(revised[0]["revision_id"], revised[1]["revision_id"])
        with tempfile.TemporaryDirectory() as root:
            snapshot = Data(root).update(
                base_snapshot=None,
                request=UpdateRequest((batch,), "etf-div", {"scope": "ETF test"}, False),
            ).snapshot_id
            for cutoff, expected_count in (("2026-10-03T05:59:59Z", 0),
                                           ("2026-10-03T06:00:01Z", 1)):
                query = EventQuery("corporate_actions", ("cash_dividend_per_unit",),
                                   (IDENTITIES["510880.SH"],), "2026-08-31", "2026-08-31",
                                   cutoff, "operational_pit_v1", "announcement_date")
                self.assertEqual(len(Data(root).events(snapshot=snapshot, query=query).frame),
                                 expected_count)

    def test_adjustment_and_limits_retain_supplier_values(self):
        factor, = normalize_batch(
            _batch("fund_adj", [{"ts_code": CODE, "trade_date": "20260831",
                                 "adj_factor": 1.2671}]),
            {"batch_id": "b-factor", "observed_at": RECEIPT})
        limits, = normalize_batch(
            _batch("etf_limit", [{"ts_code": CODE, "trade_date": "20260831",
                                  "up_limit": 5.419, "down_limit": 4.433}]),
            {"batch_id": "b-limit", "observed_at": RECEIPT})
        self.assertEqual(factor["factor"], 1.2671)
        self.assertEqual((limits["up_limit"], limits["down_limit"]), (5.419, 4.433))

    @unittest.skipUnless(PROBE_ROOT.exists(), "saved ETF supplier probe is unavailable")
    def test_saved_supplier_responses_match_declared_layout_and_duplicate_count(self):
        store = LocalStore(PROBE_ROOT)
        records = store.get_raw_many(list(PROBE_IDS.values()))
        basic = _rows(store.read_raw_record(records[PROBE_IDS["fund_basic"]]))
        selected = [row for row in basic if row["ts_code"] in ETF_SYMBOLS]
        self.assertEqual(len(selected), 7)
        identities = {row["ts_code"]: fund_identity(row["ts_code"], row["list_date"])
                      for row in selected}
        expected = {"fund_basic": (2251, 7), "fund_daily": (65, 65),
                    "fund_adj": (65, 65), "fund_div": (32, 19),
                    "etf_limit": (5, 5)}
        for endpoint, (raw_count, canonical_count) in expected.items():
            with self.subTest(endpoint=endpoint):
                record = records[PROBE_IDS[endpoint]]
                payload = store.read_raw_record(record)
                source_rows = _rows(payload)
                self.assertEqual(len(source_rows), raw_count)
                self.assertEqual(set(FIELDS[endpoint]), set(source_rows[0]))
                request = dict(record["request"])
                request["canonical_symbols"] = list(ETF_SYMBOLS)
                batch = IngestBatch(DOMAINS[endpoint], payload, request,
                                    CONTRACTS[endpoint],
                                    profile_for(endpoint, identity_map=identities),
                                    record["observed_at"], "etf_records_v1")
                canonical = normalize_batch(batch, record)
                self.assertEqual(len(canonical), canonical_count)
                self.assertTrue(all(row["source_available_at"] is None for row in canonical))


if __name__ == "__main__":
    unittest.main()
