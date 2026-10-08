"""Synthetic native-tool acceptance, isolated work folder and public password file."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import queue
import subprocess
import struct
import tempfile
import threading
import time
import zipfile
from pathlib import Path

import pyzipper


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    app_root = args.engine.resolve().parent
    tool = next((app_root / "tools").rglob("7z.exe")) if os.name == "nt" else next((app_root / "tools").rglob("7zz"))
    evidence = {"engine": str(args.engine), "scope": "synthetic ZIP, AES ZIP, restoreAB cover, 7z volumes, Apate disguised ZIP volumes, unsafe member, public password file, remembered settings, chosen work folder", "checks": []}
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
            assert archived == originals
            assert sentinel.read_bytes() == b"preserve-existing-content"
            assert not (data / "work").exists()
            assert (output / "intermediate/workspaces").is_dir()
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
            evidence["checks"].append({"native_formats_and_original_hashes": "passed", "groups": 5, "archive_members": len(originals), "apate_disguised_volumes": "passed"})
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
        assert reply["passwords"]["values"] == ["smoke-public-password"]
        evidence["checks"].append({"settings_and_library_restart": "passed"})
    evidence["exit_code"] = 0
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(evidence, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
