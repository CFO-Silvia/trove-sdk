"""Shared CLI plumbing: error handling and authenticated httpx client."""

from __future__ import annotations

import functools
import sys
from typing import Callable, Optional

import click
import httpx

from . import config


def _build_client(
    profile: config.Profile,
    *,
    namespace: Optional[str] = None,
    timeout: float = 30.0,
) -> httpx.Client:
    """Construct an httpx.Client with auth and (optionally) X-Namespace set."""
    headers = {"Authorization": f"Bearer {profile.api_key}"}
    if namespace:
        headers["X-Namespace"] = namespace
    return httpx.Client(base_url=profile.base_url, headers=headers, timeout=timeout)


def get_client(ctx: click.Context) -> tuple[httpx.Client, config.Profile, str]:
    """Resolve the active profile and return (client, profile, profile_name).

    The client carries the bearer token but no namespace header. Use this for
    admin/management endpoints (`/v1/workspaces/...`) and read-only
    introspection (`/v1/me`, `/v1/workspaces/{ws}/events`).
    """
    profile_name = ctx.obj.get("profile") if ctx.obj else None
    name, profile = config.resolve(profile_name)
    return _build_client(profile), profile, name


def get_runtime_client(
    ctx: click.Context,
    namespace_override: Optional[str] = None,
) -> tuple[httpx.Client, config.Profile, str, str]:
    """Resolve a client + namespace for runtime endpoints (`/exec`, `/files/...`).

    Namespace resolution: `--namespace` flag > `$TROVE_NAMESPACE` > profile.namespace.
    Raises `click.ClickException` if none can be resolved.

    Returns (client, profile, profile_name, namespace).
    """
    profile_name = ctx.obj.get("profile") if ctx.obj else None
    name, profile = config.resolve(profile_name)

    namespace = config.resolve_namespace(profile, namespace_override)
    if not namespace:
        raise click.ClickException(
            "no namespace set. Pass `-n/--namespace`, set `TROVE_NAMESPACE`, "
            "or run `trove login --namespace <ns>` to bake one into the profile."
        )

    return _build_client(profile, namespace=namespace), profile, name, namespace


def fetch_me(profile: config.Profile, *, timeout: float = 10.0) -> Optional[dict]:
    """GET /v1/me — returns key metadata, or None if the endpoint isn't there.

    Old API versions (pre-/v1/me) silently return None so callers degrade
    gracefully. Auth/network errors propagate so login/whoami can surface them.
    """
    with _build_client(profile, timeout=timeout) as client:
        r = client.get("/v1/me")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()


def handle_errors(fn: Callable) -> Callable:
    """Turn common errors into pretty exit codes. `--debug` re-raises everything."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        ctx = click.get_current_context()
        debug = bool(ctx.obj.get("debug")) if ctx.obj else False
        try:
            return fn(*args, **kwargs)
        except KeyboardInterrupt:
            click.echo("", err=True)
            sys.exit(130)
        except click.ClickException:
            raise  # let click format + exit
        except LookupError as e:
            click.secho(f"error: {e}", fg="red", err=True)
            sys.exit(3)
        except httpx.HTTPStatusError as e:
            click.secho(
                f"error: HTTP {e.response.status_code} — {e.response.text[:200]}",
                fg="red",
                err=True,
            )
            sys.exit(2 if e.response.status_code in (401, 403) else 1)
        except httpx.HTTPError as e:
            click.secho(f"error: network — {e}", fg="red", err=True)
            sys.exit(4)
        except Exception as e:
            if debug:
                raise
            click.secho(f"error: {e.__class__.__name__}: {e}", fg="red", err=True)
            sys.exit(1)

    return wrapper
