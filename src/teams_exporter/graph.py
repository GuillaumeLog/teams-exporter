"""Minimal Microsoft Graph client with pagination, throttling, and downloads."""

from __future__ import annotations

import base64
import logging
import time
from collections.abc import Iterator
from typing import Any

import httpx

from .auth import Authenticator

log = logging.getLogger("teams_exporter.graph")

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
MAX_RETRIES = 5


def _encode_share_url(share_url: str) -> str:
    """Encode a sharing URL for the /shares API using the u!... format."""
    return "u!" + base64.urlsafe_b64encode(share_url.encode()).decode().rstrip("=")


class GraphClient:
    def __init__(self, auth: Authenticator):
        self._auth = auth
        # /shares/.../content redirects to SharePoint. httpx strips Authorization when
        # the redirect changes hosts.
        self._client = httpx.Client(timeout=60.0, follow_redirects=True)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._auth.get_token()}"}

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """Retry throttled (429) and transient server (5xx) responses with backoff."""
        if url.startswith("/"):
            url = GRAPH_BASE + url
        for attempt in range(1, MAX_RETRIES + 1):
            resp = self._client.request(method, url, headers=self._headers(), **kwargs)
            if resp.status_code == 429 or resp.status_code >= 500:
                wait = int(resp.headers.get("Retry-After", min(2**attempt, 60)))
                log.warning(
                    "Graph %s %s -> %s; retrying in %ss (%d/%d)",
                    method, url, resp.status_code, wait, attempt, MAX_RETRIES,
                )
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp
        resp.raise_for_status()
        return resp

    def get(self, url: str, **kwargs: Any) -> dict[str, Any]:
        return self._request("GET", url, **kwargs).json()

    def get_bytes(self, url: str) -> bytes:
        return self._request("GET", url).content

    def _safe_get_bytes(self, url: str, what: str) -> bytes | None:
        """Download an optional asset without blocking the export on failure."""
        try:
            return self.get_bytes(url)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                # A missing optional resource, such as a user photo, is expected.
                log.debug("Download skipped (%s): %s", what, exc)
            else:
                log.warning("Download skipped (%s): %s", what, exc)
            return None
        except httpx.HTTPError as exc:
            log.warning("Download skipped (%s): %s", what, exc)
            return None

    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return self._request("POST", url, **kwargs)

    def delete(self, url: str) -> httpx.Response:
        return self._request("DELETE", url)

    def paginate(self, url: str, **kwargs: Any) -> Iterator[dict[str, Any]]:
        """Iterate through every page in a Graph collection using @odata.nextLink."""
        page = self.get(url, **kwargs)
        while True:
            yield from page.get("value", [])
            next_link = page.get("@odata.nextLink")
            if not next_link:
                return
            page = self.get(next_link)

    # --- Domain helpers -------------------------------------------------

    def me(self) -> dict[str, Any]:
        return self.get("/me")

    def list_chats(self) -> Iterator[dict[str, Any]]:
        yield from self.paginate("/me/chats?$expand=members&$top=50")

    def chat_messages(self, chat_id: str) -> Iterator[dict[str, Any]]:
        yield from self.paginate(f"/me/chats/{chat_id}/messages?$top=50")

    # Profile fields requested for each participant on a best-effort basis.
    USER_FIELDS = (
        "displayName,mail,userPrincipalName,jobTitle,department,companyName,"
        "officeLocation,city,country,mobilePhone,businessPhones,preferredLanguage"
    )

    def user_profile(self, user_id: str) -> dict[str, Any] | None:
        try:
            return self.get(f"/users/{user_id}?$select={self.USER_FIELDS}")
        except httpx.HTTPError as exc:
            log.warning("Profile unavailable (%s): %s", user_id, exc)
            return None

    def user_photo(self, user_id: str) -> bytes | None:
        return self._safe_get_bytes(f"/users/{user_id}/photo/$value", "avatar")

    def hosted_content(self, chat_id: str, message_id: str, content_id: str) -> bytes | None:
        return self._safe_get_bytes(
            f"/me/chats/{chat_id}/messages/{message_id}/hostedContents/{content_id}/$value",
            "embedded image",
        )

    def download_shared_url(self, share_url: str) -> bytes | None:
        """Download a OneDrive or SharePoint attachment through its sharing URL."""
        return self._safe_get_bytes(f"/shares/{_encode_share_url(share_url)}/driveItem/content",
                                    f"attachment {share_url[:70]}")

    # --- Purge (write operations) --------------------------------------

    def soft_delete_message(self, chat_id: str, message_id: str) -> None:
        """Soft-delete one message sent by the current user."""
        self.post(f"/me/chats/{chat_id}/messages/{message_id}/softDelete")

    def my_drive_id(self) -> str | None:
        try:
            return self.get("/me/drive?$select=id").get("id")
        except httpx.HTTPError as exc:
            log.warning("OneDrive unavailable: %s", exc)
            return None

    def resolve_shared_item(self, share_url: str) -> dict[str, Any] | None:
        """Resolve a sharing URL to a driveItem with its ID, parent, and size."""
        url = f"/shares/{_encode_share_url(share_url)}/driveItem?$select=id,name,size,webUrl,parentReference"
        try:
            return self.get(url)
        except httpx.HTTPError as exc:
            log.warning("Could not resolve file (%s): %s", share_url[:70], exc)
            return None

    def delete_drive_item(self, drive_id: str, item_id: str) -> None:
        self.delete(f"/drives/{drive_id}/items/{item_id}")

    def hide_chat_for_user(self, chat_id: str, user_id: str, tenant_id: str) -> None:
        """Hide a chat from the current user's history."""
        body = {"user": {"id": user_id, "tenantId": tenant_id}}
        self.post(f"/chats/{chat_id}/hideForUser", json=body)

    def close(self) -> None:
        self._client.close()
