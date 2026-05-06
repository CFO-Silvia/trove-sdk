"""Smoke + unit tests for the CLI helpers.

End-to-end CLI behavior is covered by the integration suite (live API). These
tests pin down the things that are worth catching before they hit a user:

  • `--since` accepts the formats we advertise
  • event timestamps localize correctly and prefix the date for old events
  • profile namespace + `TROVE_NAMESPACE` env precedence
  • the `trove` entrypoint emits a friendly hint when `[cli]` extras are missing
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

# Skip the whole module if click isn't available — the friendly entrypoint
# behavior is covered separately.
click = pytest.importorskip("click")

from trove_sdk.cli import config, duration, fmt

# ── duration.parse_since ──────────────────────────────────────────────────────


def test_parse_since_now_close_to_real_now():
    before = datetime.now(timezone.utc)
    got = duration.parse_since("now")
    after = datetime.now(timezone.utc)
    assert before <= got <= after


def test_parse_since_simple_durations():
    now = datetime.now(timezone.utc)
    # "5m" should be roughly 5 min ago. Tolerate 2s of test runtime.
    five_min_ago = duration.parse_since("5m")
    assert timedelta(seconds=298) <= now - five_min_ago <= timedelta(seconds=302)

    # All units should resolve.
    for s in ["30s", "2h", "1d"]:
        result = duration.parse_since(s)
        assert isinstance(result, datetime) and result.tzinfo is timezone.utc


def test_parse_since_compound_duration():
    now = datetime.now(timezone.utc)
    # 1h30m == 5400 seconds back
    got = duration.parse_since("1h30m")
    assert timedelta(seconds=5398) <= now - got <= timedelta(seconds=5402)

    # 2d4h == 2 * 86400 + 4 * 3600 = 187200 seconds back
    got = duration.parse_since("2d4h")
    assert timedelta(seconds=187198) <= now - got <= timedelta(seconds=187202)


def test_parse_since_iso_timestamp_z():
    got = duration.parse_since("2024-12-01T00:00:00Z")
    assert got == datetime(2024, 12, 1, tzinfo=timezone.utc)


def test_parse_since_iso_timestamp_offset():
    # +00:00 explicit and date-only both equal midnight UTC.
    got_offset = duration.parse_since("2024-12-01T00:00:00+00:00")
    got_date = duration.parse_since("2024-12-01")
    assert got_offset == got_date == datetime(2024, 12, 1, tzinfo=timezone.utc)


def test_parse_since_iso_timestamp_with_zone_normalizes_to_utc():
    # 2024-12-01T05:00:00+05:00 == 2024-12-01T00:00:00Z
    got = duration.parse_since("2024-12-01T05:00:00+05:00")
    assert got == datetime(2024, 12, 1, tzinfo=timezone.utc)


@pytest.mark.parametrize("bad", ["", "5x", "garbage", "1h30", "h", "5"])
def test_parse_since_rejects_garbage(bad):
    with pytest.raises(click.BadParameter):
        duration.parse_since(bad)


# ── fmt.print_event time formatting ───────────────────────────────────────────


def test_short_time_today_omits_date():
    """An event from today should render as just `HH:MM:SS` (local)."""
    # Build a UTC timestamp for "now" so it's definitely today.
    now_utc = datetime.now(timezone.utc).replace(microsecond=0)
    out = fmt._short_time(now_utc.isoformat())
    # 8 chars exactly: HH:MM:SS
    assert len(out) == 8 and out.count(":") == 2


def test_short_time_old_event_prefixes_date():
    """An event from a previous day should include MM-DD."""
    old_utc = datetime.now(timezone.utc) - timedelta(days=2)
    out = fmt._short_time(old_utc.isoformat())
    # `MM-DD HH:MM:SS` = 14 chars
    assert len(out) == 14 and "-" in out[:5]


def test_short_time_handles_z_suffix():
    """Trove's API emits `Z`-suffixed timestamps; parsing must accept them."""
    now_utc = datetime.now(timezone.utc).replace(microsecond=0)
    z_form = now_utc.isoformat().replace("+00:00", "Z")
    assert fmt._short_time(z_form) == fmt._short_time(now_utc.isoformat())


def test_short_time_invalid_input_falls_back():
    assert fmt._short_time("") == ""
    assert fmt._short_time("not-a-date") == "not-a-d"[:8] or fmt._short_time(
        "not-a-date"
    ).startswith("not-a-d")


# ── fmt._summary verbose mode ─────────────────────────────────────────────────


def test_summary_exec_truncates_by_default():
    cmd = "echo " + "x" * 200
    event = {"type": "exec.completed", "data": {"command": cmd, "exit_code": 0}}
    out = fmt._summary(event)
    # Should truncate the long command and append exit info.
    assert "…" in out and "exit=0" in out and len(out) < len(cmd)


def test_summary_exec_verbose_keeps_full_command_and_first_stdout_line():
    event = {
        "type": "exec.completed",
        "data": {
            "command": "ls workspace/",
            "exit_code": 0,
            "stdout": "file1.txt\nfile2.txt\nfile3.txt",
        },
    }
    out = fmt._summary(event, verbose=True)
    assert "ls workspace/" in out
    assert "exit=0" in out
    assert "> file1.txt" in out  # first stdout line
    assert "file2" not in out  # not the second


def test_summary_snapshot_deleted_recognized():
    event = {"type": "snapshot.deleted", "data": {"snapshot_id": "snap-abc"}}
    assert fmt._summary(event) == "snap-abc"


# ── config namespace resolution ───────────────────────────────────────────────


def test_resolve_namespace_explicit_override_wins(monkeypatch):
    monkeypatch.setenv("TROVE_NAMESPACE", "from-env")
    p = config.Profile(api_key="k", workspace_id="ws-1", namespace="from-profile")
    assert config.resolve_namespace(p, "from-flag") == "from-flag"


def test_resolve_namespace_env_beats_profile(monkeypatch):
    monkeypatch.setenv("TROVE_NAMESPACE", "from-env")
    p = config.Profile(api_key="k", workspace_id="ws-1", namespace="from-profile")
    assert config.resolve_namespace(p, None) == "from-env"


def test_resolve_namespace_falls_back_to_profile(monkeypatch):
    monkeypatch.delenv("TROVE_NAMESPACE", raising=False)
    p = config.Profile(api_key="k", workspace_id="ws-1", namespace="from-profile")
    assert config.resolve_namespace(p, None) == "from-profile"


def test_resolve_namespace_returns_none_when_unset(monkeypatch):
    monkeypatch.delenv("TROVE_NAMESPACE", raising=False)
    p = config.Profile(api_key="k", workspace_id="ws-1")
    assert config.resolve_namespace(p, None) is None


def test_save_and_load_profile_round_trips_namespace(tmp_path, monkeypatch):
    # Redirect the config dir to a tmp path so we don't clobber the user's.
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    p = config.Profile(api_key="k", workspace_id="ws-1", namespace="alice")
    config.save_profile("test", p)
    loaded = config.get_profile("test")
    assert loaded is not None
    assert loaded.namespace == "alice"
    assert loaded.workspace_id == "ws-1"


# ── main() entrypoint behavior ───────────────────────────────────────────────────────


# ── trove run flag passthrough ───────────────────────────────────────────────────


def test_run_command_passes_through_short_flags(monkeypatch):
    """Real bug from 0.5.0 smoke: `trove run wc -c file` errored with
    "No such option: -c" because click ate the flag. The fix is
    `ignore_unknown_options=True` on the run command — verify the args make
    it to /exec verbatim.
    """
    from click.testing import CliRunner

    from trove_sdk.cli import _build_root

    captured: dict = {}

    class FakeClient:
        def post(self, url, json=None, timeout=None, **kwargs):
            captured["url"] = url
            captured["command"] = json["command"] if json else None

            class R:
                status_code = 200
                text = "ok\n"

                def raise_for_status(self):
                    pass

            return R()

        def close(self):
            pass

    def fake_runtime_client(ctx, ns_override=None):
        return FakeClient(), None, "test", "alice"

    monkeypatch.setattr(
        "trove_sdk.cli.cmds.files.get_runtime_client", fake_runtime_client
    )

    root = _build_root()
    runner = CliRunner()

    # The flag-shaped args (`-c`, `-la`, `-F,`) should all reach the API verbatim.
    for argv, expected_cmd in [
        (["run", "wc", "-c", "workspace/file.txt"], "wc -c workspace/file.txt"),
        (["run", "ls", "-la", "workspace/"], "ls -la workspace/"),
        (
            ["run", "-n", "alice", "grep", "-i", "x", "workspace/f"],
            "grep -i x workspace/f",
        ),
        (
            ["run", "awk", "-F,", "NR>1{print $2}", "workspace/d.csv"],
            "awk -F, NR>1{print $2} workspace/d.csv",
        ),
    ]:
        captured.clear()
        result = runner.invoke(root, argv, obj={})
        assert result.exit_code == 0, f"argv={argv}: {result.output}"
        assert captured["url"] == "/exec"
        assert captured["command"] == expected_cmd


# ── main() entrypoint behavior ───────────────────────────────────────────────────────


def test_main_prints_friendly_hint_when_click_missing(capsys, monkeypatch):
    """`pip install trove-sdk` without the [cli] extra registers the `trove`
    entrypoint but fails to import click. main() should catch that and print
    a one-line install hint instead of a raw traceback.
    """
    import builtins

    from trove_sdk.cli import main

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "click":
            raise ImportError("No module named click")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "pip install" in err and "trove-sdk[cli]" in err
