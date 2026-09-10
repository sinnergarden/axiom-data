"""Explicit source plans for the accepted V1 domains, independent of wall time."""
from datetime import date, timedelta
from axiom_data.artifacts import ArtifactError
from axiom_data.consumption import _symbols, _session
from axiom_data.operations import _request


def _windows(start, end, years):
    cursor=date.fromisoformat(start);stop=date.fromisoformat(end)
    while cursor<=stop:
        upper=min(stop,date(cursor.year+years-1,12,31))
        yield cursor.strftime('%Y%m%d'),upper.strftime('%Y%m%d')
        cursor=upper+timedelta(days=1)


def plan_bootstrap_sources(*, symbols, start_session, end_session,
                           financial_observation_start, benchmarks, universe_ids):
    """Plan all remaining observations; validated industry/reference refs are reused.

    Date bounds are explicit. Financial lookback is separately declared because
    a 2014 TTM consumer needs earlier observations. Plans describe acquisition,
    not proof of source completeness, PIT safety or baseline admission.
    """
    selected=_symbols(symbols);_symbols(benchmarks);_symbols(universe_ids)
    start=_session(start_session,'start_session');end=_session(end_session,'end_session')
    financial_start=_session(financial_observation_start,'financial_observation_start')
    if start>end or financial_start>start:raise ArtifactError('invalid bootstrap bounds/lookback')
    result={}
    def add(family,domain,endpoint,params,policy,first=start,last=end):
        spec=dict(collector=family,domain=domain,endpoint=endpoint,params=params,
                  economic_scope={'start':first.replace('-',''),'end':last.replace('-','')},availability_policy=policy)
        _request(spec)
        result.setdefault(domain,[]).append(spec)
    bounds={'start_date':start.replace('-',''),'end_date':end.replace('-','')}
    for symbol in selected:
        params=dict(bounds,ts_code=symbol)
        for ep in ('daily','adj_factor','daily_basic','stk_limit','suspend_d'):
            add('market','market_daily',ep,params,'session_close')
        for domain,ep in [('security_status','daily'),('security_status','suspend_d'),
                          ('price_limits','stk_limit'),('adjustment_factors','adj_factor'),('security_capital','daily_basic')]:
            add('dm1',domain,ep,params,'session_close')
        add('dm1','corporate_actions','dividend',{'ts_code':symbol},'revision_scan')
        add('pr6','valuation_daily','daily_basic',params,'session_close')
        for first,last in _windows(financial_start,end,5):
            for ep in ('income','balancesheet','cashflow','fina_indicator'):
                p={'ts_code':symbol,'start_date':first,'end_date':last}
                if ep!='fina_indicator':p['report_type']='1'
                add('pr6','financial_events',ep,p,'revision_scan',financial_start,end)
        for domain,ep,policy in [('holder_count_events','stk_holdernumber','revision_scan'),
                                 ('top_holders_reports','top10_holders','revision_scan'),
                                 ('margin_daily','margin_detail','next_session_publication'),
                                 ('moneyflow_daily','moneyflow','session_close'),
                                 ('forecast_observations','forecast','revision_scan')]:
            # Keep reports below the existing endpoint caps, including Top10's
            # ten rows per report. The collector still rejects an at-cap result.
            windows=_windows(start,end,5) if ep=='top10_holders' else [(bounds['start_date'],bounds['end_date'])]
            for first,last in windows:
                add('pr7_holder' if domain=='holder_count_events' else 'pr7',domain,ep,
                    {'ts_code':symbol,'start_date':first,'end_date':last},policy)
    for symbol in benchmarks:
        add('dm1','benchmark_daily','index_daily',dict(bounds,ts_code=symbol),'session_close')
    # Index-weight history is sampled by its published observation sessions;
    # collecting monthly bounds does not invent a daily membership observation.
    cursor=date.fromisoformat(start);stop=date.fromisoformat(end)
    while cursor<=stop:
        next_month=date(cursor.year+int(cursor.month==12),cursor.month%12+1,1)
        upper=min(stop,next_month-timedelta(days=1))
        for symbol in universe_ids:
            add('pr6','universe_membership','index_weight',{'index_code':symbol,
                'start_date':cursor.strftime('%Y%m%d'),'end_date':upper.strftime('%Y%m%d')},'reference_observation')
        cursor=next_month
    return {'schema_version':'v1_bootstrap_sources.v1','scope':{'symbols':list(selected),
            'start_session':start,'end_session':end,'financial_observation_start':financial_start,
            'benchmarks':list(benchmarks),'universe_ids':list(universe_ids)},
            'requests_by_domain':result,'required_reused_domains':['security_master','trading_calendar','industry_membership'],
            'ready_for_consumption':False}


def collect_bootstrap_sources(data_root, *, run_id, plan, domains, client=None):
    """Resume bounded source batches and validate their terminal Raw artifacts.

    This is the collection stage, never a baseline acceptance or pointer update.
    The caller may run distinct domain sets; each has an exclusive run lock.
    """
    import fcntl
    import json
    from pathlib import Path
    from axiom_data.artifacts import _identity,_digest,_json_bytes,load_raw_batch,_layout,_ensure_directory
    from axiom_data.operations import collect_requests,_save
    _identity('run_id',run_id)
    if not domains or len(set(domains))!=len(domains) or not set(domains)<=set(plan['requests_by_domain']):
        raise ArtifactError('explicit unique planned domains required')
    # Validate every request before writing an execution record or contacting a source.
    for domain in domains:
        for spec in plan['requests_by_domain'][domain]:
            if spec['domain']!=domain:raise ArtifactError('bootstrap domain/request mismatch')
            _request(spec)
    if client is None:
        from axiom_data.source_client import PacedSourceClient
        from axiom_data.tushare import TushareCollector
        client=PacedSourceClient(TushareCollector(data_root)._client())
    root=_layout(data_root).root;directory=root/'operations'/run_id
    _ensure_directory(root,directory)
    frozen={'plan':plan,'domains':domains};digest=_digest(_json_bytes(frozen))
    with (directory/'collection.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as exc:raise ArtifactError('source run is already active') from exc
        plan_path=directory/'source_plan.json'
        if plan_path.exists():
            if json.loads(plan_path.read_bytes())!=frozen:raise ArtifactError('source run plan changed')
        else:_save(plan_path,frozen)
        state={'run_id':run_id,'stage':'SOURCE_COLLECTION','status':'RUNNING','plan_digest':digest,
               'ready_for_consumption':False,'domains':{},'validated_requests':0,'validated_rows':0}
        _save(directory/'progress.json',state)
        for domain in domains:
            requests=plan['requests_by_domain'][domain];local={'status':'RUNNING','batches':[]}
            state['domains'][domain]=local
            for offset in range(0,len(requests),50):
                batch=run_id+'-'+domain+'-'+str(offset)
                result=collect_requests(root,run_id=batch,requests=requests[offset:offset+50],client=client)
                local['batches'].append(batch)
                for identity in result['completed'].values():
                    raw=load_raw_batch(root,identity);rows=json.loads(raw.payload)
                    # Financial endpoints can cap results below the generic Raw
                    # envelope's limit. Keep the response, but never admit it.
                    limit=100 if domain=='financial_events' else 5000 if domain=='universe_membership' else 6000
                    if len(rows)>=limit:
                        state.update(status='FAILED',failure={'kind':'possible_truncation','raw_batch_id':identity,'batch':batch})
                        _save(directory/'progress.json',state)
                        return state
                    state['validated_requests']+=1;state['validated_rows']+=len(rows)
                if result['status']!='COMPLETE':
                    state.update(status='FAILED',failure={'kind':'collection_incomplete','batch':batch,'failed':result['failed']})
                    _save(directory/'progress.json',state)
                    return state
                _save(directory/'progress.json',state)
            local['status']='COMPLETE'
        state['status']='COMPLETE';_save(directory/'progress.json',state)
        return state
