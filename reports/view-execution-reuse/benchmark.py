"""REPRODUCTION_EVIDENCE: isolated public View operation timing/count comparison.

Uses the immutable test fixture and test-only parent-link correction; no formal data writes.
Run from repository root with PYTHONPATH=src:tests python3 reports/view-execution-reuse/benchmark.py.
"""
import sys,time,json,copy
from collections import Counter
from contextlib import ExitStack
from unittest.mock import patch
sys.path[:0]=['src','tests']
import test_view_execution_reuse as fixture
from axiom_data import SnapshotReader
from axiom_data import views,consumption,financial_views,event_views
x=fixture.ViewExecutionReuseTest();x.setUp()
results=[]
try:
 for size in (1,2):
  plan={}
  for kind,spec in x.plan.items():
   for index,symbol in enumerate(['688981.SH','600036.SH'][:size]):
    item=copy.deepcopy(spec);item['config']['symbols']=[symbol];plan[kind+str(index)]=item
  first=x.run_views('baseline-'+str(size),plan=plan)
  assert first['status']=='VIEWS_BUILT',first
  if size==1:changed=x.changed_snapshot('benchmark_daily')
  candidates={k:dict(v,reuse_candidate=first['published_views'][k]) for k,v in plan.items()}
  for mode,p in [('reuse',candidates),('fresh',plan)]:
   reads=Counter();original=SnapshotReader.session_rows
   def read(reader,domain,*args,**kwargs):
    reads[domain]+=1;return original(reader,domain,*args,**kwargs)
   with ExitStack() as stack:
    stack.enter_context(patch.object(SnapshotReader,'session_rows',read))
    projections={}
    for module,name in [(views,'_build_adjusted_price_view'),(views,'_build_market_replay_view'),
         (consumption,'_build_qlib_view'),(financial_views,'project'),(event_views,'project')]:
     projections[module.__name__+'.'+name]=stack.enter_context(patch.object(module,name,wraps=getattr(module,name)))
    start=time.monotonic();result=x.run_views(mode+'-'+str(size),changed,p);elapsed=time.monotonic()-start
   assert result['status']=='VIEWS_BUILT',result
   results.append(dict(symbols=size,views=len(plan),mode=mode,seconds=elapsed,
       projection_calls={k:v.call_count for k,v in projections.items()},session_reads=dict(reads),
       reused=len(result.get('reused_views',[]))))
 print(json.dumps({'fixture':'isolated copy of pr7 real fixture; synthetic benchmark parent-link-only correction',
  'scale':'four sessions, one/two securities, five families; not production extrapolation',
  'results':results},indent=2))
finally:x.doCleanups()
