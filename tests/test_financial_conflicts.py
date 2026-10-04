"""Retained real disclosure conflict and offline PIT/audit regressions.

Fixture: income_vip, 2020-08-01..2020-08-31, report_type=1; receipt
2026-10-04T04:41:00.034535Z, batch b_df9dac7789b34603adcb8e69ed30c9e9.
The full 8669-row payload SHA256 is
b3662cf97d178cb41ddf76f292985822a348eb0f6b8825a87e09dd605ac6e182.
These are its two 600816.SH/20200630/20200831 conflicting source rows,
not the full payload. No company/end/update context was captured in that Raw.
"""

from copy import deepcopy
from datetime import datetime, timezone
from itertools import permutations
import json
from pathlib import Path
import tempfile
import unittest

from axiom_data.event_sources import (CONTRACTS, _FIELDS, _financial_unique,
                                      collect_event_response, event_source_profile)
from axiom_data.batch_fetch import run_batch_chunk
from axiom_data.event_reader import read_events
from axiom_data.financial import ttm
from axiom_data.portable import export_bundle, import_bundle
from axiom_data.protocols import DataError, EventQuery
from axiom_data.storage import LocalStore
from axiom_data.updates import apply_saved_raw
from axiom_data.verification import audit_snapshot


ROWS = json.loads((Path(__file__).parent / "fixtures/income_same_disclosure.json").read_text())
OBSERVED = datetime(2026, 10, 4, 4, 41, 0, 34535, tzinfo=timezone.utc)


class FinancialConflictTests(unittest.TestCase):
    def test_real_conflict_only_removes_ambiguous_value_and_is_order_independent(self):
        for group in permutations(ROWS):
            resolved = _financial_unique("income_vip", list(group))[0]
            self.assertIsNone(resolved["total_revenue"])
            self.assertEqual(resolved["n_income_attr_p"], -2856488958.79)
            self.assertEqual(resolved["f_ann_date"], "20200831")
            self.assertEqual(resolved["ann_date"], "20200831")
            self.assertEqual(resolved["__status__total_revenue"], "source_missing")
            self.assertEqual(resolved["__source_issue"], "ambiguous_same_disclosure")

    def test_later_complete_row_resolves_complementary_partials_in_any_order(self):
        complete = {**ROWS[0], "total_revenue": 100.0, "n_income_attr_p": 10.0}
        rows = [{**complete, "total_revenue": None},
                {**complete, "n_income_attr_p": None}, complete]
        for group in permutations(rows):
            self.assertEqual(_financial_unique("income", list(group)), [complete])

    def test_statement_context_is_evidence_and_update_flag_does_not_order(self):
        row = {**ROWS[0], "comp_type": "1", "end_type": "2", "update_flag": "0"}
        flagged = {**row, "update_flag": "1", "total_revenue": ROWS[1]["total_revenue"]}
        result = _financial_unique("income", [row, flagged])[0]
        self.assertIsNone(result["total_revenue"])
        self.assertEqual(result["n_income_attr_p"], row["n_income_attr_p"])
        self.assertIsNone(result["update_flag"])
        result = _financial_unique("income", [row, {**row, "comp_type": "2"}])[0]
        self.assertIsNone(result["total_revenue"])
        self.assertIsNone(result["n_income_attr_p"])

    def test_conflicting_announcement_date_is_not_invented(self):
        result = _financial_unique("income", [ROWS[0], {**ROWS[0], "ann_date": "20200830"}])[0]
        self.assertIsNone(result["ann_date"])
        self.assertEqual(result["f_ann_date"], "20200831")
        self.assertEqual(result["__status__ann_date"], "source_missing")

    def test_conflict_does_not_block_another_batch_request(self):
        class Client:
            def query(self, _endpoint, **params):
                return ROWS if params["period"] == "20200630" else [
                    {**ROWS[0], "end_date": "20200930", "total_revenue": 100.0}]

        with tempfile.TemporaryDirectory() as directory:
            store = LocalStore(directory)
            fetched = run_batch_chunk(
                store, specs=[{"endpoint": "income_vip", "params": {"period": period, "report_type": "1"},
                               "fields": list(_FIELDS["income_vip"]), "canonical_symbols": ["600816.SH"]}
                              for period in ("20200630", "20200930")], client=Client(),
                operation_id="two-request-fetch", plan_fingerprint="old-seven-field-selectors",
                identity_map={"600816.SH": "sec-conflict"}, raw_log_offset=0,
                event_calendar_map={"2020-08-31": "2020-09-01"}, max_workers=2,
                global_calls_per_minute=0, stock_basic_calls_per_minute=0)
            self.assertEqual(fetched["completed_requests"], 2)
            self.assertEqual(fetched["raw_attempts"], 2)
            self.assertEqual(fetched["raw_rows"], 3)
            receipt = apply_saved_raw(store, base_snapshot=None, raw_batch_ids=fetched["raw_batch_ids"],
                                      operation_id="two-request-publish", build_context={"test": "batch"},
                                      promote=False)
            self.assertEqual(audit_snapshot(store, snapshot_id=receipt.snapshot_id)
                             ["source_mapping_checks"]["financial_events"], 2)

    def test_ttm_propagates_ambiguity_without_using_an_older_report(self):
        original = {**ROWS[0], "ann_date": "20200830", "f_ann_date": "20200830",
                    "total_revenue": 20.0}
        rows = [original, *ROWS, *[
            {**ROWS[0], "ann_date": "20210430", "f_ann_date": "20210430",
             "end_date": period, "total_revenue": value, "n_income_attr_p": value / 10}
            for period, value in (("20200331", 10.0), ("20200930", 60.0), ("20201231", 100.0))]]

        class Client:
            def query(self, _endpoint, **params):
                return rows

        with tempfile.TemporaryDirectory() as directory:
            store = LocalStore(directory)
            raw = collect_event_response(
                store, client=Client(), endpoint="income", params={"ts_code": "600816.SH", "report_type": "1",
                                                                 "start_date": "20200801", "end_date": "20210501"},
                identity_map={"600816.SH": "sec-conflict"}, operation_id="ttm-conflict-fetch",
                observed_at=OBSERVED, next_open_session_by_date={"2020-08-30": "2020-08-31",
                                                               "2020-08-31": "2020-09-01",
                                                               "2021-04-30": "2021-05-06"})
            receipt = apply_saved_raw(store, base_snapshot=None, raw_batch_ids=[raw["batch_id"]],
                                      operation_id="ttm-conflict-publish", build_context={"test": "ttm"},
                                      promote=False)
            selected = read_events(store, receipt.snapshot_id, EventQuery(
                "financial_events", ("total_revenue", "parent_net_income"), ("sec-conflict",),
                "2020-01-01", "2020-12-31", OBSERVED.isoformat(), "operational_pit_v1", "report_period"))
            args = dict(endpoint="income", report_type="1", basis="cumulative_ytd", unit="CNY",
                        periods=("2020-12-31",))
            revenue = ttm(selected, field="total_revenue", **args)
            self.assertTrue(revenue.frame.isna()["total_revenue"].iloc[0])
            self.assertEqual(revenue.field_meta["total_revenue"]["by_key"][0]["status"], "source_missing")
            profit = ttm(selected, field="parent_net_income", **args)
            self.assertEqual(profit.frame["parent_net_income"].iloc[0], 10.0)

    def test_old_failed_raw_reinterpretation_preserves_original_receipt_and_bytes(self):
        profile = event_source_profile("income_vip", identity_map={"600816.SH": "sec-conflict"},
                                       next_open_session_by_date={"2020-08-31": "2020-09-01"})
        old_contract = deepcopy(CONTRACTS["income_vip"])
        old_contract["contract_id"] = "local.financial_events.tushare.v1"
        old_contract["fields"] = {name: spec for name, spec in old_contract["fields"].items()
                                  if not name.endswith("__status") and name not in
                                  {"source_issue", "company_type", "report_end_type", "update_flag"}}
        for spec in old_contract["fields"].values():
            spec.pop("status_field", None)
        old_contract["fields"]["announcement_date"]["nullable"] = False
        old_profile = deepcopy(profile)
        old_profile["id"] = "tushare.local.income.v1"
        old_profile.pop("same_disclosure_policy")
        old_profile["field_map"] = {name: source for name, source in old_profile["field_map"].items()
                                    if name in old_contract["fields"]}
        request = {"endpoint": "income_vip", "params": {"period": "20200630", "report_type": "1"},
                   "fields": list(_FIELDS["income_vip"]), "canonical_symbols": ["600816.SH"]}
        with tempfile.TemporaryDirectory() as directory:
            store = LocalStore(Path(directory) / "source")
            original = store.write_raw(json.dumps(ROWS).encode(), domain="financial_events", request=request,
                                       contract=old_contract, source_profile=old_profile, observed_at=OBSERVED,
                                       status="failed", normalizer="event_records_v1", operation_id="old-fetch")
            for operation, observed in (("new-reinterpretation", OBSERVED),
                                        ("bad-receipt", "2026-10-04T04:41:01Z")):
                alias = store.write_raw(
                    store.read_raw_record(original), domain="financial_events",
                    request={**request, "revalidated_from_batch_id": original["batch_id"]},
                    source_profile=profile, contract=CONTRACTS["income_vip"], observed_at=observed,
                    normalizer="event_records_v1", operation_id=operation)
                receipt = apply_saved_raw(store, base_snapshot=None, raw_batch_ids=[alias["batch_id"]],
                                          operation_id=operation + "-publish", build_context={"test": operation},
                                          promote=False)
                if operation == "new-reinterpretation":
                    self.assertEqual(audit_snapshot(store, snapshot_id=receipt.snapshot_id)
                                     ["source_mapping_checks"]["financial_events"], 1)
                    self.assertEqual(alias["payload_sha256"], original["payload_sha256"])
                    self.assertEqual(alias["observed_at"], original["observed_at"])
                    bundle = Path(directory) / "bundle"
                    manifest = export_bundle(store.root, bundle, snapshot_id=receipt.snapshot_id,
                                             code_root=Path(__file__).resolve().parents[1])
                    self.assertEqual(manifest["raw_scope"]["raw_batch_count"], 2)
                    restored = Path(directory) / "restored"
                    import_bundle(bundle, restored)
                    self.assertEqual(audit_snapshot(LocalStore(restored), snapshot_id=receipt.snapshot_id)
                                     ["source_mapping_checks"]["financial_events"], 1)
                else:
                    with self.assertRaisesRegex(DataError, "revalidation changed original bytes or receipt"):
                        audit_snapshot(store, snapshot_id=receipt.snapshot_id)
            self.assertEqual(store.get_raw(original["batch_id"]), original)

    def test_raw_reader_cutoff_unaffected_security_and_independent_audit(self):
        other = {**ROWS[0], "ts_code": "600000.SH", "total_revenue": 100.0}
        responses = [*ROWS, other]

        class Client:
            def query(self, _endpoint, **params):
                self.fields = params["fields"]
                return responses

        with tempfile.TemporaryDirectory() as directory:
            store, client = LocalStore(directory), Client()
            raw = collect_event_response(
                store, client=client, endpoint="income_vip",
                params={"start_date": "20200801", "end_date": "20200831", "report_type": "1"},
                identity_map={"600816.SH": "sec-conflict", "600000.SH": "sec-ok"},
                operation_id="conflict-fetch", observed_at=OBSERVED,
                next_open_session_by_date={"2020-08-31": "2020-09-01"})
            self.assertEqual(json.loads(store.read_raw_record(raw)), responses)
            self.assertTrue({"comp_type", "end_type", "update_flag"} <= set(client.fields.split(",")))
            receipt = apply_saved_raw(store, base_snapshot=None, raw_batch_ids=[raw["batch_id"]],
                                      operation_id="conflict-publish", build_context={"test": "conflict"},
                                      promote=False)
            query = lambda cutoff, policy: EventQuery(
                "financial_events", ("total_revenue", "parent_net_income", "source_issue"),
                ("sec-conflict", "sec-ok"), "2020-01-01", "2020-12-31", cutoff,
                policy, "report_period")
            result = read_events(store, receipt.snapshot_id,
                                 query(OBSERVED.isoformat(), "operational_pit_v1"))
            self.assertEqual(len(result.frame), 2)
            bad, good = result.frame.iloc[0], result.frame.iloc[1]
            self.assertTrue(bad.isna()["total_revenue"])
            self.assertEqual(bad["parent_net_income"], ROWS[0]["n_income_attr_p"])
            self.assertEqual(good["total_revenue"], 100.0)
            meta = result.field_meta["total_revenue"]["by_key"][0]
            self.assertEqual(meta["status"], "source_missing")
            self.assertEqual(meta["missing_reason"], "ambiguous_same_disclosure")
            self.assertEqual(meta["raw_batch_id"], raw["batch_id"])
            self.assertTrue(read_events(store, receipt.snapshot_id,
                                       query("2026-10-04T04:40:59Z", "operational_pit_v1")).frame.empty)
            historical = read_events(store, receipt.snapshot_id,
                                     query("2020-09-01T02:00:00Z", "best_effort_vendor_v1"))
            self.assertEqual(len(historical.frame), 2)
            self.assertEqual(audit_snapshot(store, snapshot_id=receipt.snapshot_id)
                             ["source_mapping_checks"]["financial_events"], 2)
            snapshot = store.load_snapshot(receipt.snapshot_id)
            domain = snapshot["domains"]["financial_events"]
            part = domain["partitions"][0]
            table = store.read_partition(part)
            # Independently audit value, missing status and reason corruption.
            for field, value, message in (
                ("total_revenue", ROWS[0]["total_revenue"], "total_revenue differs"),
                ("total_revenue__status", "not_provided", "status differs"),
                ("source_issue", None, "source_issue differs"),
            ):
                rows = table.to_pylist()
                next(row for row in rows if row["security_id"] == "sec-conflict")[field] = value
                changed = store.write_partition("financial_events", part["partition"], rows, domain["contract"])
                domains = deepcopy(snapshot["domains"])
                domains["financial_events"]["partitions"] = [changed]
                altered = store.publish_snapshot(domains, parent_snapshot=receipt.snapshot_id,
                                                build_context={"tampered": field}, promote=False)
                with self.assertRaisesRegex(DataError, message):
                    audit_snapshot(store, snapshot_id=altered["snapshot_id"])


if __name__ == "__main__":
    unittest.main()
