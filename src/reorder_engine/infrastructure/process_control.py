from __future__ import annotations

import os
import signal
import subprocess
import sys


class ProcessScope:
    """Windows Job Object owns tool descendants and closes on engine termination."""

    def __init__(self, process: subprocess.Popen):
        self.handle = None
        if sys.platform != "win32":
            return
        import ctypes
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [("ProcessTime", ctypes.c_int64), ("JobTime", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSet", ctypes.c_size_t),
                ("MaximumWorkingSet", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in ("ReadOps", "WriteOps", "OtherOps", "ReadBytes", "WriteBytes", "OtherBytes")]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [("Basic", BasicLimits), ("Io", IoCounters), ("ProcessMemory", ctypes.c_size_t),
                ("JobMemory", ctypes.c_size_t), ("PeakProcessMemory", ctypes.c_size_t), ("PeakJobMemory", ctypes.c_size_t)]

        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        api.CreateJobObjectW.restype = wintypes.HANDLE
        api.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = api.CreateJobObjectW(None, None)
        limits = ExtendedLimits()
        limits.Basic.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if (not handle or not api.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits))
                or not api.AssignProcessToJobObject(handle, wintypes.HANDLE(int(process._handle)))):
            if handle:
                api.CloseHandle(handle)
            # Retain taskkill /T fallback for hosts that disallow nested jobs.
            return
        self.handle = handle
        self._api = api

    def close(self) -> None:
        if self.handle:
            self._api.CloseHandle(self.handle)
            self.handle = None


def spawn_options() -> dict:
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
    return {"start_new_session": True}


def terminate_process_tree(process: subprocess.Popen) -> None:
    if sys.platform == "win32" and process.poll() is not None:
        return
    if sys.platform == "win32":
        # taskkill /T terminates children too; an arbitrary user-supplied PID
        # never enters this function.
        try:
            subprocess.run(
                ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW, shell=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
        if process.poll() is None:
            process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
