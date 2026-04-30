import hashlib
import hmac
import json
import time

import httpx
import pytest
import respx

from trove_sdk import (
    TroveAdminClient,
    WebhookCreated,
    WebhookMetadata,
    WebhookSignatureError,
    WebhookTestResult,
    verify_webhook,
)


BASE = "https://api.trovefiles.dev"
WS_ID = "ws-abc123"
HOOKS_URL = f"{BASE}/v1/workspaces/{WS_ID}/webhooks"


# ── Admin client ──────────────────────────────────────────────────────────────

@respx.mock
def test_create_webhook():
    respx.post(HOOKS_URL).mock(return_value=httpx.Response(201, json={
        "webhook_id":     "wh-aaa",
        "url":            "https://example.com/hook",
        "events":         ["file.written"],
        "namespace":      None,
        "description":    None,
        "enabled":        True,
        "created_at":     "2026-04-30T00:00:00Z",
        "signing_secret": "trove-whsec-deadbeef",
    }))
    with TroveAdminClient("trove-sk-admin", WS_ID, base_url=BASE) as admin:
        wh = admin.create_webhook("https://example.com/hook", events=["file.written"])
    assert isinstance(wh, WebhookCreated)
    assert wh.signing_secret == "trove-whsec-deadbeef"
    assert wh.events == ["file.written"]


@respx.mock
def test_list_webhooks():
    respx.get(HOOKS_URL).mock(return_value=httpx.Response(200, json={"webhooks": [
        {"webhook_id": "wh-1", "url": "https://a", "events": ["*"], "namespace": None, "description": None, "enabled": True, "created_at": "2026-04-30T00:00:00Z"},
        {"webhook_id": "wh-2", "url": "https://b", "events": ["file.written"], "namespace": "alice", "description": "alice prod", "enabled": True, "created_at": "2026-04-30T01:00:00Z"},
    ]}))
    with TroveAdminClient("trove-sk-admin", WS_ID, base_url=BASE) as admin:
        hooks = admin.list_webhooks()
    assert len(hooks) == 2
    assert isinstance(hooks[0], WebhookMetadata)
    assert hooks[1].namespace == "alice"


@respx.mock
def test_delete_webhook():
    respx.delete(f"{HOOKS_URL}/wh-1").mock(return_value=httpx.Response(200, json={"webhook_id": "wh-1", "deleted": True}))
    with TroveAdminClient("trove-sk-admin", WS_ID, base_url=BASE) as admin:
        admin.delete_webhook("wh-1")


@respx.mock
def test_test_webhook():
    respx.post(f"{HOOKS_URL}/wh-1/test").mock(return_value=httpx.Response(200, json={
        "ok": True, "status": 204, "event_id": "evt-zzz",
    }))
    with TroveAdminClient("trove-sk-admin", WS_ID, base_url=BASE) as admin:
        result = admin.test_webhook("wh-1")
    assert isinstance(result, WebhookTestResult)
    assert result.ok and result.status == 204


# ── Signature verification ────────────────────────────────────────────────────

SECRET = "trove-whsec-test"

def _sign(secret: str, ts: int, body: bytes) -> str:
    digest = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={ts},v1={digest}"


def _payload() -> bytes:
    return json.dumps({
        "id": "evt-abc",
        "type": "file.written",
        "api_version": "2026-04-30",
        "workspace_id": "ws-abc",
        "namespace": "alice",
        "created_at": "2026-04-30T00:00:00Z",
        "data": {"path": "workspace/foo.txt", "size_bytes": 12, "source": "write"},
    }).encode()


def test_verify_webhook_happy():
    body = _payload()
    ts = int(time.time())
    event = verify_webhook(secret=SECRET, body=body, signature_header=_sign(SECRET, ts, body))
    assert event.id == "evt-abc"
    assert event.type == "file.written"
    assert event.data["path"] == "workspace/foo.txt"


def test_verify_webhook_bad_secret():
    body = _payload()
    ts = int(time.time())
    sig = _sign("wrong-secret", ts, body)
    with pytest.raises(WebhookSignatureError, match="mismatch"):
        verify_webhook(secret=SECRET, body=body, signature_header=sig)


def test_verify_webhook_stale_timestamp():
    body = _payload()
    ts = int(time.time()) - 1000
    sig = _sign(SECRET, ts, body)
    with pytest.raises(WebhookSignatureError, match="tolerance"):
        verify_webhook(secret=SECRET, body=body, signature_header=sig, tolerance_seconds=300)


def test_verify_webhook_tampered_body():
    body = _payload()
    ts = int(time.time())
    sig = _sign(SECRET, ts, body)
    tampered = body.replace(b"foo.txt", b"bar.txt")
    with pytest.raises(WebhookSignatureError, match="mismatch"):
        verify_webhook(secret=SECRET, body=tampered, signature_header=sig)


def test_verify_webhook_malformed_header():
    with pytest.raises(WebhookSignatureError):
        verify_webhook(secret=SECRET, body=_payload(), signature_header="garbage")


def test_verify_webhook_accepts_string_body():
    body_bytes = _payload()
    ts = int(time.time())
    sig = _sign(SECRET, ts, body_bytes)
    event = verify_webhook(secret=SECRET, body=body_bytes.decode(), signature_header=sig)
    assert event.id == "evt-abc"
