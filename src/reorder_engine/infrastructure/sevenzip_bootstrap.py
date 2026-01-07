from __future__ import annotations

import re
import subprocess
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from reorder_engine.services.config import AppConfig, ConfigManager


@dataclass(frozen=True)
class SevenZipEnsureResult:
    ok: bool
    exe: Path | None
    message: str


class SevenZipBootstrapper:
    """确保 7-Zip 可用：优先使用配置/本地 tools/，否则联网下载并本地解包。"""

    def ensure(self, cfg: AppConfig, cfg_mgr: ConfigManager) -> SevenZipEnsureResult:
        # 1) config 指定
        if cfg.tools.seven_zip.exe and cfg.tools.seven_zip.exe.exists():
            return SevenZipEnsureResult(True, cfg.tools.seven_zip.exe, "7z found from config")

        # 2) tools/ 默认位置
        tools_guess = cfg.root_dir / "tools" / "7zip" / "7z.exe"
        if tools_guess.exists():
            cfg_mgr.set_seven_zip_exe(tools_guess)
            cfg_mgr.save()
            return SevenZipEnsureResult(True, tools_guess, "7z found from tools/7zip")

        # 3) PATH
        import shutil

        found = shutil.which("7z") or shutil.which("7z.exe")
        if found:
            exe = Path(found)
            return SevenZipEnsureResult(True, exe, "7z found from PATH")

        # 4) download
        if not cfg.tools.seven_zip.auto_download:
            return SevenZipEnsureResult(False, None, "7z not found and auto_download disabled")

        try:
            exe = self._download_and_extract(cfg)
        except Exception as e:  # noqa: BLE001
            return SevenZipEnsureResult(False, None, f"7z auto download failed: {e}")

        if exe and exe.exists():
            cfg_mgr.set_seven_zip_exe(exe)
            cfg_mgr.save()
            return SevenZipEnsureResult(True, exe, "7z downloaded and configured")

        return SevenZipEnsureResult(False, None, "7z download finished but exe not found")

    def _download_and_extract(self, cfg: AppConfig) -> Path | None:
        install_dir = cfg.tools.seven_zip.install_dir
        install_dir.mkdir(parents=True, exist_ok=True)
        download_dir = cfg.root_dir / "tools" / "_downloads"
        download_dir.mkdir(parents=True, exist_ok=True)

        msi_url = self._resolve_latest_msi_url(timeout_sec=cfg.tools.seven_zip.download.timeout_sec)
        msi_name = msi_url.rsplit("/", 1)[-1]
        msi_path = download_dir / msi_name

        if not msi_path.exists():
            self._download(msi_url, msi_path, timeout_sec=cfg.tools.seven_zip.download.timeout_sec)

        # 使用 msiexec 做“管理安装”到本地目录，不需要管理员权限
        target_dir = install_dir
        self._msi_extract(msi_path, target_dir)

        # 在解包目录里找 7z.exe
        candidates = list(target_dir.rglob("7z.exe"))
        if not candidates:
            return None
        # 优先短路径（更像实际程序目录）
        candidates.sort(key=lambda p: (len(str(p)), str(p).lower()))
        return candidates[0]

    def _resolve_latest_msi_url(self, *, timeout_sec: int) -> str:
        url = "https://www.7-zip.org/download.html"
        html = self._download_text(url, timeout_sec=timeout_sec)

        # 页面结构会变化：尽量收集全部 .msi，然后选择最合适的一个。
        hrefs = re.findall(r"href=['\"](?P<href>[^'\"]+)['\"]", html, flags=re.IGNORECASE)

        def to_abs(h: str) -> str:
            if h.startswith("http"):
                return h
            return "https://www.7-zip.org/" + h.lstrip("/")

        def version_key(h: str) -> int:
            # 常见文件名：7z2501-x64.msi / 7z1900-x64.msi
            m = re.search(r"7z(?P<v>\d{4})-x64\.(msi|exe)$", h, flags=re.IGNORECASE)
            if m:
                return int(m.group("v"))
            # 兜底：提取最大数字串
            nums = re.findall(r"\d+", h)
            return int(max(nums, key=len)) if nums else 0

        msi = [h for h in hrefs if h.lower().endswith(".msi")]
        if msi:
            # 优先 x64（排除 arm64），再按版本号从大到小
            msi.sort(
                key=lambda h: (
                    0 if ("x64" in h.lower() and "arm64" not in h.lower()) else 1,
                    -version_key(h),
                    len(h),
                    h.lower(),
                )
            )
            return to_abs(msi[0])

        # 某些版本下载页可能不再提供 msi（或隐藏在脚本里），尝试回退到 exe。
        exe = [h for h in hrefs if h.lower().endswith(".exe")]
        if exe:
            exe.sort(
                key=lambda h: (
                    0 if ("x64" in h.lower() and "arm64" not in h.lower()) else 1,
                    -version_key(h),
                    len(h),
                    h.lower(),
                )
            )
            raise RuntimeError(
                "Cannot find 7-Zip msi link on download page; found exe link instead: " + to_abs(exe[0])
            )

        raise RuntimeError("Cannot find 7-Zip download link (.msi/.exe) on download page")

    def _download_text(self, url: str, *, timeout_sec: int) -> str:
        req = urllib.request.Request(url, headers={"User-Agent": "reorder-engine/0.1"})
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            data = resp.read()
        return data.decode("utf-8", errors="ignore")

    def _download(self, url: str, dest: Path, *, timeout_sec: int) -> None:
        req = urllib.request.Request(url, headers={"User-Agent": "reorder-engine/0.1"})
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            dest.write_bytes(resp.read())

    def _msi_extract(self, msi_path: Path, target_dir: Path) -> None:
        # msiexec /a <msi> TARGETDIR=<dir> /qn
        target_dir = target_dir.resolve()
        target_dir.mkdir(parents=True, exist_ok=True)

        # Windows Installer 对 ADMIN install 的 TARGETDIR 比较挑剔：
        # - 必须是绝对路径
        # - 通常要求目录路径以 "\" 结尾
        target_dir_prop = str(target_dir) + "\\"

        log_path = (target_dir.parent / "_downloads" / "7zip-msiexec.log").resolve()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        args = [
            "msiexec",
            "/a",
            str(msi_path.resolve()),
            f"TARGETDIR={target_dir_prop}",
            "/qn",
            "/l*v",
            str(log_path),
        ]
        proc = subprocess.run(args, capture_output=True, text=True, shell=False)
        if proc.returncode != 0:
            details = (proc.stderr.strip() or proc.stdout.strip() or "<no output>")
            raise RuntimeError(
                f"msiexec failed rc={proc.returncode}: {details}. log={log_path}"
            )
