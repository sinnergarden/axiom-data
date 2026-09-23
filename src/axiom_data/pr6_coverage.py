"""Compatibility import; implementation lives in ``financial_coverage``."""
import sys
from axiom_data import financial_coverage as _implementation

sys.modules[__name__] = _implementation
