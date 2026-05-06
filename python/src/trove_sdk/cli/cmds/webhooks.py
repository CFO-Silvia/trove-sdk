"""Webhook subscription management.

Wraps GET/POST/DELETE/test on /v1/workspaces/{ws}/webhooks. Requires admin
scope (the API enforces this — we surface the 403 clearly when it happens).
"""

from __future__ import annotations

import json as _json
from datetime import datetime
from typing import Optional

import click

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
