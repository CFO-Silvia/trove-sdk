from __future__ import annotations

from typing import BinaryIO, Sequence

import httpx

from .exceptions import TroveError, TroveExecError, raise_for_response
from .models import (
    BytesContent,
    ExecResult,
    FileContent,
    FileInfo,
    FileResult,
    ListResult,
    Snapshot,
    WorkspaceBootstrap,
)

_DEFAULT_BASE_URL = "https://api.trovefiles.dev"

# Sourced by /v1/exec before each user command if present. Persistent shell
# context (cwd, env, venv activation) lives here so it survives across agent
# process restarts — see set_init/get_init/clear_init.
_INIT_PATH = "workspace/.trove/init.sh"

# Free-form cross-session note an agent leaves for the next instance of itself.
# Read by `get_agent_memory` / surfaced in `bootstrap()`. The runtime doesn't
# parse it — agents pick whatever format works.
_AGENT_MEMORY_PATH = "workspace/.trove/agent.md"

# How many recent files to surface in `bootstrap()`.
_BOOTSTRAP_RECENT_COUNT = 10


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


def _parse_bytes_content(resp: httpx.Response) -> BytesContent:
    """Wrap a /files/{path} response with its truncation headers."""
    raw_size = resp.headers.get("X-Trove-Size")
    try:
        size = int(raw_size) if raw_size is not None else None
    except ValueError:
        size = None
    return BytesContent(
        content=resp.content,
        truncated=resp.headers.get("X-Trove-Truncated") == "1",
        size_bytes=size,
    )


_raise_for = raise_for_response  # back-compat alias for any downstream that imported it


def _join_chain(commands: Sequence[str]) -> str:
    """Join a sequence of shell commands with ``&&`` for a single exec invocation.

    Each command is wrapped in ``{ ... ; }`` (a brace group, not a subshell)
    so its parse boundary is unambiguous — an internal ``&&`` / ``||`` won't
    swallow the join — while ``cd`` / ``export`` / shell variables still
    persist across steps. Subshells (``( ... )``) would isolate that state
    and silently break the multi-step flow that ``exec_chain`` is for.
    Empty / whitespace-only entries are rejected so a stray blank string
    can't short-circuit the chain.
    """
    if not commands:
        raise ValueError("exec_chain requires at least one command")
    parts = []
    for i, cmd in enumerate(commands):
        if not isinstance(cmd, str) or not cmd.strip():
            raise ValueError(f"exec_chain command at index {i} is empty")
        parts.append("{ " + cmd + "; }")
    return " && ".join(parts)


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
        self._namespace = namespace
        self._http = httpx.Client(
            base_url=base_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "X-Namespace": namespace,
            },
            timeout=timeout,
        )

    def exec(self, command: str, *, stdin: str | None = None) -> str:
        """Run a shell command and return its stdout.

        Convenience wrapper around :meth:`exec_detailed` for the common
        "happy path returns text" case. Raises :class:`TroveExecError` on
        non-zero exit (the exception carries `exit_code`, `stdout`, and
        `stderr`), so a failed command never silently looks like a string
        of normal output.

        Use :meth:`exec_detailed` directly when you want to branch on
        exit codes without the exception, or :meth:`exec_chain` when
        multiple steps need to share `cwd` / shell variables.

        ``stdin`` is piped to the spawned shell as UTF-8. None == /dev/null.
        Server caps the payload at 1 MB; for binary input, ``upload`` then
        redirect from disk in the command string (``"jq . < workspace/x"``).
        """
        result = self.exec_detailed(command, stdin=stdin)
        if result.exit_code != 0:
            raise TroveExecError(
                command=command,
                exit_code=result.exit_code,
                stdout=result.stdout,
                stderr=result.stderr,
            )
        return result.stdout

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

    def exec_chain(
        self, commands: Sequence[str], *, stdin: str | None = None
    ) -> ExecResult:
        """Run a list of commands as one chained shell invocation.

        Each ``exec`` call gets a fresh shell, so a ``cd`` (or any state set
        inside the command) doesn't carry over to the next call. ``exec_chain``
        joins the commands with ``&&`` and runs them as a single exec, so
        ``cd`` / ``export`` / variables persist *within* the chain. The chain
        short-circuits on the first non-zero exit, just like shell ``&&``.

        For setup that should apply to *every* call (a venv, a default cwd),
        use :meth:`set_init` instead — its prelude lives in the namespace
        volume and survives across calls and process restarts.

        The same 30-second wall-clock timeout applies to the whole chain.
        For long-running multi-step workflows, write progress to files so a
        retry can resume.
        """
        return self.exec_detailed(_join_chain(commands), stdin=stdin)

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

    def set_init(self, text: str) -> FileResult:
        """Set the namespace's persistent shell init script.

        Writes ``workspace/.trove/init.sh``. The exec endpoint sources this
        file before every command, so cwd, env vars, and venv activation
        survive across calls — and across agent process restarts, since it
        lives in the namespace volume.

        Example::

            client.set_init("cd workspace/data\\nsource .venv/bin/activate\\n")
            client.exec("python script.py")  # runs in workspace/data, venv active

        Requires a server that honors the init.sh convention; older servers
        will store the file but not source it.
        """
        return self.write(_INIT_PATH, text)

    def get_init(self) -> str | None:
        """Read the persistent shell init script, or None if unset."""
        try:
            return self.read_text(_INIT_PATH)
        except TroveError as e:
            if e.status_code == 404:
                return None
            raise

    def clear_init(self) -> bool:
        """Delete the init script. Returns True if it existed, False otherwise."""
        try:
            self.delete(_INIT_PATH)
            return True
        except TroveError as e:
            if e.status_code == 404:
                return False
            raise

    def bootstrap(self) -> WorkspaceBootstrap:
        """One-call orientation packet for an agent picking up the namespace.

        Returns a :class:`WorkspaceBootstrap` rolling up:

        - file count + most-recently-modified files (top 10 by ``modified_at``,
          ``.trove/`` excluded so its metadata files don't crowd out real work)
        - the active ``init.sh`` text, if any
        - the ``agent.md`` handoff note from the previous session, if any

        Composed from existing endpoints — no new server contract — so the
        same call works against any server version. Costs a small handful
        of HTTP round-trips; meant to be called once per session, not in
        a loop. Pipe :meth:`WorkspaceBootstrap.as_system_prompt_block` into
        the model's system message so it orients before its first tool call.

        **Convention**: ``workspace/.trove/agent.md`` is the cross-session
        handoff slot. To leave a note for the next instance, the agent writes
        it with the normal :meth:`write` (free-form markdown — the runtime
        doesn't parse it). ``bootstrap`` surfaces it as ``agent_memory``.
        """
        listing = self.list_dir("workspace/", recursive=True)
        files = [f for f in listing if not f.is_dir and not f.path.startswith("workspace/.trove/")]
        files.sort(key=lambda f: f.modified_at, reverse=True)
        recent = files[:_BOOTSTRAP_RECENT_COUNT]
        try:
            agent_memory: str | None = self.read_text(_AGENT_MEMORY_PATH)
        except TroveError as e:
            if e.status_code != 404:
                raise
            agent_memory = None
        return WorkspaceBootstrap(
            namespace=self._namespace,
            file_count=len(files),
            last_modified_at=files[0].modified_at if files else None,
            recent_files=recent,
            init_script=self.get_init(),
            agent_memory=agent_memory,
            truncated=listing.truncated,
        )

    def list_dir(self, path: str = "workspace/", *, recursive: bool = False) -> ListResult:
        """List a directory in the workspace. Directories first, then files, alphabetical.

        Pass ``recursive=True`` to get all descendants in one call (depth-first,
        max 1000 entries, max 20 levels). The ``is_dir`` field lets callers
        distinguish files from intermediate directories in the flat list.

        Returns a :class:`ListResult` (a ``list[FileInfo]`` subclass with a
        ``truncated`` attribute). Iteration, indexing, and ``len()`` work
        exactly like a list; check ``result.truncated`` to know whether the
        server-side cap was hit.
        """
        params: dict = {"path": path}
        if recursive:
            params["recursive"] = "true"
        resp = self._http.get("/v1/files", params=params)
        _raise_for(resp)
        body = resp.json()
        return ListResult(
            (_parse_file_info(e) for e in body.get("entries", [])),
            truncated=bool(body.get("truncated", False)),
        )

    def read(self, path: str) -> str | bytes:
        """Read a file, returning ``str`` for UTF-8 text or ``bytes`` for binary.

        Hits ``/v1/files/content`` first to detect the encoding. For text the
        content comes back in that one response; for binary a second request
        to ``/files/{path}`` fetches the raw bytes. So: one round-trip for
        text, two for binary. Prefer :meth:`read_text` / :meth:`read_bytes`
        when the type is known up front.
        """
        info = self.read_file(path)
        if info.encoding == "binary":
            return self.read_bytes(path)
        return info.content or ""

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
        For files that may exceed the cap, use :meth:`read_bytes_full` to
        also receive the truncation flag and full file size.
        """
        resp = self._http.get(f"/files/{_norm_path(path)}")
        _raise_for(resp)
        return resp.content

    def read_bytes_full(self, path: str) -> BytesContent:
        """Download a file's raw bytes plus truncation metadata.

        Reads the ``X-Trove-Truncated`` and ``X-Trove-Size`` response
        headers so callers can detect when the 100 MB cap was hit and act
        on the full file size.
        """
        resp = self._http.get(f"/files/{_norm_path(path)}")
        _raise_for(resp)
        return _parse_bytes_content(resp)

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
        self._namespace = namespace
        self._http = httpx.AsyncClient(
            base_url=base_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "X-Namespace": namespace,
            },
            timeout=timeout,
        )

    async def exec(self, command: str, *, stdin: str | None = None) -> str:
        """Run a shell command and return its stdout.

        Async mirror of :meth:`TroveClient.exec`. Raises
        :class:`TroveExecError` on non-zero exit.
        """
        result = await self.exec_detailed(command, stdin=stdin)
        if result.exit_code != 0:
            raise TroveExecError(
                command=command,
                exit_code=result.exit_code,
                stdout=result.stdout,
                stderr=result.stderr,
            )
        return result.stdout

    async def exec_detailed(self, command: str, *, stdin: str | None = None) -> ExecResult:
        body: dict = {"command": command}
        if stdin is not None:
            body["stdin"] = stdin
        resp = await self._http.post("/v1/exec", json=body)
        _raise_for(resp)
        return _parse_exec_result(resp.json())

    async def exec_chain(
        self, commands: Sequence[str], *, stdin: str | None = None
    ) -> ExecResult:
        """Run a list of commands as one chained shell invocation.
        See :meth:`TroveClient.exec_chain`.
        """
        return await self.exec_detailed(_join_chain(commands), stdin=stdin)

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

    async def set_init(self, text: str) -> FileResult:
        """Set the namespace's persistent shell init script. See :meth:`TroveClient.set_init`."""
        return await self.write(_INIT_PATH, text)

    async def get_init(self) -> str | None:
        """Read the persistent shell init script, or None if unset."""
        try:
            return await self.read_text(_INIT_PATH)
        except TroveError as e:
            if e.status_code == 404:
                return None
            raise

    async def clear_init(self) -> bool:
        """Delete the init script. Returns True if it existed, False otherwise."""
        try:
            await self.delete(_INIT_PATH)
            return True
        except TroveError as e:
            if e.status_code == 404:
                return False
            raise

    async def bootstrap(self) -> WorkspaceBootstrap:
        """One-call orientation packet. See :meth:`TroveClient.bootstrap`.

        Fans the three constituent reads (recursive listing, init.sh,
        agent.md) out as concurrent tasks so the round-trip cost stays
        bounded by the slowest one — meaningful for the very first agent
        call where the user is watching.
        """
        import asyncio

        async def _read_agent_memory() -> str | None:
            try:
                return await self.read_text(_AGENT_MEMORY_PATH)
            except TroveError as e:
                if e.status_code == 404:
                    return None
                raise

        listing, init_script, agent_memory = await asyncio.gather(
            self.list_dir("workspace/", recursive=True),
            self.get_init(),
            _read_agent_memory(),
        )
        files = [f for f in listing if not f.is_dir and not f.path.startswith("workspace/.trove/")]
        files.sort(key=lambda f: f.modified_at, reverse=True)
        recent = files[:_BOOTSTRAP_RECENT_COUNT]
        return WorkspaceBootstrap(
            namespace=self._namespace,
            file_count=len(files),
            last_modified_at=files[0].modified_at if files else None,
            recent_files=recent,
            init_script=init_script,
            agent_memory=agent_memory,
            truncated=listing.truncated,
        )

    async def list_dir(self, path: str = "workspace/", *, recursive: bool = False) -> ListResult:
        params: dict = {"path": path}
        if recursive:
            params["recursive"] = "true"
        resp = await self._http.get("/v1/files", params=params)
        _raise_for(resp)
        body = resp.json()
        return ListResult(
            (_parse_file_info(e) for e in body.get("entries", [])),
            truncated=bool(body.get("truncated", False)),
        )

    async def read(self, path: str) -> str | bytes:
        """Read a file, returning ``str`` for UTF-8 text or ``bytes`` for binary.

        One round-trip for text, two for binary (encoding is detected on the
        first call to ``/v1/files/content``; binary triggers a follow-up to
        ``/files/{path}``). See :meth:`TroveClient.read`.
        """
        info = await self.read_file(path)
        if info.encoding == "binary":
            return await self.read_bytes(path)
        return info.content or ""

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

    async def read_bytes_full(self, path: str) -> BytesContent:
        resp = await self._http.get(f"/files/{_norm_path(path)}")
        _raise_for(resp)
        return _parse_bytes_content(resp)

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
