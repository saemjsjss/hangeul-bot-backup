"""The graphics card's facts, read with NVML through ctypes (APP_SPEC revision 2, R2.3.1; G5, G7).

Standard library only. This module never imports torch, so the window and the worker can show a plain
message (no NVIDIA card, driver too old, card not supported, card too small) at once, before any heavy
import. ``nvml.dll`` is loaded only by its FULL path in ``%SystemRoot%\\System32`` (the NVIDIA driver
puts it there), through an injectable ``loader`` so the unit tests use a fake DLL object.

Contract for the other tasks (E defines it; C, W, B and D use it):

- ``probe(loader=None) -> CardInfo`` never raises. Index 0 = the first NVIDIA card in PCI bus order
  (NVML's order); there is no choice of card.
- ``problems(info, arch_flags) -> [Problem]`` in the order NO_NVIDIA_CARD, DRIVER_TOO_OLD,
  CARD_NOT_SUPPORTED, CARD_TOO_SMALL. NO_NVIDIA_CARD comes alone (nothing else can be judged then).
  ``Problem.values`` keys: DRIVER_TOO_OLD {"driver"}; CARD_NOT_SUPPORTED {"name", "which": "old"|"new"};
  CARD_TOO_SMALL {"name", "total": "<GB, one decimal>"}; NO_NVIDIA_CARD {}. Values are the card name,
  the driver version and GB numbers only, never paths.
- The two other card codes are raised elsewhere: LOW_CARD_MEMORY (``cardgate.CardMemoryShort``, values
  {"free": gb_text(free_mb)} by convention) and CARD_FAILED (a CUDA error during the check).
- ``card_env(environ, index)`` returns a NEW, cleaned copy of the environment. NOTE: because it removes
  keys, ``os.environ.update(card_env(os.environ, i))`` would NOT delete them; use
  ``apply_card_env(os.environ, i)``, which deletes and sets in place.
- ``HDC_TEST_NO_CARD`` (any non-empty value): ``probe()`` and ``free_mb()`` without an injected
  ``loader`` report "no NVML" and load nothing. An injected loader is used as given (it is a fake).

No personal data: the card name and the driver version are hardware facts.
"""
from __future__ import annotations

import ctypes
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Tuple, Union

NVML_DLL = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "nvml.dll")   # full path only
CUDA_DRIVER_MIN = 12080    # the driver must support CUDA 12.8 (torch 2.11.0+cu128)
MIN_TOTAL_MB = 5800        # a "6 GB" card reports about 6144 MiB
NEED_FREE_MB = 4096        # cap 3.0 GB + CUDA context + headroom (gpuread NEED_FREE_GB 4.0)
NO_CARD_ENV = "HDC_TEST_NO_CARD"   # unit tests: probe() returns "no NVML" without loading the DLL

#: torch 2.11.0+cu128's compiled targets (APP_SPEC R2 facts; no PTX). Used only when a caller passes no
#: arch flags (DEV without BUILD.json); the frozen app passes BUILD.json's ``arch_flags``.
TORCH_ARCH_FLAGS: Tuple[str, ...] = ("sm_75", "sm_80", "sm_86", "sm_90", "sm_100", "sm_120")

# The six card codes (worker exit 6). The first four come from problems(); the window shows the first.
NO_NVIDIA_CARD = "NO_NVIDIA_CARD"
DRIVER_TOO_OLD = "DRIVER_TOO_OLD"
CARD_NOT_SUPPORTED = "CARD_NOT_SUPPORTED"
CARD_TOO_SMALL = "CARD_TOO_SMALL"
LOW_CARD_MEMORY = "LOW_CARD_MEMORY"
CARD_FAILED = "CARD_FAILED"
CARD_CODES: Tuple[str, ...] = (NO_NVIDIA_CARD, DRIVER_TOO_OLD, CARD_NOT_SUPPORTED, CARD_TOO_SMALL,
                               LOW_CARD_MEMORY, CARD_FAILED)
HARD_CODES: Tuple[str, ...] = CARD_CODES[:4]      # the ones problems() reports: Check stays disabled

_MIB = 1024 * 1024
_NAME_BUF = 96             # NVML_DEVICE_NAME_V2_BUFFER_SIZE
_DRIVER_BUF = 80           # NVML_SYSTEM_DRIVER_VERSION_BUFFER_SIZE
_NVML_SUCCESS = 0

Loader = Callable[[str], Any]


# ------------------------------------------------------------------------------------------- data types
@dataclass(frozen=True)
class CardInfo:
    nvml: bool                      # NVML loaded, initialised and the device count read
    error: str = ""                 # a short scrubbed reason when nvml is False (never a path)
    count: int = 0                  # NVIDIA cards NVML sees
    index: int = 0                  # 0 = the first NVIDIA card in PCI bus order (NVML's order)
    name: str = ""                  # "NVIDIA GeForce RTX 0000"
    driver: str = ""                # "000.00"
    cuda_driver: int = 0            # nvmlSystemGetCudaDriverVersion_v2, e.g. 12090
    cc: Tuple[int, int] = (0, 0)    # nvmlDeviceGetCudaComputeCapability
    total_mb: int = 0               # nvmlDeviceGetMemoryInfo, MiB
    free_mb: int = 0


@dataclass(frozen=True)
class Problem:
    code: str
    values: Dict[str, str] = field(default_factory=dict)    # card name, driver, GB numbers only


class _NvmlMemory(ctypes.Structure):          # nvmlMemory_t (v1)
    _fields_ = [("total", ctypes.c_ulonglong), ("free", ctypes.c_ulonglong), ("used", ctypes.c_ulonglong)]


class _NvmlError(Exception):
    def __init__(self, fn: str, how: str):
        super().__init__(fn)
        self.reason = f"{fn} {how}"


# -------------------------------------------------------------------------------------------- loading
def _system_loader(path: str) -> Any:
    """The real loader: the DLL by its full path only (no search of PATH or the app folder)."""
    if not os.path.isfile(path):
        raise FileNotFoundError("nvml.dll")
    return ctypes.CDLL(path)


def _no_card_env() -> bool:
    return bool(os.environ.get(NO_CARD_ENV, ""))


def _open(loader: Optional[Loader]) -> Tuple[Any, str]:
    """(dll, "") or (None, scrubbed reason). Exception texts are never kept: they may hold a path."""
    if loader is None:
        if _no_card_env():
            return None, f"no NVML ({NO_CARD_ENV} is set)"
        loader = _system_loader
    try:
        dll = loader(NVML_DLL)
    except FileNotFoundError:
        return None, "nvml.dll not found"
    except Exception as e:                        # OSError (a broken DLL), anything a loader raises
        return None, f"nvml.dll could not be loaded ({type(e).__name__})"
    if dll is None:
        return None, "nvml.dll not found"
    return dll, ""


def _call(dll: Any, fn: str, *args: Any) -> None:
    try:
        func = getattr(dll, fn)
    except AttributeError:
        raise _NvmlError(fn, "is missing") from None
    try:
        rc = func(*args)
    except Exception as e:
        raise _NvmlError(fn, f"failed ({type(e).__name__})") from None
    if rc != _NVML_SUCCESS:
        raise _NvmlError(fn, f"failed (NVML error {int(rc)})")


def _shutdown(dll: Any) -> None:
    try:
        getattr(dll, "nvmlShutdown")()
    except Exception:
        pass


def _text(buf: Any, limit: int) -> str:
    raw = buf.value if hasattr(buf, "value") else bytes(buf)
    if isinstance(raw, bytes):
        raw = raw.split(b"\0", 1)[0].decode("utf-8", "replace")
    return "".join(ch for ch in str(raw) if ch.isprintable()).strip()[:limit]


def _handle(dll: Any, index: int) -> ctypes.c_void_p:
    handle = ctypes.c_void_p()
    _call(dll, "nvmlDeviceGetHandleByIndex_v2", ctypes.c_uint(int(index)), ctypes.pointer(handle))
    return handle


def _memory(dll: Any, handle: ctypes.c_void_p) -> Tuple[int, int]:
    mem = _NvmlMemory()
    _call(dll, "nvmlDeviceGetMemoryInfo", handle, ctypes.pointer(mem))
    return int(mem.total) // _MIB, int(mem.free) // _MIB


def _cuda_driver(dll: Any) -> int:
    """The highest CUDA version the driver supports (12090 = 12.9); 0 when it cannot be read (an old
    driver may lack the _v2 call, then the plain one is tried)."""
    for fn in ("nvmlSystemGetCudaDriverVersion_v2", "nvmlSystemGetCudaDriverVersion"):
        value = ctypes.c_int(0)
        try:
            _call(dll, fn, ctypes.pointer(value))
        except _NvmlError:
            continue
        return int(value.value)
    return 0


# ------------------------------------------------------------------------------------------- the probe
def probe(loader: Optional[Loader] = None) -> CardInfo:
    """nvmlInit_v2 ... nvmlShutdown; the facts of card 0 in PCI order. Never raises: a failure gives
    ``CardInfo(nvml=False, error=<scrubbed reason>)``. The driver string, the CUDA driver version and
    the name are soft (a failure leaves "" / 0); the count, handle, compute capability and memory are
    required."""
    try:
        dll, why = _open(loader)
        if dll is None:
            return CardInfo(nvml=False, error=why)
        try:
            _call(dll, "nvmlInit_v2")
        except _NvmlError as e:
            return CardInfo(nvml=False, error=e.reason)
        try:
            return _probe_initialised(dll)
        finally:
            _shutdown(dll)
    except Exception as e:                        # belt and braces: probe never raises
        return CardInfo(nvml=False, error=f"NVML probe failed ({type(e).__name__})")


def _probe_initialised(dll: Any) -> CardInfo:
    driver = ""
    buf = ctypes.create_string_buffer(_DRIVER_BUF)
    try:
        _call(dll, "nvmlSystemGetDriverVersion", buf, ctypes.c_uint(_DRIVER_BUF))
        driver = _text(buf, 32)
    except _NvmlError:
        pass
    cuda = _cuda_driver(dll)
    count = ctypes.c_uint(0)
    try:
        _call(dll, "nvmlDeviceGetCount_v2", ctypes.pointer(count))
    except _NvmlError as e:
        return CardInfo(nvml=False, error=e.reason, driver=driver, cuda_driver=cuda)
    n = int(count.value)
    if n < 1:
        return CardInfo(nvml=True, count=0, driver=driver, cuda_driver=cuda)
    index = 0
    try:
        handle = _handle(dll, index)
        name = ""
        nbuf = ctypes.create_string_buffer(_NAME_BUF)
        try:
            _call(dll, "nvmlDeviceGetName", handle, nbuf, ctypes.c_uint(_NAME_BUF))
            name = _text(nbuf, _NAME_BUF)
        except _NvmlError:
            pass
        major, minor = ctypes.c_int(0), ctypes.c_int(0)
        _call(dll, "nvmlDeviceGetCudaComputeCapability", handle, ctypes.pointer(major), ctypes.pointer(minor))
        total, free = _memory(dll, handle)
    except _NvmlError as e:
        return CardInfo(nvml=False, error=e.reason, count=n, driver=driver, cuda_driver=cuda)
    return CardInfo(nvml=True, error="", count=n, index=index, name=name, driver=driver, cuda_driver=cuda,
                    cc=(int(major.value), int(minor.value)), total_mb=total, free_mb=free)


def free_mb(index: int = 0, loader: Optional[Loader] = None) -> Optional[int]:
    """Free memory of card ``index`` in MiB, or None when it cannot be read (no NVML, the test switch,
    any NVML error). Never raises. Used by the card gate's memory wait."""
    try:
        dll, _why = _open(loader)
        if dll is None:
            return None
        try:
            _call(dll, "nvmlInit_v2")
        except _NvmlError:
            return None
        try:
            return _memory(dll, _handle(dll, index))[1]
        except _NvmlError:
            return None
        finally:
            _shutdown(dll)
    except Exception:
        return None


# ------------------------------------------------------------------------------------- requirements
_ARCH_RE = re.compile(r"^(sm|compute)_(\d{2,4})([af]?)$")


def _arch_entries(arch_flags: Union[None, str, Iterable[str]]) -> List[Tuple[str, Tuple[int, int], str]]:
    """[(kind, (major, minor), suffix)] of every well-formed ``sm_XY`` / ``compute_XY`` entry. A string
    is split on spaces, commas and semicolons."""
    if not arch_flags:
        return []
    items = re.split(r"[\s,;]+", arch_flags) if isinstance(arch_flags, str) else list(arch_flags)
    out = []
    for item in items:
        m = _ARCH_RE.match(str(item).strip().lower())
        if m:
            digits = m.group(2)
            out.append((m.group(1), (int(digits[:-1]), int(digits[-1])), m.group(3)))
    return out


def arch_ok(cc: Sequence[int], arch_flags: Union[None, str, Iterable[str]]) -> bool:
    """True when the build's compiled code runs on a card of compute capability ``cc``:
    - ``sm_XY`` (a cubin) runs on X.Y and on every later minor of the SAME major (CUDA's binary
      compatibility: sm_86 code runs on an 8.9 card, which is how torch's own check judges it);
      ``sm_XYa`` runs on X.Y only;
    - ``compute_XY`` (PTX) runs on every card of X.Y or newer (``compute_XYa``: X.Y only).
    No usable entry gives False."""
    try:
        card = (int(cc[0]), int(cc[1]))
    except (TypeError, ValueError, IndexError):
        return False
    for kind, built, suffix in _arch_entries(arch_flags):
        if suffix == "a":
            if card == built:
                return True
        elif kind == "sm":
            if card[0] == built[0] and card[1] >= built[1]:
                return True
        elif card >= built:
            return True
    return False


def arch_range(arch_flags: Union[None, str, Iterable[str]] = None) -> Tuple[Tuple[int, int], Tuple[int, int]]:
    """(lowest, highest) compute capability named by the arch flags (TORCH_ARCH_FLAGS when none):
    ((7, 5), (12, 0)) for this build."""
    entries = _arch_entries(arch_flags) or _arch_entries(TORCH_ARCH_FLAGS)
    ccs = [built for _kind, built, _suffix in entries]
    return min(ccs), max(ccs)


def gb_text(mb: Optional[int]) -> str:
    """MiB as GB with one decimal ("8.0"); "?" when unknown."""
    if mb is None:
        return "?"
    return f"{int(mb) / 1024.0:.1f}"


def cc_text(cc: Sequence[int]) -> str:
    return f"{int(cc[0])}.{int(cc[1])}"


def cuda_text(version: int) -> str:
    """NVML's CUDA version integer as text: 12090 -> "12.9"; 0 -> "?"."""
    v = int(version or 0)
    if v <= 0:
        return "?"
    return f"{v // 1000}.{(v % 1000) // 10}"


def problems(info: CardInfo, arch_flags: Union[None, str, Iterable[str]]) -> List[Problem]:
    """The hard card problems in the order NO_NVIDIA_CARD, DRIVER_TOO_OLD, CARD_NOT_SUPPORTED,
    CARD_TOO_SMALL; empty when the card can be used. The window shows the first. Without arch flags
    (DEV) torch 2.11.0+cu128's list is used."""
    if not info.nvml or info.count < 1:
        return [Problem(NO_NVIDIA_CARD, {})]
    out: List[Problem] = []
    if int(info.cuda_driver) < CUDA_DRIVER_MIN:
        out.append(Problem(DRIVER_TOO_OLD, {"driver": info.driver}))
    flags = arch_flags if _arch_entries(arch_flags) else TORCH_ARCH_FLAGS
    if not arch_ok(info.cc, flags):
        lowest, _highest = arch_range(flags)
        which = "old" if tuple(info.cc) < lowest else "new"
        out.append(Problem(CARD_NOT_SUPPORTED, {"name": info.name, "which": which}))
    if int(info.total_mb) < MIN_TOTAL_MB:
        out.append(Problem(CARD_TOO_SMALL, {"name": info.name, "total": gb_text(info.total_mb)}))
    return out


# ------------------------------------------------------------------------------------ the environment
_REMOVE_PREFIXES = ("PYTORCH_", "TORCH_", "CUBLAS_", "CUDNN_", "CUDA_")
_REMOVE_NAMES = ("NVIDIA_TF32_OVERRIDE",)


def _removed(key: str) -> bool:
    k = str(key).upper()
    return k.startswith(_REMOVE_PREFIXES) or k in _REMOVE_NAMES


def card_env(environ: Mapping[str, str], index: int) -> Dict[str, str]:
    """A NEW environment for the worker roles (R2.1.2): ``environ`` without PYTORCH_*, TORCH_*,
    CUBLAS_*, CUDNN_*, CUDA_* (CUDA_PATH* too: the CUDA DLLs come from the app folder) and
    NVIDIA_TF32_OVERRIDE, matched case-insensitively; plus CUDA_DEVICE_ORDER=PCI_BUS_ID and
    CUDA_VISIBLE_DEVICES=<index>. Nothing else is set. ``environ`` is not changed."""
    out = {k: v for k, v in environ.items() if not _removed(k)}
    out["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
    out["CUDA_VISIBLE_DEVICES"] = str(int(index))
    return out


def apply_card_env(environ: MutableMapping[str, str], index: int) -> List[str]:
    """``card_env`` applied IN PLACE (for ``os.environ`` at boot): deletes the removed keys and sets the
    two CUDA_* keys. Returns the removed key names (names only: values may be paths), sorted."""
    gone = sorted(k for k in list(environ.keys()) if _removed(k))
    for k in gone:
        del environ[k]
    environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
    environ["CUDA_VISIBLE_DEVICES"] = str(int(index))
    return gone


# ------------------------------------------------------------------------------------------ reporting
def card_fields(info: CardInfo) -> Dict[str, Any]:
    """The fields of the ``card`` / ``found`` event (R2.3.3): name, cc, driver, total_mb, free_mb."""
    return {"name": info.name, "cc": cc_text(info.cc), "driver": info.driver,
            "total_mb": int(info.total_mb), "free_mb": int(info.free_mb)}


def selftest_line(info: CardInfo, arch_flags: Union[None, str, Iterable[str]] = None) -> str:
    """SELFTEST.txt's card line (R2.3.4), for example
    "Graphics card: NVIDIA GeForce RTX 0000 | 8.0 GB total, 6.0 GB free | driver 000.00 (CUDA 12.9) |
    compute 12.0 (this build: 7.5-12.0)". No paths."""
    if not info.nvml:
        return f"Graphics card: none found ({info.error or 'NVML not available'})"
    if info.count < 1:
        return (f"Graphics card: none found (the NVIDIA driver reports no card) | driver {info.driver or '?'} "
                f"(CUDA {cuda_text(info.cuda_driver)})")
    lowest, highest = arch_range(arch_flags)
    line = (f"Graphics card: {info.name or 'NVIDIA card'} | {gb_text(info.total_mb)} GB total, "
            f"{gb_text(info.free_mb)} GB free | driver {info.driver or '?'} (CUDA {cuda_text(info.cuda_driver)}) | "
            f"compute {cc_text(info.cc)} (this build: {cc_text(lowest)}-{cc_text(highest)})")
    if info.count > 1:
        line += f" | {info.count} NVIDIA cards: the first in PCI order is used"
    return line
