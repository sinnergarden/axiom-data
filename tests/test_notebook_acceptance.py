"""Executed notebook and complete existing terminal evidence contract."""
import copy
import importlib.util
import json
from pathlib import Path
import shutil
import unittest

from axiom_data import ArtifactError
from axiom_data.artifacts import _digest
from axiom_data.full_admission import full_admission
from axiom_data.gate_a import make_gate_a_plan, validate_terminal_evidence
from axiom_data.notebook_acceptance import execute_notebook_smoke, validate_notebook_smoke
from axiom_data.recovery import verify_recovery
from test_full_admission import fixture
from test_daily_acceptance import daily_cases


@unittest.skipUnless(importlib.util.find_spec('nbclient'), 'optional Notebook runtime unavailable')
class NotebookAcceptanceTest(unittest.TestCase):
    def test_executed_fixed_smoke_and_terminal_evidence(self):
        fixture(self,daily_baseline=True,real_window=True)
        admission=full_admission(self.root,run_id='full',snapshot_id=self.snapshot,plan=self.plan)
        self.assertEqual(admission['status'],'PASS')
        daily=daily_cases(self,self.root,self.snapshot)
        notebook=execute_notebook_smoke(self.root,snapshot_id=self.snapshot,views=self.plan['view_refs'],
            target=self.plan['target'],output_path='operations/notebook/smoke.ipynb')
        self.assertEqual(validate_notebook_smoke(self.root,notebook,expected_views=self.plan['view_refs'])['status'],
                         'NOTEBOOK_SMOKE_VALIDATED')
        restored=Path(self.temp.name)/'restored';shutil.copytree(self.root,restored)
        def writable():
            for path in restored.rglob('*'):
                if path.is_dir():path.chmod(0o755)
        self.addCleanup(writable)
        recovery=verify_recovery(restored,run_id='recovery',snapshot_id=self.snapshot,
            expected_snapshot_manifest_digest=self.plan['snapshot_manifest_digest'],views=self.plan['view_refs'])
        self.assertEqual(recovery['status'],'RECOVERY_VALIDATED',recovery)
        recovery.update(snapshot_id=self.snapshot,restored_root=str(restored),content_digest=_digest((restored/'operations/recovery/recovery.json').read_bytes()))
        target_digest=admission['target_digest']
        evidence=dict(baseline=dict(snapshot_id=self.snapshot,manifest_digest=self.plan['snapshot_manifest_digest'],
            target_digest=target_digest),views=dict(snapshot_id=self.snapshot,target_digest=target_digest,refs=self.plan['view_refs']),
            full_admission=admission,daily=daily,recovery=recovery,notebook=notebook)
        result=validate_terminal_evidence(self.root,evidence,plan=make_gate_a_plan(self.plan['target'])['terminal_evidence_plan'])
        self.assertEqual(result['status'],'EVIDENCE_VALIDATED',result)
        self.assertEqual(result['gate_b_status'],'REVIEW_REQUIRED')
        self.assertFalse(result['ready_for_consumption'])
        forged=copy.deepcopy(notebook);forged['parameters']['target']['end_session']='2025-06-30'
        with self.assertRaises(ArtifactError):validate_notebook_smoke(self.root,forged,expected_views=self.plan['view_refs'])
        path=self.root/notebook['executed_notebook_path'];data=json.loads(path.read_bytes())
        data['cells'][1]['outputs']=[];path.write_text(json.dumps(data))
        forged=copy.deepcopy(notebook);forged['content_digest']=_digest(path.read_bytes())
        with self.assertRaisesRegex(ArtifactError,'output'):
            validate_notebook_smoke(self.root,forged,expected_views=self.plan['view_refs'])
        self.assertFalse((self.root/'current.json').exists())
