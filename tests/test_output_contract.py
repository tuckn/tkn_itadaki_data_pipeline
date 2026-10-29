from __future__ import annotations

import datetime as dt
import json
import logging
import os
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from itadaki_pipeline import cli
from itadaki_pipeline.config import PipelineConfig, ProfileConfig, ResolvedConfig
from itadaki_pipeline.config_display import config_lines
from itadaki_pipeline.diagnostics import annotate
from itadaki_pipeline.output import emit, weekly_result
from itadaki_pipeline.paths import user_paths
from itadaki_pipeline.pipeline import run_pipeline


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("itadaki_pipeline.config.runtime_temp_base", lambda: tmp_path / "temp")
    source = tmp_path / "source 日本語"
    shutil.copytree(Path(__file__).parent / "fixtures/itadaki_rec", source / "Rec")
    profile = ProfileConfig(
        name="fixture",
        device_id="Example PC",
        timezone_name="Asia/Tokyo",
        timezone=dt.timezone(dt.timedelta(hours=9)),
        itadaki_root=source,
        archive_root=tmp_path / "archive",
        delete_after_success=False,
    )
    config = PipelineConfig(
        config_path=tmp_path / "config.yaml",
        processed_data_root=tmp_path / "processed",
        log_dir=tmp_path / "logs",
        default_profile_name="fixture",
        selected_profile_name="fixture",
        profiles=(profile,),
        weekly_mart_root=tmp_path / "mart",
    )
    monkeypatch.setattr(cli, "resolve_config", lambda **_: ResolvedConfig(config, (), user_paths()))
    monkeypatch.setattr(cli, "_open_report", lambda _: pytest.fail("Tests must not open browsers"))
    return config


def snapshot(root):
    return {
        str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mtime_ns) if p.is_file() else None
        for p in root.rglob("*")
    }


def read_result(output, json_mode):
    assert "\x1b" not in output
    if json_mode:
        result = json.loads(output)
        assert isinstance(result, dict)
        return result
    assert all("=" in line for line in output.splitlines())
    return dict(line.split("=", 1) for line in output.splitlines())


@pytest.mark.parametrize("json_mode", [False, True])
@pytest.mark.parametrize("flags", [[], ["--quiet"], ["--verbose"]])
@pytest.mark.parametrize(
    "command", ["config-init", "config-list", "verify", "ingest", "build-weekly"]
)
def test_all_commands_share_output_and_verbosity(
    isolated, tmp_path, monkeypatch, capsys, command, flags, json_mode
):
    # Prepare a real archive and processed CSVs, using only fixtures.
    run_pipeline(isolated, apply=True, now=dt.datetime(2026, 4, 13, tzinfo=isolated.timezone))
    if command == "config-init":
        args = ["config", "init"]
    elif command == "config-list":
        args = ["config", "list"]
    else:
        args = [command]
        if command in {"ingest", "build-weekly"}:
            args += ["--dry-run"]
    before = snapshot(tmp_path)
    args += flags + (["--json"] if json_mode else [])
    assert cli.main(args) == 0
    captured = capsys.readouterr()
    result = read_result(captured.out, json_mode)
    assert result["status"] == ("created" if command == "config-init" else "success")
    if command != "config-init":
        assert snapshot(tmp_path) == before
    if flags == ["--quiet"]:
        assert captured.err == ""
    if command in {"ingest", "build-weekly"} and flags == ["--verbose"]:
        assert "[DEBUG] Selected profile:" in captured.err
    assert "[INFO]" not in captured.out


@pytest.mark.parametrize("command", ["init", "list"])
@pytest.mark.parametrize("position", [0, 1, 2, 3])
def test_json_survives_each_subcommand_level(isolated, capsys, command, position):
    args = ["config", command, "--quiet"]
    args.insert(position, "--json")
    assert cli.main(args) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["status"] in {"created", "success"}
    assert captured.err == ""


@pytest.mark.parametrize("json_mode", [False, True])
@pytest.mark.parametrize(
    "args",
    [
        [],
        ["config"],
        ["bogus"],
        ["ingest", "--unknown"],
        ["--quiet", "config", "list", "--verbose"],
        ["--config"],
        ["ingest", "--json=wrong"],
    ],
)
def test_argument_errors_use_selected_format(capsys, args, json_mode):
    assert cli.main([*args, *(["--json"] if json_mode else [])]) == 2
    capture = capsys.readouterr()
    result = read_result(capture.out, json_mode)
    assert result["status"] == "failed"
    assert result["stage"] == "arguments"
    assert "[ERROR]" in capture.err


@pytest.mark.parametrize("json_mode", [False, True])
@pytest.mark.parametrize(
    "error,code,status",
    [
        (RuntimeError("runtime failure"), 1, "failed"),
        (PermissionError(13, "denied", r"C:\ExampleUser\対象\input.csv"), 1, "failed"),
        (KeyboardInterrupt(), 130, "interrupted"),
    ],
)
def test_runtime_errors_and_interrupts(
    isolated, monkeypatch, capsys, error, code, status, json_mode
):
    def fail(*_, **__):
        raise error

    monkeypatch.setattr(cli, "run_pipeline", fail)
    assert cli.main(["ingest", "--dry-run", *(["--json"] if json_mode else [])]) == code
    captured = capsys.readouterr()
    result = read_result(captured.out, json_mode)
    assert result["status"] == status
    assert result["dry_run"] is True if json_mode else result["dry_run"] == "true"
    if getattr(error, "filename", None):
        assert (
            result["errors"][0]["path"] if json_mode else result["errors[0].path"]
        ) == error.filename
    assert "[ERROR]" in captured.err


@pytest.mark.parametrize("json_mode", [False, True])
def test_malformed_config_does_not_quote_private_yaml_source(
    tmp_path, monkeypatch, capsys, json_mode
):
    config = tmp_path / "private.yaml"
    config.write_text("private_key: [never-echo-this-private-value\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    args = ["config", "list", "--config", str(config), "--verbose"]
    assert cli.main(args + (["--json"] if json_mode else [])) == 1
    captured = capsys.readouterr()
    result = read_result(captured.out, json_mode)
    assert result["stage"] == "configuration"
    assert (result["errors"][0]["path"] if json_mode else result["errors[0].path"]) == str(config)
    assert "never-echo-this-private-value" not in captured.out + captured.err


@pytest.mark.parametrize("json_mode", [False, True])
@pytest.mark.parametrize("full_flag", [None, "--details", "--full-output"])
def test_verify_details_are_independent_of_format(isolated, capsys, json_mode, full_flag):
    run_pipeline(isolated, apply=True, now=dt.datetime(2026, 4, 13, tzinfo=isolated.timezone))
    args = ["verify"] + ([full_flag] if full_flag else []) + (["--json"] if json_mode else [])
    assert cli.main(args) == 0
    output = capsys.readouterr().out
    result = read_result(output, json_mode)
    if full_flag:
        path = isolated.processed_data_root / "Example PC/InputEvents/2026/04.csv"
        assert (
            result["details"][0]["input_events_path"]
            if json_mode
            else result["details[0].input_events_path"]
        ) == str(path)
    else:
        assert "--details" in result["details_hint"]
        assert result["omitted_detail_count"] == (1 if json_mode else "1")


@pytest.mark.parametrize("json_mode", [False, True])
def test_invalid_record_and_mismatched_csv_identify_exact_file(isolated, capsys, json_mode):
    flags = ["--json"] if json_mode else []
    path = isolated.profile.rec_dir / "Key/20260404.rec"
    original = path.read_bytes()
    path.write_bytes(b"invalid")
    assert cli.main(["ingest", "--dry-run", *flags]) == 1
    result = read_result(capsys.readouterr().out, json_mode)
    assert (result["errors"][0]["path"] if json_mode else result["errors[0].path"]) == str(path)
    path.write_bytes(original)
    run_pipeline(isolated, apply=True, now=dt.datetime(2026, 4, 13, tzinfo=isolated.timezone))
    path = isolated.processed_data_root / "Example PC/InputEvents/2026/04.csv"
    path.write_text("wrong header\n", encoding="utf-8")
    assert cli.main(["verify", *flags]) == 1
    result = read_result(capsys.readouterr().out, json_mode)
    assert (result["errors"][0]["path"] if json_mode else result["errors[0].path"]) == str(path)


@pytest.mark.parametrize("json_mode", [False, True])
def test_partial_cleanup_failure_preserves_saved_manifest_and_target_paths(
    isolated,
    monkeypatch,
    capsys,
    json_mode,
):
    config = replace(isolated, profiles=(replace(isolated.profile, delete_after_success=True),))
    monkeypatch.setattr(cli, "resolve_config", lambda **_: ResolvedConfig(config, (), user_paths()))
    original = Path.unlink
    blocked = config.profile.rec_dir / "Key/20260404.rec"

    def unlink(path, *args, **kwargs):
        if path == blocked:
            raise PermissionError(13, "fixture denial", str(path))
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink)
    assert cli.main(["ingest", *(["--json"] if json_mode else [])]) == 1
    result = read_result(capsys.readouterr().out, json_mode)
    if json_mode:
        assert result["error_count"] == 1
        assert result["errors"][0]["path"] == str(blocked)
        assert result["completed"]["copied_files"] == 4
        manifest_path = result["targets"]["manifest_path"]
    else:
        assert result["errors[0].path"] == str(blocked)
        assert result["completed.copied_files"] == "4"
        manifest_path = result["targets.manifest_path"]
    saved = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    assert saved["cleanup"]["status"] == "incomplete"
    assert isinstance(saved["cleanup"]["errors"][0], str)
    assert "errors" not in saved  # CLI fields were not added to persisted data.


@pytest.mark.parametrize("json_mode", [False, True])
@pytest.mark.parametrize("full", [False, True])
def test_build_output_and_saved_payload_are_independent(isolated, capsys, json_mode, full):
    run_pipeline(isolated, apply=True, now=dt.datetime(2026, 4, 13, tzinfo=isolated.timezone))
    args = ["build-weekly", "--no-open"] + (["--json"] if json_mode else [])
    if full:
        args += ["--full-output"]
    assert cli.main(args) == 0
    result = read_result(capsys.readouterr().out, json_mode)
    saved = json.loads(Path(result["result_path"]).read_text(encoding="utf-8"))
    assert saved["generated_weeks"] == ["2026-W14", "2026-W15"]
    assert "status" not in saved and "browser_status" not in saved
    assert "path" not in saved["weeks_to_generate"][0]
    if json_mode:
        assert ("generated_weeks" in result) == full
    else:
        assert ("generated_weeks[0]" in result) == full
    assert result["browser_status"] == "suppressed"


@pytest.mark.parametrize("json_mode", [False, True])
def test_weekly_failure_keeps_completed_weeks(isolated, monkeypatch, capsys, json_mode):
    import itadaki_pipeline.weekly as weekly

    run_pipeline(isolated, apply=True, now=dt.datetime(2026, 4, 13, tzinfo=isolated.timezone))
    original = weekly._write_week

    def write(plan, *args):
        if plan.week.week_id == "2026-W15":
            raise PermissionError(13, "fixture denial", str(plan.root / "report.html"))
        return original(plan, *args)

    monkeypatch.setattr(weekly, "_write_week", write)
    assert cli.main(["build-weekly", "--no-open", *(["--json"] if json_mode else [])]) == 1
    result = read_result(capsys.readouterr().out, json_mode)
    assert (
        result["completed"]["generated_weeks"]
        if json_mode
        else [result["completed.generated_weeks[0]"]]
    ) == ["2026-W14"]
    assert (isolated.weekly_mart_root / "weeks/2026-W14/report.html").is_file()


@pytest.mark.parametrize("tty", [False, True])
@pytest.mark.parametrize("json_mode", [False, True])
def test_tty_does_not_select_format(monkeypatch, capsys, tty, json_mode):
    monkeypatch.setattr(sys.stdout, "isatty", lambda: tty)
    path = "C:\\ExampleUser\\" + "長い名前 " * 100 + "\\file.csv"
    emit({"path": path}, json_output=json_mode)
    output = capsys.readouterr().out
    assert read_result(output, json_mode)["path"] == path


def test_copyable_paths_control_characters_and_empty_containers():
    assert config_lines(
        {
            "path": Path("日本語=example"),
            "empty": [],
            "map": {},
            "value": "a\x1b\x9b\n\t",
            "flag": False,
            "missing": None,
        }
    ) == [
        "path=日本語=example",
        "empty=[]",
        "map={}",
        "value=a\\u001b\\u009b\\n\\t",
        "flag=false",
        "missing=null",
    ]


def test_full_output_does_not_modify_api_payload():
    payload = {
        "mart_path": r"C:\ExampleUser\mart",
        "apply": True,
        "generated_weeks": ["2026-W14"],
        "weeks_to_generate": [{"week_id": "2026-W14", "reason": "missing"}],
    }
    original = json.loads(json.dumps(payload))
    weekly_result(payload, full=False)
    weekly_result(payload, full=True)
    assert payload == original


def test_parser_help_version_and_end_of_options(capsys):
    for args in [["--json", "--help"], ["--json", "--version"], ["config", "list", "--help"]]:
        with pytest.raises(SystemExit) as error:
            cli.main(args)
        assert error.value.code == 0
        assert not capsys.readouterr().out.startswith("{")
    assert cli.main(["ingest", "--", "--json"]) == 2
    assert capsys.readouterr().out.startswith("status=failed")


def test_subprocess_pipe_and_file_redirection(tmp_path):
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    command = [sys.executable, "-m", "itadaki_pipeline", "config"]
    piped = subprocess.run(
        [*command, "--json"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert piped.returncode == 2
    assert json.loads(piped.stdout)["status"] == "failed"
    target = tmp_path / "stdout.txt"
    with target.open("w", encoding="utf-8") as stream:
        redirected = subprocess.run(
            command,
            cwd=tmp_path,
            env=env,
            stdout=stream,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
    assert redirected.returncode == 2
    assert target.read_text(encoding="utf-8").startswith("status=failed")
    assert "[ERROR]" in redirected.stderr


def test_file_handler_closed_after_failure(isolated, monkeypatch, capsys):
    def fail(*_, **__):
        raise annotate(OSError("fixture failure"), path=isolated.profile.itadaki_root)

    monkeypatch.setattr(cli, "run_pipeline", fail)
    assert cli.main(["ingest"]) == 1
    capsys.readouterr()
    assert not any(isinstance(handler, logging.FileHandler) for handler in cli.LOG.handlers)


@pytest.mark.parametrize("json_mode", [False, True])
@pytest.mark.parametrize("full", [False, True])
def test_ingest_detail_level(isolated, capsys, json_mode, full):
    arguments = ["ingest", "--dry-run"] + (["--json"] if json_mode else [])
    if full:
        arguments += ["--full-output"]
    assert cli.main(arguments) == 0
    result = read_result(capsys.readouterr().out, json_mode)
    if json_mode:
        assert ("source_files" in result["plans"][0]) == full
        assert result["counts"]["source_files"] == 4
        if full:
            assert len(result["plans"][0]["source_files"]) == 4
    else:
        assert ("plans[0].source_files[0].path" in result) == full
        assert result["counts.source_files"] == "4"


@pytest.mark.parametrize("json_mode", [False, True])
def test_saved_result_failure_reports_already_generated_weeks(
    isolated,
    monkeypatch,
    capsys,
    json_mode,
):
    run_pipeline(isolated, apply=True, now=dt.datetime(2026, 4, 13, tzinfo=isolated.timezone))

    def fail(*_):
        raise OSError("Could not save result")

    monkeypatch.setattr(cli, "_write_result_json", fail)
    assert cli.main(["build-weekly", "--no-open", *(["--json"] if json_mode else [])]) == 1
    result = read_result(capsys.readouterr().out, json_mode)
    assert result["stage"] == "save result"
    assert (
        result["completed"]["generated_week_count"]
        if json_mode
        else result["completed.generated_week_count"]
    ) == (2 if json_mode else "2")
    assert (isolated.weekly_mart_root / "index.html").is_file()


@pytest.mark.parametrize("json_mode", [False, True])
def test_config_validation_targets_the_config_file(tmp_path, monkeypatch, capsys, json_mode):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    config = tmp_path / "config.yaml"
    config.write_text("unknown_setting: true\n", encoding="utf-8")
    assert (
        cli.main(["--config", str(config), "config", "list", *(["--json"] if json_mode else [])])
        == 1
    )
    result = read_result(capsys.readouterr().out, json_mode)
    assert (result["errors"][0]["path"] if json_mode else result["errors[0].path"]) == str(config)
