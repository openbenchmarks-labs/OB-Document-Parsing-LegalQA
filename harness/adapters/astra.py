"""GPT-6 Astra asked to parse the PDF. Model and prompt come from tools/astra.yaml."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from ..vendor_errors import VendorError
from .common import dump, exchange, page_count


def parse(pdf: Path, settings: dict) -> tuple[str, dict]:
    """GPT-6 Astra, one Responses call with the PDF attached.

    `input_file` takes the contract whole and lets OpenAI rasterise it, which is
    the point: the arm is measuring the model's reading of a document, not our
    choice of DPI.

    The native API is pinned rather than inherited. Foundry deployments carry
    their own model list and their own per-token rates, and an arm whose bill
    depends on which endpoint happened to be configured is not a number worth
    publishing -- so this asks for OpenAI directly and says so if it cannot.
    """
    import base64

    from ..llm_client import PROVIDER_ENV, load_environment

    load_environment()
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError(f"OPENAI_API_KEY is required for astra; "
                           f"set {PROVIDER_ENV}=openai to pin the native API")

    from openai import OpenAI

    model, prompt = settings["model"], settings["prompt"]
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"], timeout=1800, max_retries=0)
    data_uri = ("data:application/pdf;base64,"
                + base64.b64encode(pdf.read_bytes()).decode("ascii"))
    content = [
        {"type": "input_text", "text": prompt},
        {"type": "input_file", "filename": pdf.name, "file_data": data_uri},
    ]
    try:
        response = client.responses.create(
            model=model, input=[{"role": "user", "content": content}],
        )
    except Exception as exc:  # noqa: BLE001
        status = getattr(exc, "status_code", None)
        if status:
            raise VendorError(f"astra {status}: {str(exc)[:400]}",
                              status=status, arm="astra") from exc
        raise

    markdown = (getattr(response, "output_text", "") or "").strip()
    if not markdown:
        raise RuntimeError("astra returned no text")
    # A truncated parse is the dangerous failure on this board: it returns
    # plausible markdown for the first N pages and scores as a bad parser rather
    # than as a broken call. Fail loudly instead.
    if getattr(response, "status", None) == "incomplete":
        reason = getattr(getattr(response, "incomplete_details", None), "reason", "")
        raise RuntimeError(f"astra parse incomplete ({reason}); "
                           f"{len(markdown)} chars returned for {pdf.name}")

    from ..llm_client import usage_of

    usage = usage_of(response)
    meta = {
        "model": getattr(response, "model", None) or model,
        "n_pages": page_count(pdf),
        "usage": usage,
        "parsed_at": datetime.now(timezone.utc).isoformat(),
    }
    # The request carries a megabyte of base64. Record its shape, not the file.
    meta["io"] = exchange(
        {"sdk": "openai", "method": "responses.create", "model": model,
         "input": [{"role": "user", "content": [
             {"type": "input_text", "text": prompt},
             {"type": "input_file", "filename": pdf.name,
              "file_data": f"<base64 {pdf.name}>"}]}]},
        {**dump(response), "output": "<markdown returned above>"},
    )
    return markdown, meta
