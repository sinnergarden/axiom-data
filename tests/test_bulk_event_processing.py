"""Period-keyed event partitions stay bounded during incremental publication."""

import json
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime, timezone
from unittest.mock import patch

from axiom_data.batch_fetch import run_batch_chunk
from axiom_data.event_reader import read_events
from axiom_data.event_sources import collect_event_response, event_source_profile
from axiom_data.financial import single_quarter, ttm
from axiom_data.protocols import (ConflictError, CoverageError, DataError, EventQuery,
                                  IngestBatch, QueryError, UpdateRequest)
from axiom_data.storage import LocalStore
from axiom_data.updates import _join_source_profiles, _partition, apply_update, rebuild_from_raw
from axiom_data.updates import apply_saved_raw
from axiom_data.verification import audit_snapshot


CONTRACT = {
    "contract_id": "test.period_events.v1",
    "logical_key": ["security_id", "endpoint", "report_type", "report_period"],
    "fields": {
        "security_id": {"dtype": "string", "nullable": False},
        "endpoint": {"dtype": "string", "nullable": False},
        "report_type": {"dtype": "string", "nullable": False},
        "report_period": {"dtype": "date", "nullable": False},
        "announcement_date": {"dtype": "date", "nullable": False},
        "revenue": {"dtype": "float64", "unit": "CNY"},
    },
}
PROFILE = {
    "id": "test.period_events.v1",
    "field_map": {**{name: name for name in CONTRACT["fields"]},
                  "revision_id": "revision_id", "revision_sequence": "revision_sequence"},
    "source_units": {"revenue": "CNY"},
    "revision_order": "source_sequence_only",
    "availability": {"date_field": "announcement_date", "date_rule": "same_day_release",
                     "timezone": "Asia/Shanghai", "session_release_time": "20:00:00"},
}


def row(period, revenue, revision, sequence):
    return {"security_id": "sec-1", "endpoint": "income", "report_type": "1",
            "report_period": period, "announcement_date": "2026-09-28",
            "revenue": revenue, "revision_id": revision,
            "revision_sequence": sequence}


def batch(rows, observed_day):
    return IngestBatch("financial_events", json.dumps(rows).encode(), {"test": observed_day},
                       CONTRACT, PROFILE,
                       datetime(2026, 9, observed_day, tzinfo=timezone.utc))


def update(store, base, operation, rows, observed_day):
    return apply_update(store, base_snapshot=base, request=UpdateRequest(
        (batch(rows, observed_day),), operation, {"test": operation}, promote=False))


def events(store, snapshot, cutoff):
    return read_events(store, snapshot, EventQuery(
        "financial_events", ("revenue",), ("sec-1",),
        "2019-01-01", "2020-12-31", cutoff, "operational_pit_v1", "report_period"))


class BulkEventProcessingTests(unittest.TestCase):
    def test_indicator_audit_uses_one_dominating_returned_source_row(self):
        partial = {"ts_code": "000001.SZ", "ann_date": "20200420",
                   "end_date": "20191231", "roe": 12.5,
                   "roe_waa": None, "debt_to_assets": 88.8}
        complete = {**partial, "roe_waa": 11.8}

        class Client:
            def query(self, _endpoint, **_params):
                return [partial, complete]

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            store = LocalStore(directory)
            raw = collect_event_response(
                store, client=Client(), endpoint="fina_indicator",
                params={"ts_code": "000001.SZ", "period": "20191231"},
                identity_map={"000001.SZ": "sec-1"},
                observed_at=datetime(2026, 9, 28, tzinfo=timezone.utc),
                operation_id="indicator-dominating-row",
                next_open_session_by_date={"2020-04-20": "2020-04-21"})
            published = apply_saved_raw(store, base_snapshot=None,
                                        raw_batch_ids=[raw["batch_id"]],
                                        operation_id="indicator-dominating-publish",
                                        build_context={"test": "dominating"}, promote=False)
            self.assertEqual(audit_snapshot(store, snapshot_id=published.snapshot_id)
                             ["source_mapping_checks"]["financial_indicator_events"], 1)
            domains = store.load_snapshot(published.snapshot_id)["domains"]
            domain = domains["financial_indicator_events"]
            rows = store.read_partition(domain["partitions"][0]).to_pylist()
            rows[0]["weighted_roe"] = 12.0
            domain["partitions"] = [store.write_partition(
                "financial_indicator_events", domain["partitions"][0]["partition"],
                rows, domain["contract"])]
            altered = store.publish_snapshot(domains, parent_snapshot=published.snapshot_id,
                                             build_context={"tampered": "weighted_roe"}, promote=False)
            with self.assertRaisesRegex(DataError, "weighted_roe differs"):
                audit_snapshot(store, snapshot_id=altered["snapshot_id"])

    def test_revalidated_raw_audits_against_original_source_payload(self):
        source_row = {"ts_code": "000001.SZ", "ann_date": "20200420",
                      "f_ann_date": "20200420", "end_date": "20191231",
                      "report_type": "1", "total_revenue": 100.0,
                      "n_income_attr_p": 10.0}

        class Client:
            def __init__(self):
                self.calls = 0

            def query(self, _endpoint, **_params):
                self.calls += 1
                return [source_row]

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            store, client = LocalStore(directory), Client()
            options = {"specs": [{"endpoint": "income_vip",
                                  "params": {"period": "20191231", "report_type": "1"},
                                  "fields": ["ts_code", "ann_date", "f_ann_date", "end_date",
                                             "report_type", "total_revenue", "n_income_attr_p"],
                                  "canonical_symbols": ["000001.SZ"]}],
                       "client": client, "operation_id": "audit-revalidation",
                       "plan_fingerprint": "audit-revalidation", "identity_map": {"000001.SZ": "sec-1"},
                       "raw_log_offset": 0, "max_attempts": 1,
                       "max_total_requests_per_chunk": 8,
                       "event_calendar_map": {"2020-04-20": "2020-04-21"},
                       "global_calls_per_minute": 0, "stock_basic_calls_per_minute": 0}
            with patch("axiom_data.event_sources._response_issue", return_value="obsolete validator"):
                with self.assertRaises(CoverageError):
                    run_batch_chunk(store, **options)
            failed = store.find_raw_by_operation("audit-revalidation")[0]
            selected = run_batch_chunk(store, **options)
            alias = store.get_raw(selected["raw_batch_ids"][0])
            self.assertEqual(client.calls, 1)
            self.assertEqual(alias["request"]["revalidated_from_batch_id"], failed["batch_id"])
            self.assertEqual(store.read_raw_record(alias), store.read_raw_record(failed))
            receipt = apply_saved_raw(store, base_snapshot=None,
                                      raw_batch_ids=selected["raw_batch_ids"],
                                      operation_id="audit-revalidation-publish",
                                      build_context={"test": "revalidation"}, promote=False)
            self.assertEqual(audit_snapshot(store, snapshot_id=receipt.snapshot_id)
                             ["source_mapping_checks"]["financial_events"], 1)

    def test_cash_flow_ytd_derives_quarters_but_balance_sheet_stock_does_not(self):
        cumulative = {"20200331": 10.0, "20200630": 30.0,
                      "20200930": 60.0, "20201231": 100.0}

        class Client:
            def query(self, endpoint, **params):
                period = params["period"]
                common = {"ts_code": "000001.SZ", "ann_date": "20210420",
                          "f_ann_date": "20210420", "end_date": period,
                          "report_type": "1"}
                if endpoint == "cashflow":
                    return [{**common, "n_cashflow_act": cumulative[period],
                             "n_cashflow_inv_act": -1.0,
                             "n_cash_flows_fnc_act": 2.0}]
                return [{**common, "total_assets": 1000.0,
                         "total_liab": 800.0,
                         "total_hldr_eqy_exc_min_int": 200.0}]

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            store = LocalStore(directory)
            ids = []
            for endpoint in ("cashflow", "balancesheet"):
                periods = cumulative if endpoint == "cashflow" else ("20201231",)
                for period in periods:
                    raw = collect_event_response(
                        store, client=Client(), endpoint=endpoint,
                        params={"ts_code": "000001.SZ", "period": period,
                                "report_type": "1"},
                        identity_map={"000001.SZ": "sec-1"},
                        observed_at=datetime(2026, 9, 28, tzinfo=timezone.utc),
                        operation_id=f"cash-recipe-{endpoint}-{period}",
                        next_open_session_by_date={"2021-04-20": "2021-04-21"})
                    ids.append(raw["batch_id"])
            receipt = apply_saved_raw(store, base_snapshot=None, raw_batch_ids=ids,
                                      operation_id="cash-recipe-publish",
                                      build_context={"test": "cash-recipe"}, promote=False)
            def selected(domain, field):
                return read_events(store, receipt.snapshot_id, EventQuery(
                    domain, (field,), ("sec-1",), "2020-01-01", "2020-12-31",
                    "2026-09-29T00:00:00Z", "operational_pit_v1", "report_period"))

            cash = selected("cash_flow_events", "operating_cash_flow")
            args = {"field": "operating_cash_flow", "endpoint": "cashflow",
                    "report_type": "1", "basis": "cumulative_ytd", "unit": "CNY"}
            self.assertEqual(single_quarter(cash, **args).frame["operating_cash_flow"].tolist(),
                             [10.0, 20.0, 30.0, 40.0])
            self.assertEqual(ttm(cash, **args).frame["operating_cash_flow"].iloc[-1], 100.0)
            self.assertEqual(ttm(cash, **args).context["domain"], "cash_flow_events")
            balance = selected("balance_sheet_events", "total_assets")
            with self.assertRaisesRegex(QueryError, "income or cash-flow events"):
                ttm(balance, field="total_assets", endpoint="balancesheet",
                    report_type="1", basis="period_end_stock", unit="CNY")

    def test_same_fact_with_extended_availability_publishes_metadata_snapshot(self):
        source = {"ts_code": "000001.SZ", "ann_date": "20200420",
                  "f_ann_date": "20200420", "end_date": "20191231",
                  "report_type": "1", "total_revenue": 100.0,
                  "n_income_attr_p": 10.0}

        class Client:
            def query(self, _endpoint, **_params):
                return [source]

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            store = LocalStore(directory)
            first_raw = collect_event_response(
                store, client=Client(), endpoint="income",
                params={"ts_code": "000001.SZ", "period": "20191231", "report_type": "1"},
                identity_map={"000001.SZ": "sec-1"},
                observed_at=datetime(2026, 9, 28, tzinfo=timezone.utc),
                operation_id="availability-first",
                next_open_session_by_date={"2020-04-20": "2020-04-21"})
            first = apply_saved_raw(store, base_snapshot=None,
                                    raw_batch_ids=[first_raw["batch_id"]],
                                    operation_id="availability-first-publish",
                                    build_context={"test": "availability"}, promote=False)
            second_raw = collect_event_response(
                store, client=Client(), endpoint="income",
                params={"ts_code": "000001.SZ", "period": "20191231", "report_type": "1"},
                identity_map={"000001.SZ": "sec-1"},
                observed_at=datetime(2026, 9, 29, tzinfo=timezone.utc),
                operation_id="availability-extended",
                next_open_session_by_date={"2020-04-20": "2020-04-21",
                                               "2020-05-20": "2020-05-21"})
            second = apply_saved_raw(store, base_snapshot=first.snapshot_id,
                                     raw_batch_ids=[second_raw["batch_id"]],
                                     operation_id="availability-extended-publish",
                                     build_context={"test": "availability"}, promote=False)
            self.assertTrue(second.changed)
            self.assertNotEqual(second.snapshot_id, first.snapshot_id)
            older = store.load_snapshot(first.snapshot_id)["domains"]["financial_events"]
            newer = store.load_snapshot(second.snapshot_id)["domains"]["financial_events"]
            self.assertEqual(newer["partitions"], older["partitions"])
            self.assertEqual(newer["source_profile"]["availability"]["next_open_session_by_date"],
                             {"2020-04-20": "2020-04-21", "2020-05-20": "2020-05-21"})
            self.assertEqual(len(list(store.root.glob("canonical/**/*.parquet"))), 1)
            self.assertEqual(len(list(store.root.glob("raw/objects/*/payload.bin"))), 1)

    def test_financial_refresh_reuses_objects_and_rebuild_reads_each_raw_once(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            store = LocalStore(directory)
            base = None
            raw_ids = []
            payload = {"ts_code": "000001.SZ", "ann_date": "20200420",
                       "f_ann_date": "20200420", "end_date": "20191231",
                       "report_type": "1", "total_revenue": 100.0,
                       "n_income_attr_p": 10.0}

            class Client:
                def query(self, _endpoint, **_params):
                    return [dict(payload)]

            def objects(pattern):
                paths = list(store.root.glob(pattern))
                return len(paths), sum(path.stat().st_size for path in paths)

            def observe(day, amount):
                nonlocal base
                payload["total_revenue"] = amount
                raw = collect_event_response(
                    store, client=Client(), endpoint="income",
                    params={"ts_code": "000001.SZ", "period": "20191231",
                            "report_type": "1"}, identity_map={"000001.SZ": "sec-1"},
                    observed_at=datetime(2026, 9, day, tzinfo=timezone.utc),
                    operation_id=f"financial-refresh-fetch-{day}",
                    next_open_session_by_date={"2020-04-20": "2020-04-21"})
                raw_ids.append(raw["batch_id"])
                result = apply_saved_raw(
                    store, base_snapshot=base, raw_batch_ids=[raw["batch_id"]],
                    operation_id=f"financial-refresh-publish-{day}",
                    build_context={"test": "financial-refresh", "day": day}, promote=False)
                base = result.snapshot_id
                return result

            first = observe(24, 100.0)
            first_partition = store.load_snapshot(first.snapshot_id)["domains"]
            first_partition = first_partition["financial_events"]["partitions"][0]
            self.assertEqual(first_partition["partition"], "period-2019")
            canonical_before = objects("canonical/**/*.parquet")
            snapshots_before = objects("snapshots/*.json")
            raw_objects_before = objects("raw/objects/*/payload.bin")

            duplicate = observe(25, 100.0)
            self.assertFalse(duplicate.changed)
            self.assertEqual(duplicate.snapshot_id, first.snapshot_id)
            self.assertEqual(objects("canonical/**/*.parquet"), canonical_before)
            self.assertEqual(objects("snapshots/*.json"), snapshots_before)
            self.assertEqual(objects("raw/objects/*/payload.bin"), raw_objects_before)
            self.assertEqual(first_partition["rows"], 1)

            observe(26, 120.0)
            final = observe(27, 100.0)
            domain = store.load_snapshot(final.snapshot_id)["domains"]["financial_events"]
            self.assertEqual(domain["partitions"][0]["rows"], 3)
            self.assertEqual(objects("raw/objects/*/payload.bin")[0], 2)
            self.assertEqual(len((store.root / "raw" / "fetches.jsonl").read_bytes().splitlines()), 4)
            retained = sorted(store.read_partition(domain["partitions"][0]).to_pylist(),
                              key=lambda item: item["first_observed_at"])
            self.assertEqual([row["total_revenue"] for row in retained], [100.0, 120.0, 100.0])
            self.assertEqual([row["raw_batch_id"] for row in retained],
                             [raw_ids[0], raw_ids[2], raw_ids[3]])
            self.assertEqual(retained[0]["first_observed_at"],
                             datetime(2026, 9, 24, tzinfo=timezone.utc))
            def amount_at(cutoff):
                return read_events(store, final.snapshot_id, EventQuery(
                    "financial_events", ("total_revenue",), ("sec-1",),
                    "2019-01-01", "2019-12-31", cutoff,
                    "operational_pit_v1", "report_period")).frame["total_revenue"].tolist()

            self.assertEqual(amount_at("2026-09-25T12:00:00Z"), [100.0])
            self.assertEqual(amount_at("2026-09-26T12:00:00Z"), [120.0])
            self.assertEqual(amount_at("2026-09-27T12:00:00Z"), [100.0])
            with patch.object(store, "read_raw_record", wraps=store.read_raw_record) as reads:
                rebuilt = rebuild_from_raw(
                    store, base_snapshot=final.snapshot_id, raw_batch_ids=raw_ids,
                    domains=["financial_events"], operation_id="financial-refresh-rebuild",
                    build_context={"test": "financial-refresh-rebuild"}, promote=False)
            self.assertEqual(reads.call_count, len(raw_ids))
            replay = store.load_snapshot(rebuilt.snapshot_id)["domains"]["financial_events"]
            self.assertEqual(replay["partitions"], domain["partitions"])
            self.assertEqual(objects("canonical/**/*.parquet")[0], 3)

    def test_period_year_update_reads_only_affected_partition_and_preserves_old(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            store = LocalStore(directory)
            first = update(store, None, "period-first", [
                row("2019-12-31", 10, "r19", 1),
                row("2020-03-31", 20, "r20", 1)], 28)
            old = store.load_snapshot(first.snapshot_id)["domains"]["financial_events"]
            before = {part["partition"]: part for part in old["partitions"]}
            self.assertEqual(set(before), {"period-2019", "period-2020"})
            object_paths = list(store.root.glob("canonical/financial_events/**/*.parquet"))
            self.assertEqual(len(object_paths), 2)
            bytes_before = sum(path.stat().st_size for path in object_paths)
            with patch.object(store, "read_partition", wraps=store.read_partition) as read:
                revised = update(store, first.snapshot_id, "period-revision",
                                 [row("2020-03-31", 25, "r20b", 2)], 29)
            self.assertEqual([call.args[0]["partition"] for call in read.call_args_list],
                             ["period-2020"])
            after = store.load_snapshot(revised.snapshot_id)["domains"]["financial_events"]
            changed = {part["partition"]: part for part in after["partitions"]}
            self.assertEqual(changed["period-2019"], before["period-2019"])
            self.assertNotEqual(changed["period-2020"], before["period-2020"])
            object_paths = list(store.root.glob("canonical/financial_events/**/*.parquet"))
            self.assertEqual(len(object_paths), 3)
            self.assertGreater(sum(path.stat().st_size for path in object_paths), bytes_before)
            self.assertEqual(len(list(store.root.glob(
                "canonical/financial_events/period-2019/*.parquet"))), 1)
            self.assertEqual(events(store, first.snapshot_id, "2026-09-30T00:00:00Z")
                             .frame["revenue"].tolist(), [10, 20])
            self.assertEqual(events(store, revised.snapshot_id, "2026-09-30T00:00:00Z")
                             .frame["revenue"].tolist(), [10, 25])
            self.assertEqual(events(store, revised.snapshot_id, "2026-09-28T12:00:00Z")
                             .frame["revenue"].tolist(), [10, 20])
            rebuilt = rebuild_from_raw(store, base_snapshot=revised.snapshot_id,
                                       raw_batch_ids=after["raw_batch_ids"],
                                       domains=["financial_events"], operation_id="period-rebuild",
                                       build_context={"test": "period-rebuild"}, promote=False)
            replay = store.load_snapshot(rebuilt.snapshot_id)["domains"]["financial_events"]
            self.assertEqual(replay["partitions"], after["partitions"])
            self.assertEqual(events(store, rebuilt.snapshot_id, "2026-09-30T00:00:00Z")
                             .frame["revenue"].tolist(), [10, 25])

    def test_legacy_history_requires_explicit_raw_rebuild(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            store = LocalStore(directory)
            first = update(store, None, "legacy-first",
                           [row("2019-12-31", 10, "r19", 1)], 28)
            domain = store.load_snapshot(first.snapshot_id)["domains"]["financial_events"]
            original = store.read_partition(domain["partitions"][0]).to_pylist()
            legacy = {**domain, "partitions": [store.write_partition(
                "financial_events", "history", original, CONTRACT)]}
            old = store.publish_snapshot({"financial_events": legacy},
                                         parent_snapshot=first.snapshot_id,
                                         build_context={"test": "legacy-history"}, promote=False)
            with self.assertRaisesRegex(ConflictError, "explicitly rebuild"):
                update(store, old["snapshot_id"], "legacy-increment",
                       [row("2020-03-31", 20, "r20", 1)], 29)
            self.assertEqual(store.load_snapshot(old["snapshot_id"])["domains"]
                             ["financial_events"]["partitions"][0]["partition"], "history")
            raw_ids = [*domain["raw_batch_ids"],
                       store.find_raw_by_operation("legacy-increment")[0]["batch_id"]]
            rebuilt = rebuild_from_raw(store, base_snapshot=old["snapshot_id"],
                                       raw_batch_ids=raw_ids, domains=["financial_events"],
                                       operation_id="legacy-explicit-rebuild",
                                       build_context={"test": "legacy-rebuild"}, promote=False)
            parts = store.load_snapshot(rebuilt.snapshot_id)["domains"]
            self.assertEqual([part["partition"] for part in
                              parts["financial_events"]["partitions"]],
                             ["period-2019", "period-2020"])

    def test_mutable_event_date_does_not_split_one_logical_event(self):
        contract = {"logical_key": ["security_id", "logical_event_key"]}
        self.assertEqual(_partition({"report_period": "2024-12-31",
                                     "effective_date": "2025-05-01"}, contract), "history")

    def test_next_open_calendar_extends_only_when_existing_days_agree(self):
        first = event_source_profile("income", identity_map={"000001.SZ": "sec-1"},
                                     next_open_session_by_date={"2020-04-20": "2020-04-21"})
        second = event_source_profile("income", identity_map={"000001.SZ": "sec-1"},
                                      next_open_session_by_date={"2020-05-20": "2020-05-21"})
        joined = _join_source_profiles(first, second)
        self.assertEqual(joined["availability"]["next_open_session_by_date"],
                         {"2020-04-20": "2020-04-21", "2020-05-20": "2020-05-21"})
        self.assertEqual(first["availability"]["next_open_session_by_date"],
                         {"2020-04-20": "2020-04-21"})
        conflict = event_source_profile("income", identity_map={"000001.SZ": "sec-1"},
                                        next_open_session_by_date={"2020-04-20": "2020-04-22"})
        with self.assertRaisesRegex(ConflictError, "remaps a source date"):
            _join_source_profiles(first, conflict)

    def test_announcement_day_then_terminal_keeps_versions_and_receipt_transitions(self):
        contract = deepcopy(CONTRACT)
        contract["fields"]["actual_announcement_date"] = {"dtype": "date", "nullable": False}
        profile = deepcopy(PROFILE)
        profile["field_map"]["actual_announcement_date"] = "actual_announcement_date"
        profile["revision_order"] = "announcement_day_then_terminal_v1"

        def response(amount, ann, revision, day):
            event = {**row("2020-03-31", amount, revision, 1),
                     "actual_announcement_date": ann}
            source = IngestBatch("financial_events", json.dumps([event]).encode(),
                                 {"test": day}, contract, profile,
                                 datetime(2026, 9, day, tzinfo=timezone.utc))
            return UpdateRequest((source,), f"announcement-{day}", {"test": day}, promote=False)

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            store = LocalStore(directory)
            snapshot = None
            for amount, ann, revision, day in (
                    (10, "2020-04-20", "a", 24),
                    (11, "2020-05-20", "b", 25),
                    (12, "2020-04-20", "c", 26),
                    (10, "2020-04-20", "d", 27)):
                snapshot = apply_update(store, base_snapshot=snapshot,
                                        request=response(amount, ann, revision, day)).snapshot_id
            domain = store.load_snapshot(snapshot)["domains"]["financial_events"]
            rows = store.read_partition(domain["partitions"][0]).to_pylist()
            self.assertEqual(len(rows), 4)
            self.assertEqual([value for value in sorted(row["revenue"] for row in rows)],
                             [10, 10, 11, 12])
            self.assertEqual({row["actual_announcement_date"].isoformat() for row in rows},
                             {"2020-04-20", "2020-05-20"})

            duplicate = [{**row("2020-03-31", 20, "e", 1),
                          "actual_announcement_date": "2020-04-20"},
                         {**row("2020-03-31", 21, "f", 1),
                          "actual_announcement_date": "2020-04-20"}]
            source = IngestBatch("financial_events", json.dumps(duplicate).encode(),
                                 {"test": "ambiguous"}, contract, profile,
                                 datetime(2026, 9, 28, tzinfo=timezone.utc))
            with self.assertRaisesRegex(ConflictError, "ambiguous terminal responses"):
                apply_update(store, base_snapshot=snapshot, request=UpdateRequest(
                    (source,), "announcement-ambiguous", {"test": "ambiguous"}, promote=False))

    def test_event_audit_checks_raw_fields_native_keys_and_unit_values(self):
        source_rows = {
            "income": {"ts_code": "000001.SZ", "ann_date": "20200420",
                       "f_ann_date": "20200420", "end_date": "20191231",
                       "report_type": "1", "total_revenue": 1000000000,
                       "n_income_attr_p": 100000000},
            "income_vip": {"ts_code": "000001.SZ", "ann_date": "20200519",
                           "f_ann_date": "20200520", "end_date": "20191231",
                           "report_type": "1", "total_revenue": 1100000000,
                           "n_income_attr_p": 110000000},
            "balancesheet": {"ts_code": "000001.SZ", "ann_date": "20200420",
                             "f_ann_date": "20200420", "end_date": "20191231",
                             "report_type": "1", "total_assets": 9000000000,
                             "total_liab": 8000000000,
                             "total_hldr_eqy_exc_min_int": 1000000000},
            "cashflow": {"ts_code": "000001.SZ", "ann_date": "20200420",
                         "f_ann_date": "20200420", "end_date": "20191231",
                         "report_type": "1", "n_cashflow_act": 300000000,
                         "n_cashflow_inv_act": -100000000,
                         "n_cash_flows_fnc_act": 200000000},
            "fina_indicator": {"ts_code": "000001.SZ", "ann_date": "20200420",
                               "end_date": "20191231", "roe": 12.5,
                               "roe_waa": 11.8, "debt_to_assets": 88.8888},
            "dividend": {"ts_code": "000001.SZ", "end_date": "20191231",
                         "ann_date": "20200420", "imp_ann_date": None,
                         "div_proc": "预案", "cash_div_tax": 0.5,
                         "stk_bo_rate": 0.1, "stk_co_rate": 0.0,
                         "record_date": None, "ex_date": None},
            "top10_holders": [
                {"ts_code": "000001.SZ", "ann_date": "20200420",
                 "end_date": "20191231", "holder_name": f"holder {index:02d}",
                 "hold_amount": 1000 + index, "hold_ratio": 1 + index / 10}
                for index in range(10)],
            "stk_limit": {"ts_code": "000001.SZ", "trade_date": "20200421",
                          "up_limit": 11, "down_limit": 9},
        }

        class Client:
            def query(self, endpoint, **_params):
                result = source_rows[endpoint]
                return result if isinstance(result, list) else [result]

        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            store = LocalStore(directory)
            identities = {"000001.SZ": "sec-1"}
            raw_ids = []
            for endpoint, params in (
                    ("income", {"ts_code": "000001.SZ", "period": "20191231", "report_type": "1"}),
                    ("income_vip", {"period": "20191231", "report_type": "1"}),
                    ("balancesheet", {"ts_code": "000001.SZ", "period": "20191231", "report_type": "1"}),
                    ("cashflow", {"ts_code": "000001.SZ", "period": "20191231", "report_type": "1"}),
                    ("fina_indicator", {"ts_code": "000001.SZ", "period": "20191231"}),
                    ("dividend", {"ts_code": "000001.SZ"}),
                    ("top10_holders", {"ts_code": "000001.SZ", "period": "20191231"}),
                    ("stk_limit", {"ts_code": "000001.SZ", "trade_date": "20200421"})):
                raw = collect_event_response(
                    store, client=Client(), endpoint=endpoint, params=params,
                    identity_map=identities, observed_at=datetime(2026, 9, 28,
                                                                  tzinfo=timezone.utc),
                    operation_id=f"audit-fetch-{endpoint}",
                    next_open_session_by_date={"2020-04-20": "2020-04-21",
                                               "2020-05-20": "2020-05-21"}
                    if endpoint != "stk_limit" else None)
                raw_ids.append(raw["batch_id"])
            receipt = apply_saved_raw(store, base_snapshot=None, raw_batch_ids=raw_ids,
                                      operation_id="audit-event-publish", build_context={"test": "audit"},
                                      promote=False)
            report = audit_snapshot(store, snapshot_id=receipt.snapshot_id)
            self.assertEqual({name: report["source_mapping_checks"][name] for name in
                              ("financial_events", "balance_sheet_events", "cash_flow_events",
                               "financial_indicator_events", "corporate_actions",
                               "top_holders_reports", "price_limits")},
                             {"financial_events": 2, "corporate_actions": 1,
                              "balance_sheet_events": 1, "cash_flow_events": 1,
                              "financial_indicator_events": 1,
                              "top_holders_reports": 1, "price_limits": 1})

            original = store.load_snapshot(receipt.snapshot_id)
            for field, replacement in (("total_revenue", 999),
                                       ("report_period", "2018-12-31"),
                                       ("raw_batch_id", "missing-raw-reference")):
                domains = deepcopy(original["domains"])
                domain = domains["financial_events"]
                rows = store.read_partition(domain["partitions"][0]).to_pylist()
                rows[0][field] = replacement
                domain["partitions"] = [store.write_partition(
                    "financial_events", domain["partitions"][0]["partition"], rows,
                    domain["contract"])]
                altered = store.publish_snapshot(domains, parent_snapshot=receipt.snapshot_id,
                                                 build_context={"tampered": field}, promote=False)
                with self.assertRaises(DataError):
                    audit_snapshot(store, snapshot_id=altered["snapshot_id"])

            for name, field in (("balance_sheet_events", "total_assets"),
                                ("cash_flow_events", "operating_cash_flow"),
                                ("financial_indicator_events", "weighted_roe")):
                domains = deepcopy(original["domains"])
                domain = domains[name]
                rows = store.read_partition(domain["partitions"][0]).to_pylist()
                rows[0][field] = 999
                domain["partitions"] = [store.write_partition(
                    name, domain["partitions"][0]["partition"], rows,
                    domain["contract"])]
                altered = store.publish_snapshot(domains, parent_snapshot=receipt.snapshot_id,
                                                 build_context={"tampered": field}, promote=False)
                with self.assertRaisesRegex(DataError, f"{name}.{field} differs"):
                    audit_snapshot(store, snapshot_id=altered["snapshot_id"])

            domains = deepcopy(original["domains"])
            holder = domains["top_holders_reports"]
            rows = store.read_partition(holder["partitions"][0]).to_pylist()
            holders = json.loads(rows[0]["holders"])
            holders[0]["hold_amount_shares"] += 1
            rows[0]["holders"] = json.dumps(holders)
            holder["partitions"] = [store.write_partition(
                "top_holders_reports", holder["partitions"][0]["partition"], rows,
                holder["contract"])]
            altered = store.publish_snapshot(domains, parent_snapshot=receipt.snapshot_id,
                                             build_context={"tampered": "holder_amount"}, promote=False)
            with self.assertRaisesRegex(DataError, "holder amounts or names differ"):
                audit_snapshot(store, snapshot_id=altered["snapshot_id"])

    def test_holder_partial_and_duplicate_groups_remain_raw_relative(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            store = LocalStore(directory)
            ids = []
            for operation, ann, count, duplicate in (
                    ("holder-partial", "20200420", 9, False),
                    ("holder-duplicate", "20200520", 10, True)):
                rows = [{"ts_code": "000001.SZ", "end_date": "20191231",
                         "ann_date": ann,
                         "holder_name": "holder 00" if duplicate and i == 1 else f"holder {i:02d}",
                         "hold_amount": 1000 + i, "hold_ratio": 1 + i / 10}
                        for i in range(count)]

                class Client:
                    def query(self, _endpoint, **_params):
                        return rows

                raw = collect_event_response(
                    store, client=Client(), endpoint="top10_holders",
                    params={"ts_code": "000001.SZ", "period": "20191231"},
                    identity_map={"000001.SZ": "sec-1"},
                    observed_at=datetime(2026, 9, 28, tzinfo=timezone.utc),
                    operation_id=operation,
                    next_open_session_by_date={"2020-04-20": "2020-04-21",
                                               "2020-05-20": "2020-05-21"})
                ids.append(raw["batch_id"])
            receipt = apply_saved_raw(store, base_snapshot=None, raw_batch_ids=ids,
                                      operation_id="holder-groups-publish",
                                      build_context={"test": "group-shapes"}, promote=False)
            domain = store.load_snapshot(receipt.snapshot_id)["domains"]["top_holders_reports"]
            rows = store.read_partition(domain["partitions"][0]).to_pylist()
            self.assertEqual({row["group_completeness"] for row in rows},
                             {"partial_supplier_report", "ambiguous_duplicate_holder"})
            self.assertTrue(all(row["top10_ratio"] is None for row in rows))
            self.assertEqual(audit_snapshot(store, snapshot_id=receipt.snapshot_id)
                             ["source_mapping_checks"]["top_holders_reports"], 2)


if __name__ == "__main__":
    unittest.main()
