"""Clean the Teams activity feed.

EXPERIMENTAL / UNSUPPORTED. This feature calls undocumented internal Teams client
endpoints under `*.ng.msg.teams.microsoft.com`. They may stop working at any time and are
outside the supported public API surface.

Authentication flow, separate from Graph:
  1. acquire an AAD token for `api.spaces.skype.com` through the first-party Teams client;
  2. exchange it for a `skypetoken` at the Teams authz endpoint, which also returns the
     regional messaging-service base URL.

Notifications are marked as read, then deletion is attempted when configured.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from urllib.parse import quote

import httpx

from .auth import Authenticator, token_cache_path
from .config import Config
from .logging_setup import console

log = logging.getLogger("teams_exporter.notifications")

# First-party Microsoft Teams client that can request a Skype/Teams service token.
TEAMS_CLIENT_ID = "1fec8e78-bce4-4aaf-ab1b-5451cc387264"
SKYPE_SCOPES = ["https://api.spaces.skype.com/.default"]
AUTHZ_URL = "https://authsvc.teams.microsoft.com/v1.0/authz"

# Internal conversation containing the activity feed.
NOTIF_CONV = quote("48:notifications", safe="")
LIST_QUERY = "view=msnp24Equivalent|supportsMessageProperties&pageSize=200"
MAX_PAGES = 25
MAX_RETRIES = 5
# Successive passes retry transient failures from the previous pass.
MAX_PASSES = 8
PASS_DELAY = 3       # Seconds between passes
STALL_LIMIT = 2      # Consecutive passes without progress before stopping


@dataclass
class Counts:
    read_ok: int = 0
    read_fail: int = 0
    del_ok: int = 0
    del_fail: int = 0


class NotificationCleaner:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.skype_token: str = ""
        self.base: str = ""
        self._client = httpx.Client(timeout=60.0, follow_redirects=True)

    def run(self) -> None:
        console.print(
            "[yellow]Warning: experimental feature using unsupported internal Teams endpoints.[/yellow]"
        )
        self._authenticate()

        totals = Counts()
        passes = 0
        prev_unread: int | None = None
        stalls = 0
        while passes < MAX_PASSES:
            passes += 1
            notifs = self._list_notifications()
            unread = [n for n in notifs if self._is_unread(n)]
            log.info("Pass %d: %d notification(s), %d unread.",
                     passes, len(notifs), len(unread))
            if not unread:
                break
            if prev_unread is not None and len(unread) >= prev_unread:
                stalls += 1
                if stalls >= STALL_LIMIT:
                    log.warning("Still %d unread after %d pass(es) without progress; stopping.",
                                len(unread), stalls)
                    break
            else:
                stalls = 0
            prev_unread = len(unread)
            self._clear(unread, totals)
            time.sleep(PASS_DELAY)

        self._summary(totals, passes)

    # --- Authentication -------------------------------------------------

    def _authenticate(self) -> None:
        auth = Authenticator(
            self.cfg.auth,
            token_cache_path("teams"),
            SKYPE_SCOPES,
            client_id=TEAMS_CLIENT_ID,
        )
        log.info("Acquiring a Teams token for api.spaces.skype.com...")
        aad_token = auth.get_token()

        log.info("Exchanging the token for a Teams skypetoken...")
        resp = self._client.post(AUTHZ_URL, headers={"Authorization": f"Bearer {aad_token}"})
        resp.raise_for_status()
        data = resp.json()

        self.skype_token = (data.get("tokens") or {}).get("skypeToken", "")
        region = data.get("regionGtms") or {}
        log.debug("regionGtms: %s", ", ".join(sorted(region)))
        self.base = (region.get("chatService") or "").rstrip("/")

        if not self.skype_token:
            raise RuntimeError("The authz endpoint did not return a skypetoken.")
        if not self.base:
            raise RuntimeError(
                "The authz endpoint did not return the regional messaging-service URL "
                "(regionGtms.chatService)."
            )
        log.info("Messaging service: %s", self.base)

    # --- Messaging-service requests ------------------------------------

    def _headers(self) -> dict[str, str]:
        return {
            "Authentication": f"skypetoken={self.skype_token}",
            "Content-Type": "application/json",
            "x-ms-client-type": "web",
        }

    def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        for attempt in range(1, MAX_RETRIES + 1):
            resp = self._client.request(method, url, headers=self._headers(), **kwargs)
            if resp.status_code == 429 or resp.status_code >= 500:
                wait = int(resp.headers.get("Retry-After", min(2**attempt, 30)))
                log.warning("%s %s -> %s; retrying in %ss (%d/%d)",
                            method, url, resp.status_code, wait, attempt, MAX_RETRIES)
                time.sleep(wait)
                continue
            return resp
        return resp

    def _list_notifications(self) -> list[dict]:
        """Retrieve notifications, following messaging-service pagination."""
        out: list[dict] = []
        url = f"{self.base}/v1/users/ME/conversations/{NOTIF_CONV}/messages?{LIST_QUERY}"
        for _ in range(MAX_PAGES):
            resp = self._request("GET", url)
            resp.raise_for_status()
            data = resp.json()
            out.extend(data.get("messages") or [])
            nxt = (data.get("_metadata") or {}).get("backwardLink")
            if not nxt or not data.get("messages"):
                break
            url = nxt
        return out

    def _msg_url(self, message_id: str) -> str:
        return (f"{self.base}/v1/users/ME/conversations/{NOTIF_CONV}"
                f"/messages/{quote(str(message_id), safe='')}")

    @staticmethod
    def _is_unread(n: dict) -> bool:
        props = n.get("properties") or {}
        return str(props.get("isread", "")).lower() != "true"

    def _clear(self, notifs: list[dict], totals: Counts) -> None:
        try_delete = self.cfg.notifications.try_delete
        for n in notifs:
            mid = n.get("id")
            if not mid:
                continue
            # 1) Mark as read (reliable).
            try:
                r = self._request("PUT", f"{self._msg_url(mid)}/properties?name=isread",
                                  json={"isread": True})
                if r.status_code < 300:
                    totals.read_ok += 1
                else:
                    totals.read_fail += 1
                    log.debug("isread %s -> HTTP %s", mid, r.status_code)
            except httpx.HTTPError as exc:
                totals.read_fail += 1
                log.debug("isread %s : %s", mid, exc)

            # 2) Attempt deletion (experimental).
            if try_delete:
                try:
                    r = self._request("DELETE", self._msg_url(mid))
                    if r.status_code < 300:
                        totals.del_ok += 1
                    else:
                        totals.del_fail += 1
                        log.debug("delete %s -> HTTP %s", mid, r.status_code)
                except httpx.HTTPError as exc:
                    totals.del_fail += 1
                    log.debug("delete %s : %s", mid, exc)

    def _summary(self, t: Counts, passes: int) -> None:
        console.print("\n[bold]Notification cleanup results[/bold]")
        console.print(f"  {passes} pass(es)")
        console.print(f"  marked as read: [green]{t.read_ok}[/green]"
                      + (f", [yellow]{t.read_fail} failed[/yellow]" if t.read_fail else ""))
        if self.cfg.notifications.try_delete:
            if t.del_ok:
                console.print(f"  deleted: [green]{t.del_ok}[/green]"
                              + (f", [yellow]{t.del_fail} failed[/yellow]" if t.del_fail else ""))
            else:
                console.print("  deletion: [yellow]unsupported by the service[/yellow] "
                              "(nothing was deleted; read status was still updated)")

    def close(self) -> None:
        self._client.close()
