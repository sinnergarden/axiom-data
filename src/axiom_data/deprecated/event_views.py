"""Read the published historical event View formats without changing identity."""
from axiom_data.event_views import (ArtifactError, DerivedView, DerivedViewRef, LEAF_DOMAINS, Path, SnapshotReader, SparseDailyRows, _declared_content_files, _identity, _layout, _load_manifest, _safe_path, _validate_manifest_identity, json, manifest_for, payload_files, project, sparse_files, unpacked_states, validate_symbols, view_source_bundle)

from axiom_data.event_views import manifest_for as current_manifest

def manifest_for(*args, schema_version="pr7_fact_view.v3", **kwargs):
    value = current_manifest(*args, schema_version=schema_version, **kwargs)
    value["artifact_type"] = "pr7_fact_view"
    return value

def load_event_fact_view_with_reader(data_root,view_id,*,checked_reader=None):
    view_id=_identity('view_id',view_id);layout=_layout(data_root);target=layout.derived_commits('pr7_fact')/view_id
    manifest,digest=_load_manifest(layout.root,target,artifact_type='pr7_fact_view',
        schema_version=('pr7_fact_view.v1','pr7_fact_view.v2','pr7_fact_view.v3','pr7_fact_view.v4','pr7_fact_view.v5'),
        identity_field='view_id',identity=view_id)
    _validate_manifest_identity(manifest,'view_id','pr7-fact',view_id)
    if manifest['schema_version']=='pr7_fact_view.v1':
        from axiom_data.deprecated.pr7_views_v1 import project as projection, manifest_for as make_manifest, payload_files as make_files
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
    if manifest['schema_version'] in {'pr7_fact_view.v4','pr7_fact_view.v5'}:
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
        return DerivedView(DerivedViewRef('pr7_fact',view_id,digest),manifest,rows)
    if manifest['schema_version']=='pr7_fact_view.v3':
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
        return DerivedView(DerivedViewRef('pr7_fact',view_id,digest),manifest,tuple(payload['wide']))
    payload=projection(reader,manifest['scope'],manifest['pit_policy'],manifest['knowledge_cutoff'])
    bundle=json.loads(_safe_path(layout.root,target/'code_bundle.json',closure=target).read_bytes())
    if not isinstance(bundle,dict) or not bundle or any(not isinstance(v,str) for v in bundle.values()):raise ArtifactError('invalid code bundle')
    expected=make_manifest(reader,manifest['scope'],manifest['pit_policy'],manifest['knowledge_cutoff'],payload,bundle)
    if {k:v for k,v in manifest.items() if k not in {'view_id','identity_digest','created_at'}}!=expected:raise ArtifactError('event View semantic closure mismatch')
    for path,content in make_files(payload,manifest['scope']['symbols'],bundle).items():
        if _safe_path(layout.root,target/path,closure=target).read_bytes()!=content:raise ArtifactError('event View file differs from Snapshot projection')
    return DerivedView(DerivedViewRef('pr7_fact',view_id,digest),manifest,tuple(payload['wide']))
