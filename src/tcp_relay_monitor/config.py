"""Relay configuration: one JSON file, validated before a socket is opened."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from ipaddress import ip_network
from pathlib import Path
from typing import Any

REQUIRED = ("listen_host", "listen_port", "target_host", "target_port")


class ConfigError(ValueError):
    """Raised when a relay configuration cannot be used."""


@dataclass(frozen=True)
class RelayConfig:
    listen_host: str = "127.0.0.1"
    listen_port: int = 0
    target_host: str = "127.0.0.1"
    target_port: int = 0
    max_connections: int = 64
    idle_timeout: float = 300.0
    connect_timeout: float = 5.0
    allowlist: tuple[str, ...] = ()
    log_path: str = "relay-access.jsonl"
    backpressure_chunk: int = 65536
    extra: dict[str, Any] = field(default_factory=dict, compare=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RelayConfig:
        problems = validate_config(data)
        if problems:
            raise ConfigError("; ".join(problems))
        known = {name for name in cls.__dataclass_fields__ if name != "extra"}
        kwargs = {key: value for key, value in data.items() if key in known}
        if "allowlist" in kwargs:
            kwargs["allowlist"] = tuple(kwargs["allowlist"])
        extra = {key: value for key, value in data.items() if key not in known}
        return cls(**kwargs, extra=extra)

    @classmethod
    def load(cls, path: str | Path) -> RelayConfig:
        raw = Path(path)
        try:
            data = json.loads(raw.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ConfigError(f"config not found: {raw}") from exc
        except json.JSONDecodeError as exc:
            raise ConfigError(f"config is not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ConfigError("config must be a JSON object")
        return cls.from_dict(data)

    def allows(self, client_ip: str) -> bool:
        from ipaddress import ip_address

        if not self.allowlist:
            return True
        address = ip_address(client_ip)
        for entry in self.allowlist:
            if address in ip_network(entry, strict=False):
                return True
        return False


def validate_config(data: dict[str, Any]) -> list[str]:
    """Return human readable problems, empty when the config is usable."""
    problems: list[str] = []
    if not isinstance(data, dict):
        return ["config must be a JSON object"]

    for key in REQUIRED:
        if key not in data:
            problems.append(f"missing required field: {key}")

    for key in ("listen_port", "target_port"):
        value = data.get(key)
        if key in data and (not isinstance(value, int) or isinstance(value, bool)):
            problems.append(f"{key} must be an integer")
        elif isinstance(value, int) and not 0 <= value <= 65535:
            problems.append(f"{key} must be between 0 and 65535")

    for key in ("listen_host", "target_host"):
        value = data.get(key)
        if key in data and (not isinstance(value, str) or not value.strip()):
            problems.append(f"{key} must be a non-empty string")

    if "max_connections" in data:
        value = data["max_connections"]
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            problems.append("max_connections must be an integer >= 1")

    for key in ("idle_timeout", "connect_timeout"):
        if key in data:
            value = data[key]
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
                problems.append(f"{key} must be a positive number")

    if "backpressure_chunk" in data:
        value = data["backpressure_chunk"]
        if not isinstance(value, int) or isinstance(value, bool) or value < 1024:
            problems.append("backpressure_chunk must be an integer >= 1024")

    allowlist = data.get("allowlist")
    if allowlist is not None:
        if not isinstance(allowlist, list) or not all(isinstance(item, str) for item in allowlist):
            problems.append("allowlist must be a list of CIDR strings")
        else:
            for item in allowlist:
                try:
                    ip_network(item, strict=False)
                except ValueError:
                    problems.append(f"allowlist entry is not a CIDR network: {item}")

    log_path = data.get("log_path")
    if log_path is not None and (not isinstance(log_path, str) or not log_path.strip()):
        problems.append("log_path must be a non-empty string")

    return problems
