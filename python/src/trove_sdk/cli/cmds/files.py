"""Filesystem verbs: run, ls, cat, put, write, rm.

`trove run "<cmd>"` is the verb that closes the loop with `trove tail` —
push a command, watch its event land in the activity feed.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path as LocalPath
from typing import Optional

import click

from ..base import get_runtime_client, handle_errors


def _norm_remote(path: str) -> str:
    """Strip a leading `workspace/` so the user can include it or not.

    The runtime endpoints (POST /write, POST /delete) want namespace-relative
    paths. The /exec endpoint gets the raw command unchanged so the user can
    keep using `workspace/...` everywhere they'd use it in the SDK.
    """
    p = path.lstrip("/")
    if p == "workspace":
        return ""
    if p.startswith("workspace/"):
        return p[len("workspace/") :]
    return p


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
        return iso


# ── run ───────────────────────────────────────────────────────────────────────


@click.command("run")
@click.argument("command", nargs=-1, required=True)
@click.option("--namespace", "-n", default=None, help="Override the profile namespace.")
@click.pass_context
@handle_errors
def run(ctx: click.Context, command: tuple[str, ...], namespace: Optional[str]) -> None:
    """Run a shell command in the workspace and print its output.

    Examples:
        trove run ls workspace/
        trove run "cat workspace/notes.txt | wc -l"
        trove run -n alice "ls workspace/uploads/"
    """
    cmd = " ".join(command).strip()
    if not cmd:
        raise click.ClickException("empty command")
    client, _, _, _ = get_runtime_client(ctx, namespace)
    try:
        # POST /exec returns text/plain (stdout, or `[exit N]` summary).
        # Bump the timeout above the API's 30s exec cap so we don't race.
        r = client.post("/exec", json={"command": cmd}, timeout=45.0)
        r.raise_for_status()
        sys.stdout.write(r.text)
        if r.text and not r.text.endswith("\n"):
            sys.stdout.write("\n")
    finally:
        client.close()


# ── ls ────────────────────────────────────────────────────────────────────────


@click.command("ls")
@click.argument("path", default="workspace/")
@click.option("--namespace", "-n", default=None, help="Override the profile namespace.")
@click.option(
    "--long", "-l", "long_mode", is_flag=True, help="Show size and modified time."
)
@click.option("--json", "json_mode", is_flag=True, help="Emit raw JSON.")
@click.pass_context
@handle_errors
def ls(
    ctx: click.Context,
    path: str,
    namespace: Optional[str],
    long_mode: bool,
    json_mode: bool,
) -> None:
    """List a directory in the workspace."""
    client, _, _, _ = get_runtime_client(ctx, namespace)
    try:
        r = client.get("/v1/files", params={"path": path})
        r.raise_for_status()
        body = r.json()
        entries = body.get("entries", []) or []

        if json_mode:
            import json as _json

            click.echo(_json.dumps(body))
            return

        if not entries:
            click.secho("(empty)", fg="bright_black")
            return

        for e in entries:
            name = e["name"] + ("/" if e.get("is_dir") else "")
            if long_mode:
                size = "-" if e.get("is_dir") else _fmt_bytes(e.get("size_bytes"))
                when = _fmt_local(e.get("modified_at", ""))
                click.echo(f"{size:>10}  {when}  {name}")
            else:
                click.echo(name)
        if body.get("truncated"):
            click.secho("…(truncated)", fg="bright_black", err=True)
    finally:
        client.close()


# ── cat ───────────────────────────────────────────────────────────────────────


@click.command("cat")
@click.argument("path")
@click.option("--namespace", "-n", default=None, help="Override the profile namespace.")
@click.pass_context
@handle_errors
def cat(ctx: click.Context, path: str, namespace: Optional[str]) -> None:
    """Print a UTF-8 text file. Errors on binary content."""
    client, _, _, _ = get_runtime_client(ctx, namespace)
    try:
        r = client.get("/v1/files/content", params={"path": path})
        r.raise_for_status()
        body = r.json()
        if body.get("encoding") == "binary":
            raise click.ClickException(
                f"{body.get('path', path)} is binary "
                f"({_fmt_bytes(body.get('size_bytes'))}); "
                f"use `trove run` with hexdump or similar."
            )
        sys.stdout.write(body.get("content") or "")
        if body.get("truncated"):
            click.secho(
                f"\n…(truncated at 1MB; full size {_fmt_bytes(body.get('size_bytes'))})",
                fg="yellow",
                err=True,
            )
    finally:
        client.close()


# ── put ───────────────────────────────────────────────────────────────────────


@click.command("put")
@click.argument(
    "local",
    type=click.Path(exists=True, dir_okay=False, readable=True, path_type=LocalPath),
)
@click.argument("remote", default=None, required=False)
@click.option("--namespace", "-n", default=None, help="Override the profile namespace.")
@click.pass_context
@handle_errors
def put(
    ctx: click.Context,
    local: LocalPath,
    remote: Optional[str],
    namespace: Optional[str],
) -> None:
    """Upload a local file (binary-safe).

    If REMOTE is omitted, uploads to `workspace/<basename>`. Pass a directory
    target with a trailing `/` to keep the local filename:

        trove put report.pdf                    → workspace/report.pdf
        trove put report.pdf workspace/docs/    → workspace/docs/report.pdf
        trove put report.pdf workspace/r2.pdf   → workspace/r2.pdf
    """
    if remote is None:
        remote = local.name
    if remote.endswith("/"):
        remote = remote.rstrip("/") + "/" + local.name

    rel = _norm_remote(remote)
    if not rel:
        raise click.ClickException("remote path cannot be empty")

    client, _, _, _ = get_runtime_client(ctx, namespace)
    try:
        with local.open("rb") as fh:
            data = fh.read()
        r = client.put(f"/files/{rel}", content=data, timeout=120.0)
        r.raise_for_status()
        body = r.json()
        click.secho(
            f"uploaded {body.get('path', remote)}  "
            f"({_fmt_bytes(body.get('size_bytes'))})",
            fg="green",
        )
    finally:
        client.close()


# ── write ─────────────────────────────────────────────────────────────────────


@click.command("write")
@click.argument("path")
@click.argument("content", required=False)
@click.option("--stdin", "from_stdin", is_flag=True, help="Read content from stdin.")
@click.option("--namespace", "-n", default=None, help="Override the profile namespace.")
@click.pass_context
@handle_errors
def write(
    ctx: click.Context,
    path: str,
    content: Optional[str],
    from_stdin: bool,
    namespace: Optional[str],
) -> None:
    """Write a UTF-8 text file (small, inline). Use `trove put` for binary.

    Examples:
        trove write workspace/notes.txt "hello world"
        echo "from a pipe" | trove write workspace/piped.txt --stdin
    """
    if from_stdin:
        if content is not None:
            raise click.ClickException(
                "pass content as an argument OR --stdin, not both"
            )
        content = sys.stdin.read()
    if content is None:
        raise click.ClickException("missing content (pass as arg or use --stdin)")

    client, _, _, _ = get_runtime_client(ctx, namespace)
    try:
        r = client.post("/write", json={"path": _norm_remote(path), "content": content})
        r.raise_for_status()
        body = r.json()
        click.secho(
            f"wrote {body.get('path', path)}  ({_fmt_bytes(body.get('size_bytes'))})",
            fg="green",
        )
    finally:
        client.close()


# ── rm ────────────────────────────────────────────────────────────────────────


@click.command("rm")
@click.argument("paths", nargs=-1, required=True)
@click.option("--namespace", "-n", default=None, help="Override the profile namespace.")
@click.option("--yes", "-y", is_flag=True, help="Skip the confirmation prompt.")
@click.pass_context
@handle_errors
def rm(
    ctx: click.Context, paths: tuple[str, ...], namespace: Optional[str], yes: bool
) -> None:
    """Delete one or more files or directories. Recursive.

    Confirms before deleting more than one path; `--yes` to skip.
    """
    if not yes and len(paths) > 1:
        click.confirm(f"delete {len(paths)} paths?", abort=True)

    client, _, _, _ = get_runtime_client(ctx, namespace)
    try:
        for p in paths:
            r = client.post("/delete", json={"path": _norm_remote(p)})
            if r.status_code == 404:
                click.secho(f"not found: {p}", fg="yellow", err=True)
                continue
            r.raise_for_status()
            body = r.json()
            click.secho(f"deleted {body.get('deleted', p)}", fg="red")
    finally:
        client.close()
