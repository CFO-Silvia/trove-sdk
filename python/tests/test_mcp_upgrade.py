"""Tests for `trove mcp upgrade` and the env-detection helpers it uses."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
import respx

click = pytest.importorskip("click")
from click.testing import CliRunner

from trove_sdk.mcp import upgrade as up


# ── env detection ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("path,expected", [
    # Real path observed in the wild on Windows
    (r"C:\Users\me\AppData\Roaming\uv\tools\trove-sdk\Scripts\python.exe", up.UV_TOOL),
    # macOS / Linux uv tool layout
    ("/home/me/.local/share/uv/tools/trove-sdk/bin/python", up.UV_TOOL),
    # pipx
    ("/home/me/.local/pipx/venvs/trove-sdk/bin/python", up.PIPX),
    (r"C:\Users\me\pipx\venvs\trove-sdk\Scripts\python.exe", up.PIPX),
    # System / venv / conda — anything else falls into PIP
    ("/usr/bin/python3", up.PIP),
    ("/Users/me/projects/foo/.venv/bin/python", up.PIP),
    (r"C:\Python312\python.exe", up.PIP),
])
def test_detect_env_kind_routes_known_layouts(path, expected):
    assert up.detect_env_kind(path) is expected


def test_detect_env_kind_handles_empty_path():
    assert up.detect_env_kind("").key == "unknown"
    assert up.detect_env_kind(None).key == "unknown"  # type: ignore[arg-type]


# ── upgrade_command picks the right recipe per env ────────────────────────────


def test_upgrade_command_uv_tool():
    cmd = up.upgrade_command("/whatever", up.UV_TOOL)
    assert cmd == ["uv", "tool", "upgrade", "trove-sdk"]


def test_upgrade_command_pipx():
    cmd = up.upgrade_command("/whatever", up.PIPX)
    assert cmd == ["pipx", "upgrade", "trove-sdk"]


def test_upgrade_command_pip_runs_target_python_with_mcp_extra():
    """Pip path must call the target Python explicitly and keep the [mcp] extra
    so users don't lose the MCP server silently."""
    cmd = up.upgrade_command("/usr/bin/python3", up.PIP)
    assert cmd == [
        "/usr/bin/python3", "-m", "pip", "install", "--upgrade", "trove-sdk[mcp]",
    ]


# ── latest_pypi_version + caching ─────────────────────────────────────────────


@respx.mock
def test_latest_pypi_version_parses_info():
    up._LATEST_CACHE.clear()
    respx.get(up.PYPI_URL).mock(
        return_value=httpx.Response(200, json={"info": {"version": "1.2.3"}})
    )
    assert up.latest_pypi_version() == "1.2.3"


@respx.mock
def test_latest_pypi_version_caches_within_process():
    """One status-then-upgrade flow shouldn't fan out to PyPI per client."""
    up._LATEST_CACHE.clear()
    route = respx.get(up.PYPI_URL).mock(
        return_value=httpx.Response(200, json={"info": {"version": "9.9.9"}})
    )
    up.latest_pypi_version()
    up.latest_pypi_version()
    up.latest_pypi_version()
    assert route.call_count == 1


@respx.mock
def test_latest_pypi_version_returns_none_on_network_error():
    up._LATEST_CACHE.clear()
    respx.get(up.PYPI_URL).mock(side_effect=httpx.ConnectError("no network"))
    assert up.latest_pypi_version(cache=False) is None


# ── upgrade_install never raises; surfaces failures via UpgradeResult ─────────


def _fake_install(path="/usr/bin/python3", kind=up.PIP, version="0.7.0"):
    return up.ClientInstall(
        client_key="claude-desktop",
        label="Claude Desktop",
        python_path=Path(path),
        env_kind=kind,
        installed_version=version,
    )


def test_upgrade_install_dry_run_returns_command_string():
    r = up.upgrade_install(_fake_install(kind=up.UV_TOOL), dry_run=True)
    assert r.ran is False
    assert r.succeeded is True
    assert "uv tool upgrade trove-sdk" in r.message


def test_upgrade_install_handles_missing_uv_binary_gracefully():
    """uv isn't always on PATH for the shell that runs `trove mcp upgrade`.
    We surface a clear next-step instead of raising."""
    install = _fake_install(kind=up.UV_TOOL)
    with patch("subprocess.run", side_effect=FileNotFoundError("no uv on PATH")):
        r = up.upgrade_install(install)
    assert r.ran is False
    assert r.succeeded is False
    assert "not found on PATH" in r.message
    assert "uv tool upgrade trove-sdk" in r.message  # gives the manual recipe


def test_upgrade_install_with_no_python_path_explains_itself():
    install = up.ClientInstall(
        client_key="claude-desktop", label="Claude Desktop",
        python_path=None, env_kind=up.UNKNOWN, installed_version=None,
    )
    r = up.upgrade_install(install)
    assert r.ran is False
    assert r.succeeded is False
    assert "no command path" in r.message


# ── CLI: trove mcp upgrade ────────────────────────────────────────────────────


def test_cli_upgrade_errors_when_no_clients_configured():
    from trove_sdk.cli.cmds.mcp import mcp
    with patch("trove_sdk.cli.cmds.mcp.mcp_upgrade.discover_installs", return_value=[]):
        result = CliRunner().invoke(mcp, ["upgrade"])
    assert result.exit_code != 0
    assert "no clients" in result.output.lower()


def test_cli_upgrade_dedupes_clients_sharing_same_python():
    """Two clients wired to the same uv-tool env must only run upgrade once."""
    from trove_sdk.cli.cmds.mcp import mcp

    same_python = Path("/shared/uv/tools/trove-sdk/bin/python")
    installs = [
        up.ClientInstall("claude-desktop", "Claude Desktop", same_python, up.UV_TOOL, "0.9.1"),
        up.ClientInstall("cursor", "Cursor", same_python, up.UV_TOOL, "0.9.1"),
    ]
    with patch("trove_sdk.cli.cmds.mcp.mcp_upgrade.discover_installs", return_value=installs), \
         patch("trove_sdk.cli.cmds.mcp.mcp_upgrade.upgrade_install") as up_call:
        up_call.return_value = up.UpgradeResult(installs[0], False, True, None, "would run: uv tool upgrade trove-sdk")
        result = CliRunner().invoke(mcp, ["upgrade", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert up_call.call_count == 1, "second client shares Python, must not re-run"
    # Peer label is mentioned so the user knows both clients got covered.
    assert "Cursor" in result.output


def test_cli_upgrade_dry_run_does_not_invoke_subprocess():
    from trove_sdk.cli.cmds.mcp import mcp
    install = up.ClientInstall(
        "claude-desktop", "Claude Desktop",
        Path("/some/uv/tools/trove-sdk/bin/python"), up.UV_TOOL, "0.9.0",
    )
    with patch("trove_sdk.cli.cmds.mcp.mcp_upgrade.discover_installs", return_value=[install]), \
         patch("subprocess.run") as sp_run:
        result = CliRunner().invoke(mcp, ["upgrade", "--dry-run"])
    assert result.exit_code == 0, result.output
    sp_run.assert_not_called()
    assert "would run: uv tool upgrade trove-sdk" in result.output


def test_cli_upgrade_nonzero_exit_when_any_client_fails():
    from trove_sdk.cli.cmds.mcp import mcp
    install = up.ClientInstall(
        "claude-desktop", "Claude Desktop",
        Path("/some/uv/tools/trove-sdk/bin/python"), up.UV_TOOL, "0.9.0",
    )
    with patch("trove_sdk.cli.cmds.mcp.mcp_upgrade.discover_installs", return_value=[install]), \
         patch("trove_sdk.cli.cmds.mcp.mcp_upgrade.upgrade_install",
               return_value=up.UpgradeResult(install, True, False, None, "upgrade failed: 503")):
        result = CliRunner().invoke(mcp, ["upgrade"])
    assert result.exit_code != 0
    assert "upgrade failed" in result.output


# ── CLI: trove mcp status flags stale installs ────────────────────────────────


def test_cli_status_flags_stale_install_with_upgrade_hint():
    from trove_sdk.cli.cmds.mcp import mcp

    # Make status_for_client return a configured entry for one client.
    fake_entry = {
        "command": "/some/uv/tools/trove-sdk/bin/python",
        "env": {"TROVE_NAMESPACE": "alice"},
    }

    def fake_status_for_client(key, server_name):
        return fake_entry if key == "claude-desktop" else None

    with patch("trove_sdk.cli.cmds.mcp.mcp_install.status_for_client",
               side_effect=fake_status_for_client), \
         patch("trove_sdk.cli.cmds.mcp.mcp_upgrade.installed_version_for_python",
               return_value="0.7.5"), \
         patch("trove_sdk.cli.cmds.mcp.mcp_upgrade.latest_pypi_version",
               return_value="0.9.2"):
        result = CliRunner().invoke(mcp, ["status"])
    assert result.exit_code == 0, result.output
    assert "trove-sdk 0.7.5" in result.output
    assert "latest 0.9.2" in result.output
    assert "trove mcp upgrade" in result.output


def test_cli_status_no_check_updates_skips_pypi():
    """Offline / hurried users can opt out of the network call."""
    from trove_sdk.cli.cmds.mcp import mcp
    with patch("trove_sdk.cli.cmds.mcp.mcp_install.status_for_client", return_value=None), \
         patch("trove_sdk.cli.cmds.mcp.mcp_upgrade.latest_pypi_version") as latest, \
         patch("trove_sdk.cli.cmds.mcp.mcp_upgrade.installed_version_for_python") as inst:
        result = CliRunner().invoke(mcp, ["status", "--no-check-updates"])
    assert result.exit_code == 0
    latest.assert_not_called()
    inst.assert_not_called()
