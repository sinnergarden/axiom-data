import json
import unittest
from importlib.resources import files
from axiom_data.pr7_views import LEAF_DOMAINS
from axiom_data.contracts import load_contract

class Pr7ScopeTest(unittest.TestCase):
    def test_exact_partition_and_public_owners(self):
        scope=json.loads(files('axiom_data.scope').joinpath('pr7_scope.v1.json').read_bytes())
        parts={k:set(v) for k,v in scope['partition'].items()}
        self.assertEqual([len(parts[k]) for k in ('PR5','PR6','PR7')],[18,23,15])
        self.assertEqual(len(set.union(*parts.values())),56)
        self.assertFalse(parts['PR5']&parts['PR6']|parts['PR5']&parts['PR7']|parts['PR6']&parts['PR7'])
        self.assertEqual(parts['PR7'],set(LEAF_DOMAINS))
        self.assertEqual(len(scope['feature_dependencies']),469)
        for leaf,domain in LEAF_DOMAINS.items():self.assertIn(leaf.split('.')[1],load_contract(domain+'.v1')['value_units'])
        for f in scope['feature_dependencies']:self.assertLessEqual(set(f['leaves']),set.union(*parts.values()))
