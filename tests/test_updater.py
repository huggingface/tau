from pathlib import Path
from subprocess import CompletedProcess

from tau_coding.updater import detect_install_method, update_tau


def _success(command: tuple[str, ...], **kwargs: object) -> CompletedProcess[str]:
    assert kwargs == {"capture_output": True, "text": True, "check": False}
    return CompletedProcess(command, 0, stdout="upgraded", stderr="")


def test_detect_install_method_uses_receipts_before_installer_metadata(tmp_path: Path) -> None:
    assert detect_install_method(tmp_path) is None
    assert detect_install_method(tmp_path, installer="uv") == "uv-pip"
    assert detect_install_method(tmp_path, installer="pip") == "pip"

    (tmp_path / "uv-receipt.toml").touch()
    assert detect_install_method(tmp_path, installer="pip") == "uv-tool"

    (tmp_path / "uv-receipt.toml").unlink()
    (tmp_path / "pipx_metadata.json").touch()
    assert detect_install_method(tmp_path, installer="pip") == "pipx"


def test_update_tau_uses_uv_tool_for_uv_owned_tool_environment(tmp_path: Path) -> None:
    (tmp_path / "uv-receipt.toml").touch()
    calls: list[tuple[str, ...]] = []

    def runner(command: tuple[str, ...], **kwargs: object) -> CompletedProcess[str]:
        calls.append(command)
        return _success(command, **kwargs)

    result = update_tau(
        runner=runner,
        environment_prefix=tmp_path,
        inspect_distribution=False,
        latest_version_fetcher=lambda: "0.2.4",
        platform_name="linux",
    )

    assert result.succeeded is True
    assert result.command == ("uv", "tool", "install", "tau-ai@0.2.4")
    assert calls == [("uv", "tool", "install", "tau-ai@0.2.4")]


def test_update_tau_hands_windows_uv_tool_update_to_waiting_process(tmp_path: Path) -> None:
    (tmp_path / "uv-receipt.toml").touch()
    handoff_dir = tmp_path / "handoff"
    launches: list[tuple[tuple[str, ...], dict[str, object]]] = []

    def launcher(command: tuple[str, ...], **kwargs: object) -> object:
        launches.append((command, kwargs))
        return object()

    def runner(*args: object, **kwargs: object) -> CompletedProcess[str]:
        del args, kwargs
        raise AssertionError("uv must not run in the live Tau process")

    result = update_tau(
        runner=runner,
        environment_prefix=tmp_path,
        inspect_distribution=False,
        latest_version_fetcher=lambda: "0.2.4",
        platform_name="win32",
        detached_launcher=launcher,
        executable_finder=lambda name: (
            "C:/Windows/powershell.exe" if name == "powershell.exe" else None
        ),
        parent_pid=4242,
        handoff_directory=handoff_dir,
    )

    assert result.succeeded is True
    assert result.deferred is True
    assert result.command == ("uv", "tool", "install", "tau-ai@0.2.4")
    assert "scheduled" in result.stdout
    assert str(handoff_dir / "update.log") in result.stdout
    assert len(launches) == 1
    detached_command, options = launches[0]
    assert detached_command[-7:] == (
        str(handoff_dir / "update.ps1"),
        "4242",
        str(handoff_dir / "update.log"),
        "uv",
        "tool",
        "install",
        "tau-ai@0.2.4",
    )
    assert options["creationflags"] == 0x00000208
    script = (handoff_dir / "update.ps1").read_text(encoding="utf-8")
    assert script.index("Wait-Process -Id $ParentProcessId") < script.index(
        "& $UpdateExecutable @UpdateArguments"
    )


def test_update_tau_reports_uv_latest_version_lookup_failure(tmp_path: Path) -> None:
    (tmp_path / "uv-receipt.toml").touch()

    result = update_tau(
        runner=_success,
        environment_prefix=tmp_path,
        inspect_distribution=False,
        latest_version_fetcher=lambda: None,
    )

    assert result.succeeded is False
    assert result.failures == ("Could not determine the latest Tau version from PyPI.",)


def test_update_tau_uses_pipx_for_pipx_owned_environment(tmp_path: Path) -> None:
    (tmp_path / "pipx_metadata.json").touch()

    result = update_tau(
        runner=_success,
        environment_prefix=tmp_path,
        inspect_distribution=False,
    )

    assert result.command == ("pipx", "upgrade", "tau-ai")


def test_update_tau_reuses_uv_pip_for_uv_installed_distribution(tmp_path: Path) -> None:
    result = update_tau(
        runner=_success,
        python_executable="/env/bin/python",
        environment_prefix=tmp_path,
        installer="uv",
        inspect_distribution=False,
    )

    assert result.command == (
        "uv",
        "pip",
        "install",
        "--python",
        "/env/bin/python",
        "--upgrade",
        "tau-ai",
    )


def test_update_tau_uses_current_environment_pip_for_pip_install(tmp_path: Path) -> None:
    result = update_tau(
        runner=_success,
        python_executable="/env/bin/python",
        environment_prefix=tmp_path,
        installer="pip",
        inspect_distribution=False,
    )

    assert result.command == (
        "/env/bin/python",
        "-m",
        "pip",
        "install",
        "--upgrade",
        "tau-ai",
    )


def test_update_tau_does_not_fall_back_when_owner_update_fails(tmp_path: Path) -> None:
    (tmp_path / "uv-receipt.toml").touch()
    calls: list[tuple[str, ...]] = []

    def runner(command: tuple[str, ...], **kwargs: object) -> CompletedProcess[str]:
        del kwargs
        calls.append(command)
        return CompletedProcess(command, 2, stdout="", stderr="uv failed")

    result = update_tau(
        runner=runner,
        environment_prefix=tmp_path,
        inspect_distribution=False,
        latest_version_fetcher=lambda: "0.2.4",
    )

    assert result.succeeded is False
    assert result.failures == ("uv tool install tau-ai@0.2.4: uv failed",)
    assert calls == [("uv", "tool", "install", "tau-ai@0.2.4")]


def test_update_tau_refuses_direct_url_install(tmp_path: Path) -> None:
    result = update_tau(
        runner=_success,
        environment_prefix=tmp_path,
        direct_url="file:///checkout/tau",
        installer="uv",
        inspect_distribution=False,
    )

    assert result.succeeded is False
    assert "original source: file:///checkout/tau" in result.failures[0]


def test_update_tau_refuses_conda_or_pixi_environment(tmp_path: Path) -> None:
    (tmp_path / "conda-meta").mkdir()

    result = update_tau(
        runner=_success,
        environment_prefix=tmp_path,
        inspect_distribution=False,
    )

    assert result.succeeded is False
    assert "Conda/Pixi-managed" in result.failures[0]


def test_update_tau_refuses_unknown_installer(tmp_path: Path) -> None:
    result = update_tau(
        runner=_success,
        environment_prefix=tmp_path,
        installer="custom-manager",
        inspect_distribution=False,
    )

    assert result.succeeded is False
    assert "Package metadata reports: custom-manager" in result.failures[0]
