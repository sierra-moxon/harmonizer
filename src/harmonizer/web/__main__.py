"""CLI entry point: serve the Harmonizer web UI (``just web``).

``python -m harmonizer.web`` — starts the NiceGUI server on ``127.0.0.1:8080`` by
default. Provider credentials (e.g. ``ANTHROPIC_API_KEY``) must be in the
environment for real runs; the deterministic pre-pass and the UI itself work
without them.
"""

from __future__ import annotations

import argparse

from harmonizer.web.app import run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve the Harmonizer web UI.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args(argv)
    run(host=args.host, port=args.port, reload=args.reload)
    return 0


if __name__ in {"__main__", "__mp_main__"}:
    raise SystemExit(main())
