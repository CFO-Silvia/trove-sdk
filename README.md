# trove-sdk

Client libraries for [Trove](https://trovefiles.dev) — files and commands for AI agents.

```bash
pip install trove-sdk                # client only
pip install 'trove-sdk[cli]'         # + the `trove` CLI
pip install 'trove-sdk[cli,mcp]'     # + MCP server for Claude Desktop, Cursor, Claude Code
```

Python 3.10+. See [`python/README.md`](./python/README.md) for the full SDK + CLI + MCP docs.

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

## Persistent shell context

Tired of prefixing every `exec` with `cd workspace/data && source .venv/bin/activate && ...`? Set the namespace's init script once — the exec endpoint sources it before every command, and it survives across agent process restarts because it lives in the namespace volume.

```python
client.set_init("""
cd workspace/data
source .venv/bin/activate
export REPORT_DATE=2026-05-06
""")

client.exec("python analyze.py")    # runs in workspace/data, venv active, env set
client.exec("pytest tests/")        # same context — no re-setup
client.get_init()                   # → the script text, or None if unset
client.clear_init()                 # remove it
```

Stored at `workspace/.trove/init.sh` — snapshots include it, events fire when it changes, namespace isolation holds. Requires a server that honors the convention; older servers will store the file but not source it.

## Multi-tenant key management

Issue one admin key from the [dashboard](https://trovefiles.dev/dashboard), then mint scoped keys per customer from your backend:

```python
from trove_sdk import TroveAdminClient

admin = TroveAdminClient(api_key="trove-sk-admin-...", workspace_id="ws-...")
key = admin.create_key("customer-alice", namespace="alice")
# hand key.api_key to alice — she can only access her namespace
```
