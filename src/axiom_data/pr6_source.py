"""Compatibility import; implementation lives in ``fundamentals_source``."""
import sys
from axiom_data import fundamentals_source as _implementation

sys.modules[__name__] = _implementation
