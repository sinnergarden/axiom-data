# Repeated corporate-action observations

A replay of the actual undated action payload in the existing fixture, with a
simulated later retrieval, reproduced the strict merge failure: action key and
revision were identical, but source_ref and first_observed_at differed.
This affects repeated dividend-history scans.

Explicit corporate_action_reobservation.v1 retains the earliest observation for
an identical action revision. Every other canonical field must match. Ties use
immutable Raw IDs. Different source content keeps its distinct action_version;
conflicting canonical content under one revision still fails. Source batches
remain immutable and retained. Repeated canonical state can reuse its parent
under reuse_equal_state.v1 while acquisition records retain the repeated Raw.

The mapping profile/digest is bound in builder configuration and accepted by the
public candidate service. Qualified v1 and v2 formal loading replay the mapping.
Default configs retain their prior strict merge behavior. Both ordinary and
security-batched mapping use the same merge helper.

208/208 tests PASS (63.154s). Tests cover the original counterexample, unchanged
Raw, earliest evidence, reversed-input clean replay, no-change identity, a real
content revision and same-revision corruption. Later retrieval timestamps in
these tests are simulated; no future supplier observation is claimed.

This closes one daily prerequisite. Full-root daily execution and V1 acceptance
remain incomplete; no pointer, merge or push was performed.
