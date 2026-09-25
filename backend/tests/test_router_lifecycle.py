from pathlib import Path

import pytest

from llamawebui.domain.router_lifecycle import RouterLaunch, RouterState, require_transition


def test_router_state_machine_accepts_operational_lifecycle() -> None:
    for current, target in (
        (RouterState.STOPPED, RouterState.STARTING),
        (RouterState.STARTING, RouterState.READY),
        (RouterState.READY, RouterState.DEGRADED),
        (RouterState.DEGRADED, RouterState.READY),
        (RouterState.READY, RouterState.STOPPING),
        (RouterState.STOPPING, RouterState.STOPPED),
        (RouterState.STARTING, RouterState.CRASHED),
        (RouterState.CRASHED, RouterState.STARTING),
        (RouterState.CRASHED, RouterState.STOPPED),
    ):
        require_transition(current, target)


def test_router_state_machine_rejects_invalid_transition() -> None:
    with pytest.raises(ValueError, match="stopped -> ready"):
        require_transition(RouterState.STOPPED, RouterState.READY)


def test_router_launch_builds_argument_vector(tmp_path: Path) -> None:
    executable = tmp_path / "llama-server.exe"
    preset = tmp_path / "llama-models.ini"
    key_file = tmp_path / "api-keys.txt"
    for path in (executable, preset, key_file):
        path.touch()

    arguments = RouterLaunch(
        executable=executable,
        preset_path=preset,
        host="0.0.0.0",
        port=1234,
        api_key_file=key_file,
    ).arguments()

    assert arguments == (
        str(executable.resolve()),
        "--models-preset",
        str(preset.resolve()),
        "--host",
        "0.0.0.0",
        "--port",
        "1234",
        "--api-key-file",
        str(key_file.resolve()),
    )


@pytest.mark.parametrize(
    ("launch", "message"),
    (
        (RouterLaunch(Path("missing.exe"), Path("missing.ini")), "executable not found"),
        (
            RouterLaunch(Path("missing.exe"), Path("missing.ini"), port=0),
            "outside the valid range",
        ),
        (
            RouterLaunch(Path("missing.exe"), Path("missing.ini"), host="localhost"),
            "host must be an IP address",
        ),
        (
            RouterLaunch(Path("missing.exe"), Path("missing.ini"), host="0.0.0.0"),
            "requires an API key file",
        ),
        (
            RouterLaunch(
                Path("missing.exe"),
                Path("missing.ini"),
                api_key_file=Path("missing-keys.txt"),
            ),
            "API key file not found",
        ),
    ),
)
def test_router_launch_rejects_invalid_configuration(
    launch: RouterLaunch, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        launch.arguments()