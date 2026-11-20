"""Pulse over HTTP. Model and refine options come from tools/pulse.yaml."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from ..vendor_errors import VendorError
from .common import exchange, redact

URL = "https://api.runpulse.com/extract"


def parse(pdf: Path, settings: dict) -> tuple[str, dict]:
    import httpx

    from ..llm_client import load_environment

    load_environment()
    api_key = os.environ.get("PULSE_API_KEY")
    if not api_key:
        raise RuntimeError("PULSE_API_KEY is required for pulse")

    model, refine = settings["model"], settings["refine_options"]
    data = {"model": model, "refine_options": json.dumps(refine)}
    headers = {"x-api-key": api_key}
    with pdf.open("rb") as handle:
        response = httpx.post(
            URL,
            headers=headers,
            data=data,
            files={"file": (pdf.name, handle, "application/pdf")},
            timeout=1800,
        )
    if response.status_code >= 400:
        raise VendorError(f"pulse {response.status_code}: {response.text[:400]}",
                          status=response.status_code, arm="pulse")
    payload = response.json()

    markdown = payload.get("markdown") or payload.get("text") or ""
    if not markdown and payload.get("is_url") and payload.get("url"):
        # Past some size Pulse stops inlining the result and hands back a link
        # to it instead. Not following it read as "the vendor produced nothing",
        # which on a scanned contract looks exactly like a parser that failed at
        # the task rather than a client that stopped one call short.
        fetched = httpx.get(payload["url"], timeout=1800)
        if fetched.status_code >= 400:
            raise VendorError(f"pulse result {fetched.status_code}: {fetched.text[:400]}",
                              status=fetched.status_code, arm="pulse")
        try:
            body = fetched.json()
        except ValueError:
            body = {}
        markdown = (body.get("markdown") or body.get("text") or "") if isinstance(body, dict) else ""
        if not markdown:
            markdown = fetched.text
        payload = {**payload, **(body if isinstance(body, dict) else {})}
    if not markdown:
        raise RuntimeError(f"pulse returned no markdown; keys={sorted(payload)}")

    meta = {
        "model": model,
        "refine_options": refine,
        "extraction_id": payload.get("extraction_id", ""),
        "n_pages": payload.get("page_count", 0),
        "credits": payload.get("credits_used", 0),
        "parsed_at": datetime.now(timezone.utc).isoformat(),
    }
    meta["io"] = exchange(
        {"method": "POST", "url": URL, "headers": redact(headers),
         "data": data, "files": {"file": pdf.name}},
        {"status": response.status_code, "headers": dict(response.headers),
         "body": payload},
    )
    return markdown, meta
