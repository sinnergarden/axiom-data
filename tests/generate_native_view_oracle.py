"""Explicit golden generation using frozen production sources, never in tests.

Run with PYTHONPATH pointing to Data 8bebf147 and Engine 633fd7a1, plus tests.
Uses only the deterministic in-memory synthetic fixture, no actual Data root.
"""
import hashlib
import json
from pathlib import Path
import subprocess

import axiom_data
from axiom_engine.core.contracts import canonical
from axiom_engine.runtime.stock_evidence import native_ref
from native_view_fixture import data,reads


root=Path(axiom_data.__file__).resolve().parents[2]
expected='8bebf14742c36277a7ba4d3b913ac7c1bdca102d'
assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()==expected
assert not subprocess.check_output(['git','diff','--','src/axiom_data'],cwd=root)
items=[];d=data()
for request in reads():
    wire=getattr(d,request['method'])(snapshot='s1',query=request['query']).to_json()
    # Original Engine canonical/native_ref, not the new storage composer.
    raw=canonical(wire).encode('utf-8')
    items.append({'method':request['method'],'wire':wire,'native_ref':native_ref(wire)})
out={'oracle_commit':expected,'canonical_source_commit':'633fd7a1bd585115a247632488a2582e569bb4d6',
    'canonical_algorithm':'axiom_engine.core.contracts.canonical / stock_evidence.native_ref',
    'oracle_source_files':{str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (root/'src/axiom_data').glob('*.py') if p.name in ('reader.py','local_states.py','protocols.py','event_reader.py')},
    'fixture_sha256':hashlib.sha256(Path('tests/native_view_fixture.py').read_bytes()).hexdigest(),
    'cases':items}
p=Path('tests/fixtures/native_view_oracle_v1.json')
p.write_text(json.dumps(out,sort_keys=True,ensure_ascii=False,indent=2)+'\n')
print({'cases':len(items),'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
