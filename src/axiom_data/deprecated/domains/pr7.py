"""Compatibility import; implementation lives in ``domains.events``."""
import sys
from axiom_data.domains import events as _implementation

sys.modules[__name__] = _implementation
