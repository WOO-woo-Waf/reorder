"""Synthetic native-tool acceptance, isolated data and session-only test passwords."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import subprocess
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
    evidence = {"engine": str(args.engine), "scope": "synthetic ZIP, AES ZIP, restoreAB cover, 7z volumes, unsafe member, secret redaction", "checks": []}
    with tempfile.TemporaryDirectory(prefix="reorder-desktop-smoke-") as raw:
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
            handle.setpassword(b"smoke-session-password")
            handle.writestr("payload.txt", payload)
        volume_payload = root / "volume-payload.bin"
        volume_bytes = os.urandom(10000)
        volume_payload.write_bytes(volume_bytes)
        subprocess.run([str(tool), "a", "-t7z", "-mx0", "-v2k", str(inputs / "split.7z"), str(volume_payload)],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        originals = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs.iterdir()}
        output = root / "results"
        data = root / "state"
        command = [str(args.engine.resolve()), "--app-root", str(app_root), "--data-root", str(data), "--session-secrets"]
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
            assert info["passwords"]["storage"] == "session"
            evidence["checks"].append({"system_info": "passed", "platform": info["platform"]})
            request("passwords.replace", {"passwords": ["smoke-session-password"]})
            preview = request("plans.create", {"input_paths": [str(inputs)], "output_root": str(output)})
            assert len(preview["packages"]) == 4, preview
            job = request("jobs.start", {"plan_id": preview["plan_id"], "idempotency_key": "native-smoke"})
            final = finish(job["job_id"])
            assert final["state"] == "succeeded", final
            archived = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (output / "success/archives").rglob("*") if p.is_file()}
            assert archived == originals
            assert not list(inputs.iterdir())
            payloads = list((output / "final").rglob("payload.txt"))
            assert len(payloads) == 3 and all(p.read_bytes() == payload for p in payloads)
            volume_outputs = list((output / "final").rglob("volume-payload.bin"))
            assert len(volume_outputs) == 1 and volume_outputs[0].read_bytes() == volume_bytes
            registered = request("results.get", {"job_id": job["job_id"]})
            assert registered["output_root"] == str(output)
            evidence["checks"].append({"native_formats_and_original_hashes": "passed", "groups": 4, "archive_members": len(originals)})
            unsafe = inputs / "unsafe.zip"
            with zipfile.ZipFile(unsafe, "w") as handle:
                handle.writestr("../outside.txt", "must not be published")
            unsafe_bytes = unsafe.read_bytes()
            preview = request("plans.create", {"input_paths": [str(unsafe)], "output_root": str(output)})
            job = request("jobs.start", {"plan_id": preview["plan_id"], "idempotency_key": "unsafe-smoke"})
            final = finish(job["job_id"])
            assert final["packages"][0]["error_code"] == "UNSAFE_ARCHIVE"
            assert unsafe.read_bytes() == unsafe_bytes
            assert not (root / "outside.txt").exists()
            assert not (data / "passwords.txt").exists()
            assert "smoke-session-password" not in "".join(p.read_text(errors="replace") for p in (data / "logs").glob("*.log"))
            evidence["checks"].append({"unsafe_path_and_secret_redaction": "passed"})
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
    evidence["exit_code"] = 0
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(evidence, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
