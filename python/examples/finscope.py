"""FinScope — enterprise document intelligence pipeline on Trove.

WHAT THIS DEMONSTRATES
──────────────────────
A multi-tenant accounts-payable + expense intelligence platform. Three
client companies — each isolated to its own Trove namespace — upload
invoices, receipts, contracts, and expense CSVs. Every upload triggers a
webhook-driven pipeline that:

  1. Routes the file by type (PNG/JPG → vision; CSV → exec; TXT → text)
  2. Extracts structured data via Claude (vision for receipts/invoices)
  3. Writes structured JSON back to the namespace
  4. Re-aggregates monthly totals via Python in the sandbox (exec)
  5. Flags compliance issues (duplicate invoices, over-threshold spend)
  6. Snapshots the namespace for SOX-style audit retention

WHY TROVE
─────────
Real AP/expense systems are multi-tenant, document-heavy, event-driven, and
audit-sensitive. The pieces are normally spread across S3 + SQS + lambdas +
audit DB + RBAC. Trove collapses that into: one namespace per tenant, file
events on the wire, sandboxed exec for analytics, snapshots for retention.

ARCHITECTURE
────────────
  Client uploads doc → file.written webhook → FinScope pipeline:
      ├─ vision agent (PNG/JPG): invoices, receipts
      ├─ text agent  (TXT/MD): contracts, NDAs
      └─ exec analyst (CSV): expense reports
  → writes extracted/{path}.json
  → re-runs aggregator: reports/monthly/{YYYY-MM}.json
  → checks flags/{type}.json (duplicates, thresholds)
  → snapshot every Nth doc for compliance retention

FILESYSTEM LAYOUT (per client namespace)
────────────────────────────────────────
    workspace/
      inbox/          ← raw uploads
        invoices/INV-2026-0001.png        (binary, multimodal)
        receipts/RCP-Q1-001.png           (binary, multimodal)
        contracts/MSA-vendor-acme.txt     (text)
        expenses/marketing-2026-Q1.csv    (text)
      extracted/      ← structured JSON outputs (1-per-input)
        invoices/INV-2026-0001.json
        receipts/RCP-Q1-001.json
        contracts/MSA-vendor-acme.json
        expenses/marketing-2026-Q1.json
      reports/        ← aggregations
        monthly/2026-01.json
        quarterly/2026-Q1.json
      flags/          ← compliance flags
        duplicate_invoices.json
        over_threshold.json
      scripts/        ← analysis Python (executed via exec)
        aggregate.py
      audit/
        pipeline.jsonl

WEBHOOK STRATEGY
────────────────
Production: FastAPI route at /trove/events validates HMAC with verify_webhook
and dispatches to dispatch_event(). See `app` and `receive_event()` below.

This demo: same dispatch_event() is called in-process after each upload. We
exercise verify_webhook on a synthetic signed payload to prove the production
wiring is correct without needing a public tunnel.

SETUP
─────
    pip install anthropic python-dotenv pillow trove-sdk fastapi uvicorn
    (uses examples/.env — already populated)
    python examples/finscope.py
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import os
import random
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import anthropic
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont

from trove_sdk import (
    TroveAdminClient,
    TroveClient,
    TroveError,
    WebhookEvent,
    verify_webhook,
)
from trove_sdk.webhooks import WebhookSignatureError

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
load_dotenv(Path(__file__).parent / ".env", override=True)

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
TROVE_ADMIN_KEY   = os.environ["TROVE_ADMIN_KEY"]
TROVE_WORKSPACE_ID = "ws-d69634745de7ce97"  # matches TROVE_ADMIN_KEY

# We mint per-tenant scoped keys at runtime via TroveAdminClient.create_key,
# so even if a client's key leaks it cannot reach another client's data.
COMPLIANCE_THRESHOLD_USD = 5_000        # invoices over this get flagged
SNAPSHOT_EVERY_N_DOCS    = 6            # take an audit snapshot periodically
RUN_ID                   = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")

claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


# ════════════════════════════════════════════════════════════════════════════
#   1. SYNTHETIC DOCUMENT GENERATOR
# ════════════════════════════════════════════════════════════════════════════
# In a real platform the client uploads files via your portal. Here we render
# realistic-looking invoice/receipt PNGs and synthesize CSVs/contracts so the
# vision agent has something to actually look at.

def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in ("arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf"):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


def render_invoice_png(*, invoice_no: str, vendor: str, lines: list[tuple[str, int, float]],
                       due_date: str, issued: str) -> bytes:
    """Render a small invoice as PNG. Bills are intentionally legible so Claude
    vision can extract amounts reliably during the demo."""
    W, H = 720, 540
    img  = Image.new("RGB", (W, H), "white")
    d    = ImageDraw.Draw(img)
    title, normal, small, bold = _font(34), _font(18), _font(14), _font(22)

    d.rectangle([0, 0, W, 70], fill="#1f3a5f")
    d.text((24, 18), "INVOICE", fill="white", font=title)
    d.text((W - 230, 30), f"# {invoice_no}", fill="white", font=bold)

    d.text((24, 90),  f"Vendor:   {vendor}", fill="black", font=normal)
    d.text((24, 116), f"Issued:   {issued}",  fill="black", font=normal)
    d.text((24, 142), f"Due:      {due_date}", fill="black", font=normal)

    d.line([(24, 180), (W - 24, 180)], fill="#999", width=1)
    d.text((24, 190),  "DESCRIPTION",                fill="#333", font=small)
    d.text((420, 190), "QTY",                       fill="#333", font=small)
    d.text((500, 190), "UNIT",                      fill="#333", font=small)
    d.text((600, 190), "AMOUNT",                    fill="#333", font=small)
    d.line([(24, 210), (W - 24, 210)], fill="#999", width=1)

    y = 224
    total = 0.0
    for desc, qty, unit in lines:
        amount = qty * unit
        total += amount
        d.text((24, y),  desc[:48],           fill="black", font=normal)
        d.text((420, y), str(qty),            fill="black", font=normal)
        d.text((500, y), f"${unit:,.2f}",     fill="black", font=normal)
        d.text((600, y), f"${amount:,.2f}",   fill="black", font=normal)
        y += 28

    d.line([(24, y + 8), (W - 24, y + 8)], fill="#999", width=1)
    d.text((420, y + 18), "TOTAL DUE",            fill="black", font=bold)
    d.text((600, y + 18), f"${total:,.2f}",        fill="black", font=bold)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def render_receipt_png(*, merchant: str, items: list[tuple[str, float]], date: str,
                       method: str = "VISA •••• 4421") -> bytes:
    """Render a small receipt as PNG (narrower, vertical, like a real receipt)."""
    W = 360
    H = 180 + 30 * len(items) + 110
    img  = Image.new("RGB", (W, H), "white")
    d    = ImageDraw.Draw(img)
    big, normal, small = _font(20), _font(15), _font(12)

    d.text((24, 22),  merchant.upper(), fill="black", font=big)
    d.text((24, 50),  "─" * 32,         fill="#888",   font=small)
    d.text((24, 70),  date,              fill="#444",   font=small)
    d.text((24, 90),  method,            fill="#444",   font=small)
    d.text((24, 110), "─" * 32,         fill="#888",   font=small)

    y = 132
    total = 0.0
    for name, price in items:
        total += price
        d.text((24, y),       name[:24],          fill="black", font=normal)
        d.text((W - 90, y),   f"${price:,.2f}",   fill="black", font=normal)
        y += 28

    d.text((24, y + 8),       "─" * 32,             fill="#888",  font=small)
    d.text((24, y + 28),      "TOTAL",              fill="black", font=big)
    d.text((W - 110, y + 28), f"${total:,.2f}",     fill="black", font=big)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def make_expense_csv(rows: list[tuple[str, str, str, float]]) -> str:
    """rows = [(date, category, description, amount), ...]"""
    out = ["date,category,description,amount_usd"]
    for date, cat, desc, amt in rows:
        out.append(f"{date},{cat},\"{desc}\",{amt:.2f}")
    return "\n".join(out) + "\n"


def make_contract_text(*, vendor: str, value: float, term_months: int,
                       auto_renew: bool, governing_law: str) -> str:
    return (
        f"MASTER SERVICES AGREEMENT\n"
        f"=========================\n\n"
        f"Counterparty: {vendor}\n"
        f"Total Contract Value: ${value:,.2f}\n"
        f"Initial Term: {term_months} months\n"
        f"Auto-Renew: {'YES (12 month rollover)' if auto_renew else 'no'}\n"
        f"Governing Law: {governing_law}\n\n"
        f"This Master Services Agreement (\"Agreement\") sets forth the terms\n"
        f"under which Counterparty shall provide services to Customer.\n\n"
        f"1. PAYMENT. Net-30 from invoice date.\n"
        f"2. CONFIDENTIALITY. Both parties shall protect Confidential Info\n"
        f"   for the term plus 3 years.\n"
        f"3. INDEMNIFICATION. Counterparty indemnifies Customer for IP claims\n"
        f"   up to 2x fees paid in the trailing 12 months.\n"
        f"4. TERMINATION. Either party may terminate for material breach\n"
        f"   uncured after 30 days written notice.\n"
        f"5. AUTO-RENEWAL. {'Renews automatically; 60-day notice required to opt out.' if auto_renew else 'No auto-renewal.'}\n"
    )


# ════════════════════════════════════════════════════════════════════════════
#   2. CLIENT FIXTURES — three companies, varied document mixes
# ════════════════════════════════════════════════════════════════════════════
# Each client's document set is intentionally distinct so the demo output
# shows different aggregations + different compliance flags per tenant.

@dataclass
class DocSpec:
    path: str               # workspace-relative
    kind: str               # 'invoice' | 'receipt' | 'contract' | 'expense'
    bytes_payload: bytes | None = None
    text_payload: str | None  = None


def fixtures_for_client(client_id: str) -> list[DocSpec]:
    if client_id == "acme-corp":
        return [
            DocSpec("inbox/invoices/INV-2026-0001.png", "invoice", render_invoice_png(
                invoice_no="INV-2026-0001", vendor="Cloudkeep Hosting Ltd",
                issued="2026-01-08", due_date="2026-02-07",
                lines=[("Premium hosting — Jan", 1, 2_400.00),
                       ("CDN bandwidth overage",  1,   320.00)])),
            DocSpec("inbox/invoices/INV-2026-0002.png", "invoice", render_invoice_png(
                invoice_no="INV-2026-0002", vendor="Northwind Consulting",
                issued="2026-01-22", due_date="2026-02-21",
                lines=[("Q1 strategy retainer", 1, 7_500.00)])),  # over threshold
            # Same vendor + same total as INV-2026-0001 — trips the duplicate-
            # invoice detector (an accidental re-bill from the vendor's portal).
            DocSpec("inbox/invoices/INV-2026-0003.png", "invoice", render_invoice_png(
                invoice_no="INV-2026-0003", vendor="Cloudkeep Hosting Ltd",
                issued="2026-02-08", due_date="2026-03-10",
                lines=[("Premium hosting — Feb", 1, 2_400.00),
                       ("CDN bandwidth overage",  1,   320.00)])),
            DocSpec("inbox/receipts/RCP-Q1-001.png", "receipt", render_receipt_png(
                merchant="Maven Coffee", date="2026-01-15 09:14",
                items=[("Drip coffee", 4.50), ("Almond croissant", 5.25)])),
            DocSpec("inbox/receipts/RCP-Q1-002.png", "receipt", render_receipt_png(
                merchant="Yellow Cab NYC", date="2026-01-22 18:42",
                items=[("Fare 5th Ave → JFK", 62.00), ("Tip", 12.00)])),
            DocSpec("inbox/expenses/marketing-2026-Q1.csv", "expense", text_payload=make_expense_csv([
                ("2026-01-04", "ads",      "LinkedIn Sponsored Posts",        2_840.00),
                ("2026-01-11", "ads",      "Google Ads — campaign awareness", 4_120.00),
                ("2026-01-18", "events",   "Booth at SaaStr",                12_500.00),  # over threshold
                ("2026-01-23", "swag",     "Conference t-shirts",               890.00),
                ("2026-02-02", "travel",   "Flights NYC ↔ SF (3 staff)",       2_460.00),
                ("2026-02-14", "ads",      "Google Ads — retargeting",         1_980.00),
                ("2026-03-01", "events",   "Webinar production",                 740.00),
            ])),
            DocSpec("inbox/contracts/MSA-northwind.txt", "contract", text_payload=make_contract_text(
                vendor="Northwind Consulting", value=90_000, term_months=12,
                auto_renew=True, governing_law="State of Delaware")),
        ]
    if client_id == "globex-inc":
        return [
            DocSpec("inbox/invoices/INV-G-100.png", "invoice", render_invoice_png(
                invoice_no="INV-G-100", vendor="SteelMech Industrial",
                issued="2026-01-12", due_date="2026-02-11",
                lines=[("CNC tooling — batch A", 4, 1_750.00),
                       ("Calibration service",    1,   450.00)])),
            DocSpec("inbox/invoices/INV-G-101.png", "invoice", render_invoice_png(
                invoice_no="INV-G-101", vendor="LogiPort Freight",
                issued="2026-01-29", due_date="2026-02-28",
                lines=[("LCL shipment SHA → LAX",   1, 3_280.00),
                       ("Customs brokerage",        1,   520.00),
                       ("Demurrage (3 days)",       3,   180.00)])),
            DocSpec("inbox/invoices/INV-G-102.png", "invoice", render_invoice_png(
                invoice_no="INV-G-102", vendor="SteelMech Industrial",
                issued="2026-02-15", due_date="2026-03-17",
                lines=[("Replacement spindle",      1, 9_800.00)])),  # over threshold
            DocSpec("inbox/receipts/RCP-G-001.png", "receipt", render_receipt_png(
                merchant="Hilton SFO",   date="2026-01-30 22:08",
                items=[("Room 1 night",      289.00), ("Parking", 42.00)])),
            DocSpec("inbox/expenses/ops-2026-Q1.csv", "expense", text_payload=make_expense_csv([
                ("2026-01-05", "supplies", "Cutting fluid — drum",           1_240.00),
                ("2026-01-19", "utilities","Plant electricity",              6_980.00),  # over threshold
                ("2026-02-08", "travel",   "Site audit Cleveland",             1_120.00),
                ("2026-02-22", "supplies", "PPE restock",                       720.00),
                ("2026-03-05", "utilities","Plant electricity",              7_240.00),  # over threshold
            ])),
            DocSpec("inbox/contracts/MSA-logiport.txt", "contract", text_payload=make_contract_text(
                vendor="LogiPort Freight", value=240_000, term_months=24,
                auto_renew=True, governing_law="State of New York")),
        ]
    if client_id == "initech":
        return [
            DocSpec("inbox/invoices/INV-IT-77.png", "invoice", render_invoice_png(
                invoice_no="INV-IT-77", vendor="DataLake.io",
                issued="2026-01-09", due_date="2026-02-08",
                lines=[("Warehouse compute — Jan", 1, 1_840.00),
                       ("Storage 2.4 TB-mo",       1,   192.00)])),
            DocSpec("inbox/invoices/INV-IT-78.png", "invoice", render_invoice_png(
                invoice_no="INV-IT-78", vendor="Sentinel Security",
                issued="2026-02-04", due_date="2026-03-06",
                lines=[("MDR retainer Q1", 1, 6_400.00)])),  # over threshold
            DocSpec("inbox/receipts/RCP-IT-001.png", "receipt", render_receipt_png(
                merchant="Lyft", date="2026-02-12 19:55",
                items=[("Ride to JFK", 38.40), ("Tip", 7.60)])),
            DocSpec("inbox/expenses/eng-2026-Q1.csv", "expense", text_payload=make_expense_csv([
                ("2026-01-10", "saas",     "GitHub Enterprise seats",           980.00),
                ("2026-01-24", "saas",     "Linear seats",                      420.00),
                ("2026-02-07", "training", "Hackathon catering",                640.00),
                ("2026-02-21", "saas",     "Datadog observability",           2_180.00),
                ("2026-03-12", "training", "Internal offsite",                3_900.00),
            ])),
            DocSpec("inbox/contracts/NDA-sentinel.txt", "contract", text_payload=make_contract_text(
                vendor="Sentinel Security", value=24_000, term_months=12,
                auto_renew=False, governing_law="Commonwealth of Massachusetts")),
        ]
    raise ValueError(f"unknown client_id: {client_id}")


# ════════════════════════════════════════════════════════════════════════════
#   3. PIPELINE STATE — minimal in-process state per tenant
# ════════════════════════════════════════════════════════════════════════════
# In production these would be cached in Redis / DynamoDB. Here we keep them
# in dicts keyed by namespace so the demo is self-contained.

@dataclass
class TenantContext:
    client_id:   str
    namespace:   str
    api_key:     str            # scoped, namespace-locked workspace key
    fs:          TroveClient
    docs_seen:   int = 0


tenants: dict[str, TenantContext] = {}     # namespace → context
_signing_secret: str | None = None         # for verify_webhook demo

# ════════════════════════════════════════════════════════════════════════════
#   4. AGENTS — vision, text, exec
# ════════════════════════════════════════════════════════════════════════════

VISION_INVOICE_PROMPT = """\
You are a senior AP automation analyst. Extract structured invoice data from \
the image. Output ONLY JSON matching this schema, no commentary:

{
  "invoice_no": "<as printed>",
  "vendor": "<vendor name>",
  "issued": "YYYY-MM-DD",
  "due":    "YYYY-MM-DD",
  "currency": "USD",
  "line_items": [{"description": "...", "qty": <int>, "unit_usd": <number>, "amount_usd": <number>}],
  "subtotal_usd": <number>,
  "total_usd":    <number>
}

If a field is illegible, use null. Sum line items to verify subtotal — if they \
disagree, prefer the printed total but include both.\
"""

VISION_RECEIPT_PROMPT = """\
You are an expense-report analyst. Extract structured receipt data from the \
image. Output ONLY JSON matching this schema:

{
  "merchant": "...",
  "date":     "YYYY-MM-DD HH:MM",
  "currency": "USD",
  "items":    [{"description": "...", "amount_usd": <number>}],
  "total_usd": <number>,
  "category": "<one of: meals|travel|lodging|supplies|other>"
}\
"""

CONTRACT_PROMPT = """\
You are a contracts paralegal. Extract key terms from the contract text below. \
Output ONLY JSON:

{
  "counterparty": "...",
  "total_value_usd": <number or null>,
  "term_months":     <int or null>,
  "auto_renew":      <bool>,
  "governing_law":   "...",
  "risk_flags":      [<short strings>],
  "summary":         "<one sentence>"
}

Risk flags should call out things like: auto-renewal without notice cap, \
unlimited indemnity, missing termination clause, governing law in unfavorable \
jurisdiction.\
"""


def _claude_json(prompt: str, content: list[dict]) -> dict[str, Any]:
    """Call Claude and parse the JSON response. Strips ```json fences if present."""
    resp = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1500,
        system=prompt,
        messages=[{"role": "user", "content": content}],
    )
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    if text.startswith("```"):
        # strip ```json ... ``` fences
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"_raw": text, "_parse_error": True}


def extract_invoice(image_bytes: bytes) -> dict[str, Any]:
    b64 = base64.standard_b64encode(image_bytes).decode()
    return _claude_json(VISION_INVOICE_PROMPT, [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
        {"type": "text",  "text": "Extract this invoice."},
    ])


def extract_receipt(image_bytes: bytes) -> dict[str, Any]:
    b64 = base64.standard_b64encode(image_bytes).decode()
    return _claude_json(VISION_RECEIPT_PROMPT, [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
        {"type": "text",  "text": "Extract this receipt."},
    ])


def extract_contract(text: str) -> dict[str, Any]:
    return _claude_json(CONTRACT_PROMPT, [
        {"type": "text", "text": text},
    ])


# CSV expense reports go through the sandbox — the agent doesn't see the data,
# Python does. This keeps token cost flat regardless of CSV size and exercises
# Trove's exec sandbox.
EXPENSE_AGGREGATOR_PY = r"""
import csv, json, sys
from collections import defaultdict
# Path is passed namespace-relative (without the 'workspace/' prefix) because
# the exec sandbox CWD is already inside workspace/.
src = sys.argv[1]
rows = list(csv.DictReader(open(src, newline='', encoding='utf-8')))
total = 0.0
by_cat   = defaultdict(float)
by_month = defaultdict(float)
flagged  = []
for r in rows:
    amt = float(r['amount_usd'])
    total += amt
    by_cat[r['category']]    += amt
    by_month[r['date'][:7]]  += amt
    if amt > 5000:
        flagged.append({'date': r['date'], 'category': r['category'],
                        'description': r['description'], 'amount_usd': amt})
out = {
    'rows': len(rows),
    'total_usd': round(total, 2),
    'by_category': {k: round(v, 2) for k, v in sorted(by_cat.items(), key=lambda kv: -kv[1])},
    'by_month':    {k: round(v, 2) for k, v in sorted(by_month.items())},
    'flagged_over_5k': flagged,
}
print(json.dumps(out, indent=2))
"""


def analyze_expense_csv(fs: TroveClient, csv_path: str) -> dict[str, Any]:
    """Run the aggregator INSIDE the Trove sandbox — file never leaves the namespace."""
    fs.write("workspace/scripts/aggregate_one.py", EXPENSE_AGGREGATOR_PY.lstrip())
    # exec CWD is inside workspace/, so strip the prefix before passing to Python.
    rel = csv_path.removeprefix("workspace/")
    result = fs.exec_detailed(f"python3 scripts/aggregate_one.py {rel}")
    if result.exit_code != 0:
        return {"_error": result.stderr or "unknown", "_exit": result.exit_code}
    return json.loads(result.stdout)


# ════════════════════════════════════════════════════════════════════════════
#   5. AGGREGATION & COMPLIANCE — re-runs after each upload
# ════════════════════════════════════════════════════════════════════════════
# These execute in the sandbox so we never pull all extracted JSON across the
# wire — the analytics live next to the data.

ROLLUP_PY = r"""
# Run from inside the namespace's workspace/ directory (exec CWD).
# All paths here are relative to that — no 'workspace/' prefix.
import json, glob, os
from collections import defaultdict

monthly = defaultdict(lambda: {'invoices_usd': 0.0, 'receipts_usd': 0.0,
                               'expenses_usd': 0.0, 'count': 0})
seen_invoice = defaultdict(list)   # (vendor, total) → [paths]
over_threshold = []                # invoices > $5,000

for path in sorted(glob.glob('extracted/invoices/*.json')):
    try:
        d = json.load(open(path, encoding='utf-8'))
    except Exception:
        continue
    issued = (d.get('issued') or '')[:7]
    total  = d.get('total_usd') or 0
    if issued and total:
        monthly[issued]['invoices_usd'] += total
        monthly[issued]['count']        += 1
    if total and d.get('vendor'):
        seen_invoice[(d['vendor'], round(total, 2))].append({
            'path': path, 'invoice_no': d.get('invoice_no'), 'issued': d.get('issued')})
    if total and total > 5000:
        over_threshold.append({'path': path, 'invoice_no': d.get('invoice_no'),
                               'vendor': d.get('vendor'), 'total_usd': total,
                               'issued': d.get('issued')})

for path in sorted(glob.glob('extracted/receipts/*.json')):
    try:
        d = json.load(open(path, encoding='utf-8'))
    except Exception:
        continue
    month = (d.get('date') or '')[:7]
    total = d.get('total_usd') or 0
    if month and total:
        monthly[month]['receipts_usd'] += total
        monthly[month]['count']        += 1

for path in sorted(glob.glob('extracted/expenses/*.json')):
    try:
        d = json.load(open(path, encoding='utf-8'))
    except Exception:
        continue
    for month, amt in (d.get('by_month') or {}).items():
        monthly[month]['expenses_usd'] += amt

duplicates = [{'vendor': v, 'total_usd': t, 'occurrences': occs}
              for (v, t), occs in seen_invoice.items() if len(occs) > 1]

os.makedirs('reports/monthly', exist_ok=True)
os.makedirs('flags',           exist_ok=True)

for month, agg in monthly.items():
    agg['total_usd'] = round(sum([agg['invoices_usd'], agg['receipts_usd'], agg['expenses_usd']]), 2)
    for k in ('invoices_usd', 'receipts_usd', 'expenses_usd'):
        agg[k] = round(agg[k], 2)
    json.dump({'month': month, **agg},
              open(f'reports/monthly/{month}.json', 'w'),
              indent=2)

# Quarterly rollup
quarterly = defaultdict(lambda: {'invoices_usd': 0.0, 'receipts_usd': 0.0,
                                 'expenses_usd': 0.0, 'total_usd': 0.0, 'months': []})
for month, agg in monthly.items():
    y, m = month.split('-')
    q = f"{y}-Q{(int(m) - 1) // 3 + 1}"
    quarterly[q]['invoices_usd'] += agg['invoices_usd']
    quarterly[q]['receipts_usd'] += agg['receipts_usd']
    quarterly[q]['expenses_usd'] += agg['expenses_usd']
    quarterly[q]['total_usd']    += agg['total_usd']
    quarterly[q]['months'].append(month)

os.makedirs('reports/quarterly', exist_ok=True)
for q, agg in quarterly.items():
    for k in ('invoices_usd', 'receipts_usd', 'expenses_usd', 'total_usd'):
        agg[k] = round(agg[k], 2)
    json.dump({'quarter': q, **agg},
              open(f'reports/quarterly/{q}.json', 'w'),
              indent=2)

json.dump(duplicates,     open('flags/duplicate_invoices.json', 'w'), indent=2)
json.dump(over_threshold, open('flags/over_threshold.json',     'w'), indent=2)

print(json.dumps({
    'months_aggregated':  len(monthly),
    'quarters':           sorted(quarterly.keys()),
    'duplicate_groups':   len(duplicates),
    'over_threshold':     len(over_threshold),
}))
"""


def re_aggregate(ctx: TenantContext) -> dict[str, Any]:
    """Re-run aggregation across all extracted JSON. Writes monthly/quarterly
    reports and compliance flag files inside the namespace."""
    ctx.fs.write("workspace/scripts/rollup.py", ROLLUP_PY.lstrip())
    res = ctx.fs.exec_detailed("python3 workspace/scripts/rollup.py")
    if res.exit_code != 0:
        return {"_error": res.stderr, "_exit": res.exit_code}
    return json.loads(res.stdout)


# ════════════════════════════════════════════════════════════════════════════
#   6. AUDIT LOG — append-only per-tenant pipeline log
# ════════════════════════════════════════════════════════════════════════════

def audit(ctx: TenantContext, *, event: str, **fields: Any) -> None:
    """Append one structured line to workspace/audit/pipeline.jsonl."""
    line = json.dumps({
        "ts":         datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "client_id":  ctx.client_id,
        "namespace":  ctx.namespace,
        "event":      event,
        **fields,
    }, separators=(",", ":"))
    # Read-modify-write — fine for a demo. In prod this would go through an
    # async queue or be replaced by Trove's own events API (which already
    # carries identical info).
    try:
        prev = ctx.fs.exec("cat workspace/audit/pipeline.jsonl 2>/dev/null || true")
        if prev.strip() in ("", "(no output)"):
            prev = ""
        elif not prev.endswith("\n"):
            prev += "\n"
    except TroveError:
        prev = ""
    ctx.fs.write("workspace/audit/pipeline.jsonl", prev + line + "\n")


# ════════════════════════════════════════════════════════════════════════════
#   7. PIPELINE DISPATCH — the heart of the webhook handler
# ════════════════════════════════════════════════════════════════════════════
# This is what the FastAPI route ultimately calls. dispatch_event() is shape-
# compatible with a verified WebhookEvent — production and demo invoke it the
# same way.

def dispatch_event(event: WebhookEvent) -> None:
    if event.type == "webhook.test":
        print(f"   [pipeline] webhook.test received: {event.data.get('message')}")
        return
    if event.type != "file.written":
        # snapshot.created etc. just get logged
        ctx = tenants.get(event.namespace or "")
        if ctx:
            audit(ctx, event=event.type, **event.data)
        return

    ns   = event.namespace
    path = event.data.get("path", "")
    if not ns or ns not in tenants:
        print(f"   [pipeline] skip — unknown namespace: {ns}")
        return
    ctx = tenants[ns]

    # Only act on inbox/* uploads. Skip our own outputs (extracted/, reports/,
    # flags/, scripts/, audit/) so we don't recurse on our own writes.
    if not path.startswith("workspace/inbox/"):
        return

    print(f"   [pipeline] {ctx.client_id}  {path}")
    ctx.docs_seen += 1
    audit(ctx, event="ingest", path=path, size_bytes=event.data.get("size_bytes"))

    out_path: str | None = None
    extracted: dict[str, Any] = {}

    if path.startswith("workspace/inbox/invoices/"):
        raw = ctx.fs.read_bytes(path)
        extracted = extract_invoice(raw)
        out_path  = path.replace("inbox/invoices/", "extracted/invoices/").rsplit(".", 1)[0] + ".json"

    elif path.startswith("workspace/inbox/receipts/"):
        raw = ctx.fs.read_bytes(path)
        extracted = extract_receipt(raw)
        out_path  = path.replace("inbox/receipts/", "extracted/receipts/").rsplit(".", 1)[0] + ".json"

    elif path.startswith("workspace/inbox/expenses/"):
        extracted = analyze_expense_csv(ctx.fs, path)
        out_path  = path.replace("inbox/expenses/", "extracted/expenses/").rsplit(".", 1)[0] + ".json"

    elif path.startswith("workspace/inbox/contracts/"):
        text = ctx.fs.read_text(path)
        extracted = extract_contract(text)
        out_path  = path.replace("inbox/contracts/", "extracted/contracts/").rsplit(".", 1)[0] + ".json"

    if out_path is None:
        return

    extracted["_meta"] = {"source": path, "extracted_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    ctx.fs.write(out_path, json.dumps(extracted, indent=2, ensure_ascii=False))
    audit(ctx, event="extracted", source=path, output=out_path)

    summary = re_aggregate(ctx)
    audit(ctx, event="rollup", **summary)
    print(f"      → extracted → {out_path.split('/')[-1]}   "
          f"(months={summary.get('months_aggregated')}, dup_groups={summary.get('duplicate_groups')}, "
          f"over_$5k={summary.get('over_threshold')})")

    # Periodic snapshot for compliance retention
    if ctx.docs_seen % SNAPSHOT_EVERY_N_DOCS == 0:
        snap = ctx.fs.create_snapshot(label=f"finscope-{RUN_ID}-{ctx.docs_seen}docs")
        audit(ctx, event="snapshot", snapshot_id=snap.snapshot_id, label=snap.label)
        print(f"      📦 snapshot {snap.snapshot_id} ({snap.size_bytes:,} bytes)")


# ════════════════════════════════════════════════════════════════════════════
#   8. FASTAPI WEBHOOK RECEIVER (production shape)
# ════════════════════════════════════════════════════════════════════════════
# Wire this up in production by:
#   1) start uvicorn, expose via tunnel/load-balancer
#   2) admin.create_webhook(url=<public_url>/trove/events, events=["*"])
#   3) save the returned signing_secret in your secret store
#
# For the demo we don't start the server — see _verify_webhook_path_works()
# which exercises the same verify_webhook() call on a synthetic signed
# delivery, proving the production code path is correct.

try:
    from fastapi import FastAPI, Header, HTTPException, Request

    app = FastAPI()

    @app.post("/trove/events")
    async def receive_event(request: Request,
                            x_trove_signature: str = Header(None)) -> dict[str, str]:
        body = await request.body()
        secret = os.environ.get("TROVE_WEBHOOK_SECRET", "")
        if not secret:
            raise HTTPException(500, "TROVE_WEBHOOK_SECRET not set")
        try:
            event = verify_webhook(secret=secret, body=body, signature_header=x_trove_signature or "")
        except WebhookSignatureError as e:
            raise HTTPException(401, str(e))
        dispatch_event(event)
        return {"ok": "true"}
except ImportError:
    app = None    # FastAPI optional


def _verify_webhook_path_works(secret: str) -> None:
    """Sign a synthetic event and round-trip it through verify_webhook,
    proving the production webhook-receive code path is correct."""
    payload = {
        "id":           f"evt-{uuid.uuid4().hex[:12]}",
        "type":         "webhook.test",
        "api_version":  "2026-04-30",
        "workspace_id": TROVE_WORKSPACE_ID,
        "namespace":    None,
        "created_at":   datetime.now(timezone.utc).isoformat(),
        "data":         {"message": "Hello from FinScope verify path."},
    }
    body = json.dumps(payload, separators=(",", ":")).encode()
    t    = int(time.time())
    sig  = hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    header = f"t={t},v1={sig}"
    event = verify_webhook(secret=secret, body=body, signature_header=header)
    print(f"   ✓ verify_webhook OK   type={event.type}  data={event.data}")


# ════════════════════════════════════════════════════════════════════════════
#   9. MULTI-TENANT ONBOARDING
# ════════════════════════════════════════════════════════════════════════════

def onboard_client(admin: TroveAdminClient, client_id: str) -> TenantContext:
    """Mint a namespace-locked workspace key for one client. The key cannot
    reach any other client's data, even if it leaks."""
    namespace = client_id.replace("_", "-")
    key = admin.create_key(name=f"finscope-{client_id}-{RUN_ID}", namespace=namespace)
    fs  = TroveClient(api_key=key.api_key, namespace=namespace)
    # Wipe any prior namespace state from earlier demo runs so output is fresh.
    try:
        fs.exec("rm -rf workspace/* 2>/dev/null || true")
    except TroveError:
        pass
    fs.exec("mkdir -p workspace/inbox/invoices workspace/inbox/receipts "
            "workspace/inbox/contracts workspace/inbox/expenses "
            "workspace/extracted/invoices workspace/extracted/receipts "
            "workspace/extracted/contracts workspace/extracted/expenses "
            "workspace/reports/monthly workspace/reports/quarterly "
            "workspace/flags workspace/scripts workspace/audit")
    ctx = TenantContext(client_id=client_id, namespace=namespace,
                        api_key=key.api_key, fs=fs)
    print(f"   ✓ {client_id:14s} ns={namespace:14s} key={key.prefix}…  ({key.key_id})")
    return ctx


# ════════════════════════════════════════════════════════════════════════════
#   10. SIMULATED WEBHOOK DELIVERY
# ════════════════════════════════════════════════════════════════════════════
# Wraps the synchronous upload + handler dispatch into one call. In production
# the upload returns immediately, Trove fires file.written async, and the
# FastAPI route runs the handler. Same outcome — same dispatch_event() call.

def upload_and_dispatch(ctx: TenantContext, doc: DocSpec) -> None:
    if doc.bytes_payload is not None:
        ctx.fs.upload(f"workspace/{doc.path}", doc.bytes_payload)
        size = len(doc.bytes_payload)
    else:
        assert doc.text_payload is not None
        ctx.fs.write(f"workspace/{doc.path}", doc.text_payload)
        size = len(doc.text_payload.encode())

    event = WebhookEvent(
        id           = f"evt-{uuid.uuid4().hex[:12]}",
        type         = "file.written",
        api_version  = "2026-04-30",
        workspace_id = TROVE_WORKSPACE_ID,
        namespace    = ctx.namespace,
        created_at   = datetime.now(timezone.utc).isoformat(),
        data         = {"path": f"workspace/{doc.path}", "size_bytes": size, "source": "upload"},
    )
    dispatch_event(event)


# ════════════════════════════════════════════════════════════════════════════
#   11. DEMO ORCHESTRATION
# ════════════════════════════════════════════════════════════════════════════

def banner(text: str) -> None:
    print(f"\n{'═' * 78}\n  {text}\n{'═' * 78}")


def main() -> None:
    global _signing_secret

    banner("FinScope — enterprise document intelligence on Trove")
    print(f"workspace_id : {TROVE_WORKSPACE_ID}")
    print(f"run_id       : {RUN_ID}")

    # ── Phase 1: register a real Trove webhook (for prod-shape; we won't run
    # an actual receiver here — the demo dispatches in-process). The signing
    # secret is then exercised against a synthetic signed payload to prove the
    # verify_webhook() code path is wired correctly. ───────────────────────────
    banner("PHASE 1   register webhook + verify signature path")
    with TroveAdminClient(TROVE_ADMIN_KEY, TROVE_WORKSPACE_ID) as admin:
        # Reuse a stable URL so we don't endlessly multiply webhooks across runs.
        # The URL is unreachable from Trove (localhost) — that's fine, we're
        # only proving the registration+signing flow, not delivery.
        existing = [w for w in admin.list_webhooks() if w.url.endswith("/trove/events/finscope-demo")]
        for old in existing:
            admin.delete_webhook(old.webhook_id)
        hook = admin.create_webhook(
            url="http://localhost:8765/trove/events/finscope-demo",
            events=["file.written", "snapshot.created", "key.created"],
            description=f"FinScope demo run {RUN_ID}",
        )
        _signing_secret = hook.signing_secret
        print(f"   webhook_id    : {hook.webhook_id}")
        print(f"   signing_secret: {hook.signing_secret[:18]}…  (shown once, stored in secrets manager IRL)")
        _verify_webhook_path_works(hook.signing_secret)

        # ── Phase 2: onboard 3 client tenants — namespace-locked keys per client
        banner("PHASE 2   onboard 3 client tenants (scoped, namespace-locked keys)")
        for client_id in ("acme-corp", "globex-inc", "initech"):
            ctx = onboard_client(admin, client_id)
            tenants[ctx.namespace] = ctx

    # ── Phase 3: each client uploads its document set; the pipeline runs
    # (vision/text/exec) for each upload exactly as it would on a real
    # file.written webhook. ────────────────────────────────────────────────────
    banner("PHASE 3   ingest documents → extract → aggregate → flag")
    rng = random.Random(0xF1_5C_0E)
    for ctx in tenants.values():
        print(f"\n── {ctx.client_id} ({ctx.namespace}) ──────────────────────")
        docs = fixtures_for_client(ctx.client_id)
        rng.shuffle(docs)   # interleave types to exercise different code paths
        for doc in docs:
            upload_and_dispatch(ctx, doc)

    # ── Phase 4: inspect filesystem state — the whole point of the demo ───────
    banner("PHASE 4   filesystem inspection — what the pipeline produced")
    for ctx in tenants.values():
        print(f"\n── {ctx.client_id} / namespace = {ctx.namespace} ──")
        tree = ctx.fs.exec("find . -type f -not -path './scripts/*' | sort | sed 's|^\\./|workspace/|'")
        for line in tree.splitlines():
            if line.strip():
                print(f"   {line}")
        # File count + total bytes give a feel for the "decent-size" filesystem.
        stats = ctx.fs.exec("echo files=$(find . -type f | wc -l) bytes=$(du -sb . 2>/dev/null | cut -f1)")
        print(f"   ├── {stats.strip()}")

    banner("PHASE 5   sample extracted outputs")
    for ctx in tenants.values():
        print(f"\n┌── {ctx.client_id}: an invoice extraction ──")
        invs = [f for f in ctx.fs.list_dir("workspace/extracted/invoices") if not f.is_dir]
        if invs:
            content = ctx.fs.read_text(invs[0].path)
            for line in content.splitlines()[:18]:
                print(f"│  {line}")
            print("│  …")

        print(f"├── {ctx.client_id}: monthly rollup ──")
        try:
            for m in ctx.fs.list_dir("workspace/reports/monthly"):
                if m.is_dir:
                    continue
                rep = json.loads(ctx.fs.read_text(m.path))
                print(f"│  {rep['month']}  total ${rep['total_usd']:>10,.2f}  "
                      f"(inv ${rep['invoices_usd']:>9,.2f}  "
                      f"rcp ${rep['receipts_usd']:>7,.2f}  "
                      f"exp ${rep['expenses_usd']:>9,.2f})")
        except TroveError as e:
            print(f"│  (no monthly reports yet: {e})")

        print(f"├── {ctx.client_id}: compliance flags ──")
        try:
            dup = json.loads(ctx.fs.read_text("workspace/flags/duplicate_invoices.json"))
            ovr = json.loads(ctx.fs.read_text("workspace/flags/over_threshold.json"))
            print(f"│  duplicate_invoices : {len(dup)} group(s)")
            for g in dup:
                print(f"│     → {g['vendor']}  ${g['total_usd']:,.2f}  ×{len(g['occurrences'])}")
            print(f"│  over_threshold     : {len(ovr)} invoice(s) > ${COMPLIANCE_THRESHOLD_USD:,}")
            for o in ovr:
                print(f"│     → {o.get('invoice_no')}  {o.get('vendor')}  ${o['total_usd']:,.2f}")
        except TroveError as e:
            print(f"│  (no flags: {e})")
        print("└──")

    banner("PHASE 6   audit retention — snapshots taken during the run")
    for ctx in tenants.values():
        snaps = ctx.fs.list_snapshots()
        run_snaps = [s for s in snaps if s.label and RUN_ID in s.label]
        print(f"   {ctx.client_id:14s} {len(run_snaps)} snapshot(s) this run "
              f"(total in namespace: {len(snaps)})")
        for s in run_snaps[:3]:
            print(f"      • {s.snapshot_id}  {s.size_bytes:>10,} bytes  {s.label}")

    banner("PHASE 7   audit log — tail of one tenant's pipeline.jsonl")
    sample = tenants["acme-corp"]
    log_text = sample.fs.read_text("workspace/audit/pipeline.jsonl")
    lines = [l for l in log_text.splitlines() if l.strip()]
    print(f"   {sample.client_id}: {len(lines)} audit entries — last 6:")
    for line in lines[-6:]:
        rec = json.loads(line)
        ev   = rec.pop("event")
        ts   = rec.pop("ts")
        rec.pop("client_id", None); rec.pop("namespace", None)
        detail = "  ".join(f"{k}={v}" for k, v in rec.items())
        print(f"   {ts}  {ev:<10s}  {detail[:90]}")

    banner("PHASE 8   isolation check — keys really are namespace-locked")
    other_ctx = tenants["globex-inc"]
    acme_key  = tenants["acme-corp"].api_key
    leaky = TroveClient(api_key=acme_key, namespace=other_ctx.namespace)
    try:
        leaky.list_dir("workspace/")
        print("   ✗ FAIL — acme-corp's key reached globex-inc's namespace!")
    except TroveError as e:
        print(f"   ✓ blocked: acme-corp's key cannot read globex-inc data")
        print(f"     {type(e).__name__}: {e}")

    banner("DONE")
    print("Each tenant's full pipeline state lives at workspace/ in their namespace.")
    print("Re-run any time — fixtures are deterministic, run_id keeps snapshots distinct.")


if __name__ == "__main__":
    main()
