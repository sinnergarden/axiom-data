"""Compatibility import; implementation lives in ``event_views``."""
import sys
from axiom_data import event_views as _implementation

sys.modules[__name__] = _implementation
