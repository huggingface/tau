# OpenCode Go & Zen Free Models Capabilities

| Model Name | Display Name | Base URL | API | Thinking | Default Level | Thinking Modes | API Parameter / Payload Mapped | Context Window | Max Output / Reasoning Tokens | Additional Details |
|---|---|---|---|---|---|---|---|---|---|---|
| **Go Models** | | | | | | | | | | |
| `deepseek-v4-pro` | DeepSeek V4 Pro | `https://opencode.ai/zen/go/v1` | OpenAI | Always on | `high` | `high`, `xhigh` (max) | `reasoning_effort` | 1,000,000 | 384,000 | Effort values: `"high"`, `"max"`. Inherits from `deepseek/deepseek-v4-pro`. |
| `deepseek-v4-flash` | DeepSeek V4 Flash | `https://opencode.ai/zen/go/v1` | OpenAI | Always on | `high` | `high`, `xhigh` (max) | `reasoning_effort` | 1,000,000 | 384,000 | Effort values: `"high"`, `"max"`. Inherits from `deepseek/deepseek-v4-flash`. |
| `glm-5.2` | GLM 5.2 | `https://opencode.ai/zen/go/v1` | OpenAI | Always on | `high` | `high`, `xhigh` (max) | `reasoning_effort` | 1,000,000 | 131,072 | Effort values: `"high"`, `"max"`. Inherits from `zhipuai/glm-5.2`. |
| `glm-5.1` | GLM 5.1 | `https://opencode.ai/zen/go/v1` | OpenAI | Always on | N/A | None | None | 202,752 | 32,768 | Always-on reasoning. No reasoning options defined in TOML. |
| `kimi-k2.7-code` | Kimi K2.7 Code | `https://opencode.ai/zen/go/v1` | OpenAI | Always on | N/A | None | None | 262,144 | 262,144 | Always-on reasoning. Resolved from compiled models.dev catalog. |
| `kimi-k2.6` | Kimi K2.6 | `https://opencode.ai/zen/go/v1` | OpenAI | Always on | N/A | None | None | 262,144 | 65,536 | Always-on reasoning. No reasoning options defined in TOML. |
| `mimo-v2.5-pro` | MiMo V2.5 Pro | `https://opencode.ai/zen/go/v1` | OpenAI | Always on | N/A | None | None | 1,048,576 | 128,000 | Always-on reasoning. No reasoning options defined in TOML. |
| `mimo-v2.5` | MiMo V2.5 | `https://opencode.ai/zen/go/v1` | OpenAI | Always on | N/A | None | None | 1,000,000 | 128,000 | Always-on reasoning. No reasoning options defined in TOML. |
| `minimax-m3` | MiniMax M3 | `https://opencode.ai/zen/go/v1` | Anthropic | Toggleable | `high` | `off`, `high` (on) | `thinking` type (`adaptive` / `disabled`) | 1,000,000 | 131,072 | Mapped toggle: `high` -> `"adaptive"`. Inherits from `minimax/MiniMax-M3`. |
| `minimax-m2.7` | MiniMax M2.7 | `https://opencode.ai/zen/go/v1` | Anthropic | Always on | N/A | None | None | 204,800 | 131,072 | Always-on reasoning. No reasoning options defined in TOML. |
| `qwen3.7-max` | Qwen 3.7 Max | `https://opencode.ai/zen/go/v1` | Anthropic | Toggleable | `high` | `off` (disabled), `low` (2048), `medium` (4096), `high` (8192), `xhigh` (16384) | `output_config.task_budget` | 1,000,000 | 65,536 | Supports `toggle` and `budget_tokens` (max 262,144 reasoning tokens). |
| `qwen3.7-plus` | Qwen 3.7 Plus | `https://opencode.ai/zen/go/v1` | Anthropic | Toggleable | `high` | `off` (disabled), `low` (2048), `medium` (4096), `high` (8192), `xhigh` (16384) | `output_config.task_budget` | 1,000,000 | 65,536 | Supports `toggle` and `budget_tokens` (max 262,144 reasoning tokens). |
| `qwen3.6-plus` | Qwen 3.6 Plus | `https://opencode.ai/zen/go/v1` | Anthropic | Toggleable | `high` | `off` (disabled), `low` (2048), `medium` (4096), `high` (8192), `xhigh` (8192/16384) | `output_config.task_budget` | 1,000,000 | 65,536 | Supports `toggle` and `budget_tokens` (max 81,920 reasoning tokens). |
| **Zen Free Models** | | | | | | | | | | |
| `big-pickle` | Big Pickle | `https://opencode.ai/zen/v1` | OpenAI | Always on | N/A | None | None | 200,000 | 32,000 | Always-on reasoning. Routed to Zen v1. |
| `deepseek-v4-flash-free` | DeepSeek V4 Flash Free | `https://opencode.ai/zen/v1` | OpenAI | Toggleable | `high` | `off`, `high`, `xhigh` (max) | `reasoning_effort` | 200,000 | 128,000 | Mapped to Zen v1. Effort values: `"high"`, `"max"`. |
| `hy3-free` | Hy3 Free | `https://opencode.ai/zen/v1` | OpenAI | Always on | N/A | None | None | 256,000 | 64,000 | Always-on reasoning. Routed to Zen v1. |
| `mimo-v2.5-free` | MiMo V2.5 Free | `https://opencode.ai/zen/v1` | OpenAI | Always on | N/A | None | None | 200,000 | 32,000 | Always-on reasoning. Routed to Zen v1. |
| `nemotron-3-ultra-free` | Nemotron 3 Ultra Free | `https://opencode.ai/zen/v1` | OpenAI | Always on | N/A | None | None | 1,000,000 | 128,000 | Always-on reasoning. Routed to Zen v1. |
| `north-mini-code-free` | North Mini Code Free | `https://opencode.ai/zen/v1` | OpenAI | Toggleable | `high` | `off` (none), `high` | `reasoning_effort` | 256,000 | 64,000 | Inherits from `cohere/north-mini-code-1-0`. Effort values: `"none"`, `"high"`. |

## Sources Used

- `anomalyco/models.dev` repository on GitHub (`dev` branch):
  - [opencode-go/models/](https://github.com/anomalyco/models.dev/tree/dev/providers/opencode-go/models)
  - [opencode/models/](https://github.com/anomalyco/models.dev/tree/dev/providers/opencode/models)
  - Compiled [models.json](https://models.dev/models.json) (compiled models.dev catalog registry)
- `anomalyco/opencode` repository on GitHub (`dev` branch):
  - [opencode.ai/zen/go/v1](https://github.com/anomalyco/opencode/tree/dev)

## Gaps & Upstream Capabilities Limitations

The following capabilities of OpenCode Go / Zen Free models are currently unsupported in upstream Tau and require local implementations:

### 1. Hybrid Protocols per Provider (Mixed OpenAI & Anthropic API models)
- **Problem**: Upstream Tau strictly assumes a provider serves models via a single client/API protocol class (either `openai-compatible` or `anthropic`/Messages API). Under a unified aggregator like `opencode-go`, some models use the OpenAI `/chat/completions` schema, while others (like Qwen and Minimax) are served via the Anthropic `/messages` API.
- **Solution**: Check model-specific metadata configuration `api = "anthropic-messages"` inside `create_model_provider` and dynamically morph the configuration class into `AnthropicProviderConfig` at runtime.

### 2. Toggle-Only Anthropic Thinking (MiniMax M3)
- **Problem**: Upstream's Anthropic provider handles thinking via either `budget` mode (which expects an integer token count) or `adaptive` mode (which always appends `output_config.effort` to the request payload). Toggle-only models like `minimax-m3` reject the `effort` config payload and require a pure `{ "thinking": { "type": "adaptive" } }` payload.
- **Solution**: Introduce the `thinking_type: Literal["adaptive", "disabled"]` runtime parameter to bypass the `output_config` payload and send a pure toggle to the endpoint.

### 3. Model-Specific Thinking Level Display Labels
- **Problem**: Provider-level thinking levels are mapped to standard canonical levels (like `high`, `xhigh`). Some models require mapping these canonical levels to custom UI display labels (e.g., mapping `high` to `"on"` or `"none"` in the CLI/TUI). Upstream does not support model-specific display label overrides.
- **Solution**: Implement `thinking_level_labels` mapping on the model catalog metadata to translate canonical levels to display strings for CLI/TUI widgets.

### 4. Always-On Reasoning Models
- **Problem**: Upstream has no native way to declare always-on reasoning models (models where reasoning cannot be toggled off or customized, but thinking parameters must NOT be sent in the API payload to prevent errors). In upstream, excluding a model from the provider's `thinking_models` list disables its reasoning display entirely (rendering it as `"unavailable"` in the UI).
- **Solution**: Set `reasoning = true` on the model metadata and exclude the model from the provider's `thinking_models` list. Our implementation detects these models by verifying that `reasoning` is `True` while supported `levels` is empty `()`. The TUI and CLI then format these models with the static `"always on"` label and disable custom level adjustments.
