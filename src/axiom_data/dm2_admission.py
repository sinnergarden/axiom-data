"""Compatibility import; implementation lives in ``consumer_admission``."""
import sys
from axiom_data import consumer_admission as _implementation

sys.modules[__name__] = _implementation
