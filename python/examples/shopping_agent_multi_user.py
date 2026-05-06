"""Multi-user shopping agent with personalized memory — Trove namespace pattern.

WHAT THIS DEMONSTRATES
──────────────────────
One agent process serves many shoppers. Each shopper's long-term memory lives
in their own Trove namespace, and a shared `catalog` namespace holds the
read-only product data everyone reads from.

This is the pattern you'd use in a real multi-tenant agent product:

    • Shared, read-only data       →  one namespace (here: "catalog")
    • Each user's private memory   →  one namespace per user_id
    • Unlocked workspace key       →  one process, picks namespace per request

Compare the other examples:
    personal_assistant.py   — one user, namespace-locked key (max isolation).
    shopping_agent.py       — one shopper, throwaway namespace per session.

THE MEMORY MODEL
────────────────
Per-user files the agent reads/writes inside the user's namespace:

    workspace/profile.md         long-lived prefs (diet, sizes, allergies)
    workspace/notes.md           free-form running notes from past chats
    workspace/wishlist.md        things they want to buy later
    workspace/history.jsonl      one-line-per-purchase audit trail
    workspace/workflows.md       saved searches / repeat queries

Files in the SHARED catalog namespace, read by everyone:

    workspace/products.csv       the product catalog

THE DEMO
────────
We simulate two real shoppers — Alice and Bob — each having a first visit
where the agent learns about them, and a return visit where the agent recalls
what it knows. After the demo we print Alice's full memory tree to prove the
information is durable and isolated.

SETUP
─────
    pip install anthropic python-dotenv trove-sdk
    cp examples/.env.example examples/.env  # fill in TROVE_SHOPPING_KEY + ANTHROPIC_API_KEY
    python examples/shopping_agent_multi_user.py

SDK SURFACE USED
────────────────
    TroveClient(api_key, namespace)   construct one client per namespace
    client.write(path, content)       seed catalog and user files
    client.exec(command)              the agent's tool: bash on a namespace
    client.list_dir(path)             enumerate files in a memory tree
    client.read_text(path)            dump a memory file at the end
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import anthropic
from dotenv import load_dotenv

from trove_sdk import TroveClient

# Make print() safe for Windows consoles when Claude returns emoji/em-dashes.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

load_dotenv(Path(__file__).parent / ".env", override=True)

SHOPPING_KEY = os.environ["TROVE_SHOPPING_KEY"]
CATALOG_NS   = "catalog"

claude = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


# ── 1. One TroveClient per namespace, lazily made and cached ──────────────────
# Trove clients hold an HTTP session keyed to one namespace. We keep one for the
# shared catalog and one per user_id we've seen this run.
_clients: dict[str, TroveClient] = {}

def client_for(namespace: str) -> TroveClient:
    if namespace not in _clients:
        _clients[namespace] = TroveClient(api_key=SHOPPING_KEY, namespace=namespace)
    return _clients[namespace]


# ── 2. Seed the shared catalog (runs once per script invocation) ──────────────
def seed_catalog() -> None:
    catalog = client_for(CATALOG_NS)
    catalog.write("workspace/products.csv",
        "sku,name,category,price,vegan,nut_free,rating\n"
        "P-001,Oat Milk Chocolate Bar,snacks,4.99,yes,yes,4.6\n"
        "P-002,Honey-roasted Almonds,snacks,7.49,no,no,4.4\n"
        "P-003,Vegan Cheese Crackers,snacks,5.99,yes,yes,4.5\n"
        "P-004,Greek Yogurt Cups,dairy,3.49,no,yes,4.2\n"
        "P-005,Coconut Yogurt,dairy,4.79,yes,yes,4.3\n"
        "P-006,Wireless Earbuds,electronics,79.00,n/a,n/a,4.7\n"
        "P-007,Hiking Backpack 30L,outdoor,89.99,n/a,n/a,4.6\n"
        "P-008,Insulated Water Bottle,outdoor,24.99,n/a,n/a,4.5\n"
        "P-009,Compact Camp Stove,outdoor,49.99,n/a,n/a,4.4\n"
        "P-010,Vegan Protein Bar Pack,snacks,18.99,yes,yes,4.5\n"
    )
    print(f"[setup] seeded {CATALOG_NS}/workspace/products.csv\n")


# ── 3. Tools the agent calls — each routes to the right namespace ─────────────
# The agent always works on behalf of one user. We close over `user_ns` so the
# tool implementations always read/write the *current* shopper's memory.
def make_tools(user_ns: str):
    user_fs    = client_for(user_ns)
    catalog_fs = client_for(CATALOG_NS)

    def search_catalog(query: str) -> str:
        # Read-only across the shared catalog namespace.
        return catalog_fs.exec(f"grep -i {query!r} workspace/products.csv || echo '(no matches)'")

    def read_memory(path: str) -> str:
        # Tolerate missing files — fresh users have no memory yet.
        return user_fs.exec(f"cat {path} 2>/dev/null || echo '(no file yet)'")

    def write_memory(path: str, content: str) -> str:
        user_fs.write(path, content)
        return f"saved {path} ({len(content)} bytes)"

    def append_memory(path: str, line: str) -> str:
        # Append a single line — handy for notes.md and history.jsonl.
        line = line.rstrip("\n")
        user_fs.exec(f"mkdir -p workspace && printf '%s\\n' {line!r} >> {path}")
        return f"appended to {path}"

    def list_memory() -> str:
        return user_fs.exec("ls -la workspace/ 2>/dev/null || echo '(empty)'")

    return {
        "search_catalog":  search_catalog,
        "read_memory":     read_memory,
        "write_memory":    write_memory,
        "append_memory":   append_memory,
        "list_memory":     list_memory,
    }


TOOL_SCHEMAS = [
    {
        "name": "search_catalog",
        "description": "Search the shared product catalog. Returns matching CSV rows.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "case-insensitive grep over products.csv"}},
            "required": ["query"],
        },
    },
    {
        "name": "read_memory",
        "description": (
            "Read one of the user's memory files. Returns '(no file yet)' if it does not exist. "
            "Common paths: workspace/profile.md, workspace/notes.md, workspace/wishlist.md, "
            "workspace/history.jsonl, workspace/workflows.md."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "write_memory",
        "description": (
            "Overwrite a memory file with new content. Use this for files that should be "
            "fully rewritten each time (profile.md, wishlist.md, workflows.md). "
            "Do NOT use for notes.md or history.jsonl — those are append-only."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
        },
    },
    {
        "name": "append_memory",
        "description": (
            "Append one line to a memory file. Use this for notes.md (running observations) "
            "and history.jsonl (one JSON object per purchase or interaction)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "line": {"type": "string"}},
            "required": ["path", "line"],
        },
    },
    {
        "name": "list_memory",
        "description": "List everything currently stored in the user's memory tree.",
        "input_schema": {"type": "object", "properties": {}},
    },
]


SYSTEM_PROMPT = """\
You are a personalized shopping agent. You help the user find products and \
remember everything you learn about them so future visits feel personal.

You serve ONE user at a time. Their persistent memory lives in their namespace \
under workspace/. ALWAYS check what you already know before asking — start \
each conversation by calling list_memory() and reading any files that look \
relevant.

Memory file conventions:
  workspace/profile.md      Long-lived preferences (diet, allergies, sizes, brands).
                            Rewrite the whole file with write_memory when prefs change.
  workspace/notes.md        Free-form running notes — facts the user mentioned.
                            Append-only. One observation per line, prefixed with the date.
  workspace/wishlist.md     Items the user wants to consider later.
                            Rewrite the whole file when they add or remove items.
  workspace/history.jsonl   One JSON object per purchase: {"sku","name","price","date"}.
                            Append-only.
  workspace/workflows.md    Saved searches / queries the user wants to re-run.
                            Rewrite the whole file with write_memory.

Be concise. When you save something to memory, briefly tell the user what \
you saved so they know."""


# ── 4. Agent loop — one full turn = "keep calling tools until claude is done" ─
def chat(user_id: str, user_message: str, history: list[dict]) -> list[dict]:
    tools = make_tools(user_id)
    history = history + [{"role": "user", "content": user_message}]

    while True:
        response = claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            tools=TOOL_SCHEMAS,
            messages=history,
        )

        for block in response.content:
            if block.type == "text" and block.text.strip():
                print(f"[claude → {user_id}] {block.text.strip()}")
            elif block.type == "tool_use":
                args = ", ".join(f"{k}={v!r}" for k, v in block.input.items() if k != "content")
                print(f"[tool   ] {block.name}({args})")

        if response.stop_reason != "tool_use":
            history.append({"role": "assistant", "content": response.content})
            return history

        tool_uses = [b for b in response.content if b.type == "tool_use"]
        history.append({"role": "assistant", "content": response.content})
        history.append({
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": tools[tu.name](**tu.input),
                }
                for tu in tool_uses
            ],
        })


# ── 5. Demo: two shoppers, two visits each, then dump Alice's memory ──────────
def banner(text: str) -> None:
    print(f"\n{'=' * 70}\n  {text}\n{'=' * 70}")


def main() -> None:
    seed_catalog()

    # -- Alice's first visit ----------------------------------------------------
    banner("Alice — first visit")
    history_alice: list[dict] = []
    history_alice = chat("alice", (
        "Hi! I'm vegan and have a tree-nut allergy. "
        "Can you find me some good snacks under $10?"
    ), history_alice)
    history_alice = chat("alice", (
        "Add the oat milk chocolate bar to my wishlist. "
        "Also remember that my sister's birthday is June 12 — she likes camping gear."
    ), history_alice)

    # -- Bob's first visit ------------------------------------------------------
    banner("Bob — first visit")
    history_bob: list[dict] = []
    history_bob = chat("bob", (
        "Hey, I'm planning a 3-day backpacking trip. "
        "What outdoor gear under $100 would you suggest?"
    ), history_bob)
    history_bob = chat("bob", (
        "Save 'outdoor gear under $100' as a workflow I might re-run later."
    ), history_bob)

    # -- Alice returns ----------------------------------------------------------
    # Fresh history (a new chat session) — but Trove memory persists.
    banner("Alice — returns later (new chat session)")
    history_alice2: list[dict] = []
    history_alice2 = chat("alice", "Hi again! What do you remember about me?", history_alice2)
    history_alice2 = chat("alice", (
        "My sister's birthday is coming up — what was your idea? "
        "Anything in our catalog that would work?"
    ), history_alice2)

    # -- Bob returns ------------------------------------------------------------
    banner("Bob — returns later")
    history_bob2: list[dict] = []
    history_bob2 = chat("bob", (
        "Run my saved workflow and tell me what's in stock."
    ), history_bob2)

    # -- Inspect Alice's memory tree -------------------------------------------
    banner("Alice's persistent memory (proof it survives across sessions)")
    alice_fs = client_for("alice")
    for f in alice_fs.list_dir("workspace/"):
        if f.is_dir:
            print(f"  📁 {f.path}/")
            continue
        print(f"  📄 {f.path}  ({f.size_bytes} B)")
        print("     " + alice_fs.read_text(f.path).replace("\n", "\n     ").rstrip())
        print()

    # -- Sanity check: Bob CANNOT see Alice's memory ---------------------------
    banner("Isolation check — Bob's memory has no Alice data")
    bob_fs = client_for("bob")
    print(bob_fs.exec("ls workspace/"))
    print("(Bob's namespace is independent — different files, no cross-contamination.)")


if __name__ == "__main__":
    main()
