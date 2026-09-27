"""Compatibility import; implementation lives in ``reference_reconciliation``."""
import sys
from axiom_data import reference_reconciliation as _implementation

sys.modules[__name__] = _implementation
