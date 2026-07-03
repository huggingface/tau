from datetime import UTC, datetime, timedelta

from tau_coding.update_check import (
    PYPI_JSON_URL,
    UPDATE_CHECK_TIMEOUT_SECONDS,
    ReleaseNoteSection,
    ReleaseNotesEntry,
    fetch_latest_pypi_version,
    load_release_notes,
    release_notes_between,
    startup_release_notes_notice,
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


def test_load_release_notes_reads_shared_json(tmp_path) -> None:
    notes_path = tmp_path / "releases.json"
    notes_path.write_text(
        """
        [
          {
            "version": "0.1.2",
            "date": "2026-07-03",
            "sections": {"New": ["Feature"], "Fixed": ["Fix"]}
          }
        ]
        """,
        encoding="utf-8",
    )

    notes = load_release_notes(notes_path)

    assert notes == (
        ReleaseNotesEntry(
            version="0.1.2",
            date="2026-07-03",
            sections=(
                ReleaseNoteSection(title="New", items=("Feature",)),
                ReleaseNoteSection(title="Fixed", items=("Fix",)),
            ),
        ),
    )


def test_startup_release_notes_notice_records_first_seen_version(tmp_path) -> None:
    state_path = tmp_path / "release-notes-state.json"

    notice = startup_release_notes_notice(
        "0.1.2",
        state_path=state_path,
        release_notes=(
            ReleaseNotesEntry(
                version="0.1.2",
                date=None,
                sections=(ReleaseNoteSection(title="New", items=("New TUI release notes",)),),
            ),
        ),
    )

    assert notice is None
    assert '"last_seen_version": "0.1.2"' in state_path.read_text(encoding="utf-8")


def test_startup_release_notes_notice_reports_upgrade_once(tmp_path) -> None:
    state_path = tmp_path / "release-notes-state.json"
    state_path.write_text('{"last_seen_version":"0.1.1"}\n', encoding="utf-8")
    release_notes = (
        ReleaseNotesEntry(
            version="0.1.2",
            date=None,
            sections=(ReleaseNoteSection(title="New", items=("New feature", "Bug fix")),),
        ),
    )

    notice = startup_release_notes_notice(
        "0.1.2",
        state_path=state_path,
        release_notes=release_notes,
    )

    assert notice is not None
    assert notice.previous_version == "0.1.1"
    assert notice.current_version == "0.1.2"
    assert notice.notes == ("New feature", "Bug fix")
    assert notice.message == "Tau updated to 0.1.2\n\n**New**\n- New feature\n- Bug fix"

    second_notice = startup_release_notes_notice(
        "0.1.2",
        state_path=state_path,
        release_notes=release_notes,
    )
    assert second_notice is None


def test_startup_release_notes_notice_combines_skipped_versions(tmp_path) -> None:
    state_path = tmp_path / "release-notes-state.json"
    state_path.write_text('{"last_seen_version":"0.1.0"}\n', encoding="utf-8")
    release_notes = (
        ReleaseNotesEntry(
            version="0.1.2",
            date=None,
            sections=(ReleaseNoteSection(title="New", items=("Second change",)),),
        ),
        ReleaseNotesEntry(
            version="0.1.1",
            date=None,
            sections=(ReleaseNoteSection(title="Fixed", items=("First change",)),),
        ),
        ReleaseNotesEntry(
            version="0.1.3",
            date=None,
            sections=(ReleaseNoteSection(title="New", items=("Future change",)),),
        ),
    )

    notice = startup_release_notes_notice(
        "0.1.2",
        state_path=state_path,
        release_notes=release_notes,
    )

    assert notice is not None
    assert notice.notes == ("First change", "Second change")


def test_release_notes_between_ignores_future_versions() -> None:
    entries = (
        ReleaseNotesEntry(version="0.1.1", date=None, sections=()),
        ReleaseNotesEntry(version="0.1.2", date=None, sections=()),
        ReleaseNotesEntry(version="0.1.3", date=None, sections=()),
    )

    assert release_notes_between("0.1.1", "0.1.2", entries) == (entries[1],)


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
