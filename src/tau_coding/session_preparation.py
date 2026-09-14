"""Shared trust-aware staging for coding-session startup and replacement."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from tau_agent.provider import ModelProvider
from tau_coding.session import CodingSession, CodingSessionConfig


@dataclass(frozen=True, slots=True)
class SessionPreparationRequest:
    """Frontend-neutral request for a staged coding session."""

    storage: object
    destination_cwd: Path
    model: str
    provider: str | None = None
    session_provider: str | None = None
    session_id: str | None = None


@dataclass(slots=True)
class PreparedCodingSession:
    """A candidate session whose resources are not authoritative until adopted."""

    session: CodingSession
    _state: str = "prepared"

    @property
    def provider(self) -> ModelProvider:
        return self.session.provider

    async def adopt(self) -> CodingSession:
        """Commit staged entries, then transfer the candidate's ownership."""
        if self._state != "prepared":
            raise RuntimeError(f"Prepared session is already {self._state}")
        trust_resolution = getattr(self.session, "project_trust_resolution", None)
        if trust_resolution is not None and trust_resolution.cancelled:
            await self.abort()
            raise ValueError("Project trust decision cancelled; session was not adopted")
        try:
            commit = getattr(self.session, "_commit_prepared_entries", None)
            if commit is not None:
                await commit()
        except BaseException:
            await self.abort()
            raise
        self._state = "adopted"
        return self.session

    async def abort(self) -> None:
        """Close an unpublished candidate exactly once."""
        if self._state != "prepared":
            return
        self._state = "aborted"
        close = getattr(self.session, "aclose", None)
        if close is not None:
            await close()


async def prepare_coding_session(
    config: CodingSessionConfig,
    *,
    session_loader: type[CodingSession] | None = None,
) -> PreparedCodingSession:
    """Prepare a session through the shared trust/provider lifecycle.

    Application frontends use this entry point with authoritative writes
    deferred. Adoption appends the complete staged batch before exposing the
    candidate. Supplying a provider remains the compatibility seam for static
    embedded callers, which retain the historical lazy initial write.
    """
    # Every frontend gets the same candidate-first durability boundary, even
    # when it supplies a compatibility provider object. Provider ownership is
    # controlled independently by CodingSessionConfig.owns_initial_provider.
    staged_config = replace(config, defer_authoritative_writes=True)
    loader = session_loader or CodingSession
    session = await loader.load(staged_config)
    return PreparedCodingSession(session)


__all__ = [
    "PreparedCodingSession",
    "SessionPreparationRequest",
    "prepare_coding_session",
]
