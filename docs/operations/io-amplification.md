# Reader ancestry and collection checkpoint I/O

This change reduces repeated validation and growing checkpoint rewrites in two
paths. It does not remove the partition integrity pass or alter PIT selection.

During SnapshotReader initialization the existing closure traversal validates
each ancestor. The Reader retains only parent IDs and direct Raw IDs from that
validated traversal. PR7 request coverage walks this in-memory index and loads
each distinct Raw once to obtain request intervals; repeated queries reuse the
Reader-local interval index. It no longer calls full ancestor validation at
each parent. A new Reader performs ordinary fresh closure validation and rejects
subsequent corruption. Ancestor row/payload objects are not retained by the new
index. This bounds ancestry traversal calls, not every mapper replay cost.

Collection keeps `collection.json` as a compatible start/end summary containing
the frozen request plan and aggregate results. Between summaries it writes
`collection-checkpoints/REQUEST_DIGEST.json` for each attempted request. Each
record binds the plan digest and request ID to its Raw ID and sanitized error
type. Atomic replacement and fsync remain per request. Resume overlays these
records on the summary, rejects conflicting completed Raw IDs, then revalidates
the Raw/source-request binding before reuse. Existing v1 summaries need no
migration and remain resumable without checkpoint files.

The aggregate summary can lag while collection is running. Per-request records
are the durable progress checkpoints; callers still receive the complete final
aggregate. Termination after checkpoint persistence but before the aggregate
update preserves completed requests. Persistence failure itself is propagated,
so the caller cannot report collection success without a durable record.

Tests measure serialized checkpoint bytes and validation/load calls rather than
physical device traffic, which also depends on filesystem and OS caches. They
cover 8 versus 16 requests, 3 versus 6 appended history nodes, crash recovery,
old summaries, substituted plans and corrupted Raw. No full-history collection
or production-root rewrite is needed to verify these properties.
