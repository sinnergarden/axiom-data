# Explicit initial universe acquisition

`universe_acquisition.policy = complete_bootstrap.v1` applies only to new
universe_membership.v3 lineages. `raw_batch_ids` identifies the complete initial
acquisition; all listed Raw must be present. Requests must have disjoint date
intervals within each universe. The builder config and implementation digest
bind this decision to the artifact identity.

The initial complete group observation becomes known at the maximum actual Raw
retrieval timestamp. Raw timestamps and supplier payloads remain unchanged.
Historical vendor availability remains best_effort. Subsequent observations keep
separate actual timestamps; a backdated extra observation cannot enter this
acquisition. An incremental rebuild replays the same explicit initial bundle.

An empty ranged initial query supplies no effective observation. Its Raw identity
is retained in the group state's raw_refs and unobserved_requests; it cannot
clear an existing member set or establish earlier coverage. Coverage starts at
the earliest actual effective observation. A wholly unobserved acquisition fails.
Explicit complete single-date empty observations retain the accepted empty-set
semantics. Source gaps outside this initial policy continue to fail closed.

The existing group_state_ref supplies each row's complete Raw closure, avoiding
repeated state_raw_refs in every member row. Group selection, complete member
validation, and projection order remain unchanged. Published older artifacts
retain their frozen configurations and interpretation.
