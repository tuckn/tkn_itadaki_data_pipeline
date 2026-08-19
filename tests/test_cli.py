import datetime as dt
import json
from pathlib import Path

import pytest

from itadaki_pipeline.cli import _open_report, _parser, main
from itadaki_pipeline.config import PipelineConfig, ProfileConfig, ResolvedConfig
from itadaki_pipeline.paths import user_paths


def _resolved_config(tmp_path: Path, *, weekly: bool = False) -> ResolvedConfig:
    profile = ProfileConfig(
        name="current",
        device_id="Example PC",
        timezone_name="Asia/Tokyo",
        timezone=dt.timezone(dt.timedelta(hours=9), name="Asia/Tokyo"),
        rec_dir=tmp_path / "source",
        archive_root=tmp_path / "archive",
        delete_after_success=False,
    )
    config = PipelineConfig(
        config_path=tmp_path / "config.yaml",
        processed_data_root=tmp_path / "processed",
        log_dir=tmp_path / "logs",
        default_profile_name="current",
        selected_profile_name="current",
        profiles=(profile,),
        weekly_mart_root=tmp_path / "mart" if weekly else None,
    )
    return ResolvedConfig(
        config=config,
        config_sources=(str(config.config_path),),
        paths=user_paths(),
    )


def test_parser_uses_installed_command_name() -> None:
    assert _parser().prog == "tkn-itadaki-pipeline"


def test_config_option_is_accepted_before_or_after_pipeline_command() -> None:
    before = _parser().parse_args(["--config", "before.yaml", "ingest"])
    after = _parser().parse_args(["ingest", "--config", "after.yaml"])

    assert before.config == Path("before.yaml")
    assert before.command_config is None
    assert after.config is None
    assert after.command_config == Path("after.yaml")

    weekly_before = _parser().parse_args(
        ["--config", "before.yaml", "build-weekly"]
    )
    weekly_after = _parser().parse_args(
        ["build-weekly", "--config", "after.yaml"]
    )
    assert weekly_before.config == Path("before.yaml")
    assert weekly_after.command_config == Path("after.yaml")


def test_profile_option_is_accepted_before_or_after_pipeline_command() -> None:
    before = _parser().parse_args(["--profile", "before", "ingest"])
    after = _parser().parse_args(["ingest", "--profile", "after"])

    assert before.profile == "before"
    assert before.command_profile is None
    assert after.profile is None
    assert after.command_profile == "after"


def test_change_commands_accept_explicit_dry_run() -> None:
    default = _parser().parse_args(["ingest"])
    dry_run = _parser().parse_args(["ingest", "--dry-run"])

    assert default.dry_run is False
    assert dry_run.dry_run is True


def test_build_weekly_opens_by_default_and_accepts_no_open() -> None:
    default = _parser().parse_args(["build-weekly"])
    suppressed = _parser().parse_args(["build-weekly", "--no-open"])

    assert default.no_open is False
    assert suppressed.no_open is True


def test_open_report_uses_default_browser_with_file_uri(
    tmp_path: Path,
    monkeypatch,
) -> None:
    report = tmp_path / "mart" / "index.html"
    opened: list[tuple[str, int]] = []

    def fake_open(url: str, new: int = 0) -> bool:
        opened.append((url, new))
        return True

    monkeypatch.setattr("itadaki_pipeline.cli.webbrowser.open", fake_open)

    assert _open_report(report) is True
    assert opened == [(report.resolve().as_uri(), 2)]


def test_apply_option_is_removed() -> None:
    with pytest.raises(SystemExit):
        _parser().parse_args(["ingest", "--apply"])


def test_backfill_option_is_removed() -> None:
    with pytest.raises(SystemExit):
        _parser().parse_args(["ingest", "--backfill"])


@pytest.mark.parametrize("command", ["plan", "backfill", "run"])
def test_replaced_ingest_commands_are_removed(command: str) -> None:
    with pytest.raises(SystemExit):
        _parser().parse_args([command])


def test_config_show_command() -> None:
    args = _parser().parse_args(["config", "show", "--profile", "current"])

    assert args.command == "config"
    assert args.config_command == "show"
    assert args.command_profile == "current"


def test_config_init_emits_status_and_absolute_path(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    destination = tmp_path / "user" / "config.yaml"
    monkeypatch.setattr(
        "itadaki_pipeline.cli.initialize_user_config",
        lambda: (destination.resolve(), "created"),
    )

    assert main(["config", "init"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "status": "created",
        "command": "config init",
        "config_path": str(destination.resolve()),
    }


def test_config_init_does_not_resolve_existing_configuration(
    tmp_path: Path,
    monkeypatch,
) -> None:
    destination = tmp_path / "config.yaml"
    monkeypatch.setattr(
        "itadaki_pipeline.cli.initialize_user_config",
        lambda: (destination.resolve(), "created"),
    )

    def unexpected_resolve(**_):
        raise AssertionError("config init must not resolve configuration")

    monkeypatch.setattr("itadaki_pipeline.cli.resolve_config", unexpected_resolve)

    assert main(["config", "init"]) == 0


def test_config_init_rejects_explicit_config(capsys) -> None:
    assert main(["--config", "other.yaml", "config", "init"]) == 1
    assert "--config cannot be combined with config init" in capsys.readouterr().err


def test_config_show_emits_processed_data_path(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(
        "itadaki_pipeline.config.global_config_path",
        lambda: tmp_path / "missing-global.yaml",
    )
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "config.yaml"
    config.write_text(
        "\n".join(
            [
                "processed_data_path: processed-data",
                "default_profile: current",
                "profiles:",
                "  - name: current",
                "    device_id: Current PC",
                "    timezone: Asia/Tokyo",
                "    source_path: source",
                "    destination_path: destination",
            ]
        ),
        encoding="utf-8",
    )

    assert main(["--config", str(config), "config", "show"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["values"]["processed_data_path"] == str(
        tmp_path / "processed-data"
    )
    assert "bronze_path" not in payload["values"]


def test_ingest_dry_run_forwards_selected_profile_without_creating_logs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    resolved = _resolved_config(tmp_path)
    config = resolved.config
    called: dict[str, object] = {}

    requested: dict[str, object] = {}

    def fake_resolve(**kwargs):
        requested.update(kwargs)
        return resolved

    monkeypatch.setattr("itadaki_pipeline.cli.resolve_config", fake_resolve)

    def fake_run(config, *, apply):
        called.update(apply=apply)
        return [], []

    monkeypatch.setattr("itadaki_pipeline.cli.run_pipeline", fake_run)

    assert main(["ingest", "--profile", "historical", "--dry-run"]) == 0
    assert requested["profile_name"] == "historical"
    assert called == {"apply": False}
    assert not config.log_dir.exists()


def test_option_free_ingest_applies_changes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    resolved = _resolved_config(tmp_path)
    config = resolved.config
    called: dict[str, object] = {}
    monkeypatch.setattr("itadaki_pipeline.cli.resolve_config", lambda **_: resolved)

    def fake_run(config, *, apply):
        called.update(apply=apply)
        return [], []

    monkeypatch.setattr("itadaki_pipeline.cli.run_pipeline", fake_run)

    assert main(["ingest"]) == 0
    assert called == {"apply": True}
    assert config.log_dir.is_dir()


def test_build_weekly_prints_progress_summary_and_result_paths(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    resolved = _resolved_config(tmp_path, weekly=True)
    monkeypatch.setattr("itadaki_pipeline.cli.resolve_config", lambda **_: resolved)
    opened: list[Path] = []
    monkeypatch.setattr(
        "itadaki_pipeline.cli._open_report",
        lambda path: opened.append(path) is None,
    )

    def fake_build(config, *, apply, progress):
        progress("Planning fixture weeks")
        return {
            "apply": apply,
            "mart_path": str(config.weekly_mart_root),
            "candidate_week_count": 3,
            "missing_week_count": 0,
            "stale_week_count": 1,
            "unchanged_week_count": 2,
            "weeks_to_generate": [
                {"week_id": "2026-W30", "status": "stale", "reason": "input changed"}
            ],
            "generated_weeks": ["2026-W30"],
            "index": {"month_report_count": 4, "year_report_count": 1},
        }

    monkeypatch.setattr("itadaki_pipeline.cli.build_weekly", fake_build)

    assert main(["build-weekly"]) == 0
    captured = capsys.readouterr()
    assert not captured.out.lstrip().startswith("{")
    assert "[SUCCESS] build-weekly completed." in captured.out
    assert "Weeks: 3 candidate, 1 generated, 2 unchanged, 1 stale." in captured.out
    assert "Calendar reports: 4 monthly, 1 yearly." in captured.out
    assert "[INFO] Planning fixture weeks" in captured.err
    result_line = next(
        line for line in captured.out.splitlines() if "Result JSON:" in line
    )
    result_path = Path(result_line.split("Result JSON:", 1)[1].strip())
    assert result_path.is_file()
    assert json.loads(result_path.read_text(encoding="utf-8"))["apply"] is True
    assert opened == [resolved.config.weekly_mart_root / "index.html"]
    assert "Opened report:" in captured.out


def test_build_weekly_no_open_suppresses_browser(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    resolved = _resolved_config(tmp_path, weekly=True)
    monkeypatch.setattr("itadaki_pipeline.cli.resolve_config", lambda **_: resolved)

    def fake_build(config, *, apply, progress):
        return {
            "apply": apply,
            "mart_path": str(config.weekly_mart_root),
            "candidate_week_count": 0,
            "missing_week_count": 0,
            "stale_week_count": 0,
            "unchanged_week_count": 0,
            "weeks_to_generate": [],
            "generated_weeks": [],
            "index": {"month_report_count": 0, "year_report_count": 0},
        }

    def unexpected_open(_path):
        raise AssertionError("--no-open must not launch a browser")

    monkeypatch.setattr("itadaki_pipeline.cli.build_weekly", fake_build)
    monkeypatch.setattr("itadaki_pipeline.cli._open_report", unexpected_open)

    assert main(["build-weekly", "--no-open"]) == 0
    captured = capsys.readouterr()
    assert "Browser opening suppressed by --no-open." in captured.out
    assert f"Report: {resolved.config.weekly_mart_root / 'index.html'}" in captured.out


def test_build_weekly_browser_failure_warns_without_failing_build(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    resolved = _resolved_config(tmp_path, weekly=True)
    monkeypatch.setattr("itadaki_pipeline.cli.resolve_config", lambda **_: resolved)

    def fake_build(config, *, apply, progress):
        return {
            "apply": apply,
            "mart_path": str(config.weekly_mart_root),
            "candidate_week_count": 0,
            "missing_week_count": 0,
            "stale_week_count": 0,
            "unchanged_week_count": 0,
            "weeks_to_generate": [],
            "generated_weeks": [],
            "index": {"month_report_count": 0, "year_report_count": 0},
        }

    monkeypatch.setattr("itadaki_pipeline.cli.build_weekly", fake_build)
    monkeypatch.setattr(
        "itadaki_pipeline.cli._open_report",
        lambda _path: False,
    )

    assert main(["build-weekly"]) == 0
    captured = capsys.readouterr()
    assert "[SUCCESS] build-weekly completed." in captured.out
    assert "[WARNING] Could not open the report" in captured.err


def test_build_weekly_dry_run_does_not_create_logs_or_result_reports(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    resolved = _resolved_config(tmp_path, weekly=True)
    config = resolved.config
    monkeypatch.setattr("itadaki_pipeline.cli.resolve_config", lambda **_: resolved)

    def fake_build(config, *, apply, progress):
        assert apply is False
        return {
            "apply": apply,
            "mart_path": str(config.weekly_mart_root),
            "candidate_week_count": 1,
            "missing_week_count": 0,
            "stale_week_count": 1,
            "unchanged_week_count": 0,
            "weeks_to_generate": [
                {"week_id": "2026-W30", "status": "stale", "reason": "input changed"}
            ],
            "generated_weeks": [],
            "index": {"month_report_count": 1, "year_report_count": 1},
        }

    monkeypatch.setattr("itadaki_pipeline.cli.build_weekly", fake_build)
    monkeypatch.setattr(
        "itadaki_pipeline.cli._open_report",
        lambda _path: (_ for _ in ()).throw(
            AssertionError("dry-run must not launch a browser")
        ),
    )

    assert main(["build-weekly", "--dry-run"]) == 0
    captured = capsys.readouterr()
    assert "dry-run completed" in captured.out
    assert "No persistent files were changed" in captured.out
    assert "0 create, 1 replace, 0 update, 0 delete, 0 skip" in captured.out
    assert "Would stale: 2026-W30 (input changed)" in captured.out
    assert "Result JSON:" not in captured.out
    assert "Log:" not in captured.out
    assert not config.log_dir.exists()
