from __future__ import annotations

import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock, patch

from reorder_engine import beta
from reorder_engine.application.errors import EngineError
from reorder_engine.beta import _ensure_tool_runtime_dir, _tool_runtime_paths, build_parser
from reorder_engine.domain.models import ArchiveKind, ArchiveProbe, ExtractionResult, VolumeSet
from reorder_engine.infrastructure.tool_bootstrap import ToolEnsureResult, ToolPrepareSummary
from reorder_engine.services.beta_pipeline import BetaFolderPipeline
from reorder_engine.services.config import BetaDeepExtractConfig


class _RestoreStub:
    def __init__(self, candidates: list[Path]):
        self.candidates = candidates
        self.identified: list[Path] = []

    def build_post_extract_candidates(self, *_args, **_kwargs):
        return list(self.candidates)

    def identify(self, path: Path):
        self.identified.append(path)
        return ArchiveProbe(path=path, kind=ArchiveKind.ARCHIVE, archive_suffix=".zip")


class BetaLegacyOptionsTests(unittest.TestCase):
    def test_external_result_directory_links_are_rejected_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root, external = base / "input", base / "external"
            root.mkdir()
            external.mkdir()
            try:
                (root / "intermediate").symlink_to(external, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"symlink unavailable: {exc}")
            with self.assertRaises(EngineError):
                _tool_runtime_paths(root)
            pipeline = BetaFolderPipeline(
                folder=root, decrypt_service=object(), restore_service=object(),
                grouper=object(), extractor=object(), passwords=(), volume_sets=(),
            )
            with self.assertRaises(EngineError):
                pipeline.run()
            self.assertEqual(list(external.iterdir()), [])

    def test_existing_empty_result_directories_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sentinels = []
            for name in ("success", "intermediate", "final", "error_files", "deferred_volumes"):
                sentinel = root / name / "existing-empty-directory"
                sentinel.mkdir(parents=True)
                sentinels.append(sentinel)
            pipeline = BetaFolderPipeline(
                folder=root, decrypt_service=object(), restore_service=object(),
                grouper=object(), extractor=object(), passwords=(), volume_sets=(),
            )
            self.assertEqual(pipeline.run().total, 0)
            self.assertTrue(all(path.is_dir() for path in sentinels))

    def test_main_stops_when_any_mandatory_tool_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target"
            cfg = SimpleNamespace(
                paths=SimpleNamespace(keywords=root / "keywords.txt", passwords=root / "passwords.txt"),
                beta=SimpleNamespace(
                    flatten=SimpleNamespace(enabled=True),
                    deep_extract=BetaDeepExtractConfig(False, 4, 100, 200),
                ),
            )
            cfg_manager = Mock()
            cfg_manager.to_app_config.return_value = cfg
            summary = ToolPrepareSummary(
                (
                    ToolEnsureResult("7z", True, True, root / "7z.exe", "7z ready"),
                    ToolEnsureResult("unrar", False, True, None, "locked bundle missing"),
                    ToolEnsureResult("bandizip", True, True, root / "bz.exe", "Bandizip ready"),
                )
            )
            logger = Mock()

            with (
                patch.object(beta, "ConfigManager", return_value=cfg_manager),
                patch.object(beta, "ExtractToolBootstrapper") as bootstrap,
                patch.object(beta, "_setup_logging", return_value=(logger, Mock())),
                patch.object(beta, "KeywordRepository") as keywords,
                patch.object(beta, "PasswordRepository") as passwords,
                patch.object(beta, "BetaFolderPipeline") as pipeline,
                patch.object(beta, "FolderFlattener") as flatten,
                patch.object(beta, "_self_check") as self_check,
            ):
                bootstrap.return_value.ensure_all.return_value = summary
                keywords.return_value.load.return_value = ()
                passwords.return_value.load.return_value = SimpleNamespace(passwords=())

                result = beta.main(["--folder", str(target), "--workdir", str(root), "--self-check"])

            self.assertEqual(result, 3)
            logger.error.assert_any_call("%s", "REQUIRED-TOOL-MISSING[unrar]: locked bundle missing")
            pipeline.assert_not_called()
            flatten.assert_not_called()
            self_check.assert_not_called()

    def test_occupied_package_workspace_is_preserved_and_not_treated_as_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            intermediate = root / "intermediate"
            stale_attempt = intermediate / "package" / "L1" / "attempt_1"
            stale_attempt.mkdir(parents=True)
            sentinel = stale_attempt / "payload.bin"
            sentinel.write_bytes(b"previous run")
            pipeline = BetaFolderPipeline(
                folder=root,
                decrypt_service=object(),
                restore_service=object(),
                grouper=object(),
                extractor=object(),
                passwords=(),
            )

            workspace = pipeline._allocate_package_workspace(intermediate, "package", dry_run=False)

            self.assertNotEqual(workspace, intermediate / "package")
            self.assertEqual(workspace.parent, intermediate)
            self.assertEqual(sentinel.read_bytes(), b"previous run")
            self.assertFalse(pipeline._has_nonempty_file(workspace / "L1" / "attempt_1"))

    def test_default_pipeline_does_not_enumerate_outputs_for_cleanup_tracking(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "input.zip"
            archive.write_bytes(b"archive")
            volumes = VolumeSet(entry=archive, members=(archive,), group_key="test")
            probe = ArchiveProbe(path=archive, kind=ArchiveKind.ARCHIVE, archive_suffix=".zip")
            extractor = SimpleNamespace(
                extract_one=lambda request, **_kwargs: ExtractionResult(
                    volume_set=request.volume_set, ok=True, tool="synthetic"
                )
            )
            pipeline = BetaFolderPipeline(
                folder=root,
                decrypt_service=object(),
                restore_service=object(),
                grouper=object(),
                extractor=extractor,
                passwords=(),
                emit=lambda _message: None,
            )

            with patch.object(Path, "rglob", side_effect=AssertionError("unexpected output enumeration")):
                result, output = pipeline._run_extract_attempt(
                    volumes,
                    probe=probe,
                    output_dir=root / "intermediate" / "attempt_1",
                    dry_run=False,
                    prefix="TEST",
                )

            self.assertTrue(result.ok)
            self.assertIsNotNone(output)
            self.assertEqual(pipeline._produced_files, set())

    def test_cli_accepts_only_supported_archive_mode_and_positive_candidate_limit(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--archive-mode", "wide", "--deep-mode", "smart", "--deep-max-candidates", "3"])
        self.assertEqual(args.archive_mode, "wide")
        self.assertEqual(args.deep_mode, "smart")
        self.assertEqual(args.deep_max_candidates, 3)
        with self.assertRaises(SystemExit):
            parser.parse_args(["--archive-mode", "narrow"])
        with self.assertRaises(SystemExit):
            parser.parse_args(["--deep-max-candidates", "0"])

    def test_tool_temp_cwd_and_environment_are_inside_target(self) -> None:
        target = Path("/work/input").resolve()
        cwd, env = _tool_runtime_paths(target)
        self.assertEqual(cwd.parent, target / "intermediate" / "tool_tmp")
        self.assertTrue(cwd.name.startswith("run_"))
        self.assertEqual(set(env), {"TEMP", "TMP", "TMPDIR"})
        self.assertTrue(all(Path(value) == cwd for value in env.values()))
        second_cwd, _ = _tool_runtime_paths(target)
        self.assertNotEqual(cwd, second_cwd)

    def test_dry_run_leaves_tool_temporary_workspace_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tool_tmp = root / "input" / "intermediate" / "tool_tmp" / "run_test"

            _ensure_tool_runtime_dir(tool_tmp, dry_run=True)
            self.assertFalse(tool_tmp.exists())

            tool_tmp.mkdir(parents=True)
            sentinel = tool_tmp / "preexisting.tmp"
            sentinel.write_bytes(b"preserve")
            _ensure_tool_runtime_dir(tool_tmp, dry_run=True)
            self.assertEqual(sentinel.read_bytes(), b"preserve")

    def test_smart_and_basic_modes_limit_candidates_before_identification(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidates = []
            for index, size in enumerate((4, 12, 8, 16), start=1):
                path = root / f"candidate-{index}.zip"
                path.write_bytes(b"x" * size)
                candidates.append(path)

            for mode, limit, expected in (
                ("smart", 2, candidates[:2]),
                ("basic", 4, [candidates[3]]),
            ):
                restore = _RestoreStub(candidates)
                pipeline = BetaFolderPipeline(
                    folder=root,
                    decrypt_service=object(),
                    restore_service=restore,
                    grouper=object(),
                    extractor=object(),
                    passwords=(),
                    deep_extract=BetaDeepExtractConfig(True, 4, 1, 1, mode, limit),
                )
                out = pipeline._nested_candidates(
                    root,
                    root / "variants",
                    min_archive_bytes=1,
                    final_single_bytes=1,
                    dry_run=True,
                )
                self.assertEqual([item.path for item in out], expected)
                self.assertEqual(restore.identified, expected)

    def test_cleanup_removes_only_matching_dlls_created_in_this_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work = root / "intermediate" / "pkg" / "L1"
            work.mkdir(parents=True)
            generated = work / "dynamic-config.dll"
            preexisting = work / "old-dynamic-config.dll"
            other = work / "payload.txt"
            generated.write_bytes(b"new")
            preexisting.write_bytes(b"old")
            other.write_bytes(b"payload")
            pipeline = BetaFolderPipeline(
                folder=root,
                decrypt_service=object(),
                restore_service=object(),
                grouper=object(),
                extractor=object(),
                passwords=(),
                cleanup_dynamic_libs=True,
                dynamic_lib_patterns=("*dynamic*config*.dll",),
            )
            pipeline._produced_files.add(generated.absolute())

            published = pipeline._promote_final_dir(
                work,
                root / "final",
                package_name="pkg",
                dry_run=False,
                cleanup_dynamic_libs=True,
            )

            self.assertFalse((published / generated.name).exists())
            self.assertTrue((published / preexisting.name).exists())
            self.assertTrue((published / other.name).exists())

    def test_partial_promotion_does_not_apply_dynamic_library_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work = root / "intermediate" / "pkg" / "L1"
            work.mkdir(parents=True)
            dll = work / "dynamic-config.dll"
            dll.write_bytes(b"usable")
            pipeline = BetaFolderPipeline(
                folder=root,
                decrypt_service=object(),
                restore_service=object(),
                grouper=object(),
                extractor=object(),
                passwords=(),
                cleanup_dynamic_libs=True,
                dynamic_lib_patterns=("*dynamic*config*.dll",),
            )
            pipeline._produced_files.add(dll.absolute())

            published = pipeline._promote_final_dir(
                work,
                root / "final",
                package_name="pkg",
                dry_run=False,
                cleanup_dynamic_libs=False,
            )

            self.assertTrue((published / dll.name).exists())


if __name__ == "__main__":
    unittest.main()
