from __future__ import annotations

from typing import BinaryIO

import httpx

from .exceptions import TroveError
from .models import FileResult, Snapshot

_DEFAULT_BASE_URL = "https://api.trovefiles.dev"


def _norm_path(path: str) -> str:
    """Strip leading workspace/ prefix — write/upload/delete paths are namespace-relative."""
    p = path.lstrip("/")
    if p.startswith("workspace/"):
        p = p[len("workspace/"):]
    return p


def _parse_snapshot(d: dict) -> Snapshot:
    return Snapshot(
        snapshot_id=d["snapshot_id"],
        namespace=d["namespace"],
        label=d.get("label"),
        size_bytes=d["size_bytes"],
        created_at=d["created_at"],
    )


def _raise_for(response: httpx.Response) -> None:
    if not response.is_success:
        try:
            detail = response.json().get("detail", response.text)
        except Exception:
            detail = response.text
        raise TroveError(detail, status_code=response.status_code)


class TroveClient:
    """Synchronous client for Trove filesystem operations."""

    def __init__(
        self,
        api_key: str,
        namespace: str,
        *,
        base_url: str = _DEFAULT_BASE_URL,
        timeout: float = 60.0,
    ) -> None:
        self._http = httpx.Client(
            base_url=base_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "X-Namespace": namespace,
            },
            timeout=timeout,
        )

    def exec(self, command: str) -> str:
        """Run a shell command in the workspace. Returns stdout (or error output)."""
        resp = self._http.post("/exec", json={"command": command})
        _raise_for(resp)
        return resp.text

    def write(self, path: str, content: str) -> FileResult:
        """Write a UTF-8 text file at path (created or overwritten)."""
        resp = self._http.post("/write", json={"path": _norm_path(path), "content": content})
        _raise_for(resp)
        data = resp.json()
        return FileResult(path=data["path"], size_bytes=data["size_bytes"])

    def upload(self, path: str, data: bytes | BinaryIO) -> FileResult:
        """Upload raw bytes to path. Accepts bytes or a file-like object."""
        body = data if isinstance(data, bytes) else data.read()
        resp = self._http.put(f"/files/{_norm_path(path)}", content=body)
        _raise_for(resp)
        d = resp.json()
        return FileResult(path=d["path"], size_bytes=d["size_bytes"])

    def delete(self, path: str) -> str:
        """Delete a file or directory. Returns the deleted path."""
        resp = self._http.post("/delete", json={"path": _norm_path(path)})
        _raise_for(resp)
        return resp.json()["deleted"]

    def create_snapshot(self, label: str | None = None) -> Snapshot:
        """Tar the current namespace state and store it. Restorable for 30 days."""
        resp = self._http.post("/v1/snapshots", json={"label": label})
        _raise_for(resp)
        return _parse_snapshot(resp.json())

    def list_snapshots(self) -> list[Snapshot]:
        resp = self._http.get("/v1/snapshots")
        _raise_for(resp)
        return [_parse_snapshot(s) for s in resp.json().get("snapshots", [])]

    def restore_snapshot(self, snapshot_id: str) -> int:
        """Wipe the namespace and restore from snapshot. Returns # files restored."""
        resp = self._http.post(f"/v1/snapshots/{snapshot_id}/restore")
        _raise_for(resp)
        return resp.json().get("files_restored", 0)

    def delete_snapshot(self, snapshot_id: str) -> None:
        resp = self._http.delete(f"/v1/snapshots/{snapshot_id}")
        _raise_for(resp)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> TroveClient:
        return self

    def __exit__(self, *_) -> None:
        self.close()


class AsyncTroveClient:
    """Async client for Trove filesystem operations."""

    def __init__(
        self,
        api_key: str,
        namespace: str,
        *,
        base_url: str = _DEFAULT_BASE_URL,
        timeout: float = 60.0,
    ) -> None:
        self._http = httpx.AsyncClient(
            base_url=base_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "X-Namespace": namespace,
            },
            timeout=timeout,
        )

    async def exec(self, command: str) -> str:
        resp = await self._http.post("/exec", json={"command": command})
        _raise_for(resp)
        return resp.text

    async def write(self, path: str, content: str) -> FileResult:
        resp = await self._http.post("/write", json={"path": _norm_path(path), "content": content})
        _raise_for(resp)
        data = resp.json()
        return FileResult(path=data["path"], size_bytes=data["size_bytes"])

    async def upload(self, path: str, data: bytes | BinaryIO) -> FileResult:
        body = data if isinstance(data, bytes) else data.read()
        resp = await self._http.put(f"/files/{_norm_path(path)}", content=body)
        _raise_for(resp)
        d = resp.json()
        return FileResult(path=d["path"], size_bytes=d["size_bytes"])

    async def delete(self, path: str) -> str:
        resp = await self._http.post("/delete", json={"path": _norm_path(path)})
        _raise_for(resp)
        return resp.json()["deleted"]

    async def create_snapshot(self, label: str | None = None) -> Snapshot:
        resp = await self._http.post("/v1/snapshots", json={"label": label})
        _raise_for(resp)
        return _parse_snapshot(resp.json())

    async def list_snapshots(self) -> list[Snapshot]:
        resp = await self._http.get("/v1/snapshots")
        _raise_for(resp)
        return [_parse_snapshot(s) for s in resp.json().get("snapshots", [])]

    async def restore_snapshot(self, snapshot_id: str) -> int:
        resp = await self._http.post(f"/v1/snapshots/{snapshot_id}/restore")
        _raise_for(resp)
        return resp.json().get("files_restored", 0)

    async def delete_snapshot(self, snapshot_id: str) -> None:
        resp = await self._http.delete(f"/v1/snapshots/{snapshot_id}")
        _raise_for(resp)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> AsyncTroveClient:
        return self

    async def __aexit__(self, *_) -> None:
        await self.aclose()
