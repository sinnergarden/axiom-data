# Historical development records

These files record how earlier releases were built and reviewed. They are not
current operating instructions. Start with the repository [README](../../README.md),
the [system overview](../overview.md), or the [operations runbooks](../operations/).

- `PR6_WORK_LOG.md`, `PR7_WORK_LOG.md`, and `V1_WORK_LOG.md` are original work logs.
- `../adr/` and `../decisions/` retain accepted decisions under their historical names.
- `../../reports/pr*/` retains original review and run evidence; its names and
  contents are not rewritten to match current product modules.

Frozen artifact names, source profile versions, View kinds and old reader modules
remain interpretable. Historical labels in those records are version identifiers,
not instructions to use a separate current implementation path.

## Naming inventory

The repository was searched for `pr[0-9]+`, `dm[0-9]+`, `phase`, `stage` and
`*_WORK_LOG` in tracked paths and text. A match by itself is not a defect:
`stage` also names a legitimate operation state.

| Class | Examples | Treatment |
| --- | --- | --- |
| Persisted compatibility | `scope/pr7_scope.v1.json`, frozen source profiles and contracts, `pr6_fact`/`pr7_fact` artifact kinds, `*_v1` readers, `pr6_*`/`pr7_*` import shims | Keep exact identifiers and historical readers. Public names resolve to them before freezing a plan. |
| Historical evidence | `reports/pr*/`, `adr/`, `decisions/`, work logs and reproduction scripts | Keep evidence unchanged; move root work logs here and label historical scripts. |
| Current product surface | Public View/request names, package exports, active runbooks and README | Use financial, event, reference and other responsibility names. |
| Test compatibility | Frozen fixture and legacy-loader tests, current behavior tests | Keep historical fixture identities; use responsibility names for tests of current behavior. |
