# Full-market scoped query validation

Frozen code: cc0c15a; explicit market commit:
`market_daily-80cf02009cdb62a11ad4c7fb47f65aa1adad678c116f7c4019f9f83d5abc6800`.
The complete 9,151,217-row domain validated in 226.229 seconds before comparison.

For 688981.SH, 2025-06-10 through 2025-06-13, both implementations returned the
same four rows and logical digest. Full scan: 153 objects, 3,089,796,676 selected
bytes, 46.334 seconds. Scoped month read: one object, 22,910,111 selected bytes,
0.275 seconds. Peak RSS was 64,644 KiB. See evidence.json for counters.

This isolates projection after complete DomainCommit validation. Snapshot startup
is excluded, and single-run cache state is uncontrolled. It does not establish
daily execution performance or full V1 acceptance. Full closure validation remains
mandatory for public Reader construction. The full suite passed 209 tests.
