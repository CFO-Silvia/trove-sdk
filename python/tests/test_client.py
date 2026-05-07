import httpx
import pytest
import respx

from trove_sdk import (
    AsyncTroveClient,
    BytesContent,
    ListResult,
    TroveAuthError,
    TroveClient,
    TroveError,
    TroveNotFoundError,
    TroveRateLimitError,
    TroveServerError,
    TroveTimeoutError,
)
from trove_sdk.models import ExecResult, FileResult

BASE = "https://api.trovefiles.dev"


@respx.mock
def test_exec_returns_stdout():
    respx.post(f"{BASE}/exec").mock(return_value=httpx.Response(200, text="hello\n"))
    with TroveClient("trove-sk-test", "ns", base_url=BASE) as c:
        assert c.exec("echo hello") == "hello\n"


@respx.mock
def test_exec_raises_on_error():
    respx.post(f"{BASE}/exec").mock(
        return_value=httpx.Response(401, json={"detail": "Invalid API key"})
    )
    with TroveClient("trove-sk-bad", "ns", base_url=BASE) as c:
        with pytest.raises(TroveError) as exc_info:
            c.exec("echo hi")
    assert exc_info.value.status_code == 401


@respx.mock
def test_write_strips_workspace_prefix():
    route = respx.post(f"{BASE}/write").mock(
        return_value=httpx.Response(
            200, json={"path": "workspace/hello.txt", "size_bytes": 5}
        )
    )
    with TroveClient("trove-sk-test", "ns", base_url=BASE) as c:
        result = c.write("workspace/hello.txt", "hello")
    assert isinstance(result, FileResult)
    assert route.called
    sent = route.calls[0].request
    import json

    body = json.loads(sent.content)
    assert body["path"] == "hello.txt"


@respx.mock
def test_write_plain_path():
    route = respx.post(f"{BASE}/write").mock(
        return_value=httpx.Response(
            200, json={"path": "workspace/data.json", "size_bytes": 2}
        )
    )
    with TroveClient("trove-sk-test", "ns", base_url=BASE) as c:
        c.write("data.json", "{}")
    import json

    body = json.loads(route.calls[0].request.content)
    assert body["path"] == "data.json"


@respx.mock
def test_upload_strips_workspace_prefix():
    route = respx.put(f"{BASE}/files/image.png").mock(
        return_value=httpx.Response(
            200, json={"path": "workspace/image.png", "size_bytes": 3}
        )
    )
    with TroveClient("trove-sk-test", "ns", base_url=BASE) as c:
        result = c.upload("workspace/image.png", b"abc")
    assert route.called
    assert isinstance(result, FileResult)


@respx.mock
def test_delete():
    respx.post(f"{BASE}/delete").mock(
        return_value=httpx.Response(200, json={"deleted": "workspace/hello.txt"})
    )
    with TroveClient("trove-sk-test", "ns", base_url=BASE) as c:
        deleted = c.delete("workspace/hello.txt")
    assert deleted == "workspace/hello.txt"


@respx.mock
def test_context_manager_closes():
    respx.post(f"{BASE}/exec").mock(return_value=httpx.Response(200, text="ok"))
    client = TroveClient("trove-sk-test", "ns", base_url=BASE)
    with client as c:
        c.exec("echo ok")


# ── exec_detailed (structured /v1/exec) ───────────────────────────────────────


@respx.mock
def test_exec_detailed_returns_structured_result():
    respx.post(f"{BASE}/v1/exec").mock(
        return_value=httpx.Response(
            200,
            json={
                "exit_code": 7,
                "stdout": "on stdout\n",
                "stderr": "on stderr\n",
                "duration_ms": 42,
            },
        )
    )
    with TroveClient("trove-sk-test", "ns", base_url=BASE) as c:
        r = c.exec_detailed("some-cmd")
    assert isinstance(r, ExecResult)
    assert r.exit_code == 7
    assert r.stdout == "on stdout\n"
    assert r.stderr == "on stderr\n"
    assert r.duration_ms == 42


@respx.mock
def test_exec_detailed_tolerates_missing_stderr():
    # Server may omit empty fields; SDK should still produce a usable result.
    respx.post(f"{BASE}/v1/exec").mock(
        return_value=httpx.Response(
            200,
            json={
                "exit_code": 0,
                "stdout": "hi\n",
            },
        )
    )
    with TroveClient("trove-sk-test", "ns", base_url=BASE) as c:
        r = c.exec_detailed("echo hi")
    assert r.stderr == "" and r.duration_ms == 0


@respx.mock
def test_exec_detailed_propagates_408_timeout():
    respx.post(f"{BASE}/v1/exec").mock(
        return_value=httpx.Response(
            408, json={"detail": "Command timed out after 30s."}
        )
    )
    with TroveClient("trove-sk-test", "ns", base_url=BASE) as c:
        with pytest.raises(TroveError) as exc:
            c.exec_detailed("sleep 999")
    assert exc.value.status_code == 408


# ── stdin pass-through ────────────────────────────────────────────────────────

@respx.mock
def test_exec_detailed_omits_stdin_when_not_set():
    """No stdin == no `stdin` field on the wire. Important for old servers
    that might 422 on unknown fields if Pydantic config tightens later."""
    import json as _j
    route = respx.post(f"{BASE}/v1/exec").mock(
        return_value=httpx.Response(
            200, json={"exit_code": 0, "stdout": "", "stderr": "", "duration_ms": 1}
        )
    )
    with TroveClient("trove-sk-test", "ns", base_url=BASE) as c:
        c.exec_detailed("true")
    assert "stdin" not in _j.loads(route.calls[0].request.content)


@respx.mock
def test_exec_detailed_forwards_stdin():
    import json as _j
    route = respx.post(f"{BASE}/v1/exec").mock(
        return_value=httpx.Response(
            200,
            json={"exit_code": 0, "stdout": "5\n", "stderr": "", "duration_ms": 3},
        )
    )
    with TroveClient("trove-sk-test", "ns", base_url=BASE) as c:
        r = c.exec_detailed("wc -c", stdin="hello")
    body = _j.loads(route.calls[0].request.content)
    assert body == {"command": "wc -c", "stdin": "hello"}
    assert r.stdout == "5\n"


@respx.mock
def test_exec_legacy_forwards_stdin():
    """The legacy /exec endpoint also accepts stdin once the server is upgraded."""
    import json as _j
    route = respx.post(f"{BASE}/exec").mock(
        return_value=httpx.Response(200, text="ok\n")
    )
    with TroveClient("trove-sk-test", "ns", base_url=BASE) as c:
        c.exec("cat", stdin="payload")
    body = _j.loads(route.calls[0].request.content)
    assert body["stdin"] == "payload"


# ── exec_chain (multi-step shell flow in one exec) ───────────────────────────


@respx.mock
def test_exec_chain_joins_with_and_in_one_request():
    """Multi-step flows persist cwd/env *within* one exec — not across calls.
    The chain wraps each command in a brace group ``{ ... ; }`` and joins with
    ``&&`` so a stray internal ``||`` in one step can't swallow the next, while
    ``cd``/``export``/variables still persist (subshells would break that).
    """
    import json as _j
    route = respx.post(f"{BASE}/v1/exec").mock(
        return_value=httpx.Response(
            200,
            json={"exit_code": 0, "stdout": "ok\n", "stderr": "", "duration_ms": 12},
        )
    )
    with TroveClient("trove-sk-test", "ns", base_url=BASE) as c:
        result = c.exec_chain([
            "cd workspace/data",
            "source .venv/bin/activate",
            "python analyze.py",
        ])
    body = _j.loads(route.calls[0].request.content)
    assert body["command"] == (
        "{ cd workspace/data; } && { source .venv/bin/activate; } && { python analyze.py; }"
    )
    assert "stdin" not in body
    assert isinstance(result, ExecResult)
    assert result.exit_code == 0


@respx.mock
def test_exec_chain_forwards_stdin():
    import json as _j
    route = respx.post(f"{BASE}/v1/exec").mock(
        return_value=httpx.Response(
            200,
            json={"exit_code": 0, "stdout": "5\n", "stderr": "", "duration_ms": 1},
        )
    )
    with TroveClient("trove-sk-test", "ns", base_url=BASE) as c:
        c.exec_chain(["cat", "wc -c"], stdin="hello")
    body = _j.loads(route.calls[0].request.content)
    assert body["stdin"] == "hello"
    assert body["command"] == "{ cat; } && { wc -c; }"


@respx.mock
def test_exec_chain_single_command():
    """A one-element chain is still wrapped — keeps the call site shape stable."""
    import json as _j
    route = respx.post(f"{BASE}/v1/exec").mock(
        return_value=httpx.Response(
            200, json={"exit_code": 0, "stdout": "", "stderr": "", "duration_ms": 1}
        )
    )
    with TroveClient("trove-sk-test", "ns", base_url=BASE) as c:
        c.exec_chain(["true"])
    body = _j.loads(route.calls[0].request.content)
    assert body["command"] == "{ true; }"


def test_exec_chain_persists_cwd_and_vars_through_real_shell():
    """Run the joined string through a real bash. Subshell wrapping (the prior
    bug) silently dropped cwd/export/vars — this test catches a regression by
    actually executing the chain instead of just asserting its shape.
    """
    import shutil
    import subprocess
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not available")
    from trove_sdk.client import _join_chain
    cmd = _join_chain([
        "cd /tmp",
        'TOKEN="abc123"',
        'echo "$TOKEN $PWD"',
    ])
    out = subprocess.run([bash, "-c", cmd], capture_output=True, text=True, timeout=10)
    assert out.returncode == 0, out
    assert "abc123 /tmp" in out.stdout, out.stdout


def test_exec_chain_rejects_empty_list():
    with TroveClient("trove-sk-test", "ns", base_url=BASE) as c:
        with pytest.raises(ValueError):
            c.exec_chain([])


def test_exec_chain_rejects_blank_command():
    with TroveClient("trove-sk-test", "ns", base_url=BASE) as c:
        with pytest.raises(ValueError):
            c.exec_chain(["echo hi", "   "])


@respx.mock
async def test_async_exec_chain_joins_with_and():
    import json as _j
    route = respx.post(f"{BASE}/v1/exec").mock(
        return_value=httpx.Response(
            200,
            json={"exit_code": 0, "stdout": "", "stderr": "", "duration_ms": 1},
        )
    )
    async with AsyncTroveClient("trove-sk-test", "ns", base_url=BASE) as c:
        await c.exec_chain(["cd workspace", "ls"])
    body = _j.loads(route.calls[0].request.content)
    assert body["command"] == "{ cd workspace; } && { ls; }"


# ── read_bytes (binary download) ──────────────────────────────────────────────


@respx.mock
def test_read_bytes_returns_raw_content_and_strips_workspace_prefix():
    payload = bytes(range(256))  # all byte values — catches any text decoding
    route = respx.get(f"{BASE}/files/img.png").mock(
        return_value=httpx.Response(200, content=payload)
    )
    with TroveClient("trove-sk-test", "ns", base_url=BASE) as c:
        got = c.read_bytes("workspace/img.png")
    assert route.called
    assert got == payload


@respx.mock
def test_read_bytes_404_raises_trove_error():
    respx.get(f"{BASE}/files/missing.png").mock(
        return_value=httpx.Response(
            404, json={"detail": "Path not found: workspace/missing.png"}
        )
    )
    with TroveClient("trove-sk-test", "ns", base_url=BASE) as c:
        with pytest.raises(TroveError) as exc:
            c.read_bytes("workspace/missing.png")
    assert exc.value.status_code == 404


# ── init.sh helpers (persistent shell context) ────────────────────────────────


@respx.mock
def test_set_init_writes_canonical_path():
    import json as _j
    route = respx.post(f"{BASE}/write").mock(
        return_value=httpx.Response(
            200, json={"path": "workspace/.trove/init.sh", "size_bytes": 12}
        )
    )
    with TroveClient("trove-sk-test", "ns", base_url=BASE) as c:
        result = c.set_init("cd workspace\n")
    body = _j.loads(route.calls[0].request.content)
    assert body["path"] == ".trove/init.sh"
    assert body["content"] == "cd workspace\n"
    assert isinstance(result, FileResult)


@respx.mock
def test_get_init_returns_text_when_present():
    respx.get(f"{BASE}/v1/files/content").mock(
        return_value=httpx.Response(
            200,
            json={
                "path": "workspace/.trove/init.sh",
                "size_bytes": 13,
                "modified_at": "2026-05-06T20:00:00Z",
                "encoding": "utf-8",
                "content": "cd workspace\n",
            },
        )
    )
    with TroveClient("trove-sk-test", "ns", base_url=BASE) as c:
        assert c.get_init() == "cd workspace\n"


@respx.mock
def test_get_init_returns_none_when_absent():
    respx.get(f"{BASE}/v1/files/content").mock(
        return_value=httpx.Response(
            404, json={"detail": "Path not found: workspace/.trove/init.sh"}
        )
    )
    with TroveClient("trove-sk-test", "ns", base_url=BASE) as c:
        assert c.get_init() is None


@respx.mock
def test_get_init_propagates_non_404_errors():
    respx.get(f"{BASE}/v1/files/content").mock(
        return_value=httpx.Response(401, json={"detail": "Invalid API key"})
    )
    with TroveClient("trove-sk-bad", "ns", base_url=BASE) as c:
        with pytest.raises(TroveError) as exc:
            c.get_init()
    assert exc.value.status_code == 401


@respx.mock
def test_clear_init_returns_true_when_deleted():
    respx.post(f"{BASE}/delete").mock(
        return_value=httpx.Response(
            200, json={"deleted": "workspace/.trove/init.sh"}
        )
    )
    with TroveClient("trove-sk-test", "ns", base_url=BASE) as c:
        assert c.clear_init() is True


@respx.mock
def test_clear_init_returns_false_when_absent():
    respx.post(f"{BASE}/delete").mock(
        return_value=httpx.Response(
            404, json={"detail": "Path not found: workspace/.trove/init.sh"}
        )
    )
    with TroveClient("trove-sk-test", "ns", base_url=BASE) as c:
        assert c.clear_init() is False


# ── error subclasses ─────────────────────────────────────────────────────────


@respx.mock
@pytest.mark.parametrize(
    "status,cls",
    [
        (401, TroveAuthError),
        (403, TroveAuthError),
        (404, TroveNotFoundError),
        (408, TroveTimeoutError),
        (429, TroveRateLimitError),
        (504, TroveTimeoutError),
        (500, TroveServerError),
        (502, TroveServerError),
    ],
)
def test_status_codes_raise_specific_subclass(status, cls):
    respx.post(f"{BASE}/exec").mock(
        return_value=httpx.Response(status, json={"detail": "boom"})
    )
    with TroveClient("trove-sk-test", "ns", base_url=BASE) as c:
        with pytest.raises(cls) as exc:
            c.exec("nope")
    # Every subclass still satisfies `except TroveError` for back-compat.
    assert isinstance(exc.value, TroveError)
    assert exc.value.status_code == status


# ── list_dir truncation ──────────────────────────────────────────────────────


@respx.mock
def test_list_dir_returns_listresult_with_truncated_flag():
    respx.get(f"{BASE}/v1/files").mock(
        return_value=httpx.Response(
            200,
            json={
                "entries": [
                    {"name": "a.txt", "path": "workspace/a.txt", "is_dir": False,
                     "size_bytes": 3, "modified_at": "2026-05-06T20:00:00Z"},
                ],
                "truncated": True,
            },
        )
    )
    with TroveClient("trove-sk-test", "ns", base_url=BASE) as c:
        result = c.list_dir("workspace/")
    # Behaves like a list (existing callers keep working).
    assert isinstance(result, list)
    assert isinstance(result, ListResult)
    assert len(result) == 1
    assert result[0].name == "a.txt"
    # New affordance.
    assert result.truncated is True


@respx.mock
def test_list_dir_truncated_defaults_to_false():
    respx.get(f"{BASE}/v1/files").mock(
        return_value=httpx.Response(200, json={"entries": []})
    )
    with TroveClient("trove-sk-test", "ns", base_url=BASE) as c:
        result = c.list_dir("workspace/")
    assert result.truncated is False


# ── read() unified text/bytes ────────────────────────────────────────────────


@respx.mock
def test_read_returns_str_for_utf8_file():
    respx.get(f"{BASE}/v1/files/content").mock(
        return_value=httpx.Response(
            200,
            json={
                "path": "workspace/n.txt",
                "size_bytes": 5,
                "modified_at": "2026-05-06T20:00:00Z",
                "encoding": "utf-8",
                "content": "hello",
            },
        )
    )
    with TroveClient("trove-sk-test", "ns", base_url=BASE) as c:
        got = c.read("workspace/n.txt")
    assert got == "hello"
    assert isinstance(got, str)


@respx.mock
def test_read_returns_bytes_for_binary_file():
    payload = bytes(range(64))
    respx.get(f"{BASE}/v1/files/content").mock(
        return_value=httpx.Response(
            200,
            json={
                "path": "workspace/img.png",
                "size_bytes": 64,
                "modified_at": "2026-05-06T20:00:00Z",
                "encoding": "binary",
                "content": None,
            },
        )
    )
    respx.get(f"{BASE}/files/img.png").mock(
        return_value=httpx.Response(200, content=payload)
    )
    with TroveClient("trove-sk-test", "ns", base_url=BASE) as c:
        got = c.read("workspace/img.png")
    assert got == payload
    assert isinstance(got, bytes)


# ── read_bytes_full (truncation flag + full size) ────────────────────────────


@respx.mock
def test_read_bytes_full_surfaces_truncation_headers():
    payload = b"\x00\x01\x02\x03"
    respx.get(f"{BASE}/files/big.bin").mock(
        return_value=httpx.Response(
            200,
            content=payload,
            headers={"X-Trove-Truncated": "1", "X-Trove-Size": "104857600"},
        )
    )
    with TroveClient("trove-sk-test", "ns", base_url=BASE) as c:
        result = c.read_bytes_full("workspace/big.bin")
    assert isinstance(result, BytesContent)
    assert result.content == payload
    assert result.truncated is True
    assert result.size_bytes == 104857600


@respx.mock
def test_read_bytes_full_when_not_truncated():
    respx.get(f"{BASE}/files/small.bin").mock(
        return_value=httpx.Response(200, content=b"hi")
    )
    with TroveClient("trove-sk-test", "ns", base_url=BASE) as c:
        result = c.read_bytes_full("workspace/small.bin")
    assert result.truncated is False
    assert result.size_bytes is None
    assert result.content == b"hi"


@respx.mock
async def test_async_init_round_trip():
    import json as _j
    write_route = respx.post(f"{BASE}/write").mock(
        return_value=httpx.Response(
            200, json={"path": "workspace/.trove/init.sh", "size_bytes": 4}
        )
    )
    respx.get(f"{BASE}/v1/files/content").mock(
        return_value=httpx.Response(
            200,
            json={
                "path": "workspace/.trove/init.sh",
                "size_bytes": 5,
                "modified_at": "2026-05-06T20:00:00Z",
                "encoding": "utf-8",
                "content": "cd /\n",
            },
        )
    )
    respx.post(f"{BASE}/delete").mock(
        return_value=httpx.Response(
            200, json={"deleted": "workspace/.trove/init.sh"}
        )
    )
    async with AsyncTroveClient("trove-sk-test", "ns", base_url=BASE) as c:
        await c.set_init("cd /\n")
        assert await c.get_init() == "cd /\n"
        assert await c.clear_init() is True
    body = _j.loads(write_route.calls[0].request.content)
    assert body["path"] == ".trove/init.sh"
