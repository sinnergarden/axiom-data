import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from axiom_data.cli import main


class GateACliTest(unittest.TestCase):
    def test_plan_and_validation_use_public_readonly_services(self):
        scope = dict(symbols=['688981.SH'], start_session='2014-01-01', end_session='2014-01-02',
                     financial_observation_start='2013-01-01', benchmarks=['000300.SH'], universe_ids=['000906.SH'])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)/'absent-data'; path = Path(directory)/'scope.json'
            path.write_text(json.dumps(scope))
            output = io.StringIO()
            with patch('sys.argv',['axiom-data','--data-root',str(root),'plan-gate-a','--scope',str(path)]), contextlib.redirect_stdout(output):
                self.assertEqual(main(),0)
            plan = json.loads(output.getvalue()); self.assertEqual(plan['scope'],scope)
            path.write_text(json.dumps(plan)); output = io.StringIO()
            with patch('sys.argv',['axiom-data','--data-root',str(root),'validate-gate-a','--plan',str(path)]), contextlib.redirect_stdout(output):
                self.assertEqual(main(),0)
            report = json.loads(output.getvalue())
            self.assertEqual(report['status'],'GATE_A_READY_FOR_BULK_BUILD', report['findings'])
            self.assertEqual(len(report['evidence']['requirement_bindings']),56)
            self.assertFalse(root.exists())
