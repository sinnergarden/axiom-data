"""Small synthetic producer/admission measurement, never a real Data query.

Run --oracle with PYTHONPATH pointing at clean frozen Data 8bebf147 and
Engine 633fd7a1. Run --part-bytes with current src:tests and pass that oracle.
Prints actual process RSS (including synthetic inputs and loaded Snapshot),
physical read/decode counts and timing. No five-year performance claim.
"""
import argparse
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import resource
import subprocess
import sys
import tempfile
from time import perf_counter

from axiom_data import Data, QuerySpec
from native_view_fixture import Store, source, reads


def fixture():
    manifest,rows=source()
    days=tuple(f'2024-01-{n:02d}' for n in range(2,22))
    symbols=tuple(f'S{n:04d}' for n in range(300))
    stamp='2024-01-01T00:00:00+00:00'
    def row(**kw):
        return dict(revision_id='r1',revision_sequence=1,raw_batch_id='synthetic',
                    first_observed_at=stamp,source_available_at=None,evidence_ref=None,**kw)
    def values(domain,items):
        uri=manifest['domains'][domain]['partitions'][0]['uri']
        rows[uri]=items;manifest['domains'][domain]['partitions'][0]['rows']=len(items)
    values('market_daily',[row(security_id=s,session=day,close=float(i+1),volume=2**53+1)
        for day in days for i,s in enumerate(symbols)])
    values('trading_calendar',[row(exchange='SSE',session=day,is_open=True) for day in days])
    values('security_master',[row(security_id=s,exchange='SSE',listing_date='2024-01-01',
        delisting_date=None) for s in symbols])
    values('security_status',[row(security_id=s,session=day,is_suspended=False) for day in days for s in symbols])
    values('universe_membership',[row(security_id=s,universe_id='IDX',membership_id=s,
        effective_from='2024-01-01',effective_to='2024-02-01') for s in symbols])
    manifest['domains']['universe_membership']['coverage']={'complete_states':[
        dict(universe_id='IDX',complete=True,effective_from='2024-01-01',effective_to='2024-02-01',
            members=list(symbols),first_observed_at=stamp,raw_batch_id='synthetic')]}
    # Deliberately exercise complete shared coverage, not a tiny summary.
    manifest['domains']['market_daily']['coverage']={'complete_cells':[
        dict(security_id=s,session=day,complete=True) for day in days for s in symbols],
        'note':'合成 coverage; no private data'}
    requests=[]
    for method in ('read_market','members','states'):
        members=method=='members'
        q=QuerySpec('universe_membership' if members else 'market_daily',
            ('is_member',) if members else ('close',) if method=='states' else ('close','volume'),
            symbols,days,'operational_pit_v1',{s:'2024-02-02T00:00:00+00:00' for s in days},
            purpose='decision_facts' if members else 'market_replay',universe_id='IDX' if members else None)
        requests.append({'method':method,'query':q})
    requests.extend(reads()[10:12])
    d=Data('/synthetic-native-view',cache_bytes=32*1048576)
    d.store=Store(manifest,rows)
    return d,requests


def rss_bytes():
    value=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value if sys.platform=='darwin' else value*1024


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--oracle',action='store_true')
    parser.add_argument('--expected',type=Path)
    parser.add_argument('--part-bytes',type=int,default=1048576)
    args=parser.parse_args()
    d,requests=fixture()
    if args.oracle:
        import axiom_data
        from axiom_engine.core.contracts import canonical
        from axiom_engine.runtime.stock_evidence import native_ref
        root=Path(axiom_data.__file__).resolve().parents[2]
        assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()=='8bebf14742c36277a7ba4d3b913ac7c1bdca102d'
        assert not subprocess.check_output(['git','diff','--','src/axiom_data'],cwd=root)
        start=perf_counter();cases=[]
        for request in requests:
            wire=getattr(d,request['method'])(snapshot='s1',query=request['query']).to_json()
            encoded=canonical(wire).encode('utf-8')
            cases.append({'native_ref':native_ref(wire),'logical_bytes':len(encoded)})
        print(json.dumps({'cases':cases,'seconds':perf_counter()-start,'process_peak_rss_bytes':rss_bytes()},sort_keys=True))
        return
    assert args.expected is not None
    expected=json.loads(args.expected.read_bytes())
    from axiom_data import open_native_view
    limits={'max_part_bytes':args.part_bytes,'max_rows_per_block':100000,
        'max_working_bytes':512*1048576,'max_saved_bytes':64*1048576}
    with tempfile.TemporaryDirectory() as temp:
        root=Path(temp)/'native';start=perf_counter()
        result=d.export_native_view(snapshot='s1',reads=requests,destination=root,limits=limits)
        producer=perf_counter()-start;start=perf_counter()
        view=open_native_view(root,manifest_sha256=result['content_digest'],limits=limits)
        cold=perf_counter()-start;admission=deepcopy(view.statistics)
        assert result['native_refs']==[c['native_ref'] for c in expected['cases']]
        start=perf_counter();count=0
        for ref in result['native_refs']:
            for block in view.iter_blocks(native_ref=ref): count+=len(block['records'])
        warm=perf_counter()-start;full=deepcopy(view.statistics)
        for name in ('file_validations','coverage_validations','logical_replays','admission_reads'):
            assert full[name]==admission[name]
        out={'synthetic_rows':count,'part_cap_bytes':args.part_bytes,'producer_seconds':producer,
            'cold_admission_seconds':cold,'warm_selected_seconds':warm,
            'process_peak_rss_bytes':rss_bytes(),'producer':result['statistics'],
            'cold':admission,'after_warm':full,'native_refs':result['native_refs'],
            'saved_bytes':sum(p.stat().st_size for p in root.rglob('*') if p.is_file()),
            'largest_array_bytes':max(v['bytes'] for v in view.manifest['files'].values() if v['kind']=='array'),
            'oracle_file_sha256':sha256(args.expected.read_bytes()).hexdigest()}
        view.close();print(json.dumps(out,sort_keys=True))


if __name__=='__main__': main()
