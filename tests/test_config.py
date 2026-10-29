import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from game_platform.config import Config, load_config

EXAMPLE = Path(__file__).resolve().parents[1] / "config.example.json"


def _write_config(directory: Path, config: object) -> Path:
    path = directory / "config.json"
    path.write_text(json.dumps(config))
    return path


def test_the_example_config_lists_every_field_with_its_default() -> None:
    example = json.loads(EXAMPLE.read_text())
    assert Config.model_validate(example) == Config()
    defaults = json.loads(Config().model_dump_json())
    defaults["data_file"]["path"] = Path(defaults["data_file"]["path"]).as_posix()
    assert example == defaults


def test_omitted_fields_take_their_defaults(tmp_path: Path) -> None:
    config = load_config(_write_config(tmp_path, {"server": {"port": 9000}}))
    assert (config.server.host, config.server.port, config.debug_tools) == (
        "127.0.0.1",
        9000,
        True,
    )


def test_data_file_path_is_relative_to_the_config_file(tmp_path: Path) -> None:
    config_dir = tmp_path / "conf"
    config_dir.mkdir()

    def data_file(path: str) -> Path:
        config = {"data_file": {"path": path}}
        return load_config(_write_config(config_dir, config)).data_file.path

    assert data_file("data/game.json") == config_dir / "data" / "game.json"
    absolute_path = tmp_path / "absolute" / "game.json"
    assert data_file(str(absolute_path)) == absolute_path
    assert data_file("~/game.json") == Path.home() / "game.json"


def test_invalid_configs_are_rejected(tmp_path: Path) -> None:
    def errors(config: object) -> list[str]:
        with pytest.raises(ValidationError) as error:
            load_config(_write_config(tmp_path, config))
        return [
            ".".join(str(part) for part in detail["loc"]) + ": " + detail["msg"]
            for detail in error.value.errors()
        ]

    assert errors({"data_file": {"save_interval": 5}}) == [
        "data_file.save_interval: Extra inputs are not permitted"
    ]
    assert errors(
        {"server": {"port": 0}, "data_file": {"save_interval_seconds": -1}}
    ) == [
        "server.port: Input should be greater than or equal to 1",
        "data_file.save_interval_seconds: Input should be greater than or equal to 0",
    ]
    assert errors({"log_level": "LOUD"}) == [
        "log_level: Input should be 'DEBUG', 'INFO', 'WARNING' or 'ERROR'"
    ]


@pytest.mark.parametrize(
    ("config", "expected_error"),
    [
        ({"server": {"host": "127.0.0.1"}}, None),
        ({"server": {"host": "0.0.0.0"}}, None),
        ({"server": {"host": "::"}}, None),
        (
            {"server": {"host": "localhost"}},
            "Value error, master_password must be set when server.host ('localhost') "
            "is a hostname rather than a numeric IP address",
        ),
        ({"server": {"host": "localhost"}, "master_password": "s3cret"}, None),
        # Without debug tools there is nothing for the password to protect.
        ({"server": {"host": "localhost"}, "debug_tools": False}, None),
    ],
)
def test_a_hostname_needs_a_master_password(
    tmp_path: Path, config: object, expected_error: str | None
) -> None:
    path = _write_config(tmp_path, config)
    if expected_error is None:
        load_config(path)
        return
    with pytest.raises(ValidationError) as error:
        load_config(path)
    assert [detail["msg"] for detail in error.value.errors()] == [expected_error]
