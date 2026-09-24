import json
import shutil
import tempfile
import unittest
from pathlib import Path

from axiom_data import ArtifactError, FactView, SnapshotReader
from axiom_data.financial_views import build_financial_fact_view_from_reader, load_financial_fact_view, project
from axiom_data.financial_views import _files, _manifest
from axiom_data.artifacts import _derived_identity, _identity_digest, _layout, _write_file, _write_manifest
from axiom_data.pit import instant
from fixture_locations import fixture_root


class FinancialMembershipRefTest(unittest.TestCase):
    def test_v3_manifest_keeps_its_inline_membership_loader(self):
        run = json.loads(Path('reports/pr7/run_manifest.json').read_bytes())
        source = fixture_root(run['source_root'])
        old = json.loads((source / 'derived/pr6_fact/commits' /
                          run['refs']['pr6_view_id'] / 'manifest.json').read_bytes())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'data'
            shutil.copytree(source, root)
            reader = SnapshotReader(root, run['refs']['snapshot_id'])
            scope = dict(old['scope'], symbols=['688981.SH'])
            payload = project(reader, scope, old['pit_policy'], old['knowledge_cutoff'])
            package = Path(__import__('axiom_data').__file__).parent
            bundle = {p.relative_to(package).as_posix():p.read_text()
                      for p in sorted(package.rglob('*')) if p.is_file() and p.suffix in {'.py','.json'}}
            manifest = _manifest(reader, scope, old['pit_policy'], old['knowledge_cutoff'], payload, bundle)
            self.assertEqual(manifest['schema_version'], 'pr6_fact_view.v3')
            identity = _identity_digest(manifest, 'view_id')
            view_id = _derived_identity('pr6-fact', identity)
            manifest.update(view_id=view_id,identity_digest=identity,created_at='2026-09-24T00:00:00+00:00')
            target = _layout(root).derived_commits('pr6_fact') / view_id
            target.mkdir(parents=True)
            for name, content in _files(payload,scope['symbols'],bundle).items():
                path = target / name
                path.parent.mkdir(parents=True,exist_ok=True)
                _write_file(path,content)
            _write_manifest(target,manifest)
            loaded = load_financial_fact_view(root,view_id)
            self.assertEqual(loaded.manifest['schema_version'],'pr6_fact_view.v3')
            self.assertEqual(list(loaded.rows),payload['wide'])
            self.assertIn('memberships',json.loads((target / 'rows.json').read_bytes()))

    def test_v4_uses_snapshot_membership_ref_without_changing_financial_facts(self):
        run = json.loads(Path('reports/pr7/run_manifest.json').read_bytes())
        source = fixture_root(run['source_root'])
        old_view = source / 'derived/pr6_fact/commits' / run['refs']['pr6_view_id']
        old_manifest = json.loads((old_view / 'manifest.json').read_bytes())
        scope = dict(old_manifest['scope'], symbols=['688981.SH'])
        policy = old_manifest['pit_policy']
        cutoff = old_manifest['knowledge_cutoff']
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'data'
            shutil.copytree(source, root)
            reader = SnapshotReader(root, run['refs']['snapshot_id'])
            legacy = project(reader, scope, policy, cutoff)
            compact = project(reader, scope, policy, cutoff, membership_ref=True)
            for key in ('wide', 'sessions'):
                self.assertEqual(compact[key], legacy[key], key)
            self.assertEqual(compact['events'], [])
            self.assertEqual(compact['derived'], [])
            self.assertEqual(compact['industries'], [])
            self.assertEqual(compact['compact_derived_count'], len(legacy['derived']))
            self.assertTrue(legacy['memberships'])
            self.assertEqual(compact['memberships'], [])
            ref = build_financial_fact_view_from_reader(reader, **scope,
                pit_policy=policy, knowledge_cutoff=cutoff)
            view = load_financial_fact_view(root, ref.view_id)
            package = Path(__import__('axiom_data').__file__).parent
            bundle = {p.relative_to(package).as_posix():p.read_text()
                      for p in sorted(package.rglob('*')) if p.is_file() and p.suffix in {'.py','.json'}}
            legacy_manifest = _manifest(reader,scope,policy,cutoff,legacy,bundle)
            self.assertEqual(view.manifest['pit_qualification'],legacy_manifest['pit_qualification'])
            self.assertEqual(view.manifest['validation_summary'],legacy_manifest['validation_summary'])
            self.assertEqual(view.manifest['schema_version'], 'pr6_fact_view.v4')
            self.assertEqual(list(view.rows), compact['wide'])
            stored = root / 'derived/pr6_fact/commits' / ref.view_id / 'rows.json'
            stored_rows = json.loads(stored.read_bytes())
            self.assertEqual(set(stored_rows), {'wide','sessions'})
            self.assertLess(stored.stat().st_size, len(json.dumps(legacy,separators=(',',':')).encode()))
            binding = view.manifest['membership_ref']
            self.assertEqual(binding['snapshot_id'], reader.snapshot.ref.snapshot_id)
            self.assertEqual(binding['domain_commit_id'], reader.commits['universe_membership'].ref.commit_id)
            self.assertEqual(binding['knowledge_cutoff'], instant(cutoff).isoformat())
            public = FactView(root, run['refs']['snapshot_id'], financial_fact_view_id=ref.view_id)
            self.assertEqual(public.read('financial')['membership_ref'], binding)
            for group in scope['universe_ids']:
                for day in compact['sessions']:
                    effective = min(instant(cutoff), instant(day+'T23:59:59+08:00')).isoformat()
                    expected = reader.members(group, day, knowledge_cutoff=effective,
                                              pit_policy=policy, symbols=scope['symbols'])
                    self.assertEqual(public.financial_members(group,day,symbols=scope['symbols']), expected)
                    complete = reader.members(group, day, knowledge_cutoff=effective,pit_policy=policy)
                    self.assertEqual(public.financial_members(group,day), complete)
            wrong = dict(binding, snapshot_id='snapshot-other')
            with self.assertRaisesRegex(ArtifactError, 'membership ref'):
                reader.members_from_view_ref(wrong,scope['universe_ids'][0],compact['sessions'][0])
            wrong = dict(binding, domain_commit_id='universe_membership-other')
            with self.assertRaisesRegex(ArtifactError, 'membership ref'):
                reader.members_from_view_ref(wrong,scope['universe_ids'][0],compact['sessions'][0])
            with self.assertRaisesRegex(ArtifactError, 'does not cover session'):
                reader.members_from_view_ref(binding,scope['universe_ids'][0],'2025-06-09')
