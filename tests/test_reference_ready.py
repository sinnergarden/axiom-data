"""Pure reference readiness checks; no supplier calls or real data writes."""

import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from axiom_data.protocols import ConflictError, CoverageError, DataError
from axiom_data.storage import LocalStore
from axiom_data.universe_sources import (
    LISTING_STATUSES, EXCHANGES, bounded_weight_observations,
    listing_catalogue_evidence, listing_identity_candidates,
    plan_full_listing_requests, plan_reference_bootstrap,
    plan_weight_months, prepare_universe_scope, resolve_index_preset,
    run_reference_bootstrap, save_prepared_scope, validate_identity_bindings,
)


class ReferenceReadyTest(unittest.TestCase):
    def test_complete_terminal_listing_catalogue_requires_every_status_and_binding(self):
        requests = plan_full_listing_requests()
        self.assertEqual({(r["params"]["exchange"], r["params"]["list_status"])
                          for r in requests}, {(e, s) for e in EXCHANGES for s in LISTING_STATUSES})
        responses = {(e, s): [] for e in EXCHANGES for s in LISTING_STATUSES}
        responses[("SZSE", "D")] = [{"ts_code": "000001.SZ", "exchange": "SZSE",
                                     "list_status": "D", "list_date": "19910403",
                                     "delist_date": "20260630"}]
        candidates = listing_identity_candidates(responses)
        self.assertEqual(candidates["000001.SZ"]["vendor_delist_date"], "2026-06-30")
        self.assertEqual(candidates["000001.SZ"]["delist_boundary"],
                         "supplier_reported_delist_date")
        binding = validate_identity_bindings(candidates, {"000001.SZ": "stable-bank-1"},
                                             version="reviewed-2026-09")
        self.assertEqual(binding["source_codes"], 1)
        self.assertEqual(binding["historical_status"], "unverified")
        with self.assertRaises(DataError):
            listing_identity_candidates({k: v for k, v in responses.items() if k != ("SSE", "P")})
        with self.assertRaises(DataError):
            validate_identity_bindings(candidates, {}, version="reviewed-2026-09")

    def test_listing_conflict_and_cap_are_not_a_complete_catalogue(self):
        responses = {(e, s): [] for e in EXCHANGES for s in LISTING_STATUSES}
        row = {"ts_code": "000001.SZ", "exchange": "SZSE", "list_status": "L",
               "list_date": "19910403", "delist_date": None}
        responses[("SZSE", "L")] = [row]
        responses[("SZSE", "P")] = [dict(row, list_status="P")]
        with self.assertRaisesRegex(DataError, "duplicated"):
            listing_identity_candidates(responses)
        responses[("SZSE", "P")] = []
        responses[("SZSE", "L")] = [row] * 6000
        with self.assertRaisesRegex(DataError, "cap"):
            listing_identity_candidates(responses)

    def test_real_vendor_t_alias_stays_separate_from_later_ordinary_code(self):
        fixture = json.loads((Path(__file__).parent / "fixtures/reference_listing_alias.json").read_text())
        responses = {(e, s): [] for e in EXCHANGES for s in LISTING_STATUSES}
        responses[("SSE", "L")] = [fixture["ordinary"]]
        responses[("SSE", "D")] = [fixture["historical_vendor_alias"]]
        evidence = listing_catalogue_evidence(responses)
        self.assertEqual(list(evidence["catalogue"]), ["600018.SH"])
        self.assertEqual(evidence["catalogue"]["600018.SH"]["listing_date"], "2006-10-26")
        self.assertEqual(evidence["unresolved_catalogue_rows"][0]["source_code"], "T600018.SH")
        self.assertEqual(evidence["unresolved_catalogue_rows"][0]["listing_date"], "2000-07-19")
        self.assertEqual(evidence["a_share_classification"],
                         "supplier_stock_basic_code_exchange_observation")
        self.assertNotIn("T600018.SH", evidence["catalogue"])

    def test_monthly_groups_only_cover_source_dates_and_union_keeps_exits(self):
        plan = plan_weight_months("000300.SH", "2020-01-15", "2020-03-31")
        self.assertEqual(len(plan), 3)
        responses = {(p["params"]["index_code"], p["params"]["start_date"],
                      p["params"]["end_date"]): [] for p in plan}
        first, last = list(responses)[0], list(responses)[-1]
        responses[first] = [{"index_code": "000300.SH", "con_code": "000001.SZ",
                             "trade_date": "20200120", "weight": 1.5}]
        responses[last] = [{"index_code": "000300.SH", "con_code": "600000.SH",
                            "trade_date": "20200320", "weight": 2.0}]
        result = bounded_weight_observations(plan, responses)
        self.assertEqual(result["candidate_union"], ["000001.SZ", "600000.SH"])
        self.assertEqual(set(result["groups_by_source_date"]), {"2020-01-20", "2020-03-20"})
        self.assertEqual(result["groups_by_source_date"]["2020-01-20"][0]["effective_to"],
                         "2020-01-21")
        self.assertEqual(len(result["empty_months_unknown"]), 1)
        self.assertFalse(result["continuous_membership"])
        with self.assertRaises(DataError):
            bounded_weight_observations(plan, {first: responses[first]})

    def test_aliased_weight_candidate_requires_identity_resolution(self):
        plan = plan_weight_months("000300.SH", "2020-01-01", "2020-01-31")
        params = plan[0]["params"]
        responses = {(params["index_code"], params["start_date"], params["end_date"]): [
            {"index_code": "000300.SH", "con_code": "T600018.SH",
             "trade_date": "20200120", "weight": 0.1},
            {"index_code": "000300.SH", "con_code": "600000.SH",
             "trade_date": "20200120", "weight": 0.2},
        ]}
        result = bounded_weight_observations(plan, responses)
        self.assertEqual(result["candidate_union"], ["600000.SH"])
        self.assertEqual(result["unresolved_weight_rows"][0]["source_code"], "T600018.SH")
        self.assertTrue(result["candidate_union_requires_resolution"])

    def test_raw_first_bootstrap_retries_only_failed_slot_and_never_promotes(self):
        class Client:
            def __init__(self):
                self.calls = []
                self.fail_february = True

            def query(self, endpoint, *, fields, **params):
                self.calls.append((endpoint, dict(params), fields))
                if endpoint == "stock_basic":
                    if (params["exchange"], params["list_status"]) == ("SSE", "D"):
                        return [{"ts_code": "600000.SH", "exchange": "SSE", "list_status": "D",
                                 "list_date": "19991110", "delist_date": "20200131",
                                 "name": "preserved only in Raw"}]
                    if (params["exchange"], params["list_status"]) == ("SZSE", "L"):
                        return [{"ts_code": "000001.SZ", "exchange": "SZSE", "list_status": "L",
                                 "list_date": "19910403", "delist_date": None}]
                    return []
                if params["start_date"] == "20200201" and self.fail_february:
                    self.fail_february = False
                    raise RuntimeError("token=must-not-persist")
                source_date = "20200120" if params["start_date"] == "20200101" else "20200220"
                code = "600000.SH" if source_date == "20200120" else "000001.SZ"
                return [{"index_code": params["index_code"], "con_code": code,
                         "trade_date": source_date, "weight": 1.5}]

        plan = plan_reference_bootstrap(index_codes=["000300.SH"],
                                        start_session="2020-01-01", end_session="2020-02-29")
        persisted_plan = json.loads(json.dumps(plan))
        self.assertEqual(persisted_plan, plan)
        stamp = datetime(2026, 9, 28, 12, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory(dir="/private/tmp") as root:
            store, client = LocalStore(root), Client()
            options = dict(store=store, plan=persisted_plan, client=client,
                           operation_id="ref-seed", max_attempts=3,
                           min_interval_seconds=0, clock=lambda: stamp)
            def interrupted_backoff(_seconds):
                if len(client.calls) == 8:
                    raise InterruptedError("test interruption after durable failed Raw")
            with self.assertRaises(InterruptedError):
                run_reference_bootstrap(**options, sleeper=interrupted_backoff)
            first_calls = len(client.calls)
            raws = store.find_raw_by_operation("ref-seed")
            self.assertEqual(len(raws), 8)
            self.assertEqual(raws[21]["status"], "failed")
            self.assertNotIn(b"must-not-persist", store.read_raw_record(raws[21]))
            self.assertFalse((store.root / "current.json").exists())
            result = run_reference_bootstrap(**options, sleeper=lambda _seconds: None)
            self.assertEqual(len(client.calls), first_calls + 1)
            self.assertEqual(len(result["raw_batch_ids"]), 8)
            self.assertEqual(result["candidate_union"], ["000001.SZ", "600000.SH"])
            self.assertEqual(len(result["bounded_observations"]["groups_by_source_date"]), 2)
            self.assertEqual(result["identity_binding_template"]["binding_version"], None)
            self.assertFalse(result["continuous_membership_ready"])
            self.assertFalse((store.root / "current.json").exists())
            self.assertEqual(result, run_reference_bootstrap(**options, sleeper=lambda _seconds: None))
            self.assertEqual(len(client.calls), first_calls + 1)
            listed = store.get_raw(result["raw_batch_ids"][1])
            self.assertIn(b"preserved only in Raw", store.read_raw_record(listed))
            changed = plan_reference_bootstrap(index_codes=["000300.SH"],
                                               start_session="2020-01-01", end_session="2020-03-31")
            with self.assertRaises(ConflictError):
                run_reference_bootstrap(**{**options, "plan": changed})

    def test_transient_failure_retries_automatically_in_one_run(self):
        class Client:
            def __init__(self):
                self.calls = 0

            def query(self, endpoint, *, fields, **params):
                self.calls += 1
                if endpoint == "index_weight" and self.calls == 7:
                    raise RuntimeError("temporary failure")
                if endpoint == "index_weight":
                    return [{"index_code": params["index_code"], "con_code": "000001.SZ",
                             "trade_date": "20200120", "weight": 1.0}]
                return []

        plan = plan_reference_bootstrap(index_codes=["000300.SH"],
                                        start_session="2020-01-01", end_session="2020-01-31")
        with tempfile.TemporaryDirectory(dir="/private/tmp") as root:
            store, client = LocalStore(root), Client()
            result = run_reference_bootstrap(store, plan=plan, client=client,
                                             operation_id="ref-auto", min_interval_seconds=0,
                                             sleeper=lambda _seconds: None)
            self.assertEqual(client.calls, 8)
            self.assertEqual(len(store.find_raw_by_operation("ref-auto")), 8)
            self.assertEqual(len(result["raw_batch_ids"]), 7)
            self.assertEqual(result["identity_binding_template"]["missing_from_listing_catalogue"],
                             ["000001.SZ"])

    def test_reference_pacing_keeps_stock_limit_and_respects_global_override(self):
        class VirtualTime:
            def __init__(self):
                self.value = 0.0

            def ticks(self):
                return self.value

            def sleep(self, seconds):
                self.value += seconds

        class Client:
            def __init__(self, timebase):
                self.timebase = timebase
                self.calls = []

            def query(self, endpoint, *, fields, **params):
                self.calls.append((endpoint, self.timebase.ticks()))
                if endpoint == "index_weight":
                    return [{"index_code": params["index_code"], "con_code": "000001.SZ",
                             "trade_date": "20200120", "weight": 1.0}]
                return []

        plan = plan_reference_bootstrap(index_codes=["000300.SH"],
                                        start_session="2020-01-01", end_session="2020-01-31")
        for configured_gap, expected_weight_time in ((0.2, 6.2), (1.5, 9.0)):
            with self.subTest(configured_gap=configured_gap):
                with tempfile.TemporaryDirectory(dir="/private/tmp") as root:
                    virtual = VirtualTime()
                    client = Client(virtual)
                    run_reference_bootstrap(LocalStore(root), plan=plan, client=client,
                                            operation_id="paced-reference", max_attempts=1,
                                            min_interval_seconds=configured_gap,
                                            monotonic=virtual.ticks, sleeper=virtual.sleep)
                    stock_times = [at for endpoint, at in client.calls if endpoint == "stock_basic"]
                    self.assertEqual(len(stock_times), 6)
                    self.assertTrue(all(round(second - first, 10) >= 1.2
                                        for first, second in zip(stock_times, stock_times[1:])))
                    self.assertAlmostEqual(client.calls[-1][1], expected_weight_time)

    def test_calendar_selector_checks_and_prepared_scope_from_saved_raw(self):
        codes = resolve_index_preset("csi1800")
        self.assertEqual(codes, ("000300.SH", "000905.SH", "000852.SH"))
        names = {"000300.SH": "沪深300", "000905.SH": "中证500",
                 "000852.SH": "中证1000"}
        members = {"000300.SH": "600001.SH", "000905.SH": "600002.SH",
                   "000852.SH": "600003.SH"}

        class Client:
            def __init__(self):
                self.calls = []

            def query(self, endpoint, *, fields, **params):
                self.calls.append((endpoint, dict(params)))
                if endpoint == "stock_basic":
                    if (params["exchange"], params["list_status"]) != ("SSE", "L"):
                        return []
                    return [{"ts_code": code, "exchange": "SSE", "list_status": "L",
                             "list_date": "20000101", "delist_date": None}
                            for code in members.values()]
                if endpoint == "index_basic":
                    code = params["ts_code"]
                    return [{"ts_code": code, "name": names[code], "market": "CSI",
                             "publisher": "CSI"}]
                if endpoint == "index_weight":
                    if params["start_date"] != "20260101":
                        return []
                    return [{"index_code": params["index_code"],
                             "con_code": members[params["index_code"]],
                             "trade_date": "20260120", "weight": 1.0}]
                start = datetime.strptime(params["start_date"], "%Y%m%d").date()
                end = datetime.strptime(params["end_date"], "%Y%m%d").date()
                return [{"exchange": params["exchange"],
                         "cal_date": (start + timedelta(days=i)).strftime("%Y%m%d"),
                         "is_open": 1} for i in range((end - start).days + 1)]

        plan = plan_reference_bootstrap(index_codes=codes, start_session="2026-01-01",
                                        end_session="2026-01-31", include_calendar=True)
        with tempfile.TemporaryDirectory(dir="/private/tmp") as root:
            store, client = LocalStore(root), Client()
            seed = run_reference_bootstrap(store, plan=json.loads(json.dumps(plan)), client=client,
                                           operation_id="scope-seed", min_interval_seconds=0,
                                           sleeper=lambda _seconds: None)
            self.assertEqual(len(client.calls), 14)
            self.assertEqual(len(seed["calendar"]["open_sessions_by_exchange"]["SSE"]), 31)
            self.assertEqual(set(seed["index_selector_evidence"]), set(codes))
            prior = {"identity_map": {"600001.SH": "existing-stable-1"},
                     "listing_dates": {"600001.SH": "2000-01-01"}}
            prepared = prepare_universe_scope(seed, anchor_session="2026-01-31",
                                              scope_mode="anchor_members",
                                              previous_bindings=prior)
            self.assertEqual(prepared["symbols"], sorted(members.values()))
            self.assertEqual(prepared["identity_map"]["600001.SH"], "existing-stable-1")
            self.assertEqual(prepared["identity_map"]["600002.SH"],
                             "cnstock.600002.SH.20000101")
            self.assertEqual(prepared["anchor_observation_dates"]["000852.SH"], "2026-01-20")
            self.assertTrue(prepared["ready_for_bulk"])  # supplier group count is diagnostic
            historical = prepare_universe_scope(seed, anchor_session="2026-01-31",
                                                scope_mode="historical_union",
                                                previous_bindings=prepared)
            self.assertEqual(historical["symbols"], prepared["symbols"])
            path = save_prepared_scope(store, historical, preparation_id="scope-202601")
            self.assertEqual(json.loads(Path(path).read_text())["symbols"], historical["symbols"])
            self.assertEqual(path, save_prepared_scope(store, historical,
                                                        preparation_id="scope-202601"))
            with self.assertRaises(ConflictError):
                save_prepared_scope(store, {**historical, "symbols": []},
                                    preparation_id="scope-202601")
            self.assertFalse((store.root / "current.json").exists())
            # A different range/operation reuses exact saved listing Raw and
            # fetches only selectors, new month weights and calendar.
            extended_plan = plan_reference_bootstrap(index_codes=codes,
                                                     start_session="2025-12-01",
                                                     end_session="2026-01-31",
                                                     include_calendar=True)
            next_client = Client()
            extended = run_reference_bootstrap(store, plan=extended_plan, client=next_client,
                                               operation_id="scope-seed-extended",
                                               reuse_raw_batch_ids=seed["raw_batch_ids"][:6],
                                               min_interval_seconds=0,
                                               sleeper=lambda _seconds: None)
            self.assertEqual(len(next_client.calls), 11)
            self.assertFalse(any(endpoint == "stock_basic" for endpoint, _ in next_client.calls))
            self.assertEqual(extended["candidate_union"], sorted(members.values()))

    def test_nominal_anchor_1800_and_larger_historical_union_are_distinct(self):
        codes = resolve_index_preset("csi1800")
        names = {"000300.SH": "沪深300", "000905.SH": "中证500",
                 "000852.SH": "中证1000"}
        symbols = [f"{600000 + i:06d}.SH" for i in range(1800)]
        groups = {code: symbols[sum((300, 500, 1000)[:i]):sum((300, 500, 1000)[:i + 1])]
                  for i, code in enumerate(codes)}
        exited = "603000.SH"
        catalogue = {code: {"listing_date": "2000-01-01"}
                     for code in [*symbols, exited]}
        seed = {"status": "complete_observations", "index_codes": list(codes),
                "start_session": "2025-09-01", "end_session": "2026-08-31",
                "calendar": {"start_session": "2025-09-01", "end_session": "2026-08-31",
                             "open_sessions_by_exchange": {"SSE": [], "SZSE": []}},
                "index_selector_evidence": {code: {"name": names[code]} for code in codes},
                "catalogue": catalogue, "unresolved_catalogue_rows": [],
                "bounded_observations": {"groups_by_source_date": {
                    "2026-08-20": [{"index_code": code, "con_code": symbol}
                                   for code in codes for symbol in groups[code]],
                    "2025-09-20": [{"index_code": codes[0], "con_code": exited}]},
                    "candidate_union": [*symbols, exited], "unresolved_weight_rows": []},
                "plan_digest": "synthetic", "operation_id": "synthetic",
                "raw_batch_ids": [], "a_share_classification": "unverified"}
        anchor = prepare_universe_scope(seed, anchor_session="2026-08-31",
                                        scope_mode="anchor_members")
        historical = prepare_universe_scope(seed, anchor_session="2026-08-31",
                                            scope_mode="historical_union")
        self.assertEqual(anchor["symbol_count"], 1800)
        self.assertTrue(anchor["anchor_nominal_1800"])
        self.assertTrue(anchor["ready_for_bulk"])
        self.assertEqual(historical["symbol_count"], 1801)
        self.assertIn(exited, historical["symbols"])
        self.assertTrue(historical["ready_for_bulk"])

    def test_unverified_index_selector_stops_before_monthly_weights(self):
        class Client:
            def __init__(self):
                self.calls = []

            def query(self, endpoint, *, fields, **params):
                self.calls.append(endpoint)
                return []

        plan = plan_reference_bootstrap(index_codes=resolve_index_preset("csi1800"),
                                        start_session="2026-01-01", end_session="2026-01-31",
                                        include_calendar=True)
        with tempfile.TemporaryDirectory(dir="/private/tmp") as root:
            store, client = LocalStore(root), Client()
            with self.assertRaisesRegex(DataError, "index_basic did not verify"):
                run_reference_bootstrap(store, plan=plan, client=client,
                                        operation_id="selector-reject", min_interval_seconds=0)
            self.assertEqual(client.calls, ["stock_basic"] * 6 + ["index_basic"])
            self.assertEqual(len(store.find_raw_by_operation("selector-reject")), 7)
            self.assertFalse((store.root / "current.json").exists())


if __name__ == "__main__":
    unittest.main()
