"""Crash boundaries in the append-only Raw receipt log, without supplier I/O."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from axiom_data import DataError
from axiom_data.storage import LocalStore
from test_batch_jobs_v2 import WholeMarket, run, v2_plan


def observe(store, payload=b'[]', index=0):
    return store.write_raw(payload, request={"endpoint": "fixture"},
                           source_profile={"id": "fixture"},
                           observed_at="2026-10-03T00:00:00Z",
                           operation_id="crash-probe", batch_index=index)


class RawLogRecoveryTest(unittest.TestCase):
    def test_reads_use_complete_prefix_without_mutating_an_uncommitted_tail(self):
        for fragment in (b'{"batch_id":', b'{"batch_id":"not-committed"}', b'\xe4\xb8'):
            with self.subTest(fragment=fragment), tempfile.TemporaryDirectory() as root:
                store = LocalStore(root)
                saved = observe(store)
                # Exercise both an existing incremental index and a fresh reader.
                store.get_raw(saved["batch_id"])
                log = Path(root) / "raw/fetches.jsonl"
                committed = log.read_bytes()
                with log.open("ab") as out:
                    out.write(fragment)
                for reader in (store, LocalStore(root)):
                    self.assertEqual(reader.read_raw(saved["batch_id"]), b'[]')
                    recovered = reader.find_raw_by_operation("crash-probe")
                    self.assertEqual(recovered, {0: saved})
                self.assertEqual(log.read_bytes(), committed + fragment)
                self.assertFalse((Path(root) / "raw/recovery").exists())

    def test_preparation_reuse_and_export_read_only_committed_prefix(self):
        from axiom_data.cli import _reusable_reference_raw
        from axiom_data.portable import _raw_lines
        from axiom_data.universe_sources import _reference_profile
        with tempfile.TemporaryDirectory() as root:
            store = LocalStore(root)
            request = {"endpoint": "stock_basic", "params": {}, "fields": []}
            record = store.write_raw(b'[]', domain="reference_bootstrap",
                request=request, source_profile=_reference_profile("stock_basic"),
                observed_at="2026-10-03T00:00:00+00:00")
            log = Path(root) / "raw/fetches.jsonl"
            committed = log.read_bytes()
            log.write_bytes(committed + b'{"batch_id":')
            self.assertEqual(_reusable_reference_raw(store,
                {"reference_raw_batch_ids": [record["batch_id"]]},
                {"requests": [request]}), (record["batch_id"],))
            selected, records = _raw_lines(store, {record["batch_id"]})
            self.assertEqual(selected, committed)
            self.assertEqual(records[record["batch_id"]], record)
            self.assertEqual(log.read_bytes(), committed + b'{"batch_id":')

    def test_next_append_preserves_fragment_and_prior_observations(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalStore(root)
            saved = observe(store)
            log = Path(root) / "raw/fetches.jsonl"
            committed = log.read_bytes()
            fragment = b'{"batch_id":"incomplete","observed_at":'
            with log.open("ab") as out:
                out.write(fragment)
            added = observe(store, b'[1]', 1)
            self.assertTrue(log.read_bytes().startswith(committed))
            self.assertNotIn(fragment, log.read_bytes())
            self.assertEqual(store.read_raw(saved["batch_id"]), b'[]')
            self.assertEqual(store.read_raw(added["batch_id"]), b'[1]')
            self.assertEqual(store.find_raw_by_operation("crash-probe"), {0: saved, 1: added})
            fragments = list((Path(root) / "raw/recovery").glob("*.bin"))
            self.assertEqual([p.read_bytes() for p in fragments], [fragment])

    def test_checkpoint_offset_is_a_complete_log_boundary(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalStore(root)
            saved = observe(store)
            log = Path(root) / "raw/fetches.jsonl"
            committed_size = log.stat().st_size
            with log.open("ab") as out:
                out.write(b'{"batch_id":')
            self.assertEqual(store.raw_log_size(), committed_size)
            added = observe(store, b'[2]', 1)
            self.assertEqual(store.find_raw_by_operation("crash-probe", since_offset=committed_size), {1: added})
            self.assertEqual(store.get_raw(saved["batch_id"])["observed_at"], saved["observed_at"])

    def test_committed_corruption_is_not_treated_as_a_crash_tail(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalStore(root)
            saved = observe(store)
            log = Path(root) / "raw/fetches.jsonl"
            with log.open("ab") as out:
                out.write(b'not-json\n')
            before = log.read_bytes()
            for call in (lambda: store.get_raw(saved["batch_id"]),
                         lambda: store.find_raw_by_operation("crash-probe")):
                with self.assertRaisesRegex(DataError, "corrupt"):
                    call()
            self.assertEqual(log.read_bytes(), before)
            self.assertFalse((Path(root) / "raw/recovery").exists())

    def test_bulk_resumes_completed_receipts_after_interrupted_log_append(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalStore(root)
            plan = v2_plan(["trade_cal", "daily"])
            original_write = store.write_raw

            def interrupted(payload, **kwargs):
                record = original_write(payload, **kwargs)
                if kwargs["request"]["endpoint"] == "daily":
                    with (Path(root) / "raw/fetches.jsonl").open("ab") as out:
                        out.write(b'{"batch_id":"next-uncommitted"')
                    raise KeyboardInterrupt()
                return record

            with patch.object(store, "write_raw", interrupted):
                with self.assertRaises(KeyboardInterrupt):
                    run(store, plan, WholeMarket(), max_workers=1)
            self.assertFalse((Path(root) / "current.json").exists())
            client = WholeMarket()
            result = run(LocalStore(root), plan, client, max_workers=1)
            self.assertEqual(client.calls, [])
            self.assertEqual(LocalStore(root).resolve("current"), result.snapshot_id)


if __name__ == "__main__":
    unittest.main()
