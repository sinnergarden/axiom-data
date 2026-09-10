"""Domain-contract gaps block admission even when both date endpoints exist."""
import unittest
from types import SimpleNamespace
from axiom_data.artifacts import ArtifactError
from axiom_data.pr6_coverage import admit_view
from axiom_data.pit import financial_derived
from test_pr6_pit import span,fact,POLICY,CUTOFF


class CoverageTest(unittest.TestCase):
    def test_middle_date_and_security_gaps(self):
        days=['2025-01-01','2025-01-02','2025-01-03'];symbol='600000.SH'
        membership=span(days[0],'2025-01-04','universe')
        industry=[span(d,'2025-01-0'+str(i+2),'industry'+d) for i,d in enumerate(days)]
        reader=SimpleNamespace(commits={d:SimpleNamespace(rows=rows,ref=SimpleNamespace(contract_version=d+'.v1'),manifest={'builder_config':{'symbols':[symbol]}}) for d,rows in {
            'trading_calendar':[{'session':d,'exchange':'SSE','is_open':True} for d in days],
            'universe_membership':[membership],'industry_membership':industry,
            'valuation_daily':[{'symbol':symbol,'session':d} for d in days],
            'financial_events':[dict(fact('2024-03-31',1),endpoint=e,logical_event_key=e) for e in ('income','balancesheet','cashflow','fina_indicator')]}.items()})
        scope={'symbols':[symbol],'start_session':days[0],'end_session':days[-1],
               'universe_ids':['test'],'industry_system':'test'}
        admitted=admit_view(reader,scope,POLICY,CUTOFF)
        self.assertTrue(admitted)
        future=dict(fact('2024-06-30',3,observed='2025-07-01T00:00:00Z'),logical_event_key='future-income')
        reader.commits['financial_events'].rows.append(future)
        self.assertEqual(admit_view(reader,scope,POLICY,CUTOFF),admitted)
        reader.commits['financial_events'].rows.pop()
        for domain in ('trading_calendar','valuation_daily','industry_membership'):
            rows=reader.commits[domain].rows
            reader.commits[domain].rows=[rows[0],rows[-1]]
            with self.assertRaisesRegex(ArtifactError,'INSUFFICIENT_SCOPE'):admit_view(reader,scope,POLICY,CUTOFF)
            reader.commits[domain].rows=rows
        reader.commits['valuation_daily'].rows[1]['symbol']='600001.SH'
        with self.assertRaisesRegex(ArtifactError,'INSUFFICIENT_SCOPE'):admit_view(reader,scope,POLICY,CUTOFF)

    def test_distinct_ttm_missing_reasons_and_components(self):
        periods=['2024-03-31','2024-06-30','2024-09-30','2024-12-31']
        rows=[fact(p,v) for p,v in zip(periods,[100,300,600,1000])]
        def ttm(rows):
            return next(r for r in financial_derived(rows,policy=POLICY,knowledge_cutoff=CUTOFF)
                        if r['field']=='ttm_revenue' and r['report_period']=='2024-12-31')
        self.assertEqual(ttm(rows)['value'],1000)
        self.assertEqual(ttm([r for r in rows if r['report_period']!=periods[1]])['missing_reason'],'missing_quarter')
        rows[1]['first_observed_at']='2025-07-01T00:00:00Z'
        hidden=ttm(rows);self.assertEqual(hidden['missing_reason'],'missing_quarter')
        self.assertTrue(hidden['component_revisions']);self.assertEqual(hidden['expected_quarters'],periods)
        rows[1]['first_observed_at']='2025-01-01T00:00:00Z';rows[1]['values']['revenue']=None
        self.assertEqual(ttm(rows)['missing_reason'],'source_value_missing')
        for row in rows:row['report_type']='2'
        self.assertEqual(ttm(rows)['missing_reason'],'incompatible_report_type')
