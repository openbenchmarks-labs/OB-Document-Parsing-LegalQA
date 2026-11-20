"""Mistral OCR over HTTP. The model comes from tools/mistral.yaml."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from ..vendor_errors import VendorError
from .common import exchange, redact

URL = "https://api.mistral.ai/v1/ocr"


def parse(pdf: Path, settings: dict) -> tuple[str, dict]:
    """Mistral OCR, one synchronous call for the whole contract.

    The PDF goes inline as a base64 data URI rather than through the files API,
    which is what the endpoint documents for a document you do not intend to
    keep, and it means the arm has no upload step to fail at. Our largest
    contract is under a megabyte, so there is nothing here that needs chunking.

    Markdown comes back per page and is joined in order. Mistral does emit
    `~~struck~~`, which is why the arm is worth running -- but it emits far less
    of it than the source carries, so expect it to place well above the blind
    floor and well below a parser built for tracked changes.
    """
    import base64

    import httpx

    from ..llm_client import load_environment

    load_environment()
    api_key = os.environ.get("MISTRAL_API_KEY")
    if not api_key:
        raise RuntimeError("MISTRAL_API_KEY is required for mistral")

    model = settings["model"]
    body = {
        "model": model,
        "document": {
            "type": "document_url",
            "document_url": "data:application/pdf;base64,"
                            + base64.b64encode(pdf.read_bytes()).decode("ascii"),
        },
        "include_image_base64": False,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    response = httpx.post(URL, headers=headers, json=body, timeout=1800)
    if response.status_code >= 400:
        raise VendorError(f"mistral-ocr {response.status_code}: {response.text[:400]}",
                          status=response.status_code, arm="mistral-ocr")
    payload = response.json()

    pages = payload.get("pages") or []
    markdown = "\n\n".join(page.get("markdown") or "" for page in pages).strip()
    if not markdown:
        raise RuntimeError(f"mistral returned no markdown; keys={sorted(payload)}")

    usage = payload.get("usage_info") or {}
    meta = {
        "model": payload.get("model") or model,
        "n_pages": usage.get("pages_processed") or len(pages),
        "parsed_at": datetime.now(timezone.utc).isoformat(),
    }
    # The document is echoed back inside the request, and it is a megabyte of
    # base64. Record what was sent without re-recording the contract itself.
    meta["io"] = exchange(
        {"method": "POST", "url": URL, "headers": redact(headers),
         "body": {**body, "document": {"type": "document_url",
                                       "document_url": f"<base64 {pdf.name}>"}}},
        {"status": response.status_code, "headers": dict(response.headers),
         "body": {**payload, "pages": f"<{len(pages)} pages of markdown>"}},
    )
    return markdown, meta
