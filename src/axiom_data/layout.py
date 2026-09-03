"""Pure path derivation for the axiom-data filesystem contract."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")


class LayoutError(ValueError):
    """Raised when a root or artifact identity would make a path ambiguous."""


def _segment(name: str, value: str) -> str:
    if not isinstance(value, str) or not _SEGMENT.fullmatch(value):
        raise LayoutError(f"{name} must be one safe path segment")
    return value


@dataclass(frozen=True, slots=True)
class DataRootLayout:
    """Names paths without creating directories or resolving mutable pointers."""

    root: Path

    def __post_init__(self) -> None:
        root = Path(self.root)
        if not root.is_absolute():
            raise LayoutError("data root must be absolute")
        if root.is_symlink():
            raise LayoutError("data root must not be a symlink")
        object.__setattr__(self, "root", root)

    @property
    def raw_batches(self) -> Path:
        return self.root / "raw" / "batches"

    @property
    def raw_objects(self) -> Path:
        return self.root / "raw" / "objects"

    def domain_commits(self, domain: str) -> Path:
        return self.root / "canonical" / _segment("domain", domain) / "commits"

    def domain_objects(self, domain: str) -> Path:
        return self.root / "canonical" / _segment("domain", domain) / "objects"

    def derived_commits(self, name: str) -> Path:
        return self.root / "derived" / _segment("derived name", name) / "commits"

    def derived_objects(self, name: str) -> Path:
        return self.root / "derived" / _segment("derived name", name) / "objects"

    @property
    def snapshots(self) -> Path:
        return self.root / "snapshots"

    @property
    def qlib_exports(self) -> Path:
        return self.root / "exports" / "qlib"

    @property
    def patches(self) -> Path:
        return self.root / "patches"

    @property
    def build_provenance(self) -> Path:
        return self.root / "build_provenance"

    @property
    def staging(self) -> Path:
        return self.root / "staging"

    @property
    def reports(self) -> Path:
        return self.root / "reports"

    @property
    def current_pointer(self) -> Path:
        return self.root / "current.json"

    @property
    def catalog(self) -> Path:
        return self.root / "catalog.sqlite"
