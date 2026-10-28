"""Regenerate the README assets from a real run.

Usage:  python scripts/render_assets.py

Writes:
  docs/demo-summary.json          numbers quoted in the README
  docs/images/traffic-report.png  terminal capture of ``tcp-relay demo``
  docs/images/connection-timeline.svg  per-connection view of the same run
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
IMAGES = ROOT / "docs" / "images"
sys.path.insert(0, str(SRC))

from tcp_relay_monitor.demo import run_demo  # noqa: E402
from tcp_relay_monitor.report import render_text  # noqa: E402

BG = "#0d1117"
CHROME = "#161b22"
FG = "#e6edf3"
MUTED = "#8b949e"
ACCENT = "#3fb950"
WARN = "#d29922"
ERROR = "#f85149"


def load_font(size: int) -> ImageFont.FreeTypeFont:
    for name in ("consola.ttf", "cour.ttf", "DejaVuSansMono.ttf"):
        candidate = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / name
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default()


def render_terminal(text: str, out_path: Path, title: str) -> None:
    font = load_font(16)
    lines = text.expandtabs(4).splitlines() or [""]
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    char_width = probe.textlength("M", font=font)
    line_height = 23
    pad = 22
    width = int(max(len(line) for line in lines) * char_width) + pad * 2
    height = pad * 2 + len(lines) * line_height + 44
    image = Image.new("RGB", (max(width, 640), height), BG)
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, image.width, 36], fill=CHROME)
    for index, color in enumerate(("#ff5f56", "#ffbd2e", "#27c93f")):
        draw.ellipse([16 + index * 22, 11, 28 + index * 22, 23], fill=color)
    draw.text((92, 10), title, font=load_font(14), fill=MUTED)

    for row, line in enumerate(lines):
        y = 52 + row * line_height
        color = FG
        if row == 0:
            color = "#79c0ff"
        elif line.strip().startswith(("Relay traffic", "Close reasons", "Top clients")):
            color = "#79c0ff"
        elif "over limit" in line or "denied" in line or "failed" in line:
            color = ERROR
        elif "idle timeout" in line:
            color = WARN
        elif line.strip().startswith(("Connections", "Completed", "Peak", "Throughput")):
            color = ACCENT
        draw.text((pad, y), line, font=font, fill=color)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(out_path)


def render_timeline(records, out_path: Path) -> None:
    records = sorted(records, key=lambda r: r.opened_at)
    start = records[0].opened_at
    labels = [f"#{r.connection_id}" for r in records]
    offsets = [(r.opened_at - start) * 1000 for r in records]
    durations = [max(r.duration_ms, 1.0) for r in records]
    colors = {
        "completed": "#2f81f7",
        "idle_timeout": "#d29922",
        "over_limit": "#f85149",
        "denied": "#a371f7",
        "connect_failed": "#db6d28",
    }

    fig, (ax_time, ax_bytes) = plt.subplots(
        1, 2, figsize=(13.0, 4.6), gridspec_kw={"width_ratios": [1.45, 1]}
    )
    fig.patch.set_facecolor("#ffffff")

    for index, record in enumerate(records):
        ax_time.barh(index, durations[index], left=offsets[index],
                     color=colors.get(record.reason, "#57606a"), height=0.66)
    ax_time.set_yticks(range(len(records)))
    ax_time.set_yticklabels(labels, fontsize=9)
    ax_time.invert_yaxis()
    ax_time.set_xlabel("time since first connection (ms)")
    ax_time.set_title("Connection timeline by close reason", fontsize=11, loc="left")
    ax_time.grid(axis="x", linestyle=":", alpha=0.5)
    for spine in ("top", "right"):
        ax_time.spines[spine].set_visible(False)
    handles = [
        plt.Rectangle((0, 0), 1, 1, color=color)
        for key, color in colors.items()
        if any(r.reason == key for r in records)
    ]
    names = [key for key in colors if any(r.reason == key for r in records)]
    ax_time.legend(handles, names, fontsize=8, frameon=False, ncol=3,
                   loc="upper center", bbox_to_anchor=(0.5, -0.18))

    order = sorted(records, key=lambda r: r.connection_id)
    positions = range(len(order))
    ax_bytes.bar([p - 0.19 for p in positions], [r.bytes_up / 1024 for r in order],
                 width=0.38, label="client -> target", color="#2f81f7")
    ax_bytes.bar([p + 0.19 for p in positions], [r.bytes_down / 1024 for r in order],
                 width=0.38, label="target -> client", color="#3fb950")
    ax_bytes.set_xticks(list(positions))
    ax_bytes.set_xticklabels(labels, fontsize=7, rotation=60, ha="right")
    ax_bytes.set_ylabel("KiB")
    ax_bytes.set_title("Bytes carried per connection", fontsize=11, loc="left")
    ax_bytes.grid(axis="y", linestyle=":", alpha=0.5)
    ax_bytes.legend(fontsize=8, frameon=False)
    for spine in ("top", "right"):
        ax_bytes.spines[spine].set_visible(False)

    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, format=out_path.suffix.lstrip(".") or "svg", transparent=False)
    plt.close(fig)


def main() -> int:
    IMAGES.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        result = run_demo(connections=8, rounds=3, payload_size=4096, connection_limit=6,
                          idle_timeout=1.0, log_path=Path(tmp) / "demo.jsonl")
    summary = result.summary
    summary["client_latency_ms"] = {
        "min": round(result.client_latencies_ms[0], 3) if result.client_latencies_ms else 0.0,
        "max": round(result.client_latencies_ms[-1], 3) if result.client_latencies_ms else 0.0,
    }
    (ROOT / "docs" / "demo-summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )

    header = (
        "TCP relay demo\n"
        "--------------\n"
        f"target    127.0.0.1:{result.target_port} (echo)\n"
        f"relay     127.0.0.1:{result.server_port} -> target\n"
        "clients   8 connections x 3 requests, cap 6\n"
        "log       relay-access.jsonl\n\n"
    )
    render_terminal(header + render_text(summary),
                    IMAGES / "traffic-report.png",
                    "tcp-relay demo  -  localhost relay in front of an echo target")
    render_timeline(result.records, IMAGES / "connection-timeline.svg")
    print(f"connections={summary['connections']} completed={summary['completed']} "
          f"p95={summary['p95_duration_ms']}ms bytes={summary['bytes_total']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
