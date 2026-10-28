"""Command line entry point for ``tcp-relay``."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import __version__
from .accesslog import read_records, summarize
from .config import ConfigError, RelayConfig
from .demo import run_demo
from .relay import RelayServer
from .report import render_json, render_markdown, render_text


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tcp-relay",
        description="Relay TCP traffic and turn the access log into a report.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the relay in the foreground")
    serve.add_argument("--config", required=True, help="path to the relay JSON config")
    serve.add_argument("--quiet", action="store_true", help="do not print each connection")

    check = sub.add_parser("check", help="validate a config without opening a socket")
    check.add_argument("--config", required=True)

    demo = sub.add_parser("demo", help="run a loopback experiment and print the report")
    demo.add_argument("--connections", type=int, default=8)
    demo.add_argument("--rounds", type=int, default=3)
    demo.add_argument("--payload", type=int, default=4096, help="base payload bytes")
    demo.add_argument("--limit", type=int, default=6, help="max concurrent connections")
    demo.add_argument("--idle-timeout", type=float, default=1.0)
    demo.add_argument("--log", default="relay-access.jsonl")
    demo.add_argument("--format", choices=("text", "json", "markdown"), default="text")

    report = sub.add_parser("report", help="summarise an existing access log")
    report.add_argument("--log", required=True)
    report.add_argument("--format", choices=("text", "json", "markdown"), default="text")
    return parser


def _print_report(summary: dict, fmt: str) -> None:
    if fmt == "json":
        print(render_json(summary))
    elif fmt == "markdown":
        print(render_markdown(summary))
    else:
        print(render_text(summary))


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.command == "check":
        try:
            config = RelayConfig.load(args.config)
        except ConfigError as exc:
            print(f"config error: {exc}", file=sys.stderr)
            return 2
        print(f"config ok: {args.config}")
        print(f"  listen   {config.listen_host}:{config.listen_port}")
        print(f"  target   {config.target_host}:{config.target_port}")
        print(f"  limit    {config.max_connections} concurrent connections")
        print(f"  idle     {config.idle_timeout}s before a quiet connection is closed")
        print(f"  log      {config.log_path}")
        allow = ", ".join(config.allowlist) if config.allowlist else "any client"
        print(f"  clients  {allow}")
        return 0

    if args.command == "serve":
        try:
            config = RelayConfig.load(args.config)
        except ConfigError as exc:
            print(f"config error: {exc}", file=sys.stderr)
            return 2

        def log(record) -> None:  # type: ignore[no-untyped-def]
            from .accesslog import write_record

            write_record(config.log_path, record)
            if not args.quiet:
                print(
                    f"#{record.connection_id:<4} {record.client_ip}:{record.client_port} "
                    f"{record.reason:<14} up={record.bytes_up}B down={record.bytes_down}B "
                    f"{record.duration_ms}ms"
                )

        server = RelayServer(config, on_record=log)
        port = server.start()
        print(f"relay listening on {config.listen_host}:{port} -> "
              f"{config.target_host}:{config.target_port}")
        print("press Ctrl+C to stop")
        try:
            while True:
                time.sleep(1.0)
        except KeyboardInterrupt:
            print("\nstopping relay")
        finally:
            server.stop()
        return 0

    if args.command == "demo":
        result = run_demo(
            connections=args.connections,
            rounds=args.rounds,
            payload_size=args.payload,
            connection_limit=args.limit,
            idle_timeout=args.idle_timeout,
            log_path=args.log,
        )
        print("TCP relay demo")
        print("--------------")
        print(f"target    127.0.0.1:{result.target_port} (echo)")
        print(f"relay     127.0.0.1:{result.server_port} -> target")
        print(f"clients   {args.connections} connections x {args.rounds} requests, "
              f"cap {args.limit}")
        print(f"log       {args.log}")
        print()
        _print_report(result.summary, args.format)
        return 0

    if args.command == "report":
        path = Path(args.log)
        if not path.exists():
            print(f"log not found: {path}", file=sys.stderr)
            return 1
        records, skipped = read_records(path)
        if not records:
            print(f"no usable records in {path}", file=sys.stderr)
            return 1
        summary = summarize(records)
        if skipped:
            print(f"warning: skipped {skipped} malformed line(s)", file=sys.stderr)
        if args.format == "json":
            print(json.dumps(summary, indent=2, sort_keys=True))
        else:
            _print_report(summary, args.format)
        return 0

    return 2
