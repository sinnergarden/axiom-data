from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from axiom_data.artifacts import ArtifactError,_validate_dm1_observation_refs


class ObservationValidationCacheTest(unittest.TestCase):
    def test_repeated_refs_verify_once_per_call_and_never_across_calls(self):
        raw=SimpleNamespace(ref=SimpleNamespace(raw_batch_id='raw'),manifest={'domain':'financial_events','retrieved_at':'2025-01-01T00:00:00Z'})
        rows=[{'source_ref':'raw','first_observed_at':'2025-01-02T00:00:00Z'}]*1000
        with patch('axiom_data.artifacts.load_raw_batch',return_value=raw) as load:
            _validate_dm1_observation_refs(Path('/tmp'),'financial_events',rows,{'raw'})
            self.assertEqual(load.call_count,1)
            _validate_dm1_observation_refs(Path('/tmp'),'financial_events',rows,{'raw'})
            self.assertEqual(load.call_count,2)
        with patch('axiom_data.artifacts.load_raw_batch',side_effect=ArtifactError('corrupt raw')):
            with self.assertRaises(ArtifactError):_validate_dm1_observation_refs(Path('/tmp'),'financial_events',rows,{'raw'})
        with patch('axiom_data.artifacts.load_raw_batch',return_value=raw):
            with self.assertRaisesRegex(ArtifactError,'predates'):
                _validate_dm1_observation_refs(Path('/tmp'),'financial_events',[dict(rows[0],first_observed_at='2024-01-01T00:00:00Z')],{'raw'})
            with self.assertRaisesRegex(ArtifactError,'outside'):
                _validate_dm1_observation_refs(Path('/tmp'),'financial_events',rows,set())
