"""Optional runtime model-catalog discovery contracts for provider adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from tau_ai.model_limits import RuntimeModelLimits

RuntimeInputModality = Literal["text", "image"]
RuntimeThinkingLevel = Literal["off", "minimal", "low", "medium", "high", "xhigh", "max"]


@dataclass(frozen=True, slots=True)
class RuntimeModel:
    """One model advertised by an authenticated provider catalog."""

    id: str
    name: str | None = None
    limits: RuntimeModelLimits | None = None
    input_modalities: tuple[RuntimeInputModality, ...] = ("text",)
    thinking_levels: tuple[RuntimeThinkingLevel, ...] = ()
    default_thinking_level: RuntimeThinkingLevel | None = None

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("model id must be non-empty")
        if not self.input_modalities:
            raise ValueError("input_modalities must be non-empty")
        if self.default_thinking_level is not None and (
            self.default_thinking_level not in self.thinking_levels
        ):
            raise ValueError("default_thinking_level must be advertised in thinking_levels")


@dataclass(frozen=True, slots=True)
class RuntimeModelCatalog:
    """Complete account-specific model snapshot from a provider."""

    models: tuple[RuntimeModel, ...]

    def model(self, model_id: str) -> RuntimeModel | None:
        """Return one advertised model by exact ID."""
        return next((model for model in self.models if model.id == model_id), None)


@runtime_checkable
class ModelCatalogProvider(Protocol):
    """Optional provider capability for authenticated model discovery."""

    async def discover_models(self) -> RuntimeModelCatalog:
        """Return the complete model inventory available to the active account."""
        ...
