"""Provider-neutral transcript message models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    model_serializer,
)

from tau_agent.tools import ToolCall
from tau_agent.types import JSONValue


class UsageCost(BaseModel):
    """Billed cost breakdown for a single provider response, in USD.

    Ports Pi's `Usage.cost`. Populated only when a per-model pricing table is
    available; Tau has none yet, so providers leave the enclosing ``Usage.cost``
    set to ``None`` (see the phase-21 ruling).
    """

    model_config = ConfigDict(extra="forbid")

    input: float = 0.0
    output: float = 0.0
    cache_read: float = 0.0
    cache_write: float = 0.0
    total: float = 0.0


class Usage(BaseModel):
    """Real billed token usage reported by a provider for one assistant response.

    Ports Pi's `Usage` interface (packages/ai/src/types.ts) to snake_case. Token
    counts are the provider's billed figures, not local estimates. ``reasoning``
    is a subset of ``output`` (already included in it). ``cache_write_1h`` is a
    subset of ``cache_write`` and is only reported by Anthropic.
    """

    model_config = ConfigDict(extra="forbid")

    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    cache_write_1h: int | None = None
    reasoning: int | None = None
    total_tokens: int = 0
    cost: UsageCost | None = None


class UserMessage(BaseModel):
    """A message authored by the user.

    ``custom_type``/``details`` are optional presentation metadata attached by
    an extension via ``send_custom_message``. They are benign for the model
    (which still reads ``content``) and let a frontend render the message with a
    registered custom renderer instead of the raw content. Both default to
    ``None`` so sessions persisted before these fields existed still load, and
    both are **omitted from serialization when None** so a session that never
    uses custom messages stays byte-identical to the pre-metadata wire format
    (old binaries use ``extra="forbid"`` and would reject unknown keys).
    """

    model_config = ConfigDict(extra="forbid")

    role: Literal["user"] = "user"
    content: str
    custom_type: str | None = None
    details: dict[str, JSONValue] | None = None

    @model_serializer(mode="wrap")
    def _omit_unused_custom_metadata(
        self, handler: SerializerFunctionWrapHandler
    ) -> dict[str, Any]:
        """Drop ``custom_type``/``details`` keys when unset (forward compat).

        Targeted on purpose: only these two fields are conditional, so the wire
        semantics of every other field (including explicit Nones elsewhere in
        the entry models) are unchanged.
        """
        data: dict[str, Any] = handler(self)
        if data.get("custom_type") is None:
            data.pop("custom_type", None)
        if data.get("details") is None:
            data.pop("details", None)
        return data


class AssistantMessage(BaseModel):
    """A message authored by the assistant.

    ``usage`` defaults to ``None`` and is **omitted from serialization when
    None**, for the same forward-compat reason as ``UserMessage``'s custom
    metadata: old binaries use ``extra="forbid"``, and virtually every session
    contains an assistant message, so an always-present ``"usage": null`` key
    would make every new session file unreadable by them.
    """

    model_config = ConfigDict(extra="forbid")

    role: Literal["assistant"] = "assistant"
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: Usage | None = None

    @model_serializer(mode="wrap")
    def _omit_unused_usage(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        """Drop the ``usage`` key when unset (forward compat, see class docs)."""
        data: dict[str, Any] = handler(self)
        if data.get("usage") is None:
            data.pop("usage", None)
        return data


class ToolResultMessage(BaseModel):
    """A transcript message containing the result of a previous tool call."""

    model_config = ConfigDict(extra="forbid")

    role: Literal["tool"] = "tool"
    tool_call_id: str
    name: str
    content: str
    ok: bool = True
    data: dict[str, JSONValue] | None = None
    details: dict[str, JSONValue] | None = None
    error: str | None = None


type AgentMessage = UserMessage | AssistantMessage | ToolResultMessage
