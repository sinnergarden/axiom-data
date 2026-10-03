"""Credential-safe supplier failure payload for durable Raw records."""

import json


def safe_failure(exc: Exception, phase: str) -> bytes:
    # Supplier exceptions may contain credentials, URLs or request bodies.
    from .live_client import SourceResponseError
    detail = {"phase": phase, "error_type": type(exc).__name__}
    if isinstance(exc, SourceResponseError):
        detail["error_code"] = exc.code
    return json.dumps(detail, separators=(",", ":")).encode()
