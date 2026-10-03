"""Independent acceptance checks for evidence repair and interval PIT."""

import json
import tempfile
import unittest

from axiom_data import Data, IngestBatch, QuerySpec, UpdateRequest
from axiom_data.storage import LocalStore


class CompletionAcceptanceTests(unittest.TestCase):
    def test_new_revision_bound_evidence_changes_safe_pit_without_rewriting_old_snapshot(self):
        contract = {
            "contract_id": "evidence-repair-v1",
            "logical_key": ["security_id", "session"],
            "fields": {
                "security_id": {"dtype": "string", "nullable": False},
                "session": {"dtype": "date", "nullable": False},
                "close": {"dtype": "float64", "unit": "CNY/share"},
            },
        }
        profile = {
            "id": "terminal-with-evidence-v1",
            "revision_order": "terminal_observation_v1",
            "field_map": {"security_id": "security_id", "session": "session",
                          "close": "close", "source_available_at": "public_at",
                          "evidence_ref": "public_ref"},
            "source_units": {"close": "CNY/share"},
            "availability": {"timezone": "Asia/Shanghai", "session_release_time": "20:00:00"},
        }
        value = {"security_id": "A", "session": "2020-01-02", "close": 10.0}

        def update(data, base, operation, source_row, observed):
            batch = IngestBatch("market_daily", json.dumps([source_row]).encode(), {},
                                contract, profile, observed)
            return data.update(base_snapshot=base, request=UpdateRequest(
                (batch,), operation, {"fixture": "evidence repair"}))

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            data = Data(directory)
            old = update(data, None, "observation_1", value, "2025-01-01T00:00:00Z").snapshot_id
            with_evidence = {**value, "public_at": "2020-01-02T12:00:00Z",
                             "public_ref": "revision-bound-announcement-1"}
            result = update(data, old, "observation_2", with_evidence,
                            "2025-01-02T00:00:00Z")
            query = QuerySpec("market_daily", ("close",), ("A",), ("2020-01-02",),
                              "market_pit_safe_v1", {"2020-01-02": "2020-01-02T13:00:00Z"})
            self.assertIsNone(data.read(snapshot=old, query=query).to_json()["records"][0]["close"])
            self.assertTrue(result.changed, "revision-bound evidence must produce a new Snapshot")
            self.assertEqual(data.read(snapshot=result.snapshot_id, query=query).frame.close.iloc[0], 10.0)

    def test_membership_interval_remains_visible_after_start_day_release(self):
        contract = {
            "contract_id": "membership-interval-v1",
            "logical_key": ["membership_id"],
            "fields": {"membership_id": {"dtype": "string"},
                       "security_id": {"dtype": "string"},
                       "universe_id": {"dtype": "string"},
                       "effective_from": {"dtype": "date"},
                       "effective_to": {"dtype": "date"}},
        }
        profile = {"id": "interval-vendor-v1",
                   "availability": {"timezone": "Asia/Shanghai", "session_release_time": "20:00:00"}}
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            store = LocalStore(directory)
            source = store.write_raw(b"[]", request={"fixture": "interval"},
                                     source_profile=profile, observed_at="2025-01-01T00:00:00Z")
            rows = [{"membership_id": "interval-1", "security_id": "A", "universe_id": "IDX",
                     "effective_from": "2020-01-02", "effective_to": "2020-01-10",
                     "revision_id": "r1", "revision_sequence": 1,
                     "first_observed_at": "2025-01-01T00:00:00Z",
                     "raw_batch_id": source["batch_id"]}]
            partition = store.write_partition("universe_membership", "history", rows, contract)
            snapshot = store.publish_snapshot({"universe_membership": {
                "contract": contract, "source_profile": profile, "partitions": [partition],
                "raw_batch_ids": [source["batch_id"]],
                "coverage": {"complete_states": [{
                    "universe_id": "IDX", "effective_from": "2020-01-02",
                    "effective_to": "2020-01-10", "complete": True,
                    "members": ["A"], "first_observed_at": "2025-01-01T00:00:00Z"}]},
                "build_context": {}}},
                parent_snapshot=None, build_context={})["snapshot_id"]
            query = QuerySpec("universe_membership", ("is_member",), ("A", "B"),
                              ("2020-01-03",), "best_effort_vendor_v1",
                              {"2020-01-03": "2020-01-03T09:30:00+08:00"},
                              universe_id="IDX")
            result = Data(directory).members(snapshot=snapshot, query=query)
            self.assertEqual([row["is_member"] for row in result.to_json()["records"]],
                             [True, False], "the interval and complete group were released the previous evening")

    def test_reference_identity_stays_visible_and_uncertain_vendor_states_are_named(self):
        days = ("2020-01-03", "2020-01-06", "2020-01-07")

        def contract(name, key, fields):
            return {"contract_id": f"{name}-acceptance-v1", "logical_key": key,
                    "fields": {field: {"dtype": dtype} for field, dtype in fields.items()}}

        def add(store, name, rows, spec, release, partition):
            profile = {"id": name, "availability": {"timezone": "Asia/Shanghai",
                       "session_release_time": release}}
            raw = store.write_raw(b"[]", request={"fixture": name},
                                  source_profile=profile, observed_at="2025-01-01T00:00:00Z")
            versioned = [{**row, "revision_id": "r1", "revision_sequence": 1,
                          "first_observed_at": "2025-01-01T00:00:00Z",
                          "raw_batch_id": raw["batch_id"]} for row in rows]
            part = store.write_partition(name, partition, versioned, spec)
            return {"contract": spec, "source_profile": profile, "partitions": [part],
                    "raw_batch_ids": [raw["batch_id"]], "coverage": {}, "build_context": {}}

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            store = LocalStore(directory)
            domains = {
                "trading_calendar": add(store, "trading_calendar",
                    [{"exchange": "SSE", "session": day, "is_open": True} for day in days],
                    contract("calendar", ["exchange", "session"],
                             {"exchange": "string", "session": "date", "is_open": "bool"}),
                    "08:00:00", "2020-01"),
                "security_master": add(store, "security_master", [{
                    "security_id": "A", "exchange": "SSE", "listing_date": "2019-01-02",
                    "delisting_date": None, "vendor_delist_date": "2020-01-07", "list_status": "D"}],
                    contract("master", ["security_id", "listing_date"],
                             {"security_id": "string", "exchange": "string",
                              "listing_date": "date", "delisting_date": "date",
                              "vendor_delist_date": "date", "list_status": "string"}),
                    "20:00:00", "history"),
                "security_status": add(store, "security_status", [
                    {"security_id": "A", "session": "2020-01-03", "is_suspended": False,
                     "status_reason": "explicit_resumption"},
                    {"security_id": "A", "session": "2020-01-06", "is_suspended": None,
                     "status_reason": "partial_session_suspension"}],
                    contract("status", ["security_id", "session"],
                             {"security_id": "string", "session": "date", "is_suspended": "bool",
                              "status_reason": "string"}), "08:00:00", "2020-01"),
                "market_daily": add(store, "market_daily", [
                    {"security_id": "A", "session": day, "close": 10.0} for day in days],
                    contract("market", ["security_id", "session"],
                             {"security_id": "string", "session": "date", "close": "float64"}),
                    "08:00:00", "2020-01"),
            }
            snapshot = store.publish_snapshot(domains, parent_snapshot=None,
                                              build_context={})["snapshot_id"]
            result = Data(directory).states(snapshot=snapshot, query=QuerySpec(
                "market_daily", ("close",), ("A",), days, "best_effort_vendor_v1",
                {days[0]: "2020-01-03T09:30:00+08:00",
                 days[1]: "2020-01-06T21:00:00+08:00",
                 days[2]: "2020-01-07T21:00:00+08:00"}))
            self.assertEqual(result.frame.market_state.tolist(),
                             ["normal_trading", "unknown_status", "unknown_status"])
            reasons = [item["missing_reason"] for item in result.field_meta["market_state"]["by_key"]]
            self.assertEqual(reasons, [None, "partial_session_suspension",
                                       "status_source_missing"])


if __name__ == "__main__":
    unittest.main()
