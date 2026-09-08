"""Single-writer operational boundary and read-only publication."""
from contextlib import contextmanager
from contextvars import ContextVar
import fcntl
import os
from pathlib import Path
from axiom_data.artifacts import ArtifactError, _ensure_directory, _layout, _safe_path

readonly_publication = ContextVar('axiom_readonly_publication', default=False)


def seal(directory):
    """Remove all write bits; digest and closure checks remain authoritative."""
    directory = Path(directory)
    for path in sorted(directory.rglob('*'), key=lambda p: len(p.parts), reverse=True):
        if path.is_symlink():
            raise ArtifactError('published closure must not contain symlinks')
        path.chmod(0o555 if path.is_dir() else 0o444)
    directory.chmod(0o555)


@contextmanager
def writer(data_root):
    layout = _layout(data_root)
    _ensure_directory(layout.root, layout.root/'locks')
    path = _safe_path(layout.root, layout.root/'locks'/'writer.lock')
    with open(path, 'a+b') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ArtifactError('another Data writer is active') from exc
        token = readonly_publication.set(True)
        try:
            yield
        finally:
            readonly_publication.reset(token)
            fcntl.flock(handle, fcntl.LOCK_UN)
