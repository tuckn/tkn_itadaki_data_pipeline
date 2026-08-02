"""Configuration discovery, layering, and path normalization."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .paths import CONFIG_FILENAME, AppPaths, runtime_temp_base, user_paths


@dataclass(frozen=True)
class SourceConfig:
    name: str
    device_id: str
    rec_dir: Path
    archive_root: Path
    modes: tuple[str, ...]
    delete_after_success: bool
    legacy_run_mode: bool = False


@dataclass(frozen=True)
class PipelineConfig:
    config_path: Path
    timezone_name: str
    timezone: dt.tzinfo
    processed_data_root: Path
    log_dir: Path
    sources: tuple[SourceConfig, ...]
    weekly_mart_root: Path | None = None


@dataclass(frozen=True)
class ResolvedConfig:
    config: PipelineConfig
    sources: tuple[str, ...]
    paths: AppPaths


def global_config_path() -> Path:
    return user_paths().config_file


def cwd_config_path(cwd: Path) -> Path:
    return cwd / ".tkn" / CONFIG_FILENAME


def _path(value: str | Path, base: Path) -> Path:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = base / candidate
    return candidate.resolve()


def _timezone(name: str) -> dt.tzinfo:
    if name != "Asia/Tokyo":
        raise ValueError('This pipeline currently supports only timezone: "Asia/Tokyo"')
    return dt.timezone(dt.timedelta(hours=9), name=name)


def _load_mapping(path: Path) -> dict[str, Any]:
    if path.suffix.lower() not in {".yaml", ".yml"}:
        raise ValueError(f"Config must be a YAML file (.yaml or .yml): {path}")
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ValueError(f"Cannot read config {path}: {exc}") from exc

    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"Config must be a mapping: {path}")
    return dict(value)


def _only_known_keys(
    values: dict[str, Any],
    allowed: set[str],
    *,
    location: str,
) -> None:
    unknown = set(values) - allowed
    if unknown:
        raise ValueError(f"{location}: unknown keys: {sorted(unknown)}")


def _build_config(
    raw: dict[str, Any],
    *,
    cwd: Path,
    config_path: Path,
    paths: AppPaths,
) -> PipelineConfig:
    _only_known_keys(
        raw,
        {
            "timezone",
            "processed_data_path",
            "weekly_mart_path",
            "log_path",
            "sources",
        },
        location=str(config_path),
    )

    timezone_name = str(raw.get("timezone", "Asia/Tokyo"))
    processed_data_root_value = raw.get("processed_data_path")
    if not processed_data_root_value:
        raise ValueError("processed_data_path is required")

    source_items = raw.get("sources", [])
    if not isinstance(source_items, list) or not source_items:
        raise ValueError("At least one sources entry is required")

    sources: list[SourceConfig] = []
    names: set[str] = set()
    for item in source_items:
        if not isinstance(item, dict):
            raise ValueError("Each sources entry must be a mapping")
        _only_known_keys(
            item,
            {
                "name",
                "device_id",
                "source_path",
                "destination_path",
                "modes",
                "delete_after_success",
            },
            location=f"{config_path}: sources entry",
        )
        name = str(item["name"])
        if name in names:
            raise ValueError(f"Duplicate source name: {name}")
        names.add(name)
        raw_modes = tuple(
            str(mode) for mode in item.get("modes", ["backfill", "ingest"])
        )
        unsupported = set(raw_modes) - {"backfill", "ingest", "run"}
        if unsupported:
            raise ValueError(f"{name}: unsupported modes: {sorted(unsupported)}")
        legacy_run_mode = "run" in raw_modes
        modes = tuple(dict.fromkeys("ingest" if mode == "run" else mode for mode in raw_modes))

        source_path = item.get("source_path")
        destination_path = item.get("destination_path")
        if not source_path:
            raise ValueError(f"{name}: source_path is required")
        if not destination_path:
            raise ValueError(f"{name}: destination_path is required")

        sources.append(
            SourceConfig(
                name=name,
                device_id=str(item["device_id"]),
                rec_dir=_path(str(source_path), cwd),
                archive_root=_path(str(destination_path), cwd),
                modes=modes,
                delete_after_success=bool(item.get("delete_after_success", False)),
                legacy_run_mode=legacy_run_mode,
            )
        )

    weekly_mart_value = raw.get("weekly_mart_path")

    return PipelineConfig(
        config_path=config_path,
        timezone_name=timezone_name,
        timezone=_timezone(timezone_name),
        processed_data_root=_path(str(processed_data_root_value), cwd),
        log_dir=_path(
            str(raw.get("log_path", paths.state_dir / "logs")),
            cwd,
        ),
        sources=tuple(sources),
        weekly_mart_root=(
            _path(str(weekly_mart_value), cwd) if weekly_mart_value else None
        ),
    )


def resolve_config(
    *,
    cwd: Path | None = None,
    explicit_config: Path | None = None,
) -> ResolvedConfig:
    """Resolve global, CWD, and explicit configuration in that order."""
    current = (cwd or Path.cwd()).resolve()
    paths = user_paths()
    candidates = [global_config_path(), cwd_config_path(current)]
    if explicit_config is not None:
        candidates.append(explicit_config.expanduser().resolve())

    values: dict[str, Any] = {}
    sources: list[str] = []
    last_path: Path | None = None
    for path in candidates:
        if path.is_file():
            values.update(_load_mapping(path))
            sources.append(str(path))
            last_path = path

    if last_path is None:
        searched = ", ".join(str(path) for path in candidates)
        raise FileNotFoundError(f"No configuration file found. Searched: {searched}")

    return ResolvedConfig(
        config=_build_config(
            values,
            cwd=current,
            config_path=last_path,
            paths=paths,
        ),
        sources=tuple(sources),
        paths=paths,
    )

def public_config(
    config: PipelineConfig,
    paths: AppPaths | None = None,
) -> dict[str, Any]:
    storage = paths or user_paths()
    return {
        "timezone": config.timezone_name,
        "processed_data_path": str(config.processed_data_root),
        "weekly_mart_path": (
            str(config.weekly_mart_root) if config.weekly_mart_root else None
        ),
        "log_path": str(config.log_dir),
        "storage": {
            "app_root": str(storage.app_root),
            "config_path": str(storage.config_file),
            "data_path": str(storage.data_dir),
            "state_path": str(storage.state_dir),
            "cache_path": str(storage.cache_dir),
            "runtime_temp_base": str(runtime_temp_base()),
        },
        "sources": [
            {
                "name": source.name,
                "device_id": source.device_id,
                "source_path": str(source.rec_dir),
                "destination_path": str(source.archive_root),
                "modes": list(source.modes),
                "delete_after_success": source.delete_after_success,
            }
            for source in config.sources
        ],
    }
