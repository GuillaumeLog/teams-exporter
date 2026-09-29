"""Purge the current user's messages and files from selected conversations.

Interactive flow:
  1. select conversations from an unchecked list;
  2. analyze the data that would be deleted;
  3. review the selected conversations and item counts;
  4. confirm the complete plan before any deletion.

Messages use reversible softDelete; files are moved to the OneDrive recycle bin.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import httpx
import questionary
from rich.progress import track

from .config import Config
from .export import Member, _chat_title, _matches
from .graph import GraphClient
from .logging_setup import console

log = logging.getLogger("teams_exporter.purge")

# Marker for the Teams upload folder, used as a deletion safety boundary.
TEAMS_FILES_MARKER = "teams chat files"

# Retry message deletion failures caused by eventual consistency in Teams. These are not
# 429/5xx responses, which GraphClient already handles, so they need a dedicated retry.
MSG_DELETE_ATTEMPTS = 4
MSG_RETRY_DELAY = 3  # Seconds between attempts


@dataclass
class FileTarget:
    drive_id: str
    item_id: str
    name: str


@dataclass
class Candidate:
    chat: dict
    title: str
    label: str
    total: int = 0
    mine: list[dict] = field(default_factory=list)  # Raw own messages reused by analysis
    accessible: bool = True


@dataclass
class Result:
    title: str
    msg_ok: int = 0
    msg_fail: int = 0
    file_ok: int = 0
    file_fail: int = 0
    hidden: bool = False


@dataclass
class Plan:
    chat_id: str
    title: str
    message_ids: list[str] = field(default_factory=list)
    files: list[FileTarget] = field(default_factory=list)
    skipped_files: list[str] = field(default_factory=list)
    accessible: bool = True

    @property
    def has_work(self) -> bool:
        return bool(self.message_ids or self.files)


class Purger:
    def __init__(self, cfg: Config, graph: GraphClient):
        self.cfg = cfg
        self.graph = graph
        self.my_id = ""
        self.my_tenant_id: str | None = None
        self.my_drive_id: str | None = None

    def run(self) -> None:
        me = self.graph.me()
        self.my_id = me["id"]
        log.info("Signed in as %s", me.get("displayName", self.my_id))

        candidates = self._candidates()
        if not candidates:
            log.info("No conversations available (is the 'only' filter too restrictive?).")
            return

        selected = self._select(candidates)
        if not selected:
            log.info("No conversations selected; nothing to do.")
            return

        if self.cfg.purge.delete_onedrive_files:
            self.my_drive_id = self.graph.my_drive_id()

        log.info("Analyzing %d conversation(s)...", len(selected))
        plans = [self._analyze(c) for c in selected]

        self._print_recap(plans)
        if not any(p.has_work for p in plans) and not self.cfg.purge.hide_when_cleaned:
            log.info("Nothing to delete in the selection.")
            return

        if not self._confirm():
            log.info("Canceled; no deletion was performed.")
            return

        results = [self._execute(plan) for plan in plans]
        self._print_summary([r for r in results if r])

    # --- Selection ------------------------------------------------------

    def _candidates(self) -> list[Candidate]:
        # 1) Build the inexpensive list of pre-filtered conversations.
        prefiltered: list[tuple[dict, list[Member], str]] = []
        for chat in self.graph.list_chats():
            members = [
                Member(id=(m.get("userId") or m.get("id") or ""), name=m.get("displayName") or "?")
                for m in chat.get("members", [])
            ]
            for rawm in chat.get("members", []):
                if rawm.get("userId") == self.my_id and rawm.get("tenantId"):
                    self.my_tenant_id = self.my_tenant_id or rawm.get("tenantId")
            title = _chat_title(chat, members, self.my_id)
            if _matches(chat, title, self.cfg.purge.only):
                prefiltered.append((chat, members, title))

        if not prefiltered:
            return []

        # 2) Scan messages for counts, retaining them for the later analysis.
        log.info("Counting messages in %d conversation(s)...", len(prefiltered))
        out: list[Candidate] = []
        for chat, members, title in track(prefiltered, description="Analyzing", console=console):
            total, mine, accessible = self._scan_messages(chat["id"])
            out.append(Candidate(
                chat=chat, title=title,
                label=self._label(title, members, len(mine), total, accessible),
                total=total, mine=mine, accessible=accessible,
            ))
        out.sort(key=lambda c: c.title.casefold())  # Alphabetical order
        return out

    def _scan_messages(self, chat_id: str) -> tuple[int, list[dict], bool]:
        """Return the visible message count and the current user's raw messages."""
        total = 0
        mine: list[dict] = []
        try:
            for raw in self.graph.chat_messages(chat_id):
                if raw.get("messageType") not in (None, "message") or raw.get("deletedDateTime"):
                    continue
                total += 1
                if ((raw.get("from") or {}).get("user") or {}).get("id") == self.my_id:
                    mine.append(raw)
        except httpx.HTTPStatusError:
            return total, mine, False
        return total, mine, True

    def _label(self, title: str, members: list[Member], mine: int, total: int, accessible: bool) -> str:
        others = [m.name for m in members if m.id != self.my_id]
        if not others:
            participants = "only me"
        elif len(others) > 4:
            participants = ", ".join(others[:4]) + f" +{len(others) - 4}"
        else:
            participants = ", ".join(others)
        counts = f"{mine}/{total} msg" + ("" if accessible else " (limited access)")
        return f"{title}   📨 {counts}   👥 {participants}"

    def _select(self, candidates: list[Candidate]) -> list[Candidate]:
        # Highlight conversations containing the user's messages with a green marker;
        # dim and indent the others.
        choices = []
        for idx, c in enumerate(candidates):
            if c.mine:
                title = [("bold fg:ansigreen", f"● {c.label}")]
            else:
                title = [("fg:ansibrightblack", f"  {c.label}")]
            choices.append(questionary.Choice(title=title, value=idx, checked=False))
        try:
            picked = questionary.checkbox(
                "Conversations to purge (space to select, enter to continue):",
                choices=choices,
            ).ask()
        except (KeyboardInterrupt, EOFError):
            picked = None
        if not picked:
            return []
        return [candidates[i] for i in picked]

    # --- Analysis (dry run) --------------------------------------------

    def _analyze(self, cand: Candidate) -> Plan:
        # Reuse the messages collected while counting; no second API read is needed.
        plan = Plan(chat_id=cand.chat["id"], title=cand.title, accessible=cand.accessible)
        seen_items: set[str] = set()
        for raw in cand.mine:
            plan.message_ids.append(raw["id"])
            if self.cfg.purge.delete_onedrive_files:
                self._collect_files(raw, plan, seen_items)
        return plan

    def _collect_files(self, raw: dict, plan: Plan, seen_items: set[str]) -> None:
        for att in raw.get("attachments") or []:
            url = att.get("contentUrl")
            if not url or not url.startswith("http"):
                continue
            item = self.graph.resolve_shared_item(url)
            if not item:
                plan.skipped_files.append(att.get("name") or url[:60])
                continue
            ref = item.get("parentReference") or {}
            drive_id = ref.get("driveId")
            item_id = item.get("id")
            name = item.get("name") or att.get("name") or "file"
            path = (ref.get("path") or "").lower()
            in_my_uploads = (
                drive_id == self.my_drive_id
                and self.my_drive_id is not None
                and TEAMS_FILES_MARKER in path
            )
            if in_my_uploads and item_id and item_id not in seen_items:
                seen_items.add(item_id)
                plan.files.append(FileTarget(drive_id=drive_id, item_id=item_id, name=name))
            elif not in_my_uploads:
                plan.skipped_files.append(name)

    # --- Summary and confirmation --------------------------------------

    def _print_recap(self, plans: list[Plan]) -> None:
        console.print("\n[bold]Purge summary (no action has been taken yet)[/bold]")
        total_msg = total_files = total_skip = 0
        for p in plans:
            note = "" if p.accessible else " [yellow](unavailable)[/yellow]"
            skip = f", {len(p.skipped_files)} skipped file(s)" if p.skipped_files else ""
            console.print(
                f"  • {p.title}{note}: "
                f"[red]{len(p.message_ids)}[/red] message(s), "
                f"[red]{len(p.files)}[/red] file(s){skip}"
            )
            total_msg += len(p.message_ids)
            total_files += len(p.files)
            total_skip += len(p.skipped_files)
        console.print(
            f"[bold]Total:[/bold] [red]{total_msg}[/red] message(s) to delete, "
            f"[red]{total_files}[/red] file(s) to delete, "
            f"{total_skip} skipped file(s) (outside the Teams folder or not owned)."
        )
        if self.cfg.purge.hide_when_cleaned:
            console.print(
                f"The [bold]{len(plans)}[/bold] conversation(s) purged without errors will "
                "then be [bold]hidden from your history[/bold] (reversible).\n"
            )
        else:
            console.print("")

    def _confirm(self) -> bool:
        console.print(
            "[yellow]softDelete is reversible; files go to the OneDrive recycle bin"
            + ("; hiding is also reversible (the chat returns after new activity)." if self.cfg.purge.hide_when_cleaned else ".")
            + "[/yellow]"
        )
        try:
            return bool(
                questionary.confirm(
                    "Proceed with deletion?", default=False, auto_enter=False
                ).ask()
            )
        except (KeyboardInterrupt, EOFError):
            return False

    # --- Execution ------------------------------------------------------

    def _soft_delete_with_retry(self, chat_id: str, message_id: str) -> bool:
        """Soft-delete a message, retrying transient consistency failures."""
        for attempt in range(1, MSG_DELETE_ATTEMPTS + 1):
            try:
                self.graph.soft_delete_message(chat_id, message_id)
                return True
            except httpx.HTTPError as exc:
                if attempt == MSG_DELETE_ATTEMPTS:
                    log.warning("   message %s was not deleted after %d attempt(s): %s",
                                message_id, attempt, exc)
                    return False
                log.debug("   message %s failed (attempt %d/%d); retrying in %ds",
                          message_id, attempt, MSG_DELETE_ATTEMPTS, MSG_RETRY_DELAY)
                time.sleep(MSG_RETRY_DELAY)
        return False

    def _execute(self, plan: Plan) -> Result | None:
        hide = self.cfg.purge.hide_when_cleaned
        if not plan.has_work and not hide:
            return None
        log.info("-> %s", plan.title)
        res = Result(title=plan.title)

        for mid in plan.message_ids:
            if self._soft_delete_with_retry(plan.chat_id, mid):
                res.msg_ok += 1
            else:
                res.msg_fail += 1
        if plan.message_ids:
            log.info("   messages: %d deleted, %d failed", res.msg_ok, res.msg_fail)

        for f in plan.files:
            try:
                self.graph.delete_drive_item(f.drive_id, f.item_id)
                res.file_ok += 1
            except httpx.HTTPError as exc:
                res.file_fail += 1
                log.warning("   file '%s' was not deleted: %s", f.name, exc)
        if plan.files:
            log.info("   files: %d deleted, %d failed", res.file_ok, res.file_fail)

        # Hide the chat only if none of the user's messages remain.
        if hide and res.msg_fail == 0:
            res.hidden = self._hide(plan.chat_id)
        return res

    def _hide(self, chat_id: str) -> bool:
        tenant = self._tenant_id()
        if not tenant:
            log.warning("   cannot hide conversation: tenant ID is unavailable")
            return False
        try:
            self.graph.hide_chat_for_user(chat_id, self.my_id, tenant)
            log.info("   conversation hidden from history")
            return True
        except httpx.HTTPError as exc:
            log.warning("   failed to hide conversation: %s", exc)
            return False

    def _tenant_id(self) -> str | None:
        if self.my_tenant_id:
            return self.my_tenant_id
        # Accept a configured GUID, but not authority aliases or domain names.
        t = self.cfg.auth.tenant_id
        if t and t not in ("organizations", "common", "consumers") and "." not in t:
            return t
        return None

    def _print_summary(self, results: list[Result]) -> None:
        console.print("\n[bold]Purge results[/bold]")
        if not results:
            console.print("  No deletion was performed.")
            return
        msg_ok = msg_fail = file_ok = file_fail = 0
        for r in results:
            fails = ""
            if r.msg_fail or r.file_fail:
                fails = f" [yellow]({r.msg_fail} message(s) + {r.file_fail} file(s) failed)[/yellow]"
            hidden = " [cyan]— hidden[/cyan]" if r.hidden else ""
            console.print(
                f"  • {r.title}: [green]{r.msg_ok}[/green] message(s), "
                f"[green]{r.file_ok}[/green] file(s) deleted{fails}{hidden}"
            )
            msg_ok += r.msg_ok
            msg_fail += r.msg_fail
            file_ok += r.file_ok
            file_fail += r.file_fail
        hidden_count = sum(1 for r in results if r.hidden)
        console.print(
            f"[bold]Total:[/bold] [green]{msg_ok}[/green] message(s) and "
            f"[green]{file_ok}[/green] file(s) deleted "
            f"across {len(results)} conversation(s)"
            + (f", [cyan]{hidden_count} hidden[/cyan]." if hidden_count else ".")
        )
        if msg_fail or file_fail:
            console.print(
                f"[yellow]Failures: {msg_fail} message(s), {file_fail} file(s) "
                "(see warnings above).[/yellow]"
            )
        console.print(
            "[dim]Reminder: softDelete is reversible; files are in the OneDrive recycle bin.[/dim]"
        )
