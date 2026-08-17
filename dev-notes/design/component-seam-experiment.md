# Component-seam experiment: extension-owned agent UI

**Decision (2026-07-09): adopted — this is the committed design, no longer an
experiment.** Extensions need real control over the UI to be useful, and this
seam carried the full subagents UX in practice. The measured costs in §8 are
accepted and recorded: core grew rather than shrank, and Textual is now
deliberately part of the public extension contract (extensions build against
the Textual version tau pins; a Textual major bump is a coordinated break for
core and extensions together). The phase-21 strings-not-widgets Ruling is
superseded (see the follow-up Ruling in `phase-21-extensions.md`); strings
remain the preferred form wherever they suffice. Everything below is preserved
as the historical record of how the decision was made.

**Status:** implemented through Step 3 on branch `component-seam-experiment` (both
repos), plus post-experiment fixes: the pre-dispatch interceptor relocation
(§2e implementation note) and the sequenced slot/main-view swaps (§2f
implementation note, bug fix 2). Core adds the generic component seam and has
fully removed the old transcript-source seam (§3); the `tau-subagents`
extension owns the entire agent UI via `ComponentBridge`. Both suites green
(core 802, extension 116). Measured outcome recorded in §8 (updated after the
fix commits). This is the deliberate other-path exploration of
the phase-21 strings-not-widgets Ruling (`phase-21-extensions.md` L416–442). The
Ruling's reopen triggers and the "preferred middle ground" (a declarative UI
layer) are unchanged; this branch instead tests the raw-widget seam so we can
measure the contract cost the Ruling predicted rather than argue it in the
abstract.

The experiment migrates the **entire** agent UI — agents strip, in-place
conversation view, steer composer, two-press stop — out of tau core and into the
`tau-subagents` extension, replacing the current generic *transcript-sources*
data seam with a pi-style *component* seam. Core keeps only a generic,
agent-agnostic widget-hosting layer.

Design order below follows the porting rule: pi's surface first, then the tau
analog, deviations flagged **Ruling-style**.

---

## 1. Pi's actual surface (what we are porting)

### 1a. `ctx.ui` component-related API

All from `packages/coding-agent/src/core/extensions/types.ts` in
`earendil-works/pi`,
interface `ExtensionUIContext` (L125–276). Only the component-relevant members:

| Member | Signature (abridged) | Semantics |
|---|---|---|
| `setWidget` | `(key, string[] \| ((tui, theme) => Component & {dispose?}) \| undefined, {placement?}) ` — L163–169 | Persistent widget above/below the editor. String-array or **factory callback** form; the callback re-runs on theme change and is re-invoked via `tui.requestRender()` without re-mounting. `undefined` removes it. `placement`: `"aboveEditor"` (default) \| `"belowEditor"` — L97–104. |
| `custom<T>` | `(factory:(tui,theme,keybindings,done)=>Component\|Promise<Component>, {overlay?, overlayOptions?, onHandle?}) => Promise<T>` — L190–204 | Show a **focus-capturing** component. `done(result)` resolves the promise and tears it down. `overlay:true` floats it via `showOverlay`; `overlayOptions` is `OverlayOptions` or a `()=>OverlayOptions` re-evaluated per frame; `onHandle` receives the `OverlayHandle` for visibility control. This is what the conversation viewer uses. |
| `onTerminalInput` | `(handler:(data:string)=>{consume?,data?}\|undefined) => (()=>void)` — L107, L139 | **Pre-editor raw-input listener.** Fires *before* the focused editor, can `consume` a key or rewrite `data`. Returns an unsubscribe fn. The fleet list routes ALL its nav keys through this, self-gated on `getEditorText()===""`. |
| `setHeader` / `setFooter` | `((tui,theme[,footerData]) => Component & {dispose?}) \| undefined` — L177–184 | Replace the startup header / status footer with a custom component. Not needed by subagents; not ported. |
| `setEditorComponent` / `getEditorComponent` | `(EditorFactory \| undefined)` — L119, L254–257 | Swap the input editor itself (vim mode etc.). Not needed by subagents; not ported. |
| `getEditorText` / `setEditorText` / `pasteToEditor` | L207–213 | Read/write the core editor buffer. The fleet list reads it to gate activation. |
| `theme` | `readonly Theme` — L260 | Current theme, handed into every factory callback. |
| `notify` / `setStatus` / `setTitle` | L136, L142, L187 | Toasts, footer status keys, terminal title. `notify` and `setStatus` are used by the widgets. |
| `select` / `confirm` / `input` | L127–133 | Host dialogs. **Stay host-side in tau** (constraint 3); already ported. |

### 1b. pi-tui `Component` lifecycle

From `packages/tui/src/tui.ts` in `earendil-works/pi`:

- **`Component`** (L64–88): `render(width:number):string[]`; optional
  `handleInput?(data:string):void` (only when focused); optional
  `wantsKeyRelease?:boolean` (Kitty press/release filtering); `invalidate():void`
  (drop cached render state — called on theme change / forced redraw). Widget
  factories additionally may expose `dispose?()`.
- **`Focusable`** (L104–112): a `focused:boolean` the TUI sets; the component
  emits `CURSOR_MARKER` (L120) at the cursor so the host positions the hardware
  cursor. `isFocusable()` type-guard.
- **Overlays**: `TUI.showOverlay(component, options):OverlayHandle` (L493);
  `OverlayHandle` (L218–231) = `hide/setHidden/isHidden/focus/unfocus/isFocused`;
  `OverlayOptions` (L171–207) = sizing (`width`, `minWidth`, `maxHeight` as
  `number` or `"%"`), anchor + offsets, `row/col`, `margin`, a `visible(w,h)`
  predicate, and `nonCapturing`.
- **Render/input model**: single-threaded pull. `render(width)` returns lines;
  the TUI diffs them. Input is pushed to a pre-dispatch listener chain
  (`onTerminalInput`) first, then to `focusedComponent.handleInput` (L761–832).
  Live updates are driven by the component calling `tui.requestRender()` — e.g.
  `ConversationViewer` subscribes to `session.subscribe(cb)` and calls
  `requestRender()` on each event (conversation-viewer.ts L50–54). **This is
  push, not polling.**

### 1c. The consumer (what the three widgets actually rely on)

- **`agent-widget.ts`** — persistent `aboveEditor` widget, factory-callback form,
  reads live manager state each `render()`, drives an 80 ms `setInterval` +
  `requestRender()`, unregisters (`setWidget(key,undefined)`) when empty.
- **`fleet-list.ts`** — `belowEditor` render-only widget + **all** key handling via
  `onTerminalInput`, gated on `getEditorText()===""`; `↓`/`←` at empty prompt
  activates, `↑↓` move, Enter → `ctx.ui.custom(...,{overlay:true})`, Esc/up-past-top
  deactivate; press-only (`isKeyRelease` filter); yields to the overlay while open.
- **`conversation-viewer.ts`** — `Component` shown via `custom({overlay:true})`;
  `session.subscribe` push updates; embedded `Input` steer composer (Enter sends,
  Esc cancels); two-press `x` stop guard; scroll via resolved keybindings
  (`viewer-keys.ts`); auto-scroll stickiness.

---

## 2. The tau seam design

### 2a. The core "component" type = a Textual `Widget`

Pi hands extensions its own `Component` because it owns pi-tui. Tau renders with
Textual. Per constraint 5 the experiment **accepts** that extension widgets
`import textual` directly — that is the cost being measured. So the seam's
"component" is simply `textual.widget.Widget` (and, for overlays, a widget the
host wraps in a screen). Core does **not** invent a `Component` protocol; it
mounts Textual widgets the extension builds.

**Ruling (experiment):** the component type is Textual's `Widget`, not a
tau-owned abstraction. This is the whole point of the branch and the exact thing
the phase-21 Ruling warns against (Textual promoted into the public contract).
Recorded honestly, not hidden.

Core stays **agent-agnostic** (constraint 4): every name below is generic
("slot", "overlay", "interceptor"). No "agent", "fleet", "subagent" anywhere in
core.

### 2b. New API — signatures and placement

Three files change. All type names live in `extensions/api.py`; the host
implementation lives in `tui/app.py`; `runtime.py` only loses code.

```python
# --- extensions/api.py -----------------------------------------------------
from typing import Literal, Protocol
from collections.abc import Callable
# NB: api.py already imports nothing from textual and must not; the Widget type
# is referenced only under TYPE_CHECKING to keep print-mode import-clean.
if TYPE_CHECKING:
    from textual.widget import Widget
    from textual import events
    from tau_coding.tui.theme import TuiTheme

Placement = Literal["above_prompt", "below_prompt"]

# Factories are called by the host on the UI thread. They receive the live
# theme (theme handoff, mirrors pi's (tui, theme) => Component).
SlotWidgetFactory = Callable[["TuiTheme"], "Widget"]
OverlayWidgetFactory = Callable[["OverlayHandle", "TuiTheme"], "Widget"]

# Pre-editor key hook (ports onTerminalInput). Returns True to consume the key.
# The host passes the Textual Key event and the current prompt text so the
# handler can self-gate (pi gates on getEditorText()===\"\").
KeyInterceptor = Callable[["events.Key", str], bool]

class OverlayHandle(Protocol):
    """Handle to an open overlay (ports pi's OverlayHandle, trimmed)."""
    def close(self) -> None: ...
    @property
    def is_open(self) -> bool: ...

class ComponentBridge(Protocol):
    """Host widget-hosting capability. Part of what a UiBridge exposes when a
    TUI is attached; NullUiBridge/StderrUiBridge implement it as no-ops so an
    extension stays fully functional (just widget-less) in print mode."""
    @property
    def supports_components(self) -> bool: ...
    @property
    def theme(self) -> "TuiTheme": ...
    def get_prompt_text(self) -> str: ...
    def request_render(self) -> None: ...
    def set_slot_widget(
        self, key: str, factory: SlotWidgetFactory | None, *, placement: Placement
    ) -> None: ...
    def open_overlay(self, factory: OverlayWidgetFactory) -> OverlayHandle: ...
    def register_key_interceptor(self, handler: KeyInterceptor) -> Callable[[], None]: ...
```

`UiBridge` (the existing Protocol, L229) gains these members;
`NullUiBridge`/`StderrUiBridge` gain no-op defaults (`supports_components ->
False`, `set_slot_widget`/`open_overlay` do nothing, `open_overlay` returns a
dead handle, `register_key_interceptor` returns a no-op unsubscribe,
`get_prompt_text -> ""`, `theme` raises or returns a shared default). `ExtensionUi`
(L349) grows a `components` property returning the bridge (or a
`supports_components=False` view) so extensions call
`context.ui.components.set_slot_widget(...)`.

> **Implementation note (landed):** `set_slot_widget` now matches pi's
> `setWidget` on two points the first cut deferred. (1) **String-array form
> ported** — `content` is `SlotWidgetContent = Sequence[str] |
> SlotWidgetFactory | None` (not factory-only). A list of display lines is
> turned into a `Static` **host-side** (joined with newlines, parsed as Rich
> markup with a literal-text fallback via `_custom_markup_to_text`, mirroring
> the custom-message renderer guard), so a simple extension never imports
> Textual. The host normalizes strings into a factory inside
> `_set_extension_slot_widget` — it checks `callable()` first so a `Sequence`
> test can't swallow a factory, and treats a bare `str` as one line — leaving
> the reconcile/quarantine/last-writer-wins machinery untouched. (2)
> **`placement` default is now `"above_prompt"`** (pi's `aboveEditor`), not
> `"below_prompt"`.

**Removed from `api.py`:** `TranscriptSource`, `TranscriptSourceStatus`,
`TranscriptSourceProvider`, `TranscriptSourcesChangedCallback`,
`UiBridge.view_transcript`, `ExtensionUi.view_transcript`,
`ExtensionAPI.set_transcript_source_provider`,
`ExtensionAPI.notify_transcript_sources_changed` (and the same names from
`extensions/__init__.py` exports).

### 2c. `runtime.py` changes

Pure subtraction. Remove `_transcript_source_providers`,
`_transcript_sources_changed`, `set_transcript_source_provider`,
`set_transcript_sources_changed_callback`, `notify_transcript_sources_changed`,
`transcript_sources`, and their `reset_for_reload`/`_remove_registrations`
clean-up lines. The component bridge needs **no** runtime aggregation: it is a
straight pass-through capability on the already-installed `self._ui`
(`set_ui_bridge`). `ExtensionUi.components` returns `self._runtime.ui` narrowed to
`ComponentBridge`. So runtime shrinks and gains nothing.

### 2d. Compose-tree mounting

Current tree (compose, L2250–2280):
```
main-pane
├── #transcript            (main conversation)
├── #agent-transcript-pane (display-toggled sibling; agent view)   ← REMOVE
├── #queued-messages
├── #prompt-row
├── #compact-session-info
├── #autocomplete
└── #agent-strip           (below prompt)                          ← REMOVE
```
Replaced generically:
```
main-pane
├── #transcript
├── #above-prompt-slot     (empty Container; host-managed mount point)  ← NEW
├── #queued-messages
├── #prompt-row
├── #compact-session-info
├── #autocomplete
└── #below-prompt-slot     (empty Container; host-managed mount point)  ← NEW
```
- `set_slot_widget(key, factory, placement="below_prompt")` mounts
  `factory(theme)` into the matching slot container under a host wrapper (see
  guards); `factory=None` unmounts and forgets that key. Multiple keys per slot
  mount in call order. `refresh_slot`/`request_render` triggers a re-render
  (Textual `widget.refresh()`), the analog of `tui.requestRender()`.
- The overlay **replaces the `#agent-transcript-pane` display-toggle** with a
  pushed screen: `open_overlay(factory)` pushes a host `ComponentOverlayScreen`
  (a `ModalScreen`) that mounts `factory(handle, theme)` and focuses it.
  `handle.close()` pops the screen (the `done()` analog). A pushed screen gives
  correct focus capture and Esc scoping for free, and removes the manual
  `display=False`/`display=True` transcript-swap dance from core. Sizing/anchor
  (pi's `OverlayOptions`) is left to the extension's own Textual CSS on its
  widget — core does not re-expose `OverlayOptions` (smaller contract; the
  extension already imports Textual and can size itself).

**Ruling (experiment):** overlay = pushed `ModalScreen`, not an in-tree toggled
pane. Textual screens already own focus/Esc/return semantics that the old
`_activate_source`/`_activate_main` pair hand-rolled; reusing them shrinks core.

> **Design revision (major — behavioral regression to reconcile):**
> a `ModalScreen` is not behaviorally equivalent to today's in-place view, and the
> divergence is larger than "steer composer feel" (risk §7.4). Today `_activate_
> source` only swaps `#transcript` → `#agent-transcript-pane` (app.py L2929–2930);
> the **agents strip, prompt row, and sidebar stay mounted and interactive**. That
> is load-bearing: the viewed source stays listed in the strip (L2833), so the
> user can left-arrow back into the strip and ↑↓ to *switch to another agent
> without leaving the view*, and can watch other agents' status dots while
> reading one. A full-screen `ModalScreen` covers the strip — switching agents now
> requires Esc → re-enter strip → Enter, and you lose peripheral fleet awareness.
> Also today steering is done in the **same main prompt** (`_submit_prompt_from_
> editor` routes to `_steer_viewed_agent`, L2376–2383) with the prompt prefix
> flipping to `▸` and placeholder "Steer …" (`_sync_prompt_identity`, L2986–3003);
> the modal replaces this with a separate embedded `Input`. Either (a) explicitly
> accept these as intended UX changes and say so (the old multi-agent-switch-from-
> view affordance is dropped), or (b) reconsider open-question 2 and use an
> in-tree toggled container that keeps the strip visible — which also makes the
> push-refresh trivially on-loop and avoids the modal focus-restoration risk.
> Right now the doc presents ModalScreen as a pure simplification; it is a UX
> trade the reviewer should decide deliberately.

> **Decision (final): option (b) — in-tree main-area slot, not a
> ModalScreen.** The seam gains a third placement, `placement="main"`: the host
> mounts the widget as a display-toggled sibling of `#transcript` inside a
> generic `#main-slot` container (exactly the mechanics `#agent-transcript-pane`
> uses today, made generic), and `open_main_view(key)` / `close_main_view()`
> (or `set_slot_widget(key, factory, placement="main")` plus a show/hide call —
> implementer picks the cleaner shape and documents it) toggle
> `#transcript`/slot visibility. Rationale: preserves the two load-bearing
> affordances the review identified (strip stays visible → switch-agents-from-
> view and peripheral fleet awareness) and makes push-refresh trivially
> on-loop. Steering follows PI parity, not old-tau parity: the viewer widget
> embeds its own composer (pi-subagents' `ConversationViewer` model); the main
> prompt no longer routes to a viewed agent, and `_steer_viewed_agent` /
> prompt-identity swapping leave core entirely. Esc handling: the extension's
> key interceptor (2e) closes the view — no core special case. This drops the
> ModalScreen focus-restoration and live-refresh-into-modal risks.

> **Implementation note (Step 1 — API shape as landed):** Step 1 is purely
> additive; the new seam sits *alongside* the still-present `#agent-transcript-
> pane` / `#agent-strip` and the transcript-source seam (removals are Step 3).
> Concrete choices:
> - **Main view, not overlay.** `open_overlay`/`OverlayHandle` (the ModalScreen
>   shape in §2b) landed instead as `open_main_view(factory) -> MainViewHandle`
>   (renamed to reflect the in-tree reality). The factory is
>   `(handle, theme) -> Widget`; the host mounts it into a generic `#main-slot`
>   container (display-toggled sibling of `#transcript`, the generic form of
>   `#agent-transcript-pane`), hides `#transcript`, and `handle.close()` reverses
>   it and refocuses the prompt. This is the "explicit method" arm of the
>   orchestrator's option (b), chosen over `placement="main"` because the viewer
>   is per-open and needs a handle + theme at open time (mirrors Pi's
>   `custom({overlay:true})`), which a slot key + separate show/hide call could
>   not carry cleanly. Consequently **`Placement` is only `above_prompt` /
>   `below_prompt`** — there is no `"main"` placement.
> - **Focus stays on the prompt** when a main view opens (the host does not steal
>   it), so a registered key interceptor keeps firing and can `handle.close()` on
>   Esc. Step 2's viewer must `focus()` its own embedded composer if it wants
>   typing, and wire that composer's Esc/close back to `handle.close()` (the
>   interceptor only fires while the *prompt* is focused).
> - **`runtime.py` was not touched.** `ExtensionUi.components` is a pure
>   pass-through returning the installed `UiBridge` narrowed to `ComponentBridge`
>   (the bridge already reaches extensions via `runtime.ui`), so no runtime
>   aggregation/registration was needed.
>
> **Implementation note (result resolution — Pi's `done`):** `MainViewHandle`
> now carries the result-resolution half of Pi's `ctx.ui.custom<T>`. `close()`
> gained an optional `close(result=None)` and the handle gained
> `async def wait() -> object | None`, so an extension can "show a view, get an
> answer": the factory (or a key interceptor) calls `close(result)`, and the
> opener awaits `handle.wait()` for that value. We kept the **synchronous
> open/handle** model rather than porting `custom` as an async call — `wait()` is
> the awaitable, not `open_main_view`. Mechanics: the host creates an
> `asyncio.Future` on its event loop at open time; `close(result)` resolves it
> (first close wins, later closes no-op) and `wait()` awaits it (returning at once
> if already closed). Every *host-driven* teardown resolves the future with
> `None` via `_release_main_view_handle` — session rebind
> (`_clear_extension_components`), quarantine of the view's widget, mount failure,
> and being superseded by a later `open_main_view` (last writer wins) — so an
> awaiting extension task can never hang. The dead handles
> (`NullUiBridge`/`StderrUiBridge`, print mode) return `None` from `wait()`
> immediately and ignore `close(result)`.

### 2e. The pre-editor input hook and Esc precedence

Pi's `onTerminalInput` fires before the focused editor. Textual delivers keys to
the focused widget first, then bubbles to ancestors/bindings. The faithful,
minimal splice point is the **top of `PromptInput.on_key`** (app.py L509–536) —
exactly where the hardcoded `focus_agent_strip()` call sits today (L528–533).

Replace that special case with a generic consult:
```python
# PromptInput.on_key, before any built-in handling:
for interceptor in self.app._extension_key_interceptors():
    if interceptor(event, self.text):        # host guards each call (2f)
        event.stop(); event.prevent_default()
        return
```
- Because this runs *inside* the focused editor before it consumes the key and
  before app-level bindings resolve, an interceptor that returns `True` for
  `escape` preempts `action_cancel` — this is how the strip's "Esc deactivates
  the list" wins over "Esc cancels the turn" **without** any strip branch in
  `action_cancel`. When no interceptor consumes Esc, it falls through to core's
  existing cancel binding unchanged.
- Interceptors see completions naturally: the extension gates on
  `prompt_text == ""` (pi parity), so a non-empty prompt or an open completion
  menu leaves normal typing/nav untouched. Core keeps its completion handling as
  is; only the removed `_strip_focused` branches in
  `action_completion_next/previous`, `on_text_area_changed`,
  `action_submit_prompt/follow_up`, and `action_cancel` go away.
- While the overlay screen is open the prompt is not focused, so interceptors do
  not fire — the overlay handles its own keys as a Textual screen (matches pi's
  "yield to the overlay while open").

**Theme handoff:** the host passes `self.tui_settings.resolved_theme` into every
factory and exposes it via `ComponentBridge.theme`. On theme change the host
re-invokes slot factories (drop + remount, or call an optional
`widget.on_theme_change(theme)` if present) — the analog of pi's `invalidate()`.

**Lifecycle (reload / unbind / shutdown):**
- Primary: the extension subscribes to `session_shutdown` and clears its own
  widgets (`set_slot_widget(key, None)`, close overlays) — pi parity (its widgets
  `dispose()` on shutdown).
- Safety net: the host tracks every slot key and overlay it mounted for
  extensions and force-clears them when `set_ui_bridge` is re-installed or the
  runtime is reset (`reset_for_reload`). A leaked extension widget must never
  survive a reload.

> **Design revision (major — /resume path under-specified):**
> `_connect_extension_runtime` (app.py L2559) — which calls `set_ui_bridge` — runs
> on **every** session bind, including `/resume` session switches (it is invoked
> from `__init__` L2196 and per bound session, each `CodingSession` carrying its
> *own* `extension_runtime`). So "force-clear on `set_ui_bridge` re-install" fires
> against the *new* runtime's bridge, while the stale strip/overlay widgets were
> mounted by the *previous* session's extension instance into host-owned slot
> containers that persist across the switch. The design must state concretely:
> on each `_connect_extension_runtime`, the host first force-clears **all**
> tracked slot keys and overlays (regardless of which runtime registered them),
> *then* installs the new bridge — otherwise a `/resume` leaves the old agents
> strip mounted while the new session's extension re-registers key
> `"subagents-fleet"` (same key → replace saves the strip, but any **open overlay**
> from the old session, which has no stable key, would leak). Add: open overlays
> are closed on bridge re-install and on `session_shutdown`, not only on runtime
> reset.
>
> Note on the interceptor's focus scope (concern #1): a `PromptInput.on_key` hook
> only fires while the prompt has real Textual focus — but so does today's entire
> strip UX (left-arrow entry at L528 and arrow-nav via `action_completion_*`/
> `action_scroll_*` all require prompt focus; `_strip_focused` is a flag, the
> prompt never loses focus). So the parity is preserved for keyboard. The one
> capability today that works *without* prompt focus is the mouse click
> (`AgentStrip.on_click` → `_strip_click`, L1082–1090); the design's §4a omits
> click handling. Because the new strip is a real Textual `Widget`, it should
> implement its own `on_click`/`on_mouse_down` (strictly better than the
> app-routed click today) — add that to §4a so click-to-switch does not silently
> regress.

> **Implementation note (post-experiment bug fix):** the `PromptInput.on_key`
> splice point above turned out to be **too late and too narrow**. tau binds
> `down`/`up`/`tab`/`alt+enter` on the *App* with `priority=True`
> (`_app_bindings`), and Textual's `App.on_event` runs
> `_check_bindings(key, priority=True)` **before** forwarding a `Key` to the
> focused widget. So `PromptInput.on_key` never even sees those nav keys —
> completion_next/previous/accept fire first. A widget-owned nav model (the
> strip taking focus and handling its own `on_key`) is therefore impossible
> under tau's app-priority bindings.
>
> The fix ports pi's `onTerminalInput` as a *true* pre-dispatch hook: the
> interceptor consult moved to an override of `async def on_event` on
> `TauTuiApp`, which for a non-forwarded `events.Key` consults
> `_run_extension_key_interceptors(event, prompt_text)` **before** calling
> `super().on_event` (hence before the priority bindings and before the focused
> widget). Consequences, all deliberate:
> - Interceptors now fire for **every** key regardless of which widget has
>   focus. They are consulted **only** on the main screen (`len(screen_stack)
>   <= 1`) — a modal dialog/picker/command-palette on top is never intercepted,
>   so overlays keep owning their keys. Interceptors must self-gate (on prompt
>   text and their own state); documented on `register_key_interceptor`.
> - The Esc-precedence story is unchanged (an interceptor that consumes
>   `escape` still preempts `action_cancel`), just relocated upstream.
> - The extension's strip no longer takes Textual focus at all: the prompt
>   keeps focus throughout and the controller's interceptor owns the whole nav
>   state machine (pi's fleet-list model), now viable because the interceptor is
>   pre-dispatch.

> **Ruling (experiment) — reserved keys the interceptor never sees:** because
> the consult is now *pre-dispatch and the only key hook*, an interceptor that
> returns `True` too broadly could swallow the session's hard interrupt/exit
> keys and brick the TUI (no way out). The host therefore skips the consult
> entirely for a minimal `RESERVED_EXTENSION_INTERCEPTOR_KEYS` frozenset —
> `{"ctrl+c", "ctrl+d"}` — so those keys always flow to normal dispatch
> untouched. These are the app's actual escape hatches: `ctrl+d` is bound to
> the `quit` action (exits the app) and `ctrl+c` to `clear_prompt` but is the
> terminal-standard SIGINT/interrupt reflex; `ctrl+q` is deliberately *not*
> included because tau's `_bindings` (`_app_bindings`) does not actually bind
> it. **Deviation from Pi (deliberate):** Pi's
> `RESERVED_KEYBINDINGS_FOR_EXTENSION_CONFLICTS` (`runner.ts:69` — `app.interrupt`,
> `app.exit`, `app.model.*`, `tui.input.submit`) guards its *`registerShortcut`*
> API, while Pi's raw `onTerminalInput` is unrestricted. Tau's interceptor *is*
> the `onTerminalInput` port, but unlike Pi's it fires pre-dispatch and is the
> only key hook, so it gets a reserved subset — and only the two hard
> interrupt/exit keys, not Pi's fuller list. Explicitly **not** reserved:
> `escape`, `enter`, arrows, `tab`, `left`/`right` — all load-bearing for the
> tau-subagents extension (Esc deactivates strip nav / closes the viewer, Enter
> opens/switches the viewer, arrows navigate the strip, each gated on an empty
> prompt), so they must stay interceptable.

### 2f. Error-isolation guard points

A throwing extension component must never crash the TUI. Guards, mirroring
runtime's existing "swallow + diagnose once" discipline (runtime.py
`_record_runtime_failure`):

1. **Mount / factory call** — `set_slot_widget` and `open_overlay` invoke the
   factory inside `try/except`; on failure the slot stays empty / no screen is
   pushed, a diagnostic is recorded once, and a `notify(..., "error")` fires.
2. **Render** — the extension widget is mounted inside a host wrapper
   (`_GuardedSlot`, a thin `Container`). Textual cannot fully sandbox a child's
   render/reactive/message-handler exception (see risks §7); the wrapper catches
   what it can (mount, explicit `refresh` calls the host drives) and the app gets
   a top-level `on_exception`/error hook that unmounts the offending extension
   widget and notifies rather than letting the app die.

> **Design revision (blocker, resolved with a concrete fix):** the guard as written
> is partly false. A spike (Textual 8.2.7, the pinned version) mounted
> widgets whose `render()`, `compose()`, and `on_mount()` raise. **All three kill
> the app**, and the `try/except` around the host's `mount()`/`refresh()` call
> does *not* save it: `render()` is invoked by the compositor's own reflow loop
> (`screen._refresh_layout` → `_compositor.reflow`), not synchronously inside the
> host's `refresh()` call, so the exception surfaces in `App._handle_exception`
> and the app tears down (`run_test` re-raises; `return_code`/panic). The "wrapper
> catches mount/explicit-refresh" clause therefore covers only the *least*
> dangerous case.
>
> Two concrete corrections are mandatory:
> 1. **There is no public `on_exception`.** `hasattr(App, "on_exception")` is
>    `False`; the only hook is the private `App._handle_exception(self, error)`.
>    The guard must override *that* (accepting the private-API coupling, which is
>    itself a contract-weight cost worth recording).
> 2. **Overriding `_handle_exception` to NOT call `super()` and instead remove the
>    offending widget DOES keep the app alive and responsive** — the spike verified a
>    recovered app stays `is_running=True`, `_exit=False`, and can mount new
>    widgets afterward. But `_handle_exception` receives only `error`, not the
>    culprit widget, so the host must either (a) walk the incoming traceback for a
>    frame owned by a tracked extension widget and remove that subtree, or (b)
>    on any exception whose traceback touches the slot/overlay registry, tear down
>    *all* extension-mounted widgets and notify. It must re-raise (call `super()`)
>    for exceptions with no extension frame, so core's own bugs still surface.
>    Guard #2's "wrapper catches" wording should be deleted and replaced by this
>    app-level `_handle_exception` policy; without it the experiment's headline
>    "a throwing extension component never crashes the TUI" is unmet.
> **Implementation note (Step 1 — quarantine as landed):** `TauTuiApp.
> _handle_exception` is overridden (private-API coupling recorded in a code
> comment there). It walks the incoming traceback for a frame whose
> `f_locals["self"]` is — or is a descendant of — a tracked extension widget
> (slot widgets + the open main view). Found → that tracked root is quarantined
> and the exception swallowed; not found → `super()._handle_exception(error)` so
> core bugs still surface. Verified against Textual 8.2.7: a **render** crash
> quarantines cleanly (widget fully removed). An **on_mount** crash cannot be
> fully pruned (the widget never finished mounting, so `remove()`'s prune never
> drains — a bare `pilot.pause()` after one will time out), so the quarantine
> additionally sets `display=False`/`disabled=True` to make the ghost inert; the
> app stays `is_running` and can still mount new widgets. The headline
> guarantee ("a throwing extension component never crashes the TUI") holds for
> render/mount/interceptor crashes.

3. **Key interception** — each interceptor is called inside `try/except` in
   `PromptInput.on_key`; an exception is treated as "not consumed" and diagnosed
   once (so a broken interceptor degrades to normal typing, never a dead prompt).
4. **Dispose / unmount** — all teardown runs under `contextlib.suppress` +
   diagnostic, so a throwing `dispose` cannot block reload.

> **Implementation note (post-experiment bug fix 2):** two user-reported crashes
> traced to the same seam race — a slot/main-view *replacement* mounted the new
> widget synchronously while the old widget's `Widget.remove()` was still
> deferred (`AwaitableRemove` had not drained). For a beat the DOM held two
> widgets sharing one id (`subagents-conversation-viewer` / the strip's id) →
> `DuplicateIds` at mount → the swap was recorded as a component failure and the
> handle/strip was lost. It fired when opening a second agent's viewer while one
> was open, and on a same-tick extension teardown+reinstall (session rebind:
> `/reload`, `/new`, `/resume` — `session_shutdown` always precedes the next
> `session_start`, so the extension tears its strip down then remounts a same-id
> strip in one turn). Fix: `_set_extension_slot_widget` and
> `_open_extension_main_view` now record the *intended* widget synchronously
> (`_extension_slot_widgets` / `_extension_main_view` are the target, so mid-swap
> reads by clear/quarantine/refresh stay coherent, and `handle.is_open` reports
> the intended state the instant it is returned) and hand the actual mount to a
> serialized async continuation (`_reconcile_slot` / `_reconcile_main_view`, each
> under a lock) that first `await`s the outgoing widget's removal, then re-reads
> the live target and mounts only if it is still the winner. A burst (rapid
> A→B→C) collapses to last-writer-wins with no orphaned widgets, and a widget
> quarantined between schedule and continuation is dropped from both the target
> and mounted trackers so the continuation no-ops. `_record_extension_component_
> failure` also now carries a truncated `Type: message` summary in the
> notification and logs the full traceback via `self.log.error` (that trail is
> what pinned the race).
>
> The same fix unblocked the extension's interceptor-while-viewer-open model
> (bug 2): the controller's key interceptor used to yield entirely while a viewer
> was open, so once a viewer was up the fleet strip was unreachable (the only
> exit was Esc *with the viewer focused*). It now stays active while a viewer is
> open — except while the steer composer owns the keyboard (a new
> `ConversationViewer.composer_active` property gates it) — with `left`
> re-activating strip nav (not `down`, which the focused viewer uses to scroll),
> `enter` on `main` closing the viewer via its handle, and `enter` on another
> agent switching the viewer (now race-safe thanks to the sequenced swap above).

---

## 3. Core removal list

Line references are against this repo as of the pre-experiment baseline
(`subagents-integration`); they drifted as the steps landed.

**`src/tau_coding/tui/app.py`**
- `class AgentStrip(Static)` + its `on_click` (L1082–1090).
- `_render_agent_strip(...)` helper (L4296–~4372).
- Constants `AGENT_STRIP_MAX_ROWS`, `AGENT_VIEW_POLL_SECONDS`,
  `AGENT_STRIP_STATUS_GLYPHS` (L128–131+).
- State fields (L2204–2211): `_agent_strip_sources`, `_strip_focused`,
  `_strip_index`, `_active_source_id`, `_agent_view_state`,
  `_agent_view_revision`, `_agent_view_status`, `_agent_view_timer`.
- Methods (L2790–3024): `_on_transcript_sources_changed`, `_transcript_sources`,
  `_current_source`, `_refresh_agent_strip`, `focus_agent_strip`, `_strip_move`,
  `_strip_exit`, `_strip_select`, `_strip_click`, `_activate_source_by_id`,
  `_activate_source`, `_activate_main`, `_tick_agent_view`, `_steer_viewed_agent`
  (and `_sync_prompt_identity`'s agent-view branches — the main-prompt reset
  stays).
- Strip/view branches inside shared methods: `action_cancel` (L3028–3033),
  `action_completion_next` (L3121–3123), `action_completion_previous`
  (L3148–3150), `on_text_area_changed` (L2332–2334), `action_submit_prompt`
  (L2342–2344), `action_submit_follow_up` (L2349–2351), the steer-viewed branch
  in `_submit_prompt_from_editor` (L2376–2383), the `focus_agent_strip` call in
  `PromptInput.on_key` (L528–533), and `CompletionActionTarget.focus_agent_strip`
  (L305).
- `_TuiExtensionUiBridge.view_transcript` (L235–237); the
  `set_transcript_sources_changed_callback` wiring in
  `_connect_extension_runtime` (L2566–2570); `_activate_source_by_id` call site.

> **Design revision (minor, but required before Step 3 can pass):** the call-site list for
> `_refresh_agent_strip` is incomplete. It is also called from `_refresh_chrome`
> (L3676, the theme/chrome refresh path) — removing the method without deleting
> that call leaves an `AttributeError` on every chrome refresh. Separately,
> `_build_completion_state` has an `_active_source_id` branch (L3836, "while an
> agent view is open the input steers that agent, so main completions don't
> apply") that is not enumerated among the shared-method branches; it must be
> removed with the others. Note also: a naive grep for `_strip_` false-positives
> on `tools.py` `_strip_bom`/`_strip_bom` (L356/L961) — that is unrelated BOM
> handling and must NOT be touched; the removal list correctly omits `tools.py`,
> but the grep discipline should be spelled out so the implementer doesn't chase
> it.
- Compose: the `#agent-transcript-pane` `TranscriptView` (L2262–2268) and the
  `AgentStrip(..., id="agent-strip")` (L2279).
- CSS: `#agent-transcript-pane` (L1818–1827) and `#agent-strip` (L1829–1837)
  blocks; add `#above-prompt-slot` / `#below-prompt-slot`.
- Import of `TranscriptSource` (L60).

**`src/tau_coding/extensions/api.py`** — `TranscriptSource`,
`TranscriptSourceStatus`, `TranscriptSourceProvider`,
`TranscriptSourcesChangedCallback`, `UiBridge.view_transcript` (+ Null/Stderr),
`ExtensionUi.view_transcript`, `ExtensionAPI.set_transcript_source_provider`,
`ExtensionAPI.notify_transcript_sources_changed`.

**`src/tau_coding/extensions/runtime.py`** — everything listed in §2c.

**`src/tau_coding/extensions/__init__.py`** — the four `TranscriptSource*`
imports (L28–31) and `__all__` entries (L79–82).

**`tests/test_tui_app.py`** (delete or relocate to extension): `test_agent_strip_
opens_in_place_view_and_steers`, `test_agent_view_rejects_steering_finished_
agents`, `test_agent_view_rerenders_on_revision_change`, `test_agent_strip_fills_
only_the_viewed_dot`, `test_agent_strip_drops_finished_agents`, `test_agent_strip_
click_switches_view`, `test_agent_view_activation_degrades_when_messages_gone`,
`test_escape_returns_to_main_before_cancelling_a_running_turn`, `test_agent_view_
returns_to_main_when_source_vanishes` (L2496–2740). **Keep** the
compaction/running-turn escape tests (L2947, L4030, L4084) — core still owns
those — and the extension-dialog tests.

---

## 4. Extension architecture

New package `src/tau_subagents/ui/` (Textual). The extension already depends on
`textual>=1.0` transitively via tau; make it a direct dependency.

### 4a. `strip_widget.py` — `AgentStripWidget(Widget)` (ports `fleet-list.ts`)
- Mounted into `below_prompt` via `context.ui.components.set_slot_widget(
  "subagents-fleet", build, placement="below_prompt")`.
- Registers one `KeyInterceptor` via `register_key_interceptor`; self-gates on
  the passed `prompt_text == ""`. `↓`/`←` activate, `↑↓` move, Enter opens the
  viewer overlay, Esc/up-past-top deactivate, any other key deactivates and lets
  the key through (returns `False`). Press-only (ignore Textual key-repeat/
  release equivalents).
- Roster = `main` + running/queued + currently-viewed + recently-finished
  (linger), earliest-first — identical policy to `fleet-list.ts` `agentRecords()`,
  reading `manager.runs`.
- Renders with the tau `Tuitheme` handed to the factory (Rich `Text`/markup, as
  the old `_render_agent_strip` did).

### 4b. `conversation_viewer.py` — `ConversationViewerScreen(ModalScreen)` (ports `conversation-viewer.ts`)
- Opened by the strip via `context.ui.components.open_overlay(build)`; `build`
  closes over the selected `AgentRun` and returns the viewer widget bound to the
  `OverlayHandle`.
- Live transcript of the run, scroll + auto-scroll stickiness, header stats.
- Embedded steer composer: a Textual `Input`; Enter → `steer_run(run, text)`
  (from `agents_menu.py`), Esc cancels the composer (not the overlay).
- Two-press stop guard on `x` → `stop_run(run)`; any other key disarms.
- Esc/`q` closes the overlay (`handle.close()`).

### 4c. Event-push wiring (replaces revision polling)
The extension owns its child `CodingSession`s, so it can subscribe directly
instead of the host polling `run.revision` every 0.5 s (`_tick_agent_view`, now
deleted). Design:
- `SubagentManager` keeps a single change signal but re-points it at the
  extension's own widgets: rename `sources_changed` → `on_change`, wired in
  `setup()` to a controller that calls `strip_widget.refresh()` and any open
  viewer's `refresh()`. `_notify_sources()` → `_notify_change()`.
- Per-run push for the viewer: `AgentRun` gains `listeners: list[Callable[[],
  None]]`; the manager's `_on_agent_event`/`_apply_message` path (which already
  bumps `run.revision` and appends messages) also calls each listener. The open
  viewer registers a listener on mount and removes it on close — the direct
  analog of pi's `session.subscribe(() => tui.requestRender())`. `run.revision`
  is retained only as a cheap "did content change" dirty-check inside the
  viewer's refresh; it is no longer a host polling key.

> **Design revision (verified safe; the invariant must be stated):** deleting the poll
> in favour of push is only safe because subagent runs execute as `asyncio` tasks
> on the *same* event loop as the TUI — `_run_agent` is launched via
> `asyncio.get_running_loop().create_task` (extension.py L299) and
> `run.revision += 1` / `_notify_sources` fire synchronously inside that loop
> (L614, L185–258). So a listener calling `widget.refresh()` runs on the UI
> thread and is safe. The old poll used the host's `set_interval` (UI-loop) and
> the change callback marshalled via `self.call_later` (app.py L2792) precisely to
> stay on-loop. **This invariant must be recorded as a hard constraint:** if any
> subagent work is ever moved to a thread (`asyncio.to_thread`, an executor), the
> listener must marshal via `app.call_from_thread`/`post_message` or the direct
> `refresh()` becomes a data race. There are no such threads today (grep: none),
> so the migration is safe as designed.

### 4d. `/agents` menu + `agents_menu.view_run_conversation`
- `view_run_conversation` no longer calls a host `view_transcript` seam (removed).
  It calls the extension's own `open_conversation(run)`, which uses
  `context.ui.components.open_overlay(...)`. On success it returns `"exit"` (menu
  loop closes; user lands in the overlay) — same control flow as today.
- Degrade path unchanged in spirit: when `context.ui.components.supports_
  components` is `False` (print mode / no TUI), `open_conversation` returns
  falsy and the menu falls to the action submenu (`"actions"`) exactly as the
  current `view_transcript`-missing branch does. The capability check moves from
  `getattr(ui, "view_transcript")` to `components.supports_components`.

> **Design revision (major):** the plumbing does not exist as written.
> `view_run_conversation(ui, run)` (agents_menu.py L115) receives `ui: DialogUi` —
> a Protocol (L30–45) exposing *only* `select`/`confirm`/`input`/`notify`. It has
> no `.components`, no manager, no theme. Today it calls
> `getattr(ui, "view_transcript")(run.agent_id)` and the *host* resolves the id,
> builds the view, and steers. In this design the extension must build the viewer
> widget itself, which needs (a) the component bridge, (b) the `SubagentManager`
> (for `steer_run`/`stop_run`/listener registration), and (c) the theme — none of
> which reach the menu through `DialogUi`. Fix: either widen `DialogUi` to carry a
> `components` member *and* thread the manager into `view_run_conversation`
> (change its signature — `show_agents_menu` already holds `manager`), or move
> `open_conversation` out of `agents_menu.py` into `extension.py`/the controller
> where the manager + bridge are in scope and have the menu call back into it.
> The one-line `getattr(ui, "view_transcript")` swap in §4d hides a real
> refactor of the menu's dependency surface.

### 4e. `extension.py` `setup()` changes
- Delete `run_transcript_source`, `SOURCE_STATUS`, the `set_transcript_source_
  provider`/`notify_transcript_sources_changed` block (L1452–1461).
- In `setup()`, when `context.ui.components.supports_components`: build the
  controller, `set_slot_widget` the strip, `register_key_interceptor`, and wire
  `manager.on_change`. Guard the whole block so a core **without** the component
  seam (old tau) does not crash at import/setup — a `getattr(context.ui,
  "components", None)` check keeps the extension loadable (constraint 8), even
  though on this branch the UI path drops the older-tau *behavioral* compat
  (no fallback strip).
- Keep `render_call` lines, dialogs, tools, scheduler, message renderer — all
  unchanged (constraint 3).

---

## 5. Migration order

Both suites must be green after each **repo-step boundary**; a red window is
tolerated only *within* one step in one repo. The naive "remove core first" is
wrong (it reds core strip tests and leaves a no-UI gap). "Seam-first → consume →
remove-last" is correct **because the old and new seams coexist without
conflict**: once the extension stops publishing transcript sources, the old
host strip simply has zero rows (hidden), and the old `focus_agent_strip`
left-arrow returns `False` and falls through, so it never fights the new
interceptor.

**Step 1 — core adds the seam (core green).** Add `ComponentBridge` + slot
containers + `ComponentOverlayScreen` + interceptor registry + guards +
`NullUiBridge`/`StderrUiBridge` no-ops, *alongside* the still-present
transcript-source seam and `AgentStrip`. Add new core pilot tests for the seam
(§6). Old strip tests still pass; extension untouched and still on the old seam,
its tests still pass (it builds against the now-superset core).

**Step 2 — extension consumes the seam (extension green).** Build `ui/`, switch
`setup()` from `set_transcript_source_provider` to slot+interceptor+overlay,
repoint `manager.on_change`, add the Textual test harness (§6). Core untouched,
core tests still green. The old host strip is now dead-but-passing (its pilot
tests drive it with synthetic providers, not the extension).

**Step 3 — core removes the old seam (both green).** Delete everything in §3 and
its pilot tests. Extension is already off the old seam, so nothing breaks; core
keeps only the generic seam and its new tests. Extension tests unaffected.

This is genuinely "removal last," justified by conflict-free coexistence rather
than by ignoring the ordering hazard.

---

## 6. Test plan (constraint 7)

**Core (`tests/`), new pilot tests for the generic seam** — drive the real
`TauTuiApp` via `Pilot` with a tiny in-test fake extension/bridge caller (no
subagents vocabulary):
- mounts a dummy `Static` into `#below-prompt-slot` and asserts it renders; then
  `set_slot_widget(key, None)` unmounts it.
- `open_overlay` pushes a screen, it captures focus, `handle.close()` pops it and
  restores prompt focus.
- a registered interceptor consumes a key when `prompt_text == ""` and is
  bypassed when the prompt is non-empty; a consumed `escape` does **not** trigger
  `action_cancel`.
- a factory that raises → app survives, slot empty, diagnostic recorded, notify
  fired (error isolation).
- reload / `set_ui_bridge` re-install force-clears mounted widgets and overlays.

**Extension (`tests/`), new Textual harness.** The repo currently has no Textual
test setup. Add: dev-dependency `textual>=1.0` (direct), reuse the existing
`pytest.mark.anyio` + `asyncio` backend already configured in
`tests/test_extension.py` (no new async plugin needed). Two layers:
- **Unit** (fast, no full app): a `FakeComponentBridge` implementing
  `ComponentBridge` (records `set_slot_widget`/`open_overlay` calls, feeds
  synthetic `events.Key` to the interceptor, exposes settable `prompt_text`).
  Test the strip roster/selection/activation, the viewer's steer/stop-guard/
  scroll, and push-refresh on a fake run listener — this is where the deleted
  core UX tests are re-homed (fills-only-viewed-dot, drops-finished, click-
  switches, opens+steers, rejects-finished-steer, rerenders-on-change, degrades-
  when-messages-gone).
- **Integration** (one test): construct a real `TauTuiApp` with the extension
  loaded and drive it via `Pilot` — assert the strip mounts in
  `#below-prompt-slot`, `←` at an empty prompt activates it, Enter opens the
  viewer overlay, and a steer reaches the run. This proves the actual seam wiring
  against real core, which the fake-bridge unit tests cannot.

**Both:** `uv run pytest` green in each repo at every step boundary; `uv run ruff
check src tests` clean in core.

---

## 7. Honest contract accounting

**LoC deltas (estimates).**
- *Core removed:* `AgentStrip` (~10), `_render_agent_strip` (~75), strip/view
  state + methods (L2790–3024, ~235), scattered branches (~40), CSS (~20),
  `api.py`/`runtime.py`/`__init__.py` transcript-source machinery (~120), deleted
  pilot tests (~250). ≈ **−500 non-test / −250 test**.
- *Core added:* `ComponentBridge` + slots + overlay screen + interceptor registry
  + guards + Null/Stderr no-ops (~280), new seam pilot tests (~180). ≈ **+280
  non-test / +180 test**.
- *Net core:* roughly **−200 non-test LoC** — core does get smaller.
- *Extension added:* strip (~360) + viewer (~360) + keys/glue + controller +
  harness/fakes (~250 test). ≈ **+900 non-test / +250 test**, plus a direct
  `textual` dependency.

**Public-API-surface delta — the honest part.** Per-feature the contract
*shrinks to zero*: core no longer speaks any agent vocabulary, and "show agent
UI" is now entirely the extension's business. But the *generic* extension
contract **grows** and hardens exactly as the phase-21 Ruling predicted: core now
publicly exposes a widget-hosting layer — `set_slot_widget`, `open_overlay`,
`register_key_interceptor`, `OverlayHandle`, `Placement`, the factory types, and
(transitively) **Textual's `Widget`/`events.Key`/screen model** as tau's public
extension API. We trade four small, data-only, frontend-portable symbols
(`TranscriptSource` + 3) for a larger, Textual-coupled, TUI-only surface. The
"removes code from core" claim is real in LoC and false in contract weight — the
seam is fewer lines but a much heavier and less portable promise.

**Risks / Textual coupling points the extension (and core) now touch.**
1. **Render sandboxing is imperfect.** Textual bubbles a child widget's
   exception (in reactive watchers, message handlers, or `render`) up toward the
   app; the host wrapper can catch mount/explicit-refresh failures but not every
   in-widget crash. A truly robust guard needs a spike on Textual's
   `on_exception`/error boundary behavior — **open question.**
2. **Key-dispatch ordering.** The interceptor must beat both the focused
   `TextArea` and app-level bindings. Splicing at the top of `PromptInput.on_key`
   works on today's Textual, but it is load-bearing on Textual's
   focused-widget-first + `event.stop()`-preempts-binding semantics; a Textual
   upgrade could shift this.
3. **Version lockstep.** Core and extension now share a pinned Textual across a
   public seam — the precise ecosystem-break-on-upgrade the Ruling flagged. A
   Textual major bump now risks both repos at once.
4. **Overlay focus/return semantics** via `ModalScreen` (steer `Input` focus,
   Esc scoping, focus restoration to the prompt on close) must match the old
   in-place view's feel — behavioral, needs the integration test to pin.
5. **Theme-object stability** across reload/theme-change: factories capture a
   `TuiTheme`; the host must re-invoke them on change or expose a refresh hook,
   or extension widgets render stale colors.
6. **Slot vs responsive layout:** the new slot containers interact with
   `_update_responsive_layout`; an extension widget with unbounded height could
   crowd the transcript. Core should cap slot height (as `#agent-strip` did with
   `max-height: 8`).

**Open questions not resolvable from the source alone:** (1) whether a child
widget render crash can be fully contained in Textual without a per-widget
subprocess/boundary — needs a runtime spike; (2) whether the overlay should be a
`ModalScreen` (chosen here for focus/Esc correctness) or an in-tree toggled
container (closer to the removed `#agent-transcript-pane`, easier live-refresh) —
`ModalScreen` was recommended but both were not prototyped; (3) whether `run.revision`
should be dropped entirely in favor of pure listener push or retained as a dirty
check (retained here, but it is redundant once listeners exist).

---

## 8. Measured outcome

The §7 LoC deltas were estimates. Here are the real numbers, measured against
the pre-experiment baseline (`subagents-integration`) — first as of Step 3,
then updated after the post-experiment fix commits.

**Core source (`src/` only), as of Step 3 (`c291557`)** —
`git diff --stat subagents-integration..c291557 -- src/`:

```
 src/tau_coding/extensions/__init__.py |  20 +-
 src/tau_coding/extensions/api.py      | 251 ++++++++---
 src/tau_coding/extensions/runtime.py  |  53 ---
 src/tau_coding/tui/app.py             | 773 ++++++++++++++++------------------
 4 files changed, 568 insertions(+), 529 deletions(-)
```

- **Net core src at Step 3: +39 lines** (568 inserted, 529 deleted).
- Of that, **Step 3 alone removed a net 585 src lines** (6 inserted, 591
  deleted) — the old transcript-source seam plus the entire host-side agents
  strip / in-place view / steer machinery in `app.py`.
- So Steps 1–2 (adding the component seam) added ~624 net src lines, and Step 3
  (removing the old seam) gave ~585 back.

**Updated after the post-experiment fixes** (`5f8a78f` pre-dispatch
interceptors, `ff55f54` sequenced swaps) —
`git diff --stat subagents-integration..HEAD -- src/`:

```
 src/tau_coding/extensions/__init__.py |  20 +-
 src/tau_coding/extensions/api.py      | 264 +++++++--
 src/tau_coding/extensions/runtime.py  |  53 --
 src/tau_coding/tui/app.py             | 972 +++++++++++++++++++---------------
 4 files changed, 778 insertions(+), 531 deletions(-)
```

- **Net core src now: +247 lines** (778 inserted, 531 deleted). The two fix
  commits — moving the interceptor consult to a true pre-dispatch
  `TauTuiApp.on_event` hook, and serializing slot/main-view swaps behind locks
  so a deferred remove drains before the same-id replacement mounts — cost a
  further ~208 net src lines. Both were bugs the experiment had to fix to be
  usable, so they belong in the honest total.

**The estimate was wrong in sign.** §7 predicted "roughly **−200 non-test
LoC** — core does get smaller." Measured, core src grew by **+39 lines** net at
Step 3 and **+247 lines** with the correctness fixes in. The component seam
(`ComponentBridge` + slot/main-view mounting + the in-tree main view +
interceptor registry + the `_handle_exception` quarantine guard + the sequenced
swap machinery + Null/Stderr no-ops) is *larger* than the transcript-source
seam it replaced — the guard/quarantine, main-view plumbing, and swap
sequencing in particular cost more than the estimate allowed. The honest
headline stands but flips: core did **not** get smaller in LoC. It traded a
small, data-only, frontend-portable seam for a larger, Textual-coupled one —
heavier in both contract weight (§7) *and* line count.

**Test counts (current, both repos green):**
- Core: **802 passing** (796 at Step 3 — Step 3 deleted 9 strip/view pilot
  tests from `test_tui_app.py`, 3 transcript-source runtime tests from
  `test_extensions.py`, and replaced the legacy-coexistence component test with
  a plain open→close restore-`#transcript` test, net −12 from the pre-Step-3
  808; the fix commits then added 6 interceptor/swap pilot tests).
- Extension (`tau-subagents`): **116 passing** (103 at Step 3; the fix-commit
  batches added the nav-while-viewer-open, rapid-switch, rebind, and
  quiet-rows tests).
