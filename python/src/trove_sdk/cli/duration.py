"""Shared `--since` parsing for the events tail/list commands.

Accepts:
  • `now`                     — start at the current moment
  • `5m`, `2h`, `30s`, `1d`   — single-unit relative durations
  • `1h30m`, `2d4h15m`        — compound relative durations
  • `2024-12-01T00:00:00Z`    — explicit ISO timestamp (with or without zone)
  • `2024-12-01`              — date-only ISO timestamp (treated as midnight UTC)

Returned datetimes are always tz-aware, in UTC.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import click

# `5m`, `2h`, `30s`, `1d` — must consume the entire string when used solo.
_UNIT      = re.compile(r"(\d+)([smhd])")
_UNIT_SECS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def _parse_iso(s: str) -> datetime:
    """Parse an ISO-8601 string. Date-only strings become midnight UTC."""
    raw = s.strip()
    # Date only (e.g. "2024-12-01"): treat as midnight UTC.
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        raw = raw + "T00:00:00+00:00"
    # Allow trailing `Z` per RFC 3339.
    raw = raw.replace("Z", "+00:00") if raw.endswith("Z") else raw
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_since(since: str) -> datetime:
    """Resolve a user-supplied `--since` value to a UTC datetime.

    Raises `click.BadParameter` on anything we can't interpret.
    """
    raw = since.strip()
    if raw == "now":
        return datetime.now(timezone.utc)

    # Heuristic: ISO timestamps contain `:` or `-` (after position 0) or `T`.
    # Compound durations only contain digits and unit letters.
    looks_iso = "T" in raw or ":" in raw or (raw.count("-") >= 2)
    if looks_iso:
        try:
            return _parse_iso(raw)
        except ValueError as e:
            raise click.BadParameter(f"--since: invalid ISO timestamp: {e}")

    # Compound duration: greedily consume `\d+[smhd]` chunks. The whole
    # input must be consumed — partial matches are rejected.
    matches = _UNIT.findall(raw)
    consumed = "".join(n + u for n, u in matches)
    if not matches or consumed != raw:
        raise click.BadParameter(
            f"--since: expected `now`, `5m`/`2h`/`30s`/`1d`, "
            f"`1h30m`-style compound, or an ISO timestamp (got {since!r})"
        )

    seconds = sum(int(n) * _UNIT_SECS[u] for n, u in matches)
    return datetime.now(timezone.utc) - timedelta(seconds=seconds)
