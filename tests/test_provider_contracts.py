"""Source value mappings independent of collection orchestration."""
import json
import unittest
from datetime import datetime, timezone
from axiom_data.provider_local import CONTRACTS, profile_for
from axiom_data.sources import normalize_batch
from axiom_data.protocols import IngestBatch

OBS = datetime(2026, 9, 28, 4, 30, tzinfo=timezone.utc)
IDS = {"000001.SZ": "sec-000001-sz"}

class ProviderContractTests(unittest.TestCase):
    def test_reference_value_maps_and_membership_one_day_boundary(self):
        calendar = IngestBatch("trading_calendar", json.dumps([{
            "exchange": "SZSE", "cal_date": "20200102", "is_open": "0"}]).encode(), {},
            CONTRACTS["trade_cal"], profile_for("trade_cal", identity_map=IDS), OBS)
        row, = normalize_batch(calendar, {"batch_id": "raw-calendar"})
        self.assertIs(row["is_open"], False)
        master = IngestBatch("security_master", json.dumps([{
            "ts_code": "000001.SZ", "exchange": "SZSE", "list_status": "D",
            "list_date": "19910403", "delist_date": "20200102"}]).encode(), {},
            CONTRACTS["stock_basic"], profile_for("stock_basic", identity_map=IDS), OBS)
        row, = normalize_batch(master, {"batch_id": "raw-master"})
        self.assertEqual(row["vendor_delist_date"], "2020-01-02")
        self.assertIsNone(row["delisting_date"])
        membership = IngestBatch("universe_membership", json.dumps([{
            "index_code": "000300.SH", "con_code": "000001.SZ",
            "trade_date": "20200131", "weight": 0.5}]).encode(), {},
            CONTRACTS["index_weight"], profile_for("index_weight", identity_map=IDS),
            OBS, "tushare_index_weight_v1")
        row, = normalize_batch(membership, {"batch_id": "raw-membership"})
        self.assertEqual((row["effective_from"], row["effective_to"]),
                         ("2020-01-31", "2020-02-01"))
        self.assertIsNone(row["source_available_at"])
