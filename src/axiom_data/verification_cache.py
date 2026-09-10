"""Transient memoization within one writer-controlled candidate build."""
from contextvars import ContextVar
from functools import wraps
from pathlib import Path

_active = ContextVar('axiom_candidate_verification', default=None)


def candidate_verification(function):
    """Scope verified nodes to this invocation; never persist or share roots."""
    @wraps(function)
    def wrapped(data_root, *args, **kwargs):
        root=Path(data_root)
        token=_active.set((root,({},{})))
        try:
            return function(data_root,*args,**kwargs)
        finally:
            _active.reset(token)
    return wrapped


def closure_cache(root):
    current=_active.get()
    return current[1] if current is not None and current[0]==Path(root) else ({},{})
