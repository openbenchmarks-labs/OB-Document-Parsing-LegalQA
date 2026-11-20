"""Unstructured Parse API over HTTP: submit, then poll. Settings come from tools/unstructured.yaml."""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path

from ..vendor_errors import VendorError, classify, retry_after
from .common import exchange, redact

HOST = "https://transform.unstructured.io"
JOBS_URL = f"{HOST}/api/v2/jobs"
# Submitted with `Prefer: wait=0` so the request returns a job at once and the
# call polls, the same shape as Datalab, rather than holding a connection open
# for up to fifteen minutes.
POLL_SECONDS = 5
MAX_WAIT = 1800
POLL_FAILURES = 5


def parse(pdf: Path, settings: dict) -> tuple[str, dict]:
    """Unstructured Transform Parse API: submit, then poll the job to completion."""
    import httpx

    from ..llm_client import load_environment

    load_environment()
    api_key = os.environ.get("UNSTRUCTURED_API_KEY")
    if not api_key:
        raise RuntimeError("UNSTRUCTURED_API_KEY is required for unstructured")

    url, profile, output = settings["url"], settings["profile"], settings["output"]
    label = "unstructured"
    headers = {"unstructured-api-key": api_key}
    data = {"profile": profile, "output": output}
    with pdf.open("rb") as handle:
        response = httpx.post(
            url,
            headers={**headers, "Prefer": "wait=0"},
            data=data,
            files={"input": (pdf.name, handle, "application/pdf")},
            timeout=300,
        )
    if response.status_code >= 400:
        raise VendorError(f"{label} {response.status_code}: {response.text[:400]}",
                          status=response.status_code, arm=label)
    submitted = response.json()
    job_id = submitted.get("id", "")

    polls: list[dict] = []
    poll_url = ""
    if response.status_code == 200:
        # The server may finish inside the submit even when asked not to wait;
        # a 200 is then the ParseResult itself.
        result = submitted
    else:
        poll_url = (submitted.get("poll_url") or response.headers.get("Location")
                    or (f"{JOBS_URL}/{job_id}" if job_id else ""))
        if not poll_url:
            raise RuntimeError(f"{label} gave no job to poll: {str(submitted)[:300]}")
        if poll_url.startswith("/"):
            poll_url = f"{HOST}{poll_url}"

        deadline = time.time() + MAX_WAIT
        failed_in_a_row = 0
        wait = POLL_SECONDS
        while True:
            time.sleep(wait)
            wait = POLL_SECONDS
            # A failed poll is not a failed parse: the job is still running and
            # already billed, so wait and look again rather than resubmit.
            try:
                poll = httpx.get(poll_url, headers=headers,
                                 params={"output": output}, timeout=120)
                if poll.status_code >= 400:
                    raise VendorError(f"{label} poll {poll.status_code}: {poll.text[:200]}",
                                      status=poll.status_code, arm=label)
                polled = poll.json()
            except Exception as exc:  # noqa: BLE001 - classified immediately below
                status, verdict = classify(exc)
                failed_in_a_row += 1
                polls.append({"n": len(polls) + 1,
                              "error": f"{type(exc).__name__}: {str(exc)[:200]}",
                              "http_status": status})
                if verdict == "fatal":
                    raise
                if failed_in_a_row >= POLL_FAILURES or time.time() > deadline:
                    raise VendorError(f"{label} poll failed {failed_in_a_row} times in a row: {exc}",
                                      status=status, arm=label) from exc
                wait = retry_after(exc, failed_in_a_row)
                continue
            failed_in_a_row = 0
            status = polled.get("status")
            polls.append({"n": len(polls) + 1, "status": status})
            if status in ("completed", "completed_with_warnings"):
                break
            if status in ("failed", "cancelled"):
                raise VendorError(f"{label} {status}: {polled.get('error')}", arm=label)
            if time.time() > deadline:
                raise VendorError(f"{label} timed out after {MAX_WAIT}s", arm=label)
        result = polled.get("result") or {}

    markdown = result.get("markdown") or ""
    if not markdown:
        raise RuntimeError(f"{label} returned no markdown; keys={sorted(result)}")

    meta = {
        "profile": result.get("profile") or profile,
        "output": output,
        "job_id": job_id or result.get("id", ""),
        "n_pages": (result.get("metadata") or {}).get("page_count") or 0,
        "format_version": result.get("format_version"),
        "warnings": result.get("warnings") or [],
        "parsed_at": datetime.now(timezone.utc).isoformat(),
    }
    meta["io"] = exchange(
        {"submit": {"method": "POST", "url": url,
                    "headers": redact({**headers, "Prefer": "wait=0"}),
                    "data": data, "files": {"input": pdf.name}},
         "poll": {"method": "GET", "url": poll_url, "headers": redact(headers),
                  "every_seconds": POLL_SECONDS}},
        {"submit": {"status": response.status_code, "body": submitted},
         "polls": polls, "final": result},
    )
    return markdown, meta
