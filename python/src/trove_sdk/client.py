from __future__ import annotations

from typing import BinaryIO

import httpx

from .exceptions import TroveError
from .models import ExecResult, FileContent, FileInfo, FileResult, Snapshot

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


def _parse_file_info(d: dict) -> FileInfo:
    return FileInfo(
        name=d["name"],
        path=d["path"],
        is_dir=d["is_dir"],
        size_bytes=d.get("size_bytes"),
        modified_at=d["modified_at"],
    )


def _parse_exec_result(d: dict) -> ExecResult:
    return ExecResult(
        exit_code=int(d["exit_code"]),
        stdout=d.get("stdout", "") or "",
        stderr=d.get("stderr", "") or "",
        duration_ms=int(d.get("duration_ms", 0)),
    )


def _parse_file_content(d: dict) -> FileContent:
    return FileContent(
        path=d["path"],
        size_bytes=d["size_bytes"],
        modified_at=d["modified_at"],
        encoding=d["encoding"],
        content=d.get("content"),
        truncated=d.get("truncated", False),
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

    def exec(self, command: str, *, stdin: str | None = None) -> str:
        """Run a shell command in the workspace. Returns stdout (or error output).

        For agent loops where you need to branch on exit code or read stderr
        cleanly, prefer :meth:`exec_detailed` — this method preserves the
        legacy text response (`[exit N]\nstderr\nstdout` on non-zero) for
        backwards compatibility.

        ``stdin`` is piped to the spawned shell as UTF-8. None == /dev/null.
        Server caps the payload at 1 MB; for binary input, ``upload`` then
        redirect from disk in the command string (``"jq . < workspace/x"``).
        Requires server capability ``exec.stdin`` — older servers ignore the
        field.
        """
        body: dict = {"command": command}
        if stdin is not None:
            body["stdin"] = stdin
        resp = self._http.post("/exec", json=body)
        _raise_for(resp)
        return resp.text

    def exec_detailed(self, command: str, *, stdin: str | None = None) -> ExecResult:
        """Run a shell command and return structured output.

        Hits the JSON-mode `POST /v1/exec` endpoint, so `exit_code`, `stdout`,
        and `stderr` come back as separate fields. The 30-second server-side
        timeout still applies; on timeout the SDK raises :class:`TroveError`
        with status_code=408. ``stdin`` semantics match :meth:`exec`.
        """
        body: dict = {"command": command}
        if stdin is not None:
            body["stdin"] = stdin
        resp = self._http.post("/v1/exec", json=body)
        _raise_for(resp)
        return _parse_exec_result(resp.json())

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

    def list_dir(self, path: str = "workspace/") -> list[FileInfo]:
        """List a directory in the workspace. Directories first, then files, alphabetical."""
        resp = self._http.get("/v1/files", params={"path": path})
        _raise_for(resp)
        return [_parse_file_info(e) for e in resp.json().get("entries", [])]

    def read_text(self, path: str) -> str:
        """Read a UTF-8 text file. Caps at 1 MB; raises TroveError on binary content."""
        info = self.read_file(path)
        if info.encoding == "binary":
            raise TroveError(f"{path} is binary; use read_bytes() instead", status_code=415)
        return info.content or ""

    def read_file(self, path: str) -> FileContent:
        """Read a file's metadata + content (UTF-8 or 'binary' marker)."""
        resp = self._http.get("/v1/files/content", params={"path": path})
        _raise_for(resp)
        return _parse_file_content(resp.json())

    def read_bytes(self, path: str) -> bytes:
        """Download a file's raw bytes. Binary-safe — use this for images,
        PDFs, audio, anything `read_text` would refuse.

        The server caps the response at the same size as `upload` (100 MB).
        Larger files come back truncated; check
        ``response.headers["X-Trove-Truncated"]`` if you need to know.
        """
        resp = self._http.get(f"/files/{_norm_path(path)}")
        _raise_for(resp)
        return resp.content

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

    async def exec(self, command: str, *, stdin: str | None = None) -> str:
        body: dict = {"command": command}
        if stdin is not None:
            body["stdin"] = stdin
        resp = await self._http.post("/exec", json=body)
        _raise_for(resp)
        return resp.text

    async def exec_detailed(self, command: str, *, stdin: str | None = None) -> ExecResult:
        body: dict = {"command": command}
        if stdin is not None:
            body["stdin"] = stdin
        resp = await self._http.post("/v1/exec", json=body)
        _raise_for(resp)
        return _parse_exec_result(resp.json())

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

    async def list_dir(self, path: str = "workspace/") -> list[FileInfo]:
        resp = await self._http.get("/v1/files", params={"path": path})
        _raise_for(resp)
        return [_parse_file_info(e) for e in resp.json().get("entries", [])]

    async def read_text(self, path: str) -> str:
        info = await self.read_file(path)
        if info.encoding == "binary":
            raise TroveError(f"{path} is binary; use read_bytes() instead", status_code=415)
        return info.content or ""

    async def read_file(self, path: str) -> FileContent:
        resp = await self._http.get("/v1/files/content", params={"path": path})
        _raise_for(resp)
        return _parse_file_content(resp.json())

    async def read_bytes(self, path: str) -> bytes:
        resp = await self._http.get(f"/files/{_norm_path(path)}")
        _raise_for(resp)
        return resp.content

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
