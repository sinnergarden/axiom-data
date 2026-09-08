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
    collect = commands.add_parser('collect')
    collect.add_argument('--run-id', required=True)
    collect.add_argument('--plan', type=Path, required=True)
    inspect = commands.add_parser('inspect')
    inspect.add_argument('--snapshot', required=True)
    commands.add_parser('rebuild-catalog')
    args = parser.parse_args()
    if args.operation == 'collect':
        result = collect_requests(args.data_root, run_id=args.run_id,
                                  requests=json.loads(args.plan.read_bytes()))
        print(json.dumps({'run_id': result['run_id'], 'stage': result['stage'],
            'status': result['status'], 'completed': len(result['completed']),
            'failed': len(result['failed']), 'pending': result['pending_count']}))
        return 0 if result['status'] == 'COMPLETE' else 1
    if args.operation == 'inspect':
        print(json.dumps(inspect_snapshot(args.data_root, args.snapshot), ensure_ascii=False, indent=2))
    else:
        print(json.dumps({'catalog_entries': rebuild_catalog(args.data_root)}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
