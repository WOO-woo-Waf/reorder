import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
WINDOWS = ROOT / "scripts" / "windows"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def test_root_launchers_delegate_with_their_own_path_and_preserve_arguments():
    wrappers = {
        "extract_here.bat": "extract",
        "beta_here.bat": "self-check",
        "run_in_this_folder.bat": "self-check",
        "beta_basic_2000mb.bat": "basic",
        "beta_smart_identify.bat": "smart",
        "prepare_extract_tools.bat": "prepare",
    }

    for filename, mode in wrappers.items():
        content = _read(ROOT / filename)
        assert "setlocal DisableDelayedExpansion" in content
        expected = f'launcher.bat" {mode} "%~f0"'
        if mode != "prepare":
            expected += " %*"
        assert expected in content
        assert "exit /b %ERRORLEVEL%" in content


def _cmd_available() -> bool:
    return shutil.which("cmd.exe") is not None and (os.name == "nt" or shutil.which("wslpath") is not None)


def _windows_path(path: Path) -> str:
    if os.name == "nt":
        return str(path)
    result = subprocess.run(["wslpath", "-w", str(path)], capture_output=True, text=True, check=True)
    return result.stdout.strip()


def _run_cmd_batch(path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["cmd.exe", "/d", "/c", _windows_path(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )


@pytest.mark.skipif(not _cmd_available(), reason="Windows cmd.exe is required for this integration check")
def test_external_root_bat_uses_override_repo_and_preserves_target_and_exit_code():
    python_exe = os.environ.get("REORDER_TEST_PYTHON")
    if not python_exe:
        if os.name == "nt":
            python_exe = sys.executable
        else:
            candidate = ROOT / "runtime/desktop-build-venv/Scripts/python.exe"
            if not candidate.is_file():
                pytest.skip("set REORDER_TEST_PYTHON to an existing Windows Python")
            python_exe = _windows_path(candidate)
    with tempfile.TemporaryDirectory(prefix="batcompat_", dir=ROOT) as temp:
        base = Path(temp)
        repo = base / "mini repo 中文"
        launcher_dir = repo / "scripts" / "windows"
        package_dir = repo / "src" / "reorder_engine"
        external = base / "Downloaded Link" / "unzip-buff"
        launcher_dir.mkdir(parents=True)
        package_dir.mkdir(parents=True)
        external.mkdir(parents=True)
        (repo / "pyproject.toml").write_text("[project]\nname='mini'\n", encoding="utf-8")
        shutil.copyfile(WINDOWS / "launcher.bat", launcher_dir / "launcher.bat")
        (repo / "src" / "pyzipper.py").write_text("", encoding="utf-8")
        args_file = base / "captured args.json"
        (package_dir / "__init__.py").write_text("", encoding="utf-8")
        (package_dir / "beta.py").write_text(
            "import json, os, sys\n"
            "from pathlib import Path\n"
            "if __name__ == '__main__':\n"
            "    Path(os.environ['BAT_ARGS_FILE']).write_text(json.dumps(sys.argv[1:]), encoding='utf-8')\n"
            "    raise SystemExit(int(os.environ.get('FAKE_BETA_RC', '0')))\n",
            encoding="utf-8",
        )
        wrapper = external / "extract_here.bat"
        shutil.copyfile(ROOT / "extract_here.bat", wrapper)
        driver = base / "run_success.cmd"
        driver.write_text(
            "@echo on\r\n"
            "chcp 65001 >nul\r\n"
            f'set "REORDER_WORK_DIR={_windows_path(repo)}"\r\n'
            f'set "REORDER_PYTHON={python_exe}"\r\n'
            f'set "BAT_ARGS_FILE={_windows_path(args_file)}"\r\n'
            f'set "PYTHONPATH={_windows_path(repo / "src")}"\r\n'
            '"%REORDER_PYTHON%" -V\r\n'
            "echo DIRECT_PY_RC=%ERRORLEVEL%\r\n"
            "set ""FAKE_BETA_RC=37""\r\n"
            "set ""NO_PAUSE=1""\r\n"
            "echo WORK=%REORDER_WORK_DIR% PYTHON=%REORDER_PYTHON%\r\n"
            f'call "{_windows_path(wrapper)}"\r\n'
            "exit /b %ERRORLEVEL%\r\n",
            encoding="utf-8",
        )
        result = _run_cmd_batch(driver)
        evidence = ROOT / "artifacts" / "desktop" / "bat-compat-20261014" / "windows-cmd-integration.log"
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text(
            f"returncode={result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}\n",
            encoding="utf-8",
        )
        assert result.returncode == 37, (result.stdout, result.stderr)
        args = json.loads(args_file.read_text(encoding="utf-8"))
        assert args[args.index("--workdir") + 1].casefold() == _windows_path(repo).casefold()
        assert args[args.index("--folder") + 1].casefold() == _windows_path(external).casefold()

        invalid_driver = base / "run_invalid.cmd"
        invalid_driver.write_text(
            "@echo off\r\n"
            f'set "REORDER_WORK_DIR={_windows_path(base / "missing repo")}"\r\n'
            f'call "{_windows_path(wrapper)}"\r\n'
            "exit /b %ERRORLEVEL%\r\n",
            encoding="utf-8",
        )
        invalid = _run_cmd_batch(invalid_driver)
        assert invalid.returncode == 10, (invalid.stdout, invalid.stderr)


def test_launcher_uses_repository_or_explicit_override_and_fails_invalid_override():
    content = _read(WINDOWS / "launcher.bat")
    assert "set \"WORK_DIR=%REORDER_WORK_DIR%\"" in content
    assert "if not defined WORK_DIR set \"WORK_DIR=%REPO_DIR%\"" in content
    assert "if not exist \"%WORK_DIR%\\pyproject.toml\" goto invalid_workdir" in content
    assert "if not exist \"%WORK_DIR%\\src\\reorder_engine\\beta.py\" goto invalid_workdir" in content
    assert 'Invalid REORDER_WORK_DIR: "%WORK_DIR%"' in content
    assert "D:\\buff\\reorder" not in content


def test_launcher_python_priority_and_validation_are_explicit():
    content = _read(WINDOWS / "launcher.bat")
    sequence = [
        "if defined REORDER_PYTHON (",
        "set \"SELECTED_CONDA_ENV=%REORDER_CONDA_ENV%\"",
        'if exist "%WORK_DIR%\\.venv\\Scripts\\python.exe"',
        'set "SELECTED_CONDA_ENV=reorder"',
        'set "SELECTED_CONDA_ENV=unzip"',
        'set "PYTHON_COMMAND=py -3"',
        'set "PYTHON_COMMAND=python"',
    ]
    offsets = [content.index(item) for item in sequence]
    assert offsets == sorted(offsets)
    assert "if not defined SELECTED_CONDA_ENV set \"SELECTED_CONDA_ENV=%CONDA_ENV%\"" in content
    assert "sys.version_info >= (3, 10)" in content
    assert "import sys, pyzipper, reorder_engine.beta" in content


def test_processing_modes_keep_their_approved_cli_profiles():
    content = _read(WINDOWS / "launcher.bat")
    assert "--deep-extract --preserve-payload-names" in content
    assert "--deep-mode basic --deep-max-depth %DEEP_MAX_DEPTH%" in content
    assert "set \"ARCHIVE_MIN_MB=2000\"" in content
    assert "set \"DEEP_MIN_MB=2000\"" in content
    assert "--deep-max-candidates 1" in content
    assert "set \"ARCHIVE_MIN_MB=128\"" in content
    assert "set \"DEEP_MIN_MB=128\"" in content
    assert "set \"DEEP_FINAL_SINGLE_MB=512\"" in content
    assert "set \"DEEP_MAX_DEPTH=6\"" in content
    assert "set \"DEEP_MAX_CANDIDATES=3\"" in content
    assert "--cleanup-dynamic-libs --dynamic-lib-patterns \"%DYNAMIC_LIB_PATTERNS%\"" in content
    assert 'if not defined NO_PAUSE set "NO_PAUSE=0"' in content
    assert 'if /i "%MODE%"=="extract" if not "%NO_PAUSE%"=="1" pause' in content
    assert "exit /b %RC%" in content


def test_prepare_only_prepares_tools_and_self_checks():
    content = _read(WINDOWS / "launcher.bat")
    command = next(line for line in content.splitlines() if line.startswith("%PYTHON_COMMAND% -m") and "--prepare-tools" in line)
    assert "--prepare-tools --self-check" in command
    assert "--folder \"%WORK_DIR%\"" in command
    assert "--deep-extract" not in command


def test_setup_and_external_stub_generation_are_location_independent():
    setup = _read(WINDOWS / "setup.bat")
    deploy = _read(WINDOWS / "deploy_extract_here.bat")
    assert "py -3" in setup
    assert 'set "BASE_PYTHON=python"' in setup
    assert "sys.version_info >= (3, 10)" in setup
    assert "-m venv \"%REPO_DIR%\\.venv\"" in setup
    assert "-m pip install -e \"%REPO_DIR%\"" in setup
    assert "node" not in setup.lower()
    assert "rust" not in setup.lower()
    generator = _read(WINDOWS / "deploy_extract_here.ps1")
    assert "-LiteralPath" in generator
    assert "FileMode]::CreateNew" in generator
    assert "Refusing to overwrite existing path" in deploy
    assert ".Replace('%', '%%')" in generator
    assert "chcp 65001 >nul" in generator
    assert "launcher.bat" in generator
    assert "extract \"%~f0\" %*" in generator
    assert "D:\\buff\\reorder" not in deploy
