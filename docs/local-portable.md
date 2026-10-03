# Portable local snapshot bundles

`axiom_data.portable` exports a directory containing one pinned Snapshot and its
parent chain, the canonical Parquet objects and Raw observations those manifests
reference, a captured copy of the package source, `pyproject.toml`, and
`requirements-local.lock`, plus `requirements-qlib.lock` when present. The latter
records the optional actual Qlib consumer environment, not a base ingestion
dependency. The bundle uses relative paths and SHA-256 hashes.
It does not contact a supplier, carry operation checkpoints, or include caches,
credentials, unrelated Raw observations, or unrelated files from the data root.

A Qlib P04 export is a separate immutable, self-contained reading directory.
Copy the whole directory to read it through Qlib without the original data root.
Bring the source Snapshot bundle as well when rebuilding the export or querying
native financial events. Preserve the original view identity after relocation;
new cutoffs or fields require a new export. See [Qlib interface](qlib-interface.md).

```python
from axiom_data.portable import export_bundle, verify_bundle, import_bundle

manifest = export_bundle("/data/axiom", "/transfer/research-bundle",
                         snapshot_id="current", code_root="/path/to/axiom-data")
assert verify_bundle("/transfer/research-bundle")["snapshot_id"] == manifest["snapshot_id"]
snapshot_id = import_bundle("/transfer/research-bundle", "/data/offline-copy")
```

`current` is resolved once while the exporter holds the local writer lock. An
explicit snapshot ID is also valid. The exporter and importer refuse an existing
destination. Import verifies every declared file and the full snapshot/Raw
closure before publishing a new root. A damaged byte leaves no published root.
The imported root has `current.json` pinned to the exported ID; old parent
snapshots remain readable. Its captured source is under `portable-code/` and the
original bundle manifest is retained as `portable-bundle.json`.

To replay with those saved bytes, use a compatible Python interpreter, install
the dependencies from the captured lockfile in an offline environment with
those distributions already available, then load the captured package:

```sh
python -m pip install --no-index --find-links /path/to/wheelhouse \
  -r /data/offline-copy/portable-code/requirements-local.lock
PYTHONPATH=/data/offline-copy/portable-code/src python - <<'PY'
from axiom_data import Data
data = Data("/data/offline-copy")
snapshot_id = data.resolve()
print(snapshot_id)
PY
```

Queries, updates from locally supplied bytes, and `rebuild` from saved Raw use
the same API as the source root. Use the pinned `snapshot_id` for all reads in a
decision job. The bundle saves dependency pins and records the source machine's
Python/platform and installed core dependency versions; it does not include a
Python interpreter or wheels. The current writer lock uses POSIX `fcntl`
(macOS/Linux); Windows write/rebuild support has not been implemented or tested.
The format itself is relative JSON/Parquet, but cross-OS execution still needs
validation with compatible dependencies. Transfer the directory unchanged, or archive and
extract it externally before calling `verify_bundle` or `import_bundle`. Cloud
storage may carry that archive but plays no role in export or replay.

The source identity is the hash of the **captured working-tree files**. `git_head`
is supplementary context, and `git_dirty` records whether Git reported local
changes. A dirty checkout is never represented as if it were exactly HEAD. The
checksums detect accidental or later byte changes; they are not signatures and
do not establish the trustworthiness of the original source or supplier data.
