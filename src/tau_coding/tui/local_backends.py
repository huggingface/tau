"""Generic Textual host screens for local backends.

This module intentionally knows only the local-backend contracts. Protocol
names and provider-specific management concepts stay in backend extensions.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from typing import ClassVar, Literal, cast

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, ListItem, ListView, Select, Static

from tau_coding.local_backends import (
    LocalAction,
    LocalBackendRegistry,
    LocalBackendStatus,
    LocalConfigureSpec,
    LocalConfigValues,
    LocalOperationResult,
    LocalProgress,
    ProgressCallback,
)
from tau_coding.tui.config import TuiTheme

LocalUseCallback = Callable[[str, str], Awaitable[None] | None]
LocalNotifyCallback = Callable[[str, str], None]
LocalIdleCallback = Callable[[], bool]


class LocalBackendPickerScreen(ModalScreen[str | None]):
    """Explicitly confirm a backend choice, including when there is one."""

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("escape", "cancel", "Cancel"),
        Binding("enter", "confirm", "Select", show=False),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
    ]

    def __init__(self, registry: LocalBackendRegistry, *, theme: TuiTheme) -> None:
        super().__init__()
        self.registry = registry
        self.theme = theme
        self.views = registry.effective_backends()
        recommended = next((view for view in self.views if view.recommended), None)
        self.selected = (
            recommended.backend.id
            if recommended is not None
            else self.views[0].backend.id
            if self.views
            else None
        )

    def compose(self) -> ComposeResult:
        with Vertical(id="local-backend-picker"):
            yield Static("Local backends", id="local-backend-picker-title")
            if not self.views:
                yield Static("No local backends are available.", id="local-backend-empty")
            else:
                yield Static(
                    "Choose a backend, then confirm. The recommended choice is marked.",
                    id="local-backend-picker-help",
                )
                yield ListView(
                    *[ListItem(Label(self._label(view), markup=False)) for view in self.views],
                    id="local-backend-list",
                )
                with Horizontal(id="local-backend-picker-buttons"):
                    yield Button("Confirm", id="local-backend-confirm", variant="primary")
                    yield Button("Cancel", id="local-backend-cancel")

    def on_mount(self) -> None:
        if self.views:
            backend_list = self.query_one("#local-backend-list", ListView)
            backend_list.index = self._selected_index()
            backend_list.focus()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        event.stop()
        self._sync_selected()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "local-backend-confirm":
            self.action_confirm()
        elif event.button.id == "local-backend-cancel":
            self.action_cancel()

    def action_cursor_up(self) -> None:
        self.query_one("#local-backend-list", ListView).action_cursor_up()
        self._sync_selected()

    def action_cursor_down(self) -> None:
        self.query_one("#local-backend-list", ListView).action_cursor_down()
        self._sync_selected()

    def action_confirm(self) -> None:
        self._sync_selected()
        if self.selected is not None:
            self.dismiss(self.selected)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _sync_selected(self) -> None:
        if not self.views:
            self.selected = None
            return
        index = self.query_one("#local-backend-list", ListView).index
        if index is not None and 0 <= index < len(self.views):
            self.selected = self.views[index].backend.id

    def _selected_index(self) -> int:
        if self.selected is None:
            return 0
        return next(
            (index for index, view in enumerate(self.views) if view.backend.id == self.selected),
            0,
        )

    @staticmethod
    def _label(view) -> str:  # type: ignore[no-untyped-def]
        marker = " — Recommended" if view.recommended else ""
        effective = "" if view.use_available else " — unavailable"
        return f"{view.backend.display_name}{marker}{effective}"


class LocalBackendScreen(ModalScreen[None]):
    """Generic local-backend action screen."""

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("escape", "cancel", "Close"),
    ]

    def __init__(
        self,
        registry: LocalBackendRegistry,
        backend_id: str,
        *,
        theme: TuiTheme,
        on_use: LocalUseCallback | None = None,
        notify_callback: LocalNotifyCallback | None = None,
        is_idle: LocalIdleCallback | None = None,
    ) -> None:
        super().__init__()
        self.registry = registry
        self.backend_id = backend_id
        self.theme = theme
        self.on_use = on_use
        self._notify_callback = notify_callback or (lambda message, level: None)
        self._is_idle = is_idle or (lambda: True)
        self.status: LocalBackendStatus | None = None
        self._worker: asyncio.Task[None] | None = None
        self._use_task: asyncio.Task[None] | None = None
        self._closing = False

    def compose(self) -> ComposeResult:
        with Vertical(id="local-backend-screen"):
            yield Static("Local backend", id="local-backend-title")
            yield Static(
                "Select an action to inspect or change this backend.",
                id="local-backend-help",
            )
            yield Static("Not checked yet.", id="local-backend-status")
            with Horizontal(id="local-backend-actions"):
                yield Button("Configure", id="local-action-configure")
                yield Button("Refresh", id="local-action-refresh")
                if (view := self.registry.effective(self.backend_id)) is not None:
                    if view.backend.doctor is not None:
                        yield Button("Doctor", id="local-action-doctor")
                    if view.backend.reset is not None:
                        yield Button("Reset", id="local-action-reset")
                    if view.backend.load_model is not None:
                        yield Button("Load model", id="local-action-load-model")
                    if view.backend.unload_model is not None:
                        yield Button("Unload model", id="local-action-unload-model")
                    if view.backend.download_model is not None:
                        yield Button("Download model", id="local-action-download-model")
                yield Button("Use", id="local-action-use")
            yield Static("", id="local-backend-progress")
            yield Button("Close", id="local-action-close")

    def on_mount(self) -> None:
        self._update_action_visibility(None)

    def on_unmount(self) -> None:
        """Stop host-owned tasks before Textual detaches this modal."""
        self._closing = True
        self.registry.cancel(self.backend_id)
        for task in (self._worker, self._use_task):
            if task is not None and not task.done():
                task.cancel()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        action = event.button.id
        if action == "local-action-close":
            self.action_cancel()
        elif action == "local-action-configure":
            self._open_configure()
        elif action == "local-action-refresh":
            self._start_operation("refresh")
        elif action == "local-action-use":
            self._use_selected()
        elif action == "local-action-doctor":
            self._start_operation("doctor")
        elif action == "local-action-reset":
            self._confirm_reset()
        elif action in {
            "local-action-load-model",
            "local-action-unload-model",
            "local-action-download-model",
        }:
            operation = action.removeprefix("local-action-")
            self._open_model_action(operation)  # type: ignore[arg-type]

    def action_cancel(self) -> None:
        self._closing = True
        self.registry.cancel(self.backend_id)
        for task in (self._worker, self._use_task):
            if task is not None and not task.done():
                task.cancel()
        self.dismiss(None)

    def _open_configure(self) -> None:
        view = self.registry.effective(self.backend_id)
        if view is None:
            self._show_message("This backend is no longer available.", "error")
            return
        try:
            spec = view.backend.read_configure_spec()
        except Exception:  # noqa: BLE001 - a backend must not crash the host
            self._show_message("Could not read the backend configuration.", "error")
            return
        self.app.push_screen(
            LocalConfigureScreen(spec, theme=self.theme),
            callback=self._handle_configuration,
        )

    def _handle_configuration(self, values: LocalConfigValues | None) -> None:
        if values is None:
            return
        self._start_operation("configure", values=values)

    def _confirm_reset(self) -> None:
        if not self._is_idle():
            self._show_message(
                "Tau must be idle before resetting local backend settings.",
                "warning",
            )
            return
        self.app.push_screen(
            LocalConfirmScreen(
                "Reset local backend?",
                "Remove this backend's saved integration settings and credentials?",
                theme=self.theme,
            ),
            callback=self._handle_reset_confirmation,
        )

    def _handle_reset_confirmation(self, confirmed: bool | None) -> None:
        if confirmed:
            self._start_operation("reset")

    def _open_model_action(self, action: LocalAction) -> None:
        if not self._is_idle():
            self._show_message(
                "Tau must be idle before changing local backend models.",
                "warning",
            )
            return
        labels = {
            "load_model": "Load model",
            "unload_model": "Unload model",
            "download_model": "Download model",
        }
        self.app.push_screen(
            LocalModelActionScreen(labels[action], theme=self.theme),
            callback=lambda model_id: self._handle_model_action(action, model_id),
        )

    def _handle_model_action(self, action: LocalAction, model_id: str | None) -> None:
        if model_id is not None:
            self._start_operation(action, model_id=model_id)

    def _start_operation(
        self,
        action: LocalAction,
        *,
        values: Mapping[str, str] | LocalConfigValues | None = None,
        model_id: str | None = None,
    ) -> None:
        if not self._is_idle():
            self._show_message(
                "Tau must be idle before changing local backend settings.",
                "warning",
            )
            return
        if self._worker is not None and not self._worker.done():
            self._show_message("An operation is already in progress.", "warning")
            return
        self._worker = asyncio.create_task(self._run_operation(action, values, model_id))

    async def _run_operation(
        self,
        action: LocalAction,
        values: Mapping[str, str] | LocalConfigValues | None,
        model_id: str | None,
    ) -> None:
        self._set_progress("Working…")

        def progress(item: LocalProgress) -> None:
            self._set_progress(item.message)

        try:
            if action == "configure":
                assert values is not None
                result = await self.registry.configure(
                    self.backend_id,
                    values,
                    progress=cast(ProgressCallback, progress),
                )
            elif action == "refresh":
                result = await self.registry.refresh(
                    self.backend_id,
                    progress=cast(ProgressCallback, progress),
                )
            elif action == "doctor":
                result = await self.registry.doctor(
                    self.backend_id,
                    progress=cast(ProgressCallback, progress),
                )
            elif action == "reset":
                result = await self.registry.reset(
                    self.backend_id,
                    progress=cast(ProgressCallback, progress),
                )
            elif action in {"load_model", "unload_model", "download_model"}:
                assert model_id is not None
                manage_action = cast(
                    Literal["load_model", "unload_model", "download_model"], action
                )
                result = await self.registry.manage_model(
                    self.backend_id,
                    manage_action,
                    model_id,
                    progress=cast(ProgressCallback, progress),
                )
            else:
                result = LocalOperationResult(message="Unsupported action.")
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001 - host keeps modal alive
            if self._can_update_ui:
                self._show_message(f"Could not complete action: {type(exc).__name__}", "error")
            return
        if not self._can_update_ui:
            return
        if result.stale:
            self._show_message("The backend changed while this action was running.", "warning")
            return
        if result.cancelled:
            self._show_message("Action cancelled.", "warning")
            return
        if result.backend_status is not None:
            self.status = result.backend_status
            self._render_status(result.backend_status)
        if result.field_errors:
            self._show_message(
                "Configuration was not saved. Review the fields and try again.",
                "error",
            )
        elif result.message:
            self._show_message(result.message, "info")
        for diagnostic in result.diagnostics:
            message = (
                f"{diagnostic.stage}: {diagnostic.message}"
                if diagnostic.stage
                else diagnostic.message
            )
            self._show_message(message, diagnostic.severity)
        self._set_progress(
            "Credential cleanup needs attention." if result.credential_orphaned else ""
        )

    def _use_selected(self) -> None:
        if not self._is_idle():
            self._show_message(
                "Tau must be idle before switching models.",
                "warning",
            )
            return
        if self._worker is not None and not self._worker.done():
            self._show_message("An operation is already in progress.", "warning")
            return
        if self._use_task is not None and not self._use_task.done():
            self._show_message("A model switch is already in progress.", "warning")
            return
        if self.status is None or self.status.selected_model is None:
            self._show_message(
                "Refresh this backend and select an available model first.",
                "warning",
            )
            return
        if "use" not in self.status.actions:
            self._show_message("Using a model is unavailable for this backend.", "warning")
            return
        if self.on_use is None:
            self._show_message("Model selection is unavailable in this host.", "warning")
            return
        view = self.registry.effective(self.backend_id)
        if view is None or not view.use_available:
            self._show_message(
                "Using this backend is unavailable while it is shadowed.",
                "warning",
            )
            return
        result = self.on_use(view.backend.provider_id, self.status.selected_model)
        if result is not None:
            self._use_task = asyncio.create_task(self._await_use(result))

    async def _await_use(self, result: Awaitable[None]) -> None:
        try:
            await result
        except asyncio.CancelledError:
            return
        except Exception:  # noqa: BLE001 - keep host modal alive
            if self._can_update_ui:
                self._show_message("Could not switch to the selected model.", "error")

    def _render_status(self, status: LocalBackendStatus) -> None:
        lines = [f"State: {status.state}"]
        if status.endpoint_display:
            lines.append(f"Endpoint: {status.endpoint_display}")
        lines.append(f"Authentication: {status.authentication_source}")
        if status.models:
            lines.append(
                "Models: " + ", ".join(model.display_name or model.id for model in status.models)
            )
        else:
            lines.append("Models: none discovered")
        if status.selected_model:
            lines.append(f"Selected: {status.selected_model}")
        if status.cached:
            lines.append("Using cached results.")
        if status.stale:
            lines.append("Results may be stale.")
        for diagnostic in status.diagnostics:
            lines.append(
                f"{diagnostic.stage}: {diagnostic.message}"
                if diagnostic.stage
                else diagnostic.message
            )
        self.query_one("#local-backend-status", Static).update("\n".join(lines))
        self._update_action_visibility(status)

    def _update_action_visibility(self, status: LocalBackendStatus | None) -> None:
        """Reflect backend-declared capabilities without protocol assumptions."""
        if status is not None:
            actions = set(status.actions)
        else:
            view = self.registry.effective(self.backend_id)
            actions = {"configure", "refresh"}
            if view is not None:
                for action in (
                    "doctor",
                    "reset",
                    "load_model",
                    "unload_model",
                    "download_model",
                ):
                    if getattr(view.backend, action) is not None:
                        actions.add(action)
        for action, button_id in (
            ("use", "#local-action-use"),
            ("doctor", "#local-action-doctor"),
            ("reset", "#local-action-reset"),
            ("load_model", "#local-action-load-model"),
            ("unload_model", "#local-action-unload-model"),
            ("download_model", "#local-action-download-model"),
        ):
            with suppress(NoMatches):
                self.query_one(button_id, Button).styles.display = (
                    "block" if action in actions else "none"
                )

    @property
    def _can_update_ui(self) -> bool:
        return not self._closing and self.is_mounted and self.is_attached and self.is_current

    def _set_progress(self, message: str) -> None:
        if not self._can_update_ui:
            return
        with suppress(NoMatches):
            self.query_one("#local-backend-progress", Static).update(message)

    def _show_message(self, message: str, level: str) -> None:
        if not self._can_update_ui:
            return
        self._notify_callback(message, level)
        self._set_progress(message)


class LocalConfigureScreen(ModalScreen[LocalConfigValues | None]):
    """Render arbitrary text, secret, and choice fields without backend UI code."""

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, spec: LocalConfigureSpec, *, theme: TuiTheme) -> None:
        super().__init__()
        self.spec = spec
        self.theme = theme
        self._field_ids = {
            field.key: f"local-config-input-{index}" for index, field in enumerate(spec.fields)
        }

    def compose(self) -> ComposeResult:
        with Vertical(id="local-configure-screen"):
            yield Static("Configure local backend", id="local-configure-title")
            for field in self.spec.fields:
                field_id = self._field_ids[field.key]
                yield Label(field.label, id=f"local-config-label-{field_id}")
                if field.kind == "choice":
                    yield Select(
                        [(choice, choice) for choice in field.choices],
                        allow_blank=not field.required,
                        id=field_id,
                    )
                else:
                    yield Input(
                        placeholder=field.placeholder or "",
                        password=field.kind == "secret",
                        id=field_id,
                    )
            with Horizontal(id="local-configure-buttons"):
                yield Button("Save", id="local-configure-save", variant="primary")
                yield Button("Cancel", id="local-configure-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "local-configure-save":
            self.action_save()
        elif event.button.id == "local-configure-cancel":
            self.action_cancel()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_save(self) -> None:
        values: dict[str, str] = {}
        secret_keys: set[str] = set()
        for field in self.spec.fields:
            widget = self.query_one(f"#{self._field_ids[field.key]}")
            if field.kind == "choice":
                selected = cast(Select[str], widget).value
                value = selected if isinstance(selected, str) else ""
            else:
                value = cast(Input, widget).value
            values[field.key] = value
            if field.kind == "secret":
                secret_keys.add(field.key)
        self.dismiss(LocalConfigValues(values, secret_keys=frozenset(secret_keys)))


class LocalConfirmScreen(ModalScreen[bool | None]):
    """Small generic confirmation used for destructive backend actions."""

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("escape", "cancel", "Cancel"),
        Binding("enter", "confirm", "Confirm", show=False),
    ]

    def __init__(self, title: str, message: str, *, theme: TuiTheme) -> None:
        super().__init__()
        self.title_text = title
        self.message = message
        self.theme = theme

    def compose(self) -> ComposeResult:
        with Vertical(id="local-confirm-screen"):
            yield Static(self.title_text, id="local-confirm-title", markup=False)
            yield Static(self.message, id="local-confirm-message", markup=False)
            with Horizontal(id="local-confirm-buttons"):
                yield Button("Confirm", id="local-confirm-yes", variant="primary")
                yield Button("Cancel", id="local-confirm-no")

    def on_mount(self) -> None:
        self.query_one("#local-confirm-yes", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(event.button.id == "local-confirm-yes")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(None)


class LocalModelActionScreen(ModalScreen[str | None]):
    """Collect one opaque model reference for a backend-provided action."""

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, title: str, *, theme: TuiTheme) -> None:
        super().__init__()
        self.title_text = title
        self.theme = theme

    def compose(self) -> ComposeResult:
        with Vertical(id="local-model-action-screen"):
            yield Static(self.title_text, id="local-model-action-title", markup=False)
            yield Input(placeholder="Model identifier", id="local-model-action-input")
            with Horizontal(id="local-model-action-buttons"):
                yield Button("Continue", id="local-model-action-continue", variant="primary")
                yield Button("Cancel", id="local-model-action-cancel")

    def on_mount(self) -> None:
        self.query_one("#local-model-action-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self._submit()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "local-model-action-continue":
            self._submit()
        elif event.button.id == "local-model-action-cancel":
            self.action_cancel()

    def _submit(self) -> None:
        value = self.query_one("#local-model-action-input", Input).value.strip()
        if value:
            self.dismiss(value)

    def action_cancel(self) -> None:
        self.dismiss(None)


__all__ = [
    "LocalBackendPickerScreen",
    "LocalBackendScreen",
    "LocalConfigureScreen",
    "LocalConfirmScreen",
    "LocalModelActionScreen",
]
