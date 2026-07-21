from subprocess import CompletedProcess

from tau_coding.updater import update_tau


def test_update_tau_falls_back_to_next_installer() -> None:
    calls: list[tuple[str, ...]] = []

    def runner(command: tuple[str, ...], **kwargs: object) -> CompletedProcess[str]:
        assert kwargs == {"capture_output": True, "text": True, "check": False}
        calls.append(command)
        if command[0] == "uv":
            return CompletedProcess(command, 1, stdout="", stderr="not a uv tool")
        return CompletedProcess(command, 0, stdout="upgraded", stderr="")

    result = update_tau(
        runner=runner,
        commands=(("uv", "tool", "upgrade", "tau-ai"), ("pipx", "upgrade", "tau-ai")),
    )

    assert result.succeeded is True
    assert result.command == ("pipx", "upgrade", "tau-ai")
    assert result.stdout == "upgraded"
    assert result.failures == ("uv tool upgrade tau-ai: not a uv tool",)
    assert calls == [
        ("uv", "tool", "upgrade", "tau-ai"),
        ("pipx", "upgrade", "tau-ai"),
    ]


def test_update_tau_reports_unavailable_and_failed_installers() -> None:
    def runner(command: tuple[str, ...], **kwargs: object) -> CompletedProcess[str]:
        del kwargs
        if command[0] == "uv":
            raise FileNotFoundError("uv not found")
        return CompletedProcess(command, 2, stdout="failed", stderr="")

    result = update_tau(
        runner=runner,
        commands=(("uv",), ("pipx",)),
    )

    assert result.succeeded is False
    assert result.command is None
    assert result.failures == ("uv: uv not found", "pipx: failed")
