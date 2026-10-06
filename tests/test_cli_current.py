"""Current CLI wiring; source access is replaced by a small injected client."""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from axiom_data import bulk_jobs
from axiom_data import cli
from axiom_data import etf_jobs
from axiom_data import universe_sources
from axiom_data.protocols import DataError, OperationResult
from axiom_data.storage import LocalStore


class FakeJob:
    request_strategy = "trading_day_market_v2"
    mode = "bulk"

    def to_dict(self):
        return {"schema_version": "fake_test_job_v1", "requests": ["daily"]}

    def fingerprint(self):
        return "fake-job-fingerprint"

    @classmethod
    def from_dict(cls, value):
        if value != cls().to_dict():
            raise ValueError("job changed")
        return cls()


class CliCurrentTests(unittest.TestCase):
    def invoke(self, *args, client=None):
        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(list(args), client=client)
        return code, out.getvalue(), err.getvalue()

    def test_help_does_not_import_historical_path(self):
        program = (
            "import sys, axiom_data.cli; "
            "assert not any(name.startswith(('axiom_data.deprecated', "
            "'axiom_data.operations', 'axiom_data.admission_plan', "
            "'axiom_data.views', 'axiom_data.full_admission')) "
            "for name in sys.modules); "
            "axiom_data.cli.main(['--help'])"
        )
        result = subprocess.run([sys.executable, "-c", program], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("{prepare,plan,run,continue-financial,status,read,inspect,audit,rebuild,export,import,qlib-export,qlib-verify,verify}", result.stdout)
        module = subprocess.run([sys.executable, "-m", "axiom_data", "--help"],
                                capture_output=True, text=True)
        self.assertEqual(module.returncode, 0, module.stderr)
        self.assertIn("Raw, Parquet and Snapshot", module.stdout)

    def test_etf_scope_uses_independent_plan_without_stock_financial_sources(self):
        class FakeEtfPlan:
            reference_snapshot = "s_reference"

            def to_dict(self):
                return {"schema_version": "axiom_data_etf_scope_v1",
                        "reference_snapshot": self.reference_snapshot}

            @classmethod
            def from_dict(cls, value):
                if value != cls().to_dict():
                    raise ValueError("ETF scope changed")
                return cls()

            def fingerprint(self):
                return "etf-test-plan"

        with tempfile.TemporaryDirectory() as directory:
            scope, plan = Path(directory) / "scope.json", Path(directory) / "plan.json"
            scope.write_text(json.dumps(FakeEtfPlan().to_dict()))
            with mock.patch.object(etf_jobs, "EtfJobPlan", FakeEtfPlan), \
                 mock.patch.object(etf_jobs, "estimate_etf_job", return_value={"planned_requests": 36}), \
                 mock.patch.object(LocalStore, "load_snapshot", return_value={"parent_snapshot": None}):
                code, output, error = self.invoke("--data-root", directory, "plan",
                    "--scope", str(scope), "--output", str(plan), "--operation-id", "etf-test")
                self.assertEqual((code, error), (0, ""))
                self.assertEqual(json.loads(output)["estimate"]["planned_requests"], 36)
                envelope = json.loads(plan.read_text())
                self.assertEqual(envelope["schema_version"], cli._ETF_SCHEMA)
                self.assertEqual(envelope["base_snapshot"], None)
                self.assertEqual(envelope["job"], FakeEtfPlan().to_dict())
                self.assertNotIn("event_endpoints", plan.read_text())
                self.assertNotIn("token", plan.read_text().lower())
                self.assertEqual(cli._load_plan(plan)[0], envelope)

    def test_plan_is_stable_resume_uses_frozen_arguments_and_no_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "data"
            scope = Path(directory) / "scope.json"
            plan = Path(directory) / "plan.json"
            scope.write_text(json.dumps({"mode": "daily", "symbols": ["000001.SZ"],
                "start_session": "2026-06-01", "end_session": "2026-06-01",
                "identity_map": {"000001.SZ": "sec-000001-sz"}, "endpoints": ["daily"]}))
            calls = []

            def plan_job(**kwargs):
                calls.append(("plan", kwargs))
                return FakeJob()

            def run_job(store, **kwargs):
                calls.append(("run", kwargs))
                self.assertEqual(store.root, root.resolve())
                return OperationResult("s_fake", True, kwargs["operation_id"])

            with mock.patch.object(bulk_jobs, "BulkJobPlan", FakeJob), \
                 mock.patch.object(bulk_jobs, "plan_bulk_job", side_effect=plan_job), \
                 mock.patch.object(bulk_jobs, "estimate_bulk_job", return_value={"requests": 1}), \
                 mock.patch.object(bulk_jobs, "run_bulk_job", side_effect=run_job):
                code, output, error = self.invoke("--data-root", str(root), "plan", "--market-only", "--scope", str(scope),
                    "--output", str(plan), "--operation-id", "small-debug")
                self.assertEqual((code, error), (0, ""))
                self.assertEqual(json.loads(output)["estimate"], {"requests": 1})
                saved = json.loads(plan.read_text())
                self.assertEqual(saved["base_snapshot"], None)
                self.assertEqual(saved["max_attempts"], 3)
                self.assertNotIn("token", plan.read_text().lower())
                code, output, error = self.invoke("--data-root", str(root), "status", "--plan", str(plan))
                self.assertEqual((code, error), (0, ""))
                self.assertEqual(json.loads(output)["status"], "not_started")
                client = object()
                code, output, error = self.invoke("--data-root", str(root), "run", "--plan", str(plan),
                                                  client=client)
                self.assertEqual((code, error), (0, ""))
                self.assertEqual(json.loads(output)["snapshot_id"], "s_fake")
                self.assertIs(calls[-1][1]["client"], client)
                self.assertEqual(calls[-1][1]["operation_id"], "small-debug")
                self.assertEqual(calls[-1][1]["max_attempts"], 3)
                saved["operation_id"] = "tampered"
                plan.write_text(json.dumps(saved))
                code, _, error = self.invoke("--data-root", str(root), "run", "--plan", str(plan),
                                             client=client)
                self.assertEqual(code, 2)
                self.assertIn("digest mismatch", error)
                self.assertEqual(len([item for item in calls if item[0] == "run"]), 1)

    def test_scope_rejects_unreviewed_credentials_without_writing_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            scope, plan = Path(directory) / "scope.json", Path(directory) / "plan.json"
            scope.write_text(json.dumps({"token": "private-value"}))
            code, output, error = self.invoke("--data-root", directory, "plan", "--scope", str(scope),
                                               "--output", str(plan), "--operation-id", "small-debug")
            self.assertEqual((code, output), (2, ""))
            self.assertFalse(plan.exists())
            self.assertNotIn("private-value", error)

    def test_prepare_writes_reusable_1800_scope_without_manual_identity_map(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "data"
            config, scope = Path(directory) / "delivery.json", Path(directory) / "scope.json"
            config.write_text(json.dumps({"schema_version": cli._PREPARE_SCHEMA,
                "preparation_id": "csi1800-2026", "preset": "csi1800",
                "scope_mode": "anchor_members", "start_session": "2025-09-01",
                "end_session": "2026-08-31", "anchor_session": "2026-08-31",
                "required_symbol_count": 1800}))
            symbols = [f"{number:06d}.SZ" for number in range(1800)]
            prepared = {"version": "prepared_universe_scope.v1", "symbols": symbols,
                "symbol_count": 1800, "identity_map": {symbol: f"cnstock.{symbol}.20000101"
                                                    for symbol in symbols},
                "ready_for_bulk": True, "anchor_nominal_1800": True,
                "historical_union": symbols, "membership_basis": "bounded_monthly_vendor_observations"}
            with mock.patch.object(universe_sources, "plan_reference_bootstrap",
                                   return_value={"requests": [{}]}), \
                 mock.patch.object(universe_sources, "run_reference_bootstrap",
                                   return_value={"raw_batch_ids": ["b_one"]}) as collect, \
                 mock.patch.object(universe_sources, "prepare_universe_scope",
                                   return_value=prepared), \
                 mock.patch.object(bulk_jobs, "plan_bulk_job", return_value=FakeJob()):
                for _ in range(2):
                    code, output, error = self.invoke("--data-root", str(root), "prepare",
                        "--market-only", "--config", str(config), "--output-scope", str(scope), client=object())
                    self.assertEqual((code, error), (0, ""))
                    self.assertEqual(json.loads(output)["symbols"], 1800)
            generated = json.loads(scope.read_text())
            self.assertEqual(len(generated["symbols"]), 1800)
            self.assertEqual(len(generated["identity_map"]), 1800)
            self.assertEqual(generated["request_strategy"], "trading_day_market_v2")
            self.assertTrue((root / "operations/preparation/csi1800-2026.json").is_file())
            self.assertEqual(collect.call_args.kwargs["operation_id"], "csi1800-2026.reference")
            self.assertNotIn("token", scope.read_text().lower())

    def test_prepare_debug_subset_keeps_full_tushare_membership_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "data"
            config, scope = Path(directory) / "config.json", Path(directory) / "scope.json"
            config.write_text(json.dumps({"schema_version": cli._PREPARE_SCHEMA,
                "preparation_id": "vendor-members", "preset": "csi1800",
                "scope_mode": "anchor_members", "start_session": "2026-08-01",
                "end_session": "2026-08-31", "anchor_session": "2026-08-31",
                "required_symbol_count": 1800, "symbol_limit": 2}))
            symbols = [f"{number:06d}.SZ" for number in range(1, 1801)]
            prepared = {"version": "prepared_universe_scope.v1",
                "symbols": symbols, "symbol_count": 1800,
                "anchor_members": symbols, "historical_union": symbols,
                "identity_map": {symbol: f"cnstock.{symbol}.20000101" for symbol in symbols},
                "ready_for_bulk": True, "anchor_nominal_1800": True,
                "membership_basis": "bounded_monthly"}
            store = LocalStore(root)
            weight_ids = [store.write_raw(b"[]", domain="reference_bootstrap",
                request={"endpoint": "index_weight", "index": index},
                source_profile={"id": "test.index_weight"},
                observed_at="2026-09-01T00:00:00+00:00")["batch_id"]
                for index in range(3)]
            listing_ids = [store.write_raw(b"[]", domain="reference_bootstrap",
                request={"endpoint": "stock_basic", "index": index},
                source_profile={"id": "test.stock_basic"},
                observed_at="2026-09-01T00:00:00+00:00")["batch_id"]
                for index in range(6)]
            seed = {"raw_batch_ids": [*listing_ids, *weight_ids],
                    "raw_observations": [
                        *[{"endpoint": "stock_basic", "batch_id": raw_id}
                          for raw_id in listing_ids],
                        *[{"endpoint": "index_weight", "batch_id": raw_id}
                          for raw_id in weight_ids]],
                    "calendar": {"open_sessions_by_exchange": {
                        "SSE": ["2026-08-31"], "SZSE": ["2026-08-31"]}}}
            with mock.patch.object(universe_sources, "plan_reference_bootstrap",
                                   return_value={"requests": [{}]}) as reference_plan, \
                 mock.patch.object(universe_sources, "run_reference_bootstrap",
                                   return_value=seed), \
                 mock.patch.object(universe_sources, "prepare_universe_scope",
                                   return_value=prepared):
                code, output, error = self.invoke("--data-root", str(root), "prepare",
                    "--config", str(config), "--output-scope", str(scope), client=object())
            self.assertEqual((code, error), (0, ""))
            self.assertEqual(reference_plan.call_args.kwargs["start_session"], "2026-07-01")
            frozen = json.loads(scope.read_text())
            self.assertEqual(frozen["symbols"], symbols[:2])
            self.assertEqual(len(frozen["identity_map"]), 1800)
            self.assertEqual(frozen["membership_source"], {
                "schema_version": "tushare_csi1800_membership_source_v1",
                "index_weight_raw_batch_ids": weight_ids,
                "stock_basic_raw_batch_ids": listing_ids,
                "verified_through": "2026-08-31",
                "between_snapshots": "carry_forward"})
            self.assertEqual(json.loads(output)["membership_raw_batches"], 3)

    def test_longer_reference_window_reuses_only_exact_local_selectors(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LocalStore(Path(directory) / "source")
            codes = universe_sources.resolve_index_preset("csi1800")
            year = universe_sources.plan_reference_bootstrap(index_codes=codes,
                start_session="2025-09-01", end_session="2026-08-31", include_calendar=True)
            longer = universe_sources.plan_reference_bootstrap(index_codes=codes,
                start_session="2014-11-01", end_session="2026-09-28", include_calendar=True)
            listing = next(spec for spec in year["requests"] if spec["endpoint"] == "stock_basic")
            selector = next(spec for spec in year["requests"] if spec["endpoint"] == "index_basic")
            weight = next(spec for spec in year["requests"] if spec["endpoint"] == "index_weight"
                          and spec["params"]["start_date"] == "20260801")
            calendar = next(spec for spec in year["requests"] if spec["endpoint"] == "trade_cal")
            selected = {}
            for spec in (listing, selector, weight, calendar):
                raw = store.write_raw(b"[]", domain="reference_bootstrap", status="empty",
                    request={"endpoint": spec["endpoint"], "params": spec["params"],
                             "fields": spec["fields"]},
                    source_profile=universe_sources._reference_profile(spec["endpoint"]),
                    observed_at="2026-09-28T00:00:00+00:00")
                selected[spec["endpoint"]] = raw["batch_id"]
            previous = {"identity_map": {"000001.SZ": "existing-stable-id"},
                        "listing_dates": {"000001.SZ": "1991-04-03"},
                        "reference_raw_batch_ids": list(selected.values())}
            reusable = cli._reusable_reference_raw(store, previous, longer)
            self.assertEqual(set(reusable), {selected["stock_basic"], selected["index_basic"],
                                             selected["index_weight"]})
            self.assertNotIn(selected["trade_cal"], reusable)
            self.assertEqual(cli._reusable_reference_raw(LocalStore(Path(directory) / "other"),
                                                        previous, longer), ())

            previous_file = Path(directory) / "previous.json"
            previous_file.write_text(json.dumps(previous))
            config, scope = Path(directory) / "longer.json", Path(directory) / "longer.scope.json"
            config.write_text(json.dumps({"schema_version": cli._PREPARE_SCHEMA,
                "preparation_id": "longer-prep", "preset": "csi1800",
                "scope_mode": "historical_union", "start_session": "2014-11-01",
                "end_session": "2026-09-28", "anchor_session": "2026-09-28",
                "previous_preparation": str(previous_file)}))
            prepared = {"version": "prepared_universe_scope.v1", "symbols": ["000001.SZ"],
                "symbol_count": 1, "identity_map": previous["identity_map"],
                "ready_for_bulk": True, "anchor_nominal_1800": False,
                "historical_union": ["000001.SZ"], "membership_basis": "bounded_source_test"}
            with mock.patch.object(universe_sources, "run_reference_bootstrap",
                                   return_value={"raw_batch_ids": []}) as collect, \
                 mock.patch.object(universe_sources, "prepare_universe_scope",
                                   return_value=prepared) as prepare:
                code, output, error = self.invoke("--data-root", str(store.root),
                    "prepare", "--market-only", "--config", str(config), "--output-scope", str(scope),
                    client=object())
                self.assertEqual((code, error), (0, ""))
                code, output, error = self.invoke("--data-root", str(Path(directory) / "other"),
                    "prepare", "--market-only", "--config", str(config), "--output-scope", str(scope),
                    client=object())
            self.assertEqual((code, error), (0, ""))
            self.assertEqual(set(collect.call_args_list[0].kwargs["reuse_raw_batch_ids"]), set(reusable))
            self.assertEqual(collect.call_args_list[1].kwargs["reuse_raw_batch_ids"], ())
            self.assertEqual(prepare.call_args.kwargs["previous_bindings"]["identity_map"],
                             previous["identity_map"])
            self.assertEqual(json.loads(scope.read_text())["identity_map"],
                             previous["identity_map"])

    def test_run_requires_explicit_credential_source(self):
        with tempfile.TemporaryDirectory() as directory:
            plan = Path(directory) / "plan.json"
            body = {"schema_version": cli._PLAN_SCHEMA, "job": FakeJob().to_dict(),
                    "operation_id": "small-debug", "base_snapshot": None,
                    "promote": True, "max_attempts": 3, "min_interval_seconds": 1.25}
            plan.write_text(json.dumps({**body, "plan_sha256": cli._digest(body)}))
            def needs_source(_store, **kwargs):
                kwargs["client"].query("daily", fields="close", ts_code="000001.SZ")
            with mock.patch.object(bulk_jobs, "BulkJobPlan", FakeJob), \
                 mock.patch.object(bulk_jobs, "run_bulk_job", side_effect=needs_source), \
                 mock.patch.dict(os.environ, {"TUSHARE_TOKEN": "", "TS_TOKEN": ""}):
                code, output, error = self.invoke("--data-root", directory, "run", "--plan", str(plan))
            self.assertEqual((code, output), (2, ""))
            self.assertIn("requires --token-file", error)

    def test_completed_replay_needs_no_credential(self):
        with tempfile.TemporaryDirectory() as directory:
            plan = Path(directory) / "plan.json"
            body = {"schema_version": cli._PLAN_SCHEMA, "job": FakeJob().to_dict(),
                    "operation_id": "small-debug", "base_snapshot": None,
                    "promote": True, "max_attempts": 3, "min_interval_seconds": 1.25}
            plan.write_text(json.dumps({**body, "plan_sha256": cli._digest(body)}))
            with mock.patch.object(bulk_jobs, "BulkJobPlan", FakeJob), \
                 mock.patch.object(bulk_jobs, "run_bulk_job", return_value=OperationResult(
                     "s_complete", False, "small-debug")), \
                 mock.patch.dict(os.environ, {"TUSHARE_TOKEN": "", "TS_TOKEN": ""}):
                code, output, error = self.invoke("--data-root", directory, "run", "--plan", str(plan))
            self.assertEqual((code, error), (0, ""))
            self.assertEqual(json.loads(output)["snapshot_id"], "s_complete")

    def test_status_rejects_checkpoint_for_other_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            plan = Path(directory) / "plan.json"
            body = {"schema_version": cli._PLAN_SCHEMA, "job": FakeJob().to_dict(),
                    "operation_id": "small-debug", "base_snapshot": None,
                    "promote": True, "max_attempts": 3, "min_interval_seconds": 1.25}
            plan.write_text(json.dumps({**body, "plan_sha256": cli._digest(body)}))
            store = LocalStore(directory)
            store.write_operation("small-debug", {"kind": "bulk_job", "status": "running",
                "plan_fingerprint": "a-different-plan", "base_snapshot": None,
                "error": {"message": "token=private-value"}})
            with mock.patch.object(bulk_jobs, "BulkJobPlan", FakeJob):
                code, output, error = self.invoke("--data-root", directory, "status", "--plan", str(plan))
            self.assertEqual((code, output), (2, ""))
            self.assertIn("different plan", error)
            self.assertNotIn("private-value", error)

    def test_audit_writes_failed_report_and_returns_nonzero(self):
        from axiom_data import verification
        with tempfile.TemporaryDirectory() as directory:
            root, plan, report = (Path(directory) / name for name in ("data", "plan.json", "report.json"))
            body = {"schema_version": cli._PLAN_SCHEMA, "job": FakeJob().to_dict(),
                    "operation_id": "small-debug", "base_snapshot": None,
                    "promote": True, "max_attempts": 3, "min_interval_seconds": 1.25}
            plan.write_text(json.dumps({**body, "plan_sha256": cli._digest(body)}))
            root.mkdir()
            LocalStore(root).write_operation("small-debug", {"status": "success",
                "result": {"snapshot_id": "s_fake"},
                "plan_fingerprint": FakeJob().fingerprint()})
            with mock.patch.object(bulk_jobs, "BulkJobPlan", FakeJob), \
                 mock.patch.object(cli, "_load_plan", return_value=(body, FakeJob())), \
                 mock.patch.object(cli, "_root", return_value=root), \
                 mock.patch("axiom_data.api.Data.resolve", return_value="s_fake"), \
                 mock.patch.object(verification, "audit_snapshot", side_effect=DataError("bad partition")):
                code, output, error = self.invoke("--data-root", str(root), "audit",
                    "--plan", str(plan), "--output", str(report))
            self.assertEqual((code, error), (1, ""))
            self.assertEqual(json.loads(output)["status"], "failed")
            self.assertEqual(json.loads(report.read_text())["issues"][0]["message"], "bad partition")

    def test_one_day_mock_job_read_and_offline_replay(self):
        class Client:
            calls = 0

            def query(self, endpoint, **_params):
                self.calls += 1
                if endpoint == "trade_cal":
                    return [{"exchange": _params["exchange"], "cal_date": "20260601",
                             "is_open": "1"}]
                assert endpoint == "daily"
                return [{"ts_code": "000001.SZ", "trade_date": "20260601",
                         "open": 10, "high": 11, "low": 9, "close": 10.5,
                         "pre_close": 10, "vol": 12.34, "amount": 45.6}]

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "data"
            scope, plan, query = (Path(directory) / name for name in
                                  ("scope.json", "plan.json", "query.json"))
            scope.write_text(json.dumps({"mode": "daily", "symbols": ["000001.SZ"],
                "start_session": "2026-06-01", "end_session": "2026-06-01",
                "identity_map": {"000001.SZ": "sec-000001-sz"},
                "endpoints": ["trade_cal", "daily"], "max_requests_per_chunk": 8,
                "request_strategy": "trading_day_market_v2"}))
            query.write_text(json.dumps({"domain": "market_daily", "fields": ["close"],
                "symbols": ["sec-000001-sz"], "sessions": ["2026-06-01"],
                "pit_policy": "best_effort_vendor_v1",
                "cutoff_by_session": {"2026-06-01": "2026-06-01T20:30:00+08:00"}}))
            args = ("--data-root", str(root))
            code, _, error = self.invoke(*args, "plan", "--market-only", "--scope", str(scope),
                "--output", str(plan), "--operation-id", "cli-one-day",
                "--min-interval-seconds", "0")
            self.assertEqual((code, error), (0, ""))
            client = Client()
            code, output, error = self.invoke(*args, "run", "--plan", str(plan), client=client)
            self.assertEqual((code, error), (0, ""))
            snapshot = json.loads(output)["snapshot_id"]
            self.assertEqual(client.calls, 3)
            code, output, error = self.invoke(*args, "status", "--plan", str(plan))
            self.assertEqual((code, error), (0, ""))
            self.assertEqual(json.loads(output)["status"], "success")
            code, output, error = self.invoke(*args, "read", "--snapshot", snapshot,
                                              "--query", str(query))
            self.assertEqual((code, error), (0, ""))
            self.assertEqual(json.loads(output)["records"][0]["close"], 10.5)
            code, output, error = self.invoke(*args, "inspect", "--snapshot", snapshot)
            self.assertEqual((code, error), (0, ""))
            self.assertEqual(json.loads(output)["status"], "inventory_only")
            raw = LocalStore(root).find_raw_by_operation("cli-one-day.v2.c000001")
            rebuild_request = Path(directory) / "rebuild.json"
            rebuild_request.write_text(json.dumps({"base_snapshot": snapshot,
                "raw_batch_ids": [raw[0]["batch_id"]], "domains": ["market_daily"],
                "operation_id": "cli-offline-rebuild", "build_context": {"reason": "cli-test"},
                "promote": False}))
            code, output, error = self.invoke(*args, "rebuild", "--request", str(rebuild_request))
            self.assertEqual((code, error), (0, ""))
            self.assertIn("snapshot_id", json.loads(output))
            code, output, error = self.invoke(*args, "rebuild", "--snapshot", snapshot,
                "--operation-id", "cli-auto-rebuild", "--domain", "market_daily", "--no-promote")
            self.assertEqual((code, error), (0, ""))
            self.assertIn("snapshot_id", json.loads(output))
            bundle, restored = Path(directory) / "bundle", Path(directory) / "restored"
            code, output, error = self.invoke(*args, "export", "--snapshot", snapshot,
                "--destination", str(bundle), "--code-root", str(Path(__file__).resolve().parents[1]))
            self.assertEqual((code, error), (0, ""))
            code, output, error = self.invoke("verify", "--bundle", str(bundle))
            self.assertEqual((code, error), (0, ""))
            self.assertEqual(json.loads(output)["status"], "verified")
            code, output, error = self.invoke("--data-root", str(restored), "import",
                                              "--bundle", str(bundle))
            self.assertEqual((code, error), (0, ""))
            self.assertEqual(json.loads(output)["snapshot_id"], snapshot)

    def test_daily_full_plan_freezes_bounded_event_refresh(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scope, plan = root / "scope.json", root / "plan.json"
            scope.write_text(json.dumps({"mode": "daily", "symbols": ["000001.SZ"],
                "start_session": "2026-09-30", "end_session": "2026-09-30",
                "identity_map": {"000001.SZ": "sec-one"},
                "endpoints": ["trade_cal", "daily"],
                "request_strategy": "trading_day_market_v2",
                "max_requests_per_chunk": 64,
                "membership_source": {
                    "schema_version": "tushare_csi1800_membership_source_v1",
                    "index_weight_raw_batch_ids": ["b_a", "b_b", "b_c"],
                    "stock_basic_raw_batch_ids": [f"b_stock_{i}" for i in range(6)],
                    "verified_through": "2026-09-30",
                    "between_snapshots": "carry_forward"}}))
            code, output, error = self.invoke("--data-root", str(root / "data"), "plan",
                "--scope", str(scope), "--output", str(plan),
                "--operation-id", "daily-full", "--calendar-end", "2026-10-31",
                "--event-announcement-lookback-days", "7")
            self.assertEqual((code, error), (0, ""))
            saved = json.loads(plan.read_text())
            self.assertEqual(saved["job"]["schema_version"], "local_full_source_job_v4")
            from axiom_data.full_sources import FullSourcePlan
            self.assertEqual(FullSourcePlan.from_dict(saved["job"]).to_dict(), saved["job"])
            self.assertEqual(saved["job"]["announcement_start"], "2026-09-24")
            self.assertEqual(json.loads(output)["estimate"]["event_by_endpoint"]["dividend"], 7)

    def test_batch_v2_plan_and_run_use_calendar_then_daily(self):
        class Client:
            def __init__(self):
                self.calls = []

            def query(self, endpoint, **params):
                self.calls.append((endpoint, params))
                if endpoint == "trade_cal":
                    return [{"exchange": params["exchange"], "cal_date": "20260601",
                             "is_open": "1"}]
                if endpoint == "daily":
                    return [{"ts_code": "000001.SZ", "trade_date": "20260601",
                             "open": 10, "high": 11, "low": 9, "close": 10.5,
                             "pre_close": 10, "vol": 12.34, "amount": 45.6}]
                raise AssertionError(endpoint)

        with tempfile.TemporaryDirectory() as directory:
            root, scope, plan = (Path(directory) / name for name in ("data", "scope.json", "plan.json"))
            scope.write_text(json.dumps({"mode": "bulk", "symbols": ["000001.SZ"],
                "start_session": "2026-06-01", "end_session": "2026-06-01",
                "identity_map": {"000001.SZ": "sec-000001-sz"},
                "endpoints": ["trade_cal", "daily"], "request_strategy": "trading_day_market_v2",
                "max_requests_per_chunk": 8}))
            args = ("--data-root", str(root))
            code, output, error = self.invoke(*args, "plan", "--market-only", "--scope", str(scope),
                "--output", str(plan), "--operation-id", "cli-batch-one-day",
                "--global-calls-per-minute", "100000")
            self.assertEqual((code, error), (0, ""))
            saved = json.loads(plan.read_text())
            self.assertEqual(saved["schema_version"], cli._PLAN_SCHEMA_V2)
            self.assertEqual(saved["job"]["request_strategy"], "trading_day_market_v2")
            client = Client()
            code, output, error = self.invoke(*args, "run", "--plan", str(plan), client=client)
            self.assertEqual((code, error), (0, ""))
            self.assertEqual([name for name, _ in client.calls].count("trade_cal"), 2)
            self.assertEqual([name for name, _ in client.calls].count("daily"), 1)
            result = json.loads(output)
            snapshot = result["snapshot_id"]
            self.assertTrue(result["verification"]["verified"])
            self.assertEqual(result["progress"]["completed_requests"], 3)
            self.assertEqual(LocalStore(root).resolve("current"), snapshot)
            code, output, error = self.invoke(*args, "status", "--plan", str(plan))
            self.assertEqual((code, error), (0, ""))
            progress = json.loads(output)
            self.assertEqual(progress["status"], "success")
            self.assertEqual(progress["completed_requests"], 3)
            self.assertGreaterEqual(progress["raw_rows"], 3)
            self.assertIsInstance(progress["elapsed_seconds"], float)
            code, output, error = self.invoke(*args, "verify", "--plan", str(plan))
            self.assertEqual((code, error), (0, ""))
            self.assertTrue(json.loads(output)["verified"])
            report_path = Path(directory) / "audit.json"
            code, output, error = self.invoke(*args, "audit", "--plan", str(plan),
                "--snapshot", snapshot, "--output", str(report_path))
            self.assertEqual((code, error), (0, ""))
            self.assertEqual(json.loads(report_path.read_text())["status"], "limited")
            with mock.patch.dict(os.environ, {"TUSHARE_TOKEN": "", "TS_TOKEN": ""}):
                code, output, error = self.invoke(*args, "run", "--plan", str(plan))
            self.assertEqual((code, error), (0, ""))
            self.assertEqual(json.loads(output)["snapshot_id"], snapshot)
            with mock.patch.dict(os.environ, {"TUSHARE_TOKEN": "", "TS_TOKEN": ""}):
                code, output, error = self.invoke(*args, "run", "--plan", str(plan))
            self.assertEqual((code, error), (0, ""))
            self.assertEqual(json.loads(output)["snapshot_id"], snapshot)


if __name__ == "__main__":
    unittest.main()
