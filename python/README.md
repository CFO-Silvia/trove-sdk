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

`AsyncTroveAdminClient` mirrors the same interface with `async`/`await`.

### Errors

All errors raise `TroveError(message, status_code)`.
