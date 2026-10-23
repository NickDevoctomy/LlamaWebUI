"""Tie a process and everything it spawns to one lifetime.

llama-server in router mode starts a child llama-server per loaded model. On Windows
those children outlive the router when it exits or crashes, because nothing kills a
process tree whose root is gone. A job object set to kill on close fixes that: every
process the router starts joins its job, and closing the job ends them all.
"""

from __future__ import annotations

import logging
import sys

LOGGER = logging.getLogger("llamawebui.router")

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    _PROCESS_SET_QUOTA = 0x0100
    _PROCESS_TERMINATE = 0x0001

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            (name, ctypes.c_ulonglong)
            for name in (
                "ReadOperationCount",
                "WriteOperationCount",
                "OtherOperationCount",
                "ReadTransferCount",
                "WriteTransferCount",
                "OtherTransferCount",
            )
        ]

    class _BasicLimitInformation(ctypes.Structure):
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

    class _ExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimitInformation),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    def _kernel32() -> ctypes.WinDLL:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.OpenProcess.restype = wintypes.HANDLE
        return kernel32

    def _create_job(pid: int) -> int:
        kernel32 = _kernel32()
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = _ExtendedLimitInformation()
        limits.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        process = None
        try:
            if not kernel32.SetInformationJobObject(
                wintypes.HANDLE(job),
                _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(limits),
                ctypes.sizeof(limits),
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            process = kernel32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid)
            if not process:
                raise ctypes.WinError(ctypes.get_last_error())
            if not kernel32.AssignProcessToJobObject(
                wintypes.HANDLE(job), wintypes.HANDLE(process)
            ):
                raise ctypes.WinError(ctypes.get_last_error())
        except OSError:
            kernel32.CloseHandle(wintypes.HANDLE(job))
            raise
        finally:
            if process:
                kernel32.CloseHandle(wintypes.HANDLE(process))
        return int(job)

    def _close_job(handle: int) -> None:
        _kernel32().CloseHandle(wintypes.HANDLE(handle))

else:

    def _create_job(pid: int) -> int:
        raise OSError("job objects are only available on Windows")

    def _close_job(handle: int) -> None:
        return None


class ProcessJob:
    """Kill-on-close job for one process tree; inactive where job objects do not exist."""

    def __init__(self, pid: int) -> None:
        self._handle: int | None = None
        if sys.platform != "win32":
            return
        try:
            self._handle = _create_job(pid)
        except OSError as error:
            # Without a job the router still runs; only orphan cleanup is lost.
            LOGGER.warning(
                "router_job_unavailable",
                extra={"component": "router", "event": "job_unavailable", "error": str(error)},
            )

    @property
    def active(self) -> bool:
        return self._handle is not None

    def close(self) -> None:
        """End every process still in the job. Safe to call more than once."""
        handle, self._handle = self._handle, None
        if handle is not None:
            _close_job(handle)
