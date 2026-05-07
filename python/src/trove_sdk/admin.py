from __future__ import annotations

import httpx

from .exceptions import raise_for_response as _raise_for
from .models import (
    KeyCreated,
    KeyMetadata,
    WebhookCreated,
    WebhookMetadata,
    WebhookTestResult,
)

_DEFAULT_BASE_URL = "https://api.trovefiles.dev"


def _parse_key_metadata(d: dict) -> KeyMetadata:
    return KeyMetadata(
        key_id=d["key_id"],
        name=d["name"],
        prefix=d["prefix"],
        scope=d["scope"],
        namespace=d.get("namespace"),
        created_at=d["created_at"],
    )


def _parse_key_created(d: dict) -> KeyCreated:
    return KeyCreated(
        key_id=d["key_id"],
        name=d["name"],
        prefix=d["prefix"],
        scope=d["scope"],
        namespace=d.get("namespace"),
        created_at=d["created_at"],
        api_key=d["api_key"],
    )


def _parse_webhook_metadata(d: dict) -> WebhookMetadata:
    return WebhookMetadata(
        webhook_id=d["webhook_id"],
        url=d["url"],
        events=d.get("events", ["*"]),
        namespace=d.get("namespace"),
        description=d.get("description"),
        enabled=d.get("enabled", True),
        created_at=d.get("created_at", ""),
    )


def _parse_webhook_created(d: dict) -> WebhookCreated:
    return WebhookCreated(
        webhook_id=d["webhook_id"],
        url=d["url"],
        events=d.get("events", ["*"]),
        namespace=d.get("namespace"),
        description=d.get("description"),
        enabled=d.get("enabled", True),
        created_at=d.get("created_at", ""),
        signing_secret=d["signing_secret"],
    )


def _parse_webhook_test_result(d: dict) -> WebhookTestResult:
    return WebhookTestResult(
        ok=bool(d.get("ok")),
        event_id=d.get("event_id", ""),
        status=d.get("status"),
        error=d.get("error"),
    )


class TroveAdminClient:
    """Synchronous client for Trove key management (requires admin key or service secret)."""

    def __init__(
        self,
        api_key: str,
        workspace_id: str,
        *,
        base_url: str = _DEFAULT_BASE_URL,
        timeout: float = 30.0,
    ) -> None:
        self._workspace_id = workspace_id
        self._http = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )

    @classmethod
    def from_api_key(
        cls,
        api_key: str,
        *,
        base_url: str = _DEFAULT_BASE_URL,
        timeout: float = 30.0,
    ) -> "TroveAdminClient":
        """Discover ``workspace_id`` from ``/v1/me`` and construct the client.

        Saves callers from fishing the workspace ID out of the dashboard
        when the key already encodes it. Raises ``TroveError`` if the key
        is invalid or the server is older than ``/v1/me``.
        """
        with httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10.0,
        ) as probe:
            resp = probe.get("/v1/me")
            _raise_for(resp)
            workspace_id = resp.json()["workspace_id"]
        return cls(api_key, workspace_id, base_url=base_url, timeout=timeout)

    def _keys_url(self) -> str:
        return f"/v1/workspaces/{self._workspace_id}/keys"

    def _webhooks_url(self) -> str:
        return f"/v1/workspaces/{self._workspace_id}/webhooks"

    def create_key(self, name: str, *, namespace: str | None = None) -> KeyCreated:
        """Mint a namespace-scoped workspace key for a customer."""
        resp = self._http.post(
            self._keys_url(),
            json={"name": name, "namespace": namespace, "admin": False},
        )
        _raise_for(resp)
        return _parse_key_created(resp.json())

    def list_keys(self) -> list[KeyMetadata]:
        resp = self._http.get(self._keys_url())
        _raise_for(resp)
        return [_parse_key_metadata(k) for k in resp.json().get("keys", [])]

    def revoke_key(self, key_id: str) -> None:
        resp = self._http.delete(f"{self._keys_url()}/{key_id}")
        _raise_for(resp)

    def create_webhook(
        self,
        url: str,
        *,
        events: list[str] | None = None,
        namespace: str | None = None,
        description: str | None = None,
    ) -> WebhookCreated:
        """Subscribe a URL to workspace events. Signing secret is shown once."""
        resp = self._http.post(
            self._webhooks_url(),
            json={
                "url": url,
                "events": events or ["*"],
                "namespace": namespace,
                "description": description,
            },
        )
        _raise_for(resp)
        return _parse_webhook_created(resp.json())

    def list_webhooks(self) -> list[WebhookMetadata]:
        resp = self._http.get(self._webhooks_url())
        _raise_for(resp)
        return [_parse_webhook_metadata(w) for w in resp.json().get("webhooks", [])]

    def delete_webhook(self, webhook_id: str) -> None:
        resp = self._http.delete(f"{self._webhooks_url()}/{webhook_id}")
        _raise_for(resp)

    def test_webhook(self, webhook_id: str) -> WebhookTestResult:
        """Fire a `webhook.test` event at the endpoint and report what came back."""
        resp = self._http.post(f"{self._webhooks_url()}/{webhook_id}/test")
        _raise_for(resp)
        return _parse_webhook_test_result(resp.json())

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> TroveAdminClient:
        return self

    def __exit__(self, *_) -> None:
        self.close()


class AsyncTroveAdminClient:
    """Async client for Trove key management."""

    def __init__(
        self,
        api_key: str,
        workspace_id: str,
        *,
        base_url: str = _DEFAULT_BASE_URL,
        timeout: float = 30.0,
    ) -> None:
        self._workspace_id = workspace_id
        self._http = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )

    @classmethod
    async def from_api_key(
        cls,
        api_key: str,
        *,
        base_url: str = _DEFAULT_BASE_URL,
        timeout: float = 30.0,
    ) -> "AsyncTroveAdminClient":
        """Discover ``workspace_id`` from ``/v1/me`` and construct the client."""
        async with httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10.0,
        ) as probe:
            resp = await probe.get("/v1/me")
            _raise_for(resp)
            workspace_id = resp.json()["workspace_id"]
        return cls(api_key, workspace_id, base_url=base_url, timeout=timeout)

    def _keys_url(self) -> str:
        return f"/v1/workspaces/{self._workspace_id}/keys"

    def _webhooks_url(self) -> str:
        return f"/v1/workspaces/{self._workspace_id}/webhooks"

    async def create_key(self, name: str, *, namespace: str | None = None) -> KeyCreated:
        resp = await self._http.post(
            self._keys_url(),
            json={"name": name, "namespace": namespace, "admin": False},
        )
        _raise_for(resp)
        return _parse_key_created(resp.json())

    async def list_keys(self) -> list[KeyMetadata]:
        resp = await self._http.get(self._keys_url())
        _raise_for(resp)
        return [_parse_key_metadata(k) for k in resp.json().get("keys", [])]

    async def revoke_key(self, key_id: str) -> None:
        resp = await self._http.delete(f"{self._keys_url()}/{key_id}")
        _raise_for(resp)

    async def create_webhook(
        self,
        url: str,
        *,
        events: list[str] | None = None,
        namespace: str | None = None,
        description: str | None = None,
    ) -> WebhookCreated:
        resp = await self._http.post(
            self._webhooks_url(),
            json={
                "url": url,
                "events": events or ["*"],
                "namespace": namespace,
                "description": description,
            },
        )
        _raise_for(resp)
        return _parse_webhook_created(resp.json())

    async def list_webhooks(self) -> list[WebhookMetadata]:
        resp = await self._http.get(self._webhooks_url())
        _raise_for(resp)
        return [_parse_webhook_metadata(w) for w in resp.json().get("webhooks", [])]

    async def delete_webhook(self, webhook_id: str) -> None:
        resp = await self._http.delete(f"{self._webhooks_url()}/{webhook_id}")
        _raise_for(resp)

    async def test_webhook(self, webhook_id: str) -> WebhookTestResult:
        resp = await self._http.post(f"{self._webhooks_url()}/{webhook_id}/test")
        _raise_for(resp)
        return _parse_webhook_test_result(resp.json())

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> AsyncTroveAdminClient:
        return self

    async def __aexit__(self, *_) -> None:
        await self.aclose()
