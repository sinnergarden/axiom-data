from __future__ import annotations

import inspect
import unittest

from axiom_data import BuildApplication, BuildContractError, BuildRequest, DomainCommitRef


FLOATING_IDENTITIES = (
    "current",
    "latest",
    "live",
    "current.json",
    "latest.json",
    "live.json",
    "mtime",
    "CURRENT",
    "Latest.JSON",
    "MtImE",
)


class Recorder:
    def __init__(self) -> None:
        self.requests: list[BuildRequest] = []

    def __call__(self, request: BuildRequest) -> DomainCommitRef:
        self.requests.append(request)
        return DomainCommitRef(
            domain="market_daily",
            commit_id="market-commit-001",
            contract_version=request.contract_version,
        )


def unchecked_ref(domain: object, commit_id: object, contract_version: object) -> DomainCommitRef:
    """Simulate an executor object that bypassed DomainCommitRef construction."""

    result = object.__new__(DomainCommitRef)
    object.__setattr__(result, "domain", domain)
    object.__setattr__(result, "commit_id", commit_id)
    object.__setattr__(result, "contract_version", contract_version)
    return result


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

    def test_build_freezes_inputs_and_preserves_caller_order(self) -> None:
        recorder = Recorder()
        application = BuildApplication(domain="market_daily", executor=recorder)

        result = application.build(
            "market-commit-000",
            ["raw-002", "raw-001"],
            ["patch-002", "patch-001"],
            "market_daily.v1",
        )

        self.assertEqual(result.commit_id, "market-commit-001")
        self.assertEqual(recorder.requests[0].raw_batch_ids, ("raw-002", "raw-001"))
        self.assertEqual(recorder.requests[0].patch_ids, ("patch-002", "patch-001"))

    def test_floating_identity_is_rejected_in_every_request_position(self) -> None:
        for position in ("parent_commit", "raw_batch_ids", "patch_ids"):
            for identity in FLOATING_IDENTITIES:
                recorder = Recorder()
                application = BuildApplication("market_daily", recorder)
                request = {
                    "parent_commit": "market-commit-000",
                    "raw_batch_ids": ["raw-001"],
                    "patch_ids": [],
                    "contract_version": "market_daily.v1",
                }
                if position == "parent_commit":
                    request[position] = identity
                elif position == "raw_batch_ids":
                    request[position] = [identity]
                else:
                    request[position] = [identity]

                with self.subTest(position=position, identity=identity):
                    with self.assertRaises(BuildContractError):
                        application.build(**request)
                    self.assertEqual(recorder.requests, [])

    def test_executor_result_identity_is_revalidated(self) -> None:
        for identity in FLOATING_IDENTITIES:
            def invalid_result(
                _request: BuildRequest, commit_id: str = identity
            ) -> DomainCommitRef:
                return unchecked_ref("market_daily", commit_id, "market_daily.v1")

            with self.subTest(identity=identity), self.assertRaises(BuildContractError):
                BuildApplication("market_daily", invalid_result).build(
                    None, ["raw-001"], [], "market_daily.v1"
                )

    def test_contract_must_be_exactly_registered_before_executor_runs(self) -> None:
        for contract_version in (
            "market_daily",
            "market_daily.v2",
            "market_daily.current",
            "market_daily.latest",
            "MARKET_DAILY.V1",
            "unknown.v1",
        ):
            recorder = Recorder()
            with self.subTest(contract_version=contract_version):
                with self.assertRaises(BuildContractError):
                    BuildApplication("market_daily", recorder).build(
                        None, ["raw-001"], [], contract_version
                    )
                self.assertEqual(recorder.requests, [])

    def test_registered_contract_must_belong_to_requested_domain(self) -> None:
        recorder = Recorder()
        with self.assertRaises(BuildContractError):
            BuildApplication("market_daily", recorder).build(
                None, ["raw-001"], [], "security_master.v1"
            )
        self.assertEqual(recorder.requests, [])

    def test_genesis_and_incremental_legal_matrix(self) -> None:
        valid = (
            (None, ["raw-001"], []),
            (None, ["raw-001"], ["patch-001"]),
            ("market-commit-000", ["raw-001"], []),
            ("market-commit-000", [], ["patch-001"]),
            ("market-commit-000", ["raw-001"], ["patch-001"]),
        )
        for parent, raw, patches in valid:
            recorder = Recorder()
            with self.subTest(valid=(parent, raw, patches)):
                BuildApplication("market_daily", recorder).build(
                    parent, raw, patches, "market_daily.v1"
                )
                self.assertEqual(len(recorder.requests), 1)

        invalid = (
            (None, [], []),
            (None, [], ["patch-001"]),
            ("market-commit-000", [], []),
        )
        for parent, raw, patches in invalid:
            recorder = Recorder()
            with self.subTest(invalid=(parent, raw, patches)):
                with self.assertRaises(BuildContractError):
                    BuildApplication("market_daily", recorder).build(
                        parent, raw, patches, "market_daily.v1"
                    )
                self.assertEqual(recorder.requests, [])

    def test_malformed_and_duplicate_input_sequences_fail_before_executor(self) -> None:
        invalid_inputs = (
            (None, ["raw-001", "raw-001"], []),
            (None, "raw-001", []),
            ("market-commit-000", [], ["patch-001", "patch-001"]),
        )
        for parent, raw, patches in invalid_inputs:
            recorder = Recorder()
            with self.subTest(inputs=(parent, raw, patches)):
                with self.assertRaises(BuildContractError):
                    BuildApplication("market_daily", recorder).build(
                        parent, raw, patches, "market_daily.v1"
                    )
                self.assertEqual(recorder.requests, [])

    def test_executor_cannot_change_domain_or_contract(self) -> None:
        def wrong_domain(_request: BuildRequest) -> DomainCommitRef:
            return DomainCommitRef("security_master", "commit-001", "security_master.v1")

        def wrong_contract(_request: BuildRequest) -> DomainCommitRef:
            return unchecked_ref("market_daily", "commit-001", "market_daily.v2")

        with self.assertRaises(BuildContractError):
            BuildApplication("market_daily", wrong_domain).build(
                None, ["raw-001"], [], "market_daily.v1"
            )
        with self.assertRaises(BuildContractError):
            BuildApplication("market_daily", wrong_contract).build(
                None, ["raw-001"], [], "market_daily.v1"
            )

    def test_domain_commit_ref_itself_requires_registered_matching_contract(self) -> None:
        with self.assertRaises(BuildContractError):
            DomainCommitRef("market_daily", "commit-001", "market_daily.v2")
        with self.assertRaises(BuildContractError):
            DomainCommitRef("market_daily", "commit-001", "security_master.v1")


if __name__ == "__main__":
    unittest.main()
