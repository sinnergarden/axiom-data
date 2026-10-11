"""Synthetic exact-revision, document, allocation and clock counterexamples."""
from copy import deepcopy
from decimal import Context, Decimal, ROUND_FLOOR, ROUND_HALF_UP, localcontext
from hashlib import sha256
import json
import unittest

from axiom_data import QueryError, produce_ordinary_shareholder_action_facts


def ref(value):
    return "sha256:" + sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                       separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def alias(security, announcement="2020-01-02"):
    return dict(security_id=security, report_period="2019-12-31",
                announcement_date=announcement, process_status="实施")


def action(security, identity, ratio="0.7", announcement="2020-01-02"):
    return dict(economic_event_id=identity, revision_ref=ref(identity), identity_status="RESOLVED",
                security_id=security, report_period="2019-12-31", record_date="2020-01-10",
                ex_date="2020-01-13", payment_date=None, stock_listing_date="2020-01-13",
                cash_dividend_before_tax_per_share=None, stock_distribution_shares_per_share=ratio,
                bonus_shares_per_share=None, capital_transfer_shares_per_share=ratio,
                available_at="2020-01-03T09:30:00+08:00", first_observed_at="2026-01-01T00:00:00+00:00",
                raw_batch_ids=["b_"+identity], source_refs=[ref("native")],
                aliases=[alias(security, announcement)])


def fixture():
    actions = [action("normal", "normal"), action("minority", "minority", "1.92387"),
               action("unknown", "unknown", "1.8"), action("zero", "zero", "1.623"),
               action("zero", "zero-alias", "1.623", "2020-01-03"),
               action("untouched", "untouched", "0.1")]
    actions[3]["cash_dividend_before_tax_per_share"] = "0.0"
    actions[3]["identity_status"] = actions[4]["identity_status"] = "UNRESOLVED"
    source = dict(contract_version="stock_action_facts_v1", producer="axiom-data", snapshot_id="s_fixed",
                  observed_at="2026-01-02T00:00:00+00:00", availability_basis="declared_vendor_assumption",
                  universe=[a["security_id"] for a in actions if a["economic_event_id"] != "zero-alias"],
                  calendar=["2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07", "2020-01-08",
                            "2020-01-09", "2020-01-10", "2020-01-13"], parent_native_refs=[ref("parent")],
                  supplemental_view_refs=[ref("native")], identity_policy_ref=ref("policy"),
                  actions=actions, limitations=["vendor historical assumption"])
    documents = {"sha256:"+sha256(b).hexdigest(): b for b in (b"implementation", b"approved", b"original")}
    doc_refs = list(documents)
    descriptors = {r: dict(source_url="https://issuer.example/"+str(i), public_date=day,
                          retrieved_at="2026-01-03T00:00:00+00:00")
                   for i, (r, day) in enumerate(zip(doc_refs, ("2020-01-06", "2020-01-02", "2020-01-02")))}
    def entry(targets, allocation, strength="explicit_no_cash"):
        return dict(targets=[{k:a[k] for k in ("economic_event_id", "revision_ref")} for a in targets],
                    economic_event_id=targets[0]["economic_event_id"], aliases=[b for a in targets for b in a["aliases"]],
                    expected_phase_dates={k:targets[0][k] for k in ("record_date", "ex_date", "payment_date", "stock_listing_date")},
                    cash_evidence_strength=strength, eligibility="ordinary account outside excluded classes",
                    allocation=allocation, delivery_basis="scheduled listing proxy, actual credit unknown",
                    dependencies=[dict(document_ref=doc_refs[0], role="implementation", locator="section 2/5")])
    entries = [entry(actions[:1], dict(rule="ordinary_pro_rata_transfer", shares_per_share="0.7"), "share_only_inference"),
               entry(actions[1:2], dict(rule="proportional_common_surrender", original_shares_per_share="2",
                     base_shares=3907592460, surrender_shares=[3300000000,1583247960])),
               entry(actions[2:3], dict(rule="eligible_proportional_unknown_denominator",
                     allocated_shares=1113309515, eligible_original_holdings=None)),
               entry(actions[3:5], dict(rule="original_holders_receive_no_new_shares"))]
    entries[1]["dependencies"].append(dict(document_ref=doc_refs[1], role="approved_scheme", locator="section 3"))
    entries[3]["dependencies"].extend(dict(document_ref=doc_refs[i], role=role, locator="approved adjustment")
                                      for i,role in ((1,"approved_scheme"),(2,"original_scheme")))
    review = dict(contract_version="ordinary_shareholder_interpretation_v1", supplier_facts_ref=ref(source),
                  holder_class="ordinary_secondary_market_a_share_v1", holder_assumptions=["not top 50/control/designated creditor"],
                  documents=descriptors, entries=entries)
    calendar={"2020-01-02":"2020-01-03", "2020-01-06":"2020-01-07", "2020-01-08":"2020-01-09"}
    return source, review, documents, calendar


class OrdinaryEntitlements(unittest.TestCase):
    def setUp(self):
        self.source, self.review, self.documents, self.calendar = fixture()

    def produce(self):
        return produce_ordinary_shareholder_action_facts(self.source, supplier_ref=ref(self.source),
            review=self.review, documents=self.documents, next_open_session_by_date=self.calendar,
            observed_at="2026-01-04T00:00:00+00:00")

    def test_v1_shape_source_preservation_unknown_and_aliases(self):
        before=deepcopy((self.source,self.review)); output=self.produce()
        facts=output["facts"]; items={a["economic_event_id"]:a for a in facts["actions"]}
        self.assertEqual(set(facts),set(self.source))
        self.assertEqual(set(items["minority"]),set(self.source["actions"][1]))
        self.assertEqual(len(facts["actions"]),5)
        self.assertEqual(items["untouched"],self.source["actions"][-1])
        self.assertIsNone(items["unknown"]["stock_distribution_shares_per_share"])
        self.assertEqual(items["zero"]["stock_distribution_shares_per_share"],"0")
        self.assertEqual(len(items["zero"]["aliases"]),2)
        self.assertEqual(items["zero"]["capital_transfer_shares_per_share"],"1.623")
        for a in facts["actions"][:-1]:
            self.assertEqual(a["cash_dividend_before_tax_per_share"],"0")
            self.assertIn(output["interpretation_ref"],a["source_refs"])
            self.assertEqual(a["first_observed_at"],"2026-01-04T00:00:00+00:00")
        self.assertEqual(output["facts_ref"],ref(facts))
        self.assertEqual(output["interpretation_ref"],ref(output["interpretation"]))
        self.assertEqual((self.source,self.review),before)
        facts["actions"][0]["aliases"][0]["security_id"]="polluted"
        output["interpretation"]["documents"].clear()
        self.assertEqual(self.produce()["facts"]["actions"][0]["security_id"],"normal")

    def test_every_dependency_clock_and_actual_receipt(self):
        doc=list(self.documents)[1]
        self.review["documents"][doc]["public_date"]="2020-01-08"
        output=self.produce(); items={a["economic_event_id"]:a for a in output["facts"]["actions"]}
        self.assertEqual(items["minority"]["available_at"],"2020-01-09T09:30:00+08:00")
        self.assertEqual(items["normal"]["available_at"],"2020-01-07T09:30:00+08:00")
        self.assertEqual(output["interpretation"]["availability_basis"],"mixed_reviewed_sources_v1")
        self.assertIn("vendor historical assumption",output["interpretation"]["clock_rule"])
        self.review["documents"][doc]["retrieved_at"]="2026-01-05T00:00:00+00:00"
        with self.assertRaises(QueryError): self.produce()

    def test_supplier_boundary_is_also_necessary(self):
        self.source["actions"][1]["available_at"]="2020-01-09T12:00:00+08:00"
        self.review["supplier_facts_ref"]=ref(self.source)
        output=self.produce()
        self.assertEqual(output["facts"]["actions"][1]["available_at"],"2020-01-09T12:00:00+08:00")

    def test_document_mutation_and_wrong_supplier_ref_refused(self):
        doc=list(self.documents)[0]; self.documents[doc]+=b"mutation"
        with self.assertRaises(QueryError): self.produce()
        self.source,self.review,self.documents,self.calendar=fixture()
        self.source["actions"][0]["stock_distribution_shares_per_share"]="0.8"
        with self.assertRaises(QueryError): self.produce()

    def test_exact_common_surrender_and_decimal_floor_boundaries(self):
        output=self.produce(); item=output["interpretation"]["entries"][1]
        self.assertEqual(item["exact_net_new_share_ratio"],dict(numerator=2874448,denominator=3830973))
        scalar=item["effective_net_new_shares_per_share"]
        self.assertNotEqual(scalar,"0.750318")
        # Exercise integer-rational boundaries, their neighbors and large
        # positions under the consumer's 40-digit multiplication context.
        quantities=[1,3830972,3830973,3830974,3907592460]
        quantities += [3830973*k+j for k in (2,10,100,1000) for j in (-1,0,1)]
        for q in quantities:
            with localcontext(Context(prec=40,rounding=ROUND_HALF_UP)):
                grant=Decimal(q)*Decimal(scalar)
            self.assertEqual(int(grant.to_integral_value(rounding=ROUND_FLOOR)),q*2874448//3830973)
        self.assertEqual(item["issuer_total_shares_per_share"],"1.92387")

    def test_required_plan_chain_and_unknown_denominator(self):
        self.review["entries"][1]["dependencies"]=self.review["entries"][1]["dependencies"][:1]
        with self.assertRaises(QueryError): self.produce()
        self.source,self.review,self.documents,self.calendar=fixture()
        self.review["entries"][2]["allocation"]["eligible_original_holdings"]=4973861675
        with self.assertRaises(QueryError): self.produce()
        self.source,self.review,self.documents,self.calendar=fixture()
        self.review["entries"][0]["cash_evidence_strength"]="approved_scheme_chain"
        with self.assertRaises(QueryError): self.produce()

    def test_alias_omission_conflict_and_identity_collision(self):
        self.review["entries"][3]["aliases"].pop()
        with self.assertRaises(QueryError): self.produce()
        self.source,self.review,self.documents,self.calendar=fixture()
        self.source["actions"][4]["ex_date"]="2020-01-14"
        self.review["supplier_facts_ref"]=ref(self.source)
        with self.assertRaises(QueryError): self.produce()
        self.source,self.review,self.documents,self.calendar=fixture()
        self.review["entries"][3]["economic_event_id"]="untouched"
        with self.assertRaises(QueryError): self.produce()

    def test_invalid_units_scope_and_calendar(self):
        self.review["entries"][1]["allocation"]["base_shares"]=True
        with self.assertRaises(QueryError): self.produce()
        self.source,self.review,self.documents,self.calendar=fixture()
        self.calendar["2020-01-06"]="2020-01-06"
        with self.assertRaises(QueryError): self.produce()
        self.source,self.review,self.documents,self.calendar=fixture()
        self.review["entries"][0]["allocation"]["shares_per_share"]="NaN"
        with self.assertRaises(QueryError): self.produce()
        self.source,self.review,self.documents,self.calendar=fixture()
        self.source["actions"][0]["cash_dividend_before_tax_per_share"]="0.1"
        self.review["supplier_facts_ref"]=ref(self.source)
        with self.assertRaises(QueryError): self.produce()
        self.source,self.review,self.documents,self.calendar=fixture()
        self.calendar["2020-01-06"]="2020-01-09"
        with self.assertRaises(QueryError): self.produce()


if __name__ == "__main__":
    unittest.main()
