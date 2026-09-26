"""Isolated canonical corrections derived from real immutable source fixtures.
All deliberate wrong values are synthetic probes, not supplier amendments.
"""
import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from axiom_data import BuildApplication, SnapshotReader, validate_domain_commit_closure, repair
from axiom_data.artifacts import ArtifactError, _validated_domain_commit_with_raw_closure, _DOMAIN_DEPENDENCIES
from axiom_data.tushare import TushareMarketBuilder
from axiom_data.reference_source import TushareReferenceBuilder
from axiom_data.event_source import EventBuilder
from axiom_data.fundamentals_source import FundamentalsBuilder
from axiom_data.patches import publish_patch, load_patch, row_digest
from fixture_locations import fixture_root


class ImmutableCorrectionsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.root=Path(cls.temp.name)/'data'
        report=json.loads(Path('reports/pr7/run_manifest.json').read_text())
        shutil.copytree(fixture_root(report['source_root']),cls.root)
        cls.snapshot_id=report['refs']['snapshot_id'];cls.reader=SnapshotReader(cls.root,cls.snapshot_id)
        cls.before=cls.reader.snapshot.manifest;cls.parents={};cls.raws={}
        with patch('axiom_data.frozen_execution.is_frozen',return_value=True):
            for domain in ('market_daily','adjustment_factors','holder_count_events','financial_events'):
                old,ids=_validated_domain_commit_with_raw_closure(cls.root,domain,cls.reader.commits[domain].ref.commit_id)
                cls.raws[domain]=sorted(ids)
                version='financial_events.v4' if domain=='financial_events' else old.ref.contract_version
                ref=BuildApplication(domain,cls.builder(domain)).build(None,sorted(ids),[],version)
                cls.parents[domain]=validate_domain_commit_closure(cls.root,domain,ref.commit_id)
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def setUp(self):self.enterContext(patch('axiom_data.frozen_execution.is_frozen',return_value=True))
    @classmethod
    def builder(cls,domain):
        old=cls.reader.commits[domain]
        kind=TushareMarketBuilder if domain=='market_daily' else TushareReferenceBuilder if domain=='adjustment_factors' else FundamentalsBuilder if domain=='financial_events' else EventBuilder
        deps={d:cls.reader.commits[d].ref.commit_id for d in _DOMAIN_DEPENDENCIES[domain]}
        options={'calendar_commit_id':deps['trading_calendar'],'security_master_commit_id':deps['security_master']} if domain=='market_daily' else {'dependency_commit_ids':deps}
        return kind(cls.root,domain,builder_config=dict(old.manifest['builder_config'],storage_policy='domain_time_blocks.v1'),**options)
    def op(self,domain,kind,row,new=None):
        op={'op':kind,'key':{k:row[k] for k in self.parents[domain].contract['primary_key']}}
        if kind!='insert':op['expected_old_digest']=row_digest(row)
        if kind!='tombstone':op['row']=copy.deepcopy(new if new is not None else row)
        return op
    def publish(self,domain,ops):
        return publish_patch(self.root,domain=domain,contract_version=self.parents[domain].ref.contract_version,reason='Isolated fixture canonical correction example',operations=ops,source_raw_batch_ids=self.raws[domain])
    def build(self,domain,parent,patches,raws=()):
        ref=BuildApplication(domain,self.builder(domain)).build(parent.ref.commit_id,list(raws),[p['patch_id'] for p in patches],parent.ref.contract_version)
        return validate_domain_commit_closure(self.root,domain,ref.commit_id)
    def test_ordered_all_families_old_snapshot_and_determinism(self):
        for domain,parent in self.parents.items():
            with self.subTest(domain=domain):
                row=parent.rows[0]
                removed=self.build(domain,parent,[self.publish(domain,[self.op(domain,'tombstone',row)])])
                self.assertEqual(len(removed.rows),len(parent.rows)-1)
                restored=self.build(domain,removed,[self.publish(domain,[self.op(domain,'insert',row)])])
                self.assertEqual(list(restored.rows),list(parent.rows))
                p=self.publish(domain,[self.op(domain,'replace',row)])
                replaced=self.build(domain,restored,[p])
                self.assertEqual(list(replaced.rows),list(parent.rows));self.assertNotEqual(replaced.ref.commit_id,restored.ref.commit_id)
                self.assertEqual(self.build(domain,restored,[p]).ref.commit_id,replaced.ref.commit_id)
                replayed=self.build(domain,replaced,[],self.raws[domain]);self.assertEqual(list(replayed.rows),list(parent.rows))
                self.assertEqual(list(validate_domain_commit_closure(self.root,domain,parent.ref.commit_id).rows),list(parent.rows))
        self.assertEqual(SnapshotReader(self.root,self.snapshot_id).snapshot.manifest,self.before)
    def test_preconditions_keys_domain_and_ancestry(self):
        domain='holder_count_events';parent=self.parents[domain];row=parent.rows[0]
        wrong=self.op(domain,'tombstone',row);wrong['expected_old_digest']='sha256:'+'0'*64
        for op in (wrong,self.op(domain,'insert',row)):
            with self.assertRaisesRegex(ArtifactError,'precondition'):self.build(domain,parent,[self.publish(domain,[op])])
        op=self.op(domain,'replace',row);op['row']['revision_id']='wrong'
        with self.assertRaises(ArtifactError):self.publish(domain,[op])
        p=self.publish(domain,[self.op(domain,'replace',row)]);replaced=self.build(domain,parent,[p])
        with self.assertRaisesRegex(ArtifactError,'ancestor'):self.build(domain,replaced,[p])
        with self.assertRaisesRegex(ArtifactError,'domain/contract'):self.build('financial_events',self.parents['financial_events'],[p])
    def test_revision_and_pit_rejection(self):
        from axiom_data.pit import fingerprint
        for domain in ('holder_count_events','financial_events'):
            row=self.parents[domain].rows[0];bad=copy.deepcopy(row);bad['values'][next(iter(bad['values']))]+=1
            with self.assertRaisesRegex(ArtifactError,'fingerprint'):self.publish(domain,[self.op(domain,'replace',row,bad)])
            bad=copy.deepcopy(row);bad['first_observed_at']='1990-01-01T00:00:00Z'
            with self.assertRaisesRegex(ArtifactError,'availability'):self.publish(domain,[self.op(domain,'replace',row,bad)])
            bad=copy.deepcopy(row);o=bad['observations'][0];o['source_ref']='unverified-raw';o['observation_id']=fingerprint({k:v for k,v in o.items() if k!='observation_id'})
            with self.assertRaisesRegex(ArtifactError,'provenance'):self.publish(domain,[self.op(domain,'replace',row,bad)])
    def test_tombstone_retained_or_explicit_raw_conflict(self):
        for domain,parent in self.parents.items():
            row=parent.rows[0];removed=self.build(domain,parent,[self.publish(domain,[self.op(domain,'tombstone',row)])])
            if domain in ('market_daily','adjustment_factors'):
                with self.assertRaisesRegex(ArtifactError,'inherited correction'):self.build(domain,removed,[],self.raws[domain])
            else:self.assertEqual(list(self.build(domain,removed,[],self.raws[domain]).rows),list(removed.rows))
    def test_corrupted_patch_and_evidence(self):
        domain='holder_count_events';row=self.parents[domain].rows[0];p=self.publish(domain,[self.op(domain,'replace',row)])
        for target in (self.root/'patches'/p['patch_id']/'manifest.json',self.root/'raw/batches'/self.raws[domain][0]/'manifest.json'):
            content=target.read_bytes();mode=target.stat().st_mode;target.chmod(mode | 0o200)
            try:
                target.write_bytes(content+b' ')
                with self.assertRaisesRegex(ArtifactError,'digest'):load_patch(self.root,p['patch_id'])
            finally:
                target.write_bytes(content);target.chmod(mode)
    def test_frozen_patch_only_repair_resume_and_domain_isolation(self):
        domain='holder_count_events'
        with patch('axiom_data.frozen_execution.is_frozen',return_value=False):
            first=repair(self.root,run_id='correction-parent',snapshot_id=self.snapshot_id,domain_inputs={domain:{'raw_batch_ids':self.raws[domain],'contract_version':'holder_count_events.v1','config':{},'new_lineage':True}})
            self.assertEqual(first['status'],'CANDIDATE_BUILT',first)
            before=SnapshotReader(self.root,first['snapshot_id']);parent=before.commits[domain];row=parent.rows[0]
            p=self.publish(domain,[self.op(domain,'tombstone',row)])
            spec={'raw_batch_ids':[],'patch_ids':[p['patch_id']],'contract_version':'holder_count_events.v1','config':{},'new_lineage':False}
            result=repair(self.root,run_id='correction-repair',snapshot_id=first['snapshot_id'],domain_inputs={domain:spec})
            self.assertEqual(result['status'],'CANDIDATE_BUILT',result)
            again=repair(self.root,run_id='correction-repair',snapshot_id=first['snapshot_id'],domain_inputs={domain:spec})
            self.assertEqual(result['snapshot_id'],again['snapshot_id']);after=SnapshotReader(self.root,result['snapshot_id'])
            for name,commit in before.commits.items():
                if name!=domain:self.assertEqual(commit.ref.commit_id,after.commits[name].ref.commit_id)
            self.assertEqual(len(after.commits[domain].rows),len(parent.rows)-1)
    def test_source_supported_times_keys_and_numeric_revision_correction(self):
        from axiom_data.pit import fingerprint
        from axiom_data.domains.fundamentals import economic_content
        domain='holder_count_events';parent=self.parents[domain];row=parent.rows[0]
        for kind in ('vendor_time','later_observation','logical_key'):
            bad=copy.deepcopy(row)
            if kind=='vendor_time':
                bad['vendor_available_at']='1990-01-01T00:00:00Z'
                for o in bad['observations']:o['vendor_available_at']=bad['vendor_available_at']
            elif kind=='later_observation':
                bad['first_observed_at']='2030-01-01T00:00:00Z'
                for o in bad['observations']:o['observed_at']=bad['first_observed_at']
            else:bad['logical_event_key']='unsupported-key'
            bad['revision_id']=fingerprint(economic_content(bad))
            for o in bad['observations']:
                o['revision_id']=bad['revision_id'];o['observation_id']=fingerprint({k:v for k,v in o.items() if k!='observation_id'})
            # A fresh key cannot evade the source-backed availability boundary.
            p=self.publish(domain,[self.op(domain,'tombstone',row),self.op(domain,'insert',bad)])
            with self.assertRaisesRegex(ArtifactError,'unsupported patch'):
                self.build(domain,parent,[p])
        corrected=copy.deepcopy(row);corrected['values']['number']+=1
        corrected['revision_id']=fingerprint(economic_content(corrected))
        for o in corrected['observations']:
            o['revision_id']=corrected['revision_id'];o['observation_id']=fingerprint({k:v for k,v in o.items() if k!='observation_id'})
        p=self.publish(domain,[self.op(domain,'tombstone',row),self.op(domain,'insert',corrected)])
        result=self.build(domain,parent,[p])
        self.assertIn(corrected,list(result.rows));self.assertNotIn(row,list(result.rows))
        self.assertEqual(list(self.build(domain,result,[],self.raws[domain]).rows),list(result.rows))

    def test_new_actual_observation_conflicts_with_old_digest(self):
        from axiom_data.event_source import EventCollector
        from axiom_data import load_raw_batch
        from test_pr6_artifacts import Client
        domain='holder_count_events';parent=self.parents[domain];row=parent.rows[0]
        raw=load_raw_batch(self.root,row['source_ref'])
        # Re-observing the exact frozen response later is a synthetic observation,
        # not a claimed new supplier revision.
        later=EventCollector(self.root,Client(json.loads(raw.payload))).collect(
            raw.manifest['request']['endpoint'],raw.manifest['request']['params'],retrieved_at='2026-10-01T00:00:00Z')
        p=self.publish(domain,[self.op(domain,'tombstone',row)])
        removed=self.build(domain,parent,[p])
        with self.assertRaisesRegex(ArtifactError,'precondition'):
            self.build(domain,removed,[],[later.raw_batch_id])

    def test_patch_only_reuses_untouched_partitions(self):
        from axiom_data.partitions import partition_key, publish_partitions
        domain='market_daily';parent=self.parents[domain];row=parent.rows[0]
        p=self.publish(domain,[self.op(domain,'tombstone',row)])
        sizes=[]
        def publish(layout,name,rows):
            selected=list(rows);sizes.append(len(selected))
            self.assertTrue(all(partition_key(name,r)==partition_key(domain,row) for r in selected))
            return publish_partitions(layout,name,selected)
        with patch('axiom_data.partitions.publish_partitions',side_effect=publish):
            corrected=self.build(domain,parent,[p])
        self.assertTrue(sizes);self.assertLess(max(sizes),len(parent.rows))
        untouched={e['key']:e for e in parent.manifest['partitions'] if e['key']!=partition_key(domain,row)}
        self.assertTrue(untouched)
        self.assertTrue(all(e in corrected.manifest['partitions'] for e in untouched.values()))

    def test_patch_protocol_cannot_be_dropped_or_invented(self):
        from axiom_data.artifacts import _identity_digest,_derived_identity,_write_manifest,load_domain_commit
        domain='holder_count_events';parent=self.parents[domain];row=parent.rows[0]
        corrected=self.build(domain,parent,[self.publish(domain,[self.op(domain,'tombstone',row)])])
        for protocol in (None,'unknown-patch.v99'):
            manifest=copy.deepcopy(corrected.manifest)
            if protocol is None:manifest.pop('patch_protocol')
            else:manifest['patch_protocol']=protocol
            digest=_identity_digest(manifest,'domain_commit_id');identity=_derived_identity(domain,digest)
            manifest.update(identity_digest=digest,domain_commit_id=identity)
            source=self.root/'canonical'/domain/'commits'/corrected.ref.commit_id
            target=source.parent/identity;shutil.copytree(source,target)
            for f in target.iterdir():f.chmod(f.stat().st_mode | 0o200)
            (target/'manifest.json').unlink();(target/'manifest.sha256').unlink()
            _write_manifest(target,manifest)
            with self.assertRaisesRegex(ArtifactError,'patch protocol'):load_domain_commit(self.root,domain,identity)

    def test_group_state_completeness_still_blocks_tombstone(self):
        from test_universe_acquisition import UniverseAcquisitionTest
        fixture=UniverseAcquisitionTest();fixture.setUp();self.addCleanup(fixture.doCleanups)
        raw=fixture.raw('20250101','20250131','2025-04-01T00:00:00Z',['000001.SZ'])
        parent=fixture.build([raw]);row=parent.rows[0];domain='universe_membership'
        op={'op':'tombstone','key':{k:row[k] for k in parent.contract['primary_key']},'expected_old_digest':row_digest(row)}
        p=publish_patch(fixture.root,domain=domain,contract_version='universe_membership.v3',reason='Synthetic incomplete group probe',operations=[op],source_raw_batch_ids=[raw.raw_batch_id])
        builder=FundamentalsBuilder(fixture.root,domain,builder_config=parent.manifest['builder_config'],dependency_commit_ids={'security_master':fixture.security.commit_id})
        with self.assertRaisesRegex(ValueError,'group|member'):
            BuildApplication(domain,builder).build(parent.ref.commit_id,[],[p['patch_id']],'universe_membership.v3')
    def test_full_tombstone_retains_source_ancestry(self):
        domain='holder_count_events';parent=self.parents[domain]
        p=self.publish(domain,[self.op(domain,'tombstone',row) for row in parent.rows])
        removed=self.build(domain,parent,[p]);self.assertEqual(list(removed.rows),[])
        replayed=self.build(domain,removed,[],self.raws[domain]);self.assertEqual(list(replayed.rows),[])

    def test_equal_patch_is_not_swallowed_by_no_change_policy(self):
        domain='holder_count_events';builder=self.builder(domain)
        builder.builder_config['no_change_policy']='reuse_equal_state.v1'
        app=BuildApplication(domain,builder)
        ref=app.build(None,self.raws[domain],[],'holder_count_events.v1')
        parent=validate_domain_commit_closure(self.root,domain,ref.commit_id);row=parent.rows[0]
        p=self.publish(domain,[self.op(domain,'replace',row)])
        changed=app.build(ref.commit_id,[],[p['patch_id']],'holder_count_events.v1')
        self.assertNotEqual(ref.commit_id,changed.commit_id)
        current=validate_domain_commit_closure(self.root,domain,changed.commit_id)
        self.assertEqual(list(current.rows),list(parent.rows))
        self.assertEqual(current.manifest['ordered_patch_refs'][0]['patch_id'],p['patch_id'])
