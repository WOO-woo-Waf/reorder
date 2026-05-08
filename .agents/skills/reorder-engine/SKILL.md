---
name: reorder-engine
description: Work with the reorder-engine project as a reusable Windows archive extraction automation skill. Use when Codex needs to run or modify this project, prepare 7-Zip/RAR/Bandizip tools, process messy download folders, package or install the project skill, troubleshoot archive grouping/restoration/extraction failures, or explain the repository workflow to another agent.
---

# Reorder Engine

## Start Here

Use this skill for the `reorder-engine` repository. Treat the repository root as the workdir that owns `config.json`, `resources/`, `tools/`, and the Python package under `src/`.

On this project, shell commands may need the repo instructions loaded by the user. If the injected instructions mention `rtk`, prefix shell commands with `rtk`.

## Common Commands

Run from the repository root:

```powershell
$env:PYTHONPATH="$PWD\src"
python -m unittest discover -s tests
python -m reorder_engine.beta --workdir "$PWD" --folder "$PWD" --prepare-tools --self-check
```

For normal folder processing:

```powershell
python -m reorder_engine.beta --workdir <repo-root> --folder <target-folder> --self-check
```

BAT entrypoints:

```bat
prepare_extract_tools.bat
beta_here.bat <target-folder>
cleanup_extract_artifacts.bat
```

## Windows Extractor Preparation

Use `prepare_extract_tools.bat` first when setting up a fresh machine. The project supports:

- `tools/7zip/7z.exe`
- `tools/7zip/Files/7-Zip/7z.exe`
- `tools/rar/Rar.exe` or `tools/rar/UnRAR.exe`
- `tools/bandizip/bz.exe`
- `tools/_packages/winrar-cli.zip`
- `tools/_packages/bandizip-cli.zip`

The tool prep code lives in `src/reorder_engine/infrastructure/tool_bootstrap.py`. It writes discovered executable paths back to `config.json`.

Do not add private license files such as `rarreg.key` to Git. Before committing WinRAR/RAR or Bandizip binaries, confirm the target repository's redistribution policy.

## Repository Map

- `src/reorder_engine/beta.py`: beta CLI and operational pipeline wiring
- `src/reorder_engine/services/beta_pipeline.py`: multi-stage folder processing
- `src/reorder_engine/services/restoring.py`: archive signature detection and suffix repair variants
- `src/reorder_engine/services/grouping.py`: split volume grouping
- `src/reorder_engine/services/extracting.py`: extractor orchestration and password/tool matrix
- `src/reorder_engine/infrastructure/tools.py`: command wrappers for 7z/RAR/Bandizip
- `src/reorder_engine/infrastructure/tool_bootstrap.py`: Windows extractor discovery, ZIP package extraction, and config persistence
- `resources/passwords.txt`: password candidates
- `resources/keywords.txt`: cleanup keyword candidates
- `tools/README.md`: local binary layout and packaging notes

## Skill Packaging

To distribute this skill, copy `.agents/skills/reorder-engine/` into another Codex skill directory such as:

```powershell
Copy-Item -Recurse .agents\skills\reorder-engine "$env:USERPROFILE\.codex\skills\reorder-engine"
```

The skill expects the full repository to be available. For another agent, provide the repository path and ask it to use `$reorder-engine`.
