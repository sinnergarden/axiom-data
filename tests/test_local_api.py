"""Public API acceptance on small synthetic input; no supplier credentials."""

from dataclasses import replace
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from axiom_data import Data, IngestBatch, QueryError, QuerySpec, UpdateRequest
from axiom_data.sources import collect_tushare_daily


class DailyClient:
    def __init__(self, close=10.5, day="20200102"):
        self.close, self.day = close, day

    def query(self, endpoint, **params):
        assert endpoint == "daily"
        return [{"ts_code": "000001.SZ", "trade_date": self.day,
                 "open": 10.0, "high": 11.0, "low": 9.0, "close": self.close,
                 "pre_close": 9.8, "vol": 12.34, "amount": 45.6}]


def daily_batch(day="20200102", observed="2026-09-28T10:00:00+08:00"):
    return collect_tushare_daily(
        DailyClient(day=day), request={"ts_code": "000001.SZ", "trade_date": day},
        identity_map={"000001.SZ": "sec-1"}, observed_at=observed,
    )


def query(policy="best_effort_vendor_v1", cutoff="2020-01-02T20:00:00+08:00"):
    return QuerySpec("market_daily", ("close", "volume_shares"), ("sec-1", "sec-missing"),
                     ("2020-01-02",), policy, {"2020-01-02": cutoff})


class LocalApiTest(unittest.TestCase):
    def test_public_round_trip_fixed_snapshot_duplicate_and_move(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "data"
            data = Data(root)
            self.assertFalse(root.exists())
            s1 = data.update(base_snapshot=None, request=UpdateRequest(
                (daily_batch(),), "initial", {"normalizer_version": "test-v1"}))
            self.assertTrue(s1.changed)
            self.assertEqual(data.resolve(), s1.snapshot_id)
            original_files = {str(p.relative_to(root)): p.stat().st_mtime_ns
                              for p in root.rglob("*") if p.is_file()}
            first = data.read(snapshot=s1.snapshot_id, query=query())
            self.assertEqual(first.frame.iloc[0]["close"], 10.5)
            self.assertEqual(first.frame.iloc[0]["volume_shares"], 1234)
            self.assertEqual(first.field_meta["close"]["by_key"][1]["missing_reason"], "source_missing")
            json.dumps(first.to_json(), allow_nan=False)
            with self.assertRaisesRegex(QueryError, "implicit keys"):
                data.read(snapshot=s1.snapshot_id, query=replace(query(), fields=("security_id",)))
            first.frame.loc[0, "close"] = 999
            self.assertEqual(data.read(snapshot=s1.snapshot_id, query=query()).frame.iloc[0]["close"], 10.5)
            self.assertEqual(original_files, {str(p.relative_to(root)): p.stat().st_mtime_ns
                                              for p in root.rglob("*") if p.is_file()})
            strict = data.read(snapshot=s1.snapshot_id, query=query("operational_pit_v1"))
            self.assertEqual(strict.field_meta["close"]["by_key"][0]["missing_reason"], "not_visible_at_cutoff")
            self.assertEqual(data.inspect(s1.snapshot_id, required_scope=query())["status"], "limited")
            repeat = data.update(base_snapshot=s1.snapshot_id, request=UpdateRequest(
                (daily_batch(observed="2026-09-29T10:00:00+08:00"),), "repeat", {}))
            self.assertFalse(repeat.changed)
            self.assertEqual(repeat.snapshot_id, s1.snapshot_id)
            s2 = data.update(base_snapshot=s1.snapshot_id, request=UpdateRequest(
                (daily_batch(day="20200103"),), "next-day", {}))
            self.assertTrue(s2.changed)
            self.assertEqual(data.read(snapshot=s1.snapshot_id, query=query()).frame.iloc[0]["close"], 10.5)
            relocated = Path(directory) / "relocated"
            shutil.copytree(root, relocated)
            moved = Data(relocated)
            self.assertEqual(moved.resolve(), s2.snapshot_id)
            self.assertEqual(moved.read(snapshot=s1.snapshot_id, query=query()).to_json(),
                             data.read(snapshot=s1.snapshot_id, query=query()).to_json())

    def test_unresolved_alias_and_market_purpose_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Data(Path(directory) / "absent")
            with self.assertRaises(QueryError):
                data.read(snapshot="current", query=query())
            with self.assertRaises(QueryError):
                data.read_market(snapshot="s-nope", query=query())
            with self.assertRaises(QueryError):
                data.read_market(snapshot="s-nope", query=replace(query(), purpose="market_replay", price_basis="adjusted"))

    def test_declared_int64_with_missing_key_does_not_round_through_float(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Data(directory)
            contract = {"contract_id": "int.v1", "logical_key": ["security_id", "session"],
                        "fields": {"security_id": {"dtype": "string"}, "session": {"dtype": "date"},
                                   "count": {"dtype": "int64"}}}
            profile = {"id": "int.v1", "field_map": {name: name for name in contract["fields"]}}
            payload = json.dumps([{"security_id": "X", "session": "2020-01-02", "count": 9007199254740993}]).encode()
            result = data.update(base_snapshot=None, request=UpdateRequest((
                IngestBatch("market_daily", payload, {}, contract, profile, "2020-01-03T00:00:00Z"),), "int-test", {}))
            batch = data.read(snapshot=result.snapshot_id, query=QuerySpec(
                "market_daily", ("count",), ("X", "missing"), ("2020-01-02",),
                "operational_pit_v1", {"2020-01-02": "2020-01-04T00:00:00Z"}))
            self.assertEqual(str(batch.frame["count"].dtype), "Int64")
            self.assertEqual(batch.to_json()["records"][0]["count"], 9007199254740993)
            self.assertIsNone(batch.to_json()["records"][1]["count"])


if __name__ == "__main__":
    unittest.main()
