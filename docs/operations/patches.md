# Immutable canonical corrections

A correction changes the interpretation of committed rows. It does not amend a
supplier publication or add an observation, delisting, or newly learned fact.
Those remain ordinary Raw inputs. Raw, ancestor commits and Snapshots stay intact.

Publish with `axiom_data.patches.publish_patch(data_root, domain=...,
contract_version=..., reason=..., operations=[...], source_raw_batch_ids=[...])`.
The return value contains `patch_id`, `manifest_digest` and the manifest.
`load_patch(data_root, patch_id)` verifies its immutable closure. The artifact is
stored under the existing `patches/<patch_id>/` directory, using
`canonical_patch.v1`. Publication verifies structure, row contracts and Raw
bindings; the canonical build additionally proves application preconditions,
source availability, complete state and dependency validity.

Operations are ordered dictionaries:

```python
from axiom_data.patches import row_digest

insert = {"op": "insert", "key": exact_primary_key, "row": new_row}
replace = {"op": "replace", "key": exact_primary_key,
           "expected_old_digest": row_digest(old_row), "row": new_row}
tombstone = {"op": "tombstone", "key": exact_primary_key,
             "expected_old_digest": row_digest(old_row)}
```

`key` contains exactly the contract's `primary_key` fields. Insert requires an
absent key; replace and tombstone require an existing row with that exact digest.
Replace preserves the key. Changing economic content in a revision-key domain
requires an ordered tombstone followed by a valid insert with its new revision
fingerprint and observation bindings. It cannot overwrite another valid revision.
Final rows retain the declared `sort_order`. Existing PIT, revision, group-state,
source and cross-domain validators still apply; patches do not waive them.

Pass ordered IDs through `BuildApplication.build(parent, raw_ids, patch_ids,
contract_version)` or the optional `patch_ids` field in a repair domain input.
A parent with empty `raw_batch_ids` and nonempty `patch_ids` is supported.
Genesis still requires Raw. Operation plans and resumed commits bind exact patch
refs; changed plans require a different run ID. A new patch creates explicit
lineage even when final row bytes equal the parent. Reusing a patch already in
that ancestry is rejected.

Commits with corrections declare `patch_protocol=canonical_patch.v1`; this field
is part of their identity and continues through descendants. Historical commits
without the field retain their empty-patch interpretation. Ancestor patches run
oldest-first, in each commit's declared order. Source-replaying financial/event
builders obtain the full Raw ancestry independently of surviving rows before
reapplying corrections. Incremental daily/reference builders preserve corrected
parent rows and reject new Raw that overwrites or resurrects corrected keys.
New observations that change an expected old digest fail explicitly; they require
a new correction decision, rather than silently dropping an earlier correction.

Patch-only partitioned builds retain untouched partition objects. The overlay
holds only touched keys and rewritten partitions, while normal digest/contract
validation still traverses the canonical state.

## Evidence and supported boundary

Evidence is immutable, profile-qualified Raw in the same domain. The manifest
binds Raw manifest/payload digests through existing Raw refs. Every source and
observation reference on a new row must resolve to this evidence, with actual
retrieval availability. Evidence-only Raw belongs to the correction's source
closure; it is not counted as a new ingestion observation in source coverage.

The existing builder normalizes that evidence to prove the proposed key and its
actual economic identity: security, endpoint, report period/type, daily session,
or membership group/start as applicable. Corporate action type and announcement
date must match the source-backed action identity. A caller-supplied key alone
does not prove those fields. The same mapper output proves source binding and
availability. Known observations of the affected facts are
included so choosing only a later observation cannot move the earliest proved
time forward. Vendor availability may precede retrieval only when the existing
source mapping supports it. Numeric interpretation corrections use the normal
row/revision validators; they do not establish a new supplier publication.

A key, economic identity or time correction unsupported by the existing normalizer is rejected as
unsupported evidence. No general historical evidence or group-state amendment
protocol is introduced. Membership corrections inconsistent with an existing
complete group state are rejected. Synthetic `scope-...` source pointers used by
some `security_status` source-gap and `price_limits` unknown rows are not Raw
identities: insert/replace of such rows is unsupported by this Raw-only protocol.
Evidence must also cover the source mapper's declared scope and prerequisites.

These APIs produce canonical candidates. They do not collect sources, promote a
Snapshot, or imply business/data acceptance.
