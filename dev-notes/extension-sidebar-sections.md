---
title: "Extension-owned sidebar sections"
---

Tau extensions can now add host-framed sections to the interactive session
sidebar through `context.ui.sidebar`. This closes the gap between the existing
prompt-adjacent component slots and the host-owned sidebar without exposing
`TauTuiApp`, Textual container IDs, or private sidebar widgets.

## API

An extension feature-detects the facade, checks availability, then sets content
under a local stable key:

```python
sidebar = getattr(context.ui, "sidebar", None)
if sidebar is not None and sidebar.supported:
    sidebar.set_section(
        "status",
        title="build",
        content=["[green]ready[/green]"],
    )
```

Calling `set_section` again with the same key replaces the section in place.
For host-rendered display lines, Tau retains the mounted section root and updates
its title and body directly; identical contributions are no-ops. Widget factories
retain replacement semantics because the host cannot safely mutate an arbitrary
extension widget. `remove_section(key)` removes it. Keys are internally scoped by
extension name, so unrelated extensions can safely choose the same local key. Registration
order is deterministic; replacing preserves position, while remove plus re-add
moves the section to the end.

A body may be Rich-markup display lines or a `factory(theme) -> Widget`.
Display lines are preferred because they do not import Textual. A factory is
available for live or interactive content and is rebuilt when Tau's live theme
changes.

## Ownership boundary

This remains in `tau_coding`:

```text
extension event handler
        ↓
ExtensionSidebar (extension identity + generation guard)
        ↓
UiBridge sidebar methods
        ↓
TauTuiApp mounts into SessionSidebar
```

`tau_agent` is unchanged. The host owns section headings, separators, width,
wrapping, scrolling, left/right placement, responsive hiding, and teardown.
Extensions never receive a sidebar container.

`ExtensionSidebar` carries the extension name so host keys are
`(extension_name, local_key)`, unlike the older raw component bridge's global
keys. The generation guard rejects a facade captured before `/reload`.

## Availability and lifecycle

The sidebar reports unsupported in print/headless mode and when
`sidebar_position` is `"off"`; setters then safely do nothing and do not invoke
widget factories. Responsive hiding is different: contributions remain mounted
and return when the terminal grows.

The existing `clear_components()` lifecycle path also clears sidebar sections.
It runs for `/reload`, `/new`, `/resume`, related session replacement flows,
and application teardown. Extensions should normally mount in `session_start`,
after the frontend bridge exists, and may explicitly remove their sections from
`session_shutdown`.

## Failure isolation

Factory and mount failures keep the prior UI usable, produce the existing
extension-component notification, and add an extension-owned runtime diagnostic.
A body that crashes during `render` or `on_mount` is found through the existing
tracked-widget traceback boundary, quarantined, and removed without terminating
the TUI.

## Verification

Focused coverage lives in `tests/test_extensions.py` and
`tests/test_tui_components.py`. It exercises ownership, validation, update and
ordering semantics, removal, disabled and responsive sidebars, live themes,
factory isolation, headless no-ops, stale generations, and lifecycle cleanup.
Run the complete project gate with:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
```
