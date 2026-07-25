"""Command-line entry point for the installed KiroSpan gateway."""

from __future__ import annotations

import logging
import os
import sys

import uvicorn

from .composition import create_app
from .config import ConfigError, GatewayConfig


def main() -> None:
    """Validate environment configuration and run the local HTTP gateway."""
    try:
        config = GatewayConfig.from_env(os.environ)
    except ConfigError as exc:
        print(f"KiroSpan configuration error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    uvicorn.run(
        create_app(config),
        host=config.host,
        port=config.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
