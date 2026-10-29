"""Ownership of task descendants, established before task code is released.

POSIX module processes share one private session/process group. External programs
inside that module inherit the group so the controller can also stop them if the
module itself is stuck. Standalone external launches create their own session.
Windows uses non-inheritable, kill-on-close Job Objects and suspended launches.
"""
from __future__ import annotations

import os
import signal
import subprocess
import time


_task_group: int | None = None


def enter_task_process(ready, proceed) -> None:
    """Child half of the runner's start gate; call before running any module."""
    global _task_group
    if os.name != "nt":
        os.setsid()
        _task_group = os.getpid()
    ready.set()
    proceed.wait()


if os.name == "nt":
    import ctypes
    import msvcrt
    from ctypes import wintypes

    _kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    _SIZE_T = ctypes.c_size_t

    class _BasicLimits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", _SIZE_T),
            ("MaximumWorkingSetSize", _SIZE_T), ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", _SIZE_T), ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
        )]

    class _ExtendedLimits(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", _BasicLimits), ("IoInfo", _IoCounters)] + [
            (name, _SIZE_T) for name in (
                "ProcessMemoryLimit", "JobMemoryLimit", "PeakProcessMemoryUsed", "PeakJobMemoryUsed",
            )
        ]

    class _Accounting(ctypes.Structure):
        _fields_ = [(name, ctypes.c_longlong) for name in (
            "TotalUserTime", "TotalKernelTime", "ThisPeriodTotalUserTime", "ThisPeriodTotalKernelTime",
        )] + [(name, wintypes.DWORD) for name in (
            "TotalPageFaultCount", "TotalProcesses", "ActiveProcesses", "TotalTerminatedProcesses",
        )]

    class _ThreadEntry(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ThreadID", wintypes.DWORD), ("th32OwnerProcessID", wintypes.DWORD),
                    ("tpBasePri", wintypes.LONG), ("tpDeltaPri", wintypes.LONG),
                    ("dwFlags", wintypes.DWORD)]

    def _api(name, result, arguments):
        function = getattr(_kernel, name)
        function.restype = result
        function.argtypes = arguments
        return function

    _create_job = _api("CreateJobObjectW", wintypes.HANDLE, [ctypes.c_void_p, wintypes.LPCWSTR])
    _set_job = _api("SetInformationJobObject", wintypes.BOOL,
                    [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD])
    _query_job = _api("QueryInformationJobObject", wintypes.BOOL,
                      [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p])
    _assign_job = _api("AssignProcessToJobObject", wintypes.BOOL, [wintypes.HANDLE, wintypes.HANDLE])
    _terminate_job = _api("TerminateJobObject", wintypes.BOOL, [wintypes.HANDLE, wintypes.UINT])
    _open_process = _api("OpenProcess", wintypes.HANDLE, [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD])
    _close_handle = _api("CloseHandle", wintypes.BOOL, [wintypes.HANDLE])
    _snapshot = _api("CreateToolhelp32Snapshot", wintypes.HANDLE, [wintypes.DWORD, wintypes.DWORD])
    _thread_first = _api("Thread32First", wintypes.BOOL, [wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)])
    _thread_next = _api("Thread32Next", wintypes.BOOL, [wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)])
    _open_thread = _api("OpenThread", wintypes.HANDLE, [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD])
    _resume_thread = _api("ResumeThread", wintypes.DWORD, [wintypes.HANDLE])
    _peek_pipe = _api("PeekNamedPipe", wintypes.BOOL, [
        wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
        ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
    ])

    def _resume_suspended_process(pid: int) -> None:
        snapshot = _snapshot(0x00000004, 0)  # TH32CS_SNAPTHREAD
        if snapshot == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            entry = _ThreadEntry()
            entry.dwSize = ctypes.sizeof(entry)
            found = _thread_first(snapshot, ctypes.byref(entry))
            thread_ids = []
            while found:
                if entry.th32OwnerProcessID == pid:
                    thread_ids.append(entry.th32ThreadID)
                found = _thread_next(snapshot, ctypes.byref(entry))
            if ctypes.get_last_error() != 18:  # ERROR_NO_MORE_FILES
                raise ctypes.WinError(ctypes.get_last_error())
            if len(thread_ids) != 1:
                raise RuntimeError("Suspended process must have exactly one initial thread")
            thread = _open_thread(0x0002, False, thread_ids[0])  # THREAD_SUSPEND_RESUME
            if not thread:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                if _resume_thread(thread) == 0xFFFFFFFF:
                    raise ctypes.WinError(ctypes.get_last_error())
            finally:
                _close_handle(thread)
        finally:
            _close_handle(snapshot)


class OwnedProcessTree:
    """One task's group/job. Always close before releasing its task reservation."""

    def __init__(self):
        self._group: int | None = None
        self._process: subprocess.Popen | None = None
        self._closed = False
        self._job = None
        if os.name == "nt":
            self._job = _create_job(None, None)
            if not self._job:
                raise ctypes.WinError(ctypes.get_last_error())
            limits = _ExtendedLimits()
            limits.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
            if not _set_job(self._job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                error = ctypes.WinError(ctypes.get_last_error())
                _close_handle(self._job)
                self._job = None
                raise error

    def attach(self, pid: int) -> None:
        """Attach a gated module child, or an already suspended external child."""
        if self._closed:
            raise RuntimeError("Process ownership is already closed")
        if os.name == "nt":
            handle = _open_process(0x0100 | 0x0001, False, pid)  # SET_QUOTA | TERMINATE
            if not handle:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                if not _assign_job(self._job, handle):
                    raise ctypes.WinError(ctypes.get_last_error())
            finally:
                _close_handle(handle)
        else:
            if pid == os.getpgrp() or os.getpgid(pid) != pid:
                raise RuntimeError("Refusing to own a process without its private process group")
            self._group = pid

    def popen(self, command, **kwargs) -> subprocess.Popen:
        """Launch before work can escape ownership; stdout, if any, is binary."""
        if self._process is not None or self._closed:
            raise RuntimeError("Process tree already used")
        if os.name == "nt":
            kwargs["creationflags"] = int(kwargs.get("creationflags", 0)) | 0x00000004  # CREATE_SUSPENDED
        else:
            kwargs["start_new_session"] = _task_group != os.getpgrp()
        try:
            self._process = subprocess.Popen(command, **kwargs)
            if os.name == "nt":
                self.attach(self._process.pid)
                _resume_suspended_process(self._process.pid)
            elif kwargs["start_new_session"]:
                self._group = self._process.pid
            return self._process
        except BaseException:
            try:
                self.close()
            finally:
                if self._process is not None:
                    for stream in (self._process.stdout, self._process.stderr, self._process.stdin):
                        if stream is not None:
                            stream.close()
            raise

    def close(self) -> None:
        """Stop owned descendants, then reap the direct child. Safe to repeat.

        A POSIX external child inside a module shares the controller-owned group;
        its remaining descendants are stopped by the controller before returning
        the task result. Do not split that group, even for external programs.
        """
        if self._closed:
            return
        try:
            if os.name == "nt" and self._job:
                try:
                    if not _terminate_job(self._job, 1):
                        raise ctypes.WinError(ctypes.get_last_error())
                    deadline = time.monotonic() + 5
                    while True:
                        accounting = _Accounting()
                        if not _query_job(self._job, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None):
                            raise ctypes.WinError(ctypes.get_last_error())
                        if accounting.ActiveProcesses == 0:
                            break
                        if time.monotonic() >= deadline:
                            raise TimeoutError("Task Job Object still contains running processes")
                        time.sleep(0.01)
                finally:
                    # Closing invokes KILL_ON_JOB_CLOSE even if termination or
                    # accounting fails. Report the error rather than pretending
                    # that the wait confirmed an empty job.
                    if not _close_handle(self._job):
                        raise ctypes.WinError(ctypes.get_last_error())
                    self._job = None
            elif self._group is not None:
                try:
                    os.killpg(self._group, signal.SIGTERM)
                    time.sleep(0.2)
                    os.killpg(self._group, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        finally:
            if self._process is not None:
                if self._process.poll() is None:
                    self._process.terminate()
                    try:
                        self._process.wait(timeout=0.2)
                    except subprocess.TimeoutExpired:
                        self._process.kill()
                self._process.wait(timeout=5)
        self._closed = True


def read_pipe_available(stream) -> bytes | None:
    """Read currently buffered bytes without waiting for inherited pipe handles."""
    if os.name == "nt":
        available = wintypes.DWORD()
        if not _peek_pipe(msvcrt.get_osfhandle(stream.fileno()), None, 0, None, ctypes.byref(available), None):
            error = ctypes.get_last_error()
            if error in (109, 232):  # BROKEN_PIPE / NO_DATA
                return None
            raise ctypes.WinError(error)
        if available.value == 0:
            return b""
        return os.read(stream.fileno(), min(available.value, 65536)) or None
    os.set_blocking(stream.fileno(), False)
    try:
        return os.read(stream.fileno(), 65536) or None
    except BlockingIOError:
        return b""
