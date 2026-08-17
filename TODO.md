# Multimodal read follow-up

Status: WIP follow-up for PR #479. Not required to complete the current provider-wire implementation.

## Live image validation

Test every catalog model declared with `input = ["text", "image"]` using an actual credential for its provider.

- [ ] Generate a provider/model matrix from the effective catalog.
- [ ] Use the same small JPEG, PNG, GIF, and WebP fixtures where each provider supports them.
- [ ] Confirm the `read` result reaches the model as visual content, not only as text metadata.
- [ ] Cover direct API, subscription/OAuth, gateway, and OpenAI-compatible transports.
- [ ] Record provider-specific limits, rejected formats, required headers, and payload differences.
- [ ] Add regression tests and compatibility metadata for every discovered exception.
- [ ] Verify text-only models still receive the explicit omission marker.

Run credentialed validation manually; do not store API keys, OAuth tokens, image payloads, or sensitive provider responses in the repository or CI artifacts.
