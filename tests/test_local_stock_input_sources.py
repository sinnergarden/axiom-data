"""Small synthetic Raw→Snapshot→consumer checks; no supplier/root access."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from axiom_data import Data, EventQuery, QuerySpec
from axiom_data.event_sources import collect_event_response, _FIELDS
from axiom_data.live_client import SourceResponseError
from axiom_data.protocols import ConflictError, CoverageError, QueryError
from axiom_data.stock_input_sources import collect_stock_input_response, stock_input_source_spec
from axiom_data.storage import LocalStore
from axiom_data.updates import apply_saved_raw

IDS = {"000001.SZ": "sec-a", "600000.SH": "sec-b"}
CAL = {"2024-04-29": "2024-04-30", "2024-05-03": "2024-05-06"}
OBS = datetime(2026, 10, 11, tzinfo=timezone.utc)


class Client:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def query(self, endpoint, **params):
        self.calls.append((endpoint, params))
        if isinstance(self.rows, Exception):
            raise self.rows
        return deepcopy(self.rows)


def complete(endpoint, **values):
    spec = stock_input_source_spec(endpoint, identity_map=IDS, next_open_session_by_date=CAL)
    return {**dict.fromkeys(spec["fields"]), **values}


def income(**values):
    return complete("income_vip", ts_code="000001.SZ", ann_date="20240429", f_ann_date="20240429",
                    end_date="20240331", report_type="1", comp_type="1", end_type="1", update_flag="0",
                    total_revenue=100, n_income_attr_p=10, revenue=80, n_income=12, oper_cost=60, **values)


class StockInputTests(unittest.TestCase):
    def collect(self, store, endpoint, rows, *, params=None, operation="fetch", **extra):
        return collect_stock_input_response(store, client=Client(rows), endpoint=endpoint,
            params=params or {"trade_date": "20240503"}, identity_map=IDS, operation_id=operation,
            canonical_symbols=["000001.SZ"], observed_at=OBS, next_open_session_by_date=CAL, **extra)

    def publish(self, store, *raw, operation="publish"):
        return apply_saved_raw(store, base_snapshot=None, raw_batch_ids=[r["batch_id"] for r in raw],
                               operation_id=operation, build_context={"synthetic": True}, promote=False).snapshot_id

    def test_market_batch_units_scope_missing_and_slot_reuse(self):
        selected = complete("daily_basic", ts_code="000001.SZ", trade_date="20240503",
                            total_mv=2, circ_mv=1, total_share=3, float_share=2, free_share=1, turnover_rate=4, pe=None)
        outside = {**selected, "ts_code": "999999.SZ"}
        with tempfile.TemporaryDirectory() as root:
            store = LocalStore(root);client = Client([selected, outside])
            args = dict(client=client, endpoint="daily_basic", params={"trade_date": "20240503"}, identity_map=IDS,
                        canonical_symbols=["000001.SZ"], operation_id="fetch", observed_at=OBS)
            raw = collect_stock_input_response(store, **args)
            self.assertEqual(collect_stock_input_response(store, **args)["batch_id"], raw["batch_id"])
            self.assertEqual(len(client.calls), 1)
            self.assertEqual(json.loads(store.read_raw_record(raw))[1]["ts_code"], "999999.SZ")
            sid = self.publish(store, raw)
            q = QuerySpec("valuation_daily", ("total_market_value_cny", "total_shares", "turnover_rate", "pe"),
                          ("sec-a", "sec-b"), ("2024-05-03",), "best_effort_vendor_v1", {"2024-05-03": "2024-05-03T20:01:00+08:00"})
            records = Data(root).read(snapshot=sid, query=q).to_json()["records"]
            self.assertEqual(records[0]["total_market_value_cny"], 20000)
            self.assertEqual(records[0]["total_shares"], 30000)
            self.assertEqual(records[0]["turnover_rate"], 4)
            self.assertIsNone(records[0]["pe"])
            self.assertIsNone(records[1]["total_shares"])
            with self.assertRaises(ConflictError):
                collect_stock_input_response(store, **{**args, "canonical_symbols": list(IDS)})

    def test_cap_and_bad_full_response_do_not_become_empty(self):
        row = complete("daily_basic", ts_code="000001.SZ", trade_date="20240503")
        with tempfile.TemporaryDirectory() as root:
            store = LocalStore(root)
            with patch.dict("axiom_data.stock_input_sources._CAPS", {"daily_basic": 2}):
                with self.assertRaises(CoverageError):self.collect(store, "daily_basic", [row, row])
            self.assertEqual(store.find_raw_by_operation("fetch")[0]["status"], "cap")
            for operation, bad in [("bad-fields", {k:v for k,v in row.items() if k != "pb"}),
                                    ("bad-date", {**row, "ts_code": "999999.SZ", "trade_date": "20240502"})]:
                with self.assertRaises(CoverageError):self.collect(store, "daily_basic", [bad], operation=operation)
                self.assertEqual(store.find_raw_by_operation(operation)[0]["status"], "failed")

    def test_permission_refusal_retains_safe_code_without_extra_calls(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalStore(root);client = Client(SourceResponseError(-2002))
            with self.assertRaises(CoverageError):
                collect_stock_input_response(store, client=client, endpoint="moneyflow", params={"trade_date":"20240503"},
                    identity_map=IDS, operation_id="denied", observed_at=OBS)
            raw = store.find_raw_by_operation("denied")[0]
            self.assertEqual(raw["status"], "failed")
            self.assertEqual(json.loads(store.read_raw_record(raw))["error_code"], -2002)
            self.assertEqual(len(client.calls), 1)

    def test_expanded_disclosure_conflicts_and_complementary_rows_never_merge(self):
        first = income();second = {**first, "revenue": 90}
        partials = [{**first, "revenue": None}, {**first, "n_income": None}]
        for rows in ([first, second], [second, first], partials, list(reversed(partials))):
            with self.subTest(rows=rows), tempfile.TemporaryDirectory() as root:
                store = LocalStore(root)
                raw = self.collect(store, "income_vip", rows, params={"period":"20240331", "report_type":"1"})
                sid = self.publish(store, raw)
                batch = Data(root).events(snapshot=sid, query=EventQuery("financial_events", ("revenue", "net_income", "total_revenue"),
                    ("sec-a",), "2024-03-31", "2024-03-31", "2024-04-30T10:00:00+08:00", "best_effort_vendor_v1", "report_period"))
                result = batch.to_json()["records"][0]
                self.assertIsNone(result["revenue"])
                self.assertEqual(result["total_revenue"], 100)
                if rows == partials or rows == list(reversed(partials)):self.assertIsNone(result["net_income"])
                self.assertEqual(json.loads(store.read_raw_record(raw)), rows)

    def test_explicit_financial_rebuild_preserves_old_receipts_and_snapshots(self):
        first = income();legacy = {f:first[f] for f in _FIELDS["income"]}
        params = {"ts_code":"000001.SZ", "period":"20240331", "report_type":"1"}
        early = datetime(2024, 4, 30, 2, tzinfo=timezone.utc)
        later = datetime(2024, 5, 2, 2, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as root:
            store = LocalStore(root)
            old = collect_event_response(store, client=Client([legacy]), endpoint="income", params=params, identity_map=IDS,
                operation_id="legacy", observed_at=early, next_open_session_by_date=CAL)
            valuation = self.collect(store, "daily_basic", [complete("daily_basic", ts_code="000001.SZ", trade_date="20240503")], operation="unrelated")
            old_sid = self.publish(store, old, valuation)
            old_manifest = deepcopy(store.load_snapshot(old_sid))
            old_bytes = (Path(root)/"snapshots"/f"{old_sid}.json").read_bytes()
            new = collect_stock_input_response(store, client=Client([{**first, "total_revenue":None}]), endpoint="income_vip",
                params={"period":"20240331","report_type":"1"}, identity_map=IDS, operation_id="expanded", observed_at=later,
                next_open_session_by_date=CAL)
            with self.assertRaises(ConflictError):
                apply_saved_raw(store, base_snapshot=old_sid, raw_batch_ids=[new["batch_id"]], operation_id="no-migrate",
                                build_context={"synthetic":True}, promote=False)
            spec = stock_input_source_spec("income_vip", identity_map=IDS, next_open_session_by_date=CAL)
            rebuilt = Data(root).rebuild(base_snapshot=old_sid, raw_batch_ids=[old["batch_id"], new["batch_id"]],
                domains=["financial_events"], operation_id="explicit-v3", build_context={"synthetic":True}, promote=False,
                domain_overrides={"financial_events":{k:spec[k] for k in ("contract","source_profile","normalizer")}}).snapshot_id
            self.assertEqual(store.load_snapshot(old_sid), old_manifest)
            self.assertEqual((Path(root)/"snapshots"/f"{old_sid}.json").read_bytes(), old_bytes)
            self.assertEqual(store.load_snapshot(rebuilt)["domains"]["valuation_daily"], old_manifest["domains"]["valuation_daily"])
            def read(cutoff):
                return Data(root).events(snapshot=rebuilt, query=EventQuery("financial_events", ("revenue","total_revenue"), ("sec-a",),
                    "2024-03-31","2024-03-31",cutoff,"operational_pit_v1","report_period")).to_json()["records"][0]
            late = read("2024-05-03T12:00:00Z");early_result = read("2024-05-01T12:00:00Z")
            self.assertEqual(late["revenue"],80)
            self.assertIsNone(late["total_revenue"])  # Never copy old core into the new native row.
            self.assertIsNone(early_result["revenue"])
            self.assertEqual(early_result["total_revenue"],100)
            self.assertEqual(store.read_raw_record(old), json.dumps([legacy],separators=(",", ":")).encode())
            self.assertEqual(store.get_raw(old["batch_id"])["observed_at"], old["observed_at"])

    def test_margin_next_morning_and_unresolved_quantity_not_same_day(self):
        row = complete("margin_detail", ts_code="000001.SZ",trade_date="20240503", rzye=10,rzmre=2,rzche=1,
                       rzrqye=12,rqyl=100,rqchl=20,rqmcl=15)
        with tempfile.TemporaryDirectory() as root:
            store=LocalStore(root);sid=self.publish(store,self.collect(store,"margin_detail",[row]))
            def read(cutoff):
                return Data(root).events(snapshot=sid,query=EventQuery("margin_daily",("margin_balance","lend_sell_volume","lend_sell_native"),
                    ("sec-a",),"2024-05-03","2024-05-03",cutoff,"best_effort_vendor_v1","session")).to_json()["records"]
            self.assertEqual(read("2024-05-03T21:00:00+08:00"),[])
            self.assertEqual(read("2024-05-06T08:29:00+08:00"),[])
            after=read("2024-05-06T08:31:00+08:00")[0]
            self.assertEqual(after["lend_sell_native"],15)
            self.assertIsNone(after["lend_sell_volume"])
            q=QuerySpec("margin_daily",("margin_balance",),("sec-a",),("2024-05-03",),"best_effort_vendor_v1",
                        {"2024-05-03":"2024-05-03T21:00:00+08:00"})
            with self.assertRaises(QueryError):Data(root).read(snapshot=sid,query=q)

    def test_industry_y_n_native_intervals_and_no_invented_publication(self):
        old={"ts_code":"000001.SZ","l1_code":"801010.SI","l2_code":"801016.SI","l3_code":"850111.SI",
             "in_date":"20200101","out_date":"20240430","is_new":"N"}
        new={**old,"l1_code":"801020.SI","in_date":"20240501","out_date":"","is_new":"Y"}
        with tempfile.TemporaryDirectory() as root:
            store=LocalStore(root)
            with self.assertRaisesRegex(Exception,"explicit classification/security"):
                self.collect(store,"index_member_all",[old],params={"ts_code":"000001.SZ"})
            raws=[self.collect(store,"index_member_all",[r],params={"ts_code":"000001.SZ","is_new":r['is_new']},operation=f"industry-{r['is_new']}") for r in (old,new)]
            sid=self.publish(store,*raws)
            q=EventQuery("industry_membership_events",("effective_to","is_new"),("sec-a",),"2020-01-01","2024-05-02",
                         "2026-10-12T00:00:00Z","operational_pit_v1","effective_from")
            records=Data(root).events(snapshot=sid,query=q).to_json()["records"]
            self.assertEqual(len(records),2)
            self.assertEqual(records[0]['effective_to'],'2024-04-30')
            self.assertIsNone(records[1]['effective_to'])
            with self.assertRaises(QueryError):
                Data(root).events(snapshot=sid,query=EventQuery(q.domain,q.fields,q.symbols,q.start,q.end,q.cutoff,"best_effort_vendor_v1",q.time_field))

    def test_forecast_current_revision_date_and_source_spec_isolation(self):
        first=complete("forecast_vip",ts_code="000001.SZ",ann_date="20240429",first_ann_date="20240301",end_date="20240630",type="预增")
        with tempfile.TemporaryDirectory() as root:
            store=LocalStore(root);raw=self.collect(store,"forecast_vip",[first],params={"start_date":"20240401","end_date":"20240430"})
            sid=self.publish(store,raw)
            q=lambda c:EventQuery("forecast_events",("forecast_type","first_announcement_date"),("sec-a",),"2024-06-30","2024-06-30",c,"best_effort_vendor_v1","report_period")
            self.assertTrue(Data(root).events(snapshot=sid,query=q("2024-04-29T10:00:00+08:00")).frame.empty)
            self.assertEqual(Data(root).events(snapshot=sid,query=q("2024-04-30T10:00:00+08:00")).to_json()['records'][0]['forecast_type'],'预增')
        spec=stock_input_source_spec("daily_basic",identity_map=IDS);spec['source_profile']['identity_map'].clear()
        self.assertEqual(stock_input_source_spec("daily_basic",identity_map=IDS)['source_profile']['identity_map'],IDS)

    def test_residual_balance_indicator_and_holder_native_values(self):
        cases=[("balancesheet_vip", complete("balancesheet_vip",ts_code="000001.SZ",ann_date="20240429",f_ann_date="20240429",
                  end_date="20240331",report_type="1",comp_type="1",end_type="1",update_flag="0",total_assets=100,
                  total_liab=40,total_hldr_eqy_exc_min_int=60,inventories=8,accounts_receiv=9),
                {"period":"20240331","report_type":"1"},"balance_sheet_events",("inventory","accounts_receiv"),(8,9)),
               ("fina_indicator_vip",complete("fina_indicator_vip",ts_code="000001.SZ",ann_date="20240429",end_date="20240331",
                  roe=12,roe_waa=11,debt_to_assets=40,grossprofit_margin=25,current_ratio=1.5),
                {"period":"20240331"},"financial_indicator_events",("grossprofit_margin","current_ratio"),(25,1.5)),
               ("stk_holdernumber",complete("stk_holdernumber",ts_code="000001.SZ",ann_date="20240429",end_date="20240331",holder_num=42),
                {"start_date":"20240401","end_date":"20240430"},"shareholder_count_events",("holder_num",),(42,))]
        for endpoint,row,params,domain,fields,expected in cases:
            with self.subTest(endpoint=endpoint),tempfile.TemporaryDirectory() as root:
                store=LocalStore(root);sid=self.publish(store,self.collect(store,endpoint,[row],params=params))
                batch=Data(root).events(snapshot=sid,query=EventQuery(domain,fields,("sec-a",),"2024-03-31","2024-03-31",
                    "2024-04-30T10:00:00+08:00","best_effort_vendor_v1","report_period"))
                result=batch.to_json()['records'][0]
                self.assertEqual(tuple(result[f] for f in fields),expected)

    def test_moneyflow_native_net_and_catalog_request_version(self):
        row=complete("moneyflow",ts_code="000001.SZ",trade_date="20240503",net_mf_amount=-2,buy_elg_amount=10,sell_elg_amount=7)
        catalog=complete("index_classify",index_code="801010.SI",industry_name="synthetic",parent_code="0",level="L1",
                         industry_code="110000",is_pub="1",src="SW")
        with tempfile.TemporaryDirectory() as root:
            store=LocalStore(root);raw=self.collect(store,"moneyflow",[row]);sid=self.publish(store,raw)
            q=QuerySpec("money_flow_daily",("net_inflow_cny","extra_large_buy_cny","extra_large_sell_cny"),("sec-a",),
                ("2024-05-03",),"best_effort_vendor_v1",{"2024-05-03":"2024-05-03T20:01:00+08:00"})
            r=Data(root).read(snapshot=sid,query=q).to_json()['records'][0]
            self.assertEqual(r['net_inflow_cny'],-20000)
            self.assertEqual(r['extra_large_buy_cny']-r['extra_large_sell_cny'],30000)
            saved=self.collect(store,"index_classify",[catalog],params={"level":"L1","src":"SW2021"},operation="catalog")
            csid=self.publish(store,saved,operation="catalog-publish")
            domain=store.load_snapshot(csid)['domains']['industry_catalogue']
            c=store.read_partition(domain['partitions'][0]).to_pylist()[0]
            self.assertEqual(c['requested_src'],'SW2021')
            self.assertEqual(c['src'],'SW')  # Preserve native value; do not infer per-member vintage.


if __name__ == "__main__":
    unittest.main()
