# Per-session sandboxes

Real-world pattern: each agent run gets its own namespace and a throwaway scoped key.
Three actors, three keys, hard isolation between sessions.

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
  provision.py                  runtime.py               dashboard.py
  (admin key)                  (scoped key)            (unscoped key)
       │                            │                        │
       │  mints scoped key          │  hard-isolated to      │  reads across
       │  per session               │  one namespace          │  every namespace
       │                            │                        │
       └────────► session-abc123 ◄──┘                        │
                  session-xyz789  ◄────────────────────────► │
                  …
```

## Why three keys?

| Key | Where it lives | What it does | Why not a runtime key? |
|---|---|---|---|
| **Admin** | Backend secrets manager | Mint and revoke other keys | Mint/revoke needs `scope=admin`; runtime keys get 403 |
| **Scoped runtime** | The agent process for one session | Read/write its own namespace | One per session means one revoke instantly stops a runaway agent |
| **Unscoped runtime** | Backend ops jobs (billing, metrics) | Walk every namespace | Scoped keys can't see other tenants; admin keys can't touch the filesystem |

## Setup

```bash
cp .env.example .env
# Edit .env with your TROVE_WORKSPACE_ID, TROVE_ADMIN_KEY, TROVE_RUNTIME_KEY
# from https://trovefiles.dev/dashboard

pip install trove-sdk python-dotenv
```

## Walkthrough

```bash
# 1. Backend mints a scoped key for a new session
python provision.py start abc123
# → started session abc123 → session-abc123 (key key-...)

# 2. Agent process does its work in the sandbox
python runtime.py abc123 "summarize https://example.com"
# → [abc123] 47 words · snapshot snap-...

# 3. Ops dashboard rolls up all active sessions
python dashboard.py
# session                     files       size
# ----------------------------------------------
# abc123                          3        12K

# 4. Session ends — revoke the scoped key
python provision.py end abc123
# → ended session abc123
```

## Try the isolation

After `start abc123` and `start xyz789`, try editing `runtime.py` to point its
`TroveClient` at a different namespace than the one its key was scoped to:

```python
TroveClient(api_key=session["api_key"], namespace="session-xyz789")
```

Every request returns `403 — This key is scoped to namespace 'session-abc123'`.
That's the security boundary, enforced server-side regardless of what path the
agent tries to write.
