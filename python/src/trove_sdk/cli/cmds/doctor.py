"""`trove doctor` — one-shot diagnosis when something feels off.

Prints CLI version, where the binary is loaded from, the active profile (with
its key masked), all configured profiles, the env-var overrides currently in
effect, and the result of pinging /v1/me. Designed to be the first thing a
user runs when `trove run` mysteriously hits the wrong tenant — most of the
time the output will say "yep, you're hitting prod from the staging key on
PATH" and the next step is obvious.
"""

from __future__ import annotations

import os
import shutil
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import click
import httpx

from .. import config
from ..base import fetch_me


_RELEVANT_ENV = (
    "TROVE_API_KEY",
    "TROVE_WORKSPACE_ID",
    "TROVE_NAMESPACE",
    "TROVE_BASE_URL",
)


def _mask(value: str | None) -> str:
    if not value:
        return "-"
    if len(value) <= 12:
        return "***"
    return f"{value[:14]}…{value[-4:]}"


def _ok(label: str) -> None:
    click.echo(f"  ✓ {label}")


def _warn(label: str) -> None:
    click.secho(f"  ⚠ {label}", fg="yellow")


def _bad(label: str) -> None:
    click.secho(f"  ✗ {label}", fg="red")


@click.command("doctor")
@click.pass_context
def doctor(ctx: click.Context) -> None:
    """Show config, env, and a live API ping. Read-only — safe to run anywhere."""
    # ── version + binary location ────────────────────────────────────────────
    click.secho("CLI", bold=True)
    try:
        ver = version("trove-sdk")
    except PackageNotFoundError:
        ver = "(unknown — not installed as a package?)"
    binary = shutil.which("trove") or sys.argv[0] or "(unknown)"
    click.echo(f"  version           : {ver}")
    click.echo(f"  python            : {sys.version.split()[0]} ({sys.executable})")
    click.echo(f"  binary on PATH    : {binary}")

    # ── config file ──────────────────────────────────────────────────────────
    click.secho("\nConfig", bold=True)
    cfg_path: Path = config.CONFIG_FILE
    click.echo(f"  path              : {cfg_path}")
    if cfg_path.exists():
        _ok("config file present")
        if os.name == "posix":
            mode = cfg_path.stat().st_mode & 0o777
            if mode & 0o077:
                _warn(f"file mode is {oct(mode)}; recommend chmod 600")
            else:
                _ok(f"file mode is {oct(mode)}")
        else:
            # On Windows we can't chmod easily; just point it out.
            _warn("Windows: file ACL not checked (it holds your raw API key)")
    else:
        _bad("no config file — run `trove login` first")

    profiles = config.list_profiles()
    if profiles:
        click.echo(f"  saved profiles    : {', '.join(profiles)}")
    else:
        click.echo("  saved profiles    : (none)")

    # ── env vars ─────────────────────────────────────────────────────────────
    click.secho("\nEnvironment", bold=True)
    seen_any = False
    for name in _RELEVANT_ENV:
        v = os.environ.get(name)
        if v is None:
            continue
        seen_any = True
        shown = _mask(v) if name == "TROVE_API_KEY" else v
        click.echo(f"  {name:18}: {shown}")
    if not seen_any:
        click.echo("  (no TROVE_* env vars set)")

    # ── active profile ───────────────────────────────────────────────────────
    click.secho("\nActive profile", bold=True)
    requested = ctx.obj.get("profile") if ctx.obj else None
    try:
        name, profile = config.resolve(requested)
    except LookupError as e:
        _bad(str(e))
        click.echo("\n(skipping live ping)")
        ctx.exit(1)
        return  # pragma: no cover
    click.echo(f"  source            : {name}"
               + ("  (from --profile)" if requested else ""))
    click.echo(f"  workspace         : {profile.workspace_id}")
    click.echo(f"  api key           : {_mask(profile.api_key)}")
    click.echo(f"  base url          : {profile.base_url}")
    click.echo(f"  default namespace : {profile.namespace or '-'}")

    # ── live ping ────────────────────────────────────────────────────────────
    click.secho("\nLive check", bold=True)
    try:
        me = fetch_me(profile, timeout=8.0)
        if me is None:
            _warn("/v1/me returned 404 — server is older than 0.3.0")
        else:
            _ok(f"/v1/me OK — scope={me.get('scope', '?')}, "
                f"key_id={me.get('key_id', '-')}")
            ns_lock = me.get("namespace")
            if ns_lock:
                _ok(f"key is locked to namespace={ns_lock}")
                if profile.namespace and profile.namespace != ns_lock:
                    _warn(
                        f"profile default namespace ({profile.namespace}) "
                        f"differs from the key lock ({ns_lock}); the key wins"
                    )
    except httpx.HTTPStatusError as e:
        if e.response.status_code in (401, 403):
            _bad(f"auth rejected ({e.response.status_code}) — key revoked or wrong workspace")
        else:
            _bad(f"HTTP {e.response.status_code}: {e.response.text[:120]}")
    except httpx.HTTPError as e:
        _bad(f"network error: {e}")
