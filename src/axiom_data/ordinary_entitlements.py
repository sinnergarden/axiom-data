"""Reviewed ordinary A-share entitlements beside immutable supplier facts.

This narrow producer does not interpret legal documents. A reviewer declares
the exact disclosure/revision bindings, locators, applicability and allocation
rule. The producer verifies that closure and computes effective per-share terms.
There is no network, Snapshot write, account operation or process-wide cache.
"""
from copy import deepcopy
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation, localcontext
from fractions import Fraction
from hashlib import sha256
import json
import re
from zoneinfo import ZoneInfo

from .protocols import QueryError

VERSION = "ordinary_shareholder_interpretation_v1"
BASIS = "mixed_reviewed_sources_v1"
HOLDER_CLASS = "ordinary_secondary_market_a_share_v1"
_KEY = {"security_id", "report_period", "announcement_date", "process_status"}
_DATES = ("record_date", "ex_date", "payment_date", "stock_listing_date")
_STRENGTHS = {"explicit_no_cash", "approved_scheme_chain", "share_only_inference"}
_ROLES = {"implementation", "approved_scheme", "original_scheme"}
_RULES = {"ordinary_pro_rata_transfer", "original_holders_receive_no_new_shares",
          "proportional_common_surrender", "eligible_proportional_unknown_denominator"}
_REF = re.compile(r"sha256:[0-9a-f]{64}\Z")


def _require(ok, message):
    if not ok:
        raise QueryError(message)


def _encoded(value):
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"), allow_nan=False).encode()
    except (TypeError, ValueError) as exc:
        raise QueryError("entitlement inputs require strict JSON") from exc


def _ref(value):
    return "sha256:" + sha256(_encoded(value)).hexdigest()


def _instant(value):
    try:
        result = datetime.fromisoformat(value)
        _require(result.tzinfo is not None and result.utcoffset() is not None,
                 "entitlement clocks require aware timestamps")
        return result
    except (TypeError, ValueError) as exc:
        raise QueryError("invalid entitlement timestamp") from exc


def _day(value):
    try:
        _require(type(value) is str and date.fromisoformat(value).isoformat() == value,
                 "entitlement dates require canonical ISO dates")
        return value
    except (TypeError, ValueError) as exc:
        raise QueryError("invalid entitlement date") from exc


def _integer(value):
    _require(type(value) is int and value > 0, "allocation quantity must be a positive integer")
    return value


def _rate(value):
    _require(type(value) is str, "allocation rate requires an exact decimal string")
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise QueryError("invalid allocation rate") from exc
    _require(number.is_finite() and number >= 0, "allocation rate must be finite and nonnegative")
    return Fraction(number)


def _scalar(value):
    # A repeating rational cannot be exact in v1's decimal scalar. Keep its
    # exact numerator/denominator in the bound interpretation. Sixty significant
    # digits also avoid the six-decimal truncation of the HNA minority ratio.
    with localcontext() as context:
        context.prec = 60
        return format(Decimal(value.numerator) / Decimal(value.denominator), "f")


def _alias(value):
    _require(type(value) is dict and set(value) == _KEY and
             type(value["security_id"]) is str and bool(value["security_id"]) and
             value["process_status"] == "实施", "complete implemented Native alias required")
    _day(value["report_period"])
    _day(value["announcement_date"])
    return _encoded(value)


def _allocation(entry, issuer_ratio, roles):
    terms = entry["allocation"]
    _require(type(terms) is dict and terms.get("rule") in _RULES,
             "reviewed ordinary-holder allocation rule required")
    rule = terms["rule"]
    if rule == "ordinary_pro_rata_transfer":
        _require(set(terms) == {"rule", "shares_per_share"}, "unexpected pro-rata terms")
        ratio = _rate(terms["shares_per_share"])
        _require(issuer_ratio is not None and ratio == _rate(issuer_ratio),
                 "ordinary pro-rata rate differs from bound supplier total")
    elif rule == "original_holders_receive_no_new_shares":
        _require(set(terms) == {"rule"}, "unexpected no-distribution terms")
        ratio = Fraction(0)
    elif rule == "proportional_common_surrender":
        _require(set(terms) == {"rule", "original_shares_per_share", "base_shares", "surrender_shares"}
                 and "approved_scheme" in roles,
                 "common surrender requires approved scheme and exact quantities")
        surrendered = terms["surrender_shares"]
        _require(type(surrendered) is list and bool(surrendered), "surrender quantities required")
        ratio = (_rate(terms["original_shares_per_share"]) -
                 Fraction(sum(_integer(q) for q in surrendered), _integer(terms["base_shares"])))
        _require(ratio >= 0, "negative minority allocation")
    else:
        _require(set(terms) == {"rule", "allocated_shares", "eligible_original_holdings"}
                 and terms["eligible_original_holdings"] is None,
                 "unestablished eligible denominator must remain null")
        _integer(terms["allocated_shares"])
        return None, {"numerator": terms["allocated_shares"], "denominator": None}
    return _scalar(ratio), {"numerator": ratio.numerator, "denominator": ratio.denominator}


def produce_ordinary_shareholder_action_facts(supplier_facts, *, supplier_ref, review,
                                              documents, next_open_session_by_date,
                                              observed_at):
    """Return a bound interpretation and compatible stock_action_facts_v1.

    Inputs are a fixed supplier v1 mapping/ref, a reviewed VERSION mapping,
    original public document bytes keyed by physical SHA-256, and a frozen
    next-open calendar. Review entries bind complete Native aliases and exact
    supplier revisions; they declare cash-zero evidence and one of four narrow
    allocation rules. Caller review establishes document meaning/applicability.

    Date-only documents use next A-share session 09:30 Asia/Shanghai. Each
    affected action's replay clock is the maximum of every necessary supplier
    and document boundary. BASIS explicitly retains vendor historical
    assumptions; it is not proof of full historical public vintage. Actual
    observations/retrievals remain separate. New revisions have observed_at as
    their actual producer observation. Unknown quantities stay null.

    The original Snapshot and supplier mapping remain unchanged. Unaffected
    actions remain exactly equal. Returns independent strict-JSON mappings and
    canonical refs; caller may save these as new immutable artifacts. Failure
    raises QueryError and has no side effects. Engine remains responsible for
    account eligibility, rounding and its declared listing-date simulation.
    """
    _require(type(supplier_facts) is dict and _ref(supplier_facts) == supplier_ref,
             "supplier facts content reference differs")
    _require(supplier_facts.get("contract_version") == "stock_action_facts_v1" and
             supplier_facts.get("producer") == "axiom-data" and
             supplier_facts.get("availability_basis") == "declared_vendor_assumption",
             "original supplier v1 facts and declared vendor assumption required")
    _require(type(review) is dict and set(review) == {"contract_version", "supplier_facts_ref",
             "holder_class", "holder_assumptions", "documents", "entries"} and
             review["contract_version"] == VERSION and review["supplier_facts_ref"] == supplier_ref and
             review["holder_class"] == HOLDER_CLASS and type(review["holder_assumptions"]) is list and
             bool(review["holder_assumptions"]) and
             all(type(x) is str and bool(x.strip()) for x in review["holder_assumptions"]),
             "explicit ordinary-holder review and assumptions required")
    _require(type(review["entries"]) is list and 0 < len(review["entries"]) <= 64,
             "ordinary entitlement review must have 1..64 scoped entries")
    receipt = _instant(observed_at)
    _require(receipt >= _instant(supplier_facts["observed_at"]), "producer precedes supplier observation")
    sessions = supplier_facts["calendar"]
    _require(type(sessions) is list and bool(sessions) and sessions == sorted(set(sessions)) and
             all(_day(s) for s in sessions) and type(next_open_session_by_date) is dict,
             "fixed supplier exchange calendar required")
    _require(type(documents) is dict and set(documents) == set(review["documents"]),
             "exact original document closure required")
    _require(sum(len(x) for x in documents.values() if type(x) is bytes) <= 16 * 1024 * 1024,
             "review document closure exceeds small producer budget")
    document_clocks = {}
    for ref, descriptor in review["documents"].items():
        raw = documents[ref]
        _require(type(ref) is str and bool(_REF.fullmatch(ref)) and type(raw) is bytes and
                 0 < len(raw) <= 2 * 1024 * 1024 and "sha256:" + sha256(raw).hexdigest() == ref,
                 "original document byte reference differs")
        _require(type(descriptor) is dict and set(descriptor) == {"source_url", "public_date", "retrieved_at"}
                 and type(descriptor["source_url"]) is str and
                 descriptor["source_url"].startswith("https://"), "original public document descriptor required")
        day = _day(descriptor["public_date"])
        following = _day(next_open_session_by_date.get(day))
        _require(sessions[0] <= day < sessions[-1] and
                 following == next(s for s in sessions if s > day),
                 "next-open mapping differs from fixed supplier exchange calendar")
        public_clock = datetime.combine(date.fromisoformat(following), time(9, 30), ZoneInfo("Asia/Shanghai"))
        retrieved = _instant(descriptor["retrieved_at"])
        _require(public_clock <= retrieved <= receipt, "document retrieval/producer chronology differs")
        document_clocks[ref] = public_clock
    originals = supplier_facts["actions"]
    _require(type(originals) is list and len(_encoded(supplier_facts)) <= 8 * 1024 * 1024,
             "supplier facts exceed small producer budget")
    index, all_aliases = {}, set()
    for position, action in enumerate(originals):
        key = (action["economic_event_id"], action["revision_ref"])
        _require(key not in index and _REF.fullmatch(action["revision_ref"]), "duplicate/invalid supplier revision")
        index[key] = (position, action)
        _require(type(action["aliases"]) is list and bool(action["aliases"]), "supplier aliases required")
        for alias in action["aliases"]:
            identity = _alias(alias)
            _require(identity not in all_aliases, "supplier Native alias assigned twice")
            all_aliases.add(identity)
        _require(_instant(action["first_observed_at"]) <= _instant(supplier_facts["observed_at"]),
                 "supplier action receipt after original producer")
    interpreted, claimed, output_ids, used_documents = [], set(), set(), set()
    for entry in review["entries"]:
        _require(type(entry) is dict and set(entry) == {"targets", "economic_event_id", "aliases",
                 "expected_phase_dates", "cash_evidence_strength", "dependencies", "eligibility",
                 "allocation", "delivery_basis"}, "exact reviewed entry fields required")
        _require(entry["cash_evidence_strength"] in _STRENGTHS and
                 type(entry["eligibility"]) is str and bool(entry["eligibility"].strip()) and
                 type(entry["delivery_basis"]) is str and bool(entry["delivery_basis"].strip()),
                 "cash evidence, applicability and delivery basis required")
        _require(type(entry["targets"]) is list and bool(entry["targets"]), "bound supplier targets required")
        targets, positions = [], []
        for target in entry["targets"]:
            _require(type(target) is dict and set(target) == {"economic_event_id", "revision_ref"},
                     "exact target identity/revision required")
            key = (target["economic_event_id"], target["revision_ref"])
            _require(key in index and key not in claimed, "missing or multiply reviewed supplier revision")
            position, action = index[key]
            claimed.add(key); targets.append(action); positions.append(position)
        aliases = entry["aliases"]
        _require(type(aliases) is list and bool(aliases), "reviewed complete alias set required")
        alias_keys = [_alias(a) for a in aliases]
        _require(len(set(alias_keys)) == len(alias_keys) and set(alias_keys) ==
                 {_alias(a) for action in targets for a in action["aliases"]},
                 "reviewed aliases differ from exact target closure")
        first = targets[0]
        _require(all(a["cash_dividend_before_tax_per_share"] is None or
                     _rate(a["cash_dividend_before_tax_per_share"]) == 0 for a in targets),
                 "cash-zero review cannot replace an observed positive dividend")
        _require(all(a["security_id"] == first["security_id"] and a["report_period"] == first["report_period"]
                 and all(a[k] == first[k] for k in _DATES) for a in targets),
                 "reviewed alias group has conflicting securities/periods/phases")
        _require(entry["expected_phase_dates"] == {k: first[k] for k in _DATES},
                 "official reviewed phases differ from supplier target")
        event_id = entry["economic_event_id"]
        _require(type(event_id) is str and bool(event_id.strip()) and event_id not in output_ids,
                 "unique explicit economic identity required")
        _require(len(targets) > 1 or event_id == first["economic_event_id"],
                 "single-action correction cannot reassign stable economic identity")
        output_ids.add(event_id)
        deps = entry["dependencies"]
        _require(type(deps) is list and bool(deps), "necessary official dependencies required")
        roles, dep_refs = set(), set()
        for dep in deps:
            _require(type(dep) is dict and set(dep) == {"document_ref", "role", "locator"} and
                     dep["document_ref"] in document_clocks and dep["role"] in _ROLES and
                     type(dep["locator"]) is str and bool(dep["locator"].strip()) and
                     dep["document_ref"] not in dep_refs, "unique exact document dependency/locator required")
            roles.add(dep["role"]); dep_refs.add(dep["document_ref"])
        _require("implementation" in roles and
                 (entry["cash_evidence_strength"] != "approved_scheme_chain" or "approved_scheme" in roles) and
                 (len(targets) == 1 or {"original_scheme", "approved_scheme"} <= roles),
                 "review lacks required implementation/scheme chain")
        issuer_ratio = first["stock_distribution_shares_per_share"]
        _require(all(a["stock_distribution_shares_per_share"] == issuer_ratio for a in targets),
                 "reviewed alias issuer quantities conflict")
        scalar, rational = _allocation(entry, issuer_ratio, roles)
        usable = max([_instant(a["available_at"]) for a in targets] + [document_clocks[r] for r in dep_refs])
        used_documents.update(dep_refs)
        interpreted.append({"review": deepcopy(entry), "target_positions": positions,
                            "supplier_actions": deepcopy(targets), "issuer_total_shares_per_share": issuer_ratio,
                            "effective_cash_per_share": "0", "effective_net_new_shares_per_share": scalar,
                            "effective_share_status": "value" if scalar is not None else "UNKNOWN",
                            "exact_net_new_share_ratio": rational, "usable_from": usable.isoformat(),
                            "actual_producer_observed_at": receipt.isoformat()})
    _require(used_documents == set(documents), "unused documents are outside the necessary dependency closure")
    unclaimed = [a for a in originals if (a["economic_event_id"], a["revision_ref"]) not in claimed]
    _require(not output_ids.intersection(a["economic_event_id"] for a in unclaimed),
             "effective identity collides with an unaffected supplier action")
    interpretation = {"contract_version": VERSION, "supplier_facts_ref": supplier_ref,
                      "original_identity_policy_ref": supplier_facts["identity_policy_ref"],
                      "snapshot_id": supplier_facts["snapshot_id"], "holder_class": HOLDER_CLASS,
                      "holder_assumptions": deepcopy(review["holder_assumptions"]),
                      "availability_basis": BASIS, "observed_at": receipt.isoformat(),
                      "clock_rule": "max all necessary official next-open and supplier declared boundaries; includes vendor historical assumption; actual receipts remain separate",
                      "next_open_calendar_ref": _ref(next_open_session_by_date),
                      "documents": deepcopy(review["documents"]), "entries": interpreted}
    interpretation_ref = _ref(interpretation)
    replacements, removed = {}, set()
    for item in interpreted:
        action = deepcopy(item["supplier_actions"][0])
        action.update(economic_event_id=item["review"]["economic_event_id"], identity_status="RESOLVED",
                      aliases=deepcopy(item["review"]["aliases"]), cash_dividend_before_tax_per_share="0",
                      stock_distribution_shares_per_share=item["effective_net_new_shares_per_share"],
                      available_at=item["usable_from"], first_observed_at=receipt.isoformat())
        action["raw_batch_ids"] = sorted({r for a in item["supplier_actions"] for r in a["raw_batch_ids"]})
        action["source_refs"] = sorted({r for a in item["supplier_actions"] for r in a["source_refs"]} | {interpretation_ref})
        action["revision_ref"] = _ref({"interpretation_ref": interpretation_ref, "review": item["review"],
                                       "effective_cash": "0", "effective_shares": item["effective_net_new_shares_per_share"]})
        positions = item["target_positions"]
        replacements[min(positions)] = action
        removed.update(positions)
    facts = deepcopy(supplier_facts)
    facts.update(observed_at=receipt.isoformat(), availability_basis=BASIS, identity_policy_ref=interpretation_ref,
                 supplemental_view_refs=[*supplier_facts["supplemental_view_refs"], interpretation_ref],
                 actions=[replacements[p] if p in replacements else deepcopy(a)
                          for p, a in enumerate(originals) if p not in removed or p in replacements])
    facts["limitations"] = [*facts["limitations"],
        "mixed_reviewed_sources_v1 includes vendor historical availability assumptions and reviewed date-only official next-open boundaries; it does not certify complete historical public vintages.",
        "Reviewed total stock_distribution_shares_per_share is ordinary-holder net entitlement; issuer aggregate and unchanged supplier bonus/capital components are retained independently in the bound interpretation/original source. Do not add components to the effective total.",
        "Ordinary holder exclusions are explicit model assumptions; listing dates proxy release under the consumer simulation, not actual account credit. Unknown eligible denominators remain null.",
        "Repeating per-share rates retain exact rational derivation in the interpretation and a 60-significant-digit decimal projection in v1; consumer rounding is a separately declared simulation."]
    return {"interpretation": interpretation, "interpretation_ref": interpretation_ref,
            "facts": facts, "facts_ref": _ref(facts)}
