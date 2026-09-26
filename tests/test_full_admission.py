"""Complete declared real-fixture scope; source nulls remain explicit."""
import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from axiom_data import ArtifactError, materialize_views
from axiom_data.artifacts import _digest
from axiom_data.full_admission import full_admission, validate_full_admission
import test_admission_plan


REAL_FINANCIAL_RAW_IDS = [
    'pr6-8fc46f30cf3f7386130f1225f0112d107de337ebe18453fd6df1ce076d0274b5',
    'pr6-347deb9d246404b0715348e5ec85775385cd49c22984611056cc59a386ba05bc',
    'pr6-d48393119e06e2fb98295834ddce16958d519941f2f0faeb8eb3fc0d53eee1f5',
    'pr6-38c18df035bacb10c7b6e7e551e104c13fe243d60edf49109f918e91a12bb4eb',
    'pr6-2efe232e2c8ff7ffa65080d5fcf0818ce5933261457bcf4c14dc9a9c6f307121',
    'pr6-1bc6d661e3854e22815493936dc3fd8b72ed442f36aca09dedde174cc29561f6',
    'pr6-f1c017e42d4d9a7e6abbb453d8bb4c1fcc68d954ae39ca5f4334fb9fec358e27',
    'pr6-852c62d4a4e84b9ef4dbea5d5d0bb48bddecca65f33d04c8c6db2d067bac79a5',
    'pr6-dcf60d03904e78d2c6a995c4bfaf9f29d393cd3d27824f4cbd7c2e048af2d80a',
]

def add_real_observation_window(root, snapshot):
    from axiom_data import SnapshotReader, load_raw_batch
    from axiom_data.operations import repair
    original=[r["raw_batch_id"] for r in SnapshotReader(root,snapshot).commits["financial_events"].manifest["ordered_raw_batch_refs"]]
    source=Path("/var/lib/axiom-data")
    for identity in REAL_FINANCIAL_RAW_IDS:
        raw=load_raw_batch(source,identity)
        assert raw.manifest["domain"]=="financial_events"
        assert raw.manifest["request"]["params"]["ts_code"]=="688981.SH"
        shutil.copytree(source/"raw/batches"/identity,root/"raw/batches"/identity)
    result=repair(root,run_id="real-observation-window",snapshot_id=snapshot,
        domain_inputs={"financial_events":dict(raw_batch_ids=original+REAL_FINANCIAL_RAW_IDS,
            contract_version="financial_events.v4",config={},new_lineage=True)})
    if result["status"]!="CANDIDATE_BUILT":raise AssertionError(result)
    return result["snapshot_id"]


def add_synthetic_observation_window(root, snapshot):
    """Synthetic empty source windows; all historical payloads stay unchanged."""
    from axiom_data import SnapshotReader
    from axiom_data.fundamentals_source import FundamentalsCollector
    from axiom_data.operations import repair
    reader=SnapshotReader(root,snapshot)
    original=[r['raw_batch_id'] for r in reader.commits['financial_events'].manifest['ordered_raw_batch_refs']]
    class EmptySource:
        def query(self,endpoint,**params): return []
    collector=FundamentalsCollector(root,EmptySource())
    observed=[]
    for endpoint in ('income','balancesheet','cashflow','fina_indicator'):
        params=dict(ts_code='688981.SH',start_date='20240101',end_date='20250613')
        if endpoint!='fina_indicator':params['report_type']='1'
        ref=collector.collect(endpoint,params,retrieved_at='2026-09-25T00:00:00Z',
            profile_version='tushare_fina_indicator.v1' if endpoint=='fina_indicator' else 'tushare_pr6.v1')
        observed.append(ref.raw_batch_id)
    result=repair(root,run_id='synthetic-observation-window',snapshot_id=snapshot,
        domain_inputs={'financial_events':dict(raw_batch_ids=original+observed,
            contract_version='financial_events.v4',config={},new_lineage=True)})
    if result['status']!='CANDIDATE_BUILT': raise AssertionError(result)
    return result['snapshot_id']


def fixture(test, *, daily_baseline=False, synthetic_window=True, real_window=False):
    existing = test_admission_plan.AdmissionPlanTest(); existing.setUp()
    test.temp = tempfile.TemporaryDirectory()
    test.root = Path(test.temp.name)/'data'
    shutil.copytree(existing.root, test.root)
    test.addCleanup(test.temp.cleanup)
    def writable():
        for path in test.root.rglob('*'):
            if path.is_dir(): path.chmod(0o755)
    test.addCleanup(writable)
    test.snapshot = existing.snapshot
    if daily_baseline:
        from test_daily_acceptance import prepare_baseline
        test.snapshot = prepare_baseline(test.root, test.snapshot)
    if real_window:
        test.snapshot = add_real_observation_window(test.root, test.snapshot)
    elif synthetic_window:
        test.snapshot = add_synthetic_observation_window(test.root, test.snapshot)
    from axiom_data.operations import bootstrap
    from axiom_data import SnapshotReader
    reader=SnapshotReader(test.root,test.snapshot)
    candidate=bootstrap(test.root,run_id="baseline-checkpoint",
        domain_commit_ids={d:c.ref.commit_id for d,c in reader.commits.items()})
    test.assertEqual(candidate["status"],"CANDIDATE_BUILT")
    test.assertEqual(candidate["snapshot_id"],test.snapshot)
    geometry = dict(symbols=['688981.SH'], start_session='2025-06-10', end_session='2025-06-13')
    configs = existing.plan['required_view_configs']
    views = {k:dict(kind=k, config=dict(c, **geometry)) for k,c in configs.items()}
    built = materialize_views(test.root, run_id='fixture-views', snapshot_id=test.snapshot, views=views)
    test.assertEqual(built['status'], 'VIEWS_BUILT', built)
    test.plan = dict(schema_version='full_admission_plan.v1',
        target=dict(geometry, benchmarks=['000300.SH'], universe_ids=configs['pr6_fact']['universe_ids'],
                    financial_observation_start='2024-01-01'),
        scope_registry_digest=existing.plan['scope_registry_digest'],
        snapshot_manifest_digest=(test.root/'snapshots'/test.snapshot/'manifest.sha256').read_text().strip(),
        required_view_configs=configs, views=views, view_refs=built['published_views'])


class FullAdmissionTest(unittest.TestCase):
    def setUp(self): fixture(self)

    def test_complete_scope_and_revalidation(self):
        report = full_admission(self.root, run_id='full', snapshot_id=self.snapshot, plan=self.plan)
        self.assertEqual(report['status'], 'PASS', [(r['requirement'],r['states']) for r in report['requirements'] if r['status']!='PASS'])
        self.assertEqual(len(report['requirements']), 56)
        self.assertEqual(len(report['features']), 469)
        from axiom_data.full_admission import _fact_state
        self.assertEqual(_fact_state({'value':None,'missing_reason':'no_observation_at_cutoff','pit_qualification':'best_effort'}, 'margin.balance'), 'BLOCKED')
        self.assertEqual(_fact_state({'value':1,'missing_reason':None,'pit_qualification':'unknown'}, 'income.revenue'), 'UNKNOWN')
        from axiom_data.financial_views import load_financial_fact_view
        from fixture_locations import fixture_root
        original_run=json.loads(Path('reports/pr7/run_manifest.json').read_bytes())
        original_view=load_financial_fact_view(fixture_root(original_run['source_root']),original_run['refs']['pr6_view_id'])
        missing=next(r['facts']['income.oper_cost'] for r in original_view.rows if r['symbol']=='600036.SH')
        before=copy.deepcopy(missing)
        self.assertEqual(missing['quality_state'],'BLOCKED')
        self.assertEqual(_fact_state(missing,'income.oper_cost'),'SOURCE_MISSING')
        self.assertEqual(missing,before)

        holder = next(r for r in report['requirements'] if r['requirement']=='holder.number')
        self.assertEqual(holder['states']['SOURCE_MISSING'], 4)
        self.assertIsNone(holder['missing_examples'][0]['fact']['value'])
        self.assertEqual(holder['missing_examples'][0]['fact']['missing_reason'], 'vendor_null')
        self.assertEqual(validate_full_admission(self.root, report, expected_views=self.plan['view_refs'],
            target_digest=report['target_digest'])['status'], 'FULL_ADMISSION_VALIDATED')
        other=Path(self.temp.name)/'other-root';shutil.copytree(self.root,other)
        def writable_other():
            for path in other.rglob('*'):
                if path.is_dir():path.chmod(0o755)
        self.addCleanup(writable_other)
        with self.assertRaises(ArtifactError):
            validate_full_admission(other, report, expected_views=self.plan['view_refs'],target_digest=report['target_digest'])
        forged=copy.deepcopy(report); forged['requirements'][0]['status']='invented'
        with self.assertRaises(ArtifactError):
            validate_full_admission(self.root, forged, expected_views=self.plan['view_refs'], target_digest=report['target_digest'])
        with self.assertRaises(ArtifactError):
            validate_full_admission(self.root, report, expected_views=self.plan['view_refs'], target_digest='sha256:'+'0'*64)

    def test_omitted_shard_and_wider_target(self):
        for mutation in ('omitted','wider','policy'):
            plan=copy.deepcopy(self.plan)
            if mutation=='omitted': del plan['views']['pr7_fact']
            if mutation=='wider': plan['target']['symbols'].append('600036.SH')
            if mutation=='policy': plan['views']['pr7_fact']['config']['knowledge_cutoff']='2026-01-01T00:00:00Z'
            with self.subTest(mutation=mutation), self.assertRaises(ArtifactError):
                full_admission(self.root,run_id=mutation,snapshot_id=self.snapshot,plan=plan)

    def test_period_only_fixture_does_not_prove_observation_window(self):
        import test_admission_plan
        existing=test_admission_plan.AdmissionPlanTest();existing.setUp()
        from axiom_data import SnapshotReader
        from axiom_data.full_admission import _financial_request_coverage
        with self.assertRaisesRegex(ArtifactError,'observation request coverage'):
            _financial_request_coverage(SnapshotReader(existing.root,existing.snapshot),self.plan['target'])

    def test_corruption_is_not_a_report_label(self):
        report=full_admission(self.root,run_id='full',snapshot_id=self.snapshot,plan=self.plan)
        view=self.plan['view_refs']['market_replay']['view_id']
        path=self.root/'derived/market_replay/commits'/view/'rows.json'
        path.chmod(0o600); path.write_bytes(b'corrupt')
        with self.assertRaises(ArtifactError):
            validate_full_admission(self.root,report,expected_views=self.plan['view_refs'],target_digest=report['target_digest'])


class RealFullAdmissionTest(unittest.TestCase):
    def test_real_source_window_and_full_dependency_scope(self):
        fixture(self,real_window=True)
        report=full_admission(self.root,run_id='real-full',snapshot_id=self.snapshot,plan=self.plan)
        self.assertEqual(report['status'],'PASS',[(r['requirement'],r['states']) for r in report['requirements'] if r['status']!='PASS'])
        self.assertEqual(validate_full_admission(self.root,report,expected_views=self.plan['view_refs'],target_digest=report['target_digest'])['status'],'FULL_ADMISSION_VALIDATED')
        wider=copy.deepcopy(self.plan);wider['target']['financial_observation_start']='2010-01-01'
        with self.assertRaisesRegex(ArtifactError,'observation request coverage'):
            full_admission(self.root,run_id='wider-observation',snapshot_id=self.snapshot,plan=wider)


class HistoricalPlanAdmissionTest(unittest.TestCase):
    """Synthetic Reader geometry only; this is not a v2 artifact end-to-end run."""
    def test_two_lifetimes_and_anchors_reuse_authoritative_planner(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        import test_price_anchor_scope
        from axiom_data.full_admission import _checked_plan
        from axiom_data.gate_a import plan_historical_views
        from axiom_data import requirement_registry_digest
        helper=test_price_anchor_scope.HistoricalAnchorTest()
        reader=helper.reader()
        first=reader.security_master()[0]
        security=[first,dict(symbol='688981.SH',exchange='SSE',list_session='2020-08-27',
            delist_session=None,status='source-current-L')]
        reader.security_master=lambda:security
        factors=[dict(symbol=s,session=d,factor=2,source_ref='fixed-source',source_available_at=None,
            first_observed_at='2020-08-28T12:00:00+08:00',availability_basis='terminal_history_observed',
            pit_qualification='best_effort') for s,d in [('600069.SH','2020-08-26'),('688981.SH','2020-08-28')]]
        reader.session_rows.side_effect=lambda *args:iter(factors)
        geometry=dict(symbols=['600069.SH','688981.SH'],start_session='2020-08-26',end_session='2020-08-28')
        target=dict(geometry,financial_observation_start='2020-01-01',benchmarks=['000300.SH'],universe_ids=['000906.SH'])
        with patch('axiom_data.full_admission.SnapshotReader',return_value=reader), \
                patch('axiom_data.consumption.SnapshotReader',return_value=reader), \
                patch('axiom_data.artifacts.load_raw_batch',return_value=SimpleNamespace(
                    manifest={'domain':'adjustment_factors','retrieved_at':'2020-08-28T12:00:00+08:00'})), \
                patch('axiom_data.artifacts._raw_ref',return_value={'raw_batch_id':'fixed-source'}):
            historical=plan_historical_views(target=geometry,security_rows=security,
                calendar_rows=reader.trading_calendar(),universe_ids=target['universe_ids'],
                knowledge_cutoff='2020-08-28T23:59:59+08:00',data_root='fixture',snapshot_id='fixture-snapshot')
            plan=dict(schema_version='full_admission_plan.v2',target=target,
                scope_registry_digest=requirement_registry_digest(),snapshot_manifest_digest='fixed-digest',
                historical_plan=historical,view_refs={label:dict(kind=spec['kind'],view_id='future',manifest_digest='future')
                    for label,spec in historical['views'].items()})
            checked=_checked_plan('fixture','fixture-snapshot',plan)[1]
            self.assertEqual(checked['expected_sessions']['600069.SH'],['2020-08-26','2020-08-27'])
            self.assertEqual(checked['expected_sessions']['688981.SH'],['2020-08-27','2020-08-28'])
            self.assertEqual(checked['views']['adjusted_price-600069.SH']['config']['anchor_session'],'2020-08-26')
            self.assertEqual(checked['views']['adjusted_price-688981.SH']['config']['anchor_session'],'2020-08-28')
            for kind in ('wrong_anchor','omitted','truncated','evidence'):
                wrong=copy.deepcopy(plan)
                if kind=='wrong_anchor':wrong['historical_plan']['views']['adjusted_price-600069.SH']['config']['anchor_session']='2020-08-27'
                elif kind=='omitted':del wrong['historical_plan']['views']['event_fact-688981.SH']
                elif kind=='truncated':wrong['target']['end_session']='2020-08-27'
                else:wrong['historical_plan']['price_anchor_resolution']['anchors']['adjusted_price-600069.SH']['factor_rows'][0]['factor']=3
                with self.subTest(kind=kind),self.assertRaises(ArtifactError):
                    _checked_plan('fixture','fixture-snapshot',wrong)
