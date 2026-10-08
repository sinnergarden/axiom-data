"""Deterministic synthetic inputs; the frozen oracle was produced by 8bebf147."""
from copy import deepcopy
from pathlib import Path
from dataclasses import replace
import json
import pyarrow as pa
from axiom_data import Data, EventQuery, QuerySpec


class Store:
    def __init__(self, manifest, rows):
        self.root = Path('/synthetic-native-view')
        self.manifest, self.rows = deepcopy(manifest), deepcopy(rows)
        self.loads = self.decodes = 0

    def load_snapshot(self, snapshot):
        assert snapshot == 's1'
        self.loads += 1
        return deepcopy(self.manifest)

    def verify_partition(self, part):
        pass

    def get_raw(self, batch):
        assert batch=='ambiguous'
        return {'raw_batch_id':batch,'source_profile':{'identity_map':{'A':'A','B':'B'},
            'field_map':{k:k for k in ('implementation_announcement_date','record_date','ex_date')}}}

    def read_raw_record(self, raw):
        return json.dumps([dict(ts_code=s,div_proc='implemented',end_date='20231231',ann_date='20240101',
            implementation_announcement_date='20240101',record_date=d,ex_date=d)
            for s in ('A','B') for d in ('20240102','20240104')]).encode('utf-8')

    def read_partition(self, part, *, columns=None, symbols=None, sessions=None):
        self.decodes += 1
        rows = self.rows[part['uri']]
        if symbols is not None:
            rows = [r for r in rows if r.get('security_id') in symbols]
        if sessions is not None:
            rows = [r for r in rows if r.get('session') in sessions]
        return pa.Table.from_pylist([{c:r.get(c) for c in columns} for r in rows],
            schema=None if rows else pa.schema([(c, pa.null()) for c in columns]))


def source():
    stamp = '2024-01-01T00:00:00+00:00'
    def row(**kw):
        return dict(revision_id='r1', revision_sequence=1, raw_batch_id='raw1',
                    first_observed_at=stamp, source_available_at=None, evidence_ref=None, **kw)
    domains, rows = {}, {}
    def add(name, keys, fields, values, coverage=None, partition='history', profile=None):
        uri = name + '/' + partition
        rows[uri] = values
        domains[name] = dict(contract=dict(contract_id=name+'.fixture.v1', logical_key=keys,
            fields={k:dict(dtype=v, unit='CNY/share' if k=='close' else None) for k,v in fields.items()}),
            source_profile=profile or dict(id=name+'.fixture.v1', availability=dict(
                timezone='Asia/Shanghai', session_release_time='18:00:00')),
            partitions=[dict(partition=partition, uri=uri, rows=len(values), file_sha256='0'*64)],
            coverage=coverage or {})
    prices = [row(security_id='A',session='2024-01-02',close=-0.0,volume=2**53+1),
        row(security_id='B',session='2024-01-02',close=3,volume=None),
        row(security_id='A',session='2024-01-03',close=4,volume=5)]
    revised = dict(prices[0], revision_id='r2',revision_sequence=2,
                   first_observed_at='2024-01-04T00:00:00+00:00',close=7.5)
    add('market_daily',['security_id','session'],{'close':'float64','volume':'int64'},
        prices+[revised],coverage={'complete_cells':[{'security_id':'A','session':'2024-01-05','complete':True}],
                                  'note':'汉字\\\"\n'},partition='2024-01')
    add('trading_calendar',['exchange','session'],{'exchange':'string','session':'date','is_open':'bool'},
        [row(exchange='SSE',session=f'2024-01-{d:02d}',is_open=d!=1) for d in range(1,6)],partition='2024-01')
    add('security_master',['security_id','listing_date'],
        {'exchange':'string','listing_date':'date','delisting_date':'date'},
        [row(security_id='A',exchange='SSE',listing_date='2024-01-02',delisting_date='2024-01-05'),
         row(security_id='B',exchange='SSE',listing_date='2024-01-03',delisting_date=None)])
    add('security_status',['security_id','session'],{'is_suspended':'bool'},
        [row(security_id='A',session='2024-01-02',is_suspended=False)],partition='2024-01')
    add('universe_membership',['membership_id'],
        {'universe_id':'string','effective_from':'date','effective_to':'date'},
        [row(security_id='A',universe_id='IDX',membership_id='m1',effective_from='2024-01-01',effective_to='2024-01-03')],
        coverage={'complete_states':[dict(universe_id='IDX',complete=True,effective_from='2024-01-01',
            effective_to='2024-01-03',members=['A'],first_observed_at=stamp,raw_batch_id='state1'),
            dict(universe_id='IDX',complete=True,effective_from='2024-01-03',effective_to='2024-01-05',
                 members=[],first_observed_at='2024-01-03T00:00:00+00:00',raw_batch_id='state2')]})
    action_keys=['security_id','logical_event_key','report_period','announcement_date','process_status']
    def action(s,k,**kw):
        result=row(security_id=s,logical_event_key=k,report_period='2023-12-31',
            announcement_date='2024-01-01',process_status='implemented',vendor_ann_date='2024-01-01',
            ex_date='2024-01-02',record_date='2024-01-01',cash=1.,cash__status='value',
            implementation_announcement_date='2024-01-01')
        result.update(kw);return result
    old=action('A','moves');correction=dict(old,revision_id='r2',revision_sequence=2,
        first_observed_at='2024-01-04T00:00:00+00:00',vendor_ann_date='2024-01-04',ex_date='2024-02-01')
    add('corporate_actions',action_keys,{'ex_date':'date','record_date':'date','cash':'float64',
        'cash__status':'string','implementation_announcement_date':'date'},
        [action('A','uncertain',ex_date=None,cash=None,cash__status='source_missing',
                source_issue='ambiguous_action_identity_or_revision',raw_batch_id='ambiguous'),
         action('B','uncertain',ex_date=None,cash=None,cash__status='source_missing',
                source_issue='ambiguous_action_identity_or_revision',raw_batch_id='ambiguous'),old,
         action('B','zero',cash=0.),action('A','withdrawn',cash=None,cash__status='retracted'),
         action('B','bound',first_observed_at='2024-01-04T00:00:00+00:00',
                source_available_at=stamp,evidence_ref='raw:revision-bound#fixture')],
        coverage={'note':'full event coverage kept'},partition='history-1',
        profile=dict(id='actions.fixture.v1',revision_order='source_sequence_only',availability=dict(
            timezone='Asia/Shanghai',session_release_time='18:00:00',date_field='vendor_ann_date',
            date_rule='same_day_release')))
    rows['corporate_actions/history-2']=[correction]
    domains['corporate_actions']['partitions'].append(dict(partition='history-2',
        uri='corporate_actions/history-2',rows=1,file_sha256='0'*64))
    return dict(snapshot_id='s1',domains=domains),rows


def reads():
    out=[]
    for cutoff in ('2024-01-06T00:00:00+00:00','2024-01-02T12:00:00+00:00'):
        for method in ('read_market','members','states'):
            members=method=='members'
            days=('2024-01-03','2024-01-02','2024-01-05') if method!='states' else ('2024-01-01','2024-01-02','2024-01-04','2024-01-05')
            q=QuerySpec('universe_membership' if members else 'market_daily',
                ('is_member',) if members else ('close',) if method=='states' else ('close','volume'), ('B','A','C'),days,
                'operational_pit_v1',{d:cutoff for d in days},
                purpose='decision_facts' if members else 'market_replay',universe_id='IDX' if members else None)
            out.append({'method':method,'query':q})
    base=out[0]['query']
    for policy in ('market_pit_safe_v1','best_effort_vendor_v1'):
        out.append({'method':'read_market','query':replace(base,pit_policy=policy)})
    out.append({'method':'read_market','query':replace(base,pit_policy='bootstrap_hybrid_v1',
        policy_by_session={s:('best_effort_vendor_v1' if i%2 else 'operational_pit_v1')
                           for i,s in enumerate(base.sessions)})})
    out.append({'method':'states','query':replace(out[2]['query'],pit_policy='market_pit_safe_v1')})
    for policy in ('operational_pit_v1','market_pit_safe_v1','best_effort_vendor_v1'):
        for cutoff in ('2024-01-06T00:00:00+00:00','2024-01-02T12:00:00+00:00'):
            for clock in ('ex_date','record_date'):
                out.append({'method':'events','query':EventQuery('corporate_actions',
                    ('cash','record_date','implementation_announcement_date'),('B','A','C'),
                    '2024-01-01','2024-01-05',cutoff,policy,time_field=clock,purpose='market_replay')})
    return out


def data():
    d=Data('/synthetic-native-view',cache_bytes=1024*1024)
    d.store=Store(*source())
    return d
