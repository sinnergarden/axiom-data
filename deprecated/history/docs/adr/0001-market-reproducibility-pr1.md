# ADR-0001: Market reproducibility PR1 boundary

- Status: Accepted
- Date: 2026-09-05
- Scope: Phase 1 PR1

## Context

PR1 must freeze the public domain-build request and the business meaning of the
first three market contracts before any artifact publication exists. A version
label alone is insufficient if callers can pass floating identities, if an
unregistered contract is accepted, or if two builders can interpret a v1 field
differently.

## Decision

### Public build boundary

The public shape remains exactly:

```text
build(parent_commit, raw_batch_ids, patch_ids, contract_version)
```

Every artifact identity is an explicit immutable identifier. Floating names
such as `current`, `latest`, `live`, their `.json` aliases, and `mtime` are
invalid in `parent_commit`, `raw_batch_ids`, `patch_ids`, and the identity
returned by an executor. Matching is case-insensitive. PR1 performs syntax and
identity validation only; it does not resolve files, manifests, catalogs, or
object stores.

`contract_version` must exactly name a packaged registered contract. An
unversioned domain, an unknown version, and aliases such as
`market_daily.current` are invalid. The registered contract's domain must equal
the requested build domain. Invalid requests fail before the executor runs.

Build request combinations are:

| Kind | Parent | Raw batches | Patches | Validity |
|---|---|---|---|---|
| Genesis | none | one or more | zero or more | valid |
| Genesis | none | none | any | invalid |
| Incremental | explicit | one or more | zero or more | valid |
| Incremental | explicit | zero or more | one or more | valid |
| Incremental | explicit | none | none | invalid |

Caller order is part of the request. Neither `raw_batch_ids` nor `patch_ids` is
sorted or converted to a set. The frozen semantic order is:

```text
parent state -> ordered raw batches -> ordered patches
```

PR1 does not implement row merging. A later merge implementation may treat a
repeated identical key/value as idempotent, but conflicting values must be
resolved by the domain contract or an explicit patch and must not silently win
because they appeared later.

### `trading_calendar.v1`

- One row represents one exchange calendar date, including closed dates; this
  is not an open-session-only list, and a missing row means unknown rather than
  closed.
- Canonical exchanges are `SSE` and `SZSE`. Session dates use the corresponding
  Chinese exchange's local `Asia/Shanghai` calendar semantics.
- `is_open` distinguishes an open session from an explicit closed date.
- `previous_open_session` is the nearest strictly earlier `is_open=true` date
  for the same exchange. It is null when the artifact's left boundary cannot
  prove a predecessor.
- Trading hours and a holiday-rule engine are outside PR1.

### `security_master.v1`

- `symbol` is six digits plus canonical `.SH` or `.SZ`; the suffix must match
  canonical exchange `SSE` or `SZSE`. Supplier exchange codes are not canonical
  values.
- `list_session` is the inclusive identity/listing start. `delist_session` is
  the exclusive delisting-effective date, not the final date with a market row.
- `status` is a source/current observation. It is not historical PIT
  eligibility, tradability, or a replacement for a later status domain.
- Identity bounds do not cause PR1 to crop or synthesize market history.

### `market_daily.v1`

- OHLC values are unadjusted canonical market prices.
- `pre_close` is the supplier/exchange reference previous close. It is not
  required to equal the prior row's `close`, including around corporate
  actions.
- `adj_factor` is a positive adjustment input, not an adjusted price. For anchor
  session `a`, the frozen ratio meaning is
  `unadjusted_price(t) * adj_factor(t) / adj_factor(a)`. PR1 has no adjusted-price
  builder.
- `turnover_rate` is a percentage: `100 * volume_shares / circulating_shares`.
- `circulating_market_cap_cny` means the CNY market value of all circulating
  shares. It is not free-float market capitalization.
- A normal row has `is_suspended=false` and populated OHLC, `pre_close`, volume,
  and amount. A confirmed suspended row requires explicit evidence, null OHLC,
  and zero volume and amount; OHLC is never forward-filled.
- A missing row is unknown unless separate identity/status facts prove
  not-yet-listed, delisted, or another state. Absence and null OHLC do not prove
  suspension.

The normalized full JSON content of all three v1 contracts is protected by
golden digests. A semantic change therefore requires a new contract version.

## PR1 implementation boundary

PR1 contains the repository/package skeleton, the three market v1 contracts, a
pure path model, the public build port, pure in-memory contract checks, and unit
tests.

PR1 does not collect or persist RawBatch data, publish DomainCommit artifacts,
compose DataSnapshot objects, mutate a catalog, export Qlib, implement Readers,
compare legacy data, or switch a production `current` pointer. Those capabilities
require separately reviewed later work.
