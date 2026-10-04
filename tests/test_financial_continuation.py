"""Explicit financial continuation, with no provider network.

The stopped source fixture models the legacy duplicate-disclosure rejection
and seven-field selector policy. Real retained v1 Raw is exercised separately
by test_financial_conflicts; no test invents a supplier revision order.
"""
from contextlib import redirect_stdout
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from axiom_data import cli
from axiom_data.builder import freeze_builder
from axiom_data.bulk_jobs import plan_bulk_job
from axiom_data.event_sources import _FIELDS, _response_issue
from axiom_data.financial_continuation import (
    continue_financial_bulk, prepare_financial_continuation,
    verify_continuation_selectors)
from axiom_data.full_sources import plan_full_sources, run_full_sources, verify_full_sources
from axiom_data.protocols import ConflictError, CoverageError
from axiom_data.storage import LocalStore
from axiom_data.updates import apply_saved_raw


ROWS = json.loads((Path(__file__).parent / "fixtures/income_same_disclosure.json").read_text())
OBSERVED = datetime(2026, 10, 4, 4, 41, tzinfo=timezone.utc)
OPTIONS = dict(max_attempts=1, max_workers=1, min_interval_seconds=0,
               global_calls_per_minute=50000, stock_basic_calls_per_minute=50000)


class Client:
    def __init__(self, *, continuation=False):
        self.calls = []
        self.continuation = continuation

    def query(self, endpoint, *, fields, **params):
        self.calls.append((endpoint, params))
        if self.continuation and endpoint != "fina_indicator_vip":
            raise AssertionError("continuation repeated a retained supplier request")
        if endpoint == "trade_cal":
            day = datetime.strptime(params["start_date"], "%Y%m%d").date()
            end = datetime.strptime(params["end_date"], "%Y%m%d").date()
            rows = []
            while day <= end:
                rows.append(dict(exchange=params["exchange"], cal_date=day.strftime("%Y%m%d"),
                                 is_open="1" if day.weekday() < 5 else "0"))
                day += timedelta(days=1)
            return rows
        if endpoint == "income_vip":
            if params["start_date"] == "20200801":
                return ROWS
            if params["start_date"] == "20200701":
                return [{**ROWS[0], "ann_date": "20200731", "f_ann_date": "20200731",
                         "total_revenue": 100.0, "n_income_attr_p": 10.0}]
            return []
        if endpoint == "fina_indicator_vip":
            return [dict(ts_code="600816.SH", ann_date="20200831", end_date=params["period"],
                         roe=1.0, roe_waa=2.0, debt_to_assets=3.0)]
        raise AssertionError(endpoint)


def legacy_issue(endpoint, rows, params, profile):
    if endpoint == "income_vip" and len(rows) == 2:
        return "legacy same-disclosure conflict"
    return _response_issue(endpoint, rows, params, profile)


class FinancialContinuationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = LocalStore(self.directory.name)
        market = plan_bulk_job(mode="bulk", symbols=["600816.SH"],
            identity_map={"600816.SH": "sec-conflict"}, start_session="2020-08-31",
            end_session="2020-08-31", endpoints=("trade_cal",))
        self.plan = plan_full_sources(market=market, calendar_end="2020-10-31",
            event_endpoints=("income", "fina_indicator"), max_event_requests_per_chunk=12)
        with patch("axiom_data.full_sources.request_fields", side_effect=lambda ep: _FIELDS[ep]), \
                patch("axiom_data.event_sources._response_issue", side_effect=legacy_issue):
            with self.assertRaises(CoverageError):
                run_full_sources(self.store, plan=self.plan, client=Client(), operation_id="original",
                    base_snapshot=None, promote=False, clock=lambda: OBSERVED, **OPTIONS)
        self.original_files = {p.name: p.read_bytes() for p in (self.store.root / "operations").glob("original*.json")}
        self.source_hash = sha256(self.original_files["original.json"]).hexdigest()
        self.options = dict(plan=self.plan, source_operation_id="original",
            source_checkpoint_sha256=self.source_hash, operation_id="continuation", promote=False,
            clock=lambda: OBSERVED, **OPTIONS)

    def run_continuation(self, client=None):
        return continue_financial_bulk(self.store, client=client or Client(continuation=True), **self.options)

    def assert_original_unchanged(self):
        for name, data in self.original_files.items():
            self.assertEqual((self.store.root / "operations" / name).read_bytes(), data)

    def test_reuses_completed_and_failed_receipts_then_verifies_every_selector(self):
        before = self.store.raw_log_size()
        binding = prepare_financial_continuation(self.store, plan=self.plan,
            source_operation_id="original", source_checkpoint_sha256=self.source_hash)
        self.assertEqual(binding["reused_initial_requests"], 17)
        self.assertEqual(binding["planned_event_requests"], 22)
        self.assertEqual(self.store.raw_log_size(), before)
        client = Client(continuation=True)
        receipt = self.run_continuation(client)
        state = self.store.read_operation("continuation")
        self.assertEqual(len(client.calls), 5)
        self.assertEqual(state["reused_event_requests"], 17)
        self.assertEqual(state["event_raw_attempts"], 5)
        self.assertEqual(state["audit"]["source_mapping_checks"]["financial_events"], 2)
        self.assertTrue(verify_full_sources(self.store, plan=self.plan, operation_id="continuation")["verified"])
        original = self.store.load_snapshot(binding["event_base_snapshot"])
        final = self.store.load_snapshot(receipt.snapshot_id)
        self.assertEqual(final["domains"]["trading_calendar"], original["domains"]["trading_calendar"])
        self.assert_original_unchanged()
        again = self.run_continuation(client)
        self.assertEqual(again.snapshot_id, receipt.snapshot_id)
        self.assertEqual(len(client.calls), 5)

    def test_crash_after_publication_resumes_without_duplicate_aliases_or_calls(self):
        def crash_after_publish(*args, **kwargs):
            apply_saved_raw(*args, **kwargs)
            raise KeyboardInterrupt()

        client = Client(continuation=True)
        with patch("axiom_data.financial_continuation.apply_saved_raw", side_effect=crash_after_publish):
            with self.assertRaises(KeyboardInterrupt):
                self.run_continuation(client)
        self.assertEqual(self.store.read_operation("continuation")["status"], "interrupted")
        child_records = self.store.find_raw_by_operation("continuation.e.c000000")
        self.assertEqual(len(child_records), 9)
        self.assertEqual(client.calls, [])
        changed = deepcopy(freeze_builder())
        changed["source"]["sha256"] = "changed-builder"
        with patch("axiom_data.builder.freeze_builder", return_value=changed):
            with self.assertRaises(ConflictError):
                self.run_continuation(client)
        self.assertEqual(client.calls, [])
        self.run_continuation(client)
        self.assertEqual(len(self.store.find_raw_by_operation("continuation.e.c000000")), 9)
        self.assertEqual(len(client.calls), 5)
        self.assert_original_unchanged()

    def test_changed_original_checkpoint_rejected_before_new_operation_or_supplier_call(self):
        source = self.store.read_operation("original")
        source["error"] = {"type": "changed"}
        self.store.write_operation("original", source)
        client = Client(continuation=True)
        with self.assertRaises(ConflictError):
            self.run_continuation(client)
        self.assertIsNone(self.store.read_operation("continuation"))
        self.assertEqual(client.calls, [])

    def test_independent_verifier_rejects_selector_tampering_with_unchanged_counts(self):
        self.run_continuation()
        child = self.store.read_operation("continuation.e.c000001")
        child["tasks"][0]["params"]["start_date"] = "20200102"
        self.store.write_operation("continuation.e.c000001", child)
        with self.assertRaises(CoverageError):
            verify_continuation_selectors(self.store, self.store.read_operation("continuation"),
                                         self.plan, "continuation")

    def test_failed_audit_never_promotes_and_retry_only_rechecks_candidate(self):
        self.options["promote"] = True
        client = Client(continuation=True)
        with patch("axiom_data.verification.audit_snapshot", return_value={"status": "failed"}):
            with self.assertRaises(CoverageError):
                self.run_continuation(client)
        self.assertFalse((self.store.root / "current.json").exists())
        calls = list(client.calls)
        self.run_continuation(client)
        self.assertEqual(client.calls, calls)
        self.assertEqual(self.store.read_operation("continuation")["status"], "success")
        self.assert_original_unchanged()

    def test_cli_dry_run_and_explicit_status_verify_audit_use_new_operation(self):
        body = dict(schema_version=cli._FULL_SCHEMA, job=self.plan.to_dict(), operation_id="original",
                    base_snapshot=None, promote=False, **OPTIONS)
        path = self.store.root / "plan.json"
        path.write_text(json.dumps({**body, "plan_sha256": cli._digest(body)}))
        prefix = ["--data-root", str(self.store.root)]
        def invoke(args, client):
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(cli.main(prefix + args, client=client), 0)
            return json.loads(output.getvalue())
        args = ["continue-financial", "--plan", str(path), "--operation-id", "continuation",
                "--source-checkpoint-sha256", self.source_hash]
        client = Client(continuation=True)
        before = self.store.raw_log_size()
        dry = invoke(args + ["--dry-run"], client)
        self.assertEqual(dry["reused_initial_requests"], 17)
        self.assertEqual(dry["remaining_initial_requests"], 5)
        self.assertEqual(client.calls, [])
        self.assertEqual(before, self.store.raw_log_size())
        self.assertIsNone(self.store.read_operation("continuation"))
        invoke(args, client)
        for command in ("status", "verify", "audit"):
            read_args = [command, "--plan", str(path), "--continuation-operation-id", "continuation"]
            if command == "audit":
                read_args += ["--output", str(self.store.root / "audit.json"), "--snapshot",
                              self.store.read_operation("continuation")["result"]["snapshot_id"]]
            result = invoke(read_args, client)
            self.assertNotEqual(result.get("status"), "failed")
        self.assertEqual(len(client.calls), 5)
        self.assert_original_unchanged()

    def test_future_capped_event_split_is_covered_and_omitted_child_rejected(self):
        market = plan_bulk_job(mode="daily", symbols=["600816.SH"],
            identity_map={"600816.SH": "sec-conflict"}, start_session="2020-08-31",
            end_session="2020-08-31", endpoints=("trade_cal",))
        plan = plan_full_sources(market=market, calendar_end="2020-10-31",
            announcement_start="2020-08-30", event_endpoints=("income", "dividend"))

        class CappedClient(Client):
            def query(self, endpoint, *, fields, **params):
                if endpoint != "dividend":
                    if endpoint == "income_vip":
                        self.calls.append((endpoint, params))
                        return ROWS
                    return super().query(endpoint, fields=fields, **params)
                self.calls.append((endpoint, params))
                if params["ann_date"] == "20200831":
                    return []
                row = dict(ts_code="600816.SH", ann_date="20200830", end_date="20191231",
                           div_proc="预案", cash_div_tax=0.5, stk_bo_rate=0.0, stk_co_rate=0.0,
                           imp_ann_date=None, record_date=None, ex_date=None)
                return [row] if "ts_code" in params else [row, row]

        with patch("axiom_data.full_sources.request_fields", side_effect=lambda ep: _FIELDS[ep]), \
                patch("axiom_data.event_sources._response_issue", side_effect=legacy_issue):
            with self.assertRaises(CoverageError):
                run_full_sources(self.store, plan=plan, client=CappedClient(), operation_id="cap-original",
                    base_snapshot=None, promote=False, clock=lambda: OBSERVED, **OPTIONS)
        digest = sha256((self.store.root / "operations/cap-original.json").read_bytes()).hexdigest()
        client = CappedClient()
        with patch.dict("axiom_data.event_sources._CAPS", {"dividend": 2}):
            continue_financial_bulk(self.store, plan=plan, source_operation_id="cap-original",
                source_checkpoint_sha256=digest, operation_id="cap-continuation", client=client,
                promote=False, clock=lambda: OBSERVED, **OPTIONS)
            self.assertEqual([ep for ep, _ in client.calls], ["dividend"] * 3)
            child = self.store.read_operation("cap-continuation.e.c000001")
            self.assertEqual(child["tasks"][0]["status"], "split")
            child["tasks"][0]["child_indexes"] = []
            self.store.write_operation("cap-continuation.e.c000001", child)
            with self.assertRaises(CoverageError):
                verify_continuation_selectors(self.store, self.store.read_operation("cap-continuation"),
                                             plan, "cap-continuation")

    def test_new_builder_chains_stopped_continuation_and_reuses_unpublished_chunk(self):
        def stop_second_publish(*args, **kwargs):
            if kwargs['operation_id'].endswith('.c000001.publish'):
                raise RuntimeError('retained response publication stopped')
            return apply_saved_raw(*args, **kwargs)

        old_client = Client(continuation=True)
        with patch('axiom_data.financial_continuation.apply_saved_raw', side_effect=stop_second_publish):
            with self.assertRaises(RuntimeError):
                self.run_continuation(old_client)
        self.assertEqual(old_client.calls, [])
        source_files = {p.name:p.read_bytes() for p in (self.store.root/'operations').glob('continuation*.json')}
        source_sha = sha256(source_files['continuation.json']).hexdigest()
        binding = prepare_financial_continuation(self.store, plan=self.plan,
            source_operation_id='continuation', source_checkpoint_sha256=source_sha)
        self.assertEqual(binding['reference_operation_id'], 'original')
        self.assertEqual(binding['reused_initial_requests'], 17)
        new_builder = deepcopy(freeze_builder())
        new_builder['source']['sha256'] = 'new-reviewed-builder'
        options = {**self.options, 'operation_id':'next-continuation',
                   'source_operation_id':'continuation', 'source_checkpoint_sha256':source_sha}
        client = Client(continuation=True)
        with patch('axiom_data.builder.freeze_builder', return_value=new_builder):
            result = continue_financial_bulk(self.store, client=client, **options)
        self.assertEqual(len(client.calls), 5)
        state = self.store.read_operation('next-continuation')
        self.assertEqual(state['builder'], new_builder)
        self.assertEqual(state['reference_operation_id'], 'original')
        self.assertEqual(state['source_binding']['source_builder'], self.store.read_operation('continuation')['builder'])
        report = verify_full_sources(self.store, plan=self.plan, operation_id='next-continuation')
        self.assertEqual(report['reused_event_requests'], 17)
        self.assertEqual(report['source_operation_id'], 'continuation')
        for name, data in source_files.items():
            self.assertEqual((self.store.root/'operations'/name).read_bytes(), data)
        self.assert_original_unchanged()
        # CLI readers keep the unchanged original plan while explicitly reading
        # the newest operation, whose immediate source is another continuation.
        body = dict(schema_version=cli._FULL_SCHEMA, job=self.plan.to_dict(), operation_id='original',
                    base_snapshot=None, promote=False, **OPTIONS)
        path = self.store.root/'chain.plan.json'
        path.write_text(json.dumps({**body, 'plan_sha256':cli._digest(body)}))
        for command in ('status', 'verify'):
            with redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(['--data-root',str(self.store.root),command,'--plan',str(path),
                    '--continuation-operation-id','next-continuation'],client=client),0)
        before = self.store.raw_log_size()
        with patch('axiom_data.builder.freeze_builder', return_value=new_builder):
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(cli.main(['--data-root',str(self.store.root),'continue-financial','--plan',str(path),
                    '--operation-id','review-next','--source-operation-id','continuation',
                    '--source-checkpoint-sha256',source_sha,'--dry-run'],client=client),0)
        self.assertEqual(json.loads(output.getvalue())['reference_operation_id'], 'original')
        self.assertEqual(self.store.raw_log_size(), before)
        self.assertEqual(len(client.calls),5)
