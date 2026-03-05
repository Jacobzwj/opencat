"""CLI entry for OpenCat."""

from __future__ import annotations

import argparse
import logging

from opencat import config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="opencat",
        description="OpenCat — a cute desktop cat companion for OpenClaw",
    )
    parser.add_argument("--host", type=str, default=None,
                        help="OpenClaw gateway host (default: 127.0.0.1, use Tailscale IP for remote)")
    parser.add_argument("--port", type=int, default=None, help="Override OpenClaw gateway port")
    parser.add_argument("--token", type=str, default=None, help="Override OpenClaw gateway token")
    parser.add_argument("--debug", action="store_true", help="Enable debug mode")
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    config.load(port_override=args.port, token_override=args.token,
                host_override=args.host)

    from opencat.app import run_app
    run_app(debug=args.debug)


if __name__ == "__main__":
    main()
