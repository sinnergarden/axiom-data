# Reviewed fund share conversions

The authoritative facts and time contract is
[Data02 §6.2 at a4421ac](https://github.com/sinnergarden/axiom-docs/blob/a4421acddca44a56b65d0708673dd9c10323a4d9/docs/design/02_axiom_data.md#etf-unit-split-proposal).
`fund_share_conversions` is a native event domain with stable
`security_id + event_id` identity. It supplements issuer unit-split disclosures;
it does not replace Tushare cash dividends, market prices, factors or states.

`reviewed_fund_share_conversion_batch` describes an already received JSON bundle.
It does not fetch URLs or parse PDFs. A person reviews the originals and records
their facts and locators. The bundle contains:

- `documents`: each original once, with `document_id`, `issuer`, `source_url`,
  `retrieval_host`, `sha256`, `document_base64`, `actual_document_observed_at`,
  `announcement_date`, `process_status` (`planned`/`implemented`) and `locator`.
  The issuer is distinct from a broker or newspaper that hosts the document.
- `records`: all business fields in the module's `CONTRACT`, plus the reviewed
  integer `revision_sequence`. Nullable fields are explicitly null. `document_refs`
  is a JSON list of document IDs from this bundle; normalization canonicalizes it.
  Keep the known plan/result versions for each reviewed event in the input.

The helper returns an ordinary `IngestBatch`. `Data.update` saves complete Raw
before checking hashes, referenced documents, exact integer ratios, date boundaries,
rounding and plan/result order. Invalid input remains retained for diagnosis but
never publishes a candidate. The input does not supply canonical Raw IDs,
`first_observed_at`, verified public times, modeled sellability or account values.

```python
from pathlib import Path
from axiom_data import Data, EventQuery, UpdateRequest, reviewed_fund_share_conversion_batch

data = Data("your-existing-data-root")
base = data.resolve()  # bind once, then retain this ID
batch = reviewed_fund_share_conversion_batch(
    Path("reviewed-bundle.json").read_bytes(),
    observed_at=bundle_receipt,  # actual complete-bundle receipt, saved before update
    next_open_session_by_date=frozen_next_open_map,
)
candidate = data.update(base_snapshot=base, request=UpdateRequest(
    batches=(batch,), operation_id="your-bound-operation",
    build_context={"scope": "reviewed issuer unit splits"}, promote=False,
))
answer = data.events(snapshot=candidate.snapshot_id, query=EventQuery(
    domain="fund_share_conversions",
    fields=("process_status", "ratio_numerator", "ratio_denominator", "effective_phase",
            "quantity_rounding", "quantity_rounding_scope", "suspension_start", "suspension_end"),
    symbols=bound_security_ids, start=economic_start, end=economic_end,
    cutoff=bound_cutoff, pit_policy="best_effort_vendor_v1", time_field="effective_date",
))
```

Retain the original receipt, payload, base, frozen calendar and operation ID on
retry. Rebuild from the saved `fund_share_conversions` Raw IDs through `Data.rebuild`;
no original is fetched again and receipt times stay unchanged. Adding another
event can extend the same profile's next-open calendar; remapping an existing
date or changing the source profile still requires explicit correction.

Best-effort uses each revision's announcement date and declared next-open 09:30
assumption. A result notice cannot make its confirmation visible at the plan's
earlier clock. Operational PIT uses real first receipt; market-safe PIT initially
falls back to it because this input does not attach historical public evidence.
Neither claims that a 2026 receipt was available in 2022. Reader metadata returns
the actual revision, Raw ref, `usable_from`, `first_observed_at` and limitations.

Query an economic range that includes the relevant conversions before examining
their visible suspension intervals. `Data.states` remains unchanged. The Engine
consumes these public events at the same cutoff for quantity accounting and
announced suspension checks; Data does not infer normal trading, correct factors,
apply holdings, or choose a modeled sellability time. Only declared `unit_split`
semantics are supported; other action types fail explicitly.

The first production supplement requires independent code/input review, bounded
candidate verification, unchanged old-domain evidence and explicit publication.
Creating this helper or passing synthetic tests does not publish a supplement or
establish complete issuer-history coverage.
