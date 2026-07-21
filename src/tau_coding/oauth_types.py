"""Provider-neutral OAuth contracts used by Tau's coding application."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from tau_agent.types import JSONValue
from tau_coding.credentials import OAuthCredential

OAuthFlowKind = Literal["browser", "device_code"]


@dataclass(frozen=True, slots=True)
class OAuthAuthInfo:
    """Authorization URL and optional instructions for a browser flow."""

    url: str
    instructions: str | None = None


@dataclass(frozen=True, slots=True)
class OAuthDeviceCodeInfo:
    """User-facing values returned by an OAuth device authorization request."""

    user_code: str
    verification_uri: str
    interval_seconds: float | None = None
    expires_in_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class OAuthPrompt:
    """Text input requested by an OAuth provider before or during login."""

    message: str
    placeholder: str | None = None
    allow_empty: bool = False


@dataclass(frozen=True, slots=True)
class OAuthSelectOption:
    """One choice in a provider-defined OAuth selection prompt."""

    id: str
    label: str


@dataclass(frozen=True, slots=True)
class OAuthSelectPrompt:
    """Selection input requested by an OAuth provider."""

    message: str
    options: tuple[OAuthSelectOption, ...]


@dataclass(frozen=True, slots=True)
class OAuthRuntimeAuth:
    """Request authentication derived from a stored OAuth credential."""

    api_key: str
    base_url: str | None = None
    headers: Mapping[str, str] | None = None


AuthCallback = Callable[[OAuthAuthInfo], None]
DeviceCodeCallback = Callable[[OAuthDeviceCodeInfo], None]
PromptCallback = Callable[[OAuthPrompt], Awaitable[str]]
SelectCallback = Callable[[OAuthSelectPrompt], Awaitable[str | None]]
ManualCodeCallback = Callable[[], Awaitable[str]]
ProgressCallback = Callable[[str], None]


@dataclass(frozen=True, slots=True)
class OAuthLoginCallbacks:
    """Frontend-independent callbacks available to an OAuth login flow."""

    on_auth: AuthCallback
    on_device_code: DeviceCodeCallback
    on_prompt: PromptCallback
    on_select: SelectCallback
    on_progress: ProgressCallback | None = None
    on_manual_code_input: ManualCodeCallback | None = None
    method: OAuthFlowKind | None = None


class OAuthProvider(Protocol):
    """Provider-specific OAuth behavior registered with Tau."""

    @property
    def id(self) -> str:
        """Stable provider/credential identifier."""
        ...

    @property
    def name(self) -> str:
        """User-facing provider name."""
        ...

    @property
    def flow_kinds(self) -> Sequence[OAuthFlowKind]:
        """Interactive flow families supported by this provider."""
        ...

    async def login(self, callbacks: OAuthLoginCallbacks) -> OAuthCredential:
        """Complete login and return credentials to persist."""
        ...

    async def refresh(self, credential: OAuthCredential) -> OAuthCredential:
        """Refresh an expired credential."""
        ...

    def runtime_auth(self, credential: OAuthCredential) -> OAuthRuntimeAuth:
        """Convert stored credentials to request auth."""
        ...


def oauth_metadata_string(
    metadata: Mapping[str, JSONValue],
    name: str,
) -> str | None:
    """Return one non-empty string from provider-specific OAuth metadata."""
    value = metadata.get(name)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None
