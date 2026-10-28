import json

import pytest

from tcp_relay_monitor.cli import main
from tcp_relay_monitor.demo import run_demo
from tcp_relay_monitor.relay import AccessRecord


def write_config(tmp_path, **overrides):
    data = {
        "listen_host": "127.0.0.1",
        "listen_port": 0,
        "target_host": "127.0.0.1",
        "target_port": 9000,
    }
    data.update(overrides)
    path = tmp_path / "relay.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_check_accepts_valid_config(tmp_path, capsys):
    code = main(["check", "--config", str(write_config(tmp_path))])
    output = capsys.readouterr().out
    assert code == 0
    assert "config ok" in output
    assert "any client" in output


def test_check_rejects_broken_config(tmp_path, capsys):
    path = tmp_path / "relay.json"
    path.write_text("{}", encoding="utf-8")
    code = main(["check", "--config", str(path)])
    assert code == 2
    assert "config error" in capsys.readouterr().err


def test_report_missing_log(tmp_path, capsys):
    code = main(["report", "--log", str(tmp_path / "absent.jsonl")])
    assert code == 1
    assert "log not found" in capsys.readouterr().err


def test_report_renders_json(tmp_path, capsys):
    log = tmp_path / "access.jsonl"
    record = AccessRecord(1, "127.0.0.1", 1234, "127.0.0.1", 9000, 0.0, 0.01, 10, 20, "completed")
    log.write_text(json.dumps(record.as_dict()) + "\n", encoding="utf-8")
    code = main(["report", "--log", str(log), "--format", "json"])
    assert code == 0
    assert json.loads(capsys.readouterr().out)["bytes_total"] == 30


def test_report_renders_markdown(tmp_path, capsys):
    log = tmp_path / "access.jsonl"
    record = AccessRecord(1, "127.0.0.1", 1234, "127.0.0.1", 9000, 0.0, 0.01, 10, 20, "completed")
    log.write_text(json.dumps(record.as_dict()) + "\n", encoding="utf-8")
    assert main(["report", "--log", str(log), "--format", "markdown"]) == 0
    assert "| Connections | 1 |" in capsys.readouterr().out


def test_demo_runs_and_writes_log(tmp_path, capsys):
    log = tmp_path / "demo.jsonl"
    code = main(
        [
            "demo",
            "--connections",
            "3",
            "--rounds",
            "1",
            "--payload",
            "512",
            "--limit",
            "4",
            "--idle-timeout",
            "0.3",
            "--log",
            str(log),
        ]
    )
    output = capsys.readouterr().out
    assert code == 0
    assert "TCP relay demo" in output
    assert "Connections" in output
    assert log.exists()
    assert len(log.read_text(encoding="utf-8").splitlines()) >= 3


def test_demo_result_accounts_for_echoed_bytes(tmp_path):
    result = run_demo(connections=3, rounds=1, payload_size=256, connection_limit=4,
                      idle_timeout=0.3, log_path=tmp_path / "demo.jsonl")
    payloads = [r for r in result.records if r.reason == "completed"]
    assert payloads
    assert all(record.bytes_up == record.bytes_down for record in payloads)
    assert result.summary["completed"] >= 2
    assert result.summary["reasons"].get("idle_timeout") == 1


def test_unknown_command_exits_with_usage():
    with pytest.raises(SystemExit) as excinfo:
        main([])
    assert excinfo.value.code == 2
