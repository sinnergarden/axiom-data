"""PR7 row and grouped-report contracts; observations are separate from content."""
import math
from axiom_data.contracts import load_contract
from axiom_data.domains.market import _rows, _validate_keys, _symbol, _date, MarketContractError
from axiom_data.domains.dm1 import _provenance
from axiom_data.domains.pr6 import economic_content
from axiom_data.pit import fingerprint, instant

PR7_DOMAINS=('holder_count_events','top_holders_reports','margin_daily','moneyflow_daily','forecast_observations')
DAILY_DOMAINS=('margin_daily','moneyflow_daily')


def validate_rows(domain,rows):
    frozen=_rows(domain,rows)
    units=load_contract(domain+'.v1')['value_units']
    for r in frozen:
        _symbol(r['symbol']);_provenance(r);instant(r['vendor_available_at'])
        _date('session',r['session'],nullable=domain not in DAILY_DOMAINS)
        _date('report_period',r['report_period'],nullable=domain in DAILY_DOMAINS)
        _date('announcement',r['announcement'],nullable=domain in DAILY_DOMAINS)
        if r['revision_id']!=fingerprint(economic_content(r)):raise MarketContractError('revision fingerprint mismatch')
        if set(r['values'])!=set(units):raise MarketContractError('PR7 value schema mismatch')
        if set(r['missing_reasons'])!={k for k,v in r['values'].items() if v is None}:raise MarketContractError('missing reasons mismatch')
        for k,v in r['values'].items():
            if v is None:
                if r['missing_reasons'][k] not in {'vendor_null','not_provided','incomplete_report'}:raise MarketContractError('unknown missing reason')
            elif units[k] in {'date','fiscal_date'}:_date(k,v)
            elif units[k]=='enum':
                if v not in {'预增','预减','扭亏','首亏','续亏','续盈','略增','略减'}:raise MarketContractError('unsupported forecast enum')
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
            complete=len(holders)==10 and all(h['validity']=='valid' for h in holders)
            if r['group_completeness']!=('complete' if complete else 'incomplete'):raise MarketContractError('group completeness mismatch')
            expected=sum(h['ratio'] for h in holders) if complete else None
            if r['values']['top10_ratio']!=expected:raise MarketContractError('incomplete report aggregate or incorrect ratio')
            if expected is not None and expected>100.01:raise MarketContractError('top10 ratios exceed total capital')
        elif r['holders'] or r['group_completeness']!='not_applicable':raise MarketContractError('unexpected holder group')
        if not isinstance(r['observations'],list) or not r['observations']:raise MarketContractError('observation sequence required')
        for o in r['observations']:
            if o['observation_id']!=fingerprint({k:v for k,v in o.items() if k!='observation_id'}):raise MarketContractError('observation identity mismatch')
            if o['revision_id']!=r['revision_id'] or instant(o['observed_at'])<instant(r['first_observed_at']):raise MarketContractError('observation content mismatch')
    _validate_keys(domain,frozen)


DOMAIN_VALIDATORS={d:(lambda rows,d=d:validate_rows(d,rows)) for d in PR7_DOMAINS}
