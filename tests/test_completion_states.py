"""Independent D08/D15 expectations against actual local Snapshot objects."""

from __future__ import annotations

from datetime import datetime, timezone
import tempfile
import unittest
from unittest.mock import patch

from axiom_data import Data, DataError

from axiom_data.local_states import plan_research_scope, read_states
from axiom_data.protocols import QuerySpec
from axiom_data.storage import LocalStore


OBSERVED = "2024-01-01T00:00:00+00:00"
CUTOFF = "2024-01-20T00:00:00+00:00"


def contract(name, key, fields):
    return {"contract_id": name + "_test_v1", "logical_key": key,
            "fields": {k: {"dtype": v, "nullable": True} for k, v in fields.items()}}


CAL = contract("calendar", ["exchange", "session"], {"exchange": "string", "session": "date", "is_open": "bool"})
MASTER = contract("master", ["security_id", "listing_date"], {
    "exchange": "string", "listing_date": "date", "delisting_date": "date"})
STATUS = contract("status", ["security_id", "session"], {"is_suspended": "bool"})
MARKET = contract("market", ["security_id", "session"], {"close": "float64", "volume": "int64"})
MEMBER = contract("member", ["membership_id"], {
    "universe_id": "string", "membership_id": "string", "effective_from": "date", "effective_to": "date"})


def row(**fields):
    return {"revision_id": "r1", "revision_sequence": 1,
            "first_observed_at": OBSERVED, **fields}


def add_domain(store, name, rows, spec, *, coverage=None, partition="history"):
    raw = store.write_raw(b"[]", request={"endpoint": "independent_fixture", "domain": name},
                          source_profile={"id": name}, observed_at=OBSERVED)
    rows = [dict(item, raw_batch_id=raw["batch_id"]) for item in rows]
    part = store.write_partition(name, partition, rows, spec)
    return {"contract": spec, "source_profile": {"id": name},
            "partitions": [part], "raw_batch_ids": [raw["batch_id"]],
            "coverage": coverage or {}, "build_context": {"test": "independent_expected"}}


class CompletionStatesTests(unittest.TestCase):
    def test_calendar_identity_suspension_gap_and_unknown_are_distinct(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LocalStore(tmp)
            days = range(1, 9)
            calendar = [row(exchange="SSE", session=f"2024-01-{day:02d}",
                            is_open=day != 1) for day in days if day != 7]
            master = [row(security_id="A", exchange="SSE", listing_date="2024-01-02",
                          delisting_date="2024-01-06"),
                      row(security_id="B", exchange="SSE", listing_date="2024-01-03",
                          delisting_date=None)]
            statuses = [row(security_id="A", session=f"2024-01-{day:02d}", is_suspended=suspend)
                        for day, suspend in [(3, False), (4, True), (5, False)]]
            statuses.append(row(security_id="B", session="2024-01-08", is_suspended=False))
            prices = [row(security_id="A", session="2024-01-03", close=10.0, volume=100),
                      row(security_id="A", session="2024-01-04", close=10.0, volume=0)]
            domains = {
                "trading_calendar": add_domain(store, "trading_calendar", calendar, CAL),
                "security_master": add_domain(store, "security_master", master, MASTER),
                "security_status": add_domain(store, "security_status", statuses, STATUS, partition="2024-01"),
                "market_daily": add_domain(store, "market_daily", prices, MARKET,
                    coverage={"complete_cells": [{"security_id": "A", "session": "2024-01-05", "complete": True}]},
                    partition="2024-01"),
            }
            snapshot = store.publish_snapshot(domains, parent_snapshot=None,
                                              build_context={"test": "states"})["snapshot_id"]
            sessions = tuple(f"2024-01-{day:02d}" for day in days)
            query = QuerySpec("market_daily", ("close", "volume"), ("A", "B"), sessions,
                              "operational_pit_v1", {day: CUTOFF for day in sessions})
            result = read_states(store, snapshot, query)
            by_key = {(r.security_id, r.session): r for r in result.frame.itertuples(index=False)}
            self.assertEqual(by_key["A", "2024-01-01"].market_state, "calendar_closed")
            self.assertEqual(by_key["B", "2024-01-02"].market_state, "not_listed")
            self.assertEqual(by_key["A", "2024-01-03"].market_state, "normal_trading")
            self.assertEqual(by_key["A", "2024-01-04"].market_state, "suspended")
            self.assertEqual(by_key["A", "2024-01-04"].volume, 0)
            self.assertEqual(by_key["A", "2024-01-05"].market_state, "source_gap")
            self.assertEqual(by_key["A", "2024-01-06"].market_state, "delisted")
            self.assertEqual(by_key["A", "2024-01-07"].market_state, "unknown_status")
            self.assertEqual(by_key["B", "2024-01-08"].market_state, "unknown_status")
            self.assertTrue(result.frame.loc[(result.frame.security_id == "A") &
                                             (result.frame.session == "2024-01-05"), "close"].isna().all())
            reasons = {(m["security_id"], m["session"]): m["missing_reason"]
                       for m in result.field_meta["market_state"]["by_key"]}
            self.assertEqual(reasons["A", "2024-01-07"], "calendar_source_missing")
            self.assertEqual(reasons["B", "2024-01-08"], "market_coverage_unknown")
            price_reasons = {(m["security_id"], m["session"]): m["missing_reason"]
                             for m in result.field_meta["close"]["by_key"]}
            self.assertEqual(price_reasons["A", "2024-01-01"], "calendar_closed")
            self.assertTrue(result.context["diagnostic_only"])
            self.assertEqual(len(store.read_partition(domains["market_daily"]["partitions"][0])), 2)

    def test_late_status_revision_does_not_change_earlier_cutoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LocalStore(tmp)
            domains = {
                "trading_calendar": add_domain(store, "trading_calendar", [
                    row(exchange="SSE", session="2024-01-03", is_open=True)], CAL),
                "security_master": add_domain(store, "security_master", [
                    row(security_id="A", exchange="SSE", listing_date="2024-01-02",
                        delisting_date=None)], MASTER),
                "security_status": add_domain(store, "security_status", [
                    row(security_id="A", session="2024-01-03", is_suspended=False,
                        first_observed_at="2024-01-02T00:00:00+00:00"),
                    row(security_id="A", session="2024-01-03", is_suspended=True,
                        revision_id="r2", revision_sequence=2,
                        first_observed_at="2024-01-04T00:00:00+00:00")], STATUS),
                "market_daily": add_domain(store, "market_daily", [
                    row(security_id="A", session="2024-01-03", close=10.0, volume=100)], MARKET),
            }
            snapshot = store.publish_snapshot(domains, parent_snapshot=None,
                                              build_context={"test": "status_revision"})["snapshot_id"]
            def query(cutoff):
                return QuerySpec("market_daily", ("close",), ("A",), ("2024-01-03",),
                                 "operational_pit_v1", {"2024-01-03": cutoff})
            self.assertEqual(read_states(store, snapshot, query("2024-01-03T12:00:00Z"))
                             .frame.loc[0, "market_state"], "normal_trading")
            self.assertEqual(read_states(store, snapshot, query("2024-01-05T00:00:00Z"))
                             .frame.loc[0, "market_state"], "suspended")

    def test_public_scope_loads_once_per_call_and_does_not_mix_snapshots(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LocalStore(tmp)
            calendar = add_domain(store, "trading_calendar", [
                row(exchange="SSE", session=day, is_open=True)
                for day in ("2024-01-05", "2024-01-08")], CAL)
            snapshots = []
            for symbol in ("A", "B"):
                membership = add_domain(store, "universe_membership", [
                    row(security_id=symbol, universe_id="IDX", membership_id=symbol,
                        effective_from="2024-01-02", effective_to=None)], MEMBER,
                    coverage={"complete_states": [{"universe_id": "IDX", "complete": True,
                        "effective_from": "2024-01-02", "effective_to": None,
                        "members": [symbol], "first_observed_at": OBSERVED}]})
                snapshots.append(store.publish_snapshot(
                    {"trading_calendar": calendar, "universe_membership": membership},
                    parent_snapshot=snapshots[-1] if snapshots else None,
                    build_context={"case": symbol})["snapshot_id"])
            data = Data(tmp)
            options = dict(universe_id="IDX", output_sessions=("2024-01-08",),
                           cutoff_by_session={"2024-01-08": CUTOFF},
                           pit_policy="operational_pit_v1", lookback_sessions=1, exchange="SSE")
            with patch.object(data.store, "load_snapshot", wraps=data.store.load_snapshot) as loads:
                for snapshot, symbol in zip(snapshots, ("A", "B")):
                    before = loads.call_count
                    scope = data.plan_scope(snapshot=snapshot, **options)
                    self.assertEqual(loads.call_count - before, 1)
                    self.assertEqual(scope["snapshot_id"], snapshot)
                    self.assertEqual(scope["decision_membership_by_session"], {"2024-01-08": [symbol]})
                    self.assertEqual(scope["warmup_sessions"], ["2024-01-05"])
                    self.assertEqual(scope["missing_reasons"], [])
                path = store.root / "snapshots" / (snapshots[0] + ".json")
                path.write_bytes(path.read_bytes().replace(b'"case":"A"', b'"case":"forged"'))
                with self.assertRaisesRegex(DataError, "digest mismatch"):
                    data.plan_scope(snapshot=snapshots[0], **options)

    def test_historical_union_reentry_holiday_lookback_and_outside_holdings(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LocalStore(tmp)
            calendar = [row(exchange="SSE", session=f"2024-01-{day:02d}",
                            is_open=day not in {4, 9}) for day in (2, 3, 4, 5, 8, 9, 10, 11)]
            membership = [
                row(security_id="A", universe_id="IDX", membership_id="first",
                    effective_from="2024-01-02", effective_to="2024-01-10"),
                row(security_id="A", universe_id="IDX", membership_id="reentry",
                    effective_from="2024-01-11", effective_to=None),
                row(security_id="B", universe_id="IDX", membership_id="second",
                    effective_from="2024-01-10", effective_to="2024-01-12"),
            ]
            complete = [
                {"universe_id": "IDX", "complete": True, "effective_from": "2024-01-02",
                 "effective_to": "2024-01-10", "members": ["A"], "first_observed_at": OBSERVED},
                {"universe_id": "IDX", "complete": True, "effective_from": "2024-01-10",
                 "effective_to": "2024-01-11", "members": ["B"], "first_observed_at": OBSERVED},
                {"universe_id": "IDX", "complete": True, "effective_from": "2024-01-11",
                 "effective_to": "2024-01-12", "members": ["A", "B"], "first_observed_at": OBSERVED},
            ]
            domains = {
                "trading_calendar": add_domain(store, "trading_calendar", calendar, CAL),
                "universe_membership": add_domain(store, "universe_membership", membership, MEMBER,
                    coverage={"complete_states": complete}),
            }
            snapshot = store.publish_snapshot(domains, parent_snapshot=None,
                                              build_context={"test": "scope"})["snapshot_id"]
            outputs = ("2024-01-08", "2024-01-10", "2024-01-11")
            planned = plan_research_scope(
                store, snapshot, universe_id="IDX", output_sessions=outputs,
                cutoff_by_session={day: CUTOFF for day in outputs},
                pit_policy="operational_pit_v1", lookback_sessions=2,
                held_symbols=("C",), pending_symbols=("D",), exchange="SSE")
            self.assertEqual(planned["warmup_sessions"], ["2024-01-03", "2024-01-05"])
            self.assertEqual(planned["decision_membership_by_session"], {
                "2024-01-08": ["A"], "2024-01-10": ["B"], "2024-01-11": ["A", "B"]})
            self.assertEqual(planned["read_symbols"], ["A", "B", "C", "D"])
            self.assertEqual(planned["tracking_symbols"], ["C", "D"])
            self.assertEqual(planned["missing_reasons"], [])


if __name__ == "__main__":
    unittest.main()
