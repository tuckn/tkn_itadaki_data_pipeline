"""Copyable, one-setting-per-line configuration display."""

from __future__ import annotations

import json
from typing import Any


def config_lines(value: Any, prefix: str = "") -> list[str]:
    """Flatten nested configuration without quoting strings or escaping path separators."""
    if isinstance(value, dict):
        if not value:
            return [f"{prefix}={{}}"]
        return [
            line
            for key, item in value.items()
            for line in config_lines(item, f"{prefix}.{key}" if prefix else key)
        ]
    if isinstance(value, list):
        if not value:
            return [f"{prefix}=[]"]
        return [
            line
            for index, item in enumerate(value)
            for line in config_lines(item, f"{prefix}[{index}]")
        ]
    if isinstance(value, str):
        escapes = {"\r": "\\r", "\n": "\\n", "\t": "\\t", "\b": "\\b", "\f": "\\f"}
        display = "".join(
            escapes.get(char, f"\\u{ord(char):04x}")
            if ord(char) < 32 or ord(char) in {127, 133, 0x2028, 0x2029}
            else char
            for char in value
        )
    else:
        display = json.dumps(value, ensure_ascii=False)
    return [f"{prefix}={display}"]
