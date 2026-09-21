"""Public Snapshot-bound PR7 facts and numeric Qlib projection."""
import json
from pathlib import Path
from datetime import date,timedelta
from importlib.resources import files
from axiom_data.artifacts import (ArtifactError,_layout,_json_bytes,_digest,_identity_digest,_derived_identity,
    _timestamp,_write_file,_write_manifest,_publish_directory,_load_manifest,_validate_manifest_identity,_safe_path,_identity,load_raw_batch)
from axiom_data.consumption import SnapshotReader,_feature_bytes,_qlib_symbol,_symbols,_session
from axiom_data.views import DerivedView,DerivedViewRef
from axiom_data.domains.pr7 import PR7_DOMAINS,DAILY_DOMAINS
from axiom_data.contracts import load_contract
from axiom_data.pit import instant,fingerprint
from axiom_data.pr7_source import select_pr7_revisions

LEAF_DOMAINS={**{'holder.'+f:d for f,d in [('number','holder_count_events'),('top10_ratio','top_holders_reports')]},
    **{'margin.'+f:'margin_daily' for f in load_contract('margin_daily.v1')['value_units']},
    **{'moneyflow.'+f:'moneyflow_daily' for f in load_contract('moneyflow_daily.v1')['value_units']},
    **{'forecast.'+f:'forecast_observations' for f in load_contract('forecast_observations.v1')['value_units']}}
NUMERIC_FIELDS=tuple(f for f in LEAF_DOMAINS if not f.startswith('forecast.'))


def request_coverage(reader,domain,symbol,session):
    """Admit bounded requests, including explicit empty supplier responses."""
    if symbol not in {r['symbol'] for r in reader.security_master()}:raise ArtifactError('unknown security')
    if domain not in reader.commits:raise ArtifactError('Snapshot lacks PR7 domain')
    # Only this checked Reader owns the index. New Readers validate their closure
    # again; no disk cache or execution report can supply request authority.
    if not hasattr(reader,'_pr7_request_intervals'):reader._pr7_request_intervals={}
    if domain not in reader._pr7_request_intervals:
        identity=reader.commits[domain].ref.commit_id;refs=set()
        while identity:
            node=reader._verified_lineage[(domain,identity)]
            refs.update(node['raw_batch_ids'])
            identity=node['parent_commit_id']
        intervals={}
        for ref in sorted(refs):
            raw=load_raw_batch(reader.data_root,ref);params=raw.manifest['request']['params']
            intervals.setdefault(params['ts_code'],[]).append((params['start_date'],params['end_date']))
        reader._pr7_request_intervals[domain]=intervals
    day=session.replace('-','')
    if any(start<=day<=end for start,end in reader._pr7_request_intervals[domain].get(symbol,())):return
    raise ArtifactError('INSUFFICIENT_SCOPE: no bounded supplier request for '+symbol+' '+session)


def dependency_session(reader, domain, session):
    """Plan against the source bound frozen in this Snapshot's DomainCommit.

    An absent bound retains legacy strict request coverage. Never infer a
    cutoff from returned rows or a different security's observed coverage.
    """
    if domain not in reader.commits:
        raise ArtifactError('Snapshot lacks PR7 domain')
    end = reader.commits[domain].manifest['builder_config'].get('end_session')
    return min(session, _session(end, 'source end_session')) if end is not None else session


def exchange_sessions(reader,symbols,start,end):
    """Validate each requested exchange for every calendar day before projection."""
    from axiom_data.domains.market import _symbol
    start=_session(start,'start_session');end=_session(end,'end_session')
    if start>end:raise ArtifactError('reversed calendar scope')
    master=reader.security_master();calendar=reader.trading_calendar()
    days=[];day=date.fromisoformat(start)
    while day<=date.fromisoformat(end):
        days.append(day.isoformat());day+=timedelta(days=1)
    result={}
    for symbol in _symbols(symbols):
        identities=[r for r in master if r['symbol']==symbol]
        if not identities:raise ArtifactError('unknown security')
        if len(identities)!=1:raise ArtifactError('ambiguous security exchange mapping')
        exchange=identities[0]['exchange']
        if exchange not in {'SSE','SZSE'}:raise ArtifactError('unknown exchange')
        _symbol(symbol,exchange)
        rows=[r for r in calendar if r['exchange']==exchange and start<=r['session']<=end]
        by_day={r['session']:r for r in rows}
        if len(rows)!=len(by_day) or set(by_day)!=set(days):
            raise ArtifactError('INSUFFICIENT_SCOPE: '+exchange+' calendar coverage')
        if any(type(r['is_open']) is not bool for r in rows):raise ArtifactError('invalid exchange session state')
        result[symbol]={'exchange':exchange,'sessions':[d for d in days if by_day[d]['is_open']]}
    return result


def select_latest_report(period_winners,target_session):
    """PIT has selected each period's revision; economic period now takes precedence."""
    candidates=[r for r in period_winners if r['report_period']<=target_session]
    return max(candidates,key=lambda r:r['report_period']) if candidates else None


def leaf_facts(reader,leaf,*,symbol,target_session,knowledge_cutoff,pit_policy):
    if leaf not in LEAF_DOMAINS:raise ArtifactError('unknown PR7 leaf')
    _session(target_session,'target_session');instant(knowledge_cutoff)
    domain=LEAF_DOMAINS[leaf];field=leaf.split('.',1)[1]
    if not exchange_sessions(reader,[symbol],target_session,target_session)[symbol]['sessions']:
        raise ArtifactError('CLOSED_SESSION: '+symbol+' '+target_session)
    source_session=dependency_session(reader,domain,target_session)
    request_coverage(reader,domain,symbol,source_session)
    bounds={'start_session':source_session,'end_session':source_session} if domain in DAILY_DOMAINS else {}
    selected=reader.as_of(domain,symbols=[symbol],pit_policy=pit_policy,knowledge_cutoff=knowledge_cutoff,**bounds)
    return _leaf_metadata(reader,leaf,symbol,target_session,knowledge_cutoff,pit_policy,selected,source_session=source_session)


def _leaf_metadata(reader,leaf,symbol,target_session,knowledge_cutoff,pit_policy,selected,*,source_session=None):
    domain=LEAF_DOMAINS[leaf];field=leaf.split('.',1)[1]
    candidates=[r for r in selected if (r['session']==target_session if domain in DAILY_DOMAINS else
        domain=='forecast_observations' or r['report_period']<=target_session)]
    row=(select_latest_report(selected,target_session) if domain in {'holder_count_events','top_holders_reports'} else
         max(candidates,key=lambda r:(r['announcement'] or r['session'],r['report_period'] or r['session'])) if candidates else None)
    value=row['values'][field] if row else None
    reason=row['missing_reasons'].get(field) if row else 'no_observation_at_cutoff'
    if domain in DAILY_DOMAINS and source_session is not None and source_session < target_session:
        reason='source_scope_not_available'
    contract_version=reader.commits[domain].ref.contract_version
    metadata={'leaf':leaf,'symbol':symbol,'target_session':target_session,'snapshot_id':reader.snapshot.ref.snapshot_id,
        'contract_version':contract_version,'domain_commit_id':reader.commits[domain].ref.commit_id,
        'value':value,'unit':load_contract(contract_version)['value_units'][field],
        'validity':'valid' if value is not None else 'missing','missing_reason':reason,
        'pit_policy':pit_policy,'knowledge_cutoff':instant(knowledge_cutoff).isoformat(),
        'pit_qualification':row['pit_qualification'] if row else 'unknown',
        'usable_at':row['usable_from'] if row else None,
        'source_ref':row['source_ref'] if row else None,'revision_ref':row['revision_id'] if row else None,
        'observation_ref':row['observation_ref'] if row else None,
        'report_period':row['report_period'] if row else None,
        'quality':row['group_completeness'] if row else 'no_visible_fact'}
    if domain=='top_holders_reports':
        metadata['holders']=row['holders'] if row else []
        metadata['component_refs']=[{'report_revision':row['revision_id'],'holder_id':h['holder_id']} for h in row['holders']] if row else []
        metadata['derived_ref']=fingerprint({'snapshot':reader.snapshot.ref.snapshot_id,'revision':row['revision_id'],'field':field,'value':value}) if row else None
    if domain=='forecast_observations':metadata['source_kind']='company_performance_forecast'
    return metadata


def project(reader,scope,policy,cutoff,*,source_cutoffs=True):
    symbols=_symbols(scope['symbols']);start=_session(scope['start_session'],'start');end=_session(scope['end_session'],'end')
    if start>end:raise ArtifactError('reversed View range')
    calendars=exchange_sessions(reader,symbols,start,end)
    if not any(item['sessions'] for item in calendars.values()):
        raise ArtifactError('INSUFFICIENT_SCOPE: no open exchange sessions')
    if any(d not in reader.commits for d in PR7_DOMAINS):raise ArtifactError('PR7 Snapshot required')
    wide=[];event_history={}
    open_days={symbol:set(item['sessions']) for symbol,item in calendars.items()}
    for session in sorted({day for item in calendars.values() for day in item['sessions']}):
        active=[symbol for symbol in symbols if session in open_days[symbol]]
        effective=min(instant(cutoff),instant(session+'T23:59:59+08:00')).isoformat()
        selected={};source_sessions={}
        for domain in PR7_DOMAINS:
            source_session=dependency_session(reader,domain,session) if source_cutoffs else session
            source_sessions[domain]=source_session
            for symbol in active:request_coverage(reader,domain,symbol,source_session)
            bounds={'start_session':source_session,'end_session':source_session} if domain in DAILY_DOMAINS else {}
            groups={symbol:[] for symbol in active}
            if domain in DAILY_DOMAINS:
                selected_rows=reader.as_of(domain,symbols=active,pit_policy=policy,knowledge_cutoff=effective,**bounds)
            else:
                # Coverage checks remain above preparation. Histories are scoped
                # to requested securities and released when this View returns.
                if domain not in event_history:
                    event_history[domain]=reader.facts(domain,symbols=symbols)
                selected_rows=select_pr7_revisions(
                    [row for row in event_history[domain] if row['symbol'] in groups],
                    policy=policy,knowledge_cutoff=effective)
            for row in selected_rows:
                groups[row['symbol']].append(row)
            selected[domain]=groups
        for symbol in active:
            facts={leaf:_leaf_metadata(reader,leaf,symbol,session,effective,policy,selected[domain][symbol],source_session=source_sessions[domain]) for leaf,domain in LEAF_DOMAINS.items()}
            wide.append({'symbol':symbol,'session':session,'values':{f:facts[f]['value'] for f in NUMERIC_FIELDS},'facts':facts})
    wide.sort(key=lambda r:r['session'])
    return {'wide':wide,'sessions':sorted({r['session'] for r in wide}),'symbol_calendars':calendars}


def payload_files(payload,symbols,bundle):
    output={'rows.json':_json_bytes(payload),'code_bundle.json':_json_bytes(bundle)};sessions=payload['sessions']
    output['calendars/day.txt']=('\n'.join(sessions)+'\n').encode()
    output['instruments/all.txt']=''.join(f'{_qlib_symbol(s)}\t{sessions[0]}\t{sessions[-1]}\n' for s in symbols).encode()
    for symbol in symbols:
        rows={r['session']:r for r in payload['wide'] if r['symbol']==symbol}
        # Qlib's shared storage axis is padded, never projected across exchanges.
        for f in NUMERIC_FIELDS:output[f'features/{_qlib_symbol(symbol).lower()}/{f.replace(".","__")}.day.bin']=_feature_bytes(0,[rows[d]['values'][f] if d in rows else None for d in sessions])
    return output


def manifest_for(reader,scope,policy,cutoff,payload,bundle,*,schema_version='pr7_fact_view.v3'):
    contents=payload_files(payload,scope['symbols'],bundle)
    return {'artifact_type':'pr7_fact_view','schema_version':schema_version,
        'snapshot_ref':{'snapshot_id':reader.snapshot.ref.snapshot_id,'identity_digest':reader.snapshot.manifest['identity_digest']},
        'scope':scope,'validated_scope':dict(scope,fields=list(LEAF_DOMAINS)),
        'pit_policy':policy,'knowledge_cutoff':instant(cutoff).isoformat(),
        'cutoff_policy':'min_knowledge_cutoff_session_end_Asia_Shanghai.v1',
        'fields':list(NUMERIC_FIELDS),'fact_fields':list(LEAF_DOMAINS),'qlib_field_mapping':{f:f.replace('.','__') for f in NUMERIC_FIELDS},
        'instrument_storage_scope':[{'symbol':s,'qlib_symbol':_qlib_symbol(s),'storage_path':'features/'+_qlib_symbol(s).lower(),'start_session':payload['sessions'][0],'end_session':payload['sessions'][-1],'exchange':payload['symbol_calendars'][s]['exchange'],'valid_sessions':payload['symbol_calendars'][s]['sessions']} for s in scope['symbols']],
        'implementation_digests':{n:_digest(c.encode()) for n,c in sorted(bundle.items())},
        'files':[{'path':p,'content_digest':_digest(b),'size':len(b)} for p,b in sorted(contents.items())],
        'validation_summary':{'status':'PASS','rows':len(payload['wide']),'leaves':len(LEAF_DOMAINS)}}


def build_pr7_fact_view(data_root,snapshot_id,*,symbols,start_session,end_session,pit_policy,knowledge_cutoff):
    return _build_pr7_fact_view(SnapshotReader(data_root,snapshot_id),symbols=symbols,
        start_session=start_session,end_session=end_session,pit_policy=pit_policy,knowledge_cutoff=knowledge_cutoff)


def _build_pr7_fact_view(reader,*,symbols,start_session,end_session,pit_policy,knowledge_cutoff):
    data_root=reader.data_root;scope={'symbols':list(_symbols(symbols)),'start_session':start_session,'end_session':end_session}
    payload=project(reader,scope,pit_policy,knowledge_cutoff)
    source=Path(str(files('axiom_data')));bundle={p.relative_to(source).as_posix():p.read_text() for p in sorted(source.rglob('*')) if p.is_file() and p.suffix in {'.py','.json'}}
    manifest=manifest_for(reader,scope,pit_policy,knowledge_cutoff,payload,bundle)
    identity=_identity_digest(manifest,'view_id');view_id=_derived_identity('pr7-fact',identity)
    manifest.update(view_id=view_id,identity_digest=identity,created_at=_timestamp(None))
    layout=_layout(data_root);target=layout.derived_commits('pr7_fact')/view_id
    def prepare(candidate):
        for path,content in payload_files(payload,scope['symbols'],bundle).items():
            (candidate/path).parent.mkdir(parents=True,exist_ok=True);_write_file(candidate/path,content)
        _write_manifest(candidate,manifest)
    _publish_directory(layout,target,prepare,identity_digest=identity)
    return _load_pr7_fact_view(data_root,view_id,checked_reader=reader).ref


def load_pr7_fact_view(data_root,view_id):
    return _load_pr7_fact_view(data_root,view_id)


def _load_pr7_fact_view(data_root,view_id,*,checked_reader=None):
    view_id=_identity('view_id',view_id);layout=_layout(data_root);target=layout.derived_commits('pr7_fact')/view_id
    manifest,digest=_load_manifest(layout.root,target,artifact_type='pr7_fact_view',schema_version=('pr7_fact_view.v1','pr7_fact_view.v2','pr7_fact_view.v3'),identity_field='view_id',identity=view_id)
    _validate_manifest_identity(manifest,'view_id','pr7-fact',view_id)
    if manifest['schema_version']=='pr7_fact_view.v1':
        from axiom_data.pr7_views_v1 import project as projection, manifest_for as make_manifest, payload_files as make_files
    elif manifest['schema_version']=='pr7_fact_view.v2':
        from functools import partial
        projection=partial(project,source_cutoffs=False)
        make_manifest=partial(manifest_for,schema_version='pr7_fact_view.v2')
        make_files=payload_files
    else:
        projection,make_manifest,make_files=project,manifest_for,payload_files
    reader=checked_reader or SnapshotReader(data_root,manifest['snapshot_ref']['snapshot_id'])
    if (Path(reader.data_root).resolve()!=layout.root.resolve() or
        reader.snapshot.ref.snapshot_id!=manifest['snapshot_ref']['snapshot_id']):
        raise ArtifactError('checked Reader does not match PR7 View Snapshot')
    payload=projection(reader,manifest['scope'],manifest['pit_policy'],manifest['knowledge_cutoff'])
    bundle=json.loads(_safe_path(layout.root,target/'code_bundle.json',closure=target).read_bytes())
    if not isinstance(bundle,dict) or not bundle or any(not isinstance(v,str) for v in bundle.values()):raise ArtifactError('invalid code bundle')
    expected=make_manifest(reader,manifest['scope'],manifest['pit_policy'],manifest['knowledge_cutoff'],payload,bundle)
    if {k:v for k,v in manifest.items() if k not in {'view_id','identity_digest','created_at'}}!=expected:raise ArtifactError('PR7 View semantic closure mismatch')
    for path,content in make_files(payload,manifest['scope']['symbols'],bundle).items():
        if _safe_path(layout.root,target/path,closure=target).read_bytes()!=content:raise ArtifactError('PR7 file differs from Snapshot projection')
    return DerivedView(DerivedViewRef('pr7_fact',view_id,digest),manifest,tuple(payload['wide']))
