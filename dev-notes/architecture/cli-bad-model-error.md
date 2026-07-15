# CLI: surface bad `--model` as a clean error (issue #265)

## What changed

`tau --model <bad-model>` no longer crashes with an `anyio`/`asyncio` traceback
when the model is not declared by the selected provider. Two `except` handlers in
`src/tau_coding/cli.py` were broadened from `RuntimeError` to
`(RuntimeError, ValueError)`:

- the **TUI** startup branch (`main()` → `anyio.run(run_openai_tui, …)`), and
- the **print-mode** branch (`main()` → `anyio.run(run_openai_print_mode, …)`).

`ProviderConfigError(ValueError)` is raised by `validate_provider_model()` deep
inside the event loop (via `resolve_provider_selection()` →
`_resolve_tui_startup_selection()` / `run_openai_print_mode`). Because the
handlers only caught `RuntimeError`, the `ValueError` subclass escaped as an
unhandled exception and printed a multi-frame traceback. Catching `ValueError`
lets `typer.BadParameter` surface the existing, friendly message verbatim:

> Invalid value: Model is not configured for provider local: llama. Available
> models: qwen

The `export` command in the same file already used the correct pattern
(`except (RuntimeError, ValueError)`) and served as the reference.

## Why it exists

Reported in https://github.com/huggingface/tau/issues/265. The provider catalog
is intentionally provider-specific, so an unknown/typo'd model name is a common
user error. The actionable message (which lists the valid models for the
provider) already existed in `provider_config.validate_provider_model`; it just
wasn't being surfaced cleanly through the CLI entry points.

This keeps the validation logic in `provider_config.py` as the single source of
truth for the message and restores parity across all `--model` entry points
(TUI, print mode, and export).

## How it maps to the design

This is a pure CLI-layer fix. It does not touch:

- the portable agent harness (`tau_agent`),
- the provider/model streaming layer (`tau_ai`), or
- the Textual TUI rendering.

The validation itself already lives in `tau_coding.provider_config`; only the
CLI's error boundary was wrong. This is consistent with the architecture
principle that CLI error handling should consume errors from the lower layers
rather than letting them leak as raw tracebacks.

## How to test

Regression tests (verified to fail before the fix and pass after):

```bash
uv run pytest tests/test_cli.py -k "bad_model" -v
```

- `test_tui_surfaces_bad_model_as_clean_error`
- `test_print_mode_surfaces_bad_model_as_clean_error`

Both monkeypatch `load_provider_settings` (in both the `cli` and `tui.app`
namespaces, since each imports it) to return a provider whose only model is
`qwen`, then invoke the CLI runner with `--model llama --provider local` (TUI)
and with `-p hello` added (print mode). They assert `exit_code == 2` (Typer's
`BadParameter` convention) and that the output panel contains
`"Model is not configured for provider local: llama. Available models: qwen"`.

Full suite:

```bash
uv run pytest -q
uv run ruff check src/tau_coding/cli.py tests/test_cli.py
uv run ruff format --check src/tau_coding/cli.py tests/test_cli.py
```

## Notes / follow-ups

- The original issue draft incorrectly claimed the print-mode path already
  handled `ValueError`; that `except (RuntimeError, ValueError)` snippet was
  actually from the `export` command. Empirical reproduction confirmed
  print-mode was *also* broken, so both paths were fixed together.
- A larger follow-up (not in scope here): audit remaining `anyio.run(...)` call
  sites and extract a shared error-handling helper so all entry points use one
  handler and cannot drift apart again. Pre-loop validation (resolving the
  provider/model before entering the event loop) is another optional polish.
