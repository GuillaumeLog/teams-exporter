from unittest.mock import MagicMock, patch

from teams_exporter.config import (
    AuthConfig,
    Config,
    ExportConfig,
    NotificationsConfig,
    PurgeConfig,
)
from teams_exporter.purge import Plan, Purger


def _config(tmp_path, *, delete_files: bool = True) -> Config:
    return Config(
        auth=AuthConfig(),
        export=ExportConfig(),
        purge=PurgeConfig(delete_onedrive_files=delete_files),
        notifications=NotificationsConfig(),
        root=tmp_path,
    )


def test_collect_files_only_accepts_own_teams_upload_folder(tmp_path) -> None:
    graph = MagicMock()
    graph.resolve_shared_item.return_value = {
        "id": "item-1",
        "name": "document.pdf",
        "parentReference": {
            "driveId": "my-drive",
            "path": "/drive/root:/Microsoft Teams Chat Files",
        },
    }
    purger = Purger(_config(tmp_path), graph)
    purger.my_drive_id = "my-drive"
    plan = Plan(chat_id="chat-1", title="Test")

    purger._collect_files(
        {"attachments": [{"name": "document.pdf", "contentUrl": "https://example.test/a"}]},
        plan,
        set(),
    )

    assert [(item.drive_id, item.item_id) for item in plan.files] == [("my-drive", "item-1")]
    assert plan.skipped_files == []


def test_collect_files_rejects_items_outside_own_teams_folder(tmp_path) -> None:
    graph = MagicMock()
    graph.resolve_shared_item.return_value = {
        "id": "item-1",
        "name": "document.pdf",
        "parentReference": {"driveId": "other-drive", "path": "/drive/root:/Shared"},
    }
    purger = Purger(_config(tmp_path), graph)
    purger.my_drive_id = "my-drive"
    plan = Plan(chat_id="chat-1", title="Test")

    purger._collect_files(
        {"attachments": [{"name": "document.pdf", "contentUrl": "https://example.test/a"}]},
        plan,
        set(),
    )

    assert plan.files == []
    assert plan.skipped_files == ["document.pdf"]


def test_failed_message_deletion_prevents_chat_hiding(tmp_path) -> None:
    graph = MagicMock()
    purger = Purger(_config(tmp_path, delete_files=False), graph)
    plan = Plan(chat_id="chat-1", title="Test", message_ids=["message-1"])

    with (
        patch.object(purger, "_soft_delete_with_retry", return_value=False),
        patch.object(purger, "_hide") as hide,
    ):
        result = purger._execute(plan)

    assert result is not None
    assert result.msg_fail == 1
    hide.assert_not_called()


def test_tenant_id_rejects_authority_aliases_and_domains(tmp_path) -> None:
    purger = Purger(_config(tmp_path), MagicMock())

    purger.cfg.auth.tenant_id = "organizations"
    assert purger._tenant_id() is None
    purger.cfg.auth.tenant_id = "contoso.onmicrosoft.com"
    assert purger._tenant_id() is None
    purger.cfg.auth.tenant_id = "00000000-0000-0000-0000-000000000000"
    assert purger._tenant_id() == "00000000-0000-0000-0000-000000000000"
