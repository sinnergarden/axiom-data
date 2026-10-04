"""Manually reviewed issuer unit-split facts through the normal Raw pipeline.

The input is a JSON bundle of original document bytes and reviewed records, not
a PDF parser. Its real receipt and declared announcement clock stay separate.
"""

from __future__ import annotations

import base64
from copy import deepcopy
from datetime import date, datetime
from hashlib import sha256
import json
from math import gcd
from typing import Mapping
from urllib.parse import urlparse

from .protocols import DataError, IngestBatch
from .provider_local import _contract, _f


DOMAIN = "fund_share_conversions"
NORMALIZER = "reviewed_fund_share_conversions_v1"
CONTRACT = _contract(DOMAIN, ("security_id", "event_id"), {
    **{name: _f("string", False) for name in (
        "security_id", "event_id", "event_type", "announcement_precision",
        "process_status", "effective_phase", "quantity_rounding",
        "document_refs", "extraction_version")},
    **{name: _f("date", False) for name in (
        "announcement_date", "record_date", "effective_date")},
    **{name: _f("date") for name in (
        "new_price_basis_session", "suspension_start", "suspension_end", "resume_session")},
    **{name: _f("string") for name in (
        "new_price_basis_basis", "quantity_rounding_scope", "suspension_scope")},
    "ratio_numerator": _f("int64", False, "dimensionless"),
    "ratio_denominator": _f("int64", False, "dimensionless"),
})
CONTRACT["contract_id"] = "local.fund_share_conversions.reviewed_disclosure.v1"
CONTRACT["fields"]["revision_sequence"] = {
    **CONTRACT["fields"]["revision_sequence"], "nullable": False}
_META = {"revision_id", "revision_sequence", "first_observed_at", "raw_batch_id",
         "source_available_at", "evidence_ref"}
_INPUT_FIELDS = set(CONTRACT["fields"]) - _META | {"revision_sequence"}


def reviewed_fund_share_conversion_batch(
        payload: bytes, *, observed_at: datetime | str,
        next_open_session_by_date: Mapping[str, str]) -> IngestBatch:
    """Describe a received reviewed bundle without reading, writing or fetching.

    ``payload`` contains ``documents`` and ``records``. Each document retains
    document_id, issuer, source_url, retrieval_host, sha256, document_base64,
    actual_document_observed_at, announcement_date, process_status and locator.
    Records use the frozen native contract plus reviewed revision_sequence.
    Document refs are a JSON list of IDs in this same bundle.

    ``observed_at`` is the actual first receipt of the complete bundle, at or
    after its original document receipts; retries/rebuilds reuse it unchanged.
    The supplied frozen next-open map declares the date-only 09:30 exploration
    assumption. It does not certify a historical publication timestamp.

    Pass the result to Data.update with a fixed base and promote=False. Update
    saves Raw first, then validates this bundle; failures never promote it.
    No document parsing, source call, state inference or price adjustment occurs.
    """
    fields = CONTRACT["fields"]
    profile = {
        "id": "issuer_fund_disclosure_supplement_v1",
        "source_kind": "explicit_bounded_manually_reviewed_issuer_disclosures",
        "field_map": {name: name for name in fields if name not in _META},
        "date_formats": {name: "ISO" for name, spec in fields.items()
                         if spec["dtype"] == "date"},
        "source_units": {"ratio_numerator": "dimensionless",
                         "ratio_denominator": "dimensionless"},
        "revision_order": "source_sequence",
        "revision_capability": "reviewed document order, not vendor sequence or complete revision history",
        "availability": {
            "date_field": "announcement_date", "date_rule": "next_open",
            "timezone": "Asia/Shanghai", "session_release_time": "09:30:00",
            "next_open_session_by_date": dict(next_open_session_by_date),
            "basis": "explicit date-only next-open assumption; public instant unverified",
        },
        "coverage_claim": "selected reviewed documents and events only",
        "missing_row": "unknown; no inference of no conversion or normal trading",
    }
    profile["field_map"]["revision_sequence"] = "revision_sequence"
    return IngestBatch(DOMAIN, payload, {"kind": NORMALIZER}, deepcopy(CONTRACT),
                       profile, observed_at, NORMALIZER)


def _day(value, name):
    if not isinstance(value, str):
        raise DataError(f"{name} must be an ISO date")
    try:
        parsed = date.fromisoformat(value)
        if value != parsed.isoformat():
            raise ValueError
        return parsed
    except ValueError as exc:
        raise DataError(f"{name} must be an ISO date") from exc


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise DataError(f"{name} must be a nonempty string")
    return value


def prepare_conversion_rows(batch: IngestBatch) -> list[dict]:
    """Validate retained document/extract bytes and return generic source rows.

    Called only after Raw persistence. No I/O or automatic PDF interpretation;
    document locators and extracted values remain human-reviewed assertions.
    Unknown phases/rounding and unstated suspension facts retain that meaning.
    """
    from .sources import _aware

    if (batch.domain != DOMAIN or dict(batch.contract) != CONTRACT or
            batch.source_profile.get("id") != "issuer_fund_disclosure_supplement_v1"):
        raise DataError("reviewed conversions require their frozen domain/contract/profile")
    try:
        bundle = json.loads(batch.payload)
    except (ValueError, UnicodeDecodeError) as exc:
        raise DataError("reviewed conversion bundle must be JSON") from exc
    if not isinstance(bundle, dict) or set(bundle) != {"documents", "records"}:
        raise DataError("reviewed conversion bundle requires documents and records")
    if not isinstance(bundle["documents"], list) or not bundle["documents"]:
        raise DataError("reviewed conversion bundle requires original documents")
    if not isinstance(bundle["records"], list) or not bundle["records"]:
        raise DataError("reviewed conversion bundle requires reviewed records")
    receipt = datetime.fromisoformat(_aware(batch.observed_at, "observed_at"))
    documents = {}
    for doc in bundle["documents"]:
        if not isinstance(doc, dict):
            raise DataError("reviewed document must be an object")
        for field in ("document_id", "issuer", "source_url", "retrieval_host",
                      "sha256", "document_base64", "locator"):
            _text(doc.get(field), field)
        identity = doc["document_id"]
        if identity in documents:
            raise DataError("duplicate reviewed document_id")
        url = urlparse(doc["source_url"])
        if url.scheme not in {"http", "https"} or url.hostname != doc["retrieval_host"]:
            raise DataError("reviewed document URL/host mismatch")
        try:
            original = base64.b64decode(doc["document_base64"], validate=True)
        except (ValueError, TypeError) as exc:
            raise DataError("invalid original document_base64") from exc
        if not original or sha256(original).hexdigest() != doc["sha256"]:
            raise DataError("reviewed document bytes/hash mismatch")
        captured = datetime.fromisoformat(_aware(
            doc.get("actual_document_observed_at"), "actual_document_observed_at"))
        if captured > receipt:
            raise DataError("bundle receipt precedes an original document receipt")
        _day(doc.get("announcement_date"), "document announcement_date")
        if doc.get("process_status") not in {"planned", "implemented"}:
            raise DataError("document process_status must identify plan or result")
        documents[identity] = doc

    rows = []
    events = {}
    clock = (batch.source_profile.get("availability") or {}).get("next_open_session_by_date", {})
    for source in bundle["records"]:
        if not isinstance(source, dict) or set(source) != _INPUT_FIELDS:
            raise DataError("reviewed conversion record fields differ from the frozen input contract")
        row = dict(source)
        for name, spec in CONTRACT["fields"].items():
            if name in _META:
                continue
            value = row[name]
            if value is None and spec["nullable"]:
                continue
            if spec["dtype"] == "date":
                _day(value, name)
            elif spec["dtype"] == "string":
                _text(value, name)
        if row["event_type"] != "unit_split":
            raise DataError("unsupported fund share conversion event_type")
        if (row["process_status"] not in {"planned", "implemented"} or
                row["announcement_precision"] != "day" or row["extraction_version"] != NORMALIZER):
            raise DataError("unsupported conversion stage, announcement precision or extraction version")
        numerator, denominator = row["ratio_numerator"], row["ratio_denominator"]
        if (any(type(v) is not int or not 0 < v < 2**63 for v in (numerator, denominator)) or
                numerator <= denominator or gcd(numerator, denominator) != 1):
            raise DataError("unit_split requires an exact positive reduced int64 ratio greater than one")
        sequence = row["revision_sequence"]
        if type(sequence) is not int or not 0 < sequence < 2**63:
            raise DataError("reviewed revision_sequence must be a positive int64")
        if row["effective_phase"] not in {"end_of_day", "not_stated"}:
            raise DataError("unsupported conversion effective_phase")
        rounding, scope = row["quantity_rounding"], row["quantity_rounding_scope"]
        if ((rounding == "not_stated" and scope is not None) or
                (rounding == "ceiling_to_whole_fund_unit" and scope != "registered_holder_units") or
                rounding not in {"not_stated", "ceiling_to_whole_fund_unit"}):
            raise DataError("unsupported or inconsistent quantity rounding/scope")
        if row["record_date"] > row["effective_date"]:
            raise DataError("registration cannot follow conversion")
        basis_day, basis = row["new_price_basis_session"], row["new_price_basis_basis"]
        if (basis_day is None) != (basis is None):
            raise DataError("new price basis requires both session and declared basis")
        if basis_day is not None and (basis_day < row["effective_date"] or
                (row["effective_phase"] == "end_of_day" and basis_day == row["effective_date"])):
            raise DataError("new price basis precedes the declared conversion phase")
        start, end, suspension = (row[n] for n in (
            "suspension_start", "suspension_end", "suspension_scope"))
        if any(v is not None for v in (start, end, suspension)):
            if start is None or end is None or suspension != "full_session" or start > end:
                raise DataError("suspension requires a complete full-session interval")
            if row["resume_session"] is not None and row["resume_session"] <= end:
                raise DataError("resume session overlaps announced suspension")
        announcement = row["announcement_date"]
        if _day(clock.get(announcement), "next-open session") <= _day(announcement, "announcement_date"):
            raise DataError("announcement next-open assumption must follow its date")
        try:
            refs = json.loads(row["document_refs"])
        except ValueError as exc:
            raise DataError("document_refs must be a JSON list") from exc
        if (not isinstance(refs, list) or not refs or
                any(not isinstance(ref, str) or ref not in documents for ref in refs) or
                len(set(refs)) != len(refs)):
            raise DataError("document_refs must resolve unique documents in the retained bundle")
        referenced = [documents[ref] for ref in refs]
        if any(doc["announcement_date"] > announcement for doc in referenced):
            raise DataError("an earlier revision cannot borrow a later document")
        if not any(doc["announcement_date"] == announcement and
                   doc["process_status"] == row["process_status"] for doc in referenced):
            raise DataError("revision announcement/stage requires its own reviewed document")
        row["document_refs"] = json.dumps(sorted(refs), ensure_ascii=False, separators=(",", ":"))
        events.setdefault((row["security_id"], row["event_id"]), []).append(row)
        rows.append(row)
    for revisions in events.values():
        ordered = sorted(revisions, key=lambda row: row["revision_sequence"])
        for previous, later in zip(ordered, ordered[1:]):
            if (previous["announcement_date"] > later["announcement_date"] or
                    (previous["process_status"] == "implemented" and later["process_status"] == "planned")):
                raise DataError("reviewed document sequence contradicts plan/result order")
    return rows
