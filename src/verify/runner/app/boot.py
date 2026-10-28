"""The app's boot, run by ``entry`` in every process before any heavy import (APP_SPEC rev 1 section 6.2, revision 2
R2.3.3).

Order (entry.main): parse the flags -> ``trace.arm`` (with --trace-opens) -> ``--data-dir`` -> for the card roles
(``--worker``, ``--cli``, ``--selftest-part card``) ``prepare_card_env`` BEFORE numpy or torch is imported ->
``isolate`` -> ``check_platform`` -> ``preload_runtime`` -> (the window: ``take_single_instance``) -> dispatch.

- ``prepare_card_env(index)``: ``gpuinfo.apply_card_env(os.environ, index)`` (Task E; no PYTORCH_*, TORCH_*,
  CUBLAS_*, CUDNN_*, CUDA_* or NVIDIA_TF32_OVERRIDE; CUDA_DEVICE_ORDER=PCI_BUS_ID, CUDA_VISIBLE_DEVICES=<index>),
  OPENBLAS_NUM_THREADS=1 unless set (as ``runner.__main__``), PYMUPDF_MESSAGE=fd:2. The window's ``spawn`` gives
  its children the same environment (``spawn.child_env``).
- ``isolate()``: frozen, every ``sys.path`` entry must lie under ``sys._MEIPASS`` (others are dropped and counted);
  the working folder becomes DATA (R2.1.2's ASCII copies are passed relative to it); TMP and TEMP become DATA\\tmp.
- ``check_platform()``: Windows build >= 17763 and a 64-bit process, else WINDOWS_TOO_OLD. The CPU flags
  (SSE4.2, AVX, AVX2, AVX512F) are recorded only: a CPU numpy cannot use shows up as numpy's import error or exit
  0xC000001D, both mapped to CPU_UNSUPPORTED (a feature flag that an older Windows does not know would otherwise
  refuse a good CPU).
- ``preload_runtime()``: frozen, the bundled VC++ DLLs (vcruntime140, vcruntime140_1, msvcp140, msvcp140_1 at the
  top of ``_internal``) and ``pyzbar\\msvcr120.dll`` are loaded by full path before numpy, torch or MuPDF, so an
  older Windows copy never wins. DEV: nothing.

DATA = ``HDC_DATA_DIR`` when set (tests, ``--data-dir``), else ``%LOCALAPPDATA%\\Hangeul Document Checker``.
Standard library only (ctypes). No personal data: the log lines carry counts and variable names, never paths.
"""
from __future__ import annotations

import os
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, MutableMapping, Optional

APP_DIR_NAME = "Hangeul Document Checker"
DATA_ENV = "HDC_DATA_DIR"
MIN_WINDOWS_BUILD = 17763                   # Windows 10 1809
VCRT = ("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll", "msvcp140_1.dll")
MSVCR120 = ("pyzbar", "msvcr120.dll")
MUTEX_NAME = "Local\\HangeulDocumentChecker"
ERROR_ALREADY_EXISTS = 183
CARD_ROLES = ("worker", "cli")
CARD_PARTS = ("card",)                      # --selftest-part card
WINDOWS_TOO_OLD = "WINDOWS_TOO_OLD"

_dlls: List[Any] = []                        # keeps the preloaded DLL handles alive


def is_card_role(role: str, part: Optional[str] = None) -> bool:
    return role in CARD_ROLES or (role == "selftest-part" and part in CARD_PARTS)


def data_dir(environ: Optional[Mapping[str, str]] = None) -> Path:
    """DATA: HDC_DATA_DIR, else %LOCALAPPDATA%\\Hangeul Document Checker (rev 1 section 4.1)."""
    env = os.environ if environ is None else environ
    override = env.get(DATA_ENV)
    if override:
        return Path(os.path.abspath(override))
    base = env.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    return Path(base) / APP_DIR_NAME


# ------------------------------------------------------------------------------------------- the environment
def _gpuinfo() -> Any:
    from . import gpuinfo                                  # Task E; standard library + ctypes only
    return gpuinfo


def prepare_card_env(index: Optional[int] = None, *, environ: Optional[MutableMapping[str, str]] = None,
                     gpuinfo: Any = None) -> Dict[str, Any]:
    """The card roles' environment, applied IN PLACE to ``os.environ`` (or ``environ``) before numpy or torch is
    imported. ``index`` None asks NVML (``gpuinfo.probe``; 0 when there is no card). Returns {"index", "removed"}
    (variable names only)."""
    from .spawn import apply_env, is_card_var
    env = os.environ if environ is None else environ
    gi = gpuinfo if gpuinfo is not None else _gpuinfo()
    if index is None:
        try:
            info = gi.probe()
            index = int(getattr(info, "index", 0) or 0) if getattr(info, "nvml", False) else 0
        except Exception:
            index = 0
    index = int(index)
    if hasattr(gi, "apply_card_env"):
        removed = list(gi.apply_card_env(env, index))
    else:
        removed = apply_env(env, gi.card_env(dict(env), index))
    keep = {"CUDA_DEVICE_ORDER", "CUDA_VISIBLE_DEVICES"}
    for k in [k for k in list(env.keys()) if is_card_var(k) and k.upper() not in keep]:
        del env[k]                                         # belt and braces: the same rule as card_env
        removed.append(k)
    if "OPENBLAS_NUM_THREADS" not in env:
        env["OPENBLAS_NUM_THREADS"] = "1"
    env["PYMUPDF_MESSAGE"] = "fd:2"
    return {"index": index, "removed": sorted(set(removed))}


# -------------------------------------------------------------------------------------------------- isolate
def _under(p: str, root: str) -> bool:
    a = os.path.normcase(os.path.abspath(p))
    r = os.path.normcase(os.path.abspath(root)).rstrip("\\/")
    return a == r or a.startswith(r + os.sep)


def isolate(*, frozen: Optional[bool] = None, meipass: Optional[str] = None, data: Optional[Any] = None,
            environ: Optional[MutableMapping[str, str]] = None, path_list: Optional[List[str]] = None,
            chdir: Callable[[str], None] = os.chdir, make_dirs: bool = True) -> Dict[str, Any]:
    """sys.path inside the bundle (frozen), the working folder = DATA, TMP / TEMP = DATA\\tmp. Returns
    {"dropped": n, "data_ascii": bool}."""
    frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    env = os.environ if environ is None else environ
    paths = sys.path if path_list is None else path_list
    dropped = 0
    if frozen:
        base = meipass if meipass is not None else getattr(sys, "_MEIPASS", None)
        if base:
            keep = [p for p in paths if p and _under(p, base)]
            dropped = len(paths) - len(keep)
            paths[:] = keep
    else:
        cwd = os.getcwd()                                  # DEV: "" (the old working folder) keeps its meaning
        paths[:] = [cwd if p == "" else p for p in paths]
    d = Path(data) if data is not None else data_dir(env)
    tmp = d / "tmp"
    if make_dirs:
        tmp.mkdir(parents=True, exist_ok=True)
    chdir(str(d))
    env["TMP"] = str(tmp)
    env["TEMP"] = str(tmp)
    if environ is None:
        tempfile.tempdir = None                            # gettempdir() reads TMP again
    try:
        str(d).encode("ascii")
        ascii_ok = True
    except UnicodeEncodeError:
        ascii_ok = False
    return {"dropped": dropped, "data_ascii": ascii_ok}


# --------------------------------------------------------------------------------------------- the platform
@dataclass(frozen=True)
class PlatformResult:
    ok: bool
    code: Optional[str]                     # WINDOWS_TOO_OLD or None
    windows_build: int
    is64: bool
    machine: str
    cpu: Dict[str, Optional[bool]] = field(default_factory=dict)    # sse42, avx, avx2, avx512f (None: unknown)


_PF = {"sse42": 38, "avx": 39, "avx2": 40, "avx512f": 41}           # IsProcessorFeaturePresent


def _norm_flag(name: Any) -> str:
    return "".join(ch for ch in str(name).lower() if ch.isalnum())


def _truthy(v: Any) -> Optional[bool]:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("yes", "true", "1", "on"):
            return True
        if s in ("no", "false", "0", "off"):
            return False
    return None


def cpu_flags(sysinfo_flags: Any = "auto") -> Dict[str, Optional[bool]]:
    """The four CPU flags: from ``sysinfo.cpu_flags()`` (Task D) when it answers in a form understood here (a
    mapping or a collection of flag names), else from ``IsProcessorFeaturePresent`` directly."""
    out: Dict[str, Optional[bool]] = {k: None for k in _PF}
    flags = sysinfo_flags
    if flags == "auto":
        try:
            from . import sysinfo
            flags = sysinfo.cpu_flags()
        except Exception:
            flags = None
    if isinstance(flags, Mapping):
        for k, v in flags.items():
            n = _norm_flag(k)
            if n in out:
                out[n] = _truthy(v)
    elif isinstance(flags, (list, tuple, set, frozenset)):
        names = {_norm_flag(x) for x in flags}
        for n in out:
            out[n] = n in names
    if any(v is None for v in out.values()):
        try:
            import ctypes
            k32 = ctypes.WinDLL("kernel32")
            for n, pf in _PF.items():
                if out[n] is None:
                    out[n] = bool(k32.IsProcessorFeaturePresent(pf))
        except Exception:
            pass
    return out


def check_platform(*, build: Optional[int] = None, is64: Optional[bool] = None,
                   cpu: Any = "auto") -> PlatformResult:
    """Windows build >= 17763 and a 64-bit process; the CPU flags are recorded, never refused here."""
    if build is None:
        try:
            build = int(sys.getwindowsversion().build)
        except Exception:
            build = 0
    if is64 is None:
        is64 = sys.maxsize > 2 ** 32
    machine = os.environ.get("PROCESSOR_ARCHITEW6432") or os.environ.get("PROCESSOR_ARCHITECTURE") or ""
    flags = cpu_flags(cpu)
    ok = build >= MIN_WINDOWS_BUILD and bool(is64)
    return PlatformResult(ok, None if ok else WINDOWS_TOO_OLD, int(build), bool(is64), machine, flags)


# --------------------------------------------------------------------------------------------- the runtimes
def preload_runtime(*, frozen: Optional[bool] = None, meipass: Optional[str] = None,
                    loader: Optional[Callable[[str], Any]] = None) -> Dict[str, str]:
    """Frozen: loads the bundled VC++ DLLs by full path. Returns {dll name: "bundled" | "missing" |
    "failed (<error type>)"}; empty in DEV."""
    frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    if not frozen:
        return {}
    base = meipass if meipass is not None else getattr(sys, "_MEIPASS", None)
    if not base:
        return {n: "missing" for n in VCRT + (MSVCR120[1],)}
    if loader is None:
        import ctypes
        loader = ctypes.WinDLL
    out: Dict[str, str] = {}
    for rel in [(n,) for n in VCRT] + [MSVCR120]:
        path = os.path.join(base, *rel)
        name = rel[-1]
        if not os.path.isfile(path):
            out[name] = "missing"
            continue
        try:
            _dlls.append(loader(path))
            out[name] = "bundled"
        except Exception as e:
            out[name] = f"failed ({type(e).__name__})"
    return out


def missing_runtime(result: Mapping[str, str]) -> Optional[str]:
    """The first DLL of ``preload_runtime``'s answer that is not loaded from the bundle, or None."""
    for name, status in result.items():
        if status != "bundled":
            return name
    return None


# ----------------------------------------------------------------------------------------- single instance
def take_single_instance(name: Optional[str] = None) -> Optional[int]:
    """The window's named mutex (rev 1 section 1.4; ``MUTEX_NAME`` by default). Returns its handle, or None when
    another window holds it. Without the Windows API (never on Windows) it returns -1, meaning "no check"."""
    name = MUTEX_NAME if name is None else name
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        k32.CreateMutexW.restype = ctypes.c_void_p
        k32.CloseHandle.argtypes = [ctypes.c_void_p]
        h = k32.CreateMutexW(None, False, name)
        err = ctypes.get_last_error()
    except Exception:
        return -1
    if not h:
        return -1
    if err == ERROR_ALREADY_EXISTS:
        k32.CloseHandle(h)
        return None
    return int(h)


def release_single_instance(handle: Optional[int]) -> None:
    if not handle or handle == -1:
        return
    try:
        import ctypes
        k32 = ctypes.WinDLL("kernel32")
        k32.CloseHandle.argtypes = [ctypes.c_void_p]
        k32.CloseHandle(ctypes.c_void_p(handle))
    except Exception:
        pass


def log_line(text: str) -> None:
    """One boot line on stderr (the worker log); never raises (a windowed process may have no stderr)."""
    try:
        if sys.stderr is not None:
            sys.stderr.write(f"boot: {text}\n")
            sys.stderr.flush()
    except Exception:
        pass
