"""Immutable filesystem artifacts and rebuildable catalog."""

from __future__ import annotations

import hashlib
import json
import os
import re
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
    ALL_CANONICAL_DOMAINS,
    REFERENCE_SNAPSHOT_DOMAINS,
    MARKET_DOMAINS,
    MarketContractError,
    security_identity_state,
    validate_market_daily_rows,
    validate_security_master_rows,
    validate_trading_calendar_rows,
    validate_reference_snapshot_rows,
)
from axiom_data.domains.reference import DOMAIN_VALIDATORS as DM1_DOMAIN_VALIDATORS
from axiom_data.domains.reference import REFERENCE_DOMAINS
from axiom_data.domains import FUNDAMENTAL_DOMAINS, FUNDAMENTAL_SNAPSHOT_DOMAINS, EVENT_DOMAINS, EVENT_SNAPSHOT_DOMAINS
from axiom_data.domains.events import DOMAIN_VALIDATORS as PR7_DOMAIN_VALIDATORS
from axiom_data.domains.fundamentals import DOMAIN_VALIDATORS as PR6_DOMAIN_VALIDATORS
from axiom_data.layout import DataRootLayout


_MANIFEST = "manifest.json"
_MANIFEST_DIGEST = "manifest.sha256"
_REQUIRED_SNAPSHOT_DOMAINS = frozenset(MARKET_DOMAINS)
_REQUIRED_DM1_SNAPSHOT_DOMAINS = frozenset(REFERENCE_SNAPSHOT_DOMAINS)
_DOMAIN_VALIDATORS = {
    "trading_calendar": validate_trading_calendar_rows,
    "security_master": validate_security_master_rows,
    "market_daily": validate_market_daily_rows,
}
_DOMAIN_VALIDATORS.update(DM1_DOMAIN_VALIDATORS)
_DOMAIN_VALIDATORS.update(PR6_DOMAIN_VALIDATORS)
_DOMAIN_VALIDATORS.update(PR7_DOMAIN_VALIDATORS)
_DOMAIN_DEPENDENCIES = {
    "trading_calendar": (),
    "security_master": (),
    "market_daily": ("trading_calendar", "security_master"),
    "security_status": ("trading_calendar", "security_master", "market_daily"),
    "price_limits": ("trading_calendar", "security_master"),
    "corporate_actions": ("trading_calendar", "security_master"),
    "adjustment_factors": ("trading_calendar", "security_master", "market_daily"),
    "benchmark_daily": ("trading_calendar",),
    "security_capital": ("trading_calendar", "security_master"),
}
_DOMAIN_DEPENDENCIES.update({d: ("security_master",) for d in FUNDAMENTAL_DOMAINS})
_DOMAIN_DEPENDENCIES["valuation_daily"] = ("trading_calendar", "security_master")
_DOMAIN_DEPENDENCIES.update({d: ("security_master",) for d in EVENT_DOMAINS})
_DOMAIN_DEPENDENCIES.update({d: ("trading_calendar", "security_master") for d in ("margin_daily", "moneyflow_daily")})
_SUFFIX_EXCHANGE = {".SH": "SSE", ".SZ": "SZSE"}
_MARKET_BUILDER_REVISION = "market-json-builder.v1"
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_RAW_BATCH_WRITE_SCHEMA = "raw_batch.v2"
_RAW_BATCH_SCHEMAS = ("raw_batch.v1", _RAW_BATCH_WRITE_SCHEMA)


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


class RawBatches(Sequence):
    """Exact ordered identities, loading one verified payload at a time."""
    def __init__(self, root, identities):
        self.root, self.identities = root, tuple(identities)

    def __len__(self):
        return len(self.identities)

    def __getitem__(self, index):
        if isinstance(index, slice):
            return RawBatches(self.root, self.identities[index])
        return load_raw_batch(self.root, self.identities[index])


@dataclass(frozen=True, slots=True)
class DomainCommit:
    ref: DomainCommitRef
    manifest_digest: str
    manifest: dict[str, Any]
    contract: dict[str, Any]
    rows: Sequence[dict[str, Any]]


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
    from axiom_data.verification_cache import observe_validation_path
    observe_validation_path(root, path)
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
    # Per-row round trips otherwise allocate another copy of every schema key.
    # Intern only immutable dictionary keys; nested mutable values remain copied.
    import sys
    return json.loads(_json_bytes(value),object_pairs_hook=lambda pairs:{sys.intern(k):v for k,v in pairs})


def _digest(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def _validated_digest(name: str, value: object) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ArtifactError(f"{name} must be a canonical sha256 digest")
    return value


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
    schema_version: str | Sequence[str],
    identity_field: str,
    identity: str,
) -> tuple[dict[str, Any], str]:
    allowed_schema_versions = (
        (schema_version,) if isinstance(schema_version, str) else tuple(schema_version)
    )
    if not allowed_schema_versions or any(
        not isinstance(version, str) or not version
        for version in allowed_schema_versions
    ):
        raise ArtifactError("allowed artifact schema versions are invalid")
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
        or manifest.get("schema_version") not in allowed_schema_versions
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
        from axiom_data.publication import readonly_publication, seal
        if readonly_publication.get():
            seal(target)
        _fsync_directory(target.parent)


def _payload_file(root: Path, manifest: Mapping[str, Any], artifact_dir: Path) -> bytes:
    payload_files = manifest.get("payload_files")
    if not isinstance(payload_files, list) or len(payload_files) != 1:
        raise ArtifactError("market RawBatch requires exactly one payload file")
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
    source_profile_version: str,
    source_profile_digest: str,
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
    if domain not in ALL_CANONICAL_DOMAINS:
        raise ArtifactError("RawBatch domain must be a registered canonical domain")
    source_profile = _identity("source_profile", source_profile)
    source_profile_version = _identity(
        "source_profile_version", source_profile_version
    )
    source_profile_digest = _validated_digest(
        "source_profile_digest", source_profile_digest
    )
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
        "schema_version": _RAW_BATCH_WRITE_SCHEMA,
        "raw_batch_id": raw_batch_id,
        "domain": domain,
        "source_profile_ref": source_profile,
        "source_profile_version": source_profile_version,
        "source_profile_digest": source_profile_digest,
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
        schema_version=_RAW_BATCH_SCHEMAS,
        identity_field="raw_batch_id",
        identity=raw_batch_id,
    )
    if manifest.get("domain") not in ALL_CANONICAL_DOMAINS:
        raise ArtifactError("RawBatch has an unsupported domain")
    for field in ("source_profile_ref", "collector_code_ref"):
        _identity(field, manifest.get(field))
    if manifest["schema_version"] == _RAW_BATCH_WRITE_SCHEMA:
        _identity("source_profile_version", manifest.get("source_profile_version"))
        _validated_digest(
            "source_profile_digest", manifest.get("source_profile_digest")
        )
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
    for domain in ALL_CANONICAL_DOMAINS:
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
        raise ArtifactError("market RawBatch payload must be a JSON array of row objects")
    return value


def _contract_content(contract_version: str) -> tuple[dict[str, Any], bytes, str]:
    try:
        contract = load_contract(contract_version)
    except ValueError as exc:
        raise ArtifactError(f"unknown contract version: {contract_version!r}") from exc
    content = _json_bytes(contract)
    return contract, content, _digest(content)


def _equal_rows(left, right):
    from itertools import zip_longest
    missing = object()
    return all(a == b for a, b in zip_longest(left, right, fillvalue=missing))


def _validate_domain_rows(domain: str, rows: object, *, contract=None) -> None:
    try:
        from axiom_data.partition_rows import PartitionRows
        validator=_DOMAIN_VALIDATORS[domain]
        if domain=='financial_events' and contract is not None:
            from axiom_data.domains.fundamentals import validate_rows
            validator=lambda batch:validate_rows(domain,batch,contract['contract_version'].rsplit('.',1)[1])
        if domain=='forecast_observations' and contract is not None:
            from axiom_data.domains.events import validate_rows
            validator=lambda batch:validate_rows(domain,batch,contract['contract_version'].rsplit('.',1)[1])
        if domain=='corporate_actions' and contract is not None:
            from axiom_data.domains.reference import validate_corporate_action_rows
            validator=lambda batch:validate_corporate_action_rows(batch,contract['contract_version'].rsplit('.',1)[1])
        if domain=='security_capital' and contract is not None:
            from axiom_data.domains.reference import validate_security_capital_rows
            validator=lambda batch:validate_security_capital_rows(batch,contract['contract_version'].rsplit('.',1)[1])
        if isinstance(rows, PartitionRows):
            rows.validate(validator)
        else:
            validator(rows)
    except MarketContractError as exc:
        raise ArtifactError(f"{domain} rows violate {domain}.v1: {exc}") from exc


def _validate_dm1_observation_refs(
    root: Path,
    domain: str,
    rows: Sequence[Mapping[str, Any]],
    transitive_raw_batch_ids: frozenset[str] | set[str],
    *, verified_evidence=None,
) -> None:
    """Resolve observed provenance within this DomainCommit's RawBatch lineage."""
    for _ in _observation_rows(root, domain, rows, transitive_raw_batch_ids,
                               verified_evidence=verified_evidence):
        pass


def _observation_rows(root, domain, rows, transitive_raw_batch_ids, *, verified_evidence=None):
    """Check provenance while the caller consumes the complete canonical rows."""
    evidence = {} if verified_evidence is None else dict(verified_evidence)
    for row in rows:
        if row.get("pit_qualification") != "observed" and domain not in FUNDAMENTAL_DOMAINS + EVENT_DOMAINS:
            yield row
            continue
        source_refs = [row.get("source_ref")]
        if row.get("boundary_source_ref") is not None:
            source_refs.append(row["boundary_source_ref"])
        for source_ref in source_refs:
            if not isinstance(source_ref,str):
                raise ArtifactError('observed PIT source_ref must be a RawBatch identity')
            if source_ref not in evidence:
                try:
                    raw = load_raw_batch(root, source_ref)
                except ArtifactError as exc:
                    raise ArtifactError(
                        "observed PIT source_ref must resolve to an actual RawBatch"
                    ) from exc
                # Cache verified scalar evidence within this validation call;
                # never retain every large supplier payload or cache across reads.
                evidence[source_ref]=(raw.manifest.get('domain'),raw.ref.raw_batch_id,
                    datetime.fromisoformat(raw.manifest['retrieved_at'].replace('Z','+00:00')))
            raw_domain,raw_id,retrieved_at=evidence[source_ref]
            if raw_domain != domain:
                raise ArtifactError("observed PIT RawBatch belongs to another domain")
            if raw_id not in transitive_raw_batch_ids:
                raise ArtifactError(
                    "observed PIT source_ref is outside the DomainCommit transitive "
                    "RawBatch closure"
                )
            observed_at = datetime.fromisoformat(
                str(row["first_observed_at"]).replace("Z", "+00:00")
            )
            if observed_at < retrieved_at:
                raise ArtifactError(
                    "observed PIT first_observed_at predates its RawBatch retrieval"
                )
        yield row

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
    ref = {
        "raw_batch_id": raw.ref.raw_batch_id,
        "manifest_digest": raw.ref.manifest_digest,
        "payload_digest": raw.manifest["payload_files"][0]["content_digest"],
    }
    if raw.manifest["schema_version"] == _RAW_BATCH_WRITE_SCHEMA:
        ref.update(
            {
                "schema_version": _RAW_BATCH_WRITE_SCHEMA,
                "source_profile_ref": raw.manifest["source_profile_ref"],
                "source_profile_version": raw.manifest["source_profile_version"],
                "source_profile_digest": raw.manifest["source_profile_digest"],
            }
        )
    return ref


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
    from axiom_data.domains.market import _checked_security_identity_state
    validate_security_master_rows(security.rows)
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
        identity_state = _checked_security_identity_state(security_row, row["session"])
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


def _validate_pr6_dependencies(domain, rows, dependencies):
    securities = {r["symbol"]: r for r in dependencies["security_master"].rows}
    daily = domain in ("valuation_daily", "margin_daily", "moneyflow_daily")
    sessions = ({(r["exchange"], r["session"]) for r in dependencies["trading_calendar"].rows
                 if r["is_open"]} if daily else set())
    outside_calendar = False
    for row in rows:
        if row['symbol'] not in securities:
            raise ArtifactError("financial fact has no security identity")
        if daily and (securities[row['symbol']]['exchange'], row['session']) not in sessions:
            outside_calendar = True
    # Preserve security-error precedence while checking the complete input once.
    if outside_calendar:
        raise ArtifactError("valuation fact is outside open calendar coverage")


class MarketDomainBuilder:
    """BuildExecutor that publishes one immutable canonical domain commit."""

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
        dependency_commit_ids: Mapping[str, str] | None = None,
        created_at: str | None = None,
    ) -> None:
        if domain not in ALL_CANONICAL_DOMAINS:
            raise ArtifactError("builder domain must be a registered canonical domain")
        self.layout = _layout(data_root)
        self.domain = domain
        if builder_config is not None and not isinstance(builder_config, Mapping):
            raise ArtifactError("builder_config must be a mapping")
        self.builder_config = _json_copy(builder_config or {})
        self.builder_config.setdefault('coverage_state_policy', 'source_observations.v2')
        if self.builder_config.get('coverage_state_policy') == 'source_observations.v2':
            from axiom_data.source_completeness import current_contract_binding
            from axiom_data.contracts import writable_contracts
            self.builder_config['source_completeness_binding'] = current_contract_binding()
            self.builder_config['writable_contracts_digest'] = _digest(_json_bytes(writable_contracts()))
        if self.builder_config.get('coverage_state_policy') in {'source_observations.v1', 'source_observations.v2'} or 'security_session_scope' in self.builder_config:
            self.builder_config['source_admission_implementation'] = {
                name: _digest(Path(__file__).with_name(name + '.py').read_bytes())
                for name in ('source_completeness', 'source_coverage', 'public_source_scope')
            }
        if self.builder_config.get('storage_policy') == 'domain_time_blocks.v1':
            import inspect
            module = inspect.getmodule(type(self))
            self.builder_config['storage_implementation'] = {
                'publisher': _digest(Path(__file__).read_bytes()),
                'partitions': _digest(Path(__file__).with_name('partitions.py').read_bytes()),
                'builder': _digest(Path(module.__file__).read_bytes()),
            }
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
        if dependency_commit_ids is not None and not isinstance(
            dependency_commit_ids, Mapping
        ):
            raise ArtifactError("dependency_commit_ids must be a mapping")
        explicit_dependencies = {
            str(name): _identity(f"{name}_commit_id", identity)
            for name, identity in (dependency_commit_ids or {}).items()
        }
        legacy_dependencies = {
            name: identity
            for name, identity in {
                "trading_calendar": self.calendar_commit_id,
                "security_master": self.security_master_commit_id,
            }.items()
            if identity is not None
        }
        for name, identity in legacy_dependencies.items():
            if name in explicit_dependencies and explicit_dependencies[name] != identity:
                raise ArtifactError("legacy and explicit dependency identities disagree")
            explicit_dependencies[name] = identity
        self.dependency_commit_ids = explicit_dependencies
        self.created_at = _timestamp(created_at)

    def _build_rows(
        self,
        contract: Mapping[str, Any],
        parent_rows: Sequence[Mapping[str, Any]],
        raw_batches: Sequence[RawBatch],
    ) -> list[dict[str, Any]]:
        return _merge_rows(self.domain, contract, parent_rows, raw_batches)

    def __call__(self, request: BuildRequest) -> DomainCommitRef:
        from axiom_data.contracts import require_writable_contract, writable_contracts
        try:
            require_writable_contract(self.domain, request.contract_version)
        except ValueError as exc:
            raise ArtifactError(str(exc)) from exc
        if self.builder_config.get('coverage_state_policy') != writable_contracts()['source_coverage']['current']:
            raise ArtifactError('LEGACY_CONTRACT_READ_ONLY: current source coverage contract required for publication')
        if request.patch_ids:
            raise ArtifactError("this RawBatch contract does not support non-empty patch_ids")
        contract, contract_content, contract_digest = _contract_content(
            request.contract_version
        )
        if contract.get("domain") != self.domain:
            raise ArtifactError("build contract does not belong to the executor domain")

        raw_batches = RawBatches(self.layout.root, request.raw_batch_ids)
        coverage_policy = self.builder_config.get('coverage_state_policy')
        coverage_enabled = coverage_policy in {'source_observations.v1', 'source_observations.v2'}
        if 'coverage_state_policy' in self.builder_config and not coverage_enabled:
            raise ArtifactError('unsupported source coverage state policy')
        coverage_observations = []
        financial_raw_refs = []
        coverage_pages = {}
        if coverage_policy == 'source_observations.v2' and self.domain == 'industry_membership':
            from axiom_data.source_completeness import page_evidence
            coverage_pages = page_evidence(raw_batches)
        for raw in raw_batches:
            if self.domain == "financial_events":
                financial_raw_refs.append(_raw_ref(raw))
            if raw.manifest.get("domain") != self.domain:
                raise ArtifactError(
                    f"RawBatch {raw.ref.raw_batch_id!r} belongs to another domain"
                )
            if raw.manifest.get("status") != "success":
                raise ArtifactError(f"RawBatch {raw.ref.raw_batch_id!r} is not successful")
            if coverage_enabled:
                from axiom_data.source_coverage import observation
                coverage_observations.append(observation(raw, policy=coverage_policy,
                                                         evidence=coverage_pages.get(raw.ref.raw_batch_id)))
            else:
                from axiom_data.source_completeness import validate_raw_completeness
                validate_raw_completeness(raw)

        parent = None
        parent_raw_batch_ids: frozenset[str] = frozenset()
        if request.parent_commit is not None:
            parent, parent_raw_batch_ids = _validated_domain_commit_with_raw_closure(
                self.layout.root, self.domain, request.parent_commit
            )
            if (
                parent.ref.contract_version != request.contract_version
                or parent.manifest.get("contract_digest") != contract_digest
            ):
                raise ArtifactError("parent commit belongs to a different contract lineage")
            if parent.manifest.get('source_coverage', {}).get('schema_version') != coverage_policy:
                raise ArtifactError('LEGACY_CONTRACT_READ_ONLY: source coverage upgrade requires a new lineage')

        calendar = None
        security = None
        dependencies: dict[str, dict[str, Any]] = {}
        required_dependencies = set(_DOMAIN_DEPENDENCIES[self.domain])
        if set(self.dependency_commit_ids) != required_dependencies:
            raise ArtifactError(
                f"{self.domain} requires explicit dependency commits "
                f"{sorted(required_dependencies)!r}"
            )
        loaded_dependencies = {
            name: validate_domain_commit_closure(
                self.layout.root, name, self.dependency_commit_ids[name]
            )
            for name in _DOMAIN_DEPENDENCIES[self.domain]
        }
        dependencies = {
            name: _commit_ref(commit) for name, commit in loaded_dependencies.items()
        }
        calendar = loaded_dependencies.get("trading_calendar")
        security = loaded_dependencies.get("security_master")
        if coverage_policy == 'source_observations.v2':
            from axiom_data.source_completeness import requalify_sources
            inherited = dict(self.dependency_commit_ids)
            if request.parent_commit is not None:
                inherited[self.domain] = request.parent_commit
            requalify_sources(self.layout.root, inherited)
        if 'security_session_scope' in self.builder_config:
            from axiom_data.public_source_scope import validate_security_scope
            validate_security_scope(self.domain, self.builder_config, raw_batches, loaded_dependencies)

        self.parent_group_states = parent.manifest.get("group_states", []) if parent else []
        rows = self._build_rows(
            contract,
            parent.rows if parent is not None else (),
            raw_batches,
        )
        from axiom_data.partition_rows import PartitionRows, WHOLE_STATE_DOMAINS, rows_digest
        published_partitions = None
        rows_content = None
        if self.builder_config.get('storage_policy') == 'domain_time_blocks.v1':
            from axiom_data.partitions import POLICY, publish_partitions
            logical_digest = rows_digest(rows)
            published_partitions = publish_partitions(self.layout, self.domain, rows)
            if self.domain not in WHOLE_STATE_DOMAINS:
                rows = PartitionRows(self.layout, self.domain, {
                    'partition_policy': POLICY, 'output_files': [],
                    'partitions': published_partitions, 'logical_content_digest': logical_digest,
                }, contract)
        else:
            rows_content = _json_bytes(rows)
            logical_digest = _digest(rows_content)
        if self.domain in REFERENCE_DOMAINS + FUNDAMENTAL_DOMAINS + EVENT_DOMAINS:
            _validate_dm1_observation_refs(
                self.layout.root,
                self.domain,
                rows,
                parent_raw_batch_ids
                | (frozenset(request.raw_batch_ids) if self.domain == "financial_events"
                   else frozenset(raw.ref.raw_batch_id for raw in raw_batches)),
            )
        if self.domain in FUNDAMENTAL_DOMAINS:
            from axiom_data.fundamentals_source import FundamentalsBuilder
            replay = FundamentalsBuilder(self.layout.root, self.domain, builder_config=self.builder_config)
            replay.parent_group_states = self.parent_group_states
            if not _equal_rows(replay._build_rows(contract, parent.rows if parent else (), raw_batches), rows):
                raise ArtifactError("financial staged rows differ from their source mapping")
        if self.domain in EVENT_DOMAINS:
            from axiom_data.event_source import EventBuilder
            replay = EventBuilder(self.layout.root, self.domain, builder_config=self.builder_config)
            if not _equal_rows(replay._build_rows(contract, parent.rows if parent else (), raw_batches), rows):
                raise ArtifactError("event staged rows differ from RawBatch mapping")
        builder_config_digest = _digest(_json_bytes(self.builder_config))
        builder_implementation_ref = _builder_implementation_ref(self)
        parent_ref = _commit_ref(parent) if parent is not None else None
        coverage = None
        if coverage_enabled:
            from axiom_data.source_coverage import state as coverage_state
            coverage = coverage_state(parent, parent_raw_batch_ids, coverage_observations, policy=coverage_policy)
        if (parent is not None and self.builder_config.get('no_change_policy') == 'reuse_equal_state.v1'
            and logical_digest == parent.manifest['logical_content_digest']
            and set(request.raw_batch_ids) <= parent_raw_batch_ids
            and self.builder_config == parent.manifest['builder_config']
            and builder_implementation_ref == parent.manifest['builder_implementation_ref']
            and dependencies == parent.manifest['dependency_commit_refs']
            and (not coverage_enabled or coverage['state_digest'] == parent.manifest.get('source_coverage', {}).get('state_digest'))
            and (request.contract_version != 'universe_membership.v3' or
                 self.group_states == parent.manifest['group_states'])):
            return parent.ref
        raw_refs = (financial_raw_refs if self.domain == "financial_events"
                    else [_raw_ref(raw) for raw in raw_batches])
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
                "cross_domain": "PASS" if dependencies else "NOT_APPLICABLE",
            },
        }
        if self.builder_config.get('storage_policy') == 'domain_time_blocks.v1':
            from axiom_data.partitions import POLICY, publish_partitions
            manifest['schema_version'] = 'domain_commit.v2'
            manifest['partition_policy'] = POLICY
            manifest['partitions'] = published_partitions
            manifest['output_files'] = []
        if request.contract_version == 'universe_membership.v3':
            if self.group_states != replay.group_states:
                raise ArtifactError('group states differ from raw replay')
            from axiom_data.domains.fundamentals import validate_group_states
            validate_group_states(rows,self.group_states)
            manifest['group_states'] = self.group_states
        if coverage_enabled:
            manifest['source_coverage'] = coverage
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
            if manifest['schema_version'] == 'domain_commit.v2':
                from axiom_data.partitions import read_partitions
                staged_rows = (PartitionRows(self.layout, self.domain, manifest, contract)
                    if self.domain not in WHOLE_STATE_DOMAINS else
                    read_partitions(self.layout, self.domain, manifest, contract))
            else:
                _write_file(candidate / "rows.json", rows_content)
                staged_rows = json.loads((candidate / "rows.json").read_bytes())
            _validate_domain_rows(self.domain, staged_rows,contract=contract)
            if self.domain in FUNDAMENTAL_DOMAINS + EVENT_DOMAINS:
                _validate_pr6_dependencies(self.domain, staged_rows, loaded_dependencies)
            if self.domain == "market_daily" and calendar is not None and security is not None:
                _validate_market_dependencies(staged_rows, calendar, security)
            if _digest((candidate / "contract.json").read_bytes()) != contract_digest:
                raise ArtifactError("staged contract digest mismatch")
            if rows_digest(staged_rows) != logical_digest:
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
    if domain not in ALL_CANONICAL_DOMAINS:
        raise ArtifactError("domain must be a registered canonical domain")
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
        schema_version=("domain_commit.v1", "domain_commit.v2"),
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
        raise ArtifactError("DomainCommit uses an unsupported contract") from exc

    if manifest['schema_version'] == 'domain_commit.v2':
        from axiom_data.partition_rows import PartitionRows, STREAM_ROW_THRESHOLD, WHOLE_STATE_DOMAINS
        from axiom_data.partitions import read_partitions
        partition_rows = PartitionRows(layout, domain, manifest, contract)
        # Membership overlap/group checks and calendar predecessor checks remain
        # whole-state validators. Other contracts have row checks plus global keys.
        if len(partition_rows) >= STREAM_ROW_THRESHOLD and domain not in WHOLE_STATE_DOMAINS:
            rows = partition_rows
        else:
            rows = read_partitions(layout, domain, manifest, contract)
    else:
        output_files = manifest.get("output_files")
        if not isinstance(output_files, list) or len(output_files) != 1:
            raise ArtifactError("market DomainCommit requires exactly one output file")
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
    _validate_domain_rows(domain, rows,contract=contract)
    expected_cross_domain = (
        "PASS" if _DOMAIN_DEPENDENCIES[domain] else "NOT_APPLICABLE"
    )
    if manifest.get("validation_summary") != {
        "status": "PASS",
        "contract_rows": len(rows),
        "cross_domain": expected_cross_domain,
    }:
        raise ArtifactError("DomainCommit validation summary is invalid")
    return DomainCommit(ref, manifest_digest, manifest, contract,
                        tuple(rows) if isinstance(rows, list) else rows)


def _validate_domain_commit_closure(
    root: Path,
    domain: str,
    domain_commit_id: str,
    active: set[tuple[str, str]],
    cache: dict[tuple[str, str], DomainCommit],
    raw_closure_cache: dict[tuple[str, str], frozenset[str]],
) -> DomainCommit:
    """Postorder traversal; caches live only for this verified closure, never across reads."""
    requested = (domain, domain_commit_id)
    stack = [(requested, None)]
    entered = set()
    try:
        while stack:
            key, loaded = stack.pop()
            if key in cache:
                continue
            if loaded is not None:
                active.remove(key)
                entered.remove(key)
                _validate_domain_commit_node(root, *key, active, cache,
                                             raw_closure_cache, loaded=loaded)
                continue
            if key in active:
                raise ArtifactError("DomainCommit lineage contains a cycle")
            commit = load_domain_commit(root, *key)
            children = []
            parent = commit.manifest.get("parent_commit_ref")
            if parent is not None:
                if (not isinstance(parent, dict) or parent.get("domain") != key[0]
                    or not isinstance(parent.get("domain_commit_id"), str)):
                    raise ArtifactError("DomainCommit parent ref is invalid")
                children.append((key[0], parent["domain_commit_id"]))
            dependencies = commit.manifest.get("dependency_commit_refs")
            if not isinstance(dependencies, dict) or set(dependencies) != set(_DOMAIN_DEPENDENCIES[key[0]]):
                raise ArtifactError("DomainCommit dependency refs are incomplete")
            for name, ref in dependencies.items():
                if not isinstance(ref, dict) or not isinstance(ref.get("domain_commit_id"), str):
                    raise ArtifactError("DomainCommit dependency ref is invalid")
                children.append((name, ref["domain_commit_id"]))
            active.add(key)
            entered.add(key)
            stack.append((key, commit))
            stack.extend((child, None) for child in reversed(children))
    finally:
        active.difference_update(entered)
    return cache[requested]


def _validate_domain_commit_node(
    root: Path,
    domain: str,
    domain_commit_id: str,
    active: set[tuple[str, str]],
    cache: dict[tuple[str, str], DomainCommit],
    raw_closure_cache: dict[tuple[str, str], frozenset[str]],
    *, loaded: DomainCommit,
) -> DomainCommit:
    key = (domain, domain_commit_id)
    if key in cache:
        return cache[key]
    if key in active:
        raise ArtifactError("DomainCommit lineage contains a cycle")

    commit = loaded
    active.add(key)
    try:
        raw_refs = commit.manifest.get("ordered_raw_batch_refs")
        if not isinstance(raw_refs, list):
            raise ArtifactError("DomainCommit ordered raw refs are invalid")
        raw_ids: set[str] = set()
        coverage_policy = commit.manifest['builder_config'].get('coverage_state_policy')
        coverage_enabled = coverage_policy in {'source_observations.v1', 'source_observations.v2'}
        if 'coverage_state_policy' in commit.manifest['builder_config'] and not coverage_enabled:
            raise ArtifactError('unsupported coverage state policy')
        if ('source_coverage' in commit.manifest) != coverage_enabled:
            raise ArtifactError('source coverage manifest/policy mismatch')
        coverage_observations = []
        # This node already verifies every declared Raw below. Preserve only
        # the scalar provenance needed by the subsequent canonical-row check.
        # The dictionary is invocation-local; no supplier payload is retained.
        raw_evidence = {}
        coverage_pages = {}
        if coverage_policy == 'source_observations.v2' and domain == 'industry_membership':
            from axiom_data.source_completeness import page_evidence
            coverage_pages = page_evidence(RawBatches(root, [ref['raw_batch_id'] for ref in raw_refs]))
        if 'security_session_scope' in commit.manifest['builder_config']:
            from axiom_data.reference_source import _SECURITY_SESSION_SCOPE_DOMAINS
            if domain not in _SECURITY_SESSION_SCOPE_DOMAINS:
                raise ArtifactError('unsupported security session scope domain')
        for raw_ref in raw_refs:
            if not isinstance(raw_ref, dict) or not isinstance(
                raw_ref.get("raw_batch_id"), str
            ):
                raise ArtifactError("DomainCommit raw ref is invalid")
            raw = load_raw_batch(root, raw_ref["raw_batch_id"])
            if raw.manifest.get("domain") != domain or _raw_ref(raw) != raw_ref:
                raise ArtifactError("DomainCommit raw ref does not match its artifact")
            raw_ids.add(raw.ref.raw_batch_id)
            raw_evidence[raw.ref.raw_batch_id] = (
                raw.manifest['domain'], raw.ref.raw_batch_id,
                datetime.fromisoformat(raw.manifest['retrieved_at'].replace('Z', '+00:00')),
            )
            if coverage_enabled:
                from axiom_data.source_coverage import observation
                coverage_observations.append(observation(raw, policy=coverage_policy,
                                                         evidence=coverage_pages.get(raw.ref.raw_batch_id)))
            else:
                # No-coverage manifests predate the writable v2 contract.
                from axiom_data.source_completeness import validate_raw_completeness_legacy
                validate_raw_completeness_legacy(raw)
        if len(raw_ids) != len(raw_refs):
            raise ArtifactError("DomainCommit raw refs must not contain duplicates")
        transitive_raw_batch_ids = set(raw_ids)

        if commit.manifest.get("ordered_patch_refs") != []:
            raise ArtifactError("market DomainCommit patch refs must be empty")

        parent_ref = commit.manifest.get("parent_commit_ref")
        parent = None
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
                raw_closure_cache,
            )
            if _commit_ref(parent) != parent_ref:
                raise ArtifactError("DomainCommit parent ref does not match its artifact")
            if (
                parent.ref.contract_version != commit.ref.contract_version
                or parent.manifest["contract_digest"]
                != commit.manifest["contract_digest"]
            ):
                raise ArtifactError("DomainCommit parent has a different contract lineage")
            transitive_raw_batch_ids.update(
                raw_closure_cache[(domain, parent.ref.commit_id)]
            )

        if coverage_enabled:
            from axiom_data.source_coverage import state as coverage_state
            expected_coverage = coverage_state(parent,
                raw_closure_cache[(domain, parent.ref.commit_id)] if parent is not None else frozenset(),
                coverage_observations, policy=coverage_policy)
            if commit.manifest['source_coverage'] != expected_coverage:
                raise ArtifactError('source coverage differs from validated Raw lineage')

        canonical_rows = commit.rows
        if domain in FUNDAMENTAL_DOMAINS + EVENT_DOMAINS:
            # Mapping replay below already consumes every canonical row. Check
            # its provenance in that pass instead of parsing all partitions again.
            canonical_rows = _observation_rows(
                root, domain, commit.rows, transitive_raw_batch_ids,
                verified_evidence=raw_evidence,
            )
        elif domain in REFERENCE_DOMAINS:
            _validate_dm1_observation_refs(
                root,
                domain,
                commit.rows,
                transitive_raw_batch_ids,
                verified_evidence=raw_evidence,
            )

        if domain in FUNDAMENTAL_DOMAINS:
            from axiom_data.fundamentals_source import FundamentalsBuilder
            previous_rows = parent.rows if parent_ref is not None else ()
            replay = FundamentalsBuilder(root, domain, builder_config=commit.manifest["builder_config"])
            replay.parent_group_states = parent.manifest.get("group_states", []) if parent_ref is not None else []
            expected_rows = replay._build_rows(commit.contract, previous_rows,
                RawBatches(root, [ref['raw_batch_id'] for ref in raw_refs]))
            if commit.ref.contract_version == "universe_membership.v3" and commit.manifest.get("group_states") != replay.group_states:
                raise ArtifactError("universe group states differ from RawBatch mapping")
            if commit.ref.contract_version == "universe_membership.v3":
                from axiom_data.domains.fundamentals import validate_group_states
                validate_group_states(commit.rows,commit.manifest["group_states"])
            if not _equal_rows(expected_rows, canonical_rows):
                raise ArtifactError("financial canonical rows differ from their RawBatch mapping")
            del expected_rows

        if domain in EVENT_DOMAINS:
            from axiom_data.event_source import EventBuilder
            replay = EventBuilder(root, domain, builder_config=commit.manifest["builder_config"])
            expected_rows = replay._build_rows(commit.contract, parent.rows if parent_ref is not None else (),
                RawBatches(root, [ref['raw_batch_id'] for ref in raw_refs]))
            if not _equal_rows(expected_rows, canonical_rows):
                raise ArtifactError("event canonical rows differ from RawBatch mapping")
            del expected_rows

        if commit.ref.contract_version in {'corporate_actions.v2', 'security_capital.v2'} or (domain == 'price_limits' and commit.manifest['builder_config'].get('limit_qualification')) or (domain == 'corporate_actions' and commit.manifest['builder_config'].get('corporate_action_reobservation')):
            from axiom_data.reference_source import TushareReferenceBuilder
            replay=TushareReferenceBuilder(root,domain,builder_config=commit.manifest['builder_config'],
                dependency_commit_ids={d:ref['domain_commit_id'] for d,ref in commit.manifest['dependency_commit_refs'].items()})
            expected_rows=replay._build_rows(commit.contract,parent.rows if parent_ref is not None else (),
                RawBatches(root,[ref['raw_batch_id'] for ref in raw_refs]))
            if not _equal_rows(expected_rows,commit.rows):
                raise ArtifactError('qualified D-M1 observations differ from RawBatch mapping')
            del expected_rows

        dependency_refs = commit.manifest.get("dependency_commit_refs")
        required = set(_DOMAIN_DEPENDENCIES[domain])
        if required:
            if not isinstance(dependency_refs, dict) or set(dependency_refs) != required:
                raise ArtifactError(f"{domain} dependency refs are incomplete")
            dependencies: dict[str, DomainCommit] = {}
            for dependency_domain in required:
                dependency_ref = dependency_refs[dependency_domain]
                if not isinstance(dependency_ref, dict) or not isinstance(
                    dependency_ref.get("domain_commit_id"), str
                ):
                    raise ArtifactError(f"{domain} dependency ref is invalid")
                dependency = _validate_domain_commit_closure(
                    root,
                    dependency_domain,
                    dependency_ref["domain_commit_id"],
                    active,
                    cache,
                    raw_closure_cache,
                )
                if _commit_ref(dependency) != dependency_ref:
                    raise ArtifactError(
                        f"{domain} dependency ref does not match its artifact"
                    )
                dependencies[dependency_domain] = dependency
            if 'security_session_scope' in commit.manifest['builder_config']:
                from axiom_data.public_source_scope import validate_security_scope
                validate_security_scope(domain, commit.manifest['builder_config'],
                    RawBatches(root, [ref['raw_batch_id'] for ref in raw_refs]), dependencies)
            if domain in FUNDAMENTAL_DOMAINS + EVENT_DOMAINS:
                _validate_pr6_dependencies(domain, commit.rows, dependencies)
            if domain == "market_daily":
                _validate_market_dependencies(
                    commit.rows,
                    dependencies["trading_calendar"],
                    dependencies["security_master"],
                )
        elif dependency_refs != {}:
            raise ArtifactError(f"{domain} must not contain dependency commit refs")
    finally:
        active.remove(key)

    raw_closure_cache[key] = frozenset(transitive_raw_batch_ids)
    cache[key] = commit
    return commit


def _validated_domain_commit_with_raw_closure(
    data_root: str | Path,
    domain: str,
    domain_commit_id: str,
) -> tuple[DomainCommit, frozenset[str]]:
    layout = _layout(data_root)
    domain_commit_id = _identity("domain_commit_id", domain_commit_id)
    from axiom_data.verification_cache import closure_cache
    cache, raw_closure_cache = closure_cache(layout.root)
    commit = _validate_domain_commit_closure(
        layout.root,
        domain,
        domain_commit_id,
        set(),
        cache,
        raw_closure_cache,
    )
    return commit, raw_closure_cache[(domain, domain_commit_id)]


def validate_domain_commit_closure(
    data_root: str | Path,
    domain: str,
    domain_commit_id: str,
) -> DomainCommit:
    """Validate one commit and every immutable parent/raw/dependency reference."""

    return _validated_domain_commit_with_raw_closure(
        data_root, domain, domain_commit_id
    )[0]


def _checked_snapshot_commits(
    data_root: Path,
    domain_commit_ids: Mapping[str, str],
    *, validation_cache=None, lineage_index=None, required_domains=None,
) -> dict[str, DomainCommit]:
    requested_domains = set(domain_commit_ids)
    if requested_domains == _REQUIRED_SNAPSHOT_DOMAINS:
        ordered_domains = MARKET_DOMAINS
    elif requested_domains == _REQUIRED_DM1_SNAPSHOT_DOMAINS:
        ordered_domains = REFERENCE_SNAPSHOT_DOMAINS
    elif requested_domains == set(FUNDAMENTAL_SNAPSHOT_DOMAINS):
        ordered_domains = FUNDAMENTAL_SNAPSHOT_DOMAINS
    elif requested_domains == set(EVENT_SNAPSHOT_DOMAINS):
        ordered_domains = EVENT_SNAPSHOT_DOMAINS
    else:
        raise ArtifactError("DataSnapshot requires exactly a registered domain set")
    if required_domains is not None:
        selected = set(required_domains)
        if not selected or not selected <= requested_domains:
            raise ArtifactError('View dependencies are absent from Snapshot')
        if any(not set(_DOMAIN_DEPENDENCIES[d]) <= selected for d in selected):
            raise ArtifactError('View dependency set is not closed')
        ordered_domains = tuple(d for d in ordered_domains if d in selected)
    from axiom_data.verification_cache import closure_cache
    cache, raw_closure_cache = closure_cache(data_root) if validation_cache is None else validation_cache
    commits = {
        domain: _validate_domain_commit_closure(
            data_root,
            domain,
            _identity("domain_commit_id", domain_commit_ids[domain]),
            set(),
            cache,
            raw_closure_cache,
        )
        for domain in ordered_domains
    }
    if 'market_daily' in commits:
        market_dependencies = commits["market_daily"].manifest.get("dependency_commit_refs")
        expected_dependencies = {
            "trading_calendar": _commit_ref(commits["trading_calendar"]),
            "security_master": _commit_ref(commits["security_master"]),
        }
        if market_dependencies != expected_dependencies:
            raise ArtifactError("snapshot domain refs do not match market_daily fixed dependencies")
    for domain in ordered_domains:
        expected = {
            dependency: _commit_ref(commits[dependency])
            for dependency in _DOMAIN_DEPENDENCIES[domain]
        }
        if commits[domain].manifest.get("dependency_commit_refs") != expected:
            raise ArtifactError(
                f"snapshot domain refs do not match {domain} fixed dependencies"
            )
    # Each closure already checked its rows against these exact dependencies.
    # The ref comparisons above bind that check to the Snapshot composition;
    # scanning the entire market again would repeat the identical validation.
    if set(REFERENCE_SNAPSHOT_DOMAINS).issubset(ordered_domains):
        try:
            validate_reference_snapshot_rows(commits)
        except MarketContractError as exc:
            raise ArtifactError("D-M1 snapshot cross-domain validation failed") from exc
    if lineage_index is not None:
        # Retain only verified ancestry metadata for this Reader, not ancestor
        # row/payload objects or a process-wide validation cache.
        for domain in EVENT_DOMAINS:
            identity = commits[domain].ref.commit_id if domain in commits else None
            while identity is not None:
                key = (domain, identity)
                if key in lineage_index:
                    break
                commit = cache[key]
                parent = (commit.manifest['parent_commit_ref'] or {}).get('domain_commit_id')
                lineage_index[key] = {
                    'parent_commit_id': parent,
                    'raw_batch_ids': tuple(ref['raw_batch_id'] for ref in commit.manifest['ordered_raw_batch_refs']),
                }
                identity = parent
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
    ordered_domains = tuple(commits)
    domain_refs = {domain: _commit_ref(commits[domain]) for domain in ordered_domains}
    expected_snapshot_id = (
        _identity("snapshot_id", snapshot_id) if snapshot_id is not None else None
    )
    created_at = _timestamp(created_at)
    manifest = {
        "artifact_type": "data_snapshot",
        "schema_version": (
            "data_snapshot.v4" if ordered_domains == EVENT_SNAPSHOT_DOMAINS else
            "data_snapshot.v3" if ordered_domains == FUNDAMENTAL_SNAPSHOT_DOMAINS else
            "data_snapshot.v2" if ordered_domains == REFERENCE_SNAPSHOT_DOMAINS else "data_snapshot.v1"
        ),
        "domain_refs": domain_refs,
        "validation_summary": {
            "status": "PASS",
            "required_domains": list(ordered_domains),
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
    return _load_snapshot_with_commits(layout.root, snapshot_id, checked_commits=commits)[0].ref


def load_snapshot(data_root: str | Path, snapshot_id: str) -> DataSnapshot:
    """Load one exact snapshot and verify its fixed DomainCommit composition."""
    return _load_snapshot_with_commits(data_root, snapshot_id)[0]


def _load_snapshot_with_commits(data_root: str | Path, snapshot_id: str, *, checked_commits=None, validation_cache=None, lineage_index=None, required_domains=None):
    """Return the already checked commits from this one Snapshot validation."""

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
        schema_version=("data_snapshot.v1", "data_snapshot.v2", "data_snapshot.v3", "data_snapshot.v4"),
        identity_field="snapshot_id",
        identity=snapshot_id,
    )
    domain_refs = manifest.get("domain_refs")
    _validate_manifest_identity(manifest, "snapshot_id", "snapshot", snapshot_id)
    if not isinstance(manifest.get("created_at"), str):
        raise ArtifactError("DataSnapshot created_at is missing")
    _timestamp(manifest["created_at"])
    schema_version = manifest.get("schema_version")
    ordered_domains = (
        MARKET_DOMAINS if schema_version == "data_snapshot.v1" else
        EVENT_SNAPSHOT_DOMAINS if schema_version == "data_snapshot.v4" else
        FUNDAMENTAL_SNAPSHOT_DOMAINS if schema_version == "data_snapshot.v3" else REFERENCE_SNAPSHOT_DOMAINS
    )
    registered_domains = frozenset(ordered_domains)
    if not isinstance(domain_refs, dict) or set(domain_refs) != registered_domains:
        raise ArtifactError("DataSnapshot manifest has incomplete domain refs")
    if manifest.get("validation_summary") != {
        "status": "PASS",
        "required_domains": list(ordered_domains),
        "cross_domain": "PASS",
    }:
        raise ArtifactError("DataSnapshot validation summary is invalid")
    ids: dict[str, str] = {}
    for domain in ordered_domains:
        ref = domain_refs[domain]
        if not isinstance(ref, dict) or not isinstance(ref.get("domain_commit_id"), str):
            raise ArtifactError("DataSnapshot domain ref is invalid")
        ids[domain] = ref["domain_commit_id"]
    # Publication has just validated this exact composition. Validate the stored
    # Snapshot manifest against those commits without repeating all source replay.
    # Public loads supply no cache and always validate the complete closure.
    commits = _checked_snapshot_commits(layout.root, ids, validation_cache=validation_cache,
                                      lineage_index=lineage_index, required_domains=required_domains) if checked_commits is None else checked_commits
    expected_domains = set(ids) if required_domains is None else set(required_domains)
    if set(commits) != expected_domains:
        raise ArtifactError('checked Snapshot composition is incomplete')
    for domain in commits:
        if domain_refs[domain] != _commit_ref(commits[domain]):
            raise ArtifactError("DataSnapshot domain ref digest mismatch")
    return DataSnapshot(DataSnapshotRef(snapshot_id, manifest_digest), manifest), commits


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
    from axiom_data.consumption import SnapshotReader, _load_qlib_view
    from axiom_data.views import _load_adjusted_price_view, _load_market_replay_view

    entries: list[CatalogEntry] = []
    closure_cache: dict[tuple[str, str], DomainCommit] = {}
    raw_closure_cache: dict[tuple[str, str], frozenset[str]] = {}
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
    for domain in ALL_CANONICAL_DOMAINS:
        directory = layout.domain_commits(domain)
        for artifact_dir in _artifact_directories(layout.root, directory):
            commit = _validate_domain_commit_closure(
                layout.root,
                domain,
                artifact_dir.name,
                set(),
                closure_cache,
                raw_closure_cache,
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
    readers = {}
    for artifact_dir in _artifact_directories(layout.root, layout.snapshots):
        # Reuse only this catalog call's verified commits. Snapshot composition
        # and every View's own files/semantics are still independently checked.
        reader = object.__new__(SnapshotReader)
        reader.data_root = layout.root
        reader._verified_lineage = {}
        reader.snapshot, reader.commits = _load_snapshot_with_commits(
            layout.root, artifact_dir.name, validation_cache=(closure_cache, raw_closure_cache),
            lineage_index=reader._verified_lineage)
        snapshot = reader.snapshot
        readers[snapshot.ref.snapshot_id] = reader
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
    def view_reader(artifact_dir):
        # This read only routes to a checked Snapshot; the loader validates the
        # complete manifest/digest and binds that Snapshot before trusting rows.
        try:
            routing = json.loads(_safe_path(layout.root, artifact_dir / _MANIFEST,
                                           closure=artifact_dir).read_bytes())
            return readers[routing['snapshot_ref']['snapshot_id']], routing['schema_version']
        except (KeyError, TypeError, ValueError, OSError) as exc:
            raise ArtifactError('catalog View has an invalid Snapshot ref') from exc

    for name, artifact_type, loader in (
        ("adjusted_price", "adjusted_price_view", _load_adjusted_price_view),
        ("market_replay", "market_replay_view", _load_market_replay_view),
    ):
        directory = layout.derived_commits(name)
        for artifact_dir in _artifact_directories(layout.root, directory):
            reader, _ = view_reader(artifact_dir)
            view = loader(layout.root, artifact_dir.name, checked_reader=reader)
            version = (
                view.manifest["derived_contract"]["contract_version"]
                if name == "adjusted_price"
                else view.manifest["schema_version"]
            )
            entries.append(
                CatalogEntry(
                    artifact_type,
                    view.ref.view_id,
                    name,
                    version,
                    (artifact_dir / _MANIFEST).relative_to(layout.root).as_posix(),
                    view.ref.manifest_digest,
                )
            )
    from axiom_data.financial_views import load_financial_fact_view_with_reader
    for artifact_dir in _artifact_directories(layout.root, layout.derived_commits("pr6_fact")):
        reader, version = view_reader(artifact_dir)
        view = load_financial_fact_view_with_reader(layout.root, artifact_dir.name,
            checked_reader=reader if version in {'pr6_fact_view.v2','pr6_fact_view.v3','pr6_fact_view.v4'} else None)
        for artifact_type in ("pr6_fact_view", "qlib_view"):
            entries.append(CatalogEntry(artifact_type, view.ref.view_id, "pr6_fact",
                view.manifest["schema_version"],
                (artifact_dir / _MANIFEST).relative_to(layout.root).as_posix(), view.ref.manifest_digest))
    from axiom_data.event_views import load_event_fact_view_with_reader
    for artifact_dir in _artifact_directories(layout.root, layout.derived_commits("pr7_fact")):
        reader, _ = view_reader(artifact_dir)
        view = load_event_fact_view_with_reader(layout.root, artifact_dir.name, checked_reader=reader)
        for artifact_type in ("pr7_fact_view", "qlib_view"):
            entries.append(CatalogEntry(artifact_type, view.ref.view_id, "pr7_fact",
                view.manifest["schema_version"],
                (artifact_dir / _MANIFEST).relative_to(layout.root).as_posix(), view.ref.manifest_digest))
    for artifact_dir in _artifact_directories(layout.root, layout.qlib_exports):
        reader, _ = view_reader(artifact_dir)
        view = _load_qlib_view(layout.root, artifact_dir.name, checked_reader=reader)
        entries.append(
            CatalogEntry(
                "qlib_view",
                view.ref.view_id,
                "qlib",
                view.manifest["schema_version"],
                (artifact_dir / _MANIFEST).relative_to(layout.root).as_posix(),
                view.ref.manifest_digest,
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


# Compatibility exports for historical callers.
PR6_DOMAINS = FUNDAMENTAL_DOMAINS
PR7_DOMAINS = EVENT_DOMAINS
DM1_REFERENCE_DOMAINS = REFERENCE_DOMAINS
DM1_SNAPSHOT_DOMAINS = REFERENCE_SNAPSHOT_DOMAINS
PR6_SNAPSHOT_DOMAINS = FUNDAMENTAL_SNAPSHOT_DOMAINS
PR7_SNAPSHOT_DOMAINS = EVENT_SNAPSHOT_DOMAINS
validate_dm1_snapshot_rows = validate_reference_snapshot_rows
