"""Event row and grouped-report contracts; observations are separate from content."""
import math
from axiom_data.contracts import load_contract
from axiom_data.domains.market import _rows, _validate_keys, _symbol, _date, MarketContractError
from axiom_data.domains.reference import _provenance
from axiom_data.domains.fundamentals import economic_content
from axiom_data.pit import fingerprint, instant

EVENT_DOMAINS=('holder_count_events','top_holders_reports','margin_daily','moneyflow_daily','forecast_observations')
DAILY_DOMAINS=('margin_daily','moneyflow_daily')


def validate_rows(domain,rows,version='v1'):
    frozen=_rows(domain,rows,version)
    contract=load_contract(domain+'.'+version)
    units=contract['value_units']
    for r in frozen:
        _symbol(r['symbol']);_provenance(r);instant(r['vendor_available_at'])
        _date('session',r['session'],nullable=domain not in DAILY_DOMAINS)
        _date('report_period',r['report_period'],nullable=domain in DAILY_DOMAINS)
        _date('announcement',r['announcement'],nullable=domain in DAILY_DOMAINS)
        if r['revision_id']!=fingerprint(economic_content(r)):raise MarketContractError('revision fingerprint mismatch')
        if set(r['values'])!=set(units):raise MarketContractError('event value schema mismatch')
        if set(r['missing_reasons'])!={k for k,v in r['values'].items() if v is None}:raise MarketContractError('missing reasons mismatch')
        for k,v in r['values'].items():
            if v is None:
                allowed={'vendor_null','not_provided','incomplete_report'}
                if domain=='margin_daily' and k in {'repay','lend_repay_volume'}:allowed.add('source_repayment_unresolved')
                if r['missing_reasons'][k] not in allowed:raise MarketContractError('unknown missing reason')
            elif units[k] in {'date','fiscal_date'}:_date(k,v)
            elif units[k]=='enum':
                if v not in contract.get('source_type_values', ['预增','预减','扭亏','首亏','续亏','续盈','略增','略减']):raise MarketContractError('unsupported forecast enum')
            elif isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v):raise MarketContractError('nonfinite canonical numeric value')
            elif domain in {'margin_daily','holder_count_events'} and v<0:raise MarketContractError('negative balance/count/volume')
        if domain=='holder_count_events' and r['values']['number'] is not None and r['values']['number']%1:
            raise MarketContractError('fractional holder count')
        if domain=='top_holders_reports':
            holders=r['holders']
            ids=[h['holder_id'] for h in holders]
            if ids!=sorted(set(ids)):raise MarketContractError('holder identity closure mismatch')
            for h in holders:
                if h['holder_id']!=fingerprint(['tushare',h['name']]):raise MarketContractError('holder identity mismatch')
                valid=all(isinstance(h[k],(int,float)) and not isinstance(h[k],bool) and math.isfinite(h[k]) for k in ('shares','ratio')) and h['shares']>=0 and 0<=h['ratio']<=100
                if h['validity']!=('valid' if valid else 'invalid'):raise MarketContractError('holder validity mismatch')
                if h.get('missing_reason')=='ambiguous_source_rows':
                    variants=h.get('source_variants',[])
                    if (h.get('qualification_profile')!='top10_ambiguity.v1' or valid
                        or any(h[k] is not None for k in ('shares','ratio','category'))
                        or len(variants)<2 or len({fingerprint(s) for s in variants})!=len(variants)
                        or any(s.get('holder_name')!=h['name'] or s.get('ts_code')!=r['symbol']
                            or s.get('end_date')!=r['report_period'].replace('-','')
                            or s.get('ann_date')!=r['announcement'].replace('-','') for s in variants)):
                        raise MarketContractError('ambiguous holder source closure mismatch')
            complete=len(holders)==10 and all(h['validity']=='valid' for h in holders)
            if any(h.get('missing_reason')=='inconsistent_report_total' for h in holders):
                if (len(holders)!=10 or any(h.get('qualification_profile')!='top10_ambiguity.v1'
                    or h.get('missing_reason')!='inconsistent_report_total' or h['ratio'] is not None
                    or not isinstance(h.get('source_ratio'),(int,float)) or isinstance(h['source_ratio'],bool)
                    or not 0<=h['source_ratio']<=100 for h in holders)
                    or sum(h['source_ratio'] for h in holders)<=100.01):
                    raise MarketContractError('inconsistent report total evidence mismatch')
            if r['group_completeness']!=('complete' if complete else 'incomplete'):raise MarketContractError('group completeness mismatch')
            expected=sum(h['ratio'] for h in holders) if complete else None
            if r['values']['top10_ratio']!=expected:raise MarketContractError('incomplete report aggregate or incorrect ratio')
            if expected is not None and expected>100.01:raise MarketContractError('top10 ratios exceed total capital')
        elif r['holders'] or r['group_completeness']!='not_applicable':raise MarketContractError('unexpected holder group')
        if not isinstance(r['observations'],list) or not r['observations']:raise MarketContractError('observation sequence required')
        for o in r['observations']:
            if o['observation_id']!=fingerprint({k:v for k,v in o.items() if k!='observation_id'}):raise MarketContractError('observation identity mismatch')
            if o['revision_id']!=r['revision_id'] or instant(o['observed_at'])<instant(r['first_observed_at']):raise MarketContractError('observation content mismatch')
            unresolved={k for k,v in r['missing_reasons'].items() if v=='source_repayment_unresolved'}
            if unresolved or 'source_qualification' in o:
                q=o.get('source_qualification',{});values=q.get('negative_source_values',{})
                mapping={'rzche':'repay','rqchl':'lend_repay_volume'}
                if (domain!='margin_daily' or q.get('profile')!='margin_negative_repayment.v1'
                    or not values or set(values)-set(mapping) or {mapping[k] for k in values}!=unresolved
                    or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or v>=0 for v in values.values())):
                    raise MarketContractError('negative repayment qualification closure mismatch')
    _validate_keys(domain,frozen,version)


DOMAIN_VALIDATORS={d:(lambda rows,d=d:validate_rows(d,rows)) for d in EVENT_DOMAINS}


LEAF_DOMAINS={**{'holder.'+f:d for f,d in [('number','holder_count_events'),('top10_ratio','top_holders_reports')]},
    **{'margin.'+f:'margin_daily' for f in load_contract('margin_daily.v1')['value_units']},
    **{'moneyflow.'+f:'moneyflow_daily' for f in load_contract('moneyflow_daily.v1')['value_units']},
    **{'forecast.'+f:'forecast_observations' for f in load_contract('forecast_observations.v1')['value_units']}}
NUMERIC_FIELDS=tuple(f for f in LEAF_DOMAINS if not f.startswith('forecast.'))



# Compatibility exports for historical callers.
PR7_DOMAINS = EVENT_DOMAINS
