"""Attach CLI context without changing API exception types, messages, or return values."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any


def annotate(error: BaseException, **context: Any) -> BaseException:
    existing = dict(getattr(error, "cli_context", {}))
    for key, value in context.items():
        existing.setdefault(key, value)
    error.cli_context = existing
    return error


@contextmanager
def diagnostic_context(**context: Any) -> Iterator[None]:
    try:
        yield
    except (Exception, KeyboardInterrupt) as error:
        annotate(error, **context)
        raise
