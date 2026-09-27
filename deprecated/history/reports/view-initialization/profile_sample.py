"""REPRODUCTION_EVIDENCE: bounded immutable Raw admission profile; no writes to data."""
import cProfile,json,pstats,time
from pathlib import Path
from axiom_data.artifacts import load_raw_batch
from axiom_data.source_coverage import observation
from axiom_data.layout import DataRootLayout
ROOT=Path('/var/lib/axiom-data')
OUT=Path(__file__).parent
snapshot='snapshot-1d69dc236a358f1627ae91080c33cf993b9e2acb55bbd2c2cb7a37129ce6b5a5'
manifest=json.loads((ROOT/'snapshots'/snapshot/'manifest.json').read_bytes())
profile=cProfile.Profile(); counts={}; start=time.perf_counter()
for domain in ('financial_events','valuation_daily','universe_membership','industry_membership','security_master','trading_calendar'):
 ref=manifest['domain_refs'][domain]['domain_commit_id']
 commit=json.loads((DataRootLayout(ROOT).domain_commits(domain)/ref/'manifest.json').read_bytes())
 refs=commit['ordered_raw_batch_refs']; counts[domain]={'total_raw_refs':len(refs),'sample':min(100,len(refs))}
 profile.enable()
 for rawref in refs[:100]:
  raw=load_raw_batch(ROOT,rawref['raw_batch_id'])
  if domain!='industry_membership': observation(raw,policy=commit['builder_config']['coverage_state_policy'])
  load_raw_batch(ROOT,rawref['raw_batch_id'])
 profile.disable()
profile.dump_stats(OUT/'sample.prof')
with (OUT/'sample-profile.txt').open('w') as f: pstats.Stats(profile,stream=f).sort_stats('cumulative').print_stats(60)
(OUT/'sample.json').write_text(json.dumps({'counts':counts,'elapsed':time.perf_counter()-start},indent=2))
print(json.dumps(counts))
