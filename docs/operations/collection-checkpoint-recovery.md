# Current-validation collection checkpoints

`collect_requests` reads existing v1 summaries and v1/v2 per-request checkpoints,
checks the frozen plan, loads each referenced immutable Raw, and applies current
source binding/completeness admission before any collection. It persists upgraded
`collection_checkpoint.v2` records. Historical completed/failed flags do not
supply admission authority.

Each request has one state in `request_states`:

| State | Meaning and next action |
| --- | --- |
| PENDING | Not yet attempted; use the same request checkpoint. |
| VALID_COMPLETE | This request/page passes current admission; expose its Raw in completed. |
| NEEDS_RETRY | Rejected response or source failure; retry the unbound request. |
| NEEDS_SPLIT | Supported bounded split exists; expose the retained parent and split plan, not a completed Raw. |
| FAILED_TERMINAL | No safe supported recovery (for example corrupt immutable bytes or an indivisible saturated scope). |
| SUPERSEDED_BY_SPLIT | Parent replaced by frozen child requests; keep parent Raw for audit, never for canonical input. |

The compatible `completed` and `failed` maps are projections of those states and
never overlap after normalization. Page/request admission is not full domain
coverage or baseline acceptance; required page-series closure, source policies
and Gate A remain separate checks.

Before bootstrap executes split children it durably writes the parent's
SUPERSEDED_BY_SPLIT state, the child run ID and request IDs. The child plan/run
identity is deterministic. Every resume revalidates the parent against that
split and the children against their own checkpoints. Successful children are
reused; interrupted children continue. Recovered parents never become completed.
Only admitted leaf Raw IDs appear in bootstrap's completed_raw_batch_ids.

Supported splits do not trigger the transport-failure circuit. Unattempted
requests remain in their original batch checkpoint, rather than moving to an
unrecorded secondary run. A crash after a durable child or request checkpoint
therefore does not require recollecting that completed request.

Explicit observed_raw_batch_ids remains an immutable binding. An invalid binding
on a new run is rejected before publication; an existing run first normalizes
its checkpoint. It cannot silently replace a bound observation by collecting a
new one. Unbound retry/split recovery continues within the existing frozen plan.
No user needs to delete a legacy checkpoint or choose a new run ID to recover
an ordinary supported legacy completed-at-cap request.

Integrity failures stay fail-closed. Raw payloads and manifests are never edited.
Per-request records remain atomic and fsynced; aggregate summaries are written
at stage boundaries, preserving linear checkpoint write growth.
