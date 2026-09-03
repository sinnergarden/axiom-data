from __future__ import annotations

import inspect
import unittest

from axiom_data import BuildApplication, BuildContractError, BuildRequest, DomainCommitRef


class Recorder:
    def __init__(self) -> None:
        self.request: BuildRequest | None = None

    def __call__(self, request: BuildRequest) -> DomainCommitRef:
        self.request = request
        return DomainCommitRef(
            domain="market_daily",
            commit_id="market-commit-001",
            contract_version=request.contract_version,
        )


class BuildApplicationTest(unittest.TestCase):
    def test_public_build_signature_is_stable(self) -> None:
        self.assertEqual(
            list(inspect.signature(BuildApplication.build).parameters),
            [
                "self",
                "parent_commit",
                "raw_batch_ids",
                "patch_ids",
                "contract_version",
            ],
        )

    def test_build_freezes_inputs_and_preserves_order(self) -> None:
        recorder = Recorder()
        application = BuildApplication(domain="market_daily", executor=recorder)

        result = application.build(
            "market-commit-000",
            ["raw-002", "raw-001"],
            ["patch-001"],
            "market_daily.v1",
        )

        self.assertEqual(result.commit_id, "market-commit-001")
        self.assertEqual(recorder.request.raw_batch_ids, ("raw-002", "raw-001"))
        self.assertEqual(recorder.request.patch_ids, ("patch-001",))

    def test_dynamic_aliases_are_rejected(self) -> None:
        application = BuildApplication(domain="market_daily", executor=Recorder())

        for alias in ("current", "latest", "live"):
            with self.subTest(alias=alias), self.assertRaises(BuildContractError):
                application.build(alias, ["raw-001"], [], "market_daily.v1")

    def test_empty_and_duplicate_inputs_are_rejected(self) -> None:
        application = BuildApplication(domain="market_daily", executor=Recorder())

        with self.assertRaises(BuildContractError):
            application.build(None, [], [], "market_daily.v1")
        with self.assertRaises(BuildContractError):
            application.build(None, ["raw-001", "raw-001"], [], "market_daily.v1")
        with self.assertRaises(BuildContractError):
            application.build(None, "raw-001", [], "market_daily.v1")

    def test_executor_cannot_change_domain_or_contract(self) -> None:
        def wrong_domain(_request: BuildRequest) -> DomainCommitRef:
            return DomainCommitRef("financial", "commit-001", "market_daily.v1")

        def wrong_contract(_request: BuildRequest) -> DomainCommitRef:
            return DomainCommitRef("market_daily", "commit-001", "market_daily.v2")

        with self.assertRaises(BuildContractError):
            BuildApplication("market_daily", wrong_domain).build(
                None, ["raw-001"], [], "market_daily.v1"
            )
        with self.assertRaises(BuildContractError):
            BuildApplication("market_daily", wrong_contract).build(
                None, ["raw-001"], [], "market_daily.v1"
            )

    def test_contract_must_belong_to_build_domain(self) -> None:
        with self.assertRaises(BuildContractError):
            BuildApplication("market_daily", Recorder()).build(
                None, ["raw-001"], [], "security_master.v1"
            )


if __name__ == "__main__":
    unittest.main()
