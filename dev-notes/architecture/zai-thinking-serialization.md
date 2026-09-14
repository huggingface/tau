# Z.AI thinking serialization

## What changed

OpenAI-compatible requests for Z.AI now preserve Tau's logical thinking state
when `supportsReasoningEffort` is false. The request uses Z.AI's documented
`thinking` object:

```json
{"thinking": {"type": "enabled"}}
```

and sends `type: "disabled"` for Tau's `off` mode. It no longer sends the
unrecognized `enable_thinking` field.

Z.AI documents `reasoning_effort` as a separate field supported only by
GLM-5.2 and newer, with model-dependent values. Tau emits that field only when
compatibility metadata says the selected model supports it. This keeps the
provider-wide guard for models such as GLM-5.1 while allowing an explicit
model-level opt-in when catalog evidence supports it.

## Why

The old request builder filtered the logical effort to `None` before provider
serialization whenever `supportsReasoningEffort` was false. Z.AI's serializer
then saw thinking as disabled, even when the user selected `high`. Separating
logical toggle handling from raw effort-field support fixes the state loss
without enabling unsupported fields for other providers.

## Validation

Offline `httpx.MockTransport` tests cover:

- Z.AI enabled and disabled thinking payloads;
- Z.AI models with and without the separately supported effort field; and
- the existing guard that omits unsupported OpenAI `reasoning_effort` fields.

Protocol evidence:

- <https://docs.z.ai/guides/capabilities/thinking>
- <https://docs.z.ai/api-reference/llm/chat-completion>
