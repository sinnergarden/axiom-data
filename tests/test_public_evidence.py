"""Synthetic document bindings: isolated versions, original bytes and portability."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from axiom_data import Data, QuerySpec, attach_public_evidence
from axiom_data.portable import export_bundle, import_bundle
from axiom_data.protocols import DataError
from test_local_updates import batch, row, update, stored_rows


class PublicEvidenceTest(unittest.TestCase):
    def test_exact_revision_only_with_portable_original_and_operational_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Data(Path(tmp) / "source")
            original = update(data.store, None, "initial", batch([row()])).snapshot_id
            args = dict(snapshot=original, document=b"SYNTHETIC publication: close=10",
                        source_url="https://example.invalid/synthetic-report",
                        assertions=[dict(domain="market_daily", key={"security_id": "sec-A", "session": "2020-01-02"},
                            revision_id="r1", public_at="2020-01-02T20:00:00+08:00", locator="synthetic paragraph 1",
                            values={"security_id": "sec-A", "session": "2020-01-02", "close": 10.0})],
                        operation_id="attest")
            enriched = attach_public_evidence(data.store, **args).snapshot_id
            query = QuerySpec("market_daily", ("close",), ("sec-A",), ("2020-01-02",),
                              "market_pit_safe_v1", {"2020-01-02": "2020-01-02T21:00:00+08:00"})
            self.assertTrue(data.read(snapshot=original, query=query).frame.close.isna().all())
            self.assertEqual(data.read(snapshot=enriched, query=query).frame.close.tolist(), [10])
            self.assertTrue(data.read(snapshot=enriched, query=replace(query, pit_policy="operational_pit_v1")).frame.close.isna().all())
            self.assertEqual(attach_public_evidence(data.store, **args).snapshot_id, enriched)
            self.assertEqual(stored_rows(data.store, original), stored_rows(data.store, enriched))
            revised = update(data.store, enriched, "correction", batch([row(close=20, revision="r2", sequence=2)], day=2)).snapshot_id
            # An unattested revision cannot borrow r1's source time.
            self.assertEqual(data.read(snapshot=revised, query=query).frame.close.tolist(), [10])
            export_bundle(data.store.root, snapshot_id=revised, destination=Path(tmp) / "bundle", code_root=Path(__file__).resolve().parents[1])
            import_bundle(Path(tmp) / "bundle", Path(tmp) / "restored")
            restored = Data(Path(tmp) / "restored")
            self.assertEqual(restored.read(snapshot=revised, query=query).frame.close.tolist(), [10])
            manifest = restored.store.load_snapshot(revised)
            raw_ids = manifest["domains"]["public_evidence"]["raw_batch_ids"]
            raw = restored.store.get_raw_many(raw_ids)[raw_ids[0]]
            self.assertIn(b"document_base64", restored.store.read_raw_record(raw))
            rebuilt = restored.rebuild(base_snapshot=revised, raw_batch_ids=raw_ids,
                                       domains=("public_evidence",), operation_id="rebuild-evidence",
                                       build_context={"method": "offline-test"}, promote=False)
            self.assertEqual(restored.read(snapshot=rebuilt.snapshot_id, query=query).frame.close.tolist(), [10])

    def test_partial_or_wrong_attestation_is_saved_but_cannot_publish(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Data(tmp)
            original = update(data.store, None, "initial", batch([row()])).snapshot_id
            with self.assertRaises(DataError):
                attach_public_evidence(data.store, snapshot=original,
                    document=b"SYNTHETIC report with different values", source_url="https://example.invalid/test",
                    assertions=[dict(domain="market_daily", key={"security_id": "sec-A", "session": "2020-01-02"},
                        revision_id="r1", public_at="2020-01-02T20:00:00+08:00", locator="line 1", values={"close": 99})],
                    operation_id="bad-evidence")
            self.assertEqual(data.resolve(), original)
            self.assertEqual(len(data.store.find_raw_by_operation("bad-evidence")), 1)
