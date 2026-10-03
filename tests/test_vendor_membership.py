"""Tushare-only dated membership and its Raw replay boundaries."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import tempfile
import unittest

from axiom_data.api import Data
from axiom_data.protocols import QuerySpec
from axiom_data.storage import LocalStore
from axiom_data.updates import rebuild_from_raw
from axiom_data.vendor_membership import publish_vendor_membership


def _raw(store, code, day, members, receipt, *, status="success"):
    rows = [{"index_code": code, "con_code": symbol,
             "trade_date": day.replace("-", ""), "weight": 1.0}
            for symbol in members]
    return store.write_raw(
        json.dumps(rows, separators=(",", ":")).encode(), domain="reference_bootstrap",
        request={"endpoint": "index_weight", "params": {
            "index_code": code, "start_date": day.replace("-", ""),
            "end_date": day.replace("-", "")}},
        source_profile={"id": "tushare.reference_bootstrap.index_weight.v1"},
        observed_at=datetime.fromisoformat(receipt), status=status)["batch_id"]


class VendorMembershipTests(unittest.TestCase):
    def test_supplier_date_carries_forward_without_backfill_or_empty_clear(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalStore(root)
            first = [
                _raw(store, "000300.SH", "2026-01-31", ["A.SH"], "2026-02-01T10:00:00+00:00"),
                _raw(store, "000905.SH", "2026-01-31", ["B.SH"], "2026-02-01T10:01:00+00:00"),
                _raw(store, "000852.SH", "2026-01-31", ["C.SH"], "2026-02-01T10:02:00+00:00"),
                _raw(store, "000300.SH", "2026-02-15", [], "2026-02-16T10:00:00+00:00", status="empty"),
            ]
            published = publish_vendor_membership(
                store, index_weight_raw_batch_ids=first, identity_map={},
                verified_through="2026-02-28", operation_id="vendor-first", base_snapshot=None,
                promote=False)
            self.assertEqual(published["complete_state_count"], 4)
            domain = store.load_snapshot(published["snapshot_id"])["domains"]["universe_membership"]
            self.assertEqual(len(domain["coverage"]["warnings"]), 3)
            def read(snapshot, day, cutoff, policy="best_effort_vendor_v1"):
                query = QuerySpec("universe_membership", ("is_member",), ("A.SH", "D.SH"),
                                  (day,), policy, {day: cutoff}, universe_id="csi1800")
                return Data(root).members(snapshot=snapshot, query=query).frame["is_member"].tolist()
            snapshot = published["snapshot_id"]
            self.assertTrue(all(value is None or str(value) == "<NA>" for value in
                                read(snapshot, "2026-01-30", "2026-03-01T00:00:00Z")))
            self.assertEqual(read(snapshot, "2026-02-03", "2026-03-01T00:00:00Z"), [True, False])
            dated = Data(root).members(snapshot=snapshot, query=QuerySpec(
                "universe_membership", ("is_member",), ("A.SH",), ("2026-02-03",),
                "best_effort_vendor_v1", {"2026-02-03": "2026-03-01T00:00:00Z"},
                universe_id="csi1800"))
            self.assertEqual(dated.field_meta["is_member"]["by_key"][0]["source_snapshot_date"],
                             "2026-01-31")
            self.assertTrue(all(value is None or str(value) == "<NA>" for value in
                                read(snapshot, "2026-02-03", "2026-02-01T09:59:00Z",
                                     "operational_pit_v1")))
            self.assertEqual(read(snapshot, "2026-02-20", "2026-03-01T00:00:00Z"), [True, False])
            repeated = publish_vendor_membership(
                store, index_weight_raw_batch_ids=first, identity_map={},
                verified_through="2026-02-28", operation_id="vendor-repeat",
                base_snapshot=snapshot, promote=False)
            self.assertFalse(repeated["changed"])
            self.assertEqual(repeated["snapshot_id"], snapshot)

            second = [
                _raw(store, "000300.SH", "2026-02-28", ["D.SH"], "2026-03-02T10:00:00+00:00"),
                _raw(store, "000905.SH", "2026-02-28", ["B.SH"], "2026-03-02T10:01:00+00:00"),
                _raw(store, "000852.SH", "2026-02-28", ["C.SH"], "2026-03-02T10:02:00+00:00"),
            ]
            newer = publish_vendor_membership(
                store, index_weight_raw_batch_ids=[*first, *second], identity_map={},
                verified_through="2026-03-31", operation_id="vendor-second",
                base_snapshot=snapshot, promote=False)
            self.assertEqual(read(newer["snapshot_id"], "2026-02-27",
                                  "2026-04-01T00:00:00Z"), [True, False])
            self.assertEqual(read(newer["snapshot_id"], "2026-03-03",
                                  "2026-04-01T00:00:00Z"), [False, True])
            # Asia/Shanghai 09:30 and 16:00 on a new supplier date still
            # inherit January's released group; 18:01 selects February's.
            self.assertEqual(read(newer["snapshot_id"], "2026-02-28",
                                  "2026-02-28T01:30:00Z"), [True, False])
            self.assertEqual(read(newer["snapshot_id"], "2026-02-28",
                                  "2026-02-28T08:00:00Z"), [True, False])
            self.assertEqual(read(newer["snapshot_id"], "2026-02-28",
                                  "2026-02-28T10:01:00Z"), [False, True])
            self.assertEqual(read(newer["snapshot_id"], "2026-02-20",
                                  "2026-02-20T01:30:00Z"), [True, False])
            self.assertEqual(read(newer["snapshot_id"], "2026-03-03",
                                  "2026-03-01T00:00:00Z", "operational_pit_v1"),
                             [True, False])
            domain = store.load_snapshot(newer["snapshot_id"])["domains"]["universe_membership"]
            rebuilt = rebuild_from_raw(
                store, base_snapshot=newer["snapshot_id"], raw_batch_ids=[*first, *second],
                domains=["universe_membership"], operation_id="vendor-rebuild",
                build_context={"test": "vendor-rebuild"}, promote=False)
            replay = store.load_snapshot(rebuilt.snapshot_id)["domains"]["universe_membership"]
            self.assertEqual(domain["partitions"], replay["partitions"])
            self.assertEqual(domain["coverage"], replay["coverage"])


if __name__ == "__main__":
    unittest.main()
