"""Explicit development-only dependency validation; full loaders stay unchanged."""
from collections import OrderedDict
from contextlib import contextmanager
from importlib.resources import files

from axiom_data.artifacts import (
    ArtifactError, _identity, _layout, _digest, _json_bytes,
    _load_snapshot_with_commits, _DOMAIN_DEPENDENCIES,
)
from axiom_data.domains import DM1_SNAPSHOT_DOMAINS, PR6_DOMAINS, PR7_DOMAINS
from axiom_data.verification_cache import validation_paths, file_state


def _dependencies(kind, config):
    domains = {
        'pr6_fact': set(PR6_DOMAINS),
        'pr7_fact': set(PR7_DOMAINS),
        'market_qlib': {'market_daily'},
        'adjusted_price': {'adjustment_factors'},
        'market_replay': {'market_daily', 'security_status', 'price_limits', 'corporate_actions'},
    }
    if kind not in domains:
        raise ArtifactError('unknown View validation kind')
    selected = domains[kind] | {'security_master', 'trading_calendar'}
    if kind == 'market_qlib' and config.get('price_basis') == 'anchor_adjusted':
        selected.add('adjustment_factors')
    # Existing D-M1 Snapshot checks are a single cross-domain contract. Keep the
    # whole group when needed, rather than dropping any of those checks.
    if selected & (set(DM1_SNAPSHOT_DOMAINS) - {'security_master','trading_calendar','market_daily'}):
        selected.update(DM1_SNAPSHOT_DOMAINS)
    while True:
        expanded = selected | {dep for d in selected for dep in _DOMAIN_DEPENDENCIES[d]}
        if expanded == selected:
            return selected
        selected = expanded


class ViewValidationSession:
    """One in-process development session, holding at most one checked closure.

    Use inputs() around a small View projection. The yielded Reader is scoped to
    these dependencies, not proof of full Snapshot validity. Do not retain it
    outside the context. Gate/public loaders always do their own full validation.
    """
    def __init__(self, data_root, snapshot_id):
        self.root = _layout(data_root).root
        self.snapshot_id = _identity('snapshot_id', snapshot_id)
        self._cached = None

    @contextmanager
    def inputs(self, kind, config):
        from axiom_data.consumption import SnapshotReader
        source = files('axiom_data')
        paths = [p for p in sorted(source.rglob('*')) if p.is_file() and p.suffix in {'.py','.json'}]
        code_states = {p:file_state(p) for p in paths}
        code = {p.relative_to(source).as_posix(): _digest(p.read_bytes()) for p in paths}
        if any(file_state(p) != state for p,state in code_states.items()):
            raise ArtifactError('View implementation changed during validation')
        key = _digest(_json_bytes([str(self.root), self.snapshot_id, kind, config, code]))
        domains = _dependencies(kind, config)
        cached = self._cached
        if cached is None or cached[0] != key or any(file_state(p) != state for p,state in cached[2].items()):
            self._cached = None
            with validation_paths() as observed:
                observed.update(code_states)
                reader = object.__new__(SnapshotReader)
                reader.data_root = self.root
                reader._verified_lineage = {}
                reader._security_projection = OrderedDict()
                reader._security_projection_bytes = 0
                reader.snapshot, reader.commits = _load_snapshot_with_commits(
                    self.root, self.snapshot_id, required_domains=domains,
                    lineage_index=reader._verified_lineage, validation_cache=({}, {}))
                if kind == 'market_qlib':
                    from axiom_data.consumption import _validate_qlib_inputs
                    _validate_qlib_inputs(reader, **config)
            if any(file_state(p) != state for p,state in observed.items()):
                raise ArtifactError('View validation inputs changed during validation')
            reader._view_validation_paths = observed
            self._cached = (key, reader, observed)
        _, reader, observed = self._cached
        try:
            yield reader
        finally:
            if any(file_state(p) != state for p,state in observed.items()):
                self._cached = None
                raise ArtifactError('View validation inputs changed during use')
