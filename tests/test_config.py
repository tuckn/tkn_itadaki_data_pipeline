from __future__ import annotations

from pathlib import Path

from itadaki_pipeline.config import resolve_config
from itadaki_pipeline.paths import user_paths


def test_config_precedence_and_cwd_relative_paths(
    tmp_path: Path,
    monkeypatch,
) -> None:
    global_config = tmp_path / "global" / "config.yaml"
    global_config.parent.mkdir()
    global_config.write_text(
        "\n".join(
            [
                "timezone: Asia/Tokyo",
                "bronze_path: global-bronze",
                "sources:",
                "  - name: global",
                "    device_id: Global PC",
                "    source_path: global-source",
                "    destination_path: global-destination",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "itadaki_pipeline.config.global_config_path",
        lambda: global_config,
    )

    cwd_config = tmp_path / ".tkn" / "config.yaml"
    cwd_config.parent.mkdir()
    cwd_config.write_text("bronze_path: cwd-bronze\n", encoding="utf-8")
    explicit = tmp_path / "explicit.yaml"
    explicit.write_text(
        "\n".join(
            [
                "sources:",
                "  - name: explicit",
                "    device_id: Explicit PC",
                "    source_path: source",
                "    destination_path: destination",
            ]
        ),
        encoding="utf-8",
    )

    resolved = resolve_config(cwd=tmp_path, explicit_config=explicit)

    assert resolved.config.bronze_root == tmp_path / "cwd-bronze"
    assert resolved.config.sources[0].rec_dir == tmp_path / "source"
    assert resolved.config.sources[0].archive_root == tmp_path / "destination"
    assert resolved.sources == (
        str(global_config),
        str(cwd_config),
        str(explicit),
    )
    assert resolved.paths == user_paths()


def test_legacy_toml_keys_remain_supported(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "itadaki_pipeline.config.global_config_path",
        lambda: tmp_path / "missing-global.yaml",
    )
    explicit = tmp_path / "pipeline.toml"
    explicit.write_text(
        "\n".join(
            [
                'bronze_root = "bronze"',
                "[[sources]]",
                'name = "legacy"',
                'device_id = "Legacy PC"',
                'rec_dir = "source"',
                'archive_root = "destination"',
            ]
        ),
        encoding="utf-8",
    )

    resolved = resolve_config(cwd=tmp_path, explicit_config=explicit)

    assert resolved.config.bronze_root == tmp_path / "bronze"
    assert resolved.config.sources[0].rec_dir == tmp_path / "source"
    assert resolved.config.log_dir == user_paths().state_dir / "logs"
