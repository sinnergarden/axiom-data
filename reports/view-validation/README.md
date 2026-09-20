# Dependency-scoped development validation

Base: `da737a7e30a64bb2784c2336c090dabc204652b8`.

`ViewValidationSession` is an explicit, read-only development entrypoint. Ordinary
`SnapshotReader`, `load_snapshot`, public View builders/loaders, operations and
Gates retain full validation. A scoped result is not full Snapshot acceptance.
No production operation has been switched to this path.

## Use

```python
from axiom_data.view_validation import ViewValidationSession
from axiom_data.pr7_views import project

session = ViewValidationSession(data_root, concrete_snapshot_id)
scope = dict(symbols=['688981.SH'], start_session='2025-06-10', end_session='2025-06-13')
config = dict(scope, pit_policy='best_effort_vendor_v1',
              knowledge_cutoff='2025-06-13T23:59:59+08:00')
with session.inputs('pr7_fact', config) as reader:
    payload = project(reader, scope, config['pit_policy'], config['knowledge_cutoff'])
# Repeat this context in the same session to reuse unchanged validated inputs.
```

Use the complete View configuration, including policy and cutoff. Use one session
serially; do not retain or mutate its Reader outside the context. This is for
read-only projections/tests, not artifact publication. Tests that mutate inputs
must use an isolated copy. There is no durable cache or new artifact schema.

## Validation boundaries

The exact Snapshot manifest, identity, declared domain set and summary are checked.
Each required domain is then validated with the existing complete parent, Raw,
partition and fixed-dependency validators. All securities in those domains are
validated before projection. The Snapshot's selected commit refs must match the
validated commits. The original Snapshot manifest/identity is retained.

Dependencies are fixed by View kind, plus transitive contract dependencies:

- Financial Fact: financial, valuation, universe, industry, calendar and security.
- Event Fact: holder, Top10, margin, moneyflow, forecast, calendar and security.
- Unadjusted market Qlib: market, calendar and security.
- Adjusted price, adjusted Qlib and replay: retain the whole existing D-M1 group
  because its Snapshot cross-domain validator is one shared contract. This is
  conservative; splitting that contract is outside this ticket.
- Adjusted Qlib also validates and tracks its explicit adjusted View dependency
  using the existing Qlib input validator.

Only one checked closure is retained per session. Its key binds root, concrete
Snapshot ID, View kind, complete config and package Python/JSON content digests.
The Snapshot digest check binds concrete DomainCommit identities. During initial
validation, existing safe-path checks record every accessed artifact path and its
parent directories. Reuse compares device, inode, mode, size and nanosecond ctime;
it does not use mtime or current/latest to select identities. Missing, replaced,
changed or symlinked inputs invalidate reuse. State is checked before reuse and
again when leaving the context; changes during use raise an error.

This assumes ordinary local filesystem change metadata. It is not protection
against privileged timestamp forgery or hardware corruption without filesystem
state changes. Formal full validation still reads and verifies artifact content.
New processes/sessions perform fresh dependency validation. Code or policy changes
also cause fresh validation. This does not hot-reload Python modules.

## Evidence

24 targeted tests PASS in 16.206s, with no skips:

An additional 30 artifact tests PASS in 2.740s (`test_artifacts` and
`test_pr6_artifacts`). Total: 54/54 PASS.

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests timeout 120s python3 -m unittest -v \
  test_view_validation test_view_operation test_view_resume test_recovery_operation
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests timeout 120s python3 tests/view_validation_probe.py
```

The small real fixture probe identifies its exact Snapshot in `real-probe.json`.
688981.SH, four sessions, full/scoped/reused event payloads match exactly.

| Initialization | Domain loads | Elapsed |
|---|---:|---:|
| Full | 18 | 234.7ms |
| Dependency-scoped | 7 | 72.8ms |
| Same-session reuse | 0 | 5.4ms |

Tests also establish financial projection equality; corrupted unrelated financial
data still fails the full loader, while an event projection does not load that
domain. Relevant canonical, Raw, Snapshot and adjusted View corruption fails;
policy/code changes revalidate; symlinks and changes during use fail closed.

These are warm-cache small-fixture timings, not a measurement of the production
Snapshot. The reported 94-minute initialization was not rerun. Formal data,
bulk state and historical review/benchmark evidence remain unchanged.
Independent review pending.
