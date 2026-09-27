"""Explicit locations for the inventoried, immutable test evidence roots."""
import os
from pathlib import Path


LEGACY_DATA_ROOT = Path('/home/liuming/workspace/axiom/data')
FORENSIC_ROOTS = frozenset({
    'pr3-market-slice-20260905-blocker-fix-v2',
    'pr3-market-slice-20260905-schema-v2-rerun',
    'pr5-d01-snapshot-coexistence-b1303edb',
    'pr5-dm1-market-reference-20260906-b1b6-r4',
    'pr6-empty-prefix-compat-20260908-r4',
    'pr6-pit-financial-20260908-r1',
    'pr6-review-20260908-r1',
    'pr7-dm2-20260909-r1',
})
SOURCE_QUALIFICATION_ROOT = 'pr8-source-qualification-20260913'


def fixture_root(recorded_root: str | Path) -> Path:
    """Relocate a known recorded path without selecting or modifying artifacts."""
    destination = Path(os.environ.get('AXIOM_TEST_FORENSIC_ROOT', '/var/lib/axiom-data/forensic'))
    if not destination.is_absolute() or '..' in destination.parts:
        raise ValueError('AXIOM_TEST_FORENSIC_ROOT must be an absolute path without traversal')
    recorded = Path(recorded_root)
    if not recorded.is_absolute() or '..' in recorded.parts:
        raise ValueError('fixture path must be absolute without traversal')
    if recorded == LEGACY_DATA_ROOT:
        return destination / SOURCE_QUALIFICATION_ROOT
    try:
        relative = recorded.relative_to(LEGACY_DATA_ROOT / 'forensic')
    except ValueError:
        raise ValueError(f'unknown fixture root: {recorded}') from None
    if not relative.parts or relative.parts[0] not in FORENSIC_ROOTS:
        raise ValueError(f'unknown fixture root: {recorded}')
    return destination / relative
