"""Synthetic bundles exercising the two reviewed events, never production receipts."""

import base64
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from axiom_data import (Data, DataError, EventQuery, IngestBatch,
                        QuerySpec, UpdateRequest, reviewed_fund_share_conversion_batch)
from axiom_data.protocols import ConflictError
from axiom_data.provider_local import _contract, _f
from axiom_data.verification import audit_snapshot


RECEIPT = datetime(2026, 10, 4, 15, tzinfo=timezone.utc)
NEXT_OPEN = {"2022-01-04": "2022-01-05", "2022-01-14": "2022-01-17",
             "2022-08-23": "2022-08-24", "2022-08-29": "2022-08-30"}
SEC513 = "cn.etf.SSE.513100.20130515"
SEC510 = "cn.etf.SSE.510500.20130315"
CONTEXT = {"scope": "synthetic reviewed conversion integration"}


def fixture_bundle():
    """Use the accepted dates/ratios with unmistakably synthetic document bytes."""
    common = {"event_type": "unit_split", "announcement_precision": "day",
              "extraction_version": "reviewed_fund_share_conversions_v1"}
    first = {**common, "security_id": SEC513, "event_id": "gtfund:513100:unit-split:2022-01",
             "record_date": "2022-01-12", "effective_date": "2022-01-13",
             "effective_phase": "not_stated", "new_price_basis_session": "2022-01-14",
             "new_price_basis_basis": "issuer_announced_next_business_resume_after_conversion",
             "ratio_numerator": 5, "ratio_denominator": 1,
             "quantity_rounding": "not_stated", "quantity_rounding_scope": None,
             "suspension_start": "2022-01-13", "suspension_end": "2022-01-13",
             "suspension_scope": "full_session", "resume_session": "2022-01-14"}
    second = {**common, "security_id": SEC510, "event_id": "nffund:510500:unit-split:2022-08",
              "record_date": "2022-08-26", "effective_date": "2022-08-26",
              "effective_phase": "end_of_day", "new_price_basis_session": "2022-08-29",
              "new_price_basis_basis": "declared_next_open_price_unit_interpretation_after_issuer_end_of_day_conversion",
              "ratio_numerator": 114539, "ratio_denominator": 100000,
              "quantity_rounding": "ceiling_to_whole_fund_unit",
              "quantity_rounding_scope": "registered_holder_units",
              "suspension_start": None, "suspension_end": None,
              "suspension_scope": None, "resume_session": None}
    records, documents = [], []
    for row, plan, result in ((first, "2022-01-04", "2022-01-14"),
                              (second, "2022-08-23", "2022-08-29")):
        plan_id = row["event_id"] + ":plan"
        for sequence, status, announcement in ((1, "planned", plan), (2, "implemented", result)):
            doc_id = row["event_id"] + (":plan" if sequence == 1 else ":result")
            original = ("SYNTHETIC original issuer disclosure " + doc_id).encode()
            documents.append({"document_id": doc_id, "issuer": "SYNTHETIC issuer",
                              "source_url": "https://example.invalid/" + doc_id,
                              "retrieval_host": "example.invalid",
                              "sha256": sha256(original).hexdigest(),
                              "document_base64": base64.b64encode(original).decode(),
                              "actual_document_observed_at": "2026-10-04T14:00:00Z",
                              "announcement_date": announcement, "process_status": status,
                              "locator": "synthetic paragraph 1"})
            refs = [doc_id] if sequence == 1 or row is second else [plan_id, doc_id]
            records.append({**row, "announcement_date": announcement,
                            "process_status": status, "revision_sequence": sequence,
                            "document_refs": json.dumps(refs)})
    return {"documents": documents, "records": records}


def conversion_batch(bundle=None, *, observed_at=RECEIPT, calendar=None):
    return reviewed_fund_share_conversion_batch(
        json.dumps(bundle or fixture_bundle(), ensure_ascii=False).encode(),
        observed_at=observed_at, next_open_session_by_date=calendar or NEXT_OPEN)


def ordinary_batch(domain, key, fields, rows):
    contract = _contract(domain, key, fields)
    names = set(fields)
    return IngestBatch(domain, json.dumps(rows).encode(), {"kind": "synthetic"}, contract,
                       {"id": "synthetic." + domain, "field_map": {n: n for n in names},
                        "source_units": {n: spec["unit"] for n, spec in fields.items() if "unit" in spec},
                        "availability": {"timezone": "Asia/Shanghai", "session_release_time": "20:00:00"}},
                       RECEIPT)


class FundShareConversionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = Data(self.root)

    def update(self, base, operation, batch, *, promote=False):
        return self.data.update(base_snapshot=base, request=UpdateRequest(
            (batch,), operation, CONTEXT, promote))

    def query(self, snapshot, cutoff, *, symbols=(SEC513, SEC510), policy="best_effort_vendor_v1",
              start="2022-01-01", end="2022-12-31", filters=None):
        return self.data.events(snapshot=snapshot, query=EventQuery(
            "fund_share_conversions", ("process_status", "announcement_date", "ratio_numerator",
            "ratio_denominator", "quantity_rounding", "quantity_rounding_scope", "effective_phase",
            "new_price_basis_session", "suspension_start", "suspension_scope", "resume_session"),
            symbols, start, end, cutoff, policy, "effective_date", filters or {}))

    def test_plan_result_cutoffs_exact_units_and_metadata(self):
        result = self.update(None, "conversions", conversion_batch())
        cases = ((SEC513, "2022-01-13T09:30:00+08:00", "planned", 5, 1),
                 (SEC513, "2022-01-14T09:30:00+08:00", "planned", 5, 1),
                 (SEC513, "2022-01-17T09:30:00+08:00", "implemented", 5, 1),
                 (SEC510, "2022-08-26T15:00:00+08:00", "planned", 114539, 100000),
                 (SEC510, "2022-08-29T09:30:00+08:00", "planned", 114539, 100000),
                 (SEC510, "2022-08-30T09:30:00+08:00", "implemented", 114539, 100000))
        for symbol, cutoff, status, numerator, denominator in cases:
            with self.subTest(symbol=symbol, cutoff=cutoff):
                answer = self.query(result.snapshot_id, cutoff, symbols=(symbol,))
                row, = answer.frame.to_dict("records")
                self.assertEqual((row["process_status"], row["ratio_numerator"], row["ratio_denominator"]),
                                 (status, numerator, denominator))
                metadata, = answer.field_meta["process_status"]["by_key"]
                self.assertEqual(metadata["revision_sequence"], 1 if status == "planned" else 2)
                self.assertEqual(metadata["first_observed_at"], RECEIPT.isoformat())
                self.assertEqual(metadata["availability_basis"], "declared_vendor_assumption")
        first = self.query(result.snapshot_id, "2022-01-13T09:30:00+08:00", symbols=(SEC513,)).frame.iloc[0]
        self.assertEqual(first.quantity_rounding, "not_stated")
        self.assertTrue(pd.isna(first.quantity_rounding_scope))
        self.assertEqual(first.effective_phase, "not_stated")
        self.assertEqual(first.suspension_scope, "full_session")
        second = self.query(result.snapshot_id, "2022-08-29T09:30:00+08:00", symbols=(SEC510,)).frame.iloc[0]
        self.assertEqual(second.quantity_rounding, "ceiling_to_whole_fund_unit")
        self.assertEqual(second.quantity_rounding_scope, "registered_holder_units")
        self.assertTrue(pd.isna(second.suspension_start))
        self.assertTrue(pd.isna(second.resume_session))
        for policy in ("operational_pit_v1", "market_pit_safe_v1"):
            self.assertTrue(self.query(result.snapshot_id, "2022-12-31T23:59:59+08:00", policy=policy).frame.empty)
            self.assertTrue(self.query(result.snapshot_id, "2026-10-04T14:59:59Z", policy=policy).frame.empty)
            self.assertEqual(self.query(result.snapshot_id, RECEIPT, policy=policy).frame.process_status.tolist(),
                             ["implemented", "implemented"])
        # Economic filtering is applied after PIT selection, not to pick a hidden result.
        self.assertTrue(self.query(result.snapshot_id, "2022-08-29T09:30:00+08:00",
                                   symbols=(SEC510,), filters={"process_status": "implemented"}).frame.empty)

    def test_raw_bundle_repeat_offline_rebuild_and_other_domains_unchanged(self):
        symbols = (SEC513, SEC510)
        market = ordinary_batch("market_daily", ("security_id", "session"),
            {"security_id": _f("string", False), "session": _f("date", False), "close": _f("float64")},
            [{"security_id": SEC513, "session": "2022-01-14", "close": 1.015},
             {"security_id": SEC510, "session": "2022-08-29", "close": 6.3}])
        calendar = ordinary_batch("trading_calendar", ("exchange", "session"),
            {"exchange": _f("string", False), "session": _f("date", False), "is_open": _f("bool", False)},
            [{"exchange": "SSE", "session": session, "is_open": True}
             for session in ("2022-01-13", "2022-01-14", "2022-08-26", "2022-08-29")])
        master = ordinary_batch("security_master", ("security_id", "listing_date"),
            {"security_id": _f("string", False), "listing_date": _f("date", False),
             "delisting_date": _f("date"), "exchange": _f("string", False)},
            [{"security_id": symbol, "listing_date": "2013-01-01", "delisting_date": None, "exchange": "SSE"}
             for symbol in symbols])
        status = ordinary_batch("security_status", ("security_id", "session"),
            {"security_id": _f("string", False), "session": _f("date", False),
             "is_suspended": _f("bool"), "status_reason": _f("string", False)}, [])
        others = tuple(ordinary_batch(domain, ("security_id", "session"),
            {"security_id": _f("string", False), "session": _f("date", False), name: _f("float64")},
            [{"security_id": SEC513, "session": "2022-01-14", name: value}])
            for domain, name, value in (("corporate_actions", "cash", .1),
                                       ("adjustment_factors", "factor", 5.0019),
                                       ("price_limits", "up_limit", 1.117)))
        base = self.data.update(base_snapshot=None, request=UpdateRequest(
            (market, calendar, master, status, *others), "base", CONTEXT)).snapshot_id
        old_path = self.root / "snapshots" / (base + ".json")
        old_bytes = old_path.read_bytes()
        old_domains = self.data.store.load_snapshot(base)["domains"]
        query = QuerySpec("market_daily", ("close",), (SEC513,), ("2022-01-13",),
                          "operational_pit_v1", {"2022-01-13": RECEIPT})
        before = self.data.states(snapshot=base, query=query)
        candidate = self.update(base, "supplement", conversion_batch())
        self.assertEqual(self.data.resolve(), base)
        new_domains = self.data.store.load_snapshot(candidate.snapshot_id)["domains"]
        for domain, state in old_domains.items():
            self.assertEqual(new_domains[domain], state)
        self.assertEqual(old_path.read_bytes(), old_bytes)
        after = self.data.states(snapshot=candidate.snapshot_id, query=query)
        pd.testing.assert_frame_equal(after.frame, before.frame)
        self.assertEqual(after.field_meta, before.field_meta)
        self.assertEqual(before.frame.iloc[0]["market_state"], "unknown_status")
        raw_id, = new_domains["fund_share_conversions"]["raw_batch_ids"]
        raw = self.data.store.get_raw_many([raw_id])[raw_id]
        retained = json.loads(self.data.store.read_raw_record(raw))
        self.assertEqual(len(retained["documents"]), 4)
        self.assertEqual(retained["records"], fixture_bundle()["records"])
        count = len((self.root / "raw/fetches.jsonl").read_text().splitlines())
        self.assertEqual(self.update(base, "supplement", conversion_batch()), candidate)
        self.assertEqual(len((self.root / "raw/fetches.jsonl").read_text().splitlines()), count)
        repeated = self.update(candidate.snapshot_id, "later-receipt", conversion_batch(
            observed_at=datetime(2026, 10, 5, tzinfo=timezone.utc)))
        self.assertFalse(repeated.changed)
        self.assertEqual(repeated.snapshot_id, candidate.snapshot_id)
        rebuilt = self.data.rebuild(base_snapshot=candidate.snapshot_id, raw_batch_ids=[raw_id],
            domains=("fund_share_conversions",), operation_id="offline-rebuild", build_context=CONTEXT, promote=False)
        original_answer = self.query(candidate.snapshot_id, RECEIPT, policy="operational_pit_v1")
        rebuilt_answer = self.query(rebuilt.snapshot_id, RECEIPT, policy="operational_pit_v1")
        pd.testing.assert_frame_equal(rebuilt_answer.frame, original_answer.frame)
        self.assertEqual(rebuilt_answer.field_meta, original_answer.field_meta)
        rebuilt_domains = self.data.store.load_snapshot(rebuilt.snapshot_id)["domains"]
        for domain, state in old_domains.items():
            self.assertEqual(rebuilt_domains[domain], state)
        self.assertEqual(old_path.read_bytes(), old_bytes)

    def test_bad_documents_or_facts_leave_raw_and_current_unchanged(self):
        base = self.update(None, "valid", conversion_batch(), promote=True).snapshot_id
        mutations = {
            "bad-hash": lambda b: b["documents"][0].update(sha256="0" * 64),
            "late-receipt": lambda b: b["documents"][0].update(actual_document_observed_at="2026-10-05T00:00:00Z"),
            "missing-ref": lambda b: b["records"][0].update(document_refs='["absent"]'),
            "future-proof": lambda b: b["records"][0].update(document_refs=b["records"][1]["document_refs"]),
            "float-ratio": lambda b: b["records"][0].update(ratio_numerator=5.0019),
            "unreduced-ratio": lambda b: b["records"][0].update(ratio_numerator=10, ratio_denominator=2),
            "NAV-rounding": lambda b: b["records"][0].update(quantity_rounding="round_half_up_four_decimals"),
            "modeled-phase": lambda b: b["records"][0].update(effective_phase="pre_open"),
            "partial-halt": lambda b: b["records"][0].update(suspension_end=None),
            "unsupported": lambda b: b["records"][0].update(event_type="cash_compensation"),
            "fake-receipt": lambda b: b["records"][0].update(first_observed_at="2022-01-04T00:00:00Z"),
            "reverse-order": lambda b: b["records"][0].update(revision_sequence=3),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                bundle = fixture_bundle()
                mutate(bundle)
                with self.assertRaises(DataError):
                    self.update(base, label, conversion_batch(bundle))
                self.assertEqual(self.data.resolve(), base)
                self.assertEqual(len(self.data.store.find_raw_by_operation(label)), 1)

    def test_stable_event_date_correction_selected_before_economic_range(self):
        bundle = fixture_bundle()
        # The same issuer event may be corrected; identity never comes from dates or ratio.
        bundle["records"][1].update(effective_date="2022-01-12")
        result = self.update(None, "date-correction", conversion_batch(bundle))
        plan = self.query(result.snapshot_id, "2022-01-13T09:30:00+08:00", symbols=(SEC513,),
                          start="2022-01-13", end="2022-01-13")
        self.assertEqual(plan.frame.process_status.tolist(), ["planned"])
        confirmed = self.query(result.snapshot_id, "2022-01-17T09:30:00+08:00", symbols=(SEC513,),
                               start="2022-01-13", end="2022-01-13")
        self.assertTrue(confirmed.frame.empty)

    def test_same_contract_accepts_another_event_and_safe_calendar_extension(self):
        first = self.update(None, "first", conversion_batch())
        bundle = fixture_bundle()
        bundle["documents"] = bundle["documents"][:1]
        bundle["records"] = bundle["records"][:1]
        doc, = bundle["documents"]
        row, = bundle["records"]
        doc.update(document_id="another-plan", announcement_date="2022-08-24")
        row.update(security_id="cn.etf.SSE.123456.20200101", event_id="another-issuer:another-split",
                   announcement_date="2022-08-24", record_date="2022-08-26", effective_date="2022-08-26",
                   effective_phase="end_of_day", new_price_basis_session="2022-08-29",
                   document_refs='["another-plan"]', suspension_start=None, suspension_end=None,
                   suspension_scope=None, resume_session=None)
        calendar = {**NEXT_OPEN, "2022-08-24": "2022-08-25"}
        second = self.update(first.snapshot_id, "another", conversion_batch(bundle, calendar=calendar))
        query = self.query(second.snapshot_id, "2022-08-25T09:30:00+08:00",
                           symbols=(row["security_id"],))
        self.assertEqual(query.frame.event_id.tolist(), [row["event_id"]])
        self.assertEqual(query.frame.ratio_numerator.tolist(), [5])
        changed_clock = {**calendar, "2022-01-04": "2022-01-06"}
        with self.assertRaises(ConflictError):
            self.update(second.snapshot_id, "changed-clock", conversion_batch(bundle, calendar=changed_clock))

    def test_conflicting_extract_sequence_cannot_publish(self):
        base = self.update(None, "initial", conversion_batch(), promote=True).snapshot_id
        bundle = fixture_bundle()
        bundle["records"][0].update(ratio_numerator=6)
        with self.assertRaises(ConflictError):
            self.update(base, "conflicting-extract", conversion_batch(bundle))
        self.assertEqual(self.data.resolve(), base)
        self.assertEqual(len(self.data.store.find_raw_by_operation("conflicting-extract")), 1)

    def test_frozen_mapping_and_announcement_clock_cannot_be_overridden(self):
        base = self.update(None, "original", conversion_batch(), promote=True).snapshot_id
        original = conversion_batch()
        for kind in ("mapping", "clock"):
            profile = deepcopy(original.source_profile)
            if kind == "mapping":
                profile["field_map"].update(ratio_numerator="ratio_denominator",
                                           ratio_denominator="ratio_numerator")
            else:
                profile["availability"].update(date_rule="same_day_release", session_release_time="00:00:00")
            with self.assertRaisesRegex(DataError, "frozen field mapping and announcement clock"):
                self.update(base, "changed-" + kind, replace(original, source_profile=profile))
            self.assertEqual(self.data.resolve(), base)
            self.assertEqual(len(self.data.store.find_raw_by_operation("changed-" + kind)), 1)

    def test_later_bundle_cannot_regress_completed_event_to_old_plan(self):
        base = self.update(None, "completed", conversion_batch(), promote=True).snapshot_id
        before = self.query(base, "2022-01-17T09:30:00+08:00", symbols=(SEC513,)).to_json()
        bundle = fixture_bundle()
        bundle["documents"] = bundle["documents"][:1]
        bundle["records"] = bundle["records"][:1]
        bundle["records"][0].update(revision_sequence=3, ratio_numerator=6)
        with self.assertRaisesRegex(DataError, "document sequence contradicts"):
            self.update(base, "regressed-plan", conversion_batch(bundle))
        self.assertEqual(self.data.resolve(), base)
        self.assertEqual(len(self.data.store.find_raw_by_operation("regressed-plan")), 1)
        self.assertEqual(self.query(base, "2022-01-17T09:30:00+08:00", symbols=(SEC513,)).to_json(), before)

    def test_independent_audit_checks_native_mapping_and_retained_source_closure(self):
        result = self.update(None, "auditable", conversion_batch())
        report = audit_snapshot(self.data.store, snapshot_id=result.snapshot_id)
        self.assertEqual(report["source_mapping_checks"]["fund_share_conversions"], 4)
        self.assertTrue(any("manual extracts" in text for text in report["limitations"]))
        manifest = self.data.store.load_snapshot(result.snapshot_id)
        domain = deepcopy(manifest["domains"]["fund_share_conversions"])
        part, = domain["partitions"]
        rows = self.data.store.read_partition(part).to_pylist()
        rows[0]["ratio_numerator"] = 7
        with self.data.store.writer():
            domain["partitions"] = [self.data.store.write_partition(
                "fund_share_conversions", "history", rows, domain["contract"])]
            altered = self.data.store.publish_snapshot(
                {"fund_share_conversions": domain}, parent_snapshot=result.snapshot_id,
                build_context=CONTEXT, promote=False)["snapshot_id"]
        with self.assertRaisesRegex(DataError, "ratio_numerator differs"):
            audit_snapshot(self.data.store, snapshot_id=altered)
