import pytest

from tau_coding.thinking import (
    DEFAULT_THINKING_LEVEL,
    THINKING_LEVELS,
    next_thinking_level,
    normalize_thinking_level,
    normalize_thinking_levels,
    reasoning_effort_for_level,
)


def test_normalize_thinking_level_accepts_supported_modes() -> None:
    assert normalize_thinking_level("HIGH") == "high"
    assert normalize_thinking_level(None) == DEFAULT_THINKING_LEVEL


def test_normalize_thinking_level_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError, match="Unknown thinking mode"):
        normalize_thinking_level("maximum")


def test_next_thinking_level_cycles_supported_modes() -> None:
    assert next_thinking_level("medium") == "high"
    assert next_thinking_level("xhigh") == "max"
    assert next_thinking_level("max") == "off"
    assert next_thinking_level("missing", available=("low", "high")) == "low"
    assert THINKING_LEVELS == ("off", "minimal", "low", "medium", "high", "xhigh", "max")


def test_normalize_thinking_levels_rejects_empty_and_duplicates() -> None:
    assert normalize_thinking_levels(["OFF", "high"]) == ("off", "high")

    with pytest.raises(ValueError, match="non-empty"):
        normalize_thinking_levels([])

    with pytest.raises(ValueError, match="unique"):
        normalize_thinking_levels(["high", "HIGH"])


def test_reasoning_effort_maps_off_to_none() -> None:
    assert reasoning_effort_for_level("off") == "none"
    assert reasoning_effort_for_level("xhigh") == "xhigh"
