import json
from pathlib import Path

from itadaki_pipeline.cli import _parser, main


def test_config_option_is_accepted_before_or_after_pipeline_command() -> None:
    before = _parser().parse_args(["--config", "before.yaml", "plan"])
    after = _parser().parse_args(["plan", "--config", "after.yaml"])

    assert before.config == Path("before.yaml")
    assert before.command_config is None
    assert after.config is None
    assert after.command_config == Path("after.yaml")


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
