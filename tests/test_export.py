from pathlib import Path

from teams_exporter.export import (
    Exporter,
    _matches,
    _signed_in_label,
    _slug,
    _tenant_label,
)


def test_slug_removes_path_characters() -> None:
    assert _slug(" ../Project / draft ") == "Project_draft"


def test_slug_has_safe_fallback() -> None:
    assert _slug("💬") == "unnamed"


def test_matches_title_or_chat_id_case_insensitively() -> None:
    chat = {"id": "19:ABC@thread.v2"}

    assert _matches(chat, "Demo Project", ["demo"])
    assert _matches(chat, "Demo Project", ["abc@THREAD"])
    assert not _matches(chat, "Demo Project", ["finance"])


def test_legacy_conversation_directory_is_migrated(tmp_path: Path) -> None:
    legacy = tmp_path / "conversation-slug"
    legacy.mkdir()
    (legacy / "conversation.json").write_text('{"messages": []}', encoding="utf-8")
    destination = tmp_path / "conversations" / "conversation-slug"
    destination.parent.mkdir()
    exporter = Exporter.__new__(Exporter)
    exporter.out = tmp_path

    exporter._migrate_legacy_conversation("conversation-slug", destination)

    assert (destination / "conversation.json").is_file()
    assert not legacy.exists()


def test_export_identity_labels_use_the_signed_in_account() -> None:
    me = {
        "id": "user-id",
        "displayName": "Ada Lovelace",
        "userPrincipalName": "ada@contoso.example",
    }

    assert _tenant_label("organizations", me) == "contoso.example"
    assert _tenant_label("tenant-id", me) == "tenant-id"
    assert _signed_in_label(me) == "Ada Lovelace (ada@contoso.example)"
