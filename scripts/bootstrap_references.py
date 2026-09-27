#!/usr/bin/env python3
"""Build three reference commits from one existing frozen bootstrap scope.

Run with PYTHONPATH=src. Config contains official_termination_raw_batch_ids
(an exchange-to-Raw-ID mapping) and optional page_size. Official termination
Raw must already have been published through publish_termination. This script
uses the reference collector's existing operation checkpoints and deterministic
public builders; it does not publish a Snapshot or change a default pointer.
"""
import argparse
import json
from pathlib import Path

from axiom_data import (ArtifactError, BuildApplication, TushareMarketBuilder,
                        load_raw_batch, validate_domain_commit_closure)
from axiom_data.artifacts import _digest, _identity, _layout, _safe_path, _ensure_directory, _json_bytes
from axiom_data.exchange_security import ExchangeSecurityBuilder, profile
from axiom_data.operations import save_progress
from axiom_data.fundamentals_source import FundamentalsBuilder
from axiom_data.publication import writer
from axiom_data.reference_sources import collect_reference_sources, plan_reference_requests
from axiom_data.source_completeness import validate_raw_completeness


def bootstrap_references(data_root, *, run_id, parent_run_id, source_plan_path, config):
    """Resume source collection, then validate all three canonical reference commits."""
    _identity('run_id', run_id)
    _identity('parent_run_id', parent_run_id)
    if run_id == parent_run_id:
        raise ArtifactError('reference collection requires its own child operation')
    root = _layout(data_root).root
    source_path = Path(source_plan_path)
    if '..' in source_path.parts:
        raise ArtifactError('source plan must remain inside the parent operation')
    source_path = _safe_path(root, source_path if source_path.is_absolute() else root / source_path)
    parent_directory = root / 'operations' / parent_run_id / 'source-plans'
    if not source_path.is_relative_to(parent_directory):
        raise ArtifactError('source plan must belong to the explicit parent operation')
    source_bytes = source_path.read_bytes()
    source_plan = json.loads(source_bytes)
    if not isinstance(source_plan, dict) or 'scope' not in source_plan:
        raise ArtifactError('frozen bootstrap source plan with scope required')
    if (not isinstance(config, dict)
            or set(config) - {'official_termination_raw_batch_ids', 'page_size'}
            or 'official_termination_raw_batch_ids' not in config):
        raise ArtifactError('explicit official termination Raw config required')
    config = json.loads(_json_bytes(config))
    scope = source_plan['scope']
    plan = plan_reference_requests(scope, page_size=config.get('page_size', 1000))
    official = config['official_termination_raw_batch_ids']
    exchanges = {'SSE' if symbol.endswith('.SH') else 'SZSE' for symbol in scope['symbols']}
    if not isinstance(official, dict) or set(official) != exchanges:
        raise ArtifactError('exact scoped exchange termination Raw refs required')
    authority = profile()
    for exchange, raw_id in sorted(official.items()):
        _identity('official_termination_raw_batch_id', raw_id)
        raw = load_raw_batch(root, raw_id)
        if (raw.manifest['domain'] != 'security_master'
                or raw.manifest['source_profile_version'] != authority['profile_version']
                or raw.manifest['request'] != authority['endpoints'][exchange]):
            raise ArtifactError('official termination Raw exchange/profile binding mismatch')
        validate_raw_completeness(raw)
    common = {key: scope[key] for key in ('symbols', 'start_session', 'end_session')}
    common.update(storage_policy='domain_time_blocks.v1',
                  no_change_policy='reuse_equal_state.v1', coverage_state_policy='source_observations.v2')
    calendar_symbols = list(scope['symbols'])
    for benchmark in scope['benchmarks']:
        exchange = 'SSE' if benchmark.endswith('.SH') else 'SZSE'
        if exchange not in exchanges:
            calendar_symbols.append(benchmark)
    builds = {
        'trading_calendar': {'contract_version': 'trading_calendar.v1',
                             'config': dict(common, symbols=calendar_symbols)},
        'security_master': {'contract_version': 'security_master.v2',
                            'config': dict(common, security_boundary_policy='exchange_security.v1')},
        'industry_membership': {'contract_version': 'industry_membership.v3',
                                'config': dict(common, industry_source_profile='tushare_sw2021.v1')},
    }
    context = {'parent_run_id': parent_run_id,
               'source_plan_path': source_path.relative_to(root).as_posix(),
               'source_plan_digest': _digest(source_bytes), 'config': config, 'builds': builds}
    collected = collect_reference_sources(root, run_id=run_id, plan=plan, context=context)
    if collected['status'] != 'COMPLETE':
        report = dict(collected, run_id=run_id, parent_run_id=parent_run_id,
                      stage='SOURCE_COLLECTION', domain_commit_ids={}, ready_for_consumption=False)
        with writer(root):
            directory = root / 'operations' / run_id
            _ensure_directory(root, directory)
            save_progress(_safe_path(root, directory / 'reference-report.json'), report)
        return report
    raw_ids = {domain: list(collected['raw_batch_ids'][domain]) for domain in builds}
    raw_ids['security_master'].extend(official[exchange] for exchange in sorted(official))
    report = {'status': 'RUNNING', 'stage': 'REFERENCE_BUILD',
              'run_id': run_id, 'parent_run_id': parent_run_id, 'domain_commit_ids': {},
              'raw_batch_ids': raw_ids, 'collection': collected, 'ready_for_consumption': False}
    with writer(root):
        directory = root / 'operations' / run_id
        _ensure_directory(root, directory)
        report_path = _safe_path(root, directory / 'reference-report.json')
        save_progress(report_path, report)
        commits = report['domain_commit_ids']
        try:
            for domain, spec in builds.items():
                if domain == 'trading_calendar':
                    builder = TushareMarketBuilder(root, domain, builder_config=spec['config'])
                elif domain == 'security_master':
                    builder = ExchangeSecurityBuilder(root, builder_config=spec['config'])
                else:
                    builder = FundamentalsBuilder(root, domain, builder_config=spec['config'],
                                        dependency_commit_ids={'security_master': commits['security_master']})
                # Replaying the same explicit inputs reuses the publisher's immutable ID.
                ref = BuildApplication(domain, builder).build(None, raw_ids[domain], [], spec['contract_version'])
                checked = validate_domain_commit_closure(root, domain, ref.commit_id)
                if (checked.ref.commit_id != ref.commit_id
                        or checked.ref.contract_version != spec['contract_version']):
                    raise ArtifactError('reference commit validation differs from build result')
                commits[domain] = ref.commit_id
        except Exception as exc:
            report.update(status='FAILED', error_type=type(exc).__name__)
            save_progress(report_path, report)
            raise
        report.update(status='REFERENCES_VALIDATED', stage='REFERENCE_COMMITS_VALIDATED')
        save_progress(report_path, report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', required=True)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--parent-run-id', required=True)
    parser.add_argument('--source-plan', required=True)
    parser.add_argument('--config', required=True)
    args = parser.parse_args(argv)
    result = bootstrap_references(args.data_root, run_id=args.run_id,
        parent_run_id=args.parent_run_id, source_plan_path=args.source_plan,
        config=json.loads(Path(args.config).read_bytes()))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if result['status'] == 'REFERENCES_VALIDATED' else 1


if __name__ == '__main__':
    raise SystemExit(main())
