"""Build the Setup.exe of the Hangeul Document Checker with Inno Setup 6.7.4 (APP_SPEC revision 2, R2.5).

Called by Task F's ``packaging\\build.py installer`` as ``build_installer.main(argv)``, or directly::

    %BPY% -I -B packaging\\installer\\build_installer.py --build-id <id> --check-date <YYYY-MM-DD>
          --this-pc-code <XXXX-XXXX>

What it does, in order (any failure stops it, exit code 1; bad arguments give 2):
1. Checks the arguments, that hdc.iss still carries the fixed AppId, and the frozen app folder
   ``<builds>\\<id>\\dist\\Hangeul Document Checker``: the exe is there, ``_internal\\BUILD.json`` has this build id,
   and every file equals ``_internal\\MANIFEST.json`` (size and sha256, none missing, none extra). So the installer
   wraps exactly the files that ``build.py check`` and the tests saw (the MANIFEST chain of G1).
2. Runs ``ISCC.exe /Qp /DAppFolder=... /DAppVersion=... /DFileVersion=1.0.0.<N> /DBuildId=<id> /O<out> hdc.iss`` at
   BELOW_NORMAL priority inside a Windows job object (ISCC and its islzma64 helper are waited for, kept at
   BELOW_NORMAL, and ended together on the 3 h timeout), with ISCC's output in ``<builds>\\<id>\\reports\\iscc.log``.
3. Only when ISCC refuses on its 4,200,000,000-byte limit for one Setup.exe, compiles once more with ``/DSpan=1``
   (Setup.exe plus ``-1.bin``, ``-2.bin`` ...).
4. Writes ``setup.sha256``, ``installer.json``, ``README_INSTALL.txt`` (the owner's release README, filled from this
   folder's template with this build's sizes: the Setup file, the installed app, the free disk space needed) and
   ``SHA256SUMS.txt`` (the Setup file(s) and README_INSTALL.txt) next to Setup.exe in ``<builds>\\<id>\\installer``:
   that folder holds what a release folder holds (1.0.1, APP_SPEC R2.10 I30).

Standard library only (it runs in the build venv and in the runner venv). It never installs anything. It prints
counts, sizes, seconds, file names of the app and codes; no student data exists anywhere in its inputs.

install_cycle.py (test P3) imports this module for APP_GUID / UNINSTALL_KEY, the manifest helpers and run_tree.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

APP_NAME = "Hangeul Document Checker"
EXE_NAME = APP_NAME + ".exe"
# The AppId of hdc.iss, chosen ONCE on 29 Oct 2026 by Task G and never changed: Inno Setup and Windows find an
# existing install (upgrade, uninstall, Settings > Apps) only through it. hdc.iss writes it as AppId={{GUID}.
APP_GUID = "767599DF-1DFD-490C-A03A-32EE783D7301"
APP_ID = "{" + APP_GUID + "}"
UNINSTALL_KEY = "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\" + APP_ID + "_is1"   # under HKCU
UNINSTALL_DISPLAY_NAME = APP_NAME + " (GPU)"
APP_VERSION_DEFAULT = "1.0.1"
SETUP_BASE = APP_NAME + " Setup {version} GPU"           # = OutputBaseFilename of hdc.iss

HERE = Path(__file__).resolve().parent
ISS = HERE / "hdc.iss"
README_TEMPLATE = HERE / "README_INSTALL.txt"
README_NAME = "README_INSTALL.txt"
SUMS_NAME = "SHA256SUMS.txt"
ISCC = Path(r"C:\Hangeul\DOCCHECK\tools\InnoSetup6\ISCC.exe")
BUILDS = Path(r"C:\Hangeul\DOCCHECK\lab\app-build\builds")

INNO_LIMIT_BYTES = 4_200_000_000     # Inno Setup 6.7.4: the most compressed data one Setup.exe may hold
TIMEOUT_S = 3 * 3600                 # per ISCC pass
# 1.0.1 (R2.10 I30): the release README names sizes of THIS build; no CHECK_0001 step (that file is not shipped)
README_KEYS = ("APP_VERSION", "BUILD_ID", "SETUP_GB", "SETUP_BYTES", "INSTALLED_GB", "DISK_GB")
DISK_MARGIN_GB = 0.5                 # added before the free disk space is rounded up to whole GB

BUILD_ID_RE = re.compile(r"^(?P<date>\d{8})\.(?P<n>\d{1,5})-(?P<git>[0-9a-f]{7,40})(?P<dirty>-dirty)?$")
APP_VERSION_RE = re.compile(r"^\d{1,5}\.\d{1,5}\.\d{1,5}$")
CHECK_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PC_CODE_RE = re.compile(r"^[0-9A-F]{4}-[0-9A-F]{4}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
APP_ID_RE = re.compile(r"^AppId=\{\{([0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12})\}\s*$",
                       re.M)
PLACEHOLDER_RE = re.compile(r"\{([A-Z][A-Z0-9_]*)\}")
# ISCC's refusal above the limit reads: "Disk spanning must be enabled to create an installation larger than
# <n> bytes in size for a single Setup.exe, ..." (ISCmplr.dll 6.7.4).
SPAN_RE = re.compile(r"disk\s+spanning\s+must\s+be\s+enabled", re.I)
INNO_OWN_FILE_RE = re.compile(r"^unins\d{3}\.(exe|dat|msg)$", re.I)   # what Inno Setup adds to {app}


class BuildError(Exception):
    """A check before or after ISCC failed; the message is printed as it is (no student data)."""


class ManifestError(BuildError):
    """MANIFEST.json cannot be read or has a malformed entry."""


# ----------------------------------------------------------------------------------------------------------------
# Small pure helpers (unit-tested)

def parse_build_id(build_id: str) -> Dict[str, Any]:
    """``YYYYMMDD.N-<git short>[-dirty]`` (rev 1 6.1) -> its parts; ValueError when malformed."""
    m = BUILD_ID_RE.match(build_id or "")
    if not m:
        raise ValueError("the build id must look like YYYYMMDD.N-<git short>[-dirty]")
    _dt.datetime.strptime(m.group("date"), "%Y%m%d")
    n = int(m.group("n"))
    if not 0 <= n <= 65535:
        raise ValueError("the build number N must be 0..65535 (it becomes the 4th part of the file version)")
    return {"date": m.group("date"), "n": n, "git": m.group("git"), "dirty": bool(m.group("dirty"))}


def file_version(app_version: str, build_id: str) -> str:
    """FileVersion of Setup.exe and of the app exe: ``<app version>.<N>`` (rev 1 6.1 step 8)."""
    if not APP_VERSION_RE.match(app_version or ""):
        raise ValueError("the app version must look like 1.0.0")
    return "%s.%d" % (app_version, parse_build_id(build_id)["n"])


def check_date_ok(value: str) -> str:
    if not CHECK_DATE_RE.match(value or ""):
        raise ValueError("the check date must look like YYYY-MM-DD")
    _dt.date.fromisoformat(value)
    return value


def pc_code_ok(value: str) -> str:
    if not PC_CODE_RE.match(value or ""):
        raise ValueError("the result code must look like XXXX-XXXX (8 upper-case hex digits)")
    return value


def read_app_id(iss_text: str) -> str:
    """The GUID of hdc.iss's ``AppId={{GUID}`` line (exactly one such line)."""
    found = APP_ID_RE.findall(iss_text)
    if len(found) != 1:
        raise BuildError("hdc.iss must hold exactly one line AppId={{<GUID>}")
    return found[0].upper()


def setup_base(app_version: str) -> str:
    return SETUP_BASE.format(version=app_version)


def setup_gb(total_bytes: int) -> str:
    """Size for README_INSTALL.txt, as Explorer shows it (1 GB = 1024^3 bytes), one decimal."""
    return "%.1f" % (total_bytes / float(1 << 30))


def disk_gb(setup_bytes: int, app_bytes: int) -> str:
    """The free disk space README_INSTALL.txt asks for, in whole GB as Explorer counts them (1024^3 bytes): the Setup
    file(s) and the installed app together (both are on the disk until the Setup file is deleted), plus
    DISK_MARGIN_GB (the data folder, Windows' own files), rounded up. Build 20261029.3: 1.8 + 4.4 GB -> "7"."""
    if setup_bytes < 0 or app_bytes < 0:
        raise ValueError("a size cannot be negative")
    return "%d" % math.ceil((setup_bytes + app_bytes) / float(1 << 30) + DISK_MARGIN_GB)


def bytes_text(n: int) -> str:
    """A byte count with thousands separators, such as "1,939,911,235" (as Explorer's Properties show a size)."""
    return "{:,}".format(int(n))


def needs_span(returncode: Optional[int], log_text: str) -> bool:
    """True only when ISCC failed because one Setup.exe would exceed its 4,200,000,000-byte limit."""
    return returncode not in (0, None) and bool(SPAN_RE.search(log_text or ""))


def _quoted(value: Any) -> str:
    text = str(value)
    if '"' in text:
        raise ValueError("a path for ISCC must not hold a double quote")
    return text.rstrip("\\") if len(text) > 3 else text


def iscc_command(iscc: Path, iss: Path, app_folder: Path, app_version: str, file_ver: str, build_id: str,
                 out_dir: Optional[Path], *, span: bool = False) -> str:
    """The ISCC command line of R2.5, as one string (Windows; Inno's own parser strips the quotes).

    ``out_dir=None`` gives ``/O-``: compile and check the script, write no Setup.exe (the unit tests' syntax check).
    """
    parts = ['"%s"' % _quoted(iscc), "/Qp", '/DAppFolder="%s"' % _quoted(app_folder),
             "/DAppVersion=%s" % app_version, "/DFileVersion=%s" % file_ver, "/DBuildId=%s" % build_id]
    if not (APP_VERSION_RE.match(app_version) and re.match(r"^[0-9.]+$", file_ver) and BUILD_ID_RE.match(build_id)):
        raise ValueError("app version, file version or build id malformed")
    if span:
        parts.append("/DSpan=1")
    parts.append("/O-" if out_dir is None else '/O"%s"' % _quoted(out_dir))
    parts.append('"%s"' % _quoted(iss))
    return " ".join(parts)


def fill_readme(template: str, values: Mapping[str, str]) -> str:
    """Fill the ``{KEY}`` places of README_INSTALL.txt (exactly README_KEYS); the result has CRLF line ends (Notepad,
    USB sticks)."""
    found = PLACEHOLDER_RE.findall(template)
    if sorted(set(found)) != sorted(README_KEYS):
        raise BuildError("README_INSTALL.txt must hold exactly the places %s" % ", ".join(README_KEYS))
    for key in README_KEYS:
        v = values.get(key)
        if not isinstance(v, str) or not v or any(c in v for c in "{}\r\n") or not v.isascii():
            raise BuildError("README value %s is missing or not a plain one-line ASCII text" % key)
    text = PLACEHOLDER_RE.sub(lambda m: values[m.group(1)], template)
    if "{" in text or "}" in text:
        raise BuildError("README_INSTALL.txt still holds a { or } after filling")
    lines = text.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n").split("\n")
    return "\r\n".join(lines) + "\r\n"


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


# ----------------------------------------------------------------------------------------------------------------
# MANIFEST.json (written by Task F's build.py check; rev 1 1.1: relative path, size and sha256 of every file of the
# app folder). The reader accepts the plausible shapes, because the writer is built in parallel:
#   {"files": [{"path": "...", "size": n, "sha256": "..."}, ...], ...}     (also "rel"/"relpath"/"file"/"name",
#   {"files": {"<rel>": {"size": n, "sha256": "..."}, ...}}                  "bytes", "sha"/"hash")
#   {"files": {"<rel>": [n, "<sha256>"]}},  a bare list of entries,  or a bare {"<rel>": {...}} map.

@dataclass(frozen=True)
class Entry:
    rel: str          # relative to the app folder, "/" separators, case as written
    size: int
    sha256: str


def norm_rel(rel: str) -> str:
    r = str(rel).replace("\\", "/")
    while r.startswith("./"):
        r = r[2:]
    return r.strip("/")


def rel_key(rel: str) -> str:
    """Windows compares names without case."""
    return norm_rel(rel).casefold()


def _entry_from(rel: Any, value: Any) -> Entry:
    if isinstance(value, Mapping):
        size = next((value[k] for k in ("size", "bytes") if k in value), None)
        sha = next((value[k] for k in ("sha256", "sha", "hash") if k in value), None)
    elif isinstance(value, (list, tuple)) and len(value) >= 2:
        size, sha = value[0], value[1]
    else:
        raise ManifestError("MANIFEST.json: an entry is neither a map nor [size, sha256]")
    r = norm_rel(rel) if isinstance(rel, str) else ""
    if (not r or r.startswith("/") or ":" in r or any(p in ("", ".", "..") for p in r.split("/"))):
        raise ManifestError("MANIFEST.json: an entry has no clean relative path")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise ManifestError("MANIFEST.json: %s has no valid size" % r)
    if not isinstance(sha, str) or not SHA256_RE.match(sha.lower()):
        raise ManifestError("MANIFEST.json: %s has no valid sha256" % r)
    return Entry(r, size, sha.lower())


def parse_manifest(data: Any) -> Dict[str, Entry]:
    """``{rel_key: Entry}`` from a decoded MANIFEST.json (see the shapes above)."""
    files = data.get("files", data) if isinstance(data, Mapping) else data
    out: Dict[str, Entry] = {}
    pairs: List[Tuple[Any, Any]] = []
    if isinstance(files, list):
        for item in files:
            if not isinstance(item, Mapping):
                raise ManifestError("MANIFEST.json: a list entry is not a map")
            rel = next((item[k] for k in ("path", "rel", "relpath", "file", "name") if k in item), None)
            pairs.append((rel, item))
    elif isinstance(files, Mapping):
        pairs = list(files.items())
    else:
        raise ManifestError("MANIFEST.json holds no file list")
    for rel, value in pairs:
        e = _entry_from(rel, value)
        k = rel_key(e.rel)
        if k in out:
            raise ManifestError("MANIFEST.json lists %s twice" % e.rel)
        out[k] = e
    if not out:
        raise ManifestError("MANIFEST.json lists no file")
    return out


def load_manifest(path: Path) -> Dict[str, Entry]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        raise ManifestError("MANIFEST.json cannot be read (%s)" % type(e).__name__) from None
    return parse_manifest(data)


def _find_in_app(app_folder: Path, name: str) -> Optional[Path]:
    for p in (Path(app_folder) / "_internal" / name, Path(app_folder) / name):
        if p.is_file():
            return p
    return None


def find_manifest(app_folder: Path) -> Optional[Path]:
    """``_internal\\MANIFEST.json`` (rev 1 1.1), else ``MANIFEST.json`` beside the exe."""
    return _find_in_app(app_folder, "MANIFEST.json")


def find_build_json(app_folder: Path) -> Optional[Path]:
    """``_internal\\BUILD.json`` (PyInstaller 6 puts datas there), else ``BUILD.json`` beside the exe."""
    return _find_in_app(app_folder, "BUILD.json")


def read_build_json(app_folder: Path) -> Dict[str, Any]:
    p = find_build_json(app_folder)
    if p is None:
        raise BuildError("BUILD.json is missing in the app folder")
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        raise BuildError("BUILD.json cannot be read") from None
    if not isinstance(data, dict):
        raise BuildError("BUILD.json is not a JSON object")
    return data


def list_files(root: Path) -> Dict[str, Tuple[str, Path]]:
    """``{rel_key: (rel, path)}`` of every file under root (links are not followed)."""
    out: Dict[str, Tuple[str, Path]] = {}
    root = Path(root)
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        for name in sorted(filenames):
            p = Path(dirpath) / name
            rel = p.relative_to(root).as_posix()
            out[rel_key(rel)] = (rel, p)
    return out


@dataclass
class TreeReport:
    checked: int = 0
    bytes: int = 0
    missing: List[str] = field(default_factory=list)
    changed: List[str] = field(default_factory=list)    # "<rel> (size)" or "<rel> (sha256)"
    extra: List[str] = field(default_factory=list)
    allowed_extra: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (self.missing or self.changed or self.extra)

    def summary(self, limit: int = 10) -> str:
        if self.ok:
            return "%d files match MANIFEST.json, none extra" % self.checked
        bits = []
        for label, items in (("missing", self.missing), ("changed", self.changed), ("extra", self.extra)):
            if items:
                shown = ", ".join(items[:limit]) + (" ..." if len(items) > limit else "")
                bits.append("%d %s: %s" % (len(items), label, shown))
        return "; ".join(bits)


def compare_tree(root: Path, manifest: Mapping[str, Entry], *, allow_extra: Callable[[str], bool] = lambda r: False,
                 hash_files: bool = True) -> TreeReport:
    """Every manifest file present with its size (and sha256 when hash_files); nothing else except allow_extra."""
    rep = TreeReport()
    present = list_files(root)
    for k in sorted(manifest):
        e = manifest[k]
        hit = present.get(k)
        if hit is None:
            rep.missing.append(e.rel)
            continue
        rep.checked += 1
        size = hit[1].stat().st_size
        rep.bytes += size
        if size != e.size:
            rep.changed.append("%s (size)" % e.rel)
        elif hash_files and sha256_file(hit[1]) != e.sha256:
            rep.changed.append("%s (sha256)" % e.rel)
    for k in sorted(set(present) - set(manifest)):
        rel = present[k][0]
        (rep.allowed_extra if allow_extra(rel) else rep.extra).append(rel)
    return rep


def inno_own_file(rel: str) -> bool:
    """unins000.exe / .dat / .msg at the top of {app}: Inno Setup's uninstaller, not part of the app."""
    r = norm_rel(rel)
    return "/" not in r and bool(INNO_OWN_FILE_RE.match(r))


# ----------------------------------------------------------------------------------------------------------------
# Running a program tree (ISCC + islzma64; Setup.exe + setup.tmp; unins000.exe + its second phase in %TEMP%)

@dataclass
class RunResult:
    returncode: Optional[int]
    seconds: float
    timed_out: bool = False
    in_job: bool = False          # the whole process tree was waited for (and ended on timeout)
    peak_job_mb: Optional[int] = None
    pid: Optional[int] = None


CREATE_SUSPENDED = 0x00000004
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
CREATE_NO_WINDOW = 0x08000000
_JOB_LIMIT_PRIORITY_CLASS = 0x00000020
_JOB_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_BASIC_ACCOUNTING = 1
_JOB_EXTENDED_LIMITS = 9

if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    class _IoCounters(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in ("ReadOperationCount", "WriteOperationCount",
                                                      "OtherOperationCount", "ReadTransferCount",
                                                      "WriteTransferCount", "OtherTransferCount")]

    class _BasicLimits(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class _ExtendedLimits(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", _BasicLimits), ("IoInfo", _IoCounters),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    class _Accounting(ctypes.Structure):
        _fields_ = [("TotalUserTime", ctypes.c_int64), ("TotalKernelTime", ctypes.c_int64),
                    ("ThisPeriodTotalUserTime", ctypes.c_int64), ("ThisPeriodTotalKernelTime", ctypes.c_int64),
                    ("TotalPageFaultCount", wintypes.DWORD), ("TotalProcesses", wintypes.DWORD),
                    ("ActiveProcesses", wintypes.DWORD), ("TotalTerminatedProcesses", wintypes.DWORD)]

    class _PidList(ctypes.Structure):
        _fields_ = [("NumberOfAssignedProcesses", wintypes.DWORD), ("NumberOfProcessIdsInList", wintypes.DWORD),
                    ("ProcessIdList", ctypes.c_size_t * 1024)]

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    _k32.CreateJobObjectW.restype = wintypes.HANDLE
    _k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    _k32.SetInformationJobObject.restype = wintypes.BOOL
    _k32.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                               ctypes.POINTER(wintypes.DWORD)]
    _k32.QueryInformationJobObject.restype = wintypes.BOOL
    _k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _k32.AssignProcessToJobObject.restype = wintypes.BOOL
    _k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _k32.TerminateJobObject.restype = wintypes.BOOL
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.CloseHandle.restype = wintypes.BOOL
    _k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _k32.WaitForSingleObject.restype = wintypes.DWORD
    _SYNCHRONIZE = 0x00100000
    _JOB_PID_LIST = 3
    _ntdll = ctypes.WinDLL("ntdll")
    _ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
    _ntdll.NtResumeProcess.restype = ctypes.c_long

    class _Job:
        """A job object: every process the child starts belongs to it, so the whole tree is waited for, kept at
        BELOW_NORMAL, and ended together (on timeout, or when this script dies: KILL_ON_JOB_CLOSE)."""

        def __init__(self, below_normal: bool) -> None:
            h = _k32.CreateJobObjectW(None, None)
            if not h:
                raise ctypes.WinError(ctypes.get_last_error())
            self.h = h
            info = _ExtendedLimits()
            flags = _JOB_LIMIT_KILL_ON_JOB_CLOSE
            if below_normal:
                flags |= _JOB_LIMIT_PRIORITY_CLASS
                info.BasicLimitInformation.PriorityClass = BELOW_NORMAL_PRIORITY_CLASS
            info.BasicLimitInformation.LimitFlags = flags
            if not _k32.SetInformationJobObject(h, _JOB_EXTENDED_LIMITS, ctypes.byref(info), ctypes.sizeof(info)):
                err = ctypes.get_last_error()
                self.close()
                raise ctypes.WinError(err)

        def assign(self, process_handle: int) -> bool:
            return bool(_k32.AssignProcessToJobObject(self.h, wintypes.HANDLE(process_handle)))

        def active(self) -> int:
            acc = _Accounting()
            if not _k32.QueryInformationJobObject(self.h, _JOB_BASIC_ACCOUNTING, ctypes.byref(acc),
                                                  ctypes.sizeof(acc), None):
                return 0
            return int(acc.ActiveProcesses)

        def peak_mb(self) -> Optional[int]:
            info = _ExtendedLimits()
            if not _k32.QueryInformationJobObject(self.h, _JOB_EXTENDED_LIMITS, ctypes.byref(info),
                                                  ctypes.sizeof(info), None):
                return None
            return int(info.PeakJobMemoryUsed // (1 << 20))

        def terminate(self, wait_s: float = 30.0) -> None:
            """End every process of the job and wait until each one is completely gone (its handle signaled:
            the exit code is set at once, the teardown finishes a moment later)."""
            handles = []
            ids = _PidList()
            if _k32.QueryInformationJobObject(self.h, _JOB_PID_LIST, ctypes.byref(ids), ctypes.sizeof(ids), None):
                for pid in ids.ProcessIdList[:min(ids.NumberOfProcessIdsInList, 1024)]:
                    h = _k32.OpenProcess(_SYNCHRONIZE, False, int(pid))
                    if h:
                        handles.append(h)
            _k32.TerminateJobObject(self.h, 1)
            end = time.monotonic() + wait_s
            for h in handles:
                _k32.WaitForSingleObject(h, max(0, int((end - time.monotonic()) * 1000)))
                _k32.CloseHandle(h)

        def close(self) -> None:
            if self.h:
                _k32.CloseHandle(self.h)
                self.h = None

    def _resume(process_handle: int) -> bool:
        return _ntdll.NtResumeProcess(wintypes.HANDLE(process_handle)) == 0


def run_tree(cmd: Any, *, timeout_s: float, log_path: Optional[Path] = None, below_normal: bool = True,
             poll_s: float = 0.5, cwd: Optional[Path] = None) -> RunResult:
    """Run cmd and wait for it AND every process it starts; end them all after timeout_s.

    On Windows the child starts suspended, joins a job object (BELOW_NORMAL for the whole tree,
    KILL_ON_JOB_CLOSE), and is then resumed. If the job cannot be made or joined, only the child itself is waited
    for and killed (``in_job`` False). Output (stdout and stderr) goes to log_path, else nowhere.
    """
    start = time.monotonic()
    out: Any = subprocess.DEVNULL
    if log_path is not None:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        out = open(log_path, "wb")
    job = None
    if os.name == "nt":
        try:
            job = _Job(below_normal)
        except OSError:
            job = None
        flags = CREATE_NO_WINDOW | (BELOW_NORMAL_PRIORITY_CLASS if below_normal else 0)
        flags |= CREATE_SUSPENDED if job is not None else 0
    else:  # pragma: no cover - the app and its build are Windows only
        flags = 0
    proc = None
    in_job = False
    timed_out = False
    peak = None
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=out,
                                stderr=subprocess.STDOUT if log_path is not None else subprocess.DEVNULL,
                                cwd=None if cwd is None else str(cwd), creationflags=flags)
        if job is not None:
            handle = int(proc._handle)          # the process handle subprocess keeps (PROCESS_ALL_ACCESS)
            in_job = job.assign(handle)
            if not _resume(handle):
                proc.kill()
                proc.wait(30)
                raise OSError("the child process could not be resumed")
        deadline = start + timeout_s
        while True:
            if proc.poll() is not None and (not in_job or job.active() == 0):
                break
            if time.monotonic() >= deadline:
                timed_out = True
                break
            time.sleep(poll_s)
    finally:
        if proc is not None and (timed_out or proc.poll() is None or (in_job and job.active() > 0)):
            if in_job:
                job.terminate()
            elif proc.poll() is None:
                proc.kill()
            try:
                proc.wait(30)
            except subprocess.TimeoutExpired:
                pass
            if in_job:
                end = time.monotonic() + 30
                while job.active() > 0 and time.monotonic() < end:
                    time.sleep(0.1)
        if in_job:
            peak = job.peak_mb()
        if job is not None:
            job.close()                          # KILL_ON_JOB_CLOSE: nothing of the tree outlives this call
        if log_path is not None:
            out.close()
    return RunResult(None if proc is None else proc.returncode, round(time.monotonic() - start, 1), timed_out,
                     in_job, peak, None if proc is None else proc.pid)


def file_version_of(path: Path) -> Optional[str]:
    """The FILEVERSION of a Windows exe or DLL (for installer.json), or None."""
    if os.name != "nt":
        return None
    try:
        ver = ctypes.WinDLL("version", use_last_error=True)
        ver.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
        ver.GetFileVersionInfoSizeW.restype = wintypes.DWORD
        ver.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
        ver.GetFileVersionInfoW.restype = wintypes.BOOL
        ver.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p),
                                       ctypes.POINTER(wintypes.UINT)]
        ver.VerQueryValueW.restype = wintypes.BOOL
        size = ver.GetFileVersionInfoSizeW(str(path), None)
        if not size:
            return None
        buf = ctypes.create_string_buffer(size)
        if not ver.GetFileVersionInfoW(str(path), 0, size, buf):
            return None
        ptr, n = ctypes.c_void_p(), wintypes.UINT()
        if not ver.VerQueryValueW(buf, "\\", ctypes.byref(ptr), ctypes.byref(n)) or not ptr.value:
            return None
        words = (wintypes.DWORD * 4).from_address(ptr.value + 8)    # dwFileVersionMS, LS after signature, struc
        ms, ls = words[0], words[1]
        return "%d.%d.%d.%d" % (ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF)
    except Exception:
        return None


# ----------------------------------------------------------------------------------------------------------------
# Outputs

def output_files(out_dir: Path, base: str) -> List[Path]:
    """Setup.exe first, then ``<base>-1.bin``, ``-2.bin`` ... in number order (only those that exist)."""
    out_dir = Path(out_dir)
    files = []
    exe = out_dir / (base + ".exe")
    if exe.is_file():
        files.append(exe)
    bins = []
    pat = re.compile(re.escape(base) + r"-(\d+)\.bin$", re.I)
    if out_dir.is_dir():
        for p in out_dir.iterdir():
            m = pat.match(p.name)
            if m and p.is_file():
                bins.append((int(m.group(1)), p))
    return files + [p for _, p in sorted(bins)]


def write_sha256(out_dir: Path, files: Sequence[Path], name: str = "setup.sha256") -> Dict[str, str]:
    """``<sha256> *<file name>`` per file, in order, into ``out_dir`` / ``name`` (setup.sha256: the Setup files;
    SUMS_NAME: those and README_INSTALL.txt, for the release folder)."""
    sums = {p.name: sha256_file(p) for p in files}
    text = "".join("%s *%s\n" % (sums[p.name], p.name) for p in files)
    (Path(out_dir) / name).write_text(text, encoding="ascii", newline="\n")
    return sums


def _say(msg: str) -> None:
    print(msg, flush=True)


def _tail(path: Path, lines: int = 15) -> List[str]:
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return [ln for ln in text.splitlines() if ln.strip()][-lines:]


def _read_log(path: Path) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


Runner = Callable[..., RunResult]


def main(argv: Optional[Sequence[str]] = None, *, runner: Optional[Runner] = None,
         now: Optional[Callable[[], _dt.datetime]] = None) -> int:
    """Exit codes: 0 Setup.exe built and described; 1 a check or ISCC failed; 2 bad arguments."""
    p = argparse.ArgumentParser(prog="build_installer.py",
                                description="Build the Setup.exe of the Hangeul Document Checker (APP_SPEC r2 R2.5).")
    p.add_argument("--build-id", required=True, help="YYYYMMDD.N-<git short>[-dirty]")
    p.add_argument("--check-date", required=True,
                   help="P1's fixed check date of CHECK_0001 (YYYY-MM-DD), recorded in installer.json")
    p.add_argument("--this-pc-code", required=True,
                   help="CHECK_0001's result code on this PC (XXXX-XXXX, P1), recorded in installer.json")
    p.add_argument("--builds-root", default=str(BUILDS))
    p.add_argument("--app-folder", help="default: <builds>\\<id>\\dist\\Hangeul Document Checker")
    p.add_argument("--out-dir", help="default: <builds>\\<id>\\installer")
    p.add_argument("--reports-dir", help="default: <builds>\\<id>\\reports")
    p.add_argument("--iscc", default=str(ISCC))
    p.add_argument("--iss", default=str(ISS))
    p.add_argument("--readme-template", default=str(README_TEMPLATE))
    p.add_argument("--timeout-s", type=float, default=TIMEOUT_S, help="per ISCC pass (default 3 h)")
    p.add_argument("--sizes-only", action="store_true", help="check the app folder against MANIFEST.json by size only")
    p.add_argument("--replace", action="store_true", help="delete an earlier Setup of this build id first")
    p.add_argument("--dry-run", action="store_true", help="run every check and print the ISCC command; compile nothing")
    try:
        a = p.parse_args(list(argv) if argv is not None else None)
    except SystemExit as e:
        return int(e.code or 0) if isinstance(e.code, int) else 2
    run = runner or run_tree
    clock = now or (lambda: _dt.datetime.now().astimezone())

    try:
        build_id = a.build_id
        parse_build_id(build_id)
        check_date_ok(a.check_date)
        pc_code_ok(a.this_pc_code)
    except ValueError as e:
        _say("build_installer: %s" % e)
        return 2
    builds = Path(a.builds_root)
    app_folder = Path(a.app_folder) if a.app_folder else builds / build_id / "dist" / APP_NAME
    out_dir = Path(a.out_dir) if a.out_dir else builds / build_id / "installer"
    reports = Path(a.reports_dir) if a.reports_dir else builds / build_id / "reports"
    iscc, iss, template_path = Path(a.iscc), Path(a.iss), Path(a.readme_template)

    try:
        # 1. Inputs
        if not iscc.is_file():
            raise BuildError("ISCC.exe is not where it should be (Inno Setup 6.7.4)")
        if read_app_id(iss.read_text(encoding="ascii")) != APP_GUID:
            raise BuildError("hdc.iss carries another AppId than build_installer.APP_GUID")
        template = template_path.read_text(encoding="utf-8")
        fill_readme(template, {k: "0" for k in README_KEYS})          # the template itself is well-formed
        if not (app_folder / EXE_NAME).is_file():
            raise BuildError("the app folder has no %s" % EXE_NAME)
        build = read_build_json(app_folder)
        if build.get("build_id") != build_id:
            raise BuildError("BUILD.json's build_id is not %s" % build_id)
        app_version = build.get("app_version") or APP_VERSION_DEFAULT
        file_ver = file_version(app_version, build_id)
        mpath = find_manifest(app_folder)
        if mpath is None:
            raise BuildError("MANIFEST.json is missing in the app folder (build.py check writes it)")
        manifest = load_manifest(mpath)
        mrel = mpath.relative_to(app_folder).as_posix()
        _say("checking the app folder against MANIFEST.json (%s) ..." % ("sizes" if a.sizes_only else "sha256"))
        t0 = time.monotonic()
        tree = compare_tree(app_folder, manifest, allow_extra=lambda r: rel_key(r) == rel_key(mrel),
                            hash_files=not a.sizes_only)
        if not tree.ok:
            raise BuildError("the app folder differs from MANIFEST.json: " + tree.summary())
        _say("  ok   %s (%.1f GB, %.0f s)" % (tree.summary(), tree.bytes / 1e9, time.monotonic() - t0))
        base = setup_base(app_version)
        old = output_files(out_dir, base)
        if old and not a.replace:
            raise BuildError("a Setup of build %s already exists (use --replace to build it again)" % build_id)
        cmd = iscc_command(iscc, iss, app_folder, app_version, file_ver, build_id, out_dir)
        if a.dry_run:
            _say("dry run, ISCC command:")
            _say("  " + cmd)
            return 0
        for f in old:
            f.unlink()
        out_dir.mkdir(parents=True, exist_ok=True)
        reports.mkdir(parents=True, exist_ok=True)

        # 2-3. ISCC, then once with /DSpan=1 only on the size limit
        passes: List[Dict[str, Any]] = []
        spanning = False
        log = reports / "iscc.log"
        _say("ISCC at BELOW_NORMAL (lzma2/ultra64; about 30-60 min) ...")
        r = run(cmd, timeout_s=a.timeout_s, log_path=log)
        limit_hit = needs_span(r.returncode, _read_log(log))
        passes.append({"span": False, "returncode": r.returncode, "seconds": r.seconds, "timed_out": r.timed_out,
                       "limit_hit": limit_hit, "peak_job_mb": r.peak_job_mb, "in_job": r.in_job})
        if r.timed_out:
            raise BuildError("ISCC took longer than %d s and was ended" % a.timeout_s)
        if r.returncode != 0 and limit_hit:
            _say("ISCC: one Setup.exe would exceed %d bytes; compiling again with disk spanning ..." % INNO_LIMIT_BYTES)
            for f in output_files(out_dir, base):
                f.unlink()
            spanning = True
            log = reports / "iscc-span.log"
            cmd = iscc_command(iscc, iss, app_folder, app_version, file_ver, build_id, out_dir, span=True)
            r = run(cmd, timeout_s=a.timeout_s, log_path=log)
            passes.append({"span": True, "returncode": r.returncode, "seconds": r.seconds,
                           "timed_out": r.timed_out, "limit_hit": False, "peak_job_mb": r.peak_job_mb,
                           "in_job": r.in_job})
            if r.timed_out:
                raise BuildError("ISCC (disk spanning) took longer than %d s and was ended" % a.timeout_s)
        if r.returncode != 0:
            for ln in _tail(log):
                _say("  | " + ln)
            raise BuildError("ISCC failed with exit code %s (log: reports\\%s)" % (r.returncode, log.name))

        # 4. Outputs
        files = output_files(out_dir, base)
        if not files or files[0].suffix.lower() != ".exe":
            raise BuildError("ISCC reported success but %s.exe is missing" % base)
        if not spanning and len(files) > 1:
            raise BuildError("unexpected .bin files without disk spanning")
        sums = write_sha256(out_dir, files)
        total = sum(f.stat().st_size for f in files)
        gb = setup_gb(total)
        installed_gb = setup_gb(tree.bytes)
        need_gb = disk_gb(total, tree.bytes)
        info = {
            "app_id": APP_ID, "app_version": app_version, "build_id": build_id, "file_version": file_ver,
            "setup": files[0].name,
            "files": [{"name": f.name, "bytes": f.stat().st_size, "sha256": sums[f.name]} for f in files],
            "bytes": total, "setup_gb": gb, "app_files": tree.checked, "app_bytes": tree.bytes,
            "installed_gb": installed_gb, "disk_gb": need_gb,
            "ratio": round(total / tree.bytes, 4) if tree.bytes else None,
            "seconds": round(sum(x["seconds"] for x in passes), 1), "spanning": spanning,
            "passes": passes, "iscc": file_version_of(iscc), "check_date": a.check_date,
            "this_pc_code": a.this_pc_code, "built": clock().isoformat(timespec="seconds"),
        }
        (out_dir / "installer.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
        readme = fill_readme(template, {"APP_VERSION": app_version, "BUILD_ID": build_id, "SETUP_GB": gb,
                                        "SETUP_BYTES": bytes_text(total), "INSTALLED_GB": installed_gb,
                                        "DISK_GB": need_gb})
        (out_dir / README_NAME).write_bytes(readme.encode("ascii"))
        write_sha256(out_dir, list(files) + [out_dir / README_NAME], SUMS_NAME)
        _say("  ok   %s: %s bytes (%s GB), %d file(s), spanning %s, ratio %s, %.0f s"
             % (files[0].name, total, gb, len(files), "yes" if spanning else "no", info["ratio"], info["seconds"]))
        _say("  ok   README: Setup %s GB, installed %s GB, free disk %s GB" % (gb, installed_gb, need_gb))
        _say("  ok   setup.sha256, installer.json, %s, %s written" % (README_NAME, SUMS_NAME))
        return 0
    except BuildError as e:
        _say("build_installer: FAILED: %s" % e)
        return 1
    except (OSError, UnicodeError) as e:
        _say("build_installer: FAILED: %s while reading or writing a build file" % type(e).__name__)
        return 1
    except ValueError as e:
        _say("build_installer: FAILED: %s" % e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
