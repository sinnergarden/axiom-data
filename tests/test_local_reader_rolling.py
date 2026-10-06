"""Small synthetic rolling-window and per-session PIT regression cases."""

from __future__ import annotations

import unittest
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
import tracemalloc
from unittest.mock import patch

from axiom_data.derived import adjust_prices
from axiom_data.protocols import ConflictError, DataError, QuerySpec
from axiom_data.public_evidence import CONTRACT as EVIDENCE_CONTRACT, PROFILE as EVIDENCE_PROFILE
from axiom_data.reader import SnapshotQueryReader, _ENTRY_BYTES, _object_size
from axiom_data.storage import LocalStore
from test_local_reader import (
    MemoryStore, daily_manifest, daily_row, membership_manifest, membership_query, membership_row,
    query, utc,
)


def vendor_membership_store():
    manifest = membership_manifest()
    domain = manifest["domains"]["universe_membership"]
    domain["source_profile"]["id"] = "tushare.index_weight.dated_membership.v1"
    domain["coverage"] = {"verified_through": "2024-01-10", "complete_states": [{
        "universe_id": "IDX", "complete": True, "effective_from": "2024-01-01",
        "effective_to": None, "members": ["A"], "state_id": "state",
        "source_snapshot_date": "2024-01-01", "revision_id": "state-r1",
        "revision_sequence": 1, "first_observed_at": utc(1), "raw_batch_id": "state-raw",
    }]}
    old = membership_row("event-A", "2024-01-01", None, utc(1))
    old["revision_id"] = "r1"
    corrected = membership_row("event-A", "2024-01-01", "2024-01-10", utc(3), sequence=2)
    corrected["revision_id"] = "r2"
    return MemoryStore(manifest, {"history": [old, corrected]})


class MembershipRollingTests(unittest.TestCase):
    def test_mixed_cutoffs_select_each_sessions_visible_source_revision(self):
        sessions = ("2024-01-02", "2024-01-03")
        # Both intervals cover the supplier snapshot date. Only the knowledge
        # time changes which revision is visible; expected values come from
        # the source sequences and receipts, not the pre-fix reader's output.
        for cutoffs, revisions, usable in (
            ((utc(4), utc(2)), ["r2", "r1"], [utc(3), utc(1)]),
            ((utc(2), utc(4)), ["r1", "r2"], [utc(1), utc(3)]),
        ):
            spec = QuerySpec("universe_membership", ("is_member",), ("A",), sessions,
                             "operational_pit_v1", dict(zip(sessions, cutoffs)), universe_id="IDX")
            for budget in (0, 1_000_000):
                with self.subTest(cutoffs=cutoffs, budget=budget):
                    batch = SnapshotQueryReader(vendor_membership_store(), "s1",
                                                cache_bytes=budget).read(spec)
                    self.assertEqual(batch.frame["is_member"].tolist(), [True, True])
                    meta = batch.field_meta["is_member"]["by_key"]
                    self.assertEqual([item["revision_id"] for item in meta], revisions)
                    self.assertEqual([item["usable_from"] for item in meta],
                                     [value.isoformat() for value in usable])
                    self.assertTrue(all(value <= cutoff for value, cutoff in zip(usable, cutoffs)))

    def test_membership_result_cache_keeps_full_query_identity(self):
        for vendor in (False, True):
            with self.subTest(vendor=vendor):
                store = (vendor_membership_store() if vendor else
                         MemoryStore(membership_manifest(), {"history": []}))
                reader = SnapshotQueryReader(store, "s1", cache_bytes=1_000_000)
                spec = membership_query(cutoff=utc(4))
                first = reader.read(spec)
                first.frame.loc[0, "is_member"] = False
                first.field_meta["is_member"]["by_key"][0]["revision_id"] = "polluted"
                second = reader.read(spec)
                self.assertEqual(second.frame["is_member"].tolist(), [True, False])
                self.assertNotEqual(second.field_meta["is_member"]["by_key"][0]["revision_id"],
                                    "polluted")
                self.assertEqual((reader.partition_reads, reader.cache_hits), (1, 1))
                self.assertIn(reader._cache_key(spec, {s: utc(4) for s in spec.sessions}), reader._cache)

    def test_history_unknown_and_dependencies_survive_reuse_and_output_mutation(self):
        rows = [membership_row("first", "2024-01-01", "2024-01-03", utc(2)),
                membership_row("again", "2024-01-05", None, utc(5))]
        rows[0]["dependency_raw_batch_ids"] = ["source-proof"]
        cached = SnapshotQueryReader(MemoryStore(membership_manifest(), {"history": rows}), "s1")
        reference = SnapshotQueryReader(MemoryStore(membership_manifest(), {"history": rows}), "s1",
                                        cache_bytes=0)
        sessions = ("2024-01-02", "2024-01-03", "2024-01-05")
        for cutoff in (utc(6), utc(1), utc(3)):
            spec = membership_query(cutoff=cutoff, sessions=sessions)
            actual = cached.read(spec)
            self.assertEqual(actual.to_json(), reference.read(spec).to_json())
            if cutoff == utc(6):
                self.assertEqual(actual.frame["is_member"].iloc[:5].tolist(),
                                 [True, False, False, False, True])
                self.assertTrue(actual.frame["is_member"].isna().iloc[-1])
                actual.field_meta["is_member"]["by_key"][0]["dependency_raw_batch_ids"].append("bad")
                actual.context["coverage"]["complete_states"][0]["members"].clear()
            if cutoff == utc(1):
                self.assertTrue(actual.frame["is_member"].isna().all())
        self.assertEqual(cached.partition_reads, 1)
        self.assertEqual(cached.index_cache_hits, 2)


def evidence_row(target, public_at):
    return {"target_domain": "market_daily",
            "target_key": json.dumps({"security_id": target["security_id"], "session": target["session"]},
                                     sort_keys=True, separators=(",", ":")),
            "target_revision": target["revision_id"], "public_at": public_at,
            "document_sha256": "a" * 64, "source_url": "https://example.invalid/synthetic",
            "locator": "synthetic assertion", "asserted_values": "{}",
            "revision_id": "e-" + target["revision_id"], "revision_sequence": 1,
            "first_observed_at": utc(5), "raw_batch_id": "synthetic-evidence"}


def parquet_snapshot(store, rows, *, evidence=(), factors=()):
    """Write only tiny temporary synthetic roots, with native partition verification."""
    domain = deepcopy(daily_manifest()["domains"]["market_daily"])
    domain.update(raw_batch_ids=[], build_context={})
    domain["contract"]["fields"]["session"] = {"dtype": "date", "nullable": False}
    by_month = {}
    for row in rows:
        by_month.setdefault(str(row["session"])[:7], []).append(row)
    domain["partitions"] = [store.write_partition("market_daily", month, values, domain["contract"])
                            for month, values in by_month.items()]
    domains = {"market_daily": domain}
    if factors:
        factor_domain = deepcopy(domain)
        factor_domain["contract"] = {"contract_id": "synthetic_factors_v1",
            "logical_key": ["security_id", "session"],
            "fields": {"session": {"dtype": "date"}, "adj_factor": {"dtype": "float64"}}}
        factor_domain["partitions"] = [store.write_partition(
            "cumulative_factors", "2024-01", factors, factor_domain["contract"])]
        domains["cumulative_factors"] = factor_domain
    if evidence:
        domains["public_evidence"] = {"contract": EVIDENCE_CONTRACT,
            "source_profile": EVIDENCE_PROFILE, "partitions": [store.write_partition(
                "public_evidence", "history", evidence, EVIDENCE_CONTRACT)], "coverage": {},
                "raw_batch_ids": [], "build_context": {}}
    snapshot = store.publish_snapshot(domains, parent_snapshot=None, build_context={}, promote=False)
    return snapshot["snapshot_id"], domains


class RollingIndexTests(unittest.TestCase):
    def assert_budget(self, reader):
        self.assertEqual(reader._cached_bytes, sum(size for _, size in reader._cache.values()))
        self.assertLessEqual(reader._cached_bytes, reader.cache_bytes)
        self.assertLessEqual(reader.index_build_peak_bytes, reader.cache_bytes)
        for key, (value, charged) in reader._cache.items():
            if key.startswith(("rows:", "evidence:")):
                self.assertGreaterEqual(charged, _object_size(value) + _ENTRY_BYTES)

    def test_two_overlapping_21_day_windows_reuse_month_decode_and_grouping(self):
        days = [(date(2024, 1, 1) + timedelta(days=i)).isoformat() for i in range(45)]
        rows = [daily_row(session=day, symbol=symbol, revision=f"{symbol}/{day}", observed=utc(1),
                          close=float(i + 1)) for i, day in enumerate(days) for symbol in ("A", "B")]
        with tempfile.TemporaryDirectory() as tmp:
            store = LocalStore(tmp)
            snapshot, _ = parquet_snapshot(store, rows)
            cached = SnapshotQueryReader(store, snapshot)
            reference = SnapshotQueryReader(store, snapshot, cache_bytes=0)
            specs = [query(sessions=tuple(days[9:30]), symbols=("A", "B"), cutoff=utc(31)),
                     query(sessions=tuple(days[16:37]), symbols=("A", "B"),
                           cutoff=datetime(2024, 2, 7, tzinfo=timezone.utc))]
            self.assertEqual(cached.read(specs[0]).to_json(), reference.read(specs[0]).to_json())
            with patch.object(cached, "_group_key", wraps=cached._group_key) as grouping:
                self.assertEqual(cached.read(specs[1]).to_json(), reference.read(specs[1]).to_json())
            # January's 62 native rows are never decoded or regrouped for the
            # second window; only the 28 February rows enter a new index.
            self.assertEqual(grouping.call_count, 28)
            self.assertEqual((cached.partition_reads, reference.partition_reads), (2, 3))
            self.assertEqual(cached.index_cache_hits, 1)
            self.assert_budget(cached)

    def test_late_early_policy_evidence_and_projection_identity_are_exact(self):
        old = daily_row(revision="r1", observed=utc(3), close=10)
        new = daily_row(revision="r2", sequence=2, observed=utc(4), close=20)
        rows = [old, new, daily_row(symbol="B", revision="B", observed=utc(3)),
                daily_row(session="2024-01-03", revision="next", observed=utc(3))]
        with tempfile.TemporaryDirectory() as tmp:
            store = LocalStore(tmp)
            snapshot, _ = parquet_snapshot(store, rows, evidence=[evidence_row(old, utc(1))])
            cached = SnapshotQueryReader(store, snapshot)
            reference = SnapshotQueryReader(store, snapshot, cache_bytes=0)
            specs = [query(cutoff=utc(5)), query(cutoff=utc(2)),
                     query(cutoff=utc(2), policy="market_pit_safe_v1"),
                     query(cutoff=utc(2, 12), policy="best_effort_vendor_v1"),
                     query(cutoff=utc(5), fields=("volume", "close"), symbols=("A", "B")),
                     query(cutoff=utc(2), fields=("close", "volume"), symbols=("B", "A")),
                     query(cutoff=utc(2), sessions=("2024-01-02", "2024-01-03"),
                           policy="bootstrap_hybrid_v1", policy_by_session={
                               "2024-01-02": "market_pit_safe_v1", "2024-01-03": "operational_pit_v1"})]
            for spec in specs:
                with self.subTest(spec=spec):
                    self.assertEqual(cached.read(spec).to_json(), reference.read(spec).to_json())
                    self.assert_budget(cached)
            self.assertTrue(cached.read(specs[1]).frame["close"].isna().all())
            self.assertEqual(cached.read(specs[2]).frame["close"].tolist(), [10])
            self.assertEqual(cached.read(specs[3]).frame["close"].tolist(), [20])
            # Same field/symbol set in a different output order reuses the
            # projection; a different set still gets its own source index.
            self.assertEqual(cached.partition_reads, 2)
            self.assertGreaterEqual(cached.index_cache_hits, 4)

    def test_snapshot_evidence_binding_does_not_reuse_another_snapshots_answer(self):
        row = daily_row(observed=utc(3))
        with tempfile.TemporaryDirectory() as tmp:
            store = LocalStore(tmp)
            absent, _ = parquet_snapshot(store, [row])
            present, _ = parquet_snapshot(store, [row], evidence=[evidence_row(row, utc(1))])
            spec = query(cutoff=utc(2), policy="market_pit_safe_v1")
            enriched = SnapshotQueryReader(store, present)
            self.assertEqual(enriched.read(spec).frame["close"].tolist(), [10])
            plain = SnapshotQueryReader(store, absent)
            self.assertTrue(plain.read(spec).frame["close"].isna().all())
            self.assertEqual(enriched.read(spec).frame["close"].tolist(), [10])
            self.assertEqual((plain.partition_reads, enriched.partition_reads), (1, 1))

    def test_unrequested_evidence_conflict_is_checked_only_after_query_selection(self):
        good = daily_row(observed=utc(1))
        conflict = daily_row(session="2024-01-20", revision="bad", observed=utc(1),
                             source_available=utc(2), evidence="inline")
        with tempfile.TemporaryDirectory() as tmp:
            store = LocalStore(tmp)
            snapshot, _ = parquet_snapshot(store, [good, conflict],
                                           evidence=[evidence_row(conflict, utc(3))])
            cached = SnapshotQueryReader(store, snapshot)
            reference = SnapshotQueryReader(store, snapshot, cache_bytes=0)
            for cutoff in (utc(4), utc(5)):
                spec = query(cutoff=cutoff)
                self.assertEqual(cached.read(spec).to_json(), reference.read(spec).to_json())
            self.assertEqual((cached.partition_reads, cached.index_cache_hits), (1, 1))
            bad = query(sessions=("2024-01-20",), cutoff=utc(5))
            for reader in (cached, reference):
                with self.assertRaisesRegex(ConflictError, "public time conflicts"):
                    reader.read(bad)

    def test_factor_revisions_and_each_requests_anchor_are_selected_afresh(self):
        prices = [daily_row(close=10, observed=utc(1)),
                  daily_row(session="2024-01-03", close=5, observed=utc(1))]
        factors = [{**daily_row(observed=utc(1)), "adj_factor": 1},
                   {**daily_row(revision="f2", sequence=2, observed=utc(4)), "adj_factor": 3},
                   {**daily_row(session="2024-01-03", observed=utc(1)), "adj_factor": 2}]
        with tempfile.TemporaryDirectory() as tmp:
            store = LocalStore(tmp)
            snapshot, _ = parquet_snapshot(store, prices, factors=factors)
            cached = SnapshotQueryReader(store, snapshot)
            reference = SnapshotQueryReader(store, snapshot, cache_bytes=0)
            sessions = ("2024-01-02", "2024-01-03")
            for cutoff, anchor, expected in ((utc(5), sessions[1], [15, 5]),
                                             (utc(3), sessions[1], [5, 5]),
                                             (utc(3), sessions[0], [10, 10])):
                price_spec = query(sessions=sessions, cutoff=cutoff)
                factor_spec = replace(price_spec, domain="cumulative_factors", fields=("adj_factor",))
                def adjusted(reader):
                    return adjust_prices(reader.read(price_spec), reader.read(factor_spec),
                                         fields=("close",), anchor_session=anchor)
                result = adjusted(cached)
                self.assertEqual(result.to_json(), adjusted(reference).to_json())
                self.assertEqual(result.frame["close"].tolist(), expected)
                self.assertEqual(result.context["query"]["adjustment_anchor"], anchor)
            self.assertEqual(cached.partition_reads, 2)

    def test_small_disabled_and_oversize_budgets_return_original_query(self):
        manifest = daily_manifest()
        partitions = {"2024-01": [daily_row()]}
        for budget in (0, 1, 8192):
            with self.subTest(budget=budget):
                reader = SnapshotQueryReader(MemoryStore(manifest, partitions), "s1", cache_bytes=budget)
                reference = SnapshotQueryReader(MemoryStore(manifest, partitions), "s1", cache_bytes=0)
                for spec in (query(), query(cutoff=utc(3))):
                    self.assertEqual(reader.read(spec).to_json(), reference.read(spec).to_json())
                self.assertFalse(any(key.startswith("rows:") and value is not None
                                     for key, (value, _) in reader._cache.items()))
                self.assert_budget(reader)
                reads = [call for call in reader.store.calls if call[0] != "verify"]
                self.assertEqual(reads[-1][3], query().sessions)

        # Large strings make the pre-conversion chunk bound inadmissible.
        manifest["domains"]["market_daily"]["contract"]["fields"]["note"] = {"dtype": "string"}
        partitions = {"2024-01": [{**daily_row(), "note": "x" * 200_000}]}
        reader = SnapshotQueryReader(MemoryStore(manifest, partitions), "s1", cache_bytes=100_000)
        spec = query(fields=("note",))
        self.assertEqual(reader.read(spec).to_json(), SnapshotQueryReader(
            MemoryStore(manifest, partitions), "s1", cache_bytes=0).read(spec).to_json())
        self.assertEqual(reader.index_build_peak_bytes, 0)
        self.assertEqual(reader.partition_reads, 2)  # Discard candidate, then original filtered read.
        self.assert_budget(reader)

    def test_candidate_construction_error_completely_falls_back(self):
        partitions = {"2024-01": [daily_row(), daily_row(session="2024-01-20")]}
        reader = SnapshotQueryReader(MemoryStore(daily_manifest(), partitions), "s1")
        original_key = reader._group_key
        def fail_on_unrequested(row, domain, spec):
            if row["session"] == "2024-01-20":
                raise MemoryError("synthetic candidate conversion failure")
            return original_key(row, domain, spec)
        with patch.object(reader, "_group_key", side_effect=fail_on_unrequested):
            actual = reader.read(query())
        reference = SnapshotQueryReader(MemoryStore(daily_manifest(), partitions), "s1", cache_bytes=0)
        self.assertEqual(actual.to_json(), reference.read(query()).to_json())
        self.assertEqual(reader.partition_reads, 2)
        self.assertFalse(any(key.startswith("rows:") and value is not None
                             for key, (value, _) in reader._cache.items()))
        self.assert_budget(reader)

    def test_continuous_month_eviction_and_shared_object_accounting(self):
        manifest = daily_manifest()
        partitions = {}
        for month_number in range(60):
            year, month = 2020 + month_number // 12, month_number % 12 + 1
            day = f"{year:04d}-{month:02d}-02"
            partitions[day[:7]] = [daily_row(session=day)]
        manifest["domains"]["market_daily"]["partitions"] = [{"partition": m} for m in partitions]
        reader = SnapshotQueryReader(MemoryStore(manifest, partitions), "s1", cache_bytes=256_000)
        reference = SnapshotQueryReader(MemoryStore(manifest, partitions), "s1", cache_bytes=0)
        cutoff = datetime(2025, 1, 1, tzinfo=timezone.utc)
        specs = [query(sessions=(m + "-02",), cutoff=cutoff) for m in partitions]
        for spec in specs:
            self.assertEqual(reader.read(spec).to_json(), reference.read(spec).to_json())
            self.assert_budget(reader)
        self.assertEqual(reader.partition_reads, 60)
        reads = reader.partition_reads
        self.assertEqual(reader.read(specs[0]).to_json(), reference.read(specs[0]).to_json())
        self.assertEqual(reader.partition_reads, reads + 1)
        self.assert_budget(reader)

        shared = {"dependencies": ["proof"] * 100}
        duplicates = [shared] * 100
        self.assertEqual(_object_size(duplicates), sys.getsizeof(duplicates) + _object_size(shared))

    def test_candidate_upper_bound_covers_measured_python_construction_peak(self):
        symbols = tuple(f"synthetic-{i}" for i in range(250))
        store = MemoryStore(daily_manifest(), {"2024-01": [daily_row(symbol=s) for s in symbols]})
        reader = SnapshotQueryReader(store, "s1", cache_bytes=8_000_000)
        spec = query(symbols=symbols)
        domain = reader.snapshot["domains"]["market_daily"]
        columns = reader._columns(domain, spec)
        # Build the mandatory Arrow projection before tracing the optional
        # Python index construction. Its full buffers are charged by Reader.
        table = store.read_partition({"partition": "2024-01"}, columns=columns,
                                     symbols=symbols, sessions=None)
        with patch.object(store, "read_partition", return_value=table):
            tracemalloc.start()
            try:
                groups = reader._build_index({"partition": "2024-01"}, domain, spec, columns)
                _, python_peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
        self.assertIsNotNone(groups)
        self.assertEqual(len(groups), 250)
        self.assertLessEqual(python_peak, reader.index_build_peak_bytes)
        self.assert_budget(reader)

    def test_raw_index_results_are_isolated_even_when_final_result_is_not_cached(self):
        row = daily_row(observed=utc(3))
        with tempfile.TemporaryDirectory() as tmp:
            store = LocalStore(tmp)
            snapshot, _ = parquet_snapshot(store, [row], evidence=[evidence_row(row, utc(1))])
            reader = SnapshotQueryReader(store, snapshot)
            reference = SnapshotQueryReader(store, snapshot, cache_bytes=0)
            spec = query(cutoff=utc(2), policy="market_pit_safe_v1")
            with patch("axiom_data.reader._cache_size", return_value=reader.cache_bytes + 1):
                first = reader.read(spec)
                first.frame.loc[0, "close"] = 999
                first.field_meta["close"]["by_key"][0]["evidence_ref"] = "bad"
                first.context["query"]["cutoff_by_session"].clear()
                with patch.object(store, "read_partition", wraps=store.read_partition) as decoding:
                    second = reader.read(spec)
                self.assertEqual(decoding.call_count, 0)  # Both facts and evidence reuse verified memory.
                self.assertEqual(second.to_json(), reference.read(spec).to_json())
                self.assertEqual(reader.partition_reads, 1)
                self.assertEqual(reader.cache_hits, 0)
                raw = next(value for key, (value, _) in reader._cache.items() if key.startswith("rows:"))
                self.assertIsNone(raw[("A", "2024-01-02")][0]["evidence_ref"])
                strict = replace(spec, pit_policy="operational_pit_v1")
                self.assertTrue(reader.read(strict).frame["close"].isna().all())
            self.assert_budget(reader)

    def test_fact_and_evidence_mutations_are_rejected_on_both_hit_paths(self):
        row = daily_row(observed=utc(3))
        for changed_domain in ("market_daily", "public_evidence"):
            for result_hit in (False, True):
                with self.subTest(domain=changed_domain, result_hit=result_hit), tempfile.TemporaryDirectory() as tmp:
                    store = LocalStore(tmp)
                    snapshot, domains = parquet_snapshot(store, [row], evidence=[evidence_row(row, utc(1))])
                    reader = SnapshotQueryReader(store, snapshot)
                    spec = query(cutoff=utc(2), policy="market_pit_safe_v1")
                    self.assertEqual(reader.read(spec).frame["close"].tolist(), [10])
                    part = domains[changed_domain]["partitions"][0]
                    path = Path(tmp) / part["uri"]
                    content = path.read_bytes()
                    path.write_bytes(content[:-1] + bytes([content[-1] ^ 1]))
                    next_spec = spec if result_hit else replace(spec, cutoff_by_session={"2024-01-02": utc(4)})
                    with self.assertRaisesRegex(DataError, "SHA-256 validation"):
                        reader.read(next_spec)


if __name__ == "__main__":
    unittest.main()
