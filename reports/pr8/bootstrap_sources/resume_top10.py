import json
from pathlib import Path
from axiom_data.bootstrap_sources import collect_bootstrap_sources
from axiom_data.source_client import PacedSourceClient
from axiom_data.tushare import TushareCollector
RUN=Path('/home/liuming/workspace/axiom/data/operations/v1-full-bootstrap-20260910-r1')
root=RUN/'top10-recovery-root'
plan=json.loads((RUN/'source_plan.json').read_bytes())
plan['requests_by_domain']={'top_holders_reports':plan['requests_by_domain']['top_holders_reports']}
client=PacedSourceClient(TushareCollector(root)._client())
result=collect_bootstrap_sources(root,run_id='v1-full-20260910-r1-top_holders_reports',plan=plan,domains=['top_holders_reports'],client=client)
print(json.dumps({k:v for k,v in result.items() if k!='domains'}),flush=True)
