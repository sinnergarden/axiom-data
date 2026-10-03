"""Transport refusal must never masquerade as a successful empty response."""
import io
import json
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from axiom_data.source_failure import safe_failure as _safe_failure
from axiom_data.live_client import SourceResponseError, TushareHttpClient
from axiom_data.protocols import QueryError
from test_local_derived import inputs, transformed


class LiveBoundaryTests(unittest.TestCase):
    def test_explicit_plain_token_file_takes_precedence_over_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "token.txt"
            path.write_text("fixture-file-token\n")
            with patch.dict("os.environ", {"TUSHARE_TOKEN": "fixture-stale-env"}):
                client = TushareHttpClient(token_file=path)
            with patch("axiom_data.live_client.urlopen", return_value=io.BytesIO(json.dumps(
                    {"code": 0, "data": {"fields": [], "items": []}}).encode())) as request:
                client.query("trade_cal")
            self.assertEqual(json.loads(request.call_args.args[0].data)["token"], "fixture-file-token")

    def test_response_clock_does_not_mutate_semantic_query_identity(self):
        price, _ = inputs()
        semantic = price.to_json()
        response = price.to_response()
        self.assertIn("generated_at", response["context"])
        response["context"].pop("generated_at")
        self.assertEqual(response, semantic)
        self.assertEqual(price.to_json(), semantic)

    def test_empty_success_is_distinct_from_refusal_and_secrets_are_not_logged(self):
        client = TushareHttpClient(token="fixture-secret")
        with patch("axiom_data.live_client.urlopen", return_value=io.BytesIO(json.dumps(
                {"code": 0, "data": {"fields": ["close"], "items": []}}).encode())):
            self.assertEqual(client.query("daily"), {"fields": ["close"], "rows": []})
        with patch("axiom_data.live_client.urlopen", return_value=io.BytesIO(json.dumps(
                {"code": 40101, "msg": "fixture-secret"}).encode())):
            with self.assertRaises(SourceResponseError) as caught:
                client.query("daily")
        self.assertEqual(caught.exception.code, 40101)
        logged = _safe_failure(caught.exception, "fetch").decode()
        self.assertIn("40101", logged)
        self.assertNotIn("fixture-secret", logged)
        self.assertEqual(SourceResponseError("fixture-secret").code, "INVALID_RESPONSE")
        with patch("axiom_data.live_client.urlopen", side_effect=HTTPError("url", 403, "fixture-secret", {}, None)):
            with self.assertRaisesRegex(SourceResponseError, "HTTP_403"):
                client.query("daily")

    def test_adjustment_rejects_mixed_hybrid_maps(self):
        prices, factors = inputs()
        for batch in (prices, factors):
            batch.context["query"]["pit_policy"] = "bootstrap_hybrid_v1"
            batch.context["query"]["policy_by_session"] = {
                day: "operational_pit_v1" for day in batch.context["query"]["sessions"]}
        factors.context["query"]["policy_by_session"]["2024-01-02"] = "best_effort_vendor_v1"
        with self.assertRaisesRegex(QueryError, "per-session"):
            transformed(prices, factors)
