"""Load configuration from config.yaml."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger("teams_exporter.config")


def _kwargs_for(cls, data: dict | None) -> dict[str, Any]:
    """Keep supported dataclass keys and warn about unknown keys."""
    data = data or {}
    allowed = {f.name for f in fields(cls) if f.init}
    unknown = set(data) - allowed
    if unknown:
        log.warning("config: ignored key(s) for %s: %s",
                    cls.__name__, ", ".join(sorted(unknown)))
    return {k: v for k, v in data.items() if k in allowed}


@dataclass
class AuthConfig:
    tenant_id: str = "organizations"

    @property
    def authority(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}"


@dataclass
class ExportConfig:
    output_dir: str = "output"
    download_avatars: bool = True
    download_attachments: bool = True
    only: list[str] = field(default_factory=list)
    # true rebuilds the export; false completes an existing export incrementally.
    force: bool = False


@dataclass
class PurgeConfig:
    # Optional pre-filter for conversations offered for selection. Empty means all.
    only: list[str] = field(default_factory=list)
    # Also delete sent files from the Microsoft Teams Chat Files folder.
    delete_onedrive_files: bool = True
    # Hide the conversation from the current user's history after an error-free purge.
    hide_when_cleaned: bool = True


@dataclass
class NotificationsConfig:
    # Experimentally attempt deletion after marking entries as read.
    try_delete: bool = True


@dataclass
class Config:
    auth: AuthConfig
    export: ExportConfig
    purge: PurgeConfig
    notifications: NotificationsConfig
    log_level: str = "INFO"
    # Project root used to resolve relative paths.
    root: Path = field(default_factory=Path.cwd)

    def path(self, value: str) -> Path:
        """Resolve an absolute path or a path relative to the project root."""
        p = Path(value)
        return p if p.is_absolute() else self.root / p


def load_config(path: str | Path) -> Config:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"Configuration file not found: {path}. "
            "Copy config.example.yaml to config.yaml and customize it."
        )
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    # Every section is optional. Unknown or obsolete keys produce a warning instead of
    # preventing configuration loading.
    return Config(
        auth=AuthConfig(**_kwargs_for(AuthConfig, data.get("auth"))),
        export=ExportConfig(**_kwargs_for(ExportConfig, data.get("export"))),
        purge=PurgeConfig(**_kwargs_for(PurgeConfig, data.get("purge"))),
        notifications=NotificationsConfig(**_kwargs_for(NotificationsConfig, data.get("notifications"))),
        log_level=(data.get("logging") or {}).get("level", "INFO"),
        root=path.resolve().parent,
    )
