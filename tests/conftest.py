import json
import socket
import time
from pathlib import Path

import pytest


@pytest.fixture()
def wait_for():
    def _wait(predicate, timeout=5.0, interval=0.02):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(interval)
        return False

    return _wait


@pytest.fixture()
def recv_exact():
    def _recv(sock: socket.socket, size: int) -> bytes:
        data = bytearray()
        while len(data) < size:
            chunk = sock.recv(size - len(data))
            if not chunk:
                break
            data.extend(chunk)
        return bytes(data)

    return _recv


@pytest.fixture()
def write_log(tmp_path: Path):
    def _write(records):
        path = tmp_path / "access.jsonl"
        path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
        return path

    return _write
