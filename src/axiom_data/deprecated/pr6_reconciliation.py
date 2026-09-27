"""Compatibility import; implementation lives in ``financial_reconciliation``."""
import sys
from axiom_data import financial_reconciliation as _implementation

sys.modules[__name__] = _implementation
