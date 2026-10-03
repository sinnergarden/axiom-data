"""Saved Tushare stock_basic listing events retain their supplier dates."""

from __future__ import annotations

from datetime import datetime
import json
import tempfile
import unittest

from axiom_data.api import Data
from axiom_data.protocols import EventQuery
from axiom_data.storage import LocalStore
from axiom_data.updates import rebuild_from_raw
from axiom_data.vendor_listing import publish_vendor_listing


def _slice(store, exchange, status, rows, receipt):
    return store.write_raw(
        json.dumps(rows, separators=(",", ":")).encode(), domain="reference_bootstrap",
        request={"endpoint": "stock_basic", "params": {"exchange": exchange,
                                                      "list_status": status}},
        source_profile={"id": "tushare.reference_bootstrap.stock_basic.v1"},
        observed_at=datetime.fromisoformat(receipt),
        status="success" if rows else "empty")["batch_id"]


class VendorListingTests(unittest.TestCase):
    def test_source_dates_receipt_rebuild_and_no_change(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalStore(root)
            ids = []
            for exchange in ("SSE", "SZSE"):
                for status in ("L", "D", "P"):
                    rows = ([{"ts_code": "600001.SH", "exchange": "SSE",
                              "list_status": "D", "list_date": "20100104",
                              "delist_date": "20260131"}]
                            if (exchange, status) == ("SSE", "D") else [])
                    ids.append(_slice(store, exchange, status, rows,
                                      "2026-02-02T09:00:00+00:00"))
            published = publish_vendor_listing(
                store, stock_basic_raw_batch_ids=ids,
                identity_map={"600001.SH": "sec-1"},
                operation_id="listing-first", base_snapshot=None, promote=False)
            snapshot = published["snapshot_id"]
            query = EventQuery("listing_events", ("delisting_date", "vendor_list_status"),
                               ("sec-1",), "2026-01-01", "2026-02-28",
                               "2026-02-02T10:00:00Z", "operational_pit_v1",
                               "event_date", filters={"event_type": "delisting"})
            frame = Data(root).events(snapshot=snapshot, query=query).frame
            self.assertEqual(len(frame), 1)
            self.assertEqual(str(frame.iloc[0]["delisting_date"]), "2026-01-31")
            self.assertEqual(frame.iloc[0]["vendor_list_status"], "D")
            too_early = EventQuery("listing_events", ("delisting_date",), ("sec-1",),
                                   "2026-01-01", "2026-02-28", "2026-02-02T08:59:00Z",
                                   "operational_pit_v1", "event_date",
                                   filters={"event_type": "delisting"})
            self.assertTrue(Data(root).events(snapshot=snapshot, query=too_early).frame.empty)
            repeated = publish_vendor_listing(
                store, stock_basic_raw_batch_ids=ids,
                identity_map={"600001.SH": "sec-1"},
                operation_id="listing-repeat", base_snapshot=snapshot, promote=False)
            self.assertFalse(repeated["changed"])
            self.assertEqual(repeated["snapshot_id"], snapshot)
            rebuilt = rebuild_from_raw(
                store, base_snapshot=snapshot, raw_batch_ids=ids,
                domains=["listing_events"], operation_id="listing-rebuild",
                build_context={"test": "listing-rebuild"}, promote=False)
            original = store.load_snapshot(snapshot)["domains"]["listing_events"]
            replay = store.load_snapshot(rebuilt.snapshot_id)["domains"]["listing_events"]
            self.assertEqual(original["partitions"], replay["partitions"])
            self.assertEqual(original["coverage"], replay["coverage"])


if __name__ == "__main__":
    unittest.main()
