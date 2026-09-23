"""Compatibility import; implementation lives in ``event_reconciliation``."""
import sys
from axiom_data import event_reconciliation as _implementation

sys.modules[__name__] = _implementation
