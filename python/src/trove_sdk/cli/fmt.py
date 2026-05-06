"""Pretty-printing helpers for events. Keep colors readable on dark + light terms."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Optional

import click

# Color per event type. Read like a lightboard: green = wrote, red = deleted,
# yellow = exec, cyan = snapshot lifecycle, blue = workspace lifecycle,
# magenta = security.
_TYPE_COLORS = {
    "file.written": "green",
    "file.deleted": "red",
    "exec.completed": "yellow",
    "snapshot.created": "cyan",
    "snapshot.restored": "cyan",
    "snapshot.deleted": "cyan",
    "namespace.deleted": "red",
    "workspace.created": "blue",
    "key.created": "magenta",
    "key.revoked": "magenta",
    "webhook.test": "white",
}


def _parse_iso_utc(iso: str) -> Optional[datetime]:
    """Parse an ISO-8601 UTC timestamp from the API. None if unparseable."""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _short_time(iso: str) -> str:
    """Render an event timestamp in the user's local timezone.

    Today's events show `HH:MM:SS`. Older events get a `MM-DD ` prefix so the
    log doesn't look stuck in a single day. JSON mode bypasses this — the raw
    ISO string is preserved for downstream tooling.
    """
    dt = _parse_iso_utc(iso)
    if dt is None:
        return iso[:8] if iso else ""
    local = dt.astimezone()  # convert UTC → system local
    today = datetime.now(local.tzinfo).date()
    if local.date() == today:
        return local.strftime("%H:%M:%S")
    return local.strftime("%m-%d %H:%M:%S")


def _short_ns(ns: Optional[str]) -> str:
    if not ns:
        return "-"
    return ns if len(ns) <= 16 else ns[:13] + "…"


def _summary(event: dict, *, verbose: bool = False) -> str:
    """One-line human summary of the event's `data` payload.

    `verbose=True` expands `exec.completed` to the full command + first line of
    stdout (a `-v` lift). Other event types ignore the flag for now — their
    summaries are already short.
    """
    t = event.get("type", "")
    data = event.get("data", {}) or {}
    if t == "file.written":
        path = data.get("path", "?")
        size = data.get("size_bytes")
        return f"{path}" + (f"  ({_fmt_bytes(size)})" if size is not None else "")
    if t == "file.deleted":
        return data.get("path", "?")
    if t == "exec.completed":
        cmd = (data.get("command") or "").strip().replace("\n", " ")
        if not verbose and len(cmd) > 60:
            cmd = cmd[:57] + "…"
        exit_code = data.get("exit_code", "?")
        ms = data.get("duration_ms")
        tail = f"  exit={exit_code}"
        if ms is not None:
            tail += f"  ({ms}ms)"
        line = f"{cmd}{tail}"
        if verbose:
            stdout = (data.get("stdout") or "").strip()
            first = stdout.split("\n", 1)[0] if stdout else ""
            if first:
                line += f"\n    > {first}"
        return line
    if t == "snapshot.created":
        return f"{data.get('snapshot_id', '?')}  label={data.get('label') or '-'}"
    if t == "snapshot.restored":
        return (
            f"{data.get('snapshot_id', '?')}  files={data.get('files_restored', '?')}"
        )
    if t == "snapshot.deleted":
        return data.get("snapshot_id", "?")
    if t == "key.created":
        return f"{data.get('key_id', '?')}  name={data.get('name', '-')}"
    if t == "key.revoked":
        return data.get("key_id", "?")
    if t == "namespace.deleted":
        return data.get("namespace") or "?"
    if t == "webhook.test":
        return data.get("message", "")
    return json.dumps(data, separators=(",", ":"))[:80]


def _fmt_bytes(n: object) -> str:
    try:
        n_int = int(n)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "?"
    if n_int < 1024:
        return f"{n_int}B"
    if n_int < 1024 * 1024:
        return f"{n_int / 1024:.1f}KB"
    if n_int < 1024 * 1024 * 1024:
        return f"{n_int / 1024 / 1024:.1f}MB"
    return f"{n_int / 1024 / 1024 / 1024:.2f}GB"


def print_event(event: dict, *, json_mode: bool = False, verbose: bool = False) -> None:
    if json_mode:
        click.echo(json.dumps(event, separators=(",", ":")))
        sys.stdout.flush()
        return
    t = event.get("type", "")
    color = _TYPE_COLORS.get(t, "white")
    line = (
        f"{_short_time(event.get('created_at', '')):<14}  "
        f"{click.style(f'{t:<18}', fg=color, bold=True)}  "
        f"{_short_ns(event.get('namespace')):<16}  "
        f"{_summary(event, verbose=verbose)}"
    )
    click.echo(line)
    sys.stdout.flush()
