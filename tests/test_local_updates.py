"""Raw-first update and offline rebuild behavior on small synthetic roots."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from axiom_data.protocols import ConflictError, DataError, IngestBatch, UpdateRequest
from axiom_data.storage import LocalStore
from axiom_data.updates import apply_update, rebuild_from_raw


CONTRACT = {
    "contract_id": "synthetic.daily.v1", "logical_key": ["security_id", "session"],
    "fields": {
        "security_id": {"dtype": "string", "nullable": False},
        "session": {"dtype": "date", "nullable": False},
        "close": {"dtype": "float64", "unit": "CNY/share", "nullable": True},
    },
}
PROFILE = {
    "id": "synthetic.v1", "field_map": {
        "security_id": "security_id", "session": "session", "close": "close",
        "revision_id": "revision", "revision_sequence": "sequence",
    }, "source_units": {"close": "CNY/share"},
    "availability": {"timezone": "Asia/Shanghai", "session_release_time": "20:00:00"},
}
CONTEXT = {"code_ref": "synthetic-test-v1", "config": {"partition": "month"}}


def observed(day: int) -> datetime:
    return datetime(2026, 9, day, 12, tzinfo=timezone.utc)


def batch(rows, *, day=1, domain="market_daily", request=None, contract=None, profile=None):
    return IngestBatch(domain, json.dumps(rows, sort_keys=True).encode(),
                       request or {"endpoint": "synthetic", "sessions": ["2020-01-02"]},
                       contract or CONTRACT, profile or PROFILE, observed(day))


def row(close=10, *, revision="r1", sequence=1, session="2020-01-02"):
    return {"security_id": "sec-A", "session": session, "close": close,
            "revision": revision, "sequence": sequence}


def update(store, base, operation, *batches):
    return apply_update(store, base_snapshot=base,
                        request=UpdateRequest(batches, operation, CONTEXT))


def stored_rows(store, snapshot, domain="market_daily"):
    manifest = store.load_snapshot(snapshot)
    return [item for part in manifest["domains"][domain]["partitions"]
            for item in store.read_partition(part).to_pylist()]


class LocalUpdatesTest(unittest.TestCase):
    def test_raw_first_idempotent_repeat_and_earliest_observation(self):
        with tempfile.TemporaryDirectory() as temp:
            store = LocalStore(temp)
            first = update(store, None, "initial", batch([row()], day=1))
            self.assertTrue(first.changed)
            self.assertEqual(store.resolve("current"), first.snapshot_id)
            same_op = update(LocalStore(temp), None, "initial", batch([row()], day=1))
            self.assertEqual(same_op, first)
            self.assertEqual(len((Path(temp) / "raw/fetches.jsonl").read_text().splitlines()), 1)
            repeated = update(store, first.snapshot_id, "repeat", batch([row()], day=2))
            self.assertFalse(repeated.changed)
            self.assertEqual(repeated.snapshot_id, first.snapshot_id)
            self.assertEqual(store.resolve("current"), first.snapshot_id)
            self.assertEqual(len((Path(temp) / "raw/fetches.jsonl").read_text().splitlines()), 2)
            original = stored_rows(store, first.snapshot_id)[0]
            self.assertEqual(original["first_observed_at"], observed(1))
            self.assertEqual(original["raw_batch_id"],
                             store.read_operation("initial")["raw_batch_ids"][0])
            self.assertNotIn("completed", store.load_snapshot(first.snapshot_id)
                             ["domains"]["market_daily"]["coverage"])
            with self.assertRaises(ConflictError):
                update(store, None, "initial", batch([row(close=99)], day=1))

    def test_equal_observation_time_keeps_first_raw_reference(self):
        with tempfile.TemporaryDirectory() as temp:
            store = LocalStore(temp)
            first = update(store, None, "equal-initial", batch([row()], day=1))
            original_ref = stored_rows(store, first.snapshot_id)[0]["raw_batch_id"]
            repeat = update(store, first.snapshot_id, "equal-repeat", batch([row()], day=1))
            self.assertFalse(repeat.changed)
            self.assertEqual(repeat.snapshot_id, first.snapshot_id)
            self.assertEqual(stored_rows(store, first.snapshot_id)[0]["raw_batch_id"], original_ref)
            self.assertEqual(len((Path(temp) / "raw/fetches.jsonl").read_text().splitlines()), 2)

    def test_revisions_conflicts_and_old_snapshot_stability(self):
        with tempfile.TemporaryDirectory() as temp:
            store = LocalStore(temp)
            first = update(store, None, "rev-initial", batch([row()], day=1))
            second = update(store, first.snapshot_id, "rev-second",
                            batch([row(20, revision="r2", sequence=2)], day=2))
            self.assertTrue(second.changed)
            self.assertEqual([item["close"] for item in stored_rows(store, first.snapshot_id)], [10])
            self.assertEqual({item["revision_id"] for item in stored_rows(store, second.snapshot_id)},
                             {"r1", "r2"})
            conflict = batch([row(25, revision="r-other", sequence=2)], day=3)
            with self.assertRaisesRegex(ConflictError, "identical sequence"):
                update(store, second.snapshot_id, "rev-conflict", conflict)
            self.assertEqual(store.resolve("current"), second.snapshot_id)
            self.assertEqual(store.read_operation("rev-conflict")["status"], "failed")
            raw_count = len((Path(temp) / "raw/fetches.jsonl").read_text().splitlines())
            with self.assertRaises(ConflictError):
                update(LocalStore(temp), second.snapshot_id, "rev-conflict", conflict)
            self.assertEqual(len((Path(temp) / "raw/fetches.jsonl").read_text().splitlines()), raw_count)
            with self.assertRaisesRegex(ConflictError, "identical source revision"):
                update(store, second.snapshot_id, "rev-content",
                       batch([row(21, revision="r2", sequence=2)], day=3))

    def test_only_affected_partition_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            store = LocalStore(temp)
            initial = update(store, None, "part-initial", batch([
                row(session="2020-01-02"),
                row(30, revision="feb-r1", session="2020-02-03"),
            ], request={"endpoint": "synthetic", "sessions": ["2020-01-02", "2020-02-03"]}))
            revised = update(store, initial.snapshot_id, "part-revised",
                             batch([row(11, revision="r2", sequence=2)], day=2))
            before = {part["partition"]: part for part in store.load_snapshot(initial.snapshot_id)
                      ["domains"]["market_daily"]["partitions"]}
            after = {part["partition"]: part for part in store.load_snapshot(revised.snapshot_id)
                     ["domains"]["market_daily"]["partitions"]}
            self.assertEqual(before["2020-02"], after["2020-02"])
            self.assertNotEqual(before["2020-01"], after["2020-01"])

    def test_failed_normalization_resumes_saved_raw_in_new_session(self):
        with tempfile.TemporaryDirectory() as temp:
            store = LocalStore(temp)
            input_batch = batch([row()])
            from axiom_data.sources import normalize_batch
            with patch("axiom_data.updates.normalize_batch", side_effect=DataError("transient parser failure")):
                with self.assertRaisesRegex(DataError, "transient parser failure"):
                    update(store, None, "resume-op", input_batch)
            saved = store.read_operation("resume-op")
            self.assertEqual(saved["status"], "failed")
            raw_id = saved["raw_batch_ids"][0]
            self.assertFalse((Path(temp) / "current.json").exists())
            with patch("axiom_data.updates.normalize_batch", wraps=normalize_batch) as parser:
                result = update(LocalStore(temp), None, "resume-op", input_batch)
                self.assertEqual(parser.call_count, 1)
            self.assertTrue(result.changed)
            self.assertEqual(stored_rows(store, result.snapshot_id)[0]["raw_batch_id"], raw_id)
            self.assertEqual(len((Path(temp) / "raw/fetches.jsonl").read_text().splitlines()), 1)

    def test_crash_between_raw_append_and_checkpoint_recovers_log_record(self):
        with tempfile.TemporaryDirectory() as temp:
            store = LocalStore(temp)
            source = batch([row()])
            original_write = store.write_operation

            def interrupt_after_raw(operation_id, state):
                if state.get("raw_batch_ids"):
                    raise KeyboardInterrupt("simulated process interruption")
                return original_write(operation_id, state)

            with patch.object(store, "write_operation", side_effect=interrupt_after_raw):
                with self.assertRaises(KeyboardInterrupt):
                    update(store, None, "raw-gap", source)
            checkpoint = store.read_operation("raw-gap")
            self.assertEqual(checkpoint["raw_batch_ids"], [])
            raw_record = store.find_raw_by_operation("raw-gap")[0]
            resumed = update(LocalStore(temp), None, "raw-gap", source)
            self.assertEqual(stored_rows(store, resumed.snapshot_id)[0]["raw_batch_id"],
                             raw_record["batch_id"])
            self.assertEqual(len((Path(temp) / "raw/fetches.jsonl").read_text().splitlines()), 1)

    def test_rebuild_selected_raw_offline_and_reuse_other_domain(self):
        with tempfile.TemporaryDirectory() as temp:
            store = LocalStore(temp)
            first = update(store, None, "rebuild-initial",
                           batch([row()], day=1),
                           batch([row(5)], day=1, domain="independent_daily"))
            second = update(store, first.snapshot_id, "rebuild-revision",
                            batch([row(20, revision="r2", sequence=2)], day=2))
            saved_raw = store.read_operation("rebuild-initial")["raw_batch_ids"][0]
            raw_count = len((Path(temp) / "raw/fetches.jsonl").read_text().splitlines())
            rebuilt = rebuild_from_raw(LocalStore(temp), base_snapshot=second.snapshot_id,
                                       raw_batch_ids=[saved_raw], domains=["market_daily"],
                                       operation_id="rebuild-selected", build_context=CONTEXT)
            self.assertTrue(rebuilt.changed)
            self.assertEqual(len((Path(temp) / "raw/fetches.jsonl").read_text().splitlines()), raw_count)
            self.assertEqual({item["revision_id"] for item in stored_rows(store, rebuilt.snapshot_id)},
                             {"r1"})
            self.assertEqual(stored_rows(store, rebuilt.snapshot_id)[0]["first_observed_at"], observed(1))
            previous_other = store.load_snapshot(second.snapshot_id)["domains"]["independent_daily"]
            rebuilt_other = store.load_snapshot(rebuilt.snapshot_id)["domains"]["independent_daily"]
            self.assertEqual(previous_other, rebuilt_other)
            self.assertEqual(len(stored_rows(store, second.snapshot_id)), 2)
            self.assertEqual(rebuild_from_raw(store, base_snapshot=second.snapshot_id,
                                              raw_batch_ids=[saved_raw], domains=["market_daily"],
                                              operation_id="rebuild-selected",
                                              build_context=CONTEXT), rebuilt)

    def test_rebuild_can_correct_mapping_from_original_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            store = LocalStore(temp)
            wrong_profile = deepcopy(PROFILE)
            wrong_profile["source_units"] = {"close": "cent/share"}
            wrong_profile["unit_conversions"] = {
                "close": {"from": "cent/share", "to": "CNY/share", "factor": "1"}}
            initial = update(store, None, "wrong-mapping",
                             batch([row(1050)], profile=wrong_profile))
            raw_id = store.read_operation("wrong-mapping")["raw_batch_ids"][0]
            corrected = deepcopy(wrong_profile)
            corrected["unit_conversions"]["close"]["factor"] = "0.01"
            rebuilt = rebuild_from_raw(store, base_snapshot=initial.snapshot_id,
                                       raw_batch_ids=[raw_id], domains=["market_daily"],
                                       operation_id="correct-mapping", build_context=CONTEXT,
                                       domain_overrides={"market_daily": {
                                           "source_profile": corrected}})
            self.assertEqual(stored_rows(store, initial.snapshot_id)[0]["close"], 1050)
            self.assertEqual(stored_rows(store, rebuilt.snapshot_id)[0]["close"], 10.5)
            self.assertEqual(stored_rows(store, rebuilt.snapshot_id)[0]["raw_batch_id"], raw_id)
            self.assertEqual(store.get_raw(raw_id)["source_profile"], wrong_profile)
            self.assertEqual(len((Path(temp) / "raw/fetches.jsonl").read_text().splitlines()), 1)

    def test_stale_promotion_rejected_but_retry_after_publish_recovers(self):
        with tempfile.TemporaryDirectory() as temp:
            store = LocalStore(temp)
            first = update(store, None, "stale-base", batch([row()]))
            second = update(store, first.snapshot_id, "stale-next",
                            batch([row(20, revision="r2", sequence=2)], day=2))
            with self.assertRaisesRegex(ConflictError, "current Snapshot changed"):
                update(store, first.snapshot_id, "stale-conflict",
                       batch([row(30, revision="r3", sequence=3)], day=3))
            with self.assertRaisesRegex(ConflictError, "current Snapshot changed"):
                update(store, first.snapshot_id, "stale-duplicate", batch([row()], day=3))
            self.assertEqual(store.resolve("current"), second.snapshot_id)

            next_batch = batch([row(40, revision="r4", sequence=4)], day=4)
            original_write = store.write_operation

            def fail_after_publish(operation_id, state):
                if operation_id == "publish-gap" and state.get("status") == "success":
                    raise OSError("checkpoint interrupted")
                return original_write(operation_id, state)

            with patch.object(store, "write_operation", side_effect=fail_after_publish):
                with self.assertRaisesRegex(DataError, "checkpoint interrupted"):
                    update(store, second.snapshot_id, "publish-gap", next_batch)
            published = store.resolve("current")
            self.assertNotEqual(published, second.snapshot_id)
            retried = update(LocalStore(temp), second.snapshot_id, "publish-gap", next_batch)
            self.assertEqual(retried.snapshot_id, published)
            self.assertEqual(store.resolve("current"), published)


if __name__ == "__main__":
    unittest.main()
