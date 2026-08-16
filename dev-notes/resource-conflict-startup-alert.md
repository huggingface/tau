# Resource conflict startup alert

## What changed

The TUI now turns existing skill and prompt-template override diagnostics into one red startup alert in the transcript. Each entry names the resource and shows the winning and shadowed paths. The sidebar also groups loaded skills and prompt templates by their user or project resource directory, shows every loaded resource, and scrolls its content independently when the groups overflow while keeping the Tau brand pinned.

## Why

Tau supports user and project resources under both `.tau` and `.agents`. Precedence keeps duplicate names non-fatal, but the prior diagnostics were easy to miss. Surfacing conflicts at startup makes accidental shadowing visible without changing loading behavior.

## Architecture

Resource discovery and precedence remain in `tau_coding.skills` and `tau_coding.prompt_templates`. The Textual adapter formats relevant `ResourceDiagnostic` values and adds display-only alert items. No TUI behavior enters `tau_agent`.

## Verification

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
```
