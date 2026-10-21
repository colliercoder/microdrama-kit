"""Tiny urllib wrapper with one safety property: paid calls can be switched off.

Every request that can spend money goes through `request(..., paid=True)`. When the
process is in dry-run mode, a paid request raises instead of leaving the machine.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

_PAID_ALLOWED = True


class PaidCallBlocked(RuntimeError):
    pass


class HttpError(RuntimeError):
    def __init__(self, status: int, body: str, url: str):
        super().__init__(f"HTTP {status} from {url}: {body[:600]}")
        self.status = status
        self.body = body
        self.url = url


def block_paid_calls() -> None:
    global _PAID_ALLOWED
    _PAID_ALLOWED = False


def paid_calls_allowed() -> bool:
    return _PAID_ALLOWED


def request(method: str, url: str, *, headers: dict | None = None, body: Any = None,
            paid: bool, timeout: float = 300, retries: int = 3, raw: bool = False) -> Any:
    if paid and not _PAID_ALLOWED:
        raise PaidCallBlocked(f"dry run: refusing paid call {method} {url}")
    data = None
    hdrs = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode()
        hdrs.setdefault("Content-Type", "application/json")
    hdrs.setdefault("User-Agent", "microdrama-kit/0.1")
    attempt = 0
    while True:
        attempt += 1
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = resp.read()
                return payload if raw else json.loads(payload.decode() or "null")
        except urllib.error.HTTPError as e:
            text = e.read().decode(errors="replace")
            # Retry only rate limits and server errors. A 4xx is a real answer.
            if (e.code == 429 or e.code >= 500) and attempt <= retries:
                time.sleep(min(30, 4 * attempt))
                continue
            raise HttpError(e.code, text, url) from None
        except urllib.error.URLError:
            # A dropped connection on a paid call may still have been billed upstream,
            # so only free calls are retried blindly.
            if not paid and attempt <= retries:
                time.sleep(min(30, 4 * attempt))
                continue
            raise
