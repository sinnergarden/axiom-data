"""Independent expectations for terminal state transitions and hybrid PIT."""
from dataclasses import replace
import json
import tempfile
import unittest

from axiom_data import Data, IngestBatch, QuerySpec, UpdateRequest, QueryError
from axiom_data.updates import apply_saved_raw


CONTRACT = {"contract_id": "terminal.v1", "logical_key": ["security_id", "session"],
            "fields": {"security_id": {"dtype": "string"}, "session": {"dtype": "date"},
                       "close": {"dtype": "float64", "unit": "CNY"}}}
PROFILE = {"id": "terminal.v1", "revision_order": "terminal_observation_v1",
           "field_map": {k: k for k in CONTRACT["fields"]}, "source_units": {"close": "CNY"},
           "availability": {"timezone": "Asia/Shanghai", "session_release_time": "20:00:00"}}


def batch(price, observed):
    return IngestBatch("market_daily", json.dumps([{"security_id": "A", "session": "2020-01-02", "close": price}]).encode(),
                       {}, CONTRACT, PROFILE, observed)


def query(cutoff):
    return QuerySpec("market_daily", ("close",), ("A",), ("2020-01-02",),
                     "operational_pit_v1", {"2020-01-02": cutoff})


class CompletionTemporalTest(unittest.TestCase):
    def test_terminal_return_to_old_value_preserves_intermediate_state(self):
        with tempfile.TemporaryDirectory() as root:
            data = Data(root)
            parent = None
            snapshots = []
            for i, price in enumerate([10.0, 11.0, 10.0]):
                result = data.update(base_snapshot=parent, request=UpdateRequest(
                    (batch(price, f"2020-01-0{i+3}T00:00:00Z"),), f"u{i}", {}))
                parent = result.snapshot_id
                snapshots.append(parent)
            for day, price in [(3,10.0),(4,11.0),(5,10.0)]:
                self.assertEqual(data.read(snapshot=parent, query=query(f"2020-01-0{day}T12:00:00Z")).frame.close.iloc[0], price)
            duplicate = data.update(base_snapshot=parent, request=UpdateRequest(
                (batch(10.0,"2020-01-06T00:00:00Z"),), "same", {}))
            self.assertFalse(duplicate.changed)
            self.assertEqual(data.read(snapshot=snapshots[1], query=query("2020-01-09T00:00:00Z")).frame.close.iloc[0],11.0)
            self.assertIn("vendor revision/publication order is unknown", data.read(snapshot=parent, query=query("2020-01-09T00:00:00Z")).context["limitations"][0])

    def test_hybrid_has_explicit_map_and_separate_cache_identity(self):
        with tempfile.TemporaryDirectory() as root:
            data = Data(root)
            result = data.update(base_snapshot=None, request=UpdateRequest((batch(10,"2026-01-01T00:00:00Z"),),"u",{}))
            q = replace(query("2020-01-03T00:00:00Z"), pit_policy="bootstrap_hybrid_v1")
            with self.assertRaises(QueryError): data.read(snapshot=result.snapshot_id, query=q)
            best = replace(q,policy_by_session={"2020-01-02":"best_effort_vendor_v1"})
            strict = replace(q,policy_by_session={"2020-01-02":"operational_pit_v1"})
            self.assertEqual(data.read(snapshot=result.snapshot_id,query=best).frame.close.iloc[0],10)
            self.assertIsNone(data.read(snapshot=result.snapshot_id,query=strict).to_json()["records"][0]["close"])

    def test_saved_raw_publishes_original_reference_without_second_observation(self):
        with tempfile.TemporaryDirectory() as root:
            data = Data(root); source=batch(10,"2020-01-03T00:00:00Z")
            raw=data.store.write_raw(source.payload, domain=source.domain, request=source.request,
                                     source_profile=source.source_profile, contract=source.contract,
                                     observed_at=source.observed_at, normalizer=source.normalizer)
            result=apply_saved_raw(data.store,base_snapshot=None,raw_batch_ids=[raw["batch_id"]],operation_id="publish",build_context={})
            self.assertEqual(len((data.store.root/'raw/fetches.jsonl').read_text().splitlines()),1)
            self.assertEqual(data.read(snapshot=result.snapshot_id,query=query("2020-01-04T00:00:00Z")).field_meta["close"]["by_key"][0]["raw_batch_id"],raw["batch_id"])


if __name__ == "__main__": unittest.main()
