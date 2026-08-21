"""
app/utils/logger.py
===================
Central logging configuration for the Bible AI project.

Usage
-----
    from app.utils.logger import get_logger
    log = get_logger(__name__)
    log.info("Something happened")
"""

import logging
import sys
from pathlib import Path

# Log file sits next to the project root
_LOG_FILE = Path(__file__).resolve().parents[2] / "bible_ai.log"

_configured = False


def _configure() -> None:
    global _configured
    if _configured:
        return

    fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)-8s] %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler — INFO and above
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)

    # File handler — DEBUG and above (full detail for debugging)
    try:
        fh = logging.FileHandler(_LOG_FILE, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
    except OSError:
        fh = None  # If we can't write a log file, just use console

    root = logging.getLogger("bible_ai")
    root.setLevel(logging.DEBUG)
    root.addHandler(ch)
    if fh:
        root.addHandler(fh)
    root.propagate = False

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """
    Return a child logger under the 'bible_ai' namespace.

    Parameters
    ----------
    name : typically __name__ from the calling module
    """
    _configure()
    # Prefix so every project logger is grouped under (and inherits the
    # handlers of) the "bible_ai" logger _configure() sets up — Python's
    # logging hierarchy is purely dot-based, so e.g. "app.retrieval"
    # is NOT a child of "bible_ai" and would silently miss its handlers
    # (falling through to the unconfigured root logger) without this.
    if not name.startswith("bible_ai"):
        name = f"bible_ai.{name}"
    return logging.getLogger(name)
