from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from pi_event_helpers import assistant_done, assistant_start
from tau_agent.messages import AgentMessage, AssistantMessage, Usage, UserMessage
from tau_agent.provider_events import AssistantErrorEvent
from tau_agent.session import CustomEntry, JsonlSessionStorage
from tau_agent.tools import AgentTool
from tau_ai import CancellationToken, FakeProvider
from tau_ai.events import AssistantMessageEvent
from tau_coding import (
    CodingSession,
    CodingSessionConfig,
    OpenAICompatibleProviderConfig,
    ProviderSettings,
    SessionManager,
    TauPaths,
)
from tau_coding import session as coding_session_module
from tau_coding.events import AgentSettledEvent, HuggingFaceRouteEvent, SessionAgentEndEvent
from tau_coding.huggingface_routing import (
    HF_CACHE_MAX_CANDIDATES,
    HF_CACHE_MAX_ELIGIBLE_REQUESTS,
    HF_CACHE_MIN_PROMPT_TOKENS,
    HuggingFaceRoutingState,
    discover_huggingface_routes,
    next_huggingface_routes,
    observe_huggingface_cache,
)


class _RouteAwareProvider:
    def __init__(
        self,
        route: str | None,
        responses: dict[str | None, list[AssistantMessage]],
        calls: list[str | None],
    ) -> None:
        self.route = route
        self.responses = responses
        self.calls = calls

    def stream_response(
        self,
        *,
        model: str,
        system: str,
        messages: list[AgentMessage],
        tools: list[AgentTool],
        signal: CancellationToken | None = None,
        session_id: str | None = None,
    ) -> AsyncIterator[AssistantMessageEvent]:
        del system, messages, tools, signal, session_id
        self.calls.append(self.route)
        response = self.responses[self.route].pop(0)

        async def events() -> AsyncIterator[AssistantMessageEvent]:
            yield assistant_start(model=model)
            if response.stop_reason == "error":
                yield AssistantErrorEvent(reason="error", error=response)
            else:
                yield assistant_done(response)

        return events()

    async def aclose(self) -> None:
        pass


def _usage(*, cache_read: int = 0, reported: bool = False) -> Usage:
    return Usage(
        input=HF_CACHE_MIN_PROMPT_TOKENS - cache_read,
        cache_read=cache_read,
        cache_read_reported=reported,
        total_tokens=HF_CACHE_MIN_PROMPT_TOKENS,
    )


def _messages(*values: str) -> tuple[UserMessage, ...]:
    return tuple(UserMessage(content=value, timestamp=index) for index, value in enumerate(values))


def _observe(
    state: HuggingFaceRoutingState,
    usage: Usage,
    *messages: str,
) -> HuggingFaceRoutingState:
    return observe_huggingface_cache(
        state,
        usage,
        system="You are Tau.",
        messages=_messages(*messages),
        tools=(),
    )


def test_huggingface_cache_policy_warms_then_reroutes_on_absent_telemetry() -> None:
    state = HuggingFaceRoutingState.automatic("org/model", route="scaleway")

    cold = _observe(state, _usage(), "one")
    first_probe = _observe(cold, _usage(), "one", "two")
    exhausted = _observe(first_probe, _usage(), "one", "two", "three")

    assert cold.phase == "evaluating"
    assert cold.absent_probes == 0
    assert first_probe.phase == "evaluating"
    assert first_probe.absent_probes == 1
    assert exhausted.phase == "reroute"
    assert exhausted.absent_probes == 2
    assert exhausted.zero_probes == 0
    assert exhausted.eligible_requests == 3

    assert _observe(exhausted, _usage(), "one", "two", "three", "four") == exhausted


def test_huggingface_cache_policy_distinguishes_zero_and_retains_positive_reuse() -> None:
    state = HuggingFaceRoutingState.automatic("org/model", route="deepinfra")
    first_hit = _observe(state, _usage(cache_read=2_048, reported=True), "one")

    cold = _observe(state, _usage(reported=True), "one")
    zero = _observe(cold, _usage(reported=True), "one", "two")
    retained = _observe(zero, _usage(cache_read=2_048, reported=True), "one", "two", "three")
    later_miss = _observe(retained, _usage(), "one", "two", "three", "four")

    assert first_hit.phase == "retained"
    assert cold.zero_probes == 0
    assert zero.zero_probes == 1
    assert zero.absent_probes == 0
    assert retained.phase == "retained"
    assert retained.last_reason == "positive cache reuse reported"
    assert later_miss == retained


def test_huggingface_cache_policy_ignores_short_and_incomparable_requests() -> None:
    state = HuggingFaceRoutingState.automatic("org/model", route="deepinfra")
    short_usage = Usage(
        input=HF_CACHE_MIN_PROMPT_TOKENS - 1,
        cache_read_reported=False,
        total_tokens=HF_CACHE_MIN_PROMPT_TOKENS - 1,
    )

    short = _observe(state, short_usage, "one")
    cold = _observe(short, _usage(), "one")
    incomparable = _observe(cold, _usage(), "replacement")
    first_probe = _observe(incomparable, _usage(), "replacement", "continued")

    assert short.phase == "evaluating"
    assert short.eligible_requests == 0
    assert short.last_reason == "prompt below 4096-token eligibility threshold"
    assert incomparable.absent_probes == 0
    assert incomparable.last_reason == "request context was not append-only comparable"
    assert first_probe.absent_probes == 1


def test_huggingface_cache_policy_exhaustion_is_terminal_and_bounded() -> None:
    attempted = tuple(f"route-{index}" for index in range(HF_CACHE_MAX_CANDIDATES))
    state = HuggingFaceRoutingState.automatic("org/model", route=attempted[-1]).model_copy(
        update={
            "attempted_routes": attempted,
            "eligible_requests": HF_CACHE_MAX_ELIGIBLE_REQUESTS - 2,
        }
    )

    cold = _observe(state, _usage(), "one")
    exhausted = _observe(cold, _usage(), "one", "two")
    unchanged = _observe(exhausted, _usage(cache_read=1, reported=True), "one", "two", "three")

    assert exhausted.phase == "exhausted"
    assert exhausted.eligible_requests == HF_CACHE_MAX_ELIGIBLE_REQUESTS
    assert "candidate budget" in (exhausted.last_reason or "")
    assert unchanged == exhausted


@pytest.mark.anyio
async def test_huggingface_session_surfaces_budget_exhaustion(tmp_path: Path) -> None:
    model = "org/model"
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=FakeProvider([]),
            model=model,
            system="You are Tau.",
            storage=JsonlSessionStorage(tmp_path / "session.jsonl"),
            cwd=tmp_path,
            provider_name="huggingface",
            inference_provider="deepinfra",
            inference_provider_mode="automatic",
        )
    )
    session._huggingface_routing_state = HuggingFaceRoutingState.automatic(
        model,
        route="deepinfra",
    ).model_copy(
        update={
            "attempted_routes": ("scaleway", "novita", "deepinfra"),
            "eligible_requests": HF_CACHE_MAX_ELIGIBLE_REQUESTS - 2,
        }
    )

    first_context = [UserMessage(content="one")]
    session._harness.replace_messages(first_context)
    assert await session._observe_huggingface_response(AssistantMessage(usage=_usage())) is None

    session._harness.replace_messages(
        [*first_context, AssistantMessage(content="reply"), UserMessage(content="two")]
    )
    event = await session._observe_huggingface_response(AssistantMessage(usage=_usage()))

    assert event is not None
    assert event.status == "exhausted"
    assert event.route == "deepinfra"
    assert "budget" in event.reason
    terminal_state = session._huggingface_routing_state
    assert terminal_state is not None
    assert terminal_state.phase == "exhausted"
    assert await session._observe_huggingface_response(AssistantMessage(usage=_usage())) is None
    assert session._huggingface_routing_state == terminal_state
    await session.aclose()


@pytest.mark.anyio
async def test_huggingface_session_surfaces_unpinnable_automatic_route(tmp_path: Path) -> None:
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=FakeProvider([]),
            model="org/model",
            system="You are Tau.",
            storage=JsonlSessionStorage(tmp_path / "session.jsonl"),
            cwd=tmp_path,
            provider_name="huggingface",
            inference_provider_mode="automatic",
        )
    )

    event = await session._observe_huggingface_response(
        AssistantMessage(response_provider="scaleway", usage=_usage())
    )

    assert event is not None
    assert event.status == "exhausted"
    assert event.route == "scaleway"
    assert event.reason == "scaleway could not be pinned locally"
    assert "evaluation stopped" in (session.huggingface_routing_status or "")
    await session.aclose()


@pytest.mark.anyio
async def test_huggingface_route_observation_failure_keeps_completed_response(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    model = "org/model"
    provider_config = OpenAICompatibleProviderConfig(
        name="huggingface",
        models=(model,),
        default_model=model,
    )
    responses = {
        None: [
            AssistantMessage(
                content="completed response",
                response_provider="deepinfra",
                usage=_usage(),
            )
        ]
    }
    calls: list[str | None] = []
    active_provider = _RouteAwareProvider(None, responses, calls)
    staged_provider = _RouteAwareProvider("deepinfra", {}, calls)

    def create_provider(*_args: object, **kwargs: object) -> _RouteAwareProvider:
        return (
            staged_provider if kwargs.get("inference_provider") == "deepinfra" else active_provider
        )

    monkeypatch.setattr(coding_session_module, "create_model_provider", create_provider)
    manager = SessionManager(TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents"))
    record = manager.create_session(
        cwd=tmp_path,
        model=model,
        provider_name="huggingface",
        title="Routing failure test",
    )
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=active_provider,
            model=model,
            system="You are Tau.",
            storage=JsonlSessionStorage(record.path),
            cwd=tmp_path,
            session_id=record.id,
            session_manager=manager,
            provider_name="huggingface",
            inference_provider_mode="automatic",
            provider_settings=ProviderSettings(providers=(provider_config,)),
            runtime_provider_config=provider_config,
        )
    )
    original_touch_session = manager.touch_session

    def fail_route_touch_session(*args: object, **kwargs: object) -> object:
        if kwargs.get("inference_provider") == "deepinfra":
            raise PermissionError("session index is read-only")
        return original_touch_session(*args, **kwargs)

    monkeypatch.setattr(manager, "touch_session", fail_route_touch_session)
    events = await _collect(session.prompt("hello"))

    assert calls == [None]
    assert any(
        isinstance(event, SessionAgentEndEvent)
        and any(
            isinstance(message, AssistantMessage) and message.text == "completed response"
            for message in event.messages
        )
        for event in events
    )
    assert isinstance(events[-1], AgentSettledEvent)
    assert not any(isinstance(event, HuggingFaceRouteEvent) for event in events)
    assert session.inference_provider is None
    assert session._harness.config.provider is active_provider
    assert session._huggingface_routing_state is not None
    assert session._huggingface_routing_state.route is None
    assert session._last_diagnostic_log_path is not None
    await session.aclose()


def test_huggingface_routing_state_round_trips_custom_entry_data() -> None:
    state = _observe(
        HuggingFaceRoutingState.automatic("org/model", route="deepinfra"),
        _usage(reported=True),
        "one",
    )

    restored = HuggingFaceRoutingState.from_custom_data(state.to_custom_data())

    assert restored == state
    assert HuggingFaceRoutingState.from_custom_data({"version": 999}) is None


def test_next_huggingface_routes_is_deterministic_and_budgeted() -> None:
    state = HuggingFaceRoutingState.automatic("org/model", route="scaleway").model_copy(
        update={"attempted_routes": ("scaleway", "baseten")}
    )

    candidates = next_huggingface_routes(
        state,
        ("novita", "deepinfra", "scaleway", "fireworks-ai", "baseten"),
    )

    assert candidates == ("deepinfra",)


@pytest.mark.anyio
async def test_discover_huggingface_routes_filters_live_conversational_mappings() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params.get("expand[]") == "inferenceProviderMapping"
        return httpx.Response(
            200,
            json={
                "inferenceProviderMapping": {
                    "novita": {"status": "live", "task": "conversational"},
                    "deepinfra": {"status": "live", "task": "conversational"},
                    "offline": {"status": "staging", "task": "conversational"},
                    "embeddings": {"status": "live", "task": "feature-extraction"},
                    "malformed": "live",
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        routes = await discover_huggingface_routes(
            "org/model",
            client=client,
        )

    assert routes == ("deepinfra", "novita")


@pytest.mark.anyio
async def test_huggingface_session_reroutes_persists_and_resumes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    responses = {
        None: [
            AssistantMessage(
                content="automatic response",
                response_provider="scaleway",
                usage=_usage(),
            )
        ],
        "scaleway": [
            AssistantMessage(stop_reason="error", error_message="temporary route error"),
            AssistantMessage(
                content="route mismatch",
                response_provider="deepinfra",
                usage=_usage(),
            ),
            AssistantMessage(content="first warmed miss", usage=_usage()),
            AssistantMessage(content="second warmed miss", usage=_usage()),
        ],
        "deepinfra": [
            AssistantMessage(content="replacement warm-up", usage=_usage()),
            AssistantMessage(
                content="replacement cache hit",
                usage=_usage(cache_read=2_048, reported=True),
            ),
            AssistantMessage(content="retained after resume", usage=_usage()),
        ],
    }
    provider_calls: list[str | None] = []
    created_routes: list[str | None] = []

    def create_provider(_provider_config: object, **kwargs: object) -> _RouteAwareProvider:
        route = kwargs.get("inference_provider")
        assert route is None or isinstance(route, str)
        created_routes.append(route)
        if route == "broken":
            raise RuntimeError("route unavailable")
        return _RouteAwareProvider(route, responses, provider_calls)

    discovery_calls: list[str] = []

    async def discover_routes(model: str, **_kwargs: object) -> tuple[str, ...]:
        discovery_calls.append(model)
        return ("scaleway", "broken", "deepinfra")

    monkeypatch.setattr(coding_session_module, "create_model_provider", create_provider)
    monkeypatch.setattr(coding_session_module, "discover_huggingface_routes", discover_routes)
    model = "org/model"
    provider_config = OpenAICompatibleProviderConfig(
        name="huggingface",
        models=(model,),
        default_model=model,
    )
    manager = SessionManager(TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents"))
    record = manager.create_session(
        cwd=tmp_path,
        model=model,
        provider_name="huggingface",
        title="Cache routing test",
    )
    config = CodingSessionConfig(
        provider=FakeProvider([]),
        model=model,
        system="You are Tau.",
        storage=JsonlSessionStorage(record.path),
        cwd=tmp_path,
        session_id=record.id,
        session_manager=manager,
        provider_name="huggingface",
        inference_provider_mode="automatic",
        provider_settings=ProviderSettings(providers=(provider_config,)),
        runtime_provider_config=provider_config,
    )
    session = await CodingSession.load(config)

    events = []
    for prompt in ("one", "two", "three", "four", "five", "six", "seven"):
        events.extend([event async for event in session.prompt(prompt)])

    route_events = [event for event in events if isinstance(event, HuggingFaceRouteEvent)]
    assert [(event.previous_route, event.route) for event in route_events] == [
        (None, "scaleway"),
        ("scaleway", "deepinfra"),
    ]
    assert provider_calls == [
        None,
        "scaleway",
        "scaleway",
        "scaleway",
        "scaleway",
        "deepinfra",
        "deepinfra",
    ]
    assert discovery_calls == [model]
    assert "broken" in created_routes
    current = manager.get_session(record.id)
    assert current is not None
    assert current.inference_provider == "deepinfra"
    assert current.inference_provider_mode == "automatic"
    snapshots = [
        HuggingFaceRoutingState.from_custom_data(entry.data)
        for entry in session.state.custom_entries
        if entry.namespace == "tau.huggingface-cache-routing"
    ]
    latest_snapshot = snapshots[-1]
    assert latest_snapshot is not None
    assert latest_snapshot.phase == "retained"
    assert latest_snapshot.unavailable_routes == ("broken",)
    status = session.handle_command("/session").message
    assert status is not None
    assert "Hugging Face cache routing: automatic; retained" in status
    await session.aclose()

    resumed = await CodingSession.load(
        replace(
            config,
            provider=FakeProvider([]),
            inference_provider=current.inference_provider,
            inference_provider_mode=current.inference_provider_mode,
        )
    )
    assert "retained" in (resumed.huggingface_routing_status or "")
    before_discovery = len(discovery_calls)

    await _collect(resumed.prompt("eight"))

    assert len(discovery_calls) == before_discovery
    assert resumed.inference_provider == "deepinfra"
    resumed.set_inference_provider(None)
    await resumed.aclose()

    reset_record = manager.get_session(record.id)
    assert reset_record is not None
    restarted = await CodingSession.load(
        replace(
            config,
            provider=FakeProvider([]),
            inference_provider=reset_record.inference_provider,
            inference_provider_mode=reset_record.inference_provider_mode,
        )
    )
    assert restarted.inference_provider is None
    assert restarted.huggingface_routing_status == "automatic; waiting for route resolution"
    await restarted.aclose()


@pytest.mark.anyio
async def test_huggingface_explicit_route_stays_locked_until_reset(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    responses: dict[str | None, list[AssistantMessage]] = {
        "scaleway": [AssistantMessage(content="explicit response", usage=_usage())],
        None: [],
    }
    provider_calls: list[str | None] = []

    def create_provider(_provider_config: object, **kwargs: object) -> _RouteAwareProvider:
        route = kwargs.get("inference_provider")
        assert route is None or isinstance(route, str)
        return _RouteAwareProvider(route, responses, provider_calls)

    async def fail_discovery(*_args: object, **_kwargs: object) -> tuple[str, ...]:
        raise AssertionError("explicit routes must not discover candidates")

    monkeypatch.setattr(coding_session_module, "create_model_provider", create_provider)
    monkeypatch.setattr(coding_session_module, "discover_huggingface_routes", fail_discovery)
    model = "org/model"
    provider_config = OpenAICompatibleProviderConfig(
        name="huggingface",
        models=(model,),
        default_model=model,
    )
    manager = SessionManager(TauPaths(home=tmp_path / ".tau", agents_home=tmp_path / ".agents"))
    record = manager.create_session(
        cwd=tmp_path,
        model=model,
        provider_name="huggingface",
        inference_provider="scaleway",
        title="Explicit route test",
    )
    session = await CodingSession.load(
        CodingSessionConfig(
            provider=FakeProvider([]),
            model=model,
            system="You are Tau.",
            storage=JsonlSessionStorage(record.path),
            cwd=tmp_path,
            session_id=record.id,
            session_manager=manager,
            provider_name="huggingface",
            inference_provider="scaleway",
            provider_settings=ProviderSettings(providers=(provider_config,)),
            runtime_provider_config=provider_config,
        )
    )

    events = await _collect(session.prompt("one"))

    assert not any(isinstance(event, HuggingFaceRouteEvent) for event in events)
    assert provider_calls == ["scaleway"]
    assert session.huggingface_routing_status == "explicit route lock"
    assert not any(
        isinstance(entry, CustomEntry) and entry.namespace == "tau.huggingface-cache-routing"
        for entry in session.state.custom_entries
    )

    assert session.set_inference_provider(None).startswith("automatic")
    current = manager.get_session(record.id)
    assert current is not None
    assert current.inference_provider is None
    assert current.inference_provider_mode == "automatic"
    assert session.huggingface_routing_status == "automatic; waiting for route resolution"

    assert session.set_inference_provider("scaleway") == "scaleway"
    current = manager.get_session(record.id)
    assert current is not None
    assert current.inference_provider_mode == "explicit"
    assert session.huggingface_routing_status == "explicit route lock"
    await session.aclose()


async def _collect(events: AsyncIterator[object]) -> list[object]:
    return [event async for event in events]
