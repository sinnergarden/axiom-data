"""Thin Data-consumer conformance over the frozen 56-leaf dependency boundary."""
from axiom_data.deprecated.resources import resource_file, profile_generation, historical_profile, source_reference
import json
from importlib.resources import files
from axiom_data import SnapshotReader,FactView
from axiom_data.artifacts import ArtifactError
from axiom_data.domains.fundamentals import WIDE_FIELDS
from axiom_data.domains.events import LEAF_DOMAINS
from axiom_data.financial_views import load_financial_fact_view
from axiom_data.event_views import load_event_fact_view
from axiom_data.pit import fingerprint


def consumer_admission(root,refs):
    scope=json.loads(resource_file('scope', 'data_dependency_scope.v1.json').read_bytes())
    reader=SnapshotReader(root,refs['snapshot_id'])
    facts=FactView(root,refs['snapshot_id'],adjusted_price_view_id=refs['adjusted_view_id'],financial_fact_view_id=refs['financial_view_id'])
    financial=load_financial_fact_view(root,refs['financial_view_id']);event=load_event_fact_view(root,refs['event_view_id'])
    if event.manifest['snapshot_ref']['snapshot_id']!=refs['snapshot_id']:raise ArtifactError('consumer View Snapshot mismatch')
    matrix=[];logical={}
    for leaf,spec in scope['reference_public_evidence'].items():
        domain=spec['domain'];field=spec['field']
        if domain=='adjusted_price':
            result=facts.read('adjusted_price',symbols=['688981.SH'],start_session='2025-06-10',end_session='2025-06-13',price_basis='anchor_adjusted',pit_policy='research_non_pit')
            rows=result['rows'];artifact=refs['adjusted_view_id'];contract='adjusted_price_view.v1'
        else:
            kwargs={'start_session':'2025-06-10','end_session':'2025-06-13','fields':[field]}
            if domain!='benchmark_daily':kwargs['symbols']=['688981.SH']
            rows=facts.read(domain,**kwargs)['rows'];artifact=reader.commits[domain].ref.commit_id;contract=reader.commits[domain].ref.contract_version
        if not rows:raise ArtifactError('consumer has no actual rows for '+leaf)
        logical[leaf]=list(rows)
        matrix.append({'requirement':leaf,'owner':domain,'contract':contract,'artifact':artifact,'public_field':field,
            'pit_qualification':'best_effort','coverage':{'symbol':'688981.SH' if domain!='benchmark_daily' else 'benchmark','start':'2025-06-10','end':'2025-06-13','rows':len(rows)},'status':'resolved'})
    for leaf in WIDE_FIELDS:
        rows=[r['facts'][leaf] for r in financial.rows if r['symbol']=='688981.SH']
        if not rows:raise ArtifactError('no public financial metadata for '+leaf)
        logical[leaf]=rows
        owner=('financial_stable_derived' if leaf.startswith('financial.') else
               'universe_membership' if leaf=='universe.membership' else
               'industry_membership' if leaf=='industry.membership' else
               'valuation_daily' if leaf.startswith('valuation.') else 'financial_events')
        source_commit=reader.commits['financial_events' if owner=='financial_stable_derived' else owner]
        matrix.append({'requirement':leaf,'owner':owner,
            'contract':financial.manifest['schema_version'] if owner=='financial_stable_derived' else source_commit.ref.contract_version,
            'artifact':refs['financial_view_id'] if owner=='financial_stable_derived' else source_commit.ref.commit_id,
            'view':refs['financial_view_id'],'public_field':leaf,'pit_qualification':'best_effort',
            'coverage':{'symbol':'688981.SH','start':'2025-06-10','end':'2025-06-13','rows':len(rows)},'status':'resolved'})
    for leaf,domain in LEAF_DOMAINS.items():
        symbol='000401.SZ' if leaf.startswith('forecast.') else '688981.SH'
        row=facts.leaf_fact(leaf,symbol=symbol,target_session='2025-06-13',knowledge_cutoff='2025-06-13T23:59:59+08:00',pit_policy='best_effort_vendor_v1')
        if row['revision_ref'] is None:raise ArtifactError('real event leaf has no visible evidence: '+leaf)
        logical[leaf]=[row]
        matrix.append({'requirement':leaf,'owner':domain,'contract':row['contract_version'],'artifact':reader.commits[domain].ref.commit_id,
            'public_field':leaf,'view':refs['event_view_id'],'pit_qualification':row['pit_qualification'],
            'coverage':{'symbol':symbol,'target_session':'2025-06-13','rows':1},'status':'resolved'})
    all_leaves={r['requirement'] for r in matrix}
    if len(matrix)!=56 or all_leaves!=set().union(*map(set,scope['partition'].values())):raise ArtifactError('56 requirement closure mismatch')
    features=[]
    for feature in scope['feature_dependencies']:
        unresolved=set(feature['leaves'])-all_leaves
        if unresolved:raise ArtifactError('unresolved Feature dependencies: '+str(unresolved))
        features.append(dict(feature,status='resolved'))
    if len(features)!=469:raise ArtifactError('authoritative Feature count mismatch')
    for row in matrix:row['consumer_feature_count']=sum(row['requirement'] in f['leaves'] for f in features)
    query={'pit_policy':'best_effort_vendor_v1','knowledge_cutoff':'2025-06-13T23:59:59+08:00'}
    union=reader.historical_union('000906.SH','2025-06-10','2025-06-13','2025-06-09',**query)
    memberships={s:list(reader.members('000906.SH',s,**query)) for s in ('2025-06-10','2025-06-11','2025-06-12','2025-06-13')}
    lookback=reader.market_daily(['688981.SH'],'2025-06-09','2025-06-13')
    if not union or not lookback or lookback[0]['session']!='2025-06-09':raise ArtifactError('historical union/lookback missing')
    return {'schema_version':'event_admission_admission.v1','snapshot_id':refs['snapshot_id'],'requirements':sorted(matrix,key=lambda r:r['requirement']),
        'features':features,'requirement_count':56,'feature_count':469,'logical_results':logical,
        'logical_digest':fingerprint(logical),'historical_union':list(union),'per_session_members':memberships,'lookback_rows':list(lookback),
        'status':'PASS','qualification':'bounded real scope; historical terminal best_effort; Data dependency admission only'}


# Compatibility exports for historical callers.
