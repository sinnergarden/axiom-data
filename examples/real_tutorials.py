"""Execute the two authoritative notebooks and render their generated HTML.

The saved ipynb files are the sole teaching source. This runner never generates
or replaces prose/code cells, loads credentials or calls a supplier. Examples
read fixed local data and write only their disposable temporary roots.
"""
from pathlib import Path
import argparse
import hashlib
import json
import subprocess
import sys
import time
import nbformat
from nbclient import NotebookClient

REPO = Path(__file__).resolve().parents[1]

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--no-execute', action='store_true', help='Render retained outputs without claiming a new execution')
    parser.add_argument('--audience', choices=('researcher','developer'), help='Run only the selected saved notebook')
    args = parser.parse_args()
    report = {'schema_version':'axiom_tutorial_execution_v2',
              'supplier_requests':0, 'formal_data_root_writes':False,
              'python_version':sys.version.split()[0], 'notebooks':[]}
    for audience in ((args.audience,) if args.audience else ('researcher','developer')):
        path = REPO/'notebooks'/f'{audience}_tutorial.ipynb'
        notebook = nbformat.read(path,as_version=4)
        started = time.perf_counter()
        if not args.no_execute:
            NotebookClient(notebook,timeout=240,kernel_name='axiom-data',
                resources={'metadata':{'path':str(REPO.parent)}}).execute()
            # Qlib's runtime diagnostics are logs, not teaching results.
            for cell in notebook.cells:
                if cell.cell_type=='code':
                    cell.outputs=[o for o in cell.outputs if not (o.output_type=='stream' and o.name=='stderr')]
            nbformat.validate(notebook)
            nbformat.write(notebook,path)
        subprocess.run([sys.executable,str(REPO/'examples/render_notebook.py'),
                        '--notebook',str(path)],check=True)
        errors=sum(o.output_type=='error' for c in notebook.cells if c.cell_type=='code' for o in c.outputs)
        if errors:raise RuntimeError(f'{audience} retained {errors} error outputs')
        headings=sum(c.cell_type=='markdown' and c.source.startswith('## ') for c in notebook.cells)
        assert path.with_suffix('.html').read_text().count('href="#section-')==headings
        report['notebooks'].append({'audience':audience,
            'path':str(path.relative_to(REPO)), 'html':str(path.with_suffix('.html').relative_to(REPO)),
            'executed':not args.no_execute, 'code_cells':sum(c.cell_type=='code' for c in notebook.cells),
            'errors':errors, 'chapters':headings,
            'elapsed_seconds':round(time.perf_counter()-started,3),
            'notebook_sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
    target=REPO/'docs/real-tutorial-validation.json'
    if not args.no_execute:
        if args.audience and target.exists():
            previous=json.loads(target.read_text())
            report['notebooks']=[n for n in previous['notebooks'] if n['audience']!=args.audience]+report['notebooks']
        target.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
