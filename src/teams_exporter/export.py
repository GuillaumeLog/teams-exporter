"""Export Teams conversations to local data and HTML pages."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import httpx

from .config import Config
from .graph import GraphClient
from .render import ExportMetadata, render_site

log = logging.getLogger("teams_exporter.export")

# Find embedded hostedContent images in a message's HTML body.
_HOSTED_RE = re.compile(
    r"hostedContents/([^/\"'?]+)/\$value", re.IGNORECASE
)
_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _slug(text: str, maxlen: int = 60) -> str:
    # Strip edge punctuation so attachment names cannot become the special path segments
    # "." or "..".
    s = _SAFE_RE.sub("_", text.strip()).strip("._-")
    return (s or "unnamed")[:maxlen]


@dataclass
class Attachment:
    name: str
    local_path: str | None = None  # Relative to the conversation directory
    url: str | None = None         # Original URL when not downloaded


@dataclass
class Message:
    id: str
    author_id: str
    author_name: str
    created: str
    body_html: str
    is_mine: bool
    deleted: bool = False
    attachments: list[Attachment] = field(default_factory=list)


@dataclass
class Member:
    id: str
    name: str
    avatar: str | None = None  # Relative path
    email: str | None = None
    upn: str | None = None
    job_title: str | None = None
    department: str | None = None
    company: str | None = None
    office: str | None = None
    city: str | None = None
    country: str | None = None
    mobile: str | None = None
    phones: list[str] = field(default_factory=list)
    language: str | None = None
    is_me: bool = False


@dataclass
class Conversation:
    id: str
    title: str
    chat_type: str
    slug: str
    members: list[Member] = field(default_factory=list)
    messages: list[Message] = field(default_factory=list)
    last_activity: str = ""          # Latest message date, used for sorting
    list_avatar: str | None = None   # Avatar used in the one-to-one chat list


def _chat_title(chat: dict, members: list[Member], my_id: str) -> str:
    if chat.get("topic"):
        return chat["topic"]
    others = [m.name for m in members if m.id != my_id]
    return ", ".join(others) if others else "(conversation)"


def _matches(chat: dict, title: str, filters: list[str]) -> bool:
    if not filters:
        return True
    hay = f"{chat.get('id', '')} {title}".lower()
    return any(f.lower() in hay for f in filters)


def _tenant_label(configured_tenant: str, me: dict) -> str:
    """Prefer the configured tenant, or infer a useful domain for generic authorities."""
    if configured_tenant.casefold() not in {"common", "organizations"}:
        return configured_tenant
    for field_name in ("userPrincipalName", "mail"):
        _, separator, domain = str(me.get(field_name) or "").rpartition("@")
        if separator and domain:
            return domain
    return configured_tenant


def _signed_in_label(me: dict) -> str:
    name = me.get("displayName") or ""
    account = me.get("userPrincipalName") or me.get("mail") or ""
    if name and account and name.casefold() != account.casefold():
        return f"{name} ({account})"
    return name or account or me.get("id") or "Unknown account"


class Exporter:
    def __init__(self, cfg: Config, graph: GraphClient):
        self.cfg = cfg
        self.graph = graph
        self.out = cfg.path(cfg.export.output_dir)
        self.my_id: str = ""
        self._avatar_cache: dict[str, str | None] = {}
        self._profile_cache: dict[str, dict] = {}

    def run(self) -> None:
        me = self.graph.me()
        self.my_id = me["id"]
        log.info("Signed in as %s", me.get("displayName", self.my_id))

        self.out.mkdir(parents=True, exist_ok=True)
        (self.out / "avatars").mkdir(exist_ok=True)
        (self.out / "conversations").mkdir(exist_ok=True)
        if self.cfg.export.download_avatars:
            self._avatar(self.my_id)  # Preload the avatar used on the user's messages

        conversations: list[Conversation] = []
        chats = list(self.graph.list_chats())
        log.info("Found %d conversation(s).", len(chats))

        for chat in chats:
            members = [
                Member(
                    id=(m.get("userId") or m.get("id") or ""),
                    name=m.get("displayName") or "?",
                    email=m.get("email"),
                )
                for m in chat.get("members", [])
            ]
            title = _chat_title(chat, members, self.my_id)
            if not _matches(chat, title, self.cfg.export.only):
                continue

            log.info("-> %s", title)
            conv = self._export_chat(chat, members, title)
            conversations.append(conv)
            self._write_json(conv)

        render_site(
            self.out,
            conversations,
            ExportMetadata(
                tenant=_tenant_label(self.cfg.auth.tenant_id, me),
                signed_in_as=_signed_in_label(me),
                exported_at=datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z"),
            ),
        )
        log.info("Export complete: %s", (self.out / "index.html"))

    def _export_chat(self, chat: dict, members: list[Member], title: str) -> Conversation:
        chat_id = chat["id"]
        slug = f"{_slug(title)}_{_slug(chat_id[-8:])}"
        conv_dir = self.out / "conversations" / slug
        self._migrate_legacy_conversation(slug, conv_dir)
        conv_dir.mkdir(exist_ok=True)
        (conv_dir / "media").mkdir(exist_ok=True)

        for m in members:
            self._enrich_member(m)
            if self.cfg.export.download_avatars:
                m.avatar = self._avatar(m.id)

        # Incremental mode starts from existing data and stops at the first known ID.
        existing = [] if self.cfg.export.force else self._load_existing(conv_dir)
        existing_ids = {m.id for m in existing}

        new_messages: list[Message] = []
        try:
            for raw in self.graph.chat_messages(chat_id):  # Newest to oldest
                if raw["id"] in existing_ids:
                    break
                msg = self._build_message(chat_id, raw, conv_dir)
                if msg is not None:
                    new_messages.append(msg)
        except httpx.HTTPStatusError as exc:
            log.warning("   conversation unavailable (%s); ignoring %d new message(s)",
                        exc.response.status_code, len(new_messages))
        new_messages.reverse()  # Restore chronological order
        messages = existing + new_messages

        if existing and not new_messages:
            log.info("   up to date (%d message(s))", len(messages))
        else:
            log.info("   %d message(s) (+%d new)", len(messages), len(new_messages))

        other_avatars = [m.avatar for m in members if m.id != self.my_id and m.avatar]
        return Conversation(
            id=chat_id,
            title=title,
            chat_type=chat.get("chatType", "chat"),
            slug=slug,
            members=members,
            messages=messages,
            last_activity=messages[-1].created if messages else "",
            list_avatar=other_avatars[0] if len(other_avatars) == 1 else None,
        )

    def _enrich_member(self, m: Member) -> None:
        """Enrich a participant with a cached, best-effort Graph profile."""
        m.is_me = m.id == self.my_id
        if not m.id:
            return
        if m.id not in self._profile_cache:
            self._profile_cache[m.id] = self.graph.user_profile(m.id) or {}
        p = self._profile_cache[m.id]
        m.name = p.get("displayName") or m.name
        m.email = m.email or p.get("mail")
        m.upn = p.get("userPrincipalName")
        m.job_title = p.get("jobTitle")
        m.department = p.get("department")
        m.company = p.get("companyName")
        m.office = p.get("officeLocation")
        m.city = p.get("city")
        m.country = p.get("country")
        m.mobile = p.get("mobilePhone")
        m.phones = p.get("businessPhones") or []
        m.language = p.get("preferredLanguage")

    def _load_existing(self, conv_dir: Path) -> list[Message]:
        """Load exported messages, or return an empty list if data is missing or invalid."""
        path = conv_dir / "conversation.json"
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return [
                Message(
                    **{**m, "attachments": [Attachment(**a) for a in m.get("attachments", [])]}
                )
                for m in data.get("messages", [])
            ]
        except (ValueError, TypeError) as exc:
            log.warning("   existing export is invalid; rebuilding it (%s)", exc)
            return []

    def _migrate_legacy_conversation(self, slug: str, conv_dir: Path) -> None:
        """Move an export created before conversations had a dedicated directory."""
        legacy_dir = self.out / slug
        if conv_dir.exists() or not (legacy_dir / "conversation.json").is_file():
            return
        legacy_dir.rename(conv_dir)
        log.info("   moved existing export to conversations/%s", slug)

    def _build_message(self, chat_id: str, raw: dict, conv_dir: Path) -> Message | None:
        if raw.get("messageType") not in (None, "message"):
            return None  # Ignore system messages
        frm = (raw.get("from") or {}).get("user") or {}
        author_id = frm.get("id") or ""
        body = raw.get("body") or {}
        content = body.get("content") or ""

        deleted = raw.get("deletedDateTime") is not None
        if self.cfg.export.download_attachments and not deleted:
            content = self._localize_inline_images(chat_id, raw["id"], content, conv_dir)

        attachments: list[Attachment] = []
        if self.cfg.export.download_attachments and not deleted:
            attachments = self._download_attachments(raw, conv_dir)

        return Message(
            id=raw["id"],
            author_id=author_id,
            author_name=frm.get("displayName") or "System",
            created=raw.get("createdDateTime", ""),
            body_html=content,
            is_mine=author_id == self.my_id,
            deleted=deleted,
            attachments=attachments,
        )

    def _localize_inline_images(self, chat_id: str, msg_id: str, html: str, conv_dir: Path) -> str:
        for content_id in set(_HOSTED_RE.findall(html)):
            data = self.graph.hosted_content(chat_id, msg_id, content_id)
            if data:
                fname = f"media/img_{_slug(content_id)}.png"
                (conv_dir / fname).write_bytes(data)
                # Replace Graph URLs with the local file path.
                html = re.sub(
                    rf"https?://[^\"']*hostedContents/{re.escape(content_id)}/\$value",
                    fname,
                    html,
                )
            else:
                # The original Graph URL requires a token and cannot work in a static
                # export, so remove it instead of leaving a broken URL.
                html = re.sub(
                    rf'<img[^>]*hostedContents/{re.escape(content_id)}/\$value[^>]*>',
                    "<em>[image unavailable]</em>",
                    html,
                    flags=re.IGNORECASE,
                )
        return html

    def _download_attachments(self, raw: dict, conv_dir: Path) -> list[Attachment]:
        out: list[Attachment] = []
        for att in raw.get("attachments") or []:
            name = att.get("name") or att.get("id") or "file"
            url = att.get("contentUrl")
            if not url or not url.startswith("http"):
                continue
            data = self.graph.download_shared_url(url)
            if data:
                fname = f"media/{_slug(name)}"
                (conv_dir / fname).write_bytes(data)
                out.append(Attachment(name=name, local_path=fname))
            else:
                out.append(Attachment(name=name, url=url))
        return out

    def _avatar(self, user_id: str) -> str | None:
        if not user_id:
            return None
        if user_id in self._avatar_cache:
            return self._avatar_cache[user_id]
        fname = f"avatars/{_slug(user_id)}.jpg"
        result: str | None = None
        if not self.cfg.export.force and (self.out / fname).exists():
            result = fname  # Reuse a file downloaded by an earlier run
        else:
            data = self.graph.user_photo(user_id)
            if data:
                (self.out / fname).write_bytes(data)
                result = fname
        self._avatar_cache[user_id] = result
        return result

    def _write_json(self, conv: Conversation) -> None:
        path = self.out / "conversations" / conv.slug / "conversation.json"
        path.write_text(json.dumps(asdict(conv), ensure_ascii=False, indent=2), encoding="utf-8")
