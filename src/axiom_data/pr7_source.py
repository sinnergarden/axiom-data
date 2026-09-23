"""Compatibility import; implementation lives in ``event_source``."""
import sys
from axiom_data import event_source as _implementation

sys.modules[__name__] = _implementation
