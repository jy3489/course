import json

import pytest

from fake_users.config import ConfigError, Settings, load, settings_from_dict, settings_to_dict, validate


def write(tmp_path, data):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data))
    return path


def test_the_example_config_loads_once_it_has_a_password(tmp_path):
    example = json.loads(open("config.example.json").read())
    with pytest.raises(ConfigError, match="control_password"):
        load(write(tmp_path, example))
    config = load(write(tmp_path, {**example, "control_password": "s3cret"}))
    assert config.settings == Settings()
    assert config.allow_remote_target is False
    assert config.settings.target_url == "http://127.0.0.1:8000"


def test_allow_remote_target_must_be_exactly_true(tmp_path):
    for value in ("true", 1, "yes"):
        assert load(write(tmp_path, {"control_password": "x", "allow_remote_target": value})).allow_remote_target is False
    assert load(write(tmp_path, {"control_password": "x", "allow_remote_target": True})).allow_remote_target is True


def test_settings_changes_are_checked_and_converted():
    s = settings_from_dict({"max_users": "8", "users_per_hour": "30", "skip_games": "Debug, Poker"})
    assert (s.max_users, s.users_per_hour, s.skip_games) == (8, 30.0, ("Debug", "Poker"))
    with pytest.raises(ConfigError, match="unknown settings: nope"):
        settings_from_dict({"nope": 1})
    with pytest.raises(ConfigError, match="add up to 100"):
        settings_from_dict({"mix_vs_computer": 90})
    with pytest.raises(ConfigError, match="think_min_seconds <= think_max_seconds"):
        settings_from_dict({"think_min_seconds": 5, "think_max_seconds": 1})
    with pytest.raises(ConfigError, match="leave_probability"):
        settings_from_dict({"leave_probability": 2})
    with pytest.raises(ConfigError, match="speed_match_percent"):
        settings_from_dict({"speed_match_percent": 150})
    with pytest.raises(ConfigError, match="max_users"):
        settings_from_dict({"max_users": "lots"})


def test_a_bad_change_keeps_the_old_settings():
    base = settings_from_dict({"max_users": 3})
    with pytest.raises(ConfigError):
        settings_from_dict({"max_users": 4, "mix_quick_match": 99}, base)
    assert base.max_users == 3


def test_settings_round_trip_through_json():
    s = settings_from_dict({"skip_games": ["Debug"], "speed_match_percent": 35})
    assert settings_from_dict(json.loads(json.dumps(settings_to_dict(s)))) == s
    validate(s)
