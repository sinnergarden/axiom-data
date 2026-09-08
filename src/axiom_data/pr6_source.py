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


def load_pr6_source_profile(version='tushare_pr6.v1'):
    if version not in {'tushare_pr6.v1','tushare_pr6.v2'}:
        raise ArtifactError('unsupported PR6 SourceProfile version')
    return json.loads(files('axiom_data.source_profiles').joinpath(version+'.json').read_bytes())


def profile_digest(version='tushare_pr6.v1'):
    return _digest(_json_bytes(load_pr6_source_profile(version)))


def source_date(value):
    if not isinstance(value,str) or len(value)!=8:
        raise ArtifactError('supplier date requires YYYYMMDD')
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise ArtifactError('invalid supplier date') from exc


def validate_payload(endpoint, params, records, *, profile_version='tushare_pr6.v1'):
    profile=load_pr6_source_profile(profile_version)
    if endpoint not in profile['endpoints']:
        raise ArtifactError('endpoint outside SourceProfile')
    definition=profile['endpoints'][endpoint]
    if profile_version=='tushare_pr6.v2' and (not isinstance(records,list) or len(records)>=7000):
        raise ArtifactError('possibly truncated bulk industry payload')
    allowed = {'ts_code','start_date','end_date','period','report_type','trade_date','index_code'}
    if not isinstance(params,dict) or not params or set(params)-allowed:
        raise ArtifactError('PR6 request must have bounded allowed parameters')
    if endpoint=='index_weight':
        if set(params)!={'index_code','start_date','end_date'}:
            raise ArtifactError('index_weight requires explicit index and date bounds')
    elif endpoint=='bak_basic':
        if set(params) not in ([{'trade_date'},{'ts_code','trade_date'}] if profile_version=='tushare_pr6.v2' else [{'ts_code','trade_date'}]):
            raise ArtifactError('bak_basic requires explicit security/date scope')
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

    def collect(self, endpoint, params, *, retrieved_at=None, membership_complete=False, profile_version='tushare_pr6.v1'):
        definition=load_pr6_source_profile(profile_version)['endpoints'].get(endpoint)
        if definition is None:
            raise ArtifactError('unsupported PR6 endpoint')
        validate_payload(endpoint,params,[],profile_version=profile_version)
        records=_response_records(self._client().query(endpoint,fields=','.join(definition['fields']),**params))
        validate_payload(endpoint,params,records,profile_version=profile_version)
        observed=_retrieved_at(retrieved_at)
        request={'endpoint':endpoint,'params':params,'fields':definition['fields']}
        payload=_json_bytes(records)
        identity=fingerprint({'request':request,'payload':_digest(payload),'retrieved_at':observed,'profile':profile_digest(profile_version),
                              'membership_complete':membership_complete})
        return write_raw_batch(self.data_root,'pr6-'+identity,domain=definition['domain'],
            source_profile='tushare.pr6.'+endpoint,source_profile_version=profile_version,
            source_profile_digest=profile_digest(profile_version),request=request,retrieved_at=observed,
            payload=payload,collector_code=self.implementation_revision,
            summary={'rows':len(records),'historical_availability':'best_effort',
                     'membership_complete':membership_complete})


class Pr6Builder(MarketDomainBuilder):
    implementation_revision='tushare-pr6-builder.v1'

    def __init__(self, data_root, domain, *, builder_config=None, **kwargs):
        config=dict(builder_config or {})
        config['implementation_content']={name:_digest(files('axiom_data').joinpath(name).read_bytes())
            for name in ('pr6_source.py','pit.py','domains/pr6.py','artifacts.py')}
        super().__init__(data_root,domain,builder_config=config,**kwargs)

    def _legacy_rows(self, contract, parent_rows, raw_batches):
        normalized=[]
        weights=defaultdict(list)
        for raw in raw_batches:
            m=raw.manifest; request=m['request']; endpoint=request.get('endpoint')
            version=m.get('source_profile_version')
            definition=load_pr6_source_profile(version)['endpoints'].get(endpoint)
            if definition is None or definition['domain']!=self.domain:
                raise ArtifactError('PR6 endpoint/domain mismatch')
            if (m['schema_version']!='raw_batch.v2' or m['source_profile_digest']!=profile_digest(version)
                 or m['source_profile_ref']!='tushare.pr6.'+endpoint
                or request.get('fields')!=definition['fields']):
                raise ArtifactError('PR6 source profile binding mismatch')
            records=json.loads(raw.payload)
            validate_payload(endpoint,request['params'],records,profile_version=version)
            if endpoint=='index_weight':
                for source in records:
                    weights[source['index_code']].append((source,raw))
                continue
            for source in records:
                if version=='tushare_pr6.v2' and self.builder_config.get('symbols') and source['ts_code'] not in self.builder_config['symbols']:
                    continue
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

    def _build_rows(self, contract, parent_rows, raw_batches):
        group_mode=contract['contract_version']=='universe_membership.v3'
        self.group_states=[]
        if not group_mode and not contract['contract_version'].endswith('.v2'):
            return self._legacy_rows(contract, parent_rows, raw_batches)
        # Parent is immutable; replay only its explicit raw lineage plus new inputs.
        from axiom_data.artifacts import load_raw_batch
        refs = {o['source_ref'] for r in parent_rows for o in r['observations']}
        refs.update(o['boundary_source_ref'] for r in parent_rows for o in r['observations']
                    if o.get('boundary_source_ref'))
        refs.update(ref for r in parent_rows for o in r['observations']
                    for ref in o.get('state_raw_refs', []))
        if group_mode:
            refs.update(ref for state in getattr(self,'parent_group_states',[]) for ref in state['raw_refs'])
        raws = {r.ref.raw_batch_id:r for r in raw_batches}
        for ref in sorted(refs - raws.keys()):
            raws[ref] = load_raw_batch(self.layout.root, ref)
        ordered = sorted(raws.values(), key=lambda r:(instant(r.manifest['retrieved_at']),r.ref.raw_batch_id))
        content = {}
        def retain(row, observation):
            key = (row['logical_event_key'], row['revision_id'])
            previous = content.get(key)
            if previous is None:
                previous = dict(row, observations=[]); content[key] = previous
            elif instant(row['first_observed_at']) < instant(previous['first_observed_at']):
                history = previous['observations'];previous.update(row);previous['observations']=history
            if group_mode:
                observation['group_state_ref']=observation['state_id']
            observation['observation_id'] = fingerprint(observation)
            if observation not in previous['observations']:
                previous['observations'].append(observation)
        if self.domain != 'universe_membership':
            for raw in ordered:
                for row in self._legacy_rows(contract, (), [raw]):
                    retain(row, {'observed_at':raw.manifest['retrieved_at'],
                        'source_ref':raw.ref.raw_batch_id,'revision_id':row['revision_id'],
                        'vendor_available_at':row['vendor_available_at']})
        else:
            # One complete interval state per actual observation timestamp. For a
            # repeated effective snapshot, later retrieval replaces that snapshot.
            timeline = defaultdict(list)
            for raw in ordered: timeline[raw.manifest['retrieved_at']].append(raw)
            snapshots = {}; history = []
            for observed in sorted(timeline, key=instant):
                history.extend(timeline[observed])
                simultaneous={}
                for raw in timeline[observed]:
                    # Validate the original profile/payload before deriving any state.
                    profile=raw.manifest
                    if (profile['source_profile_digest']!=profile_digest() or profile['source_profile_ref']!='tushare.pr6.index_weight'
                        or profile['source_profile_version']!='tushare_pr6.v1' or profile['domain']!=self.domain
                        or profile['request']['fields']!=load_pr6_source_profile()['endpoints']['index_weight']['fields']):
                        raise ArtifactError('PR6 source profile binding mismatch')
                    validate_payload('index_weight',profile['request']['params'],json.loads(raw.payload))
                    records=json.loads(raw.payload);params=raw.manifest['request']['params']
                    grouped=defaultdict(list)
                    for row in records:grouped[(row['index_code'],row['trade_date'])].append(row)
                    if not records:
                        if group_mode and profile['summary'].get('membership_complete') is not True:
                            raise ArtifactError('SOURCE_GAP: empty response lacks complete membership assertion')
                        if params['start_date']!=params['end_date']:
                            raise ArtifactError('empty ranged snapshot has no effective identity')
                        grouped[(params['index_code'],params['start_date'])]=[]
                    for key, values in grouped.items():
                        if key in simultaneous and sorted(simultaneous[key],key=fingerprint)!=sorted(values,key=fingerprint):
                            raise ArtifactError('ambiguous simultaneous membership snapshots')
                        simultaneous[key]=values
                    # A bounded response supersedes earlier effective snapshots in
                    # that exact request interval, including a corrected boundary.
                    for key in list(snapshots):
                        if key[0]==params['index_code'] and params['start_date']<=key[1]<=params['end_date']:
                            del snapshots[key]
                    for key,values in grouped.items():snapshots[key]=(raw,values)
                state_rows=[]
                for group in sorted({key[0] for key in snapshots}):
                    dates=sorted(key[1] for key in snapshots if key[0]==group)
                    end=self.builder_config.get('membership_end_exclusive')
                    if 'membership_end_exclusive' not in self.builder_config or (end is not None and end<=source_date(dates[-1])):
                        raise ArtifactError('membership requires explicit coverage end or open interval')
                    for i,day in enumerate(dates):
                        raw,records=snapshots[(group,day)]
                        boundary=snapshots[(group,dates[i+1])][0] if i+1<len(dates) else None
                        stop=source_date(dates[i+1]) if boundary else end
                        for source in records:
                            if self.builder_config.get('symbols') and source['con_code'] not in self.builder_config['symbols']:continue
                            row=self._base(source['con_code'],raw)
                            row.update(group_id=group,effective_from=source_date(day),effective_to=stop,
                                boundary_source_ref=boundary.ref.raw_batch_id if boundary else None,
                                logical_event_key=fingerprint([group,source['con_code'],source_date(day)]),
                                vendor_available_at=source_date(day)+'T23:59:59+08:00')
                            if boundary:row['first_observed_at']=max(row['first_observed_at'],boundary.manifest['retrieved_at'],key=instant)
                            row['revision_id']=fingerprint(economic_content(row));state_rows.append(row)
                if not state_rows and not group_mode:
                    raise ArtifactError('INSUFFICIENT_SCOPE: empty interval state has no canonical history')
                state_id=fingerprint({'raw_refs':sorted(r.ref.raw_batch_id for r in history),
                                      'observed_at':observed})
                states={}
                if group_mode:
                    for group in sorted({k[0] for k in snapshots}):
                        group_raws=[raw for raw in history if raw.manifest['request']['params']['index_code']==group]
                        last=max(group_raws,key=lambda raw:(instant(raw.manifest['retrieved_at']),raw.ref.raw_batch_id))
                        dates=sorted(k[1] for k in snapshots if k[0]==group)
                        intervals=[]
                        for i,day in enumerate(dates):
                            raw,records=snapshots[(group,day)]
                            symbols=sorted({x['con_code'] for x in records if not self.builder_config.get('symbols') or x['con_code'] in self.builder_config['symbols']})
                            intervals.append({'effective_from':source_date(day),
                                'effective_to':source_date(dates[i+1]) if i+1<len(dates) else self.builder_config['membership_end_exclusive'],
                                'member_count':len(symbols),'member_set_digest':fingerprint(symbols),
                                'members':symbols,'source_ref':raw.ref.raw_batch_id})
                        state={'universe_id':group,'first_observed_at':last.manifest['retrieved_at'],
                            'source_available_at':None,'pit_qualification':'best_effort',
                            'vendor_available_at':source_date(dates[0])+'T23:59:59+08:00',
                            'source_ref':last.ref.raw_batch_id,'raw_refs':sorted(raw.ref.raw_batch_id for raw in group_raws),
                            'coverage_from':source_date(dates[0]),
                            'coverage_to':self.builder_config['membership_end_exclusive'] or (date.fromisoformat(source_date(dates[-1]))+timedelta(days=1)).isoformat(),
                            'member_count':intervals[-1]['member_count'],
                            'member_set_digest':intervals[-1]['member_set_digest'],
                            'intervals':intervals,
                            'member_revision_refs':sorted(r['revision_id'] for r in state_rows if r['group_id']==group)}
                        state['state_id']=fingerprint(state);states[group]=state
                        if state not in self.group_states:self.group_states.append(state)
                for row in state_rows:
                    group_state=states.get(row['group_id'])
                    retain(row, {'observed_at':group_state['first_observed_at'] if group_state else observed,'source_ref':row['source_ref'],
                        'boundary_source_ref':row['boundary_source_ref'],
                        'revision_id':row['revision_id'],'vendor_available_at':row['vendor_available_at'],
                        'state_id':group_state['state_id'] if group_state else state_id,
                        'coverage_from':min(source_date(k[1]) for k in snapshots if k[0]==row['group_id']),
                        'coverage_to':self.builder_config['membership_end_exclusive'] or (date.fromisoformat(max(source_date(k[1]) for k in snapshots if k[0]==row['group_id']))+timedelta(days=1)).isoformat(),
                        'state_raw_refs':group_state['raw_refs'] if group_state else sorted(r.ref.raw_batch_id for r in history)})
        for row in content.values():
            row['observations'].sort(key=lambda o:(instant(o['observed_at']),o['observation_id']))
        return [content[k] for k in sorted(content)]

    @staticmethod
    def _base(symbol,raw):
        return {'symbol':symbol,'source_available_at':None,
                'first_observed_at':raw.manifest['retrieved_at'],
                'availability_basis':'terminal_history_observed','pit_qualification':'best_effort',
                'source_ref':raw.ref.raw_batch_id}
