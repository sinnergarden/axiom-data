"""Bounded supplier observations for the exact remaining PR4 Data leaves."""
import json
import math
from collections import defaultdict
from importlib.resources import files
from axiom_data.artifacts import ArtifactError, MarketDomainBuilder, _digest, _json_bytes, write_raw_batch, load_raw_batch
from axiom_data.tushare import TushareCollector, _response_records, _retrieved_at
from axiom_data.pr6_source import source_date
from axiom_data.domains.pr6 import economic_content
from axiom_data.pit import fingerprint, instant


def load_pr7_source_profile():
    return json.loads(files('axiom_data.source_profiles').joinpath('tushare_pr7.v1.json').read_bytes())


def profile_digest():
    return _digest(_json_bytes(load_pr7_source_profile()))


def validate_payload(endpoint, params, records):
    definition=load_pr7_source_profile()['endpoints'].get(endpoint)
    if definition is None:raise ArtifactError('unsupported PR7 endpoint')
    if not isinstance(params,dict) or set(params)!={'ts_code','start_date','end_date'}:
        raise ArtifactError('PR7 requires a single security and explicit date bounds')
    from axiom_data.domains.market import _symbol
    _symbol(params['ts_code'])
    source_date(params['start_date']);source_date(params['end_date'])
    if params['start_date']>params['end_date']:raise ArtifactError('reversed request bounds')
    if not isinstance(records,list) or len(records)>=definition['limit']:
        raise ArtifactError('payload invalid or possibly truncated; split request')
    for row in records:
        if not isinstance(row,dict) or set(row)-set(definition['fields']):raise ArtifactError('unexpected payload field')
        if row.get('ts_code')!=params['ts_code']:raise ArtifactError('security outside individual request')
        for field in ('trade_date',) if endpoint in {'margin_detail','moneyflow'} else ('ann_date','end_date'):
            source_date(row.get(field))
        represented=row[definition['bound_field']]
        if not params['start_date']<=represented<=params['end_date']:raise ArtifactError('date outside individual request')


class Pr7Collector(TushareCollector):
    implementation_revision='tushare-pr7-collector.v1'

    def collect(self, endpoint, params, *, retrieved_at=None):
        validate_payload(endpoint,params,[])
        definition=load_pr7_source_profile()['endpoints'][endpoint]
        records=_response_records(self._client().query(endpoint,fields=','.join(definition['fields']),**params))
        validate_payload(endpoint,params,records)
        observed=_retrieved_at(retrieved_at)
        request={'endpoint':endpoint,'params':params,'fields':definition['fields']}
        payload=_json_bytes(records)
        identity=fingerprint({'request':request,'payload':_digest(payload),'retrieved_at':observed,'profile':profile_digest()})
        return write_raw_batch(self.data_root,'pr7-'+identity,domain=definition['domain'],
            source_profile='tushare.pr7.'+endpoint,source_profile_version='tushare_pr7.v1',
            source_profile_digest=profile_digest(),request=request,retrieved_at=observed,
            payload=payload,collector_code=self.implementation_revision,
            summary={'rows':len(records),'historical_availability':'best_effort','empty_response':'source_gap' if not records else None})


def _number(value):
    if value is not None and (isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value)):
        raise ArtifactError('source value must be finite numeric or null')
    return value


def normalize(raw, domain):
    m=raw.manifest;request=m['request'];endpoint=request.get('endpoint')
    d=load_pr7_source_profile()['endpoints'].get(endpoint)
    if (d is None or d['domain']!=domain or m['domain']!=domain or m['schema_version']!='raw_batch.v2'
        or m['source_profile_digest']!=profile_digest() or m['source_profile_version']!='tushare_pr7.v1'
        or m['source_profile_ref']!='tushare.pr7.'+endpoint or request['fields']!=d['fields']):
        raise ArtifactError('PR7 profile/domain binding mismatch')
    records=json.loads(raw.payload);validate_payload(endpoint,request['params'],records)
    groups=defaultdict(list)
    for source in records:
        key=(source['ts_code'],source.get('trade_date') or source['end_date'],source.get('ann_date'))
        groups[key].append(source)
    result=[]
    for (symbol,period,ann),sources in sorted(groups.items()):
        session=source_date(period) if endpoint in {'margin_detail','moneyflow'} else None
        row={'symbol':symbol,'endpoint':endpoint,'session':session,
             'report_period':None if session else source_date(period),'announcement':source_date(ann) if ann else None,
             'source_available_at':None,'first_observed_at':m['retrieved_at'],
             'vendor_available_at':source_date(ann or period)+'T23:59:59+08:00',
             'availability_basis':'terminal_history_observed','pit_qualification':'best_effort',
             'source_ref':raw.ref.raw_batch_id,'holders':[],'group_completeness':'not_applicable',
             'values':{},'missing_reasons':{}}
        row['logical_event_key']=fingerprint([domain,symbol,period])
        if endpoint=='top10_holders':
            for s in sources:
                name=s.get('holder_name')
                if not isinstance(name,str) or not name:raise ArtifactError('holder name required')
                shares=_number(s.get('hold_amount'));ratio=_number(s.get('hold_ratio'))
                valid=shares is not None and ratio is not None and shares>=0 and 0<=ratio<=100
                row['holders'].append({'holder_id':fingerprint(['tushare',name]),'name':name,
                    'category':s.get('holder_type'),'shares':shares,'ratio':ratio,'validity':'valid' if valid else 'invalid',
                    'missing_reason':None if valid else 'source_value_missing'})
            row['holders'].sort(key=lambda h:h['holder_id'])
            if len({h['holder_id'] for h in row['holders']})!=len(row['holders']):raise ArtifactError('duplicate holder in report')
            complete=len(row['holders'])==10 and all(h['validity']=='valid' for h in row['holders'])
            row['group_completeness']='complete' if complete else 'incomplete'
            row['values']['top10_ratio']=sum(h['ratio'] for h in row['holders']) if complete else None
            if not complete:row['missing_reasons']['top10_ratio']='incomplete_report'
        else:
            # Multiple incompatible rows at the same publication time cannot be ordered.
            if len({fingerprint(s) for s in sources})!=1:raise ArtifactError('ambiguous same-publication source rows')
            s=sources[0]
            for field,(target,scale,unit) in d['mapping'].items():
                value=s.get(field)
                if value is None:row['missing_reasons'][target]='vendor_null' if field in s else 'not_provided'
                elif scale=='date':value=source_date(value)
                elif scale=='text':
                    if not isinstance(value,str) or not value:raise ArtifactError('invalid forecast type')
                else:value=_number(value)*scale
                row['values'][target]=value
        row['revision_id']=fingerprint(economic_content(row));result.append(row)
    return result


class Pr7Builder(MarketDomainBuilder):
    implementation_revision='tushare-pr7-builder.v1'

    def __init__(self,data_root,domain,*,builder_config=None,**kwargs):
        config=dict(builder_config or {})
        config['implementation_content']={n:_digest(files('axiom_data').joinpath(n).read_bytes()) for n in ('pr7_source.py','domains/pr7.py','pit.py','artifacts.py')}
        super().__init__(data_root,domain,builder_config=config,**kwargs)

    def _build_rows(self,contract,parent_rows,raw_batches):
        refs={o['source_ref'] for r in parent_rows for o in r['observations']}
        raws={r.ref.raw_batch_id:r for r in raw_batches}
        for ref in sorted(refs-raws.keys()):raws[ref]=load_raw_batch(self.layout.root,ref)
        content={}
        for raw in sorted(raws.values(),key=lambda r:(instant(r.manifest['retrieved_at']),r.ref.raw_batch_id)):
            for row in normalize(raw,self.domain):
                key=(row['logical_event_key'],row['revision_id'])
                kept=content.setdefault(key,dict(row,observations=[]))
                o={'observed_at':raw.manifest['retrieved_at'],'source_ref':raw.ref.raw_batch_id,
                   'revision_id':row['revision_id'],'vendor_available_at':row['vendor_available_at']}
                o['observation_id']=fingerprint(o)
                if o not in kept['observations']:kept['observations'].append(o)
        return [content[k] for k in sorted(content)]


def extend_snapshot(data_root, parent_snapshot_id, domain, raw_batch_ids):
    """Extend one explicit PR7 domain; all other immutable domain refs are reused."""
    from axiom_data import BuildApplication,SnapshotReader,create_snapshot
    from axiom_data.artifacts import _DOMAIN_DEPENDENCIES
    from axiom_data.domains.pr7 import PR7_DOMAINS
    if domain not in PR7_DOMAINS:raise ArtifactError('PR7 extension domain required')
    reader=SnapshotReader(data_root,parent_snapshot_id)
    ids={d:c.ref.commit_id for d,c in reader.commits.items()}
    if domain not in ids:raise ArtifactError('parent Snapshot lacks extension domain')
    parent=reader.commits[domain]
    builder=Pr7Builder(data_root,domain,builder_config=parent.manifest['builder_config'],
        dependency_commit_ids={d:ids[d] for d in _DOMAIN_DEPENDENCIES[domain]})
    ids[domain]=BuildApplication(domain,builder).build(ids[domain],raw_batch_ids,[],parent.ref.contract_version).commit_id
    return create_snapshot(data_root,ids)


def select_pr7_revisions(rows, *, policy, knowledge_cutoff):
    """Break same-retrieval ties by explicit publication order within PR7 reports.

    Every candidate is first selected by the shared PIT visibility rule. Thus a
    later publication cannot affect an earlier cutoff. Distinct content with
    identical retrieval and publication times remains an error.
    """
    from axiom_data.pit import select_revisions
    groups=defaultdict(list)
    for row in rows:
        selected=select_revisions([row],policy=policy,knowledge_cutoff=knowledge_cutoff)
        for candidate in selected:
            o=candidate['observation_ref']
            order=(instant(candidate['usable_from']),instant(o['observed_at']),instant(o['vendor_available_at']))
            groups[candidate['logical_event_key']].append((order,candidate))
    result=[]
    for key,candidates in sorted(groups.items()):
        order=max(k for k,_ in candidates);winners=[r for k,r in candidates if k==order]
        if len({r['revision_id'] for r in winners})!=1:raise ArtifactError('ambiguous simultaneous PR7 revisions: '+key)
        result.append(winners[0])
    return tuple(result)
