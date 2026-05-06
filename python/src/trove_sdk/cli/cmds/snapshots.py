"""Snapshot management for the active namespace.

These wrap the runtime endpoints (`/v1/snapshots*`), so they need a
workspace-scope key + a resolvable namespace — the same as `trove run`.
"""

from __future__ import annotations

import json as _json
from datetime import datetime
from typing import Optional

import click

from ..base import get_runtime_client, handle_errors


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


def _fmt_local(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.astimezone().strftime("%Y-%m-%d %H:%M")
    except Exception:
        return iso or "-"


@click.group("snapshot")
def snapshot() -> None:
    """Snapshot the current namespace state and restore it later."""


@snapshot.command("create")
@click.option(
    "--label",
    default=None,
    help="Free-form label (max 128 chars) shown in `snapshot list`.",
)
@click.option("--namespace", "-n", default=None, help="Override the profile namespace.")
@click.option("--json", "json_mode", is_flag=True, help="Emit raw JSON.")
@click.pass_context
@handle_errors
def create_cmd(
    ctx: click.Context, label: Optional[str], namespace: Optional[str], json_mode: bool
) -> None:
    """Tar the namespace and upload it to the snapshot bucket."""
    client, _, _, ns = get_runtime_client(ctx, namespace)
    try:
        # Snapshot can take a while for large namespaces; bump above the default.
        r = client.post("/v1/snapshots", json={"label": label}, timeout=120.0)
        r.raise_for_status()
        body = r.json()

        if json_mode:
            click.echo(_json.dumps(body))
            return

        click.secho(
            f"✓ snapshot {body.get('snapshot_id')}  "
            f"({_fmt_bytes(body.get('size_bytes'))})  "
            f"namespace={ns}",
            fg="green",
        )
    finally:
        client.close()


@snapshot.command("list")
@click.option("--namespace", "-n", default=None, help="Override the profile namespace.")
@click.option("--json", "json_mode", is_flag=True, help="Emit raw JSON.")
@click.pass_context
@handle_errors
def list_cmd(ctx: click.Context, namespace: Optional[str], json_mode: bool) -> None:
    """List snapshots for the active namespace (newest first)."""
    client, _, _, ns = get_runtime_client(ctx, namespace)
    try:
        r = client.get("/v1/snapshots")
        r.raise_for_status()
        body = r.json()
        items = body.get("snapshots", []) or []

        if json_mode:
            click.echo(_json.dumps(body))
            return

        if not items:
            click.secho(f"(no snapshots in namespace {ns})", fg="bright_black")
            return

        click.echo(f"{'SNAPSHOT ID':<26}  {'SIZE':>10}  {'CREATED':<16}  LABEL")
        for s in items:
            click.echo(
                f"{s.get('snapshot_id', '-'):<26}  "
                f"{_fmt_bytes(s.get('size_bytes')):>10}  "
                f"{_fmt_local(s.get('created_at', '')):<16}  "
                f"{s.get('label') or '-'}"
            )
    finally:
        client.close()


@snapshot.command("restore")
@click.argument("snapshot_id")
@click.option("--namespace", "-n", default=None, help="Override the profile namespace.")
@click.option("--yes", "-y", is_flag=True, help="Skip the confirmation prompt.")
@click.pass_context
@handle_errors
def restore_cmd(
    ctx: click.Context, snapshot_id: str, namespace: Optional[str], yes: bool
) -> None:
    """Wipe the namespace and restore from a snapshot. Destructive.

    All current files in the namespace are deleted, then the snapshot's tarball
    is extracted in their place. There is no automatic safety snapshot — if
    you might want today's state back, run `trove snapshot create` first.
    """
    client, _, _, ns = get_runtime_client(ctx, namespace)
    if not yes:
        click.confirm(
            f"restore {snapshot_id} into namespace {ns}? "
            f"This will WIPE current contents.",
            abort=True,
        )
    try:
        r = client.post(f"/v1/snapshots/{snapshot_id}/restore", timeout=180.0)
        if r.status_code == 404:
            raise click.ClickException(f"snapshot not found: {snapshot_id}")
        r.raise_for_status()
        body = r.json()
        click.secho(
            f"restored {snapshot_id} → namespace {ns}  "
            f"(files_restored={body.get('files_restored')})",
            fg="green",
        )
    finally:
        client.close()


@snapshot.command("delete")
@click.argument("snapshot_id")
@click.option("--namespace", "-n", default=None, help="Override the profile namespace.")
@click.option("--yes", "-y", is_flag=True, help="Skip the confirmation prompt.")
@click.pass_context
@handle_errors
def delete_cmd(
    ctx: click.Context, snapshot_id: str, namespace: Optional[str], yes: bool
) -> None:
    """Delete a snapshot from S3."""
    if not yes:
        click.confirm(f"delete snapshot {snapshot_id}?", abort=True)
    client, _, _, _ = get_runtime_client(ctx, namespace)
    try:
        r = client.delete(f"/v1/snapshots/{snapshot_id}")
        if r.status_code == 404:
            raise click.ClickException(f"snapshot not found: {snapshot_id}")
        r.raise_for_status()
        click.secho(f"deleted {snapshot_id}", fg="red")
    finally:
        client.close()
