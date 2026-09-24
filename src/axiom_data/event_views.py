"""Snapshot-bound event Fact/Qlib materialization."""
import json
from pathlib import Path
from importlib.resources import files
from axiom_data.artifacts import (ArtifactError,_layout,_json_bytes,_digest,_identity_digest,_derived_identity,
    _timestamp,_write_file,_write_manifest,_publish_directory,_load_manifest,_validate_manifest_identity,_safe_path,_identity)
from axiom_data.consumption import SnapshotReader,feature_bytes,qlib_symbol,validate_symbols,validate_session
from axiom_data.views import DerivedView,DerivedViewRef
from axiom_data.domains.events import EVENT_DOMAINS,DAILY_DOMAINS
from axiom_data.pit import instant
from axiom_data.pit import select_event_revisions

from axiom_data.domains.events import LEAF_DOMAINS, NUMERIC_FIELDS
from axiom_data.consumption import (
    request_coverage, dependency_session, exchange_sessions, select_latest_report,
    leaf_facts, event_leaf_metadata,
)
# Historical imports of this helper continue to resolve to the same implementation.
_leaf_metadata = event_leaf_metadata


def project(reader,scope,policy,cutoff,*,source_cutoffs=True):
    symbols=validate_symbols(scope['symbols']);start=validate_session(scope['start_session'],'start');end=validate_session(scope['end_session'],'end')
    if start>end:raise ArtifactError('reversed View range')
    calendars=exchange_sessions(reader,symbols,start,end)
    if not any(item['sessions'] for item in calendars.values()):
        raise ArtifactError('INSUFFICIENT_SCOPE: no open exchange sessions')
    if any(d not in reader.commits for d in EVENT_DOMAINS):raise ArtifactError('event Snapshot domains required')
    wide=[];event_history={}
    open_days={symbol:set(item['sessions']) for symbol,item in calendars.items()}
    for session in sorted({day for item in calendars.values() for day in item['sessions']}):
        active=[symbol for symbol in symbols if session in open_days[symbol]]
        effective=min(instant(cutoff),instant(session+'T23:59:59+08:00')).isoformat()
        selected={};source_sessions={}
        for domain in EVENT_DOMAINS:
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
                selected_rows=select_event_revisions(
                    [row for row in event_history[domain] if row['symbol'] in groups],
                    policy=policy,knowledge_cutoff=effective)
            for row in selected_rows:
                groups[row['symbol']].append(row)
            selected[domain]=groups
        for symbol in active:
            facts={leaf:event_leaf_metadata(reader,leaf,symbol,session,effective,policy,selected[domain][symbol],source_session=source_sessions[domain]) for leaf,domain in LEAF_DOMAINS.items()}
            wide.append({'symbol':symbol,'session':session,'values':{f:facts[f]['value'] for f in NUMERIC_FIELDS},'facts':facts})
    wide.sort(key=lambda r:r['session'])
    return {'wide':wide,'sessions':sorted({r['session'] for r in wide}),'symbol_calendars':calendars}


def payload_files(payload,symbols,bundle):
    output={'rows.json':_json_bytes(payload),'code_bundle.json':_json_bytes(bundle)};sessions=payload['sessions']
    output['calendars/day.txt']=('\n'.join(sessions)+'\n').encode()
    output['instruments/all.txt']=''.join(f'{qlib_symbol(s)}\t{sessions[0]}\t{sessions[-1]}\n' for s in symbols).encode()
    for symbol in symbols:
        rows={r['session']:r for r in payload['wide'] if r['symbol']==symbol}
        # Qlib's shared storage axis is padded, never projected across exchanges.
        for f in NUMERIC_FIELDS:output[f'features/{qlib_symbol(symbol).lower()}/{f.replace(".","__")}.day.bin']=feature_bytes(0,[rows[d]['values'][f] if d in rows else None for d in sessions])
    return output


def manifest_for(reader,scope,policy,cutoff,payload,bundle,*,schema_version='pr7_fact_view.v3'):
    contents=payload_files(payload,scope['symbols'],bundle)
    return {'artifact_type':'pr7_fact_view','schema_version':schema_version,
        'snapshot_ref':{'snapshot_id':reader.snapshot.ref.snapshot_id,'identity_digest':reader.snapshot.manifest['identity_digest']},
        'scope':scope,'validated_scope':dict(scope,fields=list(LEAF_DOMAINS)),
        'pit_policy':policy,'knowledge_cutoff':instant(cutoff).isoformat(),
        'cutoff_policy':'min_knowledge_cutoff_session_end_Asia_Shanghai.v1',
        'fields':list(NUMERIC_FIELDS),'fact_fields':list(LEAF_DOMAINS),'qlib_field_mapping':{f:f.replace('.','__') for f in NUMERIC_FIELDS},
        'instrument_storage_scope':[{'symbol':s,'qlib_symbol':qlib_symbol(s),'storage_path':'features/'+qlib_symbol(s).lower(),'start_session':payload['sessions'][0],'end_session':payload['sessions'][-1],'exchange':payload['symbol_calendars'][s]['exchange'],'valid_sessions':payload['symbol_calendars'][s]['sessions']} for s in scope['symbols']],
        'implementation_digests':{n:_digest(c.encode()) for n,c in sorted(bundle.items())},
        'files':[{'path':p,'content_digest':_digest(b),'size':len(b)} for p,b in sorted(contents.items())],
        'validation_summary':{'status':'PASS','rows':len(payload['wide']),'leaves':len(LEAF_DOMAINS)}}


def build_event_fact_view(data_root,snapshot_id,*,symbols,start_session,end_session,pit_policy,knowledge_cutoff):
    return build_event_fact_view_from_reader(SnapshotReader(data_root,snapshot_id),symbols=symbols,
        start_session=start_session,end_session=end_session,pit_policy=pit_policy,knowledge_cutoff=knowledge_cutoff)


def build_event_fact_view_from_reader(reader,*,symbols,start_session,end_session,pit_policy,knowledge_cutoff):
    data_root=reader.data_root;scope={'symbols':list(validate_symbols(symbols)),'start_session':start_session,'end_session':end_session}
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
    return load_event_fact_view_with_reader(data_root,view_id,checked_reader=reader).ref


def load_event_fact_view(data_root,view_id):
    return load_event_fact_view_with_reader(data_root,view_id)


def load_event_fact_view_with_reader(data_root,view_id,*,checked_reader=None):
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
        raise ArtifactError('checked Reader does not match event View Snapshot')
    payload=projection(reader,manifest['scope'],manifest['pit_policy'],manifest['knowledge_cutoff'])
    bundle=json.loads(_safe_path(layout.root,target/'code_bundle.json',closure=target).read_bytes())
    if not isinstance(bundle,dict) or not bundle or any(not isinstance(v,str) for v in bundle.values()):raise ArtifactError('invalid code bundle')
    expected=make_manifest(reader,manifest['scope'],manifest['pit_policy'],manifest['knowledge_cutoff'],payload,bundle)
    if {k:v for k,v in manifest.items() if k not in {'view_id','identity_digest','created_at'}}!=expected:raise ArtifactError('event View semantic closure mismatch')
    for path,content in make_files(payload,manifest['scope']['symbols'],bundle).items():
        if _safe_path(layout.root,target/path,closure=target).read_bytes()!=content:raise ArtifactError('event View file differs from Snapshot projection')
    return DerivedView(DerivedViewRef('pr7_fact',view_id,digest),manifest,tuple(payload['wide']))


# Compatibility exports for historical callers.
PR7_DOMAINS = EVENT_DOMAINS
build_pr7_fact_view = build_event_fact_view
_build_pr7_fact_view = build_event_fact_view_from_reader
load_pr7_fact_view = load_event_fact_view
_load_pr7_fact_view = load_event_fact_view_with_reader

select_pr7_revisions = select_event_revisions
