from stat import S_IMODE

import pytest

from tau_coding.credentials import CredentialStoreError, FileCredentialStore, OAuthCredential


def test_file_credential_store_round_trips_and_sets_private_permissions(tmp_path) -> None:
    path = tmp_path / "credentials.json"
    store = FileCredentialStore(path)

    store.set("openai", "test-key")

    assert store.get("openai") == "test-key"
    assert S_IMODE(path.stat().st_mode) == 0o600


def test_file_credential_store_deletes_key(tmp_path) -> None:
    store = FileCredentialStore(tmp_path / "credentials.json")
    store.set("openai", "test-key")

    store.delete("openai")

    assert store.get("openai") is None


def test_file_credential_store_rejects_empty_values(tmp_path) -> None:
    store = FileCredentialStore(tmp_path / "credentials.json")

    with pytest.raises(CredentialStoreError, match="must not be empty"):
        store.set("openai", "")


def test_file_credential_store_round_trips_oauth_credentials(tmp_path) -> None:
    path = tmp_path / "credentials.json"
    store = FileCredentialStore(path)
    credential = OAuthCredential(
        access="access-token",
        refresh="refresh-token",
        expires=123456,
        account_id="account-1",
    )

    store.set_oauth("openai-codex", credential)

    assert store.get("openai-codex") is None
    assert store.get_oauth("openai-codex") == credential
    assert '"type": "oauth"' in path.read_text(encoding="utf-8")


def test_file_credential_store_round_trips_extensible_oauth_metadata(tmp_path) -> None:
    path = tmp_path / "credentials.json"
    store = FileCredentialStore(path)
    credential = OAuthCredential(
        access="copilot-access",
        refresh="github-token",
        expires=123456,
        metadata={
            "enterprise_domain": "ghe.example.com",
            "available_model_ids": ["gpt-5.4", "claude-sonnet-4.6"],
        },
    )

    store.set_oauth("github-copilot", credential)

    assert store.get_oauth("github-copilot") == credential
    assert '"account_id"' not in path.read_text(encoding="utf-8")
    assert not list(tmp_path.glob(".credentials.json.*"))


def test_file_credential_store_loads_legacy_codex_oauth_shape(tmp_path) -> None:
    path = tmp_path / "credentials.json"
    path.write_text(
        '{"openai-codex":{"type":"oauth","access":"a","refresh":"r",'
        '"expires":123,"account_id":"account"}}',
        encoding="utf-8",
    )

    credential = FileCredentialStore(path).get_oauth("openai-codex")

    assert credential == OAuthCredential(
        access="a",
        refresh="r",
        expires=123,
        account_id="account",
    )
