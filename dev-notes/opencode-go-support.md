# OpenCode Go Support & Model Overrides

## Introduction

Aggregator providers such as OpenCode Go, Hugging Face, and OpenRouter serve models over different APIs. These models carry their own nuances, particularly regarding how reasoning (thinking) is driven, which API payload format is required (OpenAI-compatible vs. Anthropic Messages), and default configuration values. 

Without model-specific overrides, the flat provider-level catalog results in the UI displaying settings and labels that do nothing or are incorrect for specific models. 

This document tracks the research, design, and implementation of `model_overrides` which allows the Tau UI and provider runtime to stay honest and do what it says.

---

## 1. Research: OpenCode Go Analysis

Two sources are used to research OpenCode Go behavior:
- **models.dev** — `anomalyco/models.dev` (`providers/opencode-go/`)
- **OpenCode agent** — `anomalyco/opencode` (`packages/core` and `packages/console/app/src/routes/zen/`)

### Two APIs Behind One Base URL
The OpenCode Go Zen endpoint (`https://opencode.ai/zen/go/v1`) accepts both OpenAI-compatible and Anthropic Messages formats:
- `POST /zen/go/v1/chat/completions` (OpenAI format, used by GLM, Kimi, DeepSeek, and Mimo models).
- `POST /zen/go/v1/messages` (Anthropic format, used by Minimax and Qwen models).

Since the base URL is identical, we must dynamically adapt the client runtime configurations based on the selected model to serialize payloads correctly.

### Gaps in Thinking Option Handling
OpenCode's codebase does not consume `reasoning_options` from models.dev. It relies on named **variants** configured at runtime (`Resource.ZEN_MODELS*` from the Zen config API). The payload converter maps options as follows:
- `@ai-sdk/openai-compatible` maps `reasoningEffort` -> `reasoning_effort`.
- `@ai-sdk/anthropic` maps `effort` -> `output_config.effort` and `taskBudget` -> `output_config.task_budget`.

Because of these nuances, models have different configurations:
- **`glm-5.2`**: Exposes effort levels `high` and `max` (via `reasoning_effort`), defaulting to `max`.
- **`glm-5.1` / `kimi-k2.6` / `mimo-v2.5`**: Reasoning is always enabled and cannot be customized.
- **`minimax-m3`**: Simple toggle (on/off) using `adaptive` thinking type via Anthropic format.
- **`qwen3.7-max`**: Toggle + budget using Anthropic format.

Without individual model configurations, setting a thinking level in the UI would attempt to send parameters the model does not support or fail to enable thinking altogether.

---

## 2. Utility for Other Aggregators (Hugging Face & OpenRouter)

The model overrides system is a generic design that directly benefits other aggregators in [catalog.toml](file:///Users/ilembitov/Projects/tau/src/tau_coding/data/catalog.toml):

1. **Hugging Face (`huggingface` provider)**:
   Hugging Face is defined in [catalog.toml](file:///Users/ilembitov/Projects/tau/src/tau_coding/data/catalog.toml#L170-L195). It lists multiple heterogeneous models like `zai-org/GLM-5.2` and `deepseek-ai/DeepSeek-V4-Pro`. Using overrides, we can now configure distinct thinking parameter mappings and values without changing Python source files.
2. **OpenRouter (`openrouter` provider)**:
   OpenRouter is defined in [catalog.toml](file:///Users/ilembitov/Projects/tau/src/tau_coding/data/catalog.toml#L123-L149). It aggregates models from different vendors (`anthropic/claude-sonnet-4.6`, `google/gemini-3.5-pro`, `z-ai/glm-5.2`, `minimax/minimax-m3`). With `model_overrides`, we can enable thinking options or define specific token limits on a per-model basis for OpenRouter.

---

## 3. How the PR Fixes the Issue

We add `model_overrides` tables directly inside the catalog TOML schema. This resolves the settings gap and keeps the UI honest:

### Declarative Schema
In [catalog.toml](file:///Users/ilembitov/Projects/tau/src/tau_coding/data/catalog.toml#L265-L315), model-specific overrides are defined:
```toml
[providers.model_overrides."glm-5.2"]
thinking_default = "high"
thinking_modes = {high = {api_value = "high"}, xhigh = {api_value = "max", label = "max"}}
```

### Type-Safe Parsing & Validation
[catalog_loader.py](file:///Users/ilembitov/Projects/tau/src/tau_coding/catalog_loader.py) uses Pydantic validation to verify overrides, matching them against known `models` lists, and serializes them round-trip for user catalog overlays.

### Runtime Routing & Config Swap
- In [provider_runtime.py](file:///Users/ilembitov/Projects/tau/src/tau_coding/provider_runtime.py), `create_model_provider` dynamically swaps the configuration to `AnthropicProviderConfig` and instantiates `AnthropicProvider` if the model's override has `kind = "anthropic"`.
- [provider_config.py](file:///Users/ilembitov/Projects/tau/src/tau_coding/provider_config.py) resolves default levels and thinking modes dynamically based on the overrides, translating canonical levels (e.g., `xhigh`) to model-specific API parameters (e.g., `max`).

### Accurate UI Feedback
- The UI controls read [session.py](file:///Users/ilembitov/Projects/tau/src/tau_coding/session.py) properties like `thinking_is_always_on` and `thinking_level_label`.
- [widgets.py](file:///Users/ilembitov/Projects/tau/src/tau_coding/tui/widgets.py) and [commands.py](file:///Users/ilembitov/Projects/tau/src/tau_coding/commands.py) update status messages from "unavailable" to "always on" or display custom labels (e.g., "on" instead of "high"), ensuring the user knows exactly what mode is active.
