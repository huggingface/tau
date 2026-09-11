"""Tests for Tau's cross-session learning loop (memory + lessons + curator)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from tau_agent.messages import AssistantMessage, UserMessage
from tau_agent.provider_events import (
    AssistantDoneEvent,
    AssistantErrorEvent,
    TextDeltaEvent,
)
from tau_coding.learning import (
    ENTRY_DELIMITER,
    MAX_MEMORY_ENTRY_CHARS,
    LearnedContext,
    append_memory_entries,
    load_learned_lessons,
    load_memory_entries,
    memory_char_usage,
    resolve_store_paths,
    sanitize_lesson_name,
    snapshot_learned_context,
    write_lesson,
)
from tau_coding.learning_curator import (
    CuratorRunResult,
    _collect_provider_text,
    curate_session,
)


class _FakeHome:
    """Minimal TauPaths stand-in with only ``home``."""

    def __init__(self, home: Path) -> None:
        self.home = home


def _home(tmp_path: Path) -> Any:
    return _FakeHome(tmp_path)


def _store(tmp_path: Path) -> Any:
    return resolve_store_paths(_home(tmp_path))


# --------------------------------------------------------------------------
# Memory store
# --------------------------------------------------------------------------


class TestMemoryStore:
    def test_load_empty_when_missing(self, tmp_path: Path) -> None:
        assert load_memory_entries(tmp_path / "MEMORIES.md") == ()

    def test_append_and_load_roundtrip(self, tmp_path: Path) -> None:
        memory_path = tmp_path / "MEMORIES.md"
        added = append_memory_entries(memory_path, ("repo tests use uv run pytest",))
        assert added == ("repo tests use uv run pytest",)
        assert load_memory_entries(memory_path) == ("repo tests use uv run pytest",)

    def test_append_dedupes_case_insensitive(self, tmp_path: Path) -> None:
        memory_path = tmp_path / "MEMORIES.md"
        append_memory_entries(memory_path, ("User prefers concise responses",))
        added = append_memory_entries(memory_path, ("USER PREFERS CONCISE RESPONSES",))
        assert added == ()
        assert load_memory_entries(memory_path) == ("User prefers concise responses",)

    def test_append_multiple_entries(self, tmp_path: Path) -> None:
        memory_path = tmp_path / "MEMORIES.md"
        added = append_memory_entries(memory_path, ("First fact.", "Second fact."))
        assert added == ("First fact.", "Second fact.")
        assert load_memory_entries(memory_path) == ("First fact.", "Second fact.")

    def test_append_truncates_overlong_entry(self, tmp_path: Path) -> None:
        memory_path = tmp_path / "MEMORIES.md"
        added = append_memory_entries(memory_path, ("x" * (MAX_MEMORY_ENTRY_CHARS + 50),))
        assert len(added[0]) == MAX_MEMORY_ENTRY_CHARS
        assert added[0].endswith("...")

    def test_append_rejects_when_store_full(self, tmp_path: Path) -> None:
        memory_path = tmp_path / "MEMORIES.md"
        # 10 distinct 97-char entries + a delimiter each ≈ the 1000-char budget.
        fillers = tuple(f"fact-{index:03d}-" + "x" * 88 for index in range(10))
        append_memory_entries(memory_path, fillers, char_limit=1000)
        assert memory_char_usage(memory_path) <= 1000
        with pytest.raises(ValueError, match="Memory store is full"):
            append_memory_entries(memory_path, ("one more",), char_limit=1000)


# --------------------------------------------------------------------------
# Lessons
# --------------------------------------------------------------------------


class TestLessons:
    def test_load_empty_when_missing(self, tmp_path: Path) -> None:
        assert load_learned_lessons(tmp_path / "lessons") == ()

    def test_write_and_load_lesson(self, tmp_path: Path) -> None:
        lessons_dir = tmp_path / "lessons"
        write_lesson(
            lessons_dir,
            "uv-first",
            "Run tau tests through uv.",
            "# Rule\n\nAlways use `uv run pytest`.",
        )
        assert load_learned_lessons(lessons_dir) == (("uv-first", "Run tau tests through uv."),)

    def test_write_updates_existing_lesson(self, tmp_path: Path) -> None:
        lessons_dir = tmp_path / "lessons"
        write_lesson(lessons_dir, "uv-first", "Old description.", "old body")
        write_lesson(lessons_dir, "uv-first", "New description.", "new body")
        assert load_learned_lessons(lessons_dir) == (("uv-first", "New description."),)
        content = (lessons_dir / "uv-first" / "SKILL.md").read_text()
        assert "new body" in content
        assert "old body" not in content

    def test_sanitize_lesson_name(self) -> None:
        assert sanitize_lesson_name("Fix Flaky  Test (pytest)") == "fix-flaky-test-pytest"
        assert sanitize_lesson_name("") == "lesson"
        assert sanitize_lesson_name("a" * 80) == "a" * 48

    def test_snapshot_empty_context_renders_none(self, tmp_path: Path) -> None:
        assert snapshot_learned_context(_home(tmp_path)).render() is None

    def test_snapshot_renders_memory_and_lessons(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        append_memory_entries(store.memory_path, ("repo tests use uv",))
        write_lesson(store.lessons_dir, "uv-first", "Use uv.", "body")
        rendered = snapshot_learned_context(_home(tmp_path)).render()
        assert rendered is not None
        assert "repo tests use uv" in rendered
        assert "uv-first" in rendered

    def test_learned_context_render_truncates(self) -> None:
        learned = LearnedContext(
            memory_text="m" * 3000,
            lessons=(("lesson", "d"),) * 40,
        )
        rendered = learned.render()
        assert rendered is not None
        assert len(rendered) <= 2600
        assert rendered.endswith("...")


# --------------------------------------------------------------------------
# Curator run
# --------------------------------------------------------------------------


def _done_with_json(payload: dict[str, Any]) -> list[Any]:
    text = json.dumps(payload)
    partial = AssistantMessage(content=text)
    return [
        TextDeltaEvent(delta=text, content_index=0, partial=partial),
        AssistantDoneEvent(reason="stop", message=partial),
    ]


class _ScriptedProvider:
    """Minimal ModelProvider stand-in replaying fixed event streams."""

    def __init__(self, streams: list[list[Any]]) -> None:
        self._streams = streams
        self.calls: list[dict[str, Any]] = []

    def stream_response(
        self,
        *,
        model: str,
        system: str,
        messages: list[Any],
        tools: list[Any],
        signal: Any = None,
        session_id: Any = None,
    ) -> AsyncIterator[Any]:
        self.calls.append({"model": model, "system": system, "messages": messages})

        async def iterator() -> AsyncIterator[Any]:
            for event in self._streams.pop(0) if self._streams else []:
                yield event

        return iterator()


def _assistant(text: str) -> AssistantMessage:
    return AssistantMessage(content=text)


class TestCurateSession:
    @pytest.mark.anyio
    async def test_applies_memory_and_lessons(self, tmp_path: Path) -> None:
        provider = _ScriptedProvider(
            [
                _done_with_json(
                    {
                        "memory_entries": ["repo tests use uv run pytest -q"],
                        "lessons": [
                            {
                                "name": "uv-first",
                                "description": "Run tau tests via uv.",
                                "body": "Use `uv run pytest`.",
                            }
                        ],
                    }
                )
            ]
        )
        store = _store(tmp_path)
        messages = (_assistant("Fixed the flaky test."), UserMessage(content="thanks"))

        result = await curate_session(
            provider=provider,
            model="fake",
            messages=messages,
            store=store,
        )

        assert result.memory_added == ("repo tests use uv run pytest -q",)
        assert result.lessons == (("uv-first", False),)
        assert store.memory_path.exists()
        assert (store.lessons_dir / "uv-first" / "SKILL.md").exists()
        assert "Fixed the flaky test." in str(provider.calls[0]["messages"])

    @pytest.mark.anyio
    async def test_replaces_existing_lesson(self, tmp_path: Path) -> None:
        provider = _ScriptedProvider(
            [
                _done_with_json(
                    {
                        "memory_entries": [],
                        "lessons": [
                            {"name": "uv-first", "description": "Updated.", "body": "updated"}
                        ],
                    }
                )
            ]
        )
        store = _store(tmp_path)
        write_lesson(store.lessons_dir, "uv-first", "Original.", "original body")

        result = await curate_session(
            provider=provider,
            model="fake",
            messages=(_assistant("work"),),
            store=store,
        )

        assert result.lessons == (("uv-first", True),)

    @pytest.mark.anyio
    async def test_empty_transcript_is_rejected(self, tmp_path: Path) -> None:
        provider = _ScriptedProvider([])
        with pytest.raises(ValueError, match="Nothing to learn from"):
            await curate_session(
                provider=provider,
                model="fake",
                messages=(),
                store=_store(tmp_path),
            )

    @pytest.mark.anyio
    async def test_provider_error_raises(self, tmp_path: Path) -> None:
        provider = _ScriptedProvider(
            [[AssistantErrorEvent(reason="error", error=AssistantMessage(content=""))]]
        )
        with pytest.raises(ValueError, match="Curator request failed"):
            await curate_session(
                provider=provider,
                model="fake",
                messages=(_assistant("hi"),),
                store=_store(tmp_path),
            )

    @pytest.mark.anyio
    async def test_garbage_response_raises(self, tmp_path: Path) -> None:
        provider = _ScriptedProvider(
            [[AssistantDoneEvent(reason="stop", message=_assistant("no json here"))]]
        )
        with pytest.raises(ValueError, match="no JSON object"):
            await curate_session(
                provider=provider,
                model="fake",
                messages=(_assistant("hi"),),
                store=_store(tmp_path),
            )

    @pytest.mark.anyio
    async def test_summary_format(self, tmp_path: Path) -> None:
        provider = _ScriptedProvider(
            [
                _done_with_json(
                    {
                        "memory_entries": ["a durable fact"],
                        "lessons": [{"name": "new-lesson", "description": "d", "body": "b"}],
                    }
                )
            ]
        )
        result = await curate_session(
            provider=provider,
            model="fake",
            messages=(_assistant("hi"),),
            store=_store(tmp_path),
        )
        summary = result.format_summary()
        assert "a durable fact" in summary
        assert "new-lesson (new)" in summary


class TestCuratorRunResultFormat:
    def test_empty_summary(self) -> None:
        summary = CuratorRunResult(memory_added=(), lessons=()).format_summary()
        assert "Learning review complete." in summary
        assert "nothing new" in summary

    def test_updated_marker(self) -> None:
        result = CuratorRunResult(memory_added=(), lessons=(("x", True),))
        assert "x (updated)" in result.format_summary()


class TestCollectProviderText:
    @pytest.mark.anyio
    async def test_prefers_done_message_text(self) -> None:
        provider = _ScriptedProvider(
            [
                [
                    TextDeltaEvent(delta='{"a"', content_index=0, partial=_assistant('{"a"')),
                    AssistantDoneEvent(reason="stop", message=_assistant('{"ok": 1}')),
                ]
            ]
        )
        text = await _collect_provider_text(provider, "fake", "prompt")
        assert text == '{"ok": 1}'


def test_entry_delimiter_shape() -> None:
    assert ENTRY_DELIMITER == "\n§\n"


# --------------------------------------------------------------------------
# End-to-end through CodingSession.learn()
# --------------------------------------------------------------------------


class TestSessionLearn:
    @pytest.mark.anyio
    async def test_learn_appends_memory_and_writes_lesson(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from tau_agent import (
            JsonlSessionStorage,
        )
        from tau_coding import CodingSession, CodingSessionConfig
        from tau_coding.learning import load_learned_lessons, load_memory_entries
        from tau_coding.paths import TauPaths

        home = tmp_path / "tauhome"
        home.mkdir()

        # Redirect every TauPaths() instance at the isolated home.
        def _patched_init(self: TauPaths, **_kwargs: object) -> None:
            object.__setattr__(self, "home", home)
            object.__setattr__(self, "agents_home", tmp_path / "agents")

        monkeypatch.setattr(TauPaths, "__init__", _patched_init)

        curator_text = json.dumps(
            {
                "memory_entries": ["the repo uses uv"],
                "lessons": [
                    {
                        "name": "use-uv",
                        "description": "Run tests via uv.",
                        "body": "Use `uv run pytest`.",
                    }
                ],
            }
        )

        class _TwoPhaseProvider:
            """Main-agent stream first, curator stream on the second call."""

            def __init__(self) -> None:
                self.calls = 0

            def stream_response(self, **kwargs: object) -> AsyncIterator[object]:
                self.calls += 1
                if self.calls == 1:
                    stream: list[object] = [
                        AssistantDoneEvent(
                            reason="stop",
                            message=AssistantMessage(content="Done."),
                        )
                    ]
                else:
                    stream = [
                        TextDeltaEvent(
                            content_index=0,
                            delta=curator_text,
                            partial=AssistantMessage(content=curator_text),
                        ),
                        AssistantDoneEvent(
                            reason="stop",
                            message=AssistantMessage(content=curator_text),
                        ),
                    ]

                async def iterator() -> AsyncIterator[object]:
                    for event in stream:
                        yield event

                return iterator()

        provider = _TwoPhaseProvider()
        config = CodingSessionConfig(
            provider=provider,
            model="fake",
            system="You are Tau.",
            storage=JsonlSessionStorage(tmp_path / "session.jsonl"),
            cwd=tmp_path,
        )
        session = await CodingSession.load(config)

        # Run a real turn so the live transcript has something to learn from.
        async for _event in session.prompt("Fix the bug"):
            pass

        result = await session.learn()

        assert result.memory_added == ("the repo uses uv",)
        assert result.lessons == (("use-uv", False),)
        learned = load_memory_entries(home / "MEMORIES.md")
        assert learned == ("the repo uses uv",)
        lessons = load_learned_lessons(home / "skills" / "lessons")
        assert lessons == (("use-uv", "Run tests via uv."),)

    @pytest.mark.anyio
    async def test_learn_requires_provider(self, tmp_path: Path) -> None:
        from tau_agent import JsonlSessionStorage
        from tau_coding import CodingSession, CodingSessionConfig
        from tau_coding.paths import TauPaths

        home2 = tmp_path / "tauhome2"
        home2.mkdir()

        def _patched_init(self: TauPaths, **_kwargs: object) -> None:
            object.__setattr__(self, "home", home2)
            object.__setattr__(self, "agents_home", tmp_path / "agents2")

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(TauPaths, "__init__", _patched_init)
        try:
            config = CodingSessionConfig(
                provider=None,
                model="fake",
                system="You are Tau.",
                storage=JsonlSessionStorage(tmp_path / "session2.jsonl"),
                cwd=tmp_path,
            )
            # A provider-less config fails at load time in Tau: no fallback
            # backend exists, so there is no session to call /learn on. The
            # guard in session.learn() is defense-in-depth for hosts that
            # construct sessions directly with a None provider.
            with pytest.raises(Exception, match="Provider is not available"):
                await CodingSession.load(config)
        finally:
            monkeypatch.undo()
