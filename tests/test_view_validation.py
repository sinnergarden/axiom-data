"""Dependency validation stays explicit and never substitutes for full acceptance."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from axiom_data import ArtifactError, SnapshotReader
from axiom_data import artifacts
from axiom_data.consumption import request_coverage
from axiom_data.domains import PR7_DOMAINS
from axiom_data.pr7_views import project
from axiom_data.view_validation import ViewValidationSession
from fixture_locations import fixture_root


class ViewValidationTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)/'data'
        run=json.loads(Path('reports/pr7/run_manifest.json').read_bytes())
        shutil.copytree(fixture_root(run['source_root']),self.root)
        self.snapshot=run['refs']['snapshot_id']
        self.config=dict(symbols=['688981.SH'],start_session='2025-06-10',end_session='2025-06-13',
            pit_policy='best_effort_vendor_v1',knowledge_cutoff='2025-06-13T23:59:59+08:00')
        self.session=ViewValidationSession(self.root,self.snapshot)

    def tearDown(self):
        for path in self.root.rglob('*'):
            if path.is_dir():path.chmod(0o755)
        self.temp.cleanup()

    def project(self,reader):
        scope={k:self.config[k] for k in ('symbols','start_session','end_session')}
        return project(reader,scope,self.config['pit_policy'],self.config['knowledge_cutoff'])

    def corrupt(self,domain):
        manifest=json.loads((self.root/'snapshots'/self.snapshot/'manifest.json').read_bytes())
        commit=manifest['domain_refs'][domain]['domain_commit_id']
        path=self.root/'canonical'/domain/'commits'/commit/'rows.json'
        path.chmod(0o600);path.write_bytes(path.read_bytes()+b' ')

    def test_exact_result_and_no_unrelated_domains_or_repeat_load(self):
        full=self.project(SnapshotReader(self.root,self.snapshot))
        with patch.object(artifacts,'load_domain_commit',wraps=artifacts.load_domain_commit) as loads:
            with self.session.inputs('pr7_fact',self.config) as reader:
                self.assertEqual(self.project(reader),full)
                self.assertNotIn('financial_events',reader.commits)
            domains={c.args[1] for c in loads.call_args_list}
            self.assertEqual(domains,{'security_master','trading_calendar',*PR7_DOMAINS})
            loads.reset_mock()
            with self.session.inputs('pr7_fact',self.config) as reader:
                self.assertEqual(self.project(reader),full)
            self.assertEqual(loads.call_count,0)

    def test_unrelated_corruption_still_rejected_by_full_load(self):
        self.corrupt('financial_events')
        with self.session.inputs('pr7_fact',self.config) as reader:self.project(reader)
        with self.assertRaises(ArtifactError):SnapshotReader(self.root,self.snapshot)

    def test_new_projection_scope_reuses_full_closure_but_rechecks_request(self):
        with self.session.inputs('pr7_fact',self.config):pass
        changed=dict(self.config,symbols=['000001.SZ'],start_session='2025-06-11')
        with patch.object(artifacts,'load_domain_commit',wraps=artifacts.load_domain_commit) as loads:
            with self.session.inputs('pr7_fact',changed):pass
            self.assertEqual(loads.call_count,0)
        config={k:self.config[k] for k in ('symbols','start_session','end_session')}
        with self.session.inputs('market_qlib',config):pass
        with patch.object(artifacts,'load_domain_commit',wraps=artifacts.load_domain_commit) as loads:
            with self.assertRaisesRegex(ArtifactError,'reversed'):
                with self.session.inputs('market_qlib',dict(config,start_session='2025-06-14')):pass
            self.assertEqual(loads.call_count,0)

    def test_dependency_changes_invalidate_cached_validation(self):
        with self.session.inputs('pr7_fact',self.config):pass
        self.corrupt('holder_count_events')
        with self.assertRaises(ArtifactError):
            with self.session.inputs('pr7_fact',self.config):pass

    def test_policy_change_revalidates_and_mid_use_change_fails(self):
        with self.session.inputs('pr7_fact',self.config):pass
        changed=dict(self.config,pit_policy='operational_pit_v1')
        with patch.object(artifacts,'load_domain_commit',wraps=artifacts.load_domain_commit) as loads:
            with self.session.inputs('pr7_fact',changed):pass
            self.assertGreater(loads.call_count,0)
        with self.assertRaisesRegex(ArtifactError,'changed during use'):
            with self.session.inputs('pr7_fact',changed):self.corrupt('holder_count_events')

    def test_snapshot_manifest_change_and_symlink_invalidate(self):
        with self.session.inputs('pr7_fact',self.config):pass
        path=self.root/'snapshots'/self.snapshot/'manifest.json'
        original=path.read_bytes();path.chmod(0o600);path.write_bytes(original+b' ')
        with self.assertRaises(ArtifactError):
            with self.session.inputs('pr7_fact',self.config):pass
        path.write_bytes(original)
        with self.session.inputs('pr7_fact',self.config):pass
        path.parent.chmod(0o755);path.unlink();path.symlink_to('/tmp/nonexistent-view-validation-manifest')
        with self.assertRaises(ArtifactError):
            with self.session.inputs('pr7_fact',self.config):pass

    def test_raw_payload_change_invalidates_and_rechecks(self):
        with self.session.inputs('pr7_fact',self.config) as reader:
            raw=reader.commits['holder_count_events'].manifest['ordered_raw_batch_refs'][0]['raw_batch_id']
        path=self.root/'raw/batches'/raw/'payload.bin'
        self.assertTrue(path.exists())
        path.chmod(0o600);path.write_bytes(path.read_bytes()+b' ')
        with self.assertRaises(ArtifactError):
            with self.session.inputs('pr7_fact',self.config):pass

    def test_initialization_provenance_reuses_checked_raw(self):
        original = artifacts._observation_rows
        checks = []
        def checked(root, domain, rows, raw_ids, **kwargs):
            with patch.object(artifacts, 'load_raw_batch', wraps=artifacts.load_raw_batch) as loads:
                yield from original(root, domain, rows, raw_ids, **kwargs)
                checks.append((domain, loads.call_count))
        with patch.object(artifacts, '_observation_rows', checked):
            with self.session.inputs('pr7_fact', self.config):
                pass
        self.assertTrue(checks)
        self.assertTrue(all(count == 0 for _, count in checks), checks)

    def test_ordinary_event_request_uses_bound_raw_metadata_once(self):
        reader=SnapshotReader(self.root,self.snapshot)
        with patch('axiom_data.artifacts._payload_file',side_effect=AssertionError('unused Raw payload')):
            with patch('axiom_data.consumption._load_raw_manifest',
                       wraps=artifacts._load_raw_manifest) as metadata:
                request_coverage(reader,'margin_daily','688981.SH','2025-06-10')
                self.assertGreater(metadata.call_count,0)
                metadata.reset_mock()
                request_coverage(reader,'margin_daily','688981.SH','2025-06-11')
                self.assertEqual(metadata.call_count,0)

    def test_event_request_rejects_changed_raw_manifest_ref(self):
        reader=SnapshotReader(self.root,self.snapshot)
        raw_id=reader.commits['margin_daily'].manifest['ordered_raw_batch_refs'][0]['raw_batch_id']
        target=self.root/'raw/batches'/raw_id
        path=target/'manifest.json'
        manifest=json.loads(path.read_bytes())
        manifest['request']['params']['start_date']='19000101'
        payload=artifacts._json_bytes(manifest)
        path.chmod(0o600);path.write_bytes(payload)
        digest_path=target/'manifest.sha256'
        digest_path.chmod(0o600)
        digest_path.write_text(artifacts._digest(payload)+'\n')
        with self.assertRaisesRegex(ArtifactError,'ref mismatch'):
            request_coverage(reader,'margin_daily','688981.SH','2025-06-10')

    def test_event_request_rejects_missing_raw_payload_without_decoding_it(self):
        reader=SnapshotReader(self.root,self.snapshot)
        raw_id=reader.commits['margin_daily'].manifest['ordered_raw_batch_refs'][0]['raw_batch_id']
        target=self.root/'raw/batches'/raw_id
        target.chmod(0o755)
        (target/'payload.bin').unlink()
        with self.assertRaisesRegex(ArtifactError,'payload file is missing'):
            request_coverage(reader,'margin_daily','688981.SH','2025-06-10')

    def test_reader_rejects_replaced_direct_contract_after_first_read(self):
        reader=SnapshotReader(self.root,self.snapshot)
        self.assertTrue(reader.market_daily(['688981.SH'],'2025-06-10','2025-06-13'))
        commit=reader.commits['market_daily'].ref.commit_id
        target=self.root/'canonical/market_daily/commits'/commit
        contract=target/'contract.json'
        target.chmod(0o755)
        replacement=target/'replacement.json'
        replacement.write_bytes(contract.read_bytes())
        replacement.replace(contract)
        with self.assertRaisesRegex(ArtifactError,'Reader consumed input changed'):
            reader.market_daily(['688981.SH'],'2025-06-10','2025-06-13')
        self.assertTrue(reader._invalidated)

    def test_reader_rejects_changed_raw_request_after_interval_cache_hit(self):
        reader=SnapshotReader(self.root,self.snapshot)
        request_coverage(reader,'margin_daily','688981.SH','2025-06-10')
        self.assertIn('margin_daily',reader._pr7_request_intervals)
        raw_id=reader.commits['margin_daily'].manifest['ordered_raw_batch_refs'][0]['raw_batch_id']
        target=self.root/'raw/batches'/raw_id
        manifest_path=target/'manifest.json'
        manifest=json.loads(manifest_path.read_bytes())
        manifest['request']['params']['start_date']='19000101'
        payload=artifacts._json_bytes(manifest)
        manifest_path.chmod(0o600);manifest_path.write_bytes(payload)
        digest_path=target/'manifest.sha256'
        digest_path.chmod(0o600);digest_path.write_text(artifacts._digest(payload)+'\n')
        with self.assertRaisesRegex(ArtifactError,'Reader consumed input changed'):
            request_coverage(reader,'margin_daily','688981.SH','2025-06-10')
        self.assertNotIn('margin_daily',getattr(reader,'_pr7_request_intervals',{}))

    def test_reader_rejects_changed_raw_checksum_after_interval_cache_hit(self):
        reader=SnapshotReader(self.root,self.snapshot)
        request_coverage(reader,'margin_daily','688981.SH','2025-06-10')
        raw_id=reader.commits['margin_daily'].manifest['ordered_raw_batch_refs'][0]['raw_batch_id']
        digest_path=self.root/'raw/batches'/raw_id/'manifest.sha256'
        digest_path.chmod(0o600)
        digest_path.write_text('sha256:'+'0'*64+'\n')
        with self.assertRaisesRegex(ArtifactError,'Reader consumed input changed'):
            request_coverage(reader,'margin_daily','688981.SH','2025-06-10')
        self.assertNotIn('margin_daily',getattr(reader,'_pr7_request_intervals',{}))

    def test_implementation_change_revalidates(self):
        source=Path(self.temp.name)/'code';source.mkdir()
        module=source/'validation.py';module.write_text('revision = 1')
        with patch('axiom_data.view_validation.files',return_value=source):
            with self.session.inputs('pr7_fact',self.config):pass
            module.write_text('revision = 2')
            with patch.object(artifacts,'load_domain_commit',wraps=artifacts.load_domain_commit) as loads:
                with self.session.inputs('pr7_fact',self.config):pass
                self.assertGreater(loads.call_count,0)

    def test_raw_mutation_during_initialization_cannot_reuse_provenance(self):
        original = artifacts._observation_rows
        changed = []
        def mutate(root, domain, rows, raw_ids, **kwargs):
            if domain == 'holder_count_events' and not changed:
                raw_id = next(iter(kwargs['verified_evidence']))
                path = root/'raw/batches'/raw_id/'payload.bin'
                path.chmod(0o600)
                path.write_bytes(path.read_bytes() + b' ')
                changed.append(raw_id)
            yield from original(root, domain, rows, raw_ids, **kwargs)
        with patch.object(artifacts, '_observation_rows', mutate):
            with self.assertRaisesRegex(ArtifactError, 'changed during validation'):
                with self.session.inputs('pr7_fact', self.config):
                    pass
        self.assertTrue(changed)
        with self.assertRaises(ArtifactError):
            with self.session.inputs('pr7_fact', self.config):
                pass

    def test_financial_projection_and_market_dependency_groups(self):
        from axiom_data.pr6_views import project as financial_project
        from axiom_data.domains import DM1_SNAPSHOT_DOMAINS, PR6_DOMAINS
        run=json.loads(Path('reports/pr7/run_manifest.json').read_bytes())
        manifest=json.loads((self.root/'derived/pr6_fact/commits'/run['refs']['pr6_view_id']/'manifest.json').read_bytes())
        config=dict(manifest['scope'],pit_policy=manifest['pit_policy'],knowledge_cutoff=manifest['knowledge_cutoff'])
        expected=financial_project(SnapshotReader(self.root,self.snapshot),manifest['scope'],
            manifest['pit_policy'],manifest['knowledge_cutoff'])
        with self.session.inputs('pr6_fact',config) as reader:
            self.assertEqual(set(reader.commits),{'security_master','trading_calendar',*PR6_DOMAINS})
            self.assertEqual(financial_project(reader,manifest['scope'],manifest['pit_policy'],manifest['knowledge_cutoff']),expected)
        for kind in ('adjusted_price','market_replay'):
            with self.session.inputs(kind,self.config) as reader:
                self.assertEqual(set(reader.commits),set(DM1_SNAPSHOT_DOMAINS))
        config={k:self.config[k] for k in ('symbols','start_session','end_session')}
        with self.session.inputs('market_qlib',config) as reader:
            self.assertEqual(set(reader.commits),{'security_master','trading_calendar','market_daily'})

    def test_adjusted_qlib_derived_dependency_is_not_reused_after_damage(self):
        from axiom_data import build_adjusted_price_view
        config={k:self.config[k] for k in ('symbols','start_session','end_session')}
        adjusted=build_adjusted_price_view(self.root,self.snapshot,**config,
            anchor_session='2025-06-13',pit_policy='research_non_pit',decision_cutoff='2025-06-13')
        config.update(price_basis='anchor_adjusted',adjusted_price_view_id=adjusted.view_id,
            pit_policy='research_non_pit',decision_cutoff='2025-06-13')
        with self.session.inputs('market_qlib',config):pass
        with patch.object(artifacts,'load_domain_commit',wraps=artifacts.load_domain_commit) as loads:
            with self.assertRaisesRegex(ArtifactError,'Derived scope mismatch'):
                with self.session.inputs('market_qlib',dict(config,start_session='2025-06-11')):pass
            self.assertEqual(loads.call_count,0)
        path=self.root/'derived/adjusted_price/commits'/adjusted.view_id/'rows.json'
        path.chmod(0o600);path.write_bytes(path.read_bytes()+b' ')
        with self.assertRaises(ArtifactError):
            with self.session.inputs('market_qlib',config):pass

    def test_ancestor_symlink_replacement_rejected_on_reuse_and_exit(self):
        from axiom_data.layout import LayoutError
        ancestor=self.root.parent/'ancestor'
        ancestor.mkdir()
        nested=ancestor/'data'
        self.root.rename(nested)
        self.root=nested
        self.session=ViewValidationSession(self.root,self.snapshot)
        for target in (self.root,ancestor):
            for during_use in (False,True):
                with self.subTest(target=target.name,during_use=during_use):
                    moved=target.with_name(target.name+'-moved')
                    def redirect():
                        target.rename(moved)
                        target.symlink_to(moved,target_is_directory=True)
                    try:
                        with self.session.inputs('pr7_fact',self.config):pass
                        if during_use:
                            with self.assertRaisesRegex(ArtifactError,'changed during use'):
                                with self.session.inputs('pr7_fact',self.config):redirect()
                        else:
                            redirect()
                            # Leaf files and the data root inode remain unchanged
                            # when only an ancestor above the root is replaced.
                            with self.assertRaisesRegex((ArtifactError,LayoutError),'must not be a symlink'):
                                with self.session.inputs('pr7_fact',self.config):pass
                    finally:
                        if target.is_symlink():target.unlink()
                        if moved.exists():moved.rename(target)
        with self.session.inputs('pr7_fact',self.config):pass
        with patch.object(artifacts,'load_domain_commit',wraps=artifacts.load_domain_commit) as loads:
            with self.session.inputs('pr7_fact',self.config):pass
            self.assertEqual(loads.call_count,0)
