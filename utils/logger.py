"""Structured logging using Rich for Clara AI pipeline."""
import logging
from rich.logging import RichHandler
from rich.console import Console

console = Console()


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = RichHandler(
            console=Console(stderr=True),
            rich_tracebacks=True,
            show_path=False,
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger
