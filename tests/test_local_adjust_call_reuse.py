"""Small correctness and call-count checks for invocation-local adjustment reuse."""

from collections import Counter, UserDict
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from axiom_data import derived
from axiom_data.protocols import DataBatch, QueryError, QuerySpec
from axiom_data.reader import SnapshotQueryReader
from test_local_derived import batch
from test_local_reader import MemoryStore, daily_manifest, daily_row


SESSIONS = ("2024-01-02", "2024-01-03", "2024-01-04")
CUTOFF = "2024-01-04T10:00:00+00:00"
AVAILABLE = "2024-01-03T09:00:00+00:00"


def multi_inputs():
    """Unordered native rows, two fields and distinct anchors per security."""
    prices = batch("market_daily", "close", {}, sessions=SESSIONS,
                   cutoffs={s: CUTOFF for s in SESSIONS})
    factors = batch("cumulative_factors", "adj_factor", {}, sessions=SESSIONS,
                    cutoffs={s: CUTOFF for s in SESSIONS})
    for value, fields in ((prices, ("close", "open")), (factors, ("adj_factor",))):
        records = []
        metadata = {field: {"dtype": "float64", "unit": "CNY" if value is prices else None,
                            "by_key": []} for field in fields}
        for index, session in enumerate(SESSIONS):
            for symbol in ("A", "B"):
                record = {"security_id": symbol, "session": session}
                for field in fields:
                    record[field] = ((100 if field == "close" else 200)+index+(10 if symbol == "B" else 0)
                                     if value is prices else
                                     (7.0 if symbol == "A" else 11.0) if index == 2 else float(index+1))
                    metadata[field]["by_key"].append({"security_id": symbol, "session": session,
                        "revision_id": f"{field}/{symbol}/{session}", "revision_sequence": 1,
                        "raw_batch_id": "synthetic", "usable_from": AVAILABLE,
                        "first_observed_at": AVAILABLE, "availability_basis": "first_observed_at",
                        "missing_reason": None, "evidence_ref": None,
                        "unknown_attributes": {"keep": [None, -0.0, "original"]}})
                records.append(record)
        # DataBatch is frozen; replace its owned frame/metadata through construction.
        result = DataBatch(pd.DataFrame.from_records(records[::-1]), metadata, deepcopy(value.context))
        result.context["query"].update(fields=list(fields), symbols=["B", "A"])
        if value is prices:
            new_prices = result
        else:
            new_factors = result
    return new_prices, new_factors


def adjusted(prices, factors, **options):
    return derived.adjust_prices(prices, factors, fields=("close", "open"),
                                 anchor_session=SESSIONS[-1], **options)


class AdjustmentCallReuseTests(unittest.TestCase):
    def test_anchor_numbers_are_parsed_once_per_security(self):
        prices, factors = multi_inputs()
        with patch.object(derived, "_number", wraps=derived._number) as numbers:
            result = adjusted(prices, factors)
        # Each anchor is parsed once as an anchor and once as its daily factor.
        seen = Counter(call.args[0] for call in numbers.call_args_list)
        self.assertEqual((seen[7.0], seen[11.0], numbers.call_count), (2, 2, 20))
        keys = [(s, d) for d in SESSIONS for s in ("B", "A")]
        self.assertEqual(list(zip(result.frame.security_id, result.frame.session)), keys)
        values = result.frame.set_index(["security_id", "session"])
        self.assertEqual(values.loc[("A", SESSIONS[0]), "close"], 100.0/7.0)
        self.assertEqual(values.loc[("B", SESSIONS[0]), "close"], 110.0/11.0)
        self.assertEqual(values.loc[("A", SESSIONS[-1]), "close"], 102.0)

    def test_stateful_decimal_subclass_retains_original_anchor_parsing(self):
        class ChangingDecimal(Decimal):
            def __new__(cls, value):
                result = super().__new__(cls, value)
                result.calls = 0
                return result
            def __float__(self):
                self.calls += 1
                return super().__float__()+self.calls
        prices, factors = multi_inputs()
        factors.frame["adj_factor"] = factors.frame["adj_factor"].astype(object)
        number = ChangingDecimal("7")
        factors.frame.loc[(factors.frame.security_id == "A") &
                          (factors.frame.session == SESSIONS[-1]), "adj_factor"] = number
        result = adjusted(prices, factors)
        # e321 observes anchor conversions 1/2, then daily factor 3 and anchor 4.
        self.assertEqual(result.frame[result.frame.security_id == "A"].close.tolist(),
                         [100.0/8.0, 101.0*2.0/9.0, 102.0*10.0/11.0])
        self.assertEqual(number.calls, 4)

    def test_equal_timestamp_strings_are_parsed_once_in_one_call(self):
        prices, factors = multi_inputs()
        with patch.object(derived, "_instant", wraps=derived._instant) as instants:
            result = adjusted(prices, factors)
        self.assertEqual(Counter(c.args[0] for c in instants.call_args_list),
                         Counter({CUTOFF: 1, AVAILABLE: 1}))
        self.assertEqual(result.context["derivation"]["decision_cutoff"], CUTOFF)

    def test_string_memo_is_bounded_and_does_not_evict_then_reparse_earlier_strings(self):
        days = tuple((date(2024, 1, 1)+timedelta(days=i)).isoformat() for i in range(130))
        cutoff = "2024-06-01T10:00:00+00:00"
        prices = batch("market_daily", "close", dict.fromkeys(days, 10.0), sessions=days,
                       cutoffs=dict.fromkeys(days, cutoff))
        factors = batch("cumulative_factors", "adj_factor", dict.fromkeys(days, 2.0), sessions=days,
                        cutoffs=dict.fromkeys(days, cutoff))
        stamps = [(datetime(2024, 1, 1, tzinfo=timezone.utc)+timedelta(seconds=i)).isoformat()
                  for i in range(130)]
        for value in (prices, factors):
            for item, stamp in zip(next(iter(value.field_meta.values()))["by_key"], stamps):
                item["usable_from"] = stamp
        with patch.object(derived, "_instant", wraps=derived._instant) as instants:
            result = derived.adjust_prices(prices, factors, fields=("close",), anchor_session=days[-1])
        seen = Counter(c.args[0] for c in instants.call_args_list)
        self.assertEqual(seen[cutoff], 1)
        self.assertTrue(all(seen[s] == 1 for s in stamps[:127]))
        self.assertTrue(all(seen[s] == 2 for s in stamps[127:]))
        self.assertEqual(instants.call_count, 134)
        self.assertEqual(result.frame.close.tolist(), [10.0]*130)

    def test_nonstring_instants_keep_the_original_parser_path(self):
        prices, factors = multi_inputs()
        cutoff = datetime.fromisoformat(CUTOFF)
        available = datetime.fromisoformat(AVAILABLE)
        for value in (prices, factors):
            value.context["query"]["cutoff_by_session"] = dict.fromkeys(SESSIONS, cutoff)
            for definition in value.field_meta.values():
                for item in definition["by_key"]:
                    item["usable_from"] = available
        with patch.object(derived, "_instant", wraps=derived._instant) as instants:
            result = adjusted(prices, factors)
        self.assertEqual(instants.call_count, 24)
        self.assertEqual(result.context["derivation"]["decision_cutoff"], CUTOFF)

    def test_provenance_comparisons_still_reject_late_or_naive_values(self):
        for domain, stamp, message in (("prices", "2024-01-04T10:00:01+00:00", "prices provenance"),
                                       ("factors", "2024-01-04T10:00:01+00:00", "factors provenance"),
                                       ("prices", "2024-01-03T09:00:00", "prices usable_from")):
            with self.subTest(domain=domain, stamp=stamp):
                prices, factors = multi_inputs()
                target = prices if domain == "prices" else factors
                next(iter(target.field_meta.values()))["by_key"][-1]["usable_from"] = stamp
                with self.assertRaisesRegex(QueryError, message):
                    adjusted(prices, factors)

    def test_repeated_calls_do_not_reuse_admission_or_mutated_anchor_values(self):
        prices, factors = multi_inputs()
        first = adjusted(prices, factors)
        factors.frame.loc[(factors.frame.security_id == "A") &
                          (factors.frame.session == SESSIONS[-1]), "adj_factor"] = 14.0
        second = adjusted(prices, factors)
        self.assertEqual(second.frame.set_index(["security_id", "session"]).loc[
            ("A", SESSIONS[0]), "close"], first.frame.set_index(["security_id", "session"]).loc[
                ("A", SESSIONS[0]), "close"]/2)
        early = "2024-01-03T08:00:00+00:00"
        for value in (prices, factors):
            value.context["query"]["cutoff_by_session"] = dict.fromkeys(SESSIONS, early)
        with self.assertRaisesRegex(QueryError, "factors provenance"):
            adjusted(prices, factors)

    def test_missing_unknown_and_invalid_anchors_keep_per_cell_reason_precedence(self):
        prices, factors = multi_inputs()
        for symbol, number in (("A", None), ("B", 0.0)):
            factors.frame.loc[(factors.frame.security_id == symbol) &
                              (factors.frame.session == SESSIONS[-1]), "adj_factor"] = number
        for item in factors.field_meta["adj_factor"]["by_key"]:
            if item["security_id"] == "A" and item["session"] == SESSIONS[-1]:
                item.update(usable_from=None, missing_reason="unknown")
        prices.frame.loc[(prices.frame.security_id == "B") &
                         (prices.frame.session == SESSIONS[0]), "close"] = None
        for item in prices.field_meta["close"]["by_key"]:
            if item["security_id"] == "B" and item["session"] == SESSIONS[0]:
                item["missing_reason"] = "source_unknown"
        result = adjusted(prices, factors)
        self.assertTrue(result.frame[["close", "open"]].isna().all().all())
        for field in ("close", "open"):
            for item in result.field_meta[field]["by_key"]:
                reason = ("missing_anchor_factor" if item["security_id"] == "A" else
                          "source_unknown" if field == "close" and item["session"] == SESSIONS[0]
                          else "invalid_anchor_factor")
                self.assertEqual(item["missing_reason"], reason)

    def test_input_output_and_sibling_lineage_are_isolated(self):
        prices, factors = multi_inputs()
        prices.context["diagnostic_symbols"] = prices.context["query"]["symbols"]
        before = prices.to_json(), factors.to_json()
        result = adjusted(prices, factors)
        result.field_meta["close"]["by_key"][0]["anchor_factor_provenance"]["unknown_attributes"]["keep"].append("bad")
        self.assertEqual(result.field_meta["open"]["by_key"][0]["anchor_factor_provenance"]["unknown_attributes"]["keep"],
                         [None, -0.0, "original"])
        self.assertEqual(result.field_meta["close"]["by_key"][2]["anchor_factor_provenance"]["unknown_attributes"]["keep"],
                         [None, -0.0, "original"])
        result.context["query"]["symbols"].append("C")
        self.assertEqual(result.context["diagnostic_symbols"], ["B", "A"])
        self.assertEqual((prices.to_json(), factors.to_json()), before)
        self.assertEqual(list(result.context), list(prices.context)+["derivation"])

    def test_plain_scalar_lineage_avoids_deepcopy_and_keeps_each_branch_detached(self):
        prices, factors = multi_inputs()
        provenance = []
        for value in (prices, factors):
            for definition in value.field_meta.values():
                for item in definition["by_key"]:
                    del item["unknown_attributes"]
                    provenance.append(item)
        source_ids = {id(item) for item in provenance}
        before = prices.to_json(), factors.to_json()
        with patch.object(derived, "deepcopy", wraps=deepcopy) as copies:
            result = adjusted(prices, factors)
        self.assertEqual(sum(id(c.args[0]) in source_ids for c in copies.call_args_list), 0)
        item = result.field_meta["close"]["by_key"][0]
        item["anchor_factor_provenance"]["revision_id"] = "mutated"
        item["price_provenance"]["raw_batch_id"] = "mutated"
        for field, index in (("open", 0), ("close", 2)):
            self.assertNotEqual(result.field_meta[field]["by_key"][index]["anchor_factor_provenance"]["revision_id"],
                                "mutated")
        self.assertEqual((prices.to_json(), factors.to_json()), before)

    def test_nested_lineage_keeps_deepcopy_for_every_output_branch(self):
        prices, factors = multi_inputs()
        with patch.object(derived, "deepcopy", wraps=deepcopy) as copies:
            result = adjusted(prices, factors)
        source_ids = {id(item) for value in (prices, factors)
                      for definition in value.field_meta.values() for item in definition["by_key"]}
        self.assertEqual(sum(id(c.args[0]) in source_ids for c in copies.call_args_list), 36)
        result.field_meta["close"]["by_key"][0]["factor_provenance"]["unknown_attributes"]["keep"].append("bad")
        self.assertEqual(result.field_meta["open"]["by_key"][0]["factor_provenance"]["unknown_attributes"]["keep"],
                         [None, -0.0, "original"])

    def test_nonplain_mapping_and_mutable_unknown_keys_remain_detached(self):
        class Key:
            def __init__(self): self.notes = ["original"]
            def __str__(self): return "unknown_key"
        class Provenance(dict):
            pass
        prices, factors = multi_inputs()
        item = factors.field_meta["adj_factor"]["by_key"][0]
        del item["unknown_attributes"]
        key = Key()
        item[key] = "scalar"
        original = prices.field_meta["close"]["by_key"][0]
        prices.field_meta["close"]["by_key"][0] = Provenance(original)
        result = adjusted(prices, factors)
        selected = next(p for p in result.field_meta["close"]["by_key"]
                        if p["security_id"] == item["security_id"] and p["session"] == item["session"])
        copied_key = next(k for k in selected["factor_provenance"] if isinstance(k, Key))
        self.assertIsNot(copied_key, key)
        copied_key.notes.append("bad")
        self.assertEqual(key.notes, ["original"])
        selected_price = next(p for p in result.field_meta["close"]["by_key"]
                              if p["security_id"] == original["security_id"] and p["session"] == original["session"])
        self.assertIsInstance(selected_price["price_provenance"], Provenance)
        selected_price["price_provenance"]["unknown_attributes"]["keep"].append("bad")
        self.assertEqual(original["unknown_attributes"]["keep"], [None, -0.0, "original"])

    def test_scalar_subclass_attributes_are_deepcopied_in_lineage(self):
        class TaggedInt(int):
            def __new__(cls, value):
                result = super().__new__(cls, value)
                result.notes = ["original"]
                return result
        prices, factors = multi_inputs()
        for definition in factors.field_meta.values():
            for item in definition["by_key"]:
                item.pop("unknown_attributes")
                item["tag"] = TaggedInt(3)
        before = prices.to_json(), factors.to_json()
        result = adjusted(prices, factors)
        tag = result.field_meta["close"]["by_key"][0]["anchor_factor_provenance"]["tag"]
        self.assertIsInstance(tag, TaggedInt)
        tag.notes.append("bad")
        self.assertEqual(result.field_meta["open"]["by_key"][0]["anchor_factor_provenance"]["tag"].notes,
                         ["original"])
        self.assertTrue(all(item["tag"].notes == ["original"] for item in factors.field_meta["adj_factor"]["by_key"]))
        self.assertEqual((prices.to_json(), factors.to_json()), before)

    def test_reader_cutoffs_choose_old_and_new_price_and_anchor_revisions(self):
        manifest = daily_manifest()
        market = manifest["domains"]["market_daily"]
        market["partitions"] = [{"partition": "prices"}]
        factor = deepcopy(market)
        factor["partitions"] = [{"partition": "factors"}]
        factor["contract"]["fields"] = {"adj_factor": {"dtype": "float64"}}
        manifest["domains"]["cumulative_factors"] = factor
        early, late = SESSIONS[:2]
        known = datetime(2024, 1, 3, 9, tzinfo=timezone.utc)
        revised = datetime(2024, 1, 3, 11, tzinfo=timezone.utc)
        old_price = daily_row(session=early, observed=known, close=10.0, revision="price-old")
        new_price = daily_row(session=early, observed=revised, close=30.0, revision="price-new", sequence=2)
        anchor_price = daily_row(session=late, observed=known, close=5.0, revision="anchor-price")
        factor_rows = []
        for session, value, revision, sequence, observed in (
            (early, 1.0, "factor", 1, known), (late, 2.0, "anchor-old", 1, known),
            (late, 4.0, "anchor-new", 2, revised)):
            row = daily_row(session=session, observed=observed, revision=revision, sequence=sequence)
            row["adj_factor"] = value
            factor_rows.append(row)
        reader = SnapshotQueryReader(MemoryStore(manifest, {"prices": [old_price, new_price, anchor_price],
                                                           "factors": factor_rows}), "s1", cache_bytes=0)
        for hour, values, revisions in ((10, [5.0, 5.0], ("price-old", "anchor-old")),
                                        (12, [7.5, 5.0], ("price-new", "anchor-new")),
                                        (10, [5.0, 5.0], ("price-old", "anchor-old"))):
            cutoff = datetime(2024, 1, 3, hour, tzinfo=timezone.utc)
            price = reader.read(QuerySpec("market_daily", ("close",), ("A",), (early, late),
                                         "operational_pit_v1", {early: cutoff, late: cutoff}))
            factors = reader.read(QuerySpec("cumulative_factors", ("adj_factor",), ("A",), (early, late),
                                           "operational_pit_v1", {early: cutoff, late: cutoff}))
            result = derived.adjust_prices(price, factors, fields=("close",), anchor_session=late)
            self.assertEqual(result.frame.close.tolist(), values)
            meta = result.field_meta["close"]["by_key"][0]
            self.assertEqual((meta["price_provenance"]["revision_id"], meta["anchor_factor_provenance"]["revision_id"]), revisions)


class ScalarSerializationTests(unittest.TestCase):
    def test_exact_scalars_keep_types_negative_zero_and_nonfinite_nulls(self):
        from axiom_data.protocols import _json_safe
        values = [None, True, False, 2**80, "text", 1.25, -0.0, float("nan"), float("inf"), -float("inf")]
        result = _json_safe(values)
        self.assertEqual(result, [None, True, False, 2**80, "text", 1.25, -0.0, None, None, None])
        self.assertEqual([type(v) for v in result[:7]], [type(v) for v in values[:7]])
        self.assertEqual(result[6].hex(), "-0x0.0p+0")
        self.assertEqual(_json_safe(["nan", "NaT", "<NA>"]), ["nan", "NaT", "<NA>"])
        json.dumps(result, allow_nan=False)

    def test_numpy_pandas_decimal_date_and_mapping_keep_original_semantics(self):
        from axiom_data.protocols import _json_safe
        finite64 = np.float64(1.25)
        values = UserDict({1: (np.int64(4), np.bool_(True), finite64, np.float32(2.5),
                              np.float64(np.nan), pd.NA, pd.NaT, Decimal("3.25"), Decimal("NaN"),
                              date(2024, 1, 2), datetime(2024, 1, 2, tzinfo=timezone.utc))})
        result = _json_safe(values)
        self.assertEqual(result, {"1": [4, True, 1.25, 2.5, None, None, None, 3.25, None,
                                       "2024-01-02", "2024-01-02T00:00:00+00:00"]})
        self.assertIs(result["1"][2], finite64)
        json.dumps(result, allow_nan=False)

    def test_scalar_subclasses_and_unknown_objects_are_not_retyped(self):
        from axiom_data.protocols import _json_safe
        class Int(int): pass
        class Text(str): pass
        class Float(float): pass
        class Unknown: pass
        values = [Int(4), Text("text"), Float(1.5), Unknown()]
        result = _json_safe(values)
        self.assertTrue(all(a is b for a, b in zip(values, result)))
        with self.assertRaises(TypeError):
            json.dumps(result, allow_nan=False)
        with self.assertRaises(ValueError):
            _json_safe(np.array([1, 2]))

    def test_public_batch_conversion_detaches_nested_output_containers(self):
        original = DataBatch(pd.DataFrame({"value": [1]}),
                             {"attributes": {"values": [True, np.int64(9), None, -0.0]}},
                             {"unknown": {"nested": [Decimal("2.5")]}})
        result = original.to_json()
        result["field_meta"]["attributes"]["values"].append("bad")
        result["context"]["unknown"]["nested"].append("bad")
        self.assertEqual(original.field_meta["attributes"]["values"], [True, np.int64(9), None, -0.0])
        self.assertEqual(original.context["unknown"]["nested"], [Decimal("2.5")])


if __name__ == "__main__":
    unittest.main()
