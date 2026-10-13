"""Synthetic native-tool acceptance, isolated work folder and public password file.

HOSHI-033-NATIVE round adds, on top of the frozen 0.3.2 five-group set: a
single-volume Apate reveal that overlaps the restoreAB scanner, an ordinary ZIP
whose only disguise is a media suffix (direct read, untouched bytes, no derived
recovery copy), a CRC-damaged ZIP that publishes a partial result and stops the
tool/password/deep-dig matrix, and a wrong-then-correct password walk over AES.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import queue
import re
import subprocess
import struct
import tempfile
import threading
import time
import zipfile
from pathlib import Path

import pyzipper

NO_WINDOW = 0x08000000
PUBLIC_PASSWORD = "smoke-public-password"


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_zip_bytes(members: dict[str, bytes], *, password: str | None = None,
                    compression: int = zipfile.ZIP_DEFLATED) -> bytes:
    """Build an in-memory ZIP; AES when a password is given, plain otherwise."""
    buffer = io.BytesIO()
    if password is None:
        with zipfile.ZipFile(buffer, "w", compression) as handle:
            for name, content in members.items():
                handle.writestr(name, content)
    else:
        with pyzipper.AESZipFile(buffer, "w", compression=pyzipper.ZIP_DEFLATED,
                                 encryption=pyzipper.WZ_AES) as handle:
            handle.setpassword(password.encode())
            for name, content in members.items():
                handle.writestr(name, content)
    return buffer.getvalue()


def apate_disguise(data: bytes, mask: bytes) -> bytes:
    """Reproduce tools/apate.py: mask + body + reversed-head + LE mask length."""
    length = len(mask)
    if len(data) <= length:
        raise ValueError("payload must be longer than the disguise mask")
    return mask + data[length:] + data[:length][::-1] + struct.pack("<I", length)


def run_bundled_tool(argv: list[str]) -> str | None:
    try:
        completed = subprocess.run(argv, input=b"", stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, timeout=20, creationflags=NO_WINDOW if os.name == "nt" else 0)
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.decode("utf-8", errors="replace")


def parse_version(text: str | None, patterns: tuple[str, ...]) -> str | None:
    if not text:
        return None
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    app_root = args.engine.resolve().parent
    tool = next((app_root / "tools").rglob("7z.exe")) if os.name == "nt" else next((app_root / "tools").rglob("7zz"))
    seven_zip_text = run_bundled_tool([str(tool)])
    seven_zip_version = parse_version(seven_zip_text, (r"7-?Zip[^\d]{0,12}(\d+\.\d+)",))
    assert seven_zip_version == "26.04", (
        f"bundled 7-Zip must be 26.04, got {seven_zip_version!r}: {(seven_zip_text or '')[:200]}")
    evidence = {"engine": str(args.engine), "scope": "synthetic ZIP, AES ZIP, restoreAB cover, 7z volumes, Apate disguised ZIP volumes, unsafe member, public password file, remembered settings, chosen work folder, single-volume Apate reveal, media-suffix direct read, CRC-damaged partial output, wrong-then-correct password walk, 7-Zip 26.04", "seven_zip_version": seven_zip_version, "checks": []}
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="reorder-desktop-smoke-", dir=args.evidence.parent) as raw:
        root = Path(raw)
        inputs = root / "归序 samples"
        inputs.mkdir()
        payload = b"synthetic desktop payload\n" * 100
        plain = inputs / "ordinary.zip"
        with zipfile.ZipFile(plain, "w", zipfile.ZIP_DEFLATED) as handle:
            handle.writestr("资料/payload.txt", payload)
        cover = inputs / "merged.pdf"
        cover.write_bytes(b"%PDF-1.7\nSynthetic cover\n%%EOF\n" + plain.read_bytes())
        encrypted = inputs / "encrypted.zip"
        with pyzipper.AESZipFile(encrypted, "w", compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES) as handle:
            handle.setpassword(b"smoke-public-password")
            handle.writestr("payload.txt", payload)
        volume_payload = root / "volume-payload.bin"
        volume_bytes = os.urandom(10000)
        volume_payload.write_bytes(volume_bytes)
        subprocess.run([str(tool), "a", "-t7z", "-mx0", "-v2k", str(inputs / "split.7z"), str(volume_payload)],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        # Exercise the repaired multi-volume reveal in the actual frozen engine.
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as handle:
            handle.writestr("disguised-payload.txt", payload)
            handle.writestr("disguised-more.txt", payload)
        zipped = buffer.getvalue()
        mask = b"\x00\x00\x00\x18ftypmp42mp41" + b"\x00" * 52
        pieces = (zipped[:len(zipped) // 2], zipped[len(zipped) // 2:])
        for index, piece in enumerate(pieces, 1):
            assert len(piece) > len(mask)
            hidden = mask + piece[len(mask):] + piece[:len(mask)][::-1] + struct.pack("<I", len(mask))
            (inputs / f"disguised.zip.{index:03}.mp4").write_bytes(hidden)
        originals = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs.iterdir()}
        output = root / "results"
        data = root / "state"
        command = [str(args.engine.resolve()), "--app-root", str(app_root), "--data-root", str(data)]
        diagnostics = (root / "diagnostics.log").open("wb")
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=diagnostics,
            creationflags=0x08000000 if os.name == "nt" else 0)
        frames: queue.Queue[bytes] = queue.Queue()
        def read_frames() -> None:
            assert process.stdout
            for line in process.stdout:
                frames.put(line)
        thread = threading.Thread(target=read_frames, daemon=True)
        thread.start()
        sequence = 0
        def request(method: str, params: dict | None = None):
            nonlocal sequence
            sequence += 1
            assert process.stdin
            frame = {"jsonrpc": "2.0", "id": sequence, "method": method, "params": params or {}}
            process.stdin.write(json.dumps(frame, ensure_ascii=False).encode() + b"\n")
            process.stdin.flush()
            try:
                reply = json.loads(frames.get(timeout=30))
            except queue.Empty as exc:
                raise AssertionError("native engine did not reply; " + (root / "diagnostics.log").read_text(errors="replace")[-1500:]) from exc
            assert reply["id"] == sequence and "error" not in reply, reply
            return reply["result"]
        def finish(job_id: str):
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                job = request("jobs.get", {"job_id": job_id})
                if job["state"] not in {"queued", "running", "cancelling"}:
                    return job
                time.sleep(.1)
            raise AssertionError("native job did not finish")
        try:
            info = request("system.info")
            assert info["tools"]["seven_zip"] and info["protocol_version"] == 1
            assert info["passwords"]["storage"] == "plaintext"
            assert info["passwords"]["count"] == 115
            assert Path(info["passwords"]["path"]) == data / "passwords.txt"
            assert len(info["passwords"]["values"]) == 115
            evidence["checks"].append({"system_info": "passed", "platform": info["platform"]})
            tools_info = info["tools"]
            evidence["tools"] = {"seven_zip_version": seven_zip_version,
                                 "seven_zip": str(tools_info.get("seven_zip") or tool),
                                 "unrar": tools_info.get("unrar"), "bandizip": tools_info.get("bandizip")}
            locked_tools = app_root / "tools" / "fixed-tools.json"
            if locked_tools.is_file():
                record = json.loads(locked_tools.read_text(encoding="utf-8"))
                evidence["tools"]["bundled_versions"] = {item["id"]: item["version"] for item in record.get("tools", [])}
            request("passwords.replace", {"passwords": ["smoke-public-password"]})
            assert (data / "passwords.txt").read_text(encoding="utf-8") == "smoke-public-password\n"
            output.mkdir(parents=True)
            sentinel = output / "final" / "资料" / "existing.txt"
            sentinel.parent.mkdir(parents=True)
            sentinel.write_bytes(b"preserve-existing-content")
            saved = dict(info["settings"], work_root=str(output))
            saved["options"] = dict(saved["options"], keep_workspace=True)
            request("settings.update", saved)
            preview = request("plans.create", {"input_paths": [str(inputs)], "output_root": str(output)})
            assert len(preview["packages"]) == 5, preview
            job = request("jobs.start", {"plan_id": preview["plan_id"], "idempotency_key": "native-smoke"})
            final = finish(job["job_id"])
            assert final["state"] == "succeeded", final
            archived = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (output / "success/archives").rglob("*") if p.is_file()}
            # Legacy direct restoration archives the repaired disguise, rather
            # than an untouched staging copy. Ordinary archive bytes stay intact.
            ordinary = {name: digest for name, digest in originals.items()
                        if not name.startswith("disguised.")}
            assert all(archived.get(name) == digest for name, digest in ordinary.items()), archived
            assert len(archived) == len(originals), archived
            for index, piece in enumerate(pieces, 1):
                candidates = [name for name in archived
                              if name in {f"disguised.zip.{index:03}", f"disguised.zip.{index:03}.mp4"}]
                assert len(candidates) == 1, archived
                assert archived[candidates[0]] == hashlib.sha256(piece).hexdigest()
            staged_names = {p.name for p in (output / "intermediate/workspaces").rglob("*") if p.is_file()}
            assert not (staged_names & set(originals)), staged_names
            assert sentinel.read_bytes() == b"preserve-existing-content"
            assert not (data / "work").exists()
            assert (output / "intermediate/workspaces").is_dir()
            assert not list(output.rglob("*_restoreAB*")), "a directly readable cover needs no recovery copy"
            assert list((output / "intermediate/workspaces").rglob("tmp")), "tool TEMP/TMP/cwd stayed inside the workspaces"
            assert request("settings.get")["settings"]["work_root"] == str(output)
            assert not list(inputs.iterdir())
            payloads = list((output / "final").rglob("payload.txt"))
            assert len(payloads) == 3 and all(p.read_bytes() == payload for p in payloads)
            volume_outputs = list((output / "final").rglob("volume-payload.bin"))
            assert len(volume_outputs) == 1 and volume_outputs[0].read_bytes() == volume_bytes
            disguised_outputs = list((output / "final").rglob("disguised-payload.txt"))
            assert len(disguised_outputs) == 1 and disguised_outputs[0].read_bytes() == payload
            assert len(list((output / "final").rglob("disguised-more.txt"))) == 1
            registered = request("results.get", {"job_id": job["job_id"]})
            assert registered["output_root"] == str(output)
            assert not any(Path(p).is_relative_to(output / "intermediate/workspaces") for p in registered["paths"])
            evidence["checks"].append({"native_formats_and_direct_archives": "passed", "groups": 5,
                "archive_members": len(originals), "ordinary_hashes": "unchanged",
                "apate_disguised_volumes": "repaired_bytes_archived", "no_archive_staging": "passed"})
            unsafe = inputs / "unsafe.zip"
            with zipfile.ZipFile(unsafe, "w") as handle:
                handle.writestr("../outside.txt", "must not be published")
            unsafe_bytes = unsafe.read_bytes()
            preview = request("plans.create", {"input_paths": [str(unsafe)], "output_root": str(output)})
            job = request("jobs.start", {"plan_id": preview["plan_id"], "idempotency_key": "unsafe-smoke"})
            final = finish(job["job_id"])
            assert final["state"] == "failed", final
            assert final["packages"][0]["state"] == "failed", final
            assert final["packages"][0]["error_code"] == "UNSAFE_ARCHIVE"
            assert unsafe.read_bytes() == unsafe_bytes
            assert not (root / "outside.txt").exists()
            assert (data / "passwords.txt").read_text(encoding="utf-8") == "smoke-public-password\n"
            assert request("settings.get")["passwords"]["values"] == ["smoke-public-password"]
            evidence["checks"].append({"unsafe_path_and_plaintext_passwords": "passed", "chosen_work_root_and_existing_contents": "passed"})
            # --- HOSHI-033-NATIVE round: single-volume Apate reveal, media-suffix
            # --- direct read, CRC-damaged partial output, password continuation.
            round2 = root / "h033 samples"
            round2.mkdir()
            apate_payload = b"apate single-volume payload\n" * 40
            apate_zip = build_zip_bytes({"apate-payload.txt": apate_payload})
            apate_mask = b"\x00\x00\x00\x18ftypmp42mp41" + b"\x00" * 52
            apate_file = round2 / "apate-single.mp4"
            apate_file.write_bytes(apate_disguise(apate_zip, apate_mask))
            media_payload = b"media-suffix direct payload\n" * 40
            media_zip = build_zip_bytes({"media-payload.txt": media_payload})
            media_file = round2 / "media-only.mp4"
            media_file.write_bytes(media_zip)
            crc_good = b"crc good member\n" * 40
            crc_bad = b"0123456789ABCDEF" * 64
            crc_buffer = io.BytesIO()
            with zipfile.ZipFile(crc_buffer, "w", zipfile.ZIP_STORED) as handle:
                handle.writestr("crc-good.txt", crc_good)
                handle.writestr("crc-bad.bin", crc_bad)
            crc_raw = crc_buffer.getvalue()
            assert crc_raw.count(crc_bad) == 1
            crc_bytes = crc_raw.replace(crc_bad, b"X" + crc_bad[1:], 1)
            crc_file = round2 / "crc.zip"
            crc_file.write_bytes(crc_bytes)
            guarded_payload = b"guarded aes payload\n" * 40
            guarded_zip = build_zip_bytes({"guard-payload.txt": guarded_payload}, password=PUBLIC_PASSWORD)
            guard_wrong = round2 / "guard-wrong.zip"
            guard_ok = round2 / "guard-ok.zip"
            guard_wrong.write_bytes(guarded_zip)
            guard_ok.write_bytes(guarded_zip)

            def archived_now() -> dict:
                return {p.name: _digest(p.read_bytes())
                        for p in (output / "success" / "archives").rglob("*") if p.is_file()}

            final_dir = output / "final"

            # Job A: single-volume Apate reveal, and a plain ZIP disguised only by
            # a media suffix read directly without rewriting its bytes.
            request("passwords.replace", {"passwords": [PUBLIC_PASSWORD]})
            preview = request("plans.create", {"input_paths": [str(apate_file), str(media_file)], "output_root": str(output)})
            assert len(preview["packages"]) == 2, preview
            job = request("jobs.start", {"plan_id": preview["plan_id"], "idempotency_key": "h033-apate-media"})
            finished = finish(job["job_id"])
            states = {p["name"]: p["state"] for p in finished["packages"]}
            assert states == {"apate-single.mp4": "succeeded", "media-only.mp4": "succeeded"}, states
            assert [p.read_bytes() for p in final_dir.rglob("apate-payload.txt")] == [apate_payload]
            assert [p.read_bytes() for p in final_dir.rglob("media-payload.txt")] == [media_payload]
            archived = archived_now()
            # An in-place Apate reveal reconstructs the true ZIP bytes at the same
            # path; the media-suffix ZIP is read directly and left untouched.
            assert archived.get("apate-single.mp4") == _digest(apate_zip), archived
            # "Only a media suffix" must not trigger a restore/derivative pass:
            # the archived original keeps the exact ZIP bytes it was read from.
            assert _digest(media_zip) in archived.values(), archived
            assert not list(output.rglob("*_restoreAB*")), "no whole-package recovery copy may be derived"
            apate_log = request("jobs.logs", {"job_id": job["job_id"], "limit": 500})["lines"]
            assert any("kind=apate" in line for line in apate_log), apate_log
            assert not any("_restoreAB" in line for line in apate_log), apate_log
            evidence["checks"].append({"single_volume_apate_reveal": "passed", "media_suffix_direct_read": "passed", "no_restoreab_derivative": "passed"})

            # Job B: a CRC-damaged member leaves usable files; the package becomes
            # partial, publishes final, and stops tool/password/deep-dig retries.
            preview = request("plans.create", {"input_paths": [str(crc_file)], "output_root": str(output)})
            job = request("jobs.start", {"plan_id": preview["plan_id"], "idempotency_key": "h033-crc"})
            finished = finish(job["job_id"])
            package = finished["packages"][0]
            assert finished["state"] == "partial" and package["state"] == "partial", finished
            assert "警告" in package["message"], package
            assert [p.read_bytes() for p in final_dir.rglob("crc-good.txt")] == [crc_good]
            assert archived_now().get("crc.zip") == _digest(crc_bytes)
            crc_log = request("jobs.logs", {"job_id": job["job_id"], "limit": 500})["lines"]
            assert any("FINAL" in line for line in crc_log), crc_log
            assert not any("DEEP-EXTRACT" in line for line in crc_log), crc_log
            assert sum("EXTRACT-TRY:" in line for line in crc_log) == 1, crc_log
            assert not any(("RENAME-TRY" in line) or ("RESTORE-TRY" in line) for line in crc_log), crc_log
            assert all("preferred_password=<none>" in line for line in crc_log if "EXTRACT-TRY:" in line), crc_log
            evidence["checks"].append({"crc_damage_partial_publish": "passed", "partial_stops_tool_password_deep_dig": "passed"})

            # Job C: a wrong password alone writes no non-empty file, so it is a
            # real failure, never a partial result.
            request("passwords.replace", {"passwords": ["wrong"]})
            preview = request("plans.create", {"input_paths": [str(guard_wrong)], "output_root": str(output)})
            job = request("jobs.start", {"plan_id": preview["plan_id"], "idempotency_key": "h033-wrong-only"})
            finished = finish(job["job_id"])
            package = finished["packages"][0]
            assert package["state"] == "failed", finished
            assert not list(final_dir.rglob("guard-payload.txt"))
            assert list((output / "error_files" / "password_error").rglob("guard-wrong.zip"))
            evidence["checks"].append({"wrong_password_zero_output_is_failure_not_partial": "passed"})

            # Job D: a wrong password first must not stop the matrix; the correct
            # AES password still succeeds.
            request("passwords.replace", {"passwords": ["wrong", PUBLIC_PASSWORD]})
            preview = request("plans.create", {"input_paths": [str(guard_ok)], "output_root": str(output)})
            job = request("jobs.start", {"plan_id": preview["plan_id"], "idempotency_key": "h033-wrong-then-ok"})
            finished = finish(job["job_id"])
            package = finished["packages"][0]
            assert package["state"] == "succeeded", finished
            assert [p.read_bytes() for p in final_dir.rglob("guard-payload.txt")] == [guarded_payload]
            assert archived_now().get("guard-ok.zip") == _digest(guarded_zip)
            assert not list(output.rglob("*_restoreAB*")), "no whole-package recovery copy may be derived"
            evidence["checks"].append({"wrong_then_correct_password_continues": "passed"})

            assert sentinel.read_bytes() == b"preserve-existing-content"
        finally:
            if process.stdin:
                process.stdin.close()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            diagnostics.close()
        assert process.returncode == 0
        # Restart the frozen engine against the same state, proving disk persistence.
        restarted = subprocess.run(command, input=(json.dumps({"jsonrpc": "2.0", "id": 1,
            "method": "system.info", "params": {}}) + "\n").encode(),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
            creationflags=0x08000000 if os.name == "nt" else 0)
        assert restarted.returncode == 0
        reply = json.loads(restarted.stdout.splitlines()[0])["result"]
        assert reply["settings"]["work_root"] == str(output)
        assert reply["settings"]["options"]["keep_workspace"] is True
        assert reply["passwords"]["values"] == ["wrong", "smoke-public-password"]
        evidence["checks"].append({"settings_and_library_restart": "passed"})
    evidence["exit_code"] = 0
    evidence["not_tested"] = [
        "real RAR/RAR5 fixtures and -kb behaviour (no synthetic RAR)",
        "true cross-drive source retention on a real second drive (headless pytest simulations only)",
        "real user files, GUI acceptance and large-disk I/O",
    ]
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(evidence, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
