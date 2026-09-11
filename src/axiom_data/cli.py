"""Thin command-line adapters over public operational services."""
import argparse
import json
from pathlib import Path
from axiom_data.operations import collect_requests, inspect_snapshot
from axiom_data import rebuild_catalog


def main():
    parser = argparse.ArgumentParser(prog='axiom-data')
    parser.add_argument('--data-root', type=Path, required=True)
    commands = parser.add_subparsers(dest='operation', required=True)
    bootstrap = commands.add_parser('bootstrap')
    bootstrap.add_argument('--run-id', required=True)
    bootstrap.add_argument('--plan', type=Path, required=True)
    views = commands.add_parser('materialize-views')
    views.add_argument('--snapshot', required=True)
    views.add_argument('--run-id', required=True)
    views.add_argument('--plan', type=Path, required=True)
    collect = commands.add_parser('collect')
    collect.add_argument('--run-id', required=True)
    collect.add_argument('--plan', type=Path, required=True)
    inspect = commands.add_parser('inspect')
    inspect.add_argument('--snapshot', required=True)
    inspect.add_argument('--scope', type=Path)
    repair = commands.add_parser('repair')
    repair.add_argument('--snapshot', required=True)
    repair.add_argument('--run-id', required=True)
    repair.add_argument('--plan', type=Path, required=True)
    daily_plan = commands.add_parser('plan-daily')
    daily_plan.add_argument('--snapshot', required=True)
    daily_plan.add_argument('--plan', type=Path, required=True)
    daily = commands.add_parser('daily')
    daily.add_argument('--snapshot', required=True)
    daily.add_argument('--run-id', required=True)
    daily.add_argument('--plan', type=Path, required=True)
    source_plan=commands.add_parser('plan-bootstrap-sources')
    source_plan.add_argument('--scope',type=Path,required=True)
    source_plan.add_argument('--output',type=Path,required=True)
    source_collect=commands.add_parser('collect-bootstrap-sources')
    source_collect.add_argument('--run-id',required=True)
    source_collect.add_argument('--plan',type=Path,required=True)
    source_collect.add_argument('--domains',nargs='+',required=True)
    commands.add_parser('rebuild-catalog')
    recovery = commands.add_parser('verify-recovery')
    recovery.add_argument('--snapshot', required=True)
    recovery.add_argument('--run-id', required=True)
    recovery.add_argument('--plan', type=Path, required=True)
    args = parser.parse_args()
    if args.operation == 'verify-recovery':
        from axiom_data import verify_recovery
        result = verify_recovery(args.data_root, run_id=args.run_id,
                                 snapshot_id=args.snapshot, **json.loads(args.plan.read_bytes()))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result['status'] == 'RECOVERY_VALIDATED' else 1
    if args.operation == 'materialize-views':
        from axiom_data import materialize_views
        result=materialize_views(args.data_root,run_id=args.run_id,snapshot_id=args.snapshot,
                                 views=json.loads(args.plan.read_bytes()))
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0 if result['status']=='VIEWS_BUILT' else 1
    if args.operation == 'bootstrap':
        from axiom_data.operations import bootstrap
        result=bootstrap(args.data_root,run_id=args.run_id,**json.loads(args.plan.read_bytes()))
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0 if result['status']=='CANDIDATE_BUILT' else 1
    if args.operation == 'daily':
        from axiom_data.operations import daily
        result=daily(args.data_root,run_id=args.run_id,snapshot_id=args.snapshot,
                     **json.loads(args.plan.read_bytes()))
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0 if result['status'] in {'CANDIDATE_BUILT','NO_CHANGE'} else 1
    if args.operation == 'repair':
        from axiom_data.operations import repair
        result = repair(args.data_root, run_id=args.run_id, snapshot_id=args.snapshot,
                        domain_inputs=json.loads(args.plan.read_bytes()))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result['status'] == 'CANDIDATE_BUILT' else 1
    if args.operation == 'plan-daily':
        from axiom_data.operations import plan_daily
        print(json.dumps(plan_daily(args.data_root, args.snapshot,
            source_requests=json.loads(args.plan.read_bytes())), ensure_ascii=False, indent=2))
        return 0
    if args.operation=='plan-bootstrap-sources':
        from axiom_data.bootstrap_sources import plan_bootstrap_sources
        plan=plan_bootstrap_sources(**json.loads(args.scope.read_bytes()))
        with args.output.open('x') as output:json.dump(plan,output,ensure_ascii=False,indent=2)
        print(json.dumps({'plan':str(args.output),'requests':{d:len(r) for d,r in plan['requests_by_domain'].items()}}))
        return 0
    if args.operation=='collect-bootstrap-sources':
        from axiom_data.bootstrap_sources import collect_bootstrap_sources
        result=collect_bootstrap_sources(args.data_root,run_id=args.run_id,plan=json.loads(args.plan.read_bytes()),domains=args.domains)
        print(json.dumps({k:v for k,v in result.items() if k!='domains'}))
        return 0 if result['status']=='COMPLETE' else 1
    if args.operation == 'collect':
        result = collect_requests(args.data_root, run_id=args.run_id,
                                  requests=json.loads(args.plan.read_bytes()))
        print(json.dumps({'run_id': result['run_id'], 'stage': result['stage'],
            'status': result['status'], 'completed': len(result['completed']),
            'failed': len(result['failed']), 'pending': result['pending_count']}))
        return 0 if result['status'] == 'COMPLETE' else 1
    if args.operation == 'inspect':
        if args.scope:
            from axiom_data.operations import inspect_scope
            result=inspect_scope(args.data_root,args.snapshot,**json.loads(args.scope.read_bytes()))
        else:
            result=inspect_snapshot(args.data_root,args.snapshot)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(json.dumps({'catalog_entries': rebuild_catalog(args.data_root)}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
