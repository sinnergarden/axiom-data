"""Small normal LocalStore Snapshot; no supplier or private data dependency."""
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
import shutil

import pyarrow as pa
import pyarrow.parquet as pq

from axiom_data import Data,QuerySpec
from axiom_data.storage import _json_bytes,_json_digest

EARLY='2024-01-03T12:00:00+00:00'
LATE='2024-01-06T00:00:00+00:00'
LIMITS={'cache_bytes':2*1048576,'max_working_bytes':8*1048576}


def facts():
    domains,rows={},{}
    def row(symbol,session,**values):
        return dict(security_id=symbol,session=session,revision_id='r1',revision_sequence=1,
            raw_batch_id='raw1',first_observed_at='2024-01-01T00:00:00+00:00',
            source_available_at=None,evidence_ref=None,**values)
    def add(name,fields,values,partition='2024-01',keys=('security_id','session')):
        uri=name+'/'+partition+'.parquet';rows[uri]=values
        domains[name]=dict(contract=dict(contract_id=name+'.synthetic.v1',logical_key=list(keys),
            fields={n:dict(dtype=dtype,unit='CNY/share' if name=='market_daily' else None,basis='native') for n,dtype in fields.items()}),
            source_profile=dict(id=name+'.synthetic.v1',availability=dict(timezone='Asia/Shanghai',session_release_time='18:00:00')),
            partitions=[dict(partition=partition,uri=uri,rows=len(values),file_sha256='0'*64)],
            raw_batch_ids=[],coverage={'synthetic':True},build_context={})
    price=row('A','2024-01-02',open=-0.0,close=10.)
    add('market_daily',{'open':'float64','close':'float64'},[
        price,row('A','2024-01-03',open=4.,close=5.),row('B','2024-01-02',open=None,close=None),
        row('B','2024-01-03',open=float('nan'),close=float('inf')),
        dict(price,revision_id='r2',revision_sequence=2,open=7.,close=12.,first_observed_at='2024-01-04T00:00:00+00:00'),
        # Malformed clock and sequence outside every ordinary test query.
        dict(price,security_id='OUTSIDE',revision_sequence='invalid',first_observed_at='invalid')])
    # Keep a scalar integer column while deferring the invalid sequence outside scope.
    rows['market_daily/2024-01.parquet'][-1]['revision_sequence']=None
    factor=row('A','2024-01-03',factor=2.)
    add('adjustment_factors',{'factor':'float64'},[
        row('A','2024-01-02',factor=1.),factor,
        dict(factor,revision_id='r2',revision_sequence=2,factor=4.,first_observed_at='2024-01-04T00:00:00+00:00'),
        row('B','2024-01-02',factor=0.),row('B','2024-01-03',factor=None)])
    add('public_evidence',{n:d for n,d in zip(
        ('target_domain','target_key','target_revision','public_at','document_sha256','raw_batch_id'),
        ('string','string','string','timestamp','string','string'))},[
        dict(target_domain='market_daily',target_key='{"security_id":"A","session":"2024-01-02"}',
            target_revision='r2',public_at='2024-01-02T10:00:00+00:00',document_sha256='a'*64,raw_batch_id='evidence1')],
        partition='history',keys=('target_domain','target_key','target_revision'))
    return domains,rows


def prepared(root,*,modify=None,row_group_size=1000,column_types=None):
    root=Path(root);domains,rows=facts()
    if modify: modify(domains,rows)
    root.mkdir(parents=True,exist_ok=True)
    for name,domain in domains.items():
        for part in domain['partitions']:
            path=root/part['uri'];path.parent.mkdir(parents=True,exist_ok=True)
            fixed=Path(__file__).parent/'fixtures/column_source_parquet_v1'/(name+'.parquet')
            if modify is None and column_types is None and row_group_size==1000:
                # Fix the tiny source bytes as well as the expected wire. Arrow
                # writer-version metadata otherwise changes the Snapshot ID.
                shutil.copyfile(fixed,path)
            else:
                table=pa.Table.from_pylist(rows[part['uri']])
                for field,dtype in (column_types or {}).get(name,{}).items():
                    table=table.set_column(table.column_names.index(field),field,
                        pa.array([r.get(field) for r in rows[part['uri']]],type=dtype))
                pq.write_table(table,path,compression='zstd',row_group_size=row_group_size)
            part.update(file_sha256=sha256(path.read_bytes()).hexdigest(),rows=len(rows[part['uri']]))
    body=dict(schema_version='local_data_v1',parent_snapshot=None,domains=domains,build_context={'synthetic':True})
    snapshot='s_'+_json_digest(body)
    (root/'snapshots').mkdir(exist_ok=True)
    (root/'snapshots'/(snapshot+'.json')).write_bytes(_json_bytes(dict(body,snapshot_id=snapshot)))
    return Data(root,cache_bytes=1048576),snapshot


def query(domain='market_daily',*,cutoff=LATE,fields=None,sessions=('2024-01-03','2024-01-02','2024-01-05'),
          symbols=('B','A','C'),policy='operational_pit_v1',purpose='decision_facts'):
    return QuerySpec(domain,fields or (('open','close') if domain=='market_daily' else ('factor',)),
        symbols,sessions,policy,{s:cutoff for s in sessions},purpose=purpose)


def requests():
    out=[]
    for cutoff in (LATE,EARLY):
        for policy in ('operational_pit_v1','market_pit_safe_v1','best_effort_vendor_v1'):
            for domain in ('market_daily','adjustment_factors'):
                out.append(query(domain,cutoff=cutoff,policy=policy))
    q=query(cutoff=EARLY,policy='bootstrap_hybrid_v1')
    out.append(replace(q,policy_by_session={s:('best_effort_vendor_v1' if i%2 else 'operational_pit_v1')
        for i,s in enumerate(q.sessions)}))
    out.append(replace(query(),cutoff_by_session={'2024-01-03':EARLY,'2024-01-02':LATE,'2024-01-05':EARLY}))
    return out


def adjustment_requests():
    out=[]
    for cutoff in (LATE,EARLY):
        for anchor in ('2024-01-03','2024-01-02'):
            p=query(cutoff=cutoff,sessions=('2024-01-02','2024-01-03'))
            f=query('adjustment_factors',cutoff=cutoff,sessions=p.sessions,symbols=tuple(reversed(p.symbols)))
            out.append((p,f,anchor))
    return out
