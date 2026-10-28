"""A reproducible experiment: an echo target, a relay in front of it, and real sockets.

``tcp-relay demo`` uses this module so the numbers in the README come from an actual
run instead of a mock. Payload sizes are deterministic, only timings vary.
"""

from __future__ import annotations

import socket
import socketserver
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .accesslog import summarize, write_record
from .config import RelayConfig
from .relay import AccessRecord, RelayServer


class _EchoHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        while True:
            data = self.request.recv(65536)
            if not data:
                return
            self.request.sendall(data)


class EchoServer:
    """A loopback TCP echo target used as the upstream in demos and tests."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self._server = socketserver.ThreadingTCPServer((host, port), _EchoHandler)
        self._server.daemon_threads = True
        self._server.request_queue_size = 128
        self.port = self._server.server_address[1]
        self._thread: threading.Thread | None = None

    def start(self) -> int:
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self.port

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2.0)


@dataclass
class DemoResult:
    summary: dict
    server_port: int
    target_port: int
    client_latencies_ms: list[float]
    records: list[AccessRecord]


def payload_for(connection: int, round_index: int, size: int) -> bytes:
    """Deterministic, checkable payload with a little variation per request."""
    length = size + (connection % 4) * 512 + round_index * 256
    body = bytes(((connection * 31 + round_index * 7 + i) % 256) for i in range(length))
    return body


def _roundtrip(sock: socket.socket, payload: bytes) -> float:
    started = time.perf_counter()
    sock.sendall(payload)
    received = bytearray()
    while len(received) < len(payload):
        chunk = sock.recv(65536)
        if not chunk:
            break
        received.extend(chunk)
    if bytes(received) != payload:
        raise RuntimeError("echo mismatch: relay corrupted the stream")
    return round((time.perf_counter() - started) * 1000, 3)


def run_demo(
    *,
    connections: int = 8,
    rounds: int = 3,
    payload_size: int = 4096,
    connection_limit: int = 5,
    idle_timeout: float = 1.0,
    log_path: str | Path = "relay-access.jsonl",
) -> DemoResult:
    echo = EchoServer()
    target_port = echo.start()
    config = RelayConfig(
        listen_host="127.0.0.1",
        listen_port=0,
        target_host="127.0.0.1",
        target_port=target_port,
        max_connections=connection_limit,
        idle_timeout=idle_timeout,
        log_path=str(log_path),
    )
    records: list[AccessRecord] = []
    relay = RelayServer(config, on_record=records.append)
    relay.start()
    assert relay.port is not None

    latencies: list[float] = []
    failures: list[str] = []
    lock = threading.Lock()

    def client(connection: int, hold_open: bool) -> None:
        try:
            sock = socket.create_connection(("127.0.0.1", relay.port), timeout=10.0)
        except OSError:
            with lock:
                failures.append(f"connection {connection} refused")
            return
        with sock:
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                for round_index in range(rounds):
                    payload = payload_for(connection, round_index, payload_size)
                    elapsed = _roundtrip(sock, payload)
                    with lock:
                        latencies.append(elapsed)
                if hold_open:
                    # Staying quiet on an open socket is what the idle timer exists for.
                    time.sleep(idle_timeout + 0.3)
            except OSError:
                with lock:
                    failures.append(f"connection {connection} dropped")

    holder = threading.Thread(target=client, args=(0, True), daemon=True)
    holder.start()
    time.sleep(0.05)
    workers = [
        threading.Thread(target=client, args=(index, False), daemon=True)
        for index in range(1, connections)
    ]
    for thread in workers:
        thread.start()
    for thread in workers:
        thread.join(timeout=20.0)

    # Let the idle timer fire on the held connection before the relay goes away,
    # otherwise the close reason would be "reset" instead of "idle_timeout".
    holder.join(timeout=idle_timeout + 10.0)
    relay.stop()
    echo.stop()

    for record in records:
        write_record(log_path, record)

    return DemoResult(
        summary=summarize(records),
        server_port=relay.port,
        target_port=target_port,
        client_latencies_ms=sorted(latencies),
        records=records,
    )

    for record in records:
        write_record(log_path, record)

    return DemoResult(
        summary=summarize(records),
        server_port=relay.port,
        target_port=target_port,
        client_latencies_ms=sorted(latencies),
        records=records,
    )
