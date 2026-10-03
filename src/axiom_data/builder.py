"""Freeze the actual local builder; no source registry or dirty-tree archive."""
from __future__ import annotations

import base64
from copy import deepcopy
from hashlib import sha256
from importlib import metadata
import json
from pathlib import Path
import platform
import subprocess
from urllib.parse import unquote, urlsplit, urlunsplit

from .protocols import ConflictError, DataError


def _git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], stderr=subprocess.DEVNULL,
                                   text=True).strip()


def freeze_builder():
    """Read and verify a clean source commit or an intact installed wheel.

    The wheel must retain its local archive (pip direct_url + SHA256), or have
    an explicit archive URL and digest. Locks ship unchanged with the wheel.
    Dirty/unidentified source fails before building; commit it or install a
    retained wheel. This reads files only and never contacts the archive URL.
    """
    package = Path(__file__).resolve().parent
    root = package.parents[1]
    locks = {}
    if (root / "pyproject.toml").is_file() and (root / "src/axiom_data").resolve() == package:
        try:
            commit = _git(root, "rev-parse", "HEAD")
            if _git(root, "status", "--porcelain", "--untracked-files=all"):
                raise DataError("builder source is dirty; commit it or install a retained wheel")
            origin = _git(root, "config", "--get", "remote.origin.url")
        except subprocess.CalledProcessError as exc:
            raise DataError("builder source needs a recoverable Git commit") from exc
        parsed = urlsplit(origin)
        if parsed.scheme in {"http", "https"}:
            origin = urlunsplit((parsed.scheme, parsed.hostname or "", parsed.path, "", ""))
        source = {"kind": "git_commit", "commit": commit, "repository": origin}
        for name in ("requirements-local.lock", "requirements-qlib.lock"):
            if (root/name).is_file():
                locks[name] = sha256((root/name).read_bytes()).hexdigest()
    else:
        try:
            dist = metadata.distribution("axiom-data")
            if Path(dist.locate_file("axiom_data/__init__.py")).resolve() != package/"__init__.py":
                dist = next((d for d in metadata.distributions()
                             if d.metadata.get("Name", "").lower().replace("_", "-") == "axiom-data"
                             and Path(d.locate_file("axiom_data/__init__.py")).resolve() == package/"__init__.py"), None)
            if dist is None:
                raise DataError("loaded builder differs from the installed distribution")
            direct = json.loads(dist.read_text("direct_url.json") or "{}")
            archive = direct.get("archive_info") or {}
            digest = (archive.get("hashes") or {}).get("sha256")
            url = direct.get("url")
            if not digest or not url:
                raise DataError("installed builder needs its wheel origin and SHA256")
            parsed = urlsplit(url)
            if parsed.scheme == "file":
                wheel = Path(unquote(parsed.path))
                if not wheel.is_file() or sha256(wheel.read_bytes()).hexdigest() != digest:
                    raise DataError("retain the original installed wheel for builder recovery")
            tracked = {}
            for entry in dist.files or ():
                name = str(entry)
                if name.startswith("axiom_data/") and name.endswith((".py", ".json")) or name.endswith(("requirements-local.lock", "requirements-qlib.lock")):
                    path = Path(dist.locate_file(entry))
                    actual = sha256(path.read_bytes()).digest()
                    encoded = base64.urlsafe_b64encode(actual).decode().rstrip("=")
                    if not entry.hash or entry.hash.mode != "sha256" or entry.hash.value != encoded:
                        raise DataError("installed builder/lock differs from wheel RECORD")
                    tracked[path.resolve()] = actual.hex()
                    if name.endswith(".lock"):
                        locks[path.name] = actual.hex()
            actual_files = {p.resolve() for p in package.rglob("*") if p.is_file()
                            and p.suffix in {".py", ".json"} and "__pycache__" not in p.parts}
            if not actual_files <= tracked.keys():
                raise DataError("installed builder contains unrecorded source files")
            source = {"kind": "installed_wheel", "name": dist.metadata["Name"],
                      "version": dist.version, "origin": url, "sha256": digest}
        except (metadata.PackageNotFoundError, OSError, ValueError) as exc:
            raise DataError("cannot verify installed builder origin") from exc
    if "requirements-local.lock" not in locks:
        raise DataError("builder dependency lock is unavailable")
    versions = {}
    for name in ("pandas", "pyarrow", "numpy", "pyqlib"):
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            pass
    return {"schema_version": "axiom_builder_v1", "recoverable": True, "source": source,
            "dependency_locks": locks, "environment": {"python": platform.python_version(),
                                                        "packages": versions}}


def operation_context(store, state, operation_id, config):
    """Bind actual builder/config once, reject a changed unfinished runtime."""
    try:
        actual = freeze_builder()
    except DataError as exc:
        state.update(status="failed", error=str(exc))
        store.write_operation(operation_id, state)
        raise
    prior = state.get("builder")
    if prior is not None and prior != actual:
        raise ConflictError("unfinished operation builder changed; use the frozen builder or a new operation")
    if prior is None:
        state["builder"] = actual
        state["build_config"] = deepcopy(dict(config))
        store.write_operation(operation_id, state)
    context = deepcopy(dict(config))
    if "builder" in context and context["builder"] != actual:
        raise ConflictError("caller build_context cannot replace actual builder provenance")
    context.update(builder=actual, operation_id=operation_id)
    return context
