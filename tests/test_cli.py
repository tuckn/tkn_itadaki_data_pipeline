from pathlib import Path

from itadaki_pipeline.cli import _parser


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
