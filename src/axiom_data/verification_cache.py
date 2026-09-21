"""Transient memoization within one writer-controlled candidate build."""
from contextvars import ContextVar
from functools import wraps
from pathlib import Path
from contextlib import contextmanager
import stat as stat_mode

_active = ContextVar('axiom_candidate_verification', default=None)
_observed_paths = ContextVar('axiom_validation_paths', default=None)


def file_state(path):
    try:
        stat = path.lstat()
        # Creating an unrelated child does not replace an ancestor. Its inode
        # and mode still catch directory/symlink replacement; files retain the
        # full change stamp so in-place edits invalidate verification.
        if stat_mode.S_ISDIR(stat.st_mode):
            return stat.st_dev, stat.st_ino, stat.st_mode
        return stat.st_dev, stat.st_ino, stat.st_mode, stat.st_size, stat.st_ctime_ns
    except FileNotFoundError:
        return None


def observe_validation_path(root, path):
    observed = _observed_paths.get()
    if observed is None:
        return
    for component in (path, *path.parents):
        if component not in observed:
            observed[component] = file_state(component)
        # Match _safe_path's complete lexical ancestor walk, including parents
        # above data_root whose replacement can redirect otherwise unchanged files.
        if component == Path(component.anchor):
            break


@contextmanager
def validation_paths():
    observed = {}
    token = _observed_paths.set(observed)
    try:
        yield observed
    finally:
        _observed_paths.reset(token)


def candidate_verification(function):
    """Scope verified nodes to this invocation; never persist or share roots."""
    @wraps(function)
    def wrapped(data_root, *args, **kwargs):
        root=Path(data_root)
        token=_active.set((root,({},{}),set()))
        try:
            return function(data_root,*args,**kwargs)
        finally:
            _active.reset(token)
    return wrapped


def closure_cache(root):
    current=_active.get()
    return current[1] if current is not None and current[0]==Path(root) else ({},{})


def current_source_cache(root):
    """Current-policy checks are reused only within this candidate invocation."""
    current = _active.get()
    return current[2] if current is not None and current[0] == Path(root) else set()
