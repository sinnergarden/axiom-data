import hashlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from axiom_data import ArtifactError, plan_daily
from axiom_data.cli import main
from axiom_data.layout import DataRootLayout


class DailyPlanTest(unittest.TestCase):
    def test_explicit_dates_t_plus_one_parent_binding_and_no_writes(self):
        run = json.loads(Path('reports/pr7/run_manifest.json').read_bytes())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)/'data'
            shutil.copytree(run['source_root'], root)
            pointer = DataRootLayout(root).current_pointer
            pointer.parent.mkdir(parents=True, exist_ok=True)
            pointer.write_text(json.dumps({'snapshot_id':run['refs']['snapshot_id']}))
            specs = []
            for domain, endpoint, day, policy in [('market_daily','daily','20250613','session_close'),
                ('margin_daily','margin_detail','20250612','next_session_publication')]:
                specs.append(dict(collector='market' if domain=='market_daily' else 'pr7', domain=domain,
                    endpoint=endpoint, params=dict(ts_code='688981.SH', start_date=day, end_date=day),
                    economic_scope=dict(start=day, end=day), availability_policy=policy))
            def contents():
                return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
            before = contents()
            result = plan_daily(root, 'current', source_requests=specs)
            self.assertEqual(result['parent_snapshot_id'], run['refs']['snapshot_id'])
            self.assertEqual(result['source_requests'][1]['economic_scope']['end'], '20250612')
            self.assertTrue(result['source_requests'][1]['expected_t_plus_one'])
            self.assertEqual(result['source_requests'][0]['availability_state'], 'UNCONFIRMED_UNTIL_COLLECTION')
            self.assertIn('security_status', result['dependency_review_domains'])
            self.assertFalse(result['ready_for_consumption'])
            self.assertEqual(contents(), before)
            request_path = Path(directory)/'requests.json'; request_path.write_text(json.dumps(specs))
            output = io.StringIO()
            with patch('sys.argv', ['axiom-data','--data-root',str(root),'plan-daily','--snapshot','current','--plan',str(request_path)]), patch('sys.stdout',output):
                self.assertEqual(main(), 0)
            self.assertEqual(json.loads(output.getvalue()), result)
            self.assertEqual(contents(), before)
            bad = json.loads(json.dumps(specs)); bad[1]['availability_policy'] = 'session_close'
            with self.assertRaisesRegex(ArtifactError, 'availability policy'):
                plan_daily(root, 'current', source_requests=bad)
            with self.assertRaisesRegex(ArtifactError, 'duplicate'):
                plan_daily(root, 'current', source_requests=specs+specs)
            bad[1]['params']['token'] = 'not-a-real-secret'
            with self.assertRaises(ArtifactError):plan_daily(root, 'current', source_requests=bad)
            self.assertEqual(contents(), before)
