"""Public application boundary for immutable domain builds."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from axiom_data.contracts import load_contract


_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_FLOATING_IDENTITIES = frozenset(
    {
        "current",
        "current.json",
        "latest",
        "latest.json",
        "live",
        "live.json",
        "mtime",
    }
)


class BuildContractError(ValueError):
    """Raised before an invalid build request reaches a writer."""


def _validate_identity(name: str, value: object, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise BuildContractError(f"{name} must be a string")
    if value != value.strip() or not _IDENTITY.fullmatch(value):
        raise BuildContractError(f"{name} must be an explicit artifact identity")
    if value.casefold() in _FLOATING_IDENTITIES:
        raise BuildContractError(f"{name} must not use a floating identity")
    return value


def _identities(name: str, values: object) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise BuildContractError(f"{name} must be a sequence of identities")
    frozen = tuple(
        _validate_identity(f"{name}[{index}]", value) for index, value in enumerate(values)
    )
    if len(frozen) != len(set(frozen)):
        raise BuildContractError(f"{name} must not contain duplicate identities")
    return frozen  # type: ignore[return-value]


def _registered_contract(name: str, value: object) -> tuple[str, str]:
    contract_version = _validate_identity(name, value)
    assert contract_version is not None
    try:
        contract = load_contract(contract_version)
    except ValueError as exc:
        raise BuildContractError(f"{name} must name a registered contract") from exc
    domain = contract.get("domain")
    if not isinstance(domain, str):
        raise BuildContractError(f"registered contract {contract_version!r} has no domain")
    return contract_version, domain


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
            _validate_identity("parent_commit", self.parent_commit, optional=True),
        )
        object.__setattr__(self, "raw_batch_ids", _identities("raw_batch_ids", self.raw_batch_ids))
        object.__setattr__(self, "patch_ids", _identities("patch_ids", self.patch_ids))
        contract_version, _ = _registered_contract("contract_version", self.contract_version)
        object.__setattr__(
            self,
            "contract_version",
            contract_version,
        )
        if self.parent_commit is None and not self.raw_batch_ids:
            raise BuildContractError("a genesis build requires at least one raw batch")
        if self.parent_commit is not None and not self.raw_batch_ids and not self.patch_ids:
            raise BuildContractError("an incremental build requires at least one raw batch or patch")


@dataclass(frozen=True, slots=True)
class DomainCommitRef:
    """Identity returned by a concrete immutable domain builder."""

    domain: str
    commit_id: str
    contract_version: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "domain", _validate_identity("domain", self.domain))
        object.__setattr__(self, "commit_id", _validate_identity("commit_id", self.commit_id))
        contract_version, contract_domain = _registered_contract(
            "contract_version", self.contract_version
        )
        if self.domain != contract_domain:
            raise BuildContractError("commit contract does not belong to the commit domain")
        object.__setattr__(
            self,
            "contract_version",
            contract_version,
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
        object.__setattr__(self, "domain", _validate_identity("domain", self.domain))
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
        _, contract_domain = _registered_contract("contract_version", request.contract_version)
        if contract_domain != self.domain:
            raise BuildContractError("contract version does not belong to the build domain")
        from axiom_data.contracts import require_writable_contract
        try:
            require_writable_contract(self.domain, request.contract_version)
        except ValueError as exc:
            raise BuildContractError(str(exc)) from exc
        result = self.executor(request)
        if not isinstance(result, DomainCommitRef):
            raise BuildContractError("executor must return DomainCommitRef")
        returned_domain = _validate_identity("result.domain", result.domain)
        _validate_identity("result.commit_id", result.commit_id)
        returned_contract, returned_contract_domain = _registered_contract(
            "result.contract_version", result.contract_version
        )
        if returned_domain != self.domain:
            raise BuildContractError("executor returned a commit for another domain")
        if returned_contract_domain != returned_domain:
            raise BuildContractError("executor returned a contract for another domain")
        if returned_contract != request.contract_version:
            raise BuildContractError("executor returned a different contract version")
        return result
