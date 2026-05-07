import httpx
import pytest
import respx

from trove_sdk import AsyncTroveClient, TroveClient, TroveError
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
