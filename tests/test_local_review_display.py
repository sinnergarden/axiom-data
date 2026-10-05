"""Saved chart prices retain factual units, cutoffs, gaps and immutable output."""

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import pandas as pd

from axiom_data import (Data, DataBatch, ConflictError, IngestBatch, QueryError, QuerySpec,
                        UpdateRequest, project_review_display, save_review_display)


SESSIONS = ("2022-01-12", "2022-01-14")
CUTOFF = "2026-10-05T00:00:00+00:00"


def inputs():
    records = [dict(security_id="ETF", session=session, open=opening, high=opening * 1.1,
                    low=opening * .9, close=opening, volume_units=volume, amount_cny=amount)
               for session, opening, volume, amount in zip(SESSIONS, (10., 2.), (1000, 9000), (10000., 18000.))]
    def batch(domain, rows, fields):
        metadata = {f: {"dtype": "int64" if f == "volume_units" else "float64", "unit": unit,
                        "by_key": [{"security_id": "ETF", "session": s,
                                    "raw_batch_id": f"raw-{domain}", "revision_id": f"{domain}:{s}",
                                    "usable_from": "2026-10-04T00:00:00+00:00",
                                    "first_observed_at": "2026-10-04T00:00:00+00:00",
                                    "missing_reason": None} for s in SESSIONS]}
                    for f, unit in fields.items()}
        return DataBatch(pd.DataFrame(rows), metadata, {
            "contract_version": "data_batch_v1", "snapshot_id": "s-fixed", "domain": domain,
            "contract_id": domain + "_v1", "source_profile_id": "synthetic", "reader_version": "local_reader_v5",
            "limitations": ["synthetic fixture, not production evidence"], "query": {
                "fields": list(fields), "symbols": ["ETF"], "sessions": list(SESSIONS),
                "cutoff_by_session": {s: CUTOFF for s in SESSIONS}, "pit_policy": "operational_pit_v1",
                "purpose": "historical_exploration", "price_basis": "unadjusted", "adjustment_anchor": None}})
    prices = batch("market_daily", records, {**dict.fromkeys(("open", "high", "low", "close"), "CNY/fund unit"),
                                           "volume_units": "fund units", "amount_cny": "CNY"})
    factors = batch("adjustment_factors", [{"security_id": "ETF", "session": s, "factor": factor}
                                         for s, factor in zip(SESSIONS, (1., 5.))], {"factor": "dimensionless"})
    return prices, factors


class ReviewDisplayTest(unittest.TestCase):
    def test_public_export_reads_one_fixed_synthetic_snapshot_without_promoting_data(self):
        prices, factors = inputs()
        batches = []
        for batch in (prices, factors):
            fields = {"security_id": {"dtype": "string"}, "session": {"dtype": "date"},
                      **{f: {"dtype": spec["dtype"], "unit": spec["unit"]}
                         for f, spec in batch.field_meta.items()}}
            contract = {"contract_id": "synthetic." + batch.context["domain"],
                        "logical_key": ["security_id", "session"], "fields": fields}
            profile = {"id": "synthetic", "field_map": {f: f for f in fields},
                       "availability": {"timezone": "Asia/Shanghai", "session_release_time": "20:00:00"}}
            batches.append(IngestBatch(batch.context["domain"], json.dumps(batch.to_json()["records"]).encode(),
                                       {}, contract, profile, "2026-10-04T00:00:00Z"))
        with TemporaryDirectory() as tmp:
            data = Data(Path(tmp) / "data")
            snapshot = data.update(base_snapshot=None, request=UpdateRequest(tuple(batches), "fixture", {})).snapshot_id
            before = (data.store.root / "current.json").read_bytes()
            queries = [QuerySpec(batch.context["domain"], tuple(batch.field_meta), ("ETF",), SESSIONS,
                                 "operational_pit_v1", {s: CUTOFF for s in SESSIONS},
                                 purpose="historical_exploration") for batch in (prices, factors)]
            result = data.export_review_display(snapshot=snapshot, price_query=queries[0], factor_query=queries[1],
                                                anchor_session=SESSIONS[-1], destination=Path(tmp) / "review")
            self.assertEqual(result["context"]["snapshot_id"], snapshot)
            self.assertEqual((data.store.root / "current.json").read_bytes(), before)
            saved = json.loads((Path(tmp) / "review/ohlcv.json").read_text())
            self.assertEqual([r["close"] for r in saved["records"]], [2., 2.])
            with self.assertRaisesRegex(QueryError, "outside fact storage"):
                data.export_review_display(snapshot=snapshot, price_query=queries[0], factor_query=queries[1],
                                           anchor_session=SESSIONS[-1], destination=data.store.root / "canonical/review")

    def test_split_projection_retains_native_prices_volume_and_provenance(self):
        prices, factors = inputs()
        before = deepcopy(prices.to_json()), deepcopy(factors.to_json())
        result = project_review_display(prices, factors, anchor_session=SESSIONS[-1])
        self.assertEqual([r["close"] for r in result["records"]], [2., 2.])
        self.assertEqual([r["native_close"] for r in result["records"]], [10., 2.])
        self.assertEqual([r["display_scale"] for r in result["records"]], [.2, 1.])
        self.assertEqual([r["volume_units"] for r in result["records"]], [1000, 9000])
        self.assertEqual([r["amount_cny"] for r in result["records"]], [10000., 18000.])
        self.assertEqual(result["field_meta"]["volume_units"], prices.field_meta["volume_units"])
        self.assertEqual(result["field_meta"]["display_scale"]["by_key"][0]["anchor_factor_provenance"]["revision_id"],
                         "adjustment_factors:2022-01-14")
        self.assertEqual((prices.to_json(), factors.to_json()), before)
        self.assertEqual(result["context"]["usage"], "retrospective_review")

    def test_missing_anchor_stays_missing_without_losing_native_bar(self):
        prices, factors = inputs()
        factors.frame.loc[1, "factor"] = None
        result = project_review_display(prices, factors, anchor_session=SESSIONS[-1])
        self.assertTrue(all(r["close"] is None and r["display_scale"] is None for r in result["records"]))
        self.assertEqual(result["records"][0]["native_close"], 10.)
        self.assertEqual(result["field_meta"]["display_scale"]["by_key"][0]["missing_reason"], "missing_anchor_factor")

    def test_missing_price_preserves_reason_and_does_not_invent_volume(self):
        prices, factors = inputs()
        prices.frame.loc[0, "close"] = None
        prices.frame.loc[0, "volume_units"] = None
        prices.field_meta["close"]["by_key"][0]["missing_reason"] = "source_missing"
        prices.field_meta["volume_units"]["by_key"][0]["missing_reason"] = "source_missing"
        result = project_review_display(prices, factors, anchor_session=SESSIONS[-1])
        self.assertIsNone(result["records"][0]["close"])
        self.assertIsNone(result["records"][0]["volume_units"])
        self.assertEqual(result["field_meta"]["close"]["by_key"][0]["missing_reason"], "source_missing")
        self.assertEqual(result["records"][0]["display_scale"], .2)

    def test_rejects_changed_clock_units_and_viewport_anchor(self):
        for kind, error in (("clock", "single decision cutoff"), ("units", "volume unit"),
                            ("purpose", "historical_exploration"), ("anchor", "complete input span")):
            with self.subTest(kind=kind):
                prices, factors = inputs()
                anchor = SESSIONS[-1]
                if kind == "clock": factors.context["query"]["cutoff_by_session"][SESSIONS[0]] = "2026-10-06T00:00:00+00:00"
                if kind == "units": prices.field_meta["volume_units"]["unit"] = "shares"
                if kind == "purpose": prices.context["query"]["purpose"] = "market_replay"
                if kind == "anchor": anchor = SESSIONS[0]
                with self.assertRaisesRegex(QueryError, error):
                    project_review_display(prices, factors, anchor_session=anchor)

    def test_saved_file_digest_and_destination_immutability(self):
        prices, factors = inputs()
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "chart"
            manifest = save_review_display(prices, factors, anchor_session=SESSIONS[-1], destination=target)
            saved = (target / "ohlcv.json").read_bytes()
            self.assertEqual(sha256(saved).hexdigest(), manifest["files"]["ohlcv.json"]["sha256"])
            with self.assertRaises(ConflictError):
                save_review_display(prices, factors, anchor_session=SESSIONS[-1], destination=target)
            self.assertEqual((target / "ohlcv.json").read_bytes(), saved)
            self.assertEqual(json.loads((target / "manifest.json").read_text())["context"]["anchor_session"], SESSIONS[-1])

    def test_failed_write_does_not_publish_or_leave_temporary_result(self):
        prices, factors = inputs()
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "chart"
            with patch.object(Path, "write_text", side_effect=OSError("disk failure")):
                with self.assertRaisesRegex(OSError, "disk failure"):
                    save_review_display(prices, factors, anchor_session=SESSIONS[-1], destination=target)
            self.assertFalse(target.exists())
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_auxiliary_events_cannot_use_a_different_snapshot_or_cutoff(self):
        prices, factors = inputs()
        event = DataBatch(pd.DataFrame(), {}, {"snapshot_id": "s-other", "domain": "corporate_actions",
                         "contract_version": "data_batch_v1", "reader_version": "local_reader_v5",
                         "query": {"pit_policy": "operational_pit_v1", "purpose": "historical_exploration",
                                   "symbols": ["ETF"], "cutoff": CUTOFF}})
        with TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(QueryError, "Snapshot/policy"):
                save_review_display(prices, factors, anchor_session=SESSIONS[-1],
                                    destination=Path(tmp) / "chart", events=(event,))
            self.assertEqual(list(Path(tmp).iterdir()), [])
