"""The relay itself: accept, connect upstream, copy bytes, record the outcome.

The copy loop is written as a pure function so the interesting part - half close,
idle timeout, byte accounting - can be tested without a listening socket.
"""

from __future__ import annotations

import select
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field

from .config import RelayConfig

COMPLETED = "completed"
IDLE_TIMEOUT = "idle_timeout"
RESET = "reset"
DENIED = "denied"
OVER_LIMIT = "over_limit"
CONNECT_FAILED = "connect_failed"
LISTENER_CLOSED = "listener_closed"

CLOSE_REASONS = (
    COMPLETED,
    IDLE_TIMEOUT,
    RESET,
    DENIED,
    OVER_LIMIT,
    CONNECT_FAILED,
    LISTENER_CLOSED,
)


@dataclass
class AccessRecord:
    connection_id: int
    client_ip: str
    client_port: int
    target_host: str
    target_port: int
    opened_at: float
    closed_at: float
    bytes_up: int
    bytes_down: int
    reason: str

    @property
    def duration_ms(self) -> float:
        return round((self.closed_at - self.opened_at) * 1000, 3)

    def as_dict(self) -> dict:
        data = asdict(self)
        data["opened_at"] = round(self.opened_at, 6)
        data["closed_at"] = round(self.closed_at, 6)
        data["duration_ms"] = self.duration_ms
        return data


def pipe_streams(
    client: socket.socket,
    upstream: socket.socket,
    *,
    idle_timeout: float = 300.0,
    chunk: int = 65536,
) -> tuple[int, int, str]:
    """Copy bytes both ways until both sides close, one side resets, or time runs out.

    Returns ``(bytes_up, bytes_down, reason)`` where up means client -> upstream.
    """
    bytes_up = 0
    bytes_down = 0
    client_open = True
    upstream_open = True
    deadline = time.monotonic() + idle_timeout

    try:
        while client_open or upstream_open:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return bytes_up, bytes_down, IDLE_TIMEOUT
            readable = []
            if client_open:
                readable.append(client)
            if upstream_open:
                readable.append(upstream)
            try:
                ready, _, _ = select.select(readable, [], [], min(remaining, 1.0))
            except (OSError, ValueError):
                return bytes_up, bytes_down, RESET
            if not ready:
                continue

            if client_open and client in ready:
                data = _recv(client)
                if data is None:
                    return bytes_up, bytes_down, RESET
                if not data:
                    client_open = False
                    _shutdown_write(upstream)
                else:
                    bytes_up += len(data)
                    deadline = time.monotonic() + idle_timeout
                    if not _sendall(upstream, data):
                        return bytes_up, bytes_down, RESET

            if upstream_open and upstream in ready:
                data = _recv(upstream)
                if data is None:
                    return bytes_up, bytes_down, RESET
                if not data:
                    upstream_open = False
                    _shutdown_write(client)
                else:
                    bytes_down += len(data)
                    deadline = time.monotonic() + idle_timeout
                    if not _sendall(client, data):
                        return bytes_up, bytes_down, RESET
    finally:
        _shutdown_write(client)
        _shutdown_write(upstream)

    return bytes_up, bytes_down, COMPLETED


def _recv(sock: socket.socket) -> bytes | None:
    try:
        return sock.recv(65536)
    except (ConnectionResetError, ConnectionAbortedError, OSError, ValueError):
        return None


def _sendall(sock: socket.socket, data: bytes) -> bool:
    try:
        sock.sendall(data)
        return True
    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError, ValueError):
        return False


def _shutdown_write(sock: socket.socket) -> None:
    try:
        sock.shutdown(socket.SHUT_WR)
    except (OSError, ValueError):
        pass


@dataclass
class RelayStats:
    accepted: int = 0
    active: int = 0
    rejected: int = 0
    bytes_up: int = 0
    bytes_down: int = 0
    reasons: dict[str, int] = field(default_factory=dict)

    def record(self, record: AccessRecord) -> None:
        self.reasons[record.reason] = self.reasons.get(record.reason, 0) + 1
        self.bytes_up += record.bytes_up
        self.bytes_down += record.bytes_down


class RelayServer:
    """A threaded TCP relay with a hard connection cap and per-connection log."""

    def __init__(
        self,
        config: RelayConfig,
        on_record: Callable[[AccessRecord], None] | None = None,
    ) -> None:
        self.config = config
        self.on_record = on_record
        self.port: int | None = None
        self.stats = RelayStats()
        self._lock = threading.Lock()
        self._listener: socket.socket | None = None
        self._threads: list[threading.Thread] = []
        self._clients: set[socket.socket] = set()
        self._stopping = threading.Event()
        self._next_id = 0
        self._closed = threading.Event()

    def start(self) -> int:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((self.config.listen_host, self.config.listen_port))
        listener.listen(128)
        listener.settimeout(0.5)
        self._listener = listener
        self.port = listener.getsockname()[1]
        thread = threading.Thread(target=self._accept_loop, name="relay-accept", daemon=True)
        thread.start()
        self._threads.append(thread)
        return self.port

    def _accept_loop(self) -> None:
        while not self._stopping.is_set():
            try:
                client, address = self._listener.accept()  # type: ignore[union-attr]
            except TimeoutError:
                continue
            except OSError:
                return
            with self._lock:
                self._next_id += 1
                connection_id = self._next_id
                over_limit = self.stats.active >= self.config.max_connections
            if over_limit:
                with self._lock:
                    self.stats.rejected += 1
                self._reject(client, address, connection_id, OVER_LIMIT)
                continue
            if not self.config.allows(address[0]):
                with self._lock:
                    self.stats.rejected += 1
                self._reject(client, address, connection_id, DENIED)
                continue
            with self._lock:
                self.stats.active += 1
                self.stats.accepted += 1
                self._clients.add(client)
            thread = threading.Thread(
                target=self._handle,
                args=(client, address, connection_id),
                name=f"relay-conn-{connection_id}",
                daemon=True,
            )
            thread.start()
            self._threads.append(thread)

    def _reject(
        self, client: socket.socket, address: tuple[str, int], connection_id: int, reason: str
    ) -> None:
        now = time.time()
        record = AccessRecord(
            connection_id=connection_id,
            client_ip=address[0],
            client_port=address[1],
            target_host=self.config.target_host,
            target_port=self.config.target_port,
            opened_at=now,
            closed_at=now,
            bytes_up=0,
            bytes_down=0,
            reason=reason,
        )
        _close_quietly(client)
        self._emit(record)

    def _handle(
        self, client: socket.socket, address: tuple[str, int], connection_id: int
    ) -> None:
        opened = time.time()
        bytes_up = bytes_down = 0
        reason = CONNECT_FAILED
        upstream: socket.socket | None = None
        try:
            upstream = socket.create_connection(
                (self.config.target_host, self.config.target_port),
                timeout=self.config.connect_timeout,
            )
            upstream.settimeout(None)
            client.settimeout(None)
            # Relays forward already-framed protocols; Nagle only adds latency here.
            for sock in (client, upstream):
                try:
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                except OSError:
                    pass
            bytes_up, bytes_down, reason = pipe_streams(
                client,
                upstream,
                idle_timeout=self.config.idle_timeout,
                chunk=self.config.backpressure_chunk,
            )
        except OSError:
            reason = CONNECT_FAILED
        finally:
            _close_quietly(client)
            if upstream is not None:
                _close_quietly(upstream)
            with self._lock:
                self.stats.active = max(0, self.stats.active - 1)
                self._clients.discard(client)
            self._emit(
                AccessRecord(
                    connection_id=connection_id,
                    client_ip=address[0],
                    client_port=address[1],
                    target_host=self.config.target_host,
                    target_port=self.config.target_port,
                    opened_at=opened,
                    closed_at=time.time(),
                    bytes_up=bytes_up,
                    bytes_down=bytes_down,
                    reason=reason,
                )
            )

    def _emit(self, record: AccessRecord) -> None:
        with self._lock:
            self.stats.record(record)
        if self.on_record is not None:
            self.on_record(record)

    def stop(self) -> None:
        self._stopping.set()
        if self._listener is not None:
            try:
                self._listener.close()
            except OSError:
                pass
        with self._lock:
            clients = list(self._clients)
        for client in clients:
            _close_quietly(client)
        for thread in list(self._threads):
            thread.join(timeout=2.0)
        self._closed.set()

    def wait_closed(self, timeout: float = 10.0) -> bool:
        return self._closed.wait(timeout)

    def __enter__(self) -> RelayServer:
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()


def _close_quietly(sock: socket.socket) -> None:
    try:
        sock.close()
    except OSError:
        pass
