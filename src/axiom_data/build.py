"""Public application boundary for immutable domain builds."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_DYNAMIC_ALIASES = frozenset({"current", "latest", "live"})


class BuildContractError(ValueError):
    """Raised before an invalid build request reaches a writer."""


def _identity(name: str, value: object, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise BuildContractError(f"{name} must be a string")
    if value != value.strip() or not _IDENTITY.fullmatch(value):
        raise BuildContractError(f"{name} must be an explicit artifact identity")
    if value.casefold() in _DYNAMIC_ALIASES:
        raise BuildContractError(f"{name} must not use a dynamic alias")
    return value


def _identities(name: str, values: object) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise BuildContractError(f"{name} must be a sequence of identities")
    frozen = tuple(_identity(f"{name}[{index}]", value) for index, value in enumerate(values))
    if len(frozen) != len(set(frozen)):
        raise BuildContractError(f"{name} must not contain duplicate identities")
    return frozen  # type: ignore[return-value]


@dataclass(frozen=True, slots=True)
class BuildRequest:
    """Validated, immutable inputs for one domain commit build."""

    parent_commit: str | None
    raw_batch_ids: tuple[str, ...]
    patch_ids: tuple[str, ...]
    contract_version: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "parent_commit",
            _identity("parent_commit", self.parent_commit, optional=True),
        )
        object.__setattr__(self, "raw_batch_ids", _identities("raw_batch_ids", self.raw_batch_ids))
        object.__setattr__(self, "patch_ids", _identities("patch_ids", self.patch_ids))
        object.__setattr__(
            self,
            "contract_version",
            _identity("contract_version", self.contract_version),
        )
        if not self.raw_batch_ids and not self.patch_ids:
            raise BuildContractError("a build requires at least one raw batch or patch")


@dataclass(frozen=True, slots=True)
class DomainCommitRef:
    """Identity returned by a concrete immutable domain builder."""

    domain: str
    commit_id: str
    contract_version: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "domain", _identity("domain", self.domain))
        object.__setattr__(self, "commit_id", _identity("commit_id", self.commit_id))
        object.__setattr__(
            self,
            "contract_version",
            _identity("contract_version", self.contract_version),
        )


class BuildExecutor(Protocol):
    """Concrete persistence boundary supplied by a later implementation PR."""

    def __call__(self, request: BuildRequest) -> DomainCommitRef: ...


@dataclass(frozen=True, slots=True)
class BuildApplication:
    """Validate a public build call, then delegate to one explicit executor."""

    domain: str
    executor: BuildExecutor

    def __post_init__(self) -> None:
        object.__setattr__(self, "domain", _identity("domain", self.domain))
        if not callable(self.executor):
            raise BuildContractError("executor must be callable")

    def build(
        self,
        parent_commit: str | None,
        raw_batch_ids: Sequence[str],
        patch_ids: Sequence[str],
        contract_version: str,
    ) -> DomainCommitRef:
        request = BuildRequest(
            parent_commit=parent_commit,
            raw_batch_ids=_identities("raw_batch_ids", raw_batch_ids),
            patch_ids=_identities("patch_ids", patch_ids),
            contract_version=contract_version,
        )
        if request.contract_version.partition(".")[0] != self.domain:
            raise BuildContractError("contract version does not belong to the build domain")
        result = self.executor(request)
        if not isinstance(result, DomainCommitRef):
            raise BuildContractError("executor must return DomainCommitRef")
        if result.domain != self.domain:
            raise BuildContractError("executor returned a commit for another domain")
        if result.contract_version != request.contract_version:
            raise BuildContractError("executor returned a different contract version")
        return result
