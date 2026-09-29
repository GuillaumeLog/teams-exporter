from pathlib import Path

from teams_exporter.export import Conversation, Member, Message
from teams_exporter.render import (
    MESSAGE_BATCH_SIZE,
    ExportMetadata,
    _sanitize_message_html,
    render_site,
)

_METADATA = ExportMetadata(
    tenant="contoso.example",
    signed_in_as="Ada Lovelace (ada@contoso.example)",
    exported_at="2026-09-29 17:00 CEST",
)


def _conversation(message_count: int = 1) -> Conversation:
    member = Member(id="other", name="Ada Lovelace", is_me=False)
    messages = [
        Message(
            id=str(index),
            author_id=member.id,
            author_name=member.name,
            created=f"2026-01-{(index % 28) + 1:02d}T12:00:00Z",
            body_html=f"<p>Message {index}</p>",
            is_mine=False,
        )
        for index in range(message_count)
    ]
    return Conversation(
        id="chat-id",
        title="Project Orion",
        chat_type="oneOnOne",
        slug="Project_Orion_chat-id",
        members=[member],
        messages=messages,
        last_activity=messages[-1].created if messages else "",
    )


def test_sanitizer_keeps_basic_teams_formatting() -> None:
    result = _sanitize_message_html(
        '<p>Hello <strong>team</strong><br><a href="https://example.com">link</a></p>'
    )

    assert "<strong>team</strong>" in result
    assert 'href="https://example.com"' in result
    assert 'rel="noopener noreferrer"' in result


def test_sanitizer_removes_scripts_handlers_and_active_urls() -> None:
    result = _sanitize_message_html(
        '<script>alert(1)</script><img src="javascript:alert(2)" onerror="alert(3)">'
    )

    assert "<script" not in result
    assert "javascript:" not in result
    assert "onerror" not in result


def test_sanitizer_keeps_localized_inline_image() -> None:
    result = _sanitize_message_html('<img src="media/img_123.png" alt="capture">')

    assert 'src="media/img_123.png"' in result


def test_render_organizes_conversations_below_a_dedicated_directory(tmp_path: Path) -> None:
    conversation = _conversation()

    render_site(tmp_path, [conversation], _METADATA)

    index = (tmp_path / "index.html").read_text(encoding="utf-8")
    page = (tmp_path / "conversations" / conversation.slug / "index.html").read_text(
        encoding="utf-8"
    )
    assert 'href="conversations/Project_Orion_chat-id/index.html"' in index
    assert "contoso.example" in index
    assert "Ada Lovelace (ada@contoso.example)" in index
    assert "2026-09-29 17:00 CEST" in index
    assert 'href="../../style.css"' in page
    assert 'src="../../app.js"' in page
    assert "data-scroll-messages-top" in page
    assert "data-scroll-messages-bottom" in page
    assert not (tmp_path / conversation.slug).exists()


def test_render_loads_only_the_latest_message_batch_initially(tmp_path: Path) -> None:
    conversation = _conversation(MESSAGE_BATCH_SIZE + 1)

    render_site(tmp_path, [conversation], _METADATA)

    conv_dir = tmp_path / "conversations" / conversation.slug
    page = (conv_dir / "index.html").read_text(encoding="utf-8")
    older_chunk = (conv_dir / "messages" / "chunk-00000.js").read_text(encoding="utf-8")
    assert "Message 0" not in page
    assert "Message 1" in page
    assert f"Message {MESSAGE_BATCH_SIZE}" in page
    assert "Message 0" in older_chunk
    assert "Message 1" not in older_chunk
