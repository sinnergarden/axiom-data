"""Verified, offline, directory bundles for a pinned local Data snapshot.

This is a byte-preserving transfer format, not an authenticity signature or a
source downloader.  The imported root is self-contained for reads and rebuilds.
"""

from __future__ import annotations

from hashlib import sha256
from importlib import metadata
import errno
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Mapping

from .protocols import DataError
from .storage import LocalStore


_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_SCHEMA = "axiom_portable_directory_v1"


def _encoded(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _relative(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise DataError(f"invalid bundle path: {value!r}")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts) or PurePosixPath(value).is_absolute():
        raise DataError(f"bundle path escapes its root: {value!r}")
    return value


def _regular_file(root: Path, relative: str) -> Path:
    path = root
    for part in _relative(relative).split("/"):
        path = path / part
        if path.is_symlink():
            raise DataError(f"bundle path is a symlink: {relative}")
    if not path.is_file():
        raise DataError(f"bundle file is missing: {relative}")
    return path


def _read(root: Path, relative: str, digest: str | None = None) -> bytes:
    content = _regular_file(root, relative).read_bytes()
    if digest is not None and sha256(content).hexdigest() != digest:
        raise DataError(f"bundle file failed SHA-256 validation: {relative}")
    return content


def _hash_file(path: Path) -> tuple[str, int]:
    digest = sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _copy_file(source: Path, source_name: str, target: Path, target_name: str,
               files: dict[str, dict[str, Any]] | None = None,
               expected: str | None = None) -> None:
    _relative(target_name)
    if files is not None and target_name in files:
        raise DataError(f"duplicate bundle file: {target_name}")
    input_path = _regular_file(source, source_name)
    output_path = target / target_name
    output_path.parent.mkdir(parents=True, exist_ok=True)
    digest = sha256()
    size = 0
    with input_path.open("rb") as input_stream, output_path.open("wb") as output_stream:
        while chunk := input_stream.read(1024 * 1024):
            output_stream.write(chunk)
            digest.update(chunk)
            size += len(chunk)
    actual = digest.hexdigest()
    if expected is not None and actual != expected:
        raise DataError(f"bundle file failed SHA-256 validation: {source_name}")
    if files is not None:
        files[target_name] = {"sha256": actual, "size": size}


def _put(root: Path, relative: str, content: bytes, files: dict[str, dict[str, Any]]) -> None:
    _relative(relative)
    if relative in files:
        if sha256(content).hexdigest() != files[relative]["sha256"]:
            raise DataError(f"conflicting bundle object: {relative}")
        return
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    files[relative] = {"sha256": sha256(content).hexdigest(), "size": len(content)}


def _code_file(relative: str) -> bool:
    _relative(relative)
    if relative in {"pyproject.toml", "requirements-local.lock", "requirements-qlib.lock"}:
        return True
    path = PurePosixPath(relative)
    return (len(path.parts) >= 3 and path.parts[:2] == ("src", "axiom_data")
            and path.suffix in {".py", ".json"})


def _git_identity(root: Path) -> dict[str, Any]:
    result: dict[str, Any] = {"kind": "captured_working_tree", "git_head": None,
                              "git_dirty": None}
    try:
        head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                              capture_output=True, text=True, timeout=10, check=True)
        status = subprocess.run(["git", "-C", str(root), "status", "--porcelain",
                                 "--untracked-files=all", "--", "."],
                                capture_output=True, text=True, timeout=20, check=True)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return result
    result.update(git_head=head.stdout.strip(), git_dirty=bool(status.stdout.strip()))
    return result


def _environment() -> dict[str, Any]:
    installed: dict[str, str | None] = {}
    for name in ("pandas", "numpy", "pyarrow", "pyqlib"):
        try:
            installed[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            installed[name] = None
    return {"python": platform.python_version(), "implementation": platform.python_implementation(),
            "system": platform.system(), "machine": platform.machine(), "packages": installed}


def _publish_new_directory(stage: Path, destination: Path) -> None:
    """Atomically install a staged directory without replacing a concurrent root."""
    if sys.platform in {"darwin", "linux"}:
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        if sys.platform == "darwin":
            operation = libc.renamex_np
            operation.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)
            result = operation(os.fsencode(stage), os.fsencode(destination), 0x00000004)
        else:
            operation = libc.renameat2
            operation.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                                  ctypes.c_char_p, ctypes.c_uint)
            result = operation(-100, os.fsencode(stage), -100,
                               os.fsencode(destination), 1)
        if result:
            number = ctypes.get_errno()
            if number == errno.EEXIST:
                raise DataError(f"destination already exists: {destination}")
            raise OSError(number, os.strerror(number), str(destination))
    else:
        # On Windows, os.rename fails rather than replacing an existing target.
        if os.name != "nt":
            raise DataError("atomic no-replace directory publish is unsupported on this platform")
        os.rename(stage, destination)


def _snapshot_chain(store: LocalStore, selected: str) -> list[dict[str, Any]]:
    chain: list[dict[str, Any]] = []
    seen: set[str] = set()
    current: str | None = selected
    while current is not None:
        if current in seen:
            raise DataError("snapshot parent cycle")
        seen.add(current)
        snapshot = store.load_snapshot(current)
        chain.append(snapshot)
        current = snapshot["parent_snapshot"]
        if current is not None and not isinstance(current, str):
            raise DataError("invalid parent snapshot reference")
    return chain


def _raw_lines(store: LocalStore, wanted: set[str]) -> tuple[bytes, dict[str, dict[str, Any]]]:
    if not wanted:
        return b"", {}
    selected: list[bytes] = []
    records: dict[str, dict[str, Any]] = {}
    try:
        with (store.root / "raw/fetches.jsonl").open("rb") as stream:
            for line in stream:
                # A final incomplete append is not a committed receipt.
                if not line.endswith(b"\n"):
                    break
                try:
                    record = json.loads(line)
                except (ValueError, TypeError) as exc:
                    raise DataError("Raw fetch log is corrupt") from exc
                identity = record.get("batch_id")
                if identity not in wanted:
                    continue
                if identity in records:
                    raise DataError(f"duplicate Raw batch ID: {identity}")
                records[identity] = record
                selected.append(line)
    except FileNotFoundError as exc:
        raise DataError("Raw fetch log is missing") from exc
    missing = wanted - records.keys()
    if missing:
        raise DataError(f"Raw batch does not exist: {sorted(missing)[0]}")
    return b"".join(selected), records


def _objects(chain: list[dict[str, Any]]) -> tuple[dict[str, str], set[str]]:
    objects: dict[str, str] = {}
    raw_ids: set[str] = set()
    for snapshot in chain:
        for domain in snapshot["domains"].values():
            raw_ids.update(domain["raw_batch_ids"])
            for part in domain["partitions"]:
                uri, digest = part["uri"], part["file_sha256"]
                _relative(uri)
                if not uri.startswith("canonical/") or not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
                    raise DataError(f"invalid canonical object reference: {uri}")
                if uri in objects and objects[uri] != digest:
                    raise DataError(f"conflicting canonical object reference: {uri}")
                objects[uri] = digest
    return objects, raw_ids


def _add_raw_objects(objects: dict[str, str], records: Mapping[str, Mapping[str, Any]]) -> None:
    for record in records.values():
        uri, digest = record.get("payload_uri"), record.get("payload_sha256")
        _relative(uri)
        if not uri.startswith("raw/objects/") or not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
            raise DataError(f"invalid Raw object reference: {uri}")
        if uri in objects and objects[uri] != digest:
            raise DataError(f"conflicting Raw object reference: {uri}")
        objects[uri] = digest


def export_bundle(data_root: str | os.PathLike[str], destination: str | os.PathLike[str], *,
                  snapshot_id: str = "current", code_root: str | os.PathLike[str] | None = None,
                  raw_backup_cutoff: str | None = None) -> dict[str, Any]:
    """Copy a pinned snapshot, all parent references and captured code to a new directory.

    ``snapshot_id='current'`` resolves once under the store's writer lock.  The
    same lock protects object/log copying from local writers.  No supplier is
    called.  A destination that already exists is rejected; failures leave no
    published bundle.  ``code_root`` must contain the project source and lock.
    Default exports only the snapshot dependency closure. An explicit inclusive
    ``raw_backup_cutoff`` adds all saved receipts/statuses and payloads through
    that receipt time; referenced snapshot Raw is always retained. Operation
    checkpoints and unfinished append tails are not part of this backup.
    """
    store = LocalStore(data_root)
    if not store.root.is_dir():
        raise DataError(f"data root does not exist: {store.root}")
    code = Path(code_root).resolve() if code_root is not None else Path(__file__).resolve().parents[2]
    target = Path(destination).absolute()
    if target.exists() or target.is_symlink():
        raise DataError(f"bundle destination already exists: {target}")
    canonical_target = target.resolve()
    if canonical_target.is_relative_to(store.root) or canonical_target.is_relative_to(code):
        raise DataError("bundle destination must be outside data and code roots")
    for name in ("pyproject.toml", "requirements-local.lock"):
        _regular_file(code, name)
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".axiom-bundle-", dir=target.parent))
    files: dict[str, dict[str, Any]] = {}
    try:
        with store.writer():
            selected = store.resolve(snapshot_id)
            chain = _snapshot_chain(store, selected)
            objects, wanted = _objects(chain)
            selection = None
            if raw_backup_cutoff is not None:
                selection = store.select_raw(domains=None, receipt_cutoff=raw_backup_cutoff, statuses=None)
                wanted.update(selection["raw_batch_ids"])
            raw_log, records = _raw_lines(store, wanted)
            _add_raw_objects(objects, records)
            _put(stage, "data/current.json", _encoded({"snapshot_id": selected}), files)
            for snapshot in chain:
                name = f"snapshots/{snapshot['snapshot_id']}.json"
                _copy_file(store.root, name, stage, f"data/{name}", files)
            if wanted:
                _put(stage, "data/raw/fetches.jsonl", raw_log, files)
            for uri, digest in sorted(objects.items()):
                _copy_file(store.root, uri, stage, f"data/{uri}", files, digest)
        # Capture the bytes actually present in the working tree.  HEAD is only
        # supplementary provenance and never stands in for these file hashes.
        source_files = [Path("pyproject.toml"), Path("requirements-local.lock")]
        if (code / "requirements-qlib.lock").is_file():
            source_files.append(Path("requirements-qlib.lock"))
        source_files.extend(p.relative_to(code) for p in (code / "src/axiom_data").rglob("*")
                            if p.is_file() and _code_file(p.relative_to(code).as_posix()))
        for relative in sorted(set(source_files)):
            name = relative.as_posix()
            if not _code_file(name):
                raise DataError(f"source file outside allowlist: {name}")
            _copy_file(code, name, stage, f"code/{name}", files)
        source_files_hash = sha256(_encoded({name: meta for name, meta in files.items()
                                             if name.startswith("code/")})).hexdigest()
        manifest: dict[str, Any] = {
            "schema_version": _SCHEMA, "snapshot_id": selected,
            "snapshots": [item["snapshot_id"] for item in chain],
            "source": {**_git_identity(code), "files_sha256": source_files_hash},
            "environment": _environment(), "files": files,
            "raw_scope": {"mode": "snapshot_closure" if selection is None else "full_raw_through_receipt",
                          "receipt_cutoff": selection["receipt_cutoff"] if selection else None,
                          "raw_batch_count": len(wanted)},
        }
        manifest["bundle_id"] = sha256(_encoded(manifest)).hexdigest()
        _put(stage, "bundle.json", _encoded(manifest), {})
        verify_bundle(stage)
        _publish_new_directory(stage, target)
        return manifest
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def verify_bundle(bundle: str | os.PathLike[str]) -> dict[str, Any]:
    """Verify declared bytes and complete snapshot/Raw closure without writing."""
    root = Path(bundle).resolve()
    try:
        manifest = json.loads(_read(root, "bundle.json"))
    except (ValueError, TypeError) as exc:
        raise DataError("bundle manifest is invalid JSON") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != _SCHEMA:
        raise DataError("unsupported bundle manifest")
    identity = manifest.get("bundle_id")
    body = {key: value for key, value in manifest.items() if key != "bundle_id"}
    if identity != sha256(_encoded(body)).hexdigest():
        raise DataError("bundle manifest digest mismatch")
    files = manifest.get("files")
    if not isinstance(files, dict) or not isinstance(manifest.get("snapshots"), list):
        raise DataError("bundle file inventory is invalid")
    actual_files: set[str] = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise DataError(f"bundle contains a symlink: {path.relative_to(root)}")
        if path.is_file():
            actual_files.add(path.relative_to(root).as_posix())
        elif not path.is_dir():
            raise DataError(f"bundle contains a non-file entry: {path.relative_to(root)}")
    if actual_files != set(files) | {"bundle.json"}:
        raise DataError("bundle has undeclared or missing files")
    for relative, info in files.items():
        _relative(relative)
        if not isinstance(info, dict) or not isinstance(info.get("size"), int) or info["size"] < 0 or not isinstance(info.get("sha256"), str) or not _DIGEST.fullmatch(info["sha256"]):
            raise DataError(f"invalid bundle file entry: {relative}")
        digest, size = _hash_file(_regular_file(root, relative))
        if digest != info["sha256"]:
            raise DataError(f"bundle file failed SHA-256 validation: {relative}")
        if size != info["size"]:
            raise DataError(f"bundle file size mismatch: {relative}")
    data = LocalStore(root / "data")
    selected = manifest.get("snapshot_id")
    if data.resolve("current") != selected:
        raise DataError("bundle current pointer differs from pinned snapshot")
    chain = _snapshot_chain(data, selected)
    if [item["snapshot_id"] for item in chain] != manifest["snapshots"]:
        raise DataError("bundle snapshot ancestry is incomplete")
    objects, wanted = _objects(chain)
    raw_scope = manifest.get("raw_scope")
    if raw_scope is not None:
        if not isinstance(raw_scope, dict) or raw_scope.get("mode") not in {"snapshot_closure", "full_raw_through_receipt"}:
            raise DataError("invalid bundle Raw scope")
        if raw_scope["mode"] == "full_raw_through_receipt":
            selection = data.select_raw(domains=None, receipt_cutoff=raw_scope["receipt_cutoff"], statuses=None)
            wanted.update(selection["raw_batch_ids"])
        elif raw_scope.get("receipt_cutoff") is not None:
            raise DataError("snapshot closure cannot declare a full Raw cutoff")
        if raw_scope.get("raw_batch_count") != len(wanted):
            raise DataError("bundle Raw count differs from its declared scope")
    log, records = _raw_lines(data, wanted)
    _add_raw_objects(objects, records)
    expected = {"data/current.json", *(f"data/snapshots/{s['snapshot_id']}.json" for s in chain),
                *(f"data/{uri}" for uri in objects)}
    if wanted:
        expected.add("data/raw/fetches.jsonl")
    for relative in files:
        if relative.startswith("code/"):
            name = relative.removeprefix("code/")
            if not _code_file(name):
                raise DataError(f"non-allowlisted source file in bundle: {relative}")
            expected.add(relative)
    if set(files) != expected:
        raise DataError("bundle inventory differs from snapshot closure")
    for required in ("code/pyproject.toml", "code/requirements-local.lock", "code/src/axiom_data/__init__.py"):
        if required not in files:
            raise DataError(f"bundle lacks replay source: {required}")
    source_hash = sha256(_encoded({name: meta for name, meta in files.items()
                                   if name.startswith("code/")})).hexdigest()
    if manifest.get("source", {}).get("files_sha256") != source_hash:
        raise DataError("captured source digest mismatch")
    if wanted and log != _read(data.root, "raw/fetches.jsonl"):
        raise DataError("bundle contains unselected Raw observations")
    for uri, digest in objects.items():
        if files[f"data/{uri}"]["sha256"] != digest:
            raise DataError(f"bundle object digest differs from snapshot reference: {uri}")
    return manifest


def import_bundle(bundle: str | os.PathLike[str], destination: str | os.PathLike[str]) -> str:
    """Verify and publish a new offline root; reject any existing destination.

    Captured source appears under ``portable-code/`` in the imported root.
    A temporary sibling is fully verified before the destination is published.
    """
    source = Path(bundle).resolve()
    manifest = verify_bundle(source)
    target = Path(destination).absolute()
    if target.exists() or target.is_symlink():
        raise DataError(f"import destination already exists: {target}")
    if target.resolve().is_relative_to(source):
        raise DataError("import destination must be outside the bundle")
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".axiom-import-", dir=target.parent))
    try:
        for relative, info in manifest["files"].items():
            suffix = relative.removeprefix("data/") if relative.startswith("data/") else "portable-" + relative
            _copy_file(source, relative, stage, suffix, expected=info["sha256"])
        (stage / "portable-bundle.json").write_bytes(_read(source, "bundle.json"))
        imported = LocalStore(stage)
        if imported.resolve("current") != manifest["snapshot_id"]:
            raise DataError("imported snapshot pointer differs")
        chain = _snapshot_chain(imported, manifest["snapshot_id"])
        objects, wanted = _objects(chain)
        _, records = _raw_lines(imported, wanted)
        _add_raw_objects(objects, records)
        for uri, digest in objects.items():
            actual, _ = _hash_file(_regular_file(stage, uri))
            if actual != digest:
                raise DataError(f"imported object failed SHA-256 validation: {uri}")
        _publish_new_directory(stage, target)
        return manifest["snapshot_id"]
    finally:
        if stage.exists():
            shutil.rmtree(stage)
