# Security session scope policy

`security_session_scope=exchange_security.v1` is an explicit opt-in mapping for
`adjustment_factors` and `security_capital`. A supplier row becomes a canonical
session fact only when its frozen `security_master` identity state is
`within_identity_interval` and its frozen exchange calendar row has
`is_open=true`. Known pre-list, delist-day/post-delist, and closed-calendar rows
are excluded; an unknown or missing identity/calendar fails closed. Raw batches
remain unchanged and their existing `source_ref` values are retained for rows
that are emitted.

The paths below identify historical forensic evidence, not the runtime data root.
The complete 153-month diagnostic evidence is the externally produced report
[历史诊断证据](../../deprecated/history/index.md)
(SHA-256
`55bf1c85c603d6b72bf1e7c56d6b91658392b84701934ed1d4543504bc56c0ae`) and its
curated exact-key references
[历史诊断证据](../../deprecated/history/index.md)
(SHA-256
`da251b857bf419aa58125968504a7a3a043f81194a8f3f600d6ee190bcd1c198`). The
report is `COMPLETE` for 153/153 months: it found 16,747 adjustment-factor rows
on/after delist and two closed-calendar rows; it found no security-capital rows
in those categories. The closed examples are `689009.SH` on `2021-09-20` and
`2021-09-21`, both SSE `is_open=false`, with source ref
`tushare-adjustment_factors-adj_factor-bb01ecaf9fb05043f0078e974b750dd0bed2c4428798fe6c80f16649ff7069d0`.
