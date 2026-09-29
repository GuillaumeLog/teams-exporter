"""Render conversations as Teams-inspired HTML pages with Jinja2."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import nh3
from dateutil import parser as dateparser
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

if TYPE_CHECKING:
    from .export import Conversation

log = logging.getLogger("teams_exporter.render")

_TEMPLATES = Path(__file__).parent / "templates"
MESSAGE_BATCH_SIZE = 200


@dataclass(frozen=True)
class ExportMetadata:
    tenant: str
    signed_in_as: str
    exported_at: str

# Teams provides rich HTML message bodies, but they remain remote input and must be
# sanitized before inclusion in the local export.
_MESSAGE_TAGS = {
    "a", "b", "blockquote", "br", "code", "del", "div", "em", "h1", "h2", "h3",
    "h4", "h5", "h6", "hr", "i", "img", "li", "ol", "p", "pre", "s", "span",
    "strong", "sub", "sup", "table", "tbody", "td", "th", "thead", "tr", "u", "ul",
}
_MESSAGE_ATTRIBUTES = {
    "a": {"href", "title"},
    "img": {"alt", "height", "src", "title", "width"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan"},
}


def _sanitize_message_html(value: str | None) -> str:
    """Preserve useful formatting while removing scripts and active URLs."""
    return nh3.clean(
        value or "",
        tags=_MESSAGE_TAGS,
        attributes=_MESSAGE_ATTRIBUTES,
        url_schemes={"http", "https", "mailto"},
        link_rel="noopener noreferrer",
    )


def _fmt_datetime(value: str) -> str:
    if not value:
        return ""
    try:
        dt = dateparser.isoparse(value)
        return dt.strftime("%d/%m/%Y %H:%M")
    except (ValueError, TypeError):
        return value


def _fmt_day(value: str) -> str:
    if not value:
        return ""
    try:
        return dateparser.isoparse(value).strftime("%A %d %B %Y")
    except (ValueError, TypeError):
        return value


def _day_key(value: str) -> str:
    if not value:
        return ""
    try:
        return dateparser.isoparse(value).date().isoformat()
    except (ValueError, TypeError):
        return value


def _initials(name: str) -> str:
    parts = [p for p in name.split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def _env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(_TEMPLATES),
        autoescape=select_autoescape(["html"]),
    )
    env.filters["dt"] = _fmt_datetime
    env.filters["day"] = _fmt_day
    env.filters["day_key"] = _day_key
    env.filters["initials"] = _initials
    env.filters["safe_html"] = lambda s: Markup(_sanitize_message_html(s))
    return env


def _message_chunks(messages: list, size: int = MESSAGE_BATCH_SIZE) -> list[list]:
    """Split messages chronologically, keeping the newest batch at full size."""
    if not messages:
        return [[]]
    chunks = []
    end = len(messages)
    while end > 0:
        start = max(0, end - size)
        chunks.append(messages[start:end])
        end = start
    chunks.reverse()
    return chunks


def render_site(
    out_dir: Path, conversations: list[Conversation], metadata: ExportMetadata
) -> None:
    env = _env()
    _write_assets(out_dir)

    def _sort_key(c):
        ts = 0.0
        if c.last_activity:
            try:
                ts = dateparser.isoparse(c.last_activity).timestamp()
            except (ValueError, TypeError):
                ts = 0.0
        # Descending message count, then descending date
        return (-len(c.messages), -ts)

    convs = sorted(conversations, key=_sort_key)
    sidebar_data = {
        c.id: {
            "search": " ".join(
                filter(
                    None,
                    [c.id, c.title]
                    + [
                        value
                        for member in c.members
                        for value in (
                            member.name,
                            member.email,
                            member.upn,
                            member.department,
                            member.company,
                        )
                        if value
                    ],
                )
            ),
            "people": " ".join(m.name for m in c.members if not m.is_me),
        }
        for c in convs
    }
    people = sorted(
        {m.name for c in convs for m in c.members if not m.is_me and m.name},
        key=str.casefold,
    )

    index_tpl = env.get_template("index.html.j2")
    (out_dir / "index.html").write_text(
        index_tpl.render(
            conversations=convs,
            sidebar_data=sidebar_data,
            people=people,
            current_id=None,
            root_prefix="",
            conversation_prefix="conversations/",
            export_metadata=metadata,
        ),
        encoding="utf-8",
    )

    conv_tpl = env.get_template("conversation.html.j2")
    message_tpl = env.get_template("_message_batch.html.j2")
    for conv in convs:
        avatars = {m.id: m.avatar for m in conv.members}
        chunks = _message_chunks(conv.messages)
        initial_messages = chunks[-1]
        older_chunks = chunks[:-1]
        conv_dir = out_dir / "conversations" / conv.slug
        messages_dir = conv_dir / "messages"
        messages_dir.mkdir(parents=True, exist_ok=True)
        for stale_chunk in messages_dir.glob("chunk-*.js"):
            stale_chunk.unlink()

        for index, messages in enumerate(older_chunks):
            batch_html = message_tpl.render(
                messages=messages,
                avatars=avatars,
                avatar_prefix="../../",
                start_day=_day_key(messages[0].created) if messages else "",
                end_day=_day_key(messages[-1].created) if messages else "",
            )
            payload = {
                "html": batch_html,
                "nextChunk": f"{index - 1:05d}" if index > 0 else None,
                "remaining": sum(len(chunk) for chunk in older_chunks[:index]),
            }
            chunk_script = (
                "window.TeamsExporterMessageChunk("
                + json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
                + ");\n"
            )
            (messages_dir / f"chunk-{index:05d}.js").write_text(
                chunk_script, encoding="utf-8"
            )

        html = conv_tpl.render(
            conv=conv,
            conversations=convs,
            avatars=avatars,
            initial_messages=initial_messages,
            older_message_count=sum(len(chunk) for chunk in older_chunks),
            next_chunk=f"{len(older_chunks) - 1:05d}" if older_chunks else None,
            sidebar_data=sidebar_data,
            people=people,
            current_id=conv.id,
            root_prefix="../../",
            conversation_prefix="../",
            export_metadata=metadata,
        )
        (conv_dir / "index.html").write_text(html, encoding="utf-8")

    log.info("Generated HTML for %d conversation(s).", len(convs))


def _write_assets(out_dir: Path) -> None:
    for filename in ("style.css", "app.js"):
        (out_dir / filename).write_text(
            (_TEMPLATES / filename).read_text(encoding="utf-8"), encoding="utf-8"
        )
