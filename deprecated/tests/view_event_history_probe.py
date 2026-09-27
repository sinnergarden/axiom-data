"""REPRODUCTION_EVIDENCE: compare real, small View histories at an exact base.

Caller: authorized targeted runner. Inputs: the installed immutable PR7 fixture
and explicit git base. Uses full public SnapshotReader validation and the old/new
View project functions. Counts actual canonical sequence scans and facts calls;
legacy fixture rows are already resident. Optional partition replay writes the
same validated rows in existing format to a temporary root and counts traversals,
not physical device traffic. No production data writes or supplier access.
"""
import argparse
from contextlib import ExitStack
from collections.abc import Sequence
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import tempfile
import time
from types import ModuleType
from unittest.mock import patch

from axiom_data import SnapshotReader
from axiom_data.financial_views import project as financial_project
from axiom_data.event_views import project as event_project
from fixture_locations import fixture_root

DOMAINS=('financial_events','holder_count_events','top_holders_reports','forecast_observations')


class CountedRows(Sequence):
    def __init__(self,rows):
        self.rows=rows;self.scans=0;self.visited=0
    def __len__(self):return len(self.rows)
    def __getitem__(self,index):return self.rows[index]
    def __iter__(self):
        self.scans+=1
        for row in self.rows:
            self.visited+=1
            yield row


def baseline_module(base,name):
    source=subprocess.check_output(['git','show',base+':src/axiom_data/'+name+'.py'],text=True)
    module=ModuleType('baseline_'+name)
    exec(compile(source,base+':'+name,'exec'),module.__dict__)
    return module


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base',required=True)
    parser.add_argument('--partition-replay',action='store_true',
        help='replay the same validated real rows through existing partitions in a temporary root')
    args=parser.parse_args()
    base=subprocess.check_output(['git','rev-parse',args.base+'^{commit}'],text=True).strip()
    run=json.loads(Path('deprecated/history/reports/pr7/run_manifest.json').read_bytes())
    root=fixture_root(run['source_root']);snapshot=run['refs']['snapshot_id']
    manifest=json.loads((root/'derived/pr6_fact/commits'/run['refs']['pr6_view_id']/'manifest.json').read_bytes())
    scope=dict(manifest['scope'],symbols=['688981.SH'])
    event_scope={k:scope[k] for k in ('symbols','start_session','end_session')}
    outputs={};results={}
    for mode in ('baseline','prepared'):
        reader=SnapshotReader(root,snapshot)
        counters={};partition_reads={}
        resources=ExitStack()
        temporary=resources.enter_context(tempfile.TemporaryDirectory())
        for domain in DOMAINS:
            commit=reader.commits[domain];rows=commit.rows
            if args.partition_replay:
                from axiom_data.layout import DataRootLayout
                from axiom_data.partition_rows import PartitionRows
                from axiom_data.partitions import publish_partitions,POLICY
                layout=DataRootLayout(Path(temporary))
                entries=publish_partitions(layout,domain,rows)
                rows=PartitionRows(layout,domain,dict(partition_policy=POLICY,output_files=[],partitions=entries),commit.contract)
                partition_reads[domain]=resources.enter_context(patch.object(rows,'_rows',wraps=rows._rows))
            counter=CountedRows(rows);counters[domain]=counter
            reader.commits[domain]=replace(commit,rows=counter)
        functions=((baseline_module(base,'financial_views').project,baseline_module(base,'event_views').project)
                   if mode=='baseline' else (financial_project,event_project))
        started=time.perf_counter()
        with patch.object(reader,'facts',wraps=reader.facts) as facts:
            financial=functions[0](reader,scope,manifest['pit_policy'],manifest['knowledge_cutoff'])
            events=functions[1](reader,event_scope,manifest['pit_policy'],manifest['knowledge_cutoff'])
        elapsed=time.perf_counter()-started
        outputs[mode]=(financial,events)
        results[mode]=dict(seconds=elapsed,histories={domain:dict(
            rows=len(counters[domain]),canonical_scans=counters[domain].scans,
            rows_visited=counters[domain].visited,
            facts_calls=sum(c.args[0]==domain for c in facts.call_args_list)) for domain in DOMAINS})
        for domain,opened in partition_reads.items():
            results[mode]['histories'][domain].update(partition_reads=opened.call_count,
                partition_bytes=sum(c.args[0]['bytes'] for c in opened.call_args_list))
        resources.close()
    assert outputs['baseline']==outputs['prepared'], 'View value/metadata/order mismatch'
    print(json.dumps(dict(base_commit=base,snapshot=snapshot,symbol='688981.SH',scope=scope,
        policy=manifest['pit_policy'],cutoff=manifest['knowledge_cutoff'],logical_equal=True,
        sessions=len(outputs['prepared'][0]['sessions']),results=results,
        validation='full small forensic Snapshot and full-scope View admission; no production Snapshot initialization',
        storage=('isolated existing-format partitions of the same validated real rows; formal artifacts unchanged'
                 if args.partition_replay else 'original memory-resident legacy rows'),
        measurement='whole projection time excludes Reader initialization and replay preparation; scans include admission; partition_bytes counts full object traversals, not physical device traffic'),indent=2))


if __name__=='__main__':
    main()
