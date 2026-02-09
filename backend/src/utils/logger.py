"""Structured logging setup for Code Tumbler.

This module provides structured logging using structlog.
Logs can be output in JSON format for machine parsing or text format for humans.
"""

import sys
import logging
import structlog
from pathlib import Path
from typing import Optional


def setup_logger(
    level: str = "INFO",
    log_format: str = "json",
    log_file: Optional[str] = None
) -> None:
    """Configure structured logging for the application.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        log_format: Output format ("json" or "text").
        log_file: Optional path to log file. If None, logs only to stdout.
    """
    # Convert level string to logging constant
    log_level = getattr(logging, level.upper(), logging.INFO)

    # Create log directory if needed
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)

    # Configure structlog
    if log_format == "json":
        processors = [
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer()
        ]
    else:  # text format
        processors = [
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S", utc=False),
            structlog.dev.ConsoleRenderer()
        ]

    # Configure handlers
    handlers = []

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    handlers.append(console_handler)

    # File handler
    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(log_level)
        handlers.append(file_handler)

    # Configure stdlib logging
    logging.basicConfig(
        format="%(message)s",
        level=log_level,
        handlers=handlers
    )

    # Configure structlog
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: Optional[str] = None) -> structlog.stdlib.BoundLogger:
    """Get a structured logger instance.

    Args:
        name: Name for the logger (typically module name).

    Returns:
        Configured structlog logger.
    """
    return structlog.get_logger(name or __name__)


# Convenience function for testing
def test_logger():
    """Test logger configuration with sample messages."""
    logger = get_logger("test")

    logger.debug("This is a debug message", extra_data="debug_value")
    logger.info("This is an info message", user="test_user", action="login")
    logger.warning("This is a warning message", resource="memory", usage_pct=85)
    logger.error("This is an error message", error_code=500, endpoint="/api/test")

    try:
        raise ValueError("Test exception")
    except Exception:
        logger.exception("Caught an exception", context="test")


if __name__ == "__main__":
    # Test with JSON format
    print("=== JSON Format ===")
    setup_logger(level="DEBUG", log_format="json")
    test_logger()

    print("\n=== Text Format ===")
    setup_logger(level="DEBUG", log_format="text")
    test_logger()
