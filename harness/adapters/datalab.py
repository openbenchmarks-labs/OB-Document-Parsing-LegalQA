"""Datalab over HTTP: submit, then poll. Endpoint and mode come from tools/datalab.yaml."""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path

from ..vendor_errors import VendorError, classify, retry_after
from .common import exchange, redact

POLL_SECONDS = 5
MAX_WAIT = 1800
# Consecutive failed polls (429, 5xx, a non-JSON body, a dropped connection)
# before the job is given up on. Each one backs off first.
POLL_FAILURES = 5


def parse(pdf: Path, settings: dict, *, fresh: bool = False) -> tuple[str, dict]:
    """Both Datalab setups: the general convert endpoint, which takes a mode,
    and the track-changes endpoint, which does not."""
    data = {"output_format": "markdown"}
    if "mode" in settings:
        data["mode"] = settings["mode"]
    if fresh:
        data["skip_cache"] = "true"
    label = "datalab" if "mode" in settings else "datalab-tc"
    markdown, meta = _call(pdf, settings["url"], data, label)
    if "mode" in settings:
        meta["mode"] = settings["mode"]
    meta["skip_cache"] = fresh
    return markdown, meta


def _call(pdf: Path, url: str, data: dict, label: str) -> tuple[str, dict]:
    """Submit to a Datalab endpoint and poll until the conversion completes."""
    import httpx

    from ..llm_client import load_environment

    load_environment()
    api_key = os.environ.get("DATALAB_API_KEY")
    if not api_key:
        raise RuntimeError("DATALAB_API_KEY is required for datalab")

    headers = {"X-API-Key": api_key}
    with pdf.open("rb") as handle:
        response = httpx.post(
            url,
            headers=headers,
            data=data,
            files={"file": (pdf.name, handle, "application/pdf")},
            timeout=300,
        )
    if response.status_code >= 400:
        raise VendorError(f"{label} {response.status_code}: {response.text[:400]}",
                          status=response.status_code, arm=label)
    submitted = response.json()
    check_url = submitted.get("request_check_url")
    if not check_url:
        raise RuntimeError(f"{label} gave no check url: {str(submitted)[:300]}")

    deadline = time.time() + MAX_WAIT
    polls: list[dict] = []
    failed_in_a_row = 0
    wait = POLL_SECONDS
    while True:
        time.sleep(wait)
        wait = POLL_SECONDS
        # A poll that fails is not a failed parse: the job is still running and
        # already billed, so the answer is to wait and look again. Raising here
        # would send call_vendor round to resubmit, and pay, for the same job.
        try:
            poll = httpx.get(check_url, headers=headers, timeout=120)
            if poll.status_code >= 400:
                raise VendorError(f"{label} poll {poll.status_code}: {poll.text[:200]}",
                                  status=poll.status_code, arm=label)
            polled = poll.json()
        except Exception as exc:  # noqa: BLE001 - classified immediately below
            status, verdict = classify(exc)
            failed_in_a_row += 1
            polls.append({"n": len(polls) + 1, "error": f"{type(exc).__name__}: {str(exc)[:200]}",
                          "http_status": status})
            if verdict == "fatal":
                raise
            if failed_in_a_row >= POLL_FAILURES or time.time() > deadline:
                raise VendorError(f"{label} poll failed {failed_in_a_row} times in a row: {exc}",
                                  status=status, arm=label) from exc
            wait = retry_after(exc, failed_in_a_row)
            continue
        failed_in_a_row = 0
        # Every poll is kept, but only the last one carries the markdown, so
        # the earlier ones are recorded as status alone rather than in full.
        polls.append({"n": len(polls) + 1, "status": polled.get("status")})
        status = polled.get("status")
        if status == "complete":
            break
        if status == "failed" or polled.get("success") is False:
            raise VendorError(f"{label} failed: {polled.get('error')}", arm=label)
        if time.time() > deadline:
            raise VendorError(f"{label} timed out after {MAX_WAIT}s", arm=label)

    markdown = polled.get("markdown") or ""
    if not markdown:
        raise RuntimeError(f"{label} returned no markdown; keys={sorted(polled)}")

    meta = {
        "request_id": submitted.get("request_id", ""),
        "n_pages": polled.get("page_count", 0),
        "quality_score": polled.get("parse_quality_score"),
        "cost_cents": polled.get("cost_breakdown"),
        "parsed_at": datetime.now(timezone.utc).isoformat(),
    }
    meta["io"] = exchange(
        {"submit": {"method": "POST", "url": url, "headers": redact(headers),
                    "data": data, "files": {"file": pdf.name}},
         "poll": {"method": "GET", "url": check_url, "headers": redact(headers),
                  "every_seconds": POLL_SECONDS}},
        {"submit": {"status": response.status_code, "body": submitted},
         "polls": polls, "final": polled},
    )
    return markdown, meta
