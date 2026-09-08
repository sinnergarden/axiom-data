"""Long daily lineage: traversal is iterative and memoization is per verification."""
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from axiom_data import ArtifactError, DomainCommit, DomainCommitRef
from axiom_data.artifacts import _commit_ref, _validate_domain_commit_closure


def chain(count):
    commits = {}
    parent = None
    for n in range(count):
        identity = 'calendar-' + str(n)
        manifest = {'contract_digest': 'c', 'identity_digest': identity,
                    'logical_content_digest': 'l',
                    'parent_commit_ref': _commit_ref(parent) if parent else None,
                    'ordered_raw_batch_refs': [], 'ordered_patch_refs': [],
                    'dependency_commit_refs': {}}
        parent = DomainCommit(DomainCommitRef('trading_calendar', identity,
                              'trading_calendar.v1'), 'm', manifest, {}, ())
        commits[identity] = parent
    return commits, parent


class LineageTest(unittest.TestCase):
    def test_2501_chain_and_repeated_ref_cache(self):
        commits, head = chain(2501)
        cache, raw_cache = {}, {}
        start = time.perf_counter()
        with patch('axiom_data.artifacts.load_domain_commit',
                   side_effect=lambda root, domain, identity: commits[identity]) as load:
            for _ in range(2):
                result = _validate_domain_commit_closure(Path('/tmp'), 'trading_calendar',
                    head.ref.commit_id, set(), cache, raw_cache)
                self.assertIs(result, head)
            self.assertEqual(load.call_count, 2501)
        self.assertEqual(len(cache), 2501)
        self.assertTrue(all(value == frozenset() for value in raw_cache.values()))
        self.assertLess(time.perf_counter() - start, 10)

    def test_cycle_rejected_and_active_state_cleaned(self):
        commits, head = chain(3)
        commits['calendar-0'].manifest['parent_commit_ref'] = _commit_ref(head)
        active = set()
        with patch('axiom_data.artifacts.load_domain_commit',
                   side_effect=lambda root, domain, identity: commits[identity]):
            with self.assertRaisesRegex(ArtifactError, 'cycle'):
                _validate_domain_commit_closure(Path('/tmp'), 'trading_calendar',
                    head.ref.commit_id, active, {}, {})
        self.assertEqual(active, set())

    def test_parent_integrity_and_contract_checks_remain(self):
        for key, value in [('identity_digest', 'forged'), ('contract_version', 'trading_calendar.v9')]:
            commits, head = chain(2)
            head.manifest['parent_commit_ref'][key] = value
            with patch('axiom_data.artifacts.load_domain_commit',
                       side_effect=lambda root, domain, identity: commits[identity]):
                with self.assertRaisesRegex(ArtifactError, 'parent ref'):
                    _validate_domain_commit_closure(Path('/tmp'), 'trading_calendar',
                        head.ref.commit_id, set(), {}, {})
