"""Command line entry point: ``pdd-agent --config values.yaml``."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

import uvicorn

from pdd_agent import __version__
from pdd_agent.api.app import create_app
from pdd_agent.config import ConfigError, load_settings


def configure_logging(level: str) -> None:
    """Configure root logging from the ``server.logLevel`` value."""
    logging.basicConfig(
        level={"warn": logging.WARNING}.get(level, getattr(logging, level.upper())),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Load the configuration and serve the API until interrupted."""
    parser = argparse.ArgumentParser(prog="pdd-agent", description="PDD test generation agent")
    parser.add_argument(
        "--config",
        action="append",
        default=[],
        type=Path,
        metavar="FILE",
        help="YAML configuration file merged over the defaults; may be repeated",
    )
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)
    try:
        settings = load_settings(args.config)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2
    configure_logging(settings.server.log_level)
    host, _, port = settings.server.listen_addr.rpartition(":")
    uvicorn.run(create_app(settings), host=host, port=int(port), log_config=None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
