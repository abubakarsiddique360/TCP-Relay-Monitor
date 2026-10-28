import json

import pytest

from tcp_relay_monitor.config import ConfigError, RelayConfig, validate_config

VALID = {
    "listen_host": "127.0.0.1",
    "listen_port": 8081,
    "target_host": "127.0.0.1",
    "target_port": 8080,
}


def test_minimal_config_is_valid():
    assert validate_config(VALID) == []
    config = RelayConfig.from_dict(VALID)
    assert config.max_connections == 64
    assert config.idle_timeout == 300.0
    assert config.allowlist == ()


def test_missing_required_fields_are_listed():
    problems = validate_config({"listen_port": 1})
    assert any("listen_host" in p for p in problems)
    assert any("target_host" in p for p in problems)
    assert any("target_port" in p for p in problems)
    assert len(problems) == 3


@pytest.mark.parametrize("port", [-1, 70000])
def test_port_range_is_checked(port):
    data = dict(VALID, listen_port=port)
    assert any("listen_port" in p for p in validate_config(data))


@pytest.mark.parametrize("value", ["8080", 8080.5, True])
def test_port_must_be_integer(value):
    data = dict(VALID, target_port=value)
    assert any("target_port must be an integer" in p for p in validate_config(data))


def test_timeout_must_be_positive():
    assert validate_config(dict(VALID, idle_timeout=0))
    assert validate_config(dict(VALID, connect_timeout=-2))


def test_connection_limit_must_be_positive():
    assert validate_config(dict(VALID, max_connections=0))
    assert validate_config(dict(VALID, max_connections="many"))


def test_allowlist_rejects_bad_cidr():
    problems = validate_config(dict(VALID, allowlist=["127.0.0.0/8", "not-a-network"]))
    assert any("not-a-network" in p for p in problems)


def test_allowlist_must_be_list_of_strings():
    assert validate_config(dict(VALID, allowlist="127.0.0.1"))


def test_allows_everything_when_empty():
    config = RelayConfig.from_dict(VALID)
    assert config.allows("192.0.2.9")


def test_allows_respects_cidr():
    config = RelayConfig.from_dict(dict(VALID, allowlist=["10.1.0.0/16"]))
    assert config.allows("10.1.4.5")
    assert not config.allows("10.2.4.5")


def test_load_from_file(tmp_path):
    path = tmp_path / "relay.json"
    path.write_text(json.dumps(VALID), encoding="utf-8")
    assert RelayConfig.load(path).listen_port == 8081


def test_load_reports_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="config not found"):
        RelayConfig.load(tmp_path / "nope.json")


def test_load_reports_broken_json(tmp_path):
    path = tmp_path / "relay.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid JSON"):
        RelayConfig.load(path)


def test_load_reports_non_object(tmp_path):
    path = tmp_path / "relay.json"
    path.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(ConfigError, match="JSON object"):
        RelayConfig.load(path)


def test_from_dict_keeps_unknown_keys_as_extra():
    config = RelayConfig.from_dict(dict(VALID, comment="test rig"))
    assert config.extra == {"comment": "test rig"}
