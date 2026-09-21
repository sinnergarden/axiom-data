"""Financial ties lose only conflicting leaves; every source remains attributable."""
import copy
import unittest
from axiom_data.pit import (AMBIGUOUS_SOURCE_REVISION as AMBIGUOUS, select_revisions,
    select_financial_revisions, financial_ambiguities, financial_derived)
from axiom_data.domains.market import MarketContractError
from test_pr6_pit import fact, POLICY, CUTOFF

class FinancialLeafAmbiguityTest(unittest.TestCase):
    def pair(self):
        a=fact('2024-06-30',300)
        b=copy.deepcopy(a);b.update(revision_id='conflict',source_ref='raw-conflict')
        b['values']['revenue']=None
        return [a,b]

    def selected(self,rows,cutoff=CUTOFF):
        return select_financial_revisions(rows,policy=POLICY,knowledge_cutoff=cutoff)

    def test_only_differing_leaves_unavailable_without_winner(self):
        rows=self.pair();before=copy.deepcopy(rows)
        actual=self.selected(rows)[0]
        self.assertEqual(actual['missing_reasons'],{'revenue':AMBIGUOUS})
        self.assertIsNone(actual['values']['revenue'])
        self.assertEqual(actual['values']['oper_cost'],150)
        self.assertEqual(actual['values']['net_income'],30)
        self.assertIsNone(actual['revision_id']);self.assertIsNone(actual['source_ref'])
        self.assertEqual({x['revision_id'] for x in actual['component_revisions']},{r['revision_id'] for r in rows})
        self.assertEqual(self.selected(rows),self.selected(list(reversed(rows))))
        self.assertEqual(rows,before)
        with self.assertRaisesRegex(MarketContractError,'ambiguous simultaneous'):
            select_revisions(rows,policy=POLICY,knowledge_cutoff=CUTOFF)

    def test_identical_values_and_nulls_keep_source_tie_evidence(self):
        rows=self.pair();rows[0]['values']['revenue']=None
        actual=self.selected(rows)[0]
        self.assertEqual(actual['ambiguous_fields'],[])
        self.assertEqual(actual['missing_reasons']['revenue'],'source_value_missing')
        self.assertEqual(len(financial_ambiguities(rows,policy=POLICY,knowledge_cutoff=CUTOFF)),1)

    def test_intervals_and_prefix_do_not_leak_future_resolution(self):
        rows=self.pair()
        for row in rows:row['first_observed_at']='2025-02-01T00:00:00Z'
        initial=fact('2024-06-30',200,observed='2025-01-01T00:00:00Z',revision='initial')
        later=fact('2024-06-30',400,observed='2025-04-01T00:00:00Z',revision='later')
        before='2025-03-01T00:00:00Z'
        for func in (self.selected,lambda rs,t:financial_ambiguities(rs,policy=POLICY,knowledge_cutoff=t)):
            self.assertEqual(func([initial,*rows],before),func([initial,*rows,later],before))
        intervals=financial_ambiguities([initial,*rows,later],policy=POLICY,knowledge_cutoff=CUTOFF)
        self.assertEqual(len(intervals),1)
        self.assertEqual(intervals[0]['from'],'2025-02-01T00:00:00+00:00')
        self.assertEqual(intervals[0]['to_exclusive'],'2025-04-01T00:00:00+00:00')
        self.assertEqual(self.selected([initial,*rows,later])[0]['values']['revenue'],400)

    def test_observation_tie_and_aba_use_existing_order(self):
        rows=self.pair()
        for i,row in enumerate(rows):
            row['observations']=[dict(observation_id=str(i),observed_at='2025-02-01T00:00:00Z',
                vendor_available_at='2024-08-01T00:00:00Z',source_ref=row['source_ref'])]
        resolved=self.selected(rows)[0]
        self.assertEqual(resolved['ambiguous_fields'],['revenue'])
        rows[0]['observations'].append(dict(rows[0]['observations'][0],observation_id='return',observed_at='2025-04-01T00:00:00Z'))
        self.assertEqual(self.selected(rows)[0]['values']['revenue'],300)
        self.assertEqual(len(financial_ambiguities(rows,policy=POLICY,knowledge_cutoff=CUTOFF)),1)

    def test_derived_propagates_only_affected_field_and_all_sources(self):
        rows=[fact(p,v) for p,v in zip(['2024-03-31','2024-06-30','2024-09-30','2024-12-31'],[100,300,600,1000])]
        conflict=copy.deepcopy(rows[1]);conflict.update(revision_id='conflict',source_ref='raw-conflict');conflict['values']['revenue']=301
        actual=financial_derived([*rows,conflict],policy=POLICY,knowledge_cutoff=CUTOFF)
        by={(r['report_period'],r['field']):r for r in actual}
        for period in ('2024-06-30','2024-09-30'):
            row=by[period,'single_quarter_revenue']
            self.assertIsNone(row['value']);self.assertEqual(row['missing_reason'],AMBIGUOUS)
            self.assertIn('conflict',{r['revision_id'] for r in row['component_revisions']})
            self.assertIsNotNone(by[period,'single_quarter_oper_cost']['value'])
        self.assertEqual(by['2024-12-31','ttm_revenue']['missing_reason'],AMBIGUOUS)
        self.assertEqual(by['2024-12-31','ttm_net_income']['value'],100)
        self.assertEqual(by['2024-12-31','ttm_revenue']['contract_version'],'financial_stable.v3')

    def test_verified_remains_fail_closed(self):
        rows=self.pair();rows[1]['pit_qualification']='verified'
        with self.assertRaisesRegex(MarketContractError,'VERIFIED'):
            self.selected(rows)
