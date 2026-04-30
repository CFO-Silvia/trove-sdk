# trove-sdk

Client libraries for [Trove](https://trovefiles.dev) — a managed POSIX filesystem for AI agents.

| SDK | Install |
|-----|---------|
| [Python](./python/) | `pip install trove-sdk` |
| [Node.js](./node/) | `npm install trove-sdk` |

## What is Trove?

Trove gives your AI agent a persistent, cloud-backed filesystem it can use via shell commands — the same production-ready filesystem powering [Silvia](https://trovefiles.dev), available for your own app.

Agents interact through familiar POSIX commands (`ls`, `cat`, `grep`, `cp`, …). Your backend controls access through scoped API keys, so each customer gets an isolated namespace with no cross-tenant access.

## Quick example

```python
from trove_sdk import TroveClient

with TroveClient(api_key="trove-sk-...", namespace="alice") as client:
    client.exec("mkdir -p workspace/data")
    client.write("workspace/data/result.json", '{"score": 0.9}')
    print(client.exec("ls workspace/data/"))
```

```js
import { TroveClient } from 'trove-sdk'

const client = new TroveClient('trove-sk-...', 'alice')
await client.exec('mkdir -p workspace/data')
await client.write('workspace/data/result.json', '{"score": 0.9}')
```

## Multi-tenant key management

Issue one admin key from the [dashboard](https://trovefiles.dev/dashboard), then mint scoped keys per customer from your backend:

```python
from trove_sdk import TroveAdminClient

admin = TroveAdminClient(api_key="trove-sk-admin-...", workspace_id="ws-...")
key = admin.create_key("customer-alice", namespace="alice")
# hand key.api_key to alice — she can only access her namespace
```
