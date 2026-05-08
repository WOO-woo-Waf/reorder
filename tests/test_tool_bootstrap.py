from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from reorder_engine.infrastructure.tool_bootstrap import ExtractToolBootstrapper
from reorder_engine.services.config import ConfigManager


class ToolBootstrapTests(unittest.TestCase):
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

            data = json.loads((root / "config.json").read_text(encoding="utf-8"))
            self.assertEqual(data["tools"]["seven_zip"]["exe"], r"tools\7zip\Files\7-Zip\7z.exe")
            self.assertEqual(data["tools"]["unrar"]["exe"], r"tools\rar\UnRAR.exe")


if __name__ == "__main__":
    unittest.main()
