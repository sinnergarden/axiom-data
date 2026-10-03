"""Small, offline setup shared by the two saved notebooks.

Reads the explicitly pinned teaching data and saved acceptance files. No token,
provider client, strategy, peer repository or Qlib is imported here. All writes
in the notebooks use TEMP; formal data roots remain read-only. The paths can be
changed through the documented AXIOM_* environment variables.
"""
from pathlib import Path
import os, sys, json, tempfile, hashlib, time
sys.dont_write_bytecode = True
from dataclasses import replace
from decimal import Decimal
from datetime import datetime, timezone
import pandas as pd
from IPython.display import display, HTML

# AXIOM_WORKSPACE / AXIOM_TUTORIAL_DATA_ROOT allow another machine's paths.
WORKSPACE = Path(os.environ.get('AXIOM_WORKSPACE', Path.cwd())).resolve()
if not (WORKSPACE / 'axiom-data').exists():
    WORKSPACE = next(p for p in Path.cwd().parents if (p / 'axiom-data').exists())
# A retained installed wheel can be selected while executing/editing notebooks.
# Its builder identity is verified independently of the mutable teaching files.
PACKAGE_PATH = os.environ.get('AXIOM_TUTORIAL_DATA_PACKAGE')
sys.path.insert(0, PACKAGE_PATH or str(WORKSPACE / 'axiom-data' / 'src'))
from axiom_data import (Data, QuerySpec, EventQuery, adjust_prices,
                        import_bundle, single_quarter, ttm)
from axiom_data.sources import _rows
from axiom_data.verification import audit_snapshot

ROOT = Path(os.environ.get('AXIOM_TUTORIAL_DATA_ROOT', WORKSPACE / 'data/csi1800_one_year_202509_202608'))
REPO = WORKSPACE / 'axiom-data'
UNIFIED_ROOT = Path(os.environ.get('AXIOM_UNIFIED_DATA_ROOT',
    WORKSPACE / 'data/csi1800_tushare_only_v4_202606_202608'))
UNIFIED_SNAPSHOT = os.environ.get('AXIOM_UNIFIED_SNAPSHOT',
    's_a0db6028118997d93b9f70dff852b701b030b152a30b2e8d325c08ad64931049')
UNIFIED = Data(UNIFIED_ROOT)
UNIFIED_CURRENT_BEFORE = ((UNIFIED_ROOT/'current.json').read_bytes()
    if (UNIFIED_ROOT/'current.json').exists() else None)
RUN = json.loads((REPO / 'docs/delivery-validation.json').read_text())
SCOPE = json.loads(ROOT.with_suffix('.scope.json').read_text())
SNAPSHOT = RUN['snapshot_id']  # pinned acceptance version; not a floating current
DATA = Data(ROOT)
CURRENT_BEFORE = DATA.resolve('current')
MANIFEST = DATA.store.load_snapshot(SNAPSHOT)
AUDIT = json.loads(ROOT.with_suffix('.audit.json').read_text())
IDS = SCOPE['identity_map']
SYMBOL = IDS['000001.SZ']
CALENDAR = sorted({str(r['session']) for p in MANIFEST['domains']['trading_calendar']['partitions']
                   for r in DATA.store.read_partition(p).to_pylist() if r['is_open']})
DAYS = tuple(CALENDAR[-6:])
TEMPDIR = tempfile.TemporaryDirectory(prefix='axiom-real-tutorial-')
TEMP = Path(TEMPDIR.name)

def query(fields=('close',), symbols=(SYMBOL,), sessions=DAYS, *, domain='market_daily',
          policy='best_effort_vendor_v1', common_cutoff=None, purpose='decision_facts'):
    return QuerySpec(domain, tuple(fields), tuple(symbols), tuple(sessions), policy,
        {d: common_cutoff or d+'T20:30:00+08:00' for d in sessions}, purpose=purpose)

def measure_tree(path):
    files = [p for p in Path(path).rglob('*') if p.is_file()]
    return {'files': len(files), 'bytes': sum(p.stat().st_size for p in files),
            'MiB': round(sum(p.stat().st_size for p in files) / 2**20, 2)}
