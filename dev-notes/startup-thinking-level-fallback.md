# Startup thinking-level fallback

## Problem

Launching `tau` with a remembered default model that does not support the
global default thinking level crashed before the TUI could open:

```text
Invalid value: Thinking mode medium is not available for kimi-code:k3.
Available modes: xhigh
```

Root cause: both startup paths — `run_tui_app` (`tui/app.py`) and
`run_openai_print_mode` (`cli.py`) — hardcoded
`thinking_level=DEFAULT_THINKING_LEVEL` (`"medium"`) when constructing the
runtime provider. `create_model_provider` treats that value as an explicit user
choice and raises `ProviderConfigError` when the model does not support it. The
session layer's existing coercion (`_coerced_thinking_level`, used when
switching models mid-session) never got a chance to run because startup died
first.

## Fix

New helper `resolve_startup_thinking_level(provider, model, *, preferred)` in
`provider_config.py` picks a valid level for the selected model with the same
precedence as mid-session model switches:

1. remembered per-model preference (`provider.thinking_defaults[model]`)
2. the global preferred level (`medium`)
3. the provider/catalog default (`provider.thinking_default`)
4. the first available level

It returns `None` when the model has no configurable thinking levels (which
disables the thinking parameter entirely).

Both startup call sites now pass the resolved level instead of the raw global
default. Explicit in-session choices (`/think xhigh`, the thinking picker, or
cycling with Shift+Tab) still validate strictly and show an error listing the
available modes — the fallback only applies to the implicit startup default.

## Mapping to Pi's design

Pi treats thinking-level capability as model-scoped data and never lets an
unsupported implicit default abort the session; Tau now follows the same rule
at startup by resolving the level from the model metadata
(`thinking_level_map` / `unsupported_thinking_levels`) instead of assuming the
global default fits every model.

## Verify

```bash
uv run pytest tests/test_provider_config.py tests/test_provider_runtime.py
uv run ruff check src/tau_coding tests
```

Manual check with a remembered `kimi-code:k3` default: `tau` and
`tau --print '...'` both open with thinking level `xhigh` (sent to the API as
`max`) instead of erroring out.
