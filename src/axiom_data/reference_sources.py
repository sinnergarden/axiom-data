"""Reference request execution through the ordinary checkpoint collector."""
import fcntl
import json

from axiom_data.artifacts import (ArtifactError, _digest, _json_bytes, _identity,
    _layout, _ensure_directory, _safe_path, load_raw_batch)
from axiom_data.gate_a import _scope
from axiom_data.operations import _request, _save, collect_requests
from axiom_data.source_completeness import (
    completeness_policy, current_contract_binding, page_evidence,
    validate_raw_completeness)


def plan_reference_requests(scope, page_size=1000):
    """Freeze the existing reference selectors and SW page continuation policy."""
    scope = _scope(scope)
    policy = completeness_policy('tushare_industry_qualification.v1', 'index_member_all')
    if (policy['pagination'] != 'limit_offset'
            or policy['terminal_page_rule'] != 'contiguous_offsets_then_short_page'
            or type(page_size) is not int or not 0 < page_size < policy['limit']):
        raise ArtifactError('unsupported SW page continuation policy or page size')
    economic = {key: scope[value].replace('-', '') for key, value in
                [('start', 'start_session'), ('end', 'end_session')]}

    def spec(family, domain, endpoint, params):
        result = dict(collector=family, domain=domain, endpoint=endpoint, params=params,
                      economic_scope=economic, availability_policy='reference_observation')
        _request(result)
        return result

    exchanges = sorted({'SSE' if s.endswith('.SH') else 'SZSE' for s in scope['symbols']})
    calendar_exchanges = sorted(set(exchanges) | {
        'SSE' if s.endswith('.SH') else 'SZSE' for s in scope['benchmarks']})
    requests = [spec('market', 'trading_calendar', 'trade_cal',
                     dict(exchange=e, start_date=economic['start'], end_date=economic['end']))
                for e in calendar_exchanges]
    requests += [spec('market', 'security_master', 'stock_basic',
                      dict(exchange=e, list_status=status))
                 for e in exchanges for status in ('L', 'D', 'P')]
    requests += [spec('industry_qualification', 'industry_membership', 'index_classify',
                      dict(src='SW2021', level=level)) for level in ('L1', 'L2', 'L3')]
    groups = [spec('industry_qualification', 'industry_membership', 'index_member_all',
                   dict(is_new=mode, limit=str(page_size), offset='0')) for mode in ('Y', 'N')]
    plan = dict(scope=scope, requests=requests, page_groups=groups,
                policy_binding=current_contract_binding())
    return dict(plan, plan_digest=_digest(_json_bytes(plan)))


def collect_reference_sources(data_root, *, run_id, plan, client=None, context=None):
    """Collect stable child requests; only the shared validator closes SW groups.

    Per-page collection COMPLETE means a stored response. The reference stage
    remains PARTIAL until all page groups pass their existing evidence validator.
    """
    _identity('run_id', run_id)
    try:
        expected = plan_reference_requests(plan['scope'], int(plan['page_groups'][0]['params']['limit']))
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        raise ArtifactError('invalid reference source plan') from exc
    if plan != expected:
        raise ArtifactError('reference plan differs from public planner')
    root = _layout(data_root).root
    directory = _safe_path(root, root / 'operations' / run_id)
    _ensure_directory(root, directory)
    frozen = json.loads(_json_bytes(dict(plan=plan, context=context)))
    plan = frozen['plan']
    if client is None:
        from axiom_data.source_client import PacedSourceClient
        from axiom_data.tushare import TushareCollector
        client = PacedSourceClient(TushareCollector(root)._client())
    lock_path = _safe_path(root, directory / 'collection.lock')
    with lock_path.open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ArtifactError('reference collection already active') from exc
        path = _safe_path(root, directory / 'source_plan.json')
        if path.exists():
            if json.loads(path.read_bytes()) != frozen:
                raise ArtifactError('reference resume plan/context changed')
        else:
            _save(path, frozen)
        progress = _safe_path(root, directory / 'progress.json')
        state = dict(run_id=run_id, stage='SOURCE_COLLECTION', status='RUNNING',
                     plan_digest=_digest(_json_bytes(frozen)), ready_for_consumption=False,
                     raw_batch_ids={d: [] for d in ('trading_calendar', 'security_master', 'industry_membership')},
                     request_count=0, raw_batch_count=0, rows=0, failed={})
        _save(progress, state)

        def collect(spec):
            key = _request(spec)
            # Keep the full request identity: selectors, source binding and scope
            # all participate. Completed child checkpoints are revalidated on rerun.
            child = run_id + '-' + key.removeprefix('sha256:')
            result = collect_requests(root, run_id=child, requests=[spec], client=client)
            if result['status'] != 'COMPLETE':
                state.update(status='PARTIAL', failed=result['failed'], pending_request=key)
                _save(progress, state)
                return None
            raw = load_raw_batch(root, result['completed'][key])
            state['raw_batch_ids'][spec['domain']].append(raw.ref.raw_batch_id)
            state['request_count'] += 1
            state['raw_batch_count'] += 1
            state['rows'] += len(json.loads(raw.payload))
            _save(progress, state)
            return raw

        try:
            for spec in plan['requests']:
                raw = collect(spec)
                if raw is None:
                    return state
                if not validate_raw_completeness(raw)['complete']:
                    raise ArtifactError('reference request requires complete source evidence')
            for first in plan['page_groups']:
                pages = []
                spec = first
                while True:
                    raw = collect(spec)
                    if raw is None:
                        return state
                    pages.append(raw)
                    params = spec['params']
                    limit = int(params['limit'])
                    count = len(json.loads(raw.payload))
                    if count < limit:
                        # This is a candidate terminal response, not an admission:
                        # selectors, offsets, duplicates and terminal coverage are
                        # decided exclusively by the existing shared validator.
                        page_evidence(pages)
                        break
                    if count > limit:
                        page_evidence(pages)  # fail through the same contract
                        raise ArtifactError('oversized SW page')
                    spec = dict(first, params=dict(first['params'], offset=str(int(params['offset']) + limit)))
                    _request(spec)
            state.update(status='COMPLETE', failed={})
            _save(progress, state)
            return state
        except Exception as exc:
            state.update(status='PARTIAL', failed={'reference': {'error_type': type(exc).__name__}})
            _save(progress, state)
            raise
