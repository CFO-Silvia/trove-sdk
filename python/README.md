# trove-sdk · Python

Python client for [Trove](https://trovefiles.dev) — managed POSIX filesystem for AI agents.

## Installation

```bash
pip install trove-sdk
# or
uv add trove-sdk
```

Requires Python 3.10+.

## Usage

### Filesystem operations

```python
from trove_sdk import TroveClient

with TroveClient(api_key="trove-sk-...", namespace="alice") as client:
    # Run shell commands
    client.exec("mkdir -p workspace/data")
    output = client.exec("ls workspace/")

    # Write a text file
    client.write("workspace/data/notes.txt", "hello world")

    # Upload binary
    with open("image.png", "rb") as f:
        client.upload("workspace/data/image.png", f)

    # Delete
    client.delete("workspace/data/notes.txt")
```

### Async

```python
from trove_sdk import AsyncTroveClient

async with AsyncTroveClient(api_key="trove-sk-...", namespace="alice") as client:
    await client.exec("echo hello")
    await client.write("workspace/hello.txt", "hi")
```

### Key management (multi-tenant)

Use an admin key from the dashboard to mint scoped keys per customer:

```python
from trove_sdk import TroveAdminClient

with TroveAdminClient(api_key="trove-sk-admin-...", workspace_id="ws-...") as admin:
    # Mint a scoped key for a customer
    key = admin.create_key("customer-alice", namespace="alice")
    print(key.api_key)  # store this — shown once

    # List active keys
    keys = admin.list_keys()

    # Revoke
    admin.revoke_key(key.key_id)
```

### Webhooks

Subscribe a URL to filesystem and auth events. Trove signs every delivery with
HMAC-SHA256; use `verify_webhook` to validate the signature in your receiver.

#### Register an endpoint

```python
from trove_sdk import TroveAdminClient

with TroveAdminClient(api_key="trove-sk-admin-...", workspace_id="ws-...") as admin:
    hook = admin.create_webhook(
        url="https://api.example.com/trove/events",
        events=["file.written", "file.deleted", "exec.completed"],
        # namespace="alice",  # optional — only fire for one customer
    )
    print(hook.signing_secret)  # save this — shown once
```

Available events: `file.written`, `file.deleted`, `exec.completed`,
`workspace.created`, `key.created`, `key.revoked`. Pass `events=["*"]`
(or omit) to subscribe to all of them, including future ones.

#### Receive an event (Flask)

```python
import os
from flask import Flask, request, abort
from trove_sdk import verify_webhook, WebhookSignatureError

app = Flask(__name__)
SECRET = os.environ["TROVE_WEBHOOK_SECRET"]

@app.post("/trove/events")
def receive():
    try:
        event = verify_webhook(
            secret=SECRET,
            body=request.get_data(),  # raw bytes — DO NOT use request.json
            signature_header=request.headers["X-Trove-Signature"],
        )
    except WebhookSignatureError:
        abort(400)
    print(f"{event.type}: {event.data}")
    return "", 204
```

The `body` argument MUST be the raw request bytes. Re-serializing JSON
(e.g. `json.dumps(request.json)`) reorders keys and invalidates the HMAC.

A minimal subscribe + verify script lives in
[`examples/webhook.py`](examples/webhook.py).

## API reference

### `TroveClient(api_key, namespace, *, base_url?)`

| Method | Description |
|--------|-------------|
| `exec(command)` | Run a shell command. Returns stdout as a string. |
| `write(path, content)` | Write a UTF-8 text file. Returns `FileResult`. |
| `upload(path, data)` | Upload bytes or a file-like object. Returns `FileResult`. |
| `delete(path)` | Delete a file or directory. Returns the deleted path. |

`AsyncTroveClient` mirrors the same interface with `async`/`await`.

### `TroveAdminClient(api_key, workspace_id, *, base_url?)`

| Method | Description |
|--------|-------------|
| `create_key(name, *, namespace?)` | Mint a new workspace key, optionally scoped to a namespace. |
| `list_keys()` | List all active keys for the workspace. |
| `revoke_key(key_id)` | Revoke a key immediately. |
| `create_webhook(url, *, events?, namespace?, description?)` | Subscribe a URL to events. Returns a `WebhookCreated` (signing secret shown once). |
| `list_webhooks()` | List all registered webhook endpoints. |
| `delete_webhook(webhook_id)` | Remove an endpoint. |
| `test_webhook(webhook_id)` | Fire a `webhook.test` event and return the delivery result. |

`AsyncTroveAdminClient` mirrors the same interface with `async`/`await`.

### `verify_webhook(*, secret, body, signature_header, tolerance_seconds=300)`

Validates a webhook delivery and returns the parsed `WebhookEvent`. Raises
`WebhookSignatureError` on bad signature, missing fields, or stale timestamp
(default tolerance: 5 minutes). Pass the raw request body — re-serialized JSON
will not match the signature.

### Errors

All errors raise `TroveError(message, status_code)`.
`WebhookSignatureError` is a subclass raised by `verify_webhook`.
