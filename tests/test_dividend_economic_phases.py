"""Synthetic public v3 identities, phase projection and receipt boundaries."""
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from axiom_data import Data, EventQuery, open_native_view
from axiom_data.event_sources import DIVIDEND_ECONOMIC_CONTRACT, collect_event_response, event_source_profile
from axiom_data.protocols import DataError, QueryError
from axiom_data.storage import LocalStore
from axiom_data.updates import apply_saved_raw, apply_update


IDS = {"000001.SZ": "sec-synthetic"}
CALENDAR = {"2019-05-01": "2019-05-02", "2019-05-03": "2019-05-06",
            "2019-05-04": "2019-05-06", "2019-05-05": "2019-05-06", "2019-05-07": "2019-05-08"}
OBS = datetime(2026, 10, 9, 2, 27, tzinfo=timezone.utc)
LATER = datetime(2026, 10, 10, 3, tzinfo=timezone.utc)
FIELDS = ("economic_event_id", "stock_distribution_shares_per_share", "payment_date",
          "stock_listing_date", "cash_dividend_before_tax_per_share", "source_issue")


def action(**changes):
    return {"ts_code": "000001.SZ", "end_date": "20181231", "ann_date": "20190501", "div_proc": "实施",
            "imp_ann_date": "20190505", "cash_div_tax": 0.5, "stk_bo_rate": 0.1, "stk_co_rate": 0.2,
            "record_date": "20190601", "ex_date": "20190603", "stk_div": 0.3,
            "pay_date": "20190605", "div_listdate": "20190610", **changes}


def rules(rows, rounds=None, native=None):
    return {"rule": "declared_distribution_round_v1", "alias_rule": "identical_complete_terms_v1",
            "native_key_field": native,
            "rounds": [{**{f: row[f] for f in ("ts_code", "end_date", "ann_date", "div_proc")},
                        "round": (rounds or {}).get(row["ann_date"], "annual-1")}
                       for row in rows if row["div_proc"] == "实施"]}


class Client:
    def __init__(self, rows):
        self.rows = rows
        self.fields = None

    def query(self, endpoint, *, fields, **params):
        self.fields = fields
        return deepcopy(self.rows)


def publish(store, rows, *, policy=None, op="v3", observed=OBS, base=None):
    client = Client(rows)
    raw = collect_event_response(store, client=client, endpoint="dividend", params={"ts_code": "000001.SZ"},
                                identity_map=IDS, operation_id=op + ".fetch", observed_at=observed,
                                next_open_session_by_date=CALENDAR, corporate_action_rules=policy)
    snapshot = apply_saved_raw(store, base_snapshot=base, raw_batch_ids=[raw["batch_id"]],
                               operation_id=op, build_context={"synthetic": True}, promote=False).snapshot_id
    return raw, snapshot, client


def query(root, snapshot, *, time_field="payment_date", day="2019-06-05", cutoff=LATER.isoformat(),
          policy="operational_pit_v1", fields=FIELDS, filters=None, data=None):
    return (data or Data(root)).events(snapshot=snapshot, query=EventQuery(
        "corporate_actions", fields, ("sec-synthetic",), day, day, cutoff, policy, time_field,
        filters=filters or {"process_status": "实施"}))


class EconomicPhaseTests(unittest.TestCase):
    def test_each_phase_has_one_event_with_all_visible_alias_provenance(self):
        rows = [action(), action(ann_date="20190503")]
        declared = rules(rows)
        with tempfile.TemporaryDirectory() as root:
            raw, snapshot, client = publish(LocalStore(root), rows, policy=declared)
            self.assertEqual(set(client.fields.split(",")), set(rows[0]))
            declared["rounds"][0]["round"] = "caller-pollution"
            for field, phase, day in (("record_date", "record", "2019-06-01"), ("ex_date", "ex", "2019-06-03"),
                                      ("payment_date", "pay", "2019-06-05"), ("stock_listing_date", "listing", "2019-06-10")):
                batch = query(root, snapshot, time_field=field, day=day)
                self.assertEqual(len(batch.frame), 1)
                self.assertEqual(batch.context["event_phase"], phase)
                self.assertTrue(batch.frame.iloc[0]["economic_event_id"].startswith("ca:round:"))
                aliases = batch.field_meta["economic_event_id"]["by_key"][0]["economic_aliases"]
                self.assertEqual(len(aliases), 2)
                self.assertEqual({a["raw_batch_id"] for a in aliases}, {raw["batch_id"]})
                self.assertNotIn("rounds", batch.context["economic_identity_rule"])
            native = query(root, snapshot, time_field="report_period", day="2018-12-31")
            self.assertEqual(len(native.frame), 2)
            self.assertEqual(native.frame["economic_event_id"].nunique(), 1)

    def test_rounds_are_explicit_and_same_report_does_not_automatically_merge(self):
        rows = [action(), action(ann_date="20190503")]
        with tempfile.TemporaryDirectory() as root:
            _, snapshot, _ = publish(LocalStore(root), rows, policy=rules(rows, {"20190501": "round-1", "20190503": "round-2"}))
            batch = query(root, snapshot)
            self.assertEqual(len(batch.frame), 2)
            self.assertEqual(batch.frame["economic_event_id"].nunique(), 2)
        with tempfile.TemporaryDirectory() as root:
            undeclared = rules(rows)
            undeclared["rounds"] = []
            _, snapshot, _ = publish(LocalStore(root), rows, policy=undeclared)
            batch = query(root, snapshot)
            self.assertEqual(len(batch.frame), 2)
            self.assertTrue(batch.frame["economic_event_id"].isna().all())

    def test_native_opaque_key_is_preferred_to_a_round_binding(self):
        rows = [action(vendor_action_id="opaque-42"), action(ann_date="20190503", vendor_action_id="opaque-42")]
        with tempfile.TemporaryDirectory() as root:
            _, snapshot, _ = publish(LocalStore(root), rows,
                                      policy=rules(rows, {"20190501": "round-1", "20190503": "round-2"}, native="vendor_action_id"))
            batch = query(root, snapshot)
            self.assertEqual(len(batch.frame), 1)
            self.assertTrue(batch.frame.iloc[0]["economic_event_id"].startswith("ca:native:"))

    def test_zero_null_and_notice_conflicts_are_not_aliases(self):
        cases = ([action(cash_div_tax=0), action(ann_date="20190503", cash_div_tax=None)],
                 [action(), action(ann_date="20190503", imp_ann_date="20190504")],
                 [action(), action(ann_date="20190503", pay_date="20190606")])
        for rows in cases:
            with self.subTest(rows=rows), tempfile.TemporaryDirectory() as root:
                store = LocalStore(root)
                raw, snapshot, _ = publish(store, rows, policy=rules(rows))
                batch = query(root, snapshot, time_field="ex_date", day="2019-06-03",
                              filters={"process_status": "实施", "stock_distribution_shares_per_share": 0})
                self.assertEqual(len(batch.frame), 2)
                self.assertTrue(batch.frame["economic_event_id"].isna().all())
                self.assertEqual({m["status"] for m in batch.field_meta["economic_event_id"]["by_key"]}, {"source_missing"})
                self.assertEqual(len(batch.context["unavailable_event_scope"]), 2)
                import json
                self.assertEqual(json.loads(store.read_raw_record(raw)), rows)

    def test_native_identity_conflict_keeps_identity_but_does_not_select_terms(self):
        rows = [action(vendor_action_id="opaque-42", cash_div_tax=0),
                action(ann_date="20190503", vendor_action_id="opaque-42", cash_div_tax=None)]
        with tempfile.TemporaryDirectory() as root:
            _, snapshot, _ = publish(LocalStore(root), rows, policy=rules(rows, native="vendor_action_id"))
            batch = query(root, snapshot)
            self.assertEqual(len(batch.frame), 1)
            self.assertTrue(batch.frame.iloc[0]["economic_event_id"].startswith("ca:native:"))
            self.assertTrue(batch.frame["cash_dividend_before_tax_per_share"].isna().all())
            self.assertEqual(batch.field_meta["cash_dividend_before_tax_per_share"]["by_key"][0]["status"], "source_missing")

    def test_revision_selection_precedes_date_range_and_ids_ignore_mutable_values(self):
        first = action()
        second = action(cash_div_tax=0.8, stk_div=0.4, stk_bo_rate=0.2, ex_date="20190604", pay_date="20190609")
        with tempfile.TemporaryDirectory() as root:
            store = LocalStore(root)
            _, before, _ = publish(store, [first], policy=rules([first]))
            _, after, _ = publish(store, [second], policy=rules([first]), observed=LATER, op="revision", base=before)
            data = Data(root)
            late = query(root, after, day="2019-06-09", data=data)
            early = query(root, after, cutoff=OBS.isoformat(), data=data)
            self.assertEqual(late.frame.iloc[0]["economic_event_id"], early.frame.iloc[0]["economic_event_id"])
            self.assertEqual(float(late.frame.iloc[0]["stock_distribution_shares_per_share"]), 0.4)
            self.assertEqual(float(early.frame.iloc[0]["stock_distribution_shares_per_share"]), 0.3)
            self.assertTrue(query(root, after, data=data).frame.empty)
            self.assertEqual(early.field_meta["payment_date"]["by_key"][0]["first_observed_at"], OBS.isoformat())
            self.assertEqual(late.field_meta["payment_date"]["by_key"][0]["first_observed_at"], LATER.isoformat())
            late.field_meta["economic_event_id"]["by_key"][0]["economic_aliases"][0]["native_key"]["announcement_date"] = "pollution"
            self.assertNotIn("pollution", str(query(root, after, day="2019-06-09", data=data).to_json()))

    def test_cross_observation_alias_conflict_is_checked_before_phase_filter(self):
        first, second = action(), action(ann_date="20190503", cash_div_tax=None, pay_date="20190609")
        declared = rules([first, second])
        with tempfile.TemporaryDirectory() as root:
            store = LocalStore(root)
            _, before, _ = publish(store, [first], policy=declared)
            _, after, _ = publish(store, [second], policy=declared, op="later-alias", observed=LATER, base=before)
            late = query(root, after)
            self.assertEqual(len(late.frame), 1)
            self.assertTrue(late.frame["economic_event_id"].isna().all())
            self.assertEqual(late.context["unavailable_event_scope"][0]["source_issue"], "ambiguous_economic_alias_terms_or_dates")
            early = query(root, after, cutoff=OBS.isoformat())
            self.assertEqual(len(early.frame), 1)
            self.assertTrue(early.frame.iloc[0]["economic_event_id"].startswith("ca:round:"))

    def test_unknown_phase_is_a_marker_and_never_filled_from_ex(self):
        rows = [action(pay_date=None, div_listdate=None)]
        with tempfile.TemporaryDirectory() as root:
            _, snapshot, _ = publish(LocalStore(root), rows, policy=rules(rows))
            for field in ("payment_date", "stock_listing_date"):
                batch = query(root, snapshot, time_field=field)
                self.assertEqual(len(batch.frame), 1)
                self.assertTrue(batch.frame[field].isna().all())
                self.assertEqual(batch.field_meta[field]["by_key"][0]["status"], "not_provided")
                self.assertEqual(batch.context["unavailable_event_scope"][0]["candidate_dates"], [None])

    def test_old_v2_bytes_and_new_observation_clock_remain_distinct(self):
        row = action(stk_div=0, stk_bo_rate=None, stk_co_rate=None, div_listdate=None)
        old_row = {k: v for k, v in row.items() if k not in {"stk_div", "pay_date", "div_listdate"}}
        with tempfile.TemporaryDirectory() as root:
            store = LocalStore(root)
            raw, before, _ = publish(store, [old_row], policy=None, op="legacy")
            old_snapshot_bytes = (Path(root) / "snapshots" / f"{before}.json").read_bytes()
            old_raw_bytes = store.read_raw_record(raw)
            _, after, _ = publish(store, [row], policy=rules([row]), op="new-fields", observed=LATER)
            self.assertTrue(query(root, after, cutoff=OBS.isoformat()).frame.empty)
            with self.assertRaises(QueryError):
                query(root, before)
            vendor = query(root, after, cutoff="2019-05-06T01:30:00Z", policy="best_effort_vendor_v1")
            self.assertEqual(len(vendor.frame), 1)
            self.assertEqual(vendor.context["vendor_assumption_id"], "tushare.current_implemented_terms_from_later_notice_next_open.v1")
            self.assertEqual(vendor.field_meta["payment_date"]["by_key"][0]["first_observed_at"], LATER.isoformat())
            self.assertEqual((Path(root) / "snapshots" / f"{before}.json").read_bytes(), old_snapshot_bytes)
            self.assertEqual(store.read_raw_record(raw), old_raw_bytes)
            current = query(root, after)
            self.assertTrue(current.frame["stock_listing_date"].isna().all())
            self.assertEqual(current.field_meta["stock_listing_date"]["by_key"][0]["status"], "not_provided")
            self.assertEqual(current.field_meta["stock_distribution_shares_per_share"]["by_key"][0]["status"], "value")
            self.assertEqual(float(current.frame.iloc[0]["stock_distribution_shares_per_share"]), 0)

    def test_mutable_source_fields_cannot_be_declared_native_economic_keys(self):
        for field in ("ann_date", "ex_date", "cash_div_tax", "stk_div", "pay_date"):
            with self.subTest(field=field), self.assertRaises(DataError):
                event_source_profile("dividend", identity_map=IDS, next_open_session_by_date=CALENDAR,
                                     corporate_action_rules=rules([action()], native=field))

    def test_native_export_preserves_exact_phase_wire_and_alias_metadata(self):
        rows = [action(), action(ann_date="20190503")]
        with tempfile.TemporaryDirectory() as root:
            facts = Path(root) / "facts"
            _, snapshot, _ = publish(LocalStore(facts), rows, policy=rules(rows))
            data = Data(facts)
            q = EventQuery("corporate_actions", FIELDS, ("sec-synthetic",), "2019-06-05", "2019-06-05",
                           LATER.isoformat(), "operational_pit_v1", "payment_date", {"process_status": "实施"})
            expected = data.events(snapshot=snapshot, query=q).to_json()
            limits = {"max_part_bytes": 1024 * 1024, "max_working_bytes": 64 * 1024 * 1024,
                      "max_saved_bytes": 8 * 1024 * 1024, "max_rows_per_block": 1}
            destination = Path(root) / "native-phase"
            exported = data.export_native_view(snapshot=snapshot, reads=[{"method": "events", "query": q}],
                                               destination=destination, limits=limits)
            from axiom_data.native_view import _replay
            from axiom_data.storage import _json_bytes
            with open_native_view(destination, manifest_sha256=exported["content_digest"], limits=limits) as view:
                batch = view.manifest["batches"][0]
                self.assertEqual(batch["row_count"], 1)
                self.assertEqual(b"".join(_replay(view.root, batch, {}, view._marks)), _json_bytes(expected))

    def test_explicit_domain_upgrade_copies_unselected_domain_and_old_bytes(self):
        rows = [action()]
        with tempfile.TemporaryDirectory() as root:
            store = LocalStore(root)
            _, before, _ = publish(store, rows, op="old-domain")
            from test_local_updates import batch, row
            from axiom_data.protocols import UpdateRequest
            with_other = apply_update(store, base_snapshot=before, request=UpdateRequest(
                batches=(batch([row()], domain="independent_daily"),),
                operation_id="unrelated-domain", build_context={"synthetic": True}, promote=False)).snapshot_id
            original = store.load_snapshot(with_other)
            original_bytes = (Path(root) / "snapshots" / f"{with_other}.json").read_bytes()
            new = collect_event_response(store, client=Client(rows), endpoint="dividend", params={"ts_code": "000001.SZ"},
                                         identity_map=IDS, operation_id="supplement-only.fetch", observed_at=LATER,
                                         next_open_session_by_date=CALENDAR, corporate_action_rules=rules(rows))
            receipt = Data(root).rebuild(base_snapshot=with_other, raw_batch_ids=[new["batch_id"]],
                                        domains=("corporate_actions",), operation_id="only-actions", build_context={"synthetic": True},
                                        promote=False, domain_overrides={"corporate_actions": {
                                            "contract": DIVIDEND_ECONOMIC_CONTRACT,
                                            "source_profile": new["source_profile"], "normalizer": "event_records_v1"}})
            upgraded = store.load_snapshot(receipt.snapshot_id)
            self.assertEqual(upgraded["domains"]["independent_daily"], original["domains"]["independent_daily"])
            self.assertEqual((Path(root) / "snapshots" / f"{with_other}.json").read_bytes(), original_bytes)
            self.assertEqual(upgraded["domains"]["corporate_actions"]["contract"]["contract_id"], "local.corporate_actions.tushare.v3")
            self.assertEqual(query(root, receipt.snapshot_id).field_meta["payment_date"]["by_key"][0]["first_observed_at"], LATER.isoformat())
