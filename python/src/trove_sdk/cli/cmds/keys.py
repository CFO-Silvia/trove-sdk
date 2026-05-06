"""Key management: list, create, revoke. Requires an admin-scope key.

Workspace-scoped keys can call this group, but the API will reject mutations
with HTTP 403. We surface a clearer error than the raw HTTP message when we
can detect the scope mismatch up front via /v1/me.
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


@click.group("keys")
def keys() -> None:
    """Manage workspace API keys (admin scope required for mutations)."""


@keys.command("list")
@click.option("--json", "json_mode", is_flag=True, help="Emit raw JSON.")
@click.pass_context
@handle_errors
def list_cmd(ctx: click.Context, json_mode: bool) -> None:
    """List active keys for the workspace."""
    client, profile, _ = get_client(ctx)
    try:
        r = client.get(f"/v1/workspaces/{profile.workspace_id}/keys")
        r.raise_for_status()
        body = r.json()
        items = body.get("keys", []) or []

        if json_mode:
            click.echo(_json.dumps(body))
            return

        if not items:
            click.secho("(no keys)", fg="bright_black")
            return

        # Header — pad widths to keep tabular alignment without pulling tabulate.
        click.echo(
            f"{'KEY ID':<20}  {'NAME':<20}  {'SCOPE':<10}  "
            f"{'NAMESPACE':<16}  {'CREATED':<16}  PREFIX"
        )
        for k in items:
            ns = k.get("namespace") or "-"
            click.echo(
                f"{k.get('key_id', '-'):<20}  "
                f"{(k.get('name') or '-')[:20]:<20}  "
                f"{k.get('scope', '-'):<10}  "
                f"{ns[:16]:<16}  "
                f"{_fmt_local(k.get('created_at', '')):<16}  "
                f"{k.get('prefix', '-')}"
            )
    finally:
        client.close()


@keys.command("create")
@click.argument("name")
@click.option(
    "--namespace",
    default=None,
    help="Lock the key to one namespace (workspace scope only).",
)
@click.option(
    "--admin",
    is_flag=True,
    help="Mint an admin key. Cannot be combined with --namespace.",
)
@click.option("--json", "json_mode", is_flag=True, help="Emit raw JSON.")
@click.pass_context
@handle_errors
def create_cmd(
    ctx: click.Context,
    name: str,
    namespace: Optional[str],
    admin: bool,
    json_mode: bool,
) -> None:
    """Mint a new API key. The plaintext key is printed once — copy it now."""
    if admin and namespace:
        raise click.ClickException("--admin and --namespace are mutually exclusive")

    client, profile, _ = get_client(ctx)
    try:
        r = client.post(
            f"/v1/workspaces/{profile.workspace_id}/keys",
            json={"name": name, "namespace": namespace, "admin": admin},
        )
        r.raise_for_status()
        body = r.json()

        if json_mode:
            click.echo(_json.dumps(body))
            return

        # `api_key` is shown ONCE. Make it impossible to miss.
        click.secho(
            "✓ key created (this secret is shown once — copy it now)",
            fg="green",
            bold=True,
        )
        click.echo()
        click.echo(f"  api_key   : {body.get('api_key')}")
        click.echo(f"  key_id    : {body.get('key_id')}")
        click.echo(f"  name      : {body.get('name')}")
        click.echo(f"  scope     : {body.get('scope')}")
        click.echo(f"  namespace : {body.get('namespace') or '-'}")
        click.echo(f"  prefix    : {body.get('prefix')}")
    finally:
        client.close()


@keys.command("revoke")
@click.argument("key_id")
@click.option("--yes", "-y", is_flag=True, help="Skip the confirmation prompt.")
@click.pass_context
@handle_errors
def revoke_cmd(ctx: click.Context, key_id: str, yes: bool) -> None:
    """Revoke a key by ID. Takes effect immediately and cannot be undone."""
    if not yes:
        click.confirm(f"revoke {key_id}?", abort=True)
    client, profile, _ = get_client(ctx)
    try:
        r = client.delete(f"/v1/workspaces/{profile.workspace_id}/keys/{key_id}")
        if r.status_code == 404:
            raise click.ClickException(f"key not found: {key_id}")
        r.raise_for_status()
        click.secho(f"revoked {key_id}", fg="red")
    finally:
        client.close()
