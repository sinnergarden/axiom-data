"""Exact saved Native identity scope, without changing source observations."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import json
import tempfile
import unittest

from axiom_data import Data, EventQuery, open_native_view
from axiom_data.event_sources import DIVIDEND_ECONOMIC_CONTRACT, event_source_profile, prepare_event_rows
from axiom_data.protocols import DataError, IngestBatch, UpdateRequest
from test_dividend_economic_phases import action, rules, IDS, CALENDAR, OBS, LATER, FIELDS, query


KEY = {"security_id": "sec-synthetic", "report_period": "2018-12-31",
       "announcement_date": "2019-05-01", "process_status": "实施"}


def source(rows, *, scope=None, v3=True, identities=None, params=None):
    identities = identities or IDS
    unique = {tuple(row[f] for f in ("ts_code", "end_date", "ann_date", "div_proc")): row
              for row in rows if row["ts_code"] in identities}
    profile = event_source_profile("dividend", identity_map=identities,
                                   next_open_session_by_date=CALENDAR,
                                   corporate_action_rules=rules(list(unique.values())) if v3 else None)
    request = {"endpoint": "dividend", "params": params or {"ts_code": "000001.SZ"},
               "canonical_symbols": list(identities), "canonical_event_keys": deepcopy(scope if scope is not None else [KEY])}
    return SimpleNamespace(payload=json.dumps(rows, ensure_ascii=False).encode(), request=request,
                           source_profile=profile)


def ingest(data, batch, *, op="scoped", base=None, observed=OBS):
    return data.update(base_snapshot=base, request=UpdateRequest(
        batches=(IngestBatch("corporate_actions", batch.payload, batch.request, DIVIDEND_ECONOMIC_CONTRACT,
                             batch.source_profile, observed, normalizer="event_records_v1"),),
        operation_id=op, build_context={"synthetic": True}, promote=False))


class NativeActionScopeTests(unittest.TestCase):
    def test_two_original_single_security_requests_without_selection_rebuild_full_union(self):
        identities = {**IDS, "000002.SZ": "sec-second-synthetic"}
        rows = [action(), action(ts_code="000002.SZ", stk_div=0, stk_bo_rate=None,
                                 stk_co_rate=None, div_listdate=None)]
        profile = event_source_profile("dividend", identity_map=identities,
                                       next_open_session_by_date=CALENDAR, corporate_action_rules=rules(rows))
        scopes = [{**KEY, "security_id": identities[row["ts_code"]]} for row in rows]
        requests = [{"endpoint": "dividend", "params": {"ts_code": row["ts_code"]},
                     "fields": list(row)} for row in rows]
        with tempfile.TemporaryDirectory() as root:
            facts = Path(root) / "facts"
            data = Data(facts)
            payloads = [json.dumps([row], ensure_ascii=False).encode() for row in rows]
            seed = data.update(base_snapshot=None, request=UpdateRequest(
                batches=tuple(IngestBatch("corporate_actions", payload, request, DIVIDEND_ECONOMIC_CONTRACT,
                                          profile, OBS, normalizer="event_records_v1")
                              for payload, request in zip(payloads, requests)),
                operation_id="original-single-security-requests", build_context={"synthetic": True}, promote=False))
            ids = data.store.load_snapshot(seed.snapshot_id)["domains"]["corporate_actions"]["raw_batch_ids"]
            saved = [deepcopy(data.store.get_raw(identity)) for identity in ids]
            self.assertEqual([raw["request"] for raw in saved], requests)
            self.assertTrue(all("canonical_symbols" not in raw["request"] for raw in saved))
            log = (facts / "raw/fetches.jsonl").read_bytes()
            old_snapshot = (facts / "snapshots" / f"{seed.snapshot_id}.json").read_bytes()
            full = data.rebuild(base_snapshot=seed.snapshot_id, raw_batch_ids=ids, domains=["corporate_actions"],
                                operation_id="full-symbol-and-native-scope", build_context={"synthetic": True},
                                promote=False, domain_overrides={"corporate_actions": {
                                    "canonical_symbols": list(identities), "canonical_event_keys": scopes}})
            coverage = data.store.load_snapshot(full.snapshot_id)["domains"]["corporate_actions"]["coverage"]
            for observation in coverage["observed_requests"]:
                request = observation["request"]
                self.assertEqual(request["canonical_event_keys"],
                                 [{**KEY, "security_id": identities[request["params"]["ts_code"]]}])
            replay = data.rebuild(base_snapshot=full.snapshot_id, raw_batch_ids=ids, domains=["corporate_actions"],
                                  operation_id="inherited-full-scope", build_context={"synthetic": True}, promote=False)
            context = data.store.load_snapshot(replay.snapshot_id)["domains"]["corporate_actions"]["build_context"]
            self.assertEqual(context["canonical_selection"]["corporate_actions"], list(identities))
            self.assertEqual(context["canonical_event_selection"]["corporate_actions"], scopes)
            self.assertEqual([data.store.get_raw(identity) for identity in ids], saved)
            self.assertEqual([data.store.read_raw(identity) for identity in ids], payloads)
            self.assertEqual((facts / "raw/fetches.jsonl").read_bytes(), log)
            self.assertEqual((facts / "snapshots" / f"{seed.snapshot_id}.json").read_bytes(), old_snapshot)
            reads, expected = [], []
            for field in ("record_date", "ex_date", "payment_date", "stock_listing_date"):
                q = EventQuery("corporate_actions", FIELDS, tuple(identities.values()), "2019-06-01", "2019-06-10",
                               LATER.isoformat(), "operational_pit_v1", field, {"process_status": "实施"})
                actual = data.events(snapshot=replay.snapshot_id, query=q).to_json()
                self.assertEqual(len(actual["records"]), 2)
                self.assertEqual({m["first_observed_at"] for m in actual["field_meta"]["payment_date"]["by_key"]},
                                 {OBS.isoformat()})
                reads.append({"method": "events", "query": q}); expected.append(actual)
            limits = {"max_part_bytes": 1024 * 1024, "max_working_bytes": 64 * 1024 * 1024,
                      "max_saved_bytes": 8 * 1024 * 1024, "max_rows_per_block": 1}
            destination = Path(root) / "native"
            exported = data.export_native_view(snapshot=replay.snapshot_id, reads=reads, destination=destination,
                                               limits=limits, source_symbol_block=1)
            from axiom_data.native_view import _replay
            from axiom_data.storage import _json_bytes
            with open_native_view(destination, manifest_sha256=exported["content_digest"], limits=limits) as view:
                for native, wanted in zip(view.manifest["batches"], expected):
                    self.assertEqual(b"".join(_replay(view.root, native, {}, view._marks)), _json_bytes(wanted))

    def test_missing_original_selection_exception_requires_bound_single_security_v3_scope(self):
        with tempfile.TemporaryDirectory() as root:
            data = Data(root)
            batch = source([action()]); batch.request.pop("canonical_symbols"); batch.request.pop("canonical_event_keys")
            seed = ingest(data, batch)
            raw_id = data.store.load_snapshot(seed.snapshot_id)["domains"]["corporate_actions"]["raw_batch_ids"][0]
            for ordinal, override in enumerate((
                    {"canonical_symbols": list(IDS)},
                    {"canonical_symbols": list(IDS), "canonical_event_keys": [KEY],
                     "source_profile": {**batch.source_profile, "identity_map": {"000002.SZ": "sec-other"}}},
                    {"canonical_symbols": list(IDS), "canonical_event_keys": [KEY],
                     "source_profile": {**batch.source_profile, "economic_identity": None}},
                    {"canonical_symbols": list(IDS), "canonical_event_keys": [KEY, {**KEY, "security_id": "unbound"}]})):
                with self.subTest(override=override), self.assertRaises(DataError):
                    data.rebuild(base_snapshot=seed.snapshot_id, raw_batch_ids=[raw_id], domains=["corporate_actions"],
                                 operation_id=f"reject-unbound-batch-{ordinal}", build_context={"synthetic": True},
                                 promote=False, domain_overrides={"corporate_actions": override})

    def test_public_rebuild_preserves_raw_and_replays_complete_native_scope(self):
        rows = [action(), action(ann_date="20190503")]
        batch = source(rows)
        selected = {**KEY, "announcement_date": "2019-05-03"}
        with tempfile.TemporaryDirectory() as root:
            facts = Path(root) / "facts"
            data = Data(facts)
            before = ingest(data, batch)
            snapshot_path = facts / "snapshots" / f"{before.snapshot_id}.json"
            old_snapshot = snapshot_path.read_bytes()
            raw_id = data.store.load_snapshot(before.snapshot_id)["domains"]["corporate_actions"]["raw_batch_ids"][0]
            original_raw = deepcopy(data.store.get_raw(raw_id))
            raw_log = (facts / "raw" / "fetches.jsonl").read_bytes()
            after = data.rebuild(base_snapshot=before.snapshot_id, raw_batch_ids=[raw_id],
                                 domains=["corporate_actions"], operation_id="public-native-rebuild",
                                 build_context={"synthetic": True}, promote=False,
                                 domain_overrides={"corporate_actions": {"canonical_event_keys": [selected]}})
            # New default operations must not silently drop a derived Native scope.
            replay = data.rebuild(base_snapshot=after.snapshot_id, raw_batch_ids=[raw_id],
                                  domains=["corporate_actions"], operation_id="public-native-replay",
                                  build_context={"synthetic": True}, promote=False)
            self.assertEqual(data.store.get_raw(raw_id), original_raw)
            self.assertEqual(data.store.read_raw(raw_id), batch.payload)
            self.assertEqual((facts / "raw" / "fetches.jsonl").read_bytes(), raw_log)
            self.assertEqual(snapshot_path.read_bytes(), old_snapshot)
            phase_days = {"record_date": "2019-06-01", "ex_date": "2019-06-03",
                          "payment_date": "2019-06-05", "stock_listing_date": "2019-06-10"}
            reads, expected = [], []
            for field, day in phase_days.items():
                q = EventQuery("corporate_actions", FIELDS, ("sec-synthetic",), day, day,
                               LATER.isoformat(), "operational_pit_v1", field, {"process_status": "实施"})
                actual = data.events(snapshot=replay.snapshot_id, query=q).to_json()
                self.assertEqual(len(actual["records"]), 1)
                self.assertEqual(actual["records"][0]["announcement_date"], "2019-05-03")
                self.assertEqual(actual["field_meta"]["payment_date"]["by_key"][0]["first_observed_at"], OBS.isoformat())
                reads.append({"method": "events", "query": q})
                expected.append(actual)
            limits = {"max_part_bytes": 1024 * 1024, "max_working_bytes": 64 * 1024 * 1024,
                      "max_saved_bytes": 8 * 1024 * 1024, "max_rows_per_block": 1}
            destination = Path(root) / "native"
            exported = data.export_native_view(snapshot=replay.snapshot_id, reads=reads,
                                               destination=destination, limits=limits)
            from axiom_data.native_view import _replay
            from axiom_data.storage import _json_bytes
            with open_native_view(destination, manifest_sha256=exported["content_digest"], limits=limits) as view:
                self.assertEqual(len(view.manifest["batches"]), 4)
                for native, wanted in zip(view.manifest["batches"], expected):
                    self.assertEqual(b"".join(_replay(view.root, native, {}, view._marks)), _json_bytes(wanted))
            scope = data.store.load_snapshot(replay.snapshot_id)["domains"]["corporate_actions"]["build_context"]
            self.assertEqual(scope["canonical_event_selection"]["corporate_actions"], [selected])
            self.assertEqual(query(facts, before.snapshot_id, data=data).to_json()["records"][0]["announcement_date"],
                             "2019-05-01")

    def test_public_rebuild_rejects_partial_duplicate_and_unbound_native_scope(self):
        invalid = ([], [KEY, KEY], [{k: v for k, v in KEY.items() if k != "process_status"}],
                   [{**KEY, "security_id": "unbound"}])
        with tempfile.TemporaryDirectory() as root:
            data = Data(root)
            before = ingest(data, source([action()]))
            raw_id = data.store.load_snapshot(before.snapshot_id)["domains"]["corporate_actions"]["raw_batch_ids"][0]
            for ordinal, scope in enumerate(invalid):
                with self.subTest(scope=scope), self.assertRaises(DataError):
                    data.rebuild(base_snapshot=before.snapshot_id, raw_batch_ids=[raw_id],
                                 domains=["corporate_actions"], operation_id=f"invalid-native-scope-{ordinal}",
                                 build_context={"synthetic": True}, promote=False,
                                 domain_overrides={"corporate_actions": {"canonical_event_keys": scope}})

    def test_all_native_components_select_facts_and_full_raw_bytes_survive(self):
        rows = [action(), action(end_date="20180630"), action(ann_date="20190503"), action(div_proc="预案")]
        batch = source(rows)
        original = deepcopy(batch.request)
        with tempfile.TemporaryDirectory() as root:
            data = Data(root)
            result = ingest(data, batch)
            event = query(root, result.snapshot_id).to_json()
            self.assertEqual(len(event["records"]), 1)
            self.assertEqual(event["records"][0]["report_period"], "2018-12-31")
            self.assertEqual(event["records"][0]["announcement_date"], "2019-05-01")
            snapshot = data.store.load_snapshot(result.snapshot_id)
            raw_id = snapshot["domains"]["corporate_actions"]["raw_batch_ids"][0]
            self.assertEqual(data.store.read_raw(raw_id), batch.payload)
            self.assertEqual(len(json.loads(data.store.read_raw(raw_id))), 4)
            self.assertEqual(data.store.get_raw(raw_id)["request"], original)

    def test_stable_security_identity_is_part_of_selection(self):
        rows = [action(), action(ts_code="000002.SZ")]
        identities = {**IDS, "000002.SZ": "sec-second-synthetic"}
        batch = source(rows, scope=[{**KEY, "security_id": identities["000002.SZ"]}],
                       identities=identities, params={"ann_date": "20190501"})
        prepared = prepare_event_rows(batch)
        self.assertEqual(len(prepared), 1)
        self.assertEqual(prepared[0]["ts_code"], "000002.SZ")

    def test_all_candidates_of_selected_native_identity_remain_ambiguous(self):
        rows = [action(), action(cash_div_tax=0.8, pay_date="20190606"), action(ann_date="20190503")]
        batch = source(rows)
        with tempfile.TemporaryDirectory() as root:
            data = Data(root)
            result = ingest(data, batch)
            event = data.events(snapshot=result.snapshot_id, query=EventQuery(
                "corporate_actions", FIELDS, ("sec-synthetic",), "2019-06-05", "2019-06-06",
                LATER.isoformat(), "operational_pit_v1", "payment_date", {"process_status": "实施"})).to_json()
            self.assertEqual(len(event["records"]), 1)
            self.assertIsNone(event["records"][0]["cash_dividend_before_tax_per_share"])
            self.assertEqual(event["records"][0]["source_issue"], "ambiguous_action_identity_or_revision")
            self.assertEqual(event["context"]["unavailable_event_scope"][0]["candidate_dates"],
                             ["2019-06-05", "2019-06-06"])
            snapshot = data.store.load_snapshot(result.snapshot_id)
            raw_id = snapshot["domains"]["corporate_actions"]["raw_batch_ids"][0]
            self.assertEqual(data.store.read_raw(raw_id), batch.payload)

    def test_scope_preserves_real_revision_clocks_and_old_snapshot(self):
        with tempfile.TemporaryDirectory() as root:
            data = Data(root)
            first_source = source([action(), action(ann_date="20190503")])
            first = ingest(data, first_source)
            old_path = Path(root) / "snapshots" / f"{first.snapshot_id}.json"
            old_bytes = old_path.read_bytes()
            second_source = source([action(cash_div_tax=0.8)])
            second_source.source_profile = deepcopy(first_source.source_profile)
            second = ingest(data, second_source, op="revision", base=first.snapshot_id, observed=LATER)
            late = query(root, second.snapshot_id, data=data).to_json()
            early = query(root, second.snapshot_id, cutoff=OBS.isoformat(), data=data).to_json()
            self.assertEqual(late["records"][0]["cash_dividend_before_tax_per_share"], 0.8)
            self.assertEqual(early["records"][0]["cash_dividend_before_tax_per_share"], 0.5)
            self.assertEqual(late["field_meta"]["payment_date"]["by_key"][0]["first_observed_at"], LATER.isoformat())
            self.assertEqual(early["field_meta"]["payment_date"]["by_key"][0]["first_observed_at"], OBS.isoformat())
            self.assertEqual(old_path.read_bytes(), old_bytes)

    def test_invalid_partial_duplicate_mutable_or_unbound_scope_is_rejected(self):
        invalid = ([], [KEY, KEY], [{k: v for k, v in KEY.items() if k != "process_status"}],
                   [{**KEY, "cash_dividend_before_tax_per_share": 0}],
                   [{**KEY, "security_id": "unbound"}], [{**KEY, "report_period": "20181231"}])
        for scope in invalid:
            with self.subTest(scope=scope), self.assertRaises(DataError):
                prepare_event_rows(source([action()], scope=scope))
        with self.assertRaises(DataError):
            prepare_event_rows(source([action()], v3=False))

    def test_full_response_shape_scope_and_cap_checks_precede_selection(self):
        omitted = action(ann_date="20190503")
        del omitted["pay_date"]
        with self.assertRaisesRegex(DataError, "lacks requested source fields"):
            prepare_event_rows(source([action(), omitted]))
        with self.assertRaisesRegex(DataError, "another requested security"):
            prepare_event_rows(source([action(), action(ts_code="000002.SZ")]))
        with self.assertRaisesRegex(DataError, "row safeguard"):
            prepare_event_rows(source([action()] * 2000))

    def test_only_selected_facts_require_their_actual_later_notice_calendar(self):
        batch = source([action(), action(ann_date="20190507", imp_ann_date="20190507")])
        batch.source_profile["availability"]["next_open_session_by_date"] = {"2019-05-05": "2019-05-06"}
        with tempfile.TemporaryDirectory() as root:
            data = Data(root)
            result = ingest(data, batch)
            event = query(root, result.snapshot_id, cutoff="2019-05-06T09:30:00+08:00",
                          policy="best_effort_vendor_v1").to_json()
            self.assertEqual(len(event["records"]), 1)
            self.assertEqual(event["field_meta"]["payment_date"]["by_key"][0]["usable_from"],
                             "2019-05-06T09:30:00+08:00")
        batch.source_profile["availability"]["next_open_session_by_date"] = {"2019-05-01": "2019-05-02"}
        with self.assertRaisesRegex(DataError, "calendar"):
            prepare_event_rows(batch)
