"""Filesystem verbs: run, ls, cat, put, write, rm.

`trove run "<cmd>"` is the verb that closes the loop with `trove tail` —
push a command, watch its event land in the activity feed.
"""

from __future__ import annotations

import json as _json
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


@click.command(
    "run",
    # Pass unknown flags through to the remote shell instead of trying to
    # parse them ourselves. Without this, `trove run wc -c file` errors with
    # "No such option: -c". `allow_interspersed_args=False` says: once we see
    # the first positional, treat everything after as positional too — so
    # `trove run -n alice ls -la` still parses `-n alice` as our option and
    # leaves `ls -la` for the workspace.
    context_settings={
        "ignore_unknown_options": True,
        "allow_interspersed_args": False,
    },
)
@click.argument("command", nargs=-1, required=True, type=click.UNPROCESSED)
@click.option("--namespace", "-n", default=None, help="Override the profile namespace.")
@click.option(
    "--json", "json_mode", is_flag=True,
    help="Emit a single JSON line: {exit_code, stdout, stderr, duration_ms}.",
)
@click.option(
    "--no-stdin", "no_stdin", is_flag=True,
    help="Don't pipe local stdin to the remote shell (default: auto-detect).",
)
@click.pass_context
@handle_errors
def run(
    ctx: click.Context,
    command: tuple[str, ...],
    namespace: Optional[str],
    json_mode: bool,
    no_stdin: bool,
) -> None:
    """Run a shell command in the workspace and print its output.

    \b
    Examples:
        trove run ls workspace/
        trove run wc -c workspace/notes.txt
        trove run "cat workspace/notes.txt | wc -l"
        trove run -n alice ls -la workspace/uploads/
        echo '{"x":1}' | trove run "jq .x"          # piped stdin (1 MB cap)

    The exit code of the remote command is propagated as the local exit code,
    so `trove run "build" && trove run "deploy"` works the way you'd expect.
    Remote stderr goes to local stderr; `--json` keeps everything in one
    machine-readable line for piping into `jq`. If stdin is a pipe (not a
    tty) it gets forwarded to the remote shell — pass `--no-stdin` to opt out
    in environments where `isatty` lies (some CI runners).
    """
    cmd = " ".join(command).strip()
    if not cmd:
        raise click.ClickException("empty command")

    # Auto-detect piped stdin. Skip when stdout-only (interactive use) so
    # `trove run cat` doesn't block waiting for tty input the user didn't
    # plan to give it. 1 MB cap matches the server-side Pydantic constraint;
    # we check here too so we can give a clean error instead of a 422.
    stdin_payload: Optional[str] = None
    if not no_stdin and not sys.stdin.isatty():
        stdin_payload = sys.stdin.read()
        if not stdin_payload:
            stdin_payload = None
        elif len(stdin_payload.encode("utf-8")) > 1024 * 1024:
            raise click.ClickException(
                "stdin > 1MB; write to a file with `trove write` and redirect"
                " in the command (`tool < workspace/in.txt`)."
            )

    payload: dict = {"command": cmd}
    if stdin_payload is not None:
        payload["stdin"] = stdin_payload

    client, _, _, _ = get_runtime_client(ctx, namespace)
    try:
        # /v1/exec returns structured JSON so we can split stdout/stderr and
        # surface the exit code without parsing the legacy `[exit N]` prefix.
        # Bump the timeout above the API's 30 s exec cap so we don't race it.
        r = client.post("/v1/exec", json=payload, timeout=45.0)
        if r.status_code == 404:
            # Older server without /v1/exec — degrade to the text endpoint.
            # Exit code can't be recovered cleanly here; we parse the leading
            # `[exit N]` line if present and otherwise leave it at 0.
            r = client.post("/exec", json=payload, timeout=45.0)
            r.raise_for_status()
            text = r.text
            exit_code = _parse_legacy_exit(text)
            if json_mode:
                # Best-effort split for old servers: we only have one stream.
                payload = {
                    "exit_code": exit_code,
                    "stdout": text if exit_code == 0 else "",
                    "stderr": text if exit_code != 0 else "",
                    "duration_ms": 0,
                }
                click.echo(_json.dumps(payload))
            else:
                sys.stdout.write(text)
                if text and not text.endswith("\n"):
                    sys.stdout.write("\n")
            ctx.exit(exit_code)
            return  # pragma: no cover

        r.raise_for_status()
        body = r.json()
        exit_code = int(body.get("exit_code", 0))
        out       = body.get("stdout", "") or ""
        err       = body.get("stderr", "") or ""

        if json_mode:
            click.echo(_json.dumps({
                "exit_code":   exit_code,
                "stdout":      out,
                "stderr":      err,
                "duration_ms": int(body.get("duration_ms", 0)),
            }))
        else:
            if out:
                sys.stdout.write(out)
                if not out.endswith("\n"):
                    sys.stdout.write("\n")
            if err:
                sys.stderr.write(err)
                if not err.endswith("\n"):
                    sys.stderr.write("\n")

        ctx.exit(exit_code)
    finally:
        client.close()


_LEGACY_EXIT_RE = __import__("re").compile(r"^\[exit (\d+)\]")


def _parse_legacy_exit(text: str) -> int:
    """Extract N from a `[exit N]\n...` legacy-mode response. 0 if absent."""
    m = _LEGACY_EXIT_RE.match(text or "")
    return int(m.group(1)) if m else 0


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


# ── get ───────────────────────────────────────────────────────────────────────


@click.command("get")
@click.argument("remote")
@click.argument(
    "local",
    type=click.Path(dir_okay=False, writable=True, path_type=LocalPath),
    required=False,
)
@click.option("--namespace", "-n", default=None, help="Override the profile namespace.")
@click.option(
    "--force", "-f", is_flag=True,
    help="Overwrite LOCAL if it already exists.",
)
@click.option(
    "--stdout", "to_stdout", is_flag=True,
    help="Write to stdout instead of a file (binary-safe; combine with `> file`).",
)
@click.pass_context
@handle_errors
def get(
    ctx: click.Context,
    remote: str,
    local: Optional[LocalPath],
    namespace: Optional[str],
    force: bool,
    to_stdout: bool,
) -> None:
    """Download a file from the workspace (binary-safe).

    \b
    Examples:
        trove get workspace/report.pdf                  → ./report.pdf
        trove get workspace/report.pdf reports/r.pdf
        trove get workspace/img.png --stdout > img.png

    Use this for any file `trove cat` refuses (binaries) or for pulling files
    larger than the 1 MB text-preview cap. Server-capped at 100 MB; bigger
    files arrive truncated and emit a warning to stderr.
    """
    if to_stdout and local is not None:
        raise click.ClickException("pass --stdout OR a LOCAL path, not both")

    client, _, _, _ = get_runtime_client(ctx, namespace)
    try:
        rel = _norm_remote(remote)
        if not rel:
            raise click.ClickException("remote path cannot be empty")
        r = client.get(f"/files/{rel}", timeout=180.0)
        if r.status_code == 404:
            raise click.ClickException(f"not found: {remote}")
        r.raise_for_status()
        body = r.content
        truncated = r.headers.get("X-Trove-Truncated") == "1"
        full_size = r.headers.get("X-Trove-Size")

        if to_stdout:
            # Write raw bytes to stdout's underlying buffer (don't decode).
            sys.stdout.buffer.write(body)
        else:
            target = local or LocalPath(rel.split("/")[-1] or "download")
            if target.exists() and not force:
                raise click.ClickException(
                    f"{target} already exists. Pass --force to overwrite."
                )
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
            click.secho(
                f"downloaded {remote} → {target}  ({_fmt_bytes(len(body))})",
                fg="green",
            )

        if truncated:
            extra = f" of {_fmt_bytes(full_size)}" if full_size else ""
            click.secho(
                f"warning: response truncated{extra} — server cap exceeded.",
                fg="yellow",
                err=True,
            )
    finally:
        client.close()
