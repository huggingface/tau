# Plan: Trimming local changes for Upstream Alignment

To make the `opencode-go-support` PR minimal and easily reviewable for upstream merge, we can eliminate several custom metadata fields we introduced locally by utilizing existing upstream architecture.

## Proposed Simplifications

### 1. Eliminate `kind` metadata field
- **Local implementation**: Introduced `kind: ProviderKind` on model metadata to dynamically morph the client to Anthropic (`AnthropicProviderConfig`).
- **Upstream alignment**: Upstream already has an `api: ProviderApi` field on model metadata. We can check if `metadata.api == "anthropic-messages"` to trigger the dynamic Anthropic swap.
- **Action**: Remove `kind` field; update `provider_runtime.py` to check `metadata.api == "anthropic-messages"`.

### 2. Eliminate `always_thinking` metadata field
- **Local implementation**: Added `always_thinking: bool` to mark models with always-on reasoning.
- **Upstream alignment**: We can derive always-on status dynamically by checking if the model is declared as a reasoning model (`reasoning = true`), but supports no custom levels (`provider_thinking_levels(...) == ()`).
- **Action**: Remove `always_thinking` field; update `provider_thinking_is_always_on` and TUI widgets to compute this dynamically.

### 3. Eliminate `thinking_default` metadata override
- **Local implementation**: Added model-level default level overrides.
- **Upstream alignment**: Upstream's `provider_default_thinking_level` already has a fallback chain: if the provider's default level is not in the model's supported levels, it falls back to the first available level (e.g. `high` for `deepseek-v4-pro`).
- **Action**: Remove `thinking_default` override field.

### 4. Retain only necessary fields
- We only need to keep `thinking_level_labels` (e.g. mapping `high` -> `"on"` in UI for toggle-only models like Minimax M3) and the Anthropic `thinking_type` parameter to support toggle-only payloads.

---

## Verification Plan

1. Modify `src/tau_coding/provider_catalog.py` and `provider_config.py` to remove the redundant fields.
2. Update `data/catalog.toml` to clean up the overrides.
3. Run `uv run pytest` to ensure all tests still pass with the simplified logic.
