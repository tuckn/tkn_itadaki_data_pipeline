"""Build neutral ISO-week activity marts from processed Itadaki CSV files."""

# HTML and SVG templates intentionally remain readable as complete lines.
# ruff: noqa: E501

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import html
import json
import os
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import PipelineConfig, SourceConfig
from .pipeline import sha256_file

GENERATOR_VERSION = "0.3.0"
MANIFEST_SCHEMA_VERSION = 1
ALL_DEVICES = "all_devices"

WEEKLY_SUMMARY_HEADER = [
    "week_id",
    "week_start_date",
    "week_end_date",
    "scope_type",
    "device_id",
    "observed_date_count",
    "key_count",
    "mouse_clicks",
    "moc_clicks",
    "total_events",
    "mouse_move_cm",
    "power_on_sec",
]
DAILY_ACTIVITY_HEADER = [
    "week_id",
    "date",
    "iso_weekday",
    "scope_type",
    "device_id",
    "has_record",
    "key_count",
    "mouse_clicks",
    "moc_clicks",
    "total_events",
    "mouse_move_cm",
    "power_on_sec",
]
HOURLY_INPUT_HEADER = [
    "week_id",
    "date",
    "iso_weekday",
    "hour",
    "scope_type",
    "device_id",
    "keyboard_count",
    "mouse_click_count",
    "total_events",
]
KEY_FREQUENCY_HEADER = [
    "week_id",
    "scope_type",
    "device_id",
    "key_code",
    "key_name",
    "key_count",
    "share_of_keyboard_events",
    "rank",
]
HISTORY_HEADER = WEEKLY_SUMMARY_HEADER + [
    "key_count_delta",
    "mouse_clicks_delta",
    "moc_clicks_delta",
    "total_events_delta",
    "mouse_move_cm_delta",
    "power_on_sec_delta",
]


@dataclass(frozen=True)
class Week:
    week_id: str
    start: dt.date
    end: dt.date


@dataclass(frozen=True)
class CalendarPeriod:
    period_id: str
    start: dt.date
    end: dt.date
    grain: str


@dataclass(frozen=True)
class SourceFile:
    path: Path
    relative_path: str
    dataset: str
    device_id: str


@dataclass(frozen=True)
class WeekPlan:
    week: Week
    status: str
    reason: str
    files: tuple[SourceFile, ...]
    fingerprints: tuple[dict[str, Any], ...]


def _week_for_date(value: dt.date) -> Week:
    iso = value.isocalendar()
    start = value - dt.timedelta(days=value.isoweekday() - 1)
    return Week(f"{iso.year:04d}-W{iso.week:02d}", start, start + dt.timedelta(days=6))


def _weeks_between(first: dt.date, last: dt.date) -> list[Week]:
    current = _week_for_date(first).start
    final = _week_for_date(last).start
    result: list[Week] = []
    while current <= final:
        result.append(_week_for_date(current))
        current += dt.timedelta(days=7)
    return result


def _active_ingest_sources(config: PipelineConfig) -> list[SourceConfig]:
    return [
        source
        for source in config.sources
        if any(mode in {"ingest", "run"} for mode in source.modes)
    ]


def _latest_complete_manifest(source: SourceConfig) -> dict[str, Any] | None:
    root = source.archive_root / "_manifests"
    candidates: list[dict[str, Any]] = []
    if not root.is_dir():
        return None
    for path in root.rglob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if (
                payload.get("status") == "complete"
                and payload.get("mode") in {"ingest", "run"}
                and payload.get("cutoff_date")
                and payload.get("source_name", source.name) == source.name
                and payload.get("device_id", source.device_id) == source.device_id
            ):
                dt.date.fromisoformat(str(payload["cutoff_date"]))
                candidates.append(payload)
        except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
            continue
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda item: (
            str(item["cutoff_date"]),
            str(item.get("completed_at", "")),
            str(item.get("batch_id", "")),
        ),
    )


def _watermark(config: PipelineConfig) -> tuple[dt.date, list[dict[str, str]]]:
    sources = _active_ingest_sources(config)
    if not sources:
        raise ValueError("No active ingest source is configured")
    details: list[dict[str, str]] = []
    for source in sources:
        manifest = _latest_complete_manifest(source)
        if manifest is None:
            raise ValueError(
                f"No complete ingest manifest for source {source.name!r}; "
                "run 'tkn-itadaki-pipeline ingest --apply' successfully first"
            )
        details.append(
            {
                "source_name": source.name,
                "device_id": source.device_id,
                "cutoff_date": str(manifest["cutoff_date"]),
                "batch_id": str(manifest.get("batch_id", "")),
                "mode": str(manifest.get("mode", "")),
            }
        )
    cutoff = min(dt.date.fromisoformat(item["cutoff_date"]) for item in details)
    return cutoff, details


def _monthly_files(root: Path) -> list[SourceFile]:
    result: list[SourceFile] = []
    if not root.is_dir():
        raise FileNotFoundError(f"Processed data path not found: {root}")
    for device_root in sorted(path for path in root.iterdir() if path.is_dir()):
        for dataset in ("DailyUsage", "InputEvents"):
            dataset_root = device_root / dataset
            if not dataset_root.is_dir():
                continue
            for path in sorted(dataset_root.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9].csv")):
                result.append(
                    SourceFile(
                        path=path,
                        relative_path=path.relative_to(root).as_posix(),
                        dataset=dataset,
                        device_id=device_root.name,
                    )
                )
    return result


def _row_dates(source_file: SourceFile) -> tuple[dt.date, dt.date] | None:
    date_column = "date" if source_file.dataset == "DailyUsage" else "event_date"
    first: dt.date | None = None
    last: dt.date | None = None
    with source_file.path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            value = dt.date.fromisoformat(row[date_column])
            first = value if first is None or value < first else first
            last = value if last is None or value > last else last
    if first is None or last is None:
        return None
    return first, last


def _file_month(source_file: SourceFile) -> tuple[int, int]:
    return int(source_file.path.parent.name), int(source_file.path.stem)


def _intersects_week(source_file: SourceFile, week: Week) -> bool:
    year, month = _file_month(source_file)
    first = dt.date(year, month, 1)
    if month == 12:
        next_month = dt.date(year + 1, 1, 1)
    else:
        next_month = dt.date(year, month + 1, 1)
    return first <= week.end and next_month > week.start


def _fingerprint(source_file: SourceFile, cache: dict[Path, dict[str, Any]]) -> dict[str, Any]:
    cached = cache.get(source_file.path)
    if cached is not None:
        return cached
    stat = source_file.path.stat()
    value = {
        "path": source_file.relative_path,
        "dataset": source_file.dataset,
        "device_id": source_file.device_id,
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": sha256_file(source_file.path),
    }
    cache[source_file.path] = value
    return value


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def _outputs_match(directory: Path, manifest: dict[str, Any]) -> bool:
    outputs = manifest.get("outputs")
    if not isinstance(outputs, dict):
        return False
    expected = {
        "report.html",
        "weekly_summary.csv",
        "daily_activity.csv",
        "hourly_input.csv",
        "key_frequency.csv",
    }
    if set(outputs) != expected:
        return False
    for name, record in outputs.items():
        path = directory / name
        if not path.is_file() or not isinstance(record, dict):
            return False
        if sha256_file(path) != record.get("sha256"):
            return False
    return True


def _plan_week(
    mart_root: Path,
    week: Week,
    files: tuple[SourceFile, ...],
    cache: dict[Path, dict[str, Any]],
) -> WeekPlan:
    fingerprints = tuple(_fingerprint(item, cache) for item in files)
    directory = mart_root / "weeks" / week.week_id
    manifest = _read_json(directory / "manifest.json")
    if manifest is None:
        return WeekPlan(week, "missing", "missing manifest", files, fingerprints)
    if manifest.get("generator_version") != GENERATOR_VERSION:
        return WeekPlan(week, "stale", "generator version changed", files, fingerprints)
    if manifest.get("input_fingerprints") != list(fingerprints):
        return WeekPlan(week, "stale", "input fingerprint changed", files, fingerprints)
    if not _outputs_match(directory, manifest):
        return WeekPlan(week, "stale", "output missing or hash mismatch", files, fingerprints)
    return WeekPlan(week, "unchanged", "manifest and outputs match", files, fingerprints)


def _scope(scope_type: str, device_id: str) -> tuple[str, str]:
    return scope_type, device_id


def _number(value: str, *, integer: bool = True) -> int | float:
    return int(value) if integer else float(value)


def _aggregate(plan: WeekPlan, device_bounds: dict[str, tuple[dt.date, dt.date]]) -> dict[str, Any]:
    week = plan.week
    daily: dict[tuple[str, dt.date], dict[str, int | float]] = {}
    hourly: Counter[tuple[str, dt.date, int, str]] = Counter()
    keys: Counter[tuple[str, str, str]] = Counter()
    quality = {
        "input_event_rows": 0,
        "daily_usage_rows": 0,
        "event_datetime_date_mismatch_count": 0,
        "hourly_excluded_event_count": 0,
        "key_moc_mismatch_date_count": 0,
    }
    for source_file in plan.files:
        with source_file.path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if source_file.dataset == "DailyUsage":
                for row in reader:
                    date = dt.date.fromisoformat(row["date"])
                    if not week.start <= date <= week.end:
                        continue
                    quality["daily_usage_rows"] += 1
                    daily[(source_file.device_id, date)] = {
                        "key_count": _number(row["key_count"]),
                        "mouse_clicks": _number(row["mouse_clicks"]),
                        "moc_clicks": _number(row["moc_clicks"]),
                        "total_events": _number(row["total_events"]),
                        "mouse_move_cm": _number(row["mouse_move_cm"], integer=False),
                        "power_on_sec": _number(row["power_on_sec"]),
                    }
                    if row["mouse_clicks"] != row["moc_clicks"]:
                        quality["key_moc_mismatch_date_count"] += 1
            else:
                for row in reader:
                    date = dt.date.fromisoformat(row["event_date"])
                    if not week.start <= date <= week.end:
                        continue
                    quality["input_event_rows"] += 1
                    is_mouse = row["is_mouse"] == "1" or row["event_type"] == "mouse"
                    if not is_mouse:
                        keys[(source_file.device_id, row["key_code"], row["key_name"])] += 1
                    event_datetime = dt.datetime.fromisoformat(row["event_datetime_local"])
                    if event_datetime.date() != date:
                        quality["event_datetime_date_mismatch_count"] += 1
                        quality["hourly_excluded_event_count"] += 1
                        continue
                    kind = "mouse" if is_mouse else "keyboard"
                    hourly[(source_file.device_id, date, event_datetime.hour, kind)] += 1

    devices = sorted(device_bounds)
    scopes = [_scope("all_devices", ALL_DEVICES)] + [
        _scope("device", device) for device in devices
    ]

    summary_rows: list[list[Any]] = []
    daily_rows: list[list[Any]] = []
    hourly_rows: list[list[Any]] = []
    key_rows: list[list[Any]] = []
    dates = [week.start + dt.timedelta(days=offset) for offset in range(7)]
    metric_names = [
        "key_count",
        "mouse_clicks",
        "moc_clicks",
        "total_events",
        "mouse_move_cm",
        "power_on_sec",
    ]
    for scope_type, scope_device in scopes:
        scope_devices = devices if scope_type == "all_devices" else [scope_device]
        totals: dict[str, int | float] = {name: 0 for name in metric_names}
        observed_dates = 0
        for date in dates:
            matching = [daily[(device, date)] for device in scope_devices if (device, date) in daily]
            has_record = bool(matching)
            if has_record:
                observed_dates += 1
            values = {name: sum(record[name] for record in matching) for name in metric_names}
            for name in metric_names:
                totals[name] += values[name]
            daily_rows.append(
                [
                    week.week_id,
                    date.isoformat(),
                    date.isoweekday(),
                    scope_type,
                    scope_device,
                    1 if has_record else 0,
                    values["key_count"],
                    values["mouse_clicks"],
                    values["moc_clicks"],
                    values["total_events"],
                    _format_float(values["mouse_move_cm"]),
                    values["power_on_sec"],
                ]
            )
        summary_rows.append(
            [
                week.week_id,
                week.start.isoformat(),
                week.end.isoformat(),
                scope_type,
                scope_device,
                observed_dates,
                totals["key_count"],
                totals["mouse_clicks"],
                totals["moc_clicks"],
                totals["total_events"],
                _format_float(totals["mouse_move_cm"]),
                totals["power_on_sec"],
            ]
        )
        for date in dates:
            for hour in range(24):
                keyboard = sum(hourly[(device, date, hour, "keyboard")] for device in scope_devices)
                mouse = sum(hourly[(device, date, hour, "mouse")] for device in scope_devices)
                hourly_rows.append(
                    [
                        week.week_id,
                        date.isoformat(),
                        date.isoweekday(),
                        hour,
                        scope_type,
                        scope_device,
                        keyboard,
                        mouse,
                        keyboard + mouse,
                    ]
                )
        scope_key_counts: Counter[tuple[str, str]] = Counter()
        for (device, code, name), count in keys.items():
            if device in scope_devices:
                scope_key_counts[(code, name)] += count
        keyboard_total = sum(scope_key_counts.values())
        ranked = sorted(scope_key_counts.items(), key=lambda item: (-item[1], item[0]))
        for rank, ((code, name), count) in enumerate(ranked, start=1):
            key_rows.append(
                [
                    week.week_id,
                    scope_type,
                    scope_device,
                    code,
                    name,
                    count,
                    _format_float(count / keyboard_total if keyboard_total else 0, 8),
                    rank,
                ]
            )
    return {
        "summary": summary_rows,
        "daily": daily_rows,
        "hourly": hourly_rows,
        "keys": key_rows,
        "quality": quality,
    }


def _format_float(value: int | float, digits: int = 3) -> str:
    return f"{float(value):.{digits}f}".rstrip("0").rstrip(".") or "0"


def _temporary(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    return path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"


def _atomic_bytes(path: Path, data: bytes) -> bool:
    temporary = _temporary(path)
    try:
        temporary.write_bytes(data)
        if path.is_file() and sha256_file(path) == sha256_file(temporary):
            temporary.unlink()
            return False
        os.replace(temporary, path)
        return True
    finally:
        temporary.unlink(missing_ok=True)


def _csv_bytes(header: list[str], rows: list[list[Any]]) -> bytes:
    import io

    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return ("\ufeff" + output.getvalue()).encode("utf-8")


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _table(headers: list[str], rows: list[list[Any]], *, css_class: str = "") -> str:
    head = "".join(f"<th scope=\"col\">{html.escape(str(value))}</th>" for value in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{html.escape(str(value))}</td>" for value in row) + "</tr>"
        for row in rows
    )
    return f'<div class="table-wrap"><table class="{css_class}"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _details_table(
    summary: str,
    headers: list[str],
    rows: list[list[Any]],
    *,
    css_class: str = "",
) -> str:
    return (
        '<details class="data-table">'
        f"<summary>{html.escape(summary)}（{len(rows)}行）</summary>"
        f"{_table(headers, rows, css_class=css_class)}"
        "</details>"
    )


def _tick_positions(length: int, maximum: int = 10) -> set[int]:
    if length <= maximum:
        return set(range(length))
    step = max(1, (length - 1) // (maximum - 1))
    return {*range(0, length, step), length - 1}


def _bar_chart(
    labels: list[str],
    series: list[tuple[str, list[float]]],
    *,
    x_label: str,
    y_label: str,
) -> str:
    maximum = max((value for _, values in series for value in values), default=0) or 1
    colors = ["#356a9a", "#8ba6bf"]
    width, height = 760, 280
    left, right, top, bottom = 72, 18, 34, 62
    plot_width = width - left - right
    plot_height = height - top - bottom
    group = plot_width / max(len(labels), 1)
    marks: list[str] = []
    for index, (name, values) in enumerate(series):
        bar_width = group * 0.72 / max(len(series), 1)
        for position, value in enumerate(values):
            bar_height = value / maximum * plot_height
            x = left + position * group + group * 0.14 + index * bar_width
            y = top + plot_height - bar_height
            marks.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_width:.1f}" '
                f'height="{bar_height:.1f}" fill="{colors[index % len(colors)]}">'
                f'<title>{html.escape(name)} / {html.escape(labels[position])}: {value:g}</title></rect>'
            )
    guides: list[str] = []
    for index in range(5):
        value = maximum * index / 4
        y = top + plot_height - plot_height * index / 4
        guides.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{width-right}" y2="{y:.1f}" class="grid"/>'
            f'<text x="{left-8}" y="{y+4:.1f}" text-anchor="end">{value:g}</text>'
        )
    x_ticks = "".join(
        f'<text x="{left + position * group + group / 2:.1f}" y="{height-bottom+20}" '
        f'text-anchor="middle">{html.escape(label)}</text>'
        for position, label in enumerate(labels)
        if position in _tick_positions(len(labels))
    )
    legend = "".join(
        f'<rect x="{left + index * 130}" y="8" width="12" height="12" fill="{colors[index]}"/>'
        f'<text x="{left + index * 130 + 18}" y="18">{html.escape(name)}</text>'
        for index, (name, _) in enumerate(series)
    )
    return (
        f'<svg role="img" viewBox="0 0 {width} {height}" aria-label="{html.escape(y_label)}の棒グラフ">'
        f'{legend}{"".join(guides)}<line x1="{left}" y1="{top}" x2="{left}" y2="{top+plot_height}" class="axis"/>'
        f'<line x1="{left}" y1="{top+plot_height}" x2="{width-right}" y2="{top+plot_height}" class="axis"/>'
        f'{"".join(marks)}{x_ticks}'
        f'<text class="axis-title" x="{left+plot_width/2:.1f}" y="{height-8}" text-anchor="middle">{html.escape(x_label)}</text>'
        f'<text class="axis-title" transform="translate(17 {top+plot_height/2:.1f}) rotate(-90)" text-anchor="middle">{html.escape(y_label)}</text>'
        "</svg>"
    )


def _horizontal_bar_chart(
    labels: list[str],
    values: list[float],
    *,
    x_label: str,
    y_label: str,
) -> str:
    maximum = max(values, default=0) or 1
    width = 760
    left, right, top, bottom = 150, 30, 20, 48
    row_height = 24
    plot_height = max(len(labels), 1) * row_height
    height = top + plot_height + bottom
    plot_width = width - left - right
    bars: list[str] = []
    for index, (label, value) in enumerate(zip(labels, values, strict=True)):
        y = top + index * row_height + 3
        bar_width = value / maximum * plot_width
        bars.append(
            f'<text x="{left-8}" y="{y+13}" text-anchor="end">{html.escape(label)}</text>'
            f'<rect x="{left}" y="{y}" width="{bar_width:.1f}" height="17" fill="#356a9a">'
            f'<title>{html.escape(label)}: {value:g}</title></rect>'
        )
    return (
        f'<svg role="img" viewBox="0 0 {width} {height}" aria-label="{html.escape(x_label)}の横棒グラフ">'
        f'<line x1="{left}" y1="{top+plot_height}" x2="{width-right}" y2="{top+plot_height}" class="axis"/>'
        f'{"".join(bars)}'
        f'<text class="axis-title" x="{left+plot_width/2:.1f}" y="{height-8}" text-anchor="middle">{html.escape(x_label)}</text>'
        f'<text class="axis-title" transform="translate(18 {top+plot_height/2:.1f}) rotate(-90)" text-anchor="middle">{html.escape(y_label)}</text>'
        "</svg>"
    )


def _line_chart(
    labels: list[str],
    series: list[tuple[str, list[float]]],
    *,
    x_label: str,
    y_label: str,
) -> str:
    maximum = max((value for _, values in series for value in values), default=0) or 1
    colors = ["#356a9a", "#8ba6bf"]
    width, height = 760, 300
    left, right, top, bottom = 72, 18, 34, 64
    plot_width = width - left - right
    plot_height = height - top - bottom
    step = plot_width / max(len(labels) - 1, 1)
    lines: list[str] = []
    for index, (name, values) in enumerate(series):
        points = " ".join(
            f"{left + position * step:.1f},{top + plot_height - value / maximum * plot_height:.1f}"
            for position, value in enumerate(values)
        )
        lines.append(
            f'<polyline points="{points}" fill="none" stroke="{colors[index]}" '
            f'stroke-width="2"><title>{html.escape(name)}</title></polyline>'
        )
    guides: list[str] = []
    for index in range(5):
        value = maximum * index / 4
        y = top + plot_height - plot_height * index / 4
        guides.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{width-right}" y2="{y:.1f}" class="grid"/>'
            f'<text x="{left-8}" y="{y+4:.1f}" text-anchor="end">{value:g}</text>'
        )
    x_ticks = "".join(
        f'<text x="{left + position * step:.1f}" y="{height-bottom+20}" text-anchor="middle">{html.escape(label)}</text>'
        for position, label in enumerate(labels)
        if position in _tick_positions(len(labels))
    )
    legend = "".join(
        f'<line x1="{left + index * 130}" y1="14" x2="{left + index * 130 + 14}" y2="14" stroke="{colors[index]}" stroke-width="3"/>'
        f'<text x="{left + index * 130 + 20}" y="18">{html.escape(name)}</text>'
        for index, (name, _) in enumerate(series)
    )
    return (
        f'<svg role="img" viewBox="0 0 {width} {height}" aria-label="{html.escape(y_label)}の時系列">'
        f'{legend}{"".join(guides)}<line x1="{left}" y1="{top}" x2="{left}" y2="{top+plot_height}" class="axis"/>'
        f'<line x1="{left}" y1="{top+plot_height}" x2="{width-right}" y2="{top+plot_height}" class="axis"/>'
        f'{"".join(lines)}{x_ticks}'
        f'<text class="axis-title" x="{left+plot_width/2:.1f}" y="{height-8}" text-anchor="middle">{html.escape(x_label)}</text>'
        f'<text class="axis-title" transform="translate(17 {top+plot_height/2:.1f}) rotate(-90)" text-anchor="middle">{html.escape(y_label)}</text>'
        "</svg>"
    )


def _heatmap(
    matrix: list[list[int]],
    row_labels: list[str],
    *,
    label: str,
    x_label: str,
    y_label: str,
) -> str:
    values = [value for row in matrix for value in row]
    maximum = max(values, default=0) or 1
    width = 760
    left, right, top, bottom = 105, 25, 28, 58
    cell_width = (width - left - right) / 24
    cell_height = 25 if len(matrix) <= 12 else 18
    plot_height = max(len(matrix), 1) * cell_height
    height = top + plot_height + bottom
    cells: list[str] = []
    for row_index, row in enumerate(matrix):
        for hour, value in enumerate(row):
            opacity = 0.08 + 0.82 * value / maximum
            cells.append(
                f'<rect x="{left + hour * cell_width:.1f}" y="{top + row_index * cell_height:.1f}" '
                f'width="{cell_width-1:.1f}" height="{cell_height-1:.1f}" fill="#356a9a" '
                f'fill-opacity="{opacity:.3f}"><title>{html.escape(row_labels[row_index])} '
                f'{hour:02d}:00: {value}</title></rect>'
            )
    row_ticks = "".join(
        f'<text x="{left-7}" y="{top + index * cell_height + cell_height * 0.68:.1f}" '
        f'text-anchor="end">{html.escape(row_label)}</text>'
        for index, row_label in enumerate(row_labels)
        if index in _tick_positions(len(row_labels), maximum=12)
    )
    hour_ticks = "".join(
        f'<text x="{left + hour * cell_width + cell_width / 2:.1f}" y="{top+plot_height+18:.1f}" '
        f'text-anchor="middle">{hour:02d}</text>'
        for hour in range(0, 24, 3)
    )
    return (
        f'<svg role="img" viewBox="0 0 {width} {height}" aria-label="{html.escape(label)}">'
        f'{"".join(cells)}{row_ticks}{hour_ticks}'
        f'<text x="{width-right}" y="17" text-anchor="end">濃淡: 0 ～ {maximum}</text>'
        f'<text class="axis-title" x="{left+(width-left-right)/2:.1f}" y="{height-8}" text-anchor="middle">{html.escape(x_label)}</text>'
        f'<text class="axis-title" transform="translate(18 {top+plot_height/2:.1f}) rotate(-90)" text-anchor="middle">{html.escape(y_label)}</text>'
        "</svg>"
    )


def _styles() -> str:
    return """
:root{color-scheme:light;--ink:#243442;--muted:#607384;--line:#cbd6df;--blue:#356a9a;--pale:#edf3f7}
*{box-sizing:border-box}body{margin:0;color:var(--ink);font:15px/1.55 system-ui,sans-serif;background:#f7f9fb}
main{max-width:1120px;margin:auto;padding:24px}h1,h2{line-height:1.25}h2{margin-top:36px;border-bottom:1px solid var(--line);padding-bottom:6px}
.meta,.note{color:var(--muted)}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}.card{background:white;border:1px solid var(--line);padding:12px;border-radius:8px}.value{font-size:1.35rem;font-variant-numeric:tabular-nums}
.chart{background:white;border:1px solid var(--line);padding:12px;margin:12px 0;border-radius:8px}svg{display:block;width:100%;height:auto}svg text{font-size:11px;fill:var(--muted)}svg .axis{stroke:#607384;stroke-width:1}svg .grid{stroke:#dce4ea;stroke-width:1}svg .axis-title{font-size:13px;font-weight:600;fill:var(--ink)}
.table-wrap{overflow:auto;margin:10px 0}table{width:100%;border-collapse:collapse;background:white;font-variant-numeric:tabular-nums}th,td{padding:7px 9px;border:1px solid var(--line);text-align:right;white-space:nowrap}th:first-child,td:first-child{text-align:left}th{background:var(--pale)}a{color:var(--blue)}
details.data-table{margin:12px 0}details.data-table summary{cursor:pointer;color:var(--blue);font-weight:600;padding:8px 0}details.data-table[open] summary{margin-bottom:4px}
@media(max-width:640px){main{padding:14px}.cards{grid-template-columns:repeat(2,1fr)}th,td{padding:6px}}
"""


def _hourly_matrix(rows: list[list[Any]], value_index: int) -> list[list[int]]:
    return [
        [int(row[value_index]) for row in rows[offset : offset + 24]]
        for offset in range(0, len(rows), 24)
    ]


def _weekly_html(week: Week, data: dict[str, Any]) -> str:
    summary = data["summary"]
    all_summary = summary[0]
    daily = [row for row in data["daily"] if row[3] == "all_devices"]
    hourly = [row for row in data["hourly"] if row[4] == "all_devices"]
    keys = [row for row in data["keys"] if row[1] == "all_devices"]
    labels = [row[1][5:] for row in daily]
    cards = "".join(
        f'<div class="card"><div>{label}</div><div class="value">{html.escape(str(value))}</div></div>'
        for label, value in zip(
            ["観測日数", "keyboard", "click", "MoC", "総event", "移動cm", "電源オン秒"],
            [all_summary[5], *all_summary[6:12]],
            strict=True,
        )
    )
    key_top = keys[:20]
    daily_table = [[row[1], row[2], row[5], *row[6:12]] for row in daily]
    hourly_table = [[row[1], row[3], row[6], row[7], row[8]] for row in hourly]
    key_table = [[row[7], row[3], row[4], row[5], row[6]] for row in key_top]
    device_table = [row[4:] for row in summary if row[3] == "device"]
    quality_rows = [[name, value] for name, value in data["quality"].items()]
    heatmap_labels = [
        f'{row[1][5:]} {("月", "火", "水", "木", "金", "土", "日")[int(row[2]) - 1]}'
        for row in daily
    ]
    no_rows = '<p class="note">observed_date_count: 0（source rowなし）</p>' if all_summary[5] == 0 else ""
    return f"""<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{week.week_id} Itadaki週次活動</title><style>{_styles()}</style></head><body><main>
<h1>{week.week_id} Itadaki週次活動</h1>
<p><a href="../../index.html">活動mart索引へ戻る</a></p>
<section><h2>期間とデータ範囲</h2><p>{week.start.isoformat()} ～ {week.end.isoformat()}（ISO週、月曜～日曜）</p>{no_rows}</section>
<section><h2>週次集計</h2><div class="cards">{cards}</div></section>
<section><h2>曜日別</h2><div class="chart"><h3>keyboard / click</h3>{_bar_chart(labels, [("keyboard", [float(row[6]) for row in daily]), ("click", [float(row[7]) for row in daily])], x_label="日付", y_label="event数")}</div><div class="chart"><h3>mouse移動</h3>{_bar_chart(labels, [("mouse_move_cm", [float(row[10]) for row in daily])], x_label="日付", y_label="移動cm")}</div><div class="chart"><h3>電源オン</h3>{_bar_chart(labels, [("power_on_sec", [float(row[11]) for row in daily])], x_label="日付", y_label="秒")}</div>{_table(["日付","ISO曜日","record有無","keyboard","click","MoC","総event","移動cm","電源オン秒"], daily_table)}</section>
<section><h2>時刻別</h2><div class="chart"><h3>keyboard</h3>{_heatmap(_hourly_matrix(hourly, 6), heatmap_labels, label="keyboard hour-of-week heatmap", x_label="時刻", y_label="曜日")}</div><div class="chart"><h3>click</h3>{_heatmap(_hourly_matrix(hourly, 7), heatmap_labels, label="click hour-of-week heatmap", x_label="時刻", y_label="曜日")}</div>{_details_table("日付×時刻の数値表を表示", ["日付","時","keyboard","click","総event"], hourly_table)}</section>
<section><h2>論理キー頻度</h2><div class="chart">{_horizontal_bar_chart([str(row[4]) for row in key_top], [float(row[5]) for row in key_top], x_label="件数", y_label="論理キー")}</div>{_table(["順位","code","name","件数","keyboard内構成比"], key_table)}</section>
<section><h2>端末別</h2>{_table(["device_id","観測日数","keyboard","click","MoC","総event","移動cm","電源オン秒"], device_table)}</section>
<section><h2>データ品質</h2>{_table(["項目","件数"], quality_rows)}</section>
</main></body></html>"""


def _write_week(plan: WeekPlan, data: dict[str, Any], config: PipelineConfig, watermark: dt.date, watermark_sources: list[dict[str, str]]) -> dict[str, Any]:
    assert config.weekly_mart_root is not None
    directory = config.weekly_mart_root / "weeks" / plan.week.week_id
    payloads = {
        "weekly_summary.csv": _csv_bytes(WEEKLY_SUMMARY_HEADER, data["summary"]),
        "daily_activity.csv": _csv_bytes(DAILY_ACTIVITY_HEADER, data["daily"]),
        "hourly_input.csv": _csv_bytes(HOURLY_INPUT_HEADER, data["hourly"]),
        "key_frequency.csv": _csv_bytes(KEY_FREQUENCY_HEADER, data["keys"]),
        "report.html": _weekly_html(plan.week, data).encode("utf-8"),
    }
    outputs: dict[str, dict[str, Any]] = {}
    for name, content in payloads.items():
        path = directory / name
        _atomic_bytes(path, content)
        outputs[name] = {
            "size": path.stat().st_size,
            "sha256": sha256_file(path),
            "row_count": (
                len(data["summary"]) if name == "weekly_summary.csv" else
                len(data["daily"]) if name == "daily_activity.csv" else
                len(data["hourly"]) if name == "hourly_input.csv" else
                len(data["keys"]) if name == "key_frequency.csv" else None
            ),
        }
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "generator": "tkn-itadaki-pipeline",
        "generator_version": GENERATOR_VERSION,
        "week_id": plan.week.week_id,
        "week_start_date": plan.week.start.isoformat(),
        "week_end_date": plan.week.end.isoformat(),
        "timezone": config.timezone_name,
        "watermark_cutoff_date": watermark.isoformat(),
        "watermark_sources": watermark_sources,
        "input_fingerprints": list(plan.fingerprints),
        "row_counts": {
            "weekly_summary": len(data["summary"]),
            "daily_activity": len(data["daily"]),
            "hourly_input": len(data["hourly"]),
            "key_frequency": len(data["keys"]),
        },
        "quality": data["quality"],
        "outputs": outputs,
    }
    _atomic_bytes(directory / "manifest.json", _json_bytes(manifest))
    return manifest


def _read_summary(path: Path) -> list[str] | None:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.reader(handle):
                if row and row[0] == "week_id":
                    continue
                if row and row[3] == "all_devices":
                    return row
    except (OSError, csv.Error):
        return None
    return None


def _history_rows(mart_root: Path, weeks: list[Week]) -> list[list[Any]]:
    rows: list[list[Any]] = []
    previous: list[str] | None = None
    numeric_indexes = [6, 7, 8, 9, 10, 11]
    for week in weeks:
        row = _read_summary(mart_root / "weeks" / week.week_id / "weekly_summary.csv")
        if row is None:
            continue
        deltas: list[str] = []
        for index in numeric_indexes:
            if previous is None:
                deltas.append("")
            else:
                value = float(row[index]) - float(previous[index])
                deltas.append(_format_float(value))
        rows.append([*row, *deltas])
        previous = row
    return rows


def _calendar_periods(first: dt.date, last: dt.date, grain: str) -> list[CalendarPeriod]:
    periods: list[CalendarPeriod] = []
    if first > last:
        return periods
    if grain == "month":
        current = first.replace(day=1)
        while current <= last:
            next_start = (
                dt.date(current.year + 1, 1, 1)
                if current.month == 12
                else dt.date(current.year, current.month + 1, 1)
            )
            periods.append(
                CalendarPeriod(
                    current.strftime("%Y-%m"),
                    max(first, current),
                    min(last, next_start - dt.timedelta(days=1)),
                    grain,
                )
            )
            current = next_start
    elif grain == "year":
        for year in range(first.year, last.year + 1):
            periods.append(
                CalendarPeriod(
                    str(year),
                    max(first, dt.date(year, 1, 1)),
                    min(last, dt.date(year, 12, 31)),
                    grain,
                )
            )
    else:
        raise ValueError(f"Unsupported calendar grain: {grain}")
    return periods


def _read_materialized_details(
    mart_root: Path,
    weeks: list[Week],
) -> tuple[list[list[str]], list[list[str]]]:
    daily_rows: list[list[str]] = []
    hourly_rows: list[list[str]] = []
    for week in weeks:
        directory = mart_root / "weeks" / week.week_id
        with (directory / "daily_activity.csv").open(
            "r", encoding="utf-8-sig", newline=""
        ) as handle:
            daily_rows.extend(
                row
                for row in csv.reader(handle)
                if row and row[0] != "week_id" and row[3] == "all_devices"
            )
        with (directory / "hourly_input.csv").open(
            "r", encoding="utf-8-sig", newline=""
        ) as handle:
            hourly_rows.extend(
                row
                for row in csv.reader(handle)
                if row and row[0] != "week_id" and row[4] == "all_devices"
            )
    daily_rows.sort(key=lambda row: row[1])
    hourly_rows.sort(key=lambda row: (row[1], int(row[3])))
    return daily_rows, hourly_rows


def _period_rows(
    period: CalendarPeriod,
    daily_rows: list[list[str]],
    hourly_rows: list[list[str]],
) -> tuple[list[list[str]], list[list[str]]]:
    daily = [
        row
        for row in daily_rows
        if period.start <= dt.date.fromisoformat(row[1]) <= period.end
    ]
    hourly = [
        row
        for row in hourly_rows
        if period.start <= dt.date.fromisoformat(row[1]) <= period.end
    ]
    return daily, hourly


def _calendar_key_counts(
    source_files: list[SourceFile],
    first: dt.date,
    last: dt.date,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Counter[tuple[str, str]]]:
    result: dict[str, Counter[tuple[str, str]]] = {}
    input_files = [item for item in source_files if item.dataset == "InputEvents"]
    for index, source_file in enumerate(input_files, start=1):
        with source_file.path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                date = dt.date.fromisoformat(row["event_date"])
                if not first <= date <= last:
                    continue
                is_mouse = row["is_mouse"] == "1" or row["event_type"] == "mouse"
                if is_mouse:
                    continue
                key = (row["key_code"], row["key_name"])
                for period_id in (date.strftime("%Y-%m"), str(date.year)):
                    result.setdefault(period_id, Counter())[key] += 1
        if progress and (
            index == 1 or index == len(input_files) or index % 10 == 0
        ):
            progress(f"Logical key input files {index}/{len(input_files)}")
    return result


def _period_key_rows(
    counts: Counter[tuple[str, str]],
) -> list[list[Any]]:
    keyboard_total = sum(counts.values())
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [
        [
            rank,
            code,
            name,
            count,
            _format_float(count / keyboard_total if keyboard_total else 0, 8),
        ]
        for rank, ((code, name), count) in enumerate(ranked, start=1)
    ]


def _period_key_section(counts: Counter[tuple[str, str]]) -> str:
    rows = _period_key_rows(counts)
    top = rows[:20]
    return (
        '<section><h2>論理キー頻度</h2>'
        '<p class="note">mouseを除く、AutoHotkey変換後の論理キーをevent_dateで期間集計しています。</p>'
        '<div class="chart"><h3>上位20キー</h3>'
        f'{_horizontal_bar_chart([str(row[2]) for row in top], [float(row[3]) for row in top], x_label="件数", y_label="論理キー")}'
        "</div>"
        f'{_details_table("論理キー頻度の数値表を表示", ["順位","code","name","件数","keyboard内構成比"], rows)}'
        "</section>"
    )


def _period_cards(daily: list[list[str]]) -> str:
    values: list[int | float] = [
        sum(int(row[5]) for row in daily),
        sum(int(row[6]) for row in daily),
        sum(int(row[7]) for row in daily),
        sum(int(row[8]) for row in daily),
        sum(int(row[9]) for row in daily),
        sum(float(row[10]) for row in daily),
        sum(int(row[11]) for row in daily),
    ]
    return "".join(
        f'<div class="card"><div>{label}</div><div class="value">{html.escape(_format_float(value))}</div></div>'
        for label, value in zip(
            ["観測日数", "keyboard", "click", "MoC", "総event", "移動cm", "電源オン秒"],
            values,
            strict=True,
        )
    )


def _period_hourly_matrix(
    period: CalendarPeriod,
    hourly: list[list[str]],
    value_index: int,
) -> tuple[list[list[int]], list[str]]:
    by_hour = {
        (dt.date.fromisoformat(row[1]), int(row[3])): int(row[value_index])
        for row in hourly
    }
    if period.grain == "month":
        dates = [
            period.start + dt.timedelta(days=offset)
            for offset in range((period.end - period.start).days + 1)
        ]
        return (
            [[by_hour.get((date, hour), 0) for hour in range(24)] for date in dates],
            [date.strftime("%m-%d") for date in dates],
        )
    matrix: list[list[int]] = []
    labels: list[str] = []
    for month in range(period.start.month, period.end.month + 1):
        dates = {
            date
            for date, _ in by_hour
            if date.year == int(period.period_id) and date.month == month
        }
        matrix.append(
            [sum(by_hour.get((date, hour), 0) for date in dates) for hour in range(24)]
        )
        labels.append(f"{month:02d}月")
    return matrix, labels


def _weekday_hourly_matrix(
    hourly: list[list[str]],
    value_index: int,
) -> tuple[list[list[int]], list[str]]:
    by_weekday_hour: Counter[tuple[int, int]] = Counter()
    for row in hourly:
        by_weekday_hour[(int(row[2]), int(row[3]))] += int(row[value_index])
    labels = ["月曜日", "火曜日", "水曜日", "木曜日", "金曜日", "土曜日", "日曜日"]
    return (
        [
            [by_weekday_hour[(iso_weekday, hour)] for hour in range(24)]
            for iso_weekday in range(1, 8)
        ],
        labels,
    )


def _monthly_report_html(
    period: CalendarPeriod,
    daily: list[list[str]],
    hourly: list[list[str]],
    key_counts: Counter[tuple[str, str]],
) -> str:
    labels = [row[1][5:] for row in daily]
    daily_table = [[row[1], row[2], row[5], *row[6:12]] for row in daily]
    hourly_table = [[row[1], row[3], row[6], row[7], row[8]] for row in hourly]
    keyboard_matrix, heatmap_labels = _period_hourly_matrix(period, hourly, 6)
    click_matrix, _ = _period_hourly_matrix(period, hourly, 7)
    weekday_keyboard_matrix, weekday_labels = _weekday_hourly_matrix(hourly, 6)
    weekday_click_matrix, _ = _weekday_hourly_matrix(hourly, 7)
    weekday_hourly_table = [
        [
            weekday_label,
            hour,
            weekday_keyboard_matrix[index][hour],
            weekday_click_matrix[index][hour],
        ]
        for index, weekday_label in enumerate(weekday_labels)
        for hour in range(24)
    ]
    return f"""<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{period.period_id} Itadaki月次活動</title><style>{_styles()}</style></head><body><main>
<h1>{period.period_id} Itadaki月次活動</h1><p><a href="../../index.html">活動mart索引へ戻る</a></p>
<section><h2>期間とデータ範囲</h2><p>{period.start.isoformat()} ～ {period.end.isoformat()}（カレンダー月）</p></section>
<section><h2>月次集計</h2><div class="cards">{_period_cards(daily)}</div></section>
<section><h2>日別</h2><div class="chart"><h3>keyboard / click</h3>{_bar_chart(labels, [("keyboard", [float(row[6]) for row in daily]), ("click", [float(row[7]) for row in daily])], x_label="日付", y_label="event数")}</div><div class="chart"><h3>mouse移動</h3>{_bar_chart(labels, [("mouse_move_cm", [float(row[10]) for row in daily])], x_label="日付", y_label="移動cm")}</div><div class="chart"><h3>電源オン</h3>{_bar_chart(labels, [("power_on_sec", [float(row[11]) for row in daily])], x_label="日付", y_label="秒")}</div>{_table(["日付","ISO曜日","record有無","keyboard","click","MoC","総event","移動cm","電源オン秒"], daily_table)}</section>
<section><h2>日付×時刻</h2><div class="chart"><h3>keyboard</h3>{_heatmap(keyboard_matrix, heatmap_labels, label="keyboard hour-of-month heatmap", x_label="時刻", y_label="日付")}</div><div class="chart"><h3>click</h3>{_heatmap(click_matrix, heatmap_labels, label="click hour-of-month heatmap", x_label="時刻", y_label="日付")}</div>{_details_table("日付×時刻の数値表を表示", ["日付","時","keyboard","click","総event"], hourly_table)}</section>
<section><h2>曜日×時刻</h2><p class="note">月内の同じISO曜日・同じ時刻に属するevent数の合計です。曜日ごとの該当日数は月によって異なります。</p><div class="chart"><h3>keyboard</h3>{_heatmap(weekday_keyboard_matrix, weekday_labels, label="keyboard weekday-by-hour heatmap", x_label="時刻", y_label="曜日")}</div><div class="chart"><h3>click</h3>{_heatmap(weekday_click_matrix, weekday_labels, label="click weekday-by-hour heatmap", x_label="時刻", y_label="曜日")}</div>{_details_table("曜日×時刻の数値表を表示", ["ISO曜日","時","keyboard","click"], weekday_hourly_table)}</section>
{_period_key_section(key_counts)}
</main></body></html>"""


def _yearly_month_rows(period: CalendarPeriod, daily: list[list[str]]) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for month in range(period.start.month, period.end.month + 1):
        matching = [
            row
            for row in daily
            if dt.date.fromisoformat(row[1]).month == month
        ]
        rows.append(
            [
                f"{month:02d}月",
                sum(int(row[5]) for row in matching),
                sum(int(row[6]) for row in matching),
                sum(int(row[7]) for row in matching),
                sum(int(row[8]) for row in matching),
                sum(int(row[9]) for row in matching),
                _format_float(sum(float(row[10]) for row in matching)),
                sum(int(row[11]) for row in matching),
            ]
        )
    return rows


def _yearly_report_html(
    period: CalendarPeriod,
    daily: list[list[str]],
    hourly: list[list[str]],
    key_counts: Counter[tuple[str, str]],
) -> str:
    months = _yearly_month_rows(period, daily)
    labels = [str(row[0]) for row in months]
    keyboard_matrix, heatmap_labels = _period_hourly_matrix(period, hourly, 6)
    click_matrix, _ = _period_hourly_matrix(period, hourly, 7)
    heatmap_table = [
        [month_label, hour, keyboard_matrix[index][hour], click_matrix[index][hour]]
        for index, month_label in enumerate(heatmap_labels)
        for hour in range(24)
    ]
    return f"""<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{period.period_id} Itadaki年次活動</title><style>{_styles()}</style></head><body><main>
<h1>{period.period_id} Itadaki年次活動</h1><p><a href="../../index.html">活動mart索引へ戻る</a></p>
<section><h2>期間とデータ範囲</h2><p>{period.start.isoformat()} ～ {period.end.isoformat()}（カレンダー年）</p></section>
<section><h2>年次集計</h2><div class="cards">{_period_cards(daily)}</div></section>
<section><h2>月別</h2><div class="chart"><h3>keyboard / click</h3>{_bar_chart(labels, [("keyboard", [float(row[2]) for row in months]), ("click", [float(row[3]) for row in months])], x_label="月", y_label="event数")}</div><div class="chart"><h3>mouse移動</h3>{_bar_chart(labels, [("mouse_move_cm", [float(row[6]) for row in months])], x_label="月", y_label="移動cm")}</div><div class="chart"><h3>電源オン</h3>{_bar_chart(labels, [("power_on_sec", [float(row[7]) for row in months])], x_label="月", y_label="秒")}</div>{_table(["月","観測日数","keyboard","click","MoC","総event","移動cm","電源オン秒"], months)}</section>
<section><h2>時刻別</h2><div class="chart"><h3>keyboard</h3>{_heatmap(keyboard_matrix, heatmap_labels, label="keyboard hour-by-month heatmap", x_label="時刻", y_label="月")}</div><div class="chart"><h3>click</h3>{_heatmap(click_matrix, heatmap_labels, label="click hour-by-month heatmap", x_label="時刻", y_label="月")}</div>{_details_table("月×時刻の数値表を表示", ["月","時","keyboard","click"], heatmap_table)}</section>
{_period_key_section(key_counts)}
</main></body></html>"""


def _write_period_reports(
    mart_root: Path,
    weeks: list[Week],
    source_files: list[SourceFile],
    first: dt.date,
    last: dt.date,
    progress: Callable[[str], None] | None,
) -> tuple[list[CalendarPeriod], list[CalendarPeriod], list[dict[str, Any]]]:
    daily_rows, hourly_rows = _read_materialized_details(mart_root, weeks)
    if progress:
        progress("Scanning logical key frequencies for calendar reports")
    key_counts = _calendar_key_counts(source_files, first, last, progress)
    months = _calendar_periods(first, last, "month")
    years = _calendar_periods(first, last, "year")
    outputs: list[dict[str, Any]] = []
    all_periods = [*months, *years]
    for index, period in enumerate(all_periods, start=1):
        daily, hourly = _period_rows(period, daily_rows, hourly_rows)
        content = (
            _monthly_report_html(
                period, daily, hourly, key_counts.get(period.period_id, Counter())
            )
            if period.grain == "month"
            else _yearly_report_html(
                period, daily, hourly, key_counts.get(period.period_id, Counter())
            )
        ).encode("utf-8")
        relative_path = Path(f"{period.grain}s") / period.period_id / "report.html"
        path = mart_root / relative_path
        changed = _atomic_bytes(path, content)
        outputs.append(
            {
                "path": relative_path.as_posix(),
                "period_id": period.period_id,
                "grain": period.grain,
                "size": path.stat().st_size,
                "sha256": sha256_file(path),
                "changed": changed,
            }
        )
        if progress and (index == 1 or index == len(all_periods) or index % 10 == 0):
            progress(
                f"Calendar reports {index}/{len(all_periods)}: "
                f"{period.grain} {period.period_id}"
            )
    return months, years, outputs


def _index_html(
    rows: list[list[Any]],
    months: list[CalendarPeriod],
    years: list[CalendarPeriod],
) -> str:
    links = "".join(
        f'<li><a href="weeks/{html.escape(str(row[0]))}/report.html">{html.escape(str(row[0]))}</a> {html.escape(str(row[1]))} ～ {html.escape(str(row[2]))}</li>'
        for row in reversed(rows)
    )
    month_links = "".join(
        f'<li><a href="months/{period.period_id}/report.html">{period.period_id}</a> '
        f'{period.start.isoformat()} ～ {period.end.isoformat()}</li>'
        for period in reversed(months)
    )
    year_links = "".join(
        f'<li><a href="years/{period.period_id}/report.html">{period.period_id}</a> '
        f'{period.start.isoformat()} ～ {period.end.isoformat()}</li>'
        for period in reversed(years)
    )
    table_rows = [[row[0], row[6], row[7], row[9], row[12], row[13], row[15]] for row in rows]
    labels = [str(row[0]) for row in rows]
    return f"""<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Itadaki活動mart</title><style>{_styles()}</style></head><body><main>
<h1>Itadaki活動mart</h1><p class="meta">週はISO週（月曜～日曜）、月と年はカレンダー期間です。値と直前週との差分を同じ配色で表示します。</p>
<section><h2>年次レポート</h2><ul>{year_links}</ul></section>
<section><h2>月次レポート</h2><ul>{month_links}</ul></section>
<section><h2>週一覧</h2><ul>{links}</ul></section>
<section><h2>週次値の推移</h2><div class="chart">{_line_chart(labels, [("keyboard", [float(row[6]) for row in rows]), ("click", [float(row[7]) for row in rows])], x_label="ISO週", y_label="event数")}</div>{_table(["ISO週","keyboard","click","総event","keyboard差分","click差分","総event差分"], table_rows)}</section>
</main></body></html>"""


def _write_root(
    config: PipelineConfig,
    weeks: list[Week],
    source_files: list[SourceFile],
    watermark: dt.date,
    watermark_sources: list[dict[str, str]],
    first: dt.date,
    last: dt.date,
    progress: Callable[[str], None] | None,
) -> dict[str, Any]:
    assert config.weekly_mart_root is not None
    root = config.weekly_mart_root
    rows = _history_rows(root, weeks)
    months, years, period_outputs = _write_period_reports(
        root, weeks, source_files, first, last, progress
    )
    history = _csv_bytes(HISTORY_HEADER, rows)
    index = _index_html(rows, months, years).encode("utf-8")
    history_changed = _atomic_bytes(root / "weekly_history.csv", history)
    index_changed = _atomic_bytes(root / "index.html", index)
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "generator": "tkn-itadaki-pipeline",
        "generator_version": GENERATOR_VERSION,
        "timezone": config.timezone_name,
        "watermark_cutoff_date": watermark.isoformat(),
        "watermark_sources": watermark_sources,
        "week_count": len(rows),
        "week_range": [rows[0][0], rows[-1][0]] if rows else None,
        "outputs": {
            "index.html": {"size": len(index), "sha256": hashlib.sha256(index).hexdigest()},
            "weekly_history.csv": {"size": len(history), "sha256": hashlib.sha256(history).hexdigest(), "row_count": len(rows)},
        },
        "calendar_reports": [
            {key: value for key, value in item.items() if key != "changed"}
            for item in period_outputs
        ],
    }
    manifest_changed = _atomic_bytes(root / "manifest.json", _json_bytes(manifest))
    return {
        "index_changed": index_changed,
        "history_changed": history_changed,
        "manifest_changed": manifest_changed,
        "week_count": len(rows),
        "month_report_count": len(months),
        "year_report_count": len(years),
        "calendar_reports_changed": sum(item["changed"] for item in period_outputs),
    }


def build_weekly(
    config: PipelineConfig,
    *,
    apply: bool,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Plan or build missing and stale complete ISO weeks."""
    if config.weekly_mart_root is None:
        raise ValueError("weekly_mart_path is required for build-weekly")
    if progress:
        progress("Reading ingest watermark")
    cutoff, watermark_sources = _watermark(config)
    eligible_end = cutoff - dt.timedelta(days=cutoff.isoweekday() % 7)
    if progress:
        progress("Scanning processed monthly CSV files")
    files = _monthly_files(config.processed_data_root)
    bounds_sources = [item for item in files if item.dataset == "DailyUsage"] or files
    bounds_by_file = [(source_file, _row_dates(source_file)) for source_file in bounds_sources]
    populated = [(source_file, bounds) for source_file, bounds in bounds_by_file if bounds]
    if not populated:
        raise ValueError("No source rows found under processed_data_path")
    earliest = min(bounds[0] for _, bounds in populated)
    if earliest > eligible_end:
        weeks: list[Week] = []
    else:
        weeks = _weeks_between(earliest, eligible_end)
    device_bounds: dict[str, tuple[dt.date, dt.date]] = {}
    for source_file, bounds in populated:
        current = device_bounds.get(source_file.device_id)
        if current is None:
            device_bounds[source_file.device_id] = bounds
        else:
            device_bounds[source_file.device_id] = (min(current[0], bounds[0]), max(current[1], bounds[1]))
    cache: dict[Path, dict[str, Any]] = {}
    if progress:
        progress(f"Planning {len(weeks)} complete ISO weeks")
    plans = [
        _plan_week(
            config.weekly_mart_root,
            week,
            tuple(item for item in files if _intersects_week(item, week)),
            cache,
        )
        for week in weeks
    ]
    targets = [plan for plan in plans if plan.status != "unchanged"]
    if progress:
        progress(
            f"Plan: {len(targets)} to generate, "
            f"{sum(plan.status == 'unchanged' for plan in plans)} unchanged"
        )
    generated: list[str] = []
    if apply:
        for index, plan in enumerate(targets, start=1):
            if progress and (
                index == 1 or index == len(targets) or index % 10 == 0
            ):
                progress(
                    f"Weekly reports {index}/{len(targets)}: "
                    f"{plan.week.week_id} ({plan.status})"
                )
            data = _aggregate(plan, device_bounds)
            _write_week(plan, data, config, cutoff, watermark_sources)
            generated.append(plan.week.week_id)
        if progress:
            progress("Building calendar month/year reports and root index")
        root_result = _write_root(
            config,
            weeks,
            files,
            cutoff,
            watermark_sources,
            earliest,
            eligible_end,
            progress,
        )
    else:
        month_count = len(_calendar_periods(earliest, eligible_end, "month"))
        year_count = len(_calendar_periods(earliest, eligible_end, "year"))
        root_result = {
            "index_changed": not (config.weekly_mart_root / "index.html").is_file(),
            "history_changed": not (config.weekly_mart_root / "weekly_history.csv").is_file(),
            "manifest_changed": not (config.weekly_mart_root / "manifest.json").is_file(),
            "week_count": len(weeks),
            "month_report_count": month_count,
            "year_report_count": year_count,
            "calendar_reports_changed": None,
        }
    return {
        "apply": apply,
        "mart_path": str(config.weekly_mart_root),
        "watermark_cutoff_date": cutoff.isoformat(),
        "latest_complete_week_end": eligible_end.isoformat(),
        "source_date_start": earliest.isoformat(),
        "candidate_week_count": len(plans),
        "missing_week_count": sum(plan.status == "missing" for plan in plans),
        "stale_week_count": sum(plan.status == "stale" for plan in plans),
        "unchanged_week_count": sum(plan.status == "unchanged" for plan in plans),
        "weeks_to_generate": [
            {"week_id": plan.week.week_id, "status": plan.status, "reason": plan.reason}
            for plan in targets
        ],
        "generated_weeks": generated,
        "index": root_result,
        "watermark_sources": watermark_sources,
    }
