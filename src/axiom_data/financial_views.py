"""Snapshot-bound financial Fact/Qlib materialization."""
from __future__ import annotations
import json
from contextlib import contextmanager
from pathlib import Path
from functools import partial
from importlib.resources import files
from axiom_data.artifacts import (ArtifactError, _layout, _json_bytes, _digest, _identity_digest,
    _derived_identity, _timestamp, _write_file, _write_manifest, _publish_directory,
    _load_manifest, _validate_manifest_identity, _safe_path, _identity)
from axiom_data.consumption import SnapshotReader, feature_bytes, qlib_symbol, validate_symbols, validate_session
from axiom_data.domains import FUNDAMENTAL_DOMAINS
from axiom_data.domains.reference import weakest_pit_qualification
from axiom_data.pit import instant, financial_derived, select_revisions, select_financial_revisions, AMBIGUOUS_SOURCE_REVISION
from axiom_data.views import DerivedView, DerivedViewRef

from axiom_data.domains.fundamentals import FIELD_MAP, DERIVED_FIELDS, WIDE_FIELDS
FINANCIAL_VIEW_BATCH_SIZE = 50


@contextmanager
def financial_view_batch(reader, configs):
    """Bound valuation preparation across independent financial Views."""
    from axiom_data.verification_cache import file_state

    if not 1 < len(configs) <= FINANCIAL_VIEW_BATCH_SIZE:
        raise ArtifactError('invalid financial View batch')
    symbols = set()
    for config in configs:
        selected = validate_symbols(config['symbols'])
        if len(selected) != 1:
            raise ArtifactError('financial View batch requires one symbol per View')
        symbols.add(selected[0])
    start = min(validate_session(c['start_session'], 'start') for c in configs)
    end = max(validate_session(c['end_session'], 'end') for c in configs)
    if start > end:
        raise ArtifactError('reversed financial View interval')
    previous = getattr(reader, '_financial_view_batch', None)
    batch = {'symbols': symbols, 'start': start, 'end': end}
    reader._financial_view_batch = batch
    try:
        yield
    finally:
        reader._financial_view_batch = previous
        if any(file_state(path) != state for path, state in batch.get('observed', {}).items()):
            raise ArtifactError('financial View source changed during batch')


def _valuation_at(reader,symbols,session,policy,cutoff):
    """After View admission, select all revisions of the requested daily keys."""
    selected=set(symbols)
    batch=getattr(reader,'_financial_view_batch',None)
    if batch is not None and selected<=batch['symbols'] and batch['start']<=session<=batch['end']:
        if 'rows' not in batch:
            from axiom_data.verification_cache import validation_paths
            with validation_paths() as observed:
                grouped={}
                for row in reader.session_rows('valuation_daily',batch['start'],batch['end'],batch['symbols']):
                    if row['symbol'] in batch['symbols'] and batch['start']<=row['session']<=batch['end']:
                        grouped.setdefault((row['symbol'],row['session']),[]).append(row)
            batch['rows'],batch['observed']=grouped,observed
        rows=[r for symbol in symbols for r in batch['rows'].get((symbol,session),())]
    else:
        rows=[r for r in reader.session_rows('valuation_daily',session,session)
              if r['symbol'] in selected and r['session']==session]
    return select_revisions(rows,policy=policy,knowledge_cutoff=cutoff)


def project(reader, scope, policy, cutoff, *, financial_resolution=True, membership_ref=False):
    from axiom_data.financial_coverage import prepared_input
    symbols=scope['symbols'];start=scope['start_session'];end=scope['end_session']
    validate_symbols(symbols);validate_session(start,'start');validate_session(end,'end');instant(cutoff)
    if start>end:raise ArtifactError('reversed financial View interval')
    if not scope['universe_ids'] or len(scope['universe_ids'])!=len(set(scope['universe_ids'])):
        raise ArtifactError('financial View requires explicit unique universes')
    known=set(prepared_input(reader,'security_master',(),lambda:sorted(r['symbol'] for r in reader.security_master())))
    if not set(symbols)<=known:raise ArtifactError('financial View scope lacks security identities')
    if any(d not in reader.commits for d in FUNDAMENTAL_DOMAINS):raise ArtifactError('financial Snapshot domains required')
    calendar=prepared_input(reader,'calendar',(),lambda:list(reader.trading_calendar()))
    sessions=sorted({r['session'] for r in calendar if start<=r['session']<=end and r['is_open']})
    if not sessions:raise ArtifactError('financial View has no calendar coverage')
    from axiom_data.financial_coverage import admit_view
    actual_scope=admit_view(reader,scope,policy,cutoff,financial_resolution=financial_resolution)
    sw_state=reader.commits['industry_membership'].ref.contract_version=='industry_membership.v3'
    taxonomy=prepared_input(reader,'taxonomy',(policy,cutoff),lambda:
        sorted({s['industry_id'] for r in reader.as_of('industry_membership',knowledge_cutoff=cutoff,pit_policy=policy) for s in r['membership_spans']}) if sw_state else sorted({r['industry_id'] for r in reader.facts('industry_membership')}))
    encoding={industry:i+1 for i,industry in enumerate(taxonomy)}
    # Full-scope admission above must finish before security projection. Retain
    # only this View's requested history, for this projection call's lifetime.
    financial_history=prepared_input(reader,'financial_history',tuple(symbols),
        lambda:reader.facts('financial_events',symbols=symbols))
    events=[];derived=[];wide=[];memberships=[];industries=[]
    membership_qualifications=set()
    for session in sessions:
        session_cutoff=min(instant(cutoff),instant(session+'T23:59:59+08:00')).isoformat()
        selector=select_financial_revisions if financial_resolution else select_revisions
        facts=selector(financial_history,knowledge_cutoff=session_cutoff,policy=policy)
        stable=financial_derived(financial_history,policy=policy,knowledge_cutoff=session_cutoff,resolve_ambiguity=financial_resolution)
        valuation=_valuation_at(reader,symbols,session,policy,session_cutoff)
        membership={}
        for group in scope['universe_ids']:
            for row in reader.members(group,session,knowledge_cutoff=session_cutoff,pit_policy=policy):
                membership_qualifications.add(row['pit_qualification'])
                if not membership_ref or row['symbol'] in symbols:
                    membership[row['symbol']]=row
                if not membership_ref:
                    memberships.append(dict(row,target_session=session))
        industry={r['symbol']:r for r in reader.members(scope['industry_system'],session,
            domain='industry_membership',knowledge_cutoff=session_cutoff,pit_policy=policy,symbols=symbols)}
        industry_availability={r['symbol']:r for r in reader.industry_facts(scope['industry_system'],session,
            knowledge_cutoff=session_cutoff,pit_policy=policy,symbols=symbols)} if sw_state else {}
        industries.extend(dict(r,target_session=session) for r in industry.values())
        events.extend(dict(r,target_session=session) for r in facts)
        derived.extend(dict(r,target_session=session) for r in stable)
        for symbol in symbols:
            values={f:None for f in WIDE_FIELDS};refs={}; metadata={}
            for leaf in WIDE_FIELDS:
                metadata[leaf]=fact_metadata(None,None,leaf,reader,policy,session_cutoff)
            for leaf,(endpoint,field) in FIELD_MAP.items():
                candidates=[r for r in facts if r['symbol']==symbol and r['endpoint']==endpoint and
                            r['report_type']==('supplier_indicator' if endpoint=='fina_indicator' else '1')]
                if candidates:
                    row=max(candidates,key=lambda r:r['report_period']);values[leaf]=row['values'][field]
                    refs[leaf]=row['revision_id']
                    metadata[leaf]=fact_metadata(row,values[leaf],leaf,reader,policy,session_cutoff)
            for field in DERIVED_FIELDS:
                candidates=[r for r in stable if r['symbol']==symbol and r['field']==field and r['report_type']=='1']
                if candidates:
                    row=max(candidates,key=lambda r:r['report_period']);values['financial.'+field]=row['value']
                    refs['financial.'+field]=row['derived_id']
                    metadata['financial.'+field]=fact_metadata(row,row['value'],'financial.'+field,reader,policy,session_cutoff)
            for row in valuation:
                if row['symbol']==symbol and row['session']==session:
                    for field,value in row['values'].items():
                        values['valuation.'+field]=value;refs['valuation.'+field]=row['revision_id']
                        metadata['valuation.'+field]=fact_metadata(row,value,'valuation.'+field,reader,policy,session_cutoff)
            # Absence is unknown outside demonstrated snapshot coverage.  A separate
            # cohort list, rather than a zero, expresses positive membership.
            values['universe.membership']=1 if symbol in membership else 0
            values['industry.membership']=encoding[industry[symbol]['industry_id']] if symbol in industry else None
            for leaf,row in [('universe.membership',membership.get(symbol)),('industry.membership',industry.get(symbol))]:
                metadata[leaf]=fact_metadata(row,values[leaf],leaf,reader,policy,session_cutoff)
            metadata['universe.membership']['universe_ids']=scope['universe_ids']
            if 'group_states' in reader.commits['universe_membership'].manifest:
                from axiom_data.pit import select_group_states
                states=select_group_states(reader.commits['universe_membership'].manifest['group_states'],policy=policy,knowledge_cutoff=session_cutoff)
                metadata['universe.membership']['group_observations']=[
                    {'universe_id':state['universe_id'],'state_id':state['state_id'],
                     'source_ref':state['source_ref'],'usable_at':state['usable_from'],
                     'member_count':interval['member_count'],'member_set_digest':interval['member_set_digest']}
                    for state in states if state['universe_id'] in scope['universe_ids']
                    for interval in state['intervals'] if interval['effective_from']<=session
                    and (interval['effective_to'] is None or session<interval['effective_to'])]
            metadata['industry.membership'].update(classification_system=scope['industry_system'],mapping_ref='industry_mapping')
            if sw_state:
                state=industry_availability[symbol]
                metadata['industry.membership']=fact_metadata(state,values['industry.membership'],'industry.membership',reader,policy,session_cutoff)
                metadata['industry.membership'].update(availability_state=state['availability_state'],
                    missing_reason=state['missing_reason'],mapping_profile_digest=state['mapping_profile_digest'],
                    mapping_provenance=state.get('mapping_provenance'),classification_system=scope['industry_system'],mapping_ref='industry_mapping')
            wide.append({'session':session,'symbol':symbol,'values':values,'provenance':refs,
                         'knowledge_cutoff':session_cutoff,'facts':metadata})
    return {'wide':wide,'events':events,'derived':derived,'memberships':memberships,'industries':industries,
            **({'membership_qualification':weakest_pit_qualification(
                [{'pit_qualification':q} for q in membership_qualifications])} if membership_ref else {}),
            'industry_encoding':encoding,'industry_mapping':{'classification_system':scope['industry_system'],
                'version':reader.commits['industry_membership'].ref.commit_id,
                'code_to_industry':{str(v):k for k,v in encoding.items()}},
            'actual_available_scope':actual_scope,'sessions':sessions}


def fact_metadata(row,value,leaf,reader,policy,cutoff):
    row=row or {}
    reason=row.get('missing_reason')
    if value is None and reason is None:
        field=FIELD_MAP.get(leaf,(None,leaf.split('.')[-1]))[1]
        reason=(AMBIGUOUS_SOURCE_REVISION if row.get('missing_reasons',{}).get(field)==AMBIGUOUS_SOURCE_REVISION
                else 'source_value_missing' if field in row.get('missing_reasons',{})
                else 'PIT_component_not_visible')
    domain=('financial_events' if leaf.startswith(('income.','balance.','cashflow.','indicator.','financial.'))
            else 'valuation_daily' if leaf.startswith('valuation.') else leaf.split('.')[0]+'_membership')
    unit=('CNY' if leaf.startswith(('income.','balance.','cashflow.','financial.'))
          else 'ratio' if leaf.startswith(('indicator.','valuation.')) else 'code' if leaf.startswith('industry.') else 'boolean')
    return {'value':value,'unit':unit,'validity':'unavailable' if reason==AMBIGUOUS_SOURCE_REVISION else 'missing' if value is None else 'valid',
            'missing_reason':reason,'pit_qualification':row.get('pit_qualification','best_effort'),
            'usable_at':row.get('usable_from'),'pit_policy':policy,'knowledge_cutoff':cutoff,
            'source_ref':row.get('source_ref'),'revision_ref':row.get('revision_id'),
            'observation_ref':row.get('observation_ref'),'derived_ref':row.get('derived_id'),
            'component_revision_refs':row.get('component_revisions',[]),
            'quarter_components':row.get('quarter_components',[]),
            'expected_quarters':row.get('expected_quarters',[]),
            'domain_ref':reader.commits[domain].ref.commit_id,
            'quality_state':'BLOCKED' if value is None else 'PASS'}


def _membership_ref(reader,scope,policy,cutoff):
    commit=reader.commits['universe_membership']
    return {'snapshot_id':reader.snapshot.ref.snapshot_id,
            'domain_commit_id':commit.ref.commit_id,
            'identity_digest':reader.snapshot.manifest['domain_refs']['universe_membership']['identity_digest'],
            'universe_ids':list(scope['universe_ids']),
            'start_session':scope['start_session'],'end_session':scope['end_session'],
            'pit_policy':policy,'knowledge_cutoff':instant(cutoff).isoformat()}


def _files(payload, symbols, bundle, *, membership_ref=False):
    stored=({key:payload[key] for key in ('wide','events','derived','industries','sessions')}
            if membership_ref else payload)
    output={'code_bundle.json':_json_bytes(bundle),'rows.json':_json_bytes(stored)}
    sessions=payload['sessions']
    output['calendars/day.txt']=('\n'.join(sessions)+'\n').encode()
    output['instruments/all.txt']=''.join(f'{qlib_symbol(s)}\t{sessions[0]}\t{sessions[-1]}\n' for s in symbols).encode()
    for symbol in symbols:
        rows=[r for r in payload['wide'] if r['symbol']==symbol]
        for field in WIDE_FIELDS:
            output[f'features/{qlib_symbol(symbol).lower()}/{field.replace(".","__")}.day.bin']=feature_bytes(0,[r['values'][field] for r in rows])
    return output


def _manifest(reader, scope, policy, cutoff, payload, bundle, *, financial_resolution=True, membership_ref=False):
    contents=_files(payload,scope['symbols'],bundle,membership_ref=membership_ref)
    return {'artifact_type':'pr6_fact_view','schema_version':('pr6_fact_view.v4' if membership_ref else
        'pr6_fact_view.v3' if financial_resolution else 'pr6_fact_view.v2'),
        **({'financial_resolution_policy':'financial_leaf_resolution.v1'} if financial_resolution else {}),
        **({'membership_ref':_membership_ref(reader,scope,policy,cutoff)} if membership_ref else {}),
        'snapshot_ref':{'snapshot_id':reader.snapshot.ref.snapshot_id,'identity_digest':reader.snapshot.manifest['identity_digest']},
        'domain_refs':reader.snapshot.manifest['domain_refs'],'scope':scope,
        'requested_scope':dict(scope,fields=list(WIDE_FIELDS)),
        'actual_available_scope':payload['actual_available_scope'],
        'validated_scope':dict(scope,fields=list(WIDE_FIELDS)),
        'pit_policy':policy,'knowledge_cutoff':instant(cutoff).isoformat(),
        'cutoff_policy':'min_knowledge_cutoff_session_end_Asia_Shanghai.v1',
        'instrument_storage_scope':[{'symbol':s,'qlib_symbol':qlib_symbol(s),'storage_path':'features/'+qlib_symbol(s).lower(),'start_session':payload['sessions'][0],'end_session':payload['sessions'][-1]} for s in scope['symbols']],
        'fields':list(WIDE_FIELDS),'qlib_field_mapping':{f:f.replace('.','__') for f in WIDE_FIELDS},
        'industry_encoding':payload['industry_encoding'],'industry_mapping':payload['industry_mapping'],
        'fact_metadata_schema':'typed_fact.v2' if financial_resolution else 'typed_fact.v1',
        'implementation_digests':{name:_digest(content.encode()) for name,content in sorted(bundle.items())},
        'files':[{'path':p,'content_digest':_digest(b),'size':len(b)} for p,b in sorted(contents.items())],
        'pit_qualification':weakest_pit_qualification(payload['events']+payload['industries']+
            ([{'pit_qualification':payload['membership_qualification']}] if membership_ref and
             payload['membership_qualification']!='unknown' else payload['memberships'])),
        'validation_summary':{'status':'PASS','wide_rows':len(payload['wide']),'derived_rows':len(payload['derived'])}}


def build_financial_fact_view(data_root,snapshot_id,*,symbols,start_session,end_session,universe_ids,
                        industry_system,pit_policy,knowledge_cutoff):
    return build_financial_fact_view_from_reader(SnapshotReader(data_root,snapshot_id),symbols=symbols,
        start_session=start_session,end_session=end_session,universe_ids=universe_ids,
        industry_system=industry_system,pit_policy=pit_policy,knowledge_cutoff=knowledge_cutoff)


def build_financial_fact_view_from_reader(reader,*,symbols,start_session,end_session,universe_ids,
                         industry_system,pit_policy,knowledge_cutoff):
    data_root=reader.data_root
    scope={'symbols':list(validate_symbols(symbols)),'start_session':start_session,'end_session':end_session,
           'universe_ids':list(universe_ids),'industry_system':industry_system}
    payload=project(reader,scope,pit_policy,knowledge_cutoff,membership_ref=True)
    source_root=Path(str(files("axiom_data")))
    bundle={p.relative_to(source_root).as_posix():p.read_text() for p in sorted(source_root.rglob("*")) if p.is_file() and p.suffix in {".py",".json"}}
    manifest=_manifest(reader,scope,pit_policy,knowledge_cutoff,payload,bundle,membership_ref=True)
    identity=_identity_digest(manifest,'view_id');view_id=_derived_identity('pr6-fact',identity)
    manifest.update(view_id=view_id,identity_digest=identity,created_at=_timestamp(None))
    layout=_layout(data_root);target=layout.derived_commits('pr6_fact')/view_id
    def prepare(candidate):
        for path,content in _files(payload,scope['symbols'],bundle,membership_ref=True).items():
            (candidate/path).parent.mkdir(parents=True,exist_ok=True);_write_file(candidate/path,content)
        _write_manifest(candidate,manifest)
    _publish_directory(layout,target,prepare,identity_digest=identity)
    return load_financial_fact_view_with_reader(data_root,view_id,checked_reader=reader).ref


def load_financial_fact_view(data_root,view_id):
    return load_financial_fact_view_with_reader(data_root,view_id)


def load_financial_fact_view_with_reader(data_root,view_id,*,checked_reader=None):
    view_id=_identity('view_id',view_id)
    layout=_layout(data_root);target=layout.derived_commits('pr6_fact')/view_id
    manifest,digest=_load_manifest(layout.root,target,artifact_type='pr6_fact_view',
        schema_version=('pr6_fact_view.v1','pr6_fact_view.v2','pr6_fact_view.v3','pr6_fact_view.v4'),identity_field='view_id',identity=view_id)
    declared=manifest['schema_version']
    if declared=='pr6_fact_view.v1':
        from axiom_data.pr6_views_v1 import LegacyReader, project as projection, _manifest as manifest_builder, _files as payload_files
    else:
        resolution=declared in {'pr6_fact_view.v3','pr6_fact_view.v4'}
        refs=declared=='pr6_fact_view.v4'
        LegacyReader=SnapshotReader
        projection=partial(project,financial_resolution=resolution,membership_ref=refs)
        manifest_builder=partial(_manifest,financial_resolution=resolution,membership_ref=refs)
        payload_files=partial(_files,membership_ref=refs)
    _validate_manifest_identity(manifest,'view_id','pr6-fact',view_id)
    if checked_reader is not None and declared=='pr6_fact_view.v1':
        raise ArtifactError('legacy PR6 View requires its declared Reader')
    reader=checked_reader or LegacyReader(data_root,manifest['snapshot_ref']['snapshot_id'])
    if (Path(reader.data_root).resolve()!=layout.root.resolve() or
        reader.snapshot.ref.snapshot_id!=manifest['snapshot_ref']['snapshot_id']):
        raise ArtifactError('checked Reader does not match financial View Snapshot')
    payload=projection(reader,manifest['scope'],manifest['pit_policy'],manifest['knowledge_cutoff'])
    bundle=json.loads(_safe_path(layout.root,target/'code_bundle.json',closure=target).read_bytes())
    if not isinstance(bundle,dict) or not bundle or any(not isinstance(v,str) for v in bundle.values()):
        raise ArtifactError('invalid frozen code bundle')
    expected=manifest_builder(reader,manifest['scope'],manifest['pit_policy'],manifest['knowledge_cutoff'],payload,bundle)
    if {k:v for k,v in manifest.items() if k not in {'view_id','identity_digest','created_at'}}!=expected:
        raise ArtifactError('financial View semantic closure mismatch')
    for path,content in payload_files(payload,manifest['scope']['symbols'],bundle).items():
        checked=_safe_path(layout.root,target/path,closure=target)
        if checked.read_bytes()!=content:
            raise ArtifactError('financial Fact/Qlib file differs from source projection')
    return DerivedView(DerivedViewRef("pr6_fact",view_id,digest),manifest,tuple(payload['wide']))


# Compatibility exports for historical callers.
PR6_DOMAINS = FUNDAMENTAL_DOMAINS
build_pr6_fact_view = build_financial_fact_view
_build_pr6_fact_view = build_financial_fact_view_from_reader
load_pr6_fact_view = load_financial_fact_view
_load_pr6_fact_view = load_financial_fact_view_with_reader
