from __future__ import annotations

import json
from pathlib import Path

import pytest

from llamawebui import __main__ as cli
from llamawebui.domain.runtime_capabilities import RuntimeCapabilities, RuntimeVersion
from llamawebui.services.runtime_probe import RuntimeProbeResult


def test_probe_runtime_command_prints_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def fake_probe(executable: Path) -> RuntimeProbeResult:
        return RuntimeProbeResult(
            executable=executable,
            version=RuntimeVersion(build="10964", commit="b29c606e", raw="version output"),
            capabilities=RuntimeCapabilities(
                options=frozenset({"models-preset", "jinja"}), raw_help="help output"
            ),
            devices_output="CUDA0",
            errors=(),
        )

    monkeypatch.setattr(cli, "probe_runtime", fake_probe)
    monkeypatch.setattr("sys.argv", ["llamawebui", "probe-runtime", "llama-server.exe"])

    assert cli.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["usable"] is True
    assert payload["version"] == {"build": "10964", "commit": "b29c606e"}
    assert payload["options"] == ["jinja", "models-preset"]
    assert payload["devices"] == "CUDA0"


def test_probe_runtime_command_reports_missing_file(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def fake_probe(_executable: Path) -> RuntimeProbeResult:
        raise FileNotFoundError("missing runtime")

    monkeypatch.setattr(cli, "probe_runtime", fake_probe)
    monkeypatch.setattr("sys.argv", ["llamawebui", "probe-runtime", "missing.exe"])

    assert cli.main() == 2
    assert json.loads(capsys.readouterr().out) == {
        "usable": False,
        "errors": ["missing runtime"],
    }


def test_probe_runtime_command_returns_failure_for_unusable_runtime(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def fake_probe(executable: Path) -> RuntimeProbeResult:
        return RuntimeProbeResult(
            executable=executable,
            version=RuntimeVersion(build=None, commit=None, raw=""),
            capabilities=RuntimeCapabilities(options=frozenset(), raw_help=""),
            devices_output=None,
            errors=("help probe failed",),
        )

    monkeypatch.setattr(cli, "probe_runtime", fake_probe)
    monkeypatch.setattr("sys.argv", ["llamawebui", "probe-runtime", "llama-server.exe"])

    assert cli.main() == 1
    assert json.loads(capsys.readouterr().out)["errors"] == ["help probe failed"]