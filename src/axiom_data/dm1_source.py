"""Compatibility import; implementation lives in ``reference_source``."""
import sys
from axiom_data import reference_source as _implementation

sys.modules[__name__] = _implementation
