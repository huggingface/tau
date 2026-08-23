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
from textual.containers import Vertical
from textual.css.query import NoMatches
from textual.events import Key
from textual.screen import ModalScreen
from textual.widgets import Input, Label, ListItem, ListView, Select, Static

from tau_coding.local_backends import (
    LocalAction,
    LocalBackendRegistry,
    LocalBackendStatus,
    LocalConfigureSpec,
    LocalConfigValues,
    LocalConfirmationChoice,
    LocalConfirmationRequest,
    LocalModel,
    LocalOperationResult,
    LocalProgress,
    LocalSearchResult,
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
                    "Choose a backend. The recommended choice is marked.",
                    id="local-backend-picker-help",
                )
                yield ListView(
                    *[ListItem(Label(self._label(view), markup=False)) for view in self.views],
                    id="local-backend-list",
                )
                yield Static(
                    "↑/↓ navigate - Enter selects - Escape closes",
                    id="local-backend-picker-footer",
                )

    def on_mount(self) -> None:
        if self.views:
            backend_list = self.query_one("#local-backend-list", ListView)
            backend_list.index = self._selected_index()
            backend_list.focus()

    def on_key(self, event: Key) -> None:
        if event.key == "up":
            event.stop()
            self.action_cursor_up()
        elif event.key == "down":
            event.stop()
            self.action_cursor_down()
        elif event.key == "enter":
            event.stop()
            self.action_select_cursor()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        event.stop()
        self.selected = self.views[event.index].backend.id
        self.dismiss(self.selected)

    def action_cursor_up(self) -> None:
        self.query_one("#local-backend-list", ListView).action_cursor_up()
        self._sync_selected()

    def action_cursor_down(self) -> None:
        self.query_one("#local-backend-list", ListView).action_cursor_down()
        self._sync_selected()

    def action_select_cursor(self) -> None:
        self.query_one("#local-backend-list", ListView).action_select_cursor()

    def action_confirm(self) -> None:
        self.action_select_cursor()

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
        Binding("up", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("enter", "select_cursor", "Select", show=False),
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
        self._selected_model_id: str | None = None
        self._menu_items: tuple[tuple[str, str], ...] = ()
        self._worker: asyncio.Task[None] | None = None
        self._use_task: asyncio.Task[None] | None = None
        self._closing = False

    def compose(self) -> ComposeResult:
        with Vertical(id="local-backend-screen"):
            yield Static("Local backend", id="local-backend-title")
            yield Static(
                "Choose a model or backend action.",
                id="local-backend-help",
            )
            yield Static("Looking for a local server…", id="local-backend-status")
            yield ListView(id="local-backend-menu")
            yield Static("", id="local-backend-progress")
            yield Static(
                "↑/↓ navigate - Enter selects - Escape closes",
                id="local-backend-footer",
            )

    async def on_mount(self) -> None:
        await self._render_menu(None)
        self.query_one("#local-backend-menu", ListView).focus()
        # Opening a backend is an explicit user action, so probe its effective
        # saved/environment/default endpoint immediately. Backends still own
        # which endpoint that means; the generic host never scans.
        self._start_operation("refresh")

    def on_unmount(self) -> None:
        """Stop host-owned tasks before Textual detaches this modal."""
        self._closing = True
        self.registry.cancel(self.backend_id)
        for task in (self._worker, self._use_task):
            if task is not None and not task.done():
                task.cancel()

    def on_key(self, event: Key) -> None:
        if event.key == "up":
            event.stop()
            self.action_cursor_up()
        elif event.key == "down":
            event.stop()
            self.action_cursor_down()
        elif event.key == "enter":
            event.stop()
            self.action_select_cursor()

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.list_view.id != "local-backend-menu":
            return
        self._sync_selected_model()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id != "local-backend-menu":
            return
        event.stop()
        self._activate_menu_item(event.index)

    def action_cursor_up(self) -> None:
        self.query_one("#local-backend-menu", ListView).action_cursor_up()
        self._sync_selected_model()

    def action_cursor_down(self) -> None:
        self.query_one("#local-backend-menu", ListView).action_cursor_down()
        self._sync_selected_model()

    def action_select_cursor(self) -> None:
        self.query_one("#local-backend-menu", ListView).action_select_cursor()

    def _activate_menu_item(self, index: int) -> None:
        if not 0 <= index < len(self._menu_items):
            return
        token = self._menu_items[index][0]
        if token.startswith("model:"):
            self._selected_model_id = token.removeprefix("model:")
            self._activate_selected_model()
        elif token == "configure":
            self._open_configure()
        elif token == "refresh":
            self._start_operation("refresh")
        elif token == "doctor":
            self._start_operation("doctor")
        elif token == "reset":
            self._confirm_reset()
        elif token in {"download_model", "search_models"}:
            self._open_model_action(cast(LocalAction, token))

    def action_cancel(self) -> None:
        # on_unmount owns cancellation; let Textual begin the screen pop first.
        self.dismiss(None)

    def _open_configure(self) -> None:
        # Do not make users wait for an unavailable default probe before they
        # can enter a custom endpoint.
        if self._worker is not None and not self._worker.done():
            self.registry.cancel(self.backend_id, "refresh")
            self._worker.cancel()
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
                "Remove this backend's saved integration settings? Stored credentials "
                "require a separate confirmation.",
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
            "download_model": "Download model",
            "search_models": "Search Hugging Face models",
        }
        placeholders = {
            "download_model": "owner/repository[:quantization]",
            "search_models": "Hugging Face model ID or search query",
        }
        if action not in labels:
            self._show_message("Select a model from the model list first.", "warning")
            return
        self.app.push_screen(
            LocalModelActionScreen(
                labels[action],
                placeholder=placeholders[action],
                theme=self.theme,
            ),
            callback=lambda model_id: self._handle_model_action(action, model_id),
        )

    def _handle_model_action(self, action: LocalAction, model_id: str | None) -> None:
        if model_id is not None:
            self._start_operation(action, model_id=model_id)

    def _activate_selected_model(self) -> None:
        model = self._selected_model()
        if model is None or self.status is None:
            return
        if model.state in {"loaded", "sleeping", "available", None}:
            self._choose_loaded_model_action(model.id)
            return
        if model.state in {"loading", "downloading", "unknown"}:
            self._show_message(f"{model.id} is currently {model.state}.", "warning")
            return
        if "load_model" in self.status.actions:
            self._start_operation("load_model", model_id=model.id)
        else:
            self._show_message(f"{model.id} is not currently available to use.", "warning")

    def _selected_model(self) -> LocalModel | None:
        if self.status is None or self._selected_model_id is None:
            return None
        return next(
            (model for model in self.status.models if model.id == self._selected_model_id),
            None,
        )

    def _choose_loaded_model_action(self, model_id: str) -> None:
        request = LocalConfirmationRequest(
            f"Choose an action for {model_id!r}.",
            (
                LocalConfirmationChoice("use", "Use model", True),
                LocalConfirmationChoice("unload", "Unload model"),
                LocalConfirmationChoice("cancel", "Cancel"),
            ),
        )
        self.app.push_screen(
            LocalChoiceConfirmScreen(request, theme=self.theme),
            callback=lambda choice: self._handle_loaded_model_action(model_id, choice),
        )

    def _handle_loaded_model_action(self, model_id: str, choice: str | None) -> None:
        if choice == "use":
            self._use_model(model_id)
        elif choice == "unload":
            self._start_operation("unload_model", model_id=model_id)

    def _sync_selected_model(self) -> None:
        menu = self.query_one("#local-backend-menu", ListView)
        index = menu.index
        if index is None or not 0 <= index < len(self._menu_items):
            return
        token = self._menu_items[index][0]
        if token.startswith("model:"):
            self._selected_model_id = token.removeprefix("model:")

    def _start_operation(
        self,
        action: LocalAction,
        *,
        values: Mapping[str, str] | LocalConfigValues | None = None,
        model_id: str | None = None,
        confirmation: str | None = None,
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
        self._worker = asyncio.create_task(
            self._run_operation(action, values, model_id, confirmation)
        )

    async def _run_operation(
        self,
        action: LocalAction,
        values: Mapping[str, str] | LocalConfigValues | None,
        model_id: str | None,
        confirmation: str | None = None,
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
                    confirmation=confirmation,
                )
            elif action == "search_models":
                assert model_id is not None
                result = await self.registry.search_models(
                    self.backend_id,
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
            await self._render_status(result.backend_status)
        if result.confirmation is not None:
            self._open_operation_confirmation(result.confirmation, action, values, model_id)
            self._set_progress("")
            return
        if result.field_errors:
            self._show_message(
                "Configuration was not saved. Review the fields and try again.",
                "error",
            )
        elif result.message:
            self._show_message(result.message, "info")
        if result.search_results:
            self._set_progress("Select a model and quantization to download.")
            self.app.push_screen(
                LocalSearchResultsScreen(result.search_results, theme=self.theme),
                callback=self._download_search_result,
            )
        for diagnostic in result.diagnostics:
            message = (
                f"{diagnostic.stage}: {diagnostic.message}"
                if diagnostic.stage
                else diagnostic.message
            )
            self._show_message(message, diagnostic.severity)
        if result.credential_orphaned:
            self._set_progress("Credential cleanup needs attention.")

    def _download_search_result(self, model_id: str | None) -> None:
        if model_id is not None:
            self._start_operation("download_model", model_id=model_id)

    def _open_operation_confirmation(
        self,
        request: LocalConfirmationRequest,
        action: LocalAction,
        values: Mapping[str, str] | LocalConfigValues | None,
        model_id: str | None,
    ) -> None:
        self.app.push_screen(
            LocalChoiceConfirmScreen(request, theme=self.theme),
            callback=lambda choice: self._resume_confirmed_operation(
                choice, action, values, model_id
            ),
        )

    def _resume_confirmed_operation(
        self,
        choice: str | None,
        action: LocalAction,
        values: Mapping[str, str] | LocalConfigValues | None,
        model_id: str | None,
    ) -> None:
        if choice is not None:
            self._start_operation(action, values=values, model_id=model_id, confirmation=choice)

    def _use_selected(self) -> None:
        model = self._selected_model()
        model_id = (
            model.id if model is not None else self.status.selected_model if self.status else None
        )
        if model_id is None:
            self._show_message(
                "Refresh this backend and select an available model first.",
                "warning",
            )
            return
        if model is not None and model.state not in {"loaded", "sleeping", "available", None}:
            self._show_message(f"{model.id} must be loaded before it can be used.", "warning")
            return
        self._use_model(model_id)

    def _use_model(self, model_id: str) -> None:
        if not self._is_idle():
            self._show_message("Tau must be idle before switching models.", "warning")
            return
        if self._worker is not None and not self._worker.done():
            self._show_message("An operation is already in progress.", "warning")
            return
        if self._use_task is not None and not self._use_task.done():
            self._show_message("A model switch is already in progress.", "warning")
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
        result = self.on_use(view.backend.provider_id, model_id)
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

    async def _render_status(self, status: LocalBackendStatus) -> None:
        lines = [f"State: {status.state}"]
        if status.endpoint_display:
            lines.append(f"Endpoint: {status.endpoint_display}")
        lines.append(f"Authentication: {status.authentication_source}")
        if not status.models:
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
        await self._render_menu(status)

    async def _render_menu(self, status: LocalBackendStatus | None) -> None:
        menu = self.query_one("#local-backend-menu", ListView)
        prior_token = None
        if menu.index is not None and 0 <= menu.index < len(self._menu_items):
            prior_token = self._menu_items[menu.index][0]
        actions = set(status.actions) if status is not None else {"configure", "refresh"}
        models = status.models if status is not None else ()
        selected_model = status.selected_model if status is not None else None
        items: list[tuple[str, str]] = [
            (f"model:{model.id}", _model_label(model, selected_model)) for model in models
        ]
        action_labels = (
            ("search_models", "Search Hugging Face models…"),
            ("download_model", "Download an exact Hugging Face model…"),
            ("configure", "Configure connection…"),
            ("refresh", "Refresh server state"),
            ("doctor", "Run Doctor"),
            ("reset", "Reset integration settings…"),
        )
        items.extend((action, label) for action, label in action_labels if action in actions)
        self._menu_items = tuple(items)
        await menu.clear()
        await menu.extend(ListItem(Label(label, markup=False)) for _, label in self._menu_items)
        if not self._menu_items:
            menu.index = None
            return
        preferred = prior_token or (
            f"model:{self._selected_model_id}" if self._selected_model_id else None
        )
        if models and self._selected_model_id is None:
            preferred = f"model:{selected_model or models[0].id}"
        menu.index = next(
            (index for index, (token, _) in enumerate(self._menu_items) if token == preferred),
            0,
        )
        self._sync_selected_model()

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
        Binding("ctrl+s", "save", "Save", show=False),
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
            yield Static(
                "Enter advances/saves - Ctrl+S saves - Escape cancels",
                id="local-configure-footer",
            )

    def on_mount(self) -> None:
        if self.spec.fields:
            self.query_one(f"#{self._field_ids[self.spec.fields[0].key]}").focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        field_ids = tuple(self._field_ids[field.key] for field in self.spec.fields)
        if event.input.id not in field_ids:
            return
        event.stop()
        index = field_ids.index(event.input.id)
        if index == len(field_ids) - 1:
            self.action_save()
        else:
            self.query_one(f"#{field_ids[index + 1]}").focus()

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
        Binding("up", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("enter", "select_cursor", "Select", show=False),
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
            yield ListView(
                ListItem(Label("Yes", markup=False)),
                ListItem(Label("No", markup=False)),
                id="local-confirm-list",
            )
            yield Static(
                "↑/↓ navigate - Enter selects - Escape cancels",
                id="local-confirm-footer",
            )

    def on_mount(self) -> None:
        choices = self.query_one("#local-confirm-list", ListView)
        choices.index = 1
        choices.focus()

    def on_key(self, event: Key) -> None:
        if event.key == "up":
            event.stop()
            self.action_cursor_up()
        elif event.key == "down":
            event.stop()
            self.action_cursor_down()
        elif event.key == "enter":
            event.stop()
            self.action_select_cursor()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        event.stop()
        self.dismiss(event.index == 0)

    def action_cursor_up(self) -> None:
        self.query_one("#local-confirm-list", ListView).action_cursor_up()

    def action_cursor_down(self) -> None:
        self.query_one("#local-confirm-list", ListView).action_cursor_down()

    def action_select_cursor(self) -> None:
        self.query_one("#local-confirm-list", ListView).action_select_cursor()

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(None)


class LocalChoiceConfirmScreen(ModalScreen[str | None]):
    """Render an arbitrary backend confirmation without protocol knowledge."""

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("escape", "cancel", "Cancel"),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("enter", "select_cursor", "Select", show=False),
    ]

    def __init__(self, request: LocalConfirmationRequest, *, theme: TuiTheme) -> None:
        super().__init__()
        self.request = request
        self.theme = theme

    def compose(self) -> ComposeResult:
        with Vertical(id="local-confirm-screen"):
            yield Static("Confirm backend action", id="local-confirm-title", markup=False)
            yield Static(self.request.message, id="local-confirm-message", markup=False)
            yield ListView(
                *[
                    ListItem(
                        Label(
                            f"{choice.label} — recommended" if choice.recommended else choice.label,
                            markup=False,
                        )
                    )
                    for choice in self.request.choices
                ],
                id="local-choice-list",
            )
            yield Static(
                "↑/↓ navigate - Enter selects - Escape cancels",
                id="local-confirm-footer",
            )

    def on_mount(self) -> None:
        choices = self.query_one("#local-choice-list", ListView)
        choices.index = next(
            (index for index, choice in enumerate(self.request.choices) if choice.recommended),
            0,
        )
        choices.focus()

    def on_key(self, event: Key) -> None:
        if event.key == "up":
            event.stop()
            self.action_cursor_up()
        elif event.key == "down":
            event.stop()
            self.action_cursor_down()
        elif event.key == "enter":
            event.stop()
            self.action_select_cursor()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        event.stop()
        self.dismiss(self.request.choices[event.index].value)

    def action_cursor_up(self) -> None:
        self.query_one("#local-choice-list", ListView).action_cursor_up()

    def action_cursor_down(self) -> None:
        self.query_one("#local-choice-list", ListView).action_cursor_down()

    def action_select_cursor(self) -> None:
        self.query_one("#local-choice-list", ListView).action_select_cursor()

    def action_cancel(self) -> None:
        self.dismiss(None)


class LocalSearchResultsScreen(ModalScreen[str | None]):
    """Choose one backend-provided artifact variant for download."""

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("escape", "cancel", "Cancel"),
        Binding("enter", "confirm", "Download", show=False),
    ]

    def __init__(
        self,
        results: tuple[LocalSearchResult, ...],
        *,
        theme: TuiTheme,
    ) -> None:
        super().__init__()
        self.results = results
        self.theme = theme
        self.options = tuple(
            option for result in results for option in _search_result_options(result)
        )

    def compose(self) -> ComposeResult:
        with Vertical(id="local-search-results-screen"):
            yield Static("Download model", id="local-search-results-title", markup=False)
            yield Static(
                "Choose a Hugging Face model variant. llama.cpp performs the download.",
                id="local-search-results-help",
                markup=False,
            )
            yield ListView(
                *[ListItem(Label(label, markup=False)) for _, label, _ in self.options],
                id="local-search-results-list",
            )
            yield Static(
                "↑/↓ navigate - Enter continues - Escape cancels",
                id="local-search-results-footer",
            )

    def on_mount(self) -> None:
        if not self.options:
            return
        index = next(
            (index for index, (_, _, recommended) in enumerate(self.options) if recommended),
            0,
        )
        model_list = self.query_one("#local-search-results-list", ListView)
        model_list.index = index
        model_list.focus()

    def on_key(self, event: Key) -> None:
        if event.key == "up":
            event.stop()
            self.action_cursor_up()
        elif event.key == "down":
            event.stop()
            self.action_cursor_down()
        elif event.key == "enter":
            event.stop()
            self.action_select_cursor()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        event.stop()
        self.action_confirm()

    def action_cursor_up(self) -> None:
        self.query_one("#local-search-results-list", ListView).action_cursor_up()

    def action_cursor_down(self) -> None:
        self.query_one("#local-search-results-list", ListView).action_cursor_down()

    def action_select_cursor(self) -> None:
        self.query_one("#local-search-results-list", ListView).action_select_cursor()

    def action_confirm(self) -> None:
        if not self.options:
            return
        index = self.query_one("#local-search-results-list", ListView).index
        if index is not None and 0 <= index < len(self.options):
            self.dismiss(self.options[index][0])

    def action_cancel(self) -> None:
        self.dismiss(None)


class LocalModelActionScreen(ModalScreen[str | None]):
    """Collect one opaque model reference for a backend-provided action."""

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(
        self,
        title: str,
        *,
        placeholder: str = "Model identifier",
        theme: TuiTheme,
    ) -> None:
        super().__init__()
        self.title_text = title
        self.placeholder = placeholder
        self.theme = theme

    def compose(self) -> ComposeResult:
        with Vertical(id="local-model-action-screen"):
            yield Static(self.title_text, id="local-model-action-title", markup=False)
            yield Input(placeholder=self.placeholder, id="local-model-action-input")
            yield Static(
                "Enter continues - Escape cancels",
                id="local-model-action-footer",
            )

    def on_mount(self) -> None:
        self.query_one("#local-model-action-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self._submit()

    def _submit(self) -> None:
        value = self.query_one("#local-model-action-input", Input).value.strip()
        if value:
            self.dismiss(value)

    def action_cancel(self) -> None:
        self.dismiss(None)


def _model_label(model: LocalModel, selected_model: str | None) -> str:
    label = model.display_name or model.id
    if label != model.id:
        label = f"{label} ({model.id})"
    details = []
    if model.state:
        details.append(model.state)
    if model.id == selected_model:
        details.append("active")
    return label + (" — " + " · ".join(details) if details else "")


def _search_result_options(
    result: LocalSearchResult,
) -> tuple[tuple[str, str, bool], ...]:
    if not result.options:
        restricted = " — restricted" if result.restricted else ""
        return ((result.id, result.label + restricted, False),)
    options: list[tuple[str, str, bool]] = []
    for option in result.options:
        details = [option.label]
        if option.size_bytes is not None:
            details.append(_format_bytes(option.size_bytes))
        if option.recommended:
            details.append("recommended")
        if result.restricted:
            details.append("restricted")
        options.append((option.id, f"{result.label} — " + " · ".join(details), option.recommended))
    return tuple(options)


def _format_bytes(value: int) -> str:
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{value} B"


__all__ = [
    "LocalBackendPickerScreen",
    "LocalBackendScreen",
    "LocalConfigureScreen",
    "LocalChoiceConfirmScreen",
    "LocalConfirmScreen",
    "LocalModelActionScreen",
    "LocalSearchResultsScreen",
]
