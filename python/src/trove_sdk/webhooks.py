"""
Verify Trove webhook signatures.

The Trove API signs every webhook delivery with HMAC-SHA256:

    X-Trove-Signature: t=<unix>,v1=<hex>

Where `v1 = hmac_sha256(secret, f"{t}.{raw_body}")`. To verify a delivery,
constant-time-compare the computed digest with `v1` and reject if `t` is too
old (default tolerance: 5 minutes).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any, Union

from .exceptions import TroveError
from .models import WebhookEvent

_DEFAULT_TOLERANCE = 300  # seconds


class WebhookSignatureError(TroveError):
    """Raised when a webhook signature is malformed, mismatched, or stale."""


def _parse_header(signature_header: str) -> tuple[int, str]:
    """Pull `t=` and `v1=` out of `t=...,v1=...`. Order- and space-tolerant."""
    parts: dict[str, str] = {}
    for chunk in signature_header.split(","):
        chunk = chunk.strip()
        if "=" not in chunk:
            continue
        k, _, v = chunk.partition("=")
        parts[k.strip()] = v.strip()
    try:
        timestamp = int(parts["t"])
    except (KeyError, ValueError):
        raise WebhookSignatureError("Missing or invalid `t` in X-Trove-Signature")
    if "v1" not in parts:
        raise WebhookSignatureError("Missing `v1` in X-Trove-Signature")
    return timestamp, parts["v1"]


def verify_webhook(
    *,
    secret: str,
    body: Union[str, bytes],
    signature_header: str,
    tolerance_seconds: int = _DEFAULT_TOLERANCE,
) -> WebhookEvent:
    """Verify a Trove webhook delivery and return the parsed event.

    Raises `WebhookSignatureError` on bad signature, missing fields, or stale
    timestamp. Pass the *raw* request body — JSON re-serialization will
    invalidate the signature.

    Example (FastAPI):
        @app.post("/trove-webhook")
        async def receive(request: Request):
            body = await request.body()
            event = verify_webhook(
                secret=os.environ["TROVE_WEBHOOK_SECRET"],
                body=body,
                signature_header=request.headers["x-trove-signature"],
            )
            ...
    """
    if not signature_header:
        raise WebhookSignatureError("Missing X-Trove-Signature header")
    timestamp, provided = _parse_header(signature_header)

    age = abs(int(time.time()) - timestamp)
    if age > tolerance_seconds:
        raise WebhookSignatureError(
            f"Signature timestamp outside tolerance ({age}s > {tolerance_seconds}s)"
        )

    raw = body.encode() if isinstance(body, str) else body
    payload = f"{timestamp}.".encode() + raw
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, provided):
        raise WebhookSignatureError("Signature mismatch")

    try:
        decoded: dict[str, Any] = json.loads(raw)
    except json.JSONDecodeError as e:
        raise WebhookSignatureError(f"Body is not valid JSON: {e}")

    return WebhookEvent(
        id=decoded["id"],
        type=decoded["type"],
        api_version=decoded["api_version"],
        workspace_id=decoded["workspace_id"],
        namespace=decoded.get("namespace"),
        created_at=decoded["created_at"],
        data=decoded.get("data", {}),
    )
