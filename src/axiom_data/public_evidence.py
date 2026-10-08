"""Reviewed public documents bound to exact fact revisions in one Snapshot.

Evidence is an ordinary domain, so its original document and assertions travel
with Raw and rebuild without a new registry. Attaching evidence is a write;
reading it only joins an immutable, normally tiny table. This API checks exact
fact identity, not the truth of a document interpretation: the caller must review
all business fields and record a precise document locator before attesting.
"""
from __future__ import annotations

import base64
from datetime import date, datetime, timezone
from hashlib import sha256
import json
from typing import Any, Mapping, Sequence

from .protocols import ConflictError, DataError
from .provider_local import _contract, _f
from .storage import _json_bytes
from .updates import apply_saved_raw

_META = {"revision_id", "revision_sequence", "first_observed_at", "raw_batch_id",
         "source_available_at", "evidence_ref"}
CONTRACT = _contract("public_evidence", ("target_domain", "target_key", "target_revision"), {
    "target_domain": _f("string", False), "target_key": _f("string", False),
    "target_revision": _f("string", False), "public_at": _f("timestamp", False),
    "document_sha256": _f("string", False), "source_url": _f("string", False),
    "locator": _f("string", False), "asserted_values": _f("string", False),
})
PROFILE = {"id": "reviewed_public_evidence.v1",
           "field_map": {f: f for f in CONTRACT["fields"] if f not in _META},
           "date_formats": {}, "revision_order": "source_sequence",
           "basis": "reviewed exact-revision document assertion; not automatic certification"}


def _plain(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def fact_values(row: Mapping[str, Any]) -> dict[str, Any]:
    """Return all business fields to compare against an independent document."""
    return {k: _plain(v) for k, v in row.items() if k not in _META}


def _key(row, fields):
    return _json_bytes({k: _plain(row[k]) for k in fields}).decode()


def attach_public_evidence(store, *, snapshot: str, document: bytes,
                           source_url: str, assertions: Sequence[Mapping[str, Any]],
                           operation_id: str, promote: bool = True):
    """Persist a reviewed original document and bind its exact target revisions.

    Each assertion has domain, key, revision_id, values (all business fields),
    public_at (aware timestamp), and locator. `public_at` is the conservative
    documented availability, not receipt time. The caller must independently
    establish it; a date-only announcement should use the declared next-open
    rule. Missing/mismatching values fail after Raw is saved, before publication.
    Reusing an operation requires identical bytes/assertions and preserves the
    first receipt. It cannot silently attest a later changed vendor revision.
    """
    if not document or not isinstance(document, bytes) or not assertions:
        raise DataError("a nonempty original document and reviewed assertions are required")
    if not source_url.startswith(("https://", "http://")):
        raise DataError("public evidence requires its original source URL")
    digest = sha256(document).hexdigest()
    records = []
    for assertion in assertions:
        records.append({"target_domain": assertion["domain"],
                        "target_key": _json_bytes(assertion["key"]).decode(),
                        "target_revision": assertion["revision_id"],
                        "public_at": assertion["public_at"],
                        "document_sha256": digest, "source_url": source_url,
                        "locator": assertion["locator"],
                        "asserted_values": _json_bytes(assertion["values"]).decode()})
    # One copy of the original document per response, even for many assertions.
    payload = _json_bytes({"document_base64": base64.b64encode(document).decode(),
                           "assertions": records})
    request = {"source_url": source_url, "document_sha256": digest,
               "target_snapshot": snapshot, "kind": "reviewed_public_evidence"}
    with store.writer():
        prior = store.find_raw_by_operation(operation_id)
        raw = prior.get(0)
        if raw is None:
            raw = store.write_raw(payload, domain="public_evidence", request=request,
                                  contract=CONTRACT, source_profile=PROFILE,
                                  normalizer="public_document_v1", observed_at=datetime.now(timezone.utc),
                                  operation_id=operation_id, batch_index=0)
        elif raw["payload_sha256"] != sha256(payload).hexdigest() or raw["request"] != request:
            raise ConflictError("evidence operation is bound to different inputs")
        manifest = store.load_snapshot(snapshot)
        by_domain = {}
        for assertion in assertions:
            name = assertion["domain"]
            if name == "public_evidence" or name not in manifest["domains"]:
                raise DataError("evidence must target an existing fact domain")
            domain = manifest["domains"][name]
            fields = domain["contract"]["logical_key"]
            if set(assertion["key"]) != set(fields) or not assertion["locator"].strip():
                raise DataError("evidence needs the complete logical key and document locator")
            if name not in by_domain:
                by_domain[name] = {(_key(r, fields), r["revision_id"]): r
                    for p in domain["partitions"] for r in store.read_partition(p).to_pylist()}
            row = by_domain[name].get((_json_bytes(assertion["key"]).decode(), assertion["revision_id"]))
            if row is None or fact_values(row) != assertion["values"]:
                raise DataError("document assertion does not match all fields of the exact fact revision")
        return apply_saved_raw(store, base_snapshot=snapshot, raw_batch_ids=[raw["batch_id"]],
                               operation_id=operation_id + "-publish", promote=promote,
                               build_context={"method": "reviewed_public_evidence.v1"})


def document_assertions(payload):
    """Decode one saved envelope and verify that every assertion binds its bytes."""
    value = json.loads(payload)
    document = base64.b64decode(value["document_base64"], validate=True)
    digest = sha256(document).hexdigest()
    rows = value["assertions"]
    if not rows or any(row["document_sha256"] != digest for row in rows):
        raise DataError("public evidence document digest differs from assertion")
    return rows


def evidence_index(store, snapshot, domain_name):
    """Read only evidence for the requested domain; an absent domain costs no I/O."""
    native=getattr(store,'_native_evidence_index',None)
    if native is not None:
        return native(domain_name)
    domain = snapshot.get("domains", {}).get("public_evidence")
    if not domain or domain_name == "public_evidence":
        return {}
    result = {}
    for part in domain["partitions"]:
        for row in store.read_partition(part).to_pylist():
            if row["target_domain"] != domain_name:
                continue
            key = (row["target_key"], row["target_revision"])
            previous = result.get(key)
            if previous is not None and (previous["public_at"], previous["document_sha256"]) != (row["public_at"], row["document_sha256"]):
                raise ConflictError("conflicting public evidence for one fact revision")
            result[key] = row
    return result


def apply_evidence(rows, *, index, key_fields):
    """Enrich selected in-memory rows; first_observed_at and Raw identity stay put."""
    if not index:
        return rows
    for row in rows:
        evidence = index.get((_key(row, key_fields), row.get("revision_id")))
        if evidence is not None:
            existing = row.get("source_available_at")
            if existing is not None and existing != evidence["public_at"]:
                raise ConflictError("attached public time conflicts with canonical revision evidence")
            row["source_available_at"] = evidence["public_at"]
            row["evidence_ref"] = "raw:" + evidence["raw_batch_id"] + "#" + evidence["document_sha256"]
    return rows
