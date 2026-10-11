"""Synthetic incremental lookup and physical-log restart boundaries only."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from axiom_data import collect_stock_input_response
from axiom_data.live_client import SourceResponseError
from axiom_data.protocols import ConflictError, CoverageError, DataError
from axiom_data.storage import LocalStore
from test_local_stock_input_sources import CAL, IDS, OBS, Client, complete


class StockInputRawIndex(unittest.TestCase):
    def setUp(self):
        self.row = complete("daily_basic", ts_code="000001.SZ", trade_date="20240503")

    def collect(self, store, client, slot, **changes):
        args = dict(endpoint="daily_basic", params={"trade_date": "20240503"},
                    identity_map=IDS, canonical_symbols=["000001.SZ"],
                    operation_id="indexed", batch_index=slot, observed_at=OBS,
                    next_open_session_by_date=CAL)
        return collect_stock_input_response(store, client=client, **{**args, **changes})

    def test_cold_history_all_statuses_incremental_append_and_restart(self):
        with tempfile.TemporaryDirectory() as root, patch.dict("axiom_data.stock_input_sources._CAPS", {"daily_basic": 2}):
            original = LocalStore(root)
            first = self.collect(original, Client([self.row]), 0)
            with self.assertRaises(CoverageError):
                self.collect(original, Client(SourceResponseError(-2002)), 1)
            with self.assertRaises(CoverageError):
                self.collect(original, Client([self.row, self.row]), 2)
            resumed, client = LocalStore(root), Client([self.row])
            with patch.object(resumed, "_decode_raw_line", wraps=resumed._decode_raw_line) as decoded:
                reused = self.collect(resumed, client, 0)
                self.assertEqual(reused["batch_id"], first["batch_id"])
                self.assertEqual(decoded.call_count, 4)  # Three old receipts and one selected receipt.
                for slot in (1, 2):
                    before = decoded.call_count
                    with self.assertRaises(CoverageError):self.collect(resumed, client, slot)
                    self.assertEqual(decoded.call_count - before, 1)
                self.assertEqual(client.calls, [])
                reused["request"]["params"]["trade_date"] = "bad"
                self.assertEqual(self.collect(resumed, client, 0)["request"]["params"]["trade_date"], "20240503")
                with self.assertRaises(ConflictError):
                    self.collect(resumed, client, 0, canonical_symbols=list(IDS))
                new = self.collect(resumed, client, 3)
                before = decoded.call_count
                self.assertEqual(self.collect(resumed, client, 3)["batch_id"], new["batch_id"])
                self.assertEqual(decoded.call_count - before, 2)  # Only the new line and selected receipt.
                self.assertEqual(len(client.calls), 1)
            guard = resumed.raw_slot_checkpoint("indexed")
            self.assertEqual(self.collect(LocalStore(root), client, 0, raw_log_guard=guard)["batch_id"], first["batch_id"])
            self.assertEqual(len(client.calls), 1)
            # A duplicate slot with another receipt stays an error before I/O.
            original.write_raw(b"[]", request=first["request"], source_profile=first["source_profile"],
                contract=first["contract"], observed_at=OBS, operation_id="indexed", batch_index=0)
            with self.assertRaises(ConflictError):self.collect(resumed, client, 4)
            self.assertEqual(len(client.calls), 1)

    def test_file_replacement_truncation_mutation_and_incomplete_tail(self):
        for change in ("replace", "truncate", "mutate", "prefix-append", "tail"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as root:
                store, client = LocalStore(root), Client([self.row])
                first = self.collect(store, client, 0)
                self.collect(store, client, 1)
                guard = store.raw_slot_checkpoint("indexed")
                path = Path(root) / "raw/fetches.jsonl"
                contents = path.read_bytes()
                if change == "replace":
                    replacement = path.with_suffix(".replacement")
                    replacement.write_bytes(contents);replacement.replace(path)
                elif change == "truncate":path.write_bytes(b"")
                elif change == "mutate":path.write_bytes(contents.replace(b"20240503", b"20240502"))
                elif change == "prefix-append":
                    lines = contents.splitlines(keepends=True)
                    path.write_bytes(lines[0].replace(b"20240503", b"20240502") + lines[1] + b'{"tail":')
                else:
                    with path.open("ab") as stream:stream.write(b'{"tail":')
                before = len(client.calls)
                if change == "tail":
                    self.assertEqual(self.collect(LocalStore(root), client, 0, raw_log_guard=guard)["batch_id"], first["batch_id"])
                    self.collect(store, client, 2)  # Writer recovers only the uncommitted tail.
                    self.assertEqual(store.find_raw_slot("indexed", 2)["status"], "success")
                    self.assertEqual(len(client.calls), before + 1)
                else:
                    with self.assertRaises(DataError):self.collect(store, client, 0)
                    if change != "prefix-append":
                        with self.assertRaises(DataError):self.collect(LocalStore(root), client, 0, raw_log_guard=guard)
                    self.assertEqual(len(client.calls), before)


if __name__ == "__main__":
    unittest.main()
