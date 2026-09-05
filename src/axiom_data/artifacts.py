"""Phase 1 PR2 filesystem artifacts and rebuildable catalog."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from axiom_data.build import (
    BuildContractError,
    BuildRequest,
    DomainCommitRef,
    _validate_identity,
)
from axiom_data.contracts import load_contract
from axiom_data.domains import (
    MARKET_DOMAINS,
    MarketContractError,
    security_identity_state,
    validate_market_daily_rows,
    validate_security_master_rows,
    validate_trading_calendar_rows,
)
from axiom_data.layout import DataRootLayout


_MANIFEST = "manifest.json"
_MANIFEST_DIGEST = "manifest.sha256"
_REQUIRED_SNAPSHOT_DOMAINS = frozenset(MARKET_DOMAINS)
_DOMAIN_VALIDATORS = {
    "trading_calendar": validate_trading_calendar_rows,
    "security_master": validate_security_master_rows,
    "market_daily": validate_market_daily_rows,
}
_SUFFIX_EXCHANGE = {".SH": "SSE", ".SZ": "SZSE"}
_MARKET_BUILDER_REVISION = "market-json-builder.v1"


class ArtifactError(ValueError):
    """Base error for invalid, missing, corrupt, or conflicting artifacts."""


class ArtifactNotFoundError(ArtifactError):
    """Raised when an exact immutable identity cannot be resolved."""


class ArtifactConflictError(ArtifactError):
    """Raised when publication would overwrite different immutable content."""


@dataclass(frozen=True, slots=True)
class RawBatchRef:
    raw_batch_id: str
    manifest_digest: str


@dataclass(frozen=True, slots=True)
class RawBatch:
    ref: RawBatchRef
    manifest: dict[str, Any]
    payload: bytes


@dataclass(frozen=True, slots=True)
class DomainCommit:
    ref: DomainCommitRef
    manifest_digest: str
    manifest: dict[str, Any]
    contract: dict[str, Any]
    rows: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class DataSnapshotRef:
    snapshot_id: str
    manifest_digest: str


@dataclass(frozen=True, slots=True)
class DataSnapshot:
    ref: DataSnapshotRef
    manifest: dict[str, Any]


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    artifact_type: str
    artifact_id: str
    domain: str | None
    contract_version: str | None
    manifest_path: str
    manifest_digest: str


def _identity(name: str, value: object) -> str:
    try:
        validated = _validate_identity(name, value)
    except BuildContractError as exc:
        raise ArtifactError(str(exc)) from exc
    assert validated is not None
    return validated


def _layout(data_root: str | Path) -> DataRootLayout:
    layout = DataRootLayout(Path(data_root))
    _safe_path(layout.root, layout.root)
    return layout


def _safe_path(root: Path, path: Path, *, closure: Path | None = None) -> Path:
    """Validate a lexical path and every existing component without following links."""

    boundary = root if closure is None else closure
    try:
        path.relative_to(boundary)
        boundary.relative_to(root)
    except ValueError as exc:
        raise ArtifactError("artifact path must remain inside its allowed closure") from exc
    for component in (path, *path.parents):
        if component.is_symlink():
            raise ArtifactError(f"artifact path component must not be a symlink: {component}")
        if component == Path(component.anchor):
            break
    return path


def _json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ArtifactError("artifact metadata and rows must be canonical JSON values") from exc


def _json_copy(value: object) -> Any:
    return json.loads(_json_bytes(value))


def _digest(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def _identity_projection(
    manifest: Mapping[str, Any], identity_field: str
) -> dict[str, Any]:
    excluded = {identity_field, "identity_digest", "created_at"}
    return {key: value for key, value in manifest.items() if key not in excluded}


def _identity_digest(manifest: Mapping[str, Any], identity_field: str) -> str:
    return _digest(_json_bytes(_identity_projection(manifest, identity_field)))


def _derived_identity(prefix: str, identity_digest: str) -> str:
    return f"{prefix}-{identity_digest.removeprefix('sha256:')}"


def _builder_implementation_ref(builder: object) -> dict[str, str]:
    implementation = f"{type(builder).__module__}.{type(builder).__qualname__}"
    revision = getattr(builder, "implementation_revision", None)
    if not isinstance(revision, str) or not revision:
        raise ArtifactError("builder implementation must declare a controlled revision")
    descriptor = {
        "implementation": implementation,
        "revision": revision,
    }
    return {**descriptor, "digest": _digest(_json_bytes(descriptor))}


def _validate_builder_implementation_ref(value: object) -> None:
    if not isinstance(value, dict) or set(value) != {
        "implementation",
        "revision",
        "digest",
    }:
        raise ArtifactError("builder implementation ref is invalid")
    descriptor = {
        "implementation": value["implementation"],
        "revision": value["revision"],
    }
    if (
        not all(isinstance(item, str) and item for item in descriptor.values())
        or value["digest"] != _digest(_json_bytes(descriptor))
    ):
        raise ArtifactError("builder implementation ref digest mismatch")


def _validate_manifest_identity(
    manifest: Mapping[str, Any], identity_field: str, prefix: str, identity: str
) -> None:
    actual_digest = _identity_digest(manifest, identity_field)
    if manifest.get("identity_digest") != actual_digest:
        raise ArtifactError("artifact deterministic identity digest mismatch")
    if identity != _derived_identity(prefix, actual_digest):
        raise ArtifactError("artifact ID does not match its deterministic identity")


def _timestamp(value: str | None) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat()
    if not isinstance(value, str):
        raise ArtifactError("timestamp must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ArtifactError("timestamp must be an ISO-8601 string") from exc
    if parsed.tzinfo is None:
        raise ArtifactError("timestamp must include a UTC offset")
    return value


def _ensure_directory(root: Path, directory: Path) -> None:
    try:
        relative = directory.relative_to(root)
    except ValueError as exc:
        raise ArtifactError("artifact directory must remain inside the data root") from exc
    _safe_path(root, root)
    root.mkdir(parents=True, exist_ok=True)
    if not root.is_dir() or root.is_symlink():
        raise ArtifactError("data root must be a real directory")
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ArtifactError(f"artifact path component must not be a symlink: {current}")
        current.mkdir(exist_ok=True)
        if not current.is_dir():
            raise ArtifactError(f"artifact path component must be a directory: {current}")


def _write_file(path: Path, content: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_manifest(artifact_dir: Path, manifest: Mapping[str, Any]) -> str:
    content = _json_bytes(manifest)
    manifest_digest = _digest(content)
    _write_file(artifact_dir / _MANIFEST, content)
    _write_file(artifact_dir / _MANIFEST_DIGEST, f"{manifest_digest}\n".encode("ascii"))
    return manifest_digest


def _relative_file(root: Path, artifact_dir: Path, value: object) -> Path:
    if not isinstance(value, str):
        raise ArtifactError("artifact file path must be relative text")
    relative = PurePosixPath(value)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise ArtifactError("artifact file path must stay inside its artifact")
    path = artifact_dir.joinpath(*relative.parts)
    return _safe_path(root, path, closure=artifact_dir)


def _load_manifest(
    root: Path,
    artifact_dir: Path,
    *,
    artifact_type: str,
    schema_version: str,
    identity_field: str,
    identity: str,
) -> tuple[dict[str, Any], str]:
    _safe_path(root, artifact_dir)
    if not artifact_dir.exists():
        raise ArtifactNotFoundError(f"{artifact_type} {identity!r} does not exist")
    if artifact_dir.is_symlink() or not artifact_dir.is_dir():
        raise ArtifactError("artifact identity must resolve to a real directory")
    manifest_path = artifact_dir / _MANIFEST
    digest_path = artifact_dir / _MANIFEST_DIGEST
    _safe_path(root, manifest_path, closure=artifact_dir)
    _safe_path(root, digest_path, closure=artifact_dir)
    if (
        not manifest_path.is_file()
        or manifest_path.is_symlink()
        or not digest_path.is_file()
        or digest_path.is_symlink()
    ):
        raise ArtifactError(f"{artifact_type} {identity!r} has no valid manifest closure")
    content = manifest_path.read_bytes()
    actual_digest = _digest(content)
    try:
        recorded_digest = digest_path.read_text(encoding="ascii").strip()
    except UnicodeDecodeError as exc:
        raise ArtifactError("manifest digest sidecar is not ASCII") from exc
    if recorded_digest != actual_digest:
        raise ArtifactError(f"{artifact_type} {identity!r} manifest digest mismatch")
    try:
        manifest = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArtifactError(f"{artifact_type} {identity!r} manifest is not valid JSON") from exc
    if not isinstance(manifest, dict):
        raise ArtifactError("artifact manifest must be a JSON object")
    if (
        manifest.get("artifact_type") != artifact_type
        or manifest.get("schema_version") != schema_version
        or manifest.get(identity_field) != identity
    ):
        raise ArtifactError(f"{artifact_type} manifest identity mismatch")
    return manifest, actual_digest


def _tree_digests(directory: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise ArtifactError("published artifact closure must not contain symlinks")
        if path.is_file():
            result[path.relative_to(directory).as_posix()] = _digest(path.read_bytes())
    return result


def _content_tree_digests(directory: Path) -> dict[str, str]:
    return {
        path: digest
        for path, digest in _tree_digests(directory).items()
        if path not in {_MANIFEST, _MANIFEST_DIGEST}
    }


def _publication_equivalent(
    existing: Path,
    candidate: Path,
    identity_digest: str | None,
) -> bool:
    if identity_digest is None:
        return _tree_digests(existing) == _tree_digests(candidate)
    manifest_paths = (
        existing / _MANIFEST,
        existing / _MANIFEST_DIGEST,
        candidate / _MANIFEST,
        candidate / _MANIFEST_DIGEST,
    )
    if any(path.is_symlink() for path in manifest_paths):
        return False
    try:
        existing_manifest = json.loads((existing / _MANIFEST).read_bytes())
        candidate_manifest = json.loads((candidate / _MANIFEST).read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    return (
        isinstance(existing_manifest, dict)
        and isinstance(candidate_manifest, dict)
        and existing_manifest.get("identity_digest") == identity_digest
        and candidate_manifest.get("identity_digest") == identity_digest
        and _content_tree_digests(existing) == _content_tree_digests(candidate)
    )


def _publish_directory(
    layout: DataRootLayout,
    target: Path,
    prepare: Any,
    *,
    identity_digest: str | None = None,
) -> None:
    _ensure_directory(layout.root, layout.staging)
    _ensure_directory(layout.root, target.parent)
    if target.is_symlink():
        raise ArtifactConflictError("immutable artifact target must not be a symlink")

    with tempfile.TemporaryDirectory(prefix="publish-", dir=layout.staging) as temporary:
        candidate = Path(temporary) / "artifact"
        candidate.mkdir()
        prepare(candidate)
        _fsync_directory(candidate)

        if target.exists():
            if not target.is_dir() or not _publication_equivalent(
                target, candidate, identity_digest
            ):
                raise ArtifactConflictError(f"immutable artifact already exists: {target.name}")
            return
        try:
            os.rename(candidate, target)
        except OSError as exc:
            if target.exists() and target.is_dir():
                if _publication_equivalent(target, candidate, identity_digest):
                    return
                raise ArtifactConflictError(
                    f"immutable artifact was concurrently published: {target.name}"
                ) from exc
            raise ArtifactError(f"could not publish immutable artifact: {target.name}") from exc
        _fsync_directory(target.parent)


def _payload_file(root: Path, manifest: Mapping[str, Any], artifact_dir: Path) -> bytes:
    payload_files = manifest.get("payload_files")
    if not isinstance(payload_files, list) or len(payload_files) != 1:
        raise ArtifactError("Phase 1 RawBatch requires exactly one payload file")
    entry = payload_files[0]
    if not isinstance(entry, dict):
        raise ArtifactError("RawBatch payload entry must be an object")
    path = _relative_file(root, artifact_dir, entry.get("path"))
    if not path.is_file():
        raise ArtifactError("RawBatch payload file is missing")
    payload = path.read_bytes()
    if entry.get("content_digest") != _digest(payload) or entry.get("bytes") != len(payload):
        raise ArtifactError("RawBatch payload content does not match its manifest")
    return payload


def write_raw_batch(
    data_root: str | Path,
    raw_batch_id: str,
    *,
    domain: str,
    source_profile: str,
    request: Mapping[str, Any],
    retrieved_at: str,
    payload: bytes,
    collector_code: str,
    status: str = "success",
    summary: Mapping[str, Any] | None = None,
) -> RawBatchRef:
    """Publish one append-only observation while preserving its exact payload bytes."""

    layout = _layout(data_root)
    raw_batch_id = _identity("raw_batch_id", raw_batch_id)
    if domain not in MARKET_DOMAINS:
        raise ArtifactError("RawBatch domain must be a Phase 1 market domain")
    source_profile = _identity("source_profile", source_profile)
    collector_code = _identity("collector_code", collector_code)
    retrieved_at = _timestamp(retrieved_at)
    if not isinstance(payload, bytes):
        raise ArtifactError("RawBatch payload must be bytes")
    if not isinstance(status, str) or not status:
        raise ArtifactError("RawBatch status must be non-empty text")
    if not isinstance(request, Mapping) or (
        summary is not None and not isinstance(summary, Mapping)
    ):
        raise ArtifactError("RawBatch request and summary must be mappings")
    request_copy = _json_copy(request)
    summary_copy = _json_copy(summary or {})
    target = layout.raw_batches / raw_batch_id
    payload_digest = _digest(payload)

    manifest = {
        "artifact_type": "raw_batch",
        "schema_version": "raw_batch.v1",
        "raw_batch_id": raw_batch_id,
        "domain": domain,
        "source_profile_ref": source_profile,
        "request": request_copy,
        "retrieved_at": retrieved_at,
        "collector_code_ref": collector_code,
        "status": status,
        "summary": summary_copy,
        "payload_files": [
            {
                "path": "payload.bin",
                "content_digest": payload_digest,
                "bytes": len(payload),
            }
        ],
    }

    def prepare(candidate: Path) -> None:
        _write_file(candidate / "payload.bin", payload)
        if _digest((candidate / "payload.bin").read_bytes()) != payload_digest:
            raise ArtifactError("staged RawBatch payload failed its digest check")
        _write_manifest(candidate, manifest)

    _publish_directory(layout, target, prepare)
    return load_raw_batch(layout.root, raw_batch_id).ref


def load_raw_batch(data_root: str | Path, raw_batch_id: str) -> RawBatch:
    """Load and verify one exact RawBatch without consulting a pointer or catalog."""

    layout = _layout(data_root)
    raw_batch_id = _identity("raw_batch_id", raw_batch_id)
    target = layout.raw_batches / raw_batch_id
    if not target.exists():
        found = _find_exact_artifact_type(layout, raw_batch_id)
        if found is not None:
            raise ArtifactError(f"expected raw_batch {raw_batch_id!r}, found {found}")
    manifest, manifest_digest = _load_manifest(
        layout.root,
        target,
        artifact_type="raw_batch",
        schema_version="raw_batch.v1",
        identity_field="raw_batch_id",
        identity=raw_batch_id,
    )
    if manifest.get("domain") not in MARKET_DOMAINS:
        raise ArtifactError("RawBatch has an unsupported domain")
    for field in ("source_profile_ref", "collector_code_ref"):
        _identity(field, manifest.get(field))
    if not isinstance(manifest.get("request"), dict):
        raise ArtifactError("RawBatch request metadata must be an object")
    if not isinstance(manifest.get("summary"), dict):
        raise ArtifactError("RawBatch summary must be an object")
    if not isinstance(manifest.get("retrieved_at"), str):
        raise ArtifactError("RawBatch retrieved_at is missing")
    _timestamp(manifest["retrieved_at"])
    if not isinstance(manifest.get("status"), str) or not manifest["status"]:
        raise ArtifactError("RawBatch status must be non-empty text")
    payload = _payload_file(layout.root, manifest, target)
    return RawBatch(RawBatchRef(raw_batch_id, manifest_digest), manifest, payload)


def _find_exact_artifact_type(layout: DataRootLayout, artifact_id: str) -> str | None:
    raw_path = _safe_path(layout.root, layout.raw_batches / artifact_id)
    if raw_path.exists():
        return "raw_batch"
    snapshot_path = _safe_path(layout.root, layout.snapshots / artifact_id)
    if snapshot_path.exists():
        return "data_snapshot"
    for domain in MARKET_DOMAINS:
        commit_path = _safe_path(
            layout.root, layout.domain_commits(domain) / artifact_id
        )
        if commit_path.exists():
            return f"domain_commit:{domain}"
    return None


def _decode_rows(raw: RawBatch) -> list[dict[str, Any]]:
    try:
        value = json.loads(raw.payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArtifactError(f"RawBatch {raw.ref.raw_batch_id!r} payload is not JSON") from exc
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise ArtifactError("Phase 1 market RawBatch payload must be a JSON array of row objects")
    return value


def _contract_content(contract_version: str) -> tuple[dict[str, Any], bytes, str]:
    try:
        contract = load_contract(contract_version)
    except ValueError as exc:
        raise ArtifactError(f"unknown contract version: {contract_version!r}") from exc
    content = _json_bytes(contract)
    return contract, content, _digest(content)


def _validate_domain_rows(domain: str, rows: object) -> None:
    try:
        _DOMAIN_VALIDATORS[domain](rows)
    except MarketContractError as exc:
        raise ArtifactError(f"{domain} rows violate {domain}.v1") from exc


def _commit_ref(commit: DomainCommit) -> dict[str, Any]:
    return {
        "domain_commit_id": commit.ref.commit_id,
        "domain": commit.ref.domain,
        "contract_version": commit.ref.contract_version,
        "contract_digest": commit.manifest["contract_digest"],
        "identity_digest": commit.manifest["identity_digest"],
        "logical_content_digest": commit.manifest["logical_content_digest"],
    }


def _raw_ref(raw: RawBatch) -> dict[str, Any]:
    return {
        "raw_batch_id": raw.ref.raw_batch_id,
        "manifest_digest": raw.ref.manifest_digest,
        "payload_digest": raw.manifest["payload_files"][0]["content_digest"],
    }


def _merge_rows(
    domain: str,
    contract: Mapping[str, Any],
    parent_rows: Sequence[Mapping[str, Any]],
    raw_batches: Sequence[RawBatch],
) -> list[dict[str, Any]]:
    primary_key = contract["primary_key"]
    by_key: dict[tuple[Any, ...], dict[str, Any]] = {}
    ordered_rows: list[dict[str, Any]] = []

    def apply(row: Mapping[str, Any], source: str) -> None:
        copied = _json_copy(row)
        try:
            key = tuple(copied[name] for name in primary_key)
            hash(key)
        except (KeyError, TypeError) as exc:
            raise ArtifactError(f"{source} has an invalid {domain} primary key") from exc
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = copied
            ordered_rows.append(copied)
        elif existing != copied:
            raise ArtifactConflictError(
                f"{source} conflicts with an earlier value for {domain} key {key!r}"
            )

    for row in parent_rows:
        apply(row, "parent state")
    for raw in raw_batches:
        for row in _decode_rows(raw):
            apply(row, f"RawBatch {raw.ref.raw_batch_id!r}")

    sort_order = contract["sort_order"]
    try:
        return sorted(ordered_rows, key=lambda row: tuple(row[name] for name in sort_order))
    except (KeyError, TypeError) as exc:
        raise ArtifactError(f"{domain} rows have invalid sort keys") from exc


def _symbol_exchange(symbol: object) -> str:
    if not isinstance(symbol, str):
        raise ArtifactError("market symbol must be canonical text")
    try:
        return _SUFFIX_EXCHANGE[symbol[-3:]]
    except KeyError as exc:
        raise ArtifactError("market symbol has no canonical exchange suffix") from exc


def _validate_market_dependencies(
    market_rows: Sequence[Mapping[str, Any]],
    calendar: DomainCommit,
    security: DomainCommit,
) -> None:
    calendar_rows = {
        (row["exchange"], row["session"]): row for row in calendar.rows
    }
    security_rows = {row["symbol"]: row for row in security.rows}
    for row in market_rows:
        symbol = row["symbol"]
        exchange = _symbol_exchange(symbol)
        security_row = security_rows.get(symbol)
        if security_row is None or security_row.get("exchange") != exchange:
            raise ArtifactError(f"market symbol {symbol!r} has no matching security identity")
        identity_state = security_identity_state(security_row, row["session"])
        if identity_state != "within_identity_interval":
            raise ArtifactError(
                f"market row {(symbol, row['session'])!r} is outside the security "
                "identity interval"
            )
        calendar_row = calendar_rows.get((exchange, row["session"]))
        if calendar_row is None:
            raise ArtifactError(
                f"market session {(exchange, row['session'])!r} is absent from the fixed calendar"
            )
        if calendar_row.get("is_open") is not True:
            raise ArtifactError(
                f"market session {(exchange, row['session'])!r} is closed in the fixed calendar"
            )


class MarketDomainBuilder:
    """PR1 BuildExecutor that publishes one immutable Phase 1 market commit."""

    implementation_revision = _MARKET_BUILDER_REVISION

    def __init__(
        self,
        data_root: str | Path,
        domain: str,
        *,
        builder_config: Mapping[str, Any] | None = None,
        commit_id: str | None = None,
        calendar_commit_id: str | None = None,
        security_master_commit_id: str | None = None,
        created_at: str | None = None,
    ) -> None:
        if domain not in MARKET_DOMAINS:
            raise ArtifactError("builder domain must be a Phase 1 market domain")
        self.layout = _layout(data_root)
        self.domain = domain
        if builder_config is not None and not isinstance(builder_config, Mapping):
            raise ArtifactError("builder_config must be a mapping")
        self.builder_config = _json_copy(builder_config or {})
        self.expected_commit_id = (
            _identity("commit_id", commit_id) if commit_id is not None else None
        )
        self.calendar_commit_id = (
            _identity("calendar_commit_id", calendar_commit_id)
            if calendar_commit_id is not None
            else None
        )
        self.security_master_commit_id = (
            _identity("security_master_commit_id", security_master_commit_id)
            if security_master_commit_id is not None
            else None
        )
        self.created_at = _timestamp(created_at)

    def _build_rows(
        self,
        contract: Mapping[str, Any],
        parent_rows: Sequence[Mapping[str, Any]],
        raw_batches: Sequence[RawBatch],
    ) -> list[dict[str, Any]]:
        return _merge_rows(self.domain, contract, parent_rows, raw_batches)

    def __call__(self, request: BuildRequest) -> DomainCommitRef:
        if request.patch_ids:
            raise ArtifactError("Phase 1 PR2 does not support non-empty patch_ids")
        contract, contract_content, contract_digest = _contract_content(
            request.contract_version
        )
        if contract.get("domain") != self.domain:
            raise ArtifactError("build contract does not belong to the executor domain")

        raw_batches = [load_raw_batch(self.layout.root, raw_id) for raw_id in request.raw_batch_ids]
        for raw in raw_batches:
            if raw.manifest.get("domain") != self.domain:
                raise ArtifactError(
                    f"RawBatch {raw.ref.raw_batch_id!r} belongs to another domain"
                )
            if raw.manifest.get("status") != "success":
                raise ArtifactError(f"RawBatch {raw.ref.raw_batch_id!r} is not successful")

        parent = None
        if request.parent_commit is not None:
            parent = validate_domain_commit_closure(
                self.layout.root, self.domain, request.parent_commit
            )
            if (
                parent.ref.contract_version != request.contract_version
                or parent.manifest.get("contract_digest") != contract_digest
            ):
                raise ArtifactError("parent commit belongs to a different contract lineage")

        calendar = None
        security = None
        dependencies: dict[str, dict[str, Any]] = {}
        if self.domain == "market_daily":
            if self.calendar_commit_id is None or self.security_master_commit_id is None:
                raise ArtifactError(
                    "market_daily publication requires explicit calendar and security commits"
                )
            calendar = validate_domain_commit_closure(
                self.layout.root, "trading_calendar", self.calendar_commit_id
            )
            security = validate_domain_commit_closure(
                self.layout.root, "security_master", self.security_master_commit_id
            )
            dependencies = {
                "trading_calendar": _commit_ref(calendar),
                "security_master": _commit_ref(security),
            }
        elif self.calendar_commit_id is not None or self.security_master_commit_id is not None:
            raise ArtifactError("only market_daily accepts cross-domain dependency commits")

        rows = self._build_rows(
            contract,
            parent.rows if parent is not None else (),
            raw_batches,
        )
        rows_content = _json_bytes(rows)
        logical_digest = _digest(rows_content)
        builder_config_digest = _digest(_json_bytes(self.builder_config))
        builder_implementation_ref = _builder_implementation_ref(self)
        parent_ref = _commit_ref(parent) if parent is not None else None
        raw_refs = [_raw_ref(raw) for raw in raw_batches]
        manifest = {
            "artifact_type": "domain_commit",
            "schema_version": "domain_commit.v1",
            "domain": self.domain,
            "contract_version": request.contract_version,
            "contract_digest": contract_digest,
            "contract_path": "contract.json",
            "parent_commit_ref": parent_ref,
            "ordered_raw_batch_refs": raw_refs,
            "ordered_patch_refs": [],
            "builder_implementation_ref": builder_implementation_ref,
            "builder_config": self.builder_config,
            "builder_config_digest": builder_config_digest,
            "dependency_commit_refs": dependencies,
            "output_files": [
                {
                    "path": "rows.json",
                    "content_digest": logical_digest,
                    "rows": len(rows),
                }
            ],
            "logical_content_digest": logical_digest,
            "validation_summary": {
                "status": "PASS",
                "contract_rows": len(rows),
                "cross_domain": "PASS" if self.domain == "market_daily" else "NOT_APPLICABLE",
            },
        }
        identity_digest = _identity_digest(manifest, "domain_commit_id")
        commit_id = _derived_identity(self.domain, identity_digest)
        if self.expected_commit_id is not None and self.expected_commit_id != commit_id:
            raise ArtifactError("commit_id must equal the derived content-safe identity")
        manifest["domain_commit_id"] = commit_id
        manifest["identity_digest"] = identity_digest
        manifest["created_at"] = self.created_at
        target = self.layout.domain_commits(self.domain) / commit_id

        def prepare(candidate: Path) -> None:
            _write_file(candidate / "contract.json", contract_content)
            _write_file(candidate / "rows.json", rows_content)
            staged_rows = json.loads((candidate / "rows.json").read_bytes())
            _validate_domain_rows(self.domain, staged_rows)
            if calendar is not None and security is not None:
                _validate_market_dependencies(staged_rows, calendar, security)
            if _digest((candidate / "contract.json").read_bytes()) != contract_digest:
                raise ArtifactError("staged contract digest mismatch")
            if _digest((candidate / "rows.json").read_bytes()) != logical_digest:
                raise ArtifactError("staged logical content digest mismatch")
            _write_manifest(candidate, manifest)

        _publish_directory(
            self.layout,
            target,
            prepare,
            identity_digest=identity_digest,
        )
        return validate_domain_commit_closure(
            self.layout.root, self.domain, commit_id
        ).ref


def load_domain_commit(
    data_root: str | Path,
    domain: str,
    domain_commit_id: str,
) -> DomainCommit:
    """Load one exact immutable DomainCommit and verify its local closure."""

    layout = _layout(data_root)
    if domain not in MARKET_DOMAINS:
        raise ArtifactError("domain must be a Phase 1 market domain")
    domain_commit_id = _identity("domain_commit_id", domain_commit_id)
    target = layout.domain_commits(domain) / domain_commit_id
    if not target.exists():
        found = _find_exact_artifact_type(layout, domain_commit_id)
        if found is not None:
            raise ArtifactError(
                f"expected domain_commit:{domain} {domain_commit_id!r}, found {found}"
            )
    manifest, manifest_digest = _load_manifest(
        layout.root,
        target,
        artifact_type="domain_commit",
        schema_version="domain_commit.v1",
        identity_field="domain_commit_id",
        identity=domain_commit_id,
    )
    if manifest.get("domain") != domain:
        raise ArtifactError("DomainCommit domain mismatch")
    _validate_manifest_identity(manifest, "domain_commit_id", domain, domain_commit_id)
    _validate_builder_implementation_ref(manifest.get("builder_implementation_ref"))
    builder_config = manifest.get("builder_config")
    if not isinstance(builder_config, dict) or manifest.get(
        "builder_config_digest"
    ) != _digest(_json_bytes(builder_config)):
        raise ArtifactError("DomainCommit builder config digest mismatch")
    if not isinstance(manifest.get("created_at"), str):
        raise ArtifactError("DomainCommit created_at is missing")
    _timestamp(manifest["created_at"])
    contract_path = _relative_file(layout.root, target, manifest.get("contract_path"))
    if not contract_path.is_file():
        raise ArtifactError("DomainCommit contract content is missing")
    contract_content = contract_path.read_bytes()
    if manifest.get("contract_digest") != _digest(contract_content):
        raise ArtifactError("DomainCommit contract digest mismatch")
    try:
        contract = json.loads(contract_content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArtifactError("DomainCommit contract content is invalid") from exc
    contract_version = manifest.get("contract_version")
    if (
        not isinstance(contract, dict)
        or contract.get("contract_version") != contract_version
        or contract.get("domain") != domain
    ):
        raise ArtifactError("DomainCommit contract identity mismatch")
    try:
        ref = DomainCommitRef(domain, domain_commit_id, contract_version)
    except BuildContractError as exc:
        raise ArtifactError("DomainCommit uses an unsupported Phase 1 contract") from exc

    output_files = manifest.get("output_files")
    if not isinstance(output_files, list) or len(output_files) != 1:
        raise ArtifactError("Phase 1 DomainCommit requires exactly one output file")
    output = output_files[0]
    if not isinstance(output, dict):
        raise ArtifactError("DomainCommit output entry is invalid")
    rows_path = _relative_file(layout.root, target, output.get("path"))
    if not rows_path.is_file():
        raise ArtifactError("DomainCommit rows file is missing")
    rows_content = rows_path.read_bytes()
    if (
        output.get("content_digest") != _digest(rows_content)
        or manifest.get("logical_content_digest") != _digest(rows_content)
    ):
        raise ArtifactError("DomainCommit logical content digest mismatch")
    try:
        rows = json.loads(rows_content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArtifactError("DomainCommit rows are invalid JSON") from exc
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ArtifactError("DomainCommit rows must be a JSON array of objects")
    if output.get("rows") != len(rows):
        raise ArtifactError("DomainCommit row count mismatch")
    _validate_domain_rows(domain, rows)
    expected_cross_domain = "PASS" if domain == "market_daily" else "NOT_APPLICABLE"
    if manifest.get("validation_summary") != {
        "status": "PASS",
        "contract_rows": len(rows),
        "cross_domain": expected_cross_domain,
    }:
        raise ArtifactError("DomainCommit validation summary is invalid")
    return DomainCommit(ref, manifest_digest, manifest, contract, tuple(rows))


def _validate_domain_commit_closure(
    root: Path,
    domain: str,
    domain_commit_id: str,
    active: set[tuple[str, str]],
    cache: dict[tuple[str, str], DomainCommit],
) -> DomainCommit:
    key = (domain, domain_commit_id)
    if key in cache:
        return cache[key]
    if key in active:
        raise ArtifactError("DomainCommit lineage contains a cycle")

    commit = load_domain_commit(root, domain, domain_commit_id)
    active.add(key)
    try:
        raw_refs = commit.manifest.get("ordered_raw_batch_refs")
        if not isinstance(raw_refs, list):
            raise ArtifactError("DomainCommit ordered raw refs are invalid")
        raw_ids: set[str] = set()
        for raw_ref in raw_refs:
            if not isinstance(raw_ref, dict) or not isinstance(
                raw_ref.get("raw_batch_id"), str
            ):
                raise ArtifactError("DomainCommit raw ref is invalid")
            raw = load_raw_batch(root, raw_ref["raw_batch_id"])
            if raw.manifest.get("domain") != domain or _raw_ref(raw) != raw_ref:
                raise ArtifactError("DomainCommit raw ref does not match its artifact")
            raw_ids.add(raw.ref.raw_batch_id)
        if len(raw_ids) != len(raw_refs):
            raise ArtifactError("DomainCommit raw refs must not contain duplicates")

        if commit.manifest.get("ordered_patch_refs") != []:
            raise ArtifactError("Phase 1 PR2 DomainCommit patch refs must be empty")

        parent_ref = commit.manifest.get("parent_commit_ref")
        if parent_ref is not None:
            if (
                not isinstance(parent_ref, dict)
                or parent_ref.get("domain") != domain
                or not isinstance(parent_ref.get("domain_commit_id"), str)
            ):
                raise ArtifactError("DomainCommit parent ref is invalid")
            parent = _validate_domain_commit_closure(
                root,
                domain,
                parent_ref["domain_commit_id"],
                active,
                cache,
            )
            if _commit_ref(parent) != parent_ref:
                raise ArtifactError("DomainCommit parent ref does not match its artifact")
            if (
                parent.ref.contract_version != commit.ref.contract_version
                or parent.manifest["contract_digest"]
                != commit.manifest["contract_digest"]
            ):
                raise ArtifactError("DomainCommit parent has a different contract lineage")

        dependency_refs = commit.manifest.get("dependency_commit_refs")
        if domain == "market_daily":
            required = {"trading_calendar", "security_master"}
            if not isinstance(dependency_refs, dict) or set(dependency_refs) != required:
                raise ArtifactError("market_daily dependency refs are incomplete")
            dependencies: dict[str, DomainCommit] = {}
            for dependency_domain in required:
                dependency_ref = dependency_refs[dependency_domain]
                if not isinstance(dependency_ref, dict) or not isinstance(
                    dependency_ref.get("domain_commit_id"), str
                ):
                    raise ArtifactError("market_daily dependency ref is invalid")
                dependency = _validate_domain_commit_closure(
                    root,
                    dependency_domain,
                    dependency_ref["domain_commit_id"],
                    active,
                    cache,
                )
                if _commit_ref(dependency) != dependency_ref:
                    raise ArtifactError(
                        "market_daily dependency ref does not match its artifact"
                    )
                dependencies[dependency_domain] = dependency
            _validate_market_dependencies(
                commit.rows,
                dependencies["trading_calendar"],
                dependencies["security_master"],
            )
        elif dependency_refs != {}:
            raise ArtifactError("only market_daily may contain dependency commit refs")
    finally:
        active.remove(key)

    cache[key] = commit
    return commit


def validate_domain_commit_closure(
    data_root: str | Path,
    domain: str,
    domain_commit_id: str,
) -> DomainCommit:
    """Validate one commit and every immutable parent/raw/dependency reference."""

    layout = _layout(data_root)
    domain_commit_id = _identity("domain_commit_id", domain_commit_id)
    return _validate_domain_commit_closure(
        layout.root,
        domain,
        domain_commit_id,
        set(),
        {},
    )


def _checked_snapshot_commits(
    data_root: Path,
    domain_commit_ids: Mapping[str, str],
) -> dict[str, DomainCommit]:
    if set(domain_commit_ids) != _REQUIRED_SNAPSHOT_DOMAINS:
        raise ArtifactError("DataSnapshot requires exactly the three Phase 1 market domains")
    cache: dict[tuple[str, str], DomainCommit] = {}
    commits = {
        domain: _validate_domain_commit_closure(
            data_root,
            domain,
            _identity("domain_commit_id", domain_commit_ids[domain]),
            set(),
            cache,
        )
        for domain in MARKET_DOMAINS
    }
    market_dependencies = commits["market_daily"].manifest.get("dependency_commit_refs")
    expected_dependencies = {
        "trading_calendar": _commit_ref(commits["trading_calendar"]),
        "security_master": _commit_ref(commits["security_master"]),
    }
    if market_dependencies != expected_dependencies:
        raise ArtifactError("snapshot domain refs do not match market_daily fixed dependencies")
    _validate_market_dependencies(
        commits["market_daily"].rows,
        commits["trading_calendar"],
        commits["security_master"],
    )
    return commits


def create_snapshot(
    data_root: str | Path,
    domain_commit_ids: Mapping[str, str],
    *,
    snapshot_id: str | None = None,
    created_at: str | None = None,
) -> DataSnapshotRef:
    """Publish an immutable composition of the three required market commits."""

    layout = _layout(data_root)
    commits = _checked_snapshot_commits(layout.root, domain_commit_ids)
    domain_refs = {domain: _commit_ref(commits[domain]) for domain in MARKET_DOMAINS}
    expected_snapshot_id = (
        _identity("snapshot_id", snapshot_id) if snapshot_id is not None else None
    )
    created_at = _timestamp(created_at)
    manifest = {
        "artifact_type": "data_snapshot",
        "schema_version": "data_snapshot.v1",
        "domain_refs": domain_refs,
        "validation_summary": {
            "status": "PASS",
            "required_domains": list(MARKET_DOMAINS),
            "cross_domain": "PASS",
        },
    }
    identity_digest = _identity_digest(manifest, "snapshot_id")
    snapshot_id = _derived_identity("snapshot", identity_digest)
    if expected_snapshot_id is not None and expected_snapshot_id != snapshot_id:
        raise ArtifactError("snapshot_id must equal the derived content-safe identity")
    manifest["snapshot_id"] = snapshot_id
    manifest["identity_digest"] = identity_digest
    manifest["created_at"] = created_at
    target = layout.snapshots / snapshot_id

    def prepare(candidate: Path) -> None:
        _write_manifest(candidate, manifest)

    _publish_directory(
        layout,
        target,
        prepare,
        identity_digest=identity_digest,
    )
    return load_snapshot(layout.root, snapshot_id).ref


def load_snapshot(data_root: str | Path, snapshot_id: str) -> DataSnapshot:
    """Load one exact snapshot and verify its fixed DomainCommit composition."""

    layout = _layout(data_root)
    snapshot_id = _identity("snapshot_id", snapshot_id)
    target = layout.snapshots / snapshot_id
    if not target.exists():
        found = _find_exact_artifact_type(layout, snapshot_id)
        if found is not None:
            raise ArtifactError(f"expected data_snapshot {snapshot_id!r}, found {found}")
    manifest, manifest_digest = _load_manifest(
        layout.root,
        target,
        artifact_type="data_snapshot",
        schema_version="data_snapshot.v1",
        identity_field="snapshot_id",
        identity=snapshot_id,
    )
    domain_refs = manifest.get("domain_refs")
    _validate_manifest_identity(manifest, "snapshot_id", "snapshot", snapshot_id)
    if not isinstance(manifest.get("created_at"), str):
        raise ArtifactError("DataSnapshot created_at is missing")
    _timestamp(manifest["created_at"])
    if not isinstance(domain_refs, dict) or set(domain_refs) != _REQUIRED_SNAPSHOT_DOMAINS:
        raise ArtifactError("DataSnapshot manifest has incomplete domain refs")
    if manifest.get("validation_summary") != {
        "status": "PASS",
        "required_domains": list(MARKET_DOMAINS),
        "cross_domain": "PASS",
    }:
        raise ArtifactError("DataSnapshot validation summary is invalid")
    ids: dict[str, str] = {}
    for domain in MARKET_DOMAINS:
        ref = domain_refs[domain]
        if not isinstance(ref, dict) or not isinstance(ref.get("domain_commit_id"), str):
            raise ArtifactError("DataSnapshot domain ref is invalid")
        ids[domain] = ref["domain_commit_id"]
    commits = _checked_snapshot_commits(layout.root, ids)
    for domain in MARKET_DOMAINS:
        if domain_refs[domain] != _commit_ref(commits[domain]):
            raise ArtifactError("DataSnapshot domain ref digest mismatch")
    return DataSnapshot(DataSnapshotRef(snapshot_id, manifest_digest), manifest)


def _artifact_directories(root: Path, directory: Path) -> tuple[Path, ...]:
    _safe_path(root, directory)
    if not directory.exists():
        return ()
    if not directory.is_dir():
        raise ArtifactError("artifact collection path must be a real directory")
    artifacts: list[Path] = []
    for artifact_dir in sorted(directory.iterdir()):
        _safe_path(root, artifact_dir, closure=directory)
        if not artifact_dir.is_dir():
            raise ArtifactError("artifact collection contains a non-directory entry")
        artifacts.append(artifact_dir)
    return tuple(artifacts)


def _catalog_entries(layout: DataRootLayout) -> list[CatalogEntry]:
    entries: list[CatalogEntry] = []
    closure_cache: dict[tuple[str, str], DomainCommit] = {}
    for artifact_dir in _artifact_directories(layout.root, layout.raw_batches):
        raw = load_raw_batch(layout.root, artifact_dir.name)
        entries.append(
            CatalogEntry(
                "raw_batch",
                raw.ref.raw_batch_id,
                raw.manifest["domain"],
                None,
                (artifact_dir / _MANIFEST).relative_to(layout.root).as_posix(),
                raw.ref.manifest_digest,
            )
        )
    for domain in MARKET_DOMAINS:
        directory = layout.domain_commits(domain)
        for artifact_dir in _artifact_directories(layout.root, directory):
            commit = _validate_domain_commit_closure(
                layout.root,
                domain,
                artifact_dir.name,
                set(),
                closure_cache,
            )
            entries.append(
                CatalogEntry(
                    "domain_commit",
                    commit.ref.commit_id,
                    domain,
                    commit.ref.contract_version,
                    (artifact_dir / _MANIFEST).relative_to(layout.root).as_posix(),
                    commit.manifest_digest,
                )
            )
    for artifact_dir in _artifact_directories(layout.root, layout.snapshots):
        snapshot = load_snapshot(layout.root, artifact_dir.name)
        entries.append(
            CatalogEntry(
                "data_snapshot",
                snapshot.ref.snapshot_id,
                None,
                None,
                (artifact_dir / _MANIFEST).relative_to(layout.root).as_posix(),
                snapshot.ref.manifest_digest,
            )
        )
    return entries


def rebuild_catalog(data_root: str | Path) -> int:
    """Atomically rebuild the disposable SQLite index from artifact manifests."""

    layout = _layout(data_root)
    entries = _catalog_entries(layout)
    _ensure_directory(layout.root, layout.staging)
    if layout.catalog.is_symlink():
        raise ArtifactError("catalog path must not be a symlink")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix="catalog-", suffix=".sqlite", dir=layout.staging
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        connection = sqlite3.connect(temporary)
        try:
            connection.execute(
                """
                CREATE TABLE artifacts (
                    artifact_type TEXT NOT NULL,
                    artifact_id TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    contract_version TEXT,
                    manifest_path TEXT NOT NULL,
                    manifest_digest TEXT NOT NULL,
                    PRIMARY KEY (artifact_type, artifact_id, domain)
                )
                """
            )
            connection.executemany(
                "INSERT INTO artifacts VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (
                        entry.artifact_type,
                        entry.artifact_id,
                        entry.domain or "",
                        entry.contract_version,
                        entry.manifest_path,
                        entry.manifest_digest,
                    )
                    for entry in entries
                ],
            )
            connection.commit()
        finally:
            connection.close()
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, layout.catalog)
        _fsync_directory(layout.root)
    finally:
        if temporary.exists():
            temporary.unlink()
    return len(entries)


def list_catalog(data_root: str | Path) -> tuple[CatalogEntry, ...]:
    """List indexed artifacts; manifests remain the source of truth."""

    layout = _layout(data_root)
    if not layout.catalog.is_file() or layout.catalog.is_symlink():
        raise ArtifactNotFoundError("catalog does not exist; call rebuild_catalog")
    connection = sqlite3.connect(f"file:{layout.catalog}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            """
            SELECT artifact_type, artifact_id, domain, contract_version,
                   manifest_path, manifest_digest
            FROM artifacts
            ORDER BY artifact_type, domain, artifact_id
            """
        ).fetchall()
    finally:
        connection.close()
    return tuple(
        CatalogEntry(row[0], row[1], row[2] or None, row[3], row[4], row[5])
        for row in rows
    )


def lookup_catalog(
    data_root: str | Path,
    artifact_type: str,
    artifact_id: str,
    *,
    domain: str | None = None,
) -> CatalogEntry:
    """Resolve one exact ID in the disposable catalog without fallback semantics."""

    artifact_id = _identity("artifact_id", artifact_id)
    matches = [
        entry
        for entry in list_catalog(data_root)
        if entry.artifact_type == artifact_type
        and entry.artifact_id == artifact_id
        and (domain is None or entry.domain == domain)
    ]
    if len(matches) != 1:
        raise ArtifactNotFoundError("catalog has no unique exact artifact match")
    return matches[0]


__all__ = [
    "ArtifactConflictError",
    "ArtifactError",
    "ArtifactNotFoundError",
    "CatalogEntry",
    "DataSnapshot",
    "DataSnapshotRef",
    "DomainCommit",
    "MarketDomainBuilder",
    "RawBatch",
    "RawBatchRef",
    "create_snapshot",
    "list_catalog",
    "load_domain_commit",
    "load_raw_batch",
    "load_snapshot",
    "lookup_catalog",
    "rebuild_catalog",
    "validate_domain_commit_closure",
    "write_raw_batch",
]
