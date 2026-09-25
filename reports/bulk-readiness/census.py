"""REPRODUCTION_EVIDENCE: read-only census of the frozen bulk plan.

Not a production planner or readiness authority. Unchecked prerequisites remain
UNKNOWN. No builder, Raw payload read, publication, or network call is performed.
Run with PYTHONPATH=src python reports/bulk-readiness/census.py ROOT PLAN OUTPUT FROZEN_PLAN.
"""
import csv
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import resource
import sys
import time

from axiom_data.admission_plan import _builders, _config


def plan_binding(source, frozen):
    """The input envelope is not the operation's installed-code binding."""
    supplied, executed = json.loads(source), json.loads(frozen)
    assert supplied['snapshot_id'] == executed['snapshot_id']
    assert supplied['views'] == executed['views']
    return dict(frozen_plan_sha256=hashlib.sha256(frozen).hexdigest(),
        input_implementation_digest=supplied['implementation_digest'],
        executed_implementation_digest=executed['implementation_digest'],
        request_equality=True)


def main(root, plan_path, output, frozen_plan_path):
    began = time.monotonic()
    root, plan_path, output = map(Path, (root, plan_path, output))
    assert not output.resolve().is_relative_to(root.resolve())
    output.mkdir(parents=True, exist_ok=True)
    reads = Counter()

    def read(path, kind):
        content = path.read_bytes()
        reads[kind] += len(content)
        return content

    def metadata(path):
        content = read(path, 'metadata_bytes')
        sidecar = path.with_name('manifest.sha256')
        expected = sidecar.read_text().strip().removeprefix('sha256:')
        assert hashlib.sha256(content).hexdigest() == expected, str(path)
        return json.loads(content)

    original = read(plan_path, 'plan_bytes')
    binding = plan_binding(original, read(Path(frozen_plan_path), 'plan_bytes'))
    plan = json.loads(original)
    snapshot = metadata(root / 'snapshots' / plan['snapshot_id'] / 'manifest.json')
    plans = plan['views']
    assert len(plans) == 17895
    builders = _builders()
    manifests = {}
    domain_summary = {}
    event_intervals = defaultdict(lambda: defaultdict(list))
    for domain, ref in snapshot['domain_refs'].items():
        manifest = metadata(root / 'canonical' / domain / 'commits' / ref['domain_commit_id'] / 'manifest.json')
        observations = manifest['source_coverage'].get('observations', [])
        failures = [o['raw_ref']['raw_batch_id'] for o in observations
                    if o['result_status'] != 'success' or not o.get('payload_admission', {}).get('complete')]
        domain_summary[domain] = dict(partitions=len(manifest['partitions']),
            partition_bytes=sum(p['bytes'] for p in manifest['partitions']),
            observations=len(observations), unaccepted_observations=failures,
            start=manifest['builder_config'].get('start_session'),
            end=manifest['builder_config'].get('end_session'),
            parent=manifest.get('parent_commit_ref'))
        if domain in {'holder_count_events', 'top_holders_reports', 'margin_daily', 'moneyflow_daily', 'forecast_observations'}:
            for o in observations:
                p = o['request']['params']
                event_intervals[domain][p['ts_code']].append((p['start_date'], p['end_date']))
        # Keep only bounded metadata; the financial manifest contains 230k observations.
        manifests[domain] = {k: manifest[k] for k in ('partitions', 'builder_config', 'contract_version')}
        del manifest, observations

    def rows(domain, entry):
        path = root / 'canonical' / domain / 'objects' / entry['object_id'] / 'rows.json'
        content = read(path, 'canonical_bytes')
        assert len(content) == entry['bytes']
        assert hashlib.sha256(content).hexdigest() == entry['object_id']
        values = json.loads(content)
        assert len(values) == entry['rows']
        return values

    identities = {r['symbol']: r for p in manifests['security_master']['partitions'] for r in rows('security_master', p)}
    calendars = defaultdict(dict)
    for p in manifests['trading_calendar']['partitions']:
        for r in rows('trading_calendar', p):
            calendars[r['exchange']][r['session']] = r['is_open']
    anchors = {(v['config']['symbols'][0], v['config']['anchor_session'])
               for v in plans.values() if v['kind'] == 'adjusted_price'}
    months = {d[:7] for _, d in anchors}
    found = {}
    for p in manifests['adjustment_factors']['partitions']:
        if p['key'] not in months:
            continue
        for r in rows('adjustment_factors', p):
            key = r['symbol'], r['session']
            if key in anchors:
                assert key not in found
                found[key] = r
    missing = anchors - found.keys()
    nearby = defaultdict(dict)
    for domain in ('market_daily', 'security_status'):
        for p in manifests[domain]['partitions']:
            if p['key'] not in {d[:7] for _, d in missing}:
                continue
            wanted = {s for s, d in missing if d[:7] == p['key']}
            for r in rows(domain, p):
                if r['symbol'] in wanted:
                    nearby[r['symbol']].setdefault(domain, []).append(r)
    results = []
    for label, spec in plans.items():
        kind, c = spec['kind'], spec['config']
        symbol = c['symbols'][0]
        issues = []
        try:
            _config(builders[kind], root, plan['snapshot_id'], c)
        except Exception as exc:
            issues.append(dict(category='PLAN_SEMANTICS_ERROR', reason=str(exc)))
        identity = identities[symbol]
        start, end = c['start_session'], c['end_session']
        sessions = [d for d, opened in calendars[identity['exchange']].items() if start <= d <= end and opened]
        if (not sessions or identity['list_session'] is None or start < identity['list_session']
                or identity['delist_session'] is not None and end >= identity['delist_session']):
            issues.append(dict(category='PLAN_SEMANTICS_ERROR', reason='scope outside known identity/open sessions'))
        if kind == 'adjusted_price' and (symbol, c['anchor_session']) in missing:
            issues.append(dict(category='PLAN_SEMANTICS_ERROR',
                reason='Explicit anchor has no factor; no formal requested-to-effective anchor rule. Absence is not automatically a coverage gap.'))
        if kind == 'pr7_fact':
            for domain, intervals in event_intervals.items():
                bound = manifests[domain]['builder_config'].get('end_session')
                for day in sessions:
                    required = min(day, bound) if bound else day
                    key = required.replace('-', '')
                    if not any(a <= key <= b for a, b in intervals.get(symbol, [])):
                        issues.append(dict(category='COVERAGE_GAP', reason=f'{domain}: no accepted bounded request at {required}'))
                        break
        results.append(dict(label=label, kind=kind, symbol=symbol,
            status='BLOCKED' if issues else 'UNKNOWN', issues=issues,
            pending='Full family-specific prerequisites are not certified by this evidence census.'))
    summary = dict(snapshot_id=plan['snapshot_id'], plan_sha256=hashlib.sha256(original).hexdigest(),
        **binding,
        total=len(results), statuses=dict(Counter(r['status'] for r in results)),
        families={k:dict(Counter(r['status'] for r in results if r['kind']==k)) for k in sorted(builders)},
        categories=dict(Counter(i['category'] for r in results for i in r['issues'])),
        missing_anchors=[dict(symbol=s, anchor=d, identity=identities[s]) for s,d in sorted(missing)],
        domains=domain_summary, read_bytes=dict(reads), elapsed_seconds=time.monotonic()-began,
        peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        limitations=['No View rebuilt; no complete Snapshot closure revalidation.',
                    'UNKNOWN is retained for unchecked row-level prerequisites, not reported as READY.',
                    'Request metadata proves accepted request intervals, not presence of observations.',
                    'No Raw payloads read; no automatic source disagreement reclassification.'])
    with (output/'plans.csv').open('w', newline='') as stream:
        writer = csv.writer(stream, lineterminator='\n')
        writer.writerow(['label', 'kind', 'symbol', 'status', 'issues'])
        writer.writerows([r['label'], r['kind'], r['symbol'], r['status'], json.dumps(r['issues'])] for r in results)
    (output/'summary.json').write_text(json.dumps(summary,indent=2,sort_keys=True)+'\n')
    (output/'missing-anchor-context.json').write_text(json.dumps(nearby,indent=2,sort_keys=True)+'\n')
    print(json.dumps({k:summary[k] for k in ('total','statuses','categories','missing_anchors','read_bytes','elapsed_seconds')}))


if __name__ == '__main__':
    main(*sys.argv[1:])
