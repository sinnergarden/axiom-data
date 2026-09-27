"""REPRODUCTION_EVIDENCE: read-only sequential real financial projection baseline."""
import importlib.util
import json
import os
from pathlib import Path
import socket
import sys
import time
from collections import defaultdict
from contextlib import ExitStack
from unittest.mock import patch

REPO=Path(__file__).resolve().parents[2]
OUT=Path(sys.argv[1]).resolve()
spec=importlib.util.spec_from_file_location('measurement',REPO/'reports/view-execution-performance/profile_real.py')
measurement=importlib.util.module_from_spec(spec);spec.loader.exec_module(measurement)
from axiom_data import artifacts, pr6_views, pr6_coverage
from axiom_data.consumption import SnapshotReader
from axiom_data.view_validation import ViewValidationSession


def main():
 OUT.mkdir(exist_ok=True)
 if (OUT/'result.json').exists():raise RuntimeError('existing terminal result; validate before reuse')
 sys.addaudithook(measurement.audit)
 socket.socket.connect=lambda *a,**k:(_ for _ in ()).throw(RuntimeError('network forbidden'))
 plan=json.loads((measurement.ROOT/'operations/v1-full-bootstrap-20260913-r1/required-views.json').read_bytes())
 specs={v['config']['symbols'][0]:v['config'] for v in plan.values() if v['kind']=='pr6_fact'}
 symbols=['688981.SH']+sorted(set(specs)-{'688981.SH'})
 configs=[dict(specs[s],start_session=specs[s]['end_session']) for s in symbols]
 result={'status':'RUNNING','base_commit':'65e815350f5b28f8ec9d6ed381774a9a6dde0b16',
         'snapshot':measurement.SNAPSHOT,'pid':os.getpid(),'configs':[],'phases':{},'views':[],'milestones':{},'rejections':[]}
 calls=defaultdict(lambda:dict(calls=0,seconds=0.0))
 def timed(name,fn):
  def invoke(*args,**kwargs):
   start=time.perf_counter()
   try:return fn(*args,**kwargs)
   finally:
    calls[name]['calls']+=1;calls[name]['seconds']+=time.perf_counter()-start
  return invoke
 def phase(name,fn):
  measurement.save(OUT/'progress.json',dict(stage=name,pid=os.getpid()))
  print(json.dumps(dict(stage=name,pid=os.getpid())),flush=True)
  calls.clear();value,metrics=measurement.measured(fn)
  metrics['inclusive_function_profile']=dict(calls)
  result['phases'][name]=metrics;measurement.save(OUT/'partial.json',result)
  return value
 started=time.perf_counter()
 with ExitStack() as stack:
  for module,names in [(pr6_coverage,['admit_view','_admission_inputs','select_financial_revisions','financial_ambiguities','membership_coverage']),
                       (pr6_views,['_valuation_at','select_financial_revisions','financial_derived']),
                       (SnapshotReader,['facts','trading_calendar','members','industry_facts'])]:
   for name in names:stack.enter_context(patch.object(module,name,timed(module.__name__+'.'+name,getattr(module,name))))
  session=ViewValidationSession(measurement.ROOT,measurement.SNAPSHOT)
  context=session.inputs('pr6_fact',configs[0]);reader=phase('initialization',context.__enter__)
  try:
   for config in configs:
    index=len(result['views'])+1
    scope={k:v for k,v in config.items() if k not in {'pit_policy','knowledge_cutoff'}}
    def attempt():
     try:return {'payload':pr6_views.project(reader,scope,config['pit_policy'],config['knowledge_cutoff'])}
     except artifacts.ArtifactError as exc:
      if str(exc)!='INSUFFICIENT_SCOPE: valuation date/security gap':raise
      return {'rejection':str(exc)}
    outcome=phase('security_'+str(index),attempt)
    if 'rejection' in outcome:
     result['rejections'].append(dict(config=config,error=outcome['rejection']))
     result['phases']['rejected_'+config['symbols'][0]]=result['phases'].pop('security_'+str(index))
     measurement.save(OUT/'partial.json',result)
     continue
    payload=outcome['payload'];result['configs'].append(config)
    content=artifacts._json_bytes(payload)
    result['views'].append(dict(symbol=config['symbols'][0],digest=artifacts._digest(content),bytes=len(content),wide_rows=len(payload['wide'])))
    del payload,content,outcome
    if index in {1,10,50}:
     phases=[v for k,v in result['phases'].items() if k.startswith('security_')]
     result['milestones'][str(index)]={key:sum(p.get(key,0) for p in phases) for key in ('seconds','domain_loads','partition_traversals','partition_bytes','rchar','read_bytes')}
    measurement.save(OUT/'partial.json',result)
    if len(result['views'])==50:break
   assert len(result['views'])==50,'insufficient successful sample'
  finally:phase('context_exit',lambda:context.__exit__(None,None,None))
 result.update(status='PASS',elapsed=time.perf_counter()-started)
 measurement.save(OUT/'result.json',result)
 print(json.dumps(dict(status='PASS',elapsed=result['elapsed'])),flush=True)

if __name__=='__main__':main()
