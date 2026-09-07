"""Fail-closed admission over explicit Snapshot and View coverage."""
from datetime import date, timedelta
from axiom_data.artifacts import ArtifactError
from axiom_data.pit import select_revisions


def require_symbols(rows, symbols):
    available={r['symbol'] for r in rows}
    if symbols is not None and (not symbols or not set(symbols)<=available):
        raise ArtifactError('INSUFFICIENT_SCOPE: symbols outside domain coverage')


def require_range(start,end,available_start,available_end):
    if start>end or start<available_start or end>available_end:
        raise ArtifactError('INSUFFICIENT_SCOPE: requested dates outside actual coverage')


def membership_coverage(reader,domain,group,start,end,policy,cutoff,symbols=None):
    rows=reader.commits[domain].rows
    matching=[r for r in rows if r['group_id']==group]
    if not matching:
        raise ArtifactError('UNKNOWN_UNIVERSE' if domain=='universe_membership' else 'UNKNOWN_CLASSIFICATION')
    selected=select_revisions(matching,policy=policy,knowledge_cutoff=cutoff)
    if not selected:raise ArtifactError('INSUFFICIENT_SCOPE: membership not visible')
    if domain=='universe_membership':
        configured=reader.commits[domain].manifest['builder_config'].get('symbols')
        # Unfiltered index snapshots cover the Snapshot's security identities.
        available_symbols=configured or [r['symbol'] for r in reader.security_master()]
        if symbols is not None and not set(symbols)<=set(available_symbols):
            raise ArtifactError('INSUFFICIENT_SCOPE: universe symbol coverage')
        lower=min(r.get('observation_ref',{}).get('coverage_from',r['effective_from']) for r in selected)
        upper=max(r.get('observation_ref',{}).get('coverage_to',r['effective_to'] or r['effective_from']) for r in selected)
        if start<lower or end>=upper:raise ArtifactError('INSUFFICIENT_SCOPE: universe dates')
        return {'group_id':group,'symbols':sorted(available_symbols),'start_session':lower,'end_exclusive':upper}
    required=set(symbols) if symbols is not None else {r['symbol'] for r in matching}
    for symbol in required:
        cursor=start
        for row in sorted((r for r in selected if r['symbol']==symbol),key=lambda r:r['effective_from']):
            if row['effective_to'] is not None and row['effective_to']<=cursor:continue
            if row['effective_from']>cursor:break
            cursor=max(cursor,row['effective_to'] or '9999-12-31')
            if cursor>end:break
        if cursor<=end:raise ArtifactError('INSUFFICIENT_SCOPE: classification date/security gap')
    return {'group_id':group,'symbols':sorted(required),'intervals':[
        {'symbol':r['symbol'],'start_session':r['effective_from'],'end_exclusive':r['effective_to']} for r in selected]}


def admit_view(reader,scope,policy,cutoff):
    from axiom_data.pr6_views import FIELD_MAP, WIDE_FIELDS
    symbols=scope['symbols']; start=scope['start_session'];end=scope['end_session']
    dates=[]; day=date.fromisoformat(start);stop=date.fromisoformat(end)
    while day<=stop:dates.append(day.isoformat());day+=timedelta(days=1)
    if not dates:raise ArtifactError('INSUFFICIENT_SCOPE: reversed dates')
    calendar=reader.commits['trading_calendar'].rows
    sessions=set()
    for symbol in symbols:
        exchange={'SH':'SSE','SZ':'SZSE'}[symbol[-2:]]
        rows={r['session']:r for r in calendar if r['exchange']==exchange}
        if not set(dates)<=rows.keys():raise ArtifactError('INSUFFICIENT_SCOPE: calendar gap')
        sessions.update(d for d in dates if rows[d]['is_open'])
    if not sessions:raise ArtifactError('INSUFFICIENT_SCOPE: no open sessions')
    universes=[membership_coverage(reader,'universe_membership',g,start,end,policy,cutoff,symbols) for g in scope['universe_ids']]
    # Classification is daily: closed days need no observation, open-day holes fail.
    for session in sorted(sessions):
        membership_coverage(reader,'industry_membership',scope['industry_system'],session,session,policy,cutoff,symbols)
    valuation=reader.commits['valuation_daily'].rows
    available={(r['symbol'],r['session']) for r in valuation}
    if any((s,d) not in available for s in symbols for d in sessions):
        raise ArtifactError('INSUFFICIENT_SCOPE: valuation date/security gap')
    financial=reader.commits['financial_events'].rows
    if any(not any(r['symbol']==s and r['endpoint']==e for r in financial)
           for s in symbols for e in {e for e,_ in FIELD_MAP.values()}):
        raise ArtifactError('INSUFFICIENT_SCOPE: financial endpoint/security gap')
    return {'fields':list(WIDE_FIELDS),
            'calendar_sessions':sorted({r['session'] for r in calendar}),
            'symbols':sorted({r['symbol'] for r in valuation}),
            'valuation_sessions':sorted({r['session'] for r in valuation}),
            'classification_intervals':[{'symbol':r['symbol'],'group_id':r['group_id'],
                'effective_from':r['effective_from'],'effective_to':r['effective_to']}
                for r in reader.commits['industry_membership'].rows],
            'universes':universes,'financial_report_periods':{
                s:sorted({r['report_period'] for r in financial if r['symbol']==s}) for s in symbols}}


def admit_materialized(manifest,symbols,start,end,fields):
    scope=manifest['validated_scope']
    if not symbols or not set(symbols)<=set(scope['symbols']):
        raise ArtifactError('INSUFFICIENT_SCOPE: View symbols')
    require_range(start,end,scope['start_session'],scope['end_session'])
    if not fields or not set(fields)<=set(manifest['fields']):
        raise ArtifactError('INSUFFICIENT_SCOPE: View fields')
