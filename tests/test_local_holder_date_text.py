"""Focused holder date-text boundaries and immutable saved-Raw replay."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import json
import tempfile
import unittest

from axiom_data import Data, EventQuery
from axiom_data.protocols import DataError, IngestBatch
from axiom_data.sources import normalize_batch
from axiom_data.stock_input_sources import prepare_stock_input_rows, stock_input_source_spec
from axiom_data.storage import LocalStore


IDS = {"000001.SZ": "sec-a"}
CAL = {"2024-04-29": "2024-04-30"}
OBS = datetime(2024, 4, 29, 8, tzinfo=timezone.utc)
POLICY = "native_date_text_normalization"
ROW = {"ts_code": "000001.SZ", "ann_date": "20240429",
       "end_date": "20240331", "holder_num": 42}


def batch(row, *, declared=True, params=None):
    spec = stock_input_source_spec("stk_holdernumber", identity_map=IDS,
                                   next_open_session_by_date=CAL)
    profile = deepcopy(spec["source_profile"])
    if not declared:
        profile.pop(POLICY)
    return IngestBatch(spec["domain"], json.dumps([row], separators=(",", ":")).encode(),
        {"endpoint": "stk_holdernumber", "fields": spec["fields"],
         "params": params or {"start_date": "20240401", "end_date": "20240430"},
         "canonical_symbols": list(IDS)}, spec["contract"], profile, OBS, spec["normalizer"])


class HolderDateTextTests(unittest.TestCase):
    def test_normal_dates_and_outer_ascii_whitespace_have_identical_facts(self):
        raw = {"batch_id": "b-synthetic", "observed_at": OBS.isoformat()}
        legacy = batch(ROW, declared=False)
        normal = batch(ROW)
        padded = batch({**ROW, "ann_date": " \t20240429\r\n",
                        "end_date": "\v20240331\f "}, params={"ann_date": "20240429"})
        original = padded.payload
        expected = normalize_batch(legacy, raw)
        self.assertEqual(normalize_batch(normal, raw), expected)
        self.assertEqual(normalize_batch(padded, raw), expected)
        self.assertEqual(padded.payload, original)
        with self.assertRaises(DataError):
            prepare_stock_input_rows(batch({**ROW, "ann_date": "20240429 "}, declared=False))

    def test_invalid_and_null_dates_keep_rejection_and_scope_checks(self):
        for field in ("ann_date", "end_date"):
            for value in ("2024 0429", "20240230", None, "", " \t\r\n", 20240429, "\u00a020240429"):
                for declared in (False, True):
                    with self.subTest(field=field, value=value, declared=declared), self.assertRaises(DataError):
                        prepare_stock_input_rows(batch({**ROW, field: value}, declared=declared))
        valid = batch(ROW)
        full = replace(valid, payload=json.dumps([ROW, {**ROW, "ts_code": "999999.SZ",
                                                      "ann_date": "2024 0429"}]).encode())
        with self.assertRaises(DataError):
            prepare_stock_input_rows(full)  # Invalid unselected native rows remain rejected.
        with self.assertRaisesRegex(DataError, "outside requested date scope"):
            prepare_stock_input_rows(batch({**ROW, "ann_date": " 20240503 "}))
        original = batch(ROW, params={"ann_date": "20240429 "})
        with self.assertRaises(DataError):
            prepare_stock_input_rows(original)  # Native normalization never repairs selectors.
        foreign = batch(ROW)
        foreign.source_profile[POLICY]["fields"].append("holder_num")
        with self.assertRaisesRegex(DataError, "unsupported.*normalization"):
            prepare_stock_input_rows(foreign)
        self.assertEqual(batch(ROW).source_profile[POLICY]["fields"], ["ann_date", "end_date"])

    def test_explicit_failed_raw_replay_preserves_bytes_receipt_and_cutoff(self):
        original = batch({**ROW, "ann_date": "20240429 "}, declared=False)
        spec = stock_input_source_spec("stk_holdernumber", identity_map=IDS,
                                      next_open_session_by_date=CAL)
        with tempfile.TemporaryDirectory() as root:
            store = LocalStore(root)
            raw = store.write_raw(original.payload, request=original.request,
                source_profile=original.source_profile, contract=original.contract,
                normalizer=original.normalizer, domain=original.domain,
                observed_at=OBS, status="failed", operation_id="original", batch_index=0)
            before = deepcopy(store.get_raw(raw["batch_id"]))
            base = store.publish_snapshot({}, parent_snapshot=None, build_context={}, promote=False)
            result = Data(root).rebuild(base_snapshot=base["snapshot_id"],
                raw_batch_ids=[raw["batch_id"]], domains=[original.domain],
                operation_id="explicit-date-text-replay", promote=False,
                build_context={"synthetic": True, "replay_raw_batch_id": raw["batch_id"]},
                domain_overrides={original.domain: {"source_profile": spec["source_profile"]}})
            saved = store.load_snapshot(result.snapshot_id)
            self.assertEqual(saved["domains"][original.domain]["raw_batch_ids"], [raw["batch_id"]])
            self.assertEqual(saved["domains"][original.domain]["source_profile"][POLICY],
                             spec["source_profile"][POLICY])
            self.assertEqual(store.load_snapshot(base["snapshot_id"]), base)
            self.assertEqual(store.get_raw(raw["batch_id"]), before)
            self.assertEqual(store.read_raw_record(raw), original.payload)

            def read(cutoff, policy="operational_pit_v1"):
                q = EventQuery(original.domain, ("holder_num", "raw_batch_id", "first_observed_at"), ("sec-a",),
                    "2024-03-31", "2024-03-31", cutoff, policy, "report_period")
                return Data(root).events(snapshot=result.snapshot_id, query=q).to_json()["records"]
            late = read("2024-04-30T10:00:00+08:00")[0]
            early = read("2024-04-29T07:59:59+00:00")  # Immediately before the original receipt.
            self.assertEqual(late["holder_num"], 42)
            self.assertEqual(late["raw_batch_id"], raw["batch_id"])
            self.assertEqual(late["first_observed_at"], raw["observed_at"])
            self.assertEqual(early, [])
            self.assertEqual(read("2024-04-30T10:00:00+08:00", "best_effort_vendor_v1")[0]["holder_num"], 42)
            self.assertEqual(read("2024-04-29T23:59:59+08:00", "best_effort_vendor_v1"), [])


if __name__ == "__main__":
    unittest.main()
