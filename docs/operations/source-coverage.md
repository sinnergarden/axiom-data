# Observation coverage and public security-session scope

New operational builds enable `coverage_state_policy=source_observations.v1`.
A DomainCommit identity binds canonical content and a separate `source_coverage`
projection. The existing `logical_content_digest` continues to hash rows; equal
row digests alone cannot establish NO_CHANGE.

Each projection records an immutable Raw ref, profile version/digest, full
request metadata (including security and date scope), retrieval time, result
status, payload admission/complete qualification, row count and empty-result
flag. It contains no payload bytes. Only newly observed Raw refs are stored in
each node, with a parent coverage digest and resulting state digest. Legacy
parent coverage is bound through its validated commit and transitive Raw refs.
A distinct observation changes identity even if rows are equal; exact replay of
already included Raw preserves state. Offline replay requires the same explicit
parent, ordered Raw and configuration, not a flattened replacement lineage.

`NO_CHANGE` requires equal rows/group state, coverage, configuration, contract,
builder and dependency identities. New Raw cannot disappear through the legacy
row-equality path either. Loader closure recomputes coverage from validated Raw
and parent refs; rehashing a false coverage claim cannot make it authoritative.
The three admission/scope implementation modules are bound in enabled builder
configuration. Existing manifests are never rewritten.

Partial/truncated observations fail shared admission. A known below-limit
response may be complete under its profile; an unknown completeness policy is
stored explicitly as unestablished, never upgraded by the coverage projection.
An empty complete event-source request extends bounded query scope; its domain semantics
remain unchanged (for example no new holder event retains the prior observation).
Old Snapshots retain their original scope.

## Public scope configuration

`bootstrap`, `daily` and `repair` accept `security_session_scope` only for
`adjustment_factors` and `security_capital`, using their registered contracts.
The sole policy is `exchange_security.v1`; explicit `symbols`, `start_session`
and `end_session` are required. Unknown keys, malformed identities/dates,
reversed ranges and unrelated domains fail.

The direct builder and loader share `validate_security_scope`: resolve each
security's own exchange, validate the complete calendar interval, check Raw
request bounds and coverage of each open session within the security identity
interval. Closed and out-of-identity sessions retain the accepted mapper
semantics. A caller's projection cannot exceed its explicit request envelope.
For daily, explicitly supplied older Raw contributes to that envelope along with
new source requests; parent history alone cannot authorize a new request scope.

Genesis rebuilds use identical normalized inputs/configuration/dependencies
through all four entry paths. Incremental repair/daily use the same explicit
parent and builder service; historical parent rows are preserved. Published
reference-source v1 conflicting reobservation rules remain in force.
