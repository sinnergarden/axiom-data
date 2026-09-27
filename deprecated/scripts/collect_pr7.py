"""Collect a bounded PR7 source run with a durable per-request registry."""
import argparse
import json
from pathlib import Path
from axiom_data.pr7_source import Pr7Collector,load_pr7_source_profile
from axiom_data.artifacts import load_raw_batch

def collect(root):
    root.mkdir(parents=True,exist_ok=True);registry_path=root/'collection_registry.json'
    registry=json.loads(registry_path.read_text()) if registry_path.exists() else {'schema_version':'pr7_collection.v1','raw_batches':{}}
    collector=Pr7Collector(root/'raw-root')
    for symbol in ('600036.SH','688981.SH','000401.SZ'):
        for endpoint in load_pr7_source_profile()['endpoints']:
            start,end=('20250610','20250613') if endpoint in {'margin_detail','moneyflow'} else ('20240101','20250613')
            params={'ts_code':symbol,'start_date':start,'end_date':end}
            key=endpoint+':'+symbol+':'+start+':'+end
            if key in registry['raw_batches']:
                raw=load_raw_batch(root/'raw-root',registry['raw_batches'][key]);assert raw.manifest['request']['params']==params
                print(key,'validated existing',raw.manifest['summary']['rows'],flush=True);continue
            ref=collector.collect(endpoint,params)
            registry['raw_batches'][key]=ref.raw_batch_id
            temporary=registry_path.with_suffix('.tmp');temporary.write_text(json.dumps(registry,indent=2)+'\n');temporary.replace(registry_path)
            raw=load_raw_batch(root/'raw-root',ref.raw_batch_id)
            print(key,raw.manifest['summary']['rows'],ref.raw_batch_id,flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run-root',type=Path,required=True);a=p.parse_args();collect(a.run_root)
