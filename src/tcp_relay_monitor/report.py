"""Render the summary as text for a terminal, markdown for a README, or JSON for a dashboard."""

from __future__ import annotations

import json

REASON_LABELS = {
    "completed": "completed",
    "idle_timeout": "idle timeout",
    "reset": "reset",
    "denied": "denied by allowlist",
    "over_limit": "rejected, over limit",
    "connect_failed": "upstream connect failed",
    "listener_closed": "listener closed",
}


def _human_bytes(value: int | float) -> str:
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} GiB"


def render_text(summary: dict, *, title: str = "Relay traffic report") -> str:
    lines = [title, "=" * len(title), ""]
    lines.append(f"Connections       {summary['connections']:>10}")
    lines.append(f"Completed         {summary['completed']:>10}  ({summary['success_rate']}%)")
    lines.append(f"Peak concurrency  {summary['peak_concurrency']:>10}")
    lines.append(f"Window            {summary['window_seconds']:>10} s")
    lines.append(f"Throughput        {summary['throughput_kb_s']:>10} KiB/s")
    lines.append("")
    lines.append(f"Bytes client->target  {_human_bytes(summary['bytes_up']):>12}")
    lines.append(f"Bytes target->client  {_human_bytes(summary['bytes_down']):>12}")
    lines.append(f"Bytes total           {_human_bytes(summary['bytes_total']):>12}")
    lines.append("")
    lines.append("Latency (ms)      avg      p50      p95      p99")
    lines.append(
        "                 {avg:>7}  {p50:>7}  {p95:>7}  {p99:>7}".format(
            avg=summary["avg_duration_ms"],
            p50=summary["p50_duration_ms"],
            p95=summary["p95_duration_ms"],
            p99=summary["p99_duration_ms"],
        )
    )
    if summary["reasons"]:
        lines.append("")
        lines.append("Close reasons")
        width = max(len(REASON_LABELS.get(k, k)) for k in summary["reasons"])
        for reason, count in summary["reasons"].items():
            lines.append(f"  {REASON_LABELS.get(reason, reason):<{width}}  {count:>5}")
    if summary["top_clients"]:
        lines.append("")
        lines.append("Top clients")
        for client in summary["top_clients"]:
            lines.append(f"  {client['client_ip']:<18} {client['connections']:>5} connections")
    return "\n".join(lines)


def render_markdown(summary: dict, *, title: str = "Relay traffic report") -> str:
    lines = [f"### {title}", ""]
    lines.append("| Metric | Value |")
    lines.append("| --- | --- |")
    lines.append(f"| Connections | {summary['connections']} |")
    lines.append(f"| Completed | {summary['completed']} ({summary['success_rate']}%) |")
    lines.append(f"| Peak concurrency | {summary['peak_concurrency']} |")
    lines.append(f"| Window | {summary['window_seconds']} s |")
    lines.append(f"| Throughput | {summary['throughput_kb_s']} KiB/s |")
    lines.append(f"| Bytes up | {_human_bytes(summary['bytes_up'])} |")
    lines.append(f"| Bytes down | {_human_bytes(summary['bytes_down'])} |")
    lines.append(f"| Latency p50 / p95 / p99 | {summary['p50_duration_ms']} / "
                 f"{summary['p95_duration_ms']} / {summary['p99_duration_ms']} ms |")
    lines.append("")
    lines.append("| Close reason | Count |")
    lines.append("| --- | --- |")
    for reason, count in summary["reasons"].items():
        lines.append(f"| {REASON_LABELS.get(reason, reason)} | {count} |")
    return "\n".join(lines)


def render_json(summary: dict) -> str:
    return json.dumps(summary, indent=2, sort_keys=True)
