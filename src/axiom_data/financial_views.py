"""Snapshot-bound financial Fact/Qlib materialization."""
from __future__ import annotations

from axiom_data.frozen_execution import (frozen_operation, bind_view_execution, executed_code_ref,
                                         source_bundle, view_source_bundle)
from axiom_data import frozen_execution
import json
from bisect import bisect_right
from collections import OrderedDict
from contextlib import contextmanager
from pathlib import Path
from functools import partial
from importlib.resources import files
from axiom_data.artifacts import (ArtifactError, _layout, _json_bytes, _digest, _identity_digest,
    _derived_identity, _timestamp, _write_file, _write_manifest, _publish_directory,
    _load_manifest, _validate_manifest_identity, _safe_path, _identity, _declared_content_files)
from axiom_data.consumption import SnapshotReader, feature_bytes, qlib_symbol, validate_symbols, validate_session
from axiom_data.domains import FUNDAMENTAL_DOMAINS
from axiom_data.domains.reference import weakest_pit_qualification
from axiom_data.pit import (instant, financial_derived, financial_derived_from_selected,
    select_revisions, select_financial_revisions, select_group_states, active_members,
    visibility_times, latest_financial_income_window, AMBIGUOUS_SOURCE_REVISION, POLICIES)
from axiom_data.views import DerivedView, DerivedViewRef
from axiom_data.view_states import SparseDailyRows, encode_states, packed_states, unpacked_states

from axiom_data.domains.fundamentals import FIELD_MAP, DERIVED_FIELDS, WIDE_FIELDS
FINANCIAL_VIEW_BATCH_SIZE = 50


def _prepared_selection(reader, slot, key, prepare):
    """Reuse at most two prepared immutable inputs per slot in one operation."""
    batch=getattr(reader,'_financial_batch',None)
    if batch is None:return prepare()
    selections=batch.setdefault(slot,OrderedDict())
    if key in selections:
        value=selections.pop(key)
    else:
        from axiom_data.verification_cache import validation_paths
        with validation_paths() as consumed:
            value=prepare()
        if hasattr(reader, '_remember_consumed_metadata'):
            reader._remember_consumed_metadata(consumed)
        if len(selections)==2:selections.popitem(last=False)
    selections[key]=value
    return value


def _prepared_members(reader, selected, group, session, state_key):
    """Validate the complete group once per session, then reuse its result."""
    if getattr(reader, '_financial_view_batch', None) is None:
        return active_members(selected, group_id=group, target_session=session)
    operation=getattr(reader, '_financial_batch', None)
    if operation is None:
        return active_members(selected, group_id=group, target_session=session)
    cache=operation.setdefault('active_members', OrderedDict())
    key=(state_key,group,session)
    if key in cache:
        rows=cache.pop(key)
    else:
        rows=active_members(selected, group_id=group, target_session=session)
        if len(cache)==8192:cache.popitem(last=False)
    cache[key]=rows
    return rows


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
    common=configs[0]
    scope_keys=('start_session','end_session','universe_ids','industry_system','pit_policy','knowledge_cutoff')
    if any(any(config[key]!=common[key] for key in scope_keys) for config in configs[1:]):
        raise ArtifactError('financial View batch scopes differ')
    start = min(validate_session(c['start_session'], 'start') for c in configs)
    end = max(validate_session(c['end_session'], 'end') for c in configs)
    if start > end:
        raise ArtifactError('reversed financial View interval')
    previous = getattr(reader, '_financial_view_batch', None)
    observed=getattr(reader,'_view_validation_paths',{})
    if any(file_state(path)!=state for path,state in observed.items()):
        raise ArtifactError('financial View inputs changed before batch')
    batch = {'symbols': symbols, 'start': start, 'end': end,
             'common':common,'verified_paths':True}
    reader._financial_view_batch = batch
    try:
        from axiom_data.financial_coverage import admit_view
        scope={'symbols':sorted(symbols),'start_session':start,'end_session':end,
               'universe_ids':list(common['universe_ids']),
               'industry_system':common['industry_system']}
        batch['admission']=admit_view(reader,scope,common['pit_policy'],
            common['knowledge_cutoff'])
        yield
    finally:
        reader._financial_view_batch = previous
        if any(file_state(path) != state for path, state in
               {**observed,**batch.get('observed', {})}.items()):
            raise ArtifactError('financial View source changed during batch')


def _valuation_scope(reader,symbols,start,end):
    """Validate and group the requested date range once before daily PIT selection."""
    selected=set(symbols)
    batch=getattr(reader,'_financial_view_batch',None)
    if batch is not None and selected<=batch['symbols'] and batch['start']<=start<=end<=batch['end']:
        if 'rows' not in batch:
            from axiom_data.verification_cache import validation_paths
            with validation_paths() as observed:
                grouped={}
                for row in reader.session_rows('valuation_daily',batch['start'],batch['end']):
                    if row['symbol'] in batch['symbols'] and batch['start']<=row['session']<=batch['end']:
                        grouped.setdefault((row['symbol'],row['session']),[]).append(row)
            if hasattr(reader, '_remember_consumed_metadata'):
                reader._remember_consumed_metadata(observed)
            batch['rows'],batch['observed']=grouped,observed
        return {day:[row for symbol in symbols for row in batch['rows'].get((symbol,day),())]
                for day in sorted({day for symbol,day in batch['rows'] if symbol in selected and start<=day<=end})}
    grouped={}
    for row in reader.session_rows('valuation_daily',start,end):
        if row['symbol'] in selected and start<=row['session']<=end:
            grouped.setdefault(row['session'],[]).append(row)
    return grouped


def _valuation_at(reader,symbols,session,policy,cutoff,*,prepared=None):
    """After View admission, select all revisions of the requested daily keys."""
    rows=prepared if prepared is not None else _valuation_scope(reader,symbols,session,session)
    return select_revisions(rows.get(session,()),policy=policy,knowledge_cutoff=cutoff)


def admit_financial_view(reader, scope, policy, cutoff, *, financial_resolution=True, membership_ref=True):
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
    batch=getattr(reader,'_financial_view_batch',None)
    if membership_ref and batch is not None and batch.get('admission') is not None and set(symbols)<=batch['symbols']:
        admitted=batch['admission']
        actual_scope=dict(admitted,financial_report_periods={s:admitted['financial_report_periods'][s] for s in symbols})
    else:
        actual_scope=admit_view(reader,scope,policy,cutoff,financial_resolution=financial_resolution)
    return symbols, start, end, sessions, actual_scope


def project(reader, scope, policy, cutoff, *, financial_resolution=True, membership_ref=False):
    if hasattr(reader, '_check_consumed_metadata'):
        reader._check_consumed_metadata(force=True)
    from axiom_data.financial_coverage import prepared_input
    symbols, start, end, sessions, actual_scope = admit_financial_view(
        reader, scope, policy, cutoff, financial_resolution=financial_resolution,
        membership_ref=membership_ref)
    valuation_scope=_valuation_scope(reader,symbols,start,end)
    sw_state=reader.commits['industry_membership'].ref.contract_version=='industry_membership.v3'
    taxonomy=prepared_input(reader,'taxonomy',(policy,cutoff),lambda:
        sorted({s['industry_id'] for r in reader.as_of('industry_membership',knowledge_cutoff=cutoff,pit_policy=policy) for s in r['membership_spans']}) if sw_state else sorted({r['industry_id'] for r in reader.facts('industry_membership')}))
    encoding={industry:i+1 for i,industry in enumerate(taxonomy)}
    # Full-scope admission above must finish before security projection. Retain
    # only this View's requested history, for this projection call's lifetime.
    financial_history=prepared_input(reader,'financial_history',tuple(symbols),
        lambda:reader.facts('financial_events',symbols=symbols))
    financial_times=visibility_times(financial_history,policy) if membership_ref else ()
    previous_visibility=-1;visible_facts=();latest_facts={};latest_income=();derived_count=0
    universe_states=reader.commits['universe_membership'].manifest.get('group_states') if membership_ref else None
    universe_times=visibility_times(universe_states,policy) if universe_states is not None else ()
    industry_rows=(_prepared_selection(reader,'industry_source',
        reader.commits['industry_membership'].ref.commit_id,
        lambda:reader.facts('industry_membership')) if membership_ref and sw_state else ())
    industry_times=visibility_times(industry_rows,policy) if industry_rows else ()
    def last_session_by_visibility(times):
        last={}
        for day in sessions:
            effective=min(instant(cutoff),instant(day+'T23:59:59+08:00'))
            last[bisect_right(times,effective)]=day
        return last
    universe_last=last_session_by_visibility(universe_times) if universe_states is not None else {}
    industry_last=last_session_by_visibility(industry_times) if industry_rows else {}
    previous_universe=-1;selected_universe=();selected_group_states=();universe_key=None
    previous_industry=-1;selected_industry={}
    security={r['symbol']:r for r in reader.security_master(symbols)} if industry_rows else {}
    events=[];derived=[];wide=[];memberships=[];industries=[]
    compact_qualifications=set();compact_derived_count=0
    membership_qualifications=set()
    for session in sessions:
        session_cutoff=min(instant(cutoff),instant(session+'T23:59:59+08:00')).isoformat()
        selector=select_financial_revisions if financial_resolution else select_revisions
        if membership_ref:
            visibility=bisect_right(financial_times,instant(session_cutoff))
            if visibility!=previous_visibility:
                visible_facts=selector(financial_history,knowledge_cutoff=session_cutoff,policy=policy)
                # Validate the complete selected history once per visible world.
                # Only the latest five quarters can affect the dated wide leaves.
                derived_count=len(financial_derived_from_selected(visible_facts,
                    policy=policy,knowledge_cutoff=session_cutoff,
                    resolve_ambiguity=financial_resolution))
                latest_income=latest_financial_income_window(visible_facts)
                latest_facts={}
                for row in visible_facts:
                    key=(row['symbol'],row['endpoint'],row['report_type'])
                    if key not in latest_facts or row['report_period']>latest_facts[key]['report_period']:
                        latest_facts[key]=row
                previous_visibility=visibility
            facts=visible_facts
            stable=financial_derived_from_selected(latest_income,policy=policy,
                knowledge_cutoff=session_cutoff,resolve_ambiguity=financial_resolution)
            compact_derived_count+=derived_count
            compact_qualifications.update(r['pit_qualification'] for r in facts)
        else:
            facts=selector(financial_history,knowledge_cutoff=session_cutoff,policy=policy)
            stable=financial_derived(financial_history,policy=policy,knowledge_cutoff=session_cutoff,
                resolve_ambiguity=financial_resolution)
        valuation=_valuation_at(reader,symbols,session,policy,session_cutoff,prepared=valuation_scope)
        membership={}
        if universe_states is not None:
            visibility=bisect_right(universe_times,instant(session_cutoff))
            if visibility!=previous_universe:
                from axiom_data.financial_coverage import membership_coverage
                for group in scope['universe_ids']:
                    membership_coverage(reader,'universe_membership',group,session,
                        universe_last[visibility],policy,session_cutoff,symbols)
                selected_group_states=select_group_states(universe_states,policy=policy,
                    knowledge_cutoff=session_cutoff)
                state_key=(policy,tuple((s['universe_id'],s['state_id'],s['first_observed_at'],
                    s['usable_from']) for s in selected_group_states))
                universe_key=state_key
                selected_universe=_prepared_selection(reader,'universe_selections',state_key,
                    lambda:reader.as_of('universe_membership',knowledge_cutoff=session_cutoff,
                        pit_policy=policy))
                previous_universe=visibility
        for group in scope['universe_ids']:
            group_rows=(_prepared_members(reader,selected_universe,group,session,universe_key)
                if universe_states is not None else reader.members(group,session,
                    knowledge_cutoff=session_cutoff,pit_policy=policy))
            for row in group_rows:
                membership_qualifications.add(row['pit_qualification'])
                if not membership_ref or row['symbol'] in symbols:
                    membership[row['symbol']]=row
                if not membership_ref:
                    memberships.append(dict(row,target_session=session))
        if industry_rows:
            visibility=bisect_right(industry_times,instant(session_cutoff))
            if visibility!=previous_industry:
                from axiom_data.financial_coverage import membership_coverage
                membership_coverage(reader,'industry_membership',scope['industry_system'],
                    session,industry_last[visibility],policy,session_cutoff,symbols)
                selected_industry=_prepared_selection(reader,'industry_selections',
                    (policy,visibility),lambda:{r['symbol']:r for r in reader.as_of(
                        'industry_membership',knowledge_cutoff=session_cutoff,pit_policy=policy)})
                previous_industry=visibility
            from axiom_data.sw_industry import project_state
            industry_availability={s:project_state(selected_industry[s],security[s],session) for s in symbols}
            industry={s:r for s,r in industry_availability.items() if r['availability_state']=='classified'}
        else:
            industry={r['symbol']:r for r in reader.members(scope['industry_system'],session,
                domain='industry_membership',knowledge_cutoff=session_cutoff,pit_policy=policy,symbols=symbols)}
            industry_availability={r['symbol']:r for r in reader.industry_facts(scope['industry_system'],session,
                knowledge_cutoff=session_cutoff,pit_policy=policy,symbols=symbols)} if sw_state else {}
        if membership_ref:
            compact_qualifications.update(r['pit_qualification'] for r in industry.values())
        else:
            industries.extend(dict(r,target_session=session) for r in industry.values())
            events.extend(dict(r,target_session=session) for r in facts)
            derived.extend(dict(r,target_session=session) for r in stable)
        for symbol in symbols:
            values={f:None for f in WIDE_FIELDS};refs={}; metadata={}
            for leaf in WIDE_FIELDS:
                metadata[leaf]=fact_metadata(None,None,leaf,reader,policy,session_cutoff)
            for leaf,(endpoint,field) in FIELD_MAP.items():
                report_type='supplier_indicator' if endpoint=='fina_indicator' else '1'
                candidates=([latest_facts[(symbol,endpoint,report_type)]]
                    if (symbol,endpoint,report_type) in latest_facts else []) if membership_ref else [
                    r for r in facts if r['symbol']==symbol and r['endpoint']==endpoint and
                    r['report_type']==report_type]
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
                states=(selected_group_states if universe_states is not None else
                    select_group_states(reader.commits['universe_membership'].manifest['group_states'],
                        policy=policy,knowledge_cutoff=session_cutoff))
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
            **({'compact_derived_count':compact_derived_count,
                'compact_qualifications':sorted(compact_qualifications)} if membership_ref else {}),
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


def _industry_ref(reader,scope,policy,cutoff):
    return {'snapshot_id':reader.snapshot.ref.snapshot_id,
            'domain_commit_id':reader.commits['industry_membership'].ref.commit_id,
            'identity_digest':reader.snapshot.manifest['domain_refs']['industry_membership']['identity_digest'],
            'classification_system':scope['industry_system'],
            'start_session':scope['start_session'],'end_session':scope['end_session'],
            'pit_policy':policy,'knowledge_cutoff':instant(cutoff).isoformat()}


def _files(payload, symbols, bundle, *, membership_ref=False):
    stored=({key:payload[key] for key in ('wide','sessions')}
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


def _sparse_files(states, symbols, bundle, *, code_ref=None):
    sessions=states['sessions']
    return {'code_bundle.json':_json_bytes(code_ref if code_ref is not None else bundle), 'states.json.gz':packed_states(states),
            'calendars/day.txt':('\n'.join(sessions)+'\n').encode(),
            'instruments/all.txt':''.join(f'{qlib_symbol(s)}\t{sessions[0]}\t{sessions[-1]}\n'
                                           for s in symbols).encode()}


def _manifest(reader, scope, policy, cutoff, payload, bundle, *, financial_resolution=True,
              membership_ref=False, sparse_states=None, contents=None):
    sparse=sparse_states is not None
    if contents is None:
        contents=(_sparse_files(sparse_states,scope['symbols'],bundle) if sparse else
                  _files(payload,scope['symbols'],bundle,membership_ref=membership_ref))
    return {'artifact_type':'pr6_fact_view','schema_version':('pr6_fact_view.v5' if sparse else
        'pr6_fact_view.v4' if membership_ref else
        'pr6_fact_view.v3' if financial_resolution else 'pr6_fact_view.v2'),
        **({'financial_resolution_policy':'financial_leaf_resolution.v1'} if financial_resolution else {}),
        **({'membership_ref':_membership_ref(reader,scope,policy,cutoff)} if membership_ref else {}),
        **({'industry_ref':_industry_ref(reader,scope,policy,cutoff), 'state_encoding':'leaf_intervals.v1',
            'state_uncompressed_bytes':len(_json_bytes(sparse_states))} if sparse else {}),
        'snapshot_ref':{'snapshot_id':reader.snapshot.ref.snapshot_id,'identity_digest':reader.snapshot.manifest['identity_digest']},
        'domain_refs':reader.snapshot.manifest['domain_refs'],'scope':scope,
        'requested_scope':dict(scope,fields=list(WIDE_FIELDS)),
        'actual_available_scope':payload['actual_available_scope'],
        'validated_scope':dict(scope,fields=list(WIDE_FIELDS)),
        'pit_policy':policy,'knowledge_cutoff':instant(cutoff).isoformat(),
        'cutoff_policy':'min_knowledge_cutoff_session_end_Asia_Shanghai.v1',
        'instrument_storage_scope':[{'symbol':s,'qlib_symbol':qlib_symbol(s),
            'storage_path':'states.json.gz' if sparse else 'features/'+qlib_symbol(s).lower(),
            'start_session':payload['sessions'][0],'end_session':payload['sessions'][-1]} for s in scope['symbols']],
        'fields':list(WIDE_FIELDS),'qlib_field_mapping':{f:f.replace('.','__') for f in WIDE_FIELDS},
        'industry_encoding':payload['industry_encoding'],'industry_mapping':payload['industry_mapping'],
        'fact_metadata_schema':'typed_fact.v2' if financial_resolution else 'typed_fact.v1',
        'implementation_digests':{name:_digest(content.encode()) for name,content in sorted(bundle.items())},
        'files':[{'path':p,'content_digest':_digest(b),'size':len(b)} for p,b in sorted(contents.items())],
        'pit_qualification':weakest_pit_qualification(
            ([{'pit_qualification':q} for q in payload['compact_qualifications']] if membership_ref
             else payload['events']+payload['industries'])+
            ([{'pit_qualification':payload['membership_qualification']}] if membership_ref and
             payload['membership_qualification']!='unknown' else [] if membership_ref else payload['memberships'])),
        'validation_summary':{'status':'PASS','wide_rows':len(payload['wide']),
            'derived_rows':payload['compact_derived_count'] if membership_ref else len(payload['derived']),
            **({'state_changes':sum(len(spans) for by_field in sparse_states['states'].values()
                                   for spans in by_field.values())} if sparse else {})}}


@frozen_operation()
def build_financial_fact_view(data_root,snapshot_id,*,symbols,start_session,end_session,universe_ids,
                        industry_system,pit_policy,knowledge_cutoff):
    return build_financial_fact_view_from_reader(SnapshotReader(data_root,snapshot_id),symbols=symbols,
        start_session=start_session,end_session=end_session,universe_ids=universe_ids,
        industry_system=industry_system,pit_policy=pit_policy,knowledge_cutoff=knowledge_cutoff)


def build_financial_fact_view_from_reader(reader,*,symbols,start_session,end_session,universe_ids,
                         industry_system,pit_policy,knowledge_cutoff):
    if not frozen_execution.is_frozen():
        return build_financial_fact_view(reader.data_root, reader.snapshot.ref.snapshot_id,
            symbols=symbols, start_session=start_session, end_session=end_session,
            universe_ids=universe_ids, industry_system=industry_system,
            pit_policy=pit_policy, knowledge_cutoff=knowledge_cutoff)
    data_root=reader.data_root
    scope={'symbols':list(validate_symbols(symbols)),'start_session':start_session,'end_session':end_session,
           'universe_ids':list(universe_ids),'industry_system':industry_system}
    payload=project(reader,scope,pit_policy,knowledge_cutoff,membership_ref=True)
    states=encode_states(payload['wide'],sessions=payload['sessions'],
        symbol_sessions={s:payload['sessions'] for s in scope['symbols']},fields=WIDE_FIELDS,
        kind='financial',cutoff=knowledge_cutoff)
    SparseDailyRows(states,symbols=scope['symbols'],fields=WIDE_FIELDS,
        kind='financial',cutoff=knowledge_cutoff)
    code_ref=executed_code_ref()
    if code_ref is not None:
        bundle=source_bundle(data_root,code_ref)
    else:
        source_root=Path(str(files("axiom_data")))
        bundle={p.relative_to(source_root).as_posix():p.read_text() for p in sorted(source_root.rglob("*")) if p.is_file() and p.suffix in {".py",".json"}}
    contents=_sparse_files(states,scope['symbols'],bundle,code_ref=code_ref)
    manifest=_manifest(reader,scope,pit_policy,knowledge_cutoff,payload,bundle,
        membership_ref=True,sparse_states=states,contents=contents)
    bind_view_execution(manifest,'pr6_fact_view.v6')
    identity=_identity_digest(manifest,'view_id');view_id=_derived_identity('pr6-fact',identity)
    manifest.update(view_id=view_id,identity_digest=identity,created_at=_timestamp(None))
    layout=_layout(data_root);target=layout.derived_commits('pr6_fact')/view_id
    def prepare(candidate):
        for path,content in contents.items():
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
        schema_version=('pr6_fact_view.v1','pr6_fact_view.v2','pr6_fact_view.v3','pr6_fact_view.v4',
                        'pr6_fact_view.v5','pr6_fact_view.v6'),identity_field='view_id',identity=view_id)
    declared=manifest['schema_version']
    if declared=='pr6_fact_view.v1':
        from axiom_data.pr6_views_v1 import LegacyReader, project as projection, _manifest as manifest_builder, _files as payload_files
    else:
        resolution=declared in {'pr6_fact_view.v3','pr6_fact_view.v4','pr6_fact_view.v5','pr6_fact_view.v6'}
        refs=declared in {'pr6_fact_view.v4','pr6_fact_view.v5','pr6_fact_view.v6'}
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
    if declared in {'pr6_fact_view.v5','pr6_fact_view.v6'}:
        contents=_declared_content_files(layout.root,target,manifest.get('files'))
        try:
            scope=manifest['scope'];policy=manifest['pit_policy'];cutoff=manifest['knowledge_cutoff']
            symbols=validate_symbols(scope['symbols'])
            states=unpacked_states(contents['states.json.gz'],manifest['state_uncompressed_bytes'])
            bundle=view_source_bundle(data_root,manifest,contents['code_bundle.json'])
            rows=SparseDailyRows(states,symbols=symbols,fields=WIDE_FIELDS,kind='financial',cutoff=cutoff)
            summary=manifest['validation_summary']
            expected_scope=dict(scope,fields=list(WIDE_FIELDS))
            expected_instruments=[{'symbol':s,'qlib_symbol':qlib_symbol(s),
                'storage_path':'states.json.gz','start_session':states['sessions'][0],
                'end_session':states['sessions'][-1]} for s in symbols]
            if (scope['symbols']!=list(symbols) or policy not in POLICIES or
                not isinstance(bundle,dict) or not bundle or any(not isinstance(v,str) for v in bundle.values()) or
                not scope['start_session']<=states['sessions'][0]<=states['sessions'][-1]<=scope['end_session'] or
                manifest['snapshot_ref']!={'snapshot_id':reader.snapshot.ref.snapshot_id,
                    'identity_digest':reader.snapshot.manifest['identity_digest']} or
                manifest['domain_refs']!=reader.snapshot.manifest['domain_refs'] or
                manifest['membership_ref']!=_membership_ref(reader,scope,policy,cutoff) or
                manifest['industry_ref']!=_industry_ref(reader,scope,policy,cutoff) or
                manifest['state_encoding']!='leaf_intervals.v1' or
                manifest['requested_scope']!=expected_scope or manifest['validated_scope']!=expected_scope or
                manifest['instrument_storage_scope']!=expected_instruments or
                manifest['fields']!=list(WIDE_FIELDS) or
                manifest['qlib_field_mapping']!={f:f.replace('.','__') for f in WIDE_FIELDS} or
                manifest['implementation_digests']!={n:_digest(c.encode()) for n,c in sorted(bundle.items())} or
                manifest['financial_resolution_policy']!='financial_leaf_resolution.v1' or
                manifest['fact_metadata_schema']!='typed_fact.v2' or
                manifest['cutoff_policy']!='min_knowledge_cutoff_session_end_Asia_Shanghai.v1' or
                manifest['pit_qualification'] not in {'observed','best_effort','unknown'} or
                not isinstance(manifest['actual_available_scope'],dict) or
                summary.get('status')!='PASS' or summary.get('wide_rows')!=len(rows) or
                summary.get('state_changes')!=sum(len(spans) for by_field in states['states'].values()
                    for spans in by_field.values()) or
                type(summary.get('derived_rows')) is not int or summary['derived_rows']<0):
                raise ArtifactError('sparse financial View structural closure mismatch')
        except (KeyError,IndexError,TypeError,ValueError) as exc:
            raise ArtifactError('sparse financial View structure is invalid') from exc
        expected_files=_sparse_files(states,symbols,bundle,code_ref=manifest.get('executed_code_ref'))
        expected_files['states.json.gz']=contents['states.json.gz']
        if contents!=expected_files:
            raise ArtifactError('sparse financial View files differ from declared states')
        return DerivedView(DerivedViewRef('pr6_fact',view_id,digest),manifest,rows)
    if declared=='pr6_fact_view.v4':
        contents=_declared_content_files(layout.root,target,manifest.get('files'))
        try:
            payload=json.loads(contents['rows.json'])
            bundle=json.loads(contents['code_bundle.json'])
            scope=manifest['scope'];policy=manifest['pit_policy'];cutoff=manifest['knowledge_cutoff']
            symbols=validate_symbols(scope['symbols'])
            start=validate_session(scope['start_session'],'start')
            end=validate_session(scope['end_session'],'end')
            if (not isinstance(payload,dict) or set(payload)!={'wide','sessions'} or
                not isinstance(payload['wide'],list) or not isinstance(payload['sessions'],list) or
                not payload['sessions'] or payload['sessions']!=sorted(set(payload['sessions'])) or
                not isinstance(bundle,dict) or not bundle or any(not isinstance(v,str) for v in bundle.values()) or
                start>end or payload['sessions'][0]<start or payload['sessions'][-1]>end or
                scope['symbols']!=list(symbols) or policy not in POLICIES):
                raise ArtifactError('financial View rows have invalid structure')
            sessions=set(payload['sessions']);seen=set()
            for row in payload['wide']:
                if (not isinstance(row,dict) or row.get('symbol') not in symbols or
                    row.get('session') not in sessions or
                    not isinstance(row.get('values'),dict) or set(row['values'])!=set(WIDE_FIELDS) or
                    not isinstance(row.get('facts'),dict) or set(row['facts'])!=set(WIDE_FIELDS)):
                    raise ArtifactError('financial View row structure is invalid')
                key=(row['session'],row['symbol'])
                if key in seen:raise ArtifactError('financial View row is duplicated')
                seen.add(key)
            expected_files=_files(payload,symbols,bundle,membership_ref=True)
            summary=manifest['validation_summary']
            expected_scope=dict(scope,fields=list(WIDE_FIELDS))
            expected_instruments=[{'symbol':s,'qlib_symbol':qlib_symbol(s),
                'storage_path':'features/'+qlib_symbol(s).lower(),
                'start_session':payload['sessions'][0],'end_session':payload['sessions'][-1]} for s in symbols]
            if (manifest['snapshot_ref']!={'snapshot_id':reader.snapshot.ref.snapshot_id,
                'identity_digest':reader.snapshot.manifest['identity_digest']} or
                manifest['domain_refs']!=reader.snapshot.manifest['domain_refs'] or
                manifest['membership_ref']!=_membership_ref(reader,scope,policy,cutoff) or
                manifest['requested_scope']!=expected_scope or manifest['validated_scope']!=expected_scope or
                manifest['instrument_storage_scope']!=expected_instruments or
                manifest['fields']!=list(WIDE_FIELDS) or
                manifest['qlib_field_mapping']!={f:f.replace('.','__') for f in WIDE_FIELDS} or
                manifest['implementation_digests']!={n:_digest(c.encode()) for n,c in sorted(bundle.items())} or
                manifest['financial_resolution_policy']!='financial_leaf_resolution.v1' or
                manifest['fact_metadata_schema']!='typed_fact.v2' or
                manifest['cutoff_policy']!='min_knowledge_cutoff_session_end_Asia_Shanghai.v1' or
                manifest['pit_qualification'] not in {'observed','best_effort','unknown'} or
                not isinstance(manifest['actual_available_scope'],dict) or
                summary.get('status')!='PASS' or summary.get('wide_rows')!=len(payload['wide']) or
                type(summary.get('derived_rows')) is not int or summary['derived_rows']<0):
                raise ArtifactError('financial View structural closure mismatch')
        except (KeyError,IndexError,TypeError,ValueError) as exc:
            raise ArtifactError('financial View structure is invalid') from exc
        if contents!=expected_files:
            raise ArtifactError('financial View files differ from declared rows')
        return DerivedView(DerivedViewRef('pr6_fact',view_id,digest),manifest,tuple(payload['wide']))
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
