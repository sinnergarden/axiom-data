"""Portable bundle acceptance: fixed closure, corruption, offline replay."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from axiom_data.api import Data
from axiom_data.portable import export_bundle, import_bundle, verify_bundle
from axiom_data.protocols import DataError, IngestBatch, QuerySpec, UpdateRequest


CODE_ROOT = Path(__file__).resolve().parents[1]
CONTRACT = {
    "contract_id": "portable.synthetic.daily.v1",
    "logical_key": ["security_id", "session"],
    "fields": {
        "security_id": {"dtype": "string", "nullable": False},
        "session": {"dtype": "date", "nullable": False},
        "close": {"dtype": "float64", "unit": "CNY/share", "nullable": True},
    },
}
PROFILE = {
    "id": "portable.synthetic.v1",
    "field_map": {"security_id": "security_id", "session": "session",
                  "close": "close", "revision_id": "revision",
                  "revision_sequence": "sequence"},
    "source_units": {"close": "CNY/share"},
    "availability": {"timezone": "Asia/Shanghai", "session_release_time": "20:00:00"},
}


def ingest(close: float, sequence: int, observed_day: int) -> IngestBatch:
    row = {"security_id": "sec-A", "session": "2020-01-02", "close": close,
           "revision": f"r{sequence}", "sequence": sequence}
    return IngestBatch("market_daily", json.dumps([row]).encode(),
                       {"endpoint": "synthetic", "session": "2020-01-02"},
                       CONTRACT, PROFILE,
                       datetime(2026, 9, observed_day, tzinfo=timezone.utc))


def read_close(data: Data, snapshot: str) -> float:
    query = QuerySpec("market_daily", ("close",), ("sec-A",), ("2020-01-02",),
                      "operational_pit_v1",
                      {"2020-01-02": "2026-10-01T00:00:00+00:00"})
    return data.read(snapshot=snapshot, query=query).frame.iloc[0]["close"]


class PortableBundleTest(unittest.TestCase):
    def test_parent_closure_offline_read_rebuild_and_update(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            source = Data(temp / "source")
            first = source.update(base_snapshot=None,
                                  request=UpdateRequest((ingest(10, 1, 1),), "first",
                                                        {"code_ref": "portable-test"}))
            second = source.update(base_snapshot=first.snapshot_id,
                                   request=UpdateRequest((ingest(11, 2, 2),), "second",
                                                         {"code_ref": "portable-test"}))
            raw_ids = [source.store.read_operation(op)["raw_batch_ids"][0]
                       for op in ("first", "second")]
            unused = source.store.write_raw(b"unselected secret-like response",
                                            request={"endpoint": "unrelated"},
                                            source_profile={"id": "unrelated"},
                                            observed_at="2026-09-03T00:00:00Z")
            (temp / "source" / "credentials.txt").write_text("must not transfer")
            manifest = export_bundle(temp / "source", temp / "bundle", code_root=CODE_ROOT)
            self.assertEqual(manifest["snapshot_id"], second.snapshot_id)
            self.assertEqual(manifest["snapshots"], [second.snapshot_id, first.snapshot_id])
            self.assertEqual(manifest["source"]["kind"], "captured_working_tree")
            self.assertEqual(verify_bundle(temp / "bundle"), manifest)
            self.assertNotIn(unused["batch_id"], (temp / "bundle/data/raw/fetches.jsonl").read_text())
            self.assertFalse((temp / "bundle/data/credentials.txt").exists())
            self.assertTrue((temp / "bundle/code/requirements-local.lock").exists())
            selected = import_bundle(temp / "bundle", temp / "imported")
            imported = Data(temp / "imported")
            self.assertEqual(selected, second.snapshot_id)
            self.assertEqual(imported.resolve(), selected)
            self.assertEqual(read_close(imported, first.snapshot_id), 10)
            self.assertEqual(read_close(imported, selected), 11)
            self.assertTrue((temp / "imported/portable-code/src/axiom_data/portable.py").exists())
            # A separate interpreter must import the captured package, without
            # the checkout on sys.path or any network operation.
            captured_src = temp / "imported/portable-code/src"
            dependency_paths = [path for path in sys.path if path and Path(path).exists()
                                and Path(path).resolve() != CODE_ROOT / "src"]
            environment = dict(os.environ, PYTHONPATH=os.pathsep.join(
                [str(captured_src), *dependency_paths]))
            replay = subprocess.run(
                [sys.executable, "-c",
                 "from axiom_data.api import Data; from axiom_data.protocols import QuerySpec; "
                 "import sys; d=Data(sys.argv[1]); s=sys.argv[2]; "
                 "q=QuerySpec('market_daily',('close',),('sec-A',),('2020-01-02',),"
                 "'operational_pit_v1',{'2020-01-02':'2026-10-01T00:00:00+00:00'}); "
                 "assert d.resolve()==s; assert d.read(snapshot=s,query=q).frame.iloc[0]['close']==11; "
                 "print('captured-code-offline-read-ok')",
                 str(temp / "imported"), selected],
                cwd=temp, env=environment, capture_output=True, text=True)
            self.assertEqual(replay.returncode, 0, replay.stderr)
            self.assertIn("captured-code-offline-read-ok", replay.stdout)
            rebuilt = imported.rebuild(base_snapshot=selected, raw_batch_ids=raw_ids,
                                       domains=["market_daily"], operation_id="offline-rebuild",
                                       build_context={"code_ref": "portable-test-rebuild"})
            self.assertEqual(read_close(imported, rebuilt.snapshot_id), 11)
            advanced = imported.update(base_snapshot=rebuilt.snapshot_id,
                                       request=UpdateRequest((ingest(12, 3, 3),), "offline-update",
                                                             {"code_ref": "portable-test-update"}))
            self.assertEqual(read_close(imported, advanced.snapshot_id), 12)
            self.assertEqual(source.resolve(), second.snapshot_id)

    def test_corruption_rejected_before_destination_publication(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            source = Data(temp / "source")
            result = source.update(base_snapshot=None,
                                   request=UpdateRequest((ingest(10, 1, 1),), "first",
                                                         {"code_ref": "portable-test"}))
            export_bundle(temp / "source", temp / "bundle", snapshot_id=result.snapshot_id,
                          code_root=CODE_ROOT)
            part = source.store.load_snapshot(result.snapshot_id)["domains"]["market_daily"]["partitions"][0]
            (temp / "bundle/data" / part["uri"]).write_bytes(b"corrupt")
            with self.assertRaisesRegex(DataError, "SHA-256"):
                import_bundle(temp / "bundle", temp / "imported")
            self.assertFalse((temp / "imported").exists())

    def test_refuse_existing_root_without_touching_it(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            source = Data(temp / "source")
            source.update(base_snapshot=None,
                          request=UpdateRequest((ingest(10, 1, 1),), "first",
                                                {"code_ref": "portable-test"}))
            export_bundle(temp / "source", temp / "bundle", code_root=CODE_ROOT)
            existing = temp / "existing"
            existing.mkdir()
            (existing / "sentinel").write_text("keep")
            with self.assertRaisesRegex(DataError, "already exists"):
                import_bundle(temp / "bundle", existing)
            self.assertEqual((existing / "sentinel").read_text(), "keep")


if __name__ == "__main__":
    unittest.main()
