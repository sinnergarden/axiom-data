"""Read the published historical financial View formats without changing identity."""
from axiom_data.financial_views import (ArtifactError, DerivedView, DerivedViewRef, POLICIES, Path, SnapshotReader, SparseDailyRows, WIDE_FIELDS, _declared_content_files, _digest, _files, _identity, _industry_ref, _layout, _load_manifest, _manifest, _membership_ref, _safe_path, _sparse_files, _validate_manifest_identity, json, partial, project, qlib_symbol, unpacked_states, validate_session, validate_symbols, view_source_bundle)

from axiom_data.financial_views import _manifest as current_manifest

def _manifest(*args, **kwargs):
    value = current_manifest(*args, **kwargs)
    value["artifact_type"] = "pr6_fact_view"
    value["schema_version"] = {"financial_fact_view.v2":"pr6_fact_view.v2", "financial_fact_view.v3":"pr6_fact_view.v3", "financial_fact_view.v4":"pr6_fact_view.v4", "financial_fact_view.v5":"pr6_fact_view.v5"}[value["schema_version"]]
    return value

def load_financial_fact_view_with_reader(data_root,view_id,*,checked_reader=None):
    view_id=_identity('view_id',view_id)
    layout=_layout(data_root);target=layout.derived_commits('pr6_fact')/view_id
    manifest,digest=_load_manifest(layout.root,target,artifact_type='pr6_fact_view',
        schema_version=('pr6_fact_view.v1','pr6_fact_view.v2','pr6_fact_view.v3','pr6_fact_view.v4',
                        'pr6_fact_view.v5','pr6_fact_view.v6'),identity_field='view_id',identity=view_id)
    declared=manifest['schema_version']
    if declared=='pr6_fact_view.v1':
        from axiom_data.deprecated.pr6_views_v1 import LegacyReader, project as projection, _manifest as manifest_builder, _files as payload_files
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
