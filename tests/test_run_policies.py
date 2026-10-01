from dataclasses import replace

import pytest

from pi_event_helpers import assistant_done, assistant_error
from tau_agent import AssistantMessage, ToolCall, ToolResultMessage, Usage, UserMessage
from tau_agent.events import MessageEndEvent, TurnEndEvent
from tau_agent.messages import UsageCost
from tau_agent.session import JsonlSessionStorage
from tau_ai import FakeProvider
from tau_coding.events import QueueUpdateEvent
from tau_coding.run_policies import RunPolicyLimits, RunPolicyMonitor
from tau_coding.session import CodingSession, CodingSessionConfig
from tau_coding.shell_config import ShellConfigError, shell_settings_from_json


def monitor(limits, pricing=lambda *_: None):
    steering = []
    cancelled = []
    instance = RunPolicyMonitor(
        limits,
        queue_steering=steering.append,
        cancel=lambda: cancelled.append(True),
        price_response=pricing,
    )
    return instance, steering, cancelled


async def turn(instance, message, results=()):
    await instance.on_event(MessageEndEvent(message=message))
    await instance.on_event(TurnEndEvent(message=message, tool_results=list(results)))


@pytest.mark.anyio
async def test_cost_tokens_and_errors_trigger_once_and_reset():
    instance, steering, _ = monitor(RunPolicyLimits(max_cost_usd=0.5, max_tokens=20))
    message = AssistantMessage(usage=Usage(total_tokens=10, cost=UsageCost(total=0.25)))
    await turn(instance, message)
    assert not steering
    await turn(instance, message)
    assert len(steering) == 1
    assert "cost" in steering[0] and "tokens" in steering[0]
    await turn(instance, message)
    assert len(steering) == 1
    instance.reset()
    await turn(instance, message)
    assert len(steering) == 1
    await turn(instance, message)
    assert len(steering) == 2


@pytest.mark.anyio
async def test_failed_tools_count_once_per_turn_and_success_breaks_streak():
    instance, steering, _ = monitor(RunPolicyLimits(max_consecutive_error_turns=2))
    error = ToolResultMessage(tool_call_id="bad", tool_name="read", is_error=True)
    await turn(instance, AssistantMessage(), [error, error])
    assert not steering
    await turn(instance, AssistantMessage())
    await turn(instance, AssistantMessage(stop_reason="error"))
    assert not steering
    await turn(instance, AssistantMessage(stop_reason="aborted"))
    assert not steering
    await turn(instance, AssistantMessage(), [error])
    assert len(steering) == 1


@pytest.mark.anyio
async def test_unknown_prices_do_not_disable_other_limits():
    instance, steering, _ = monitor(RunPolicyLimits(max_cost_usd=0.1, max_tokens=10))
    # Cached input is included, reasoning/cache_write_1h are subsets, not extra tokens.
    await turn(
        instance,
        AssistantMessage(
            usage=Usage(
                input=2, output=2, cache_read=3, cache_write=3, cache_write_1h=2, reasoning=1
            )
        ),
    )
    assert len(steering) == 1 and "tokens" in steering[0] and "cost" not in steering[0]


@pytest.mark.anyio
async def test_catalog_cost_uses_cached_rates_and_counts_failed_response_usage():
    seen = []

    def price(provider, model, tokens):
        seen.append((provider, model, tokens))
        return {"input": 1, "output": 2, "cacheRead": 0.1, "cacheWrite": 2, "cacheWrite1h": 4}

    instance, steering, _ = monitor(RunPolicyLimits(max_cost_usd=0.9), pricing=price)
    await turn(
        instance,
        AssistantMessage(
            provider="test",
            model="m",
            stop_reason="error",
            usage=Usage(
                input=100_000,
                output=100_000,
                cache_read=100_000,
                cache_write=200_000,
                cache_write_1h=100_000,
            ),
        ),
    )
    assert seen == [("test", "m", 400_000)]
    assert len(steering) == 1  # 0.91 USD


@pytest.mark.anyio
async def test_defaults_never_act():
    instance, steering, cancelled = monitor(RunPolicyLimits())
    for _ in range(4):
        await turn(
            instance,
            AssistantMessage(
                stop_reason="error", usage=Usage(total_tokens=1_000_000, cost=UsageCost(total=100))
            ),
        )
    assert not steering and not cancelled


@pytest.mark.parametrize(
    "key,value",
    [
        ("maxCostUsd", -1),
        ("maxCostUsd", True),
        ("maxCostUsd", float("nan")),
        ("maxCostUsd", float("inf")),
        ("maxCostUsd", "2"),
        ("maxTokens", 0),
        ("maxTokens", 1.5),
        ("maxTokens", True),
        ("maxConsecutiveErrorTurns", -1),
        ("action", "unknown"),
        ("action", []),
    ],
)
def test_invalid_settings_raise_config_error(key, value):
    with pytest.raises(ShellConfigError):
        shell_settings_from_json({"runPolicies": {key: value}})


@pytest.mark.parametrize("value", [[], False, "steer", 4])
def test_policy_settings_must_be_object(value):
    with pytest.raises(ShellConfigError):
        shell_settings_from_json({"runPolicies": value})


def test_settings_round_trip_ignores_unknown_keys():
    settings = shell_settings_from_json(
        {
            "shellCommandPrefix": "prefix",
            "runPolicies": {
                "maxCostUsd": 0.5,
                "maxTokens": 200,
                "maxConsecutiveErrorTurns": 3,
                "action": "cancel",
                "future": "ignored",
            },
        }
    )
    assert settings.run_policies.max_tokens == 200
    assert "future" not in settings.to_json()["runPolicies"]
    assert shell_settings_from_json(settings.to_json()) == settings
    assert shell_settings_from_json({}).to_json() == {}
    assert shell_settings_from_json({"runPolicies": None}).run_policies is None


def config(tmp_path, provider, limits):
    return CodingSessionConfig(
        provider=provider,
        model="fake",
        system="",
        tools=[],
        storage=JsonlSessionStorage(tmp_path / "session.jsonl"),
        cwd=tmp_path,
        auto_compact_enabled=False,
        run_policies=limits,
    )


@pytest.mark.anyio
async def test_session_steers_at_limit_and_starts_next_prompt_fresh(tmp_path):
    provider = FakeProvider(
        [
            [assistant_done(AssistantMessage(content="working", usage=Usage(total_tokens=10)))],
            [assistant_done(AssistantMessage(content="Summary and remaining work"))],
            [assistant_done(AssistantMessage(content="second run", usage=Usage(total_tokens=1)))],
        ]
    )
    session = await CodingSession.load(config(tmp_path, provider, RunPolicyLimits(max_tokens=10)))
    try:
        events = [event async for event in session.prompt("start")]
        assert len(provider.calls) == 2
        assert any(
            isinstance(message, UserMessage) and "Run policy exceeded" in message.text
            for message in provider.calls[1][2]
        )
        assert any(isinstance(event, QueueUpdateEvent) for event in events)
        await_collect = [event async for event in session.prompt("next")]
        assert await_collect
        assert len(provider.calls) == 3
    finally:
        await session.aclose()


@pytest.mark.anyio
async def test_session_cancel_stops_repeated_malformed_tools(tmp_path):
    provider = FakeProvider(
        [
            [
                assistant_done(
                    AssistantMessage(
                        content=[
                            ToolCall(id=f"bad-{index}", name="read", malformed_arguments_text="{")
                        ]
                    )
                )
            ]
            for index in range(4)
        ]
    )
    session = await CodingSession.load(
        config(tmp_path, provider, RunPolicyLimits(max_consecutive_error_turns=2, action="cancel"))
    )
    try:
        events = [event async for event in session.prompt("start")]
        assert events
        assert len(provider.calls) == 2
        assert session._run_policy_monitor.cancelled
        assert sum(event.type == "turn_end" for event in events) == 2
        assert sum(event.type == "agent_end" for event in events) == 1
        saved = await session._config.storage.read_all()
        results = [
            entry.message
            for entry in saved
            if hasattr(entry, "message") and isinstance(entry.message, ToolResultMessage)
        ]
        assert [result.tool_call_id for result in results] == ["bad-0", "bad-1"]
    finally:
        await session.aclose()


@pytest.mark.anyio
async def test_policy_cancel_prevents_overflow_retry(tmp_path, monkeypatch):
    provider = FakeProvider([[assistant_error("maximum context length exceeded")]])
    session = await CodingSession.load(
        config(tmp_path, provider, RunPolicyLimits(max_consecutive_error_turns=1, action="cancel"))
    )

    async def forbidden(**kwargs):
        pytest.fail("Policy cancellation must not start overflow compaction")

    monkeypatch.setattr(session, "_try_overflow_compact", forbidden)
    try:
        events = [event async for event in session.prompt("start")]
        assert len(provider.calls) == 1
        assert not any(getattr(event, "will_retry", False) for event in events)
    finally:
        await session.aclose()


@pytest.mark.anyio
async def test_monitor_moves_with_replaced_harness_and_detaches_on_close(tmp_path):
    provider = FakeProvider([[assistant_done(AssistantMessage(usage=Usage(total_tokens=1)))]] * 3)
    original = config(tmp_path, provider, RunPolicyLimits(max_tokens=1, action="cancel"))
    session = await CodingSession.load(original)
    old_harness = session._harness
    old_listener = session._on_run_policy_event
    replacement = await CodingSession.load(
        replace(original, storage=JsonlSessionStorage(tmp_path / "other.jsonl"))
    )
    await session._adopt_replacement(replacement, reason="new")
    try:
        assert old_listener not in old_harness._listeners
        assert replacement._on_run_policy_event not in session._harness._listeners
        events = [event async for event in session.prompt("go")]
        assert events and len(provider.calls) == 1
        assert session._run_policy_monitor.cancelled
    finally:
        await session.aclose()
    assert session._policy_unsubscribe is None


@pytest.mark.anyio
async def test_continue_resets_run_but_overflow_retry_does_not(tmp_path, monkeypatch):
    provider = FakeProvider(
        [
            [assistant_error("maximum context length exceeded")],
            [assistant_error("maximum context length exceeded")],
            [assistant_done(AssistantMessage(content="recovered"))],
        ]
    )
    session = await CodingSession.load(
        config(tmp_path, provider, RunPolicyLimits(max_consecutive_error_turns=2))
    )

    async def compact(**kwargs):
        return True

    monkeypatch.setattr(session, "_try_overflow_compact", compact)
    try:
        events = [event async for event in session.prompt("go")]
        assert len(provider.calls) == 2
        assert len(session.queued_steering_messages) == 1
        assert any(isinstance(event, QueueUpdateEvent) for event in events)
        assert session._run_policy_monitor.consecutive_error_turns == 2
        events = [event async for event in session.continue_()]
        assert events and len(provider.calls) == 3
        assert not session._run_policy_monitor.fired
        assert session._run_policy_monitor.consecutive_error_turns == 0
    finally:
        await session.aclose()


@pytest.mark.anyio
async def test_streaming_prompt_does_not_reset_current_budget(tmp_path):
    provider = FakeProvider(
        [
            [assistant_done(AssistantMessage(content="one", usage=Usage(total_tokens=6)))],
            [assistant_done(AssistantMessage(content="two", usage=Usage(total_tokens=4)))],
            [assistant_done(AssistantMessage(content="summary"))],
        ]
    )
    session = await CodingSession.load(config(tmp_path, provider, RunPolicyLimits(max_tokens=10)))
    queued = False
    try:
        async for event in session.prompt("start"):
            if (
                isinstance(event, MessageEndEvent)
                and isinstance(event.message, AssistantMessage)
                and not queued
            ):
                queued = True
                _ = [
                    update
                    async for update in session.prompt("keep going", streaming_behavior="steer")
                ]
        assert len(provider.calls) == 3
        assert "tokens" in session._run_policy_monitor.fired
    finally:
        await session.aclose()


@pytest.mark.anyio
async def test_print_mode_reads_policy_file_and_passes_it_to_session(tmp_path, monkeypatch):
    import json

    from tau_coding import cli
    from tau_coding import session as session_module
    from tau_coding.provider_config import OpenAICompatibleProviderConfig, ProviderSettings
    from tau_coding.shell_config import shell_settings_path

    settings = ProviderSettings(
        default_provider="local",
        providers=(
            OpenAICompatibleProviderConfig(
                name="local",
                base_url="http://localhost:11434/v1",
                models=("fake",),
                default_model="fake",
            ),
        ),
    )
    path = shell_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"runPolicies": {"maxTokens": 5, "action": "cancel"}}))

    class Provider(FakeProvider):
        async def aclose(self):
            pass

    provider = Provider(
        [
            [
                assistant_done(
                    AssistantMessage(
                        content=[ToolCall(id="a", name="missing")], usage=Usage(total_tokens=5)
                    )
                )
            ],
            [assistant_done(AssistantMessage(content="must not be requested"))],
        ]
    )
    monkeypatch.setattr(cli, "load_provider_settings", lambda: settings)
    monkeypatch.setattr(cli, "create_model_provider", lambda *args, **kwargs: provider)
    monkeypatch.setattr(session_module, "create_model_provider", lambda *args, **kwargs: provider)

    async def skip_naming(*args, **kwargs):
        pass

    monkeypatch.setattr(CodingSession, "_try_auto_name_session", skip_naming)
    await cli.run_openai_print_mode("start", "fake", tmp_path, provider_name="local")
    assert len(provider.calls) == 1
