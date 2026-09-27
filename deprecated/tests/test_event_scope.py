from axiom_data.contracts import writable_contracts
import json
import unittest
from importlib.resources import files
from axiom_data.event_views import LEAF_DOMAINS
from axiom_data.contracts import load_contract

class EventScopeTest(unittest.TestCase):
    def test_exact_partition_and_public_owners(self):
        scope=json.loads(files('axiom_data.scope').joinpath('data_dependency_scope.v1.json').read_bytes())
        parts={k:set(v) for k,v in scope['partition'].items()}
        self.assertEqual([len(parts[k]) for k in ('reference evidence','financial','event')],[18,23,15])
        self.assertEqual(len(set.union(*parts.values())),56)
        self.assertFalse(parts['reference evidence']&parts['financial']|parts['reference evidence']&parts['event']|parts['financial']&parts['event'])
        self.assertEqual(parts['event'],set(LEAF_DOMAINS))
        self.assertEqual(len(scope['feature_dependencies']),469)
        for leaf,domain in LEAF_DOMAINS.items():self.assertIn(leaf.split('.')[1],load_contract(writable_contracts()["domains"][domain]["current"])['value_units'])
        for f in scope['feature_dependencies']:self.assertLessEqual(set(f['leaves']),set.union(*parts.values()))
