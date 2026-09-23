"""Fail-closed admission over explicit Snapshot and View coverage."""
from datetime import date, timedelta
from contextlib import contextmanager
from axiom_data.artifacts import ArtifactError
from axiom_data.pit import select_revisions, select_financial_revisions, financial_ambiguities


@contextmanager
def financial_batch(reader):
    """Share preparation for one operation; publish ordinary independent Views.

    Histories occupy at most the input financial domain, not securities times
    history. No projected View payload or persistent execution state is retained.
    """
    previous = getattr(reader, '_financial_batch', None)
    reader._financial_batch = {}
    try:
        yield
    finally:
        reader._financial_batch = previous


def prepared_input(reader, slot, arguments, prepare):
    """Reuse financial preparation only on a checked immutable Reader.

    File state invalidates reuse; it never selects an artifact. Ordinary fixture
    Readers without a verified closure always execute the authoritative path.
    Encoded values prevent a consumer from mutating the cached admission result.
    """
    from axiom_data.artifacts import _json_bytes
    from axiom_data.verification_cache import file_state
    import json
    observed = getattr(reader, '_view_validation_paths', None)
    if observed is None:
        return prepare()
    batch = getattr(reader, '_financial_batch', None)
    if slot not in {'admission', 'financial_history', 'calendar', 'security_master', 'taxonomy'}:
        raise ValueError('unknown financial preparation slot')
    if batch is None and slot not in {'admission', 'financial_history'}:
        return prepare()
    def check():
        if any(file_state(p) != state for p, state in observed.items()):
            reader._financial_preparation = {}
            if batch is not None:
                batch.clear()
            raise ArtifactError('View inputs changed after validation')
    # Calendar/identity/taxonomy are consumed by project/admit_view before the
    # existing admission and history guards. Those guards check the entire
    # closure, including these inputs; do not add another full stat walk for
    # every small metadata cache hit.
    if slot in {'admission', 'financial_history'}:
        check()
    key = (reader.snapshot.ref.snapshot_id,
           tuple((d, c.ref.commit_id, c.manifest_digest, id(c)) for d, c in sorted(reader.commits.items())),
           arguments)
    if batch is not None:
        if batch.get('inputs') != key[:2]:
            batch.clear()
            batch['inputs'] = key[:2]
        if slot == 'financial_history':
            if 'histories' not in batch:
                # Admission is performed by project before requesting history.
                # Read the public full history once, then retain encoded groups
                # so caller mutations cannot alter a later artifact.
                grouped = {}
                for index, row in enumerate(reader.facts('financial_events')):
                    grouped.setdefault(row['symbol'], []).append((index, row))
                batch['histories'] = {s:_json_bytes(rows) for s,rows in grouped.items()}
                check()
            if not arguments or not set(arguments) <= batch['histories'].keys():
                raise ArtifactError('INSUFFICIENT_SCOPE: symbols outside domain coverage')
            rows = [item for symbol in arguments for item in json.loads(batch['histories'][symbol])]
            return [row for _,row in sorted(rows, key=lambda item:item[0])]
        if slot in {'calendar', 'security_master', 'taxonomy'}:
            previous = batch.get(slot)
            if previous is not None and previous[0] == key:
                return json.loads(previous[1])
            value = prepare()
            check()
            batch[slot] = (key, _json_bytes(value))
            return value
    cache = getattr(reader, '_financial_preparation', {})
    previous = cache.get(slot)
    if previous is not None and previous[0] == key:
        return json.loads(previous[1])
    value = prepare()
    check()
    encoded = _json_bytes(value)
    cache.pop(slot, None)
    # Fixed two slots, at most 32 MiB each. No disk state or process-wide cache.
    if len(encoded) <= 32 * 1024 * 1024:
        cache[slot] = (key, encoded)
    reader._financial_preparation = cache
    return value


def _require_valuation(inputs, symbols, sessions):
    indexes = inputs['session_index']
    required = sum(1 << indexes[d] for d in sessions if d in indexes)
    if not set(sessions) <= indexes.keys() or any(
            int(inputs['coverage'].get(s,'0'),16) & required != required for s in symbols):
        raise ArtifactError('INSUFFICIENT_SCOPE: valuation date/security gap')


def _admission_inputs(reader, policy, cutoff, financial_resolution, symbols=(), sessions=()):
    """Full-scope admission summaries, independent of requested security/window."""
    def prepare():
        session_index = {}; coverage = {}
        for row in reader.commits['valuation_daily'].rows:
            session = row['session']; symbol = row['symbol']
            index = session_index.setdefault(session, len(session_index))
            coverage[symbol] = coverage.get(symbol, 0) | (1 << index)
        result = {'session_index':session_index, 'coverage':{s:hex(bits) for s,bits in coverage.items()}}
        # Preserve admission order on a cache miss: invalid valuation scope is
        # rejected before attempting financial selection, as on the old path.
        _require_valuation(result, symbols, sessions)
        rows = reader.commits['financial_events'].rows
        selector = select_financial_revisions if financial_resolution else select_revisions
        endpoints = {}; periods = {}
        for row in selector(rows, policy=policy, knowledge_cutoff=cutoff):
            endpoints.setdefault(row['symbol'], set()).add(row['endpoint'])
            periods.setdefault(row['symbol'], set()).add(row['report_period'])
        return {**result,
                'endpoints': {s:sorted(values) for s,values in endpoints.items()},
                'periods': {s:sorted(values) for s,values in periods.items()},
                **({'financial_ambiguities':financial_ambiguities(rows,policy=policy,knowledge_cutoff=cutoff)}
                   if financial_resolution else {})}
    result = prepared_input(reader, 'admission', (policy, cutoff, financial_resolution), prepare)
    _require_valuation(result, symbols, sessions)
    return result


def require_symbols(rows, symbols):
    available={r['symbol'] for r in rows}
    if symbols is not None and (not symbols or not set(symbols)<=available):
        raise ArtifactError('INSUFFICIENT_SCOPE: symbols outside domain coverage')


def require_range(start,end,available_start,available_end):
    if start>end or start<available_start or end>available_end:
        raise ArtifactError('INSUFFICIENT_SCOPE: requested dates outside actual coverage')


def membership_coverage(reader,domain,group,start,end,policy,cutoff,symbols=None):
    rows=reader.commits[domain].rows
    if reader.commits[domain].ref.contract_version=='industry_membership.v3':
        if group!='SW2021':raise ArtifactError('unknown industry classification system')
        require_symbols(rows,symbols)
        required=set(symbols) if symbols is not None else {r['symbol'] for r in rows}
        selected=select_revisions(rows,policy=policy,knowledge_cutoff=cutoff)
        selected=[r for r in selected if r['symbol'] in required]
        if {r['symbol'] for r in selected}!=required:raise ArtifactError('INSUFFICIENT_SCOPE: industry observation not visible')
        for row in selected:
            if start<row['coverage_from'] or end>=row['coverage_to']:raise ArtifactError('INSUFFICIENT_SCOPE: industry dates')
        return {'group_id':group,'symbols':sorted(required),'start_session':start,'end_session':end,
                'classification_gaps':'explicit supplier availability states'}
    group_states=reader.commits[domain].manifest.get('group_states')
    if group_states is not None:
        from axiom_data.pit import select_group_states
        known=[state for state in group_states if state['universe_id']==group]
        if not known:raise ArtifactError('UNKNOWN_UNIVERSE')
        selected=select_group_states(known,policy=policy,knowledge_cutoff=cutoff)
        if not selected:raise ArtifactError('INSUFFICIENT_SCOPE: group observation not visible')
        state=selected[0]
        if start<state['coverage_from'] or end>=state['coverage_to']:
            raise ArtifactError('INSUFFICIENT_SCOPE: universe dates')
        available=reader.commits[domain].manifest['builder_config'].get('symbols') or [r['symbol'] for r in reader.security_master()]
        if symbols is not None and not set(symbols)<=set(available):
            raise ArtifactError('INSUFFICIENT_SCOPE: universe symbols')
        return {'group_id':group,'symbols':sorted(available),'start_session':state['coverage_from'],
                'end_exclusive':state['coverage_to'],'state_id':state['state_id']}
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


def admit_view(reader,scope,policy,cutoff,*,financial_resolution=True):
    from axiom_data.domains.fundamentals import FIELD_MAP, WIDE_FIELDS
    symbols=scope['symbols']; start=scope['start_session'];end=scope['end_session']
    dates=[]; day=date.fromisoformat(start);stop=date.fromisoformat(end)
    while day<=stop:dates.append(day.isoformat());day+=timedelta(days=1)
    if not dates:raise ArtifactError('INSUFFICIENT_SCOPE: reversed dates')
    calendar=prepared_input(reader,'calendar',(),lambda:list(reader.commits['trading_calendar'].rows))
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
    inputs=_admission_inputs(reader,policy,cutoff,financial_resolution,symbols,sessions)
    indexes=inputs['session_index']
    if any(e not in inputs['endpoints'].get(s,())
           for s in symbols for e in {e for e,_ in FIELD_MAP.values()}):
        raise ArtifactError('INSUFFICIENT_SCOPE: financial endpoint/security gap')
    industry_rows=reader.commits['industry_membership'].rows
    if reader.commits['industry_membership'].ref.contract_version=='industry_membership.v3':
        industry_rows=select_revisions(industry_rows,policy=policy,knowledge_cutoff=cutoff)
    return {**({'financial_ambiguities':inputs['financial_ambiguities']} if financial_resolution else {}),
            'fields':list(WIDE_FIELDS),
            'calendar_sessions':sorted({r['session'] for r in calendar}),
            'symbols':sorted(inputs['coverage']),
            'valuation_sessions':sorted(indexes),
            'classification_intervals':[{'symbol':r['symbol'],'group_id':r['group_id'],
                'effective_from':span['effective_from'],'effective_to':span['effective_to']}
                for r in industry_rows
                for span in (r['membership_spans'] if 'membership_spans' in r else [r])],
            'universes':universes,'financial_report_periods':{
                s:inputs['periods'].get(s,[]) for s in symbols}}


def admit_materialized(manifest,symbols,start,end,fields):
    scope=manifest['validated_scope']
    if not symbols or not set(symbols)<=set(scope['symbols']):
        raise ArtifactError('INSUFFICIENT_SCOPE: View symbols')
    require_range(start,end,scope['start_session'],scope['end_session'])
    if not fields or not set(fields)<=set(manifest['fields']):
        raise ArtifactError('INSUFFICIENT_SCOPE: View fields')
