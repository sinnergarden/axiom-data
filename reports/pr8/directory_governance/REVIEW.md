# V1 directory governance

Base: 8ce0e86530a5df661d045710e82accf2d6e2ee3a.
The user chose /var/lib/axiom-data after WSL2 filesystem inspection. This root is
on the same Linux ext4 filesystem as the workspace; location separates storage
lifecycle without claiming a performance gain.

## Code boundary

Production Layout, PIT, mapping, contract and publication semantics are unchanged.
Operational documentation and AGENTS name the formal root. Existing entrypoints
require explicit absolute roots; the existing writer seals published artifacts.
Test consumers use eight explicitly inventoried forensic names and one bounded
qualification root. Frozen report bytes and artifact identities are retained.
The Gate A fixture producer accepts an explicit fixture source location while
continuing to validate the Snapshot identity from the frozen report.

## Migration evidence

120 byte-verified records copied eight referenced forensic groups and 112 Raw
fixtures (about 460.7 MB) into isolated forensic namespaces. Formal root catalog
remains empty. The source Raw/canonical/operations tree contains approximately
126 GB of historic runs and unclassified evidence and is retained individually,
not promoted as a production baseline or deleted wholesale.

163 required references pass, covering seven required groups; the superseded PR3
group remains diagnostic. PR7 offline recovery reports RECOVERY_VALIDATED with
zero external attempts. Twenty catalogs pass; six old diagnostic roots are
rejected with retained reasons. The manifest scan found no old absolute data-root
references. Existing publication sealing verified zero write bits for 2,930
artifact directories.

## Validation

Independent Astra reviewed fixture adaptation, copy, required-ref verification,
sealing and bounded source cleanup. Luna's first full run exposed three corruption
tests attempting to write sealed temporary copies. Five temporary-file chmod calls
preserve their intended corruption tests without changing original permissions.
Affected 3/3 PASS; final full suite 371/371 PASS in 161.303 seconds. The final log
is fullsuite.log. No Data semantics or production reader exceptions were added.

Final cleanup, new-commit Gate A replay and independent acceptance receipts are
external under workspace review. This code review report is not a claim that the
full-history data build or Gate B has completed.
