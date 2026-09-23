"""Compatibility import; implementation lives in ``financial_views``."""
import sys
from axiom_data import financial_views as _implementation

sys.modules[__name__] = _implementation
