# Valuation session projection

The full 8,878,683-row valuation artifact returned the same six rows by both
paths. Full scan: 153 objects, 8,446,083,790 bytes, 65.860s. Session partition scan:
one object, 19,267,221 bytes, 0.122s. Structural validation cost another 284.992s;
peak RSS was 48,928 KiB. These are component measurements, not daily totals.

PR6 View projection now takes that bounded path after the existing full View
scope admission, and calls the same select_revisions policy as direct as_of.
All revisions of each selected daily key stay together. A three-month regression
covers invisible future observations, A/B/A, both PIT policies, complete-object
digest failure and missing-scope admission failure. The existing real PR6
projection's entire payload digest is unchanged. Full suite: 225/225 PASS,
65.159s. Financial and membership selection semantics are unchanged.
