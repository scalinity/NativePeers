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


def terminal_owner(record: dict) -> bool:
    """A Codex TUI owns terminal stdin; a private app-server's pipe is not TUI ingress."""
    import subprocess
    if not matches(record):return False
    if sys.platform=='darwin':
        result=subprocess.run(['/usr/sbin/lsof','-a','-p',str(record['pid']),'-d','0','-Fn'],capture_output=True,text=True,timeout=1)
        paths=[line[1:] for line in result.stdout.splitlines() if line.startswith('n')]
        terminal=any(path.startswith('/dev/tty') for path in paths)
    else:
        try:terminal=os.readlink('/proc/'+str(record['pid'])+'/fd/0').startswith(('/dev/pts/','/dev/tty'))
        except OSError:terminal=False
    return terminal and matches(record)


def descendants(records):
    """Capture birth identities only while a verified ancestor is still live."""
    import subprocess
    roots={r['pid'] for r in records if r and matches(r)}
    if not roots:return []
    raw=subprocess.run(['/bin/ps' if sys.platform=='darwin' else '/usr/bin/ps','-axo','pid=,ppid='],capture_output=True,timeout=.5).stdout
    parents={int(p):int(pp) for p,pp in (line.split() for line in raw.decode().splitlines())}
    result=[]
    for _ in range(32):
        selected=[p for p,pp in parents.items() if pp in roots and p not in roots]
        if not selected:break
        for pid in selected:
            try:record=identity(pid).json()
            except (ProcessLookupError,FileNotFoundError,PermissionError):continue
            if record['uid']==os.getuid() and record['ppid'] in roots:
                result.append(record);roots.add(pid)
    return result


def refresh_captured(record):
    """An observed process may exec or setsid without changing its birth identity."""
    try:current=identity(record['pid']).json()
    except (ProcessLookupError,FileNotFoundError,PermissionError):return None
    return current if all(current[k]==record[k] for k in ('pid','uid','start')) else None
