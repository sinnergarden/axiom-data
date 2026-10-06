"""Exact canonical bytes and atomic failure boundaries for streamed Snapshots."""
from datetime import date, datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from axiom_data.protocols import ConflictError
from axiom_data.storage import LocalStore, _json_bytes, _json_chunks, _json_digest


def repeated_identities():
    identities = {f"{index:06d}.SH": f"cnstock.{index:06d}.SH.20000101"
                  for index in range(5557)}
    return {"domains": {f"synthetic_{index}": {"identity_map": identities,
                        "selection": list(identities), "notes": ["公告😀", None, -0.0]}
                        for index in range(24)}, "nested": {"empty": [], "map": {}}}


class SnapshotStreamingTest(unittest.TestCase):
    def test_chunks_digest_and_atomic_file_are_exact_legacy_bytes(self):
        import numpy as np
        values = [None, True, False, {}, [],
            {"中文😀": {"quotes": '\"\\\n\t\u0000', "items": [None, {}, [], True]}},
            {"numbers": [9007199254740993, 1.0, -0.0, 1e-300, 1e300, np.int64(7), np.float32(0.1)],
             "date": date(2024, 1, 1), "time": datetime(2024, 1, 1, tzinfo=timezone.utc),
             "path": Path("目录/😀.json")},
            {"long_unicode": "公告😀\n" * 24000, "nested": [[{"x": None}]]},
            repeated_identities()]
        with tempfile.TemporaryDirectory() as directory:
            store = LocalStore(directory)
            for index, value in enumerate(values):
                with self.subTest(vector=index):
                    expected = _json_bytes(value)
                    self.assertEqual(b"".join(_json_chunks(value)), expected)
                    self.assertEqual(_json_digest(value), sha256(expected).hexdigest())
                    path = Path(directory) / f"vector-{index}.json"
                    store._atomic_json(path, value, immutable=True)
                    self.assertEqual(path.read_bytes(), expected)
                    self.assertFalse(path.read_bytes().endswith(b"\n"))

    def test_deep_fallback_restarts_after_flushed_chunks(self):
        nested = 0
        for _ in range(995):
            nested = {"x": nested}
        value = {"a_prefix": "公告😀" * 24000, "z_nested": nested}
        expected = _json_bytes(value)
        with tempfile.TemporaryDirectory() as directory:
            store = LocalStore(directory)
            path = Path(directory) / "deep.json"
            self.assertEqual(_json_digest(value), sha256(expected).hexdigest())
            with patch("axiom_data.storage._json_bytes", wraps=_json_bytes) as fallback:
                store._atomic_json(path, value, immutable=True)
            self.assertGreater(fallback.call_count, 0)
            self.assertEqual(path.read_bytes(), expected)
            store._atomic_json(path, value, immutable=True)
            self.assertEqual(path.read_bytes(), expected)
            path.write_bytes(expected + b"\n")
            with self.assertRaises(ConflictError):
                store._atomic_json(path, value, immutable=True)
            self.assertEqual(path.read_bytes(), expected + b"\n")

    def test_public_snapshot_streams_both_hash_and_file_without_whole_encoding(self):
        context = {"synthetic_repeated_source_maps": repeated_identities()}
        with tempfile.TemporaryDirectory() as directory:
            store = LocalStore(directory)
            def reject_whole_snapshot(value):
                if isinstance(value, dict) and "domains" in value:
                    raise AssertionError("whole Snapshot serializer invoked")
                return _json_bytes(value)
            with patch("axiom_data.storage._json_bytes", side_effect=reject_whole_snapshot):
                manifest = store.publish_snapshot({}, parent_snapshot=None,
                                                  build_context=context, promote=True)
            body = {key: value for key, value in manifest.items() if key != "snapshot_id"}
            self.assertEqual(manifest["snapshot_id"], "s_" + sha256(_json_bytes(body)).hexdigest())
            self.assertEqual((Path(directory) / "snapshots" / (manifest["snapshot_id"] + ".json")).read_bytes(),
                             _json_bytes(manifest))
            self.assertEqual(store.resolve("current"), manifest["snapshot_id"])

    def test_immutable_repeat_conflict_prefix_and_suffix_keep_original(self):
        value = {"payload": "公告😀" * 24000, "end": [None, -0.0]}
        expected = _json_bytes(value)
        with tempfile.TemporaryDirectory() as directory:
            store = LocalStore(directory)
            path = Path(directory) / "immutable.json"
            store._atomic_json(path, value, immutable=True)
            initial_stat = path.stat()
            # The pre-existing identical immutable object needs no new temp
            # file, fsync or rename, matching the old atomic method.
            with patch("axiom_data.storage.tempfile.mkstemp", side_effect=AssertionError("unneeded temp")), \
                    patch("axiom_data.storage.os.fsync", side_effect=AssertionError("unneeded fsync")):
                store._atomic_json(path, value, immutable=True)
            self.assertEqual(path.stat().st_mtime_ns, initial_stat.st_mtime_ns)
            for existing in [expected[:-1], expected + b"\n", b"!" + expected[1:]]:
                path.write_bytes(existing)
                with self.assertRaises(ConflictError):
                    store._atomic_json(path, value, immutable=True)
                self.assertEqual(path.read_bytes(), existing)
                self.assertFalse(list(Path(directory).glob(".tmp-*")))

    def test_invalid_encoding_removes_partial_temp_and_keeps_existing_file(self):
        for bad in [object(), float("nan"), float("inf"), float("-inf")]:
            with self.subTest(value=type(bad).__name__), tempfile.TemporaryDirectory() as directory:
                store = LocalStore(directory)
                path = Path(directory) / "mutable.json"
                path.write_bytes(b"original")
                value = {"a_flushed_prefix": "x" * 70000, "z_invalid": bad}
                with self.assertRaises((TypeError, ValueError)):
                    store._atomic_json(path, value)
                self.assertEqual(path.read_bytes(), b"original")
                self.assertFalse(list(Path(directory).glob(".tmp-*")))

    def test_interruption_during_snapshot_stream_keeps_parent_and_current(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LocalStore(directory)
            parent = store.publish_snapshot({}, parent_snapshot=None, build_context={"old": 1})
            parent_path = Path(directory) / "snapshots" / (parent["snapshot_id"] + ".json")
            original = parent_path.read_bytes()
            pointer = (Path(directory) / "current.json").read_bytes()
            def interrupt_manifest(value):
                chunks = _json_chunks(value)
                if isinstance(value, dict) and "snapshot_id" in value:
                    yield next(chunks)
                    raise KeyboardInterrupt
                yield from chunks
            with patch("axiom_data.storage._json_chunks", side_effect=interrupt_manifest), \
                    self.assertRaises(KeyboardInterrupt):
                store.publish_snapshot({}, parent_snapshot=parent["snapshot_id"],
                                       build_context={"prefix": "x" * 70000})
            self.assertEqual(parent_path.read_bytes(), original)
            self.assertEqual((Path(directory) / "current.json").read_bytes(), pointer)
            self.assertEqual(len(list((Path(directory) / "snapshots").glob("s_*.json"))), 1)
            self.assertFalse(list((Path(directory) / "snapshots").glob(".tmp-*")))

    def test_partial_write_flush_fsync_and_rename_failures_match_old_atomic(self):
        value = {"payload": "公告😀" * 24000, "tail": [None, 1.0]}
        for boundary in ["write", "flush", "file_fsync", "rename"]:
            for mode in ["legacy", "stream"]:
                with self.subTest(boundary=boundary, mode=mode), tempfile.TemporaryDirectory() as directory:
                    store = LocalStore(directory)
                    path = Path(directory) / "object.json"
                    path.write_bytes(b"original")
                    fdopen = os.fdopen
                    class Stream:
                        def __init__(self, actual):
                            self.actual = actual
                        def __enter__(self):
                            self.actual.__enter__()
                            return self
                        def __exit__(self, *args):
                            return self.actual.__exit__(*args)
                        def __getattr__(self, name):
                            return getattr(self.actual, name)
                        def write(self, payload):
                            if boundary == "write":
                                self.actual.write(payload[:17])
                                raise OSError("injected partial write")
                            return self.actual.write(payload)
                        def flush(self):
                            if boundary == "flush":
                                raise OSError("injected flush")
                            return self.actual.flush()
                    fsync = os.fsync
                    replace = os.replace
                    def fail_fsync(fd):
                        if boundary == "file_fsync":
                            raise OSError("injected file fsync")
                        return fsync(fd)
                    def fail_replace(source, destination):
                        if boundary == "rename":
                            raise OSError("injected rename")
                        return replace(source, destination)
                    with patch("axiom_data.storage.os.fdopen", side_effect=lambda *a: Stream(fdopen(*a))), \
                            patch("axiom_data.storage.os.fsync", side_effect=fail_fsync), \
                            patch("axiom_data.storage.os.replace", side_effect=fail_replace), \
                            self.assertRaises(OSError):
                        if mode == "legacy":
                            store._atomic(path, _json_bytes(value))
                        else:
                            store._atomic_json(path, value)
                    self.assertEqual(path.read_bytes(), b"original")
                    self.assertFalse(list(Path(directory).glob(".tmp-*")))

    def test_directory_fsync_failure_and_publication_order_match_old_atomic(self):
        value = {"data": [None, "公告😀", -0.0]}
        for mode in ["legacy", "stream"]:
            for fail_directory in [False, True]:
                with self.subTest(mode=mode, fail_directory=fail_directory), tempfile.TemporaryDirectory() as directory:
                    store = LocalStore(directory)
                    path = Path(directory) / "object.json"
                    events = []
                    fsync, replace = os.fsync, os.replace
                    def record_fsync(fd):
                        kind = "directory_fsync" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file_fsync"
                        events.append(kind)
                        if fail_directory and kind == "directory_fsync":
                            raise OSError("injected directory fsync after rename")
                        return fsync(fd)
                    def record_replace(source, destination):
                        events.append("rename")
                        return replace(source, destination)
                    with patch("axiom_data.storage.os.fsync", side_effect=record_fsync), \
                            patch("axiom_data.storage.os.replace", side_effect=record_replace):
                        if fail_directory:
                            with self.assertRaises(OSError):
                                store._atomic(path, _json_bytes(value)) if mode == "legacy" else store._atomic_json(path, value)
                        else:
                            store._atomic(path, _json_bytes(value)) if mode == "legacy" else store._atomic_json(path, value)
                    self.assertEqual(events, ["file_fsync", "rename", "directory_fsync"])
                    self.assertEqual(path.read_bytes(), _json_bytes(value))
                    self.assertFalse(list(Path(directory).glob(".tmp-*")))

    def test_immutable_object_created_during_write_is_compared_and_kept(self):
        value = {"payload": "公告😀" * 24000}
        for concurrent in [b"different", _json_bytes(value)]:
            with self.subTest(same=concurrent != b"different"), tempfile.TemporaryDirectory() as directory:
                store = LocalStore(directory)
                path = Path(directory) / "immutable.json"
                fsync = os.fsync
                def concurrent_publish(fd):
                    if not stat.S_ISDIR(os.fstat(fd).st_mode):
                        path.write_bytes(concurrent)
                    return fsync(fd)
                with patch("axiom_data.storage.os.fsync", side_effect=concurrent_publish):
                    if concurrent == b"different":
                        with self.assertRaises(ConflictError):
                            store._atomic_json(path, value, immutable=True)
                    else:
                        store._atomic_json(path, value, immutable=True)
                self.assertEqual(path.read_bytes(), concurrent)
                self.assertFalse(list(Path(directory).glob(".tmp-*")))


if __name__ == "__main__":
    unittest.main()
