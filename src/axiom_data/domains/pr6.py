"""Compatibility import; implementation lives in ``domains.fundamentals``."""
import sys
from axiom_data.domains import fundamentals as _implementation

sys.modules[__name__] = _implementation
