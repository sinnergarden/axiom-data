"""Read-only source qualification over exact RawBatch refs; no canonical selector."""
from collections import defaultdict
from datetime import date
import json
from axiom_data import ArtifactError, load_raw_batch
from axiom_data.sw_source import load_profile, profile_digest, payload_issues, validate_request


def inspect_sw_pilot(data_root, raw_batch_ids, *, symbols, calendar_raw_batch_ids,
                     start_session, end_session, comparison_raw_batch_ids=(), calendar_data_root=None):
    if not raw_batch_ids or len(set(raw_batch_ids)) != len(raw_batch_ids):
        raise ArtifactError('explicit unique pilot RawBatch refs required')
    if not symbols or len(set(symbols)) != len(symbols):
        raise ArtifactError('explicit unique pilot symbols required')
    scope=set(symbols); taxonomy={}; stock={}; samples=defaultdict(list)
    raw_refs=[]; structural=[]; categories=[]
    for identity in raw_batch_ids:
        raw=load_raw_batch(data_root,identity);m=raw.manifest
        req=m['request'];ep=req['endpoint'];params=req['params']
        if (m['source_profile_version']!='tushare_sw_pilot.v1' or
            m['source_profile_digest']!=profile_digest() or
            req['fields']!=load_profile()['endpoints'][ep]['fields'] or
            m['source_profile_ref']!='tushare.sw-pilot.'+ep):
            raise ArtifactError('SW pilot source binding mismatch')
        validate_request(ep,params)
        rows=json.loads(raw.payload)
        issues=payload_issues(ep,params,rows)
        raw_refs.append({'raw_batch_id':identity,'manifest_digest':raw.ref.manifest_digest,
                         'endpoint':ep,'params':params,'rows':len(rows),'retrieved_at':m['retrieved_at']})
        if issues: structural.append({'raw_batch_id':identity,'issues':issues})
        if ep=='index_classify':
            for r in rows:
                key=r['index_code']
                if key in taxonomy and taxonomy[key]!=r: raise ArtifactError('taxonomy conflict')
                taxonomy[key]=r
        elif ep=='stock_basic':
            for r in rows:
                if r['ts_code'] in scope: stock[r['ts_code']]=r
        elif 'ts_code' in params:
            samples[(params['ts_code'],params.get('is_new','DEFAULT'))].append((identity,rows))
        else: categories.append((identity,params,rows))
    if set(stock)!=scope:raise ArtifactError('pilot lacks stock reference identities')
    levels={level:sum(r['level']==level for r in taxonomy.values()) for level in ('L1','L2','L3')}
    tree_errors=[]; by_industry={r['industry_code']:r for r in taxonomy.values()}
    for r in taxonomy.values():
        if r['level']=='L1':
            if r['parent_code']!='0':tree_errors.append(r['index_code'])
        else:
            p=by_industry.get(r['parent_code'])
            if p is None or p['level']!=('L1' if r['level']=='L2' else 'L2'):tree_errors.append(r['index_code'])
    from axiom_data.tushare import TushareMarketBuilder
    calendar_raw = [load_raw_batch(calendar_data_root or data_root, identity)
                    for identity in calendar_raw_batch_ids]
    calendar_rows = TushareMarketBuilder._calendar_rows(
        calendar_raw, symbols, start_session, end_session)
    sessions=defaultdict(set)
    for row in calendar_rows:
        if row['is_open']:
            sessions[row['exchange']].add(row['session'].replace('-', ''))
    comparisons=defaultdict(list)
    for identity in comparison_raw_batch_ids:
        raw=load_raw_batch(data_root,identity)
        from axiom_data.fundamentals_source import profile_digest as pr6_digest, validate_payload
        if raw.manifest['source_profile_version']!='tushare_pr6.v2' or raw.manifest['source_profile_digest']!=pr6_digest('tushare_pr6.v2'):
            raise ArtifactError('comparison source binding mismatch')
        req=raw.manifest['request'];rows=json.loads(raw.payload)
        validate_payload(req['endpoint'],req['params'],rows,profile_version='tushare_pr6.v2')
        for r in rows:
            if r['ts_code'] in scope:comparisons[r['ts_code']].append(dict(r,raw_batch_id=identity))
    output={}; errors=[]
    for symbol in sorted(scope):
        selected={}; stability={}
        for mode in ('DEFAULT','Y','N'):
            responses=samples.get((symbol,mode),[])
            if not responses:raise ArtifactError('missing pilot observation mode')
            selected[mode]=responses[0][1]
            normalize=lambda values:sorted(json.dumps(r,sort_keys=True) for r in values)
            stability[mode]=all(normalize(rows)==normalize(responses[0][1]) for _,rows in responses)
        union={json.dumps(r,sort_keys=True):r for mode in ('Y','N') for r in selected[mode]}
        rows=sorted(union.values(),key=lambda r:(r['in_date'] or '',r['out_date'] or '99999999',r['l3_code']))
        unknown=[]; mismatch=[]; invalid=[]
        for row in rows:
            try:
                start=date.fromisoformat(row['in_date']);end=date.fromisoformat(row['out_date']) if row['out_date'] else None
                if end and end<start:invalid.append(row)
            except (TypeError,ValueError):invalid.append(row)
            for level in ('l1','l2','l3'):
                code=row[level+'_code']; entry=taxonomy.get(code)
                if entry is None:unknown.append(code)
                elif entry['level']!=level.upper() or entry['industry_name']!=row[level+'_name']:mismatch.append(code)
        exchange=stock[symbol]['exchange']
        if not sessions[exchange]:raise ArtifactError('pilot calendar missing exchange')
        listed=stock[symbol]['list_date'];delisted=stock[symbol]['delist_date']
        # Raw source diagnostics only. Neither vendor bounds nor an endpoint convention
        # is being admitted as the canonical security or membership selector here.
        target=sorted(d for d in sessions[exchange] if d>=max(start_session.replace('-', ''),listed) and (not delisted or d<delisted))
        gaps_inclusive=[];gaps_exclusive=[];overlaps=[]
        for day in target:
            active=[r for r in rows if r['in_date'] and r['in_date']<=day and (not r['out_date'] or day<=r['out_date'])]
            if not active:gaps_inclusive.append(day)
            if len(active)>1:overlaps.append(day)
            if not any(r['in_date'] and r['in_date']<=day and (not r['out_date'] or day<r['out_date']) for r in rows):gaps_exclusive.append(day)
        transitions=[{'out_date':left['out_date'],'next_in_date':right['in_date'],
                      'old_code':left['l3_code'],'new_code':right['l3_code']} for left,right in zip(rows,rows[1:])]
        batch=[]
        for identity,params,values in categories:
            matching=[r for r in values if r['ts_code']==symbol]
            expected=[r for r in selected[params['is_new']] if r['l3_code']==params['l3_code']]
            if matching or expected:
                batch.append({'raw_batch_id':identity,'params':params,'equivalent':normalize(matching)==normalize(expected)})
        sw_current=selected['Y']
        comparison=[]
        for r in comparisons[symbol]:
            comparison.append(dict(r,sw_candidate_rows=[x for x in rows if x['in_date']<=r['trade_date'] and (not x['out_date'] or r['trade_date']<=x['out_date'])]))
        output[symbol]={'stock_basic':stock[symbol],'mode_row_counts':{m:len(v) for m,v in selected.items()},
            'default_equals_Y':normalize(selected['DEFAULT'])==normalize(selected['Y']),
            'repeat_stability':stability,'membership_rows':rows,'transitions':transitions,
            'taxonomy_unknown_codes':sorted(set(unknown)),'taxonomy_name_level_mismatch':sorted(set(mismatch)),
            'invalid_intervals':invalid,'overlap_open_dates':overlaps,
            'gap_open_dates_assuming_inclusive_out':gaps_inclusive,
            'gap_open_dates_assuming_exclusive_out':gaps_exclusive,
            'first_in_date':rows[0]['in_date'] if rows else None,
            'category_crosschecks':batch,'bak_basic_comparison':comparison,
            'gap_interpretation':'source/taxonomy coverage unresolved; absence is not explicit unclassified state'}
        if unknown or mismatch or invalid or overlaps or gaps_inclusive or not all(stability.values()) or any(not b['equivalent'] for b in batch):errors.append(symbol)
    return {'schema_version':'sw_pilot_qualification.v1','classification_system':'SW2021',
        'requested_scope':{'start_session':start_session,'end_session':end_session},
        'canonical_admission':'NOT_ADMITTED','pilot_decision':'REJECTED' if errors or structural or tree_errors else 'BOUNDARY_EVIDENCE_REQUIRED',
        'historical_pit_qualification':'best_effort','symbols':sorted(scope),'raw_refs':raw_refs,
        'calendar_data_root':str(calendar_data_root or data_root),'calendar_raw_batch_ids':list(calendar_raw_batch_ids),'comparison_raw_batch_ids':list(comparison_raw_batch_ids),
        'taxonomy_counts':levels,'taxonomy_tree_errors':tree_errors,'structural_issues':structural,
        'symbols_with_unresolved_findings':errors,'security_results':output,
        'limitations':['Endpoint out_date inclusivity remains unqualified; both candidate conventions reported.',
                      'Stock list/delist dates are source sanity references, not verified canonical boundaries.',
                      'Comparisons do not equate different classification systems.']}
