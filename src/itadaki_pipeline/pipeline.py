"""Archive verified records and build monthly processed datasets."""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import os
import shutil
import uuid
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from .config import PipelineConfig, SourceConfig
from .parser import (
    SERIES,
    ParseError,
    date_from_filename,
    iter_key_file,
    parse_moc_file,
    parse_mom_file,
    parse_pow_file,
    validate_series_file,
)

INPUT_EVENTS_HEADER = [
    "device_id",
    "event_date",
    "event_datetime_local",
    "event_index",
    "event_type",
    "key_code",
    "key_name",
    "is_mouse",
    "year",
    "month",
    "day",
    "hour",
    "minute",
    "second",
    "weekday",
]

DAILY_USAGE_HEADER = [
    "device_id",
    "date",
    "year",
    "month",
    "day",
    "weekday",
    "key_count",
    "mouse_clicks",
    "total_events",
    "moc_clicks",
    "mouse_move_cm",
    "power_on_sec",
]


@dataclass(frozen=True)
class SourcePlan:
    source: SourceConfig
    cutoff_date: dt.date
    dates: tuple[dt.date, ...]
    source_files: dict[tuple[dt.date, str], Path]
    total_bytes: int
    expected_events: int
    validation_warnings: tuple[str, ...]


@dataclass(frozen=True)
class OutputRecord:
    dataset: str
    path: Path
    row_count: int
    sha256: str
    action: str


@dataclass(frozen=True)
class SourceRunResult:
    source_name: str
    device_id: str
    dates: int
    copied_files: int
    output_updates: int
    output_unchanged: int
    deleted_files: int
    manifest_path: Path | None
    warnings: tuple[str, ...]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def archive_relative(date: dt.date, series: str) -> Path:
    return (
        Path("Rec")
        / f"{date.year:04d}"
        / f"{date.month:02d}"
        / series
        / f"{date:%Y%m%d}.rec"
    )


def archive_path(source: SourceConfig, date: dt.date, series: str) -> Path:
    return source.archive_root / archive_relative(date, series)


def _discover_series(rec_dir: Path, series: str) -> dict[dt.date, Path]:
    directory = rec_dir / series
    if not directory.is_dir():
        raise FileNotFoundError(f"Missing Itadaki series directory: {directory}")
    result: dict[dt.date, Path] = {}
    for path in sorted(directory.glob("*.rec")):
        record_date = date_from_filename(path)
        if record_date in result:
            raise ParseError(f"Duplicate {series} file for {record_date}: {path}")
        result[record_date] = path
    return result


def discover_source(source: SourceConfig, cutoff_date: dt.date) -> SourcePlan:
    if not source.rec_dir.is_dir():
        raise FileNotFoundError(f"Rec directory not found: {source.rec_dir}")
    by_series = {series: _discover_series(source.rec_dir, series) for series in SERIES}
    eligible_union = sorted(
        {
            record_date
            for files in by_series.values()
            for record_date in files
            if record_date <= cutoff_date
        }
    )

    source_files: dict[tuple[dt.date, str], Path] = {}
    total_bytes = 0
    expected_events = 0
    validation_warnings: list[str] = []
    for record_date in eligible_union:
        for series in SERIES:
            path = by_series[series].get(record_date)
            if path is None:
                archived = archive_path(source, record_date, series)
                if not archived.is_file():
                    raise ParseError(
                        f"{source.name}: {record_date} is missing {series} "
                        "from both source and archive"
                    )
                validate_series_file(archived, series)
                continue
            validate_series_file(path, series)
            source_files[(record_date, series)] = path
            total_bytes += path.stat().st_size
            if series == "Key":
                expected_events += path.stat().st_size // 12

        key_path = by_series["Key"].get(record_date) or archive_path(
            source, record_date, "Key"
        )
        moc_path = by_series["MoC"].get(record_date) or archive_path(
            source, record_date, "MoC"
        )
        mouse_clicks = 0
        for event in iter_key_file(key_path):
            mouse_clicks += event.is_mouse
            if event.datetime_local.date() != record_date:
                validation_warnings.append(
                    f"{record_date}: event {event.index} has internal "
                    f"date {event.datetime_local.date()}"
                )
        moc_clicks = parse_moc_file(moc_path)
        if mouse_clicks != moc_clicks:
            validation_warnings.append(
                f"{record_date}: Key mouse count {mouse_clicks} "
                f"!= MoC count {moc_clicks}"
            )

    return SourcePlan(
        source=source,
        cutoff_date=cutoff_date,
        dates=tuple(eligible_union),
        source_files=source_files,
        total_bytes=total_bytes,
        expected_events=expected_events,
        validation_warnings=tuple(validation_warnings),
    )


def plans_for_mode(
    config: PipelineConfig,
    mode: str,
    *,
    now: dt.datetime | None = None,
) -> list[SourcePlan]:
    local_now = now or dt.datetime.now(config.timezone)
    cutoff = local_now.astimezone(config.timezone).date() - dt.timedelta(days=1)
    return [
        discover_source(source, cutoff)
        for source in config.sources
        if mode in source.modes
    ]


def _atomic_copy_verified(source: Path, destination: Path, expected_hash: str) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        actual_hash = sha256_file(destination)
        if actual_hash != expected_hash:
            raise ParseError(
                f"Archive hash collision at {destination}: "
                f"{actual_hash} != {expected_hash}"
            )
        return "existing"

    temporary = destination.parent / f".{destination.name}.{uuid.uuid4().hex}.tmp"
    try:
        shutil.copy2(source, temporary)
        copied_hash = sha256_file(temporary)
        if copied_hash != expected_hash:
            raise OSError(
                f"Hash changed while copying {source}: "
                f"{copied_hash} != {expected_hash}"
            )
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return "copied"


def _month_archive_files(
    source: SourceConfig,
    year: int,
    month: int,
) -> dict[dt.date, dict[str, Path]]:
    month_root = source.archive_root / "Rec" / f"{year:04d}" / f"{month:02d}"
    by_series: dict[str, dict[dt.date, Path]] = {}
    for series in SERIES:
        directory = month_root / series
        paths: dict[dt.date, Path] = {}
        if directory.is_dir():
            for path in sorted(directory.glob("*.rec")):
                record_date = date_from_filename(path)
                validate_series_file(path, series)
                paths[record_date] = path
        by_series[series] = paths

    dates = sorted({item for values in by_series.values() for item in values})
    result: dict[dt.date, dict[str, Path]] = {}
    for record_date in dates:
        missing = [series for series in SERIES if record_date not in by_series[series]]
        if missing:
            raise ParseError(
                f"{source.archive_root}: incomplete archive for {record_date}; "
                f"missing {', '.join(missing)}"
            )
        result[record_date] = {
            series: by_series[series][record_date] for series in SERIES
        }
    return result


def _event_row(
    device_id: str,
    record_date: dt.date,
    event,
) -> list[object]:
    event_dt = event.datetime_local
    return [
        device_id,
        record_date.isoformat(),
        event_dt.isoformat(timespec="milliseconds"),
        event.index,
        "mouse" if event.is_mouse else "keyboard",
        event.code,
        event.key_name,
        1 if event.is_mouse else 0,
        event_dt.year,
        event_dt.month,
        event_dt.day,
        event_dt.hour,
        event_dt.minute,
        event_dt.second,
        event_dt.weekday(),
    ]


def _daily_row(
    device_id: str,
    record_date: dt.date,
    key_count: int,
    mouse_clicks: int,
    moc_clicks: int,
    mouse_move_cm: float,
    power_on_sec: int,
) -> list[object]:
    return [
        device_id,
        record_date.isoformat(),
        record_date.year,
        record_date.month,
        record_date.day,
        record_date.weekday(),
        key_count,
        mouse_clicks,
        key_count + mouse_clicks,
        moc_clicks,
        mouse_move_cm,
        power_on_sec,
    ]


def _temporary_csv(destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    return destination.parent / f".{destination.name}.{uuid.uuid4().hex}.tmp"


def _validate_csv(
    path: Path,
    header: list[str],
    *,
    expected_rows: int,
    device_id: str,
    dataset: str,
) -> None:
    previous: tuple[str, int] | str | None = None
    count = 0
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        actual_header = next(reader, None)
        if actual_header != header:
            raise ParseError(f"{path}: unexpected header {actual_header}")
        for row in reader:
            if row[0] != device_id:
                raise ParseError(f"{path}: unexpected device_id {row[0]!r}")
            if dataset == "InputEvents":
                key: tuple[str, int] | str = (row[1], int(row[3]))
            else:
                key = row[1]
            if previous is not None and key <= previous:
                raise ParseError(f"{path}: rows are not strictly sorted at {key!r}")
            previous = key
            count += 1
    if count != expected_rows:
        raise ParseError(f"{path}: expected {expected_rows} rows, got {count}")


def _commit_csv(temporary: Path, destination: Path, dataset: str, rows: int) -> OutputRecord:
    new_hash = sha256_file(temporary)
    if destination.exists() and sha256_file(destination) == new_hash:
        temporary.unlink()
        action = "unchanged"
    else:
        os.replace(temporary, destination)
        action = "updated"
    return OutputRecord(
        dataset=dataset,
        path=destination,
        row_count=rows,
        sha256=new_hash,
        action=action,
    )


def build_month(
    config: PipelineConfig,
    source: SourceConfig,
    year: int,
    month: int,
) -> tuple[list[OutputRecord], list[str]]:
    archive_files = _month_archive_files(source, year, month)
    if not archive_files:
        return [], []

    device_root = config.processed_data_root / source.device_id
    input_destination = (
        device_root / "InputEvents" / f"{year:04d}" / f"{month:02d}.csv"
    )
    daily_destination = (
        device_root / "DailyUsage" / f"{year:04d}" / f"{month:02d}.csv"
    )
    input_temporary = _temporary_csv(input_destination)
    daily_temporary = _temporary_csv(daily_destination)
    input_rows = 0
    daily_rows = 0
    warnings: list[str] = []

    try:
        with (
            input_temporary.open("w", encoding="utf-8-sig", newline="") as input_handle,
            daily_temporary.open("w", encoding="utf-8-sig", newline="") as daily_handle,
        ):
            input_writer = csv.writer(input_handle)
            daily_writer = csv.writer(daily_handle)
            input_writer.writerow(INPUT_EVENTS_HEADER)
            daily_writer.writerow(DAILY_USAGE_HEADER)

            for record_date, paths in sorted(archive_files.items()):
                key_count = 0
                mouse_clicks = 0
                for event in iter_key_file(paths["Key"]):
                    if event.datetime_local.date() != record_date:
                        warnings.append(
                            f"{record_date}: event {event.index} has internal "
                            f"date {event.datetime_local.date()}"
                        )
                    input_writer.writerow(_event_row(source.device_id, record_date, event))
                    input_rows += 1
                    if event.is_mouse:
                        mouse_clicks += 1
                    else:
                        key_count += 1

                moc_clicks = parse_moc_file(paths["MoC"])
                mouse_move_cm = parse_mom_file(paths["MoM"])
                power_on_sec = parse_pow_file(paths["Pow"])
                if mouse_clicks != moc_clicks:
                    warnings.append(
                        f"{record_date}: Key mouse count {mouse_clicks} "
                        f"!= MoC count {moc_clicks}"
                    )
                daily_writer.writerow(
                    _daily_row(
                        source.device_id,
                        record_date,
                        key_count,
                        mouse_clicks,
                        moc_clicks,
                        mouse_move_cm,
                        power_on_sec,
                    )
                )
                daily_rows += 1

            input_handle.flush()
            os.fsync(input_handle.fileno())
            daily_handle.flush()
            os.fsync(daily_handle.fileno())

        _validate_csv(
            input_temporary,
            INPUT_EVENTS_HEADER,
            expected_rows=input_rows,
            device_id=source.device_id,
            dataset="InputEvents",
        )
        _validate_csv(
            daily_temporary,
            DAILY_USAGE_HEADER,
            expected_rows=daily_rows,
            device_id=source.device_id,
            dataset="DailyUsage",
        )
        outputs = [
            _commit_csv(
                input_temporary, input_destination, "InputEvents", input_rows
            ),
            _commit_csv(
                daily_temporary, daily_destination, "DailyUsage", daily_rows
            ),
        ]
        return outputs, warnings
    finally:
        input_temporary.unlink(missing_ok=True)
        daily_temporary.unlink(missing_ok=True)


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _total_ini_snapshot(source: SourceConfig) -> dict | None:
    path = source.rec_dir / "Total.ini"
    if not path.is_file():
        return None
    return {
        "path": "Total.ini",
        "size": path.stat().st_size,
        "sha256": sha256_file(path),
        "text": path.read_text(encoding="utf-8-sig", errors="replace"),
    }


def process_source(
    config: PipelineConfig,
    plan: SourcePlan,
    *,
    mode: str,
    now: dt.datetime | None = None,
) -> SourceRunResult:
    source = plan.source
    if not plan.dates:
        return SourceRunResult(
            source_name=source.name,
            device_id=source.device_id,
            dates=0,
            copied_files=0,
            output_updates=0,
            output_unchanged=0,
            deleted_files=0,
            manifest_path=None,
            warnings=(),
        )

    local_now = (now or dt.datetime.now(config.timezone)).astimezone(config.timezone)
    batch_id = (
        local_now.strftime("%Y%m%dT%H%M%S%z")
        + f"_{mode}_{source.name.replace(' ', '-')}"
    )
    file_records: list[dict] = []
    copied_files = 0
    for record_date in plan.dates:
        for series in SERIES:
            source_path = plan.source_files.get((record_date, series))
            destination = archive_path(source, record_date, series)
            if source_path is not None:
                source_hash = sha256_file(source_path)
                action = _atomic_copy_verified(
                    source_path, destination, source_hash
                )
                if action == "copied":
                    copied_files += 1
            else:
                if not destination.is_file():
                    raise ParseError(
                        f"Recovery failed; archive file is missing: {destination}"
                    )
                source_hash = sha256_file(destination)
                action = "archive-only"
            validate_series_file(destination, series)
            if sha256_file(destination) != source_hash:
                raise ParseError(f"Archive verification failed: {destination}")
            file_records.append(
                {
                    "date": record_date.isoformat(),
                    "series": series,
                    "source_path": (
                        f"{series}/{source_path.name}"
                        if source_path is not None
                        else None
                    ),
                    "source_present": source_path is not None,
                    "archive_path": _relative(destination, source.archive_root),
                    "size": destination.stat().st_size,
                    "sha256": source_hash,
                    "action": action,
                }
            )

    outputs: list[OutputRecord] = []
    warnings: list[str] = list(plan.validation_warnings)
    months = sorted({(date.year, date.month) for date in plan.dates})
    for year, month in months:
        month_outputs, month_warnings = build_month(
            config, source, year, month
        )
        outputs.extend(month_outputs)
        warnings.extend(month_warnings)
    warnings = list(dict.fromkeys(warnings))

    manifest_path = (
        source.archive_root
        / "_manifests"
        / f"{local_now.year:04d}"
        / f"{local_now.month:02d}"
        / f"{batch_id}.json"
    )
    payload = {
        "schema_version": 1,
        "batch_id": batch_id,
        "status": "complete",
        "mode": mode,
        "started_at": local_now.isoformat(timespec="seconds"),
        "completed_at": dt.datetime.now(config.timezone).isoformat(timespec="seconds"),
        "timezone": config.timezone_name,
        "source_name": source.name,
        "device_id": source.device_id,
        "cutoff_date": plan.cutoff_date.isoformat(),
        "date_range": [plan.dates[0].isoformat(), plan.dates[-1].isoformat()],
        "date_count": len(plan.dates),
        "files": file_records,
        "outputs": [
            {
                "dataset": output.dataset,
                "path": _relative(output.path, config.processed_data_root),
                "row_count": output.row_count,
                "sha256": output.sha256,
                "action": output.action,
            }
            for output in outputs
        ],
        "warnings": warnings,
        "total_ini": _total_ini_snapshot(source),
        "cleanup": {
            "requested": source.delete_after_success,
            "status": "pending" if source.delete_after_success else "preserved",
            "deleted_files": 0,
            "errors": [],
        },
    }
    _write_json_atomic(manifest_path, payload)

    deleted_files = 0
    cleanup_errors: list[str] = []
    if source.delete_after_success:
        hashes = {
            (dt.date.fromisoformat(item["date"]), item["series"]): item["sha256"]
            for item in file_records
        }
        for (record_date, series), source_path in sorted(plan.source_files.items()):
            destination = archive_path(source, record_date, series)
            try:
                expected_hash = hashes[(record_date, series)]
                if sha256_file(destination) != expected_hash:
                    raise ParseError(f"Archive changed before cleanup: {destination}")
                if sha256_file(source_path) != expected_hash:
                    raise ParseError(f"Source changed before cleanup: {source_path}")
                source_path.unlink()
                deleted_files += 1
            except Exception as exc:  # cleanup must preserve a resumable manifest
                cleanup_errors.append(f"{source_path}: {exc}")

        payload["cleanup"] = {
            "requested": True,
            "status": "complete" if not cleanup_errors else "incomplete",
            "deleted_files": deleted_files,
            "errors": cleanup_errors,
        }
        payload["completed_at"] = dt.datetime.now(config.timezone).isoformat(
            timespec="seconds"
        )
        _write_json_atomic(manifest_path, payload)
        if cleanup_errors:
            raise OSError(
                "Source cleanup was incomplete:\n" + "\n".join(cleanup_errors)
            )

    return SourceRunResult(
        source_name=source.name,
        device_id=source.device_id,
        dates=len(plan.dates),
        copied_files=copied_files,
        output_updates=sum(output.action == "updated" for output in outputs),
        output_unchanged=sum(output.action == "unchanged" for output in outputs),
        deleted_files=deleted_files,
        manifest_path=manifest_path,
        warnings=tuple(warnings),
    )


def run_pipeline(
    config: PipelineConfig,
    mode: str,
    *,
    apply: bool,
    now: dt.datetime | None = None,
) -> tuple[list[SourcePlan], list[SourceRunResult]]:
    plans = plans_for_mode(config, mode, now=now)
    if not apply:
        return plans, []
    return plans, [
        process_source(config, plan, mode=mode, now=now) for plan in plans
    ]


def archive_months(source: SourceConfig) -> Iterator[tuple[int, int]]:
    rec_root = source.archive_root / "Rec"
    if not rec_root.is_dir():
        return
    for year_dir in sorted(rec_root.iterdir()):
        if not year_dir.is_dir() or not year_dir.name.isdigit():
            continue
        for month_dir in sorted(year_dir.iterdir()):
            if month_dir.is_dir() and month_dir.name.isdigit():
                yield int(year_dir.name), int(month_dir.name)


def _expected_month_rows(
    source: SourceConfig,
    year: int,
    month: int,
) -> tuple[Iterator[list[object]], Iterator[list[object]], int, int]:
    archive_files = _month_archive_files(source, year, month)
    event_count = sum(
        paths["Key"].stat().st_size // 12 for paths in archive_files.values()
    )
    daily_count = len(archive_files)

    def events() -> Iterator[list[object]]:
        for record_date, paths in sorted(archive_files.items()):
            for event in iter_key_file(paths["Key"]):
                yield _event_row(source.device_id, record_date, event)

    def daily() -> Iterator[list[object]]:
        for record_date, paths in sorted(archive_files.items()):
            key_count = 0
            mouse_clicks = 0
            for event in iter_key_file(paths["Key"]):
                if event.is_mouse:
                    mouse_clicks += 1
                else:
                    key_count += 1
            yield _daily_row(
                source.device_id,
                record_date,
                key_count,
                mouse_clicks,
                parse_moc_file(paths["MoC"]),
                parse_mom_file(paths["MoM"]),
                parse_pow_file(paths["Pow"]),
            )

    return events(), daily(), event_count, daily_count


def _compare_csv(
    path: Path,
    header: list[str],
    expected_rows: Iterable[list[object]],
) -> int:
    if not path.is_file():
        raise FileNotFoundError(f"Expected processed data output is missing: {path}")
    count = 0
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        actual_header = next(reader, None)
        if actual_header != header:
            raise ParseError(f"{path}: unexpected header {actual_header}")
        for expected in expected_rows:
            actual = next(reader, None)
            expected_text = [str(value) for value in expected]
            if actual != expected_text:
                raise ParseError(
                    f"{path}: row {count + 1} differs; "
                    f"expected {expected_text!r}, got {actual!r}"
                )
            count += 1
        extra = next(reader, None)
        if extra is not None:
            raise ParseError(f"{path}: unexpected extra row {extra!r}")
    return count


def verify_config(config: PipelineConfig) -> list[dict]:
    results: list[dict] = []
    seen: set[tuple[str, Path]] = set()
    for source in config.sources:
        key = (source.device_id, source.archive_root)
        if key in seen:
            continue
        seen.add(key)
        for year, month in archive_months(source):
            events, daily, expected_events, expected_daily = _expected_month_rows(
                source, year, month
            )
            device_root = config.processed_data_root / source.device_id
            event_path = (
                device_root / "InputEvents" / f"{year:04d}" / f"{month:02d}.csv"
            )
            daily_path = (
                device_root / "DailyUsage" / f"{year:04d}" / f"{month:02d}.csv"
            )
            actual_events = _compare_csv(
                event_path, INPUT_EVENTS_HEADER, events
            )
            actual_daily = _compare_csv(
                daily_path, DAILY_USAGE_HEADER, daily
            )
            if actual_events != expected_events or actual_daily != expected_daily:
                raise ParseError(
                    f"{source.device_id} {year:04d}-{month:02d}: "
                    "row count verification failed"
                )
            results.append(
                {
                    "device_id": source.device_id,
                    "month": f"{year:04d}-{month:02d}",
                    "input_events": actual_events,
                    "daily_usage": actual_daily,
                }
            )
    return results
