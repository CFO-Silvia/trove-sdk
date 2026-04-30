import pytest
import respx
import httpx

from trove_sdk import TroveClient, TroveError
from trove_sdk.models import FileResult


BASE = "https://api.trovefiles.dev"


@respx.mock
def test_exec_returns_stdout():
    respx.post(f"{BASE}/exec").mock(return_value=httpx.Response(200, text="hello\n"))
    with TroveClient("trove-sk-test", "ns", base_url=BASE) as c:
        assert c.exec("echo hello") == "hello\n"


@respx.mock
def test_exec_raises_on_error():
    respx.post(f"{BASE}/exec").mock(return_value=httpx.Response(401, json={"detail": "Invalid API key"}))
    with TroveClient("trove-sk-bad", "ns", base_url=BASE) as c:
        with pytest.raises(TroveError) as exc_info:
            c.exec("echo hi")
    assert exc_info.value.status_code == 401


@respx.mock
def test_write_strips_workspace_prefix():
    route = respx.post(f"{BASE}/write").mock(
        return_value=httpx.Response(200, json={"path": "workspace/hello.txt", "size_bytes": 5})
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
        return_value=httpx.Response(200, json={"path": "workspace/data.json", "size_bytes": 2})
    )
    with TroveClient("trove-sk-test", "ns", base_url=BASE) as c:
        c.write("data.json", "{}")
    import json
    body = json.loads(route.calls[0].request.content)
    assert body["path"] == "data.json"


@respx.mock
def test_upload_strips_workspace_prefix():
    route = respx.put(f"{BASE}/files/image.png").mock(
        return_value=httpx.Response(200, json={"path": "workspace/image.png", "size_bytes": 3})
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
