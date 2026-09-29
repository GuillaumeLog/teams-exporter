import stat
from pathlib import Path
from unittest.mock import ANY, MagicMock, patch

from teams_exporter.auth import GRAPH_CLIENT_ID, Authenticator, token_cache_path
from teams_exporter.config import AuthConfig


@patch("teams_exporter.auth.msal.PublicClientApplication")
def test_token_cache_is_created_for_current_user_only(
    public_client: MagicMock, tmp_path: Path
) -> None:
    cache_path = tmp_path / "private" / "token-cache.bin"
    auth = Authenticator(AuthConfig(), cache_path, ["User.Read"])
    auth._cache.has_state_changed = True
    auth._cache.serialize = MagicMock(return_value='{"AccessToken": {}}')

    auth._save_cache()

    assert cache_path.read_text(encoding="utf-8") == '{"AccessToken": {}}'
    assert stat.S_IMODE(cache_path.stat().st_mode) == 0o600
    public_client.assert_called_once_with(
        client_id=GRAPH_CLIENT_ID,
        authority="https://login.microsoftonline.com/organizations",
        token_cache=ANY,
    )


@patch("teams_exporter.auth.user_cache_path")
def test_token_caches_use_separate_platform_directories(
    user_cache: MagicMock, tmp_path: Path
) -> None:
    user_cache.return_value = tmp_path / "teams-exporter"

    assert token_cache_path("graph") == tmp_path / "teams-exporter" / "graph-token-cache.bin"
    assert token_cache_path("teams") == tmp_path / "teams-exporter" / "teams-token-cache.bin"
    user_cache.assert_called_with("teams-exporter", appauthor=False)
