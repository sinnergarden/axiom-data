# PR8 candidate resume request binding

The mutable execution record previously could substitute another valid domain
commit: closure verification proved that the artifact was internally valid,
but did not prove it came from the current build request.

Resume now constructs the actual builder and validates the contract, parent,
ordered Raw references, empty patch list, resolved builder configuration,
implementation reference and exact dependency commits against the stored commit.
A mismatch fails the run. If no-change reuse returns the parent, its manifest
cannot contain the new request's Raw refs, so that case replays the explicit
request and requires the same resulting identity.

Revalidation clears prior candidate Snapshot/result fields and starts a RUNNING
stage. Failure cannot retain a stale successful candidate. Successful recovery
clears prior failure records; readiness remains false until the separate required
View and full-admission gates finish.

Regression uses the actual reviewed PR7 artifact closure: normal resume passes,
no-change resume preserves identity, substituting the old valid holder commit
fails, stale Snapshot fields disappear, Raw inputs remain and the original
Snapshot is unchanged. No current pointer is created.

This is operational resume hardening, not full V1 acceptance. Industry source
promotion remains pending the previously requested taxonomy-code decision.
Full bootstrap, 56-requirement admission, baseline, daily/revision/recovery and
full acceptance Notebook are still outstanding. No merge or scope expansion.

Validation: full suite 167/167 PASS (118.309 seconds); after final execution-
status cleanup, the actual candidate regression passed again (14.940 seconds).
