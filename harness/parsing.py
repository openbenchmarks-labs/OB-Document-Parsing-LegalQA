"""Render one contract PDF to markdown, once per arm.

An arm is either a tool setup from tools/<tool>.yaml, called through its
adapter, or a reference arm. Reference arms are not calls: oracle and blind
ship inside the dataset, rendered from the source file it withholds.
They bound the scale: oracle is what a perfect strike-preserving parse would
give the reader, blind is what a parser that drops the markup gives it.

Parsing is per document, never per question. Many questions share one
contract, and the cache is keyed on the PDF's sha256 and the setup's
settings, so an unchanged document is never billed twice.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import time
from pathlib import Path

from .tool_registry import TOOLS
from .vendor_errors import FatalVendorError, VendorError, classify, retry_after

REFERENCE_ARMS = ("oracle", "blind")
REFERENCE_IDENTITY: dict[str, dict[str, str]] = {
    "oracle":   {"vendor": "reference", "variant": "struck text kept as ~~…~~"},
    "blind":    {"vendor": "reference", "variant": "all markup dropped"},
}
MAX_ATTEMPTS = 5


def arms() -> tuple[str, ...]:
    return REFERENCE_ARMS + tuple(sorted(TOOLS))


def identity(arm: str) -> dict[str, str]:
    """Who an arm belongs to, and what sets it apart from its siblings.

    Datalab and LlamaParse each run two setups, and the arm name alone does not
    say so; `variant` is the setting that makes the arm a distinct measurement.
    """
    if arm in REFERENCE_IDENTITY:
        return REFERENCE_IDENTITY[arm]
    tool = TOOLS.get(arm)
    if tool is None:
        return {"vendor": arm, "variant": ""}
    return {"vendor": tool.vendor, "variant": tool.variant}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def call_vendor(arm: str, pdf: Path, *, attempts: int = MAX_ATTEMPTS,
                fresh: bool = False) -> tuple[str, dict, list[dict]]:
    """One vendor call, retried while the failure looks like weather.

    Returns the parse plus a log of every attempt that did not succeed. That log
    is what goes in the archive: one entry per try, rather than one archive
    entry per try, so a retried parse leaves a single record that says it was
    retried instead of several records that look like several parses.

    `fresh` asks the vendor to bypass its own result cache, on the arms that
    have one to bypass. Everywhere else a call is already a fresh parse.
    """
    tool = TOOLS[arm]
    kwargs = {"fresh": True} if fresh and tool.bypasses_vendor_cache else {}
    log: list[dict] = []
    for attempt in range(1, attempts + 1):
        started = time.time()
        try:
            markdown, meta = tool.parse(pdf, tool.settings, **kwargs)
        except Exception as exc:  # noqa: BLE001 - classified immediately below
            status, verdict = classify(exc)
            entry = {"attempt": attempt, "status": status, "verdict": verdict,
                     "error": f"{type(exc).__name__}: {str(exc)[:400]}",
                     "seconds": round(time.time() - started, 2)}
            last = attempt >= attempts
            if verdict == "retry" and not last:
                entry["retry_in"] = round(retry_after(exc, attempt), 1)
                log.append(entry)
                time.sleep(entry["retry_in"])
                continue
            log.append(entry)
            message = f"{arm} failed after {attempt} attempt(s): {exc}"
            if verdict == "fatal":
                raise FatalVendorError(message, status=status, arm=arm) from exc
            raise VendorError(message, status=status, arm=arm) from exc
        return markdown, meta, log
    raise VendorError(f"{arm}: exhausted {attempts} attempts", arm=arm)


def settings_fingerprint(settings: dict) -> str:
    payload = json.dumps(settings, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def cache_paths(cache_dir: Path, document_id: str, arm: str) -> tuple[Path, Path]:
    return cache_dir / f"{document_id}.{arm}.md", cache_dir / f"{document_id}.{arm}.meta.json"


def write_io(cache_dir: Path, document_id: str, arm: str, io: dict) -> Path:
    """Save the verbatim vendor exchange for one parse, gzipped: a Reducto or
    Extend body carries every chunk with its bounding boxes."""
    path = cache_dir / f"{document_id}.{arm}.io.json.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump(io, handle, default=str)
    return path


def repeat_label(arm: str, repeat: int) -> str:
    """The cache slot for one repeat. Repeat 1 is the arm itself, so a cache
    filled before repeats existed is still repeat 1."""
    return arm if repeat <= 1 else f"{arm}.r{repeat}"


def render(document, arm: str, cache_dir: Path, *, repeat: int = 1) -> tuple[str, dict]:
    """Markdown for one document under one arm, plus what it cost to get.

    `repeat` > 1 is another parse of the same PDF with the same settings, kept
    in its own cache slot and requested with the vendor's own result cache
    bypassed where it has one: a repeat served from that cache is the same parse
    twice, and would make the spread between repeats look smaller than it is.
    Reference arms are files, not calls, so every repeat of one is the same.
    """
    if arm in REFERENCE_ARMS:
        path = document.views.get(arm)
        if not path or not path.is_file():
            raise SystemExit(f"{document.document_id}: snapshot has no {arm} view")
        text = path.read_text(encoding="utf-8")
        return text, {"arm": arm, **identity(arm), "reference": True, "cached": True,
                      "markdown_chars": len(text), "repeat": repeat}

    if arm not in TOOLS:
        raise SystemExit(f"unknown arm {arm!r}; choose from {', '.join(arms())}")

    tool = TOOLS[arm]
    pdf = document.pdf
    label = repeat_label(arm, repeat)
    md_path, meta_path = cache_paths(cache_dir, document.document_id, label)
    digest = sha256(pdf)
    fingerprint = settings_fingerprint(tool.settings)
    if md_path.is_file() and meta_path.is_file():
        meta = json.loads(meta_path.read_text())
        if meta.get("pdf_sha256") == digest and meta.get("settings_sha256") == fingerprint:
            # Stamped on read as well as on write, so a cache filled before this
            # existed still reports which vendor and variant produced it.
            meta.update({"cached": True, "repeat": repeat, **identity(arm)})
            return md_path.read_text(encoding="utf-8"), meta

    started = time.time()
    markdown, meta, attempts = call_vendor(arm, pdf, fresh=repeat > 1)
    # The verbatim exchange is bulky and is wanted in the archive rather than in
    # every meta.json the cache reads, so it goes to its own file next to it.
    exchange = meta.pop("io", None)
    # Wall clock around the whole call, including any polling. Some vendors
    # report their own processing time and some report nothing, so this is the
    # only latency figure that means the same thing across all of them.
    elapsed = round(time.time() - started, 2)
    meta.update({
        "arm": arm,
        **identity(arm),
        "reference": tool.vendor == "reference",
        "seconds": elapsed,
        "markdown_file": md_path.name,
        "pdf": pdf.name,
        "pdf_sha256": digest,
        "pdf_bytes": pdf.stat().st_size,
        "settings": tool.settings,
        "settings_sha256": fingerprint,
        "markdown_chars": len(markdown),
        "cached": False,
        "repeat": repeat,
        "vendor_cache_bypassed": repeat > 1 and tool.bypasses_vendor_cache,
    })
    if attempts:
        # One record that says it was retried, rather than one record per try.
        meta["retries"] = len(attempts)
        exchange = exchange or {}
        exchange["attempts"] = attempts

    cache_dir.mkdir(parents=True, exist_ok=True)
    md_path.write_text(markdown, encoding="utf-8")
    if exchange is not None:
        written = write_io(cache_dir, document.document_id, label, exchange)
        meta["io_file"] = written.name
        meta["io_bytes"] = written.stat().st_size
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return markdown, meta
