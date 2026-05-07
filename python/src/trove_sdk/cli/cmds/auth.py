"""Auth verbs: login (device-code or paste), logout, whoami.

`trove login` defaults to a browser-based device-code flow: the CLI prints a
short user code, opens the dashboard, the user approves there, and the CLI
collects a freshly minted API key by polling. Pass `--api-key` (or pipe one
on stdin) to skip the browser dance — useful for CI and headless boxes.
"""

from __future__ import annotations

import sys
import time
import webbrowser

import click
import httpx

from .. import config
from ..base import fetch_me, handle_errors


def _mask_key(api_key: str) -> str:
    if len(api_key) <= 8:
        return "***"
    return f"{api_key[:14]}…{api_key[-4:]}"


def _device_login(base_url: str, *, open_browser: bool = True) -> tuple[str, str]:
    """Run a device-code login against `base_url`.

    Returns `(api_key, workspace_id)`. Raises `click.ClickException` on
    timeout, denial, or network failure.

    Connects to three endpoints:
      POST /v1/auth/device/start   → device_code, user_code, verify URL, interval
      POST /v1/auth/device/poll    → status: pending | approved | denied | expired
    The browser-side approval lives at `verification_uri_complete`.
    """
    with httpx.Client(base_url=base_url, timeout=30.0) as client:
        try:
            r = client.post("/v1/auth/device/start")
        except httpx.HTTPError as e:
            raise click.ClickException(
                f"could not start login at {base_url}: {e}\n"
                f"Pass --api-key to skip the browser flow, or `--base-url` for a different host."
            )
        if r.status_code == 404:
            # Server is older than the device-flow rollout — fall back caller-side.
            raise click.ClickException(
                "this Trove server doesn't support browser login yet. "
                "Pass --api-key (or set TROVE_API_KEY) to log in with a key from the dashboard."
            )
        r.raise_for_status()
        start = r.json()

        device_code  = start["device_code"]
        user_code    = start["user_code"]
        verify_url   = start.get("verification_uri_complete") or start["verification_uri"]
        interval     = max(1, int(start.get("interval", 2)))
        expires_in   = int(start.get("expires_in", 600))

        click.echo("")
        click.echo("Opening your browser to authorize this CLI…")
        click.echo("")
        click.secho(f"  Code: {user_code}", fg="cyan", bold=True)
        click.echo(f"  URL:  {verify_url}")
        click.echo("")
        click.echo("Confirm the code matches in your browser, then approve.")
        click.echo("(Pass --api-key or pipe a key on stdin to skip the browser flow.)")
        click.echo("")

        if open_browser:
            try:
                webbrowser.open(verify_url, new=2)
            except Exception:
                # Headless boxes or weird desktop configs — the URL is already
                # printed above so the user can copy/paste manually.
                pass

        deadline = time.monotonic() + expires_in
        # Render a quiet progress message so the wait doesn't look frozen.
        # No spinner — we want CI logs to stay clean.
        last_dot = 0.0
        while time.monotonic() < deadline:
            time.sleep(interval)
            try:
                pr = client.post("/v1/auth/device/poll", json={"device_code": device_code})
            except httpx.HTTPError:
                # Transient network blips during a 10-min wait are common —
                # keep polling. The deadline still bounds us.
                continue
            if pr.status_code >= 500:
                continue
            pr.raise_for_status()
            body   = pr.json()
            status = body.get("status")
            if status == "approved":
                click.echo("")  # newline after the dots
                return body["api_key"], body["workspace_id"]
            if status == "denied":
                raise click.ClickException("authorization denied in the browser.")
            if status == "expired":
                raise click.ClickException(
                    "code expired before approval. Run `trove login` again."
                )
            # status == "pending" — emit a dot every ~5s so the user sees life.
            now = time.monotonic()
            if now - last_dot >= 5.0:
                click.echo(".", nl=False, err=True)
                last_dot = now

        raise click.ClickException("login timed out. Run `trove login` again.")


@click.command()
@click.option(
    "--save-as",
    "profile_name",
    default=None,
    help="Profile name to save under (default: 'default').",
)
@click.option(
    "--profile",
    "legacy_profile_name",
    default=None,
    expose_value=True,
    hidden=True,
    help="Deprecated alias for --save-as.",
)
@click.option("--api-key", default=None, help="API key. Skips the browser flow.")
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
@click.option(
    "--no-browser",
    is_flag=True,
    default=False,
    help="Skip the browser-based device flow; prompt for a key instead.",
)
@click.pass_context
@handle_errors
def login(
    ctx: click.Context,
    profile_name: str | None,
    legacy_profile_name: str | None,
    api_key: str | None,
    workspace_id: str | None,
    namespace: str | None,
    base_url: str,
    no_browser: bool,
) -> None:
    """Save credentials so other commands can talk to the API.

    By default opens your browser to authorize this CLI — you'll see a short
    code; confirm it matches in the dashboard and approve. A fresh API key is
    minted and saved here automatically.

    \b
    To skip the browser:
      trove login --api-key trove-sk-...        # explicit key
      echo $TROVE_KEY | trove login             # piped from stdin (CI)
      trove login --no-browser                  # paste at the prompt

    \b
    The profile to *save under* is set with `--save-as`. The root-level
    `--profile` flag (`trove --profile staging ...`) selects an existing
    profile for read commands; pass it to login and we'll fall back to using
    it as the save-as name (with a deprecation note).
    """
    # Resolve the profile name with this priority:
    #   1. explicit --save-as
    #   2. login's deprecated --profile (warn)
    #   3. root-level --profile (warn — almost certainly user intent)
    #   4. "default"
    root_profile = ctx.obj.get("profile") if ctx.obj else None
    if profile_name is None:
        if legacy_profile_name is not None:
            click.secho(
                "warning: `trove login --profile X` is deprecated; "
                "use `--save-as X` instead.",
                fg="yellow", err=True,
            )
            profile_name = legacy_profile_name
        elif root_profile is not None:
            click.secho(
                f"note: saving as '{root_profile}' (from root --profile). "
                f"Pass `--save-as` explicitly to silence this.",
                fg="yellow", err=True,
            )
            profile_name = root_profile
        else:
            profile_name = "default"
    elif legacy_profile_name is not None and legacy_profile_name != profile_name:
        raise click.ClickException(
            f"--save-as ({profile_name!r}) and --profile ({legacy_profile_name!r}) disagree"
        )

    discovered_workspace_from_device: str | None = None
    if not api_key:
        # Pipe-friendly path: someone redirected a key into stdin (CI, scripts,
        # `pass show trove | trove login`). No prompt, no browser — just consume.
        if not sys.stdin.isatty():
            api_key = sys.stdin.readline().strip()
            if not api_key:
                raise click.ClickException("missing API key on stdin")
        elif no_browser:
            click.echo("Get a key at https://trovefiles.dev/dashboard/keys.")
            api_key = click.prompt("API key", hide_input=True).strip()
            if not api_key:
                raise click.ClickException("missing API key")
        else:
            # Default: browser-based device flow.
            api_key, discovered_workspace_from_device = _device_login(base_url)

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
        # Older API without /v1/me. The device flow already returned a
        # workspace_id; use that. Otherwise (paste flow on an old server) fall
        # back to a prompt + format check.
        if discovered_workspace_from_device:
            workspace_id = discovered_workspace_from_device
        else:
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
