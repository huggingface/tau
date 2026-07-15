"""Reload summary types for Tau coding-session resources."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ReloadCategorySummary:
    """Before/after state for one reload category."""

    before: int
    after: int
    changed: bool

    @property
    def delta(self) -> int:
        """Return the count delta for this category."""
        return self.after - self.before


@dataclass(frozen=True, slots=True)
class CodingReloadSummary:
    """Summary of a local coding-resource reload."""

    skills: ReloadCategorySummary
    prompt_templates: ReloadCategorySummary
    context_files: ReloadCategorySummary
    extensions: ReloadCategorySummary
    diagnostics: ReloadCategorySummary
    system_prompt_rebuilt: bool
