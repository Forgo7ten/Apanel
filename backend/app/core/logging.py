"""Logging setup shared by API and worker entry points."""

from __future__ import annotations

import logging


def configure_logging(level: str = "INFO") -> None:
    """Configure a small, container-friendly log format once per process."""

    root_logger = logging.getLogger()
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    if not root_logger.handlers:
        logging.basicConfig(
            level=numeric_level,
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
        )
    else:
        root_logger.setLevel(numeric_level)
