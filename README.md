# trove-sdk

Client libraries for [Trove](https://trovefiles.dev) — files and commands for AI agents.

| SDK | Install |
|-----|---------|
| [Python](./python/) | `pip install trove-sdk`  ·  `pip install 'trove-sdk[cli]'` for the `trove` CLI |
| [Node.js](./node/) | `npm install trove-sdk` |

## What is Trove?

Trove gives your AI agent a place to save files and run commands — the same persistent storage and shell environment powering [Silvia](https://trovefiles.dev), available for your own app.

Store any file type your agent touches: images for vision agents, PDFs for RAG pipelines, audio for transcription, CSVs for data agents. Agents interact through familiar shell commands (`ls`, `cat`, `grep`, `cp`, …) the model already knows. Your backend controls access through scoped API keys, so each customer gets an isolated namespace with no cross-tenant access.

## Quick example

```python
from trove_sdk import TroveClient

with TroveClient(api_key="trove-sk-...", namespace="alice") as client:
    # Upload an image, process it with shell tools
    with open("chart.png", "rb") as f:
        client.upload("workspace/chart.png", f)
    print(client.exec("identify workspace/chart.png"))

    # Write and run against any text format
    client.write("workspace/data.csv", "name,score\nalice,0.9")
    print(client.exec("awk -F, 'NR>1{print $2}' workspace/data.csv"))
```

```js
import { TroveClient } from 'trove-sdk'
import { readFile } from 'node:fs/promises'

const client = new TroveClient('trove-sk-...', 'alice')
const pdf = await readFile('report.pdf')
await client.upload('workspace/report.pdf', pdf)
await client.exec('pdftotext workspace/report.pdf -')
```

## Multi-tenant key management

Issue one admin key from the [dashboard](https://trovefiles.dev/dashboard), then mint scoped keys per customer from your backend:

```python
from trove_sdk import TroveAdminClient

admin = TroveAdminClient(api_key="trove-sk-admin-...", workspace_id="ws-...")
key = admin.create_key("customer-alice", namespace="alice")
# hand key.api_key to alice — she can only access her namespace
```
