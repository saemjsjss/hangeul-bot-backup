"""System facts for the self-test, and the processor flags for the boot (APP_SPEC rev 1 sections 1.2, 1.4 and 6.2;
revision 2 R2.3.4; Task D).

Standard library and ctypes only: the window imports this module, and it never imports numpy, torch or the runner.
Every fact is a number, a version, a yes/no, or a Windows / hardware name; never a path, a user name or a PC name.
Free texts (error messages of the self-test parts) go through ``scrub()`` before they reach SELFTEST.txt.

Contract for the other tasks:
    cpu_flags(k32=None) -> {"SSE4.2", "AVX", "AVX2", "AVX512F": bool}   (C's boot; IsProcessorFeaturePresent 38-41)
    scrub(text) -> str                         drive-letter, UNC and file: paths -> <path>; the user name -> <user>;
                                               the PC name -> <pc>
    collect(data_dir, ...) -> dict             the facts of the SELFTEST.txt header lines (no paths)
    lines(facts, numpy_flags=None) -> [str]    Windows, CPU, RAM, App data path and Graphics card lines
    warnings(facts, numpy_flags=None) -> [str] the WARNINGS of rev 1 section 1.2 and R2.3.4
    file_version(path), loaded_modules(), module_path(handle), peak_working_set_mb()   (the self-test's runtime part)
No personal data.
"""
from __future__ import annotations

import ctypes
import os
import platform
import re
import shutil
import sys
from ctypes import wintypes
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

PATH_TOKEN = "<path>"
USER_TOKEN = "<user>"
PC_TOKEN = "<pc>"
# IsProcessorFeaturePresent codes (winnt.h): PF_SSE4_2 38, PF_AVX 39, PF_AVX2 40, PF_AVX512F 41
PF_CODES: Tuple[Tuple[str, int], ...] = (("SSE4.2", 38), ("AVX", 39), ("AVX2", 40), ("AVX512F", 41))
# numpy's __cpu_features__ names for the same flags (the self-test's core part reports them)
NUMPY_NAMES = {"SSE4.2": "SSE42", "AVX": "AVX", "AVX2": "AVX2", "AVX512F": "AVX512F"}
TESTED_BUILD = 19045               # rev 1 1.4: Windows builds below this are "not tested"
RAM_WARN_TOTAL_GB = 8.0            # rev 1 1.2 WARNINGS
RAM_WARN_FREE_GB = 2.5
DATA_PATH_WARN_CHARS = 100
CARD_FREE_WARN_MB = 5 * 1024       # R2.3.4: "a check may wait"
LAPTOP_CARD_WARN_MB = 7800         # R2.3.4: a laptop card under 8 GB (an "8 GB" card reports about 8150 MiB)
LAPTOP_WORDS = ("laptop", "max-q", "mobile")
_MIB = 1024 * 1024
_GIB = 1024 ** 3

_KEY_NT = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion"
_KEY_CPU = r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
_KEY_CI = r"SYSTEM\CurrentControlSet\Control\CI\Policy"
_KEY_FS = r"SYSTEM\CurrentControlSet\Control\FileSystem"

Reg = Callable[[str, str], Any]       # (key under HKEY_LOCAL_MACHINE, value name) -> value or None


# ------------------------------------------------------------------------------------------------ scrubbing
# Sentinels while scrubbing, so a user called "path" or "user" cannot hit an earlier replacement.
_S_PATH, _S_USER, _S_PC = "\x00P\x00", "\x00U\x00", "\x00C\x00"
_QUOTED_PATH = re.compile(r"""(['"])(?:\\\\\?\\|file:/+|[A-Za-z]:[\\/]|\\\\|//)(?:(?!\1)[^\r\n])*\1""",
                          re.IGNORECASE)
_FILE_URL = re.compile(r"file:/+[^\s'\"<>|]*", re.IGNORECASE)
# a drive letter not glued to a word ("https://" is no drive), a \\server\ share, a //server/ share (not a URL's)
_BARE_PATH = re.compile(r"""(?:\\\\\?\\)?(?:(?<![A-Za-z0-9])[A-Za-z]:[\\/]|\\\\[^\\/\s'"]+[\\/]"""
                        r"""|(?<![:/A-Za-z0-9])//[^\\/\s'"]+/)[^\r\n'"<>|]*""")


def _names_from_env(environ: Mapping[str, str]) -> Tuple[List[str], List[str]]:
    users: List[str] = []
    for key in ("USERNAME", "USER"):
        v = (environ.get(key) or "").strip()
        if v:
            users.append(v)
    for key in ("USERPROFILE", "HOME"):
        v = (environ.get(key) or "").strip().rstrip("\\/")
        if v:
            base = re.split(r"[\\/]", v)[-1]
            if base:
                users.append(base)
    pcs = [v for v in ((environ.get("COMPUTERNAME") or "").strip(),) if v]
    return users, pcs


def _word(name: str) -> "re.Pattern[str]":
    return re.compile(r"(?<![A-Za-z0-9])" + re.escape(name) + r"(?![A-Za-z0-9])", re.IGNORECASE)


def scrub(text: Any, *, users: Iterable[str] = (), pcs: Iterable[str] = (),
          environ: Optional[Mapping[str, str]] = None) -> str:
    """``text`` with every drive-letter, UNC, ``\\\\?\\`` and file: path replaced by <path> (a quoted path up to its
    closing quote, an unquoted one up to the end of the line), then the user name (USERNAME, the profile folder's
    name, and ``users``) by <user> and the PC name (COMPUTERNAME and ``pcs``) by <pc>, as whole words in any case.
    Never raises."""
    try:
        s = str(text)
    except Exception:
        return "<unprintable>"
    env = os.environ if environ is None else environ
    env_users, env_pcs = _names_from_env(env)
    s = _QUOTED_PATH.sub(lambda m: m.group(1) + _S_PATH + m.group(1), s)
    s = _FILE_URL.sub(_S_PATH, s)
    s = _BARE_PATH.sub(_S_PATH, s)
    names = sorted({n for n in list(users) + env_users if n and len(n) >= 2}, key=len, reverse=True)
    for n in names:
        s = _word(n).sub(_S_USER, s)
    hosts = sorted({n for n in list(pcs) + env_pcs if n and len(n) >= 2}, key=len, reverse=True)
    for n in hosts:
        s = _word(n).sub(_S_PC, s)
    return s.replace(_S_PATH, PATH_TOKEN).replace(_S_USER, USER_TOKEN).replace(_S_PC, PC_TOKEN)


def holds_private(text: str, environ: Optional[Mapping[str, str]] = None) -> List[str]:
    """What in ``text`` looks private: "path" (a drive-letter or UNC path), "user" (the user name), "pc" (the PC
    name). Empty when clean. For the self-test's own check of SELFTEST.txt and the unit tests."""
    env = os.environ if environ is None else environ
    found = []
    if _BARE_PATH.search(text) or _FILE_URL.search(text):
        found.append("path")
    users, pcs = _names_from_env(env)
    if any(len(u) >= 2 and _word(u).search(text) for u in users):
        found.append("user")
    if any(len(p) >= 2 and _word(p).search(text) for p in pcs):
        found.append("pc")
    return found


# ------------------------------------------------------------------------------------------------- Windows
def _kernel32() -> Any:
    return ctypes.WinDLL("kernel32", use_last_error=True)


def _reg_default(key: str, value: str) -> Any:
    """HKEY_LOCAL_MACHINE\\<key> <value> (64-bit view), or None. Reading only."""
    try:
        import winreg
    except ImportError:
        return None
    try:
        flags = winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0)
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key, 0, flags) as k:
            return winreg.QueryValueEx(k, value)[0]
    except OSError:
        return None


def cpu_flags(k32: Any = None) -> Dict[str, bool]:
    """SSE4.2, AVX, AVX2, AVX512F from IsProcessorFeaturePresent (codes 38-41). Pure ctypes, never raises. Older
    Windows builds may not know these codes and answer False; the self-test adds numpy's view (``merge_flags``)."""
    out = {name: False for name, _code in PF_CODES}
    try:
        k = k32 if k32 is not None else _kernel32()
        fn = k.IsProcessorFeaturePresent
        for name, code in PF_CODES:
            try:
                out[name] = bool(fn(code))
            except Exception:
                out[name] = False
    except Exception:
        pass
    return out


def merge_flags(flags: Mapping[str, bool], numpy_flags: Optional[Mapping[str, Any]]) -> Dict[str, bool]:
    """A flag is "yes" when Windows or numpy says so."""
    out = {name: bool(flags.get(name)) for name, _code in PF_CODES}
    for name, np_name in NUMPY_NAMES.items():
        if numpy_flags and numpy_flags.get(np_name):
            out[name] = True
    return out


def cpu_name(reg: Optional[Reg] = None) -> str:
    v = (reg or _reg_default)(_KEY_CPU, "ProcessorNameString")
    name = " ".join(str(v).split()) if v else ""
    return name or (platform.processor() or "unknown processor")


def cpu_counts(k32: Any = None) -> Tuple[Optional[int], Optional[int]]:
    """(physical cores, logical processors). Cores from GetLogicalProcessorInformationEx(RelationProcessorCore)."""
    threads = os.cpu_count()
    cores: Optional[int] = None
    try:
        k = k32 if k32 is not None else _kernel32()
        fn = k.GetLogicalProcessorInformationEx
        fn.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
        fn.restype = wintypes.BOOL
        size = wintypes.DWORD(0)
        fn(0, None, ctypes.byref(size))
        if size.value:
            buf = ctypes.create_string_buffer(size.value)
            if fn(0, buf, ctypes.byref(size)):
                n, off = 0, 0
                while off + 8 <= size.value:
                    rec_size = int.from_bytes(buf.raw[off + 4:off + 8], "little")
                    if rec_size <= 0:
                        break
                    n += 1
                    off += rec_size
                cores = n or None
    except Exception:
        cores = None
    return cores, threads


def native_arch(k32: Any = None) -> str:
    """"x64" or "ARM64" (the machine, not this process; IsWow64Process2), else platform.machine()."""
    try:
        k = k32 if k32 is not None else _kernel32()
        k.GetCurrentProcess.restype = wintypes.HANDLE
        fn = k.IsWow64Process2
        fn.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_ushort), ctypes.POINTER(ctypes.c_ushort)]
        fn.restype = wintypes.BOOL
        proc, native = ctypes.c_ushort(0), ctypes.c_ushort(0)
        if fn(k.GetCurrentProcess(), ctypes.byref(proc), ctypes.byref(native)):
            return {0x8664: "x64", 0xAA64: "ARM64", 0x014C: "x86"}.get(native.value, f"machine 0x{native.value:04X}")
    except Exception:
        pass
    m = (platform.machine() or "").upper()
    return {"AMD64": "x64", "X86_64": "x64", "ARM64": "ARM64"}.get(m, m or "unknown")


def windows_info(reg: Optional[Reg] = None, k32: Any = None) -> Dict[str, Any]:
    """Product name, display version, build, UBR and the machine's architecture. Windows 11 still names itself
    "Windows 10 ..." in ProductName; a build of 22000 or more is shown as Windows 11."""
    r = reg or _reg_default
    product = str(r(_KEY_NT, "ProductName") or "Windows").strip()
    display = str(r(_KEY_NT, "DisplayVersion") or r(_KEY_NT, "ReleaseId") or "").strip()
    try:
        build = int(str(r(_KEY_NT, "CurrentBuildNumber") or r(_KEY_NT, "CurrentBuild") or 0))
    except ValueError:
        build = 0
    try:
        ubr = int(r(_KEY_NT, "UBR") or 0)
    except (TypeError, ValueError):
        ubr = 0
    if build >= 22000 and product.startswith("Windows 10"):
        product = "Windows 11" + product[len("Windows 10"):]
    return {"product": product, "display_version": display, "build": build, "ubr": ubr,
            "arch": native_arch(k32)}


def smart_app_control(reg: Optional[Reg] = None) -> str:
    """"off", "on", "evaluation" (VerifiedAndReputablePolicyState 0 / 1 / 2) or "not available" (no such value:
    Windows 10, or a Windows 11 that was not installed fresh)."""
    v = (reg or _reg_default)(_KEY_CI, "VerifiedAndReputablePolicyState")
    if v is None:
        return "not available"
    return {0: "off", 1: "on", 2: "evaluation"}.get(int(v) if str(v).isdigit() else -1, f"state {v}")


def long_paths_enabled(reg: Optional[Reg] = None) -> Optional[bool]:
    v = (reg or _reg_default)(_KEY_FS, "LongPathsEnabled")
    if v is None:
        return None
    try:
        return bool(int(v))
    except (TypeError, ValueError):
        return None


class _MemoryStatusEx(ctypes.Structure):
    _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def memory_mb(k32: Any = None) -> Tuple[Optional[int], Optional[int]]:
    """(total, available) physical memory in MiB (GlobalMemoryStatusEx), or (None, None)."""
    try:
        k = k32 if k32 is not None else _kernel32()
        st = _MemoryStatusEx()
        st.dwLength = ctypes.sizeof(_MemoryStatusEx)
        if not k.GlobalMemoryStatusEx(ctypes.byref(st)):
            return None, None
        return int(st.ullTotalPhys // _MIB), int(st.ullAvailPhys // _MIB)
    except Exception:
        return None, None


def free_disk_gb(path: Any) -> Optional[float]:
    """Free space on the drive of ``path`` (its first existing ancestor), in GB (GiB)."""
    try:
        p = Path(os.path.abspath(str(path)))
        while not p.exists() and p.parent != p:
            p = p.parent
        return shutil.disk_usage(str(p)).free / _GIB
    except Exception:
        return None


def path_facts(path: Any, overridden: bool = False) -> Dict[str, Any]:
    """The app data path's length, spaces and non-ASCII letters (never the path itself)."""
    s = str(path or "")
    return {"chars": len(s), "spaces": " " in s, "non_ascii": any(ord(c) > 127 for c in s),
            "overridden": bool(overridden)}


class _ProcessMemoryCounters(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]


def peak_working_set_mb(k32: Any = None) -> Optional[int]:
    """This process's peak working set in MiB (K32GetProcessMemoryInfo), or None."""
    try:
        k = k32 if k32 is not None else _kernel32()
        k.GetCurrentProcess.restype = wintypes.HANDLE
        fn = k.K32GetProcessMemoryInfo
        fn.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
        fn.restype = wintypes.BOOL
        c = _ProcessMemoryCounters()
        c.cb = ctypes.sizeof(c)
        if not fn(k.GetCurrentProcess(), ctypes.byref(c), c.cb):
            return None
        return int(c.PeakWorkingSetSize // _MIB)
    except Exception:
        return None


# ------------------------------------------------------------------------------------ DLLs and file versions
class _FixedFileInfo(ctypes.Structure):
    _fields_ = [("dwSignature", wintypes.DWORD), ("dwStrucVersion", wintypes.DWORD),
                ("dwFileVersionMS", wintypes.DWORD), ("dwFileVersionLS", wintypes.DWORD),
                ("dwProductVersionMS", wintypes.DWORD), ("dwProductVersionLS", wintypes.DWORD),
                ("dwFileFlagsMask", wintypes.DWORD), ("dwFileFlags", wintypes.DWORD),
                ("dwFileOS", wintypes.DWORD), ("dwFileType", wintypes.DWORD), ("dwFileSubtype", wintypes.DWORD),
                ("dwFileDateMS", wintypes.DWORD), ("dwFileDateLS", wintypes.DWORD)]


def file_version(path: Any) -> Optional[str]:
    """The file version of a DLL or exe from its version resource ("14.50.35719.0"), or None. Reads the file's
    resource only (GetFileVersionInfoW); the DLL is not loaded."""
    try:
        ver = ctypes.WinDLL("version", use_last_error=True)
        p = str(path)
        size_fn = ver.GetFileVersionInfoSizeW
        size_fn.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
        size_fn.restype = wintypes.DWORD
        info_fn = ver.GetFileVersionInfoW
        info_fn.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
        info_fn.restype = wintypes.BOOL
        query = ver.VerQueryValueW
        query.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p),
                          ctypes.POINTER(wintypes.UINT)]
        query.restype = wintypes.BOOL
        handle = wintypes.DWORD(0)
        n = size_fn(p, ctypes.byref(handle))
        if not n:
            return None
        buf = ctypes.create_string_buffer(n)
        if not info_fn(p, 0, n, buf):
            return None
        ptr, length = ctypes.c_void_p(), wintypes.UINT(0)
        if not query(buf, "\\", ctypes.byref(ptr), ctypes.byref(length)) or not ptr.value:
            return None
        fi = ctypes.cast(ptr, ctypes.POINTER(_FixedFileInfo)).contents
        if fi.dwSignature != 0xFEEF04BD:
            return None
        ms, ls = fi.dwFileVersionMS, fi.dwFileVersionLS
        return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
    except Exception:
        return None


def short_version(v: Optional[str], parts: int = 2) -> str:
    """"14.50.35719.0" -> "14.50" (parts=2); None -> "?"."""
    if not v:
        return "?"
    return ".".join(str(v).split(".")[:parts])


def module_path(handle: Any) -> Optional[str]:
    """The full path Windows loaded a module from (GetModuleFileNameW)."""
    try:
        k = _kernel32()
        fn = k.GetModuleFileNameW
        fn.argtypes = [wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD]
        fn.restype = wintypes.DWORD
        buf = ctypes.create_unicode_buffer(32768)
        n = fn(wintypes.HMODULE(int(handle)), buf, 32768)
        return buf.value[:n] if n else None
    except Exception:
        return None


def loaded_modules() -> List[str]:
    """The full path of every module (exe and DLL) in this process (K32EnumProcessModules + GetModuleFileNameW).
    Empty when it cannot be read."""
    try:
        k = _kernel32()
        k.GetCurrentProcess.restype = wintypes.HANDLE
        enum = k.K32EnumProcessModules
        enum.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        enum.restype = wintypes.BOOL
        proc = k.GetCurrentProcess()
        count = 1024
        while True:
            arr = (wintypes.HMODULE * count)()
            needed = wintypes.DWORD(0)
            if not enum(proc, arr, ctypes.sizeof(arr), ctypes.byref(needed)):
                return []
            n = needed.value // ctypes.sizeof(wintypes.HMODULE)
            if n <= count:
                break
            count = n + 64
        out = []
        for h in arr[:n]:
            if h:
                p = module_path(h)
                if p:
                    out.append(p)
        return out
    except Exception:
        return []


def documents_dir() -> Path:
    """The user's Documents folder (B's userdirs when present), else ~\\Documents."""
    try:
        from . import userdirs
        return Path(userdirs.documents_dir())
    except Exception:
        return Path.home() / "Documents"


# ---------------------------------------------------------------------------------------------- collecting
def _card(info_probe: Optional[Callable[[], Any]]) -> Any:
    try:
        if info_probe is None:
            from . import gpuinfo
            return gpuinfo.probe()
        return info_probe()
    except Exception:
        return None


def collect(data_dir: Any, *, overridden: Optional[bool] = None, arch_flags: Any = None,
            card_probe: Optional[Callable[[], Any]] = None, card_line: Optional[Callable[..., str]] = None,
            reg: Optional[Reg] = None, k32: Any = None) -> Dict[str, Any]:
    """Every fact of SELFTEST.txt's header lines, gathered without raising (a fact that cannot be read is None or
    "?"). ``card_probe`` defaults to gpuinfo.probe (NVML through ctypes, no torch) and ``card_line`` to
    gpuinfo.selftest_line. The result holds no path."""
    if overridden is None:
        overridden = bool(os.environ.get("HDC_DATA_DIR", "").strip())
    cores, threads = cpu_counts(k32)
    total, free = memory_mb(k32)
    info = _card(card_probe)
    try:
        if card_line is None:
            from . import gpuinfo
            card_line = gpuinfo.selftest_line
        line = card_line(info, arch_flags) if info is not None else "Graphics card: not read"
    except Exception as e:
        line = f"Graphics card: not read ({type(e).__name__})"
    card: Dict[str, Any] = {}
    if info is not None:
        for k in ("nvml", "count", "name", "driver", "cuda_driver", "total_mb", "free_mb"):
            card[k] = getattr(info, k, None)
        cc = getattr(info, "cc", None)
        card["cc"] = list(cc) if cc is not None else None
    return {
        "windows": windows_info(reg, k32),
        "smart_app_control": smart_app_control(reg),
        "long_paths": long_paths_enabled(reg),
        "cpu": {"name": cpu_name(reg), "cores": cores, "threads": threads, "flags": cpu_flags(k32)},
        "ram": {"total_mb": total, "free_mb": free},
        "disk_free_gb": free_disk_gb(data_dir),
        "data_path": path_facts(data_dir, overridden),
        "card": card,
        "card_line": scrub(line),
    }


def _yes(v: Any) -> str:
    if v is None:
        return "?"
    return "yes" if v else "no"


def _gb(mb: Optional[int]) -> str:
    return "?" if mb is None else f"{mb / 1024.0:.1f}"


def lines(facts: Mapping[str, Any], numpy_flags: Optional[Mapping[str, Any]] = None) -> List[str]:
    """The Windows, CPU, RAM, App data path and Graphics card lines (rev 1 section 1.2, R2.3.4)."""
    w = facts.get("windows") or {}
    ver = " ".join(x for x in (str(w.get("product") or "Windows"), str(w.get("display_version") or "")) if x)
    out = [f"Windows: {ver}, build {w.get('build', '?')}.{w.get('ubr', 0)}, {w.get('arch', '?')} | "
           f"Smart App Control: {facts.get('smart_app_control', '?')} | "
           f"long paths enabled: {_yes(facts.get('long_paths'))}"]
    c = facts.get("cpu") or {}
    flags = merge_flags(c.get("flags") or {}, numpy_flags)
    cores = c.get("cores")
    threads = c.get("threads")
    out.append(f"CPU: {c.get('name') or '?'} | {cores if cores else '?'} cores, {threads if threads else '?'} threads | "
               + ", ".join(f"{name} {_yes(flags.get(name))}" for name, _code in PF_CODES))
    r = facts.get("ram") or {}
    disk = facts.get("disk_free_gb")
    out.append(f"RAM: {_gb(r.get('total_mb'))} GB total, {_gb(r.get('free_mb'))} GB free | "
               f"free disk (app data drive): {'?' if disk is None else f'{disk:.0f}'} GB")
    p = facts.get("data_path") or {}
    out.append(f"App data path: {p.get('chars', '?')} characters, spaces: {_yes(p.get('spaces'))}, "
               f"non-ASCII letters: {_yes(p.get('non_ascii'))} | overridden: {_yes(p.get('overridden'))}")
    out.append(str(facts.get("card_line") or "Graphics card: not read"))
    return out


def warnings(facts: Mapping[str, Any], numpy_flags: Optional[Mapping[str, Any]] = None) -> List[str]:
    """Rev 1 section 1.2 and R2.3.4: no AVX2; under 8 GB RAM or under 2.5 GB free; Windows below build 19045;
    Smart App Control on; an app data path over 100 characters; free card memory under 5 GB; a laptop card under
    8 GB."""
    out: List[str] = []
    c = facts.get("cpu") or {}
    flags = merge_flags(c.get("flags") or {}, numpy_flags)
    if not flags.get("AVX2"):
        out.append("the processor has no AVX2: reading will be slower")
    r = facts.get("ram") or {}
    total, free = r.get("total_mb"), r.get("free_mb")
    if total is not None and total / 1024.0 < RAM_WARN_TOTAL_GB:
        out.append(f"only {_gb(total)} GB of RAM: a check may be slow (8 GB or more is recommended)")
    if free is not None and free / 1024.0 < RAM_WARN_FREE_GB:
        out.append(f"only {_gb(free)} GB of RAM free: close other programs before a check")
    build = int((facts.get("windows") or {}).get("build") or 0)
    if 0 < build < TESTED_BUILD:
        out.append(f"Windows build {build} is older than 19045: not tested")
    if facts.get("smart_app_control") == "on":
        out.append("Smart App Control is on: it may block this unsigned app")
    p = facts.get("data_path") or {}
    if int(p.get("chars") or 0) > DATA_PATH_WARN_CHARS:
        out.append(f"the app data path has {p.get('chars')} characters (over 100): long file names may fail")
    card = facts.get("card") or {}
    if card.get("nvml") and int(card.get("count") or 0) > 0:
        free_mb = card.get("free_mb")
        if free_mb is not None and int(free_mb) < CARD_FREE_WARN_MB:
            out.append(f"only {_gb(int(free_mb))} GB of the graphics card's memory is free now: a check may wait")
        name = str(card.get("name") or "").lower()
        total_mb = card.get("total_mb")
        if (any(w in name for w in LAPTOP_WORDS) and total_mb is not None
                and int(total_mb) < LAPTOP_CARD_WARN_MB):
            out.append("a laptop graphics card with under 8 GB: large pages may be read on the processor")
    return out


def is_windows() -> bool:
    return sys.platform == "win32"
