#!/usr/bin/env python3
"""Legacy whole-folder CSV exporter for Itadaki Rec data.

This module retains the original three historical output files.
"""

from __future__ import annotations

import csv
import datetime
import sys
from collections import Counter
from pathlib import Path

from .parser import (
    KEY_NAMES,
    parse_key_file,
    parse_moc_file,
    parse_mom_file,
    parse_pow_file,
)


def get_key_name(code: int) -> str:
    return KEY_NAMES[code] if 0 <= code < len(KEY_NAMES) else f"UNKNOWN({code})"


def is_mouse_event(code: int) -> bool:
    return code in (100, 101)


def get_date_from_filename(filename: str) -> str | None:
    stem = Path(filename).stem
    if len(stem) == 8 and stem.isdigit():
        return f"{stem[:4]}-{stem[4:6]}-{stem[6:8]}"
    return None


def parse_rec_folder(rec_dir: Path) -> dict:
    directories = {
        "key_data": (rec_dir / "Key", parse_key_file),
        "moc_data": (rec_dir / "MoC", parse_moc_file),
        "mom_data": (rec_dir / "MoM", parse_mom_file),
        "pow_data": (rec_dir / "Pow", parse_pow_file),
    }
    if not directories["key_data"][0].exists():
        raise FileNotFoundError(
            f"Key/ フォルダが見つかりません: {directories['key_data'][0]}"
        )

    all_dates: set[str] = set()
    result: dict[str, dict] = {}
    for key, (directory, parser) in directories.items():
        values = {}
        if directory.exists():
            print(f"{directory.name}/ フォルダを解析中...")
            for path in sorted(directory.glob("*.rec")):
                date_text = get_date_from_filename(path.name)
                if date_text:
                    all_dates.add(date_text)
                    values[date_text] = parser(path)
        result[key] = values
    result["all_dates"] = sorted(all_dates)
    return result


def export_key_events_csv(data: dict, output_path: Path) -> None:
    print(f"key_events.csv を書き出し中: {output_path}")
    with output_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "date", "datetime", "key_code", "key_name", "is_mouse",
                "year", "month", "day", "hour", "minute", "second", "weekday",
            ]
        )
        for date_text in data["all_dates"]:
            for record in data["key_data"].get(date_text, []):
                event_dt = record["datetime"]
                writer.writerow(
                    [
                        date_text,
                        event_dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                        record["code"],
                        record["key_name"],
                        1 if record["is_mouse"] else 0,
                        event_dt.year,
                        event_dt.month,
                        event_dt.day,
                        event_dt.hour,
                        event_dt.minute,
                        event_dt.second,
                        event_dt.weekday(),
                    ]
                )


def export_daily_summary_csv(data: dict, output_path: Path) -> None:
    print(f"daily_summary.csv を書き出し中: {output_path}")
    with output_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "date", "year", "month", "day", "weekday", "key_count",
                "mouse_clicks", "total_events", "moc_clicks", "mouse_move_cm",
                "mouse_move_km", "power_on_sec", "power_on_hms",
            ]
        )
        for date_text in data["all_dates"]:
            date_value = datetime.date.fromisoformat(date_text)
            records = data["key_data"].get(date_text, [])
            key_count = sum(not record["is_mouse"] for record in records)
            mouse_clicks = sum(record["is_mouse"] for record in records)
            move_cm = data["mom_data"].get(date_text, "")
            move_km = (
                f"{move_cm / 100000:.4f}" if isinstance(move_cm, float) else ""
            )
            power_sec = data["pow_data"].get(date_text, "")
            if isinstance(power_sec, int):
                hours = power_sec // 3600
                minutes = (power_sec % 3600) // 60
                seconds = power_sec % 60
                power_hms = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
            else:
                power_hms = ""
            writer.writerow(
                [
                    date_text,
                    date_value.year,
                    date_value.month,
                    date_value.day,
                    date_value.weekday(),
                    key_count,
                    mouse_clicks,
                    len(records),
                    data["moc_data"].get(date_text, ""),
                    move_cm,
                    move_km,
                    power_sec,
                    power_hms,
                ]
            )


def _category(name: str) -> str:
    if name in ("(LClick)", "(RClick)"):
        return "Mouse"
    if name.startswith("[") and name[1:3] == "F" and name[3:-1].isdigit():
        return "FunctionKey"
    if name in {
        "[LShift]", "[RShift]", "[Shift]", "[LCtrl]", "[RCtrl]", "[Ctrl]",
        "[Alt]", "[Esc]", "[BS]", "[TAB]", "[Enter]", "[Space]", "[Ins]",
        "[Del]", "[PageUp]", "[PagEDown]", "[End]", "[Home]", "[NumLock]",
        "[ScrollLock]", "[変換]",
    }:
        return "Modifier/Special"
    if name in ("←", "↑", "→", "↓"):
        return "Arrow"
    if name.endswith("(Ten Key)"):
        return "Numpad"
    if len(name) == 1 and name.isupper():
        return "Alphabet"
    if name.endswith("(Main Key)") and name[0].isdigit():
        return "Number"
    return "Symbol"


def export_key_stats_csv(data: dict, output_path: Path) -> None:
    counter = Counter(
        record["key_name"]
        for records in data["key_data"].values()
        for record in records
    )
    total = sum(counter.values())
    name_to_code = {name: code for code, name in enumerate(KEY_NAMES)}
    with output_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "rank", "key_code", "key_name", "count", "percentage",
                "is_mouse", "category",
            ]
        )
        for rank, (name, count) in enumerate(counter.most_common(), 1):
            writer.writerow(
                [
                    rank,
                    name_to_code.get(name, -1),
                    name,
                    count,
                    f"{count / total * 100:.4f}" if total else "0.0000",
                    1 if name in ("(LClick)", "(RClick)") else 0,
                    _category(name),
                ]
            )


def main() -> None:
    rec_dir = Path(sys.argv[1]) if len(sys.argv) >= 2 else Path(__file__).parent / "Rec"
    out_dir = Path(sys.argv[2]) if len(sys.argv) >= 3 else Path(__file__).parent / "output"
    if not rec_dir.exists():
        print(f"エラー: Rec フォルダが見つかりません: {rec_dir}", file=sys.stderr)
        raise SystemExit(1)
    out_dir.mkdir(parents=True, exist_ok=True)
    data = parse_rec_folder(rec_dir)
    export_key_events_csv(data, out_dir / "key_events.csv")
    export_daily_summary_csv(data, out_dir / "daily_summary.csv")
    export_key_stats_csv(data, out_dir / "key_stats.csv")
    print(f"完了: {out_dir}")


if __name__ == "__main__":
    main()
