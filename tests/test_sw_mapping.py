from copy import deepcopy
import json
from pathlib import Path
import unittest
from axiom_data import ArtifactError
from axiom_data.sw_mapping import canonical_taxonomy,load_mapping_profile


class SwMappingTest(unittest.TestCase):
    def setUp(self):
        fixture=json.loads((Path(__file__).parent/'fixtures/industry_source_comparison.json').read_bytes())
        self.tax=fixture['taxonomy_rows'];self.members=fixture['members']['index_member_all']

    def test_real_mapping_keeps_raw_and_provenance(self):
        before=deepcopy(self.tax)
        result,provenance=canonical_taxonomy(self.tax,self.members)
        self.assertEqual(self.tax,before)
        self.assertEqual([r['index_code'] for r in result if r['level']=='L3'],['850412.SI'])
        row=next(r for r in result if r['level']=='L3')
        self.assertEqual(row['source_index_code'],'850401.SI')
        self.assertEqual(row['mapping_provenance'],provenance)
        self.assertEqual(provenance['automatic_conditions'],[True]*4)
        self.assertEqual(provenance['mapping_revision'],'sw2021-special-steel.v1')

    def test_conflicts_and_absent_mapping_fail(self):
        member=next(r for r in self.members if r['l3_code']=='850412.SI')
        for extra in [dict(member,l3_code='850401.SI'),dict(member,l3_name='另一个行业'),dict(member,l2_code='different')]:
            with self.assertRaises(ArtifactError):canonical_taxonomy(self.tax,self.members+[extra])
        tax=deepcopy(self.tax);next(r for r in tax if r['level']=='L3')['industry_name']='另一个行业'
        with self.assertRaises(ArtifactError):canonical_taxonomy(tax,self.members)
        profile=load_mapping_profile();profile['anomaly_mappings']=[]
        with self.assertRaises(ArtifactError):canonical_taxonomy(self.tax,self.members,profile=profile)
