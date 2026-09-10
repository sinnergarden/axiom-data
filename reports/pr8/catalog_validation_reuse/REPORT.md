# Catalog verification reuse

Catalog rebuild reuses its own validated canonical closures when checking each
Snapshot composition and that Snapshot's Views. Public loads and new catalog
calls start fresh. Snapshot fixed-dependency checks still execute; v1 PR6 keeps
its legacy Reader dispatch. No cache is written as correctness authority.

The copied real PR7 closure yields the same 130 entries with 18 canonical loads,
previously 103. Tests reject a validly hashed Snapshot with a mismatched fixed
calendar dependency, and reject later source corruption while preserving the
old catalog. Full suite: 227/227 PASS, 65.868s. Full-root costs remain to be measured.
