"""Pi-compatible JSONL RPC frontend for a Tau coding session."""

from __future__ import annotations

import json
import sys
from collections.abc import AsyncIterator, Mapping
from dataclasses import asdict, is_dataclass
from typing import IO, Literal, Protocol, cast

import anyio
from pydantic import BaseModel

from tau_agent.types import JSONValue
from tau_coding.commands import CommandRegistry
from tau_coding.events import CodingSessionEvent
from tau_coding.session import CodingSession

_MAX_RECORD_BYTES = 16 * 1024 * 1024


class RpcSession(Protocol):
    """Public CodingSession surface consumed by RPC mode."""

    @property
    def model(self) -> str: ...

    @property
    def provider_name(self) -> str: ...

    @property
    def thinking_level(self) -> str: ...

    @property
    def available_thinking_levels(self) -> tuple[str, ...]: ...

    @property
    def available_model_choices(self) -> tuple[object, ...]: ...

    @property
    def messages(self) -> tuple[object, ...]: ...

    @property
    def session_id(self) -> str | None: ...

    @property
    def auto_compact_token_threshold(self) -> int | None: ...

    @property
    def session_stats(self) -> object: ...

    @property
    def command_registry(self) -> CommandRegistry: ...

    @property
    def state(self) -> object: ...

    def prompt(
        self,
        content: str,
        *,
        streaming_behavior: Literal["steer", "follow_up"] | None = None,
    ) -> AsyncIterator[CodingSessionEvent]: ...

    def cancel(self) -> None: ...

    def set_provider(self, provider_name: str, *, persist_default: bool = True) -> None: ...

    def set_model(self, model: str) -> None: ...

    async def set_thinking_level(self, level: str) -> str: ...

    async def compact(self, instructions: str | None = None) -> str: ...

    async def new_session(self) -> str: ...

    async def resume(self, session_id: str) -> str: ...

    async def tree_choices(self) -> tuple[object, ...]: ...

    async def branch_to_entry(self, entry_id: str) -> object: ...

    async def emit_pending_session_start(self) -> None: ...

    async def aclose(self) -> None: ...


class RpcServer:
    """Read Pi-style commands and stream responses/events as strict JSONL."""

    def __init__(
        self,
        session: RpcSession,
        *,
        stdin: IO[str] | None = None,
        stdout: IO[str] | None = None,
    ) -> None:
        self._session = session
        self._stdin = stdin or sys.stdin
        self._stdout = stdout or sys.stdout
        self._write_lock = anyio.Lock()
        self._active_prompt_tasks = 0

    async def run(self) -> None:
        """Serve commands until stdin reaches EOF."""
        await self._session.emit_pending_session_start()
        async with anyio.create_task_group() as tasks:
            while True:
                line = await anyio.to_thread.run_sync(self._stdin.readline)
                if line == "":
                    break
                if line.endswith("\n"):
                    line = line[:-1]
                if line.endswith("\r"):
                    line = line[:-1]
                if not line:
                    continue
                if len(line.encode("utf-8")) > _MAX_RECORD_BYTES:
                    await self._error(None, "parse", "RPC record exceeds 16 MiB")
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as exc:
                    await self._error(None, "parse", f"Failed to parse command: {exc.msg}")
                    continue
                if not isinstance(value, dict):
                    await self._error(None, "parse", "Command must be a JSON object")
                    continue
                await self._dispatch(cast(dict[str, object], value), tasks)
            if self._active_prompt_tasks:
                self._session.cancel()
        await self._session.aclose()

    async def _dispatch(self, command: dict[str, object], tasks: anyio.abc.TaskGroup) -> None:
        request_id = command.get("id")
        command_type = command.get("type")
        if not isinstance(command_type, str):
            await self._error(request_id, "parse", "Command requires a string 'type'")
            return
        try:
            if command_type in {"prompt", "steer", "follow_up"}:
                message = _required_string(command, "message")
                behavior: Literal["steer", "follow_up"] | None = None
                if command_type == "steer":
                    behavior = "steer"
                elif command_type == "follow_up":
                    behavior = "follow_up"
                explicit = command.get("streamingBehavior")
                if explicit is not None:
                    if explicit not in {"steer", "followUp"}:
                        raise ValueError("streamingBehavior must be 'steer' or 'followUp'")
                    behavior = "follow_up" if explicit == "followUp" else "steer"
                if self._active_prompt_tasks and behavior is None:
                    raise ValueError(
                        "Agent is already streaming; set streamingBehavior to steer or followUp"
                    )
                self._active_prompt_tasks += 1
                await self._response(request_id, command_type)
                tasks.start_soon(self._run_prompt, message, behavior)
                return
            if command_type == "abort":
                self._session.cancel()
                await self._response(request_id, command_type)
                return
            if command_type == "get_state":
                await self._response(
                    request_id,
                    command_type,
                    {
                        "model": self._session.model,
                        "provider": self._session.provider_name,
                        "thinkingLevel": self._session.thinking_level,
                        "isStreaming": self._active_prompt_tasks > 0,
                        "sessionId": self._session.session_id,
                        "autoCompactionEnabled": (
                            self._session.auto_compact_token_threshold is not None
                        ),
                        "messageCount": len(self._session.messages),
                    },
                )
                return
            if command_type == "get_messages":
                await self._response(
                    request_id, command_type, {"messages": list(self._session.messages)}
                )
                return
            if command_type == "get_available_models":
                await self._response(
                    request_id,
                    command_type,
                    {"models": list(self._session.available_model_choices)},
                )
                return
            if command_type == "set_model":
                provider = command.get("provider")
                if provider is not None:
                    if not isinstance(provider, str):
                        raise ValueError("provider must be a string")
                    self._session.set_provider(provider, persist_default=False)
                self._session.set_model(_required_string(command, "modelId"))
                await self._response(
                    request_id,
                    command_type,
                    {"provider": self._session.provider_name, "id": self._session.model},
                )
                return
            if command_type == "get_available_thinking_levels":
                await self._response(
                    request_id,
                    command_type,
                    {"levels": list(self._session.available_thinking_levels)},
                )
                return
            if command_type == "set_thinking_level":
                level = await self._session.set_thinking_level(_required_string(command, "level"))
                await self._response(request_id, command_type, {"level": level})
                return
            if command_type == "compact":
                instructions = _optional_string(command, "customInstructions")
                result = await self._session.compact(instructions)
                await self._response(request_id, command_type, {"summary": result})
                return
            if command_type == "new_session":
                message = await self._session.new_session()
                await self._response(request_id, command_type, {"message": message})
                return
            if command_type == "switch_session":
                session_id = command.get("sessionId", command.get("sessionPath"))
                if not isinstance(session_id, str):
                    raise ValueError("switch_session requires sessionId")
                message = await self._session.resume(session_id)
                await self._response(request_id, command_type, {"message": message})
                return
            if command_type == "get_session_stats":
                await self._response(request_id, command_type, self._session.session_stats)
                return
            if command_type == "get_tree":
                choices = await self._session.tree_choices()
                await self._response(
                    request_id,
                    command_type,
                    {"entries": list(choices), "leafId": _leaf_id(self._session.state)},
                )
                return
            if command_type == "fork":
                fork_result = await self._session.branch_to_entry(
                    _required_string(command, "entryId")
                )
                await self._response(request_id, command_type, fork_result)
                return
            if command_type == "get_commands":
                commands = self._session.command_registry.list_commands()
                await self._response(
                    request_id,
                    command_type,
                    {
                        "commands": [
                            {"name": item.name, "description": item.description}
                            for item in commands
                        ]
                    },
                )
                return
            raise ValueError(f"Unknown command: {command_type}")
        except (RuntimeError, ValueError) as exc:
            await self._error(request_id, command_type, str(exc))

    async def _run_prompt(
        self, message: str, behavior: Literal["steer", "follow_up"] | None
    ) -> None:
        try:
            stream = self._session.prompt(message, streaming_behavior=behavior)
            async for event in stream:
                await self._write(event)
        except (RuntimeError, ValueError) as exc:
            await self._write({"type": "rpc_error", "error": str(exc)})
        finally:
            self._active_prompt_tasks -= 1

    async def _response(
        self,
        request_id: object,
        command: str,
        data: object | None = None,
    ) -> None:
        response: dict[str, object] = {
            "type": "response",
            "command": command,
            "success": True,
        }
        if request_id is not None:
            response["id"] = request_id
        if data is not None:
            response["data"] = data
        await self._write(response)

    async def _error(self, request_id: object, command: str, error: str) -> None:
        response: dict[str, object] = {
            "type": "response",
            "command": command,
            "success": False,
            "error": error,
        }
        if request_id is not None:
            response["id"] = request_id
        await self._write(response)

    async def _write(self, value: object) -> None:
        payload = json.dumps(_jsonable(value), ensure_ascii=False, separators=(",", ":"))
        async with self._write_lock:
            self._stdout.write(payload + "\n")
            self._stdout.flush()


def _required_string(command: Mapping[str, object], key: str) -> str:
    value = command.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _optional_string(command: Mapping[str, object], key: str) -> str | None:
    value = command.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value


def _leaf_id(state: object) -> object:
    return getattr(state, "active_leaf_id", None)


def _jsonable(value: object) -> JSONValue:
    if isinstance(value, BaseModel):
        return cast(JSONValue, value.model_dump(mode="json", by_alias=True))
    if is_dataclass(value) and not isinstance(value, type):
        return cast(JSONValue, asdict(value))
    if isinstance(value, Mapping):
        return cast(
            JSONValue,
            {str(key): _jsonable(item) for key, item in value.items()},
        )
    if isinstance(value, (list, tuple)):
        return cast(JSONValue, [_jsonable(item) for item in value])
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "model_dump"):
        return cast(JSONValue, value.model_dump(mode="json", by_alias=True))
    return str(value)


async def run_rpc_session(session: CodingSession) -> None:
    """Run RPC mode for an already configured CodingSession."""
    await RpcServer(session).run()
