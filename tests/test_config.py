from pathlib import Path

import pytest

from teams_exporter.config import NotificationsConfig, load_config


def test_load_config_uses_defaults(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("{}\n", encoding="utf-8")

    config = load_config(config_path)

    assert config.auth.tenant_id == "organizations"
    assert config.path("output") == tmp_path / "output"


def test_authentication_client_is_not_configurable(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "auth:\n  client_id: 00000000-0000-0000-0000-000000000000\n",
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert not hasattr(config.auth, "client_id")


def test_load_config_requires_existing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "missing.yaml")


def test_notifications_only_expose_supported_options() -> None:
    config = NotificationsConfig()

    assert not hasattr(config, "enabled")
    assert not hasattr(config, "region_base")


def test_authentication_cache_is_not_configurable(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("auth:\n  token_cache: custom.bin\n", encoding="utf-8")

    config = load_config(config_path)

    assert not hasattr(config.auth, "token_cache")
