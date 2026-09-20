"""Reviewer probes for period precedence and exchange-specific sessions."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from axiom_data.artifacts import ArtifactError
from axiom_data.pr7_source import select_pr7_revisions
from axiom_data.pr7_views import LEAF_DOMAINS,project,leaf_facts,payload_files
from axiom_data.pit import fingerprint
from axiom_data.consumption import QlibViewReader

SYMBOLS=['600000.SH','000001.SZ']


def report(period,value,observed,domain='holder_count_events',symbol='600000.SH',complete=True):
    revision=fingerprint([domain,period,value,observed]);key=fingerprint([domain,symbol,period])
    o={'observed_at':observed,'vendor_available_at':observed,'source_ref':revision,'revision_id':revision}
    o['observation_id']=fingerprint(o)
    field='number' if domain=='holder_count_events' else 'top10_ratio'
    return dict(symbol=symbol,logical_event_key=key,revision_id=revision,report_period=period,
        session=None,announcement=observed[:10],first_observed_at=observed,source_available_at=None,
        vendor_available_at=observed,pit_qualification='best_effort',source_ref=revision,observations=[o],
        values={field:value if complete else None},missing_reasons={} if complete else {field:'incomplete_report'},
        group_completeness='complete' if complete else 'incomplete',holders=[{'holder_id':'holder'}])


class Reader:
    def __init__(self,exchanges=('SSE','SZSE'),rows=(),closed=()):
        self.snapshot=SimpleNamespace(ref=SimpleNamespace(snapshot_id='snapshot-test'))
        self.commits={d:SimpleNamespace(ref=SimpleNamespace(commit_id=d,contract_version=d+'.v1')) for d in set(LEAF_DOMAINS.values())}
        self.rows=rows
        self.securities=[{'symbol':s,'exchange':e} for s,e in zip(SYMBOLS,['SSE','SZSE'])]
        self.calendar=[{'exchange':e,'session':day,'is_open':(e,day) not in closed}
                       for e in exchanges for day in ['2025-06-02','2025-06-03','2025-06-04']]
    def security_master(self):return self.securities
    def trading_calendar(self,**kwargs):return self.calendar
    def facts(self,domain,*,symbols):
        if domain not in ('holder_count_events','top_holders_reports'):return ()
        field='number' if domain=='holder_count_events' else 'top10_ratio'
        return tuple(r for r in self.rows if r['symbol'] in symbols and field in r['values'])
    def as_of(self,domain,*,symbols,pit_policy,knowledge_cutoff,start_session=None,end_session=None):
        if domain not in ('holder_count_events','top_holders_reports'):return ()
        field='number' if domain=='holder_count_events' else 'top10_ratio'
        return select_pr7_revisions([r for r in self.rows if r['symbol'] in symbols and field in r['values']],policy=pit_policy,knowledge_cutoff=knowledge_cutoff)


class Pr7ProjectionTest(unittest.TestCase):
    def setUp(self):
        self.guard=patch('axiom_data.pr7_views.request_coverage');self.guard.start();self.addCleanup(self.guard.stop)
    def fact(self,reader,leaf='holder.number',cutoff='2025-06-04T23:59:59+08:00',symbol='600000.SH'):
        return leaf_facts(reader,leaf,symbol=symbol,target_session='2025-06-04',knowledge_cutoff=cutoff,pit_policy='operational_pit_v1')
    def projection(self,reader,symbols=SYMBOLS):
        return project(reader,dict(symbols=symbols,start_session='2025-06-02',end_session='2025-06-04'), 'operational_pit_v1','2025-06-04T23:59:59+08:00')
    def test_forecast_metadata_declares_snapshot_contract_even_when_missing(self):
        reader=Reader()
        for version in ['forecast_observations.v1','forecast_observations.v2']:
            reader.commits['forecast_observations'].ref.contract_version=version
            fact=self.fact(reader,'forecast.type')
            self.assertEqual(fact['contract_version'],version)
            self.assertEqual(fact['missing_reason'],'no_observation_at_cutoff')
            payload=self.projection(reader)
            self.assertTrue(all(row['facts']['forecast.type']['contract_version']==version for row in payload['wide']))
    def test_old_period_late_revision_cannot_replace_q1(self):
        for domain,leaf in [('holder_count_events','holder.number'),('top_holders_reports','holder.top10_ratio')]:
            with self.subTest(domain=domain):
                reader=Reader(rows=[report('2025-03-31',200,'2025-04-10T00:00:00Z',domain),report('2024-12-31',100,'2025-05-10T00:00:00Z',domain)])
                fact=self.fact(reader,leaf);self.assertEqual((fact['report_period'],fact['value']),('2025-03-31',200))
    def test_same_period_revision_new_period_and_future_visibility(self):
        for domain,leaf in [('holder_count_events','holder.number'),('top_holders_reports','holder.top10_ratio')]:
            rows=[report('2024-12-31',100,'2025-03-01T00:00:00Z',domain),report('2024-12-31',150,'2025-03-10T00:00:00Z',domain),report('2025-03-31',200,'2025-04-10T00:00:00Z',domain),report('2025-03-31',250,'2025-06-05T00:00:00Z',domain)]
            reader=Reader(rows=rows)
            for cutoff,expected in [('2025-03-05T00:00:00Z',100),('2025-03-15T00:00:00Z',150),('2025-04-01T00:00:00Z',150),('2025-06-04T00:00:00Z',200),('2025-06-06T00:00:00Z',250)]:
                with self.subTest(domain=domain,cutoff=cutoff):self.assertEqual(self.fact(reader,leaf,cutoff)['value'],expected)
    def test_incomplete_latest_top10_does_not_fall_back(self):
        reader=Reader(rows=[report('2025-03-31',200,'2025-04-10T00:00:00Z','top_holders_reports',complete=False),report('2024-12-31',100,'2025-05-10T00:00:00Z','top_holders_reports')])
        f=self.fact(reader,'holder.top10_ratio');self.assertEqual(f['report_period'],'2025-03-31');self.assertIsNone(f['value']);self.assertEqual(f['missing_reason'],'incomplete_report')
    def test_matching_exchange_calendars_pass(self):
        for exchange,symbol in zip(['SSE','SZSE'],SYMBOLS):
            self.assertEqual(len(self.projection(Reader([exchange]),[symbol])['wide']),3)
    def test_szse_with_only_sse_and_mixed_missing_calendar_fail(self):
        for symbols in [['000001.SZ'],SYMBOLS]:
            with self.subTest(symbols=symbols),self.assertRaisesRegex(ArtifactError,'INSUFFICIENT_SCOPE'):self.projection(Reader(['SSE']),symbols)
        with self.assertRaisesRegex(ArtifactError,'INSUFFICIENT_SCOPE'):self.fact(Reader(['SSE']),symbol='000001.SZ')
    def test_exchange_mapping_mismatch_and_unknown_fail(self):
        for exchange in ['SSE','UNKNOWN']:
            reader=Reader();reader.securities[1]['exchange']=exchange
            with self.assertRaises(ValueError):self.projection(reader)
    def test_calendar_interior_gap_fails(self):
        reader=Reader();reader.calendar=[r for r in reader.calendar if (r['exchange'],r['session'])!=('SZSE','2025-06-03')]
        with self.assertRaisesRegex(ArtifactError,'INSUFFICIENT_SCOPE'):self.projection(reader)
    def test_each_symbol_uses_own_open_sessions_and_qlib_agrees(self):
        reader=Reader(rows=[report('2025-03-31',200,'2025-04-10T00:00:00Z'),report('2024-12-31',100,'2025-05-10T00:00:00Z'),report('2025-03-31',20,'2025-04-10T00:00:00Z','top_holders_reports'),report('2024-12-31',10,'2025-05-10T00:00:00Z','top_holders_reports')],closed={('SSE','2025-06-03'),('SZSE','2025-06-02')})
        payload=self.projection(reader)
        expected={('600000.SH','2025-06-02'),('600000.SH','2025-06-04'),('000001.SZ','2025-06-03'),('000001.SZ','2025-06-04')}
        self.assertEqual({(r['symbol'],r['session']) for r in payload['wide']},expected)
        from axiom_data.pr7_views import manifest_for
        reader.snapshot.manifest={'identity_digest':'test'}
        scope=dict(symbols=SYMBOLS,start_session='2025-06-02',end_session='2025-06-04')
        m=manifest_for(reader,scope,'operational_pit_v1','2025-06-04T23:59:59+08:00',payload,{})
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for path,content in payload_files(payload,SYMBOLS,{}).items():
                dest=root/path;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(content)
            q=object.__new__(QlibViewReader);q.data_root=root;q.path=root;q.view=SimpleNamespace(manifest=m)
            binary=q.market_daily(include_missing=True)
            self.assertEqual({(r['symbol'],r['session']) for r in binary},expected)
            for row in payload['wide']:
                actual=next(r for r in binary if (r['symbol'],r['session'])==(row['symbol'],row['session']))
                for field in ['holder.number','holder.top10_ratio']:
                    self.assertEqual(actual[field],row['facts'][field]['value'])

    def test_closed_day_direct_projection_fails(self):
        with self.assertRaisesRegex(ArtifactError,'CLOSED_SESSION'):
            self.fact(Reader(closed={('SSE','2025-06-04')}))
