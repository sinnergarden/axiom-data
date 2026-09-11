# Verify an offline restored closure

`verify_recovery` validates a root restored from an explicitly identified backup.
It does not copy files, collect source data, or approve a V1 baseline. Preserve
Raw payloads/manifests, canonical objects/commits and their parents/dependencies,
the exact Snapshot, and required View artifacts. Install the pinned implementation
and retain the original source profiles, contracts and configuration. A partial
copy is rejected by the ordinary closure validators.

```sh
axiom-data --data-root /absolute/restored-data verify-recovery \
  --snapshot snapshot-EXPLICIT_ID --run-id recovery-EXPLICIT_RUN \
  --plan recovery-plan.json
```

The JSON plan contains `expected_snapshot_manifest_digest` and a nonempty `views`
mapping. Each named View has exactly `kind`, `view_id`, and `manifest_digest`.
Kinds are `market_qlib`, `market_replay`, `adjusted_price`, `pr6_fact`, and
`pr7_fact`. Manifest digests come from the frozen source references, not from an
unverified replacement artifact. Published PR6 v1 and current v2 use their own
loaders and identity rules.

An optional `rebuild_views` mapping names required `market_qlib`, `market_replay`,
or `adjusted_price` Views and uses the same `{kind, config}` descriptors as
`materialize-views`. PR6/PR7 Fact Views require restoration of their original
published bytes: their builders cannot pin the original manifest creation time,
so exact rebuild requests for these kinds are rejected before execution.
Rebuilds must reproduce
the declared IDs **and manifest digests**. Preserve the original creation-time
argument when that builder includes it in the manifest. Changing implementation
or rebuild configuration is not an exact restoration of the old artifact.

Validation and rebuilding run under `deny_external_data`: network and external
legacy-data attempts are blocked, including an attempted access swallowed by a
callee. The catalog is rebuilt atomically after required artifacts validate;
canonical/Raw files and pointers are not removed or rewritten. Concurrent runs
of the same recovery identity are rejected.

The operation writes `operations/RUN/recovery.json`. Only a fully verified run
returns `RECOVERY_VALIDATED` and CLI exit 0. Failures record the failed stage and
error type, retain immutable outputs, and return exit 1. Reusing the same run ID
requires the same plan and revalidates artifacts; a previous success is not a
verification cache. `ready_for_consumption` remains false because recovery alone
does not establish full admission or baseline acceptance.

Use a separate restored root for the final recovery rehearsal and corruption
probes. Small fixture tests are implemented; full-root V1 recovery remains
`NOT_VALIDATED` until the independent artifact gate verifies its actual result.
