# Candidate verification reuse

`assemble_candidate` now scopes verified canonical and Raw closure nodes to one
writer-controlled invocation and the matching data root. The copied frozen PR7
fixture produced the same `CANDIDATE_BUILT` result while canonical-load calls
dropped from 65 to 19; measured time dropped from 0.5673211719986284 seconds to
0.2029224029975012 seconds. The before/after JSON, profiling script and full
test log are recorded in `evidence.json` by their immutable `/tmp` paths and
SHA-256 values.

The focused regressions prove that a later candidate call revalidates mutated
Raw bytes, a fresh `SnapshotReader` still rejects the mutation, and an identical
artifact identity in another data root cannot hit the cache. A failed scoped
call also unwinds its context. No persistent correctness cache or cross-call
trust is introduced.

The existing full suite log records 231/231 PASS in 71.322 seconds, including
both focused candidate-cache tests. This is bounded copied-fixture validation
reuse evidence; full-root performance and baseline acceptance remain outside
this evidence. `baseline_status` is `NOT_ACCEPTED`.
