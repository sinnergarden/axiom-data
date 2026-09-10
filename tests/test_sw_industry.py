import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from axiom_data import ArtifactError,load_raw_batch
from axiom_data.sw_source import IndustryQualificationCollector
from axiom_data.pr6_source import Pr6Builder
from axiom_data.contracts import load_contract
from axiom_data.sw_industry import validate_rows,project_state
from axiom_data.sw_mapping import load_mapping_profile
from axiom_data.pit import select_revisions

class SwIndustryTest(unittest.TestCase):
    def test_complete_history_correction_and_return_preserve_operational_prefix(self):
        fixture=json.loads((Path(__file__).parent/'fixtures/sw2021_golden.json').read_bytes())
        class Client:
            empty=False
            def query(self,endpoint,*,fields,**params):
                if endpoint=='index_classify':return [r for r in fixture['taxonomy'] if r['level']==params['level']]
                return [r for r in fixture['members'] if r['is_new']==params['is_new'] and not (self.empty and r['ts_code']=='001289.SZ')]
        with tempfile.TemporaryDirectory() as directory:
            client=Client();collector=IndustryQualificationCollector(directory,client)
            builder=Pr6Builder(directory,'industry_membership',builder_config={'symbols':list(fixture['security']),
                'start_session':'2014-01-01','end_session':'2026-09-08','industry_source_profile':'tushare_sw2021.v1'})
            def build(parent,day):
                requests=[('index_classify',{'src':'SW2021','level':level}) for level in ['L1','L2','L3']]
                requests += [('index_member_all',{'is_new':mode,'limit':'1000','offset':'0'}) for mode in ['Y','N']]
                raws=[load_raw_batch(directory,collector.collect(ep,p,retrieved_at=day+'T03:00:00+00:00').raw_batch_id) for ep,p in requests]
                result=builder._build_rows(load_contract('industry_membership.v3'),parent,raws);validate_rows(result)
                return result
            def select(rows,cutoff):
                return {r['symbol']:r for r in select_revisions(rows,policy='operational_pit_v1',knowledge_cutoff=cutoff+'T04:00:00+00:00')}
            first=build([],'2026-09-09');client.empty=True
            second=build(first,'2026-09-10')
            self.assertEqual(select(first,'2026-09-09'),select(second,'2026-09-09'))
            self.assertEqual(select(second,'2026-09-10')['001289.SZ']['membership_spans'],[])
            life={'list_session':'2022-01-24','delist_session':None}
            self.assertEqual(project_state(select(second,'2026-09-10')['001289.SZ'],life,'2024-01-02')['availability_state'],'classification_unavailable')
            client.empty=False;third=build(second,'2026-09-11')
            self.assertEqual(select(second,'2026-09-10'),select(third,'2026-09-10'))
            self.assertEqual(select(first,'2026-09-09')['001289.SZ']['revision_id'],select(third,'2026-09-11')['001289.SZ']['revision_id'])
            self.assertEqual(project_state(select(third,'2026-09-11')['001289.SZ'],life,'2024-01-02')['availability_state'],'classified')

    def test_canonical_history_gaps_and_anomaly_fail_closed(self):
        fixture=json.loads((Path(__file__).parent/'fixtures/sw2021_golden.json').read_bytes())
        class Client:
            def query(self,endpoint,*,fields,**params):
                return ([r for r in fixture['taxonomy'] if r['level']==params['level']] if endpoint=='index_classify'
                        else [r for r in fixture['members'] if r['is_new']==params['is_new']])
        with tempfile.TemporaryDirectory() as directory:
            collector=IndustryQualificationCollector(directory,Client());ids=[]
            for level in ['L1','L2','L3']:ids.append(collector.collect('index_classify',{'src':'SW2021','level':level},retrieved_at='2026-09-09T03:00:00+00:00').raw_batch_id)
            for mode in ['Y','N']:ids.append(collector.collect('index_member_all',{'is_new':mode,'limit':'1000','offset':'0'},retrieved_at='2026-09-09T03:00:00+00:00').raw_batch_id)
            raws=[load_raw_batch(directory,id) for id in ids]
            cfg={'symbols':list(fixture['security']),'start_session':'2014-01-01','end_session':'2026-09-08','industry_source_profile':'tushare_sw2021.v1'}
            builder=Pr6Builder(directory,'industry_membership',builder_config=cfg)
            rows=builder._build_rows(load_contract('industry_membership.v3'),[],raws);validate_rows(rows)
            selected={r['symbol']:r for r in select_revisions(rows,policy='best_effort_vendor_v1',knowledge_cutoff='2026-09-09T04:00:00+00:00')}
            for symbol,session,expected in [('000506.SZ','2020-01-02','source_coverage_gap'),('000975.SZ','2014-02-03','source_coverage_gap'),('001289.SZ','2022-01-24','source_coverage_gap'),('000506.SZ','2018-06-15','boundary_session_ambiguous')]:
                security=fixture['security'][symbol]
                life={'list_session':security['list_date'][:4]+'-'+security['list_date'][4:6]+'-'+security['list_date'][6:],'delist_session':None}
                self.assertEqual(project_state(selected[symbol],life,session)['missing_reason'],expected)
            steel=project_state(selected['000708.SZ'],{'list_session':'1997-03-26','delist_session':None},'2024-01-02')
            self.assertEqual(steel['industry_id'],'850412.SI');self.assertEqual(steel['mapping_provenance']['source_code'],'850401.SI')
            self.assertEqual(steel['availability_state'],'classified')
            self.assertTrue(any(r['index_code']=='850401.SI' for r in json.loads(raws[2].payload)))
            wrong=load_mapping_profile();wrong['anomaly_mappings']=[]
            with patch('axiom_data.sw_industry.load_mapping_profile',return_value=wrong):
                builder.builder_config['sw_mapping_profile']=wrong
                with self.assertRaises(ArtifactError):builder._build_rows(load_contract('industry_membership.v3'),[],raws)
            with self.assertRaises(ArtifactError):builder._build_rows(load_contract('industry_membership.v3'),[],raws[:-1])
