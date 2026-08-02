import datetime as dt
import json
from pathlib import Path

from itadaki_pipeline.cli import _parser, main
from itadaki_pipeline.config import PipelineConfig, ResolvedConfig, SourceConfig
from itadaki_pipeline.paths import user_paths


def test_config_option_is_accepted_before_or_after_pipeline_command() -> None:
    before = _parser().parse_args(["--config", "before.yaml", "plan"])
    after = _parser().parse_args(["plan", "--config", "after.yaml"])

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


def test_config_show_command() -> None:
    args = _parser().parse_args(["config", "show"])

    assert args.command == "config"
    assert args.config_command == "show"


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
                "sources:",
                "  - name: current",
                "    device_id: Current PC",
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


def test_run_alias_warns_and_calls_canonical_ingest(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    source = SourceConfig(
        name="current",
        device_id="Example PC",
        rec_dir=tmp_path / "source",
        archive_root=tmp_path / "archive",
        modes=("ingest",),
        delete_after_success=False,
    )
    config = PipelineConfig(
        config_path=tmp_path / "config.yaml",
        timezone_name="Asia/Tokyo",
        timezone=dt.timezone(dt.timedelta(hours=9), name="Asia/Tokyo"),
        processed_data_root=tmp_path / "processed",
        log_dir=tmp_path / "logs",
        sources=(source,),
    )
    resolved = ResolvedConfig(
        config=config,
        sources=(str(config.config_path),),
        paths=user_paths(),
    )
    called: dict[str, object] = {}

    monkeypatch.setattr("itadaki_pipeline.cli.resolve_config", lambda **_: resolved)

    def fake_run(config, mode, *, apply):
        called.update(mode=mode, apply=apply)
        return [], []

    monkeypatch.setattr("itadaki_pipeline.cli.run_pipeline", fake_run)

    assert main(["run"]) == 0
    captured = capsys.readouterr()
    assert "deprecated" in captured.err
    assert called == {"mode": "ingest", "apply": False}
