"""Synthetic publication ownership, exact bytes and interrupted saved-Raw recovery."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from axiom_data.builder import freeze_builder
from axiom_data.protocols import ConflictError, DataError
from axiom_data.storage import LocalStore, _json_bytes
from axiom_data import updates
from test_local_updates import batch, row, update, stored_rows, CONTEXT


def saved(store, source):
    return store.write_raw(source.payload, domain=source.domain,
                           request=source.request, contract=source.contract,
                           source_profile=source.source_profile, normalizer=source.normalizer,
                           observed_at=source.observed_at)["batch_id"]


def fixture(directory):
    store = LocalStore(directory)
    base = update(store, None, "seed", batch([
        row(), row(30, revision="feb", session="2020-02-03")]),
        batch([row(9)], domain="independent_daily")).snapshot_id
    incoming = saved(store, batch([
        row(11, revision="r2", sequence=2),
        row(12, revision="march", session="2020-03-02")], day=2))
    return store, base, incoming


def apply(store, base, incoming, operation="candidate", *, promote=False, context=None):
    return updates.apply_saved_raw(store, base_snapshot=base, raw_batch_ids=[incoming],
                                   operation_id=operation, build_context=context or CONTEXT,
                                   promote=promote)


class SnapshotPublicationTest(unittest.TestCase):
    def test_update_keeps_complete_parent_unchanged_and_loads_it_once(self):
        with tempfile.TemporaryDirectory() as directory:
            store, base, incoming = fixture(directory)
            base_file = Path(directory) / "snapshots" / (base + ".json")
            original_bytes = base_file.read_bytes()
            loaded = []
            load = store.load_snapshot
            publish = store._publish_snapshot_from_parent

            def capture_load(identity):
                manifest = load(identity)
                loaded.append((manifest, deepcopy(manifest)))
                return manifest

            def capture_publish(domains, **kwargs):
                parent = kwargs["parent"]
                self.assertIs(parent, loaded[0][0])
                self.assertEqual(parent, loaded[0][1])
                self.assertIs(domains["independent_daily"], parent["domains"]["independent_daily"])
                self.assertIsNot(domains["market_daily"], parent["domains"]["market_daily"])
                return publish(domains, **kwargs)

            with patch.object(store, "load_snapshot", side_effect=capture_load), \
                    patch.object(store, "_publish_snapshot_from_parent", side_effect=capture_publish), \
                    patch.object(store, "publish_snapshot", side_effect=AssertionError("public parent reload")):
                result = apply(store, base, incoming)
            self.assertTrue(result.changed)
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0][0], loaded[0][1])
            self.assertEqual(base_file.read_bytes(), original_bytes)
            self.assertEqual(store.resolve("current"), base)
            self.assertEqual([r["close"] for r in stored_rows(store, base)], [10, 30])

    def test_private_and_former_publication_paths_have_exact_snapshot_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            store, base, incoming = fixture(directory)
            parent = store.load_snapshot(base)
            result = apply(store, base, incoming)
            state = store.read_operation("candidate")
            context = {**deepcopy(CONTEXT), "builder": state["builder"], "operation_id": "candidate"}
            raw = store.get_raw(incoming)
            # The former update path cloned all domains and published through
            # the public method, which loads its own validated parent.
            domains = deepcopy(parent["domains"])
            domains["market_daily"], changed = updates._build_domain(
                store, "market_daily", domains["market_daily"],
                [(updates._SavedBatch(store, raw), raw)], context, replace=False)
            self.assertTrue(changed)
            expected = store.publish_snapshot(domains, parent_snapshot=base,
                                               build_context=context, promote=False)
            self.assertEqual(result.snapshot_id, expected["snapshot_id"])
            path = Path(directory) / "snapshots" / (result.snapshot_id + ".json")
            self.assertEqual(path.read_bytes(), _json_bytes(expected))
            body = {k: v for k, v in expected.items() if k != "snapshot_id"}
            self.assertEqual(result.snapshot_id, "s_" + sha256(_json_bytes(body)).hexdigest())

    def test_private_reuse_requires_writer_and_matching_parent_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            store, base, _ = fixture(directory)
            parent = store.load_snapshot(base)
            arguments = dict(parent_snapshot=base, parent=parent, build_context=CONTEXT, promote=False)
            with self.assertRaisesRegex(DataError, "active writer lock"):
                store._publish_snapshot_from_parent(parent["domains"], **arguments)
            with store.writer(), self.assertRaisesRegex(DataError, "does not match"):
                store._publish_snapshot_from_parent(parent["domains"], **{**arguments, "parent": None})
            with store.writer(), self.assertRaisesRegex(DataError, "does not match"):
                store._publish_snapshot_from_parent(parent["domains"],
                    **{**arguments, "parent_snapshot": "s_wrong"})

    def test_public_publish_freshly_validates_parent_file(self):
        with tempfile.TemporaryDirectory() as directory:
            store, base, _ = fixture(directory)
            parent = store.load_snapshot(base)
            damaged = deepcopy(parent)
            damaged["domains"]["market_daily"]["coverage"]["tampered"] = True
            (Path(directory) / "snapshots" / (base + ".json")).write_bytes(_json_bytes(damaged))
            with self.assertRaisesRegex(DataError, "digest mismatch"):
                store.publish_snapshot(parent["domains"], parent_snapshot=base,
                                       build_context=CONTEXT, promote=False)

    def test_new_bad_partition_and_raw_references_are_still_rejected(self):
        for bad in ("partition", "partition_hash", "raw", "raw_payload"):
            with self.subTest(reference=bad), tempfile.TemporaryDirectory() as directory:
                store, base, incoming = fixture(directory)
                parent = store.load_snapshot(base)
                original = deepcopy(parent)
                pointer = (Path(directory) / "current.json").read_bytes()
                build = updates._build_domain

                def corrupt(*args, **kwargs):
                    rebuilt, changed = build(*args, **kwargs)
                    if bad == "partition":
                        rebuilt["partitions"].append({"partition": "2020-04", "rows": 1,
                            "uri": "canonical/market_daily/missing.parquet", "file_sha256": "0" * 64})
                    elif bad == "partition_hash":
                        rebuilt["partitions"][0] = {**rebuilt["partitions"][0], "file_sha256": "0" * 64}
                    elif bad == "raw":
                        rebuilt["raw_batch_ids"].append("b_missing_new_observation")
                    else:
                        new_raw = store.get_raw(incoming)
                        (Path(directory) / new_raw["payload_uri"]).write_bytes(b"damaged new Raw payload")
                    return rebuilt, changed

                with patch.object(store, "load_snapshot", return_value=parent), \
                        patch.object(updates, "_build_domain", side_effect=corrupt), \
                        self.assertRaises(DataError):
                    apply(store, base, incoming)
                self.assertEqual(parent, original)
                self.assertEqual((Path(directory) / "current.json").read_bytes(), pointer)
                self.assertEqual(store.read_operation("candidate")["status"], "failed")
                self.assertEqual(len(list((Path(directory) / "snapshots").glob("s_*.json"))), 1)

    def test_snapshot_and_pointer_replace_failures_keep_atomic_recovery(self):
        import os
        for boundary in ("snapshot", "current"):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as directory:
                store, base, incoming = fixture(directory)
                pointer = (Path(directory) / "current.json").read_bytes()
                base_path = Path(directory) / "snapshots" / (base + ".json")
                original_bytes = base_path.read_bytes()
                raw_bytes = (Path(directory) / "raw/fetches.jsonl").read_bytes()
                replace = os.replace

                def fail_replace(source, destination):
                    destination = Path(destination)
                    if ((boundary == "snapshot" and destination.parent.name == "snapshots") or
                            (boundary == "current" and destination.name == "current.json")):
                        raise OSError("injected atomic replacement failure")
                    return replace(source, destination)

                with patch("axiom_data.storage.os.replace", side_effect=fail_replace), \
                        self.assertRaises(DataError):
                    apply(store, base, incoming, promote=True)
                self.assertEqual((Path(directory) / "current.json").read_bytes(), pointer)
                self.assertEqual(base_path.read_bytes(), original_bytes)
                self.assertEqual(store.read_operation("candidate")["status"], "failed")
                self.assertFalse(list((Path(directory) / "snapshots").glob(".tmp-*")))
                self.assertFalse(list(Path(directory).glob(".tmp-*")))
                result = apply(LocalStore(directory), base, incoming, promote=True)
                self.assertEqual(store.resolve("current"), result.snapshot_id)
                self.assertEqual((Path(directory) / "raw/fetches.jsonl").read_bytes(), raw_bytes)
                self.assertEqual(base_path.read_bytes(), original_bytes)

    def test_interruption_resumes_same_builder_and_new_builder_needs_new_operation(self):
        with tempfile.TemporaryDirectory() as directory:
            store, base, incoming = fixture(directory)
            actual = freeze_builder()
            raw_bytes = (Path(directory) / "raw/fetches.jsonl").read_bytes()
            with patch.object(store, "_publish_snapshot_from_parent", side_effect=KeyboardInterrupt), \
                    self.assertRaises(KeyboardInterrupt):
                apply(store, base, incoming, "stopped")
            checkpoint = Path(directory) / "operations/stopped.json"
            original_checkpoint = checkpoint.read_bytes()
            self.assertEqual(store.read_operation("stopped")["status"], "running")
            changed = deepcopy(actual)
            changed["source"]["commit"] = "different-reviewed-commit"
            with patch("axiom_data.builder.freeze_builder", return_value=changed):
                with self.assertRaisesRegex(ConflictError, "builder changed"):
                    apply(LocalStore(directory), base, incoming, "stopped")
                context = {**deepcopy(CONTEXT), "continuation": {"operation_id": "stopped",
                           "base_snapshot": base, "builder": actual}}
                result = apply(LocalStore(directory), base, incoming, "continued", context=context)
            self.assertEqual(checkpoint.read_bytes(), original_checkpoint)
            continued = store.read_operation("continued")
            self.assertEqual(continued["builder"], changed)
            self.assertEqual(continued["build_config"]["continuation"]["operation_id"], "stopped")
            self.assertEqual((Path(directory) / "raw/fetches.jsonl").read_bytes(), raw_bytes)
            self.assertEqual(store.resolve("current"), base)
            candidate_parts = {p.relative_to(directory): p.read_bytes()
                               for p in (Path(directory) / "canonical").rglob("*.parquet")}
            # The original builder may independently finish its bound operation;
            # it reuses the same saved observations and immutable partitions.
            resumed = apply(LocalStore(directory), base, incoming, "stopped")
            self.assertEqual(store.read_operation("stopped")["builder"], actual)
            self.assertEqual(store.read_operation("stopped")["status"], "success")
            self.assertEqual({p.relative_to(directory): p.read_bytes()
                             for p in (Path(directory) / "canonical").rglob("*.parquet")}, candidate_parts)
            self.assertEqual(stored_rows(store, resumed.snapshot_id), stored_rows(store, result.snapshot_id))
            self.assertEqual((Path(directory) / "raw/fetches.jsonl").read_bytes(), raw_bytes)


if __name__ == "__main__":
    unittest.main()
