"""Parse Itadaki binary record files."""

from __future__ import annotations

import datetime as dt
import struct
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

KEY_NAMES = [
    "[BS]", "[TAB]", "[Enter]", "[Shift]", "[LShift]", "[RShift]",
    "[LCtrl]", "[RCtrl]", "[Alt]", "[Ctrl]", "[Esc]", "[変換]",
    "[Space]", "[PageUp]", "[PagEDown]", "[End]", "[Home]",
    "←", "↑", "→", "↓", "[Ins]", "[Del]",
    "0(Main Key)", "1(Main Key)", "2(Main Key)", "3(Main Key)",
    "4(Main Key)", "5(Main Key)", "6(Main Key)", "7(Main Key)",
    "8(Main Key)", "9(Main Key)",
    "A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M",
    "N", "O", "P", "Q", "R", "S", "T", "U", "V", "W", "X", "Y", "Z",
    "0(Ten Key)", "1(Ten Key)", "2(Ten Key)", "3(Ten Key)",
    "4(Ten Key)", "5(Ten Key)", "6(Ten Key)", "7(Ten Key)",
    "8(Ten Key)", "9(Ten Key)",
    "*(Ten Key)", "+(Ten Key)", "-(Ten Key)", ".(Ten Key)", "/(Ten Key)",
    "[F1]", "[F2]", "[F3]", "[F4]", "[F5]", "[F6]",
    "[F7]", "[F8]", "[F9]", "[F10]", "[F11]", "[F12]",
    "[NumLock]", "[ScrollLock]",
    ":(Colon)", ";(SemiColon)", ",(Comma)", "-", ".(Main Key)",
    "/(Main Key)", "@", "[", "\\(Yen Mark)", "]", "^", "\\(Back Slash)",
    "(LClick)", "(RClick)",
]

DELPHI_EPOCH = dt.datetime(1899, 12, 30)
JST = dt.timezone(dt.timedelta(hours=9), name="Asia/Tokyo")
KEY_RECORD_SIZE = 12
SERIES = ("Key", "MoC", "MoM", "Pow")
SERIES_SIZES = {"MoC": 4, "MoM": 8, "Pow": 4}


class ParseError(ValueError):
    """Raised when an Itadaki record file violates the known schema."""


@dataclass(frozen=True)
class ParsedEvent:
    index: int
    code: int
    key_name: str
    is_mouse: bool
    datetime_local: dt.datetime


def date_from_filename(path: Path) -> dt.date:
    try:
        return dt.datetime.strptime(path.stem, "%Y%m%d").date()
    except ValueError as exc:
        raise ParseError(f"Invalid Itadaki date filename: {path.name}") from exc


def delphi_datetime_to_python(value: float) -> dt.datetime:
    try:
        return DELPHI_EPOCH + dt.timedelta(days=value)
    except (OverflowError, ValueError) as exc:
        raise ParseError(f"Invalid Delphi TDateTime value: {value!r}") from exc


def key_name(code: int) -> str:
    if not 0 <= code < len(KEY_NAMES):
        raise ParseError(f"Unknown Itadaki key code: {code}")
    return KEY_NAMES[code]


def iter_key_file(
    path: Path,
    *,
    expected_date: dt.date | None = None,
) -> Iterator[ParsedEvent]:
    data = path.read_bytes()
    if len(data) % KEY_RECORD_SIZE:
        raise ParseError(
            f"{path}: size {len(data)} is not divisible by {KEY_RECORD_SIZE}"
        )

    for index in range(len(data) // KEY_RECORD_SIZE):
        offset = index * KEY_RECORD_SIZE
        code = struct.unpack_from("<I", data, offset)[0]
        raw_timestamp = struct.unpack_from("<d", data, offset + 4)[0]
        event_datetime = delphi_datetime_to_python(raw_timestamp)
        if expected_date is not None and event_datetime.date() != expected_date:
            raise ParseError(
                f"{path}: event {index} has date {event_datetime.date()}, "
                f"expected {expected_date}"
            )
        yield ParsedEvent(
            index=index,
            code=code,
            key_name=key_name(code),
            is_mouse=code in (100, 101),
            datetime_local=event_datetime.replace(tzinfo=JST),
        )


def parse_key_file(path: Path) -> list[dict]:
    """Compatibility shape used by the legacy CSV exporter."""
    records: list[dict] = []
    data = path.read_bytes()
    if len(data) % KEY_RECORD_SIZE:
        raise ParseError(
            f"{path}: size {len(data)} is not divisible by {KEY_RECORD_SIZE}"
        )
    for index in range(len(data) // KEY_RECORD_SIZE):
        offset = index * KEY_RECORD_SIZE
        code = struct.unpack_from("<I", data, offset)[0]
        raw_timestamp = struct.unpack_from("<d", data, offset + 4)[0]
        event_datetime = delphi_datetime_to_python(raw_timestamp)
        records.append(
            {
                "code": code,
                "key_name": key_name(code),
                "is_mouse": code in (100, 101),
                "datetime": event_datetime,
                "timestamp_raw": raw_timestamp,
            }
        )
    return records


def _read_exact(path: Path, size: int) -> bytes:
    data = path.read_bytes()
    if len(data) != size:
        raise ParseError(f"{path}: expected {size} bytes, got {len(data)}")
    return data


def parse_moc_file(path: Path) -> int:
    return struct.unpack("<I", _read_exact(path, 4))[0]


def parse_mom_file(path: Path) -> float:
    return struct.unpack("<Q", _read_exact(path, 8))[0] / 10000.0


def parse_pow_file(path: Path) -> int:
    return struct.unpack("<I", _read_exact(path, 4))[0]


def validate_series_file(path: Path, series: str) -> None:
    if series == "Key":
        size = path.stat().st_size
        if size % KEY_RECORD_SIZE:
            raise ParseError(
                f"{path}: size {size} is not divisible by {KEY_RECORD_SIZE}"
            )
        return
    expected = SERIES_SIZES[series]
    actual = path.stat().st_size
    if actual != expected:
        raise ParseError(f"{path}: expected {expected} bytes, got {actual}")
