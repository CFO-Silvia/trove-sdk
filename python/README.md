# trove-sdk · Python

Python client for [Trove](https://trovefiles.dev) — managed POSIX filesystem for AI agents.

## Installation

```bash
pip install trove-sdk
# or with the CLI:
pip install 'trove-sdk[cli]'
# or
uv add 'trove-sdk[cli]'
```

Requires Python 3.10+.

## CLI

A `trove` command ships in the `[cli]` extra. After installing, log in once and
then drive your workspace from the terminal:

```bash
# One-time setup. The CLI calls /v1/me to discover your workspace_id from
# the key, so you only paste one secret. --namespace is optional.
trove login --api-key trove-sk-... --namespace alice

# Save under a non-default profile name:
trove login --save-as staging --api-key trove-sk-...
trove --profile staging tail        # use it later

# Filesystem (mirrors the SDK)
trove run "ls workspace/"          # POST /v1/exec  (exit code propagates!)
trove run --json "build"           # one JSON line: {exit_code,stdout,stderr,...}
echo '{"x":1}' | trove run "jq .x" # piped stdin auto-forwards (1 MB cap)
trove ls workspace/                # GET  /v1/files
trove cat workspace/notes.txt      # GET  /v1/files/content
trove put report.pdf workspace/    # PUT  /files/{path}
trove get workspace/img.png        # GET  /files/{path}  (binary-safe)
trove write workspace/n.txt "hi"   # POST /write
trove rm workspace/old.txt         # POST /delete

# Diagnostics: "why is this CLI hitting the wrong tenant?"
trove doctor                       # version, profile, env, live /v1/me ping

# Activity log (the killer dev flow)
trove tail                         # long-poll the event feed
trove tail -t exec.completed -v    # only exec events, full command + first stdout line
trove events list --since 1h30m    # paged replay (compound durations + ISO timestamps OK)

# Multi-tenant key & webhook management (admin scope required)
trove keys list
trove keys create alice --namespace alice
trove keys revoke key-abc123
trove webhooks create https://api.example.com/trove/events
trove webhooks test wh-xyz

# Snapshots
trove snapshot create --label "before refactor"
trove snapshot list
trove snapshot restore snap-abc123
```

`whoami` shows the active key's scope and namespace lock so you don't accidentally
point a customer-scoped key at someone else's namespace:

```bash
$ trove whoami
profile         : default
workspace       : ws-abc123...
scope           : workspace
namespace lock  : alice  (key is scoped — cannot access other namespaces)
```

### Profiles & env vars

* `--profile staging` switches between saved logins.
* `TROVE_API_KEY` + `TROVE_WORKSPACE_ID` (and optional `TROVE_NAMESPACE`,
  `TROVE_BASE_URL`) override the saved profile when no `--profile` is set.
* Per-command `-n/--namespace` beats both.

### Output

Event timestamps render in your local timezone. Today's events show
`HH:MM:SS`; older events get an `MM-DD ` prefix so the log doesn't look
stuck in a single day. `--json` mode preserves the raw ISO strings for
piping into `jq` or downstream tools.

## Usage

### Filesystem operations

```python
from trove_sdk import TroveClient

with TroveClient(api_key="trove-sk-...", namespace="alice") as client:
    # Run shell commands
    client.exec("mkdir -p workspace/data")
    output = client.exec("ls workspace/")

    # Structured exec for agent loops — separate stdout/stderr + exit code.
    result = client.exec_detailed("pytest tests/")
    if result.exit_code != 0:
        print("failures on stderr:", result.stderr)

    # Read a text file (1 MB cap; raises on binary).
    notes = client.read_text("workspace/data/notes.txt")

    # Read a binary file (100 MB cap, no encoding).
    png = client.read_bytes("workspace/data/image.png")

    # List a directory.
    for entry in client.list_dir("workspace/data/"):
        print(entry.name, entry.size_bytes)

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
`snapshot.created`, `snapshot.restored`, `snapshot.deleted`,
`namespace.deleted`, `workspace.created`, `key.created`, `key.revoked`,
`webhook.test`. Pass `events=["*"]` (or omit) to subscribe to all of them,
including future ones.

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
| `exec(command, *, stdin=None)` | Run a shell command. Returns stdout as a string (legacy text response). |
| `exec_detailed(command, *, stdin=None)` | Run a shell command. Returns `ExecResult(exit_code, stdout, stderr, duration_ms)`. |
| `write(path, content)` | Write a UTF-8 text file. Returns `FileResult`. |
| `upload(path, data)` | Upload bytes or a file-like object. Returns `FileResult`. |
| `read_text(path)` | Read a UTF-8 text file (1 MB cap). Raises `TroveError` on binary content. |
| `read_bytes(path)` | Download a file's raw bytes (100 MB cap). Binary-safe. |
| `read_file(path)` | Read metadata + content. Returns `FileContent` (`encoding` field flags binary). |
| `list_dir(path)` | List a directory. Returns `list[FileInfo]`. |
| `delete(path)` | Delete a file or directory. Returns the deleted path. |
| `create_snapshot(label?)` | Tar the namespace and store it. Returns `Snapshot`. |
| `list_snapshots()` | List snapshots newest-first. Returns `list[Snapshot]`. |
| `restore_snapshot(id)` | Wipe the namespace and restore. Returns # files restored. |
| `delete_snapshot(id)` | Delete a snapshot from S3. |

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
