"""``trove mcp`` — wire Trove into MCP-compatible AI clients.

The actual config-merging logic lives in :mod:`trove_sdk.mcp.install` so it's
testable without going through click. This module is the user-facing veneer.
"""

from __future__ import annotations

import json as _json
from typing import Optional

import click

from ...mcp import install as mcp_install
from .. import config
from ..base import handle_errors


def _ensure_mcp_extra() -> None:
    """Bail with a friendly message if the [mcp] extra isn't installed.

    The install/uninstall commands themselves don't need ``mcp`` (they only
    write JSON), but a working install does — so we probe up front to save
    the user from a confused Claude Desktop ten minutes later.
    """
    try:
        import mcp  # noqa: F401
    except ImportError:
        raise click.ClickException(
            "the [mcp] extra is not installed in this environment. Install with:\n"
            "    pip install 'trove-sdk[mcp]'\n"
            "or\n"
            "    uv add 'trove-sdk[mcp]'"
        )


@click.group("mcp")
def mcp() -> None:
    """Configure MCP-compatible AI clients (Claude Desktop, Cursor, Claude Code)."""


# ── install ───────────────────────────────────────────────────────────────────


@mcp.command("install")
@click.option(
    "--client",
    "clients",
    multiple=True,
    type=click.Choice(sorted(mcp_install.CLIENTS), case_sensitive=False),
    help="Client to configure. Repeatable. Defaults to every client detected on this machine.",
)
@click.option(
    "--namespace",
    "-n",
    default=None,
    help="Namespace the MCP server will use. Defaults to the active profile's namespace.",
)
@click.option(
    "--name",
    "server_name",
    default="trove",
    show_default=True,
    help="Name to register under in the client's config (mcpServers.<name>).",
)
@click.pass_context
@handle_errors
def install(
    ctx: click.Context,
    clients: tuple[str, ...],
    namespace: Optional[str],
    server_name: str,
) -> None:
    """Wire the active profile into your MCP clients.

    Reads the saved API key + base URL from the active ``trove`` profile and
    writes a server entry into each client's config file. Restart the client
    after running to pick up the change.

    \b
    Examples:
        trove mcp install                                # every detected client
        trove mcp install --client claude-desktop -n alice
        trove mcp install --client claude-code           # writes ./.mcp.json
    """
    _ensure_mcp_extra()

    profile_name = ctx.obj.get("profile") if ctx.obj else None
    name, profile = config.resolve(profile_name)

    ns = config.resolve_namespace(profile, namespace)
    if not ns:
        raise click.ClickException(
            "no namespace set. Pass `-n/--namespace`, set TROVE_NAMESPACE, "
            "or run `trove login --namespace <ns>` to bake one into the profile."
        )

    targets = list(clients) or mcp_install.detect_clients()
    if not targets:
        raise click.ClickException(
            "no MCP clients detected. Pass `--client claude-desktop|cursor|claude-code` explicitly."
        )

    entry = mcp_install.build_server_entry(
        api_key=profile.api_key,
        namespace=ns,
        base_url=profile.base_url,
    )

    wrote_any = False
    for c in targets:
        spec = mcp_install.CLIENTS[c]
        try:
            path = mcp_install.install_for_client(c, entry, server_name=server_name)
        except RuntimeError as e:
            click.secho(f"× {spec.label}: {e}", fg="red", err=True)
            continue
        click.secho(f"✓ {spec.label}: wrote '{server_name}' → {path}", fg="green")
        wrote_any = True

    if not wrote_any:
        raise click.ClickException("nothing was written.")

    click.echo()
    click.secho(
        f"profile={name}  namespace={ns}  workspace={profile.workspace_id}",
        fg="bright_black",
    )
    click.secho(
        "restart the client(s) above to load the new server.",
        fg="bright_black",
    )


# ── uninstall ─────────────────────────────────────────────────────────────────


@mcp.command("uninstall")
@click.option(
    "--client",
    "clients",
    multiple=True,
    type=click.Choice(sorted(mcp_install.CLIENTS), case_sensitive=False),
    help="Client to remove from. Repeatable. Defaults to every detected client.",
)
@click.option(
    "--name",
    "server_name",
    default="trove",
    show_default=True,
    help="Server name to remove (mcpServers.<name>).",
)
@handle_errors
def uninstall(clients: tuple[str, ...], server_name: str) -> None:
    """Remove the Trove server entry from MCP clients.

    Leaves any other servers in the config untouched.
    """
    targets = list(clients) or mcp_install.detect_clients()
    if not targets:
        raise click.ClickException(
            "no MCP clients detected. Pass `--client claude-desktop|cursor|claude-code` explicitly."
        )

    for c in targets:
        spec = mcp_install.CLIENTS[c]
        try:
            removed, path = mcp_install.uninstall_for_client(c, server_name=server_name)
        except RuntimeError as e:
            click.secho(f"× {spec.label}: {e}", fg="red", err=True)
            continue
        if removed:
            click.secho(f"✓ {spec.label}: removed '{server_name}' from {path}", fg="green")
        else:
            click.secho(
                f"- {spec.label}: '{server_name}' not present at {path}",
                fg="bright_black",
            )


# ── status ────────────────────────────────────────────────────────────────────


@mcp.command("status")
@click.option(
    "--name",
    "server_name",
    default="trove",
    show_default=True,
    help="Server name to look up (mcpServers.<name>).",
)
@click.option("--json", "json_mode", is_flag=True, help="Emit raw JSON.")
@handle_errors
def status(server_name: str, json_mode: bool) -> None:
    """Show which MCP clients have the Trove server configured."""
    rows: list[dict] = []
    for key, spec in mcp_install.CLIENTS.items():
        path = spec.config_path()
        entry = mcp_install.status_for_client(key, server_name=server_name)
        rows.append(
            {
                "client": key,
                "label": spec.label,
                "path": str(path),
                "config_exists": path.exists(),
                "configured": entry is not None,
                "namespace": (entry or {}).get("env", {}).get("TROVE_NAMESPACE"),
            }
        )

    if json_mode:
        click.echo(_json.dumps({"server_name": server_name, "clients": rows}, indent=2))
        return

    for r in rows:
        if r["configured"]:
            ns = r["namespace"] or "?"
            click.secho(
                f"✓ {r['label']:<24}  namespace={ns}  ({r['path']})", fg="green"
            )
        elif r["config_exists"]:
            click.secho(
                f"- {r['label']:<24}  not configured  ({r['path']})",
                fg="bright_black",
            )
        else:
            click.secho(
                f"  {r['label']:<24}  client not detected", fg="bright_black"
            )
