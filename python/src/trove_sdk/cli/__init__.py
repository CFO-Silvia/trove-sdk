"""Trove command-line interface.

The `trove` console script (registered in pyproject.toml) calls `main()` here.
We deliberately don't import `click` at module load — the entrypoint is on
PATH after `pip install trove-sdk` even when the `[cli]` extras aren't
installed, and we want a clean diagnostic instead of a raw ImportError.

Drop new resource groups into `cmds/` and register them inside `_build_root()`.
"""

from __future__ import annotations

import sys

_EXTRAS_HINT = (
    "trove CLI requires extras. Install with:\n"
    "    pip install 'trove-sdk[cli]'\n"
    "or\n"
    "    uv add 'trove-sdk[cli]'"
)


def main() -> None:
    """Console-script entrypoint. Prints a friendly hint if `[cli]` is missing."""
    try:
        import click  # noqa: F401  (probe — real imports happen in _build_root)
    except ImportError:
        print(_EXTRAS_HINT, file=sys.stderr)
        sys.exit(1)

    # Force UTF-8 on stdout/stderr so commands that print ✓/⚠/… don't crash
    # on Windows shells running cp1252. No-op on POSIX (already UTF-8) and on
    # Python builds where reconfigure isn't available. `errors="replace"` is
    # the safety belt for exotic terminals: garble, don't crash.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, OSError, ValueError):
            pass

    root = _build_root()
    root(obj={})


def _build_root():
    """Compose the root group. Done lazily so module import stays cheap."""
    import click

    from .cmds import auth, doctor as _doctor, events, files, keys, snapshots, webhooks

    @click.group(context_settings={"help_option_names": ["-h", "--help"]})
    @click.option(
        "--profile", default=None, help="Profile to use (default: 'default')."
    )
    @click.option("--debug", is_flag=True, help="Show full tracebacks on error.")
    @click.version_option(package_name="trove-sdk", prog_name="trove")
    @click.pass_context
    def root(ctx: click.Context, profile: str | None, debug: bool) -> None:
        """Trove — managed POSIX filesystem for AI agents."""
        ctx.ensure_object(dict)
        ctx.obj["profile"] = profile
        ctx.obj["debug"] = debug

    # ── Auth ────────────────────────────────────────────────────────────────
    root.add_command(auth.login)
    root.add_command(auth.logout)
    root.add_command(auth.whoami)

    # ── Filesystem (the SDK surface, mirrored) ──────────────────────────────
    root.add_command(files.run)
    root.add_command(files.ls)
    root.add_command(files.cat)
    root.add_command(files.put)
    root.add_command(files.get)
    root.add_command(files.write)
    root.add_command(files.rm)

    # ── Resource groups ─────────────────────────────────────────────────────
    root.add_command(events.events)
    root.add_command(keys.keys)
    root.add_command(webhooks.webhooks)
    root.add_command(snapshots.snapshot)

    # Top-level shortcut: `trove tail` ≡ `trove events tail`.
    root.add_command(events.tail, name="tail")

    # Diagnostic.
    root.add_command(_doctor.doctor)

    return root
