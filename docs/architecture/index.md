# Architecture Notes

This section explains Tau's architecture as it is built, one phase at a time.

Tau is intentionally developed in small layers. Each layer should answer three questions:

1. What was added?
2. Why does it exist?
3. How will later phases use it?

## Current architecture layers

```text
tau_ai       provider/model streaming layer
tau_agent    portable agent harness, loop, tools, events, sessions
tau_coding   CLI app, resources, skills, extensions, commands, UI integration
```

The most important boundary is that `tau_agent` should stay portable. It can define the reusable agent brain, but it should not know about VS Code, Textual, Rich rendering, local config directories, slash commands, or project-specific prompts.

## Phase notes

- [Phase 1: Core Types and Events](phase-1-core-types-and-events.md)
- [Phase 2: AI Provider Layer](phase-2-ai-provider-layer.md)
- [Phase 3: Pure Agent Loop](phase-3-agent-loop.md)
- [Phase 4: AgentHarness](phase-4-agent-harness.md)
- [Phase 5: Built-in Coding Tools](phase-5-coding-tools.md)
- [Phase 6: Non-interactive Print-mode CLI](phase-6-print-mode-cli.md)
- [Phase 7: Session Tree and JSONL Persistence](phase-7-session-tree.md)
- [Phase 8: Coding Session Wrapper](phase-8-coding-session.md)
- [Phase 9: Skills and Prompt Templates](phase-9-skills-prompts.md)
- [Phase 10: System Prompt Assembly](phase-10-system-prompt.md)
- [Phase 11: Print and Event Rendering Modes](phase-11-print-event-rendering.md)
- [Phase 12: Textual TUI](phase-12-textual-tui.md)
- [Phase 13: Tau Home, Paths, and `.agents` Resources](phase-13-paths-agents-resources.md)
- [Phase 14: Session Manager and Resume](phase-14-session-manager-resume.md)
- [Phase 15: Slash Command Registry](phase-15-slash-command-registry.md)
- [Phase 16: Robust Resource Discovery](phase-16-resource-discovery.md)
- [Phase 17: TUI Slash-command Autocomplete](phase-17-tui-autocomplete.md)
- [Phase 17.5: TUI Transcript Wrapping](phase-17-5-transcript-wrapping.md)
- [Phase 23: Advanced TUI and Product Polish](phase-23-tui-polish.md)
- [Phase 18: Provider Configuration Foundation](phase-18-provider-config-foundation.md)
- [Phase 19: Project Context Discovery and Reload](phase-19-context-discovery.md)
- [Phase 20: Installation and Configuration Docs](phase-20-installation-docs.md)
- [Phase 22: Compaction Replay Foundation](phase-22-compaction-foundation.md)

More pages will be added here as each phase lands.
