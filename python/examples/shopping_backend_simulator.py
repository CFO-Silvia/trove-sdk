"""Realistic backend simulator: shopping agent serving many users.

WHAT THIS DEMONSTRATES
──────────────────────
This file is shaped like a stateless backend (think FastAPI handler) — every
chat turn is one independent function call. *All* per-user state lives in
Trove namespaces, so the server holds no memory between requests.

Compared to the simpler `shopping_agent_multi_user.py` example:

    multi_user.py                   this file
    ──────────────                  ──────────
    raw user_id as namespace        HMAC-derived opaque namespace (no PII leak)
    in-memory message history       history persisted to workspace/history.jsonl
    text-only                       multimodal — users can attach images
    seeds catalog inline            catalog is a separate namespace + lazily seeded
    one big main()                  one handle_chat_request() per "HTTP call"

Map this to a real backend:

    @app.post("/chat/{user_id}")
    async def chat(user_id, body, image_upload):
        return handle_chat_request(user_id, body.message, image_upload.bytes)

WHY HMAC THE USER_ID
────────────────────
Trove namespaces are visible in operator logs, the activity dashboard, webhook
payloads, and (if a key leaks) the URL path. If the namespace IS the user_id
(an email, an account number, anything PII-shaped), you've leaked it.

We hash `user_id` through HMAC-SHA256 with a server-only secret to produce
an opaque, stable namespace token like `u-a1b2c3d4...`. Same input → same
namespace (so memory persists across sessions); the namespace alone can't be
reversed to identify the user.

THE NAMESPACE LAYOUT
────────────────────
    catalog/              shared, read-only — seeded once, every user reads it
      workspace/products.csv

    u-{hmac(user_id)}/    per-user, read+write — opaque token, no PII
      workspace/profile.md          long-lived prefs
      workspace/notes.md            running observations (append-only)
      workspace/wishlist.md         items to consider later
      workspace/history.jsonl       full chat history (one JSON message per line)
      workspace/uploads/{ts}.jpg    images the user attached, kept for posterity

WHY THIS MATTERS
────────────────
Real shopping platforms can't store agent memory in process RAM — they have
many instances, autoscale, restart on deploy. They also can't put it all in
one DB row, because LLM-curated memory is unstructured (notes, profiles, even
images). Trove namespaces solve this: each user gets a private filesystem the
agent can shape over time, with per-user isolation enforced server-side.

SETUP
─────
    pip install anthropic python-dotenv pillow trove-sdk
    cp examples/.env.example examples/.env  # fill in TROVE_API_KEY + ANTHROPIC_API_KEY
    python examples/shopping_backend_simulator.py
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import anthropic
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont

from trove_sdk import TroveClient, TroveError

# Make print() safe for Windows consoles when Claude returns emoji/em-dashes.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

load_dotenv(Path(__file__).parent / ".env", override=True)

API_KEY          = os.environ["TROVE_API_KEY"]
NAMESPACE_SECRET = os.environ["NAMESPACE_SECRET"].encode()
CATALOG_NS       = "catalog"


def derive_namespace(user_id: str) -> str:
    """Map a real user_id (PII) → opaque, stable namespace token.

    HMAC-SHA256 with a server-side secret means:
      - same user_id → same namespace forever (memory survives)
      - the namespace alone cannot be reversed to recover the user_id
      - operator logs, activity feeds, webhook URLs see only `u-…` strings

    Critical operational rule: NAMESPACE_SECRET must NEVER change. Rotating it
    would orphan every existing user's Trove data. Treat it like a DB
    encryption key — store in your secret manager, alarm on access, rotate by
    re-keying every namespace, not by replacing the secret in place.
    """
    digest = hmac.new(NAMESPACE_SECRET, user_id.encode(), hashlib.sha256).hexdigest()
    return f"u-{digest[:16]}"

claude = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


# ── 1. Per-namespace TroveClient cache ────────────────────────────────────────
# In a real backend this would be re-created per process and lazily cached.
_clients: dict[str, TroveClient] = {}

def trove_for(namespace: str) -> TroveClient:
    if namespace not in _clients:
        _clients[namespace] = TroveClient(api_key=API_KEY, namespace=namespace)
    return _clients[namespace]


# ── 2. Catalog: seed once, read by all users ──────────────────────────────────
def ensure_catalog_seeded() -> None:
    """Seed catalog only if it doesn't already exist. Idempotent across runs."""
    catalog = trove_for(CATALOG_NS)
    try:
        existing = catalog.exec("test -s workspace/products.csv && echo seeded || echo missing").strip()
    except TroveError:
        existing = "missing"
    if "seeded" in existing:
        print(f"[setup] catalog already populated in '{CATALOG_NS}'")
        return
    catalog.write("workspace/products.csv",
        "sku,name,category,price,colors,style,rating\n"
        "F-001,Velvet Reading Chair,furniture,389.00,emerald|navy|mustard,cozy,4.7\n"
        "F-002,Oak Floor Lamp,lighting,129.00,oak|walnut,minimal,4.6\n"
        "F-003,Chunky Wool Throw,textiles,79.00,cream|charcoal|rust,cozy,4.5\n"
        "F-004,Leather Wingback,furniture,899.00,cognac|black,classic,4.8\n"
        "F-005,Linen Floor Cushion,furniture,89.00,natural|sage,minimal,4.4\n"
        "F-006,Brass Pharmacy Lamp,lighting,189.00,brass|black,industrial,4.6\n"
        "F-007,Reclaimed Bookshelf,furniture,549.00,oak|walnut,rustic,4.7\n"
        "F-008,Geometric Wool Rug,textiles,329.00,terracotta|cream,modern,4.5\n"
        "F-009,Boucle Lounge Chair,furniture,649.00,cream|sand,modern,4.6\n"
        "F-010,Ceramic Table Lamp,lighting,159.00,sage|cream|navy,minimal,4.5\n"
    )
    print(f"[setup] seeded {CATALOG_NS}/workspace/products.csv")


# ── 3. History persistence — load + append jsonl per user ─────────────────────
HISTORY_PATH = "workspace/history.jsonl"

def load_history(namespace: str) -> list[dict]:
    """Read prior turns from the user's namespace. Empty list for first-time users."""
    fs = trove_for(namespace)
    try:
        # Use exec so missing-file just returns empty rather than raising.
        raw = fs.exec(f"cat {HISTORY_PATH} 2>/dev/null || true").strip()
    except TroveError:
        return []
    if not raw or raw == "(no output)":
        return []
    out: list[dict] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def append_history(namespace: str, *messages: dict) -> None:
    """Append one or more messages to the user's history.jsonl."""
    if not messages:
        return
    fs = trove_for(namespace)
    # Build with newline-terminated JSON so we can `>>` append safely via shell.
    payload = "\n".join(json.dumps(m, separators=(",", ":")) for m in messages) + "\n"
    # Heredoc-via-shell would be brittle for arbitrary JSON; safer to overwrite the
    # whole file with the previous content + new lines.
    prev = fs.exec(f"cat {HISTORY_PATH} 2>/dev/null || true")
    if prev.strip() in ("", "(no output)"):
        prev = ""
    elif not prev.endswith("\n"):
        prev += "\n"
    fs.write(HISTORY_PATH, prev + payload)


# ── 4. Image handling — upload to Trove + base64-encode for Claude ────────────
def store_user_image(namespace: str, jpeg_bytes: bytes) -> str:
    """Save the image bytes to the user's namespace and return the Trove path."""
    fs = trove_for(namespace)
    ts   = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    path = f"workspace/uploads/{ts}.jpg"
    fs.upload(path, jpeg_bytes)
    return path


# ── 5. Tool catalog — same five tools, namespace closure per request ──────────
def make_tools(namespace: str):
    user_fs    = trove_for(namespace)
    catalog_fs = trove_for(CATALOG_NS)

    def search_catalog(query: str) -> str:
        return catalog_fs.exec(f"grep -i {query!r} workspace/products.csv || echo '(no matches)'")

    def read_memory(path: str) -> str:
        return user_fs.exec(f"cat {path} 2>/dev/null || echo '(no file yet)'")

    def write_memory(path: str, content: str) -> str:
        user_fs.write(path, content)
        return f"saved {path} ({len(content)} bytes)"

    def append_memory(path: str, line: str) -> str:
        line = line.rstrip("\n")
        user_fs.exec(f"mkdir -p workspace && printf '%s\\n' {line!r} >> {path}")
        return f"appended to {path}"

    def list_memory() -> str:
        return user_fs.exec("ls -la workspace/ 2>/dev/null || echo '(empty)'")

    return {
        "search_catalog": search_catalog,
        "read_memory":    read_memory,
        "write_memory":   write_memory,
        "append_memory":  append_memory,
        "list_memory":    list_memory,
    }


TOOL_SCHEMAS = [
    {
        "name": "search_catalog",
        "description": "Case-insensitive grep over the shared product catalog CSV.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "read_memory",
        "description": (
            "Read one of the user's memory files. Returns '(no file yet)' if missing. "
            "Common: workspace/profile.md, workspace/notes.md, workspace/wishlist.md."
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
            "Overwrite a memory file. Use for files that should be fully rewritten "
            "(profile.md, wishlist.md). Don't use for notes.md (append-only)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
        },
    },
    {
        "name": "append_memory",
        "description": "Append one line to a memory file (notes.md, history.jsonl).",
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
You are a personalized home-goods shopping agent. You help one user at a time \
shop for furniture, lighting, and textiles. Their persistent memory lives in \
their namespace under workspace/.

ALWAYS check what you already know before asking — start by calling \
list_memory() and reading any files that look relevant to the request.

Memory file conventions:
  workspace/profile.md      Long-lived prefs (style, color palette, budget tier).
                            Rewrite the whole file with write_memory when prefs change.
  workspace/notes.md        Free-form running notes.
                            Append-only. Prefix each line with the date.
  workspace/wishlist.md     Items the user wants to consider later.
                            Rewrite the whole file when they add or remove items.
  workspace/uploads/        The user's reference images. You'll see filenames in
                            list_memory output — they're searchable context, not
                            something you need to re-open.

When the user attaches an image, describe what you see in workspace/notes.md \
so future visits can recall it. Be concise. When you save anything to memory, \
briefly tell the user."""


# ── 6. The "HTTP handler" — one stateless call per chat turn ──────────────────
def handle_chat_request(user_id: str, message: str, image_jpeg: bytes | None = None) -> str:
    """Stateless: load history, run agent, save updated history, return reply.

    In a real backend this would be wrapped by an HTTP framework. The caller
    (your auth middleware) supplies the real `user_id` — we hash it down to an
    opaque Trove namespace so the operator-visible namespace never holds PII.
    """
    namespace = derive_namespace(user_id)
    print(f"\n┌── [request] user_id={user_id!r} → namespace={namespace}  msg={message[:60]!r}{'  +img' if image_jpeg else ''}")
    history = load_history(namespace)
    print(f"│   loaded {len(history)} prior messages from {namespace}/{HISTORY_PATH}")

    # If the user attached an image, persist it AND build a multimodal user msg.
    user_content: list[dict] | str
    if image_jpeg:
        stored_path = store_user_image(namespace, image_jpeg)
        print(f"│   uploaded image → {stored_path}")
        b64 = base64.standard_b64encode(image_jpeg).decode()
        user_content = [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
            {"type": "text",  "text": f"{message}\n\n(reference image saved at {stored_path})"},
        ]
    else:
        user_content = message

    history.append({"role": "user", "content": user_content})

    tools = make_tools(namespace)
    final_text = ""

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
                print(f"│   [claude] {block.text.strip()}")
                final_text = block.text
            elif block.type == "tool_use":
                args = ", ".join(f"{k}={v!r}" for k, v in block.input.items() if k != "content")
                print(f"│   [tool  ] {block.name}({args})")

        # Persist the assistant turn (Anthropic returns content blocks; serialize them).
        history.append({"role": "assistant", "content": [b.model_dump() for b in response.content]})

        if response.stop_reason != "tool_use":
            break

        tool_uses = [b for b in response.content if b.type == "tool_use"]
        history.append({
            "role": "user",
            "content": [
                {
                    "type":         "tool_result",
                    "tool_use_id":  tu.id,
                    "content":      tools[tu.name](**tu.input),
                }
                for tu in tool_uses
            ],
        })

    # Persist the new turns (the original message + every assistant/tool turn we appended).
    # Simplest correct thing: rewrite history.jsonl from scratch with the full updated list.
    fs = trove_for(namespace)
    fs.write(HISTORY_PATH, "".join(json.dumps(m, separators=(",", ":")) + "\n" for m in history))
    print(f"└── persisted {len(history)} messages")
    return final_text


# ── 7. Helpers for the demo ───────────────────────────────────────────────────
def make_reference_image(label: str, palette: tuple[str, ...]) -> bytes:
    """Generate a small, realistic-feeling reference image so Claude has something to 'see'.

    In a real backend this is just `image_upload.read()` from the HTTP request.
    """
    img  = Image.new("RGB", (320, 240), "white")
    draw = ImageDraw.Draw(img)
    # Color swatches across the bottom — like a moodboard palette.
    band_h = 60
    band_w = 320 // len(palette)
    for i, color in enumerate(palette):
        draw.rectangle([i * band_w, 240 - band_h, (i + 1) * band_w, 240], fill=color)
    # Label on top.
    try:
        font = ImageFont.truetype("arial.ttf", 24)
    except OSError:
        font = ImageFont.load_default()
    draw.text((20, 20), label, fill="black", font=font)
    draw.text((20, 60), " + ".join(palette), fill="#444", font=font)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def banner(text: str) -> None:
    print(f"\n{'═' * 72}\n  {text}\n{'═' * 72}")


# ── 8. Demo: 3 simulated users sending real requests, including multimodal ────
def main() -> None:
    ensure_catalog_seeded()

    # We use intentionally PII-shaped user_ids — emails — to make the point.
    # Watch the print output: the namespace is `u-…`, never the email.
    ALICE = "alice@example.com"
    BOB   = "bob.smith@acme.co"

    # ── Sequence 1: Alice's first request — multimodal ────────────────────────
    banner(f"REQUEST 1   POST /chat  user={ALICE}  (text + image)")
    alice_image = make_reference_image("cozy reading nook", ("#3b5c4a", "#d2b48c", "#8b3a2f"))
    handle_chat_request(ALICE, (
        "Hi! I'm putting together a cozy reading nook in my apartment. "
        "Here's a moodboard of the vibe I'm going for. Budget around $500. "
        "What from your catalog would work?"
    ), image_jpeg=alice_image)

    # ── Sequence 2: Bob's first request — text only ───────────────────────────
    banner(f"REQUEST 2   POST /chat  user={BOB}  (text only)")
    handle_chat_request(BOB, (
        "Looking to outfit a minimal home office. Sage green and cream tones. "
        "Need a desk lamp under $200 and one accent piece."
    ))

    # ── Sequence 3: Alice returns later — different process, fresh state ──────
    # The simulator uses the same Python process, but handle_chat_request carries
    # no in-memory state between calls. Everything is reloaded from Trove.
    banner(f"REQUEST 3   POST /chat  user={ALICE}  (returns later)")
    handle_chat_request(ALICE, (
        "Hi again! Did I save anything to my wishlist? "
        "Also — what was the vibe I was going for last time?"
    ))

    # ── Sequence 4: Bob returns asking for something specific ─────────────────
    banner(f"REQUEST 4   POST /chat  user={BOB}  (returns later)")
    handle_chat_request(BOB, "Show me a few rugs that would match what I bought last time.")

    # ── Inspect the on-disk state we just built up ────────────────────────────
    banner(f"INSPECTION  Alice's namespace ({derive_namespace(ALICE)}) state")
    print(f"NB: the email '{ALICE}' is NOT visible to Trove — only the hashed token.")
    fs = trove_for(derive_namespace(ALICE))
    print(fs.exec("ls -laR workspace/"))

    print("\nNotes excerpt:")
    print(fs.exec("cat workspace/notes.md 2>/dev/null || echo '(no notes)'"))

    print("\nProfile excerpt:")
    print(fs.exec("cat workspace/profile.md 2>/dev/null || echo '(no profile)'"))

    print("\nHistory line count:")
    print(fs.exec("wc -l workspace/history.jsonl 2>/dev/null"))

    banner(f"ISOLATION CHECK  Bob's namespace ({derive_namespace(BOB)}) is empty of Alice's data")
    print(trove_for(derive_namespace(BOB)).exec("ls workspace/"))

    # Same email always derives the same namespace — proves the mapping is stable.
    banner("DETERMINISM CHECK  same user_id → same namespace, every time")
    print(f"  derive_namespace({ALICE!r})  →  {derive_namespace(ALICE)}")
    print(f"  derive_namespace({ALICE!r})  →  {derive_namespace(ALICE)}    (run twice)")
    print(f"  derive_namespace({BOB!r})    →  {derive_namespace(BOB)}")


if __name__ == "__main__":
    main()
