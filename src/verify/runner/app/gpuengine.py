"""The app's reading engine on the graphics card (APP_SPEC revision 2: R2.1.2, R2.1.3, R2.1.5; Task T).

Two phases of one worker process use the card:

- Phase 1, the FIRST READ of every page: ``first_read`` runs the real ``gpuread.read_all`` in this process with the
  bot's EasyOCR reader on the card (``make_easyocr_reader``), under the process conditions gpuread had: torch's
  defaults (asserted by ``start_card``, never set), no allocator or cuBLAS settings, the same 3 GB cap. Two small
  wrappers sit around the reader (``GatedReader``): it waits for the bot's turn before every model call, copies a
  file whose path holds non-ASCII letters to an ASCII path first (``ascii_path``; OpenCV cannot open such paths on
  Windows), and retries ONCE at full size after the card ran out of memory (then gpuread's own ladder acts).
- Phase 2, the runner: the light reader (``glue_numpy.LightReader``'s numpy glue) with its two networks as torch
  modules on the card in strict float32 (``light_reader`` -> ``CardLightReader`` over two ``TorchSession``s), loaded
  exactly as the lab GPU worker loaded them (``load_models``: gpu_worker's / export_onnx.py's definitions verbatim).
  One model call that does not fit the cap even after waiting and freeing runs on the processor with the same
  weights (G4, owner question Q4); it is counted.

torch and easyocr are imported only inside functions, through injectable loaders (the unit tests pass fakes; the
default loaders refuse under ``HDC_TEST_NO_CARD``). No CPU OCR engine: onnxruntime is never imported here.

Errors: ``CardRefused(code, values)`` carries one of the six card codes of R2.3.1 (worker exit 6);
``card_error_code(e)`` maps any other exception (walking ``__cause__`` / ``__context__``) to a card code or None.

Contract for the other tasks (T defines it; W and D use it):
    CAP_GB, APP_ENGINE_LABEL, NO_CARD_ENV
    CardRefused(code, values)                                  .code, .values (names, GB numbers; never paths)
    start_card(info, gate, cap_gb=3.0, torch_loader=None, easyocr_loader=None) -> Card     (sets gate.on_pause)
    Card: .torch, .pause() -> reserved MB, .free(), .peaks() -> (alloc GB, reserved GB), .facts() -> dict
    first_read(card, bot, src, gate, store, emit=None, *, model_dir=None, user_dir=None, ascii_dir=None,
               reader_factory=None) -> dict (counts only)
    light_reader(card, gate, glue_data, model_dir, *, user_dir=None, ascii_dir=None) -> CardLightReader
        (.stats() -> {"card_calls", "cpu_calls", "oom_retries", "det": {...}, "rec": {...}}, .flags, .facts)
    TorchSession(kind, cpu_module, card, gate).run(None, {"image": x}) -> [np.ndarray]; .stats()
    card_error_code(e) -> Optional[str];  reading_note(fr) -> str (<= 200 characters)
    ascii_path(image, ascii_dir) -> image or an ASCII path (relative to the working folder when it can be)
    require_weights(model_dir): FileNotFoundError (file names only) when a .pth weight is missing; first_read's
        default reader and light_reader check it before easyocr runs (a damaged install)

No personal data: this module logs and returns counts, shapes, seconds and card facts only, never page text or paths.
"""
from __future__ import annotations

import copy
import gc
import itertools
import json
import logging
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np

LOG = logging.getLogger(__name__)

CAP_GB = 3.0                    # G5: the card-memory cap on EVERY card (= gpuread.CAP_GB)
APP_ENGINE_LABEL = f"bot EasyOCR on CUDA (app card gate, cap {CAP_GB:.1f} GB)"
NO_CARD_ENV = "HDC_TEST_NO_CARD"                       # = gpuinfo.NO_CARD_ENV: unit tests never reach the card
NO_CACHING_ENV = ("PYTORCH_NO_CUDA_MEMORY_CACHING", "PYTORCH_NO_HIP_MEMORY_CACHING")
# G8: variables that would make the first read differ from gpuread's process (allocator splitting, cuBLAS workspace,
# TF32 overrides). gpuinfo.card_env removes them at boot; start_card refuses when one is still there.
NOT_DEFAULT_ENV = ("PYTORCH_CUDA_ALLOC_CONF", "PYTORCH_ALLOC_CONF", "PYTORCH_HIP_ALLOC_CONF",
                   "CUBLAS_WORKSPACE_CONFIG", "NVIDIA_TF32_OVERRIDE", "TORCH_ALLOW_TF32_CUBLAS_OVERRIDE")
# torch 2.11's defaults, which gpuread's first read ran under (APP_SPEC R2 facts)
TORCH_DEFAULTS = {"cudnn_allow_tf32": True, "matmul_allow_tf32": False, "cudnn_deterministic": False,
                  "cudnn_benchmark": False}
STAGE_CARD, STAGE_FIRSTREAD, STAGE_LIGHT = "card", "firstread", "names"

# the six card codes (R2.3.1; = gpuinfo's constants)
NO_NVIDIA_CARD = "NO_NVIDIA_CARD"
DRIVER_TOO_OLD = "DRIVER_TOO_OLD"
CARD_NOT_SUPPORTED = "CARD_NOT_SUPPORTED"
CARD_TOO_SMALL = "CARD_TOO_SMALL"
LOW_CARD_MEMORY = "LOW_CARD_MEMORY"
CARD_FAILED = "CARD_FAILED"

# the light reader's two networks (= the ONNX models' inputs and outputs)
DET, REC = "det", "rec"
CHANNELS = {DET: 3, REC: 1}
REC_HEIGHT = 64                 # english_g2: the input height is fixed
REC_CLASSES = 97                # english_g2: 96 characters + the CTC blank
DET_MAPS = 2                    # CRAFT: the region and affinity score maps (last axis)
OUTPUT_NAMES = {DET: "score", REC: "logits"}
INPUT_NAME = "image"

NOTE_BASE = (f"app GPU build: first read on the graphics card (bot EasyOCR, {CAP_GB:.1f} GB cap); "
             "light reader on the card")
NOTE_MAX = 200
_MIB = 1024 * 1024
_GIB = 1024 ** 3


# --------------------------------------------------------------------------------------------------- errors
class CardRefused(Exception):
    """A card problem with one of the six codes (worker exit 6). ``values`` hold the card name, the driver version,
    GB numbers or the stage only (never paths); the window's message fills them in."""

    def __init__(self, code: str, values: Optional[Mapping[str, Any]] = None):
        self.code = str(code)
        self.values = {str(k): str(v) for k, v in dict(values or {}).items()}
        super().__init__(self.code)


def is_oom(e: BaseException) -> bool:
    """gpuread.is_oom: CUDA ran out of card memory (torch.OutOfMemoryError, or a RuntimeError saying so)."""
    if type(e).__name__ == "OutOfMemoryError":
        return True
    return isinstance(e, RuntimeError) and "out of memory" in str(e).lower()


_CUDA_TYPES = ("AcceleratorError", "CudaError", "CUDAError")
_CUDA_WORDS = ("cuda", "cudnn", "cublas", "cufft", "cusolver", "cusparse", "nvrtc", "nvjitlink", "nvidia driver",
               "device-side assert")
_NO_KERNEL = ("no kernel image",)
_OLD_DRIVER = ("driver version is insufficient", "insufficient driver", "driver on your system is too old",
               "unsupported toolchain")


def is_cuda_error(e: BaseException) -> bool:
    """A CUDA error other than out-of-memory (gpu_worker.is_cuda_error, widened to the CUDA libraries' words)."""
    if is_oom(e):
        return False
    if type(e).__name__ in _CUDA_TYPES:
        return True
    text = str(e).lower()
    return any(w in text for w in _CUDA_WORDS)


def _chain(e: BaseException, limit: int = 32) -> List[BaseException]:
    """``e`` and every exception behind it (``__cause__``, ``__context__``), each once, breadth first."""
    out: List[BaseException] = []
    seen = set()
    todo: List[Optional[BaseException]] = [e]
    while todo and len(out) < limit:
        x = todo.pop(0)
        if x is None or id(x) in seen:
            continue
        seen.add(id(x))
        out.append(x)
        todo.append(x.__cause__)
        todo.append(x.__context__)
    return out


def card_error_code(e: BaseException) -> Optional[str]:
    """The card code for an exception, or None when it is not a card problem (the worker then reports a crash).

    Walks ``__cause__`` and ``__context__``. A CardRefused gives its own code; "no kernel image" ->
    CARD_NOT_SUPPORTED; "driver version is insufficient" (and torch's "driver ... too old") -> DRIVER_TOO_OLD; any
    other non-OOM CUDA error -> CARD_FAILED; an out-of-memory error that escaped every retry (none should) ->
    LOW_CARD_MEMORY; cardgate.CardMemoryShort -> LOW_CARD_MEMORY."""
    chain = _chain(e)
    for x in chain:
        if isinstance(x, CardRefused):
            return x.code
    texts = [str(x).lower() for x in chain]
    if any(w in t for t in texts for w in _NO_KERNEL):
        return CARD_NOT_SUPPORTED
    if any(w in t for t in texts for w in _OLD_DRIVER):
        return DRIVER_TOO_OLD
    if any(is_cuda_error(x) for x in chain):
        return CARD_FAILED
    if any(is_oom(x) or type(x).__name__ == "CardMemoryShort" for x in chain):
        return LOW_CARD_MEMORY
    return None


# --------------------------------------------------------------------------------------------- the loaders
def _no_card() -> bool:
    return bool(os.environ.get(NO_CARD_ENV, ""))


def _import_torch():
    """The real torch (refused under HDC_TEST_NO_CARD: the unit tests inject a fake)."""
    if _no_card():
        raise CardRefused(NO_NVIDIA_CARD, {})
    import torch
    return torch


def _import_easyocr():
    """The real easyocr (refused under HDC_TEST_NO_CARD: the unit tests inject a fake)."""
    if _no_card():
        raise CardRefused(NO_NVIDIA_CARD, {})
    import easyocr
    return easyocr


def _gpuread():
    from .. import gpuread
    return gpuread


def _hooks_emit():
    from .. import hooks
    return hooks.emit


def _places():
    from .. import places
    return places.current()


def _env_names(environ: Mapping[str, str]) -> set:
    return {str(k).upper() for k in environ}


# ---------------------------------------------------------------------------------------------------- the card
class Card:
    """The started card: torch with its CUDA context, the 3 GB cap set. ``pause`` and ``free`` empty torch's card
    cache; ``peaks`` reads the peak allocated and reserved memory."""

    def __init__(self, torch: Any, *, cap_gb: float = CAP_GB, fraction: Optional[float] = None,
                 total_bytes: int = 0, name: str = "", cc: Tuple[int, int] = (0, 0), nvml_index: int = 0,
                 backend: str = "", arch_list: Sequence[str] = (), import_s: float = 0.0, cuda_s: float = 0.0,
                 notes: Sequence[str] = (), easyocr_loader: Optional[Callable[[], Any]] = None) -> None:
        self.torch = torch
        self.cap_gb = float(cap_gb)
        self.fraction = fraction
        self.total_bytes = int(total_bytes or 0)
        self.name = str(name)
        self.cc = (int(cc[0]), int(cc[1]))
        self.nvml_index = int(nvml_index)
        self.backend = str(backend)
        self.arch_list = list(arch_list)
        self.import_s = float(import_s)
        self.cuda_s = float(cuda_s)
        self.notes = list(notes)
        self.easyocr_loader = easyocr_loader or _import_easyocr
        self.pauses = 0

    def import_easyocr(self):
        return self.easyocr_loader()

    def pause(self) -> int:
        """Before a wait (the gate's on_pause): wait for the card's queued work, collect garbage, empty torch's card
        cache. Returns the memory torch still reserves, in MB (the weights, about 100 MB)."""
        cuda = self.torch.cuda
        self.pauses += 1
        cuda.synchronize()
        gc.collect()
        cuda.empty_cache()
        return int(cuda.memory_reserved() // _MIB)

    def free(self) -> None:
        """gpuread run_session's free(): gc, then torch.cuda.empty_cache (after an out-of-memory page, at the end)."""
        gc.collect()
        empty = getattr(self.torch.cuda, "empty_cache", None)
        if callable(empty):
            empty()

    def peaks(self) -> Tuple[float, float]:
        cuda = self.torch.cuda
        return (round(cuda.max_memory_allocated() / _GIB, 3), round(cuda.max_memory_reserved() / _GIB, 3))

    def facts(self) -> Dict[str, Any]:
        """For the ``card`` / ``ready`` event and the logs: versions, seconds, the card; no paths."""
        torch = self.torch
        return {"torch": str(getattr(torch, "__version__", "?")),
                "cuda": str(getattr(getattr(torch, "version", None), "cuda", None) or "?"),
                "seconds": round(self.import_s + self.cuda_s, 2), "import_s": round(self.import_s, 2),
                "cuda_s": round(self.cuda_s, 2), "name": self.name, "cc": f"{self.cc[0]}.{self.cc[1]}",
                "total_mb": int(self.total_bytes // _MIB), "cap_gb": self.cap_gb,
                "fraction": None if self.fraction is None else round(self.fraction, 4),
                "allocator_backend": self.backend, "notes": list(self.notes)}


def _arch_entries(arch_list: Iterable[str]) -> List[Tuple[str, Tuple[int, int], str]]:
    out = []
    for item in arch_list or ():
        s = str(item).strip().lower()
        for kind in ("sm", "compute"):
            head = kind + "_"
            if s.startswith(head):
                rest = s[len(head):]
                suffix = rest[-1] if rest and rest[-1].isalpha() else ""
                digits = rest[:-1] if suffix else rest
                if digits.isdigit() and len(digits) >= 2:
                    out.append((kind, (int(digits[:-1]), int(digits[-1])), suffix))
    return out


def arch_supported(cc: Sequence[int], arch_list: Iterable[str]) -> bool:
    """True when torch's compiled code runs on a card of compute capability ``cc`` (gpuinfo.arch_ok's rule: an
    ``sm_XY`` cubin runs on X.Y and later minors of the same major, so an 8.9 card runs sm_86 code; ``sm_XYa`` on X.Y
    only; ``compute_XY`` PTX on X.Y and newer)."""
    card = (int(cc[0]), int(cc[1]))
    for kind, built, suffix in _arch_entries(arch_list):
        if suffix == "a":
            if card == built:
                return True
        elif kind == "sm":
            if card[0] == built[0] and card[1] >= built[1]:
                return True
        elif card >= built:
            return True
    return False


def torch_flags(torch: Any) -> Dict[str, Any]:
    """The four precision flags (and the float32 matmul precision) as torch reports them now. Reads only."""
    out = {"cudnn_allow_tf32": bool(torch.backends.cudnn.allow_tf32),
           "matmul_allow_tf32": bool(torch.backends.cuda.matmul.allow_tf32),
           "cudnn_deterministic": bool(torch.backends.cudnn.deterministic),
           "cudnn_benchmark": bool(torch.backends.cudnn.benchmark)}
    precision = getattr(torch, "get_float32_matmul_precision", None)
    if callable(precision):
        out["float32_matmul_precision"] = str(precision())
    return out


def start_card(info: Any, gate: Any, cap_gb: float = CAP_GB, torch_loader: Optional[Callable[[], Any]] = None,
               easyocr_loader: Optional[Callable[[], Any]] = None) -> Card:
    """Import torch, start CUDA on the card and set the cap, exactly as gpuread's run_session did (R2.1.2).

    Refuses (CardRefused) BEFORE importing torch under HDC_TEST_NO_CARD (NO_NVIDIA_CARD) and when an allocator
    bypass switch is set (CARD_FAILED: the cap would not hold). Then: the gate's turn ("start"); CUDA available
    (else CARD_FAILED); the card's compute capability runnable by torch's arch list (else CARD_NOT_SUPPORTED with
    "which" old/new); ``set_per_process_memory_fraction(cap GiB / total * 0.999, 0)``; the allocator backend
    "native" (only it enforces the fraction; else CARD_FAILED); torch's precision flags at their defaults (asserted,
    never set: RuntimeError otherwise). A card name, total or capability that differs from NVML's is noted, not fatal.
    Sets ``gate.on_pause = card.pause``."""
    if _no_card():                                              # before any torch import
        raise CardRefused(NO_NVIDIA_CARD, {})
    names = _env_names(os.environ)
    if any(n in names for n in NO_CACHING_ENV):                 # torch would bypass the allocator that caps
        raise CardRefused(CARD_FAILED, {"stage": STAGE_CARD})
    bad = sorted(n for n in NOT_DEFAULT_ENV if n in names)
    if bad:
        raise RuntimeError("the card environment was not prepared (set: " + ", ".join(bad) + ")")
    t0 = time.perf_counter()
    torch = (torch_loader or _import_torch)()
    import_s = time.perf_counter() - t0
    gate.wait_turn("start")                                     # no CUDA context while the bot works
    t1 = time.perf_counter()
    cuda = torch.cuda
    if not cuda.is_available():
        raise CardRefused(CARD_FAILED, {"stage": STAGE_CARD})
    props = cuda.get_device_properties(0)                       # CUDA starts here (as in gpuread)
    name = str(getattr(props, "name", "") or getattr(info, "name", "") or "")
    cc = tuple(int(v) for v in cuda.get_device_capability(0))
    arch_list = [str(a) for a in cuda.get_arch_list()]
    if not arch_supported(cc, arch_list):
        built = [b for _k, b, _s in _arch_entries(arch_list)]
        which = "old" if built and cc < min(built) else "new"
        raise CardRefused(CARD_NOT_SUPPORTED, {"name": name, "which": which})
    total = int(props.total_memory)
    fraction = (float(cap_gb) * _GIB) / total * 0.999
    if not 0.0 < fraction < 1.0:
        raise CardRefused(CARD_TOO_SMALL, {"name": name, "total": f"{total / _GIB:.1f}"})
    cuda.set_per_process_memory_fraction(fraction, 0)
    backend = str(cuda.get_allocator_backend())
    if backend != "native":
        raise CardRefused(CARD_FAILED, {"stage": STAGE_CARD})
    flags = torch_flags(torch)
    wrong = sorted(k for k, v in TORCH_DEFAULTS.items() if flags.get(k) != v)
    if flags.get("float32_matmul_precision", "highest") != "highest":
        wrong.append("float32_matmul_precision")
    if wrong:
        raise RuntimeError("torch's precision flags are not its defaults: " + ", ".join(wrong))
    cuda_s = time.perf_counter() - t1
    notes = []
    if info is not None:
        nv_name = str(getattr(info, "name", "") or "")
        nv_total = int(getattr(info, "total_mb", 0) or 0)
        nv_cc = tuple(getattr(info, "cc", ()) or ())
        if nv_name and nv_name != name:
            notes.append("torch and NVML name different cards")
        if nv_total and abs(nv_total - total // _MIB) > 256:
            notes.append(f"torch reports {total // _MIB} MB, NVML {nv_total} MB")
        if nv_cc and tuple(int(v) for v in nv_cc) != cc:
            notes.append("torch and NVML report different compute capabilities")
    for n in notes:
        LOG.warning("start_card: %s", n)
    card = Card(torch, cap_gb=cap_gb, fraction=fraction, total_bytes=total, name=name, cc=cc,
                nvml_index=int(getattr(info, "index", 0) or 0), backend=backend, arch_list=arch_list,
                import_s=import_s, cuda_s=cuda_s, notes=notes, easyocr_loader=easyocr_loader)
    gate.on_pause = card.pause
    return card


# ----------------------------------------------------------------------------------------- non-ASCII paths
_COPY_NO = itertools.count(1)
_COPIES: Dict[Tuple[str, int, int, str], str] = {}


def default_ascii_dir() -> str:
    """Where ascii_path copies go when the caller names no folder: <temp>\\hdc-ascii-<pid> (safety.prepare points
    the temp folder into the app's data folder). The worker passes <DATA>\\tmp\\<job>\\ascii instead."""
    return os.path.join(tempfile.gettempdir(), f"hdc-ascii-{os.getpid()}")


def _ascii_suffix(path: str) -> str:
    suffix = os.path.splitext(path)[1]
    return suffix if suffix.isascii() and len(suffix) <= 8 else ""


def ascii_path(image: Any, ascii_dir: Optional[str] = None) -> Any:
    """``image`` unchanged unless it is a str naming an existing file with a non-ASCII letter in its path (cv2.imread
    cannot open such a path on Windows). That file is copied byte for byte to ``<ascii_dir>\\img-<nnnn><ext>`` and
    the copy's path is returned RELATIVE to the working folder (boot sets it to DATA, so it is ASCII even when DATA is
    not), or absolute when only that is ASCII. RuntimeError when neither is ASCII. Arrays and ASCII paths pass
    unchanged. A file copied before (same path, size and mtime) reuses its copy."""
    if not isinstance(image, str) or image.isascii():
        return image
    try:
        if not os.path.isfile(image):
            return image                                         # not a file: EasyOCR reports it as before
        st = os.stat(image)
    except (OSError, ValueError):
        return image
    folder = os.path.abspath(ascii_dir or default_ascii_dir())
    key = (os.path.normcase(os.path.abspath(image)), int(st.st_size), int(st.st_mtime_ns), os.path.normcase(folder))
    dest = _COPIES.get(key)
    if dest is None or not os.path.isfile(dest) or os.path.getsize(dest) != st.st_size:
        os.makedirs(folder, exist_ok=True)
        suffix = _ascii_suffix(image)
        while True:
            dest = os.path.join(folder, f"img-{next(_COPY_NO):04d}{suffix}")
            if not os.path.exists(dest):
                break
        shutil.copyfile(image, dest)
        _COPIES[key] = dest
    try:
        rel = os.path.relpath(dest)
    except ValueError:                                          # another drive than the working folder
        rel = ""
    if rel and rel.isascii():
        return rel
    if dest.isascii():
        return dest
    raise RuntimeError("the ASCII copy of a picture has no ASCII path (the working folder must be the data folder)")


# ---------------------------------------------------------------------------------- phase 1: the first read
class GatedReader:
    """The bot's EasyOCR reader inside the card gate (phase 1). ``readtext``: the gate's turn ("read"), the image
    path made ASCII, the call; on a card out-of-memory, ONCE: ``card.pause()``, the gate's turn ("oom"), the same
    call again at full size. A second out-of-memory error goes on to gpuread's ladder or the bot's own handling, as
    in DEV. Every other attribute is the inner reader's."""

    def __init__(self, inner: Any, gate: Any, card: Any, ascii_dir: Optional[str] = None) -> None:
        self.inner = inner
        self.gate = gate
        self.card = card
        self.ascii_dir = ascii_dir
        self.calls = 0
        self.oom_retries = 0

    def readtext(self, image, *args, **kw):
        self.gate.wait_turn("read")
        image = ascii_path(image, self.ascii_dir)
        self.calls += 1
        try:
            return self.inner.readtext(image, *args, **kw)
        except BaseException as e:                               # Cancelled and crashes go on unchanged
            if not is_oom(e):
                raise
        # outside the handler: the failed call's frames (and its card tensors) are released before the cache is
        self.oom_retries += 1
        self.card.pause()
        self.gate.wait_turn("oom")
        self.calls += 1
        return self.inner.readtext(image, *args, **kw)

    def __getattr__(self, name):
        inner = self.__dict__.get("inner")
        if inner is None:
            raise AttributeError(name)
        return getattr(inner, name)


def make_easyocr_reader(card: Card, model_dir: Any, user_dir: Any, easyocr_loader: Optional[Callable[[], Any]] = None):
    """The bot's reader (dv._ocr_reader: easyocr.Reader(["en"], gpu=True, verbose=False)) with the bundle's folders
    and downloading off. The same weights (easyocr checks their MD5), networks and defaults. CARD_FAILED when the
    reader is not on the card."""
    easyocr = (easyocr_loader or card.import_easyocr)()
    reader = easyocr.Reader(["en"], gpu=True, verbose=False, model_storage_directory=str(model_dir),
                            user_network_directory=str(user_dir), download_enabled=False)
    device = str(getattr(reader, "device", "?"))
    if not device.startswith("cuda"):
        raise CardRefused(CARD_FAILED, {"stage": STAGE_FIRSTREAD})
    return reader


def user_dir_for(model_dir: Any) -> str:
    """EasyOCR's user-network folder next to the model folder: <bundle>\\engine\\easyocr\\user_network in the app
    (shipped with a .keep), which in DEV is EasyOCR's own default (~\\.EasyOCR\\user_network)."""
    return os.path.join(os.path.dirname(os.path.abspath(str(model_dir))), "user_network")


WEIGHTS = ("craft_mlt_25k.pth", "english_g2.pth")      # the bot's two EasyOCR weights (R2.4.4)


def require_weights(model_dir: Any) -> None:
    """FileNotFoundError (file names only) when a weight file is not in ``model_dir``: checked BEFORE easyocr runs,
    which would otherwise first create its folders (a user-network folder beside a wrong model folder) and then fail
    on the missing file with downloading off. A damaged install (an antivirus quarantine) ends here."""
    missing = [w for w in WEIGHTS if not os.path.isfile(os.path.join(str(model_dir), w))]
    if missing:
        raise FileNotFoundError("the reading engine's weights are missing: " + ", ".join(missing))


def _never() -> None:
    raise RuntimeError("no page is left to read")


def _write_store(store: Path, data: Dict[str, Any]) -> None:
    """gpuread.write_json's atomic rewrite (<store>.tmp, then os.replace), into the app's data folder."""
    store.parent.mkdir(parents=True, exist_ok=True)
    tmp = store.with_name(store.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, store)


def _page_keys(d: Mapping[str, Any]) -> Dict[Tuple[str, int], Mapping[str, Any]]:
    out = {}
    for e in d.get("files") or []:
        if isinstance(e, dict):
            for p in e.get("pages") or []:
                if isinstance(p, dict) and isinstance(p.get("page"), int):
                    out[(str(e.get("name")), int(p["page"]))] = p
    return out


def _reused_keys(done: Mapping[str, Any], src: Any) -> set:
    """The (file, page) keys an earlier check's file gives this input (same names and page counts)."""
    n_by_name = {sf.name: int(sf.n_pages) for sf in src.files}
    keys = set()
    for e in done.get("files") or []:
        if isinstance(e, dict) and n_by_name.get(e.get("name")) == e.get("n_pages"):
            for p in e.get("pages") or []:
                if isinstance(p, dict) and isinstance(p.get("page"), int):
                    keys.add((str(e["name"]), int(p["page"])))
    return keys


def first_read(card: Card, bot: Any, src: Any, gate: Any, store: Any, emit: Optional[Callable[..., None]] = None,
               *, model_dir: Any = None, user_dir: Any = None, ascii_dir: Optional[str] = None,
               reader_factory: Optional[Callable[[], Any]] = None) -> Dict[str, Any]:
    """Phase 1 (R2.1.2): every page of ``src`` read by the bot's EasyOCR on the card through the REAL
    ``gpuread.read_all``, into ``store`` (runner-page-texts/1, engine APP_ENGINE_LABEL, rewritten atomically after
    every page; one ``page`` event per page read).

    Pages read by an earlier check of the same input (gpuread.load_done: same names, sha256 and page counts) are
    reused; with nothing pending no reader is made and read_all only rebuilds the file. Between pages the gate's
    turn ("page"); before every model call GatedReader's. In a ``finally``: ``bot.dv._reader = None``, the reader
    dropped, ``card.free()``. A Cancelled (or any error) leaves the partial file saved (read_all saves, then raises)
    and the next check resumes from it. Returns counts only (no text).
    ``model_dir`` defaults to places' models_dir, ``user_dir`` to the user_network folder beside it,
    ``reader_factory`` (tests) to make_easyocr_reader."""
    gr = _gpuread()
    emit = emit if emit is not None else _hooks_emit()
    store = Path(store)
    t_start = time.perf_counter()
    done = gr.load_done(store, src)
    pending = int(gr.pending_pages(src, done))
    total = sum(int(sf.n_pages) for sf in src.files)
    base = total - pending
    progress = {"handled": 0, "seen": _reused_keys(done, src) if pending else set(), "t": time.perf_counter()}

    def report(d: Dict[str, Any]) -> None:
        c = gr.counts(d)
        handled = min(pending, max(0, int(c["read"]) + int(c["skipped"]) - base))
        if handled <= progress["handled"]:
            return
        pages = _page_keys(d)
        new = [k for k in pages if k not in progress["seen"]]
        progress["seen"].update(new)
        now = time.perf_counter()
        seconds = sum(float(pages[k].get("seconds") or 0.0) for k in new) if new else now - progress["t"]
        progress["handled"], progress["t"] = handled, now
        emit("page", done=handled, of=pending, seconds=round(seconds, 3), where="card")

    def save(d: Dict[str, Any]) -> None:
        d["engine"] = APP_ENGINE_LABEL
        _write_store(store, d)
        if pending:
            report(d)

    dv = bot.dv
    gated: Optional[GatedReader] = None
    model_load_s = 0.0
    try:
        if pending == 0:                                         # complete from an earlier check: no card work
            result = gr.read_all(src, None, _never, done, save)
        else:
            if reader_factory is None:
                mdir = model_dir if model_dir is not None else _places().models_dir
                udir = user_dir if user_dir is not None else user_dir_for(mdir)
                require_weights(mdir)

                def reader_factory():
                    return make_easyocr_reader(card, mdir, udir)
            gate.wait_turn("read")
            t0 = time.perf_counter()
            gated = GatedReader(reader_factory(), gate, card, ascii_dir)
            model_load_s = time.perf_counter() - t0
            dv._reader = gated
            result = gr.read_all(src, dv, lambda: gate.wait_turn("page"), done, save, card.free)
        alloc_gb, reserved_gb = card.peaks()
    finally:
        dv._reader = None
        oom_retries = gated.oom_retries if gated is not None else 0
        gated = None                                             # the last reference: the reader goes with it
        card.free()
    c = gr.counts(result)
    downscaled = sum(1 for p in _page_keys(result).values() if p.get("downscaled"))
    return {"pages": c, "pending": pending, "reused": base, "complete": bool(result.get("complete")),
            "seconds_total": round(time.perf_counter() - t_start, 2),
            "read_seconds": float(result.get("seconds_total") or 0.0),
            "model_loaded": pending > 0, "model_load_s": round(model_load_s, 2),
            "downscaled": downscaled, "skipped": int(c.get("skipped", 0)), "oom_retries": int(oom_retries),
            "peak_alloc_gb": alloc_gb, "peak_reserved_gb": reserved_gb}


def reading_note(fr: Optional[Mapping[str, Any]]) -> str:
    """The note on the runner's READING line (<= 200 characters, no personal data)."""
    note = NOTE_BASE
    k = int((fr or {}).get("skipped") or 0)
    if k:
        note += f"; {k} page(s) left to the light reader (too large for the cap)"
    return note[:NOTE_MAX]


# ------------------------------------------------------------------------ phase 2: the light reader's networks
def strict_fp32(torch, environ: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Strict float32 on the card, set before any CUDA work; returns the flags as read back. Raises when they did
    not take. (CUBLAS_WORKSPACE_CONFIG is assigned by gpu_env.prepare_worker before torch loads.)"""
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False
    flags = strict_flags(torch, environ)
    if (not flags["cudnn_deterministic"] or flags["cudnn_benchmark"] or flags["matmul_allow_tf32"]
            or flags["cudnn_allow_tf32"]):
        raise RuntimeError("strict float32 flags did not take")
    return flags


def strict_flags(torch, environ: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    env = os.environ if environ is None else environ
    return {"cudnn_deterministic": bool(torch.backends.cudnn.deterministic),
            "cudnn_benchmark": bool(torch.backends.cudnn.benchmark),
            "matmul_allow_tf32": bool(torch.backends.cuda.matmul.allow_tf32),
            "cudnn_allow_tf32": bool(torch.backends.cudnn.allow_tf32),
            "float32_matmul_precision": str(torch.get_float32_matmul_precision()),
            "cublas_workspace_config": env.get("CUBLAS_WORKSPACE_CONFIG", ""),
            "cuda_alloc_conf": env.get("PYTORCH_CUDA_ALLOC_CONF", "")}
# (strict_fp32 and strict_flags are gpu_worker.py's, verbatim: the test compares them node for node. In the app no
# CUBLAS_WORKSPACE_CONFIG or allocator setting is made (G8); the two environment fields then read "".)


def export_modules(torch, easyocr, MODEL_DIR):
    """export_onnx.py's MeanPool, DetectorOut, RecognizerOut and load_reader, verbatim (the test compares them node
    for node). ``MODEL_DIR`` is export_onnx.py's module constant, here the parameter the four definitions close
    over: the bundle's (or the lab's) folder of the two .pth weights."""

    class MeanPool(torch.nn.Module):
        """AdaptiveAvgPool2d((None, 1)) on [B, T, C, H]: identical maths, exportable with dynamic T."""

        def forward(self, x):
            return x.mean(dim=3, keepdim=True)

    class DetectorOut(torch.nn.Module):
        """CRAFT returns (score maps, feature); easyocr only uses the score maps."""

        def __init__(self, net):
            super().__init__()
            self.net = net

        def forward(self, x):
            y, _feature = self.net(x)
            return y

    class RecognizerOut(torch.nn.Module):
        """english_g2 forward(input, text): text is unused for CTC; output are raw logits [B, T, 97]."""

        def __init__(self, net):
            super().__init__()
            self.net = net

        def forward(self, x):
            return self.net(x, None)

    def load_reader():
        return easyocr.Reader(["en"], gpu=False, quantize=False, verbose=False,
                              model_storage_directory=MODEL_DIR, download_enabled=False)

    return MeanPool, DetectorOut, RecognizerOut, load_reader


class _UserNetworkFolder:
    """easyocr with ``Reader`` bound to a user-network folder. EasyOCR mkdirs that folder and puts it on sys.path; its
    default (~\\.EasyOCR\\user_network) lies outside the app's data folder, where the guard refuses a new folder on a
    fresh PC. The folder holds no network of ours ("standard" english_g2 never reads it), so the models are the same."""

    def __init__(self, easyocr: Any, user_dir: str) -> None:
        self._easyocr = easyocr
        self._user_dir = str(user_dir)

    def Reader(self, *args, **kw):
        kw.setdefault("user_network_directory", self._user_dir)
        return self._easyocr.Reader(*args, **kw)

    def __getattr__(self, name):
        return getattr(self.__dict__["_easyocr"], name)


def load_models(torch, easyocr, model_dir: Any, user_dir: Any = None) -> Tuple[Any, Any, Dict[str, Any]]:
    """(detector, recogniser, facts) as export_onnx.main builds them, on the processor (gpu_worker.load_models with
    device "cpu"; TorchSession copies each to the card). Every parameter float32."""
    MeanPool, DetectorOut, RecognizerOut, load_reader = export_modules(
        torch, _UserNetworkFolder(easyocr, user_dir if user_dir is not None else user_dir_for(model_dir)),
        str(model_dir))
    reader = load_reader()
    det = reader.detector.eval()
    rec = reader.recognizer.eval()
    rec.AdaptiveAvgPool = MeanPool()          # export_onnx.main leaves the swap in place for the export
    det_m = DetectorOut(det).eval()
    rec_m = RecognizerOut(rec).eval()
    for m in (det_m, rec_m):
        for p in list(m.parameters()) + list(m.buffers()):
            if p.is_floating_point() and p.dtype != torch.float32:
                raise RuntimeError("a model tensor is not float32")
    facts = {"detector": type(det).__name__, "recognizer": type(rec).__name__,
             "num_class": int(rec.Prediction.out_features), "characters": len(reader.character)}
    del reader
    return det_m, rec_m, facts


class TorchSession:
    """One of the light reader's two networks on the card, with ONNX Runtime's ``run`` surface (``kind`` "det" or
    "rec"). The processor copy of the module is kept for the one exception of G4 (about 100 MB of RAM).

    ``run(output_names, feeds)``: the input checked as ONNX Runtime would (float32, rank 4, 3 channels for "det",
    1 channel at height 64 for "rec", no empty axis; else ValueError), a read-only or non-contiguous array copied;
    the gate's turn (``kind``); one forward pass on the card in inference mode (gpu_worker's Nets). On a card
    out-of-memory: ``card.pause()``, the gate's turn ("oom"), once more on the card; a second one runs THIS call on
    the processor module (counted, reason "oom"). Any other error is raised (the worker maps a CUDA error to
    CARD_FAILED). The output must be float32 with the input's batch: det rank 4 with 2 score maps, rec rank 3 with
    97 classes (else RuntimeError)."""

    def __init__(self, kind: str, cpu_module: Any, card: Any, gate: Any, *, allow_cpu: bool = True,
                 device: str = "cuda") -> None:
        if kind not in CHANNELS:
            raise ValueError("kind must be 'det' or 'rec'")
        self.kind = kind
        self.card = card
        self.gate = gate
        self.torch = card.torch
        self.device = device
        self.allow_cpu = bool(allow_cpu)
        self.cpu_module = cpu_module
        gate.wait_turn(kind)
        self.cuda_module = copy.deepcopy(cpu_module).to(device)
        self.calls_card = 0
        self.calls_cpu = 0
        self.seconds_card = 0.0
        self.seconds_cpu = 0.0
        self.oom_retries = 0
        self.cpu_reasons: Dict[str, int] = {}

    # ------------------------------------------------------------------ checks
    def _input(self, output_names: Any, feeds: Any) -> np.ndarray:
        if output_names is not None and list(output_names) != [OUTPUT_NAMES[self.kind]]:
            raise ValueError("unknown output name")
        if not isinstance(feeds, Mapping) or set(feeds) != {INPUT_NAME}:
            raise ValueError("the only input is 'image'")
        x = feeds[INPUT_NAME]
        if not isinstance(x, np.ndarray):
            raise ValueError("the input is not a numpy array")
        if x.dtype != np.float32 or x.ndim != 4:
            raise ValueError("the input must be a 4-d float32 array")
        if min(x.shape) < 1:
            raise ValueError("the input has an empty axis")
        if x.shape[1] != CHANNELS[self.kind]:
            raise ValueError("the input's channels do not fit the model")
        if self.kind == REC and x.shape[2] != REC_HEIGHT:
            raise ValueError("the recogniser's input height is 64")
        if not x.flags.writeable or not x.flags.c_contiguous:
            x = np.array(x, dtype=np.float32, order="C", copy=True)
        return x

    def _output(self, out: Any, batch: int) -> np.ndarray:
        if not isinstance(out, np.ndarray) or out.dtype != np.float32:
            raise RuntimeError("the network did not return float32")
        if out.ndim < 1 or out.shape[0] != batch:
            raise RuntimeError("the network's output batch differs from its input's")
        if self.kind == DET and (out.ndim != 4 or out.shape[3] != DET_MAPS):
            raise RuntimeError("the detector's output is not [B, H, W, 2]")
        if self.kind == REC and (out.ndim != 3 or out.shape[2] != REC_CLASSES):
            raise RuntimeError("the recogniser's output is not [B, T, 97]")
        return np.ascontiguousarray(out)

    # ------------------------------------------------------------------ the passes
    def _on_card(self, x: np.ndarray) -> np.ndarray:
        torch = self.torch
        t = time.perf_counter()
        try:
            with torch.inference_mode():
                xt = torch.from_numpy(x).to(self.device)
                y = self.cuda_module(xt)
                out = y.contiguous().cpu().numpy()               # .cpu() waits for the card
            return out
        finally:
            self.seconds_card += time.perf_counter() - t

    def _on_cpu(self, x: np.ndarray) -> np.ndarray:
        torch = self.torch
        t = time.perf_counter()
        try:
            with torch.inference_mode():
                y = self.cpu_module(torch.from_numpy(x))
                out = y.contiguous().numpy()
            return out
        finally:
            self.seconds_cpu += time.perf_counter() - t

    def _attempt(self, x: np.ndarray) -> Tuple[bool, Any]:
        try:
            return True, self._on_card(x)
        except BaseException as e:                               # Cancelled and CUDA errors go on unchanged
            if not is_oom(e):
                raise
        return False, None

    def run(self, output_names: Any, feeds: Any, run_options: Any = None) -> List[np.ndarray]:
        x = self._input(output_names, feeds)
        self.gate.wait_turn(self.kind)
        ok, out = self._attempt(x)
        if not ok:                                               # outside the handler: the failed frames are gone
            self.oom_retries += 1
            self.card.pause()
            self.gate.wait_turn("oom")
            ok, out = self._attempt(x)
            if not ok:
                if not self.allow_cpu:
                    raise CardRefused(LOW_CARD_MEMORY, {})
                self.card.free()
                out = self._on_cpu(x)
                self.calls_cpu += 1
                self.cpu_reasons["oom"] = self.cpu_reasons.get("oom", 0) + 1
                LOG.warning("TorchSession(%s): one call of shape %s ran on the processor (card memory)",
                            self.kind, tuple(x.shape))
                return [self._output(out, x.shape[0])]
        self.calls_card += 1
        return [self._output(out, x.shape[0])]

    def stats(self) -> Dict[str, Any]:
        return {"kind": self.kind, "calls_card": self.calls_card, "seconds_card": round(self.seconds_card, 3),
                "calls_cpu": self.calls_cpu, "seconds_cpu": round(self.seconds_cpu, 3),
                "oom_retries": self.oom_retries, "cpu_reasons": dict(self.cpu_reasons)}


_CARD_LIGHT_READER: Any = None


def card_light_reader_class():
    """CardLightReader: a subclass of glue_numpy.LightReader (imported here, not at module import) whose
    ``readtext`` applies ascii_path first. Built once."""
    global _CARD_LIGHT_READER
    if _CARD_LIGHT_READER is None:
        import glue_numpy

        class CardLightReader(glue_numpy.LightReader):
            """glue_numpy.LightReader on two TorchSessions; image paths made ASCII before reformat_input reads
            them with cv2.imread."""

            def __init__(self, det_session, rec_session, glue_data_path, ascii_dir=None):
                super().__init__(det_session, rec_session, glue_data_path)
                self.ascii_dir = ascii_dir
                self.flags: Dict[str, Any] = {}
                self.facts: Dict[str, Any] = {}

            def readtext(self, image, detail=1, **kw):
                return super().readtext(ascii_path(image, self.ascii_dir), detail, **kw)

            def stats(self) -> Dict[str, Any]:
                parts = {}
                for name in (DET, REC):
                    s = getattr(self, name, None)
                    st = s.stats() if callable(getattr(s, "stats", None)) else {}
                    parts[name] = st
                return {"card_calls": sum(int(p.get("calls_card", 0)) for p in parts.values()),
                        "cpu_calls": sum(int(p.get("calls_cpu", 0)) for p in parts.values()),
                        "oom_retries": sum(int(p.get("oom_retries", 0)) for p in parts.values()), **parts}

        _CARD_LIGHT_READER = CardLightReader
    return _CARD_LIGHT_READER


def __getattr__(name):                                           # gpuengine.CardLightReader, built on first use
    if name == "CardLightReader":
        return card_light_reader_class()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def light_reader(card: Card, gate: Any, glue_data: Any, model_dir: Any, *, user_dir: Any = None,
                 ascii_dir: Optional[str] = None):
    """Phase 2's reader (R2.1.3): strict float32 set and read back, the two networks loaded as export_onnx.py loads
    them (the processor copies), each moved to the card in a TorchSession, inside a CardLightReader on the glue data.
    The light reader's characters must agree with the recogniser's classes."""
    require_weights(model_dir)
    torch = card.torch
    flags = strict_fp32(torch)
    easyocr = card.import_easyocr()
    det_cpu, rec_cpu, facts = load_models(torch, easyocr, model_dir, user_dir)
    det = TorchSession(DET, det_cpu, card, gate)
    rec = TorchSession(REC, rec_cpu, card, gate)
    reader = card_light_reader_class()(det, rec, str(glue_data), ascii_dir=ascii_dir)
    if int(facts.get("num_class", -1)) != len(reader.character) + 1:
        raise RuntimeError("the glue data's characters do not fit the recogniser")
    reader.flags = flags
    reader.facts = facts
    return reader
