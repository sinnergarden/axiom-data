"""Frozen published PR7 View v1 projection and identity semantics."""
import json
from pathlib import Path
from importlib.resources import files
from axiom_data.artifacts import (ArtifactError,_layout,_json_bytes,_digest,_identity_digest,_derived_identity,
    _timestamp,_write_file,_write_manifest,_publish_directory,_load_manifest,_validate_manifest_identity,_safe_path,_identity,load_raw_batch)
from axiom_data.consumption import SnapshotReader,_feature_bytes,_qlib_symbol,_symbols,_session
from axiom_data.views import DerivedView,DerivedViewRef
from axiom_data.domains.pr7 import PR7_DOMAINS,DAILY_DOMAINS
from axiom_data.contracts import load_contract
from axiom_data.pit import instant,fingerprint

LEAF_DOMAINS={**{'holder.'+f:d for f,d in [('number','holder_count_events'),('top10_ratio','top_holders_reports')]},
    **{'margin.'+f:'margin_daily' for f in load_contract('margin_daily.v1')['value_units']},
    **{'moneyflow.'+f:'moneyflow_daily' for f in load_contract('moneyflow_daily.v1')['value_units']},
    **{'forecast.'+f:'forecast_observations' for f in load_contract('forecast_observations.v1')['value_units']}}
NUMERIC_FIELDS=tuple(f for f in LEAF_DOMAINS if not f.startswith('forecast.'))


def request_coverage(reader,domain,symbol,session):
    """Admit bounded requests, including explicit empty supplier responses."""
    if symbol not in {r['symbol'] for r in reader.security_master()}:raise ArtifactError('unknown security')
    commit=reader.commits.get(domain)
    if commit is None:raise ArtifactError('Snapshot lacks PR7 domain')
    refs=set()
    while commit:
        refs.update(r['raw_batch_id'] for r in commit.manifest['ordered_raw_batch_refs'])
        parent=commit.manifest['parent_commit_ref']
        if parent:
            from axiom_data.artifacts import validate_domain_commit_closure
            commit=validate_domain_commit_closure(reader.data_root,domain,parent['domain_commit_id'])
        else:commit=None
    day=session.replace('-','')
    for ref in sorted(refs):
        raw=load_raw_batch(reader.data_root,ref);params=raw.manifest['request']['params']
        if params['ts_code']==symbol and params['start_date']<=day<=params['end_date']:return
    raise ArtifactError('INSUFFICIENT_SCOPE: no bounded supplier request for '+symbol+' '+session)


def leaf_facts(reader,leaf,*,symbol,target_session,knowledge_cutoff,pit_policy):
    if leaf not in LEAF_DOMAINS:raise ArtifactError('unknown PR7 leaf')
    _session(target_session,'target_session');instant(knowledge_cutoff)
    domain=LEAF_DOMAINS[leaf];field=leaf.split('.',1)[1]
    request_coverage(reader,domain,symbol,target_session)
    selected=reader.as_of(domain,symbols=[symbol],pit_policy=pit_policy,knowledge_cutoff=knowledge_cutoff)
    candidates=[r for r in selected if (r['session']==target_session if domain in DAILY_DOMAINS else
        domain=='forecast_observations' or r['report_period']<=target_session)]
    row=max(candidates,key=lambda r:(r['announcement'] or r['session'],r['report_period'] or r['session'])) if candidates else None
    value=row['values'][field] if row else None
    reason=row['missing_reasons'].get(field) if row else 'no_observation_at_cutoff'
    metadata={'leaf':leaf,'symbol':symbol,'target_session':target_session,'snapshot_id':reader.snapshot.ref.snapshot_id,
        'contract_version':domain+'.v1','domain_commit_id':reader.commits[domain].ref.commit_id,
        'value':value,'unit':load_contract(domain+'.v1')['value_units'][field],
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


def project(reader,scope,policy,cutoff):
    symbols=_symbols(scope['symbols']);start=_session(scope['start_session'],'start');end=_session(scope['end_session'],'end')
    if start>end:raise ArtifactError('reversed View range')
    calendar=reader.trading_calendar()
    days=[r['session'] for r in calendar]
    if not days or start<min(days) or end>max(days):raise ArtifactError('INSUFFICIENT_SCOPE: calendar bounds')
    sessions=sorted({r['session'] for r in reader.trading_calendar(start_session=start,end_session=end) if r['is_open']})
    if not sessions:raise ArtifactError('INSUFFICIENT_SCOPE: calendar')
    if any(d not in reader.commits for d in PR7_DOMAINS):raise ArtifactError('PR7 Snapshot required')
    wide=[]
    for session in sessions:
        effective=min(instant(cutoff),instant(session+'T23:59:59+08:00')).isoformat()
        for symbol in symbols:
            facts={leaf:leaf_facts(reader,leaf,symbol=symbol,target_session=session,knowledge_cutoff=effective,pit_policy=policy) for leaf in LEAF_DOMAINS}
            wide.append({'symbol':symbol,'session':session,'values':{f:facts[f]['value'] for f in NUMERIC_FIELDS},'facts':facts})
    return {'wide':wide,'sessions':sessions}


def payload_files(payload,symbols,bundle):
    output={'rows.json':_json_bytes(payload),'code_bundle.json':_json_bytes(bundle)};sessions=payload['sessions']
    output['calendars/day.txt']=('\n'.join(sessions)+'\n').encode()
    output['instruments/all.txt']=''.join(f'{_qlib_symbol(s)}\t{sessions[0]}\t{sessions[-1]}\n' for s in symbols).encode()
    for symbol in symbols:
        rows=[r for r in payload['wide'] if r['symbol']==symbol]
        for f in NUMERIC_FIELDS:output[f'features/{_qlib_symbol(symbol).lower()}/{f.replace(".","__")}.day.bin']=_feature_bytes(0,[r['values'][f] for r in rows])
    return output


def manifest_for(reader,scope,policy,cutoff,payload,bundle):
    contents=payload_files(payload,scope['symbols'],bundle)
    return {'artifact_type':'pr7_fact_view','schema_version':'pr7_fact_view.v1',
        'snapshot_ref':{'snapshot_id':reader.snapshot.ref.snapshot_id,'identity_digest':reader.snapshot.manifest['identity_digest']},
        'scope':scope,'validated_scope':dict(scope,fields=list(LEAF_DOMAINS)),
        'pit_policy':policy,'knowledge_cutoff':instant(cutoff).isoformat(),
        'cutoff_policy':'min_knowledge_cutoff_session_end_Asia_Shanghai.v1',
        'fields':list(NUMERIC_FIELDS),'fact_fields':list(LEAF_DOMAINS),'qlib_field_mapping':{f:f.replace('.','__') for f in NUMERIC_FIELDS},
        'instrument_storage_scope':[{'symbol':s,'qlib_symbol':_qlib_symbol(s),'storage_path':'features/'+_qlib_symbol(s).lower(),'start_session':payload['sessions'][0],'end_session':payload['sessions'][-1]} for s in scope['symbols']],
        'implementation_digests':{n:_digest(c.encode()) for n,c in sorted(bundle.items())},
        'files':[{'path':p,'content_digest':_digest(b),'size':len(b)} for p,b in sorted(contents.items())],
        'validation_summary':{'status':'PASS','rows':len(payload['wide']),'leaves':len(LEAF_DOMAINS)}}
