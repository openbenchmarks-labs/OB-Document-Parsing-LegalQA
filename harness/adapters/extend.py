"""Extend through its SDK. Engine and options come from tools/extend.yaml."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from .common import dump, exchange


def parse(pdf: Path, settings: dict) -> tuple[str, dict]:
    from extend_ai import Extend

    from ..llm_client import load_environment

    load_environment()
    api_key = os.environ.get("EXTEND_API_KEY")
    if not api_key:
        raise RuntimeError("EXTEND_API_KEY is required for extend")

    engine, advanced = settings["engine"], settings["advanced_options"]
    client = Extend(token=api_key, timeout=900)
    with pdf.open("rb") as handle:
        uploaded = client.files.upload(file=handle)
    result = client.parse(file={"id": uploaded.id},
                          config={"engine": engine, "advancedOptions": advanced})

    payload = result.model_dump() if hasattr(result, "model_dump") else {}
    chunks = ((payload.get("output") or {}).get("chunks")) or []
    markdown = "\n\n".join(str(chunk.get("content") or "") for chunk in chunks)
    metrics = payload.get("metrics") or {}
    usage = payload.get("usage") or {}
    meta = {
        "engine": engine,
        "advanced_options": advanced,
        "file_id": getattr(uploaded, "id", ""),
        "job_id": payload.get("id", ""),
        "n_chunks": len(chunks),
        # pageCount has come back 0; usage.pageCount is the fallback, and the
        # credits below are billed per page either way.
        "n_pages": metrics.get("pageCount") or usage.get("pageCount") or 0,
        "duration_s": round(metrics.get("processingTimeMs", 0) / 1000, 2),
        "credits": usage.get("credits", 0),
        "parsed_at": datetime.now(timezone.utc).isoformat(),
    }
    meta["io"] = exchange(
        {"sdk": "extend_ai", "calls": [
            {"method": "files.upload", "file": pdf.name},
            {"method": "parse", "file": {"id": getattr(uploaded, "id", "")},
             "config": {"engine": engine, "advancedOptions": advanced}},
        ]},
        {"files.upload": dump(uploaded), "parse": payload},
    )
    return markdown, meta
