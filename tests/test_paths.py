from __future__ import annotations

import tempfile
from pathlib import Path

from itadaki_pipeline.paths import (
    runtime_temp_base,
    runtime_temp_directory,
    user_paths,
)


def test_user_paths_are_platform_neutral(tmp_path: Path) -> None:
    paths = user_paths(tmp_path)

    assert paths.app_root == tmp_path / ".tkn" / "itadaki_data_pipeline"
    assert paths.config_file == paths.app_root / "config.yaml"
    assert paths.data_dir == paths.app_root / "data"
    assert paths.state_dir == paths.app_root / "state"
    assert paths.cache_dir == tmp_path / ".cache" / "itadaki_data_pipeline"


def test_runtime_temp_directory_uses_python_temp_root() -> None:
    expected_base = Path(tempfile.gettempdir()).resolve()

    with runtime_temp_directory() as directory:
        resolved = directory.resolve()
        assert directory.is_dir()
        assert expected_base == resolved.parent
        assert directory.name.startswith("itadaki_data_pipeline-")

    assert not directory.exists()
    assert runtime_temp_base().resolve() == expected_base
