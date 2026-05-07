# trove-sdk · Python

Python client for [Trove](https://trovefiles.dev) — files and commands for AI agents. Persistent storage that survives every session, isolated per customer, with real Unix tools (awk, jq, pdftotext, ffmpeg) preinstalled.

## Installation

```bash
pip install trove-sdk
# or with the CLI:
pip install 'trove-sdk[cli]'
# or with the MCP server (Claude Desktop, Cursor, Claude Code):
pip install 'trove-sdk[cli,mcp]'
# or
uv add 'trove-sdk[cli,mcp]'
```

Requires Python 3.10+.

## Use Trove from Claude Desktop / Cursor / Claude Code

Trove ships an MCP server. After logging in, one command wires it into every
detected client — no JSON editing.

```bash
pip install 'trove-sdk[cli,mcp]'
trove login                             # opens your browser to authorize
trove mcp install                       # every detected client
# or scope it: --client claude-desktop, --client cursor, --client claude-code
```

Want a non-default namespace? Add `--namespace my-project` to either
`trove login` or `trove mcp install`. CI / headless boxes can pass
`--api-key trove-sk-...` to skip the browser.

Restart the client and your agent gets three tools:

| Tool | What it does |
|---|---|
| `trove_exec(command, stdin?)` | Run any shell command in your workspace. `jq`, `awk`, `pdftotext`, `ffmpeg`, `python3`, etc. preinstalled. |
| `trove_read(path)` | Read a UTF-8 text file (1 MB cap). |
| `trove_write(path, content)` | Write a UTF-8 text file. |
| `trove_put_base64(path, content_b64)` | Write a binary file (PDF, image, audio) from base64 — saves the `base64 -d` shell dance. |

`trove mcp status` shows which clients are wired up; `trove mcp uninstall`
removes the entry. The MCP server reads `TROVE_API_KEY` / `TROVE_NAMESPACE`
from the env block written into the client's config — point at a different
namespace by re-running `install` with `-n <ns>`.

## Multi-tenant agent isolation (three-key pattern)

If you're running an agent product where each end-user gets their own
sandbox, this is the pattern you want. **One namespace per session, one
scoped key per session, hard isolation enforced server-side.**

```
                       ┌─────────────────────────────────────┐
                       │ secrets manager                     │
                       │   TROVE_ADMIN_KEY     (scope:admin) │
                       │   TROVE_RUNTIME_KEY   (unscoped)    │
                       └─────────────────────────────────────┘
                                    │
       ┌────────────────────────────┼────────────────────────┐
       │                            │                        │
       ▼                            ▼                        ▼
   provisioner                  agent runtime            ops dashboard
   (admin key)                 (scoped key)            (unscoped key)
       │                            │                        │
       │  mints scoped key          │  hard-isolated to      │  reads across
       │  per session               │  one namespace          │  every namespace
       │                            │                        │
       └────────► session-abc123 ◄──┘                        │
                  session-xyz789  ◄────────────────────────► │
```

| Key | Where it lives | What it does | Why not just one key? |
|---|---|---|---|
| **Admin** | Backend secrets manager | Mint and revoke session keys | Mint/revoke needs `scope=admin`; runtime keys get 403 |
| **Scoped runtime** | Agent process for one session | Read/write its own namespace only | One per session means one revoke instantly stops a runaway agent |
| **Unscoped runtime** | Backend ops jobs (billing, metrics) | Walk every namespace | Scoped keys can't see other tenants; admin keys can't touch the filesystem |

```python
# Backend — provision a session
from trove_sdk import TroveAdminClient

with TroveAdminClient(api_key=ADMIN_KEY, workspace_id=WS_ID) as admin:
    key = admin.create_key(f"session-{user_id}", namespace=f"session-{user_id}")
    # Hand key.api_key to the agent runtime — it can ONLY touch this namespace.

# Agent runtime — single-session
from trove_sdk import TroveClient

with TroveClient(api_key=session_key, namespace=f"session-{user_id}") as fs:
    fs.exec("...")   # confined to session-{user_id}/
    # Pointing at a different namespace returns 403 — the key is scoped.

# Session ends — revoke the scoped key
admin.revoke_key(key.key_id)
```

A complete runnable example (provisioner + runtime + dashboard) lives in
[`examples/sessions/`](examples/sessions/) — copy it as a starting point.

## CLI

A `trove` command ships in the `[cli]` extra. After installing, log in once and
then drive your workspace from the terminal:

```bash
# One-time setup — opens your browser, prints a short code to confirm,
# approve in the dashboard, and a freshly-minted key lands in
# ~/.trove/config.json. No paste-back.
trove login

# Skip the browser (CI / headless):
trove login --api-key trove-sk-...     # explicit key
echo $TROVE_KEY | trove login          # piped from stdin
trove login --no-browser               # paste at the prompt

# Save under a non-default profile name:
trove login --save-as staging
trove --profile staging tail           # use it later

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

# MCP server (Claude Desktop, Cursor, Claude Code)
trove mcp install                  # detects clients, writes a 'trove' server entry
trove mcp install --client cursor --namespace alice
trove mcp status                   # which clients have it wired up
trove mcp uninstall
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

### What persists between exec calls

Each `exec` runs in a fresh shell. The **filesystem** is the only thing that
carries between calls — anything that lives only in shell state is gone.

| Persists across exec calls | Doesn't persist |
|---|---|
| Files in `workspace/` | `cwd` from a prior `cd` |
| `init.sh` prelude (re-runs every call) | env vars `export`ed inside an exec |
| Snapshots | Background processes |
| | Activated venvs (use `init.sh` instead) |
| | Shell variables / functions defined inline |

Three rules of thumb:

1. **Deterministic setup goes in `init.sh`.** A default `cd`, a venv to
   activate, env vars that should be present every call. See below.
2. **Computed state goes in a file.** If one step produces a value the next
   step needs, write it to a file (`workspace/.cache/token`) and read it
   back. Don't `export FOO=$(...)` and expect FOO to exist next call.
3. **Multi-step flows that share state run in one `exec_chain`.** Joins
   commands with `&&` server-side, so `cd` / `export` / variables hold for
   the whole chain. The 30-second wall clock applies to the chain as a
   whole — for longer flows, write progress to files so a retry can resume.

```python
# Multi-step within one shell — cwd and variables hold:
client.exec_chain([
    "cd workspace/data",
    "TOKEN=$(curl -s https://api.example.com/token)",
    'curl -H "Authorization: $TOKEN" https://api.example.com/feed -o feed.json',
])

# Separate calls — TOKEN would be lost between them. Persist via a file:
client.exec("curl -s https://api.example.com/token > workspace/.token")
client.exec('curl -H "Authorization: $(cat workspace/.token)" ... -o feed.json')
```

### Persistent shell context (init.sh)

`init.sh` covers the "every command repeats the same setup" case — a default
`cd`, an activated venv, exported env vars. The server sources it before
every command, so the prelude survives across calls *and* across agent
process restarts (it lives in the namespace volume).

```python
# Without init.sh — every command repeats the setup
client.exec("cd workspace/data && source .venv/bin/activate && python analyze.py")
client.exec("cd workspace/data && source .venv/bin/activate && pytest tests/")

# With init.sh — set the prelude once
client.set_init("""
cd workspace/data
source .venv/bin/activate
""")
client.exec("python analyze.py")    # cwd, venv, env all carry over
client.exec("pytest tests/")        # same context — no re-setup

client.get_init()                   # → the script text, or None if unset
client.clear_init()                 # → True if removed, False if never set
```

It's just a file at `workspace/.trove/init.sh` — snapshots include it, webhook
events fire when it changes, namespace isolation holds. Each call still gets a
fresh shell; only the prelude carries over, not state from prior commands.
Errors in the prelude write to stderr but don't block the user command — but
**don't put `exit` in the script**: it kills the shell before your command runs.

> **`workspace/.trove/` is reserved.** Write only via `set_init` /
> `get_init` / `clear_init`; direct `write()` calls into that directory may
> be intercepted or rejected by future server versions.

Async clients have the same three methods: `await client.set_init(...)`,
`await client.get_init()`, `await client.clear_init()`.

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
| `exec_chain(commands, *, stdin=None)` | Run a list of commands in one shell, joined with `&&`. Returns `ExecResult`. Use when steps need to share `cwd` / variables. |
| `write(path, content)` | Write a UTF-8 text file. Returns `FileResult`. |
| `upload(path, data)` | Upload bytes or a file-like object. Returns `FileResult`. |
| `read(path)` | Read a file. Returns `str` for UTF-8 or `bytes` for binary. One round-trip for text, two for binary (encoding is detected on the first call). Prefer `read_text` / `read_bytes` when the type is known. |
| `read_text(path)` | Read a UTF-8 text file (1 MB cap). Raises `TroveError` on binary content. |
| `read_bytes(path)` | Download a file's raw bytes (100 MB cap). Binary-safe. |
| `read_bytes_full(path)` | Same as `read_bytes` but returns a `BytesContent(content, truncated, size_bytes)` so you can detect when the 100 MB cap was hit. |
| `read_file(path)` | Read metadata + content. Returns `FileContent` (`encoding` field flags binary). |
| `list_dir(path, *, recursive=False)` | List a directory. Returns `ListResult` — a `list[FileInfo]` subclass with a `.truncated` flag for when the server cap was hit. |
| `delete(path)` | Delete a file or directory. Returns the deleted path. |
| `set_init(text)` | Write `workspace/.trove/init.sh` — sourced before every `/exec` call. Returns `FileResult`. |
| `get_init()` | Read the init script. Returns the text, or `None` if unset. |
| `clear_init()` | Delete the init script. Returns `True` if it existed, `False` otherwise. |
| `create_snapshot(label?)` | Tar the namespace and store it. Returns `Snapshot`. |
| `list_snapshots()` | List snapshots newest-first. Returns `list[Snapshot]`. |
| `restore_snapshot(id)` | Wipe the namespace and restore. Returns # files restored. |
| `delete_snapshot(id)` | Delete a snapshot from S3. |

`AsyncTroveClient` mirrors the same interface with `async`/`await`.

### `TroveAdminClient(api_key, workspace_id, *, base_url?)`

Construct directly when you already know `workspace_id`, or call
`TroveAdminClient.from_api_key(api_key)` to discover it from `/v1/me`:

```python
admin = TroveAdminClient.from_api_key("trove-sk-admin-...")  # one secret, not two
```

| Method | Description |
|--------|-------------|
| `from_api_key(api_key)` *(classmethod)* | Discover `workspace_id` via `/v1/me` and return a constructed client. |
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

All errors raise `TroveError(message, status_code)`. Common HTTP statuses
also raise more specific subclasses so retry/recovery logic doesn't have to
match on integers:

| Status | Class |
|--------|-------|
| 401, 403 | `TroveAuthError` |
| 404 | `TroveNotFoundError` |
| 408, 504 | `TroveTimeoutError` |
| 429 | `TroveRateLimitError` |
| 5xx | `TroveServerError` |

```python
from trove_sdk import TroveRateLimitError, TroveAuthError

try:
    client.exec("expensive-job")
except TroveRateLimitError:
    backoff_and_retry()
except TroveAuthError:
    refresh_session_key()
```

All five inherit from `TroveError`, so existing `except TroveError:` blocks
keep catching everything. `WebhookSignatureError` is a `TroveError` subclass
raised by `verify_webhook`.
