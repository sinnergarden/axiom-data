"""Explicit saved price projections for retrospective review, never account input."""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path
import shutil
from tempfile import mkdtemp

from .derived import _instant, _meta, _number, adjust_prices
from .protocols import ConflictError, DataBatch, QueryError, _json_safe


SCHEMA = "review_display_v1"
EXPORTER_VERSION = "review_display_v1"
OHLC = ("open", "high", "low", "close")


def project_review_display(prices: DataBatch, factors: DataBatch, *, anchor_session: str) -> dict:
    """Project already PIT-selected OHLCV on one fixed final-session anchor.

    Inputs must be public market_daily/adjustment_factors DataBatches from one
    Snapshot, policy and common aware cutoff. The complete input span ends at
    anchor_session; smaller viewports slice this saved result without reanchoring.
    Only OHLC changes. Native price, volume, amount and their provenance remain
    available. Missing factors/anchor produce null adjusted values and scale;
    prices never supply an inferred factor. The result is a display artifact,
    not a new Reader query, model input, execution price or account calculation.
    This function performs no I/O and preserves the inputs unchanged.
    """
    if not isinstance(prices, DataBatch) or not isinstance(factors, DataBatch):
        raise QueryError("review display requires Reader DataBatches")
    if prices.context.get("domain") != "market_daily" or factors.context.get("domain") != "adjustment_factors":
        raise QueryError("review display requires market_daily and adjustment_factors")
    query = prices.context.get("query") or {}
    if query.get("purpose") != "historical_exploration":
        raise QueryError("review display requires explicit historical_exploration purpose")
    if not query.get("sessions") or anchor_session != max(query["sessions"]):
        raise QueryError("review display anchor must end the complete input span")
    volume_fields = set(query.get("fields") or ()) & {"volume_shares", "volume_units"}
    if len(volume_fields) != 1 or "amount_cny" not in (query.get("fields") or ()):
        raise QueryError("review display requires one native volume field and amount_cny")
    volume = volume_fields.pop()
    allowed = {*OHLC, volume, "amount_cny", "pre_close"}
    if set(query.get("fields") or ()) - allowed:
        raise QueryError("review display accepts native OHLCV/amount fields only")
    unit = (prices.field_meta.get("close") or {}).get("unit")
    if unit not in {"CNY/share", "CNY/fund unit"} or any(
            (prices.field_meta.get(f) or {}).get("unit") != unit for f in OHLC):
        raise QueryError("review display OHLC must have one declared native price unit")
    expected_volume = ("volume_shares", "shares") if unit == "CNY/share" else ("volume_units", "fund units")
    if (volume, (prices.field_meta.get(volume) or {}).get("unit")) != expected_volume:
        raise QueryError("review display volume unit differs from native price identity")
    if (prices.field_meta.get("amount_cny") or {}).get("unit") != "CNY":
        raise QueryError("review display amount must retain CNY units")
    if (factors.field_meta.get("factor") or {}).get("unit") != "dimensionless":
        raise QueryError("review display factor must be dimensionless")
    adjusted = adjust_prices(prices, factors, fields=OHLC, anchor_session=anchor_session,
                             factor_field="factor")
    native = {(r["security_id"], r["session"]): r for r in prices.to_json()["records"]}
    cutoff = _instant(adjusted.context["derivation"]["decision_cutoff"], "display cutoff")
    for field in (volume, "amount_cny", *(("pre_close",) if "pre_close" in query["fields"] else ())):
        if field not in prices.frame.columns:
            raise QueryError(f"review display lacks native {field}")
        _, metadata = _meta(prices, field, set(native), "prices")
        if any(item.get("usable_from") is not None and
               _instant(item["usable_from"], "native usable_from") > cutoff for item in metadata.values()):
            raise QueryError("review display native provenance is later than cutoff")
    factor_rows = {(r["security_id"], r["session"]): r for r in factors.to_json()["records"]}
    provenance = {(r["security_id"], r["session"]): r
                  for r in factors.field_meta["factor"]["by_key"]}
    records, scale_meta = [], []
    for adjusted_row in adjusted.to_json()["records"]:
        key = (adjusted_row["security_id"], adjusted_row["session"])
        anchor_key = (key[0], anchor_session)
        value, value_state = _number(factor_rows[key]["factor"])
        anchor, anchor_state = _number(factor_rows[anchor_key]["factor"])
        reason = None
        if anchor_state:
            reason = "missing_anchor_factor" if anchor_state == "missing" else "invalid_anchor_factor"
        elif anchor <= 0:
            reason = "invalid_anchor_factor"
        elif value_state:
            reason = "missing_factor" if value_state == "missing" else "invalid_factor"
        elif value <= 0:
            reason = "invalid_factor"
        scale = None if reason else value / anchor
        if scale is not None and not isfinite(scale):
            scale, reason = None, "invalid_display_scale"
        row = {**adjusted_row, **{f"native_{f}": native[key][f] for f in OHLC},
               volume: native[key][volume], "amount_cny": native[key]["amount_cny"],
               "display_scale": scale}
        if "pre_close" in query["fields"]:
            row["native_pre_close"] = native[key]["pre_close"]
        records.append(row)
        scale_meta.append({"security_id": key[0], "session": key[1], "missing_reason": reason,
                           "factor_provenance": deepcopy(provenance[key]),
                           "anchor_factor_provenance": deepcopy(provenance[anchor_key])})
    field_meta = deepcopy(dict(adjusted.field_meta))
    for f in OHLC:
        field_meta[f"native_{f}"] = deepcopy(prices.field_meta[f])
    for f in (volume, "amount_cny"):
        field_meta[f] = deepcopy(prices.field_meta[f])
    if "pre_close" in query["fields"]:
        field_meta["native_pre_close"] = deepcopy(prices.field_meta["pre_close"])
    field_meta["display_scale"] = {"dtype": "float64", "unit": "dimensionless", "by_key": scale_meta}
    return _json_safe({
        "contract_version": SCHEMA, "records": records, "field_meta": field_meta,
        "context": {"usage": "retrospective_review", "snapshot_id": prices.context["snapshot_id"],
                    "anchor_session": anchor_session,
                    "knowledge_cutoff": adjusted.context["derivation"]["decision_cutoff"],
                    "pit_policy": query["pit_policy"], "default_price_basis": "common_anchor_adjusted_v1",
                    "native_price_basis": "unadjusted", "derivation": adjusted.context["derivation"],
                    "price_source": deepcopy(dict(prices.context)),
                    "factor_source": deepcopy(dict(factors.context)),
                    "limitations": adjusted.context["limitations"]}})


def _auxiliary(batch, projection, domain):
    if not isinstance(batch, DataBatch) or batch.context.get("domain") != domain:
        raise QueryError(f"review display auxiliary must be a {domain} Reader batch")
    context, query = projection["context"], batch.context.get("query") or {}
    if batch.context.get("contract_version") != "data_batch_v1" or not batch.context.get("reader_version"):
        raise QueryError("review display auxiliary must retain Reader identity")
    if batch.context.get("snapshot_id") != context["snapshot_id"] or query.get("pit_policy") != context["pit_policy"]:
        raise QueryError("review display auxiliary Snapshot/policy differs")
    if query.get("purpose") != "historical_exploration":
        raise QueryError("review display auxiliary requires historical_exploration")
    if _instant(query.get("cutoff"), "auxiliary cutoff") != _instant(context["knowledge_cutoff"], "display cutoff"):
        raise QueryError("review display auxiliary cutoff differs")
    symbols = {r["security_id"] for r in projection["records"]}
    if not set(query.get("symbols") or ()).issubset(symbols):
        raise QueryError("review display auxiliary symbols exceed display scope")
    if domain == "security_master":
        cutoff = _instant(context["knowledge_cutoff"], "display cutoff")
        for metadata in (batch.field_meta.get("name") or {}).get("by_key", []):
            if metadata.get("first_observed_at") is not None and _instant(
                    metadata["first_observed_at"], "name observation") > cutoff:
                raise QueryError("snapshot name labels were observed after display cutoff")
    return batch.to_json()


def save_review_display(prices, factors, *, anchor_session, destination,
                        security_master=None, events=()) -> dict:
    """Save fixed Reader inputs as a new immutable review directory.

    Writes ohlcv.json and manifest.json plus explicit optional securities.json /
    events.json. The latter retain complete owner Reader records/metadata; no
    event, name, account or source is inferred. Event batches share the display's
    Snapshot/policy/cutoff. A name batch is a security_master EventQuery result;
    terminal labels carry unknown name-validity intervals, not listing dates.
    Existing destinations are refused. A failed write leaves no final directory.
    File digests identify saved bytes; this helper has no registry/cache/current
    mutation, network, source acquisition or implicit Reader calls.
    """
    destination = Path(destination)
    if destination.exists():
        raise ConflictError("review display destination already exists")
    projection = project_review_display(prices, factors, anchor_session=anchor_session)
    outputs = {"ohlcv.json": projection}
    if security_master is not None:
        names = _auxiliary(security_master, projection, "security_master")
        if not {"name", "source_code"}.issubset(security_master.frame.columns):
            raise QueryError("review display names require saved name and source_code fields")
        outputs["securities.json"] = {"name_kind": "snapshot_label", "name_validity": "unknown",
                                      "batch": names}
    event_outputs = {}
    for batch in events:
        domain = batch.context.get("domain") if isinstance(batch, DataBatch) else None
        if domain not in {"corporate_actions", "fund_share_conversions"} or domain in event_outputs:
            raise QueryError("review display needs distinct explicit supported event domains")
        event_outputs[domain] = _auxiliary(batch, projection, domain)
    if event_outputs:
        outputs["events.json"] = event_outputs
    manifest = {"contract_version": SCHEMA, "exporter_version": EXPORTER_VERSION,
                "context": projection["context"], "files": {},
                "names_status": "saved_snapshot_labels" if security_master is not None else "not_requested",
                "events_status": "saved_selected_scope" if event_outputs else "not_requested",
                "account_effect": "none; Engine owns saved fill display coordinates"}
    if security_master is not None:
        named = {r["security_id"] for r in outputs["securities.json"]["batch"]["records"]
                 if r.get("name") and r.get("source_code")}
        manifest["missing_name_security_ids"] = sorted(
            {r["security_id"] for r in projection["records"]} - named)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(mkdtemp(prefix=".review-display-", dir=destination.parent))
    try:
        for name, wire in outputs.items():
            payload = (json.dumps(wire, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n").encode()
            (temporary / name).write_bytes(payload)
            manifest["files"][name] = {"uri": name, "sha256": sha256(payload).hexdigest(), "bytes": len(payload)}
        (temporary / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, allow_nan=False,
                                                            indent=2) + "\n", encoding="utf-8")
        if destination.exists():
            raise ConflictError("review display destination already exists")
        temporary.rename(destination)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return manifest
