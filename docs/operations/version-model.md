# V1 version and publication model

This document describes the implemented artifact contracts used by PR8. Release
acceptance remains a separate full-scope decision; successful publication of one
artifact does not accept the V1 baseline.

| Version | Meaning | Change mechanism |
| --- | --- | --- |
| RawBatch | One supplier observation, request/profile binding and retrieval time | Publish another immutable batch; preserve the received payload |
| DomainCommit | Complete logical state of one domain | Publish a child or an explicit new lineage, using concrete inputs |
| DataSnapshot | Exact composition of DomainCommit refs | Publish another ordinary Snapshot; unchanged domain refs can be reused |
| Derived/View | Stable derivation or export bound to a Snapshot and its required dependencies | Publish another artifact with its declared schema/configuration |
| Contract | Domain or artifact schema and semantics | Explicit version dispatch; incompatible domain upgrade starts a new lineage |
| SourceProfile/mapping | Supplier interpretation, units and qualification rules | Explicit revision/digest in provenance and build configuration |
| Execution run | Source requests, attempts, checkpoints and stage outcomes | Mutable progress record; never an identity authority |

Builders retain the public boundary
`build(parent_commit, raw_batch_ids, patch_ids, contract_version)`. The current
patch sequence must be empty. Inputs and fixed domain dependencies are explicit
immutable identities. A public operation may resolve `current` once at entry;
all later stages use that concrete Snapshot ID. Directory ordering, mtimes and
convenience pointers do not select build inputs.

The identity projection declared by each artifact schema remains authoritative.
An expected ID supplied by a caller is checked, not imposed. Creation time is
not a substitute for logical identity. Raw retrieval time is observation
provenance and remains part of its recorded observation. A logically identical
historical PIT result can have different Derived IDs when its Snapshot differs.

## Physical state

`domain_commit.v2` uses immutable objects and a complete partition map. Daily
builds may reuse identical parent objects. The logical row digest is independent
of the physical partition traversal. Partition rules are currently
`domain_time_blocks.v1`:

- Session facts use month blocks.
- Financial events use endpoint/report-year blocks.
- Holder and forecast observations use report-year blocks.
- Universe observations use effective-year blocks; complete group states remain
  explicit in the commit, including legal empty sets.
- SW industry state uses per-security objects containing its membership spans.
- Security master uses exchange blocks.
- Undated corporate-action observations use an explicit undated block.

Published files are sealed read-only by the publisher. A conflict at an existing
object path fails; the writer never unseals and changes an old artifact. A child
manifest is not an instruction to append to a parent file. Actual daily I/O and
partition reuse still require the full-root operational acceptance cases.

## Compatibility and evidence

Domain contract versions and artifact manifest schema versions are separate.
A partitioned DomainCommit may contain a domain v1 or v2 contract. Loaders retain
published Fact/View v1 dispatch and validate those artifacts using their original
identity projections. Old manifests are not upgraded or supplemented in place.

Source corrections belong to versioned mappings. The authorized SW2021 mapping
changes index_classify 850401.SI/特钢Ⅲ to canonical 850412.SI only after its four
checks. Raw remains unchanged. Capital conflicts, unknown zero limit pairs,
undated action observations and forecast source labels likewise retain their
original evidence under their explicit profiles/contracts. A supplier fix creates
new observation/build lineage; it never rewrites a previously published Snapshot.

The full closure validator checks identities, bytes, contracts, provenance and
applicable source replay. Snapshot publication can reuse the composition checked
within that same call when verifying its newly published manifest. Every public
load starts a new closure validation. No persistent cache or execution PASS flag
stands in for artifact validation.

## Release decisions

A baseline is an ordinary DataSnapshot accepted by a report binding its exact
ID, required Derived/View refs, 56 requirements, qualification, actual coverage
and validation evidence. The catalog is a rebuildable index. A run record,
source collection completion or canonical commit alone is not baseline acceptance.

Required collection/build/View/admission failure prevents ready publication and
pointer movement. Successfully published Raw and domain artifacts remain useful
checkpoints. A no-change result requires unchanged observations/facts and unchanged
contract/configuration/identities; a later supplier observation is not silently
discarded just because its numeric value repeats. Daily, recovery and Notebook
acceptance remain explicit terminal gates in V1_WORK_LOG.md.
