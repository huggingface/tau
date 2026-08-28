# Codex TLS transport guidance

## What changed

OpenAI Codex TLS failures now show actionable provider-connection guidance instead of only an opaque OpenSSL message. The message explains that the failure can come from the upstream provider or a proxy/VPN, and suggests retrying, checking provider usage, and checking network settings.

## Why it exists

A Codex stream ended with `SSLV3_ALERT_BAD_RECORD_MAC`; the next request reported exhausted usage. A malformed TLS close cannot prove a quota failure, and it can also originate in an intermediary, so Tau must not misclassify it as an authentication or usage error. The previous raw OpenSSL text did not explain those possibilities.

## Architecture

The classification stays in `tau_ai.openai_codex`, alongside Codex transport retries. Recognized TLS exception chains and common TLS markers receive the friendly terminal message. Other HTTP transport errors retain their original message. The original TLS exception type and message remain in provider diagnostics, and `tau_coding.diagnostics` copies only their bounded scalar fields to `~/.tau/logs/agent-calls.jsonl`.

## How to test

```bash
uv run pytest tests/test_tau_ai.py -k 'openai_codex_provider_explains_tls or openai_codex_provider_preserves_non_tls'
uv run pytest tests/test_coding_session.py -k provider_transport_error
```

Manual validation requires a Codex TLS failure: the TUI should identify a TLS transport error, mention the provider/proxy/VPN possibilities, and retain the raw OpenSSL failure in `agent-calls.jsonl`.
