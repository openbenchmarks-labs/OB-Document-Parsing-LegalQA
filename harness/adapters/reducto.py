"""Reducto through its SDK. Model and formatting come from tools/reducto.yaml."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from .common import dump, exchange

# Reducto documents no cache control, but it caches: an identical request for
# the same file, even a fresh upload, comes back in ~0.2s with the earlier
# job's output, for up to about an hour. The cache is keyed on the request
# settings, `timeout` included (measured 2026-09-29). So a repeat parse sends a
# timeout no other call has used. It is far above any real parse (seconds), so
# it cannot change the result, only the cache key.
FRESH_TIMEOUT_BASE = 3600


def parse(pdf: Path, settings: dict, *, fresh: bool = False) -> tuple[str, dict]:
    from reducto import Reducto

    from ..llm_client import load_environment

    load_environment()
    api_key = os.environ.get("REDUCTO_API_KEY")
    if not api_key:
        raise RuntimeError("REDUCTO_API_KEY is required for reducto")

    model, formatting = settings["model"], settings["formatting"]
    client = Reducto(api_key=api_key, timeout=900)
    with pdf.open("rb") as handle:
        uploaded = client.upload(file=handle)
    request: dict = {"model": model}
    if fresh:
        import secrets

        request["timeout"] = FRESH_TIMEOUT_BASE + secrets.randbelow(1_000_000)
    result = client.parse.run(input=uploaded.file_id, settings=request,
                              formatting=formatting)

    payload = result.model_dump() if hasattr(result, "model_dump") else {}
    chunks = ((payload.get("result") or {}).get("chunks")) or []
    markdown = "\n\n".join(str(chunk.get("content") or "") for chunk in chunks)
    # Billing lives under `usage`, not `document_properties`, which comes back
    # empty on r-1.
    usage = payload.get("usage") or {}
    meta = {
        "model": model,
        "formatting": formatting,
        "file_id": uploaded.file_id,
        "job_id": payload.get("job_id", ""),
        "n_chunks": len(chunks),
        "n_pages": usage.get("num_pages")
                   or (payload.get("document_properties") or {}).get("num_pages", 0),
        "credits": usage.get("credits", 0),
        "credit_breakdown": usage.get("credit_breakdown"),
        "duration_s": payload.get("duration", 0),
        "cache_bust_timeout": request.get("timeout"),
        "parsed_at": datetime.now(timezone.utc).isoformat(),
    }
    meta["io"] = exchange(
        {"sdk": "reducto", "calls": [
            {"method": "upload", "file": pdf.name},
            {"method": "parse.run", "input": uploaded.file_id,
             "settings": request, "formatting": formatting},
        ]},
        {"upload": dump(uploaded), "parse.run": payload},
    )
    return markdown, meta
