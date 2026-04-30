from __future__ import annotations

import httpx

from .exceptions import TroveError
from .models import KeyCreated, KeyMetadata

_DEFAULT_BASE_URL = "https://api.trovefiles.dev"


def _raise_for(response: httpx.Response) -> None:
    if not response.is_success:
        try:
            detail = response.json().get("detail", response.text)
        except Exception:
            detail = response.text
        raise TroveError(detail, status_code=response.status_code)


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

    def _keys_url(self) -> str:
        return f"/v1/workspaces/{self._workspace_id}/keys"

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

    def _keys_url(self) -> str:
        return f"/v1/workspaces/{self._workspace_id}/keys"

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

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> AsyncTroveAdminClient:
        return self

    async def __aexit__(self, *_) -> None:
        await self.aclose()
