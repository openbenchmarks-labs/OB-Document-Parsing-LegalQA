"""Helpers every tool adapter uses to record what it sent and got back."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Anything that could carry a key. Vendor I/O is retained with local run
# artifacts, so a header logged verbatim would leak a credential to any copy.
SECRET_HEADERS = {"x-api-key", "authorization", "api-key", "token", "cookie",
                  "unstructured-api-key"}


def redact(headers: dict) -> dict:
    return {key: ("<redacted>" if key.lower() in SECRET_HEADERS else value)
            for key, value in headers.items()}


def dump(obj) -> Any:
    """Whatever a vendor handed back, as plain JSON-able data."""
    for attr in ("model_dump", "dict", "to_dict"):
        method = getattr(obj, attr, None)
        if callable(method):
            try:
                return method()
            except Exception:  # noqa: BLE001
                pass
    try:
        return json.loads(json.dumps(obj, default=str))
    except Exception:  # noqa: BLE001
        return {"repr": repr(obj)[:20000]}


def exchange(request: Any, response: Any) -> dict:
    """One vendor exchange, recorded verbatim for the raw log.

    The PDF itself is referenced by name and hash rather than embedded: it is
    already in the published snapshot, and inlining it would multiply the
    archive by the number of arms.
    """
    return {"request": request, "response": response}


def page_count(pdf: Path) -> int:
    """Pages in the file, for the trace. The frontier arms bill per token, so
    unlike the vendors this is reporting rather than an input to the bill."""
    from pypdf import PdfReader

    return len(PdfReader(str(pdf)).pages)
