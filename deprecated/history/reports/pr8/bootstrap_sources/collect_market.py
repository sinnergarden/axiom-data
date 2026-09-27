"""Frozen market Raw stage; checkpointed in bounded public collection runs."""
import fcntl
import json
from pathlib import Path
import time
from axiom_data.operations import collect_requests,_save
from axiom_data import load_raw_batch

ROOT=Path('/home/liuming/workspace/axiom/data')
RUN=ROOT/'operations/v1-full-bootstrap-20260910-r1'
scope=json.loads((ROOT/'operations/industry-qualification-20260909-r1/scope.json').read_bytes())
lock=(RUN/'market.lock').open('w')
fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
progress={'run_id':RUN.name,'stage':'MARKET_RAW_COLLECTION','total_requests':len(scope['symbols'])*5,
          'validated_requests':0,'validated_rows':0,'ready_for_consumption':False,'status':'RUNNING'}
started=time.monotonic()
for offset in range(0,len(scope['symbols']),20):
    requests=[]
    for symbol in scope['symbols'][offset:offset+20]:
        for endpoint in ('daily','adj_factor','daily_basic','stk_limit','suspend_d'):
            requests.append(dict(collector='market',domain='market_daily',endpoint=endpoint,
                params={'ts_code':symbol,'start_date':scope['start'].replace('-',''),'end_date':scope['end'].replace('-','')},
                economic_scope={'start':scope['start'].replace('-',''),'end':scope['end'].replace('-','')},availability_policy='session_close'))
    identity=f'v1-full-20260910-r1-market-{offset:04d}'
    state=collect_requests(ROOT,run_id=identity,requests=requests)
    for key,raw_id in state['completed'].items():
        raw=load_raw_batch(ROOT,raw_id)
        assert raw.manifest['status']=='success'
        rows=json.loads(raw.payload)
        assert len(rows)<5000,'potential truncated security history'
        progress['validated_requests']+=1;progress['validated_rows']+=len(rows)
    progress.update(batch_run_id=identity,elapsed_seconds=time.monotonic()-started)
    if state['status']!='COMPLETE':
        progress.update(status='FAILED',failed=state['failed'],pending_count=state['pending_count'])
        _save(RUN/'market_progress.json',progress)
        raise RuntimeError('market batch incomplete; frozen Raw retained; inspect exact batch')
    _save(RUN/'market_progress.json',progress)
    print(json.dumps(progress),flush=True)
progress.update(status='COMPLETE',elapsed_seconds=time.monotonic()-started)
assert progress['validated_requests']==progress['total_requests']
_save(RUN/'market_progress.json',progress)
