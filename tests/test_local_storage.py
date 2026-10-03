"""Focused invariants of the new local storage path (no supplier I/O)."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from axiom_data.protocols import DataBatch, DataError, QuerySpec
from axiom_data.storage import LocalStore


CONTRACT = {
    "contract_id": "market_daily_test_v1",
    "logical_key": ["security_id", "session"],
    "fields": {
        "close": {"dtype": "float64", "unit": "CNY", "nullable": True},
        "volume": {"dtype": "int64", "unit": "share", "nullable": True},
    },
}


class LocalStorageTest(unittest.TestCase):
    def test_raw_is_content_addressed_and_every_observation_is_logged(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "new-root"
            store = LocalStore(root)
            self.assertFalse(root.exists())
            with self.assertRaises(DataError):
                store.resolve("current")
            self.assertFalse(root.exists())
            options = dict(request={"endpoint": "synthetic", "symbols": ["X"]},
                           source_profile={"id": "synthetic_v1"},
                           observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
            a = store.write_raw(b'{"untouched":1}', **options)
            b = store.write_raw(b'{"untouched":1}', **options)
            self.assertNotEqual(a["batch_id"], b["batch_id"])
            self.assertEqual(a["payload_uri"], b["payload_uri"])
            self.assertEqual(store.read_raw(a["batch_id"]), b'{"untouched":1}')
            log = (root / "raw/fetches.jsonl").read_text().splitlines()
            self.assertEqual(len(log), 2)
            self.assertEqual(json.loads(log[1])["batch_id"], b["batch_id"])

    def test_typed_projection_empty_schema_and_immutable_versions(self):
        with tempfile.TemporaryDirectory() as temp:
            store = LocalStore(temp)
            rows = [{"security_id": "X", "session": "2020-01-02", "close": 10.5,
                     "volume": 100, "revision_id": "r1",
                     "first_observed_at": "2026-01-01T00:00:00+00:00"},
                    {"security_id": "Y", "session": "2020-01-03", "close": None,
                     "volume": 0, "revision_id": "r2",
                     "first_observed_at": "2026-01-02T00:00:00+00:00"}]
            original = store.write_partition("market_daily", "2020-01", rows, CONTRACT)
            repeated = store.write_partition("market_daily", "2020-01", list(reversed(rows)), CONTRACT)
            self.assertEqual(original, repeated)
            table = store.read_partition(original, columns=["security_id", "close"], symbols=["X"])
            self.assertEqual(table.column_names, ["security_id", "close"])
            self.assertEqual(table.to_pylist(), [{"security_id": "X", "close": 10.5}])
            self.assertEqual(store.read_partition(original, columns=["close"], symbols=[]).num_rows, 0)
            self.assertEqual(str(table.schema.field("close").type), "double")
            empty = store.write_partition("market_daily", "2020-02", [], CONTRACT)
            empty_table = store.read_partition(empty)
            self.assertEqual(empty_table.num_rows, 0)
            self.assertEqual(str(empty_table.schema.field("volume").type), "int64")
            required = dict(CONTRACT, fields={"close": {"dtype": "float64", "nullable": False}})
            with self.assertRaisesRegex(DataError, "non-nullable"):
                store.write_partition("market_daily", "2020-03", [
                    {"security_id": "X", "session": "2020-03-02"}], required)
            changed_rows = [dict(rows[0], close=11.0), rows[1]]
            changed = store.write_partition("market_daily", "2020-01", changed_rows, CONTRACT)
            self.assertNotEqual(changed["uri"], original["uri"])
            self.assertEqual(store.read_partition(original, symbols=["X"])["close"].to_pylist(), [10.5])

    def test_snapshot_atomic_pointer_move_and_integrity(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source"
            store = LocalStore(source)
            raw = store.write_raw(b"[]", request={"endpoint": "fixture"},
                                  source_profile={"id": "fixture"},
                                  observed_at="2026-01-01T00:00:00Z")
            p1 = store.write_partition("market_daily", "2020-01", [
                {"security_id": "X", "session": "2020-01-02", "close": 10.0,
                 "revision_id": "r1", "first_observed_at": raw["observed_at"],
                 "raw_batch_id": raw["batch_id"]}], CONTRACT)
            domain = {"contract": CONTRACT, "source_profile": {"id": "fixture"},
                      "partitions": [p1], "raw_batch_ids": [raw["batch_id"]],
                      "coverage": {"sessions": ["2020-01-02"]}, "build_context": {"code_ref": "test"}}
            first = store.publish_snapshot({"market_daily": domain}, parent_snapshot=None,
                                           build_context={"code_ref": "test"})
            self.assertEqual(store.resolve("current"), first["snapshot_id"])

            p2 = store.write_partition("market_daily", "2020-01", [
                {"security_id": "X", "session": "2020-01-02", "close": 11.0,
                 "revision_id": "r2", "first_observed_at": raw["observed_at"],
                 "raw_batch_id": raw["batch_id"]}], CONTRACT)
            second = store.publish_snapshot({"market_daily": dict(domain, partitions=[p2])},
                                            parent_snapshot=first["snapshot_id"],
                                            build_context={"code_ref": "test"})
            self.assertEqual(store.resolve("current"), second["snapshot_id"])
            self.assertEqual(store.load_snapshot(first["snapshot_id"])["domains"]["market_daily"]["partitions"], [p1])
            destination = Path(temp) / "moved"
            shutil.copytree(source, destination)
            relocated = LocalStore(destination)
            self.assertEqual(relocated.resolve("current"), second["snapshot_id"])
            self.assertEqual(relocated.read_partition(p1)["close"].to_pylist(), [10.0])
            (destination / p1["uri"]).write_bytes(b"corrupt")
            with self.assertRaisesRegex(DataError, "SHA-256"):
                relocated.read_partition(p1)
            self.assertEqual(store.read_partition(p1)["close"].to_pylist(), [10.0])
            manifest_path = destination / "snapshots" / f"{first['snapshot_id']}.json"
            tampered = json.loads(manifest_path.read_text())
            tampered["domains"]["market_daily"]["contract"]["contract_id"] = "forged"
            manifest_path.write_text(json.dumps(tampered))
            with self.assertRaisesRegex(DataError, "digest mismatch"):
                relocated.load_snapshot(first["snapshot_id"])

    def test_failed_publish_does_not_advance_current(self):
        with tempfile.TemporaryDirectory() as temp:
            store = LocalStore(temp)
            first = store.publish_snapshot({}, parent_snapshot=None, build_context={"test": 1})
            with self.assertRaisesRegex(DataError, "missing"):
                store.publish_snapshot({"bad": {
                    "contract": CONTRACT, "source_profile": {}, "coverage": {},
                    "build_context": {}, "raw_batch_ids": [],
                    "partitions": [{"partition": "2020-01", "rows": 1,
                                    "uri": "canonical/bad/no.parquet",
                                    "file_sha256": "0" * 64}]}},
                                       parent_snapshot=first["snapshot_id"], build_context={"test": 2})
            self.assertEqual(store.resolve("current"), first["snapshot_id"])

    def test_protocol_query_is_frozen_and_json_nulls_are_strict(self):
        import pandas as pd

        cutoffs = {"2020-01-02": "2020-01-02T09:00:00+08:00"}
        query = QuerySpec("market_daily", ["close"], ["X"], ["2020-01-02"],
                          "operational_pit_v1", cutoffs)
        cutoffs["2020-01-02"] = "2030-01-01T00:00:00+00:00"
        self.assertEqual(query.cutoff_by_session["2020-01-02"], "2020-01-02T09:00:00+08:00")
        with self.assertRaises(TypeError):
            query.cutoff_by_session["2020-01-02"] = "2030-01-01T00:00:00+00:00"
        batch = DataBatch(pd.DataFrame([{"close": float("nan"), "when": pd.NaT}]),
                          {"close": {"unit": "CNY"}}, {"cutoff": pd.NaT})
        encoded = batch.to_json()
        self.assertEqual(encoded["records"], [{"close": None, "when": None}])
        self.assertIsNone(encoded["context"]["cutoff"])
        json.dumps(encoded, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
