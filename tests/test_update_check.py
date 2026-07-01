from datetime import UTC, datetime, timedelta

from tau_coding.update_check import (
    PYPI_JSON_URL,
    UPDATE_CHECK_TIMEOUT_SECONDS,
    fetch_latest_pypi_version,
    startup_update_notice,
)


def test_startup_update_notice_reports_newer_stable_release(tmp_path) -> None:
    calls: list[tuple[str, float]] = []

    def fetcher(url: str, timeout: float) -> dict[str, object]:
        calls.append((url, timeout))
        return {"releases": {"0.1.0": [{}], "0.2.0": [{}], "0.3.0rc1": [{}]}}

    notice = startup_update_notice(
        "0.1.0",
        fetcher=fetcher,
        cache_path=tmp_path / "update-check.json",
        now=lambda: datetime(2026, 1, 1, tzinfo=UTC),
        env={},
    )

    assert notice is not None
    assert notice.current_version == "0.1.0"
    assert notice.latest_version == "0.2.0"
    assert "Tau 0.2.0 is available (installed: 0.1.0)" in notice.message
    assert "uv tool upgrade tau-ai" in notice.message
    assert calls == [(PYPI_JSON_URL, UPDATE_CHECK_TIMEOUT_SECONDS)]


def test_startup_update_notice_is_quiet_when_current(tmp_path) -> None:
    notice = startup_update_notice(
        "0.2.0",
        fetcher=lambda _url, _timeout: {"releases": {"0.2.0": [{}]}},
        cache_path=tmp_path / "update-check.json",
        env={},
    )

    assert notice is None


def test_startup_update_notice_uses_fresh_cache(tmp_path) -> None:
    cache_path = tmp_path / "update-check.json"
    cache_path.write_text(
        '{"checked_at":"2026-01-01T00:00:00+00:00","latest_version":"0.2.0"}\n',
        encoding="utf-8",
    )

    notice = startup_update_notice(
        "0.1.0",
        fetcher=lambda _url, _timeout: (_ for _ in ()).throw(AssertionError("no fetch")),
        cache_path=cache_path,
        now=lambda: datetime(2026, 1, 1, 12, tzinfo=UTC),
        env={},
    )

    assert notice is not None
    assert notice.latest_version == "0.2.0"


def test_startup_update_notice_uses_fresh_empty_cache(tmp_path) -> None:
    cache_path = tmp_path / "update-check.json"
    cache_path.write_text(
        '{"checked_at":"2026-01-01T00:00:00+00:00","latest_version":null}\n',
        encoding="utf-8",
    )

    notice = startup_update_notice(
        "0.1.0",
        fetcher=lambda _url, _timeout: (_ for _ in ()).throw(AssertionError("no fetch")),
        cache_path=cache_path,
        now=lambda: datetime(2026, 1, 1, 12, tzinfo=UTC),
        env={},
    )

    assert notice is None


def test_startup_update_notice_refreshes_stale_cache(tmp_path) -> None:
    cache_path = tmp_path / "update-check.json"
    cache_path.write_text(
        '{"checked_at":"2026-01-01T00:00:00+00:00","latest_version":"0.2.0"}\n',
        encoding="utf-8",
    )

    notice = startup_update_notice(
        "0.1.0",
        fetcher=lambda _url, _timeout: {"releases": {"0.3.0": [{}]}},
        cache_path=cache_path,
        now=lambda: datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=2),
        env={},
    )

    assert notice is not None
    assert notice.latest_version == "0.3.0"


def test_startup_update_notice_ignores_failures(tmp_path) -> None:
    def broken_fetcher(_url: str, _timeout: float) -> dict[str, object]:
        raise TimeoutError("offline")

    assert (
        startup_update_notice(
            "0.1.0",
            fetcher=broken_fetcher,
            cache_path=tmp_path / "update-check.json",
            env={},
        )
        is None
    )


def test_startup_update_notice_can_be_disabled(tmp_path) -> None:
    notice = startup_update_notice(
        "0.1.0",
        fetcher=lambda _url, _timeout: (_ for _ in ()).throw(AssertionError("no fetch")),
        cache_path=tmp_path / "update-check.json",
        env={"TAU_NO_UPDATE_CHECK": "1"},
    )

    assert notice is None


def test_startup_update_notice_skips_ci(tmp_path) -> None:
    notice = startup_update_notice(
        "0.1.0",
        fetcher=lambda _url, _timeout: (_ for _ in ()).throw(AssertionError("no fetch")),
        cache_path=tmp_path / "update-check.json",
        env={"CI": "true"},
    )

    assert notice is None


def test_fetch_latest_pypi_version_falls_back_to_info_version() -> None:
    latest = fetch_latest_pypi_version(
        fetcher=lambda _url, _timeout: {"info": {"version": "0.4.0"}}
    )

    assert latest == "0.4.0"


def test_fetch_latest_pypi_version_skips_malformed_release_versions() -> None:
    latest = fetch_latest_pypi_version(
        fetcher=lambda _url, _timeout: {"releases": {"0.3.0": [{}], "wat": [{}]}}
    )

    assert latest == "0.3.0"


def test_fetch_latest_pypi_version_rejects_malformed_versions() -> None:
    try:
        fetch_latest_pypi_version(fetcher=lambda _url, _timeout: {"info": {"version": "wat"}})
    except Exception as exc:
        assert exc.__class__.__name__ == "InvalidVersion"
    else:
        raise AssertionError("expected InvalidVersion")
