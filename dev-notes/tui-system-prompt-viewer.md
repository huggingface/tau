# Scrollable `/system` viewer

## What changed

The TUI now opens the active system prompt in the existing scrollable command-output modal when the user runs `/system`. The prompt no longer occupies a large inline transcript block, and the modal keeps the prompt text literal so XML-style resource sections and Markdown-like instructions remain easy to inspect exactly as sent to the provider.

Use **Up**/**Down** to scroll and **Enter**/**Escape** to close the viewer.

## Why

System prompts can include tool instructions, project context, and loaded-skill metadata. Rendering that content inline made it hard to distinguish the command output from the conversation and hard to inspect long prompts. The modal gives the output a focused viewport without adding local command output to the durable session or model context.

## Architecture

The behavior stays in `tau_coding.tui`: slash-command semantics remain in `tau_coding.commands`, while the Textual frontend chooses whether command output is transcript content or a focused modal. This preserves the separation between the reusable session layer and UI presentation policy.

## Testing

The Textual pilot test for `/system` verifies that the command opens the modal, leaves transcript state unchanged, preserves the complete prompt, and exposes scrollable output for long prompts:

```bash
uv run pytest tests/test_tui_app.py -k system
```
