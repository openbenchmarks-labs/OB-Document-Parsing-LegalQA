"""LlamaParse through the LlamaCloud SDK. Tier and version come from tools/llamaparse.yaml."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from .common import dump, exchange


def parse(pdf: Path, settings: dict, *, fresh: bool = False) -> tuple[str, dict]:
    from llama_cloud import LlamaCloud

    from ..llm_client import load_environment

    load_environment()
    api_key = os.environ.get("LLAMA_API_KEY") or os.environ.get("LLAMA_CLOUD_API_KEY")
    if not api_key:
        raise RuntimeError("LLAMA_API_KEY is required for llamaparse")

    tier, version = settings["tier"], settings["version"]
    client = LlamaCloud(api_key=api_key, timeout=900)
    with pdf.open("rb") as handle:
        uploaded = client.files.create(file=handle, purpose="parse")
    # LlamaCloud keeps a parse for 48 hours and hands the same job back to an
    # identical request, even for a fresh upload. A repeat parse has to opt out,
    # or repeat 3 is repeat 2 again. `disable_cache` both skips the cache and
    # keeps this result out of it.
    extra = {"disable_cache": True} if fresh else {}
    result = client.parsing.parse(
        file_id=uploaded.id,
        tier=tier,
        version=version,
        expand=["markdown"],
        **extra,
    )
    pages = getattr(getattr(result, "markdown", None), "pages", None) or []
    markdown = "\n\n".join((getattr(p, "markdown", "") or "") for p in pages)
    meta = {
        "tier": tier,
        "version": version,
        "file_id": uploaded.id,
        "job_id": getattr(result, "id", ""),
        "n_pages": len(pages),
        "parsed_at": datetime.now(timezone.utc).isoformat(),
    }
    meta["io"] = exchange(
        {"sdk": "llama_cloud", "calls": [
            {"method": "files.create", "purpose": "parse", "file": pdf.name},
            {"method": "parsing.parse", "file_id": uploaded.id, "tier": tier,
             "version": version, "expand": ["markdown"], **extra},
        ]},
        {"files.create": dump(uploaded), "parsing.parse": dump(result)},
    )
    return markdown, meta
