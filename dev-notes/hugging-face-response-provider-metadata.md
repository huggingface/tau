# Hugging Face response-provider metadata

Tau's built-in `huggingface` provider uses Hugging Face's OpenAI-compatible
router. An unsuffixed model leaves provider selection automatic, so the backing
Inference Provider can differ between requests and can change after a failure.

Hugging Face reports the provider that handled an HTTP response in the
`x-inference-provider` header. Tau now preserves that value on the resulting
`AssistantMessage` as `response_provider` (`responseProvider` in serialized
messages). The existing `provider` field remains the logical Tau provider,
`huggingface`.

This is intentionally per-response metadata rather than session metadata. The
OpenAI-compatible adapter reads the header only when the runtime configuration
enables it, and the built-in Hugging Face configuration is the only configuration
that does so. The provider-neutral stream bridge copies the resolved value to
streaming partials and the final persisted assistant message. Failed responses
also retain the value when the header is present.

Because the value comes from each successful HTTP response, a retry or future
session-level failover records the replacement provider on the response it
actually served. Tau does not infer a provider from the requested model and does
not expose provider selection or pinning controls in this change.

## Validation

Focused tests use an `httpx.MockTransport` to simulate one failed response from
one Inference Provider followed by a successful response from another. They
verify that only the provider from the successful request becomes the final
message's `response_provider`, while `provider` remains `huggingface`.

Run:

```bash
uv run pytest tests/test_tau_ai.py tests/test_provider_config.py tests/test_agent_types.py
uv run ruff check .
uv run ruff format --check .
uv run mypy
```
