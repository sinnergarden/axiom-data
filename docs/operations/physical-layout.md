# V1 physical storage

Deployment root: `/var/lib/axiom-data` on the WSL Linux ext4 filesystem.
The repository remains `/home/liuming/workspace/axiom/axiom-data`; project-family
design authority remains in the sibling `design` directory. Review evidence is
kept under the workspace `review` directory, separately from runtime facts.

Use the existing `DataRootLayout`: Raw lives in `raw/batches` and `raw/objects`,
canonical and derived data retain domain/name-specific commits and objects,
Snapshots retain `snapshots`, and Qlib exports retain `exports/qlib`. Do not rename
artifact directories to fit a schematic diagram. Operational state uses
`operations` and `locks`; catalog.sqlite remains a rebuildable index.

All production collection, bootstrap, daily and repair calls must explicitly pass
`/var/lib/axiom-data`. The library accepts another explicit absolute root for
isolated tests and recovery; there is no cwd-based or workspace fallback.
The existing writer boundary seals published artifacts read-only and keeps
operational checkpoints and staging writable. Consumers receive read access.

Historical bounded fixtures are relocated into separate `forensic/<run>` roots,
each retaining its own Layout and immutable identities. Their historical report
bytes are not rewritten. Test consumers use the inventoried location map;
`AXIOM_TEST_FORENSIC_ROOT` may explicitly select another absolute fixture location.
This setting does not select a production root or an artifact revision.
The Gate A fixture script likewise requires an explicit source-root argument when
copying a fixture and reads the Snapshot ID frozen in the original report.

Migration is copy, byte/identity/closure validation, then removal of the verified
source copy. It is not permanent duplication. Unknown legacy Raw, canonical and
operation records remain in the old workspace until individually classified;
they are not production baseline inputs. Use the directory-governance receipt
for exact completed moves and remaining exceptions. No symlink or rewritten
historical manifest substitutes for a relocation record.
