"""Auth verbs: login (paste-based), logout, whoami.

Browser-based OAuth is a future upgrade — paste flow ships in 20 minutes and
covers the dev who's already in the dashboard about to copy a key anyway.
"""

from __future__ import annotations

import click
import httpx

from .. import config
from ..base import fetch_me, handle_errors


def _mask_key(api_key: str) -> str:
    if len(api_key) <= 8:
        return "***"
    return f"{api_key[:14]}…{api_key[-4:]}"


@click.command()
@click.option(
    "--profile",
    "profile_name",
    default="default",
    show_default=True,
    help="Profile name to save under.",
)
@click.option("--api-key", default=None, help="API key (or paste at the prompt).")
@click.option(
    "--workspace",
    "workspace_id",
    default=None,
    help="Workspace ID (ws-...). Auto-discovered via /v1/me if omitted.",
)
@click.option(
    "--namespace",
    default=None,
    help="Default namespace for runtime commands (run/ls/cat/...).",
)
@click.option("--base-url", default=config.DEFAULT_BASE_URL, show_default=True)
@handle_errors
def login(
    profile_name: str,
    api_key: str | None,
    workspace_id: str | None,
    namespace: str | None,
    base_url: str,
) -> None:
    """Save credentials so other commands can talk to the API.

    Get keys at https://trovefiles.dev/dashboard/keys. The CLI will call
    `/v1/me` to discover the workspace ID (and namespace lock, if any) so you
    only need to paste the API key.
    """
    if not api_key:
        click.echo("Get a key at https://trovefiles.dev/dashboard/keys.")
        api_key = click.prompt("API key", hide_input=True).strip()
    if not api_key:
        raise click.ClickException("missing API key")

    # Probe the API once to validate the key and (when missing) discover the
    # workspace_id via /v1/me. A typo blows up here, not on the next command.
    discovered_ns: str | None = None
    probe_profile = config.Profile(
        api_key=api_key,
        workspace_id=workspace_id or "ws-pending",
        base_url=base_url,
    )
    try:
        me = fetch_me(probe_profile, timeout=15.0)
    except httpx.HTTPStatusError as e:
        if e.response.status_code in (401, 403):
            raise click.ClickException(
                f"auth failed ({e.response.status_code}). "
                f"Double-check the key at https://trovefiles.dev/dashboard/keys."
            )
        raise

    if me:
        # /v1/me is authoritative for both workspace_id and namespace lock.
        api_workspace = me.get("workspace_id")
        if workspace_id and api_workspace and workspace_id != api_workspace:
            raise click.ClickException(
                f"--workspace {workspace_id} doesn't match this key's workspace "
                f"({api_workspace}). Drop --workspace to auto-detect."
            )
        workspace_id = workspace_id or api_workspace
        discovered_ns = me.get("namespace")
    elif not workspace_id:
        # Older API without /v1/me: fall back to a prompt + format check.
        workspace_id = click.prompt("Workspace ID (ws-...)").strip()

    if not workspace_id:
        raise click.ClickException("could not determine workspace_id")
    if not workspace_id.startswith("ws-"):
        raise click.ClickException(
            f"workspace_id should start with `ws-` (got {workspace_id!r})"
        )

    # Namespace baked into the profile is a convenience for runtime commands.
    # Order: explicit --namespace > discovered scope from /v1/me > unset.
    final_ns = namespace or discovered_ns

    config.save_profile(
        profile_name,
        config.Profile(
            api_key=api_key,
            workspace_id=workspace_id,
            base_url=base_url,
            namespace=final_ns,
        ),
    )

    bits = [f"workspace={workspace_id}"]
    if final_ns:
        bits.append(f"namespace={final_ns}")
    if me and me.get("scope"):
        bits.append(f"scope={me['scope']}")
    click.secho(
        f"saved profile '{profile_name}' ({_mask_key(api_key)})  " + "  ".join(bits),
        fg="green",
    )


@click.command()
@click.option("--profile", "profile_name", default="default", show_default=True)
@handle_errors
def logout(profile_name: str) -> None:
    """Forget a saved profile."""
    if config.delete_profile(profile_name):
        click.secho(f"removed profile '{profile_name}'", fg="green")
    else:
        click.secho(f"no profile '{profile_name}' to remove", fg="yellow")


@click.command()
@click.pass_context
@handle_errors
def whoami(ctx: click.Context) -> None:
    """Show the active profile, including the key's scope and namespace lock."""
    name, p = config.resolve(ctx.obj.get("profile") if ctx.obj else None)

    # Try /v1/me for live key metadata. If it fails, fall back to local-only.
    me: dict | None
    try:
        me = fetch_me(p)
    except (httpx.HTTPError, OSError):
        me = None

    click.echo(f"profile         : {name}")
    click.echo(f"workspace       : {p.workspace_id}")
    click.echo(f"api key         : {_mask_key(p.api_key)}")
    click.echo(f"base url        : {p.base_url}")

    if me:
        click.echo(f"key id          : {me.get('key_id', '-')}")
        click.echo(f"key name        : {me.get('key_name', '-')}")
        scope = me.get("scope") or "?"
        click.echo(f"scope           : {scope}")
        # Namespace lock from the key itself — overrides profile.namespace.
        ns_lock = me.get("namespace")
        if ns_lock:
            click.echo(
                f"namespace lock  : {ns_lock}  (key is scoped — cannot access other namespaces)"
            )
        elif p.namespace:
            click.echo(
                f"default namespace: {p.namespace}  (profile default; overridable with -n)"
            )
        else:
            click.echo(
                "default namespace: -  (set with `trove login --namespace <ns>` or $TROVE_NAMESPACE)"
            )
    else:
        # Could not reach /v1/me — show what we have locally.
        if p.namespace:
            click.echo(f"default namespace: {p.namespace}")
        click.secho(
            "note: /v1/me unreachable — scope/lock not shown", fg="yellow", err=True
        )
