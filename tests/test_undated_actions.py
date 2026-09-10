import json,tempfile,unittest,shutil
from pathlib import Path
from axiom_data import BuildApplication,load_raw_batch,validate_domain_commit_closure,SnapshotReader,ArtifactError
from axiom_data.dm1_source import TushareDm1Builder,TushareDm1Collector
from axiom_data.artifacts import _validate_domain_rows
from axiom_data.contracts import load_contract
from test_artifacts import build_pack
from test_pr6_artifacts import Client

class UndatedActionsTest(unittest.TestCase):
    def test_real_observations_new_root_and_fail_closed_date_projection(self):
        source=[x['row'] for x in json.loads(Path('tests/fixtures/undated_corporate_actions.json').read_bytes())]
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);pack=build_pack(root)
            raw=[TushareDm1Collector(root,Client([row])).collect('corporate_actions','dividend',{'ts_code':row['ts_code']},
                retrieved_at='2026-09-10T00:00:00Z') for row in source]
            deps={'trading_calendar':pack['calendar'].commit_id,'security_master':pack['security'].commit_id}
            cfg=dict(symbols=[r['ts_code'] for r in source],start_session='2014-01-01',end_session='2026-09-08',storage_policy='domain_time_blocks.v1')
            with self.assertRaisesRegex(ArtifactError,'requires ex_date'):
                BuildApplication('corporate_actions',TushareDm1Builder(root,'corporate_actions',dependency_commit_ids=deps,builder_config=cfg)).build(None,[r.raw_batch_id for r in raw],[],'corporate_actions.v1')
            cfg['corporate_action_observations']='corporate_action_observations.v1'
            def build(parent=None,version='corporate_actions.v2'):
                return BuildApplication('corporate_actions',TushareDm1Builder(root,'corporate_actions',dependency_commit_ids=deps,builder_config=cfg)).build(parent,[r.raw_batch_id for r in raw],[],version)
            ref=build();commit=validate_domain_commit_closure(root,'corporate_actions',ref.commit_id)
            self.assertEqual({r['observation_state'] for r in commit.rows},{'undated_action','unresolved_terms'})
            self.assertTrue(all(r['effective_date'] is None for r in commit.rows))
            self.assertEqual({p['key'] for p in commit.manifest['partitions']},{'undated'})
            self.assertEqual(json.loads(load_raw_batch(root,raw[0].raw_batch_id).payload),[source[0]])
            reader=SnapshotReader.__new__(SnapshotReader);reader.commits={'corporate_actions':commit}
            self.assertEqual(len(reader.facts('corporate_actions')),2)
            with self.assertRaisesRegex(ArtifactError,'INSUFFICIENT_SCOPE'):
                reader.facts('corporate_actions',symbols=[source[0]['ts_code']],start_session='2025-01-01',end_session='2025-01-31')
            with self.assertRaises(ArtifactError):_validate_domain_rows('corporate_actions',commit.rows,contract=load_contract('corporate_actions.v1'))
            with self.assertRaises(ArtifactError):build(version='corporate_actions.v1')
            # Rebuilding this explicit root never substitutes a record/share date.
            self.assertEqual(build().commit_id,ref.commit_id)
            # Separate synthetic dated input verifies the version boundary.
            dated=dict(source[0],ex_date='20250102')
            dated_raw=TushareDm1Collector(root,Client([dated])).collect('corporate_actions','dividend',
                {'ts_code':dated['ts_code']},retrieved_at='2026-09-10T01:00:00Z')
            old_cfg={k:v for k,v in cfg.items() if k!='corporate_action_observations'}
            old_cfg['symbols']=[dated['ts_code']]
            legacy=BuildApplication('corporate_actions',TushareDm1Builder(root,'corporate_actions',
                dependency_commit_ids=deps,builder_config=old_cfg)).build(None,[dated_raw.raw_batch_id],[],'corporate_actions.v1')
            with self.assertRaises(ArtifactError):build(parent=legacy.commit_id)
            self.assertNotIn('observation_state',validate_domain_commit_closure(root,'corporate_actions',legacy.commit_id).rows[0])
