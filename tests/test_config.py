from __future__ import annotations

from pathlib import Path

import pytest

from itadaki_pipeline.config import initialize_user_config, resolve_config
from itadaki_pipeline.paths import user_paths


def test_config_init_creates_user_global_config(tmp_path: Path) -> None:
    destination = tmp_path / ".tkn" / "itadaki_data_pipeline" / "config.yaml"

    path, status = initialize_user_config(target=destination)

    assert path == destination.resolve()
    assert status == "created"
    assert "default_profile: current-pc" in path.read_text(encoding="utf-8")


def test_config_init_leaves_identical_config_unchanged(tmp_path: Path) -> None:
    destination = tmp_path / "config.yaml"
    path, first_status = initialize_user_config(target=destination)
    original_stat = path.stat()

    same_path, second_status = initialize_user_config(target=destination)

    assert first_status == "created"
    assert same_path == path
    assert second_status == "unchanged"
    assert path.stat().st_mtime_ns == original_stat.st_mtime_ns


def test_config_init_protects_edited_config(tmp_path: Path) -> None:
    destination = tmp_path / "config.yaml"
    destination.write_text("user: edited\n", encoding="utf-8")

    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        initialize_user_config(target=destination)

    assert destination.read_text(encoding="utf-8") == "user: edited\n"


def test_config_precedence_profile_selection_and_cwd_relative_paths(
    tmp_path: Path,
    monkeypatch,
) -> None:
    global_config = tmp_path / "global" / "config.yaml"
    global_config.parent.mkdir()
    global_config.write_text(
        "\n".join(
            [
                "default_profile: global",
                "processed_data_path: global-processed-data",
                "weekly_mart_path: global-weekly-mart",
                "profiles:",
                "  - name: global",
                "    device_id: Global PC",
                "    timezone: Asia/Tokyo",
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
                "default_profile: explicit",
                "profiles:",
                "  - name: explicit",
                "    device_id: Explicit PC",
                "    timezone: Asia/Tokyo",
                "    source_path: source",
                "    destination_path: destination",
            ]
        ),
        encoding="utf-8",
    )

    resolved = resolve_config(cwd=tmp_path, explicit_config=explicit)

    assert resolved.config.processed_data_root == tmp_path / "cwd-processed-data"
    assert resolved.config.weekly_mart_root == tmp_path / "global-weekly-mart"
    assert resolved.config.profile.itadaki_root == tmp_path / "source"
    assert resolved.config.profile.rec_dir == tmp_path / "source" / "Rec"
    assert resolved.config.profile.archive_root == tmp_path / "destination"
    assert resolved.config.selected_profile_name == "explicit"
    assert resolved.config_sources == (
        str(global_config),
        str(cwd_config),
        str(explicit),
    )
    assert resolved.paths == user_paths()
    assert resolved.winning_sources["processed_data_path"] == str(cwd_config)
    assert resolved.winning_sources["weekly_mart_path"] == str(global_config)
    assert resolved.winning_sources["default_profile"] == str(explicit)
    assert resolved.winning_sources["selected_profile"] == str(explicit)
    assert resolved.winning_sources["profiles[0].source_path"] == str(explicit)
    assert resolved.winning_sources["profiles[0].delete_after_success"] == "built-in"
    assert resolved.winning_sources["log_path"] == "built-in"
    selected = resolve_config(cwd=tmp_path, explicit_config=explicit, profile_name="explicit")
    assert selected.winning_sources["selected_profile"] == "CLI"
    assert selected.winning_sources["default_profile"] == str(explicit)


def test_explicit_profiles_replace_legacy_sources_from_lower_layer(
    tmp_path: Path,
    monkeypatch,
) -> None:
    global_config = tmp_path / "global.yaml"
    global_config.write_text(
        "\n".join(
            [
                "timezone: Asia/Tokyo",
                "processed_data_path: old-processed",
                "sources:",
                "  - name: old",
                "    device_id: Old PC",
                "    source_path: old-source",
                "    destination_path: old-destination",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "itadaki_pipeline.config.global_config_path",
        lambda: global_config,
    )
    explicit = tmp_path / "explicit.yaml"
    explicit.write_text(
        "\n".join(
            [
                "default_profile: current",
                "processed_data_path: processed",
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

    resolved = resolve_config(cwd=tmp_path, explicit_config=explicit)

    assert resolved.config.selected_profile_name == "current"
    assert resolved.config.profile.device_id == "Current PC"


def test_removed_sources_key_is_rejected(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "itadaki_pipeline.config.global_config_path",
        lambda: tmp_path / "missing-global.yaml",
    )
    config = tmp_path / "config.yaml"
    config.write_text(
        "\n".join(
            [
                "processed_data_path: processed-data",
                "sources:",
                "  - name: current",
                "    device_id: Example PC",
                "    source_path: source",
                "    destination_path: destination",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="sources.*no longer supported"):
        resolve_config(cwd=tmp_path, explicit_config=config)


def test_unknown_profile_is_rejected(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "itadaki_pipeline.config.global_config_path",
        lambda: tmp_path / "missing-global.yaml",
    )
    config = tmp_path / "config.yaml"
    config.write_text(
        "\n".join(
            [
                "default_profile: current",
                "processed_data_path: processed-data",
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

    with pytest.raises(ValueError, match="Unknown profile 'missing'.*current"):
        resolve_config(
            cwd=tmp_path,
            explicit_config=config,
            profile_name="missing",
        )


def test_removed_modes_key_is_rejected(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "itadaki_pipeline.config.global_config_path",
        lambda: tmp_path / "missing-global.yaml",
    )
    config = tmp_path / "config.yaml"
    config.write_text(
        "\n".join(
            [
                "default_profile: current",
                "processed_data_path: processed-data",
                "profiles:",
                "  - name: current",
                "    device_id: Current PC",
                "    source_path: source",
                "    destination_path: destination",
                "    modes: [ingest]",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="modes.*no longer supported"):
        resolve_config(cwd=tmp_path, explicit_config=config)


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
                "default_profile: current",
                "profiles:",
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


def test_source_path_rejects_rec_subfolder(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "itadaki_pipeline.config.global_config_path",
        lambda: tmp_path / "missing-global.yaml",
    )
    explicit = tmp_path / "explicit.yaml"
    explicit.write_text(
        "\n".join(
            [
                "default_profile: current",
                "processed_data_path: processed-data",
                "profiles:",
                "  - name: current",
                "    device_id: Current PC",
                "    source_path: C:/path/to/Itadaki/Rec",
                "    destination_path: destination",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Itadaki folder, not its Rec subfolder"):
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
                "default_profile: current",
                "processed_data_path: processed-data",
                "profiles:",
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


def test_profile_replacement_resets_omitted_defaults_and_origins(tmp_path: Path, monkeypatch):
    global_config, _ = initialize_user_config(target=tmp_path / "global.yaml")
    monkeypatch.setattr("itadaki_pipeline.config.global_config_path", lambda: global_config)
    override = tmp_path / ".tkn" / "config.yaml"
    override.parent.mkdir()
    override.write_text(
        "default_profile: replacement\n"
        "weekly_mart_path: null\n"
        "log_path: custom-logs\n"
        "profiles:\n"
        "  - name: replacement\n"
        "    device_id: Replacement PC\n"
        "    source_path: source\n"
        "    destination_path: destination\n",
        encoding="utf-8",
    )

    resolved = resolve_config(cwd=tmp_path)
    assert len(resolved.config.profiles) == 1
    assert resolved.config.profile.timezone_name == "Asia/Tokyo"
    assert resolved.config.profile.delete_after_success is False
    assert resolved.config.weekly_mart_root is None
    assert resolved.config.log_dir == tmp_path / "custom-logs"
    assert resolved.winning_sources["profiles[0].name"] == str(override)
    assert resolved.winning_sources["profiles[0].timezone"] == "built-in"
    assert resolved.winning_sources["profiles[0].delete_after_success"] == "built-in"
    assert resolved.winning_sources["weekly_mart_path"] == str(override)
    assert resolved.winning_sources["log_path"] == str(override)
    assert not any(key.startswith("profiles[1]") for key in resolved.winning_sources)
