from __future__ import annotations

import csv
import datetime as dt
import json
from pathlib import Path

import pytest

from itadaki_pipeline.config import PipelineConfig, ProfileConfig
from itadaki_pipeline.pipeline import DAILY_USAGE_HEADER, INPUT_EVENTS_HEADER
from itadaki_pipeline.weekly import (
    DAILY_ACTIVITY_HEADER,
    HISTORY_HEADER,
    HOURLY_INPUT_HEADER,
    KEY_FREQUENCY_HEADER,
    WEEKLY_SUMMARY_HEADER,
    _week_for_date,
    build_weekly,
)


def _write_csv(path: Path, header: list[str], rows: list[list[object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def _event(
    device: str,
    date: str,
    local_datetime: str,
    index: int,
    *,
    mouse: bool,
    code: int,
    name: str,
) -> list[object]:
    parsed = dt.datetime.fromisoformat(local_datetime)
    return [
        device,
        date,
        local_datetime,
        index,
        "mouse" if mouse else "keyboard",
        code,
        name,
        1 if mouse else 0,
        parsed.year,
        parsed.month,
        parsed.day,
        parsed.hour,
        parsed.minute,
        parsed.second,
        parsed.weekday(),
    ]


def _daily(
    device: str,
    date: str,
    *,
    keys: int,
    clicks: int,
    moc: int,
    move: float,
    power: int,
) -> list[object]:
    value = dt.date.fromisoformat(date)
    return [
        device,
        date,
        value.year,
        value.month,
        value.day,
        value.weekday(),
        keys,
        clicks,
        keys + clicks,
        moc,
        move,
        power,
    ]


def _config(tmp_path: Path) -> PipelineConfig:
    processed = tmp_path / "processed"
    _write_csv(
        processed / "PC-A" / "DailyUsage" / "2026" / "04.csv",
        DAILY_USAGE_HEADER,
        [_daily("PC-A", "2026-04-04", keys=2, clicks=0, moc=0, move=12.5, power=60)],
    )
    _write_csv(
        processed / "PC-A" / "InputEvents" / "2026" / "04.csv",
        INPUT_EVENTS_HEADER,
        [
            _event(
                "PC-A",
                "2026-04-04",
                "2026-04-04T09:15:00+09:00",
                1,
                mouse=False,
                code=65,
                name="A",
            ),
            _event(
                "PC-A",
                "2026-04-04",
                "1899-12-30T10:00:00+09:00",
                2,
                mouse=False,
                code=66,
                name="B",
            ),
        ],
    )
    _write_csv(
        processed / "PC-B" / "DailyUsage" / "2026" / "04.csv",
        DAILY_USAGE_HEADER,
        [_daily("PC-B", "2026-04-05", keys=0, clicks=1, moc=2, move=2, power=30)],
    )
    _write_csv(
        processed / "PC-B" / "InputEvents" / "2026" / "04.csv",
        INPUT_EVENTS_HEADER,
        [
            _event(
                "PC-B",
                "2026-04-05",
                "2026-04-05T20:00:00+09:00",
                1,
                mouse=True,
                code=1,
                name="(LClick)",
            )
        ],
    )
    archive = tmp_path / "archive"
    manifest = archive / "_manifests" / "2026" / "04" / "ingest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "status": "complete",
                "mode": "ingest",
                "source_name": "current",
                "device_id": "PC-B",
                "cutoff_date": "2026-04-12",
                "batch_id": "fixture",
            }
        ),
        encoding="utf-8",
    )
    profile = ProfileConfig(
        name="current",
        device_id="PC-B",
        timezone_name="Asia/Tokyo",
        timezone=dt.timezone(dt.timedelta(hours=9), name="Asia/Tokyo"),
        rec_dir=tmp_path / "Rec",
        archive_root=archive,
        delete_after_success=False,
    )
    return PipelineConfig(
        config_path=tmp_path / "config.yaml",
        processed_data_root=processed,
        log_dir=tmp_path / "logs",
        default_profile_name="current",
        selected_profile_name="current",
        profiles=(profile,),
        weekly_mart_root=tmp_path / "mart",
    )


def _read_csv(path: Path) -> list[list[str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.reader(handle))


def test_iso_week_boundaries_and_leap_day() -> None:
    assert _week_for_date(dt.date(2021, 1, 1)).week_id == "2020-W53"
    leap = _week_for_date(dt.date(2024, 2, 29))
    assert leap.start == dt.date(2024, 2, 26)
    assert leap.end == dt.date(2024, 3, 3)


def test_build_weekly_apply_quality_contract_and_idempotency(tmp_path: Path) -> None:
    config = _config(tmp_path)

    dry_run = build_weekly(config, apply=False)
    assert dry_run["latest_complete_week_end"] == "2026-04-12"
    assert dry_run["candidate_week_count"] == 2
    assert dry_run["missing_week_count"] == 2
    assert not config.weekly_mart_root.exists()

    applied = build_weekly(config, apply=True)
    assert applied["generated_weeks"] == ["2026-W14", "2026-W15"]
    week = config.weekly_mart_root / "weeks" / "2026-W14"
    assert (week / "weekly_summary.csv").read_bytes().startswith(b"\xef\xbb\xbf")
    assert _read_csv(week / "weekly_summary.csv")[0] == WEEKLY_SUMMARY_HEADER
    assert _read_csv(week / "daily_activity.csv")[0] == DAILY_ACTIVITY_HEADER
    assert _read_csv(week / "hourly_input.csv")[0] == HOURLY_INPUT_HEADER
    assert _read_csv(week / "key_frequency.csv")[0] == KEY_FREQUENCY_HEADER
    assert _read_csv(config.weekly_mart_root / "weekly_history.csv")[0] == HISTORY_HEADER

    summary = _read_csv(week / "weekly_summary.csv")
    assert summary[1][3:10] == ["all_devices", "all_devices", "2", "2", "1", "2", "3"]
    assert len(_read_csv(week / "daily_activity.csv")) == 1 + 7 * 3
    assert len(_read_csv(week / "hourly_input.csv")) == 1 + 7 * 24 * 3
    keys = _read_csv(week / "key_frequency.csv")
    assert {row[4] for row in keys[1:] if row[1] == "all_devices"} == {"A", "B"}
    manifest = json.loads((week / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 2
    assert manifest["watermark_profiles"][0]["profile_name"] == "current"
    assert "watermark_sources" not in manifest
    assert manifest["quality"]["event_datetime_date_mismatch_count"] == 1
    assert manifest["quality"]["hourly_excluded_event_count"] == 1
    assert manifest["quality"]["key_moc_mismatch_date_count"] == 1
    assert all("sha256" in item for item in manifest["input_fingerprints"])
    report = (week / "report.html").read_text(encoding="utf-8")
    assert "http://" not in report and "https://" not in report
    assert '<meta name="viewport"' in report
    assert "データ品質" in report and "<table" in report
    assert "#ff0000" not in report and "#00ff00" not in report
    assert all(word not in report for word in ("評価", "推奨", "改善案", "良好", "不良"))
    assert '>日付</text>' in report
    assert '>event数</text>' in report
    assert '>時刻</text>' in report
    assert '>曜日</text>' in report
    assert '>論理キー</text>' in report
    assert '<details class="data-table">' in report
    assert '<details class="data-table" open>' not in report
    assert "日付×時刻の数値表を表示（168行）" in report
    index = (config.weekly_mart_root / "index.html").read_text(encoding="utf-8")
    assert 'href="weeks/2026-W14/report.html"' in index
    assert 'href="months/2026-04/report.html"' in index
    assert 'href="years/2026/report.html"' in index
    assert '>ISO週</text>' in index
    assert '>event数</text>' in index

    month_report = (
        config.weekly_mart_root / "months" / "2026-04" / "report.html"
    ).read_text(encoding="utf-8")
    year_report = (
        config.weekly_mart_root / "years" / "2026" / "report.html"
    ).read_text(encoding="utf-8")
    assert "2026-04 Itadaki月次活動" in month_report
    assert '>日付</text>' in month_report and '>時刻</text>' in month_report
    assert "<h2>日付×時刻</h2>" in month_report
    assert "<h2>曜日×時刻</h2>" in month_report
    assert "月内の同じISO曜日・同じ時刻に属するevent数の合計" in month_report
    assert 'aria-label="keyboard weekday-by-hour heatmap"' in month_report
    assert 'aria-label="click weekday-by-hour heatmap"' in month_report
    assert ">曜日</text>" in month_report
    assert "<title>土曜日 09:00: 1</title>" in month_report
    assert "<title>日曜日 20:00: 1</title>" in month_report
    assert "日付×時刻の数値表を表示（216行）" in month_report
    assert "曜日×時刻の数値表を表示（168行）" in month_report
    assert '<details class="data-table" open>' not in month_report
    assert '<th scope="col">ISO曜日</th>' in month_report
    assert '<th scope="col">時</th>' in month_report
    assert '<th scope="col">keyboard</th>' in month_report
    assert '<th scope="col">click</th>' in month_report
    assert "<h2>論理キー頻度</h2>" in month_report
    assert "<title>A: 1</title>" in month_report
    assert "<title>B: 1</title>" in month_report
    assert "論理キー頻度の数値表を表示（2行）" in month_report
    assert "(LClick)" not in month_report
    assert "2026 Itadaki年次活動" in year_report
    assert '>月</text>' in year_report and '>時刻</text>' in year_report
    assert "月×時刻の数値表を表示（24行）" in year_report
    assert '<details class="data-table" open>' not in year_report
    assert "<h2>論理キー頻度</h2>" in year_report
    assert "<title>A: 1</title>" in year_report
    assert "<title>B: 1</title>" in year_report
    assert "論理キー頻度の数値表を表示（2行）" in year_report
    assert "(LClick)" not in year_report
    assert applied["index"]["month_report_count"] == 1
    assert applied["index"]["year_report_count"] == 1

    empty_summary = _read_csv(
        config.weekly_mart_root / "weeks" / "2026-W15" / "weekly_summary.csv"
    )
    assert empty_summary[1][5] == "0"
    empty_report = (
        config.weekly_mart_root / "weeks" / "2026-W15" / "report.html"
    ).read_text(encoding="utf-8")
    assert "source rowなし" in empty_report

    second = build_weekly(config, apply=True)
    assert second["generated_weeks"] == []
    assert second["unchanged_week_count"] == 2
    assert second["index"]["index_changed"] is False
    assert second["index"]["history_changed"] is False
    assert second["index"]["manifest_changed"] is False
    assert second["index"]["calendar_reports_changed"] == 0

    (week / "report.html").write_text("broken", encoding="utf-8")
    repair = build_weekly(config, apply=False)
    assert repair["stale_week_count"] == 1
    assert repair["weeks_to_generate"][0]["reason"] == "output missing or hash mismatch"

    build_weekly(config, apply=True)
    input_path = config.processed_data_root / "PC-A" / "InputEvents" / "2026" / "04.csv"
    with input_path.open("a", encoding="utf-8", newline="") as handle:
        handle.write("\n")
    stale_month = build_weekly(config, apply=False)
    assert stale_month["stale_week_count"] == 2
    assert {item["reason"] for item in stale_month["weeks_to_generate"]} == {
        "input fingerprint changed"
    }


def test_missing_watermark_stops_before_mart_write(tmp_path: Path) -> None:
    config = _config(tmp_path)
    for path in (tmp_path / "archive" / "_manifests").rglob("*.json"):
        path.unlink()

    with pytest.raises(ValueError, match="tkn-itadaki-pipeline ingest"):
        build_weekly(config, apply=True)
    assert not config.weekly_mart_root.exists()


def test_build_weekly_reports_progress(tmp_path: Path) -> None:
    messages: list[str] = []

    build_weekly(_config(tmp_path), apply=True, progress=messages.append)

    assert messages[0] == "Reading ingest watermark"
    assert any(message.startswith("Weekly reports 1/2") for message in messages)
    assert "Scanning logical key frequencies for calendar reports" in messages
    assert "Logical key input files 1/2" in messages
    assert "Logical key input files 2/2" in messages
    assert any(message.startswith("Calendar reports 1/2") for message in messages)


def test_generator_version_change_marks_week_stale(tmp_path: Path) -> None:
    config = _config(tmp_path)
    build_weekly(config, apply=True)
    manifest_path = config.weekly_mart_root / "weeks" / "2026-W14" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["generator_version"] = "0.2.0"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    plan = build_weekly(config, apply=False)

    stale = next(item for item in plan["weeks_to_generate"] if item["week_id"] == "2026-W14")
    assert stale["reason"] == "generator version changed"
