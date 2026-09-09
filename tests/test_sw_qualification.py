"""Real supplier projections exercise qualification, independently of artifact loading."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import unittest
from axiom_data.sw_qualification import inspect_sw_pilot
from axiom_data import ArtifactError


class SwQualificationTest(unittest.TestCase):
    def test_real_stable_history_gaps_fail_pilot(self):
        fixture=json.loads((Path(__file__).parent/'fixtures/sw2021_pilot.json').read_bytes())
        by_id={r['original_raw_ref']:SimpleNamespace(manifest=r['manifest'],
                 payload=json.dumps(r['rows']).encode(),ref=SimpleNamespace(manifest_digest='fixture-projection'))
               for r in fixture['source_observations']+fixture['calendar_observations']}
        source=[r['original_raw_ref'] for r in fixture['source_observations']]
        calendars=[r['original_raw_ref'] for r in fixture['calendar_observations']]
        with patch('axiom_data.sw_qualification.load_raw_batch',side_effect=lambda root,rid:by_id[rid]):
            report=inspect_sw_pilot('/tmp/sw-qualification-fixture',source,symbols=fixture['symbols'],calendar_raw_batch_ids=calendars,start_session='2014-01-01',end_session='2026-09-08')
        self.assertEqual(report['pilot_decision'],'REJECTED')
        self.assertEqual(report['taxonomy_counts'],{'L1':31,'L2':134,'L3':346})
        expected={'000506.SZ':1000,'000975.SZ':119,'001289.SZ':14}
        for symbol,count in expected.items():
            with self.subTest(symbol=symbol):
                row=report['security_results'][symbol]
                self.assertEqual(len(row['gap_open_dates_assuming_inclusive_out']),count)
                self.assertTrue(row['default_equals_Y'])
                self.assertTrue(all(row['repeat_stability'].values()))
                self.assertFalse(row['taxonomy_unknown_codes'])
                self.assertFalse(row['overlap_open_dates'])
        self.assertEqual(report['security_results']['001289.SZ']['first_in_date'],'20220218')
        self.assertEqual(report['canonical_admission'],'NOT_ADMITTED')

        calendar = by_id[calendars[0]]
        calendar.payload = json.dumps(json.loads(calendar.payload)[1:]).encode()
        with patch('axiom_data.sw_qualification.load_raw_batch',side_effect=lambda root,rid:by_id[rid]):
            with self.assertRaisesRegex(ArtifactError, 'completely cover'):
                inspect_sw_pilot('/tmp/sw-qualification-fixture',source,symbols=fixture['symbols'],
                                 calendar_raw_batch_ids=calendars,start_session='2014-01-01',end_session='2026-09-08')
