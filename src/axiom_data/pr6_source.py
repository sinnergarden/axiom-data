"""Bounded PR6 supplier collection and immutable revision normalization."""
from __future__ import annotations
import json
from datetime import date, timedelta
from importlib.resources import files
from collections import defaultdict
from axiom_data.artifacts import (ArtifactError, MarketDomainBuilder, _json_bytes,
    _digest, write_raw_batch)
from axiom_data.tushare import TushareCollector, _response_records, _retrieved_at
from axiom_data.pit import fingerprint, instant
from axiom_data.domains.pr6 import economic_content


def load_pr6_source_profile():
    return json.loads(files('axiom_data.source_profiles').joinpath('tushare_pr6.v1.json').read_bytes())


def profile_digest():
    return _digest(_json_bytes(load_pr6_source_profile()))


def source_date(value):
    if not isinstance(value,str) or len(value)!=8:
        raise ArtifactError('supplier date requires YYYYMMDD')
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise ArtifactError('invalid supplier date') from exc


def validate_payload(endpoint, params, records):
    definition=load_pr6_source_profile()['endpoints'][endpoint]
    allowed = {'ts_code','start_date','end_date','period','report_type','trade_date','index_code'}
    if not isinstance(params,dict) or not params or set(params)-allowed:
        raise ArtifactError('PR6 request must have bounded allowed parameters')
    if endpoint=='index_weight':
        if set(params)!={'index_code','start_date','end_date'}:
            raise ArtifactError('index_weight requires explicit index and date bounds')
    elif endpoint=='bak_basic':
        if set(params)!={'ts_code','trade_date'}:
            raise ArtifactError('bak_basic requires one security and date')
    elif not params.get('ts_code') or not (params.get('period') or params.get('trade_date') or
                                          (params.get('start_date') and params.get('end_date'))):
        raise ArtifactError('financial/valuation request requires security and date/period bounds')
    for key in ('start_date','end_date','period','trade_date'):
        if key in params: source_date(params[key])
    if params.get('start_date') and params['start_date'] > params['end_date']:
        raise ArtifactError('reversed supplier request interval')
    if not isinstance(records,list):
        raise ArtifactError('source payload must be rows')
    for row in records:
        if not isinstance(row,dict) or set(row)-set(definition['fields']):
            raise ArtifactError('unexpected source payload fields')
        code=row.get('con_code') if endpoint=='index_weight' else row.get('ts_code')
        if not code:
            raise ArtifactError('missing source security')
        if params.get('ts_code') and code!=params['ts_code']:
            raise ArtifactError('source security outside individual RawBatch request')
        if endpoint=='index_weight' and row.get('index_code')!=params['index_code']:
            raise ArtifactError('source index outside individual RawBatch request')
        represented = row.get('trade_date') if endpoint in {'index_weight','bak_basic','daily_basic'} else row.get('ann_date')
        source_date(represented)
        if params.get('trade_date') and represented!=params['trade_date']:
            raise ArtifactError('source date outside RawBatch request')
        if params.get('period') and row.get('end_date')!=params['period']:
            raise ArtifactError('source report period outside RawBatch request')
        if params.get('report_type') and str(row.get('report_type'))!=params['report_type']:
            raise ArtifactError('source report type outside RawBatch request')
        if params.get('start_date') and not params['start_date']<=represented<=params['end_date']:
            raise ArtifactError('source date outside RawBatch date bounds')


class Pr6Collector(TushareCollector):
    implementation_revision='tushare-pr6-collector.v1'

    def collect(self, endpoint, params, *, retrieved_at=None):
        definition=load_pr6_source_profile()['endpoints'].get(endpoint)
        if definition is None:
            raise ArtifactError('unsupported PR6 endpoint')
        validate_payload(endpoint,params,[])
        records=_response_records(self._client().query(endpoint,fields=','.join(definition['fields']),**params))
        validate_payload(endpoint,params,records)
        observed=_retrieved_at(retrieved_at)
        request={'endpoint':endpoint,'params':params,'fields':definition['fields']}
        payload=_json_bytes(records)
        identity=fingerprint({'request':request,'payload':_digest(payload),'retrieved_at':observed,'profile':profile_digest()})
        return write_raw_batch(self.data_root,'pr6-'+identity,domain=definition['domain'],
            source_profile='tushare.pr6.'+endpoint,source_profile_version='tushare_pr6.v1',
            source_profile_digest=profile_digest(),request=request,retrieved_at=observed,
            payload=payload,collector_code=self.implementation_revision,
            summary={'rows':len(records),'historical_availability':'best_effort'})


class Pr6Builder(MarketDomainBuilder):
    implementation_revision='tushare-pr6-builder.v1'

    def __init__(self, data_root, domain, *, builder_config=None, **kwargs):
        config=dict(builder_config or {})
        config['implementation_content']={name:_digest(files('axiom_data').joinpath(name).read_bytes())
            for name in ('pr6_source.py','pit.py','domains/pr6.py','artifacts.py')}
        super().__init__(data_root,domain,builder_config=config,**kwargs)

    def _build_rows(self, contract, parent_rows, raw_batches):
        normalized=[]
        weights=defaultdict(list)
        for raw in raw_batches:
            m=raw.manifest; request=m['request']; endpoint=request.get('endpoint')
            definition=load_pr6_source_profile()['endpoints'].get(endpoint)
            if definition is None or definition['domain']!=self.domain:
                raise ArtifactError('PR6 endpoint/domain mismatch')
            if (m['schema_version']!='raw_batch.v2' or m['source_profile_digest']!=profile_digest()
                or m['source_profile_version']!='tushare_pr6.v1'
                or m['source_profile_ref']!='tushare.pr6.'+endpoint
                or request.get('fields')!=definition['fields']):
                raise ArtifactError('PR6 source profile binding mismatch')
            records=json.loads(raw.payload)
            validate_payload(endpoint,request['params'],records)
            if endpoint=='index_weight':
                for source in records:
                    weights[source['index_code']].append((source,raw))
                continue
            for source in records:
                row=self._base(source['ts_code'],raw)
                if endpoint=='bak_basic':
                    represented=source_date(source['trade_date'])
                    industry=source.get('industry')
                    if not isinstance(industry,str) or not industry:
                        raise ArtifactError('industry classification missing')
                    row.update(group_id='tushare_bak_basic',industry_id=industry,boundary_source_ref=None,
                        effective_from=represented,effective_to=(date.fromisoformat(represented)+timedelta(days=1)).isoformat())
                    identity=[row['group_id'],row['symbol'],represented]
                    vendor=represented
                else:
                    values={}; reasons={}
                    for field,(target,scale) in definition['mapping'].items():
                        value=source.get(field)
                        if value is None:
                            reasons[target]='vendor_null' if field in source else 'not_provided'
                        elif isinstance(value,bool) or not isinstance(value,(int,float)):
                            raise ArtifactError('supplier numeric field is not numeric')
                        values[target]=None if value is None else value*scale
                    row.update(values=values,missing_reasons=reasons)
                    if endpoint=='daily_basic':
                        row['session']=source_date(source['trade_date'])
                        identity=[endpoint,row['symbol'],row['session']]; vendor=row['session']
                    else:
                        report_type=str(source['report_type']) if endpoint!='fina_indicator' else 'supplier_indicator'
                        row.update(endpoint=endpoint,report_period=source_date(source['end_date']),report_type=report_type)
                        identity=[endpoint,row['symbol'],row['report_period'],report_type]
                        vendor=source_date(source.get('f_ann_date') or source['ann_date'])
                row['logical_event_key']=fingerprint(identity)
                row['vendor_available_at']=vendor+'T23:59:59+08:00'
                row['revision_id']=fingerprint(economic_content(row))
                normalized.append(row)
        for index, observations in sorted(weights.items()):
            snapshots=defaultdict(list)
            for source,raw in observations:
                snapshots[source_date(source['trade_date'])].append((source,raw))
            dates=sorted(snapshots)
            end=self.builder_config.get('membership_end_exclusive')
            if not end or (dates and end<=dates[-1]):
                raise ArtifactError('index membership requires explicit coverage end after final snapshot')
            for i,start in enumerate(dates):
                stop=dates[i+1] if i+1<len(dates) else end
                for source,raw in snapshots[start]:
                    row=self._base(source['con_code'],raw)
                    boundary=max((r for _,r in snapshots[dates[i+1]]),key=lambda r:instant(r.manifest['retrieved_at'])) if i+1<len(dates) else None
                    row['boundary_source_ref']=boundary.ref.raw_batch_id if boundary else None
                    if boundary is not None:
                        row['first_observed_at']=max(row['first_observed_at'],boundary.manifest['retrieved_at'],key=instant)
                    row.update(group_id=index,effective_from=start,effective_to=stop,
                               logical_event_key=fingerprint([index,source['con_code'],start]),
                               vendor_available_at=start+'T23:59:59+08:00')
                    row['revision_id']=fingerprint(economic_content(row));normalized.append(row)
        selected = self.builder_config.get('symbols')
        if selected is not None:
            if not isinstance(selected,list) or not selected or len(selected)!=len(set(selected)):
                raise ArtifactError('PR6 scope symbols must be a nonempty unique list')
            normalized=[r for r in normalized if r['symbol'] in selected]
        by_key={}
        for row in [*parent_rows,*normalized]:
            key=(row['logical_event_key'],row['revision_id'])
            old=by_key.get(key)
            if old is None or instant(row['first_observed_at'])<instant(old['first_observed_at']):
                by_key[key]=dict(row)
        return [by_key[k] for k in sorted(by_key)]

    @staticmethod
    def _base(symbol,raw):
        return {'symbol':symbol,'source_available_at':None,
                'first_observed_at':raw.manifest['retrieved_at'],
                'availability_basis':'terminal_history_observed','pit_qualification':'best_effort',
                'source_ref':raw.ref.raw_batch_id}
