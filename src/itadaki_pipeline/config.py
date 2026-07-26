"""Configuration discovery, layering, and path normalization."""

from __future__ import annotations

import datetime as dt
import tomllib
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


@dataclass(frozen=True)
class PipelineConfig:
    config_path: Path
    timezone_name: str
    timezone: dt.tzinfo
    bronze_root: Path
    log_dir: Path
    sources: tuple[SourceConfig, ...]


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
    try:
        if path.suffix.lower() == ".toml":
            with path.open("rb") as handle:
                value = tomllib.load(handle)
        else:
            value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError, yaml.YAMLError) as exc:
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
            "bronze_path",
            "bronze_root",
            "log_path",
            "log_dir",
            "sources",
        },
        location=str(config_path),
    )

    timezone_name = str(raw.get("timezone", "Asia/Tokyo"))
    bronze_root_value = raw.get("bronze_path", raw.get("bronze_root"))
    if not bronze_root_value:
        raise ValueError("bronze_path is required")

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
                "rec_dir",
                "archive_root",
                "modes",
                "delete_after_success",
            },
            location=f"{config_path}: sources entry",
        )
        name = str(item["name"])
        if name in names:
            raise ValueError(f"Duplicate source name: {name}")
        names.add(name)
        modes = tuple(str(mode) for mode in item.get("modes", ["backfill", "run"]))
        unsupported = set(modes) - {"backfill", "run"}
        if unsupported:
            raise ValueError(f"{name}: unsupported modes: {sorted(unsupported)}")

        source_path = item.get("source_path", item.get("rec_dir"))
        destination_path = item.get(
            "destination_path",
            item.get("archive_root"),
        )
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
            )
        )

    return PipelineConfig(
        config_path=config_path,
        timezone_name=timezone_name,
        timezone=_timezone(timezone_name),
        bronze_root=_path(str(bronze_root_value), cwd),
        log_dir=_path(
            str(raw.get("log_path", raw.get("log_dir", paths.state_dir / "logs"))),
            cwd,
        ),
        sources=tuple(sources),
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


def load_config(path: Path) -> PipelineConfig:
    """Compatibility helper for callers that already pass an explicit config."""
    return resolve_config(explicit_config=path).config


def public_config(
    config: PipelineConfig,
    paths: AppPaths | None = None,
) -> dict[str, Any]:
    storage = paths or user_paths()
    return {
        "timezone": config.timezone_name,
        "bronze_path": str(config.bronze_root),
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
