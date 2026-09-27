"""Resolve the frozen PR4 package against actual PR5/6 public contracts."""
import argparse
import hashlib
import json
from pathlib import Path
from axiom_data.contracts import load_contract

PR5_FIELDS = {
    **{'market.'+leaf: ('market_daily', field) for leaf,field in {
        'open':'open','high':'high','low':'low','close':'close','session':'session',
        'volume':'volume_shares','amount':'amount_cny','circ_mv':'circulating_market_cap_cny',
        'total_mv':'total_market_cap_cny','turnover_rate':'turnover_rate'}.items()},
    'market.high_limit':('price_limits','upper_limit'),
    'market.low_limit':('price_limits','lower_limit'),
    'market.paused':('security_status','status'),
    'adjustment.factor':('adjustment_factors','factor'),
    'benchmark.close':('benchmark_daily','close'),
    'shares.float':('security_capital','circulating_shares'),
    'shares.total':('security_capital','total_shares'),
    'adjusted_price.anchored_ohlc':('adjusted_price','open/high/low/close'),
}

def resolve(package, repo):
    manifest_bytes=(package/'package_manifest.json').read_bytes()
    prior=json.loads((repo/'src/axiom_data/scope/pr6_scope.v1.json').read_text())
    manifest=json.loads(manifest_bytes)
    for entry in manifest['files']:
        data=(package/entry['path']).read_bytes()
        if len(data)!=entry['size'] or hashlib.sha256(data).hexdigest()!=entry['sha256']:
            raise ValueError('authority bytes mismatch: '+entry['path'])
    if hashlib.sha256(manifest_bytes).hexdigest()!='3fbeea59662590614fe0aa9d2d3b907929b1e3f1656d3e6ae9417e13f56f859f':
        raise ValueError('authority manifest mismatch')
    mappings=json.loads((package/'leaf_to_migration_slice.json').read_text())['mappings']
    leaves={r['leaf_id']:r for r in json.loads((package/'data_leaf_manifest.json').read_text())['rows']}
    features=json.loads((package/'feature_dependency_closure.json').read_text())['features']
    all_ids={r['leaf_id'] for r in mappings}
    pr5=set(PR5_FIELDS);pr6={r['leaf_id'] for r in prior['required_leaves']}
    pr7=all_ids-pr5-pr6
    assert len(all_ids)==56 and len(pr5)==18 and len(pr6)==23 and len(pr7)==15
    assert not pr5&pr6 and not pr5&pr7 and not pr6&pr7
    assert pr5|pr6|pr7==all_ids
    assert pr7=={r['leaf_id'] for r in prior['deferred_pr7_leaves']}
    refs=json.loads((repo/'reports/pr5/run_manifest.json').read_text())['artifact_refs']
    baseline={}
    for leaf,(domain,field) in sorted(PR5_FIELDS.items()):
        if domain=='adjusted_price':
            ref=refs['adjusted_price_view'];contract='adjusted_price_view.v1'
        else:
            contract=domain+'.v1';c=load_contract(contract)
            assert field in {f['name'] for f in c['fields']}
            ref=refs['domain_commits'][domain]
        baseline[leaf]={'domain':domain,'field':field,'contract':contract,'artifact':ref}
    remaining=[]
    for mapping in mappings:
        leaf=mapping['leaf_id']
        if leaf not in pr7:continue
        consumers=[f for f in features if leaf in f['data_leaf_ids']]
        remaining.append(dict(mapping,semantics=leaves[leaf],
            consumer_features=[f['feature_name'] for f in consumers],
            consumer_groups=sorted({s for f in consumers for s in f['source_surfaces']})))
    return {'schema_version':'pr7_scope.v1','authority_manifest_sha256':hashlib.sha256(manifest_bytes).hexdigest(),
        'validated_authority_files':len(manifest['files']), 'partition':{'PR5':sorted(pr5),'PR6':sorted(pr6),'PR7':sorted(pr7)},
        'partition_validation':{'total':56,'pairwise_intersections':[]},'pr5_public_evidence':baseline,
        'remaining':remaining,'stable_derived_requirements':[r for r in remaining if r['artifact_kind']=='stable_derived'],
        'feature_dependencies':[{'feature':f['feature_name'],'leaves':f['data_leaf_ids'],'closure_status':f['closure_status']} for f in features]}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--package',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();result=resolve(a.package,Path(__file__).resolve().parents[1])
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'partition':{k:len(v) for k,v in result['partition'].items()},'validated_files':result['validated_authority_files'],'feature_count':len(result['feature_dependencies'])}))
