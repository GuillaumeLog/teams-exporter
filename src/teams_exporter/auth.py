"""Delegated authentication through the MSAL device code flow."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import msal
from platformdirs import user_cache_path

from .config import AuthConfig

log = logging.getLogger("teams_exporter.auth")

# Delegated scopes are separated by use case: export only requests read access, while
# purge requests write access only when needed. MSAL adds offline_access/openid itself.
READ_SCOPES = ["Chat.Read", "User.Read.All"]
WRITE_SCOPES = ["User.Read", "Chat.ReadWrite"]

# Public first-party Microsoft Graph Command Line Tools client.
GRAPH_CLIENT_ID = "14d82eec-204b-4c2f-b7e8-296a70dab67e"


def token_cache_path(service: str) -> Path:
    """Return the platform-specific token-cache path for a supported service."""
    if service not in {"graph", "teams"}:
        raise ValueError(f"Unknown token-cache service: {service}")
    return user_cache_path("teams-exporter", appauthor=False) / f"{service}-token-cache.bin"


class Authenticator:
    def __init__(self, cfg: AuthConfig, cache_path: Path, scopes: list[str],
                 client_id: str = GRAPH_CLIENT_ID):
        self._scopes = scopes
        self._cache_path = cache_path
        self._cache = msal.SerializableTokenCache()
        if cache_path.exists():
            # SerializableTokenCache is not encrypted. Restrict an existing cache before
            # loading the tokens it contains.
            cache_path.chmod(0o600)
            self._cache.deserialize(cache_path.read_text(encoding="utf-8"))
        self._app = msal.PublicClientApplication(
            client_id=client_id,
            authority=cfg.authority,
            token_cache=self._cache,
        )

    def _save_cache(self) -> None:
        if self._cache.has_state_changed:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            # Path.write_text honors the umask and could create a group-readable cache.
            # os.open enforces mode 0600 from the moment the file is created.
            fd = os.open(self._cache_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as cache_file:
                    cache_file.write(self._cache.serialize())
            except Exception:
                # fdopen normally closes the descriptor; this guard covers a failure
                # occurring before it takes ownership.
                try:
                    os.close(fd)
                except OSError:
                    pass
                raise
            self._cache_path.chmod(0o600)

    def get_token(self) -> str:
        """Return a valid access token, reusing the cache when possible."""
        accounts = self._app.get_accounts()
        result = None
        if accounts:
            result = self._app.acquire_token_silent(self._scopes, account=accounts[0])

        if not result:
            log.info("Authentication required (device code).")
            flow = self._app.initiate_device_flow(scopes=self._scopes)
            if "user_code" not in flow:
                raise RuntimeError(f"Device flow failed: {flow.get('error_description')}")
            # Display the actionable device-flow message directly to the user.
            print("\n" + flow["message"] + "\n", flush=True)
            result = self._app.acquire_token_by_device_flow(flow)

        self._save_cache()

        if "access_token" not in result:
            raise RuntimeError(
                f"Authentication failed: "
                f"{result.get('error')} / {result.get('error_description')}"
            )
        return result["access_token"]
