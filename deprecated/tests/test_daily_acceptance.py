"""Real frozen closure with explicitly simulated supplier revisions."""
import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from axiom_data import ArtifactError, SnapshotReader, load_raw_batch
from axiom_data.artifacts import _digest
from axiom_data.daily_acceptance import execute_daily_case, validate_daily_evidence
from fixture_locations import fixture_root
from test_event_source import current_event_snapshot


def no_change_inputs(root, snapshot):
    parent=SnapshotReader(root,snapshot)
    holder_raw=load_raw_batch(root,parent.commits['holder_count_events'].manifest['ordered_raw_batch_refs'][0]['raw_batch_id'])
    params=holder_raw.manifest['request']['params']
    request=dict(collector='pr7',domain='holder_count_events',endpoint='stk_holdernumber',params=params,
        economic_scope=dict(start=params.get('start_date','20250101'),end=params.get('end_date','20250613')),
        availability_policy='revision_scan')
    inputs={'holder_count_events':dict(raw_batch_ids=[],contract_version='holder_count_events.v1',config={},new_lineage=False)}
    from axiom_data.operations import normalize_source_request,validate_request_spec
    key=validate_request_spec(normalize_source_request(request))
    return request, inputs, {key:holder_raw.ref.raw_batch_id}


def prepare_baseline(root, snapshot):
    from axiom_data.operations import daily
    snapshot=current_event_snapshot(root,snapshot,['holder_count_events','margin_daily'])
    request,inputs,observed=no_change_inputs(root,snapshot)
    return daily(root,run_id='baseline-source-upgrade',snapshot_id=snapshot,source_requests=[request],
        domain_inputs=inputs,observed_raw_batch_ids=observed)['snapshot_id']


def daily_cases(test, root, snapshot):
    request,inputs,observed=no_change_inputs(root,snapshot)
    result=execute_daily_case(root,case='no_change',run_id='no-change',snapshot_id=snapshot,
        source_requests=[request],domain_inputs=inputs,observed_raw_batch_ids=observed)
    test.assertEqual(result['status'],'NO_CHANGE',result)
    class Client:
        fail=False
        def query(self,endpoint,**params):
            if endpoint=='margin_detail':
                if self.fail:raise RuntimeError('simulated outage')
                return [dict(ts_code='688981.SH',trade_date='20250613',rzye=12,rzmre=2,rzche=1,rqyl=3,rqchl=1,rqmcl=1,rzrqye=20)]
            return [dict(ts_code='688981.SH',ann_date='20250612',end_date='20250331',holder_num=12345)]
    requests=[dict(collector='pr7',domain='holder_count_events',endpoint='stk_holdernumber',
        params=dict(ts_code='688981.SH',start_date='20250101',end_date='20250614'),
        economic_scope=dict(start='20250101',end='20250614'),availability_policy='revision_scan'),
        dict(collector='pr7',domain='margin_daily',endpoint='margin_detail',
        params=dict(ts_code='688981.SH',start_date='20250613',end_date='20250613'),
        economic_scope=dict(start='20250613',end='20250613'),availability_policy='next_session_publication')]
    inputs={r['domain']:dict(raw_batch_ids=[],contract_version=r['domain']+'.v1',config={},new_lineage=False) for r in requests}
    client=Client()
    with patch('axiom_data.event_source._retrieved_at',return_value='2025-06-14T06:00:00Z'):
        for case,selected in [('t_plus_1',requests[1:]),('late_data',requests[:1]),('interrupted_resume',requests)]:
            args=dict(case=case,run_id=case,snapshot_id=snapshot,source_requests=selected,
                domain_inputs={r['domain']:inputs[r['domain']] for r in selected},client=client)
            if case=='interrupted_resume':
                client.fail=True
                test.assertEqual(execute_daily_case(root,**args)['status'],'FAILED')
                client.fail=False
            test.assertEqual(execute_daily_case(root,**args)['status'],'CANDIDATE_BUILT')
    cases={case:dict(run_id=run,content_digest=_digest((root/'operations'/run/'daily.json').read_bytes()))
           for case,run in [('no_change','no-change'),('t_plus_1','t_plus_1'),('late_data','late_data'),('interrupted_resume','interrupted_resume')]}
    return dict(baseline_snapshot_id=snapshot,cases=cases)


class DailyAcceptanceTest(unittest.TestCase):
    def test_actual_outcomes_and_rejected_scenario_relabel(self):
        run=json.loads(Path('deprecated/history/reports/pr7/run_manifest.json').read_bytes())
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'data';shutil.copytree(fixture_root(run['source_root']),root)
            try:
                snapshot=prepare_baseline(root,run['refs']['snapshot_id'])
                evidence=daily_cases(self,root,snapshot)
                result=validate_daily_evidence(root,evidence,expected_baseline=snapshot)
                self.assertEqual(result['status'],'DAILY_EVIDENCE_VALIDATED')
                self.assertFalse(result['ready_for_consumption'])
                wrong=copy.deepcopy(evidence)
                wrong['cases']['late_data'],wrong['cases']['t_plus_1']=wrong['cases']['t_plus_1'],wrong['cases']['late_data']
                with self.assertRaises(ArtifactError):validate_daily_evidence(root,wrong,expected_baseline=snapshot)
                path=root/'operations/interrupted_resume/daily-case.json'
                state=json.loads(path.read_bytes());state['before']=None;path.write_text(json.dumps(state))
                with self.assertRaisesRegex(ArtifactError,'checkpoint'):
                    validate_daily_evidence(root,evidence,expected_baseline=snapshot)
            finally:
                for path in root.rglob('*'):
                    if path.is_dir():path.chmod(0o755)
