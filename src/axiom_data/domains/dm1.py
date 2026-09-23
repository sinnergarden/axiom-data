"""Compatibility import; implementation lives in ``domains.reference``."""
import sys
from axiom_data.domains import reference as _implementation

sys.modules[__name__] = _implementation
