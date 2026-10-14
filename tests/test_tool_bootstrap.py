from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from reorder_engine.infrastructure import repo_tooling
from reorder_engine.infrastructure.tool_bootstrap import ExtractToolBootstrapper
from reorder_engine.services.config import ConfigManager


class ToolBootstrapTests(unittest.TestCase):
    def test_legacy_auto_saved_bundle_path_upgrades_to_the_fixed_version(self) -> None:
        from reorder_engine.infrastructure.sevenzip_bootstrap import SevenZipBootstrapper

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = ConfigManager(self._write_config(root), root_dir=root)
            manager.load_or_create_default()
            old = root / "tools/7zip/Files/7-Zip/7z.exe"
            pinned = root / "tools/7zip/26.04/7z.exe"
            for path in (old, pinned):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"executable")
            (root / "scripts").mkdir()
            (root / "scripts/desktop-sevenzip.lock.json").write_text("{}", encoding="utf-8")
            manager.set_seven_zip_exe(old)
            with mock.patch(
                "reorder_engine.infrastructure.sevenzip_bootstrap.locked_seven_zip_path", return_value=pinned,
            ), mock.patch(
                "reorder_engine.infrastructure.sevenzip_bootstrap.find_locked_seven_zip", return_value=pinned,
            ):
                result = SevenZipBootstrapper().ensure(manager.to_app_config(), manager)
            self.assertTrue(result.ok)
            self.assertEqual(result.exe, pinned)
            self.assertEqual(manager.to_app_config().tools.seven_zip.exe, pinned)

    def test_malformed_lock_cannot_turn_saved_bundled_path_into_override(self) -> None:
        from reorder_engine.infrastructure.sevenzip_bootstrap import SevenZipBootstrapper

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = self._write_config(root)
            pinned = root / "tools/7zip/26.04/7z.exe"
            pinned.parent.mkdir(parents=True)
            pinned.write_bytes(b"fixed executable")
            (root / "scripts").mkdir()
            (root / "scripts/desktop-sevenzip.lock.json").write_text("{broken", encoding="utf-8")
            manager = ConfigManager(config_path, root_dir=root)
            manager.load_or_create_default()
            manager.set_seven_zip_exe(pinned)
            with mock.patch(
                "reorder_engine.infrastructure.sevenzip_bootstrap.locked_seven_zip_path",
                side_effect=ValueError("invalid lock JSON"),
            ):
                result = SevenZipBootstrapper().ensure(manager.to_app_config(), manager)
                self.assertFalse(result.ok)
                self.assertIn("invalid lock JSON", result.message)
                external = root / "custom/7z.exe"
                external.parent.mkdir()
                external.write_bytes(b"user override")
                manager.set_seven_zip_exe(external)
                result = SevenZipBootstrapper().ensure(manager.to_app_config(), manager)
                self.assertTrue(result.ok)
                self.assertEqual(result.exe, external)

    def _write_config(self, root: Path) -> Path:
        config = {
            "version": 1,
            "beta": {
                "flatten": {"enabled": True, "exclude_dirs": ["tools"], "allowed_roots": [], "allow_inside_project_repo": False},
                "exclude": {"names": ["config.json"], "exts": [".bat", ".cmd", ".ps1", ".py", ".json", ".md", ".log"]},
                "guess_suffixes": [".7z", ".zip", ".rar"],
                "extractor_order": ["7z", "unrar", "bandizip"],
            },
            "paths": {"keywords": "resources/keywords.txt", "passwords": "resources/passwords.txt"},
            "tools": {
                "seven_zip": {
                    "exe": None,
                    "auto_download": False,
                    "download": {"source": "7-zip.org", "prefer": "msi-x64", "timeout_sec": 1},
                    "install_dir": "tools/7zip",
                },
                "unrar": {"exe": None},
                "bandizip": {"exe": None},
            },
        }
        path = root / "config.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        return path

    def test_prepare_finds_nested_7z_and_extracts_optional_zip_package(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "resources").mkdir()
            (root / "resources" / "keywords.txt").write_text("", encoding="utf-8")
            (root / "resources" / "passwords.txt").write_text("", encoding="utf-8")
            seven = root / "tools" / "7zip" / "Files" / "7-Zip" / "7z.exe"
            seven.parent.mkdir(parents=True)
            seven.write_bytes(b"fake")

            package_dir = root / "tools" / "_packages"
            package_dir.mkdir(parents=True)
            with zipfile.ZipFile(package_dir / "winrar-cli.zip", "w") as zf:
                zf.writestr("UnRAR.exe", b"fake")

            cfg_mgr = ConfigManager(self._write_config(root), root_dir=root)
            cfg_mgr.load_or_create_default()
            summary = ExtractToolBootstrapper().ensure_all(cfg_mgr.to_app_config(), cfg_mgr)

            self.assertTrue(summary.required_ok)
            self.assertEqual(summary.get("7z").exe, seven)
            self.assertTrue((root / "tools" / "rar" / "UnRAR.exe").exists())
            self.assertFalse(summary.get("unrar").required)

            data = json.loads((root / "config.json").read_text(encoding="utf-8"))
            self.assertEqual(data["tools"]["seven_zip"]["exe"].replace("\\", "/"), "tools/7zip/Files/7-Zip/7z.exe")
            self.assertEqual(data["tools"]["unrar"]["exe"].replace("\\", "/"), "tools/rar/UnRAR.exe")

    def test_git_pinned_tools_precede_legacy_tree_and_persist_repo_relative_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = self._write_config(root)
            lock_dir = root / "scripts"
            lock_dir.mkdir()
            (lock_dir / "desktop-sevenzip.lock.json").write_text("{}", encoding="utf-8")
            (lock_dir / "desktop-tools.lock.json").write_text("{}", encoding="utf-8")
            legacy = root / "tools" / "7zip" / "7z.exe"
            legacy.parent.mkdir(parents=True)
            legacy.write_bytes(b"old")
            pinned_7z = root / "tools" / "7zip" / "26.04" / "7z.exe"
            pinned_7z.parent.mkdir(parents=True)
            pinned_7z.write_bytes(b"pinned")

            def stage_cli(repo: Path, destination: Path, seven_zip: Path | None) -> None:
                self.assertEqual(repo, root)
                self.assertEqual(seven_zip, pinned_7z)
                for rel in (Path("tools/unrar/UnRAR.exe"), Path("tools/bandizip/bz.exe")):
                    target = destination / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(b"fixed")

            cfg_mgr = ConfigManager(config_path, root_dir=root)
            cfg_mgr.load_or_create_default()
            with mock.patch(
                "reorder_engine.infrastructure.sevenzip_bootstrap.find_locked_seven_zip",
                return_value=pinned_7z,
            ), mock.patch(
                "reorder_engine.infrastructure.tool_bootstrap.stage_locked_cli_tools",
                side_effect=stage_cli,
            ):
                summary = ExtractToolBootstrapper().ensure_all(cfg_mgr.to_app_config(), cfg_mgr)

            self.assertEqual(summary.get("7z").exe, pinned_7z)
            self.assertIn("Git-pinned", summary.get("7z").message)
            self.assertEqual(summary.get("unrar").exe, root / "runtime/cli-tools/tools/unrar/UnRAR.exe")
            self.assertEqual(summary.get("bandizip").exe, root / "runtime/cli-tools/tools/bandizip/bz.exe")
            data = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(data["tools"]["seven_zip"]["exe"].replace("\\", "/"), "tools/7zip/26.04/7z.exe")
            self.assertEqual(data["tools"]["unrar"]["exe"].replace("\\", "/"),
                             "runtime/cli-tools/tools/unrar/UnRAR.exe")
            self.assertEqual(data["tools"]["bandizip"]["exe"].replace("\\", "/"),
                             "runtime/cli-tools/tools/bandizip/bz.exe")

    def test_broken_locked_seven_zip_fails_without_host_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = self._write_config(root)
            (root / "scripts").mkdir()
            (root / "scripts" / "desktop-sevenzip.lock.json").write_text("{}", encoding="utf-8")
            cfg_mgr = ConfigManager(config_path, root_dir=root)
            cfg_mgr.load_or_create_default()

            with mock.patch(
                "reorder_engine.infrastructure.sevenzip_bootstrap.find_locked_seven_zip",
                side_effect=RuntimeError("locked executable sha256 mismatch"),
            ), mock.patch("shutil.which") as which:
                result = ExtractToolBootstrapper().ensure_all(cfg_mgr.to_app_config(), cfg_mgr).get("7z")

            self.assertFalse(result.ok)
            self.assertIn("locked 7-Zip bundle is invalid", result.message)
            self.assertIn("sha256 mismatch", result.message)
            self.assertNotIn(mock.call("7z"), which.call_args_list)

    def test_locked_seven_zip_checks_every_file_and_reports_missing_or_bad_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = root / "tools" / "7zip" / "26.04"
            names = ("7z.exe", "7z.dll", "License.txt", "readme.txt")
            entry = {
                "vendored_directory": "tools/7zip/26.04",
                "files": [{"path": name, "sha256": "expected"} for name in names],
            }
            for name in names:
                (bundle / name).parent.mkdir(parents=True, exist_ok=True)
                (bundle / name).write_bytes(name.encode())
            stage_module = SimpleNamespace(
                find_seven_zip=mock.Mock(return_value=bundle / "7z.exe"),
                load_seven_zip_lock=mock.Mock(return_value=entry),
                stage_desktop_tools=SimpleNamespace(
                    sha256_file=mock.Mock(return_value="expected"),
                ),
            )
            with mock.patch.object(repo_tooling, "_import_script_module", return_value=stage_module):
                self.assertEqual(repo_tooling.find_locked_seven_zip(root), bundle / "7z.exe")

                (bundle / "readme.txt").unlink()
                with self.assertRaisesRegex(RuntimeError, "readme.txt.*is missing"):
                    repo_tooling.find_locked_seven_zip(root)

                (bundle / "readme.txt").write_bytes(b"bad")
                stage_module.stage_desktop_tools.sha256_file.side_effect = lambda path: (
                    "wrong" if path.name == "readme.txt" else "expected"
                )
                with self.assertRaisesRegex(RuntimeError, "readme.txt sha256 wrong"):
                    repo_tooling.find_locked_seven_zip(root)

    def test_broken_locked_optional_bundle_fails_without_legacy_or_path_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = self._write_config(root)
            (root / "scripts").mkdir()
            (root / "scripts" / "desktop-sevenzip.lock.json").write_text("{}", encoding="utf-8")
            (root / "scripts" / "desktop-tools.lock.json").write_text("{}", encoding="utf-8")
            old_unrar = root / "tools" / "rar" / "UnRAR.exe"
            old_unrar.parent.mkdir(parents=True)
            old_unrar.write_bytes(b"old")
            cfg_mgr = ConfigManager(config_path, root_dir=root)
            cfg_mgr.load_or_create_default()

            with mock.patch(
                "reorder_engine.infrastructure.sevenzip_bootstrap.find_locked_seven_zip",
                return_value=root / "tools/7zip/26.04/7z.exe",
            ), mock.patch(
                "reorder_engine.infrastructure.tool_bootstrap.stage_locked_cli_tools",
                side_effect=RuntimeError("vendored archive hash mismatch"),
            ), mock.patch("shutil.which") as which:
                summary = ExtractToolBootstrapper().ensure_all(cfg_mgr.to_app_config(), cfg_mgr)

            for name in ("unrar", "bandizip"):
                result = summary.get(name)
                self.assertFalse(result.ok)
                self.assertIn("locked", result.message)
                self.assertIn("vendored archive hash mismatch", result.message)
            which.assert_not_called()

    def test_valid_explicit_tool_paths_keep_precedence_over_repo_locks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = self._write_config(root)
            lock_dir = root / "scripts"
            lock_dir.mkdir()
            (lock_dir / "desktop-sevenzip.lock.json").write_text("{}", encoding="utf-8")
            (lock_dir / "desktop-tools.lock.json").write_text("{}", encoding="utf-8")
            explicit = {}
            for name, rel in (
                ("seven_zip", "custom/7z.exe"),
                ("unrar", "custom/UnRAR.exe"),
                ("bandizip", "custom/bz.exe"),
            ):
                path = root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"configured")
                explicit[name] = path
            data = json.loads(config_path.read_text(encoding="utf-8"))
            data["tools"]["seven_zip"]["exe"] = "custom/7z.exe"
            data["tools"]["unrar"]["exe"] = "custom/UnRAR.exe"
            data["tools"]["bandizip"]["exe"] = "custom/bz.exe"
            config_path.write_text(json.dumps(data), encoding="utf-8")
            cfg_mgr = ConfigManager(config_path, root_dir=root)
            cfg_mgr.load_or_create_default()

            with mock.patch(
                "reorder_engine.infrastructure.sevenzip_bootstrap.find_locked_seven_zip"
            ) as find_seven, mock.patch(
                "reorder_engine.infrastructure.tool_bootstrap.stage_locked_cli_tools"
            ) as stage_tools:
                summary = ExtractToolBootstrapper().ensure_all(cfg_mgr.to_app_config(), cfg_mgr)

            self.assertEqual(summary.get("7z").exe, explicit["seven_zip"])
            self.assertEqual(summary.get("unrar").exe, explicit["unrar"])
            self.assertEqual(summary.get("bandizip").exe, explicit["bandizip"])
            find_seven.assert_not_called()
            stage_tools.assert_not_called()

    def test_second_prepare_revalidates_configured_git_managed_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = self._write_config(root)
            lock_dir = root / "scripts"
            lock_dir.mkdir()
            (lock_dir / "desktop-sevenzip.lock.json").write_text("{}", encoding="utf-8")
            (lock_dir / "desktop-tools.lock.json").write_text("{}", encoding="utf-8")
            pinned_7z = root / "tools" / "7zip" / "26.04" / "7z.exe"
            pinned_7z.parent.mkdir(parents=True)
            pinned_7z.write_bytes(b"pinned")
            cfg_mgr = ConfigManager(config_path, root_dir=root)
            cfg_mgr.load_or_create_default()

            def stage_cli(repo: Path, destination: Path, seven_zip: Path | None) -> None:
                for rel in (Path("tools/unrar/UnRAR.exe"), Path("tools/bandizip/bz.exe")):
                    target = destination / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(b"fixed")

            stage_calls = 0

            def stage_twice(repo: Path, destination: Path, seven_zip: Path | None) -> None:
                nonlocal stage_calls
                stage_calls += 1
                if stage_calls == 1:
                    stage_cli(repo, destination, seven_zip)
                    return
                raise RuntimeError("vendored archive hash mismatch")

            with mock.patch(
                "reorder_engine.infrastructure.sevenzip_bootstrap.locked_seven_zip_path",
                return_value=pinned_7z,
            ), mock.patch(
                "reorder_engine.infrastructure.sevenzip_bootstrap.find_locked_seven_zip",
                side_effect=[pinned_7z, RuntimeError("locked 7z.dll sha256 mismatch")],
            ) as find_seven, mock.patch(
                "reorder_engine.infrastructure.tool_bootstrap.stage_locked_cli_tools",
                side_effect=stage_twice,
            ) as stage_tools:
                first = ExtractToolBootstrapper().ensure_all(cfg_mgr.to_app_config(), cfg_mgr)
                self.assertTrue(first.required_ok)

                second = ExtractToolBootstrapper().ensure_all(cfg_mgr.to_app_config(), cfg_mgr)

            self.assertEqual(find_seven.call_count, 2)
            self.assertEqual(stage_tools.call_count, 2)
            self.assertFalse(second.get("7z").ok)
            self.assertIn("7z.dll sha256 mismatch", second.get("7z").message)
            for name in ("unrar", "bandizip"):
                self.assertFalse(second.get(name).ok)
                self.assertTrue(second.get(name).required)
                self.assertIn("vendored archive hash mismatch", second.get(name).message)
            self.assertFalse(second.required_ok)


if __name__ == "__main__":
    unittest.main()
