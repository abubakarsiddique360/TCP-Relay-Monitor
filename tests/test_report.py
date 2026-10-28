import json

from tcp_relay_monitor.accesslog import (
    peak_concurrency,
    percentile,
    read_records,
    summarize,
    write_record,
)
from tcp_relay_monitor.relay import AccessRecord
from tcp_relay_monitor.report import render_json, render_markdown, render_text


def make_record(index, opened, closed, up=1000, down=2000, reason="completed", ip="127.0.0.1"):
    return AccessRecord(
        connection_id=index,
        client_ip=ip,
        client_port=40000 + index,
        target_host="127.0.0.1",
        target_port=9000,
        opened_at=opened,
        closed_at=closed,
        bytes_up=up,
        bytes_down=down,
        reason=reason,
    )


def test_percentile_handles_empty_and_single():
    assert percentile([], 0.5) == 0.0
    assert percentile([12.0], 0.99) == 12.0


def test_percentile_picks_nearest_rank():
    values = [float(v) for v in range(1, 101)]
    assert percentile(values, 0.5) == 51.0
    assert percentile(values, 0.95) == 95.0


def test_peak_concurrency_counts_overlap():
    records = [
        make_record(1, 0.0, 10.0),
        make_record(2, 1.0, 4.0),
        make_record(3, 2.0, 3.0),
        make_record(4, 20.0, 21.0),
    ]
    assert peak_concurrency(records) == 3


def test_summarize_reports_bytes_success_and_reasons():
    records = [
        make_record(1, 100.0, 100.01, up=10, down=20),
        make_record(2, 100.02, 100.04, up=30, down=40, reason="idle_timeout"),
        make_record(3, 100.05, 100.06, up=0, down=0, reason="over_limit"),
    ]
    summary = summarize(records)
    assert summary["connections"] == 3
    assert summary["completed"] == 1
    assert summary["success_rate"] == 33.3
    assert summary["bytes_up"] == 40
    assert summary["bytes_down"] == 60
    assert summary["bytes_total"] == 100
    assert summary["reasons"] == {"completed": 1, "idle_timeout": 1, "over_limit": 1}
    assert summary["window_seconds"] == 0.01
    assert summary["peak_concurrency"] == 1
    assert summary["top_clients"][0]["client_ip"] == "127.0.0.1"


def test_summarize_is_safe_for_empty_input():
    summary = summarize([])
    assert summary["connections"] == 0
    assert summary["success_rate"] == 0.0
    assert summary["throughput_kb_s"] == 0.0


def test_log_round_trip(tmp_path, wait_for):
    path = tmp_path / "access.jsonl"
    record = make_record(7, 1000.0, 1000.5, reason="denied")
    write_record(path, record)
    records, skipped = read_records(path)
    assert skipped == 0
    assert records[0].connection_id == 7
    assert records[0].reason == "denied"
    assert records[0].duration_ms == 500.0


def test_read_records_skips_malformed_lines(tmp_path):
    path = tmp_path / "access.jsonl"
    good = json.dumps(make_record(1, 0.0, 1.0).as_dict())
    path.write_text(good + "\n{bad json}\n\n" + json.dumps({"connection_id": "x"}) + "\n",
                    encoding="utf-8")
    records, skipped = read_records(path)
    assert len(records) == 1
    assert skipped == 2


def test_read_missing_log_returns_empty(tmp_path):
    assert read_records(tmp_path / "absent.jsonl") == ([], 0)


def test_text_report_contains_key_numbers():
    text = render_text(summarize([make_record(1, 0.0, 0.02)]))
    assert "Relay traffic report" in text
    assert "Connections" in text
    assert "Latency (ms)" in text
    assert "completed" in text


def test_markdown_and_json_render():
    summary = summarize([make_record(1, 0.0, 0.02), make_record(2, 0.1, 0.2, reason="denied")])
    markdown = render_markdown(summary)
    assert "| Connections | 2 |" in markdown
    assert "denied by allowlist" in markdown
    payload = json.loads(render_json(summary))
    assert payload["connections"] == 2
