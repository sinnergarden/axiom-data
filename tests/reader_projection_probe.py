"""REPRODUCTION_EVIDENCE: bounded query-phase IO comparison on immutable inputs.

Run manually with explicit root/Snapshot/month/security. Uses Reader.market_daily
and existing complete-partition validation; does not initialize the full Snapshot
closure, publish artifacts, or contact suppliers. Not an operational entrypoint.
"""
import argparse
from collections import OrderedDict
import json
from pathlib import Path
import time
from types import SimpleNamespace
from unittest.mock import patch

from axiom_data import SnapshotReader
from axiom_data.artifacts import (_load_manifest, _validate_manifest_identity,
    _digest, _validate_domain_rows)
from axiom_data.layout import DataRootLayout
from axiom_data.partition_rows import PartitionRows


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--snapshot',required=True)
    parser.add_argument('--month',required=True)
    parser.add_argument('--symbol',required=True)
    args=parser.parse_args()
    root=args.root
    snapshot,_=_load_manifest(root,root/'snapshots'/args.snapshot,artifact_type='data_snapshot',
        schema_version=('data_snapshot.v1','data_snapshot.v2','data_snapshot.v3','data_snapshot.v4'),
        identity_field='snapshot_id',identity=args.snapshot)
    _validate_manifest_identity(snapshot,'snapshot_id','snapshot',args.snapshot)
    refs=snapshot['domain_refs']
    ref=refs['market_daily']
    identity=ref['domain_commit_id'];target=root/'canonical/market_daily/commits'/identity
    manifest,_=_load_manifest(root,target,artifact_type='domain_commit',schema_version='domain_commit.v2',
        identity_field='domain_commit_id',identity=identity)
    _validate_manifest_identity(manifest,'domain_commit_id','market_daily',identity)
    for field in ('identity_digest','logical_content_digest','contract_digest','contract_version'):
        assert manifest[field]==ref[field]
    contract_bytes=(target/manifest['contract_path']).read_bytes()
    assert _digest(contract_bytes)==manifest['contract_digest']
    contract=json.loads(contract_bytes)
    entry=next(e for e in manifest['partitions'] if e['key']==args.month)
    assert entry['bytes']<=256*1024*1024, 'probe budget: partition exceeds 256 MiB'
    bounded=dict(manifest,partitions=[entry])
    parts=PartitionRows(DataRootLayout(root),'market_daily',bounded,contract)
    # Validate the entire chosen partition, including all other securities.
    complete=tuple(parts)
    _validate_domain_rows('market_daily',complete,contract=contract)
    days=sorted({r['session'] for r in complete if r['symbol']==args.symbol})[:10]
    assert len(days)>=2, 'need at least two real sessions for repeated-read comparison'
    del complete

    def run(optimized):
        reader=SnapshotReader.__new__(SnapshotReader)
        reader.commits={'market_daily':SimpleNamespace(rows=parts,contract=contract)}
        reader._security_projection=OrderedDict();reader._security_projection_bytes=0
        if not optimized:
            # Exact former _session_rows behavior for this session-partition case.
            reader._session_rows=lambda domain,start,end,symbols=None:parts.sessions(start,end)
        outputs=[];calls=[];started=time.perf_counter()
        with patch.object(parts,'_rows',wraps=parts._rows) as opened:
            for day in days:
                before=time.perf_counter()
                outputs.append(reader.market_daily([args.symbol],day,day))
                calls.append(time.perf_counter()-before)
            reads=opened.call_count
        return outputs,dict(seconds=time.perf_counter()-started,first_query_seconds=calls[0],
            partition_reads=reads,partition_bytes=reads*entry['bytes'],
            digest_plus_parse_bytes=2*reads*entry['bytes'],cache_payload_bytes=reader._security_projection_bytes)

    old,old_stats=run(False);new,new_stats=run(True)
    assert old==new, 'Reader values/provenance/order differ'
    print(json.dumps(dict(snapshot=args.snapshot,domain_commit=identity,partition=entry,
        symbol=args.symbol,sessions=days,logical_equal=True,rows=sum(map(len,new)),
        baseline=old_stats,optimized=new_stats,
        measurement='query phase; initially empty application cache; OS page cache not flushed; bytes are logical file reads',
        validation='complete selected partition validated; full Snapshot closure NOT RUN'),indent=2))


if __name__=='__main__':
    main()
