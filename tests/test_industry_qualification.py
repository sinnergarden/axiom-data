import unittest
import json
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
from axiom_data import ArtifactError
from axiom_data.industry_qualification import score_history, runs, validate_pages
from axiom_data.sw_source import validate_request


class IndustryQualificationTest(unittest.TestCase):
    def test_real_full_source_counterexamples(self):
        fixture=json.loads((Path(__file__).parent/'fixtures/industry_source_comparison.json').read_bytes())
        results={ep:score_history(rows,stock=fixture['stock'],sessions=fixture['calendar'],
                 start=fixture['scope']['start'],end=fixture['scope']['end']) for ep,rows in fixture['members'].items()}
        for ep,expected in [('index_member_all',[1000,119,14]),('ci_index_member',[1442,1442,21])]:
            self.assertEqual([results[ep]['security_results'][s]['gap_sessions'] for s in
                             ['000506.SZ','000975.SZ','001289.SZ']],expected)
        self.assertEqual(results['index_member_all']['totals']['conflict_sessions'],0)
        self.assertEqual(results['ci_index_member']['security_results']['000615.SZ']['conflict_sessions'],14)
        # Keep the supplier contradiction visible; do not invent an alias from names.
        self.assertEqual([r['index_code'] for r in fixture['taxonomy_rows'] if r['level']=='L3'],['850401.SI'])
        self.assertEqual({r['l3_code'] for r in fixture['members']['index_member_all'] if r['ts_code']=='000708.SZ'}, {'850412.SI'})
        self.assertEqual(fixture['delist_evidence'],[{'symbol':'600687.SH','supplier':'20210303','exchange':'20210304','match':False}])

    def test_gap_boundary_conflict_and_exchange_fail_closed(self):
        row=dict(ts_code='000506.SZ',in_date='20240102',out_date='20240103',is_new='N',
                 l1_code='A',l1_name='a',l2_code='B',l2_name='b',l3_code='C',l3_name='c')
        stock={'000506.SZ':dict(exchange='SZSE',list_date='20240102',delist_date=None,list_status='L')}
        days=['20240102','20240103','20240104','20240105']
        second=dict(row,in_date='20240105',out_date=None,is_new='Y')
        result=score_history([row,second],stock=stock,sessions={'SZSE':days},start='2024-01-02',end='2024-01-05')
        t=result['totals'];self.assertEqual((t['classified_sessions'],t['ambiguous_sessions'],t['gap_sessions']),(2,1,1))
        self.assertEqual(result['security_results']['000506.SZ']['gap_runs'],[{'from':'20240104','to':'20240104','sessions':1}])
        with self.assertRaises(ArtifactError):score_history([row],stock=stock,sessions={'SSE':days},start='2024-01-02',end='2024-01-05')
        conflict=dict(second,l3_code='D',l3_name='d')
        self.assertEqual(score_history([row,second,conflict],stock=stock,sessions={'SZSE':days},start='2024-01-02',end='2024-01-05')['totals']['conflict_sessions'],1)
        self.assertEqual(runs(days,set(days)),[{'from':days[0],'to':days[-1],'sessions':4}])

    def test_page_validation_requires_terminal_and_preserves_request(self):
        profile='tushare_industry_qualification.v1'
        validate_request('ci_index_member',{'limit':'5','offset':'0','is_new':'N'},profile_version=profile)
        for params in ({'limit':'5'},{'limit':'5001','offset':'0'},{'l3_code':'851617.SI'},{'limit':'0','offset':'0'}):
            with self.assertRaises(ArtifactError):validate_request('ci_index_member',params,profile_version=profile)
        entries=[dict(endpoint='ci_index_member',mode='Y',repeat=1,offset=0,limit=1,rows=1,raw_batch_id='a',manifest_digest='digest')]
        def observed(root,identity):
            offset=0 if identity=='a' else 1
            return SimpleNamespace(manifest={'request':{'endpoint':'ci_index_member','params':{'is_new':'Y','offset':str(offset),'limit':'1'}}},ref=SimpleNamespace(manifest_digest='digest')),([{'a':1}] if identity=='a' else [])
        with patch('axiom_data.industry_qualification.observation',side_effect=observed):
            with self.assertRaisesRegex(ArtifactError,'terminal'):validate_pages('root',entries,endpoint='ci_index_member',mode='Y',repeat=1)
            entries.append(dict(entries[0],offset=1,rows=0,raw_batch_id='b'))
            self.assertEqual(validate_pages('root',entries,endpoint='ci_index_member',mode='Y',repeat=1),[{'a':1}])
