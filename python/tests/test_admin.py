import pytest
import respx
import httpx

from trove_sdk import TroveAdminClient, TroveError
from trove_sdk.models import KeyCreated, KeyMetadata


BASE = "https://api.trovefiles.dev"
WS_ID = "ws-abc123"
KEYS_URL = f"{BASE}/v1/workspaces/{WS_ID}/keys"


@respx.mock
def test_list_keys():
    respx.get(KEYS_URL).mock(return_value=httpx.Response(200, json={"keys": [
        {"key_id": "key-1", "name": "prod", "prefix": "trove-sk-abc", "scope": "workspace", "namespace": None, "created_at": "2026-01-01T00:00:00Z"},
        {"key_id": "key-2", "name": "alice", "prefix": "trove-sk-def", "scope": "workspace", "namespace": "alice", "created_at": "2026-01-02T00:00:00Z"},
    ]}))
    with TroveAdminClient("trove-sk-admin", WS_ID, base_url=BASE) as admin:
        keys = admin.list_keys()
    assert len(keys) == 2
    assert isinstance(keys[0], KeyMetadata)
    assert keys[1].namespace == "alice"


@respx.mock
def test_create_key():
    respx.post(KEYS_URL).mock(return_value=httpx.Response(201, json={
        "key_id": "key-new",
        "name": "bob",
        "prefix": "trove-sk-bbb",
        "scope": "workspace",
        "namespace": "bob",
        "created_at": "2026-01-03T00:00:00Z",
        "api_key": "trove-sk-bbbfull",
    }))
    with TroveAdminClient("trove-sk-admin", WS_ID, base_url=BASE) as admin:
        key = admin.create_key("bob", namespace="bob")
    assert isinstance(key, KeyCreated)
    assert key.api_key == "trove-sk-bbbfull"
    assert key.namespace == "bob"


@respx.mock
def test_revoke_key():
    respx.delete(f"{KEYS_URL}/key-1").mock(return_value=httpx.Response(200, json={"key_id": "key-1", "revoked": True}))
    with TroveAdminClient("trove-sk-admin", WS_ID, base_url=BASE) as admin:
        admin.revoke_key("key-1")


@respx.mock
def test_raises_on_403():
    respx.post(KEYS_URL).mock(return_value=httpx.Response(403, json={"detail": "Workspace keys cannot manage keys."}))
    with TroveAdminClient("trove-sk-workspace-key", WS_ID, base_url=BASE) as admin:
        with pytest.raises(TroveError) as exc_info:
            admin.create_key("fail")
    assert exc_info.value.status_code == 403


@respx.mock
def test_from_api_key_discovers_workspace_id():
    respx.get(f"{BASE}/v1/me").mock(
        return_value=httpx.Response(
            200,
            json={
                "workspace_id": WS_ID,
                "key_id": "key-admin",
                "scope": "admin",
                "namespace": None,
            },
        )
    )
    respx.get(KEYS_URL).mock(return_value=httpx.Response(200, json={"keys": []}))
    with TroveAdminClient.from_api_key("trove-sk-admin", base_url=BASE) as admin:
        # Confirm the discovered workspace_id is what the keys endpoint sees.
        admin.list_keys()
