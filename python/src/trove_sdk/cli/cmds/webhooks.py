"""Webhook subscription management.

Wraps GET/POST/DELETE/test on /v1/workspaces/{ws}/webhooks. Requires admin
scope (the API enforces this — we surface the 403 clearly when it happens).
"""

from __future__ import annotations

import json as _json
from datetime import datetime
from typing import Optional

import click
import httpx

from ..base import get_client, handle_errors


def _fmt_local(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.astimezone().strftime("%Y-%m-%d %H:%M")
    except Exception:
        return iso or "-"


@click.group("webhooks")
def webhooks() -> None:
    """Subscribe URLs to workspace events."""


@webhooks.command("list")
@click.option("--json", "json_mode", is_flag=True, help="Emit raw JSON.")
@click.pass_context
@handle_errors
def list_cmd(ctx: click.Context, json_mode: bool) -> None:
    """List registered webhook endpoints."""
    client, profile, _ = get_client(ctx)
    try:
        r = client.get(f"/v1/workspaces/{profile.workspace_id}/webhooks")
        r.raise_for_status()
        body = r.json()
        items = body.get("webhooks", []) or []

        if json_mode:
            click.echo(_json.dumps(body))
            return

        if not items:
            click.secho(
                "(no webhooks. Create one with `trove webhooks create <url>`)",
                fg="bright_black",
            )
            return

        click.echo(
            f"{'WEBHOOK ID':<20}  {'EVENTS':<28}  "
            f"{'NAMESPACE':<12}  {'CREATED':<16}  URL"
        )
        for w in items:
            evs = ",".join(w.get("events") or ["*"])
            ns = w.get("namespace") or "-"
            click.echo(
                f"{w.get('webhook_id', '-'):<20}  "
                f"{evs[:28]:<28}  "
                f"{ns[:12]:<12}  "
                f"{_fmt_local(w.get('created_at', '')):<16}  "
                f"{w.get('url', '-')}"
            )
            # Surface the most recent delivery health under each row.
            last_ok = w.get("last_delivery_ok")
            last_at = w.get("last_delivery_at")
            if last_at is not None:
                status = w.get("last_delivery_status")
                tag = "✓" if last_ok else "✗"
                color = "green" if last_ok else "red"
                click.secho(
                    f"  └─ last delivery {tag}  status={status or '-'}  "
                    f"at {_fmt_local(last_at)}",
                    fg=color,
                )
    finally:
        client.close()


@webhooks.command("create")
@click.argument("url")
@click.option(
    "--events", default=None, help="Comma-separated event types. Default: all (`*`)."
)
@click.option(
    "--namespace", default=None, help="Only fire for this namespace (default: all)."
)
@click.option(
    "--description", default=None, help="Free-form note shown in the dashboard."
)
@click.option("--json", "json_mode", is_flag=True, help="Emit raw JSON.")
@click.pass_context
@handle_errors
def create_cmd(
    ctx: click.Context,
    url: str,
    events: Optional[str],
    namespace: Optional[str],
    description: Optional[str],
    json_mode: bool,
) -> None:
    """Subscribe a URL to events. The signing secret is printed once."""
    event_list = [e.strip() for e in events.split(",")] if events else ["*"]
    client, profile, _ = get_client(ctx)
    try:
        r = client.post(
            f"/v1/workspaces/{profile.workspace_id}/webhooks",
            json={
                "url": url,
                "events": event_list,
                "namespace": namespace,
                "description": description,
            },
        )
        r.raise_for_status()
        body = r.json()

        if json_mode:
            click.echo(_json.dumps(body))
            return

        click.secho(
            "✓ webhook created (signing secret shown once — save it now)",
            fg="green",
            bold=True,
        )
        click.echo()
        click.echo(f"  webhook_id     : {body.get('webhook_id')}")
        click.echo(f"  url            : {body.get('url')}")
        click.echo(f"  events         : {','.join(body.get('events') or ['*'])}")
        click.echo(f"  namespace      : {body.get('namespace') or '-'}")
        click.echo(f"  signing_secret : {body.get('signing_secret')}")
        click.echo()
        click.secho(
            "  Verify deliveries with `verify_webhook(secret=..., body=..., "
            "signature_header=request.headers['X-Trove-Signature'])`.",
            fg="bright_black",
        )
    finally:
        client.close()


@webhooks.command("delete")
@click.argument("webhook_id")
@click.option("--yes", "-y", is_flag=True, help="Skip the confirmation prompt.")
@click.pass_context
@handle_errors
def delete_cmd(ctx: click.Context, webhook_id: str, yes: bool) -> None:
    """Remove a webhook endpoint."""
    if not yes:
        click.confirm(f"delete {webhook_id}?", abort=True)
    client, profile, _ = get_client(ctx)
    try:
        r = client.delete(
            f"/v1/workspaces/{profile.workspace_id}/webhooks/{webhook_id}"
        )
        if r.status_code == 404:
            raise click.ClickException(f"webhook not found: {webhook_id}")
        r.raise_for_status()
        click.secho(f"deleted {webhook_id}", fg="red")
    finally:
        client.close()


@webhooks.command("listen")
@click.option(
    "--forward-to",
    default="http://localhost:8000/trove/events",
    show_default=True,
    help="Local URL to forward signed events to.",
)
@click.option(
    "--secret",
    required=True,
    envvar="TROVE_WEBHOOK_SECRET",
    help="Signing secret from `trove webhooks create`.",
)
@click.pass_context
@handle_errors
def listen_cmd(ctx: click.Context, forward_to: str, secret: str) -> None:
    """Forward live workspace events to a local handler (no ngrok needed).

    Connects to the Trove SSE stream, re-signs each event with your webhook
    secret, and POSTs it to --forward-to so your local verify_webhook() path
    runs exactly as it will in production.

    \b
    Example:
        export TROVE_WEBHOOK_SECRET=trove-whsec-...
        trove webhooks listen --forward-to http://localhost:8000/trove/events
    """
    import hmac as _hmac
    import hashlib as _hashlib
    import time as _time
    import urllib.parse

    parsed = urllib.parse.urlparse(forward_to)
    if parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise click.ClickException(
            "--forward-to must be a localhost URL (prevents accidental "
            "forwarding of live events to external hosts)"
        )

    client, profile, _ = get_client(ctx)

    def _sign_and_forward(body: bytes, event_type: str, event_id: str) -> int:
        timestamp = int(_time.time())
        payload_bytes = f"{timestamp}.".encode() + body
        digest = _hmac.new(secret.encode(), payload_bytes, _hashlib.sha256).hexdigest()
        sig = f"t={timestamp},v1={digest}"
        with httpx.Client() as fwd:
            r = fwd.post(
                forward_to,
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Trove-Signature": sig,
                    "X-Trove-Event": event_type,
                    "X-Trove-Event-Id": event_id,
                },
                timeout=5.0,
            )
        return r.status_code

    stream_url = f"/v1/workspaces/{profile.workspace_id}/webhooks/stream"
    click.secho(f"✓ connected to workspace {profile.workspace_id}", fg="green")
    click.secho(f"  forwarding → {forward_to}", fg="bright_black")
    click.secho("  Press Ctrl-C to stop.\n", fg="bright_black")

    try:
        with client.stream("GET", stream_url, timeout=None) as resp:
            resp.raise_for_status()
            buffer = ""
            for chunk in resp.iter_text():
                buffer += chunk
                while "\n\n" in buffer:
                    block, buffer = buffer.split("\n\n", 1)
                    for line in block.splitlines():
                        if not line.startswith("data: "):
                            continue
                        raw = line[6:].strip()
                        if not raw:
                            continue
                        try:
                            payload = _json.loads(raw)
                        except _json.JSONDecodeError:
                            continue
                        ev_type = payload.get("type", "unknown")
                        ev_id = payload.get("id", "-")
                        # skip the synthetic connected event
                        if ev_type == "connected":
                            continue
                        body_bytes = raw.encode()
                        try:
                            status = _sign_and_forward(body_bytes, ev_type, ev_id)
                            tag = "✓" if 200 <= status < 300 else "✗"
                            color = "green" if 200 <= status < 300 else "red"
                            click.secho(
                                f"  {tag}  {ev_type:<22} {ev_id}  →  {status}",
                                fg=color,
                            )
                        except Exception as e:
                            click.secho(f"  ✗  forward failed: {e}", fg="red")
    except KeyboardInterrupt:
        click.echo("\nStopped.")
    finally:
        client.close()


@webhooks.command("test")
@click.argument("webhook_id")
@click.option("--json", "json_mode", is_flag=True, help="Emit raw JSON.")
@click.pass_context
@handle_errors
def test_cmd(ctx: click.Context, webhook_id: str, json_mode: bool) -> None:
    """Fire a `webhook.test` event at the endpoint and report the result."""
    client, profile, _ = get_client(ctx)
    try:
        r = client.post(
            f"/v1/workspaces/{profile.workspace_id}/webhooks/{webhook_id}/test",
            timeout=30.0,
        )
        if r.status_code == 404:
            raise click.ClickException(f"webhook not found: {webhook_id}")
        r.raise_for_status()
        body = r.json()

        if json_mode:
            click.echo(_json.dumps(body))
            return

        ok = bool(body.get("ok"))
        if ok:
            click.secho(
                f"✓ delivered  status={body.get('status')}  "
                f"event_id={body.get('event_id')}",
                fg="green",
            )
        else:
            click.secho(
                f"✗ failed  status={body.get('status') or '-'}  "
                f"error={body.get('error') or '-'}",
                fg="red",
            )
    finally:
        client.close()
