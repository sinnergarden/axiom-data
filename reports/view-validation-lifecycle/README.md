# View validation lifecycle: bounded evidence

This change separates publication/audit from ordinary consumption. It does not
change any Snapshot, DomainCommit, RawBatch, View, PIT rule or stored identity.
The formal data root was read only. The paused View operation was not resumed.

## Why the earlier startup read so much

The interrupted public View startup constructed `SnapshotReader` before entering
any builder. Its full Snapshot closure replay loaded ancestor DomainCommits and
Raw payloads, rechecked mapping, traversed canonical partitions, and then ran
reference cross-domain checks. The existing monitor recorded **313,825,062,533
process-read bytes**, **148,970,844,160 OS-attributed physical-read bytes**,
4,967.9 seconds, and zero completed Views. The monitor did not record per-path
bytes, so an exact allocation of that interrupted run to Canonical, Raw and
repeated work cannot be reconstructed without another full scan; we did not run
one.

The existing six-domain initialization profile is a bounded attribution of the
same mechanism, not an estimate of all 18 domains:

| Six-domain initial validation | Measured |
| --- | ---: |
| Process-read bytes | 82,141,830,178 |
| Unique current financial + valuation partition bytes | 9,190,379,036 |
| Partition traversals | 729 (three passes over each of 243 partitions) |
| Partition bytes across passes | 27,571,137,108 |
| Approximate process reads for those partitions | 55,142,274,216 (hash pass plus parse pass) |
| Extra partition process reads beyond one current-code pass | 36,761,516,144 |
| RawBatch load calls | 468,812 |

The remaining **26,999,555,962 process-read bytes** in that profile include
Raw payloads, Raw/commit manifests and other reads. Existing counters do not
separate these bytes. One 8-second read-only call-count sample of the old public
path opened 153 partition generators and completed 10 Raw loads (1,615,343
payload bytes); its 3,269,732,870 process-read bytes show the work starts before
any View. The 3,093,242,130 declared partition bytes in that sample are the
sizes of opened generators, not a measurement that all those bytes were read.

## New lifecycle

- `write_raw_batch`, DomainCommit publication and Snapshot publication keep
  their existing full checks of newly published objects and required closure.
- `SnapshotReader` checks the concrete Snapshot manifest, its 18 direct commit
  refs/contracts and fixed dependency refs. It defers historical Raw/mapping
  replay and partition decoding. Each consumed partition is still checked in
  full before projected rows are returned.
- Event request coverage reads and binds the exact Raw request manifests and
  checks that payload files exist, without decoding unused supplier payloads.
  Its verified request intervals are reused within the Reader.
- `validate_snapshot_closure(root, snapshot_id)` is the explicit full-history
  audit. Existing `load_snapshot` and `validate_domain_commit_closure` retain
  their full validation behavior. A normal read does **not** certify unconsumed
  domains, ancestor rows or Raw payload bytes.
- Existing operation-level batch preparation and structural View publication
  checks continue to avoid repeat projection. No persistent cache or new
  artifact was added.

## One real public startup measurement

After targeted tests, one read-only process used the exact candidate Snapshot
`snapshot-1d69dc236a358f1627ae91080c33cf993b9e2acb55bbd2c2cb7a37129ce6b5a5`
at `/var/lib/axiom-data`. It started `SnapshotReader`, then requested
`market_daily(688981.SH, 2026-09-10)`. The full list of 56 startup files and
process counters is in `public-startup.json`.

| Phase | Elapsed | Actual objects / file reads | Process-read bytes | Physical-read bytes |
| --- | ---: | --- | ---: | ---: |
| Reader startup | 7.759 s | Snapshot 2 files, Canonical 54 files (18 manifests, 18 digests, 18 contracts); Raw 0, partitions 0 | 849,917,214 | 850,612,224 |
| One consumed month | 0.122 s | 1 market partition, 10,339,003 declared bytes; 1 returned row | 20,741,851 | 10,342,400 |

The startup's largest direct object was the financial commit manifest
(646,772,201 bytes). These direct manifest bytes are still read and hashed
once; they are not historical Raw or partition replay. The one consumed market
partition is read twice by the existing hash-then-parse implementation, preserving
its full-object integrity check. Filesystem cache conditions are not controlled,
so process-read and physical-read counters are reported separately.

## Verification

Targeted tests cover changed Raw ref rejection, absent Raw payload rejection,
metadata-only event request reuse, consumed partition corruption, explicit
full-closure corruption rejection, five-family View publication/resume and
legacy Fact/View reads: **87/87 PASS in 10.822 seconds**. The production
measurement did not publish anything. Full Views, full admission and a full
production Snapshot audit were not run for this change.
