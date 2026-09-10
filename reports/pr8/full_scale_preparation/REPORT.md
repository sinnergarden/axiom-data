# Full-scale source and storage preparation

Stage 3/5 continues. These are validated source/mapping/storage checkpoints;
they do not constitute full admission or an accepted V1 baseline.

## Source qualification

- Holder v3 explicitly qualifies source rows with both report period and count
  null as `unkeyed_empty_observation`. Raw retains every row and its exclusion
  index. No economic report period or holder fact is manufactured. A missing
  report period with a non-null count still fails. Inspection exposes the Raw
  qualification. Three real response fixtures cover this source condition.
- `tushare_fina_indicator.v1` applies request date bounds to report `end_date`,
  as demonstrated by the frozen 000001.SZ response: a 2017-12-31 period is
  returned under 2013–2017 bounds with a 2018-03-15 announcement. Availability
  remains the announcement; the older profile retains its original validation.
  This observation does not change other financial endpoint date semantics.
- `session_suspension.v2` parses ordered explicit interval lists and accepts a
  traded day only with positive finite volume for the same security/session.
  The 09:30–15:00 interval excludes the opening match; no execution timestamp
  is inferred from the daily bar. All 991 timed halt rows through the explicit
  checkpoint in qualification_refs.json passed against actual daily evidence.
- Empty timing does not universally prove a full-day halt: 000055.SZ on
  2015-07-06 has an untimed S record and a positive-volume daily bar. The
  [issuer's announcement](https://www.fangda.com/uploadfiles/%E5%A4%8D%E7%89%8C%E5%85%AC%E5%91%8A%EF%BC%882015-32%EF%BC%89.pdf)
  confirms an intraday halt. Its original PDF is frozen by SHA-256. v2 keeps
  this as a traded day with unspecified halt boundaries. Explicit intervals
  covering all trading opportunities still conflict with a traded bar and fail.
  Empty timing without a daily bar retains the prior full-day inference.
- Old profiles and Raw payloads remain unchanged. New profile digests and
  implementation code participate in build configuration/identity. Connection
  errors receive bounded retries; mapping errors are not transport retries.

## Bounded materialization

RawBatches holds exact ordered identities and verifies payloads when read.
The opt-in market security batching produces the same 295,050 canonical rows
and logical digest as eager mapping for 100 real securities (500 RawBatches).
Independent process measurements: eager peak RSS 1,833,136 KiB, batched 371,140
KiB, a 79.75% reduction; mapping times 19.97 and 20.24 seconds respectively.
This is a mapping probe, not full bootstrap or daily performance acceptance.

DomainCommit v2 keeps its existing manifest, partition bytes and identity
projection. Large non-membership row sets are replayable partition sequences.
Each read verifies object bytes, count, partition membership, global sort/key
uniqueness, canonical logical digest and ordinary row semantics. The builder
publishes complete objects before replay validation, so replay need not coexist
with another full in-memory canonical copy. Failed validation cannot publish a
DomainCommit; unreferenced immutable objects may remain for later inspection.

Membership group/overlap validation and calendar predecessor validation remain
whole-state checks. Raw provenance validation caches only verified scalar refs
within one call and revalidates on the next call. Corruption, malformed JSON,
wrong partition keys/counts and v1/v2 dispatch have regression coverage.

Full all-domain canonical/admission, baseline, daily/T+1/revision/no-change,
full-root offline recovery and acceptance Notebook remain outstanding. The
active acquisition jobs use their frozen code/plan and separate source roots;
no active job's inputs were replaced. No baseline pointer, merge or push.

Full suite: 189/189 PASS, zero skips, 138.450 seconds; git diff check passed.

D-M1 follow-up: opt-in `dm1_source_partitioning=security.v1` maps one
security at a time while preserving the original global observation time and
full-scope provenance hashes. All six reference domains match eager mapping,
including source gaps and empty endpoint responses; incomplete request sets
still fail. Dependency validation is cached only inside one mapping invocation.
Full follow-up suite: 190/190 PASS, zero skips, 137.532 seconds.

Full market mapping scan: 3,601 securities inspected under the frozen v2
mapping; 3,597 passed, with failures confined to four securities. v3 passed
all four remaining real histories. R events now leave full-day halt key
construction before uniqueness checks, consistent with D-M1. They do not
assert a traded bar. Three exact timing signatures have issuer-qualified daily
states in session_suspension.v3; there is no general reversed/equal-clock rule.
Raw and older mapping profiles remain unchanged. Primary PDF bytes are frozen
by digest; source signatures and contradictory daily evidence fail closed.
Full suite: 192/192 PASS, zero skips, 134.099 seconds.

Holder canonical stage is independently validated:
`holder_count_events-d2e03d72322d6254a9b95051e09276328a1e7ff0c576b48c7c2d35091ee68457`.
338,076 rows, 22 immutable partitions, peak RSS 861,424 KiB, 132.303 seconds.
Its original 3,601 RawBatches were imported by exact manifest/payload identity
into the formal data root. This is not a baseline acceptance decision.

## Identity validation I/O profile

The live full market build read more than 96 GB while its published partition
bytes were about 3.1 GB. The cross-domain loop called the public single-identity
validator per market row, which reopened both security contract projections.
The corrected loop validates the complete security table once and applies the
same interval function per row. D-M1 cross-validation uses the same helper.

For 7,202 boundary/session pairs over the actual 3,601-security commit, outputs
are identical; 0.468215s → 0.011136s. Invalid security tables, missing identities,
listing/delisting boundaries, and exchange/session validation remain checked.
This is a function-level measurement; full market recovery is still pending.
The old run was explicitly interrupted with its diagnostic saved under the
fixed operation identity; Raw and immutable partitions remain preserved.
