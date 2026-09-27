import json
from pathlib import Path
from axiom_data.bootstrap_sources import collect_bootstrap_sources
from axiom_data.source_client import PacedSourceClient
from axiom_data.tushare import TushareCollector
RUN=Path('/home/liuming/workspace/axiom/data/operations/v1-full-bootstrap-20260910-r1')
root=RUN/'holder-v2-root';root.mkdir(exist_ok=True)
plan=json.loads((RUN/'source_plan.json').read_bytes())
requests=plan['requests_by_domain']['holder_count_events']
for spec in requests:spec['collector']='pr7_holder'
plan['requests_by_domain']={'holder_count_events':requests}
client=PacedSourceClient(TushareCollector(root)._client())
result=collect_bootstrap_sources(root,run_id='v1-full-20260910-holder-v2',plan=plan,domains=['holder_count_events'],client=client)
print(json.dumps({k:v for k,v in result.items() if k!='domains'}),flush=True)
