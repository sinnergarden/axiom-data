import unittest

from axiom_data.contracts import MARKET_CONTRACT_VERSIONS, load_contract
from axiom_data.domains import MARKET_DOMAINS, market_contracts


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

    def test_market_daily_field_order_and_units_are_explicit(self) -> None:
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
                "float_market_cap_cny",
            ],
        )
        units = {field["name"]: field["unit"] for field in fields}
        types = {field["name"]: field["type"] for field in fields}
        self.assertEqual(units["volume_shares"], "shares")
        self.assertEqual(units["amount_cny"], "CNY")
        self.assertEqual(units["turnover_rate"], "percent")
        self.assertEqual(units["total_market_cap_cny"], "CNY")
        self.assertEqual(types["volume_shares"], "int64")
        self.assertEqual(types["is_suspended"], "bool")

    def test_contract_loader_has_no_current_or_latest_fallback(self) -> None:
        for alias in ("current", "latest", "live", "market_daily.v2"):
            with self.subTest(alias=alias), self.assertRaises(ValueError):
                load_contract(alias)


if __name__ == "__main__":
    unittest.main()
