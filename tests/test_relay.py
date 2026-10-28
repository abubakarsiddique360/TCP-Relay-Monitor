import socket
import struct
import threading

import pytest

from tcp_relay_monitor.config import RelayConfig
from tcp_relay_monitor.demo import EchoServer
from tcp_relay_monitor.relay import (
    COMPLETED,
    CONNECT_FAILED,
    DENIED,
    IDLE_TIMEOUT,
    OVER_LIMIT,
    RelayServer,
    pipe_streams,
)


def _paired():
    left, right = socket.socketpair()
    left.settimeout(5)
    right.settimeout(5)
    return left, right


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_pipe_streams_copies_both_directions():
    app, relay_client = _paired()
    relay_upstream, target = _paired()
    result: list = []

    thread = threading.Thread(
        target=lambda: result.append(pipe_streams(relay_client, relay_upstream, idle_timeout=5)),
        daemon=True,
    )
    thread.start()

    app.sendall(b"ping")
    assert target.recv(4) == b"ping"
    target.sendall(b"pong")
    assert app.recv(4) == b"pong"

    app.close()
    target.close()
    thread.join(timeout=5)
    assert result and result[0][2] == COMPLETED
    assert result[0][0] == 4
    assert result[0][1] == 4


def test_pipe_streams_honours_idle_timeout():
    app, relay_client = _paired()
    relay_upstream, target = _paired()
    result = pipe_streams(relay_client, relay_upstream, idle_timeout=0.2)
    assert result[2] == IDLE_TIMEOUT
    for sock in (app, relay_client, relay_upstream, target):
        sock.close()


def test_pipe_streams_reports_reset_after_peer_disappears():
    app, relay_client = _paired()
    relay_upstream, target = _paired()
    target.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    target.close()
    result = pipe_streams(relay_client, relay_upstream, idle_timeout=2.0)
    app.close()
    relay_client.close()
    relay_upstream.close()
    assert result[2] == "reset"


@pytest.fixture()
def relay_setup():
    echo = EchoServer()
    echo.start()
    records: list = []
    config = RelayConfig(target_port=echo.port, idle_timeout=5.0, max_connections=4)
    server = RelayServer(config, on_record=records.append)
    port = server.start()
    yield server, port, records, echo
    server.stop()
    echo.stop()


def test_relay_round_trip_is_accounted(relay_setup, recv_exact, wait_for):
    _server, port, records, _echo = relay_setup
    payload = bytes(range(256)) * 40
    with socket.create_connection(("127.0.0.1", port), timeout=5) as client:
        client.sendall(payload)
        assert recv_exact(client, len(payload)) == payload

    assert wait_for(lambda: len(records) == 1)
    record = records[0]
    assert record.reason == COMPLETED
    assert record.bytes_up == len(payload)
    assert record.bytes_down == len(payload)
    assert record.duration_ms >= 0
    assert record.as_dict()["duration_ms"] == record.duration_ms


def test_connection_limit_is_enforced(wait_for):
    echo = EchoServer()
    echo.start()
    records: list = []
    server = RelayServer(
        RelayConfig(target_port=echo.port, idle_timeout=10.0, max_connections=1),
        on_record=records.append,
    )
    port = server.start()
    try:
        first = socket.create_connection(("127.0.0.1", port), timeout=5)
        assert wait_for(lambda: server.stats.active == 1)
        second = socket.create_connection(("127.0.0.1", port), timeout=5)
        assert wait_for(lambda: any(r.reason == OVER_LIMIT for r in records))
        second.close()
        first.close()
    finally:
        server.stop()
        echo.stop()


def test_allowlist_denies_client(wait_for):
    echo = EchoServer()
    echo.start()
    records: list = []
    config = RelayConfig(
        target_port=echo.port, idle_timeout=5.0, allowlist=("10.0.0.0/8",)
    )
    server = RelayServer(config, on_record=records.append)
    port = server.start()
    try:
        client = socket.create_connection(("127.0.0.1", port), timeout=5)
        client.close()
        assert wait_for(lambda: len(records) == 1)
        assert records[0].reason == DENIED
        assert records[0].bytes_up == 0
        assert server.stats.accepted == 0
        assert server.stats.rejected == 1
        assert server.stats.active == 0
    finally:
        server.stop()
        echo.stop()


def test_unreachable_target_is_logged(wait_for):
    echo = EchoServer()
    echo.start()
    dead_port = _free_port()
    echo.stop()
    records: list = []
    server = RelayServer(
        RelayConfig(target_port=dead_port, idle_timeout=1.0, connect_timeout=0.5),
        on_record=records.append,
    )
    port = server.start()
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=5) as client:
            client.settimeout(1.0)
            try:
                client.recv(1)
            except OSError:
                pass
        assert wait_for(lambda: len(records) == 1)
        assert records[0].reason == CONNECT_FAILED
    finally:
        server.stop()


def test_stats_accumulate_bytes(relay_setup, recv_exact, wait_for):
    server, port, _records, _echo = relay_setup
    with socket.create_connection(("127.0.0.1", port), timeout=5) as client:
        client.sendall(b"hello")
        recv_exact(client, 5)
    assert wait_for(lambda: server.stats.bytes_up == 5)
    assert server.stats.bytes_down == 5
    assert server.stats.reasons[COMPLETED] == 1
