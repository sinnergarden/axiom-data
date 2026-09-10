"""PIT reference and revision-preserving financial canonical contracts."""
from collections.abc import Mapping
import math
from axiom_data.domains.market import _rows, _validate_keys, _date, _symbol, MarketContractError
from axiom_data.domains.dm1 import _provenance, _text
from axiom_data.pit import fingerprint, instant

PR6_DOMAINS = ('universe_membership', 'industry_membership', 'financial_events', 'valuation_daily')
FINANCIAL_FIELDS = {
    'income': ('revenue', 'oper_cost', 'net_income'),
    'balancesheet': ('accounts_receivable', 'current_assets', 'current_liabilities', 'equity', 'inventory', 'total_assets'),
    'cashflow': ('operating',),
    'fina_indicator': ('current_ratio', 'debt_ratio', 'gross_margin', 'roe'),
}


def economic_content(row):
    excluded = {'revision_id', 'source_ref', 'source_available_at', 'first_observed_at',
                'vendor_available_at', 'availability_basis', 'pit_qualification', 'boundary_source_ref', 'observations'}
    return {k:v for k,v in row.items() if k not in excluded}


def validate_rows(domain, rows):
    if domain=='industry_membership' and rows and 'membership_spans' in rows[0]:
        from axiom_data.sw_industry import validate_rows as validate_sw
        return validate_sw(rows)
    version = "v2" if rows and "observations" in rows[0] else "v1"
    frozen = _rows(domain, rows, version)
    for row in frozen:
        if version == 'v2':
            observations=row['observations']
            if not isinstance(observations,list) or not observations:
                raise MarketContractError('observation lineage required')
            for o in observations:
                if o['observation_id'] != fingerprint({k:v for k,v in o.items() if k!='observation_id'}):
                    raise MarketContractError('observation identity mismatch')
                if o['revision_id'] != row['revision_id'] or instant(o['observed_at']) < instant(row['first_observed_at']):
                    raise MarketContractError('invalid content observation binding')
        _symbol(row['symbol'])
        _provenance(row)
        for field in ('logical_event_key', 'revision_id'):
            _text(field,row[field])
        if row['vendor_available_at'] is not None:
            instant(row['vendor_available_at'])
        if row['revision_id'] != fingerprint(economic_content(row)):
            raise MarketContractError('revision content fingerprint mismatch')
        if domain in {'financial_events','valuation_daily'}:
            if domain == 'financial_events':
                endpoint = row['endpoint']
                if endpoint not in FINANCIAL_FIELDS:
                    raise MarketContractError('unsupported financial endpoint')
                _date('report_period',row['report_period'])
                _text('report_type',row['report_type'])
                expected = set(FINANCIAL_FIELDS[endpoint])
            else:
                _date('session',row['session'])
                expected = {'pe','pb','ps'}
            values, reasons = row['values'], row['missing_reasons']
            if not isinstance(values,Mapping) or set(values)!=expected or not isinstance(reasons,Mapping):
                raise MarketContractError('financial field schema mismatch')
            if set(reasons)!={k for k,v in values.items() if v is None}:
                raise MarketContractError('financial missing reasons must match null fields')
            for field,value in values.items():
                if value is not None and (isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value)):
                    raise MarketContractError('financial value must be finite or null')
                if value is None and reasons[field] not in {'vendor_null','not_provided'}:
                    raise MarketContractError('unknown missing reason')
        else:
            _text('group_id',row['group_id'])
            _text('boundary_source_ref',row['boundary_source_ref'],nullable=True)
            start=_date('effective_from',row['effective_from'])
            end=_date('effective_to',row['effective_to'],nullable=True)
            if end is not None and end<=start:
                raise MarketContractError('empty or reversed membership interval')
            if domain=='industry_membership':
                _text('industry_id',row['industry_id'])
    _validate_keys(domain,frozen,version)
    if 'membership' in domain and not any('group_state_ref' in o for r in frozen for o in r.get('observations',[])):
        from axiom_data.pit import select_revisions
        for cutoff in sorted({o['observed_at'] for r in frozen for o in r.get('observations',[{'observed_at':r['first_observed_at']}])}):
            selected=select_revisions(frozen,policy='operational_pit_v1',knowledge_cutoff=cutoff)
            intervals={}
            for row in sorted(selected,key=lambda r:(r['group_id'],r['symbol'],r['effective_from'])):
                key=(row['group_id'],row['symbol'])
                if key in intervals and (intervals[key] is None or row['effective_from']<intervals[key]):
                    raise MarketContractError('overlapping membership spans at knowledge cutoff')
                intervals[key]=row['effective_to']



DOMAIN_VALIDATORS = {domain: (lambda rows, domain=domain: validate_rows(domain,rows)) for domain in PR6_DOMAINS}


def validate_group_states(rows, states):
    """Validate complete group states, including zero-member intervals."""
    from axiom_data.pit import select_revisions
    for state in states:
        if state['state_id']!=fingerprint({k:v for k,v in state.items() if k!='state_id'}):
            raise MarketContractError('universe group state identity mismatch')
        selected=select_revisions(rows,group_states=[state],policy='operational_pit_v1',knowledge_cutoff=state['first_observed_at'])
        for interval in state['intervals']:
            actual=sorted(r['symbol'] for r in selected if r['effective_from']<=interval['effective_from']
                and (r['effective_to'] is None or interval['effective_from']<r['effective_to']))
            if (actual!=interval['members'] or len(actual)!=interval['member_count']
                or fingerprint(actual)!=interval['member_set_digest']):
                raise MarketContractError('group state member set closure mismatch')
