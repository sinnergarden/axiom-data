"""Synthetic source tests; no credentials, network, or production Raw data."""

import json
import unittest
from datetime import datetime, timezone

from axiom_data.protocols import DataError, IngestBatch
from axiom_data.sources import (
    collect_tushare_daily, normalize_batch, tushare_daily_profile,
    TUSHARE_DAILY_CONTRACT,
)


OBSERVED = datetime(2026, 9, 28, 4, 30, tzinfo=timezone.utc)
IDENTITIES = {"000001.SZ": "sec-000001-sz"}


class FakeClient:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def query(self, endpoint, **params):
        self.calls.append((endpoint, params))
        return self.rows


class LocalSourcesTest(unittest.TestCase):
    def test_injected_daily_retains_source_response_and_converts_declared_units(self):
        source_row = {
            "ts_code": "000001.SZ", "trade_date": "20200102",
            "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5,
            "pre_close": 9.8, "change": 0.7, "pct_chg": 7.1429,
            "vol": 12.34, "amount": 45.6, "vendor_extra": "kept only in Raw",
        }
        client = FakeClient([source_row])
        batch = collect_tushare_daily(
            client, request={"ts_code": "000001.SZ", "start_date": "20200102", "end_date": "20200103"},
            identity_map=IDENTITIES, observed_at=OBSERVED,
        )
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0][0], "daily")
        raw = json.loads(batch.payload)
        self.assertEqual(raw, [source_row])
        self.assertEqual(batch.request["coverage_status"], "unverified_bounded_observation")
        self.assertIn("best_effort_assumption", batch.source_profile["availability"]["basis"])
        row, = normalize_batch(batch, {"batch_id": "raw-1", "observed_at": OBSERVED})
        self.assertEqual((row["security_id"], row["session"]), ("sec-000001-sz", "2020-01-02"))
        self.assertEqual(row["volume_shares"], 1234)
        self.assertEqual(row["amount_cny"], 45600.0)
        self.assertEqual(row["pre_close"], 9.8)
        self.assertIsNone(row["source_available_at"])
        self.assertIsNone(row["evidence_ref"])
        self.assertIsNone(row["revision_sequence"])
        self.assertEqual(row["first_observed_at"], OBSERVED.isoformat())
        self.assertEqual(row["raw_batch_id"], "raw-1")
        self.assertNotIn("vendor_extra", row)

    def test_daily_nulls_are_not_zero_or_suspension(self):
        source_row = {name: None for name in
                      ("open", "high", "low", "close", "pre_close", "vol", "amount")}
        source_row.update(ts_code="000001.SZ", trade_date="20200102")
        batch = IngestBatch(
            "market_daily", json.dumps([source_row]).encode(), {}, TUSHARE_DAILY_CONTRACT,
            tushare_daily_profile(IDENTITIES), OBSERVED, "tushare_daily_v1",
        )
        row, = normalize_batch(batch, {"batch_id": "raw-null"})
        self.assertIsNone(row["volume_shares"])
        self.assertIsNone(row["amount_cny"])
        self.assertNotIn("is_suspended", row)

    def test_unmapped_identity_and_fractional_share_conversion_fail(self):
        row = {"ts_code": "600000.SH", "trade_date": "20200102", "vol": 1}
        batch = IngestBatch("market_daily", json.dumps([row]).encode(), {},
                            TUSHARE_DAILY_CONTRACT, tushare_daily_profile(IDENTITIES),
                            OBSERVED, "tushare_daily_v1")
        with self.assertRaisesRegex(DataError, "unmapped source security"):
            normalize_batch(batch, {"batch_id": "raw-x"})
        row["ts_code"] = "000001.SZ"
        row["vol"] = 0.001  # 0.1 share cannot be represented by int64 shares.
        batch = IngestBatch("market_daily", json.dumps([row]).encode(), {},
                            TUSHARE_DAILY_CONTRACT, tushare_daily_profile(IDENTITIES),
                            OBSERVED, "tushare_daily_v1")
        with self.assertRaisesRegex(DataError, "integer after unit conversion"):
            normalize_batch(batch, {"batch_id": "raw-x"})

    def test_generic_event_keeps_source_revision_order_and_bound_evidence(self):
        contract = {
            "contract_id": "membership.synthetic.v1",
            "logical_key": ["membership_id"],
            "fields": {
                "membership_id": {"dtype": "string", "nullable": False},
                "universe_id": {"dtype": "string", "nullable": False},
                "security_id": {"dtype": "string", "nullable": False},
                "effective_from": {"dtype": "date", "nullable": False},
                "effective_to": {"dtype": "date", "nullable": True},
            },
        }
        profile = {
            "id": "synthetic-membership.v1",
            "field_map": {
                "membership_id": "event", "universe_id": "universe",
                "security_id": "security_id", "effective_from": "from",
                "effective_to": "to", "revision_id": "revision",
                "revision_sequence": "sequence", "source_available_at": "published",
                "evidence_ref": "notice",
            },
        }
        source = {
            "event": "event-42", "universe": "idx-300", "security_id": "stable-security-1",
            "from": "2020-01-02", "to": None, "revision": "v2", "sequence": 2,
            "published": "2020-01-01T09:00:00+08:00", "notice": "synthetic-notice-2",
        }
        batch = IngestBatch("universe_membership", json.dumps([source]).encode(), {},
                            contract, profile, OBSERVED)
        row, = normalize_batch(batch, {"batch_id": "raw-event"})
        self.assertEqual(row["revision_id"], "v2")
        self.assertEqual(row["revision_sequence"], 2)
        self.assertEqual(row["source_available_at"], source["published"])
        self.assertEqual(row["evidence_ref"], source["notice"])
        self.assertEqual(row["effective_to"], None)
        source.pop("notice")
        invalid = IngestBatch("universe_membership", json.dumps([source]).encode(), {},
                              contract, profile, OBSERVED)
        with self.assertRaisesRegex(DataError, "requires revision-bound evidence"):
            normalize_batch(invalid, {"batch_id": "raw-event"})

    def test_repeated_response_keeps_revision_identity_but_new_observation(self):
        raw = json.dumps([{"security_id": "sec-1", "session": "2020-01-02",
                           "price": 10.0}]).encode()
        contract = {"contract_id": "daily.synthetic.v1", "logical_key": ["security_id", "session"],
                    "fields": {"security_id": {"dtype": "string", "nullable": False},
                               "session": {"dtype": "date", "nullable": False},
                               "price": {"dtype": "float64", "unit": "CNY/share"}}}
        profile = {"id": "synthetic.v1",
                   "field_map": {"security_id": "security_id", "session": "session", "price": "price"},
                   "source_units": {"price": "CNY/share"}}
        first = IngestBatch("market_daily", raw, {}, contract, profile, OBSERVED)
        later_time = datetime(2026, 9, 29, tzinfo=timezone.utc)
        later = IngestBatch("market_daily", raw, {}, contract, profile, later_time)
        a, = normalize_batch(first, {"batch_id": "raw-a"})
        b, = normalize_batch(later, {"batch_id": "raw-b"})
        self.assertEqual(a["revision_id"], b["revision_id"])
        self.assertNotEqual(a["first_observed_at"], b["first_observed_at"])

    def test_cap_and_scope_fail_closed(self):
        row = {name: None for name in
               ("open", "high", "low", "close", "pre_close", "vol", "amount")}
        row.update(ts_code="000001.SZ", trade_date="20200102")
        request = {"ts_code": "000001.SZ", "trade_date": "20200102"}
        with self.assertRaisesRegex(DataError, "6000-row cap"):
            collect_tushare_daily(FakeClient([row] * 6000), request=request,
                                  identity_map=IDENTITIES, observed_at=OBSERVED)
        wrong_scope = dict(row, trade_date="20200103")
        with self.assertRaisesRegex(DataError, "outside requested session"):
            collect_tushare_daily(FakeClient([wrong_scope]), request=request,
                                  identity_map=IDENTITIES, observed_at=OBSERVED)
        with self.assertRaisesRegex(DataError, "bounded"):
            collect_tushare_daily(FakeClient([]), request={"ts_code": "000001.SZ"},
                                  identity_map=IDENTITIES, observed_at=OBSERVED)


if __name__ == "__main__":
    unittest.main()
