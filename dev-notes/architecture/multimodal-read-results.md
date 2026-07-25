# Multimodal read-tool results

Tau's `read` tool now detects supported images from file magic and returns them
as canonical `ImageContent` blocks alongside its short text note. Attachments
are capped at 5 MB, and animated PNG files are not treated as supported static
PNG attachments. The agent loop already preserves ordered
tool-result content, so image data remains provider-neutral until the `tau_ai`
serialization boundary.

Each provider adapter maps image blocks to its wire format:

- Anthropic nests base64 image blocks in `tool_result.content`.
- OpenAI Responses and Codex use `input_image` blocks in function-call output.
- OpenAI Chat Completions and Mistral keep the textual tool result and attach
  images in a following user message.
- Gemini 3 uses multimodal `functionResponse.parts`; older Gemini models receive
  a separate user image message.

Runtime provider configuration derives image support from the selected model's
catalog `input` metadata. Text-only models receive an explicit omission marker,
which avoids invalid provider requests and makes the missing visual context
visible to the model.

The image base64 payload moved from tool-result `details` into `content`. This
prevents duplicate session storage and lets all frontends continue rendering the
small text note without exposing base64 data.

## Validation

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
```
