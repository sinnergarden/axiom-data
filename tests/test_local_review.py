"""Integration regressions using only a tiny synthetic local source."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from axiom_data import Data, IngestBatch, QuerySpec, UpdateRequest
from axiom_data.protocols import ConflictError, DataError


CONTRACT = {
    "contract_id": "review.daily.v1",
    "logical_key": ["security_id", "session"],
    "fields": {
        "security_id": {"dtype": "string", "nullable": False},
        "session": {"dtype": "date", "nullable": False},
        "close": {"dtype": "float64", "unit": "CNY/share", "nullable": False},
    },
}
PROFILE = {
    "id": "review.source.v1",
    "field_map": {
        "security_id": "security_id", "session": "session", "close": "close",
        "revision_id": "revision", "revision_sequence": "sequence",
    },
    "source_units": {"close": "CNY/share"},
}
BUILD = {"recipe": "review-v1"}


def batch(day: str, price: float, revision: str, sequence: int, observed_day: int) -> IngestBatch:
    source_row = {
        "security_id": "stable-A", "session": day, "close": price,
        "revision": revision, "sequence": sequence,
    }
    return IngestBatch(
        "market_daily", json.dumps([source_row]).encode(),
        {"source": "synthetic", "session": day}, CONTRACT, PROFILE,
        datetime(2026, 1, observed_day, 12, tzinfo=timezone.utc),
    )


def close_query(day: str, cutoff_day: int) -> QuerySpec:
    return QuerySpec(
        "market_daily", ("close",), ("stable-A",), (day,),
        "operational_pit_v1",
        {day: datetime(2026, 1, cutoff_day, 13, tzinfo=timezone.utc)},
    )


class LocalReviewTest(unittest.TestCase):
    def test_late_old_revision_cannot_replace_newer_source_revision(self):
        with tempfile.TemporaryDirectory() as temporary:
            data = Data(Path(temporary) / "root")
            day = "2024-01-02"
            first = data.update(base_snapshot=None, request=UpdateRequest(
                (batch(day, 10.0, "v1", 1, 2),), "review-first", BUILD))
            newer = data.update(base_snapshot=first.snapshot_id, request=UpdateRequest(
                (batch(day, 20.0, "v2", 2, 3),), "review-newer", BUILD))
            late_old = data.update(base_snapshot=newer.snapshot_id, request=UpdateRequest(
                (batch(day, 5.0, "v0", 0, 4),), "review-late-old", BUILD))

            early = data.read(snapshot=late_old.snapshot_id, query=close_query(day, 2))
            middle = data.read(snapshot=late_old.snapshot_id, query=close_query(day, 3))
            latest = data.read(snapshot=late_old.snapshot_id, query=close_query(day, 5))
            self.assertEqual([early.frame.iloc[0]["close"], middle.frame.iloc[0]["close"],
                              latest.frame.iloc[0]["close"]], [10.0, 20.0, 20.0])
            self.assertEqual(latest.field_meta["close"]["by_key"][0]["revision_id"], "v2")
            self.assertEqual(data.read(snapshot=first.snapshot_id, query=close_query(day, 5))
                             .frame.iloc[0]["close"], 10.0)

    def test_failed_normalization_reuses_logged_raw_on_resume(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "root"
            data = Data(root)
            request = UpdateRequest(
                (batch("2024-01-02", 10.0, "v1", 1, 2),), "review-resume", BUILD)
            with patch("axiom_data.updates.normalize_batch", side_effect=RuntimeError("temporary parse failure")):
                with self.assertRaisesRegex(DataError, "temporary parse failure"):
                    data.update(base_snapshot=None, request=request)
            operation = data.store.read_operation("review-resume")
            self.assertEqual(operation["status"], "failed")
            self.assertEqual(len(operation["raw_batch_ids"]), 1)
            self.assertFalse((root / "current.json").exists())

            result = data.update(base_snapshot=None, request=request)
            self.assertTrue(result.changed)
            self.assertEqual(data.resolve(), result.snapshot_id)
            self.assertEqual(len((root / "raw/fetches.jsonl").read_text().splitlines()), 1)
            self.assertEqual(data.read(snapshot=result.snapshot_id,
                                       query=close_query("2024-01-02", 5))
                             .frame.iloc[0]["close"], 10.0)

    def test_stale_base_cannot_roll_back_promoted_current(self):
        with tempfile.TemporaryDirectory() as temporary:
            data = Data(Path(temporary) / "root")
            first = data.update(base_snapshot=None, request=UpdateRequest(
                (batch("2024-01-02", 10.0, "a", 1, 2),), "review-base", BUILD))
            current = data.update(base_snapshot=first.snapshot_id, request=UpdateRequest(
                (batch("2024-01-03", 30.0, "b", 1, 3),), "review-current", BUILD))

            with self.assertRaises(ConflictError):
                data.update(base_snapshot=first.snapshot_id, request=UpdateRequest(
                    (batch("2024-01-04", 40.0, "c", 1, 4),), "review-stale", BUILD))
            with self.assertRaises(ConflictError):
                data.update(base_snapshot=first.snapshot_id, request=UpdateRequest(
                    (batch("2024-01-02", 10.0, "a", 1, 5),), "review-stale-repeat", BUILD))
            self.assertEqual(data.resolve(), current.snapshot_id)
            self.assertEqual(data.read(snapshot=current.snapshot_id,
                                       query=close_query("2024-01-03", 5))
                             .frame.iloc[0]["close"], 30.0)

    def test_read_checks_requested_domain_without_raw_replay_or_unrelated_scan(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "root"
            data = Data(root)
            unrelated = batch("2024-01-02", 99.0, "other", 1, 2)
            unrelated = IngestBatch("other_daily", unrelated.payload, unrelated.request,
                                    unrelated.contract, unrelated.source_profile,
                                    unrelated.observed_at)
            result = data.update(base_snapshot=None, request=UpdateRequest(
                (batch("2024-01-02", 10.0, "market", 1, 2), unrelated),
                "review-two-domains", BUILD))
            other_partition = data.store.load_snapshot(result.snapshot_id)["domains"]["other_daily"]["partitions"][0]
            (root / other_partition["uri"]).write_bytes(b"damaged unrelated partition")
            with patch.object(data.store, "_raw_records", side_effect=AssertionError("Raw replay on read")):
                market = data.read(snapshot=result.snapshot_id,
                                   query=close_query("2024-01-02", 5))
                self.assertEqual(market.frame.iloc[0]["close"], 10.0)
                self.assertEqual(market.context["domain"], "market_daily")
            with self.assertRaisesRegex(DataError, "SHA-256"):
                data.read(snapshot=result.snapshot_id, query=QuerySpec(
                    "other_daily", ("close",), ("stable-A",), ("2024-01-02",),
                    "operational_pit_v1",
                    {"2024-01-02": datetime(2026, 1, 5, 13, tzinfo=timezone.utc)},
                ))


if __name__ == "__main__":
    unittest.main()
