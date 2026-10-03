"""Offline failures across Raw selection, builder identity and PIT consumers."""
import sys, tempfile, unittest, json
from pathlib import Path
from copy import deepcopy
from dataclasses import replace

from axiom_data import Data, IngestBatch, QuerySpec, UpdateRequest, EventQuery
from axiom_data.storage import LocalStore
from axiom_data.updates import apply_saved_raw
from axiom_data.vendor_membership import publish_vendor_membership
from axiom_data.event_sources import collect_event_response
from test_local_updates import CONTRACT, PROFILE, batch, row, update, stored_rows
from test_vendor_membership import _raw
from test_completion_event_sources import Client, OBS, IDS
from test_qlib_export import fixture, DAYS
from axiom_data.reader import _revision_order
from axiom_data import export_bundle, import_bundle, verify_bundle, ConflictError
from axiom_data.builder import freeze_builder
from unittest.mock import patch

class ObservationRecoveryTests(unittest.TestCase):
    def test_full_raw_backup_preserves_unreferenced_observations(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root); store=LocalStore(root/'source')
            first=update(store,None,'initial',batch([row()]))
            extra=store.write_raw(b'saved extra response',domain='future_domain',request={},
                                 source_profile={},observed_at='2026-09-02T12:00:00Z')
            code=Path(__file__).resolve().parents[1]
            snapshot=export_bundle(store.root,root/'snapshot',code_root=code,snapshot_id=first.snapshot_id)
            backup=export_bundle(store.root,root/'backup',code_root=code,snapshot_id=first.snapshot_id,
                                 raw_backup_cutoff='2026-09-02T12:00:00Z')
            self.assertEqual(snapshot['raw_scope']['mode'],'snapshot_closure')
            self.assertNotIn(extra['batch_id'],(root/'snapshot/data/raw/fetches.jsonl').read_text())
            self.assertEqual(verify_bundle(root/'backup'),backup)
            import_bundle(root/'backup',root/'restored')
            restored=LocalStore(root/'restored')
            self.assertEqual(restored.read_raw_record(restored.get_raw(extra['batch_id'])),b'saved extra response')
            self.assertEqual(restored.resolve('current'),first.snapshot_id)

    def test_unfinished_operation_rejects_changed_builder_before_another_raw(self):
        with tempfile.TemporaryDirectory() as root:
            store=LocalStore(root); original=freeze_builder()
            with patch('axiom_data.updates.normalize_batch',side_effect=ValueError('interrupted processing')):
                with self.assertRaises(Exception):update(store,None,'resume',batch([row()]))
            raw_before=(Path(root)/'raw/fetches.jsonl').read_bytes()
            different=deepcopy(original);different['source']['version']='changed-builder'
            with patch('axiom_data.builder.freeze_builder',return_value=different):
                with self.assertRaisesRegex(ConflictError,'builder changed'):
                    update(store,None,'resume',batch([row()]))
            self.assertEqual((Path(root)/'raw/fetches.jsonl').read_bytes(),raw_before)
            self.assertEqual(update(store,None,'resume',batch([row()])).snapshot_id,store.resolve('current'))

    def test_actual_builder_is_recoverable(self):
        with tempfile.TemporaryDirectory() as root:
            store=LocalStore(root)
            result=update(store,None,'initial',batch([row()]))
            context=store.load_snapshot(result.snapshot_id)['domains']['market_daily']['build_context']
            self.assertIn('builder',context)
            self.assertTrue(context['builder']['recoverable'])

    def test_later_raw_extra_reaches_explicit_history_rebuild(self):
        with tempfile.TemporaryDirectory() as root:
            store=LocalStore(root);profile=deepcopy(PROFILE)
            profile['field_map'].pop('revision_id');profile['field_map'].pop('revision_sequence')
            profile['revision_order']='terminal_observation_v1'
            a=update(store,None,'t1',batch([row()],day=1,profile=profile))
            b=update(store,a.snapshot_id,'t2',batch([dict(row(),extra=7)],day=2,profile=profile))
            self.assertFalse(b.changed)
            saved=store.load_snapshot(a.snapshot_id)['domains']['market_daily']['raw_batch_ids']
            t2=store.read_operation('t2')['raw_batch_ids'][0]
            self.assertNotIn(t2,saved)
            contract=deepcopy(CONTRACT);contract['fields']['extra']={'dtype':'float64','nullable':True}
            profile['field_map']['extra']='extra'
            ids=Data(root).select_raw(domains=('market_daily',),receipt_cutoff='2026-09-02T12:00:00Z')['raw_batch_ids']
            self.assertEqual(len(ids),2)
            replay=Data(root).rebuild(base_snapshot=a.snapshot_id,raw_batch_ids=saved,domains=('market_daily',),
                operation_id='snapshot-only',build_context={},promote=False,
                domain_overrides={'market_daily':{'contract':contract,'source_profile':profile}})
            self.assertIsNone(stored_rows(store,replay.snapshot_id)[-1]['extra'])
            result=Data(root).rebuild(base_snapshot=a.snapshot_id,raw_batch_ids=ids,domains=('market_daily',),
                operation_id='add-extra',build_context={'reason':'field expansion'},promote=False,
                domain_overrides={'market_daily':{'contract':contract,'source_profile':profile}})
            self.assertEqual(max(stored_rows(store,result.snapshot_id),key=lambda r:r['first_observed_at'])['extra'],7)
            self.assertIn(t2,ids)

    def test_dividend_filter_precedes_nullable_economic_date(self):
        dividend=[
            {'ts_code':'000001.SZ','end_date':'20181231','ann_date':'20190420','imp_ann_date':None,
             'div_proc':'预案','cash_div_tax':.5,'stk_bo_rate':0,'stk_co_rate':0,'record_date':None,'ex_date':None},
            {'ts_code':'000001.SZ','end_date':'20181231','ann_date':'20190520','imp_ann_date':'20190520',
             'div_proc':'实施','cash_div_tax':.5,'stk_bo_rate':0,'stk_co_rate':0,'record_date':'20190601','ex_date':'20190602'}]
        with tempfile.TemporaryDirectory() as root:
            store=LocalStore(root)
            raw=collect_event_response(store,client=Client([dividend]),endpoint='dividend',
                params={'ts_code':'000001.SZ'},identity_map=IDS,observed_at=OBS,operation_id='fetch',batch_index=0,
                next_open_session_by_date={'2019-04-20':'2019-04-22','2019-05-20':'2019-05-21'})['batch_id']
            sid=apply_saved_raw(store,base_snapshot=None,raw_batch_ids=[raw],operation_id='publish',build_context={}).snapshot_id
            q=EventQuery('corporate_actions',('cash_dividend_before_tax_per_share',),('sec-bank',),
                '2019-06-01','2019-06-30','2019-06-01T00:00:00+08:00','best_effort_vendor_v1','ex_date',
                filters={'process_status':'实施'})
            self.assertEqual(len(Data(root).events(snapshot=sid,query=q).frame),1)

    def membership(self, same_date=False):
        with tempfile.TemporaryDirectory() as root:
            store=LocalStore(root)
            first=[_raw(store,code,'2026-01-31',[member],'2026-02-01T10:00:00Z')
                   for code,member in [('000300.SH','A.SH'),('000905.SH','B.SH'),('000852.SH','C.SH')]]
            second=_raw(store,'000300.SH','2026-01-31' if same_date else '2026-02-28',
                        ['D.SH'],'2026-03-02T10:00:00Z')
            sid=publish_vendor_membership(store,index_weight_raw_batch_ids=first+[second],identity_map={},
                verified_through='2026-03-31',operation_id='one-build',base_snapshot=None,promote=False)['snapshot_id']
            for policy in ['operational_pit_v1','market_pit_safe_v1']:
                q=QuerySpec('universe_membership',('is_member',),('A.SH','D.SH'),('2026-02-20',),
                    policy,{'2026-02-20':'2026-02-20T20:00:00Z'},universe_id='csi1800')
                values=Data(root).members(snapshot=sid,query=q).frame['is_member'].tolist()
                self.assertEqual([None if str(v)=='<NA>' else bool(v) for v in values],[True,False])

    def test_first_membership_build_keeps_receipt_chain(self): self.membership()
    def test_first_membership_build_keeps_same_dated_correction(self): self.membership(True)

    def test_first_membership_build_keeps_a_b_a_receipts(self):
        with tempfile.TemporaryDirectory() as root:
            store=LocalStore(root)
            ids=[_raw(store,code,'2026-01-31',[member],'2026-02-01T10:00:00Z')
                 for code,member in [('000300.SH','A.SH'),('000905.SH','B.SH'),('000852.SH','C.SH')]]
            ids += [_raw(store,'000300.SH','2026-01-31',['D.SH'],'2026-02-02T10:00:00Z'),
                    _raw(store,'000300.SH','2026-01-31',['A.SH'],'2026-02-03T10:00:00Z')]
            sid=publish_vendor_membership(store,index_weight_raw_batch_ids=ids,identity_map={},
                verified_through='2026-03-31',operation_id='history',base_snapshot=None)['snapshot_id']
            for day,expected in [(1,[True,False]),(2,[False,True]),(3,[True,False])]:
                q=QuerySpec('universe_membership',('is_member',),('A.SH','D.SH'),('2026-02-20',),
                    'operational_pit_v1',{'2026-02-20':f'2026-02-0{day}T20:00:00Z'},universe_id='csi1800')
                self.assertEqual(Data(root).members(snapshot=sid,query=q).frame['is_member'].tolist(),expected)

    def test_qlib_calendar_respects_closed_revision(self):
        with tempfile.TemporaryDirectory() as root:
            # Set the production revision policy before the first observation.
            from unittest.mock import patch
            original_update=Data.update
            def terminal_update(data,*,base_snapshot,request):
                batches=tuple(replace(b,source_profile={**b.source_profile,'revision_order':'terminal_observation_v1'})
                              if b.domain=='trading_calendar' else b for b in request.batches)
                return original_update(data,base_snapshot=base_snapshot,request=replace(request,batches=batches))
            with patch.object(Data,'update',terminal_update):
                data,sid,q=fixture(Path(root)/'data')
            d=data.store.load_snapshot(sid)['domains']['trading_calendar']
            b=IngestBatch('trading_calendar',json.dumps([{'session':DAYS[1],'is_open':False}]).encode(),
                {},d['contract'],d['source_profile'],'2026-10-04T00:00:00Z')
            newer=data.update(base_snapshot=sid,request=UpdateRequest((b,),'calendar-correction',{})).snapshot_id
            q=replace(q,pit_policy='operational_pit_v1',cutoff_by_session={s:'2026-10-05T00:00:00Z' for s in DAYS})
            domain=data.store.load_snapshot(newer)['domains']['trading_calendar']
            revisions=[r for p in domain['partitions'] for r in data.store.read_partition(p).to_pylist()
                       if str(r['session'])==DAYS[1]]
            self.assertFalse(_revision_order(revisions,'calendar',profile=domain['source_profile'])['is_open'])
            sessions=(DAYS[0],DAYS[2])
            q=replace(q,sessions=sessions,cutoff_by_session={s:q.cutoff_by_session[s] for s in sessions})
            early=replace(q,cutoff_by_session={s:'2026-10-03T20:00:00Z' for s in sessions})
            with self.assertRaisesRegex(Exception,'every open session'):
                data.export_qlib(snapshot=newer,queries=(early,),destination=Path(root)/'too-early')
            data.export_qlib(snapshot=newer,queries=(q,),destination=Path(root)/'view')
            with patch.object(data,'read',side_effect=AssertionError('persistent view must be reused')):
                data.export_qlib(snapshot=newer,queries=(q,),destination=Path(root)/'view')

if __name__=='__main__':unittest.main(verbosity=2)
