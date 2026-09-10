"""Complete SW2021 security histories and availability-aware interval projection."""
from collections import defaultdict
from copy import deepcopy
from datetime import date,timedelta
from importlib.resources import files
import json
from axiom_data import ArtifactError,load_raw_batch
from axiom_data.artifacts import _digest,_json_bytes
from axiom_data.pit import fingerprint,instant,select_revisions
from axiom_data.sw_mapping import canonical_taxonomy,load_mapping_profile
from axiom_data.industry_qualification import observation
from axiom_data.tushare import _source_date


def build_rows(builder,contract,parent_rows,raw_batches):
    if contract['contract_version']!='industry_membership.v3':raise ArtifactError('SW2021 contract version required')
    cfg=builder.builder_config; symbols=cfg.get('symbols'); start=cfg.get('start_session');end=cfg.get('end_session')
    if not isinstance(symbols,list) or not symbols or len(set(symbols))!=len(symbols):raise ArtifactError('explicit SW2021 security scope required')
    start=_source_date(start);end=_source_date(end)
    if start>end:raise ArtifactError('reversed industry coverage')
    upper=(date.fromisoformat(end)+timedelta(days=1)).isoformat()
    mapping=load_mapping_profile()
    if cfg.get('sw_mapping_profile')!=mapping:raise ArtifactError('SW2021 builder mapping profile mismatch')
    taxonomy=[];taxonomy_refs=[];pages=defaultdict(list); levels=[]
    for raw in raw_batches:
        checked,rows=observation(builder.layout.root,raw.ref.raw_batch_id)
        request=checked.manifest['request'];ep=request['endpoint'];p=request['params']
        if ep=='index_classify':
            taxonomy.extend(rows);taxonomy_refs.append(raw.ref.raw_batch_id);levels.append(p['level'])
        elif ep=='index_member_all' and set(p)=={'is_new','limit','offset'}:
            pages[p['is_new']].append((int(p['offset']),int(p['limit']),rows,raw))
        else:raise ArtifactError('SW build requires full frozen taxonomy and global Y/N pages')
    if sorted(levels)!=['L1','L2','L3'] or set(pages)!={'Y','N'}:raise ArtifactError('incomplete SW collection')
    members=[];member_refs=[];by_row={}
    for mode in ['Y','N']:
        offset=0;terminal=False;seen=set()
        for actual,limit,rows,raw in sorted(pages[mode],key=lambda p:p[0]):
            if terminal or actual!=offset or len(rows)>limit:raise ArtifactError('incomplete SW page sequence')
            for row in rows:
                key=fingerprint(row)
                if key in seen:raise ArtifactError('duplicate SW page record')
                seen.add(key);members.append(row);by_row[key]=raw.ref.raw_batch_id
            member_refs.append(raw.ref.raw_batch_id);offset+=limit;terminal=len(rows)<limit
        if not terminal:raise ArtifactError('missing SW terminal page')
    nodes,mapping_proof=canonical_taxonomy(taxonomy,members,profile=mapping,taxonomy_raw_refs=taxonomy_refs,membership_raw_refs=member_refs)
    index={r['index_code']:r for r in nodes}
    if len(index)!=len(nodes):raise ArtifactError('duplicate canonical taxonomy node')
    industry_codes={r['industry_code']:r for r in nodes}
    for node in nodes:
        if node['level']=='L1':
            if node['parent_code']!='0':raise ArtifactError('invalid taxonomy root')
        elif node['parent_code'] not in industry_codes or industry_codes[node['parent_code']]['level']!=('L1' if node['level']=='L2' else 'L2'):
            raise ArtifactError('invalid taxonomy hierarchy')
    by_symbol=defaultdict(list)
    for source in members:
        for level in ['l1','l2','l3']:
            node=index.get(source[level+'_code'])
            if node is None or node['level']!=level.upper() or node['industry_name']!=source[level+'_name']:
                raise ArtifactError('unresolved SW taxonomy/member identity')
            if level!='l1' and node['parent_code']!=index[source[('l1' if level=='l2' else 'l2')+'_code']]['industry_code']:
                raise ArtifactError('SW member parent mismatch')
        since=_source_date(source['in_date']);until=_source_date(source['out_date'],nullable=True)
        if until and until<since:raise ArtifactError('reversed SW membership interval')
        span={'industry_id':source['l3_code'],'effective_from':since,'effective_to':until,
              'taxonomy':{level:{'code':source[level+'_code'],'name':source[level+'_name']} for level in ['l1','l2','l3']},
              'mapping_provenance':mapping_proof if source['l3_code']=='850412.SI' else None,
              'source_ref':by_row[fingerprint(source)]}
        by_symbol[source['ts_code']].append(span)
    latest=max(raw_batches,key=lambda r:instant(r.manifest['retrieved_at']))
    observed=latest.manifest['retrieved_at']; raw_refs=sorted(r.ref.raw_batch_id for r in raw_batches)
    content={(r['logical_event_key'],r['revision_id']):deepcopy(r) for r in parent_rows}
    for symbol in sorted(symbols):
        spans=sorted(by_symbol[symbol],key=lambda r:(r['effective_from'],r['industry_id']))
        for left,right in zip(spans,spans[1:]):
            if left['effective_to'] is None or right['effective_from']<left['effective_to']:
                raise ArtifactError('overlapping SW membership intervals')
        row={'symbol':symbol,'group_id':'SW2021','classification_system':'SW2021',
             'logical_event_key':fingerprint(['SW2021',symbol]),'source_available_at':None,
             'first_observed_at':observed,'vendor_available_at':start+'T00:00:00+08:00',
             'availability_basis':'terminal_history_observed','pit_qualification':'best_effort',
             'source_ref':latest.ref.raw_batch_id,'membership_spans':spans,'coverage_from':start,'coverage_to':upper,
             'mapping_profile_digest':_digest(_json_bytes(mapping))}
        row['revision_id']=fingerprint(economic_content(row))
        obs={'observed_at':observed,'source_ref':row['source_ref'],'revision_id':row['revision_id'],
             'vendor_available_at':row['vendor_available_at'],'state_raw_refs':raw_refs}
        obs['observation_id']=fingerprint(obs)
        key=(row['logical_event_key'],row['revision_id'])
        if key in content:
            if obs not in content[key]['observations']:content[key]['observations'].append(obs)
        else:content[key]=dict(row,observations=[obs])
    return [content[k] for k in sorted(content)]


def economic_content(row):
    excluded={'revision_id','source_ref','source_available_at','first_observed_at','vendor_available_at',
              'availability_basis','pit_qualification','observations'}
    result=deepcopy({k:v for k,v in row.items() if k not in excluded})
    for span in result['membership_spans']:
        span.pop('source_ref',None)
        proof=span.get('mapping_provenance')
        if proof:
            proof.pop('taxonomy_raw_refs',None);proof.pop('membership_raw_refs',None)
    return result


def validate_rows(rows):
    from axiom_data.domains.market import _rows,_validate_keys,_symbol,_date
    from axiom_data.domains.dm1 import _provenance
    frozen=_rows('industry_membership',rows,'v3')
    for row in frozen:
        _symbol(row['symbol']);_provenance(row)
        if row['classification_system']!='SW2021' or row['group_id']!='SW2021':raise ArtifactError('invalid SW classification system')
        if row['logical_event_key']!=fingerprint(['SW2021',row['symbol']]) or row['revision_id']!=fingerprint(economic_content(row)):
            raise ArtifactError('industry history identity mismatch')
        if _date('coverage_from',row['coverage_from'])>=_date('coverage_to',row['coverage_to']):raise ArtifactError('invalid industry coverage')
        if not row['observations']:raise ArtifactError('industry observation lineage missing')
        for o in row['observations']:
            if o['observation_id']!=fingerprint({k:v for k,v in o.items() if k!='observation_id'}) or o['revision_id']!=row['revision_id']:
                raise ArtifactError('industry observation identity mismatch')
        previous=None
        for span in row['membership_spans']:
            since=_date('effective_from',span['effective_from']);until=_date('effective_to',span['effective_to'],nullable=True)
            if until and until<since:raise ArtifactError('reversed industry interval')
            if previous and (previous['effective_to'] is None or since<date.fromisoformat(previous['effective_to'])):raise ArtifactError('overlapping industry intervals')
            if span['industry_id']!=span['taxonomy']['l3']['code']:raise ArtifactError('industry taxonomy identity mismatch')
            previous=span
    _validate_keys('industry_membership',frozen,'v3')


def project_state(state,security,session):
    if session<state['coverage_from'] or session>=state['coverage_to']:raise ArtifactError('INSUFFICIENT_SCOPE: industry dates')
    result=dict({k:v for k,v in state.items() if k!='membership_spans'},target_session=session,industry_id=None,missing_reason=None)
    if (security['list_session'] and session<security['list_session']) or (security['delist_session'] and session>=security['delist_session']):
        return dict(result,availability_state='outside_security_lifetime',missing_reason='outside_security_lifetime')
    if any(s['effective_to']==session for s in state['membership_spans']):
        return dict(result,availability_state='boundary_session_ambiguous',missing_reason='boundary_session_ambiguous')
    active=[s for s in state['membership_spans'] if s['effective_from']<=session and (s['effective_to'] is None or session<s['effective_to'])]
    if len(active)>1:raise ArtifactError('conflicting industry projection')
    if not active:return dict(result,availability_state='classification_unavailable',missing_reason='source_coverage_gap')
    return dict(result,**active[0],availability_state='classified')
