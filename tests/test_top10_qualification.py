import copy,json,tempfile,unittest
from pathlib import Path
from axiom_data import load_raw_batch,ArtifactError
from axiom_data.pr7_source import Pr7Collector,Pr7Builder,normalize,select_pr7_revisions
from axiom_data.contracts import load_contract
from axiom_data.domains.pr7 import validate_rows
from axiom_data.domains.pr6 import economic_content
from axiom_data.domains.market import MarketContractError
from axiom_data.pit import fingerprint
from test_pr6_artifacts import Client

class Top10QualificationTest(unittest.TestCase):
    def test_real_conflict_retains_raw_invalidates_aggregate_and_later_correction(self):
        case=json.loads(Path('tests/fixtures/top10_conflicting_rows.json').read_bytes())[0]
        source=case['rows']+[
            dict(case['rows'][0],holder_name='fixture '+str(i),hold_amount=100,hold_ratio=1)
            for i in range(9)]
        with tempfile.TemporaryDirectory() as root:
            def collect(rows,when):
                return load_raw_batch(root,Pr7Collector(root,Client(rows)).collect('top10_holders',
                    {'ts_code':source[0]['ts_code'],'start_date':'20140101','end_date':'20141231'},
                    retrieved_at=when).raw_batch_id)
            raw=collect(source,'2025-01-01T00:00:00Z')
            before=raw.payload
            with self.assertRaisesRegex(ArtifactError,'duplicate holder'):normalize(raw,'top_holders_reports')
            b=Pr7Builder(root,'top_holders_reports',builder_config={'top10_qualification':'top10_ambiguity.v1'})
            rows=b._build_rows(load_contract('top_holders_reports.v1'),[],[raw]);validate_rows('top_holders_reports',rows)
            row=rows[0];self.assertEqual(len(row['holders']),10)
            self.assertEqual(row['values']['top10_ratio'],None)
            self.assertEqual(row['missing_reasons']['top10_ratio'],'incomplete_report')
            h=next(h for h in row['holders'] if h['validity']=='invalid')
            self.assertEqual(h['missing_reason'],'ambiguous_source_rows')
            self.assertEqual({fingerprint(x) for x in h['source_variants']},{fingerprint(x) for x in case['rows']})
            self.assertEqual(load_raw_batch(root,raw.ref.raw_batch_id).payload,before)
            fixed=collect(source[:1]+source[2:],'2025-02-01T00:00:00Z')
            history=b._build_rows(load_contract('top_holders_reports.v1'),rows,[fixed]);validate_rows('top_holders_reports',history)
            early=select_pr7_revisions(history,policy='operational_pit_v1',knowledge_cutoff='2025-01-15T00:00:00Z')
            late=select_pr7_revisions(history,policy='operational_pit_v1',knowledge_cutoff='2025-02-15T00:00:00Z')
            self.assertIsNone(early[0]['values']['top10_ratio'])
            self.assertAlmostEqual(late[0]['values']['top10_ratio'],63.3301)
            altered=copy.deepcopy(row);bad=next(x for x in altered['holders'] if x['validity']=='invalid')
            bad['source_variants'].pop();altered['revision_id']=fingerprint(economic_content(altered))
            with self.assertRaisesRegex(MarketContractError,'ambiguous holder source closure'):
                validate_rows('top_holders_reports',[altered])

    def test_real_impossible_total_is_missing_without_rescaling(self):
        case=json.loads(Path('tests/fixtures/top10_invalid_total.json').read_bytes())
        with tempfile.TemporaryDirectory() as root:
            ref=Pr7Collector(root,Client(case['rows'])).collect('top10_holders',
                {'ts_code':case['key'][0],'start_date':'20250101','end_date':'20251231'},retrieved_at='2026-01-01T00:00:00Z')
            raw=load_raw_batch(root,ref.raw_batch_id);contract=load_contract('top_holders_reports.v1')
            old=Pr7Builder(root,'top_holders_reports')._build_rows(contract,[],[raw])
            with self.assertRaisesRegex(MarketContractError,'exceed total capital'):validate_rows('top_holders_reports',old)
            b=Pr7Builder(root,'top_holders_reports',builder_config={'top10_qualification':'top10_ambiguity.v1'})
            rows=b._build_rows(contract,[],[raw]);validate_rows('top_holders_reports',rows)
            self.assertIsNone(rows[0]['values']['top10_ratio'])
            self.assertTrue(all(h['ratio'] is None for h in rows[0]['holders']))
            self.assertAlmostEqual(sum(h['source_ratio'] for h in rows[0]['holders']),case['total'])
            self.assertEqual(json.loads(load_raw_batch(root,ref.raw_batch_id).payload),case['rows'])
