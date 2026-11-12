"""Shared structured logging configuration using loguru."""

import sys
from pathlib import Path

from loguru import logger

LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)


def setup_logging(service_name: str, level: str = "INFO") -> None:
    """Configure loguru for a specific service.

    Args:
        service_name: Name of the service (e.g. 'deal_scout', 'investor_bot').
        level: Minimum log level.
    """
    logger.remove()

    log_format = (
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
        "<level>{level: <8}</level> | "
        f"<cyan>{service_name}</cyan> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
        "<level>{message}</level>"
    )

    # Console output
    logger.add(
        sys.stderr,
        format=log_format,
        level=level,
        colorize=True,
    )

    # File output — rotated daily, kept 30 days
    logger.add(
        LOG_DIR / f"{service_name}.log",
        format=log_format,
        level=level,
        rotation="00:00",
        retention="30 days",
        compression="gz",
        enqueue=True,
    )

    # Separate error log
    logger.add(
        LOG_DIR / f"{service_name}_errors.log",
        format=log_format,
        level="ERROR",
        rotation="00:00",
        retention="60 days",
        compression="gz",
        enqueue=True,
    )

    logger.info(f"Logging initialized for {service_name} at level {level}")
