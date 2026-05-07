"""``trove mcp`` — wire Trove into MCP-compatible AI clients.

The actual config-merging logic lives in :mod:`trove_sdk.mcp.install` so it's
testable without going through click. This module is the user-facing veneer.
"""

from __future__ import annotations

import json as _json
import sys as _sys
from typing import Optional

import click

from ...mcp import install as mcp_install
from ...mcp import upgrade as mcp_upgrade
from .. import config
from ..base import handle_errors


# How users restart MCP clients differs per OS — Claude Desktop on macOS
# uses Cmd-Q; on Windows the window-close button leaves a tray process
# alive that keeps the old MCP subprocess running. Most "I upgraded but
# it didn't take" reports are this.
def _restart_hint() -> str:
    if _sys.platform == "darwin":
        return "fully quit the client (Cmd-Q) and reopen — closing the window isn't enough"
    if _sys.platform == "win32":
        return (
            "fully quit the client (right-click tray icon → Exit) and reopen — "
            "closing the window isn't enough"
        )
    return "fully quit the client and reopen — closing the window isn't enough"


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
    click.secho(f"⚠ {_restart_hint()}.", fg="yellow")
    click.secho(
        "to update Trove later, run `trove mcp upgrade`.",
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
@click.option(
    "--check-updates/--no-check-updates",
    default=True,
    show_default=True,
    help="Query PyPI for the latest version and flag stale installs.",
)
@handle_errors
def status(server_name: str, json_mode: bool, check_updates: bool) -> None:
    """Show which MCP clients have the Trove server configured.

    By default also checks each configured client's installed trove-sdk
    version against the latest on PyPI and flags stale installs with a
    one-line ``trove mcp upgrade`` hint. Pass ``--no-check-updates`` to
    skip the PyPI lookup if you're offline or in a hurry.
    """
    latest = mcp_upgrade.latest_pypi_version() if check_updates else None

    rows: list[dict] = []
    any_stale = False
    for key, spec in mcp_install.CLIENTS.items():
        path = spec.config_path()
        entry = mcp_install.status_for_client(key, server_name=server_name)
        cmd_path = (entry or {}).get("command")
        installed: Optional[str] = None
        env_kind: Optional[str] = None
        if entry and check_updates and cmd_path:
            from pathlib import Path as _P
            installed = mcp_upgrade.installed_version_for_python(_P(cmd_path))
            env_kind = mcp_upgrade.detect_env_kind(_P(cmd_path)).key
        is_stale = bool(latest and installed and installed != latest)
        if is_stale:
            any_stale = True
        rows.append(
            {
                "client": key,
                "label": spec.label,
                "path": str(path),
                "config_exists": path.exists(),
                "configured": entry is not None,
                "namespace": (entry or {}).get("env", {}).get("TROVE_NAMESPACE"),
                "command": cmd_path,
                "installed_version": installed,
                "latest_version": latest,
                "env_kind": env_kind,
                "stale": is_stale,
            }
        )

    if json_mode:
        click.echo(_json.dumps({"server_name": server_name, "clients": rows}, indent=2))
        return

    for r in rows:
        if r["configured"]:
            ns = r["namespace"] or "?"
            ver_tag = ""
            if r["installed_version"]:
                if r["stale"]:
                    ver_tag = f"  trove-sdk {r['installed_version']} (latest {r['latest_version']} — run `trove mcp upgrade`)"
                else:
                    ver_tag = f"  trove-sdk {r['installed_version']}"
            elif check_updates and r["command"]:
                ver_tag = "  (could not read trove-sdk version)"
            click.secho(
                f"✓ {r['label']:<24}  namespace={ns}{ver_tag}", fg="green"
            )
            click.secho(f"    {r['path']}", fg="bright_black")
        elif r["config_exists"]:
            click.secho(
                f"- {r['label']:<24}  not configured  ({r['path']})",
                fg="bright_black",
            )
        else:
            click.secho(
                f"  {r['label']:<24}  client not detected", fg="bright_black"
            )

    if any_stale:
        click.echo()
        click.secho(
            "→ run `trove mcp upgrade` to update, then "
            f"{_restart_hint()}.",
            fg="yellow",
        )


# ── upgrade ───────────────────────────────────────────────────────────────────


@mcp.command("upgrade")
@click.option(
    "--name",
    "server_name",
    default="trove",
    show_default=True,
    help="Server name to look up (mcpServers.<name>).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print what would be done without running anything.",
)
@click.option("--json", "json_mode", is_flag=True, help="Emit raw JSON.")
@handle_errors
def upgrade(server_name: str, dry_run: bool, json_mode: bool) -> None:
    """Update the trove-sdk Python that each configured MCP client launches.

    \b
    Why this exists:
      `trove mcp install` writes the absolute path of the Python that ran
      it into the client config — typically a uv-tool or pipx env, not
      your shell's default. A plain `pip install --upgrade` from your
      shell would update the wrong env and the client would keep using
      the old version. This command finds the right env per client and
      runs the upgrade command for that env type (uv tool / pipx / pip).

    \b
    Examples:
      trove mcp upgrade               # upgrade every configured client
      trove mcp upgrade --dry-run     # show what would run, don't run it
    """
    installs = mcp_upgrade.discover_installs(server_name=server_name)
    if not installs:
        raise click.ClickException(
            "no clients have a Trove server configured. Run `trove mcp install` first."
        )

    # Some clients share the same Python (e.g. user installed once via
    # uv-tool and wired both Claude Desktop and Cursor to it). Upgrading
    # twice is wasteful and confusing — dedupe by python_path.
    seen: set[str] = set()
    unique: list[mcp_upgrade.ClientInstall] = []
    shared_with: dict[str, list[str]] = {}
    for ins in installs:
        key = str(ins.python_path) if ins.python_path else f"<no-path:{ins.client_key}>"
        if key in seen:
            shared_with.setdefault(key, []).append(ins.label)
            continue
        seen.add(key)
        shared_with.setdefault(key, []).append(ins.label)
        unique.append(ins)

    results: list[mcp_upgrade.UpgradeResult] = []
    for ins in unique:
        click.secho(
            f"→ {ins.label} ({ins.env_kind.label}): "
            f"trove-sdk {ins.installed_version or '?'} at {ins.python_path}",
            fg="bright_black",
        )
        r = mcp_upgrade.upgrade_install(ins, dry_run=dry_run)
        results.append(r)
        fg = "green" if r.succeeded else "red"
        marker = "✓" if r.succeeded else "×"
        click.secho(f"  {marker} {r.message}", fg=fg)
        # If multiple clients share this Python, the upgrade applies to all of them.
        # Show this for both dry-run and real runs — users want to know coverage.
        key = str(ins.python_path) if ins.python_path else f"<no-path:{ins.client_key}>"
        peers = [lbl for lbl in shared_with[key] if lbl != ins.label]
        if peers and r.succeeded:
            click.secho(
                f"    (also applies to: {', '.join(peers)} — same Python)",
                fg="bright_black",
            )

    if json_mode:
        payload = {
            "results": [
                {
                    "client": r.install.client_key,
                    "label": r.install.label,
                    "python": str(r.install.python_path) if r.install.python_path else None,
                    "env_kind": r.install.env_kind.key,
                    "ran": r.ran,
                    "succeeded": r.succeeded,
                    "previous_version": r.install.installed_version,
                    "new_version": r.new_version,
                    "message": r.message,
                }
                for r in results
            ],
            "dry_run": dry_run,
        }
        click.echo(_json.dumps(payload, indent=2))

    any_succeeded = any(r.succeeded for r in results) and not dry_run
    any_failed = any(not r.succeeded for r in results)
    if any_succeeded:
        click.echo()
        click.secho(f"⚠ {_restart_hint()}.", fg="yellow")
    if any_failed:
        # Non-zero exit when any upgrade actually failed (not dry-run skips)
        # so CI / scripts can branch.
        raise click.ClickException("one or more upgrades failed; see messages above")
