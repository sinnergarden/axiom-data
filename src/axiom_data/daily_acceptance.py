"""Evidence capture and semantic checks around the existing daily operation."""
import json
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from axiom_data.artifacts import (ArtifactError, _digest, _ensure_directory, _identity,
    _json_bytes, _layout, _safe_path, load_raw_batch, load_snapshot)
from axiom_data.consumption import SnapshotReader
from axiom_data.operations import daily, plan_daily, save_progress, normalize_source_request
from axiom_data.publication import writer
from axiom_data.pit import instant

CASES = {'t_plus_1', 'no_change', 'late_data', 'interrupted_resume'}


def execute_daily_case(root, *, case, run_id, snapshot_id, source_requests, domain_inputs,
                       client=None, observed_raw_batch_ids=None):
    """Run daily unchanged, retaining an actual failed checkpoint before resume.

    A failed first call returns its genuine stage result. Invoke this same entry
    with the same run and inputs after the supplier becomes available to resume.
    """
    if case not in CASES:
        raise ArtifactError('unknown daily acceptance case')
    root = _layout(root).root
    _identity('run_id', run_id)
    directory = root/'operations'/run_id
    path = _safe_path(root, directory/'daily-case.json')
    with writer(root):
        _ensure_directory(root, directory)
        evidence = json.loads(path.read_bytes()) if path.exists() else dict(
            schema_version='daily_case_evidence.v1', root=str(root.resolve()), case=case,
            run_id=run_id, baseline_snapshot_id=snapshot_id, before=None)
        if (evidence['case'] != case or evidence['baseline_snapshot_id'] != snapshot_id
                or evidence['root'] != str(root.resolve())):
            raise ArtifactError('daily evidence resume binding mismatch')
        previous = _safe_path(root, directory/'daily.json')
        if previous.exists() and evidence['before'] is None:
            content = previous.read_bytes()
            old = json.loads(content)
            if old['status'] in {'FAILED', 'RUNNING'}:
                collection = _safe_path(root, directory/'collection.json').read_bytes()
                evidence['before'] = dict(daily=content.decode(), collection=collection.decode(),
                    daily_digest=_digest(content), collection_digest=_digest(collection))
        save_progress(path, evidence)
    result = daily(root, run_id=run_id, snapshot_id=snapshot_id, source_requests=source_requests,
        domain_inputs=domain_inputs, client=client, observed_raw_batch_ids=observed_raw_batch_ids)
    with writer(root):
        evidence['after_digest'] = _digest(_safe_path(root, directory/'daily.json').read_bytes())
        save_progress(path, evidence)
    return result


def _case(root, case, ref, parent):
    if not isinstance(ref, dict) or set(ref) != {'run_id', 'content_digest'}:
        raise ArtifactError('daily case requires an exact execution ref')
    run_id = _identity('run_id', ref['run_id'])
    directory = root/'operations'/run_id
    content = _safe_path(root, directory/'daily.json').read_bytes()
    record = json.loads(content)
    evidence = json.loads(_safe_path(root, directory/'daily-case.json').read_bytes())
    if (ref['content_digest'] != _digest(content) or evidence.get('after_digest') != _digest(content)
            or evidence.get('root') != str(root.resolve()) or evidence.get('case') != case
            or evidence.get('run_id') != run_id or record.get('run_id') != run_id
            or evidence.get('baseline_snapshot_id') != parent.snapshot.ref.snapshot_id):
        raise ArtifactError('daily durable evidence binding mismatch')
    frozen = record['plan']
    source = frozen['source_plan']
    requests = [normalize_source_request({k:r[k] for k in
        ('collector','domain','endpoint','params','economic_scope','availability_policy')})
        for r in source['source_requests']]
    expected_plan = plan_daily(root, parent.snapshot.ref.snapshot_id, source_requests=requests)
    if (source != expected_plan or record['plan_digest'] != _digest(_json_bytes(frozen))
            or record['status'] not in {'NO_CHANGE','CANDIDATE_BUILT'}
            or record.get('ready_for_consumption') is not False
            or record.get('stage') != 'REQUIRED_VIEWS_AND_FULL_ADMISSION'):
        raise ArtifactError('daily frozen plan or terminal candidate mismatch')
    collection = json.loads(_safe_path(root, directory/'collection.json').read_bytes())
    collected = record['collected_raw_batch_ids']
    projection=dict(requests=requests,request_keys=source['unconfirmed_required_requests'])
    if 'observed_raw_batch_ids' in frozen:
        projection['observed_raw_batch_ids']=frozen['observed_raw_batch_ids']
    if (collection['plan_digest'] != _digest(_json_bytes(projection))
            or collection['status'] != 'COMPLETE' or collection['completed'] != collected
            or collection['requests'] != requests or set(collected) != set(source['unconfirmed_required_requests'])):
        raise ArtifactError('daily source collection is incomplete')
    result = SnapshotReader(root, record['snapshot_id'])
    load_snapshot(root, record['snapshot_id'])
    ids = {d:c.ref.commit_id for d,c in result.commits.items()}
    changed = sorted(d for d,c in parent.commits.items() if ids[d] != c.ref.commit_id)
    if record['domain_commit_ids'] != ids or record['changed_domains'] != changed:
        raise ArtifactError('daily outcome differs from actual Snapshot')
    resolved = json.loads(_json_bytes(frozen['domain_inputs']))
    raw_by_request = {}
    from axiom_data.operations import _check_collected
    for request in source['source_requests']:
        raw_id = collected[request['request_id']]
        raw = load_raw_batch(root, raw_id)
        _check_collected(raw, request)
        raw_by_request[request['request_id']] = raw
        inputs = resolved[request['domain']]['raw_batch_ids']
        if raw_id not in inputs:
            inputs.append(raw_id)
    if resolved != record['resolved_domain_inputs']:
        raise ArtifactError('daily resolved inputs differ from frozen plan and Raw')
    build = json.loads(_safe_path(root, directory/'build.json').read_bytes())
    build_plan = dict(parent_snapshot_id=parent.snapshot.ref.snapshot_id, domains=resolved)
    if (build['plan'] != build_plan or build['plan_digest'] != _digest(_json_bytes(build_plan))
            or build['status'] != 'CANDIDATE_BUILT' or build['snapshot_id'] != record['snapshot_id']
            or build['domain_commit_ids'] != ids):
        raise ArtifactError('daily candidate execution binding mismatch')
    for domain, commit in result.commits.items():
        if domain not in resolved:
            if commit.ref.commit_id != parent.commits[domain].ref.commit_id:
                raise ArtifactError('daily changed an unplanned domain')
            continue
        spec = resolved[domain]
        if (commit.ref.contract_version != spec['contract_version'] or
                any(commit.manifest['builder_config'].get(k) != v for k,v in spec['config'].items())):
            raise ArtifactError('daily candidate contract/config mismatch')
        if domain in changed:
            from axiom_data.artifacts import _commit_ref
            expected_parent = None if spec['new_lineage'] else _commit_ref(parent.commits[domain])
            if (commit.manifest['parent_commit_ref'] != expected_parent or
                    [r['raw_batch_id'] for r in commit.manifest['ordered_raw_batch_refs']] != spec['raw_batch_ids']):
                raise ArtifactError('daily candidate parent/input mismatch')
        refs = {r['raw_batch_id'] for r in commit.manifest['ordered_raw_batch_refs']}
        if not set(resolved[domain]['raw_batch_ids']) <= refs:
            # An unchanged content-addressed commit can retain its already
            # incorporated inputs in its lineage rather than its final delta.
            pending = [commit]; seen = set()
            from axiom_data.artifacts import load_domain_commit
            while pending:
                item = pending.pop()
                if item.ref.commit_id in seen:
                    continue
                seen.add(item.ref.commit_id)
                refs.update(r['raw_batch_id'] for r in item.manifest['ordered_raw_batch_refs'])
                ancestor = item.manifest.get('parent_commit_ref')
                if ancestor:
                    pending.append(load_domain_commit(root, domain, ancestor['domain_commit_id']))
            if not set(resolved[domain]['raw_batch_ids']) <= refs:
                raise ArtifactError('daily candidate does not consume declared Raw')
    if case == 'no_change':
        if changed or record['status'] != 'NO_CHANGE' or result.snapshot.ref != parent.snapshot.ref:
            raise ArtifactError('no-change case changed the baseline')
    else:
        if not changed or record['status'] != 'CANDIDATE_BUILT':
            raise ArtifactError('daily change case produced no change')
    if case == 't_plus_1':
        proven = False
        for request in source['source_requests']:
            if request['availability_policy'] != 'next_session_publication':
                continue
            domain = request['domain']
            raw = raw_by_request[request['request_id']]
            old = {r['revision_id'] for r in parent.commits[domain].rows}
            for row in result.commits[domain].rows:
                if (row['revision_id'] not in old and row.get('source_ref') == raw.ref.raw_batch_id
                        and row.get('session') and instant(raw.manifest['retrieved_at']).astimezone(ZoneInfo('Asia/Shanghai')).date() == date.fromisoformat(row['session']) + timedelta(days=1)):
                    proven = True
        if not proven:
            raise ArtifactError('T+1 requires an actual newly observed prior-session fact')
    if case == 'late_data':
        proven = False
        for request in source['source_requests']:
            if request['availability_policy'] != 'revision_scan':
                continue
            domain = request['domain']; raw = raw_by_request[request['request_id']]
            old = {r['revision_id'] for r in parent.commits[domain].rows}
            for row in result.commits[domain].rows:
                if (row['revision_id'] not in old and row.get('source_ref') == raw.ref.raw_batch_id
                        and row.get('report_period') and row.get('announcement')
                        and row['report_period'] < row['announcement']
                        and date.fromisoformat(row['announcement']) < instant(raw.manifest['retrieved_at']).date()):
                    proven = True
        if not proven:
            raise ArtifactError('late-data requires an actual late historical revision')
    if case == 'interrupted_resume':
        before = evidence.get('before')
        if not before or any(_digest(before[k].encode()) != before[k+'_digest'] for k in ('daily','collection')):
            raise ArtifactError('interrupted resume requires captured checkpoint bytes')
        previous = json.loads(before['daily']); partial = json.loads(before['collection'])
        if (previous['plan'] != frozen or previous['plan_digest'] != record['plan_digest']
                or previous.get('run_id') != run_id or partial.get('run_id') != run_id
                or partial['plan_digest'] != collection['plan_digest']
                or previous['status'] not in {'FAILED','RUNNING'} or partial['status'] == 'COMPLETE'
                or partial['requests'] != requests or not partial['completed']
                or len(partial['completed']) >= len(collected)
                or any(collected.get(k) != v for k,v in partial['completed'].items())):
            raise ArtifactError('interrupted resume did not retain and reuse partial work')
    return dict(run_id=run_id, status=record['status'], snapshot_id=result.snapshot.ref.snapshot_id,
                changed_domains=changed, consumption='REQUIRED_VIEWS_AND_FULL_ADMISSION_PENDING')


def validate_daily_evidence(root, evidence, *, expected_baseline):
    """Check all four actual operation histories, never caller scenario labels."""
    root = _layout(root).root
    if evidence.get('baseline_snapshot_id') != expected_baseline or set(evidence.get('cases', {})) != CASES:
        raise ArtifactError('daily evidence baseline/case mismatch')
    if len({r['run_id'] for r in evidence['cases'].values()}) != len(CASES):
        raise ArtifactError('distinct daily case runs required')
    load_snapshot(root, expected_baseline)
    parent = SnapshotReader(root, expected_baseline)
    results = {case:_case(root, case, ref, parent) for case,ref in evidence['cases'].items()}
    return dict(status='DAILY_EVIDENCE_VALIDATED', baseline_snapshot_id=expected_baseline,
                cases=results, ready_for_consumption=False)
