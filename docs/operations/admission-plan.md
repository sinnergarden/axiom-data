# Required-View plan preflight

`validate-admission-plan` checks the complete planned View coverage before any
View build. A full-market Qlib plan paired with three-security Fact probes is
`INCOMPLETE`. Each of the five View kinds must cover every eligible security and
open session of the requested target; interior holes and overlapping duplicate
work are detected with per-security bitsets.

```sh
axiom-data --data-root /absolute/data validate-admission-plan \
  --snapshot snapshot-EXPLICIT_ID --plan admission-plan.json
```

The JSON input has these fields:

- `expected_snapshot_manifest_digest`: the frozen Snapshot reference digest.
- `scope_registry_digest`: value returned by
  `axiom_data.requirement_registry_digest()`. Caller-provided registries cannot
  replace the frozen 56 requirements and 469 dependency mappings.
- `target`: complete `symbols`, `start_session`, `end_session`, with inclusive
  endpoints. This remains in the report even when identity intervals exclude
  some target days from the expected trading-session set.
- `required_view_configs`: all five kinds (`market_qlib`, `market_replay`,
  `adjusted_price`, `financial_fact`, `event_fact`) mapped to their public builder keyword
  arguments, excluding the three target keys. Declare the intended cutoff,
  policy, anchor, universe IDs and industry system here.
- `views`: the existing `materialize-views` label-to-`{kind, config}` mapping.
  Every shard includes its explicit symbols and inclusive date scope. All
  non-scope semantics must agree with the required config for its kind.

The market Qlib target and shards require the full `MARKET_VIEW_FIELDS` set,
unadjusted prices and `best_effort`; adjusted prices use the separate adjusted
View. Financial and event Facts keep their fixed complete public field sets, including forecast
metadata. Field-level sharding is not supported by this preflight. A fixed
adjusted anchor must remain inside every adjusted shard, as its builder requires.

The operation checks the Snapshot v4 manifest identity and digest, then loads
only the security-master and trading-calendar immutable closures. Every requested
security must have a valid exchange mapping and complete calendar-day coverage
for its own exchange. Expected open sessions are clipped to `[list, delist)`;
unknown identities and calendar gaps fail. Other domains are not loaded.
The adjusted anchor must be open and inside each requested security's identity
interval. A shared anchor cannot serve a security already delisted by that day;
such a plan is rejected rather than postponing an inevitable build failure.

Exit 0 and `PLAN_VALIDATED` mean the **plan geometry** is complete. Missing shards
produce `INCOMPLETE` and exit 1; malformed inputs or conflicting semantics fail.
The report retains the exact plan and its digest. It writes no artifacts or
pointers and starts no builds. Capture stdout as the execution record if needed.

`snapshot_closure_validation`, `source_availability_validation` and
`view_payload_validation` remain `PENDING`, `admission` is `NOT_ASSESSED`, and
`ready_for_consumption` is false. A count of 56/469 identifies the registry; it
does not assert that those dependencies have passed full admission. No declared
unavailability can excuse a missing planned interval. Sparse source histories
still require actual source-backed qualification and the existing strict View
loaders; this preflight neither proves buildability nor changes their contracts.

This preflight checks plan geometry only. It does not run full admission or
accept a baseline.
