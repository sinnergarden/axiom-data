"""Common SW/CITIC source diagnostics; this module never publishes canonical facts."""
from collections import Counter, defaultdict
from bisect import bisect_right
from datetime import date
import json
from axiom_data import ArtifactError, load_raw_batch
from axiom_data.sw_source import load_profile, profile_digest, validate_request, payload_issues

PROFILE = 'tushare_industry_qualification.v1'


def observation(data_root, identity):
    raw = load_raw_batch(data_root, identity)
    m = raw.manifest
    request = m['request']
    endpoint, params = request['endpoint'], request['params']
    if (m['source_profile_version'] != PROFILE or m['source_profile_digest'] != profile_digest(PROFILE)
            or m['source_profile_ref'] != 'tushare.sw-pilot.' + endpoint
            or request['fields'] != load_profile(PROFILE)['endpoints'][endpoint]['fields']):
        raise ArtifactError('industry qualification source binding mismatch')
    validate_request(endpoint, params, profile_version=PROFILE)
    rows = json.loads(raw.payload)
    issues = payload_issues(endpoint, params, rows, profile_version=PROFILE)
    if issues:
        raise ArtifactError('industry qualification payload: ' + ','.join(issues))
    return raw, rows


def normalized(rows):
    return Counter(json.dumps(r, sort_keys=True, ensure_ascii=False) for r in rows)


def validate_pages(data_root, entries, *, endpoint, mode, repeat):
    pages = sorted((e for e in entries if (e['endpoint'], e['mode'], e['repeat']) ==
                    (endpoint, mode, repeat)), key=lambda e: e['offset'])
    if not pages:
        raise ArtifactError('missing page series')
    rows = []; offset = 0; terminal = False; previous = set()
    for e in pages:
        raw, values = observation(data_root, e['raw_batch_id'])
        params = raw.manifest['request']['params']
        if (terminal or e['offset'] != offset or e['limit'] <= 0
            or raw.manifest['request']['endpoint'] != endpoint
            or params != {'is_new': mode, 'limit': str(e['limit']), 'offset': str(offset)}
            or len(values) != e['rows'] or len(values) > e['limit']
            or raw.ref.manifest_digest != e['manifest_digest']):
            raise ArtifactError('incomplete or misbound page series')
        keys = set(normalized(values))
        if previous & keys:
            raise ArtifactError('duplicate records across pages: unstable pagination')
        previous |= keys
        rows.extend(values)
        terminal = len(values) < e['limit']
        offset += e['limit']
    if not terminal:
        raise ArtifactError('missing terminal page')
    return rows


def runs(days, selected):
    """Contiguous runs in the supplied exchange-open-session sequence."""
    result = []
    for day in days:
        if day in selected:
            if not result or not result[-1].pop('_active', False):
                result.append({'from': day, 'to': day, 'sessions': 0})
            result[-1].update(to=day, sessions=result[-1]['sessions'] + 1, _active=True)
        elif result:
            result[-1].pop('_active', None)
    for r in result:
        r.pop('_active', None)
    return result


def score_history(rows, *, stock, sessions, start, end,
                  lifetime_basis='supplier list_date/delist_date diagnostic, boundary qualification tracked separately'):
    """Identical candidate metrics. Vendor lifetime is a diagnostic premise only.

    An out-date trading session is separately ambiguous; no inclusive/exclusive
    convention is silently promoted. Conflicts inside intervals remain blockers.
    """
    date.fromisoformat(start); date.fromisoformat(end)
    start, end = start.replace('-', ''), end.replace('-', '')
    if start > end or not stock:
        raise ArtifactError('invalid qualification scope')
    by_symbol = defaultdict(list); taxonomy = defaultdict(set); parents = defaultdict(set)
    invalid = []; duplicate = len(rows) - len(normalized(rows))
    for r in rows:
        by_symbol[r['ts_code']].append(r)
        try:
            date.fromisoformat(r['in_date'])
            if r['out_date']:
                date.fromisoformat(r['out_date'])
                if r['out_date'] < r['in_date']:
                    raise ValueError('reversed interval')
        except (ValueError, TypeError):
            invalid.append(r)
        for level in ('l1', 'l2', 'l3'):
            taxonomy[(level, r[level + '_code'])].add(r[level + '_name'])
        parents[('l2', r['l2_code'])].add(r['l1_code'])
        parents[('l3', r['l3_code'])].add(r['l2_code'])
    totals = Counter(); output = {}; transitions = Counter()
    for symbol, security in sorted(stock.items()):
        exchange = security['exchange']
        if exchange not in sessions or not sessions[exchange]:
            raise ArtifactError('qualification lacks exchange calendar')
        if (symbol.endswith('.SH') and exchange != 'SSE') or (symbol.endswith('.SZ') and exchange != 'SZSE'):
            raise ArtifactError('security exchange mismatch')
        days = sorted(d for d in sessions[exchange] if max(start, security['list_date']) <= d <= end
                      and (not security['delist_date'] or d < security['delist_date']))
        history = sorted(by_symbol[symbol], key=lambda r: (r['in_date'] or '', r['out_date'] or '99999999', r['l3_code']))
        if any(r in invalid for r in history):
            raise ArtifactError('invalid interval in target scope')
        classified = set(); gap = set(); ambiguous = set(); overlap = set(); conflict = set()
        for day in days:
            active = [r for r in history if r['in_date'] <= day and (not r['out_date'] or day < r['out_date'])]
            boundary = [r for r in history if r['out_date'] == day]
            if len(active) > 1:
                overlap.add(day)
                if len({r['l3_code'] for r in active}) > 1:
                    conflict.add(day)
            if boundary:
                ambiguous.add(day)
            elif active:
                classified.add(day)
            else:
                gap.add(day)
        gap_runs = runs(days, gap)
        head = gap_runs[0]['sessions'] if gap_runs and gap_runs[0]['from'] == days[0] else 0
        tail = gap_runs[-1]['sessions'] if gap_runs and gap_runs[-1]['to'] == days[-1] else 0
        internal = sum(g['sessions'] for g in gap_runs if g['from'] != days[0] and g['to'] != days[-1]) if days else 0
        local_transitions = []
        calendar = sorted(sessions[exchange])
        seen = set(); reentry = 0
        for r in history:
            if r['l3_code'] in seen:
                reentry += 1
            seen.add(r['l3_code'])
        for left, right in zip(history, history[1:]):
            out, incoming = left['out_date'], right['in_date']
            pos = bisect_right(calendar, out) if out else len(calendar)
            next_open = calendar[pos] if pos < len(calendar) else None
            relation = ('open_previous_interval' if not out else 'same_date' if out == incoming
                        else 'overlap' if incoming < out
                        else 'outside_calendar_scope' if out < calendar[0] or incoming > calendar[-1]
                        else 'next_open_session' if next_open == incoming else 'gap')
            transitions[relation] += 1
            local_transitions.append({'out_date': out, 'next_in_date': incoming, 'relation': relation})
        current = [r for r in history if r['is_new'] == 'Y']
        item = dict(expected_sessions=len(days), classified_sessions=len(classified), gap_sessions=len(gap),
                    ambiguous_sessions=len(ambiguous), overlap_sessions=len(overlap), conflict_sessions=len(conflict),
                    listing_gap_sessions=head, tail_gap_sessions=tail, internal_gap_sessions=internal,
                    gap_runs=gap_runs, ambiguous_dates=sorted(ambiguous), conflict_dates=sorted(conflict),
                    first_membership=history[0]['in_date'] if history else None,
                    list_date=security['list_date'], delist_date=security['delist_date'],
                    membership_rows=len(history), current_rows=len(current), reentries=reentry,
                    current_active_rows=sum(r['in_date']<=end and (not r['out_date'] or end<r['out_date']) for r in current),
                    transitions=local_transitions)
        output[symbol] = item
        totals['eligible_securities'] += bool(days)
        totals['gap_securities'] += bool(gap)
        totals['missing_history_securities'] += not bool(history)
        totals['live_security_without_current'] += security['list_status']=='L' and not bool(current)
        totals['live_security_multiple_current'] += security['list_status']=='L' and len(current)>1
        for key in ('expected_sessions', 'classified_sessions', 'gap_sessions', 'ambiguous_sessions',
                    'overlap_sessions', 'conflict_sessions', 'listing_gap_sessions', 'tail_gap_sessions', 'internal_gap_sessions', 'reentries'):
            totals[key] += item[key]
    return dict(totals=dict(totals), coverage_ratio=totals['classified_sessions']/totals['expected_sessions'],
                longest_gap=max((r['sessions'] for s in output.values() for r in s['gap_runs']), default=0),
                duplicate_rows=duplicate, invalid_intervals=invalid, transitions=dict(transitions),
                taxonomy_counts={level:sum(k[0]==level for k in taxonomy) for level in ('l1','l2','l3')},
                taxonomy_name_conflicts=[{'level':k[0], 'code':k[1], 'names':sorted(v)} for k,v in taxonomy.items() if len(v)!=1 or not all(v) or not k[1]],
                hierarchy_conflicts=[{'level':k[0], 'code':k[1], 'parents':sorted(v)} for k,v in parents.items() if len(v)!=1],
                taxonomy_metadata=[{'level':k[0], 'code':k[1], 'names':sorted(v), 'parents':sorted(parents.get(k,()))} for k,v in sorted(taxonomy.items())],
                security_results=output, lifetime_basis=lifetime_basis,
                historical_pit='best_effort', canonical_admission='NOT_ADMITTED')
