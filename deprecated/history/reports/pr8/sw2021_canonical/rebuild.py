"""Build and independently recover the complete industry/security closure offline."""
import json
import shutil
import socket
from pathlib import Path
from axiom_data import BuildApplication,load_raw_batch,validate_domain_commit_closure
from axiom_data.exchange_security import ExchangeSecurityBuilder
from axiom_data.pr6_source import Pr6Builder
from axiom_data.publication import writer

ROOT=Path('/home/liuming/workspace/axiom/data')
RUN=ROOT/'operations/sw2021-canonical-20260910-r1'
TARGET=Path('/tmp/axiom-sw2021-offline-20260910-r1')
security=json.loads((ROOT/'operations/exchange-security-20260909-r1/plan.json').read_bytes())
industry=json.loads((RUN/'build_plan.json').read_bytes())
for identity in security['raw_batch_ids']+industry['raw_batch_ids']:
    raw=load_raw_batch(ROOT,identity);target=TARGET/'raw/batches'/identity
    if target.exists():assert load_raw_batch(TARGET,identity).ref==raw.ref
    else:shutil.copytree(ROOT/'raw/batches'/identity,target)
def denied(*args,**kwargs):raise RuntimeError('network disabled during recovery')
socket.socket=denied;socket.create_connection=denied
def build(root):
    with writer(root):
        s=BuildApplication('security_master',ExchangeSecurityBuilder(root,builder_config=security['config'])).build(None,security['raw_batch_ids'],[],security['contract_version'])
        cfg=dict(industry['config'],storage_policy='domain_time_blocks.v1',no_change_policy='reuse_equal_state.v1')
        i=BuildApplication('industry_membership',Pr6Builder(root,'industry_membership',builder_config=cfg,dependency_commit_ids={'security_master':s.commit_id})).build(None,industry['raw_batch_ids'],[],'industry_membership.v3')
        checked=validate_domain_commit_closure(root,'industry_membership',i.commit_id)
        return dict(security_commit_id=s.commit_id,industry_commit_id=i.commit_id,logical_digest=checked.manifest['logical_content_digest'],rows=len(checked.rows))
source=build(ROOT);recovered=build(TARGET)
assert source==recovered,(source,recovered)
print(json.dumps(dict(status='PASS',source_root=str(ROOT),recovery_root=str(TARGET),network='denied',**source),indent=2))
