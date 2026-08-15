# Edit prompt templates from `/prompts`

## What changed

The `/prompts` picker now offers **Ctrl+E** for the highlighted template. Tau opens the complete Markdown source in a Textual `TextArea`; **Ctrl+S** writes it back to the template's existing path and reloads session resources. **Escape** returns without saving.

## Why

Previously the picker could only insert `/name` into the composer. Updating a template required leaving Tau, locating its winning user- or project-level file, editing it externally, then running `/reload`. In-modal editing keeps this maintenance flow inside the active session and makes the new content available immediately.

## Architecture

The feature stays in `tau_coding.tui`, Textual's adapter layer. `PromptTemplate` continues to represent discovered resources, and `CodingSession.reload()` remains the single path that republishes resource state after a file change. No Textual dependency enters `tau_agent`.

The editor loads and saves the full Markdown source rather than only parsed prompt content, preserving editable frontmatter such as `description`. Read, write, and reload failures are shown as TUI notifications.

## Validation

Automated pilot coverage opens `/prompts`, invokes **Ctrl+E**, edits and saves a temporary template, and verifies both the file content and resource reload. Manual validation:

1. Create `~/.agents/prompts/example.md`.
2. Start Tau and run `/prompts`.
3. Highlight `/example`, press **Ctrl+E**, change the Markdown, and press **Ctrl+S**.
4. Confirm the picker returns and `/example` uses the updated template without a manual `/reload`.
