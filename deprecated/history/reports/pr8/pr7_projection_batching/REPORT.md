# PR7 projection selection batching

The frozen three-security/four-session PR7 projection called as_of 180 times,
once for every leaf and security/session. Projection now selects once per domain
and exchange-valid session, then reuses the same leaf metadata function as the
direct Reader. The measured call count is 20. The entire payload digest,
including metadata and session ordering, is unchanged (see evidence.json).

Best-effort and operational policies at two cutoffs produce exactly the same
metadata as individual public leaf_fact calls for all 15 leaves. Existing holder
late-period revision, exchange/calendar fail-closed and v1 compatibility tests
remain in the full suite: 219/219 PASS, 66.642 seconds.

This bounded component measurement excludes Reader startup and does not establish
full-root daily or full-history View performance. Selection remains the existing
PIT policy; each requested security retains its own exchange calendar and source
request coverage check.
