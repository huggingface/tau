import base64
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from subprocess import CompletedProcess

import pytest

from tau_coding import updater
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
    assert detached_command[-7:-1] == (
        str(handoff_dir / "update.ps1"),
        "-ParentProcessId",
        "4242",
        "-LogPath",
        str(handoff_dir / "update.log"),
        "-UpdatePayloadBase64",
    )
    assert json.loads(base64.b64decode(detached_command[-1])) == {
        "executable": "uv",
        "arguments": '"tool" "install" "tau-ai@0.2.4"',
    }
    assert options["creationflags"] == 0x00000208
    script = (handoff_dir / "update.ps1").read_text(encoding="utf-8")
    assert "Wait-Process -InputObject $ParentProcess -ErrorAction Stop" in script
    assert "NoProcessFoundForGivenId" in script
    assert script.index("Wait-Process -InputObject $ParentProcess") < script.index(
        "$UpdateProcess.Start()"
    )
    assert "System.Diagnostics.ProcessStartInfo" in script
    assert "$StartInfo.UseShellExecute = $false" in script


@pytest.mark.parametrize(
    ("argument", "expected"),
    [
        ("", '""'),
        ("plain", '"plain"'),
        ("space value", '"space value"'),
        ('quote"value', '"quote\\"value"'),
        ("trailing\\", '"trailing\\\\"'),
        ('slashes\\\\"quote', '"slashes\\\\\\\\\\"quote"'),
        ("semi;&$()", '"semi;&$()"'),
        ("snowman ☃", '"snowman ☃"'),
    ],
)
def test_quote_windows_argument_uses_microsoft_runtime_rules(argument: str, expected: str) -> None:
    assert updater._quote_windows_argument(argument) == expected


def test_windows_command_line_preserves_argument_boundaries() -> None:
    assert updater._windows_command_line(("", "a b", 'c"', "tail\\")) == (
        '"" "a b" "c\\"" "tail\\\\"'
    )


def test_windows_handoff_reports_staging_write_failure_and_removes_owned_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    update_dir = tmp_path / "owned-handoff"

    def make_update_dir(*args: object, **kwargs: object) -> str:
        del args, kwargs
        update_dir.mkdir()
        return str(update_dir)

    original_write_text = Path.write_text

    def fail_script_write(path: Path, *args: object, **kwargs: object) -> int:
        if path == update_dir / "update.ps1":
            raise OSError("disk is full")
        return original_write_text(path, *args, **kwargs)

    monkeypatch.setattr(updater.tempfile, "mkdtemp", make_update_dir)
    monkeypatch.setattr(Path, "write_text", fail_script_write)

    result = updater._handoff_windows_update(
        ("uv", "tool", "install", "tau-ai@1.0"),
        launcher=lambda *args, **kwargs: object(),
        executable_finder=lambda name: "powershell.exe",
        parent_pid=4242,
        handoff_directory=None,
    )

    assert result.succeeded is False
    assert result.failures == ("Could not stage the detached Windows updater: disk is full",)
    assert not update_dir.exists()


def test_windows_handoff_reports_launch_failure_and_preserves_caller_directory(
    tmp_path: Path,
) -> None:
    update_dir = tmp_path / "caller-handoff"

    def fail_launch(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise OSError("process creation denied")

    result = updater._handoff_windows_update(
        ("uv", "tool", "install", "tau-ai@1.0"),
        launcher=fail_launch,
        executable_finder=lambda name: "powershell.exe",
        parent_pid=4242,
        handoff_directory=update_dir,
    )

    assert result.succeeded is False
    assert result.failures == (
        "Could not start the detached Windows updater: process creation denied",
    )
    assert update_dir.is_dir()
    assert list(update_dir.iterdir()) == []


def _wait_for_file(path: Path, timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {path}")


def _wait_for_text(path: Path, expected: str, timeout: float = 10) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            text = path.read_text(encoding="utf-8-sig")
            if expected in text:
                return text
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {expected!r} in {path}")


def _powershell_engines() -> list[str | None]:
    if sys.platform != "win32":
        return [None]
    engines = [shutil.which(name) for name in ("powershell.exe", "pwsh.exe")]
    available = list(dict.fromkeys(engine for engine in engines if engine is not None))
    return available or [None]


def _engine_id(engine: str | None) -> str:
    return Path(engine).name if engine else "unavailable"


@pytest.mark.parametrize("powershell", _powershell_engines(), ids=_engine_id)
def test_windows_handoff_blocks_for_live_parent_and_preserves_arguments(
    tmp_path: Path, powershell: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    if powershell is None:
        pytest.skip("requires Windows and PowerShell")
    parent = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    marker = tmp_path / "exact argv.json"
    fake_update = tmp_path / "fake updater.py"
    fake_update.write_text(
        "import json, pathlib, sys\n"
        "pathlib.Path(sys.argv[1]).write_text(json.dumps(sys.argv[2:]), encoding='utf-8')\n"
        "raise SystemExit(23)\n",
        encoding="utf-8",
    )
    arguments = (
        "space value",
        "semi;&$()",
        'quote"value',
        "",
        "trailing\\",
        "-named-looking",
    )
    update_dir = tmp_path / "handoff with spaces"
    executable_dir = tmp_path / "executable path with spaces"
    executable_dir.mkdir()
    fake_python = executable_dir / Path(sys.executable).name
    shutil.copy2(sys.executable, fake_python)
    for runtime_dll in Path(sys.base_prefix).glob("python*.dll"):
        shutil.copy2(runtime_dll, executable_dir / runtime_dll.name)

    monkeypatch.setenv("PYTHONHOME", sys.base_prefix)

    try:
        result = updater._handoff_windows_update(
            (str(fake_python), str(fake_update), str(marker), *arguments),
            launcher=subprocess.Popen,
            executable_finder=lambda name: powershell,
            parent_pid=parent.pid,
            handoff_directory=update_dir,
        )
        assert result.succeeded is True
        time.sleep(0.5)
        assert not marker.exists(), "updater ran while the parent was alive"
        parent.terminate()
        parent.wait(timeout=10)
        _wait_for_file(marker)
        assert json.loads(marker.read_text(encoding="utf-8")) == list(arguments)
        _wait_for_text(update_dir / "update.log", "Update command exited with code 23.")
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=10)


@pytest.mark.parametrize("powershell", _powershell_engines(), ids=_engine_id)
def test_windows_helper_wait_failure_is_fail_closed(tmp_path: Path, powershell: str | None) -> None:
    if powershell is None:
        pytest.skip("requires Windows and PowerShell")
    update_dir = tmp_path / "wait-failure"
    update_dir.mkdir()
    script_path = update_dir / "update.ps1"
    log_path = update_dir / "update.log"
    marker = update_dir / "updater-ran"
    fake_update = update_dir / "fake updater.py"
    script_path.write_text(updater._WINDOWS_UPDATE_SCRIPT, encoding="utf-8")
    fake_update.write_text(
        "import pathlib, sys\npathlib.Path(sys.argv[1]).touch()\n",
        encoding="utf-8",
    )
    wrapper = update_dir / "force-wait-failure.ps1"
    wrapper.write_text(
        "function Wait-Process { throw 'forced wait failure' }\n"
        "$Target = $args[0]\n"
        "$TargetArgs = $args[1..($args.Count - 1)]\n"
        "& $Target @TargetArgs\n"
        "exit $LASTEXITCODE\n",
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            powershell,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(wrapper),
            str(script_path),
            "-ParentProcessId",
            str(os.getpid()),
            "-LogPath",
            str(log_path),
            "-UpdatePayloadBase64",
            base64.b64encode(
                json.dumps(
                    {
                        "executable": sys.executable,
                        "arguments": updater._windows_command_line((str(fake_update), str(marker))),
                    }
                ).encode()
            ).decode("ascii"),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    assert completed.returncode != 0
    assert not marker.exists()
    assert "Could not wait for Tau process" in log_path.read_text(encoding="utf-8-sig")


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
