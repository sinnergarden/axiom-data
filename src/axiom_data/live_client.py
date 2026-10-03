"""Small explicit HTTPS client for Tushare's documented table API.

Protocol: https://tushare.pro/document/1?doc_id=40 . Credentials are loaded only
on construction and are never included in repr, request metadata or errors.
"""
from __future__ import annotations

import csv
import json
import os
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from .protocols import DataError


class SourceResponseError(DataError):
    """A transport or supplier refusal; it is never a successful empty result."""

    def __init__(self, code):
        # A supplier can return arbitrary text in an invalid code field. Only
        # documented numeric codes and our own fixed transport labels escape.
        if not (type(code) is int or (isinstance(code, str) and code in {"TRANSPORT_OR_JSON", "INVALID_RESPONSE", "INVALID_TABLE"})
                or (isinstance(code, str) and code.startswith("HTTP_") and code[5:].isdigit())):
            code = "INVALID_RESPONSE"
        self.code = code
        super().__init__(f"Tushare response failed (code={code}); response text omitted")


class TushareHttpClient:
    """Use an explicit token, environment, or the SDK's local ~/tk.csv setting.

    query performs one bounded source request. Retry/pacing/Raw checkpoints are
    owned by the bulk runner. This client never writes token files or logs.
    """
    def __init__(self, token: str | None = None, *, token_file=None, timeout=30):
        if not token and token_file is None:
            token = os.environ.get("TUSHARE_TOKEN") or os.environ.get("TS_TOKEN")
        if not token:
            path = Path(token_file) if token_file is not None else Path.home() / "tk.csv"
            if path.is_file():
                if path.suffix.lower() == ".csv":
                    with path.open(newline="", encoding="utf-8-sig") as stream:
                        token = next(csv.DictReader(stream), {}).get("token")
                else:
                    token = path.read_text(encoding="utf-8-sig").strip()
        if not isinstance(token, str) or not token.strip():
            raise DataError("Configure TUSHARE_TOKEN, TS_TOKEN, or the SDK local token file")
        self._token = token.strip()
        self.timeout = timeout

    def query(self, endpoint, *, fields="", **params):
        payload = json.dumps({"api_name": endpoint, "token": self._token,
                              "params": params, "fields": fields}).encode()
        request = Request("https://api.tushare.pro", data=payload,
                          headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(request, timeout=self.timeout) as response:
                result = json.loads(response.read())
        except HTTPError as exc:
            raise SourceResponseError(f"HTTP_{exc.code}") from None
        except (URLError, TimeoutError, ValueError):
            raise SourceResponseError("TRANSPORT_OR_JSON") from None
        if not isinstance(result, dict) or result.get("code") != 0:
            code = result.get("code", "INVALID_RESPONSE") if isinstance(result, dict) else "INVALID_RESPONSE"
            raise SourceResponseError(code)
        data = result.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("fields"), list) or not isinstance(data.get("items"), list):
            raise SourceResponseError("INVALID_TABLE")
        return {"fields": data["fields"], "rows": data["items"]}
