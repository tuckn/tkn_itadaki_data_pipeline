from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path

from itadaki_pipeline.parser import (
    date_from_filename,
    iter_key_file,
    parse_moc_file,
    parse_mom_file,
    parse_pow_file,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rec", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    key_files = sorted((args.rec / "Key").glob("*.rec"))
    event_count = 0
    counter: Counter[str] = Counter()
    with (args.output / "key_events.csv").open(
        "r", encoding="utf-8-sig", newline=""
    ) as handle:
        reader = csv.reader(handle)
        next(reader)
        for key_file in key_files:
            record_date = date_from_filename(key_file)
            date_text = record_date.isoformat()
            for event in iter_key_file(key_file):
                event_dt = event.datetime_local.replace(tzinfo=None)
                expected = [
                    date_text,
                    event_dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                    str(event.code),
                    event.key_name,
                    "1" if event.is_mouse else "0",
                    str(event_dt.year),
                    str(event_dt.month),
                    str(event_dt.day),
                    str(event_dt.hour),
                    str(event_dt.minute),
                    str(event_dt.second),
                    str(event_dt.weekday()),
                ]
                actual = next(reader, None)
                if actual != expected:
                    raise AssertionError(
                        f"key_events row {event_count + 1} differs: "
                        f"{actual!r} != {expected!r}"
                    )
                counter[event.key_name] += 1
                event_count += 1
        if next(reader, None) is not None:
            raise AssertionError("key_events.csv contains extra rows")

    daily_count = 0
    with (args.output / "daily_summary.csv").open(
        "r", encoding="utf-8-sig", newline=""
    ) as handle:
        reader = csv.DictReader(handle)
        for key_file in key_files:
            record_date = date_from_filename(key_file)
            events = list(iter_key_file(key_file))
            key_count = sum(not event.is_mouse for event in events)
            mouse_clicks = sum(event.is_mouse for event in events)
            date_name = key_file.name
            move_cm = parse_mom_file(args.rec / "MoM" / date_name)
            power_sec = parse_pow_file(args.rec / "Pow" / date_name)
            actual = next(reader, None)
            expected = {
                "date": record_date.isoformat(),
                "year": str(record_date.year),
                "month": str(record_date.month),
                "day": str(record_date.day),
                "weekday": str(record_date.weekday()),
                "key_count": str(key_count),
                "mouse_clicks": str(mouse_clicks),
                "total_events": str(len(events)),
                "moc_clicks": str(parse_moc_file(args.rec / "MoC" / date_name)),
                "mouse_move_cm": str(move_cm),
                "mouse_move_km": f"{move_cm / 100000:.4f}",
                "power_on_sec": str(power_sec),
                "power_on_hms": (
                    f"{power_sec // 3600:02d}:"
                    f"{(power_sec % 3600) // 60:02d}:"
                    f"{power_sec % 60:02d}"
                ),
            }
            if actual != expected:
                raise AssertionError(
                    f"daily_summary row {daily_count + 1} differs: "
                    f"{actual!r} != {expected!r}"
                )
            daily_count += 1
        if next(reader, None) is not None:
            raise AssertionError("daily_summary.csv contains extra rows")

    print(
        f"Verified legacy output: {daily_count} days, "
        f"{event_count} events, {len(counter)} key names"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
