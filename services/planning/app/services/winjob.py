# Copyright (c) 2026 Qnit. All rights reserved.
# SPDX-License-Identifier: LicenseRef-Proprietary

"""Run a worker subprocess under a Windows Job Object with a hard memory cap.

The job's ProcessMemoryLimit kills the worker if its committed memory goes over the cap, and
PeakProcessMemoryUsed gives the peak afterwards. On other platforms the worker runs without
a cap and the peak is read from resource.getrusage (ru_maxrss of children).
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from ctypes import wintypes

IS_WIN = sys.platform == "win32"


class _IO(ctypes.Structure):
    _fields_ = [
        (n, ctypes.c_ulonglong)
        for n in (
            "ReadOperationCount",
            "WriteOperationCount",
            "OtherOperationCount",
            "ReadTransferCount",
            "WriteTransferCount",
            "OtherTransferCount",
        )
    ]


class _BASIC(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _EXT(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BASIC),
        ("IoInfo", _IO),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


_LIMIT_PROCESS_MEMORY = 0x100
_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_EXT_INFO = 9


class CappedProcess:
    """subprocess.Popen in a job with a per-process memory cap (bytes)."""

    def __init__(self, args, cap_bytes: int, **popen_kw):
        self.cap = cap_bytes
        self.proc = subprocess.Popen(args, **popen_kw)
        self._job = None
        if IS_WIN:
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.CreateJobObjectW.restype = wintypes.HANDLE
            k32.SetInformationJobObject.argtypes = [
                wintypes.HANDLE,
                ctypes.c_int,
                ctypes.c_void_p,
                wintypes.DWORD,
            ]
            k32.QueryInformationJobObject.argtypes = [
                wintypes.HANDLE,
                ctypes.c_int,
                ctypes.c_void_p,
                wintypes.DWORD,
                ctypes.c_void_p,
            ]
            k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
            k32.CloseHandle.argtypes = [wintypes.HANDLE]
            self._k32 = k32
            job = k32.CreateJobObjectW(None, None)
            info = _EXT()
            info.BasicLimitInformation.LimitFlags = (
                _LIMIT_PROCESS_MEMORY | _LIMIT_KILL_ON_JOB_CLOSE
            )
            info.ProcessMemoryLimit = cap_bytes
            k32.SetInformationJobObject(
                job, _EXT_INFO, ctypes.byref(info), ctypes.sizeof(info)
            )
            k32.OpenProcess.restype = wintypes.HANDLE
            h = k32.OpenProcess(0x1F0FFF, False, self.proc.pid)
            k32.AssignProcessToJobObject(job, h)
            k32.CloseHandle(h)
            self._job = job

    def peak_bytes(self) -> int:
        if self._job is None:
            try:
                import resource

                return resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss * 1024
            except Exception:  # noqa: BLE001
                return 0
        info = _EXT()
        self._k32.QueryInformationJobObject(
            self._job, _EXT_INFO, ctypes.byref(info), ctypes.sizeof(info), None
        )
        return int(info.PeakProcessMemoryUsed)

    def close(self) -> None:
        if self._job is not None:
            self._k32.CloseHandle(self._job)
            self._job = None


def process_peak_bytes(pid: int | None = None) -> tuple[int, int]:
    """(current, peak) private bytes of a process (default: this one)."""
    if not IS_WIN:
        try:
            import resource

            return 0, resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
        except Exception:  # noqa: BLE001
            return 0, 0

    class PMC(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
            ("PrivateUsage", ctypes.c_size_t),
        ]

    psapi = ctypes.WinDLL("psapi")
    k32 = ctypes.WinDLL("kernel32")
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(PMC),
        wintypes.DWORD,
    ]
    h = k32.GetCurrentProcess() if pid is None else k32.OpenProcess(0x0410, False, pid)
    c = PMC()
    c.cb = ctypes.sizeof(c)
    psapi.GetProcessMemoryInfo(h, ctypes.byref(c), c.cb)
    if pid is not None:
        k32.CloseHandle(h)
    return int(c.PrivateUsage), int(c.PeakPagefileUsage)


def run_capped(args, stdin_bytes: bytes, cap_bytes: int, env=None, cwd=None):
    """Run to completion; returns (returncode, stdout, stderr, peak_bytes)."""
    p = CappedProcess(
        args,
        cap_bytes,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env or os.environ.copy(),
        cwd=cwd,
    )
    out, err = p.proc.communicate(stdin_bytes)
    peak = p.peak_bytes()
    p.close()
    return p.proc.returncode, out, err, peak
