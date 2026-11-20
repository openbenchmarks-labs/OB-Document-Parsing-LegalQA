"""Claude Fable on Amazon Bedrock asked to parse the PDF. Settings come from tools/fable.yaml."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from ..vendor_errors import VendorError
from .common import dump, exchange, page_count

# A whole contract in one call takes minutes, not seconds, on the frontier
# arms. This arm builds its own Bedrock client with a parse-length read
# timeout. Retries are left to the harness, which knows which failures are
# worth repeating and logs the attempt.
READ_TIMEOUT = 1800
_CLIENT = None


ROOT = Path(__file__).resolve().parents[2]


def _load_bedrock_environment() -> None:
    """Load AWS/Bedrock credentials from this repo's .env.local or .env."""
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env.local")
    load_dotenv(ROOT / ".env")


def _client():
    """Bedrock runtime client for parsing, with a parse-length read timeout."""
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT

    import boto3
    from botocore.config import Config

    _load_bedrock_environment()
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") \
        or "us-east-1"
    config = Config(retries={"max_attempts": 1}, read_timeout=READ_TIMEOUT,
                    connect_timeout=10)
    access_key = os.environ.get("AWS_ACCESS_KEY_ID")
    secret_key = os.environ.get("AWS_SECRET_ACCESS_KEY")
    profile = (os.environ.get("AWS_PROFILE") or "").strip()
    if access_key and secret_key:
        session = boto3.Session(aws_access_key_id=access_key,
                                aws_secret_access_key=secret_key, region_name=region)
        _CLIENT = session.client("bedrock-runtime", config=config)
    elif profile:
        session = boto3.Session(profile_name=profile, region_name=region)
        _CLIENT = session.client("bedrock-runtime", config=config)
    else:
        _CLIENT = boto3.client("bedrock-runtime", region_name=region, config=config)
    return _CLIENT


def parse(pdf: Path, settings: dict) -> tuple[str, dict]:
    """Claude Fable 5.1 on Bedrock, one converse call with the PDF attached.

    Two things about this model are not shared with the others. It reasons
    always-on and non-optionally, so the reply arrives as `reasoningContent`
    followed by `text` and the markdown has to be picked out of the block list
    rather than read off index 0 -- and those thinking tokens are billed as
    output, which is most of why the arm costs what it does. And `temperature`
    is rejected outright on Opus 4.7 and later, so inferenceConfig carries only
    a token ceiling; there is no determinism knob to set.

    Fable additionally refuses to run unless the account's Bedrock data
    retention mode is `provider_data_share`, meaning prompts are shared with
    Anthropic and held up to 30 days. That is an account-level setting this code
    cannot make on its own, so the failure is named rather than retried.
    """
    model, prompt, max_tokens = settings["model"], settings["prompt"], settings["max_tokens"]
    client = _client()
    body = {
        "modelId": model,
        "messages": [{"role": "user", "content": [
            {"text": prompt},
            {"document": {"format": "pdf", "name": "contract",
                          "source": {"bytes": pdf.read_bytes()}}},
        ]}],
        "inferenceConfig": {"maxTokens": max_tokens},
    }
    try:
        response = client.converse(**body)
    except Exception as exc:  # noqa: BLE001
        if "data retention mode" in str(exc):
            raise VendorError(
                "fable: Bedrock account data retention must be "
                "'provider_data_share' (aws bedrock put-account-data-retention "
                f"--mode provider_data_share): {str(exc)[:200]}",
                status=403, arm="fable") from exc
        status = (getattr(exc, "response", {}) or {}).get(
            "ResponseMetadata", {}).get("HTTPStatusCode")
        if status:
            raise VendorError(f"fable {status}: {str(exc)[:400]}",
                              status=status, arm="fable") from exc
        raise

    blocks = response["output"]["message"]["content"]
    markdown = "".join(b["text"] for b in blocks if "text" in b).strip()
    if not markdown:
        raise RuntimeError(f"fable returned no text; blocks={[list(b) for b in blocks]}")
    if response.get("stopReason") == "max_tokens":
        raise RuntimeError(f"fable parse truncated at {max_tokens} output "
                           f"tokens; {len(markdown)} chars returned for {pdf.name}")

    tokens = response.get("usage") or {}
    meta = {
        "model": model,
        "n_pages": page_count(pdf),
        # Normalised to the Responses API's names so cost_of_parse can price
        # both frontier arms through one code path.
        "usage": {"input_tokens": tokens.get("inputTokens") or 0,
                  "output_tokens": tokens.get("outputTokens") or 0},
        "stop_reason": response.get("stopReason"),
        "parsed_at": datetime.now(timezone.utc).isoformat(),
    }
    meta["io"] = exchange(
        {"sdk": "boto3", "method": "bedrock-runtime.converse", **{
            **body, "messages": [{"role": "user", "content": [
                {"text": prompt},
                {"document": {"format": "pdf", "name": "contract",
                              "source": f"<{pdf.name} bytes>"}}]}]}},
        {**dump(response), "output": "<markdown returned above>"},
    )
    return markdown, meta
