"""Snapshot-bound event Fact/Qlib materialization."""

from axiom_data.frozen_execution import (frozen_operation, bind_view_execution, executed_code_ref,
                                         source_bundle, view_source_bundle)
from axiom_data import frozen_execution
import json
from bisect import bisect_right
from contextlib import contextmanager, nullcontext
from pathlib import Path
from importlib.resources import files
from axiom_data.artifacts import (ArtifactError,_layout,_json_bytes,_digest,_identity_digest,_derived_identity,
    _timestamp,_write_file,_write_manifest,_publish_directory,_load_manifest,_validate_manifest_identity,_safe_path,_identity,
    _declared_content_files)
from axiom_data.consumption import SnapshotReader,feature_bytes,qlib_symbol,validate_symbols,validate_session
from axiom_data.views import DerivedView,DerivedViewRef
from axiom_data.view_states import SparseDailyRows, encode_states, packed_states, unpacked_states
from axiom_data.domains.events import EVENT_DOMAINS,DAILY_DOMAINS
from axiom_data.pit import instant, visibility_times
from axiom_data.pit import select_event_revisions

from axiom_data.domains.events import LEAF_DOMAINS, NUMERIC_FIELDS
from axiom_data.consumption import (
    request_coverage, dependency_session, exchange_sessions, select_latest_report,
    leaf_facts, event_leaf_metadata,
)
# Historical imports of this helper continue to resolve to the same implementation.
_leaf_metadata = event_leaf_metadata
EVENT_BATCH_SIZE = 50


@contextmanager
def event_view_batch(reader, configs):
    with getattr(reader, 'consumed_inputs', nullcontext)():
        with _event_view_batch(reader, configs):
            yield


@contextmanager
def _event_view_batch(reader, configs):
    """Admit and read event inputs once for a bounded group of ordinary Views."""
    from axiom_data.verification_cache import file_state, validation_paths

    if not 1 < len(configs) <= EVENT_BATCH_SIZE:
        raise ArtifactError('invalid event View batch')
    symbols = []
    for config in configs:
        selected = validate_symbols(config['symbols'])
        if len(selected) != 1:
            raise ArtifactError('event View batch requires one symbol per View')
        symbols.append(selected[0])
    start = min(validate_session(c['start_session'], 'start') for c in configs)
    end = max(validate_session(c['end_session'], 'end') for c in configs)
    with validation_paths() as observed:
        calendars = exchange_sessions(reader, symbols, start, end)
        if any(d not in reader.commits for d in EVENT_DOMAINS):
            raise ArtifactError('event Snapshot domains required')
        daily_bounds = {}
        for domain in EVENT_DOMAINS:
            required = []
            for config in configs:
                symbol = config['symbols'][0]
                sessions = (s for s in calendars[symbol]['sessions']
                            if config['start_session'] <= s <= config['end_session'])
                for session in sessions:
                    source_session = dependency_session(reader, domain, session)
                    request_coverage(reader, domain, symbol, source_session)
                    if domain in DAILY_DOMAINS:
                        required.append(source_session)
            if required:
                daily_bounds[domain] = (min(required), max(required))
        rows = {}
        for domain in EVENT_DOMAINS:
            bounds = daily_bounds.get(domain)
            rows[domain] = reader.facts(domain, symbols=symbols,
                **({'start_session':bounds[0], 'end_session':bounds[1]} if bounds else {}))
    if hasattr(reader, '_remember_consumed_metadata'):
        reader._remember_consumed_metadata(observed)
    by_symbol = {domain: {symbol: [] for symbol in symbols} for domain in EVENT_DOMAINS}
    for domain in EVENT_DOMAINS:
        for row in rows[domain]:
            by_symbol[domain][row['symbol']].append(row)
    previous = getattr(reader, '_event_view_batch', None)
    reader._event_view_batch = (start, end, set(symbols), calendars, by_symbol)
    try:
        yield
    finally:
        reader._event_view_batch = previous
        if any(file_state(path) != state for path, state in observed.items()):
            raise ArtifactError('event View source changed during batch')


def project(reader,scope,policy,cutoff,*,source_cutoffs=True):
    if hasattr(reader, '_check_consumed_metadata'):
        reader._check_consumed_metadata(force=True)
    with getattr(reader, 'consumed_inputs', nullcontext)():
        return _project(reader,scope,policy,cutoff,source_cutoffs=source_cutoffs)


def admit_event_view(reader, scope, policy, cutoff, *, source_cutoffs=True):
    from axiom_data.pit import POLICIES
    symbols=validate_symbols(scope['symbols'])
    start=validate_session(scope['start_session'],'start');end=validate_session(scope['end_session'],'end')
    if start>end:raise ArtifactError('reversed View range')
    if policy not in POLICIES:raise ArtifactError('invalid fact PIT policy')
    instant(cutoff)
    batch=getattr(reader,'_event_view_batch',None)
    prepared=source_cutoffs and batch is not None and batch[0]<=start and end<=batch[1] and set(symbols)<=batch[2]
    calendars=({s:{'exchange':batch[3][s]['exchange'],
        'sessions':[d for d in batch[3][s]['sessions'] if start<=d<=end]} for s in symbols}
        if prepared else exchange_sessions(reader,symbols,start,end))
    if not any(item['sessions'] for item in calendars.values()):
        raise ArtifactError('INSUFFICIENT_SCOPE: no open exchange sessions')
    if any(d not in reader.commits for d in EVENT_DOMAINS):raise ArtifactError('event Snapshot domains required')
    for domain in (() if prepared else EVENT_DOMAINS):
        for symbol in symbols:
            for session in calendars[symbol]['sessions']:
                request_coverage(reader,domain,symbol,
                    dependency_session(reader,domain,session) if source_cutoffs else session)
        if domain in DAILY_DOMAINS:
            days=[dependency_session(reader,domain,day) if source_cutoffs else day
                  for item in calendars.values() for day in item['sessions']]
            rows=reader.facts(domain,symbols=symbols,start_session=min(days),end_session=max(days))
        else:
            rows=reader.facts(domain,symbols=symbols)
        visibility_times(rows,policy)
    return calendars


def _project(reader,scope,policy,cutoff,*,source_cutoffs=True):
    from axiom_data.contracts import load_contract
    symbols=validate_symbols(scope['symbols']);start=validate_session(scope['start_session'],'start');end=validate_session(scope['end_session'],'end')
    if start>end:raise ArtifactError('reversed View range')
    batch=getattr(reader,'_event_view_batch',None)
    prepared=(batch is not None and batch[0]<=start and end<=batch[1]
              and set(symbols)<=batch[2] and source_cutoffs)
    calendars=({symbol:{'exchange':batch[3][symbol]['exchange'],
                        'sessions':[s for s in batch[3][symbol]['sessions'] if start<=s<=end]}
                for symbol in symbols} if prepared else admit_event_view(reader,scope,policy,cutoff,source_cutoffs=source_cutoffs))
    if not any(item['sessions'] for item in calendars.values()):
        raise ArtifactError('INSUFFICIENT_SCOPE: no open exchange sessions')
    if any(d not in reader.commits for d in EVENT_DOMAINS):raise ArtifactError('event Snapshot domains required')
    units={domain:load_contract(reader.commits[domain].ref.contract_version)['value_units']
           for domain in EVENT_DOMAINS}
    wide=[];event_history={}
    open_days={symbol:set(item['sessions']) for symbol,item in calendars.items()}
    for session in sorted({day for item in calendars.values() for day in item['sessions']}):
        active=[symbol for symbol in symbols if session in open_days[symbol]]
        effective=min(instant(cutoff),instant(session+'T23:59:59+08:00')).isoformat()
        selected={};source_sessions={}
        for domain in EVENT_DOMAINS:
            source_session=dependency_session(reader,domain,session) if source_cutoffs else session
            source_sessions[domain]=source_session
            if not prepared:
                for symbol in active:request_coverage(reader,domain,symbol,source_session)
            bounds={'start_session':source_session,'end_session':source_session} if domain in DAILY_DOMAINS else {}
            groups={symbol:[] for symbol in active}
            if domain in DAILY_DOMAINS:
                if prepared:
                    selected_rows=select_event_revisions(
                        [row for symbol in active for row in batch[4][domain][symbol]
                         if row['session']==source_session],policy=policy,knowledge_cutoff=effective)
                else:
                    selected_rows=reader.as_of(domain,symbols=active,pit_policy=policy,knowledge_cutoff=effective,**bounds)
            else:
                # Coverage checks remain above preparation. Histories are scoped
                # to requested securities and released when this View returns.
                if domain not in event_history:
                    rows=(tuple(row for symbol in symbols for row in batch[4][domain][symbol])
                          if prepared else reader.facts(domain,symbols=symbols))
                    by_symbol={symbol:tuple(row for row in rows if row['symbol']==symbol) for symbol in symbols}
                    event_history[domain]={'rows':by_symbol,
                        'times':{symbol:visibility_times(by_symbol[symbol],policy) for symbol in symbols},
                        'index':{},'selected':{}}
                history=event_history[domain]
                selected_rows=[]
                for symbol in active:
                    visibility=bisect_right(history['times'][symbol],instant(effective))
                    if (not source_cutoffs or history['index'].get(symbol)!=visibility):
                        history['selected'][symbol]=select_event_revisions(
                            history['rows'][symbol],policy=policy,knowledge_cutoff=effective)
                        history['index'][symbol]=visibility
                    selected_rows.extend(history['selected'][symbol])
            for row in selected_rows:
                groups[row['symbol']].append(row)
            selected[domain]=groups
        for symbol in active:
            facts={leaf:event_leaf_metadata(reader,leaf,symbol,session,effective,policy,selected[domain][symbol],
                source_session=source_sessions[domain],value_unit=units[domain][leaf.split('.',1)[1]])
                for leaf,domain in LEAF_DOMAINS.items()}
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


def sparse_files(states,symbols,bundle,*,code_ref=None):
    sessions=states['sessions']
    return {'states.json.gz':packed_states(states),'code_bundle.json':_json_bytes(code_ref if code_ref is not None else bundle),
        'calendars/day.txt':('\n'.join(sessions)+'\n').encode(),
        'instruments/all.txt':''.join(f'{qlib_symbol(s)}\t{sessions[0]}\t{sessions[-1]}\n'
                                       for s in symbols).encode()}


def manifest_for(reader,scope,policy,cutoff,payload,bundle,*,schema_version='event_fact_view.v3',
                 contents=None,sparse_states=None):
    if contents is None:contents=(sparse_files(sparse_states,scope['symbols'],bundle)
                                 if sparse_states is not None else payload_files(payload,scope['symbols'],bundle))
    return {'artifact_type':'event_fact_view','schema_version':schema_version,
        **({'state_encoding':'leaf_intervals.v1',
            'state_uncompressed_bytes':len(_json_bytes(sparse_states))} if sparse_states is not None else {}),
        'snapshot_ref':{'snapshot_id':reader.snapshot.ref.snapshot_id,'identity_digest':reader.snapshot.manifest['identity_digest']},
        'scope':scope,'validated_scope':dict(scope,fields=list(LEAF_DOMAINS)),
        'pit_policy':policy,'knowledge_cutoff':instant(cutoff).isoformat(),
        'cutoff_policy':'min_knowledge_cutoff_session_end_Asia_Shanghai.v1',
        'fields':list(NUMERIC_FIELDS),'fact_fields':list(LEAF_DOMAINS),'qlib_field_mapping':{f:f.replace('.','__') for f in NUMERIC_FIELDS},
        'instrument_storage_scope':[{'symbol':s,'qlib_symbol':qlib_symbol(s),
            'storage_path':'states.json.gz' if sparse_states is not None else 'features/'+qlib_symbol(s).lower(),
            'start_session':payload['sessions'][0],'end_session':payload['sessions'][-1],
            'exchange':payload['symbol_calendars'][s]['exchange'],
            'valid_sessions':payload['symbol_calendars'][s]['sessions']} for s in scope['symbols']],
        'implementation_digests':{n:_digest(c.encode()) for n,c in sorted(bundle.items())},
        'files':[{'path':p,'content_digest':_digest(b),'size':len(b)} for p,b in sorted(contents.items())],
        'validation_summary':{'status':'PASS','rows':len(payload['wide']),'leaves':len(LEAF_DOMAINS),
            **({'state_changes':sum(len(spans) for by_field in sparse_states['states'].values()
                                   for spans in by_field.values())} if sparse_states is not None else {})}}


@frozen_operation()
def build_event_fact_view(data_root,snapshot_id,*,symbols,start_session,end_session,pit_policy,knowledge_cutoff):
    return build_event_fact_view_from_reader(SnapshotReader(data_root,snapshot_id),symbols=symbols,
        start_session=start_session,end_session=end_session,pit_policy=pit_policy,knowledge_cutoff=knowledge_cutoff)


def build_event_fact_view_from_reader(reader,*,symbols,start_session,end_session,pit_policy,knowledge_cutoff):
    if not frozen_execution.is_frozen():
        return build_event_fact_view(reader.data_root, reader.snapshot.ref.snapshot_id,
            symbols=symbols, start_session=start_session, end_session=end_session,
            pit_policy=pit_policy, knowledge_cutoff=knowledge_cutoff)
    data_root=reader.data_root;scope={'symbols':list(validate_symbols(symbols)),'start_session':start_session,'end_session':end_session}
    payload=project(reader,scope,pit_policy,knowledge_cutoff)
    states=encode_states(payload['wide'],sessions=payload['sessions'],
        symbol_sessions={s:payload['symbol_calendars'][s]['sessions'] for s in scope['symbols']},
        fields=LEAF_DOMAINS,kind='event',cutoff=knowledge_cutoff)
    SparseDailyRows(states,symbols=scope['symbols'],fields=LEAF_DOMAINS,
        kind='event',cutoff=knowledge_cutoff)
    code_ref=executed_code_ref()
    if code_ref is not None:
        bundle=source_bundle(data_root,code_ref)
    else:
        source=Path(str(files('axiom_data')));bundle={p.relative_to(source).as_posix():p.read_text() for p in sorted(source.rglob('*')) if p.is_file() and p.suffix in {'.py','.json'}}
    contents=sparse_files(states,scope['symbols'],bundle,code_ref=code_ref)
    manifest=manifest_for(reader,scope,pit_policy,knowledge_cutoff,payload,bundle,
        schema_version='event_fact_view.v4',contents=contents,sparse_states=states)
    bind_view_execution(manifest,'event_fact_view.v5')
    identity=_identity_digest(manifest,'view_id');view_id=_derived_identity('event-fact',identity)
    manifest.update(view_id=view_id,identity_digest=identity,created_at=_timestamp(None))
    layout=_layout(data_root);target=layout.derived_commits('event_fact')/view_id
    def prepare(candidate):
        for path,content in contents.items():
            (candidate/path).parent.mkdir(parents=True,exist_ok=True);_write_file(candidate/path,content)
        _write_manifest(candidate,manifest)
    _publish_directory(layout,target,prepare,identity_digest=identity)
    return load_event_fact_view_with_reader(data_root,view_id,checked_reader=reader).ref


def load_event_fact_view(data_root,view_id):
    return load_event_fact_view_with_reader(data_root,view_id)


def load_event_fact_view_with_reader(data_root,view_id,*,checked_reader=None):
    from axiom_data.deprecated.view_protocols import historical_view_kind
    if historical_view_kind(view_id):
        from axiom_data.deprecated.event_views import load_event_fact_view_with_reader as load_historical
        return load_historical(data_root, view_id, checked_reader=checked_reader)
    view_id=_identity('view_id',view_id);layout=_layout(data_root);target=layout.derived_commits('event_fact')/view_id
    manifest,digest=_load_manifest(layout.root,target,artifact_type='event_fact_view',
        schema_version=('event_fact_view.v1','event_fact_view.v2','event_fact_view.v3','event_fact_view.v4','event_fact_view.v5'),
        identity_field='view_id',identity=view_id)
    _validate_manifest_identity(manifest,'view_id','event-fact',view_id)
    projection,make_manifest,make_files=project,manifest_for,payload_files
    reader=checked_reader or SnapshotReader(data_root,manifest['snapshot_ref']['snapshot_id'])
    if (Path(reader.data_root).resolve()!=layout.root.resolve() or
        reader.snapshot.ref.snapshot_id!=manifest['snapshot_ref']['snapshot_id']):
        raise ArtifactError('checked Reader does not match event View Snapshot')
    if manifest['schema_version'] in {'event_fact_view.v4','event_fact_view.v5'}:
        contents=_declared_content_files(layout.root,target,manifest.get('files'))
        try:
            states=unpacked_states(contents['states.json.gz'],manifest['state_uncompressed_bytes'])
            bundle=view_source_bundle(data_root,manifest,contents['code_bundle.json'])
            scope=manifest['scope'];symbols=validate_symbols(scope['symbols'])
            rows=SparseDailyRows(states,symbols=symbols,fields=LEAF_DOMAINS,kind='event',
                cutoff=manifest['knowledge_cutoff'])
            instruments=manifest['instrument_storage_scope']
            if (scope['symbols']!=list(symbols) or not isinstance(instruments,list) or
                len(instruments)!=len(symbols) or
                not isinstance(bundle,dict) or not bundle or any(not isinstance(v,str) for v in bundle.values()) or
                not scope['start_session']<=states['sessions'][0]<=states['sessions'][-1]<=scope['end_session'] or
                manifest['snapshot_ref']!={'snapshot_id':reader.snapshot.ref.snapshot_id,
                    'identity_digest':reader.snapshot.manifest['identity_digest']} or
                manifest['state_encoding']!='leaf_intervals.v1'):
                raise ArtifactError('sparse event View structure is invalid')
            calendars={s:{'exchange':instruments[i]['exchange'],
                          'sessions':states['symbol_sessions'][s]} for i,s in enumerate(symbols)}
            expected_payload={'wide':rows,'sessions':states['sessions'],'symbol_calendars':calendars}
            expected_files=sparse_files(states,symbols,bundle,code_ref=manifest.get('executed_code_ref'))
            expected_files['states.json.gz']=contents['states.json.gz']
            expected=manifest_for(reader,scope,manifest['pit_policy'],manifest['knowledge_cutoff'],
                expected_payload,bundle,schema_version=manifest['schema_version'],
                contents=expected_files,sparse_states=states)
            if 'executed_code_ref' in manifest:
                expected['executed_code_ref']=manifest['executed_code_ref']
        except (KeyError,IndexError,TypeError,ValueError) as exc:
            raise ArtifactError('sparse event View structure is invalid') from exc
        if {k:v for k,v in manifest.items() if k not in {'view_id','identity_digest','created_at'}}!=expected:
            raise ArtifactError('sparse event View structural closure mismatch')
        if contents!=expected_files:
            raise ArtifactError('sparse event View files differ from declared states')
        return DerivedView(DerivedViewRef('event_fact',view_id,digest),manifest,rows)
    if manifest['schema_version']=='event_fact_view.v3':
        contents=_declared_content_files(layout.root,target,manifest.get('files'))
        try:
            payload=json.loads(contents['rows.json'])
            bundle=json.loads(contents['code_bundle.json'])
            if not isinstance(payload,dict) or set(payload)!={'wide','sessions','symbol_calendars'}:
                raise ArtifactError('event View rows have invalid structure')
            if not isinstance(bundle,dict) or not bundle or any(not isinstance(v,str) for v in bundle.values()):
                raise ArtifactError('invalid code bundle')
            outputs=payload_files(payload,manifest['scope']['symbols'],bundle)
            expected=manifest_for(reader,manifest['scope'],manifest['pit_policy'],
                manifest['knowledge_cutoff'],payload,bundle,contents=outputs)
        except (KeyError,IndexError,TypeError,ValueError) as exc:
            raise ArtifactError('event View structure is invalid') from exc
        if {k:v for k,v in manifest.items() if k not in {'view_id','identity_digest','created_at'}}!=expected:
            raise ArtifactError('event View structural closure mismatch')
        if contents!=outputs:
            raise ArtifactError('event View files differ from declared rows')
        return DerivedView(DerivedViewRef('event_fact',view_id,digest),manifest,tuple(payload['wide']))
    payload=projection(reader,manifest['scope'],manifest['pit_policy'],manifest['knowledge_cutoff'])
    bundle=json.loads(_safe_path(layout.root,target/'code_bundle.json',closure=target).read_bytes())
    if not isinstance(bundle,dict) or not bundle or any(not isinstance(v,str) for v in bundle.values()):raise ArtifactError('invalid code bundle')
    expected=make_manifest(reader,manifest['scope'],manifest['pit_policy'],manifest['knowledge_cutoff'],payload,bundle)
    if {k:v for k,v in manifest.items() if k not in {'view_id','identity_digest','created_at'}}!=expected:raise ArtifactError('event View semantic closure mismatch')
    for path,content in make_files(payload,manifest['scope']['symbols'],bundle).items():
        if _safe_path(layout.root,target/path,closure=target).read_bytes()!=content:raise ArtifactError('event View file differs from Snapshot projection')
    return DerivedView(DerivedViewRef('event_fact',view_id,digest),manifest,tuple(payload['wide']))


# Compatibility exports for historical callers.



def rebound_event_states(reader, view, source):
    from axiom_data.consumption import bind_event_snapshot
    states=unpacked_states((source/'states.json.gz').read_bytes(),view.manifest['state_uncompressed_bytes'])
    for by_field in states['states'].values():
        for spans in by_field.values():
            for span in spans:
                bind_event_snapshot(span['state']['fact'],reader.snapshot.ref.snapshot_id)
    return packed_states(states), len(_json_bytes(states))
