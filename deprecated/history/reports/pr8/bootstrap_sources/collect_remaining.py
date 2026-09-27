"""Continue independent V1 source domains using the frozen explicit plan."""
import json
from pathlib import Path
from axiom_data.bootstrap_sources import collect_bootstrap_sources
from axiom_data.operations import _save
RUN=Path('/home/liuming/workspace/axiom/data/operations/v1-full-bootstrap-20260910-r1')
ROOT=RUN/'source-root'
ROOT.mkdir(exist_ok=True)
plan=json.loads((RUN/'source_plan.json').read_bytes())
domains=['benchmark_daily','universe_membership','holder_count_events','top_holders_reports',
         'margin_daily','moneyflow_daily','forecast_observations','financial_events',
         'security_status','price_limits','adjustment_factors','security_capital','corporate_actions','valuation_daily']
progress={'stage':'REMAINING_SOURCE_COLLECTION','status':'RUNNING','domains':{},'ready_for_consumption':False}
for domain in domains:
    local=dict(plan,requests_by_domain={domain:plan['requests_by_domain'][domain]})
    result=collect_bootstrap_sources(ROOT,run_id='v1-full-20260910-r1-'+domain,plan=local,domains=[domain])
    progress['domains'][domain]={k:result[k] for k in ['status','validated_requests','validated_rows']}
    if 'failure' in result:progress['domains'][domain]['failure']=result['failure']
    _save(RUN/'remaining_progress.json',progress)
    print(json.dumps(progress),flush=True)
progress['status']='COMPLETE' if all(r['status']=='COMPLETE' for r in progress['domains'].values()) else 'FAILED'
_save(RUN/'remaining_progress.json',progress)
