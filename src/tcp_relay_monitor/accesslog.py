"""JSONL access log: append one record per connection, then fold the file into statistics."""

from __future__ import annotations

import json
import threading
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

from .relay import AccessRecord

_LOG_LOCK = threading.Lock()


def write_record(path: str | Path, record: AccessRecord) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record.as_dict(), separators=(",", ":"))
    with _LOG_LOCK:
        with target.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")


def read_records(path: str | Path) -> tuple[list[AccessRecord], int]:
    """Return parsed records and the number of malformed lines that were skipped."""
    source = Path(path)
    records: list[AccessRecord] = []
    skipped = 0
    if not source.exists():
        return records, skipped
    for raw in source.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            records.append(
                AccessRecord(
                    connection_id=int(data["connection_id"]),
                    client_ip=str(data["client_ip"]),
                    client_port=int(data["client_port"]),
                    target_host=str(data["target_host"]),
                    target_port=int(data["target_port"]),
                    opened_at=float(data["opened_at"]),
                    closed_at=float(data["closed_at"]),
                    bytes_up=int(data["bytes_up"]),
                    bytes_down=int(data["bytes_down"]),
                    reason=str(data["reason"]),
                )
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            skipped += 1
    return records, skipped


def percentile(values: Iterable[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return round(ordered[0], 3)
    index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return round(ordered[index], 3)


def peak_concurrency(records: list[AccessRecord]) -> int:
    events: list[tuple[float, int]] = []
    for record in records:
        events.append((record.opened_at, 1))
        events.append((record.closed_at, -1))
    events.sort(key=lambda item: (item[0], item[1]))
    current = peak = 0
    for _, delta in events:
        current += delta
        peak = max(peak, current)
    return peak


def summarize(records: list[AccessRecord]) -> dict:
    completed = [record for record in records if record.reason == "completed"]
    durations = [record.duration_ms for record in completed]
    total_up = sum(record.bytes_up for record in records)
    total_down = sum(record.bytes_down for record in records)
    reasons = Counter(record.reason for record in records)
    clients = Counter(record.client_ip for record in records)
    window_records = completed or records
    span = 0.0
    if window_records:
        start = min(record.opened_at for record in window_records)
        end = max(record.closed_at for record in window_records)
        span = round(end - start, 3)

    return {
        "connections": len(records),
        "completed": len(completed),
        "success_rate": round(len(completed) / len(records) * 100, 1) if records else 0.0,
        "bytes_up": total_up,
        "bytes_down": total_down,
        "bytes_total": total_up + total_down,
        "latency_sample": len(completed),
        "avg_duration_ms": round(sum(durations) / len(durations), 3) if durations else 0.0,
        "p50_duration_ms": percentile(durations, 0.50),
        "p95_duration_ms": percentile(durations, 0.95),
        "p99_duration_ms": percentile(durations, 0.99),
        "peak_concurrency": peak_concurrency(records),
        "reasons": dict(sorted(reasons.items())),
        "top_clients": [
            {"client_ip": ip, "connections": count} for ip, count in clients.most_common(5)
        ],
        "window_seconds": span,
        "throughput_kb_s": round((total_up + total_down) / 1024 / span, 1) if span > 0 else 0.0,
    }
