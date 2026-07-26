"""Idempotent archive and processed-data pipeline for Itadaki ``.rec`` logs."""

from .parser import KEY_NAMES, ParsedEvent

__all__ = ["KEY_NAMES", "ParsedEvent"]
