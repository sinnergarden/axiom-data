# Adjusted-price batch performance evidence

The frozen candidate is `snapshot-1d69dc236a358f1627ae91080c33cf993b9e2acb55bbd2c2cb7a37129ce6b5a5`. The formal root was read-only during these measurements. The paused full-Views operation `v1-full-views-20260923-c291d21-1d69dc-r1` and its four completed Views were not resumed or modified.

## Before

The paused operation at execution SHA `c291d21d7f6c5ca77079188a31baf8acaf6b02cf` measured 134.02, 134.12, and 134.11 seconds for three independent 2014-01-02–2026-09-11 adjusted Views (mean 134.08 seconds). A separate read-only profile used the same frozen code and `000006.SZ` full-history request. Its dependency-scoped initialization took 1,856.69 seconds. The instrumented build took 342.76 seconds; instrumentation makes this unsuitable as an uninstrumented throughput number.

| Instrumented build component | Seconds |
| --- | ---: |
| Market read and security projection | 87.72 |
| Adjustment-factor read and security projection | 68.49 |
| Published artifact replay and validation | 186.52 |
| Artifact publication/write | 0.009 |
| Formula, join, serialization, and other work | about 0.03 |

The single View traversed 306 market partitions (6,186,484,260 logical bytes) and 306 adjustment partitions (6,312,643,992 logical bytes): each 153-month domain was traversed twice. The profiler counted 36.67 million row visits. Complete-partition parsing and merging dominate; file digest checks took about 6.9 instrumented seconds. The profile output rows and View ID matched the already published `000006.SZ` artifact exactly.

## After

The new implementation used the public `materialize_views` operation for 1, 10, and 50 full-history securities from the frozen plan. A single dependency-scoped, fully checked Reader was reused between measurements so the per-View comparison did not repeat initialization. The public operation's Reader constructor was supplied this checked Reader in the measurement harness; the ordinary full-Snapshot initialization path was not altered. Its one-time initialization took 1,828.43 seconds.

| New Views | Build seconds | Market partitions | Adjustment partitions | Logical partition bytes | Source preparation | Artifact replay |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 (serial path) | 135.72 | 306 | 306 | 12,499,128,252 | n/a | 74.25 s |
| 10 (one batch) | 75.61 | 153 | 153 | 6,249,564,126 | 75.17 s | 0.10 s |
| 50 (one batch) | 77.63 | 153 | 153 | 6,249,564,126 | 75.29 s | 0.59 s |

The 50-View batch added 2.02 seconds over the 10-View batch, or 0.051 seconds per additional security in this sample. Its artifact publication calls totalled 0.34 seconds. Logical process reads (`rchar`) were 25.00 GB for the serial View and 12.58 GB for the 50-View batch; physical disk reads were much smaller in the warm-cache batches and are not treated as portable throughput evidence. Peak process RSS was 12,385,920 KiB (11.81 GiB), reached during the shared initialization; the 50-View materialization did not exceed that peak. The implementation holds rows for at most 50 requested securities and releases them after each batch.

The 50-View completed resume took 76.27 seconds and invoked zero builders. It still performed source and artifact validation. The new 50-View outputs for `000001.SZ`, `000002.SZ`, and `000006.SZ` have the same View IDs and byte-identical `rows.json` as the three matching immutable Views from the paused operation. Their manifests are equal after excluding only the run-specific `created_at` field; this covers values, adjustment/missing state, PIT qualification, provenance references, scope, and identity projection.

The two-security fixture independently built serial and batch artifacts with equal complete manifests and row bytes, including one shorter date interval. Removing one completed artifact caused only that security to enter the builder; intact completed artifacts remained validated and skipped. A source file changed during a batch was rejected.

At the measured 50-View rate, 3,580 adjusted Views require roughly 72 bounded batches and about 93 minutes of batch materialization. This is an order-of-magnitude estimate, not a full-operation ETA: scope lengths, cache warmth, full-Snapshot initialization, and other View families have separate costs. The measured adjusted materialization is no longer multi-day.

The old operation plan remains frozen to implementation digest `sha256:9d9762f47a72bb2372e160a74ad820543efc90685985c34ecbe71eb3847a47ba`; the new code's implementation digest is `sha256:427169dcee96a8c9ffd69c10bdc38fb168eef5ca2a7cd3803dbae3fe2eafb683`. This ticket did not rewrite the old checkpoint or resume bulk. A later Runner must use a new operation ID for the new implementation. The four old artifacts remain immutable and readable. Before and after this ticket, the formal checkpoint SHA-256 was `a7839611574d846c65945d3c36bfa569020882554a0b5b3c4d64a1e1a8d8baa1` and its frozen plan SHA-256 was `01cc70f676dab1bbd84d54a521aade9ec44bca0236b39240e040854cc8c4c059`.

Targeted regression suite: 26/26 PASS (`test_adjusted_price_batch`, `test_view_operation`, `test_view_resume`, `test_view_validation`, and `test_fact_publication_validation`). No Raw, DomainCommit, Snapshot, formal View, or formal checkpoint was written.
