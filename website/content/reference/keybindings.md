---
title: Keyboard shortcuts
description: Default keys for the Tau TUI, and how to remap them.
---

These are the default keys in the interactive [TUI]({{< relref "../guides/tui.md" >}}). Run
`/hotkeys` in-session to see them, and remap them in `~/.tau/tui.json` (see
[Configuration]({{< relref "./configuration.md#tui-settings" >}})).

## Prompting

| Key | Action |
| --- | --- |
| `Enter` | Submit the prompt (or apply a highlighted completion) |
| `Shift+Enter` | Insert a newline |
| `Esc` | Cancel the active run |
| `Enter` (while running) | Queue text as steering for the current run |
| `Alt+Enter` | Queue a follow-up that waits until the run would stop |
| `Up` (empty prompt, running) | Edit the most recently queued follow-up |

## Navigation & pickers

| Key | Action |
| --- | --- |
| `Ctrl+K` | Open the command palette |
| `Ctrl+R` | Open the session picker |
| `Tab` | Accept the highlighted completion |
| `Down` / `Up` | Move through completions |

## Models & thinking

| Key | Action |
| --- | --- |
| `Ctrl+P` | Cycle scoped (favorite) models |
| `Shift+Tab` | Cycle the thinking mode |
| `Ctrl+T` | Toggle display of thinking/reasoning tokens |

## Output & session

| Key | Action |
| --- | --- |
| `Ctrl+O` | Toggle full tool output (vs. compact preview) |
| `Ctrl+C` | Clear the prompt input |
| `Ctrl+D` | Quit |

{{% note title="Remapping" %}}
Keys use Textual's syntax (`ctrl+k`, `shift+tab`, `down`, `f2`, …). Tau rejects
unknown names, empty keys, and duplicate assignments so mistakes fail early. Any
key you don't set keeps its default.
{{% /note %}}
