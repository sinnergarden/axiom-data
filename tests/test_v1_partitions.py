import json
import tempfile
import unittest
from pathlib import Path
from axiom_data import (ArtifactError, BuildApplication, MarketDomainBuilder,
                        load_domain_commit, write_raw_batch)


class PartitionTest(unittest.TestCase):
    def test_daily_reuses_complete_unchanged_objects_and_old_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def raw(identity, session):
                return write_raw_batch(root, identity, domain='trading_calendar',
                    source_profile='fixture', source_profile_version='fixture.v1',
                    source_profile_digest='sha256:' + 'a' * 64, request={'session': session},
                    retrieved_at='2026-09-09T00:00:00+00:00',
                    payload=json.dumps([{'exchange': 'SSE', 'session': session,
                      'is_open': True, 'previous_open_session': '2025-01-31' if identity == 'feb' else None}]).encode(),
                    collector_code='fixture').raw_batch_id
            first = raw('jan', '2025-01-31')
            second = raw('feb', '2025-02-01')
            legacy = BuildApplication('trading_calendar', MarketDomainBuilder(root, 'trading_calendar')).build(None, [first], [], 'trading_calendar.v1')
            b = BuildApplication('trading_calendar', MarketDomainBuilder(root, 'trading_calendar',
                                 builder_config={'storage_policy': 'domain_time_blocks.v1', 'no_change_policy': 'reuse_equal_state.v1'}))
            a = b.build(None, [first], [], 'trading_calendar.v1')
            repeated = b.build(a.commit_id, [first], [], 'trading_calendar.v1')
            self.assertEqual(a, repeated)
            c = b.build(a.commit_id, [second], [], 'trading_calendar.v1')
            old = load_domain_commit(root, 'trading_calendar', a.commit_id)
            new = load_domain_commit(root, 'trading_calendar', c.commit_id)
            self.assertEqual(old.manifest['partitions'][0], new.manifest['partitions'][0])
            self.assertEqual(len(new.manifest['partitions']), 2)
            self.assertEqual(len(list((root/'canonical/trading_calendar/objects').iterdir())), 2)
            self.assertFalse((root/'canonical/trading_calendar/commits'/c.commit_id/'rows.json').exists())
            self.assertEqual(old.rows, load_domain_commit(root, 'trading_calendar', legacy.commit_id).rows)
            entry = new.manifest['partitions'][0]
            (root/'canonical/trading_calendar/objects'/entry['object_id']/'rows.json').write_bytes(b'[]')
            with self.assertRaisesRegex(ArtifactError, 'partition content digest'):
                load_domain_commit(root, 'trading_calendar', c.commit_id)
