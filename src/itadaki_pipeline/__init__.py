"""Idempotent archive and processed-data pipeline for Itadaki ``.rec`` logs."""

from .parser import KEY_NAMES, ParsedEvent

__version__ = "0.9.0"

__all__ = ["KEY_NAMES", "ParsedEvent"]
