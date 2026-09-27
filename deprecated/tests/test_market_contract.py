from __future__ import annotations

import copy
import hashlib
import json
import unittest

from axiom_data.contracts import MARKET_CONTRACT_VERSIONS, load_contract
from axiom_data.domains import (
    MARKET_DOMAINS,
    MarketContractError,
    market_contracts,
    market_daily_observation_state,
    security_identity_state,
    validate_market_daily_rows,
    validate_security_master_rows,
    validate_trading_calendar_rows,
)


EXPECTED_V1_DIGESTS = {
    "trading_calendar.v1": "0e593f8f9651be8acfa253d3bde3190a0187fa63edd8559387485f0cb71ab8c2",
    "security_master.v1": "c970fdc656ff512e053391bb52bd784f3ee8b65bdc6195442d14da7d39a2c223",
    "market_daily.v1": "14630c5e991e4016cbcf60f2e2fb187cfdd99bc7fb75cdc81df6f232ad4704d7",
}


def normalized_digest(contract: dict[str, object]) -> str:
    payload = json.dumps(
        contract,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def calendar_rows() -> list[dict[str, object]]:
    return [
        {
            "exchange": "SSE",
            "session": "2026-01-02",
            "is_open": True,
            "previous_open_session": None,
        },
        {
            "exchange": "SZSE",
            "session": "2026-01-02",
            "is_open": True,
            "previous_open_session": None,
        },
        {
            "exchange": "SSE",
            "session": "2026-01-03",
            "is_open": False,
            "previous_open_session": "2026-01-02",
        },
        {
            "exchange": "SZSE",
            "session": "2026-01-03",
            "is_open": False,
            "previous_open_session": "2026-01-02",
        },
        {
            "exchange": "SSE",
            "session": "2026-01-04",
            "is_open": False,
            "previous_open_session": "2026-01-02",
        },
        {
            "exchange": "SZSE",
            "session": "2026-01-04",
            "is_open": False,
            "previous_open_session": "2026-01-02",
        },
        {
            "exchange": "SSE",
            "session": "2026-01-05",
            "is_open": True,
            "previous_open_session": "2026-01-02",
        },
        {
            "exchange": "SZSE",
            "session": "2026-01-05",
            "is_open": True,
            "previous_open_session": "2026-01-02",
        },
    ]


def security_row() -> dict[str, object]:
    return {
        "symbol": "000001.SZ",
        "exchange": "SZSE",
        "list_session": "1991-04-03",
        "delist_session": "2026-01-05",
        "status": "source-current-listed",
    }


def normal_market_row(session: str = "2026-01-02") -> dict[str, object]:
    return {
        "session": session,
        "symbol": "000001.SZ",
        "open": 10.0,
        "high": 11.0,
        "low": 9.0,
        "close": 10.5,
        "pre_close": 8.5,
        "volume_shares": 1_000,
        "amount_cny": 10_200.0,
        "adj_factor": 1.25,
        "up_limit": 11.0,
        "down_limit": 9.0,
        "is_suspended": False,
        "turnover_rate": 0.10,
        "total_market_cap_cny": 100_000_000.0,
        "circulating_market_cap_cny": 80_000_000.0,
    }


def suspended_market_row() -> dict[str, object]:
    row = normal_market_row("2026-01-05")
    row.update(
        {
            "open": None,
            "high": None,
            "low": None,
            "close": None,
            "pre_close": 10.5,
            "volume_shares": 0,
            "amount_cny": 0.0,
            "is_suspended": True,
            "turnover_rate": 0.0,
        }
    )
    return row


class MarketContractTest(unittest.TestCase):
    def test_market_contract_identity_and_tables_are_exact(self) -> None:
        contracts = market_contracts()

        self.assertEqual(tuple(contracts), MARKET_DOMAINS)
        self.assertEqual(
            tuple(contract["contract_version"] for contract in contracts.values()),
            MARKET_CONTRACT_VERSIONS,
        )
        self.assertEqual(contracts["trading_calendar"]["primary_key"], ["exchange", "session"])
        self.assertEqual(contracts["security_master"]["primary_key"], ["symbol"])
        self.assertEqual(contracts["market_daily"]["primary_key"], ["session", "symbol"])

    def test_trading_calendar_keeps_closed_dates_and_nearest_open_predecessor(self) -> None:
        rows = calendar_rows()
        validate_trading_calendar_rows(rows)

        self.assertFalse(rows[2]["is_open"])
        self.assertIsNone(rows[0]["previous_open_session"])
        self.assertEqual(rows[-2]["previous_open_session"], "2026-01-02")

        invalid = copy.deepcopy(rows)
        invalid[-2]["previous_open_session"] = "2026-01-01"
        with self.assertRaises(MarketContractError):
            validate_trading_calendar_rows(invalid)

    def test_trading_calendar_rejects_any_gap_inside_each_exchange_coverage(self) -> None:
        rows = calendar_rows()
        for missing_session in ("2026-01-03", "2026-01-04"):
            incomplete = [
                row
                for row in rows
                if not (
                    row["exchange"] == "SSE" and row["session"] == missing_session
                )
            ]
            with self.subTest(missing_session=missing_session):
                with self.assertRaises(MarketContractError):
                    validate_trading_calendar_rows(incomplete)

    def test_unhashable_primary_key_is_a_market_contract_error(self) -> None:
        row = security_row()
        row["symbol"] = ["000001.SZ"]

        with self.assertRaises(MarketContractError):
            validate_security_master_rows([row])

    def test_mixed_or_invalid_sort_key_is_a_market_contract_error(self) -> None:
        rows = calendar_rows()
        rows[-1]["session"] = 20260105

        with self.assertRaises(MarketContractError):
            validate_trading_calendar_rows(rows)

    def test_calendar_contract_rejects_supplier_exchange_and_missing_row_inference(self) -> None:
        contract = market_contracts()["trading_calendar"]
        self.assertEqual(
            contract["semantics"]["exchange"]["canonical_values"],
            ["SSE", "SZSE"],
        )
        self.assertEqual(
            contract["semantics"]["is_open"]["missing_row"],
            "unknown; absence must not be interpreted as closed",
        )

        invalid = calendar_rows()
        invalid[0]["exchange"] = "XSHG"
        with self.assertRaises(MarketContractError):
            validate_trading_calendar_rows(invalid)

    def test_security_symbol_exchange_and_half_open_identity_bounds(self) -> None:
        row = security_row()
        validate_security_master_rows([row])
        self.assertEqual(security_identity_state(row, "1991-04-02"), "not_yet_listed")
        self.assertEqual(security_identity_state(row, "1991-04-03"), "within_identity_interval")
        self.assertEqual(security_identity_state(row, "2026-01-04"), "within_identity_interval")
        self.assertEqual(security_identity_state(row, "2026-01-05"), "delisted")

        invalid = dict(row, exchange="SSE")
        with self.assertRaises(MarketContractError):
            validate_security_master_rows([invalid])

    def test_security_status_is_not_pit_eligibility_or_history_clipping(self) -> None:
        semantics = market_contracts()["security_master"]["semantics"]
        self.assertEqual(semantics["identity_effective_interval"]["notation"], "[list_session, delist_session)")
        self.assertIn("not historical PIT eligibility", semantics["status"]["not_a_promise"])
        self.assertIn(
            "do not authorize initial market contract to crop",
            semantics["identity_effective_interval"]["market_history_clipping"],
        )

    def test_pre_close_can_differ_from_previous_row_close(self) -> None:
        first = normal_market_row("2026-01-02")
        second = normal_market_row("2026-01-05")
        second["pre_close"] = 9.75

        self.assertNotEqual(second["pre_close"], first["close"])
        validate_market_daily_rows([first, second])

    def test_market_row_states_keep_normal_suspended_and_missing_distinct(self) -> None:
        normal = normal_market_row()
        suspended = suspended_market_row()
        validate_market_daily_rows([normal, suspended])

        self.assertEqual(market_daily_observation_state(normal), "normal_trading")
        self.assertEqual(market_daily_observation_state(suspended), "confirmed_suspended")
        self.assertEqual(market_daily_observation_state(None), "unknown")
        self.assertEqual(
            market_daily_observation_state(None, identity_state="not_yet_listed"),
            "not_yet_listed",
        )
        self.assertEqual(
            market_daily_observation_state(None, identity_state="delisted"),
            "delisted",
        )

    def test_market_daily_rejects_illegal_normal_and_suspended_null_combinations(self) -> None:
        invalid_rows = []
        normal_with_null = normal_market_row()
        normal_with_null["close"] = None
        invalid_rows.append(normal_with_null)
        suspended_with_price = suspended_market_row()
        suspended_with_price["close"] = 10.5
        invalid_rows.append(suspended_with_price)
        suspended_with_unknown_volume = suspended_market_row()
        suspended_with_unknown_volume["volume_shares"] = None
        invalid_rows.append(suspended_with_unknown_volume)

        for row in invalid_rows:
            with self.subTest(row=row), self.assertRaises(MarketContractError):
                validate_market_daily_rows([row])

    def test_market_daily_key_economic_semantics_are_explicit(self) -> None:
        contract = market_contracts()["market_daily"]
        fields = {field["name"]: field for field in contract["fields"]}
        semantics = contract["semantics"]

        self.assertFalse(semantics["price_basis"]["adjusted_prices_in_this_contract"])
        self.assertIn("unadjusted", semantics["price_basis"]["open_high_low_close"])
        self.assertEqual(
            semantics["adj_factor"]["ratio_formula"],
            "price_at_t_on_anchor_a_basis = unadjusted_price_at_t * adj_factor_at_t / adj_factor_at_a",
        )
        self.assertEqual(
            semantics["turnover_rate"]["formula"],
            "100 * volume_shares / circulating_shares",
        )
        self.assertEqual(fields["turnover_rate"]["unit"], "percent of all circulating shares")
        self.assertIn("not free-float", semantics["circulating_market_cap_cny"]["meaning"])
        self.assertNotIn("float_market_cap_cny", fields)

    def test_market_daily_field_order_is_frozen(self) -> None:
        fields = market_contracts()["market_daily"]["fields"]
        self.assertEqual(
            [field["name"] for field in fields],
            [
                "session",
                "symbol",
                "open",
                "high",
                "low",
                "close",
                "pre_close",
                "volume_shares",
                "amount_cny",
                "adj_factor",
                "up_limit",
                "down_limit",
                "is_suspended",
                "turnover_rate",
                "total_market_cap_cny",
                "circulating_market_cap_cny",
            ],
        )

    def test_contract_loader_has_no_alias_or_unregistered_fallback(self) -> None:
        for alias in (
            "current",
            "latest",
            "live",
            "current.json",
            "LATEST.JSON",
            "market_daily",
            "market_daily.v99",
            "market_daily.current",
        ):
            with self.subTest(alias=alias), self.assertRaises(ValueError):
                load_contract(alias)

    def test_v1_normalized_contract_content_is_golden(self) -> None:
        actual = {
            version: normalized_digest(load_contract(version))
            for version in EXPECTED_V1_DIGESTS
        }
        self.assertEqual(actual, EXPECTED_V1_DIGESTS)


if __name__ == "__main__":
    unittest.main()
