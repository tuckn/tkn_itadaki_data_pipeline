"""Cross-platform application storage paths."""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

APP_NAME = "itadaki_data_pipeline"
CONFIG_FILENAME = "config.yaml"


@dataclass(frozen=True)
class AppPaths:
    app_root: Path
    config_file: Path
    data_dir: Path
    state_dir: Path
    cache_dir: Path


def user_paths(home: Path | None = None) -> AppPaths:
    """Return the persistent user paths without OS-specific branching."""
    user_home = (home or Path.home()).expanduser()
    app_root = user_home / ".tkn" / APP_NAME
    return AppPaths(
        app_root=app_root,
        config_file=app_root / CONFIG_FILENAME,
        data_dir=app_root / "data",
        state_dir=app_root / "state",
        cache_dir=user_home / ".cache" / APP_NAME,
    )


def runtime_temp_base() -> Path:
    """Return the platform temp root selected by Python."""
    return Path(tempfile.gettempdir())


@contextmanager
def runtime_temp_directory() -> Iterator[Path]:
    """Create runtime-only scratch under %TMP%, TMPDIR, or the platform default."""
    with tempfile.TemporaryDirectory(prefix=f"{APP_NAME}-") as directory:
        yield Path(directory)
