from __future__ import annotations

from pathlib import Path

import pytest

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
                "processed_data_path: global-processed-data",
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
    cwd_config.write_text(
        "processed_data_path: cwd-processed-data\n",
        encoding="utf-8",
    )
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

    assert resolved.config.processed_data_root == tmp_path / "cwd-processed-data"
    assert resolved.config.sources[0].rec_dir == tmp_path / "source"
    assert resolved.config.sources[0].archive_root == tmp_path / "destination"
    assert resolved.sources == (
        str(global_config),
        str(cwd_config),
        str(explicit),
    )
    assert resolved.paths == user_paths()


def test_non_yaml_config_is_rejected(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "itadaki_pipeline.config.global_config_path",
        lambda: tmp_path / "missing-global.yaml",
    )
    explicit = tmp_path / "pipeline.toml"
    explicit.write_text(
        'processed_data_path = "processed-data"\n',
        encoding="utf-8",
    )

    try:
        resolve_config(cwd=tmp_path, explicit_config=explicit)
    except ValueError as exc:
        assert "Config must be a YAML file" in str(exc)
    else:
        raise AssertionError("TOML configuration was unexpectedly accepted")


def test_processed_data_path_is_required(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "itadaki_pipeline.config.global_config_path",
        lambda: tmp_path / "missing-global.yaml",
    )
    explicit = tmp_path / "explicit.yaml"
    explicit.write_text(
        "\n".join(
            [
                "sources:",
                "  - name: current",
                "    device_id: Current PC",
                "    source_path: source",
                "    destination_path: destination",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="processed_data_path is required"):
        resolve_config(cwd=tmp_path, explicit_config=explicit)


def test_bronze_path_is_rejected(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "itadaki_pipeline.config.global_config_path",
        lambda: tmp_path / "missing-global.yaml",
    )
    explicit = tmp_path / "explicit.yaml"
    explicit.write_text(
        "\n".join(
            [
                "bronze_path: old-output",
                "sources:",
                "  - name: current",
                "    device_id: Current PC",
                "    source_path: source",
                "    destination_path: destination",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unknown keys.*bronze_path"):
        resolve_config(cwd=tmp_path, explicit_config=explicit)
