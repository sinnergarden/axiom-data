from axiom_data.contracts import writable_contracts
"""Real immutable supplier responses; isolated commits and a bounded View Reader.

The fixture retains exact Raw bytes/IDs. Security/calendar and Snapshot binding
are test scaffolding, not a claim of full production Snapshot validation.
"""
import copy
import json
import tempfile
import unittest
import zipfile
from collections import OrderedDict
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from axiom_data import BuildApplication, MarketDomainBuilder, SnapshotReader
from axiom_data.artifacts import ArtifactError, _DOMAIN_DEPENDENCIES, _digest, _json_bytes, load_raw_batch, validate_domain_commit_closure
from axiom_data.consumption import QlibViewReader
from axiom_data.event_source import EventBuilder
from axiom_data import event_views
from test_artifacts import synthetic_source_profiles, write_rows, security_row

FIXTURE = Path(__file__).parent / 'fixtures/event_source_cutoff_688981.zip'
SYMBOL = '688981.SH'
CUTOFF = '2026-09-11T23:59:59+08:00'


class EventSourceCutoffTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name)
        with zipfile.ZipFile(FIXTURE) as archive:
            archive.extractall(cls.root)
        cls.fixture = json.loads((cls.root / 'fixture.json').read_text())
        cls.commits = {}
        # Retain the complete real responses, including their historical sessions.
        sessions = {'2026-09-10', '2026-09-11'}
        for domain in ('margin_daily', 'moneyflow_daily'):
            for raw_id in cls.fixture['domains'][domain]['raw_ids']:
                for row in json.loads(load_raw_batch(cls.root, raw_id).payload):
                    day = row['trade_date']
                    sessions.add(day[:4]+'-'+day[4:6]+'-'+day[6:])
        calendar = []; previous = None; day = date.fromisoformat(min(sessions))
        while day <= date(2026, 9, 11):
            session = day.isoformat()
            calendar.append(dict(exchange='SSE', session=session, is_open=session in sessions,
                                 previous_open_session=previous))
            if session in sessions: previous = session
            day += timedelta(days=1)
        with synthetic_source_profiles():
            fixtures = {
                'security_master': [security_row(SYMBOL, 'SSE', list_session='2020-07-16')],
                'trading_calendar': calendar,
            }
            for domain, rows in fixtures.items():
                raw = write_rows(cls.root, domain + '-fixture', domain, rows)
                ref = BuildApplication(domain, MarketDomainBuilder(cls.root, domain)).build(
                    None, [raw.raw_batch_id], [], writable_contracts()["domains"][domain]["current"])
                cls.commits[domain] = validate_domain_commit_closure(cls.root, domain, ref.commit_id)
            for domain, entry in cls.fixture['domains'].items():
                builder = EventBuilder(cls.root, domain, builder_config=entry['builder_config'],
                                     dependency_commit_ids={d:cls.commits[d].ref.commit_id for d in _DOMAIN_DEPENDENCIES[domain]})
                ref = BuildApplication(domain, builder).build(None, entry['raw_ids'], [], entry['contract_version'])
                cls.commits[domain] = validate_domain_commit_closure(cls.root, domain, ref.commit_id)
        cls.binding = _digest(_json_bytes({d:c.ref.commit_id for d,c in cls.commits.items()}))

    def setUp(self):
        self.reader = object.__new__(SnapshotReader)
        self.reader.data_root = self.root
        self.reader.commits = dict(self.commits)
        self.reader.snapshot = SimpleNamespace(
            ref=SimpleNamespace(snapshot_id='snapshot-' + self.binding[7:]),
            manifest={'identity_digest':self.binding})
        self.reader._security_projection = OrderedDict()
        self.reader._security_projection_bytes = 0
        self.reader._verified_lineage = {
            (d,c.ref.commit_id): {'raw_batch_ids': e['raw_ids'], 'parent_commit_id': None}
            for d,e in self.fixture['domains'].items() for c in [self.commits[d]]}
        self.scope = dict(symbols=[SYMBOL], start_session='2026-09-10', end_session='2026-09-11')

    def project(self, **kwargs):
        return event_views.project(self.reader, self.scope, 'best_effort_vendor_v1', CUTOFF, **kwargs)

    def test_real_public_view_keeps_end_and_missing_matches_direct_and_qlib(self):
        # Bounded synthetic Reader test; actual public subprocess builds use real Snapshots.
        with patch('axiom_data.frozen_execution.is_frozen', return_value=True), patch.object(event_views, 'SnapshotReader', return_value=self.reader):
            ref = event_views.build_event_fact_view(self.root, self.reader.snapshot.ref.snapshot_id,
                **self.scope, pit_policy='best_effort_vendor_v1', knowledge_cutoff=CUTOFF)
            view = event_views.load_event_fact_view(self.root, ref.view_id)
            qlib = QlibViewReader(self.root, ref.view_id).market_daily(include_missing=True)
        self.assertEqual(view.manifest['schema_version'], 'event_fact_view.v4')
        self.assertEqual(view.manifest['scope'], self.scope)
        self.assertEqual([r['session'] for r in view.rows], ['2026-09-10', '2026-09-11'])
        before, after = view.rows
        raw_id = self.fixture['domains']['margin_daily']['raw_ids'][0]
        raw = load_raw_batch(self.root, raw_id)
        self.assertEqual(raw.manifest['request']['params']['end_date'], '20260910')
        original = next(r for r in json.loads(raw.payload) if r['trade_date']=='20260910')
        self.assertEqual(before['values']['margin.balance'], original['rzye'])
        self.assertIsNone(after['values']['margin.balance'])
        self.assertIsNotNone(after['values']['moneyflow.net'])
        fact = after['facts']['margin.balance']
        self.assertEqual(fact['missing_reason'], 'source_scope_not_available')
        self.assertEqual(fact['pit_qualification'], 'unknown')
        self.assertIsNone(fact['source_ref'])
        self.assertIsNone(fact['usable_at'])
        self.assertEqual(fact, self.reader.leaf_fact('margin.balance', symbol=SYMBOL,
            target_session='2026-09-11', knowledge_cutoff=CUTOFF, pit_policy='best_effort_vendor_v1'))
        self.assertEqual([r['session'] for r in qlib], ['2026-09-10', '2026-09-11'])
        self.assertIsNone(qlib[-1]['margin.balance'])
        # Bounded synthetic Reader test; actual public subprocess builds use real Snapshots.
        with patch('axiom_data.frozen_execution.is_frozen', return_value=True), patch.object(event_views, 'SnapshotReader', return_value=self.reader):
            public = QlibViewReader(self.root, ref.view_id)
        self.assertEqual(public.as_of('2026-09-11'), (qlib[-1],))
        self.assertEqual(public.market_daily(include_missing=True,
            start_session='2026-09-10',end_session='2026-09-10'), (qlib[0],))
        self.assertEqual(public.fact_metadata(start_session='2026-09-11',
            end_session='2026-09-11')['rows'][0]['fields'], after['facts'])

    def test_operational_visibility_still_uses_real_observation_time(self):
        fact = self.reader.leaf_fact('margin.balance', symbol=SYMBOL,
            target_session='2026-09-10', knowledge_cutoff=CUTOFF, pit_policy='operational_pit_v1')
        self.assertIsNone(fact['value'])
        self.assertEqual(fact['missing_reason'], 'no_observation_at_cutoff')

    def test_missing_request_at_frozen_cutoff_is_still_rejected(self):
        self.reader._event_request_intervals = {'margin_daily': {SYMBOL:[('20140101','20260909')]}}
        with self.assertRaisesRegex(ArtifactError, 'INSUFFICIENT_SCOPE'):
            self.project()

    def test_other_security_coverage_cannot_supply_requested_security(self):
        self.reader._event_request_intervals = {'margin_daily': {'600036.SH':[('20140101','20260910')]}}
        with self.assertRaisesRegex(ArtifactError, 'INSUFFICIENT_SCOPE'):
            self.project()

    def test_no_explicit_source_cutoff_retains_strict_coverage(self):
        commit = self.reader.commits['margin_daily']; manifest = copy.deepcopy(commit.manifest)
        del manifest['builder_config']['end_session']
        self.reader.commits['margin_daily'] = replace(commit, manifest=manifest)
        with self.assertRaisesRegex(ArtifactError, 'INSUFFICIENT_SCOPE'):
            self.project()

    def test_source_cutoff_does_not_change_view_or_other_domain_cutoff(self):
        with patch.object(self.reader, 'as_of', wraps=self.reader.as_of) as selected:
            self.project()
        margin = [c for c in selected.call_args_list if c.args[0]=='margin_daily']
        flow = [c for c in selected.call_args_list if c.args[0]=='moneyflow_daily']
        self.assertEqual([c.kwargs['end_session'] for c in margin], ['2026-09-10']*2)
        self.assertEqual([c.kwargs['end_session'] for c in flow], ['2026-09-10','2026-09-11'])

    def test_legacy_v2_still_loads_with_frozen_strict_semantics(self):
        legacy_scope = dict(self.scope, end_session='2026-09-10')
        from axiom_data.artifacts import _derived_identity, _identity_digest, _layout, _write_file, _write_manifest
        payload=event_views.project(self.reader,legacy_scope,'best_effort_vendor_v1',CUTOFF,source_cutoffs=False)
        bundle={'frozen.py':'legacy source'}
        from axiom_data.deprecated.event_views import manifest_for
        manifest=manifest_for(self.reader,legacy_scope,'best_effort_vendor_v1',CUTOFF,
            payload,bundle,schema_version='pr7_fact_view.v2')
        identity=_identity_digest(manifest,'view_id')
        view_id=_derived_identity('pr7-fact',identity)
        manifest.update(view_id=view_id,identity_digest=identity,created_at='2026-09-24T00:00:00+00:00')
        target=_layout(self.root).derived_commits('pr7_fact')/view_id
        target.mkdir(parents=True)
        for name,content in event_views.payload_files(payload,legacy_scope['symbols'],bundle).items():
            path=target/name;path.parent.mkdir(parents=True,exist_ok=True);_write_file(path,content)
        _write_manifest(target,manifest)
        # Bounded synthetic Reader test; actual public subprocess builds use real Snapshots.
        with patch('axiom_data.frozen_execution.is_frozen', return_value=True), patch.object(event_views, 'SnapshotReader', return_value=self.reader):
            old = event_views.load_event_fact_view_with_reader(self.root, view_id, checked_reader=self.reader)
        self.assertEqual(old.manifest['schema_version'], 'pr7_fact_view.v2')
        with self.assertRaisesRegex(ArtifactError, 'INSUFFICIENT_SCOPE'):
            self.project(source_cutoffs=False)
