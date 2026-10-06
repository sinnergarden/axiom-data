"""Saved chart prices retain factual units, cutoffs, gaps and immutable output."""

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import pandas as pd

from axiom_data import (Data, DataBatch, ConflictError, EventQuery, IngestBatch, QueryError, QuerySpec,
                        UpdateRequest, load_review_display, project_review_display, save_review_display)


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
                       "source_units": {f: spec["unit"] for f, spec in fields.items() if spec.get("unit")},
                       "availability": {"timezone": "Asia/Shanghai", "session_release_time": "20:00:00"}}
            batches.append(IngestBatch(batch.context["domain"], json.dumps(batch.to_json()["records"]).encode(),
                                       {}, contract, profile, "2026-10-04T00:00:00Z"))
        auxiliary_queries = []
        for domain, key, time_field, extra_fields, row in (
            ("security_master", "listing_date", "listing_date",
             {"name": {"dtype": "string"}, "source_code": {"dtype": "string"}},
             {"listing_date": "2013-05-15", "name": "测试ETF", "source_code": "513100.SH"}),
            ("corporate_actions", "event_id", "effective_date",
             {"effective_date": {"dtype": "date"}, "cash": {"dtype": "float64", "unit": "CNY/fund unit"}},
             {"event_id": "cash-1", "effective_date": "2022-01-12", "cash": None}),
            ("fund_share_conversions", "event_id", "effective_date",
             {"effective_date": {"dtype": "date"}, "ratio_numerator": {"dtype": "int64", "unit": "dimensionless"},
              "ratio_denominator": {"dtype": "int64", "unit": "dimensionless"}},
             {"event_id": "split-1", "effective_date": "2022-01-13", "ratio_numerator": 5, "ratio_denominator": 1}),
        ):
            fields = {"security_id": {"dtype": "string"},
                      key: {"dtype": "date" if key == "listing_date" else "string"}, **extra_fields}
            contract = {"contract_id": "synthetic." + domain,
                        "logical_key": ["security_id", key], "fields": fields}
            profile = {"id": "synthetic", "field_map": {f: f for f in fields},
                       "source_units": {f: spec["unit"] for f, spec in fields.items() if spec.get("unit")},
                       "availability": {"timezone": "Asia/Shanghai", "session_release_time": "20:00:00"}}
            batches.append(IngestBatch(domain, json.dumps([{ "security_id": "ETF", **row}]).encode(),
                                       {}, contract, profile, "2026-10-04T00:00:00Z"))
            auxiliary_queries.append(EventQuery(domain, tuple(extra_fields), ("ETF",), "1900-01-01",
                                               SESSIONS[-1], CUTOFF, "operational_pit_v1", time_field,
                                               purpose="historical_exploration"))
        with TemporaryDirectory() as tmp:
            data = Data(Path(tmp) / "data")
            snapshot = data.update(base_snapshot=None, request=UpdateRequest(tuple(batches), "fixture", {})).snapshot_id
            before = (data.store.root / "current.json").read_bytes()
            queries = [QuerySpec(batch.context["domain"], tuple(batch.field_meta), ("ETF",), SESSIONS,
                                 "operational_pit_v1", {s: CUTOFF for s in SESSIONS},
                                 purpose="historical_exploration") for batch in (prices, factors)]
            expected_auxiliary = [data.events(snapshot=snapshot, query=q).to_json() for q in auxiliary_queries]
            result = data.export_review_display(snapshot=snapshot, price_query=queries[0], factor_query=queries[1],
                                                anchor_session=SESSIONS[-1], destination=Path(tmp) / "review",
                                                security_query=auxiliary_queries[0], event_queries=auxiliary_queries[1:])
            self.assertEqual(result["context"]["snapshot_id"], snapshot)
            self.assertEqual((data.store.root / "current.json").read_bytes(), before)
            saved = json.loads((Path(tmp) / "review/ohlcv.json").read_text())
            self.assertEqual([r["close"] for r in saved["records"]], [2., 2.])
            self.assertEqual(saved["field_meta"]["close"]["unit"], "CNY/fund unit")
            self.assertEqual(saved["field_meta"]["volume_units"]["unit"], "fund units")
            with patch.object(Data, "read", side_effect=AssertionError("loader queried prices")), \
                 patch.object(Data, "events", side_effect=AssertionError("loader queried events")), \
                 patch("axiom_data.review_display.project_review_display", side_effect=AssertionError("loader projected")), \
                 patch("axiom_data.review_display.adjust_prices", side_effect=AssertionError("loader adjusted")):
                loaded = load_review_display(Path(tmp) / "review", manifest_sha256=result["manifest_file_ref"]["sha256"])
            self.assertEqual(loaded["securities"]["batch"], expected_auxiliary[0])
            self.assertEqual(loaded["securities"]["name_validity"], "unknown")
            self.assertEqual(loaded["manifest"]["missing_name_security_ids"], [])
            for batch in expected_auxiliary[1:]:
                self.assertEqual(loaded["events"][batch["context"]["domain"]], batch)
            cash_meta = loaded["events"]["corporate_actions"]["field_meta"]["cash"]["by_key"][0]
            self.assertEqual(cash_meta["missing_reason"], "not_provided")
            self.assertTrue(cash_meta["revision_id"])
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

    def test_missing_and_invalid_factors_keep_scale_and_price_reasons_consistent(self):
        for position in (0, 1):
            for value, state in ((None, "missing"), (float("nan"), "missing"),
                                 (0., "invalid"), (-1., "invalid"), (float("inf"), "invalid"),
                                 (float("-inf"), "invalid")):
                with self.subTest(position=position, value=value):
                    prices, factors = inputs()
                    factors.frame.loc[position, "factor"] = value
                    result = project_review_display(prices, factors, anchor_session=SESSIONS[-1])
                    reason = state + ("_anchor_factor" if position else "_factor")
                    affected = (0, 1) if position else (0,)
                    for i in affected:
                        self.assertIsNone(result["records"][i]["display_scale"])
                        self.assertIsNone(result["records"][i]["close"])
                        self.assertEqual(result["field_meta"]["display_scale"]["by_key"][i]["missing_reason"], reason)
                        self.assertEqual(result["field_meta"]["close"]["by_key"][i]["missing_reason"], reason)
                    self.assertEqual([r["volume_units"] for r in result["records"]], [1000, 9000])
                    self.assertEqual([r["native_close"] for r in result["records"]], [10., 2.])

    def test_stock_units_keep_native_volume_and_amount(self):
        prices, factors = inputs()
        prices.frame.rename(columns={"volume_units": "volume_shares"}, inplace=True)
        prices.field_meta["volume_shares"] = prices.field_meta.pop("volume_units")
        prices.field_meta["volume_shares"]["unit"] = "shares"
        prices.context["query"]["fields"] = list(prices.field_meta)
        for field in ("open", "high", "low", "close"):
            prices.field_meta[field]["unit"] = "CNY/share"
        result = project_review_display(prices, factors, anchor_session=SESSIONS[-1])
        self.assertEqual([r["volume_shares"] for r in result["records"]], [1000, 9000])
        self.assertEqual(result["field_meta"]["volume_shares"]["unit"], "shares")
        self.assertEqual(result["field_meta"]["amount_cny"]["unit"], "CNY")

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
            loaded = load_review_display(target, manifest_sha256=manifest["manifest_file_ref"]["sha256"])
            self.assertEqual(loaded["ohlcv"]["records"][0]["native_close"], 10.)
            with self.assertRaises(ConflictError):
                save_review_display(prices, factors, anchor_session=SESSIONS[-1], destination=target)
            self.assertEqual((target / "ohlcv.json").read_bytes(), saved)
            self.assertEqual(json.loads((target / "manifest.json").read_text())["context"]["anchor_session"], SESSIONS[-1])
            (target / "ohlcv.json").write_bytes(saved + b" ")
            with self.assertRaisesRegex(QueryError, "file byte reference"):
                load_review_display(target, manifest_sha256=manifest["manifest_file_ref"]["sha256"])

    def test_failed_write_does_not_publish_or_leave_temporary_result(self):
        prices, factors = inputs()
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "chart"
            original_write = Path.write_bytes
            def fail_manifest(path, payload):
                if path.name == "manifest.json":
                    raise OSError("disk failure")
                return original_write(path, payload)
            with patch.object(Path, "write_bytes", fail_manifest):
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
