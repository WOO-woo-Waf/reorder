"""Bounded native Windows smoke checks for the root BAT compatibility contract.

Run with an existing Windows Python that has the core dependencies installed. This script intentionally copies only a small synthetic
checkout into artifacts and never reads or copies the source checkout's config,
password list, runtime state, or user archives.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import zipfile


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts" / "desktop" / "bat-compat-20261014" / "native"
WINDOWS_PYTHON = os.environ.get("REORDER_TEST_PYTHON", sys.executable)


def _windows_path(path: Path | str) -> str:
    value = str(path)
    if os.name == "nt":
        return value
    result = subprocess.run(
        ["wslpath", "-w", value], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _cmd_available() -> bool:
    return shutil.which("cmd.exe") is not None and (
        os.name == "nt" or shutil.which("wslpath") is not None
    )


def _fresh_environment(repo: Path | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("REORDER_WORK_DIR", None)
    env.pop("REORDER_CONDA_ENV", None)
    env.pop("CONDA_ENV", None)
    env["REORDER_PYTHON"] = WINDOWS_PYTHON
    env["NO_PAUSE"] = "1"
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    if repo is not None:
        env["REORDER_WORK_DIR"] = _windows_path(repo)
    return env


def _run_bat(
    batch: Path,
    *,
    cwd: Path,
    env: dict[str, str],
    arguments: tuple[str, ...] = (),
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    command = f'call "{_windows_path(batch)}"'
    if arguments:
        command += " " + " ".join(f'"{_windows_path(argument)}"' for argument in arguments)
    # Popen's Windows list quoting is for CRT programs. cmd.exe parses its own
    # command tail; passing the tail verbatim avoids literal backslash-quotes.
    invocation = f"cmd.exe /d /c {command}" if os.name == "nt" else ["cmd.exe", "/d", "/c", command]
    return subprocess.run(
        invocation,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def _copy_checkout(repo: Path) -> None:
    repo.mkdir(parents=True)
    (repo / "resources").mkdir()
    shutil.copytree(ROOT / "src", repo / "src")
    shutil.copytree(ROOT / "scripts", repo / "scripts")
    shutil.copytree(ROOT / "tools" / "desktop-vendor", repo / "tools" / "desktop-vendor")
    shutil.copytree(ROOT / "tools" / "7zip" / "26.04", repo / "tools" / "7zip" / "26.04")
    shutil.copyfile(ROOT / "resources" / "keywords.txt", repo / "resources" / "keywords.txt")
    shutil.copyfile(ROOT / "tools" / "apate.py", repo / "tools" / "apate.py")
    shutil.copyfile(ROOT / "pyproject.toml", repo / "pyproject.toml")
    for name in (
        "extract_here.bat",
        "deploy_extract_here.bat",
        "prepare_extract_tools.bat",
        "beta_smart_identify.bat",
    ):
        shutil.copyfile(ROOT / name, repo / name)


def _write_zip(path: Path, entries: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _mock_launchers(run_root: Path) -> list[dict]:
    """Check command selection and RC with a harmless importable fake engine."""
    repo = run_root / "mock repo 中文"
    src = repo / "src"
    package = src / "reorder_engine"
    package.mkdir(parents=True)
    shutil.copytree(ROOT / "scripts/windows", repo / "scripts/windows")
    (repo / "pyproject.toml").write_text("[project]\nname='mock-engine'\n", encoding="utf-8")
    (src / "pyzipper.py").write_text("", encoding="utf-8")
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "beta.py").write_text(
        "import json, os, sys\nfrom pathlib import Path\n"
        "if __name__ == '__main__':\n"
        "    Path(os.environ['BAT_ARGS_FILE']).write_text(json.dumps(sys.argv[1:]), encoding='utf-8')\n"
        "    raise SystemExit(37)\n", encoding="utf-8",
    )
    captures = run_root / "mock-args.json"
    cases = []
    for name, mode in (
        ("extract_here.bat", "extract"), ("beta_here.bat", "self-check"),
        ("run_in_this_folder.bat", "self-check"), ("beta_basic_2000mb.bat", "basic"),
        ("beta_smart_identify.bat", "smart"), ("prepare_extract_tools.bat", "prepare"),
    ):
        shutil.copyfile(ROOT / name, repo / name)
        env = _fresh_environment()
        env["BAT_ARGS_FILE"] = str(captures)
        result = _run_bat(repo / name, cwd=run_root, env=env)
        _check(result.returncode == 37, f"{name} lost engine RC: {result.stdout}\n{result.stderr}")
        args = json.loads(captures.read_text(encoding="utf-8"))
        _check(Path(args[args.index("--folder") + 1]) == repo, f"{name} default folder changed: {args}")
        if mode == "basic":
            _check(args[args.index("--archive-min-mb") + 1] == "2000", "basic threshold changed")
            _check(args[args.index("--deep-max-candidates") + 1] == "1", "basic candidate limit changed")
        if mode == "smart":
            _check(args[args.index("--deep-max-candidates") + 1] == "3", "smart candidate limit changed")
        if mode == "prepare":
            _check("--prepare-tools" in args and "--deep-extract" not in args, "prepare processed inputs")
        cases.append({"case": name, "rc": result.returncode, "args": args})

    # Never extract at a drive root: the fake module only records the argument.
    env = _fresh_environment()
    env["BAT_ARGS_FILE"] = str(captures)
    drive_root = Path(repo.anchor)
    result = _run_bat(repo / "extract_here.bat", cwd=run_root, env=env, arguments=(str(drive_root),))
    _check(result.returncode == 37, "drive-root argument lost return code")
    args = json.loads(captures.read_text(encoding="utf-8"))
    _check(Path(args[args.index("--folder") + 1]) == drive_root, f"drive root became drive-relative: {args}")
    cases.append({"case": "drive-root", "rc": result.returncode, "args": args})

    # Real condabin installs expose a BAT; verify control returns after it.
    conda_dir = run_root / "mock condabin"
    conda_dir.mkdir()
    helper = conda_dir / "conda_mock.py"
    helper.write_text(
        "import subprocess, sys\n"
        "assert sys.argv[1:6] == ['run', '-n', 'mock env', '--no-capture-output', 'python']\n"
        "raise SystemExit(subprocess.call([sys.executable, *sys.argv[6:]]))\n", encoding="utf-8",
    )
    (conda_dir / "conda.bat").write_text(
        '@echo off\r\n"%MOCK_PYTHON%" "%MOCK_CONDA_SCRIPT%" %*\r\nexit /b %ERRORLEVEL%\r\n',
        encoding="utf-8",
    )
    env.pop("REORDER_PYTHON")
    env.update({"REORDER_CONDA_ENV": "mock env", "MOCK_PYTHON": WINDOWS_PYTHON,
                "MOCK_CONDA_SCRIPT": str(helper), "PATH": str(conda_dir) + os.pathsep + env["PATH"]})
    result = _run_bat(repo / "extract_here.bat", cwd=run_root, env=env)
    _check(result.returncode == 37 and "Done. rc=37" in result.stdout,
           f"conda.bat did not return to the launcher: {result.stdout}\n{result.stderr}")
    cases.append({"case": "conda-bat", "rc": result.returncode})

    # A fresh core venv uses only stdlib and the fake dependencies; no pip/network.
    venv = repo / ".venv"
    subprocess.run([WINDOWS_PYTHON, "-m", "venv", "--without-pip", str(venv)], check=True,
                   capture_output=True, timeout=60)
    env = _fresh_environment()
    env.pop("REORDER_PYTHON")
    env["BAT_ARGS_FILE"] = str(captures)
    result = _run_bat(repo / "extract_here.bat", cwd=run_root, env=env)
    _check(result.returncode == 37, f"repo .venv selection failed: {result.stdout}\n{result.stderr}")
    cases.append({"case": "repo-venv", "rc": result.returncode})
    return cases


def _run() -> Path:
    if not _cmd_available():
        raise RuntimeError("native cmd.exe and wslpath (when under WSL) are required")
    if os.name != "nt":
        raise RuntimeError("Run this script using a Windows Python, not WSL Python")
    python_path = Path(WINDOWS_PYTHON)
    if not python_path.is_file():
        raise RuntimeError(f"required existing Windows Python is missing: {python_path}")

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    run_root = ARTIFACTS / f"run-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"
    run_root.mkdir()
    mock_cases = _mock_launchers(run_root)
    (run_root / "mock-launchers.json").write_text(json.dumps(mock_cases, ensure_ascii=False, indent=2), encoding="utf-8")
    repo = run_root / "mini repo 中文"
    target = run_root / "deployed target 中文"
    override_target = run_root / "external wrapper 中文"
    smart_target = run_root / "nested target 中文"
    outside_cwd = run_root / "unrelated cwd 中文"
    target.mkdir()
    override_target.mkdir()
    smart_target.mkdir()
    outside_cwd.mkdir()
    _copy_checkout(repo)

    forbidden = (repo / "config.json", repo / ".env", repo / "resources" / "passwords.txt")
    _check(all(not item.exists() for item in forbidden), "synthetic checkout unexpectedly contains excluded config or password inputs")
    _check(not (repo / "runtime").exists(), "synthetic checkout must not copy runtime state")
    _check(not (repo / "tests").exists(), "synthetic checkout must not copy tests or user archives")

    evidence: list[str] = [f"repo={repo}", f"cwd={outside_cwd}", f"python={WINDOWS_PYTHON}"]

    def save_evidence() -> None:
        (run_root / "evidence.txt").write_text("\n".join(evidence) + "\n", encoding="utf-8")

    save_evidence()
    config_path = repo / "config.json"
    _check(not config_path.exists(), "fresh checkout unexpectedly has config.json")
    prepare = _run_bat(
        repo / "prepare_extract_tools.bat",
        cwd=outside_cwd,
        env=_fresh_environment(repo),
    )
    evidence.extend(("prepare stdout:", prepare.stdout, "prepare stderr:", prepare.stderr))
    save_evidence()
    _check(prepare.returncode == 0, f"prepare returned {prepare.returncode}: {prepare.stdout}\n{prepare.stderr}")
    _check(config_path.is_file(), "prepare did not create a fresh default config")
    for version in ("26.04", "7.13", "7.40.0.1"):
        _check(version in prepare.stdout, f"prepare output did not report tool version {version}")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    tool_paths = [
        config.get("tools", {}).get("seven_zip", {}).get("exe"),
        config.get("tools", {}).get("unrar", {}).get("exe"),
        config.get("tools", {}).get("bandizip", {}).get("exe"),
    ]
    for configured in tool_paths:
        _check(isinstance(configured, str) and configured, f"tool lock path was not saved: {configured!r}")
        _check(not Path(configured).is_absolute(), f"locked tool path is not relative to the mini repo: {configured}")
        _check("D:\\buff\\reorder" not in configured.casefold(), f"tool config retained old host path: {configured}")
    _check(not any("D:\\buff\\reorder" in str(value).casefold() for value in tool_paths), "tool config contains old host path")
    evidence.append("config tools=" + json.dumps(tool_paths, ensure_ascii=False))

    # Migrate an automatically persisted pre-26.04 layout, then verify a damaged
    # managed bundle fails before even the self-check launches any executable.
    legacy_exe = repo / "tools/7zip/Files/7-Zip/7z.exe"
    legacy_exe.parent.mkdir(parents=True)
    legacy_exe.write_bytes(b"synthetic obsolete tool; must never execute")
    config["tools"]["seven_zip"]["exe"] = "tools/7zip/Files/7-Zip/7z.exe"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    migrated = _run_bat(repo / "prepare_extract_tools.bat", cwd=outside_cwd, env=_fresh_environment())
    evidence.extend(("managed legacy migration stdout:", migrated.stdout, "migration stderr:", migrated.stderr))
    save_evidence()
    _check(migrated.returncode == 0, f"old managed tool path did not migrate: {migrated.stdout}\n{migrated.stderr}")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    _check(config["tools"]["seven_zip"]["exe"].replace("\\", "/") == "tools/7zip/26.04/7z.exe", "old bundled tool path remained configured")
    readme = repo / "tools/7zip/26.04/readme.txt"
    readme_bytes = readme.read_bytes()
    readme.write_bytes(b"synthetic damaged bundle member")
    try:
        broken = _run_bat(repo / "prepare_extract_tools.bat", cwd=outside_cwd, env=_fresh_environment())
        evidence.extend(("broken managed bundle stdout:", broken.stdout, "broken bundle stderr:", broken.stderr))
        save_evidence()
        _check(broken.returncode == 3, "damaged managed bundle did not retain exit code 3")
        _check("REQUIRED-MISSING" in broken.stdout and "SELF-CHECK: begin" not in broken.stdout,
               "damaged managed bundle was probed or not reported")
    finally:
        readme.write_bytes(readme_bytes)

    deploy_target = target / "extract_here.bat"
    deploy = _run_bat(
        repo / "deploy_extract_here.bat",
        cwd=outside_cwd,
        env=_fresh_environment(),
        arguments=(str(target),),
        timeout=60,
    )
    evidence.extend(("deploy stdout:", deploy.stdout, "deploy stderr:", deploy.stderr))
    save_evidence()
    _check(deploy.returncode == 0, f"deploy returned {deploy.returncode}: {deploy.stdout}\n{deploy.stderr}")
    _check(deploy_target.is_file(), f"deployer did not create {deploy_target}")
    original_stub = deploy_target.read_bytes()
    redeploy = _run_bat(
        repo / "deploy_extract_here.bat",
        cwd=outside_cwd,
        env=_fresh_environment(),
        arguments=(str(target),),
        timeout=60,
    )
    evidence.extend(("redeploy stdout:", redeploy.stdout, "redeploy stderr:", redeploy.stderr))
    save_evidence()
    _check(redeploy.returncode == 13, f"redeploy should refuse overwrite with rc 13, got {redeploy.returncode}")
    _check(deploy_target.read_bytes() == original_stub, "redeploy changed the existing entry file")

    ordinary_payload = b"native BAT ordinary ZIP payload\n"
    disguised_payload = b"native BAT ZIP with a misleading suffix\n"
    _write_zip(target / "ordinary.zip", {"ordinary.txt": ordinary_payload})
    _write_zip(target / "misleading.7z", {"misleading.txt": disguised_payload})
    reserved_bytes = b"existing final output must remain unchanged\n"
    reserved_final = target / "final" / "ordinary" / "ordinary.txt"
    reserved_final.parent.mkdir(parents=True)
    reserved_final.write_bytes(reserved_bytes)
    empty_sentinel = target / "final" / "existing empty folder"
    empty_sentinel.mkdir()
    attempt_sentinel = target / "intermediate" / "ordinary" / "L1" / "attempt_1" / "sentinel.bin"
    attempt_sentinel.parent.mkdir(parents=True)
    attempt_bytes = b"preexisting attempt workspace sentinel\n"
    attempt_sentinel.write_bytes(attempt_bytes)

    extract = _run_bat(
        deploy_target,
        cwd=outside_cwd,
        env=_fresh_environment(),
    )
    evidence.extend(("deployed extract stdout:", extract.stdout, "deployed extract stderr:", extract.stderr))
    save_evidence()
    _check(extract.returncode == 0, f"deployed extract returned {extract.returncode}: {extract.stdout}\n{extract.stderr}")
    _check(reserved_final.read_bytes() == reserved_bytes, "extract changed the preexisting same-name final file")
    _check(attempt_sentinel.read_bytes() == attempt_bytes, "extract changed the preexisting intermediate attempt sentinel")
    _check(empty_sentinel.is_dir(), "extract removed a preexisting empty directory")
    for name, expected in (("ordinary.txt", ordinary_payload), ("misleading.txt", disguised_payload)):
        matches = [path for path in (target / "final").rglob(name) if path.is_file()]
        _check(any(path.read_bytes() == expected for path in matches), f"expected payload not found under target/final: {name}")
    archived = target / "success" / "archives"
    for name in ("ordinary.zip", "misleading.7z"):
        copies = [path for path in archived.rglob(name) if path.is_file()]
        _check(len(copies) == 1, f"original archive was not archived exactly once: {name}")
    leftovers = [
        path for path in target.rglob("*")
        if path.is_file() and any(token in path.name.casefold() for token in ("restoreab", "restore_ab", "restore-copy"))
    ]
    _check(not leftovers, f"unexpected full-archive recovery copy remains: {leftovers}")

    # A copied root wrapper with REORDER_WORK_DIR must also work from an unrelated cwd.
    external_wrapper = override_target / "extract_here.bat"
    shutil.copyfile(repo / "extract_here.bat", external_wrapper)
    external_payload = b"external root BAT payload\n"
    _write_zip(override_target / "external.zip", {"external.txt": external_payload})
    external = _run_bat(
        external_wrapper,
        cwd=outside_cwd,
        env=_fresh_environment(repo),
    )
    evidence.extend(("external wrapper stdout:", external.stdout, "external wrapper stderr:", external.stderr))
    save_evidence()
    _check(external.returncode == 0, f"external wrapper returned {external.returncode}: {external.stdout}\n{external.stderr}")
    _check(any(path.read_bytes() == external_payload for path in (override_target / "final").rglob("external.txt")), "external wrapper did not extract its own-directory target")

    invalid_env = _fresh_environment(run_root / "missing repo")
    invalid_env.pop("REORDER_PYTHON", None)
    invalid = _run_bat(external_wrapper, cwd=outside_cwd, env=invalid_env)
    evidence.extend(("invalid override stdout:", invalid.stdout, "invalid override stderr:", invalid.stderr))
    save_evidence()
    _check(invalid.returncode == 10, f"invalid workdir should retain rc 10, got {invalid.returncode}")

    inner_zip = run_root / "inner.zip"
    with zipfile.ZipFile(inner_zip, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("nested-payload.bin", bytes(range(256)) * 4096)
    outer_zip = smart_target / "outer.zip"
    with zipfile.ZipFile(outer_zip, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(inner_zip, arcname="inner.zip")
    smart_env = _fresh_environment(repo)
    smart_env.update(
        {
            "ARCHIVE_MIN_MB": "1",
            "DEEP_MIN_MB": "1",
            "DEEP_FINAL_SINGLE_MB": "1",
        }
    )
    nested = _run_bat(
        repo / "beta_smart_identify.bat",
        cwd=outside_cwd,
        env=smart_env,
        arguments=(str(smart_target),),
    )
    evidence.extend(("nested smart stdout:", nested.stdout, "nested smart stderr:", nested.stderr))
    save_evidence()
    _check(nested.returncode == 0, f"nested smart extraction returned {nested.returncode}: {nested.stdout}\n{nested.stderr}")
    nested_payloads = [path for path in (smart_target / "final").rglob("nested-payload.bin") if path.is_file()]
    _check(nested_payloads, "nested ZIP payload was not recursively extracted")
    _check(nested_payloads[0].read_bytes() == bytes(range(256)) * 4096, "nested ZIP payload bytes differ")

    save_evidence()

    (run_root / "result.json").write_text(json.dumps({
        "passed": True, "mock_launchers": len(mock_cases),
        "native_cases": ["fixed-tools", "legacy-tool-migration", "damaged-bundle-refusal", "deploy", "redeploy-refusal", "ordinary-disguised",
                         "existing-final-and-intermediate", "copied-wrapper", "invalid-repo", "nested-smart"],
    }, indent=2), encoding="utf-8")
    return run_root


if __name__ == "__main__":
    output = _run()
    print(f"BAT-NATIVE-14 evidence: {output}")
