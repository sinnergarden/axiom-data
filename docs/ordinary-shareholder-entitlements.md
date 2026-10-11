# Reviewed ordinary A-share entitlements

`axiom_data.produce_ordinary_shareholder_action_facts` produces a small,
reviewed interpretation and a new `stock_action_facts_v1` mapping. It preserves
the source Snapshot and original supplier artifact. It has no network or storage
side effects and never runs an account. Data does not import Engine.

The reviewer supplies the meaning of original public documents, exact supplier
revision/Native alias bindings, holder exclusions and one narrow allocation
rule. This is explicit reviewed evidence, not an automatic legal-document parser
or a general correction framework. Hash validation does not certify document
meaning. The consumer must bind the new artifact in a new execution identity.

```python
from axiom_data import produce_ordinary_shareholder_action_facts

result = produce_ordinary_shareholder_action_facts(
    supplier_facts, supplier_ref=fixed_supplier_ref, review=review,
    documents=original_document_bytes_by_sha256,
    next_open_session_by_date=frozen_calendar, observed_at=actual_producer_time,
)
# result: interpretation, interpretation_ref, facts, facts_ref
```

The review has `contract_version=ordinary_shareholder_interpretation_v1`,
`supplier_facts_ref`, `holder_class=ordinary_secondary_market_a_share_v1`,
nonempty `holder_assumptions`, `documents` and `entries`. Documents are keyed by
their original byte SHA-256 and declare `source_url`, `public_date`,
`retrieved_at`. The exact byte closure must be supplied. Each entry declares:

| Field | Binding |
| --- | --- |
| `targets` | Original economic IDs and exact revision refs |
| `economic_event_id`, `aliases` | Explicit output ID and complete original Native alias closure |
| `expected_phase_dates` | Exact original record/EX/payment/listing dates |
| `cash_evidence_strength` | `explicit_no_cash`, `approved_scheme_chain` or `share_only_inference` |
| `dependencies` | Original document refs, implementation/scheme roles and reviewed locators |
| `eligibility`, `delivery_basis` | Explicit holder conditions and scheduled versus actual delivery |
| `allocation` | One of the four rules below |

Allocation rules are ordinary pro-rata transfer with an exact decimal matching
the source total; no new shares for original holders; common proportional
surrender from the approved scheme's exact quantities; or eligible proportional
distribution with an unestablished denominator. The last stays null. A combined
alias group requires implementation, original scheme and approved scheme
dependencies. The producer does not infer aliases from matching amounts/dates.

The interpretation retains every original target action, separate issuer total,
exact rational derivation, effective terms/statuses, applicability, locators,
document hashes and actual receipts. The effective v1 artifact retains its
existing shape. Reviewed cash is `"0"`; its total stock scalar is the ordinary
holder's net new shares. Supplier bonus/capital components stay independently
preserved; they must not be added to that effective total. Unaffected actions
are exactly equal. Original Raw IDs remain original; documents are not relabeled
as supplier observations. New effective revisions are content addressed and
bind the interpretation through `source_refs`. The interpretation is also the
new identity-policy/supplemental reference.

`mixed_reviewed_sources_v1` explicitly includes the vendor historical
availability assumption. Each effective action's historical `available_at` is
the maximum of all necessary original supplier boundaries and official
document boundaries. Date-only public documents use the frozen next A-share
session at 09:30 Asia/Shanghai. This does not certify full historical vintages.
Original actual receipts, document retrievals and actual producer observation
remain separate; new effective `first_observed_at` is the producer observation,
not an invented historical receipt. Later execution-completion evidence must
not be used before its own publication boundary.

Repeating rational rates retain their exact numerator/denominator in the
interpretation and a 60-significant-digit decimal projection in v1. A decimal
scalar cannot store a repeating rational exactly. The producer does not round
account shares or certify CSDC remainder allocation. Consumer rounding and
listing-date release remain declared simulation choices; a listing date does
not prove actual account credit.

All inputs and outputs are independent JSON mappings. Mutated documents,
wrong revisions, omitted aliases, missing scheme chains, changed phases,
colliding identities and invalid calendars fail before producing a result.
Inputs are bounded to 64 reviewed entries, 8 MiB supplier JSON and 16 MiB
original documents, with at most 2 MiB per document. Save returned artifacts
under new immutable names; never replace the supplier artifact.
