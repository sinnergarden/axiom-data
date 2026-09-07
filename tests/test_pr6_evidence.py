import copy
import json
import unittest
from pathlib import Path
from axiom_data.artifacts import ArtifactError
from axiom_data.evidence import validate_pr6_evidence


class Pr6EvidenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report=json.loads((Path(__file__).resolve().parents[1]/'reports/pr6-empty-prefix-compat/run_manifest.json').read_text())

    def validate(self,report):
        return validate_pr6_evidence(report,data_root=self.report['data_root'],offline_root=self.report['offline_root'])

    def test_actual_artifacts_and_offline_recovery_validate(self):
        self.assertTrue(self.validate(self.report))

    def test_published_v1_and_v2_evidence_remain_readable(self):
        for name in ('pr6','pr6-review'):
            report=json.loads((Path(__file__).resolve().parents[1]/'reports'/name/'run_manifest.json').read_text())
            self.assertTrue(validate_pr6_evidence(report,data_root=report['data_root'],offline_root=report['offline_root']))

    def test_coordinated_fake_refs_and_root_substitution_fail(self):
        fake=copy.deepcopy(self.report)
        for key in ('artifact_refs','offline_artifact_refs'):
            fake[key]['snapshot']['snapshot_id']='snapshot-'+'0'*64
            fake[key]['view']['view_id']='pr6-fact-'+'0'*64
        with self.assertRaises(ArtifactError):self.validate(fake)
        fake=copy.deepcopy(self.report);fake['data_root']=fake['offline_root']
        with self.assertRaises(ArtifactError):self.validate(fake)

    def test_coordinated_pass_claims_cannot_replace_measurements(self):
        fake=copy.deepcopy(self.report)
        fake['reconciliation']['direct_qlib_comparisons']+=1
        fake['gates']={k:True for k in fake['gates']}
        with self.assertRaisesRegex(ArtifactError,'reconciliation'):
            self.validate(fake)
