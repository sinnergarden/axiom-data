"""Synthetic, independently expected semantics for the local Snapshot reader."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone
import json
from pathlib import Path
import tempfile

import pyarrow as pa
import unittest
from unittest.mock import patch

from axiom_data.protocols import QueryError, QuerySpec
from axiom_data.reader import SnapshotQueryReader, _release_time, _row_availability
from axiom_data.storage import LocalStore


def utc(day: int, hour: int = 0) -> datetime:
    return datetime(2024, 1, day, hour, tzinfo=timezone.utc)


class MemoryStore:
    def __init__(self, manifest, partitions):
        self.manifest = deepcopy(manifest)
        self.partitions = partitions
        self.calls = []

    def load_snapshot(self, snapshot_id):
        assert snapshot_id == "s1"
        return deepcopy(self.manifest)

    def verify_partition(self, part):
        self.calls.append(("verify", part["partition"]))

    def read_partition(self, part, *, columns=None, symbols=None, sessions=None):
        self.calls.append((part["partition"], tuple(columns), tuple(symbols), sessions))
        rows = self.partitions[part["partition"]]
        rows = [row for row in rows if row.get("security_id") in symbols]
        if sessions is not None:
            rows = [row for row in rows if (
                row.get("session").isoformat() if isinstance(row.get("session"), date)
                else row.get("session")) in sessions]
        return pa.Table.from_pylist([{col: row.get(col) for col in columns} for row in rows])


def daily_manifest(*, availability=None):
    return {
        "snapshot_id": "s1", "domains": {"market_daily": {
            "contract": {"contract_id": "market_v1", "logical_key": ["security_id", "session"],
                         "fields": {"close": {"dtype": "float64", "unit": "CNY"},
                                    "volume": {"dtype": "int64", "unit": "shares"}}},
            "source_profile": {"id": "vendor_v1", "availability": availability or {
                "timezone": "Asia/Shanghai", "session_release_time": "17:00:00"}},
            "partitions": [{"partition": "2023-12"}, {"partition": "2024-01"}, {"partition": "2024-02"}],
            "coverage": {"requested": ["2024-01-02"]},
        }},
    }


def daily_row(*, revision="r1", sequence=1, observed=None, close=10.0,
              source_available=None, evidence=None, symbol="A", session="2024-01-02"):
    return {
        "security_id": symbol, "session": session, "revision_id": revision,
        "revision_sequence": sequence, "first_observed_at": observed or utc(2, 10),
        "raw_batch_id": revision, "source_available_at": source_available,
        "evidence_ref": evidence, "close": close, "volume": 100,
    }


def query(*, cutoff=None, policy="operational_pit_v1", symbols=("A",),
          sessions=("2024-01-02",), fields=("close",), **kwargs):
    return QuerySpec("market_daily", fields, symbols, sessions, policy,
                     {s: cutoff or utc(2, 12) for s in sessions}, **kwargs)


def membership_manifest():
    return {
        "snapshot_id": "s1", "domains": {"universe_membership": {
            "contract": {"contract_id": "member_v1", "logical_key": ["membership_id"], "fields": {
                "universe_id": {"dtype": "string"}, "effective_from": {"dtype": "date"},
                "effective_to": {"dtype": "date"}}},
            "source_profile": {"id": "member_source", "availability": {
                "timezone": "Asia/Shanghai", "session_release_time": "09:00:00"}},
            "partitions": [{"partition": "history"}],
            "coverage": {"complete_states": [{
                "universe_id": "IDX", "complete": True, "effective_from": "2024-01-01",
                "effective_to": "2024-01-03", "members": ["A"],
                "first_observed_at": utc(2), "raw_batch_id": "state1",
            }, {
                "universe_id": "IDX", "complete": True, "effective_from": "2024-01-03",
                "effective_to": "2024-01-04", "members": [],
                "first_observed_at": utc(3), "raw_batch_id": "state2",
            }]},
        }},
    }


def membership_row(event, start, stop, observed, *, sequence=1):
    return {"security_id": "A", "universe_id": "IDX", "membership_id": event,
            "effective_from": start, "effective_to": stop, "revision_id": event,
            "revision_sequence": sequence, "first_observed_at": observed,
            "raw_batch_id": event, "source_available_at": None, "evidence_ref": None}


def membership_query(*, cutoff, sessions=("2024-01-02",), symbols=("A", "B")):
    return QuerySpec("universe_membership", ("is_member",), symbols, sessions,
                     "operational_pit_v1", {s: cutoff for s in sessions}, universe_id="IDX")


class LocalReaderTests(unittest.TestCase):
    def test_membership_metadata_uses_latest_snapshot_not_interval_opening(self):
        manifest = membership_manifest()
        states = manifest["domains"]["universe_membership"]["coverage"]["complete_states"]
        states[0]["source_snapshot_date"] = "2024-01-01"
        states[1].update(members=["A"], effective_to=None,
                         source_snapshot_date="2024-01-03")
        row = membership_row("continuous-A", "2024-01-01", None, utc(1))
        reader = SnapshotQueryReader(MemoryStore(manifest, {"history": [row]}), "s1")
        batch = reader.read(membership_query(cutoff=utc(4), sessions=("2024-01-03",)))
        self.assertEqual(batch.frame["is_member"].tolist(), [True, False])
        self.assertEqual([meta["source_snapshot_date"]
                          for meta in batch.field_meta["is_member"]["by_key"]],
                         ["2024-01-03", "2024-01-03"])

    def test_visibility_precedes_explicit_source_revision_order(self):
        rows = [
            daily_row(revision="new", sequence=2, observed=utc(2, 9), close=20),
            daily_row(revision="late_old", sequence=0, observed=utc(3, 9), close=5),
            daily_row(revision="old", sequence=1, observed=utc(1, 9), close=10),
        ]
        store = MemoryStore(daily_manifest(), {"2023-12": [], "2024-01": rows, "2024-02": []})
        reader = SnapshotQueryReader(store, "s1")
        before = reader.read(query(cutoff=utc(2, 8)))
        after = reader.read(query(cutoff=utc(4)))
        assert before.frame.loc[0, "close"] == 10
        assert after.frame.loc[0, "close"] == 20
        assert after.field_meta["close"]["by_key"][0]["revision_id"] == "new"
        reads = [call for call in store.calls if call[0] != "verify"]
        assert [call[0] for call in reads] == ["2024-01"]
        assert all("volume" not in call[1] for call in reads)
        assert reader.index_cache_hits == 1

    def test_market_safe_requires_revision_bound_evidence_and_preserves_missing_keys(self):
        rows = [
            daily_row(symbol="A", source_available=utc(1), evidence=None, observed=utc(3)),
            daily_row(symbol="B", source_available=utc(1), evidence="notice-1", observed=utc(3), close=None),
            daily_row(symbol="C", source_available=None, observed=utc(3)),
        ]
        store = MemoryStore(daily_manifest(), {"2023-12": [], "2024-01": rows, "2024-02": []})
        reader = SnapshotQueryReader(store, "s1")
        batch = reader.read(query(policy="market_pit_safe_v1", symbols=("A", "B", "C", "D")))
        assert batch.frame["close"].isna().tolist() == [True, True, True, True]
        meta = batch.field_meta["close"]["by_key"]
        assert [m["missing_reason"] for m in meta] == [
            "not_visible_at_cutoff", "not_provided", "not_visible_at_cutoff", "source_missing"]
        assert meta[1]["availability_basis"] == "revision_bound_source_evidence"
        assert meta[1]["usable_from"] == utc(1).isoformat()
        assert meta[0]["revision_id"] is None
        assert "2 queried revisions" in batch.context["limitations"][0]
        encoded = json.dumps(batch.to_json(), allow_nan=False)
        assert '"close": null' in encoded

    def test_best_effort_requires_declared_release_and_does_not_backdate_strict(self):
        row = daily_row(observed=utc(10), close=7)
        store = MemoryStore(daily_manifest(), {"2023-12": [], "2024-01": [row], "2024-02": []})
        reader = SnapshotQueryReader(store, "s1")
        assert reader.read(query(cutoff=utc(2, 8))).frame["close"].isna().all()
        vendor = reader.read(query(cutoff=utc(2, 10), policy="best_effort_vendor_v1"))
        assert vendor.frame.loc[0, "close"] == 7  # 17:00 CST = 09:00 UTC
        assert vendor.field_meta["close"]["by_key"][0]["availability_basis"] == "declared_vendor_assumption"
        assert vendor.context["limitations"]
        no_rule = daily_manifest(availability={"timezone": "Asia/Shanghai"})
        with self.assertRaisesRegex(QueryError, "session_release_time"):
            SnapshotQueryReader(MemoryStore(no_rule, store.partitions), "s1").read(
                query(policy="best_effort_vendor_v1"))

    def test_validation_cache_identity_and_caller_mutation_isolation(self):
        store = MemoryStore(daily_manifest(), {"2023-12": [], "2024-01": [daily_row()], "2024-02": []})
        reader = SnapshotQueryReader(store, "s1", cache_bytes=1_000_000)
        spec = query()
        first = reader.read(spec)
        first.frame.loc[0, "close"] = 999
        first.field_meta["close"]["by_key"][0]["missing_reason"] = "bad"
        second = reader.read(spec)
        assert second.frame.loc[0, "close"] == 10
        assert second.field_meta["close"]["by_key"][0]["missing_reason"] is None
        assert reader.partition_reads == 1 and reader.cache_hits == 1
        assert reader._cached_bytes <= reader.cache_bytes
        assert ("verify", "2024-01") in store.calls
        with self.assertRaisesRegex(QueryError, "timezone-aware"):
            reader.read(query(cutoff=datetime(2024, 1, 2, 12)))
        with self.assertRaisesRegex(QueryError, "adjusted prices"):
            reader.read(query(price_basis="adjusted", adjustment_anchor="2024-01-02"))
        with self.assertRaisesRegex(QueryError, "undeclared"):
            reader.read(query(fields=("unknown",)))
        with self.assertRaisesRegex(QueryError, "bootstrap_hybrid"):
            reader.read(query(policy="bootstrap_hybrid_v1"))
        with self.assertRaisesRegex(QueryError, "purpose"):
            reader.read(query(purpose="unknown"))

    def test_uncached_results_are_fresh_without_cache_sizing_or_copy(self):
        rows = [daily_row(symbol="A"), daily_row(symbol="B", revision="b")]
        store = MemoryStore(daily_manifest(), {"2023-12": [], "2024-01": rows, "2024-02": []})
        reader = SnapshotQueryReader(store, "s1", cache_bytes=0)
        spec = query(symbols=("A", "B"))
        with patch("axiom_data.reader._cache_size", side_effect=AssertionError("cache disabled")):
            first = reader.read(spec)
        first.frame.loc[0, "close"] = 999
        first.field_meta["close"]["by_key"][0]["missing_reason"] = "changed"
        second = reader.read(spec)
        self.assertEqual(second.frame.loc[0, "close"], 10)
        self.assertIsNone(second.field_meta["close"]["by_key"][0]["missing_reason"])
        self.assertEqual((reader.cache_hits, reader.partition_reads, reader._cached_bytes), (0, 2, 0))

        small = SnapshotQueryReader(store, "s1", cache_bytes=1)
        with patch("axiom_data.reader._cache_size", return_value=2) as size:
            self.assertEqual(small.read(spec).frame.loc[0, "close"], 10)
        size.assert_called_once()
        self.assertEqual(small._cached_bytes, 0)

    def test_best_effort_release_is_computed_once_per_read_session(self):
        rows = [daily_row(symbol="A"), daily_row(symbol="B", revision="b")]
        store = MemoryStore(daily_manifest(), {"2023-12": [], "2024-01": rows, "2024-02": []})
        reader = SnapshotQueryReader(store, "s1", cache_bytes=0)
        with patch("axiom_data.reader._release_time", wraps=_release_time) as release:
            result = reader.read(query(symbols=("A", "B"), cutoff=utc(2, 12),
                                       policy="best_effort_vendor_v1"))
        self.assertEqual(result.frame["close"].tolist(), [10, 10])
        # One validation call, then one shared release for both selected rows.
        self.assertEqual(release.call_count, 2)

    def test_cache_identity_accepts_iso_cutoff_and_typed_session_date(self):
        row = daily_row(session=date(2024, 1, 2))
        store = MemoryStore(daily_manifest(), {"2023-12": [], "2024-01": [row], "2024-02": []})
        reader = SnapshotQueryReader(store, "s1")
        spec = query(cutoff="2024-01-02T12:00:00+00:00")
        assert reader.read(spec).frame.loc[0, "close"] == 10
        assert reader.read(spec).frame.loc[0, "close"] == 10
        assert reader.cache_hits == 1

    def test_daily_reader_rejects_event_key_shape(self):
        manifest = daily_manifest()
        manifest["domains"]["market_daily"]["contract"]["logical_key"] = ["security_id", "event_date"]
        store = MemoryStore(manifest, {"2023-12": [], "2024-01": [], "2024-02": []})
        with self.assertRaisesRegex(QueryError, "logical_key security_id/session"):
            SnapshotQueryReader(store, "s1").read(query())

    def test_membership_reads_old_month_interval_and_contract_key(self):
        manifest = membership_manifest()
        domain = manifest["domains"]["universe_membership"]
        domain["contract"]["logical_key"] = ["event_key"]
        domain["partitions"] = [{"partition": "2023-12"}, {"partition": "2024-01"}]
        row = membership_row("old", "2023-12-31", "2024-01-03", utc(2))
        row.pop("membership_id")
        row["event_key"] = "event-1"
        store = MemoryStore(manifest, {"2023-12": [row], "2024-01": []})
        result = SnapshotQueryReader(store, "s1").read(membership_query(cutoff=utc(3)))
        assert result.frame["is_member"].iloc[0]
        assert [call[0] for call in store.calls] == ["2023-12", "2024-01"]
        assert "event_key" in store.calls[0][1]

    def test_membership_half_open_reentry_and_time_qualified_negative_state(self):
        rows = [membership_row("first", "2024-01-01", "2024-01-03", utc(2)),
                membership_row("again", "2024-01-05", None, utc(5))]
        store = MemoryStore(membership_manifest(), {"history": rows})
        reader = SnapshotQueryReader(store, "s1")
        early = reader.read(membership_query(cutoff=utc(1), sessions=("2024-01-02",)))
        assert early.frame["is_member"].isna().tolist() == [True, True]
        current = reader.read(membership_query(cutoff=utc(6), sessions=("2024-01-02", "2024-01-03", "2024-01-05")))
        assert current.frame["is_member"].iloc[:5].tolist() == [True, False, False, False, True]
        assert current.frame["is_member"].isna().iloc[-1]
        assert current.field_meta["is_member"]["by_key"][-1]["missing_reason"] == "source_missing"
        assert all(call[3] is None for call in store.calls if call[0] != "verify")

    def test_membership_requires_explicit_universe_and_visible_complete_state(self):
        store = MemoryStore(membership_manifest(), {"history": []})
        reader = SnapshotQueryReader(store, "s1")
        spec = QuerySpec("universe_membership", ("is_member",), ("A",), ("2024-01-02",),
                         "operational_pit_v1", {"2024-01-02": utc(2)})
        with self.assertRaisesRegex(QueryError, "universe_id"):
            reader.read(spec)
        result = reader.read(membership_query(cutoff=utc(1)))
        assert result.frame["is_member"].isna().all()
        self.assertEqual([m["missing_reason"] for m in result.field_meta["is_member"]["by_key"]],
                         ["not_visible_at_cutoff", "not_visible_at_cutoff"])

    def test_membership_complete_group_is_evaluated_once_per_session(self):
        manifest = membership_manifest()
        coverage = manifest["domains"]["universe_membership"]["coverage"]
        coverage["limitations"] = ["later anchor reconstruction is not historical PIT proof"]
        coverage["complete_states"][0]["dependency_raw_batch_ids"] = ["anchor", "adjustment"]
        store = MemoryStore(manifest, {"history": []})
        reader = SnapshotQueryReader(store, "s1", cache_bytes=0)
        with patch("axiom_data.reader._row_availability", wraps=_row_availability) as availability:
            result = reader.read(membership_query(cutoff=utc(3),
                symbols=tuple(f"outside-{i}" for i in range(1800))))
        self.assertEqual(availability.call_count, 1)
        self.assertFalse(result.frame["is_member"].any())
        self.assertEqual(result.field_meta["is_member"]["by_key"][0]["dependency_raw_batch_ids"],
                         ["anchor", "adjustment"])
        self.assertIn(coverage["limitations"][0], result.context["limitations"])

    def test_closed_chain_completeness_uses_canonical_intervals_without_member_copies(self):
        manifest = membership_manifest()
        state = manifest["domains"]["universe_membership"]["coverage"]["complete_states"][0]
        state.pop("members")
        state.update(member_set_source="canonical_intervals_v1", member_count=1,
                     dependency_raw_batch_ids=["baseline", "adjustment"])
        rows = [membership_row("first", "2024-01-01", "2024-01-03", utc(2))]
        rows[0]["dependency_raw_batch_ids"] = ["baseline", "exit-proof"]
        reader = SnapshotQueryReader(MemoryStore(manifest, {"history": rows}), "s1")
        result = reader.read(membership_query(cutoff=utc(3)))
        self.assertEqual(result.frame["is_member"].tolist(), [True, False])
        self.assertEqual(result.field_meta["is_member"]["by_key"][0]["dependency_raw_batch_ids"],
                         ["baseline", "exit-proof", "adjustment"])
        self.assertEqual(result.field_meta["is_member"]["by_key"][1]["dependency_raw_batch_ids"],
                         ["baseline", "adjustment"])
        hidden = reader.read(membership_query(cutoff=utc(1)))
        self.assertTrue(hidden.frame["is_member"].isna().all())
        state.pop("member_set_source")
        with self.assertRaisesRegex(QueryError, "requires explicit members"):
            SnapshotQueryReader(MemoryStore(manifest, {"history": rows}), "s1").read(
                membership_query(cutoff=utc(3)))

    def test_complete_state_revision_is_selected_before_corrected_economic_dates(self):
        manifest = membership_manifest()
        old = manifest["domains"]["universe_membership"]["coverage"]["complete_states"][0]
        old.update(state_id="stable-source-state", revision_sequence=1, revision_id="state-old")
        corrected = {**old, "effective_from": "2024-01-04", "effective_to": "2024-01-05",
                     "members": ["B"], "first_observed_at": utc(3),
                     "revision_sequence": 2, "revision_id": "state-corrected"}
        manifest["domains"]["universe_membership"]["coverage"]["complete_states"] = [old, corrected]
        reader = SnapshotQueryReader(MemoryStore(manifest, {"history": []}), "s1")
        self.assertEqual(reader.read(membership_query(cutoff=utc(2))).frame["is_member"].tolist(),
                         [True, False])
        after = reader.read(membership_query(cutoff=utc(4)))
        self.assertTrue(after.frame["is_member"].isna().all())
        self.assertEqual(reader.read(membership_query(cutoff=utc(4),
            sessions=("2024-01-04",))).frame["is_member"].tolist(), [False, True])

    def test_open_membership_needs_time_visible_verified_source_coverage(self):
        manifest = membership_manifest()
        coverage = manifest["domains"]["universe_membership"]["coverage"]
        state = coverage["complete_states"][0]
        state.pop("members")
        state.update(effective_to=None, member_set_source="canonical_intervals_v1", member_count=1)
        coverage.update(complete_states=[state], verified_through="2024-01-04",
                        verification_checkpoints=[
                            {"verified_through": "2024-01-02", "first_observed_at": utc(2),
                             "dependency_raw_batch_ids": ["initial-archive"]},
                            {"verified_through": "2024-01-04", "first_observed_at": utc(5),
                             "dependency_raw_batch_ids": ["extended-archive"]},
                        ])
        rows = [membership_row("first", "2024-01-01", None, utc(2))]
        reader = SnapshotQueryReader(MemoryStore(manifest, {"history": rows}), "s1")
        self.assertEqual(reader.read(membership_query(cutoff=utc(3))).frame["is_member"].tolist(),
                         [True, False])
        before = reader.read(membership_query(cutoff=utc(3), sessions=("2024-01-04",)))
        self.assertTrue(before.frame["is_member"].isna().all())
        self.assertEqual(before.field_meta["is_member"]["by_key"][0]["missing_reason"],
                         "not_visible_at_cutoff")
        after = reader.read(membership_query(cutoff=utc(6), sessions=("2024-01-04",)))
        self.assertEqual(after.frame["is_member"].tolist(), [True, False])
        self.assertEqual(after.field_meta["is_member"]["by_key"][0]["usable_from"],
                         utc(5).isoformat())
        self.assertIn("extended-archive",
                      after.field_meta["is_member"]["by_key"][1]["dependency_raw_batch_ids"])
        future = reader.read(membership_query(cutoff=utc(6), sessions=("2024-01-05",)))
        self.assertTrue(future.frame["is_member"].isna().all())

        assumptions = QuerySpec("universe_membership", ("is_member",), ("A", "B"),
                                ("2024-01-04",), "best_effort_vendor_v1",
                                {"2024-01-04": utc(4, 12)}, universe_id="IDX")
        self.assertEqual(reader.read(assumptions).frame["is_member"].tolist(), [True, False])

    def test_real_parquet_date_session_and_warm_cache_read_only(self):
        contract = {
            "contract_id": "date_daily_v1", "logical_key": ["security_id", "session"],
            "fields": {"session": {"dtype": "date", "nullable": False},
                       "close": {"dtype": "float64", "unit": "CNY"}},
        }
        with tempfile.TemporaryDirectory() as temp:
            store = LocalStore(temp)
            part = store.write_partition("market_daily", "2024-01", [daily_row()], contract)
            domain = {
                "contract": contract, "source_profile": {"id": "test", "availability": {
                    "timezone": "Asia/Shanghai", "session_release_time": "17:00:00"}},
                "partitions": [part], "raw_batch_ids": [], "coverage": {}, "build_context": {},
            }
            snapshot = store.publish_snapshot({"market_daily": domain}, parent_snapshot=None,
                                              build_context={}, promote=False)
            files_before = sorted(str(p.relative_to(temp)) for p in Path(temp).rglob("*") if p.is_file())
            reader = SnapshotQueryReader(store, snapshot["snapshot_id"])
            spec = query()
            assert reader.read(spec).frame.loc[0, "close"] == 10
            assert reader.read(spec).frame.loc[0, "close"] == 10
            assert reader.partition_reads == 1 and reader.cache_hits == 1
            files_after = sorted(str(p.relative_to(temp)) for p in Path(temp).rglob("*") if p.is_file())
            assert files_after == files_before
