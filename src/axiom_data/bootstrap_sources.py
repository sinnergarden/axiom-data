"""Explicit source plans for the accepted V1 domains, independent of wall time."""
from datetime import date, timedelta
from axiom_data.artifacts import ArtifactError
from axiom_data.consumption import _symbols, _session
from axiom_data.operations import _request
from axiom_data.source_completeness import SourceCompletenessError, validate_raw_completeness


def _windows(start, end, years):
    cursor=date.fromisoformat(start);stop=date.fromisoformat(end)
    while cursor<=stop:
        upper=min(stop,date(cursor.year+years-1,12,31))
        yield cursor.strftime('%Y%m%d'),upper.strftime('%Y%m%d')
        cursor=upper+timedelta(days=1)


def _quarters(start, end):
    cursor=date.fromisoformat(start);stop=date.fromisoformat(end)
    while cursor<=stop:
        month=((cursor.month-1)//3+1)*3
        following=date(cursor.year+int(month==12),month%12+1,1)
        upper=min(stop,following-timedelta(days=1))
        yield cursor.strftime('%Y%m%d'),upper.strftime('%Y%m%d')
        cursor=upper+timedelta(days=1)


def plan_truncated_raw_split(raw):
    """Return two deterministic child requests; never collect or replace the Raw.

    A single-day response at the cap remains blocked because this profile has
    no demonstrated pagination or finer supported selector.
    """
    import copy
    import json
    from axiom_data.pr6_source import source_date
    from axiom_data.source_completeness import completeness_policy
    manifest=raw.manifest;request=manifest['request'];params=request['params']
    if request.get('endpoint')!='fina_indicator':
        raise ArtifactError('bounded split planner requires fina_indicator Raw')
    policy=completeness_policy(manifest['source_profile_version'],'fina_indicator')
    rows=json.loads(raw.payload)
    if (manifest.get('status')!='success' or policy['status']!='established'
        or not isinstance(rows,list) or len(rows)<policy['limit']):
        raise ArtifactError('scope split requires a retained response at the source cap')
    try:
        validate_raw_completeness(raw)
    except SourceCompletenessError:
        pass
    else:
        raise ArtifactError('Raw is not rejected by source completeness admission')
    if set(params)!={'ts_code','start_date','end_date'}:
        raise ArtifactError('unsplittable response scope; source qualification required')
    start=date.fromisoformat(source_date(params['start_date']))
    end=date.fromisoformat(source_date(params['end_date']))
    if start>=end:
        raise ArtifactError('unsplittable single-day response at source cap')
    middle=start+(end-start)//2
    children=[]
    for first,last in ((start,middle),(middle+timedelta(days=1),end)):
        child=copy.deepcopy(params)
        child.update(start_date=first.strftime('%Y%m%d'),end_date=last.strftime('%Y%m%d'))
        family={'tushare_pr6.v1':'pr6','tushare_fina_indicator.v1':'pr6_indicator'}.get(manifest['source_profile_version'])
        if family is None:raise ArtifactError('unsupported indicator split SourceProfile')
        spec={'collector':family,'domain':'financial_events','endpoint':'fina_indicator',
              'params':child,'economic_scope':{'start':child['start_date'],'end':child['end_date']},
              'availability_policy':'revision_scan'}
        _request(spec);children.append(spec)
    return {'schema_version':'source_scope_split.v1','parent_raw_batch_id':raw.ref.raw_batch_id,
            'status':'NEEDS_COLLECTION','ready_for_consumption':False,'requests':children}


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
            for ep in ('income','balancesheet','cashflow'):
                p={'ts_code':symbol,'start_date':first,'end_date':last}
                p['report_type']='1'
                add('pr6','financial_events',ep,p,'revision_scan',financial_start,end)
        for first,last in _quarters(financial_start,end):
            add('pr6_indicator','financial_events','fina_indicator',
                {'ts_code':symbol,'start_date':first,'end_date':last},'revision_scan',financial_start,end)
        for domain,ep,policy in [('holder_count_events','stk_holdernumber','revision_scan'),
                                 ('top_holders_reports','top10_holders','revision_scan'),
                                 ('margin_daily','margin_detail','next_session_publication'),
                                 ('moneyflow_daily','moneyflow','session_close'),
                                 ('forecast_observations','forecast','revision_scan')]:
            # Keep reports below the existing endpoint caps, including Top10's
            # ten rows per report. The collector still rejects an at-cap result.
            windows=_windows(start,end,5) if ep=='top10_holders' else [(bounds['start_date'],bounds['end_date'])]
            for first,last in windows:
                add('pr7_holder_v3' if domain=='holder_count_events' else 'pr7',domain,ep,
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
    from axiom_data.operations import collect_requests,_save,_supersede_collection_request
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

        def collect_bounded(batch, specs, local):
            """Revalidate checkpoints and replace only rejected cap scopes."""
            result=collect_requests(root,run_id=batch,requests=specs,client=client)
            local['batches'].append(batch)
            for spec in specs:
                key=_request(spec)
                record=result['request_states'][key]
                identity=record['raw_batch_id']
                if record['state']=='VALID_COMPLETE':
                    raw=load_raw_batch(root,identity)
                    validate_raw_completeness(raw)
                    local['completed_raw_batch_ids'].append(identity)
                    state['validated_requests']+=1
                    state['validated_rows']+=len(json.loads(raw.payload))
                    continue
                if record['state'] in {'NEEDS_SPLIT','SUPERSEDED_BY_SPLIT'}:
                    split=record['split']
                    child_batch=run_id+'-split-'+_digest(_json_bytes({
                        'parent_batch':batch,'split':split}))[7:31]
                    _supersede_collection_request(root,run_id=batch,key=key,split=split,child_run_id=child_batch)
                    local['splits'].append(dict(split,run_id=child_batch))
                    _save(directory/'progress.json',state)
                    if not collect_bounded(child_batch,split['requests'],local):return False
                else:
                    failure=record['failure']
                    state.update(status='FAILED',failure={'kind':'collection_incomplete',
                        'batch':batch,'failed':{key:failure}})
                    if record['state']=='FAILED_TERMINAL' and (failure or {}).get('error_type')=='SourceCompletenessError':
                        state['failure'].update(kind='possible_truncation',raw_batch_id=identity,
                                                split_status='UNSPLITTABLE_SOURCE_SCOPE')
                    return False
            return True

        for domain in domains:
            requests=plan['requests_by_domain'][domain]
            local={'status':'RUNNING','batches':[],'splits':[],'completed_raw_batch_ids':[]}
            state['domains'][domain]=local
            for offset in range(0,len(requests),50):
                batch=run_id+'-'+domain+'-'+str(offset)
                if not collect_bounded(batch,requests[offset:offset+50],local):
                    local['status']='FAILED'
                    _save(directory/'progress.json',state)
                    return state
                _save(directory/'progress.json',state)
            local['status']='COMPLETE'
        state['status']='COMPLETE';_save(directory/'progress.json',state)
        return state
