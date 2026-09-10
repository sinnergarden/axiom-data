"""Current audit of canonical session coverage, separate from historical PIT facts."""
from collections import Counter

from axiom_data.artifacts import ArtifactError, _digest, _json_bytes
from axiom_data.domains.market import _checked_security_identity_state
from axiom_data.partition_rows import SESSION_PARTITION_DOMAINS
from axiom_data.pr7_views import exchange_sessions


def session_coverage(reader, domain, *, symbols, start_session, end_session, fields):
    """Count complete canonical observations, never infer continuity from extents.

    Revision domains report the union of their stored revisions. This is current
    inspection context, not a historical PIT projection or an admission decision.
    Missing and null sessions remain distinct; every exchange is checked first.
    """
    if domain not in SESSION_PARTITION_DOMAINS or domain=='benchmark_daily':
        raise ArtifactError('security session domain required')
    if domain not in reader.commits:
        raise ArtifactError('Snapshot lacks requested coverage domain')
    commit=reader.commits[domain]
    allowed=set(commit.contract.get('value_units',{})) or {f['name'] for f in commit.contract['fields']}
    if not fields or len(fields)!=len(set(fields)) or not set(fields)<=allowed:
        raise ArtifactError('coverage fields must belong to the declared contract')
    calendars=exchange_sessions(reader,symbols,start_session,end_session)
    identities={r['symbol']:r for r in reader.security_master()}
    exchange_days={}
    for item in calendars.values():
        if item['exchange'] not in exchange_days:
            exchange_days[item['exchange']]=set(item['sessions'])
    # Small integer bitsets retain interior gaps without millions of Python keys.
    days=sorted({day for item in calendars.values() for day in item['sessions']})
    bits={day:1<<i for i,day in enumerate(days)}
    expected={}
    for symbol,item in calendars.items():
        mask=0
        for day in item['sessions']:
            state=_checked_security_identity_state(identities[symbol],day)
            if state=='unknown':
                raise ArtifactError('INSUFFICIENT_SCOPE: unknown security identity interval')
            if state=='within_identity_interval':mask|=bits[day]
        expected[symbol]=mask
    present={s:0 for s in calendars}
    valid={s:{f:0 for f in fields} for s in calendars}
    counts=Counter();qualifications=Counter();nulls=Counter()
    for row in reader._session_rows(domain,start_session,end_session):
        symbol=row['symbol'];day=row['session']
        if symbol not in calendars or not start_session<=day<=end_session:continue
        bit=bits.get(day,0)
        if not bit or day not in exchange_days[calendars[symbol]['exchange']]:
            raise ArtifactError('canonical row conflicts with exchange sessions')
        counts[symbol]+=1;present[symbol]|=bit
        qualifications[row.get('pit_qualification','unknown')]+=1
        values=row.get('values',row)
        for field in fields:
            if field not in values:raise ArtifactError('coverage field missing from canonical row')
            if values[field] is None:nulls[field]+=1
            else:valid[symbol][field]|=bit
    per_symbol={}
    for symbol,mask in expected.items():
        covered=present[symbol]&mask
        per_symbol[symbol]={
            'exchange':calendars[symbol]['exchange'],'canonical_rows':counts[symbol],
            'expected_sessions':mask.bit_count(),'observed_sessions':covered.bit_count(),
            'missing_sessions':(mask&~present[symbol]).bit_count(),
            'missing_session_mask_hex':format(mask&~present[symbol],'x'),
            'rows_outside_identity_session_count':(present[symbol]&~mask).bit_count(),
            'fields':{f:{'non_null_sessions':(mask&valid[symbol][f]).bit_count(),
                'null_only_sessions':(covered&~valid[symbol][f]).bit_count()} for f in fields}}
    return {'schema_version':'canonical_session_coverage.v1',
        'snapshot_id':reader.snapshot.ref.snapshot_id,'domain':domain,
        'domain_commit_id':commit.ref.commit_id,'contract_version':commit.ref.contract_version,
        'contract_digest':commit.manifest['contract_digest'],
        'builder_implementation_ref':commit.manifest['builder_implementation_ref'],
        'builder_config_digest':_digest(_json_bytes(commit.manifest['builder_config'])),
        'structural_validation':commit.manifest['validation_summary'],
        'scope':{'symbols':list(calendars),'start_session':start_session,'end_session':end_session},
        'session_mask_axis':days,'symbols':per_symbol,
        'pit_qualification_counts':dict(qualifications),'null_observation_counts':dict(nulls),
        'qualification':'current audit; union of canonical revisions; not historical PIT selection',
        'admission':'NOT_ASSESSED'}
