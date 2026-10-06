"""Small synthetic rolling-window and per-session PIT regression cases."""

from __future__ import annotations

import unittest

from axiom_data.protocols import QuerySpec
from axiom_data.reader import SnapshotQueryReader
from test_local_reader import (
    MemoryStore, membership_manifest, membership_query, membership_row, utc,
)


def vendor_membership_store():
    manifest = membership_manifest()
    domain = manifest["domains"]["universe_membership"]
    domain["source_profile"]["id"] = "tushare.index_weight.dated_membership.v1"
    domain["coverage"] = {"verified_through": "2024-01-10", "complete_states": [{
        "universe_id": "IDX", "complete": True, "effective_from": "2024-01-01",
        "effective_to": None, "members": ["A"], "state_id": "state",
        "source_snapshot_date": "2024-01-01", "revision_id": "state-r1",
        "revision_sequence": 1, "first_observed_at": utc(1), "raw_batch_id": "state-raw",
    }]}
    old = membership_row("event-A", "2024-01-01", None, utc(1))
    old["revision_id"] = "r1"
    corrected = membership_row("event-A", "2024-01-01", "2024-01-10", utc(3), sequence=2)
    corrected["revision_id"] = "r2"
    return MemoryStore(manifest, {"history": [old, corrected]})


class MembershipRollingTests(unittest.TestCase):
    def test_mixed_cutoffs_select_each_sessions_visible_source_revision(self):
        sessions = ("2024-01-02", "2024-01-03")
        # Both intervals cover the supplier snapshot date. Only the knowledge
        # time changes which revision is visible; expected values come from
        # the source sequences and receipts, not the pre-fix reader's output.
        for cutoffs, revisions, usable in (
            ((utc(4), utc(2)), ["r2", "r1"], [utc(3), utc(1)]),
            ((utc(2), utc(4)), ["r1", "r2"], [utc(1), utc(3)]),
        ):
            spec = QuerySpec("universe_membership", ("is_member",), ("A",), sessions,
                             "operational_pit_v1", dict(zip(sessions, cutoffs)), universe_id="IDX")
            for budget in (0, 1_000_000):
                with self.subTest(cutoffs=cutoffs, budget=budget):
                    batch = SnapshotQueryReader(vendor_membership_store(), "s1",
                                                cache_bytes=budget).read(spec)
                    self.assertEqual(batch.frame["is_member"].tolist(), [True, True])
                    meta = batch.field_meta["is_member"]["by_key"]
                    self.assertEqual([item["revision_id"] for item in meta], revisions)
                    self.assertEqual([item["usable_from"] for item in meta],
                                     [value.isoformat() for value in usable])
                    self.assertTrue(all(value <= cutoff for value, cutoff in zip(usable, cutoffs)))

    def test_membership_result_cache_keeps_full_query_identity(self):
        for vendor in (False, True):
            with self.subTest(vendor=vendor):
                store = (vendor_membership_store() if vendor else
                         MemoryStore(membership_manifest(), {"history": []}))
                reader = SnapshotQueryReader(store, "s1", cache_bytes=1_000_000)
                spec = membership_query(cutoff=utc(4))
                first = reader.read(spec)
                first.frame.loc[0, "is_member"] = False
                first.field_meta["is_member"]["by_key"][0]["revision_id"] = "polluted"
                second = reader.read(spec)
                self.assertEqual(second.frame["is_member"].tolist(), [True, False])
                self.assertNotEqual(second.field_meta["is_member"]["by_key"][0]["revision_id"],
                                    "polluted")
                self.assertEqual((reader.partition_reads, reader.cache_hits), (1, 1))
                self.assertIn(reader._cache_key(spec, {s: utc(4) for s in spec.sessions}), reader._cache)


if __name__ == "__main__":
    unittest.main()
