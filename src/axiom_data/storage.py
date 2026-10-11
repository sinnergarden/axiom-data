"""Local Raw, typed Parquet, and immutable Snapshot storage.

All persisted references are relative to ``root`` so a complete root can be
copied elsewhere. Constructing or reading a store never creates files.
"""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
import base64
from copy import deepcopy
from datetime import date, datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
import threading
import zlib
from typing import Any
from collections.abc import Iterator, Mapping, Sequence
from uuid import uuid4

from .protocols import ConflictError, DataError


_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_TIME_COLUMNS = {"first_observed_at", "source_available_at"}
_BASE_TYPES = {
    "security_id": "string",
    "session": "string",
    "revision_id": "string",
    "revision_sequence": "int64",
    "first_observed_at": "timestamp",
    "raw_batch_id": "string",
    "source_available_at": "timestamp",
    "evidence_ref": "string",
}


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "item"):
        return value.item()
    raise TypeError(f"cannot JSON encode {type(value).__name__}")


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False, default=_json_default,
    ).encode("utf-8")


def _json_chunks(value: Any) -> Iterator[bytes]:
    """Yield the legacy canonical encoding in bounded character groups."""
    encoder = json.JSONEncoder(sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                               allow_nan=False, default=_json_default)
    chunks = []
    characters = 0
    for chunk in encoder.iterencode(value):
        chunks.append(chunk)
        characters += len(chunk)
        if characters >= 65536:
            yield "".join(chunks).encode("utf-8")
            chunks.clear()
            characters = 0
    if chunks:
        yield "".join(chunks).encode("utf-8")


def _json_digest(value: Any) -> str:
    """Hash canonical chunks, retaining the legacy encoder's depth tolerance."""
    digest = sha256()
    try:
        for chunk in _json_chunks(value):
            digest.update(chunk)
    except RecursionError:
        # The Python iterator has less depth headroom than json.dumps' C
        # encoder. Preserve old valid snapshots at that uncommon boundary.
        return sha256(_json_bytes(value)).hexdigest()
    return digest.hexdigest()


def _stamp(value: datetime | str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    except ValueError as exc:
        raise DataError(f"invalid observation timestamp: {value!r}") from exc
    if not isinstance(parsed, datetime) or parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DataError("observation timestamps must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat()


def _clean_name(value: str, label: str) -> str:
    if not isinstance(value, str) or value in (".", "..") or not _NAME.fullmatch(value):
        raise DataError(f"invalid {label}: {value!r}")
    return value


def _arrow_type(dtype: str) -> Any:
    import pyarrow as pa

    kind = str(dtype).lower().strip()
    aliases = {
        "string": pa.string(), "str": pa.string(), "utf8": pa.string(),
        "date": pa.date32(), "date32": pa.date32(),
        "timestamp": pa.timestamp("us", tz="UTC"),
        "datetime": pa.timestamp("us", tz="UTC"),
        "timestamp[us, tz=utc]": pa.timestamp("us", tz="UTC"),
        "bool": pa.bool_(), "boolean": pa.bool_(),
        "int": pa.int64(), "integer": pa.int64(), "int64": pa.int64(),
        "int32": pa.int32(), "float": pa.float64(), "double": pa.float64(),
        "float64": pa.float64(), "float32": pa.float32(),
    }
    if kind not in aliases:
        raise DataError(f"unsupported declared dtype: {dtype!r}")
    return aliases[kind]


def _coerce(value: Any, arrow_type: Any) -> Any:
    import pyarrow as pa

    if value is None:
        return None
    if pa.types.is_timestamp(arrow_type):
        return datetime.fromisoformat(_stamp(value))
    if pa.types.is_date(arrow_type) and isinstance(value, str):
        return date.fromisoformat(value)
    return value


class LocalStore:
    """One-root storage with process-local and advisory cross-process write locks."""

    def __init__(self, root: str | os.PathLike[str]):
        self.root = Path(root).resolve()
        self._mutex = threading.RLock()
        self._writer_depth = 0
        self._hash_cache: dict[str, tuple[tuple[int, ...], str]] = {}
        self._raw_offsets: dict[str, tuple[int, int]] = {}
        self._raw_scanned = 0
        self._raw_inode: tuple[int, int] | None = None
        self._profile_cache: dict[str, dict[str, Any]] = {}

    def raw_log_size(self) -> int:
        """Return a committed append boundary, preserving any interrupted tail.

        Called by a writer before checkpointing a new chunk. It repairs only a
        final record without a newline; completed records are never rewritten.
        """
        with self.writer():
            return self._recover_raw_tail()

    def _recover_raw_tail(self) -> int:
        """Under the writer lock, preserve/truncate only an uncommitted last line.

        Newline is the receipt boundary. Readers ignore an unfinished tail
        without modifying it; the next writer saves its exact bytes under
        raw/recovery before appending again. Normal appends inspect one byte,
        and recovery scans backwards only through the interrupted record.
        """
        path = self.root / "raw" / "fetches.jsonl"
        try:
            stream = path.open("r+b")
        except FileNotFoundError:
            return 0
        with stream:
            size = os.fstat(stream.fileno()).st_size
            if not size:
                return 0
            stream.seek(size - 1)
            if stream.read(1) == b"\n":
                return size
            boundary, cursor = 0, size
            while cursor:
                start = max(0, cursor - 4096)
                stream.seek(start)
                newline = stream.read(cursor - start).rfind(b"\n")
                if newline >= 0:
                    boundary = start + newline + 1
                    break
                cursor = start
            stream.seek(boundary)
            fragment = stream.read()
            digest = sha256(fragment).hexdigest()
            self._atomic(self._path(f"raw/recovery/tail-{boundary}-{digest}.bin"),
                         fragment, immutable=True)
            stream.truncate(boundary)
            stream.flush()
            os.fsync(stream.fileno())
            return boundary

    def _decode_raw_line(self, line: bytes, *, _budget=None) -> dict[str, Any]:
        try:
            record = json.loads(line)
        except (TypeError, ValueError) as exc:
            raise DataError("Raw fetch log is corrupt") from exc
        compressed = record.pop("source_profile_zlib", None)
        if compressed is not None:
            if not isinstance(compressed, str) or "source_profile" in record:
                raise DataError("Raw source profile encoding is invalid")
            profile = self._profile_cache.get(compressed)
            if profile is None:
                try:
                    encoded=base64.b64decode(compressed, validate=True)
                    if _budget is None:
                        profile = json.loads(zlib.decompress(encoded))
                    else:
                        from .native_view import _require
                        maximum=max(0,(_budget.remaining()-4096)//64)
                        with _budget.scope('raw-inflation',2*(maximum+1)+4096):
                            decoder=zlib.decompressobj()
                            inflated=decoder.decompress(encoded,maximum+1)
                            _require(len(inflated)<=maximum and decoder.eof,
                                     'native Raw profile exceeds working budget')
                            _budget.reserve('raw-inflation',32*len(inflated)+4096)
                            profile=json.loads(inflated)
                            del inflated,decoder
                except (ValueError, zlib.error) as exc:
                    raise DataError("Raw source profile encoding is corrupt") from exc
                if not isinstance(profile, dict):
                    raise DataError("Raw source profile is invalid")
                if _budget is not None:
                    from .reader import _object_size
                    _budget.add('store-cache',4*_object_size((compressed,profile))+1024)
                if len(self._profile_cache) >= 16:
                    self._profile_cache.clear()
                self._profile_cache[compressed] = profile
            record["source_profile"] = profile
        return record

    def _index_raw_log(self, *, _budget=None) -> None:
        """Index appended complete lines; an unfinished tail stays invisible."""
        path = self.root / "raw" / "fetches.jsonl"
        try:
            stat = path.stat()
        except FileNotFoundError:
            self._raw_offsets.clear()
            self._raw_scanned = 0
            self._raw_inode = None
            return
        inode = (stat.st_dev, stat.st_ino)
        if inode != self._raw_inode or stat.st_size < self._raw_scanned:
            self._raw_offsets.clear()
            self._raw_scanned = 0
            self._raw_inode = inode
        if stat.st_size == self._raw_scanned:
            return
        with path.open("rb") as stream:
            stream.seek(self._raw_scanned)
            while stream.tell() < stat.st_size:
                offset = stream.tell()
                maximum=None if _budget is None else min(stat.st_size-offset,max(0,(_budget.remaining()-4096)//64))
                with (nullcontext() if _budget is None else _budget.scope('raw-log-line',2*(maximum+1)+4096)):
                    line = stream.readline() if maximum is None else stream.readline(maximum+1)
                    if _budget is not None:
                        from .native_view import _require
                        _require(len(line)<=maximum,'native Raw log line exceeds working budget')
                        _budget.reserve('raw-log-line',32*len(line)+4096)
                    if not line.endswith(b"\n"):
                        self._raw_scanned = offset
                        return
                    record = self._decode_raw_line(line,_budget=_budget)
                    identity = record.get("batch_id")
                    if not isinstance(identity, str):
                        raise DataError("Raw fetch log lacks batch_id")
                    previous = self._raw_offsets.get(identity)
                    if previous is not None and previous != (offset, len(line)):
                        raise DataError(f"duplicate Raw batch ID: {identity}")
                    if _budget is not None and previous is None:
                        _budget.add('store-cache',1024+4*len(identity))
                    self._raw_offsets[identity] = (offset, len(line))
                    del record,line
            self._raw_scanned = stream.tell()

    def _path(self, uri: str) -> Path:
        if not isinstance(uri, str) or not uri or "\\" in uri:
            raise DataError(f"invalid relative URI: {uri!r}")
        relative = PurePosixPath(uri)
        if relative.is_absolute() or any(p in (".", "..") for p in relative.parts):
            raise DataError(f"URI escapes data root: {uri!r}")
        path = self.root.joinpath(*relative.parts)
        if not path.resolve().is_relative_to(self.root.resolve()):
            raise DataError(f"URI escapes data root: {uri!r}")
        return path

    @contextmanager
    def writer(self) -> Iterator[None]:
        """Serialize writes; callers may wrap an entire update in one lock."""
        import fcntl

        with self._mutex:
            if self._writer_depth:
                self._writer_depth += 1
                try:
                    yield
                finally:
                    self._writer_depth -= 1
                return
            self.root.mkdir(parents=True, exist_ok=True)
            lock_path = self.root / ".writer.lock"
            with lock_path.open("a+b") as lock:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                self._writer_depth = 1
                try:
                    yield
                finally:
                    self._writer_depth = 0
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _atomic(self, path: Path, payload: bytes, *, immutable: bool = False) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if immutable and path.exists():
            if path.read_bytes() != payload:
                raise ConflictError(f"immutable object differs: {path}")
            return
        fd, temp_name = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as out:
                out.write(payload)
                out.flush()
                os.fsync(out.fileno())
            if immutable and path.exists():
                if path.read_bytes() != payload:
                    raise ConflictError(f"immutable object differs: {path}")
            else:
                os.replace(temp_name, path)
                temp_name = ""
                dir_fd = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
        finally:
            if temp_name:
                os.unlink(temp_name)

    def _atomic_json(self, path: Path, value: Any, *, immutable: bool = False) -> None:
        """Write canonical JSON chunks with the existing atomic object semantics.

        Encoding or write failures remove the temporary file. Immutable
        objects are compared byte-for-byte with bounded reads; a conflict
        leaves the existing object intact. No newline or format change is
        introduced. Rare deep legacy values retain the C encoder fallback.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        if immutable and path.exists():
            with path.open("rb") as existing:
                different = False
                try:
                    for chunk in _json_chunks(value):
                        different |= existing.read(len(chunk)) != chunk
                    different |= bool(existing.read(1))
                except RecursionError:
                    payload = _json_bytes(value)
                    existing.seek(0)
                    different = False
                    for offset in range(0, len(payload), 65536):
                        chunk = payload[offset:offset + 65536]
                        different |= existing.read(len(chunk)) != chunk
                    different |= bool(existing.read(1))
            if different:
                raise ConflictError(f"immutable object differs: {path}")
            return
        fd, temp_name = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as out:
                try:
                    for chunk in _json_chunks(value):
                        out.write(chunk)
                except RecursionError:
                    # iterencode has less depth headroom than the legacy C
                    # encoder. Discard every partially flushed chunk first.
                    out.seek(0)
                    out.truncate()
                    out.write(_json_bytes(value))
                out.flush()
                os.fsync(out.fileno())
            if immutable and path.exists():
                with open(temp_name, "rb") as candidate, path.open("rb") as existing:
                    while chunk := candidate.read(65536):
                        if existing.read(len(chunk)) != chunk:
                            raise ConflictError(f"immutable object differs: {path}")
                    if existing.read(1):
                        raise ConflictError(f"immutable object differs: {path}")
            else:
                os.replace(temp_name, path)
                temp_name = ""
                dir_fd = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
        finally:
            if temp_name:
                os.unlink(temp_name)

    def _verified(self, uri: str, expected_hash: str) -> Path:
        path = self._path(uri)
        try:
            info = path.stat()
        except FileNotFoundError as exc:
            raise DataError(f"referenced object is missing: {uri}") from exc
        identity = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        if self._hash_cache.get(uri) != (identity, expected_hash):
            digest=sha256()
            with path.open('rb') as stream:
                while chunk:=stream.read(65536): digest.update(chunk)
            actual=digest.hexdigest()
            if actual != expected_hash:
                raise DataError(f"referenced object failed SHA-256 validation: {uri}")
            self._hash_cache[uri] = (identity, expected_hash)
        return path

    def write_raw(
        self, payload: bytes, *, request: Mapping[str, Any],
        source_profile: Mapping[str, Any], observed_at: datetime | str,
        status: str = "success", contract: Mapping[str, Any] | None = None,
        normalizer: str | None = None, domain: str | None = None,
        operation_id: str | None = None, batch_index: int | None = None,
    ) -> dict[str, Any]:
        """Append one observation, reusing the content-addressed original bytes."""
        if not isinstance(payload, bytes):
            raise DataError("Raw payload must be original bytes")
        if domain is not None:
            _clean_name(domain, "domain")
        if operation_id is not None:
            _clean_name(operation_id, "operation ID")
        if batch_index is not None and (type(batch_index) is not int or batch_index < 0):
            raise DataError("batch_index must be a non-negative integer")
        digest = sha256(payload).hexdigest()
        uri = f"raw/objects/{digest}/payload.bin"
        with self.writer():
            self._atomic(self._path(uri), payload, immutable=True)
            record: dict[str, Any] = {
                "batch_id": f"b_{uuid4().hex}", "payload_uri": uri,
                "payload_sha256": digest, "observed_at": _stamp(observed_at),
                "request": dict(request), "source_profile": dict(source_profile),
                "status": status,
            }
            if contract is not None:
                record["contract"] = dict(contract)
            if normalizer is not None:
                record["normalizer"] = normalizer
            if domain is not None:
                record["domain"] = _clean_name(domain, "domain")
            if operation_id is not None:
                record["operation_id"] = _clean_name(operation_id, "operation ID")
            if batch_index is not None:
                record["batch_index"] = batch_index
            logged = dict(record)
            profile_bytes = _json_bytes(logged["source_profile"])
            if len(profile_bytes) > 2048:
                logged.pop("source_profile")
                logged["source_profile_zlib"] = base64.b64encode(zlib.compress(profile_bytes, 9)).decode("ascii")
            line = _json_bytes(logged) + b"\n"
            log = self.root / "raw" / "fetches.jsonl"
            log.parent.mkdir(parents=True, exist_ok=True)
            self._recover_raw_tail()
            with log.open("ab") as out:
                out.write(line)
                out.flush()
                os.fsync(out.fileno())
            return record

    def _raw_records(self, batch_ids: Sequence[str], *, shared_profiles: bool = False, _budget=None) -> dict[str, dict[str, Any]]:
        """Find selected observations by an append-aware in-memory offset index."""
        wanted = set(batch_ids)
        if not wanted:
            return {}
        self._index_raw_log(_budget=_budget)
        found: dict[str, dict[str, Any]] = {}
        path = self.root / "raw" / "fetches.jsonl"
        try:
            with path.open("rb") as stream:
                for identity in wanted:
                    location = self._raw_offsets.get(identity)
                    if location is None:
                        continue
                    stream.seek(location[0])
                    with (nullcontext() if _budget is None else _budget.scope('raw-log-line',32*location[1]+4096)):
                        record = self._decode_raw_line(stream.read(location[1]),_budget=_budget)
                        if record.get("batch_id") != identity:
                            raise DataError("Raw offset index disagrees with fetch log")
                        if _budget is not None:
                            from .reader import _object_size
                            _budget.reserve('raw-record',4*_object_size(record)+4096)
                        found[identity] = record if shared_profiles else deepcopy(record)
                        del record
        except FileNotFoundError as exc:
            raise DataError(f"Raw batch does not exist: {sorted(wanted)[0]}") from exc
        missing = wanted - found.keys()
        if missing:
            raise DataError(f"Raw batch does not exist: {sorted(missing)[0]}")
        return found

    def get_raw(self, batch_id: str, *, _budget=None) -> dict[str, Any]:
        """Find one append-log observation without writing or replaying it."""
        return self._raw_records([batch_id],_budget=_budget)[batch_id]

    def get_raw_many(self, batch_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
        """Read selected observations with one pass through the append log."""
        return self._raw_records(batch_ids)

    def read_raw_record(self, record: Mapping[str, Any]) -> bytes:
        """Verify bytes for an already loaded log record without rescanning it."""
        return self._verified(record["payload_uri"], record["payload_sha256"]).read_bytes()

    def find_raw_by_operation(self, operation_id: str, *, since_offset: int = 0,
                              shared_profiles: bool = False) -> dict[int, dict[str, Any]]:
        """Recover complete receipts after a crash before a job checkpoint.

        A final line without a newline is not a completed observation. Ignore
        it read-only; malformed completed lines still raise DataError.
        """
        _clean_name(operation_id, "operation ID")
        path = self.root / "raw" / "fetches.jsonl"
        found: dict[int, dict[str, Any]] = {}
        if type(since_offset) is not int or since_offset < 0:
            raise DataError("since_offset must be a non-negative Raw log byte offset")
        try:
            with path.open("rb") as stream:
                stream.seek(since_offset)
                for line in stream:
                    if not line.endswith(b"\n"):
                        break
                    record = self._decode_raw_line(line)
                    if record.get("operation_id") != operation_id:
                        continue
                    index = record.get("batch_index")
                    if not isinstance(index, int) or index < 0:
                        raise DataError(f"operation {operation_id} has invalid batch_index")
                    if index in found and found[index] != record:
                        raise ConflictError(f"operation {operation_id} has duplicate Raw batch_index {index}")
                    found[index] = record if shared_profiles else deepcopy(record)
        except FileNotFoundError:
            return {}
        return found

    def read_raw(self, batch_id: str) -> bytes:
        record = self.get_raw(batch_id)
        return self.read_raw_record(record)

    def write_partition(
        self, domain: str, partition: str, rows: Sequence[Mapping[str, Any]],
        contract: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Write a sorted, typed, immutable Parquet partition object."""
        import pyarrow as pa
        import pyarrow.parquet as pq

        domain, partition = _clean_name(domain, "domain"), _clean_name(partition, "partition")
        declared = contract.get("fields", {})
        if not isinstance(declared, Mapping):
            raise DataError("contract.fields must be a mapping")
        logical_key = tuple(contract.get("logical_key", ("security_id", "session")))
        row_list = [dict(row) for row in rows]
        for row in row_list:
            if any(row.get(key) is None for key in logical_key):
                raise DataError(f"partition row lacks logical key {logical_key}")
            for name, definition in declared.items():
                if isinstance(definition, Mapping) and definition.get("nullable") is False and row.get(name) is None:
                    raise DataError(f"non-nullable field {name} is null or absent")
        row_list.sort(key=lambda row: _json_bytes(row))
        names = list(dict.fromkeys([*_BASE_TYPES, *logical_key, *sorted(declared)]))
        extras = sorted({key for row in row_list for key in row} - set(names))
        names.extend(extras)
        fields = []
        for name in names:
            if name in declared:
                definition = declared[name]
                dtype = definition.get("dtype") if isinstance(definition, Mapping) else definition
                if not dtype:
                    raise DataError(f"missing dtype for {name}")
                arrow_type = _arrow_type(str(dtype))
            elif name in _BASE_TYPES:
                arrow_type = _arrow_type(_BASE_TYPES[name])
            elif name in logical_key:
                arrow_type = pa.string()
            else:
                samples = [row[name] for row in row_list if row.get(name) is not None]
                try:
                    arrow_type = pa.array(samples).type if samples else pa.string()
                except (pa.ArrowException, TypeError) as exc:
                    raise DataError(f"cannot infer dtype for {name}") from exc
            definition = declared.get(name)
            nullable = not (isinstance(definition, Mapping) and definition.get("nullable") is False)
            fields.append(pa.field(name, arrow_type, nullable=nullable))
        schema = pa.schema(fields)
        try:
            # Build Arrow columns directly rather than a second full list of
            # row dictionaries. Repeated observation/session timestamps are
            # converted once per distinct value within this partition.
            columns = []
            for field in schema:
                values = [row.get(field.name) for row in row_list]
                if pa.types.is_timestamp(field.type) or pa.types.is_date(field.type):
                    converted = {value: _coerce(value, field.type) for value in set(values)}
                    values = [converted[value] for value in values]
                columns.append(pa.array(values, type=field.type))
            table = pa.Table.from_arrays(columns, schema=schema)
            sink = pa.BufferOutputStream()
            pq.write_table(table, sink, compression="zstd", version="2.6")
            payload = sink.getvalue().to_pybytes()
        except (pa.ArrowException, TypeError, ValueError) as exc:
            raise DataError(f"cannot write typed partition {domain}/{partition}: {exc}") from exc
        digest = sha256(payload).hexdigest()
        uri = f"canonical/{domain}/{partition}/{digest}.parquet"
        with self.writer():
            self._atomic(self._path(uri), payload, immutable=True)
        return {"partition": partition, "uri": uri, "rows": table.num_rows, "file_sha256": digest}

    def read_partition(
        self, part: Mapping[str, Any], *, columns: Sequence[str] | None = None,
        symbols: Sequence[str] | None = None, sessions: Sequence[str] | None = None,
    ) -> Any:
        """Read one verified object, projecting fields and filtering daily keys."""
        import pyarrow as pa
        import pyarrow.compute as pc
        import pyarrow.parquet as pq

        path = self._verified(part["uri"], part["file_sha256"])
        try:
            source = pq.ParquetFile(path)
            available = set(source.schema_arrow.names)
            needed = list(columns) if columns is not None else source.schema_arrow.names
            scan = list(dict.fromkeys([*needed, *( ["security_id"] if symbols is not None else []), *( ["session"] if sessions is not None else [])]))
            table = source.read(columns=[name for name in scan if name in available])
            if symbols is not None:
                if "security_id" not in table.column_names:
                    raise DataError("partition lacks security_id for symbol filtering")
                table = (table.filter(pc.is_in(table["security_id"], value_set=pa.array(
                    list(symbols), type=table.schema.field("security_id").type))) if symbols else table.slice(0, 0))
            if sessions is not None:
                if "session" not in table.column_names:
                    raise DataError("partition lacks session for session filtering")
                session_type = table.schema.field("session").type
                session_values = [_coerce(value, session_type) for value in sessions]
                table = (table.filter(pc.is_in(table["session"], value_set=pa.array(
                    session_values, type=session_type))) if sessions else table.slice(0, 0))
            for name in needed:
                if name not in table.column_names:
                    table = table.append_column(name, pa.nulls(table.num_rows))
            return table.select(needed)
        except (pa.ArrowException, OSError) as exc:
            raise DataError(f"cannot read partition {part['uri']}: {exc}") from exc

    def _native_partition_batches(self,part,*,columns=None,batch_size=4096,_observe=None,
                                  _membership_dependencies=False):
        """Native-export scalar and membership-provenance batches.

        Preserve BYTE_ARRAY dictionaries and physical offsets, without prefetch
        or parallel columns. The caller admits actual buffers before retaining
        or converting a batch. Row count is bounded; a page, dictionary or one
        variable-width value can still allocate beyond a caller's byte budget.
        The Snapshot-bound membership caller may retain its existing single
        list<string> dependency_raw_batch_ids column; other nesting is refused.
        Ordinary read_partition retains its existing schema/type support.
        """
        import pyarrow as pa
        import pyarrow.parquet as pq
        from .protocols import QueryError

        path=self._verified(part['uri'],part['file_sha256'])
        source=pq.ParquetFile(path,pre_buffer=False,buffer_size=0)
        try:
            schema=source.schema_arrow
            needed=list(schema.names if columns is None else columns)
            present=[name for name in needed if name in schema.names]
            for name in present:
                dtype=schema.field(name).type
                dtype=dtype.value_type if pa.types.is_dictionary(dtype) else dtype
                supported=(pa.types.is_null(dtype) or pa.types.is_boolean(dtype) or
                    pa.types.is_integer(dtype) or pa.types.is_floating(dtype) or
                    pa.types.is_date(dtype) or pa.types.is_timestamp(dtype) or
                    pa.types.is_string(dtype) or pa.types.is_large_string(dtype) or
                    pa.types.is_binary(dtype) or pa.types.is_large_binary(dtype) or
                    pa.types.is_fixed_size_binary(dtype) or
                    (_membership_dependencies and name=='dependency_raw_batch_ids' and
                     pa.types.is_list(dtype) and pa.types.is_string(dtype.value_type)))
                if not supported:
                    raise QueryError(f'native export requires flat scalar fact columns: {name} has {dtype}')
            metadata=source.metadata
            dictionaries=[]
            for name in present:
                dtype=schema.field(name).type
                dtype=dtype.value_type if pa.types.is_dictionary(dtype) else dtype
                if (pa.types.is_string(dtype) or pa.types.is_binary(dtype) or
                        pa.types.is_large_string(dtype) or pa.types.is_large_binary(dtype)):
                    dictionaries.append(name)
            source.close()
            source=pq.ParquetFile(path,metadata=metadata,read_dictionary=dictionaries,
                pre_buffer=False,buffer_size=0)
            offset=0
            for group in range(source.num_row_groups):
                if _observe is not None:
                    _observe('encoded_pages',sum(metadata.row_group(group).column(j).total_uncompressed_size
                        for j in range(metadata.num_columns)
                        if metadata.row_group(group).column(j).path_in_schema.split('.')[0] in present))
                for batch in source.iter_batches(batch_size=batch_size,row_groups=[group],
                        columns=present,use_threads=False,use_pandas_metadata=False):
                    table=pa.Table.from_batches([batch])
                    for name in needed:
                        if name not in table.column_names: table=table.append_column(name,pa.nulls(table.num_rows))
                    yield offset,table.select(needed)
                    offset+=batch.num_rows
                    del batch,table
            if metadata.num_rows==0:
                yield 0,pa.Table.from_arrays([pa.array([],type=schema.field(name).type)
                    if name in schema.names else pa.nulls(0) for name in needed],names=needed)
        except (pa.ArrowException,OSError) as exc:
            raise DataError(f"cannot stream native partition {part['uri']}: {exc}") from exc
        finally: source.close()

    def verify_partition(self, part: Mapping[str, Any]) -> None:
        """Recheck a referenced object; unchanged files need only a stat call."""
        self._verified(part["uri"], part["file_sha256"])

    def load_snapshot(self, snapshot_id: str) -> dict[str, Any]:
        _clean_name(snapshot_id, "snapshot ID")
        path = self._path(f"snapshots/{snapshot_id}.json")
        try:
            manifest = json.loads(path.read_bytes())
        except FileNotFoundError as exc:
            raise DataError(f"snapshot does not exist: {snapshot_id}") from exc
        except json.JSONDecodeError as exc:
            raise DataError(f"snapshot manifest is corrupt: {snapshot_id}") from exc
        if manifest.get("snapshot_id") != snapshot_id or manifest.get("schema_version") != "local_data_v1":
            raise DataError(f"invalid snapshot manifest: {snapshot_id}")
        body = {key: value for key, value in manifest.items() if key != "snapshot_id"}
        try:
            actual_id = "s_" + _json_digest(body)
        except (TypeError, ValueError) as exc:
            raise DataError(f"invalid snapshot manifest: {snapshot_id}") from exc
        if actual_id != snapshot_id:
            raise DataError(f"snapshot manifest content digest mismatch: {snapshot_id}")
        self._validate_manifest_body(body)
        return manifest

    @staticmethod
    def _validate_manifest_body(body: Mapping[str, Any]) -> None:
        if body.get("schema_version") != "local_data_v1":
            raise DataError("unsupported snapshot schema")
        if "parent_snapshot" not in body or not isinstance(body.get("domains"), dict) or not isinstance(body.get("build_context"), dict):
            raise DataError("snapshot root lacks required fields")
        for name, domain in body["domains"].items():
            _clean_name(name, "domain")
            if not isinstance(domain, dict):
                raise DataError(f"invalid domain manifest: {name}")
            for key in ("contract", "source_profile", "partitions", "raw_batch_ids", "coverage", "build_context"):
                if key not in domain:
                    raise DataError(f"domain {name} lacks {key}")
            contract = domain["contract"]
            if not isinstance(contract, dict) or not all(key in contract for key in ("contract_id", "logical_key", "fields")):
                raise DataError(f"domain {name} has invalid contract")
            if not isinstance(domain["partitions"], list) or not isinstance(domain["raw_batch_ids"], list):
                raise DataError(f"domain {name} has invalid references")
            if not isinstance(domain["source_profile"], dict) or not isinstance(domain["coverage"], dict) or not isinstance(domain["build_context"], dict):
                raise DataError(f"domain {name} has invalid metadata")
            for part in domain["partitions"]:
                if not isinstance(part, dict) or not all(key in part for key in ("partition", "uri", "rows", "file_sha256")):
                    raise DataError(f"domain {name} has invalid partition reference")

    def resolve(self, ref: str) -> str:
        if ref != "current":
            self.load_snapshot(ref)
            return ref
        try:
            current = json.loads((self.root / "current.json").read_bytes())
            snapshot_id = current["snapshot_id"]
        except (FileNotFoundError, KeyError, json.JSONDecodeError) as exc:
            raise DataError("current snapshot pointer is unavailable") from exc
        self.load_snapshot(snapshot_id)
        return snapshot_id

    def select_raw(self, *, domains, receipt_cutoff, statuses=("success", "empty")):
        """Preview saved Raw IDs, inclusive UTC receipt cutoff, without writes.

        IDs are ordered by actual receipt and ID; save the returned IDs to freeze
        a rebuild input. A future append cannot mutate that selection. None for
        domains/statuses selects all (used by an explicit full Raw backup).
        Incomplete append tails are ignored, complete corrupt lines still fail.
        This scans metadata only, not payload bytes or supplier endpoints.
        """
        cutoff = _stamp(receipt_cutoff)
        wanted = None if domains is None else tuple(domains)
        if wanted is not None and (not wanted or len(set(wanted)) != len(wanted)):
            raise DataError("Raw selection needs unique nonempty domains")
        if wanted is not None:
            for name in wanted:
                _clean_name(name, "Raw domain")
        selected = []
        seen = set()
        path = self.root / "raw/fetches.jsonl"
        if path.is_file():
            with path.open("rb") as stream:
                for line in stream:
                    if not line.endswith(b"\n"):
                        break
                    raw = self._decode_raw_line(line)
                    identity = raw.get("batch_id")
                    if not isinstance(identity, str) or identity in seen:
                        raise DataError("Raw selection encountered a missing or duplicate batch ID")
                    seen.add(identity)
                    if (wanted is None or raw["domain"] in wanted) and (statuses is None or raw["status"] in statuses) and _stamp(raw["observed_at"]) <= cutoff:
                        selected.append({k: raw[k] for k in ("batch_id", "domain", "status", "observed_at", "payload_sha256")})
        selected.sort(key=lambda r: (_stamp(r["observed_at"]), r["batch_id"]))
        return {"domains": list(wanted) if wanted is not None else None,
                "receipt_cutoff": cutoff, "statuses": list(statuses) if statuses is not None else None,
                "raw_batch_ids": [r["batch_id"] for r in selected], "records": selected,
                "selection_sha256": sha256(_json_bytes(selected)).hexdigest()}

    def publish_snapshot(
        self, domains: Mapping[str, Any], *, parent_snapshot: str | None,
        build_context: Mapping[str, Any], promote: bool = True,
    ) -> dict[str, Any]:
        """Publish a complete manifest, then atomically replace current."""
        with self.writer():
            parent = self.load_snapshot(parent_snapshot) if parent_snapshot is not None else None
            return self._publish_snapshot_from_parent(
                domains, parent_snapshot=parent_snapshot, parent=parent,
                build_context=build_context, promote=promote)

    def _publish_snapshot_from_parent(
        self, domains: Mapping[str, Any], *, parent_snapshot: str | None,
        parent: Mapping[str, Any] | None, build_context: Mapping[str, Any], promote: bool,
    ) -> dict[str, Any]:
        """Internal publication using this writer's already validated parent.

        The caller must have loaded the fixed parent under the same writer
        lock and kept every nested parent container unchanged. Update builders
        replace modified domains; no caller-supplied validation flag or parent
        is accepted by the public publication method. Object/Raw checks and
        atomic publication use the same path for public and internal callers.
        """
        # Acquire the mutex before inspecting writer depth: another thread
        # cannot borrow a writer owned by the thread currently holding it.
        with self._mutex:
            if not self._writer_depth:
                raise DataError("parent reuse requires the active writer lock")
            if ((parent is None) != (parent_snapshot is None) or
                    (parent is not None and parent.get("snapshot_id") != parent_snapshot)):
                raise DataError("reused parent does not match the fixed Snapshot ID")
            body = {
                "schema_version": "local_data_v1", "parent_snapshot": parent_snapshot,
                "domains": dict(domains), "build_context": dict(build_context),
            }
            self._validate_manifest_body(body)
            changed_raw_ids: set[str] = set()
            for name, domain in domains.items():
                old = parent["domains"].get(name) if parent else None
                old_parts = {(p["uri"], p["file_sha256"]) for p in old["partitions"]} if old else set()
                old_raw = set(old["raw_batch_ids"]) if old else set()
                for part in domain["partitions"]:
                    if (part["uri"], part["file_sha256"]) in old_parts:
                        if not self._path(part["uri"]).is_file():
                            raise DataError(f"referenced object is missing: {part['uri']}")
                    else:
                        self._verified(part["uri"], part["file_sha256"])
                raw_ids = set(domain["raw_batch_ids"])
                changed_raw_ids.update(raw_ids - old_raw)
            # The immutable parent was validated at its publication. Rechecking
            # every historical Raw reference for every candidate chunk grows
            # quadratically; newly attached observations are verified here.
            for raw in self._raw_records(sorted(changed_raw_ids), shared_profiles=True).values():
                self._verified(raw["payload_uri"], raw["payload_sha256"])
            snapshot_id = "s_" + _json_digest(body)
            manifest = {"snapshot_id": snapshot_id, **body}
            self._atomic_json(self._path(f"snapshots/{snapshot_id}.json"), manifest, immutable=True)
            if promote:
                self._atomic(self.root / "current.json", _json_bytes({"snapshot_id": snapshot_id}))
            return manifest

    def promote_existing_snapshot(self, snapshot_id: str, *, expected_current: str | None) -> None:
        """Atomically select a completed candidate if current still matches its base.

        Retrying after a crash following the pointer swap is idempotent. A
        concurrent writer changing current is rejected without clobbering it.
        """
        with self.writer():
            self.load_snapshot(snapshot_id)
            pointer = self.root / "current.json"
            current = self.resolve("current") if pointer.exists() else None
            if current == snapshot_id:
                return
            if current != expected_current:
                raise ConflictError("current Snapshot changed since the bulk job base")
            self._atomic(pointer, _json_bytes({"snapshot_id": snapshot_id}))

    def write_operation(self, operation_id: str, state: Mapping[str, Any]) -> None:
        """Atomically checkpoint a resumable update or its failure details."""
        _clean_name(operation_id, "operation ID")
        with self.writer():
            self._atomic(self._path(f"operations/{operation_id}.json"), _json_bytes(dict(state)))

    def read_operation(self, operation_id: str) -> dict[str, Any] | None:
        _clean_name(operation_id, "operation ID")
        try:
            return json.loads(self._path(f"operations/{operation_id}.json").read_bytes())
        except FileNotFoundError:
            return None
        except json.JSONDecodeError as exc:
            raise DataError(f"operation checkpoint is corrupt: {operation_id}") from exc
