from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from conftest import Login
from fastapi.testclient import TestClient

from llamawebui.app import create_app
from llamawebui.assistant.setup import TOOLS
from llamawebui.assistant.tools import ToolContext, ToolResult, ToolServices, call_tool
from llamawebui.config import Settings
from llamawebui.domain.runtime_capabilities import RuntimeCapabilities, RuntimeVersion
from llamawebui.services.hardware_profile import HardwareProfile
from llamawebui.services.runtime_probe import RuntimeProbeResult

ALL = frozenset(tool.privilege for tool in TOOLS)
BY_NAME = {tool.name: tool for tool in TOOLS}


async def fake_probe(path: Path) -> RuntimeProbeResult:
    return RuntimeProbeResult(
        executable=path.resolve(),
        version=RuntimeVersion(build="b9000", commit=None, raw="version"),
        capabilities=RuntimeCapabilities(
            options=frozenset({"model", "ctx-size", "models-preset"}), raw_help="help"
        ),
        devices_output=None,
        errors=(),
    )


def _app(tmp_path: Path) -> Any:
    return create_app(
        Settings(data_dir=tmp_path / "data"),
        runtime_prober=fake_probe,
        hardware_detector=lambda: HardwareProfile("Test CPU", 32 * 1024**3, ("GPU",), 0),
    )


def _services(app: Any) -> ToolServices:
    return app.state.assistant_engine.services  # type: ignore[no-any-return]


def _call(
    client: TestClient, name: str, arguments: object, privileges: frozenset[str] = ALL
) -> ToolResult:
    context = ToolContext(_services(client.app), privileges)
    text = arguments if isinstance(arguments, str) else json.dumps(arguments)

    async def run() -> ToolResult:
        return await call_tool(BY_NAME, context, name, text)

    return client.portal.call(run)  # type: ignore[union-attr]


def _profile(client: TestClient, tmp_path: Path) -> dict[str, Any]:
    executable = tmp_path / "llama-server.exe"
    executable.touch()
    runtime = client.post(
        "/api/runtimes", json={"name": "CPU", "executable_path": str(executable)}
    ).json()
    model = tmp_path / "model.gguf"
    model.write_bytes(b"GGUF")
    response = client.post(
        "/api/profiles",
        json={
            "alias": "test-model",
            "runtime_id": runtime["id"],
            "model_path": str(model),
            "arguments": [{"id": "ctx-size", "value": 8192}],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_system_tools_describe_the_control_plane(tmp_path: Path, login: Login) -> None:
    with TestClient(_app(tmp_path)) as client:
        login(client)
        profile = _profile(client, tmp_path)
        hardware = _call(client, "get_hardware", {})
        router = _call(client, "router_status", {})
        runtimes = _call(client, "list_runtimes", {})
        profiles = _call(client, "list_profiles", {})
        by_alias = _call(client, "get_profile", {"profile": "test-model"})
        by_id = _call(client, "get_profile", {"profile": profile["id"]})
        missing = _call(client, "get_profile", {"profile": "nope"})
        no_argument = _call(client, "get_profile", {})
        library = _call(client, "list_library", {})
        downloads = _call(client, "list_downloads", {})

    assert json.loads(hardware.text) == {
        "cpu": "Test CPU",
        "memory_gib": 32.0,
        "gpus": ["GPU"],
        "discrete_vram_gib": None,
    }
    assert json.loads(router.text) == {"state": "stopped", "pid": None}
    assert router.summary == "router stopped"
    assert json.loads(runtimes.text)[0]["build"] == "b9000"
    assert json.loads(profiles.text)[0]["model_file"] == "model.gguf"
    detail = json.loads(by_alias.text)
    assert detail["configuration"]["arguments"] == [{"id": "ctx-size", "value": 8192}]
    assert detail["issues"]["errors"] == []
    assert json.loads(by_id.text)["alias"] == "test-model"
    assert missing.ok is False
    assert "no profile has the id or alias 'nope'" in missing.text
    assert no_argument.text == "Error: profile is required and must be text"
    assert json.loads(library.text) == []
    assert json.loads(downloads.text) == []


def test_argument_tools_search_and_explain(tmp_path: Path, login: Login) -> None:
    with TestClient(_app(tmp_path)) as client:
        login(client)
        found = json.loads(_call(client, "search_arguments", {"query": "ctx size"}).text)
        nothing = json.loads(_call(client, "search_arguments", {"query": "zzzqqq"}).text)
        by_id = json.loads(_call(client, "get_argument", {"argument": "ctx-size"}).text)
        by_flag = json.loads(_call(client, "get_argument", {"argument": "--ctx-size"}).text)
        unknown = _call(client, "get_argument", {"argument": "--nope"})

    assert found["matches"][0]["id"] == "ctx-size"
    assert nothing == {"matches": [], "total": 0}
    assert by_id["flag"] == "--ctx-size"
    assert by_flag["id"] == "ctx-size"
    assert unknown.ok is False


def test_calls_are_refused_safely(tmp_path: Path, login: Login) -> None:
    with TestClient(_app(tmp_path)) as client:
        login(client)
        unknown = _call(client, "format_disk", {})
        not_allowed = _call(client, "get_hardware", {}, frozenset({"server.read"}))
        bad_json = _call(client, "get_hardware", "{oops")
        not_object = _call(client, "get_hardware", "[1]")
        empty = _call(client, "get_hardware", "")

    assert unknown.ok is False and "no tool named 'format_disk'" in unknown.text
    assert not_allowed.ok is False
    assert bad_json.text == "Error: the arguments must be a JSON object."
    assert not_object.ok is False
    assert empty.ok is True
