"""Events: tail (long-poll), list (paged).

`trove tail` is the killer dev flow. Backed by GET /events?since=<iso> on a
2-second poll. `trove events list` shares `--since`, `--types`, `--namespace`
with tail and adds cursor-based pagination for replay.
"""

from __future__ import annotations

import time
from typing import Optional

import click

from ..base import get_client, handle_errors
from ..duration import parse_since
from ..fmt import print_event


@click.group()
def events() -> None:
    """Inspect the workspace event log."""


@events.command("tail")
@click.option("--namespace", "-n", default=None, help="Filter to one namespace.")
@click.option(
    "--types",
    "-t",
    default=None,
    help="Comma-separated event types (e.g. file.written,exec.completed).",
)
@click.option(
    "--since",
    default="now",
    show_default=True,
    help="How far back to start: `now`, `5m`, `1h30m`, ISO timestamp.",
)
@click.option("--json", "json_mode", is_flag=True, help="One JSON object per line.")
@click.option(
    "--interval",
    default=2.0,
    show_default=True,
    type=float,
    help="Poll interval in seconds.",
)
@click.option(
    "--idle",
    default=30.0,
    show_default=True,
    type=float,
    help="Emit a `· idle` heartbeat to stderr after this many idle seconds (0 to disable).",
)
@click.option(
    "-v",
    "--verbose",
    is_flag=True,
    help="Expand truncated summaries (full exec command + first stdout line).",
)
@click.pass_context
@handle_errors
def tail(
    ctx: click.Context,
    namespace: Optional[str],
    types: Optional[str],
    since: str,
    json_mode: bool,
    interval: float,
    idle: float,
    verbose: bool,
) -> None:
    """Stream events as they happen. Ctrl-C to stop."""
    client, profile, _ = get_client(ctx)
    bookmark = parse_since(since).isoformat()
    last_event = time.monotonic()
    idle_shown = False

    if not json_mode:
        click.secho(
            f"tailing {profile.workspace_id}"
            + (f" namespace={namespace}" if namespace else "")
            + (f" types={types}" if types else "")
            + "  (Ctrl-C to stop)",
            fg="blue",
            err=True,
        )

    params_base: dict[str, str | int] = {"limit": 200}
    if namespace:
        params_base["namespace"] = namespace
    if types:
        params_base["types"] = types

    try:
        while True:
            params = dict(params_base)
            params["since"] = bookmark
            r = client.get(
                f"/v1/workspaces/{profile.workspace_id}/events",
                params=params,
            )
            r.raise_for_status()
            batch = r.json().get("events", []) or []
            # API returns newest-first; print oldest-first so the stream reads naturally.
            for ev in reversed(batch):
                print_event(ev, json_mode=json_mode, verbose=verbose)
                bookmark = ev["created_at"]
                last_event = time.monotonic()
                idle_shown = False
            # Heartbeat to stderr so the user knows the poll is alive during
            # quiet periods. Only fires once per idle interval; resets on event.
            if (
                not json_mode
                and idle > 0
                and not idle_shown
                and (time.monotonic() - last_event) >= idle
            ):
                click.secho(
                    f"· idle ({int(time.monotonic() - last_event)}s)",
                    fg="bright_black",
                    err=True,
                )
                idle_shown = True
            time.sleep(interval)
    finally:
        client.close()


@events.command("list")
@click.option("--namespace", "-n", default=None)
@click.option("--types", "-t", default=None)
@click.option(
    "--since",
    default=None,
    help="Earliest event to include: `5m`, `1h30m`, ISO timestamp.",
)
@click.option("--limit", default=20, show_default=True, type=int)
@click.option(
    "--cursor", default=None, help="Continue a previous page (printed to stderr)."
)
@click.option("--json", "json_mode", is_flag=True)
@click.option(
    "-v",
    "--verbose",
    is_flag=True,
    help="Expand truncated summaries (full exec command + first stdout line).",
)
@click.pass_context
@handle_errors
def list_cmd(
    ctx: click.Context,
    namespace: Optional[str],
    types: Optional[str],
    since: Optional[str],
    limit: int,
    cursor: Optional[str],
    json_mode: bool,
    verbose: bool,
) -> None:
    """Show recent events (newest first). Use --cursor for pagination."""
    client, profile, _ = get_client(ctx)
    params: dict[str, str | int] = {"limit": limit}
    if namespace:
        params["namespace"] = namespace
    if types:
        params["types"] = types
    if since:
        params["since"] = parse_since(since).isoformat()
    if cursor:
        params["cursor"] = cursor
    try:
        r = client.get(f"/v1/workspaces/{profile.workspace_id}/events", params=params)
        r.raise_for_status()
        body = r.json()
        evs = body.get("events", []) or []
        next_c = body.get("next_cursor")

        if not evs:
            if not json_mode:
                click.secho("(no events)", fg="bright_black", err=True)
            return

        for ev in evs:
            print_event(ev, json_mode=json_mode, verbose=verbose)

        # Hint on stderr so it doesn't pollute --json output piped to a file.
        if next_c and not json_mode:
            click.secho(
                f"\n… more available. Continue with --cursor {next_c}",
                fg="bright_black",
                err=True,
            )
    finally:
        client.close()
