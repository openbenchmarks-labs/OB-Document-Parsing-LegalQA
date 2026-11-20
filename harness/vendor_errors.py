"""Vendor failures, and whether one is worth trying again."""
from __future__ import annotations

import random
import re


class VendorError(RuntimeError):
    """A vendor call that failed. Costs this arm, not the run."""

    def __init__(self, message: str, *, status: int | None = None, arm: str = ""):
        super().__init__(message)
        self.status = status
        self.arm = arm


class FatalVendorError(VendorError):
    """Out of credit, unauthorised, or otherwise not worth trying again anywhere.

    Separate from VendorError because the response differs: a failed parse loses
    one arm, but an exhausted account means every subsequent call is going to
    fail too, and the run should stop while the answer is still cheap.
    """


# 429 is here, not in the fatal set: a rate limit is the one 4xx worth waiting
# out. Distinguishing it from an exhausted balance is done on the message,
# because several vendors report both as 429.
RETRY_STATUSES = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
FATAL_STATUSES = frozenset({401, 402, 403})
TRANSIENT_TYPES = ("timeout", "connect", "connection", "protocol", "socket", "ssl")
RATE_LIMIT_MARKERS = ("rate limit", "rate_limit", "too many requests", "slow down")
EXHAUSTED_MARKERS = (
    "insufficient_quota", "insufficient quota", "insufficient credit",
    "insufficient funds", "out of credit", "no credits", "credit balance",
    "credits remaining", "payment required", "billing", "past due",
    "subscription", "invalid api key", "invalid_api_key", "unauthorized",
    "forbidden", "quota exceeded", "plan limit",
)


def status_of(exc: BaseException) -> int | None:
    """The HTTP status behind an exception, whatever wrapper it arrived in."""
    for attr in ("status", "status_code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    value = getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(value, int):
        return value
    found = re.search(r"\b([45]\d\d)\b", str(exc))
    return int(found.group(1)) if found else None


def classify(exc: BaseException) -> tuple[int | None, str]:
    """(status, 'retry' | 'fatal' | 'fail'). What to do about a vendor error."""
    status = status_of(exc)
    text = str(exc).lower()
    name = type(exc).__name__.lower()

    # Checked before the exhausted markers: "rate limit exceeded" contains
    # "exceeded" and must not be read as an empty account.
    rate_limited = any(marker in text for marker in RATE_LIMIT_MARKERS)
    if any(marker in text for marker in EXHAUSTED_MARKERS) and not rate_limited:
        return status, "fatal"
    if status in FATAL_STATUSES:
        return status, "fatal"
    if status in RETRY_STATUSES or rate_limited:
        return status, "retry"
    if status is not None and 400 <= status < 500:
        return status, "fail"          # our request is wrong; trying again cannot help
    if status is not None and status >= 500:
        # Anything else the server says went wrong on its side is weather. The
        # named 5xx above are not the whole list: a Cloudflare 520 is not
        # 500-504 and not below 500 either, so without this it fell through
        # to "fail" and cost an arm its whole parse after a single attempt.
        return status, "retry"
    if any(word in name for word in TRANSIENT_TYPES):
        return status, "retry"
    return status, "fail"


def retry_after(exc: BaseException, attempt: int) -> float:
    """Honour the vendor's own Retry-After, else back off exponentially."""
    headers = getattr(getattr(exc, "response", None), "headers", None) or {}
    raw = headers.get("Retry-After") or headers.get("retry-after")
    if raw:
        try:
            return max(1.0, min(300.0, float(raw)))
        except (TypeError, ValueError):
            pass
    return min(60.0, 2.0 ** attempt) + random.uniform(0, 1.5)
