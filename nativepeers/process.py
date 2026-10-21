"""Exact OS process identities; no process discovery by prompt or directory."""
from __future__ import annotations

import ctypes
from dataclasses import asdict, dataclass
import os
from pathlib import Path
import signal
import sys


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    ppid: int
    pgid: int
    uid: int
    start: str
    executable: str

    def json(self):
        return asdict(self)


class _BsdInfo(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint32) for name in
                ("flags", "status", "xstatus", "pid", "ppid", "uid", "gid", "ruid", "rgid",
                 "svuid", "svgid", "reserved")]
    _fields_ += [("comm", ctypes.c_char * 16), ("name", ctypes.c_char * 32)]
    _fields_ += [(name, ctypes.c_uint32) for name in
                 ("nfiles", "pgid", "pjobc", "tdev", "tpgid", "nice")]
    _fields_ += [("seconds", ctypes.c_uint64), ("microseconds", ctypes.c_uint64)]


def identity(pid: int) -> ProcessIdentity:
    if pid <= 0:
        raise ProcessLookupError(pid)
    if sys.platform == "darwin":
        library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        info = _BsdInfo()
        count = library.proc_pidinfo(pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info))
        executable = ctypes.create_string_buffer(4096)
        size = library.proc_pidpath(pid, executable, len(executable))
        if count != ctypes.sizeof(info) or size <= 0 or info.status == 5:
            raise ProcessLookupError(pid)
        return ProcessIdentity(pid, info.ppid, info.pgid, info.uid,
                               f"{info.seconds}:{info.microseconds}",
                               str(Path(os.fsdecode(executable.value)).resolve()))
    if sys.platform.startswith("linux"):
        root = Path(f"/proc/{pid}")
        fields = (root / "stat").read_text().rsplit(")", 1)[1].split()
        if fields[0] == "Z":
            raise ProcessLookupError(pid)
        boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        return ProcessIdentity(pid, int(fields[1]), int(fields[2]), root.stat().st_uid,
                               f"{boot}:{fields[19]}", str((root / "exe").resolve(strict=True)))
    raise NotImplementedError("Process ownership is unsupported on this OS")


def matches(record: ProcessIdentity | dict) -> bool:
    if isinstance(record, dict):
        record = ProcessIdentity(**record)
    try:
        actual = identity(record.pid)
        return all(getattr(actual, field) == getattr(record, field)
                   for field in ("pid", "pgid", "uid", "start", "executable"))
    except (ProcessLookupError, FileNotFoundError, PermissionError):
        return False


def ancestry(pid: int | None = None, limit: int = 32) -> list[ProcessIdentity]:
    current = os.getpid() if pid is None else pid
    seen = set()
    result = []
    while current > 1 and current not in seen and len(result) < limit:
        seen.add(current)
        try:
            process = identity(current)
        except (ProcessLookupError, FileNotFoundError, PermissionError):
            break
        result.append(process)
        current = process.ppid
    return result


def signal_owned(record: dict, sig: int = signal.SIGTERM, *, group: bool = False) -> bool:
    if not matches(record):
        return False
    if group:
        if record["pgid"] != record["pid"] or record["pgid"] == os.getpgrp():
            raise PermissionError("Refusing an unowned process group")
        os.killpg(record["pgid"], sig)
    else:
        os.kill(record["pid"], sig)
    return True
