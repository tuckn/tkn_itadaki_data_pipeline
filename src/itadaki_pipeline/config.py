"""Configuration discovery, layering, and path normalization."""

from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

import yaml

from .paths import CONFIG_FILENAME, AppPaths, runtime_temp_base, user_paths

DEFAULT_CONFIG_RESOURCE = "resources/config.example.yaml"
ITADAKI_REC_DIRECTORY = "Rec"


@dataclass(frozen=True)
class ProfileConfig:
    name: str
    device_id: str
    timezone_name: str
    timezone: dt.tzinfo
    itadaki_root: Path
    archive_root: Path
    delete_after_success: bool

    @property
    def rec_dir(self) -> Path:
        """Return Itadaki's application-owned recording directory."""
        return self.itadaki_root / ITADAKI_REC_DIRECTORY


@dataclass(frozen=True)
class PipelineConfig:
    config_path: Path
    processed_data_root: Path
    log_dir: Path
    default_profile_name: str
    selected_profile_name: str
    profiles: tuple[ProfileConfig, ...]
    weekly_mart_root: Path | None = None

    @property
    def profile(self) -> ProfileConfig:
        return next(
            profile
            for profile in self.profiles
            if profile.name == self.selected_profile_name
        )

    @property
    def timezone_name(self) -> str:
        return self.profile.timezone_name

    @property
    def timezone(self) -> dt.tzinfo:
        return self.profile.timezone


@dataclass(frozen=True)
class ResolvedConfig:
    config: PipelineConfig
    config_sources: tuple[str, ...]
    paths: AppPaths


def global_config_path() -> Path:
    return user_paths().config_file


def initialize_user_config(*, target: Path | None = None) -> tuple[Path, str]:
    """Create the user-global config without overwriting edited settings."""
    resource = files("itadaki_pipeline").joinpath(DEFAULT_CONFIG_RESOURCE)
    try:
        payload = resource.read_bytes()
    except (OSError, FileNotFoundError) as exc:
        raise RuntimeError(
            f"Packaged config template is unavailable: {DEFAULT_CONFIG_RESOURCE}: {exc}"
        ) from exc

    try:
        template = yaml.safe_load(payload.decode("utf-8"))
    except (UnicodeError, yaml.YAMLError) as exc:
        raise RuntimeError(f"Packaged config template is invalid: {exc}") from exc
    if not isinstance(template, dict):
        raise RuntimeError("Packaged config template must contain a mapping")

    destination = (target or global_config_path()).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
    except FileExistsError as exc:
        try:
            existing = destination.read_bytes()
        except OSError as read_exc:
            raise OSError(
                f"Cannot read existing configuration {destination}: {read_exc}"
            ) from read_exc
        if existing == payload:
            return destination, "unchanged"
        raise FileExistsError(
            "Config already exists and differs from the packaged template; "
            f"refusing to overwrite it: {destination}"
        ) from exc

    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return destination, "created"


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
    profile_name: str | None,
) -> PipelineConfig:
    if "sources" in raw:
        raise ValueError(
            "Config key 'sources' is no longer supported; use 'profiles' and "
            "'default_profile'"
        )
    if "timezone" in raw:
        raise ValueError(
            "Top-level 'timezone' is no longer supported; set it on each profile"
        )
    _only_known_keys(
        raw,
        {
            "default_profile",
            "profiles",
            "processed_data_path",
            "weekly_mart_path",
            "log_path",
        },
        location=str(config_path),
    )

    processed_data_root_value = raw.get("processed_data_path")
    if not processed_data_root_value:
        raise ValueError("processed_data_path is required")

    profile_items = raw.get("profiles", [])
    if not isinstance(profile_items, list) or not profile_items:
        raise ValueError("At least one profiles entry is required")

    profiles: list[ProfileConfig] = []
    names: set[str] = set()
    for item in profile_items:
        if not isinstance(item, dict):
            raise ValueError("Each profiles entry must be a mapping")
        if "modes" in item:
            raise ValueError(
                "Profile key 'modes' is no longer supported; select one profile "
                "with --profile"
            )
        _only_known_keys(
            item,
            {
                "name",
                "device_id",
                "timezone",
                "source_path",
                "destination_path",
                "delete_after_success",
            },
            location=f"{config_path}: profiles entry",
        )
        if "name" not in item:
            raise ValueError("Each profiles entry requires name")
        name = str(item["name"])
        if not name:
            raise ValueError("Profile name must not be empty")
        if name in names:
            raise ValueError(f"Duplicate profile name: {name}")
        names.add(name)

        source_path = item.get("source_path")
        destination_path = item.get("destination_path")
        device_id = item.get("device_id")
        if not device_id:
            raise ValueError(f"{name}: device_id is required")
        if not source_path:
            raise ValueError(f"{name}: source_path is required")
        if not destination_path:
            raise ValueError(f"{name}: destination_path is required")

        itadaki_root = _path(str(source_path), cwd)
        if itadaki_root.name.casefold() == ITADAKI_REC_DIRECTORY.casefold():
            raise ValueError(
                f"{name}: source_path must be the Itadaki folder, not its Rec "
                f"subfolder: {itadaki_root.parent}"
            )

        timezone_name = str(item.get("timezone", "Asia/Tokyo"))
        profiles.append(
            ProfileConfig(
                name=name,
                device_id=str(device_id),
                timezone_name=timezone_name,
                timezone=_timezone(timezone_name),
                itadaki_root=itadaki_root,
                archive_root=_path(str(destination_path), cwd),
                delete_after_success=bool(item.get("delete_after_success", False)),
            )
        )

    default_profile_value = raw.get("default_profile")
    if not default_profile_value:
        raise ValueError("default_profile is required")
    default_profile_name = str(default_profile_value)
    if default_profile_name not in names:
        raise ValueError(
            f"Unknown default_profile {default_profile_name!r}; available profiles: "
            f"{sorted(names)}"
        )
    selected_profile_name = profile_name or default_profile_name
    if selected_profile_name not in names:
        raise ValueError(
            f"Unknown profile {selected_profile_name!r}; available profiles: "
            f"{sorted(names)}"
        )

    weekly_mart_value = raw.get("weekly_mart_path")

    return PipelineConfig(
        config_path=config_path,
        processed_data_root=_path(str(processed_data_root_value), cwd),
        log_dir=_path(
            str(raw.get("log_path", paths.state_dir / "logs")),
            cwd,
        ),
        default_profile_name=default_profile_name,
        selected_profile_name=selected_profile_name,
        profiles=tuple(profiles),
        weekly_mart_root=(
            _path(str(weekly_mart_value), cwd) if weekly_mart_value else None
        ),
    )


def resolve_config(
    *,
    cwd: Path | None = None,
    explicit_config: Path | None = None,
    profile_name: str | None = None,
) -> ResolvedConfig:
    """Resolve global, CWD, and explicit configuration in that order."""
    current = (cwd or Path.cwd()).resolve()
    paths = user_paths()
    candidates = [global_config_path(), cwd_config_path(current)]
    if explicit_config is not None:
        candidates.append(explicit_config.expanduser().resolve())

    values: dict[str, Any] = {}
    config_sources: list[str] = []
    last_path: Path | None = None
    for path in candidates:
        if path.is_file():
            layer = _load_mapping(path)
            if "profiles" in layer:
                values.pop("sources", None)
                values.pop("timezone", None)
            if "sources" in layer:
                values.pop("profiles", None)
                values.pop("default_profile", None)
            values.update(layer)
            config_sources.append(str(path))
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
            profile_name=profile_name,
        ),
        config_sources=tuple(config_sources),
        paths=paths,
    )


def public_config(
    config: PipelineConfig,
    paths: AppPaths | None = None,
) -> dict[str, Any]:
    storage = paths or user_paths()
    return {
        "default_profile": config.default_profile_name,
        "selected_profile": config.selected_profile_name,
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
        "profiles": [
            {
                "name": profile.name,
                "device_id": profile.device_id,
                "timezone": profile.timezone_name,
                "source_path": str(profile.itadaki_root),
                "destination_path": str(profile.archive_root),
                "delete_after_success": profile.delete_after_success,
            }
            for profile in config.profiles
        ],
    }
