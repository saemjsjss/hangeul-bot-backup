"""The worker's body: one check on the graphics card (APP_SPEC rev 2 R2.1.1, rev 1 section 2).

    run_job(job, listener) -> exit code

The order (R2.1.1; nothing touches the card before the guard is armed):
  1. hooks.LISTENER = (listener, recorded); the input pre-checks of rev 1 section 3.3 (ingest's order: exists ->
     type -> folder files -> pymupdf.open -> needs_pass -> page count; PIL for pictures);
  2. safety.prepare(out, cache_root, input): the APP guard, armed BEFORE any card work; its input snapshot is kept;
  3. gpuinfo.probe() and gpuinfo.problems(info, version.ARCH_FLAGS): a problem -> error event, exit 6;
  4. gate = cardgate.CardGate(job.gpu.lock_paths, hooks.emit, free_mb=...);
  5. stage "card": gate.wait_turn("start"), gate.wait_memory(need), card = gpuengine.start_card(info, gate);
  6. src = ingest.open_input(input); bot = botimport.load(cache_root);
  7. stage "firstread": gpuengine.first_read(card, bot, src, gate, store=<cache_root>\\pagetexts\\<case_id>.json);
  8. reader = gpuengine.light_reader(...); engine.READER_FACTORY = lambda threads=0: reader;
  9. pipeline.run(..., page_texts=store, reading_note=gpuengine.reading_note(fr)); during it the Recorder asks the
     gate for its turn ("stage") at the start of every runner stage, so the app also waits at stage boundaries
     while the bot's lock exists (its processor work does not compete with the bot's check);
 10. the kept input snapshot compared again (INPUT_CHANGED); resultcode.code(r); summary.pdf; checkfacts.json;
     the done event with its gpu block.
first_read and light_reader get ascii_dir = <places.temp_dir>\\<job>\\ascii (R2.1.2); the folder is removed at the
job's end, as are engine.READER_FACTORY and hooks.LISTENER put back. The "hello" event is entry.py's.

Exit codes (rev 1 section 2 + R2.3.3): 0 done; 3 done with STOP; 2 refused (an error event was sent: a bad
input, an unsafe report folder, the input changed, a wrong job = BAD_ARGS); 1 crash (an error event was sent;
CRASH, LOW_MEMORY or DISK_FULL); 4 cancelled ("cancelled" event); 5 start-up failure (STARTUP_FAILED: a module or
DLL that cannot be loaded); 6 a card problem (the six codes of R2.3.1). After exit 1 or 6, checkfacts.json holds the
error's code, stage and exception type plus what is known of the card. The mapping order: refusals and the card
gate's / engine's own exceptions first, then LOW_MEMORY (MemoryError, "not enough memory", Windows errors 8, 14 and
1455 "the paging file is too small"), then STARTUP_FAILED (ImportError; Windows errors 126, 127, 193 and 1114 loading
a DLL), then a CUDA error (whose text is never read off an OSError or ImportError: theirs names a DLL's path, and
"cublasLt64_12.dll" is not a card failure), then the input / guard refusals, DISK_FULL and CRASH.

Events go through hooks.emit (the listener turns them into protocol lines): stage (card, firstread), card (found,
ready), error (code, values, text), cancelled, done. No personal data in this file; the error text names no file
or folder (paths become <path>, a file is named by the runner's label "the bundle" / "file k of n in the folder
(sorted by name)") and an unexpected exception is reported by its type and stage only.
"""
from __future__ import annotations

import contextlib
import ctypes
import datetime as dt
import importlib
import inspect
import json
import os
import re
import shutil
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

VERBOSE = True                       # progress and error lines on stderr (the worker log); no personal data

EXIT_OK, EXIT_CRASH, EXIT_REFUSED, EXIT_STOP, EXIT_CANCELLED, EXIT_STARTUP, EXIT_CARD = 0, 1, 2, 3, 4, 5, 6
CARD_CODES = ("NO_NVIDIA_CARD", "DRIVER_TOO_OLD", "CARD_NOT_SUPPORTED", "CARD_TOO_SMALL", "LOW_CARD_MEMORY",
              "CARD_FAILED")
BAD_ARGS = "BAD_ARGS"                # = protocol.BAD_ARGS: a wrong job (program, date, a missing field)
ASCII_DIR = "ascii"                  # <temp_dir>\<job>\ascii: ASCII copies of non-ASCII picture paths (R2.1.2)
NEED_FREE_MB_DEFAULT = 4096          # = gpuinfo.NEED_FREE_MB (used when neither the job nor gpuinfo gives one)
CAP_GB_DEFAULT = 3.0
MAX_PATH = 260                       # an input path this long is refused (rev 1 section 1.4)
PROGRAMS = ("KLP", "EAP", "BACHELOR", "MASTER")
PAGETEXTS_DIR = "pagetexts"
SUMMARY_PDF = "summary.pdf"
CHECKFACTS = "checkfacts.json"
CC_RANGE = ((7, 5), (12, 0))         # this torch build's compute capabilities (sm_75 ... sm_120)

RUNNER_PKG = __package__.rsplit(".", 1)[0] if __package__ else "src.verify.runner"
APP_PKG = __package__ or "src.verify.runner.app"
_RUNNER_MODS = ("hooks", "safety", "ingest", "botimport", "engine", "pipeline", "places", "resultcode", "report",
                "model")
_APP_MODS = ("gpuinfo", "cardgate", "gpuengine", "version", "summarypdf", "checkfacts")

# text that marks a CUDA / card error in an exception's type or message (case-insensitive)
_CUDA_MARKS = ("cuda", "cudnn", "cublas", "cufft", "cusolver", "cusparse", "nvml", "nvidia driver",
               "no kernel image", "device-side assert", "accelerator")
_PATHLIKE = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\|//)[^\n'\"]*")
_STAGE_IN = re.compile(r"in stage '([^']*)'")


# ------------------------------------------------------------------------------------------ the modules
class Modules:
    """The modules run_job uses, imported by name on first use. Tests pass fakes as keyword arguments."""

    def __init__(self, **given: Any) -> None:
        self.__dict__.update(given)

    def __getattr__(self, name: str) -> Any:
        if name in _RUNNER_MODS:
            mod = importlib.import_module(f"{RUNNER_PKG}.{name}")
        elif name in _APP_MODS:
            mod = importlib.import_module(f"{APP_PKG}.{name}")
        else:
            raise AttributeError(name)
        setattr(self, name, mod)
        return mod

    def has(self, name: str) -> bool:
        """True when the module is already loaded here (given, or imported by an earlier use)."""
        return name in self.__dict__


# ------------------------------------------------------------------------------------------ the job
@dataclass(frozen=True)
class GpuJob:
    lock_paths: Tuple[str, ...] = ()
    index: int = 0
    cap_gb: float = CAP_GB_DEFAULT
    need_free_mb: Optional[int] = None


@dataclass(frozen=True)
class Job:
    """job.json (rev 1 section 2 + R2.3.3 "gpu")."""
    input: Path
    program: str
    out: Path
    today: Optional[str] = None
    cache_root: Optional[Path] = None
    job: str = ""
    trace_dir: Optional[str] = None
    gpu: GpuJob = field(default_factory=GpuJob)


class JobError(Exception):
    """job.json is unusable (a missing field): BAD_ARGS, exit 2 (as entry.py treats a bad job.json)."""


def _get(x: Any, name: str, default: Any = None) -> Any:
    if isinstance(x, Mapping):
        return x.get(name, default)
    return getattr(x, name, default)


def load_job(job: Any) -> Job:
    """A Job from a Job, a job.json path, a job.json dict or any object with the same attributes."""
    if isinstance(job, Job):
        return job
    if isinstance(job, (str, os.PathLike)):
        try:
            job = json.loads(Path(job).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise JobError(f"job.json could not be read ({type(e).__name__})") from None
    missing = [k for k in ("input", "program", "out") if not _get(job, k)]
    if missing:
        raise JobError(f"job.json lacks {', '.join(missing)}")
    try:
        g = _get(job, "gpu") or {}
        locks = _get(g, "lock_paths") or ()
        if isinstance(locks, (str, os.PathLike)):
            locks = (locks,)
        need = _get(g, "need_free_mb")
        gpu = GpuJob(lock_paths=tuple(str(p) for p in locks if p), index=int(_get(g, "index", 0) or 0),
                     cap_gb=float(_get(g, "cap_gb", CAP_GB_DEFAULT) or CAP_GB_DEFAULT),
                     need_free_mb=int(need) if need is not None else None)
        cr = _get(job, "cache_root")
        today = _get(job, "today")
        if isinstance(today, dt.date):
            today = today.isoformat()
        return Job(input=Path(_get(job, "input")), program=str(_get(job, "program")), out=Path(_get(job, "out")),
                   today=str(today) if today else None, cache_root=Path(cr) if cr else None,
                   job=str(_get(job, "job", "") or ""), trace_dir=_get(job, "trace_dir"), gpu=gpu)
    except (TypeError, ValueError) as e:
        raise JobError(f"job.json has a bad value ({type(e).__name__})") from None


# ------------------------------------------------------------------------------------------ outcomes
class Refusal(Exception):
    """An outcome with an error code: exit_code, code, values (strings, no path), text (no path)."""

    def __init__(self, code: str, values: Optional[Mapping[str, Any]] = None, exit_code: int = EXIT_REFUSED,
                 text: str = "") -> None:
        super().__init__(text or code)
        self.code, self.exit_code, self.text = code, exit_code, text or code
        self.values = {str(k): str(v) for k, v in (values or {}).items() if v is not None}


class CardProblem(Refusal):
    """gpuinfo.problems() found one: exit 6."""

    def __init__(self, code: str, values: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(code, values, EXIT_CARD, f"graphics card problem {code}")


def redact(text: Any) -> str:
    """Path-like runs (C:\\..., \\\\server\\..., //...) become <path>."""
    return _PATHLIKE.sub("<path>", str(text))


def chain(e: Optional[BaseException]) -> List[BaseException]:
    """e, its __cause__ and __context__, recursively (each exception once)."""
    out: List[BaseException] = []
    todo = [e]
    while todo:
        x = todo.pop(0)
        if x is None or any(x is y for y in out):
            continue
        out.append(x)
        todo += [x.__cause__, x.__context__]
    return out


def root_type(e: BaseException) -> str:
    """The type name of the exception at the root of e's __cause__ chain (pipeline.RunCrashed wraps the stage's
    own exception): the type only, never the text."""
    x, seen = e, 0
    while x.__cause__ is not None and seen < 32:
        x, seen = x.__cause__, seen + 1
    return type(x).__name__


def _cls(mod: Any, name: str) -> Optional[type]:
    c = getattr(mod, name, None) if mod is not None else None
    return c if isinstance(c, type) else None


def _is_a(e: BaseException, mod: Any, name: str) -> bool:
    """isinstance(e, mod.<name>); without that class, a class of that name in e's MRO."""
    c = _cls(mod, name)
    if c is not None:
        return isinstance(e, c)
    return any(k.__name__ == name for k in type(e).__mro__)


def cuda_marked(e: BaseException) -> bool:
    """True when e or anything in its chain is a CUDA / card error (by its type or its text). The text of an
    OSError or ImportError is not read: it names the DLL that failed to load (torch\\lib\\cublasLt64_12.dll,
    cudnn64_9.dll ...), which says nothing about the card."""
    for x in chain(e):
        names = " ".join(k.__name__ for k in type(x).__mro__).lower()
        if "outofmemoryerror" in names or "acceleratorerror" in names or "cudaerror" in names:
            return True
        if isinstance(x, (OSError, ImportError)):
            continue
        s = str(x).lower()
        if any(m in s for m in _CUDA_MARKS):
            return True
    return False


STARTUP_WINERRORS = (126, 127, 193, 1114)     # module not found, procedure not found, bad image, DLL init failed
LOW_MEMORY_WINERRORS = (8, 14, 1455)          # not enough memory, out of memory, the paging file is too small
_LOW_MEMORY_TEXTS = ("not enough memory", "paging file is too small", "paging file too small")


def _startup_failure(e: BaseException) -> bool:
    """A module or DLL that cannot be loaded (ImportError, or Windows error 126 / 127 / 193 / 1114 loading a DLL)."""
    for x in chain(e):
        if isinstance(x, ImportError):
            return True
        if isinstance(x, OSError) and getattr(x, "winerror", None) in STARTUP_WINERRORS:
            return True
    return False


def _disk_full(e: BaseException) -> bool:
    return any(isinstance(x, OSError) and (x.errno == 28 or getattr(x, "winerror", None) in (39, 112))
               for x in chain(e))


def _low_memory(e: BaseException) -> bool:
    """The PC ran out of memory (RAM or the paging file): a MemoryError, Windows error 8 / 14 / 1455 (also inside
    a DLL load: "[WinError 1455] The paging file is too small ... Error loading cublasLt64_12.dll"), or a message
    saying so."""
    for x in chain(e):
        if isinstance(x, MemoryError):
            return True
        if isinstance(x, OSError) and getattr(x, "winerror", None) in LOW_MEMORY_WINERRORS:
            return True
        s = str(x).lower()
        if any(t in s for t in _LOW_MEMORY_TEXTS):
            return True
    return False


# rev 1 section 3.3: the pinned message table of ingest.InputError and safety.SafetyError (first match wins)
_INPUT_TABLE: Tuple[Tuple[str, str], ...] = (
    ("sha256 differs", "INPUT_CHANGED"), ("name a file that is not in the input", "INPUT_CHANGED"),
    ("page(s), the input has", "INPUT_CHANGED"), ("changed during the run", "INPUT_CHANGED"),
    ("did not keep its size", "INPUT_CHANGED"),
    ("password-protected", "ENCRYPTED"), ("has no pages", "NO_PAGES"),
    ("could not be opened as a pdf", "UNREADABLE_PDF"), ("is not a pdf", "UNREADABLE_PDF"),
    ("could not be opened as an image", "UNREADABLE_IMAGE"),
    ("holds no .pdf", "EMPTY_FOLDER"),
    # safety.APP_DATA_OUTSIDE (1.0.1, I28): the app's own data folder (cache root, page texts), never the report folder
    ("lies outside the app's data folder", "DATA_NOT_WRITABLE"),
    # safety.check_out_dir (APP_OUT_* and the DEV wording)
    ("must not lie inside the input", "REPORT_INSIDE_INPUT"), ("inside the input", "REPORT_INSIDE_INPUT"),
    ("input must not lie inside", "INPUT_INSIDE_REPORT"), ("inside the report", "INPUT_INSIDE_REPORT"),
    ("too long", "PATH_TOO_LONG"),
    ("no space", "DISK_FULL"), ("disk full", "DISK_FULL"),
    ("the report folder", "REPORT_NOT_WRITABLE"), ("--out", "REPORT_NOT_WRITABLE"),
    ("a written path", "REPORT_NOT_WRITABLE"),
    # the input itself
    ("must be one bundle .pdf or one folder", "NOT_PDF_OR_FOLDER"),
    ("does not exist", "NOT_PDF_OR_FOLDER"), ("junction or a link", "NOT_PDF_OR_FOLDER"),
    ("leads outside it", "NOT_PDF_OR_FOLDER"), ("could not be listed", "NOT_PDF_OR_FOLDER"),
    ("the input must", "NOT_PDF_OR_FOLDER"),
)
_FILE_LABEL = re.compile(r"^(the bundle|file \d+ of \d+ in the folder \(sorted by name\))")


def file_value(label: str) -> str:
    """The message value of a file label: "The bundle", "File 2 of 5 in the folder (sorted by name)" (it starts
    the window's sentence)."""
    return label[:1].upper() + label[1:]


def classify_refusal(e: BaseException, kind: str = "") -> Tuple[str, Dict[str, str]]:
    """(code, values) of an ingest.InputError or safety.SafetyError, by its message (the pinned table). The
    value "file" is the runner's label of the file ("The bundle", "File 2 of 5 in the folder (sorted by
    name)"), never its name. An unknown SafetyError is REPORT_NOT_WRITABLE; an unknown InputError is
    NOT_PDF_OR_FOLDER."""
    msg = str(e)
    low = msg.lower()
    values: Dict[str, str] = {}
    m = _FILE_LABEL.match(msg)
    if m:
        values["file"] = file_value(m.group(1))
    for needle, code in _INPUT_TABLE:
        if needle in low:
            return code, values
    return ("REPORT_NOT_WRITABLE" if kind == "safety" else "NOT_PDF_OR_FOLDER"), values


# ------------------------------------------------------------------------------------------ pre-checks
def _visible(p: Path) -> bool:
    if p.name.startswith((".", "~$")):
        return False
    try:
        attrs = getattr(p.stat(), "st_file_attributes", 0)
    except OSError:
        return False
    return not attrs & (0x2 | 0x4)          # FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM


def _label(ingest: Any, k: int, n: int, mode: str) -> str:
    fn = getattr(ingest, "file_label", None)
    if callable(fn):
        try:
            return str(fn(k, n, mode))
        except Exception:
            pass
    return "the bundle" if mode == "bundle" else f"file {k} of {n} in the folder (sorted by name)"


def pdf_problem(path: Path, label: str, opener: Optional[Callable[[str], Any]] = None) -> Optional[Refusal]:
    """ENCRYPTED, UNREADABLE_PDF or NO_PAGES for a PDF that ingest would refuse, else None."""
    if opener is None:
        import pymupdf
        opener = pymupdf.open
    try:
        doc = opener(str(path))
    except Exception as e:
        return Refusal("UNREADABLE_PDF", {"file": file_value(label)}, text=f"{label} could not be opened as a PDF "
                                                              f"({type(e).__name__})")
    try:
        if getattr(doc, "needs_pass", False):
            return Refusal("ENCRYPTED", {"file": file_value(label)}, text=f"{label} is password-protected")
        if not getattr(doc, "is_pdf", True):
            return Refusal("UNREADABLE_PDF", {"file": file_value(label)}, text=f"{label} is not a PDF")
        if len(doc) < 1:
            return Refusal("NO_PAGES", {"file": file_value(label)}, text=f"{label} has no pages")
    except Exception as e:
        return Refusal("UNREADABLE_PDF", {"file": file_value(label)}, text=f"{label} could not be read ({type(e).__name__})")
    finally:
        with contextlib.suppress(Exception):
            doc.close()
    return None


def image_problem(path: Path, label: str) -> Optional[Refusal]:
    from PIL import Image
    try:
        with Image.open(path) as im:
            im.size
    except Exception as e:
        return Refusal("UNREADABLE_IMAGE", {"file": file_value(label)}, text=f"{label} could not be opened as an image "
                                                                f"({type(e).__name__})")
    return None


def precheck(input_path: Path, ingest: Any = None, opener: Optional[Callable[[str], Any]] = None
             ) -> Optional[Refusal]:
    """Rev 1 section 3.3, in ingest's order: exists -> type -> folder files -> pymupdf.open -> needs_pass ->
    page count (PIL for pictures). A folder is refused only when none of its files can be read (ingest lists
    the others as "not checked", revision 2.3). None = the runner can check it."""
    p = Path(input_path)
    if len(str(p)) >= MAX_PATH:
        return Refusal("PATH_TOO_LONG", text="the input path is too long")
    if not p.is_absolute() or not p.exists():
        return Refusal("NOT_PDF_OR_FOLDER", text="the input does not exist")
    if p.is_file():
        if p.suffix.lower() != ".pdf":
            return Refusal("NOT_PDF_OR_FOLDER", text="the input must be one bundle .pdf or one folder")
        return pdf_problem(p, _label(ingest, 1, 1, "bundle"), opener)
    if not p.is_dir():
        return Refusal("NOT_PDF_OR_FOLDER", text="the input is not a file or folder")
    lister = getattr(ingest, "folder_files", None)
    try:
        if callable(lister):
            files = list(lister(p))
        else:
            files = sorted((f for f in p.iterdir() if f.is_file() and f.suffix.lower() in (".pdf", ".jpg", ".jpeg",
                                                                                            ".png") and _visible(f)),
                           key=lambda f: (f.name.casefold(), f.name))
    except OSError as e:
        return Refusal("NOT_PDF_OR_FOLDER", text=f"the folder could not be listed ({type(e).__name__})")
    if not files:
        try:
            subdirs = any(d.is_dir() and _visible(d) for d in p.iterdir())
        except OSError:
            subdirs = False
        if subdirs:
            return Refusal("FOLDER_OF_FOLDERS", text="the folder holds folders, not documents")
        return Refusal("EMPTY_FOLDER", text="the folder holds no .pdf, .jpg, .jpeg or .png file")
    first: Optional[Refusal] = None
    for k, f in enumerate(files, 1):
        lab = _label(ingest, k, len(files), "folder")
        prob = (image_problem(f, lab) if f.suffix.lower() in (".jpg", ".jpeg", ".png")
                else pdf_problem(f, lab, opener))
        if prob is None:
            return None
        first = first or prob
    return first


# ------------------------------------------------------------------------------------------ the listener
def _event_of(args: Tuple[Any, ...], kw: Mapping[str, Any]) -> Tuple[str, Mapping[str, Any]]:
    """(event, data) of a listener call, whatever the hooks module's calling convention: (event, **data),
    (event, data) or ({"ev": event, ...})."""
    if args and isinstance(args[0], str):
        data = args[1] if len(args) > 1 and isinstance(args[1], Mapping) else kw
        return args[0], data
    if args and isinstance(args[0], Mapping):
        d = args[0]
        return str(d.get("ev") or d.get("event") or ""), d
    return str(kw.get("event") or kw.get("ev") or ""), kw


class Recorder:
    """hooks.LISTENER: forwards every call to the caller's listener unchanged (a Cancelled it raises goes on) and
    records the stage that started last and the budget hits (where, page) for checkfacts.

    While ``gate`` is set (the worker sets it for ``pipeline.run`` only), every stage START is followed by
    ``gate.wait_turn("stage")``, after the event went to the listener: while the bot's lock exists the check
    waits there (its card cache emptied by the gate's pause), also when the stage makes no model call at all (the
    runner's "bot" stage and the QR and TIN ladders run on the processor for up to a minute). A stage's END is
    never held up (it runs in the runner's ``finally``, where the listener must not raise)."""

    def __init__(self, listener: Optional[Callable[..., Any]]) -> None:
        self.listener = listener
        self.stage = ""
        self.budget: List[Dict[str, Any]] = []
        self.gate: Any = None
        self.stage_turns = 0

    def __call__(self, *args: Any, **kw: Any) -> Any:
        started = False
        try:
            ev, data = _event_of(args, kw)
            if ev == "stage" and data.get("state") == "start":
                self.stage = str(data.get("key") or "")
                started = True
            elif ev == "budget":
                self.budget.append({"where": str(data.get("where") or ""), "page": data.get("page")})
        except Exception:
            pass
        out = self.listener(*args, **kw) if self.listener is not None else None
        gate = self.gate
        if started and gate is not None:
            self.stage_turns += 1
            gate.wait_turn("stage")
        return out


# ------------------------------------------------------------------------------------------ helpers
def process_memory() -> Dict[str, Optional[int]]:
    """The worker's peak working set and peak commit in MB (Windows; None elsewhere or on failure)."""
    out: Dict[str, Optional[int]] = {"peak_working_set_mb": None, "peak_commit_mb": None}
    if os.name != "nt":
        return out
    try:
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t)]

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
        k32.K32GetProcessMemoryInfo.restype = wintypes.BOOL
        c = PMC()
        c.cb = ctypes.sizeof(PMC)
        if k32.K32GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(c), c.cb):
            out["peak_working_set_mb"] = int(c.PeakWorkingSetSize // (1 << 20))
            out["peak_commit_mb"] = int(c.PeakPagefileUsage // (1 << 20))
    except Exception:
        pass
    return out


def _facts_of(obj: Any, *names: str) -> Dict[str, Any]:
    """The first of obj.<name> / obj.<name>() that is a mapping, as a dict ({} when none is)."""
    for n in names:
        v = getattr(obj, n, None)
        if callable(v):
            try:
                v = v()
            except Exception:
                v = None
        if isinstance(v, Mapping):
            return dict(v)
    return {}


def card_facts(card: Any, cudnn: Any = None) -> Dict[str, Any]:
    """torch / CUDA / cuDNN versions, the cap and the start seconds of the started card (gpuengine.Card.facts(),
    else attributes). `cudnn` (BUILD.json's) is used when the card does not report one."""
    d = _facts_of(card, "facts", "info", "describe")

    def first(*keys: str) -> Any:
        for k in keys:
            v = d.get(k)
            if v is None:
                v = getattr(card, k, None)
                if callable(v):
                    v = None
            if isinstance(v, (str, int, float)) and not isinstance(v, bool):
                return v
        return None

    return {"torch": first("torch", "torch_version"), "cuda": first("cuda", "cuda_version"),
            "cudnn": first("cudnn", "cudnn_version") or cudnn, "cap_gb": first("cap_gb"),
            "seconds": first("seconds", "start_s"), "import_s": first("import_s"), "cuda_s": first("cuda_s"),
            "allocator": first("allocator_backend", "backend")}


def card_peaks(card: Any) -> Dict[str, Any]:
    """The card's peaks over the whole process (allocated / reserved GB): gpuengine.Card.peaks() gives the pair
    (alloc, reserved); a mapping is taken as it is."""
    fn = getattr(card, "peaks", None)
    if callable(fn):
        try:
            v = fn()
        except Exception:
            v = None
        if isinstance(v, (tuple, list)) and len(v) == 2:
            return {"peak_alloc_gb": v[0], "peak_reserved_gb": v[1]}
        if isinstance(v, Mapping):
            return dict(v)
    return _facts_of(card, "memory", "stats")


def call_opt(fn: Callable[..., Any], *args: Any, **optional: Any) -> Any:
    """fn(*args, **the optional keyword arguments its signature names) (pipeline.call_opt's rule)."""
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return fn(*args)
    if any(p.kind == p.VAR_KEYWORD for p in params.values()):
        return fn(*args, **optional)
    return fn(*args, **{k: v for k, v in optional.items() if k in params})


def session_stats(reader: Any) -> Dict[str, Any]:
    """{"det": ..., "rec": ...}: TorchSession.stats() of the light reader's two networks."""
    out: Dict[str, Any] = {}
    for kind in ("det", "rec"):
        s = getattr(reader, kind, None)
        fn = getattr(s, "stats", None)
        if callable(fn):
            try:
                out[kind] = fn()
            except Exception:
                out[kind] = {}
    return out


def _cc_text(cc: Any) -> str:
    if isinstance(cc, (tuple, list)) and len(cc) == 2:
        return f"{cc[0]}.{cc[1]}"
    return str(cc or "")


def _gb(mb: Any) -> str:
    try:
        return f"{float(mb) / 1024:.1f}"
    except (TypeError, ValueError):
        return "?"


def _log(msg: str) -> None:
    if VERBOSE:
        try:
            print(f"[worker] {msg}", file=sys.stderr, flush=True)
        except Exception:
            pass


def frames_only(e: BaseException) -> str:
    """The traceback's frames (file base name, line, function) and the exception types, never a message (it may
    hold a value read off a document) or a folder."""
    lines = []
    for x in reversed(chain(e)):
        lines.append(f"{type(x).__module__}.{type(x).__name__}")
        for fr in traceback.extract_tb(x.__traceback__):
            lines.append(f"  {os.path.basename(fr.filename)}:{fr.lineno} {fr.name}")
    return "\n".join(lines)


# ------------------------------------------------------------------------------------------ the check
class _Check:
    def __init__(self, m: Modules, job: Job, rec: Recorder) -> None:
        self.m, self.job, self.rec = m, job, rec
        self.stage = "start"
        self.state: Any = None
        self.info: Any = None
        self.gate: Any = None
        self.card: Any = None
        self.fr: Optional[Dict[str, Any]] = None
        self.reader: Any = None
        self.start_s: Optional[float] = None
        self.light_load_s: Optional[float] = None
        self.timings: Dict[str, float] = {}
        self.warnings: List[str] = []
        self.cache_root: Optional[Path] = None
        self.today: Optional[dt.date] = None
        self.program = ""
        self.prev_factory: Any = None
        self.factory_set = False
        self.ascii_dir: Optional[Path] = None

    # ---------------------------------------------------------------- events
    def emit(self, event: str, **data: Any) -> None:
        self.m.hooks.emit(event, **data)

    def emit_final(self, event: str, **data: Any) -> None:
        """A terminal event (error, cancelled, done) or a stage's end: a Cancelled or an error raised by the
        listener is ignored (the outcome is already decided; a stage's end must not hide the error in flight)."""
        cancelled = _cls(self.m.hooks, "Cancelled")
        try:
            self.m.hooks.emit(event, **data)
        except Exception:
            pass
        except BaseException as e:
            if cancelled is None or not isinstance(e, cancelled):
                raise

    @contextlib.contextmanager
    def phase(self, key: str) -> Iterator[None]:
        """A stage the worker runs itself ("card", "firstread"): stage start / end events, timing."""
        self.stage = key
        self.rec.stage = key
        self.emit("stage", key=key, state="start")
        t0 = time.perf_counter()
        try:
            yield
        finally:
            s = round(time.perf_counter() - t0, 3)
            self.timings[key] = s
            _log(f"{key} {s:.1f}s")
            self.emit_final("stage", key=key, state="end", seconds=s)

    def at(self, key: str) -> None:
        """A step without stage events (the error's stage name)."""
        self.stage = key

    # ---------------------------------------------------------------- the steps
    def args(self) -> None:
        j = self.job
        self.program = str(j.program or "").upper()
        if self.program not in PROGRAMS:
            raise Refusal(BAD_ARGS, {"program": self.program[:20]},
                          text=f"the program must be one of {', '.join(PROGRAMS)}")
        if j.today:
            try:
                self.today = dt.date.fromisoformat(str(j.today))
            except ValueError:
                raise Refusal(BAD_ARGS, text="the check date must be YYYY-MM-DD") from None
        else:
            self.today = dt.date.today()
        if not j.input.is_absolute() or not j.out.is_absolute():
            raise Refusal(BAD_ARGS, text="the input and the report folder must be absolute paths")
        pl = self.m.places.current()
        self.cache_root = Path(j.cache_root if j.cache_root is not None else pl.cache_root)
        if not self.cache_root.is_absolute():
            raise Refusal(BAD_ARGS, text="the cache folder must be an absolute path")
        job_id = re.sub(r"[^0-9A-Za-z_-]", "", j.job or "")[:40] or f"pid{os.getpid()}"
        self.ascii_dir = Path(pl.temp_dir) / job_id / ASCII_DIR

    def run(self) -> int:
        m, j = self.m, self.job
        self.at("start")
        self.args()
        self.at("precheck")
        bad = precheck(j.input, m.ingest)
        if bad is not None:
            raise bad
        self.at("safety")                                   # the guard BEFORE any card work (R2.1.1)
        self.state = m.safety.prepare(j.out, self.cache_root, j.input)
        self.at("card")
        info = self.info = m.gpuinfo.probe()
        if getattr(info, "nvml", False) and getattr(info, "count", 0):
            self.emit("card", state="found", name=getattr(info, "name", ""), cc=_cc_text(getattr(info, "cc", "")),
                      driver=getattr(info, "driver", ""), total_mb=getattr(info, "total_mb", None),
                      free_mb=getattr(info, "free_mb", None))
        probs = list(m.gpuinfo.problems(info, tuple(getattr(m.version, "ARCH_FLAGS", ()) or ())) or [])
        if probs:
            p = probs[0]
            raise CardProblem(str(getattr(p, "code", "CARD_FAILED")), dict(getattr(p, "values", {}) or {}))
        index = getattr(info, "index", j.gpu.index)
        gpuinfo = m.gpuinfo
        self.gate = m.cardgate.CardGate(list(j.gpu.lock_paths), m.hooks.emit,
                                        free_mb=lambda: gpuinfo.free_mb(index))
        need = j.gpu.need_free_mb or int(getattr(m.gpuinfo, "NEED_FREE_MB", NEED_FREE_MB_DEFAULT))
        with self.phase("card"):
            self.gate.wait_turn("start")
            self.gate.wait_memory(need)
            t0 = time.perf_counter()
            self.card = m.gpuengine.start_card(info, self.gate)
            self.start_s = round(time.perf_counter() - t0, 3)
            cf = card_facts(self.card)
            self.emit("card", state="ready", torch=str(cf.get("torch") or "?"), cuda=str(cf.get("cuda") or "?"),
                      seconds=cf.get("seconds") if cf.get("seconds") is not None else self.start_s)
        self.at("ingest")
        src = m.ingest.open_input(j.input)
        bot = m.botimport.load(self.cache_root)
        store = self.cache_root / PAGETEXTS_DIR / f"{src.case_id}.json"
        store.parent.mkdir(parents=True, exist_ok=True)
        ascii_dir = str(self.ascii_dir)
        with self.phase("firstread"):
            self.fr = dict(call_opt(m.gpuengine.first_read, self.card, bot, src, self.gate, store=store,
                                    emit=m.hooks.emit, ascii_dir=ascii_dir) or {})
        self.at("lightreader")
        pl = m.places.current()
        t0 = time.perf_counter()
        reader = self.reader = call_opt(m.gpuengine.light_reader, self.card, self.gate, pl.glue_data,
                                        pl.models_dir, ascii_dir=ascii_dir)
        self.light_load_s = round(time.perf_counter() - t0, 3)
        self.prev_factory = getattr(m.engine, "READER_FACTORY", None)
        self.factory_set = True
        m.engine.READER_FACTORY = lambda threads=0: reader
        note = m.gpuengine.reading_note(self.fr)
        self.at("runner")
        self.rec.gate = self.gate                           # R2.1.5: the gate's turn at every runner stage start
        try:
            r = m.pipeline.run(j.input, self.program, j.out, today=self.today, cache_root=self.cache_root,
                               page_texts=store, reading_note=note)
        finally:
            self.rec.gate = None
        self.at("input")
        try:
            m.safety.finish(self.state)                     # the snapshot kept from before the card work
        except Exception as e:
            if _is_a(e, m.safety, "SafetyError"):
                raise Refusal("INPUT_CHANGED", text="the input changed during the check") from e
            raise
        self.at("resultcode")
        code = m.resultcode.code(r)
        overall = str(getattr(r, "overall", ""))
        exit_code = EXIT_STOP if overall == "STOP" else EXIT_OK
        self.at("outputs")
        gpu = self.gpu_block()
        pdf_ok = self.write_pdf(r, code)
        facts_ok = self.write_facts(r, code, gpu)
        try:
            lines = [[str(a or ""), str(b)] for a, b in m.report.summary_lines(r, j.out)]
        except Exception as e:
            self.warnings.append(f"summary lines: {type(e).__name__}")
            lines = []
        mem = process_memory()
        self.emit_final("done", exit=exit_code, overall=overall, headline=str(getattr(r, "headline", "")),
                        code=code, out=str(j.out), lines=lines, peak_mb=mem.get("peak_working_set_mb"),
                        summary_pdf=pdf_ok, checkfacts=facts_ok, gpu=m.checkfacts.done_gpu(gpu),
                        budget_hits=len(self.rec.budget), check_date=self.today.isoformat() if self.today else None)
        _log(f"done exit {exit_code} | {overall} | code {code}")
        return exit_code

    # ---------------------------------------------------------------- outputs
    def app_facts(self) -> Dict[str, Any]:
        """version.facts() (the BUILD.json facts, no paths), with the four fields this module needs ensured."""
        v = self.m.version
        d: Dict[str, Any] = {}
        fn = getattr(v, "facts", None)
        if callable(fn):
            try:
                d = dict(fn() or {})
            except Exception:
                d = {}
        d.setdefault("app_version", getattr(v, "APP_VERSION", None))
        d.setdefault("build_id", getattr(v, "BUILD_ID", None))
        d.setdefault("flavour", getattr(v, "FLAVOUR", None) or "gpu")
        d.setdefault("arch_flags", list(getattr(v, "ARCH_FLAGS", ()) or ()))
        return d

    def flavour_label(self) -> str:
        fn = getattr(self.m.version, "flavour_label", None)
        if callable(fn):
            with contextlib.suppress(Exception):
                s = str(fn())
                if "GPU" in s:
                    return s
        return "GPU"

    def gpu_block(self) -> Dict[str, Any]:
        cf = card_facts(self.card, getattr(self.m.version, "CUDNN", None)) if self.card is not None else {}
        gs: Dict[str, Any] = {}
        if self.gate is not None:
            with contextlib.suppress(Exception):
                gs = dict(self.gate.stats() or {})
        return self.m.checkfacts.gpu_block(
            info=self.info, card=cf, cap_gb=self.job.gpu.cap_gb if cf.get("cap_gb") is None else cf["cap_gb"],
            start_s=self.start_s, fr=self.fr, sessions=session_stats(self.reader) if self.reader is not None else {},
            light_load_s=self.light_load_s, gate=gs, peaks=card_peaks(self.card) if self.card is not None else {})

    def write_pdf(self, r: Any, code: str) -> bool:
        a = self.app_facts()
        try:
            call_opt(self.m.summarypdf.write, r, self.job.out / SUMMARY_PDF, code=code,
                     app_version=str(a.get("app_version") or ""), build_id=str(a.get("build_id") or ""),
                     flavour=self.flavour_label(), assert_writable=self.m.safety.assert_writable)
            return True
        except Exception as e:                 # the check's result stands; summary.txt has the same lines
            self.warnings.append(f"summary.pdf not written: {type(e).__name__}")
            _log(f"summary.pdf not written ({type(e).__name__})")
            return False

    def write_facts(self, r: Any, code: str, gpu: Mapping[str, Any]) -> bool:
        cfm = self.m.checkfacts
        try:
            facts = cfm.build(r, result_code=code, app=self.app_facts(), gpu=gpu, budget_hits=self.rec.budget,
                              process=process_memory(), app_timings=self.timings, warnings=self.warnings)
            cfm.write(facts, self.job.out / CHECKFACTS, self.m.safety.assert_writable)
            return True
        except Exception as e:
            _log(f"checkfacts.json not written ({type(e).__name__})")
            return False

    def write_error_facts(self, code: str, stage: str, e: BaseException) -> None:
        """After a crash or a card failure (the guard armed, so the case folder exists): checkfacts.json with the
        error code, the stage, the exception's type and what is known of the card."""
        if self.state is None:
            return
        try:
            cfm = self.m.checkfacts
            gpu = self.gpu_block() if self.info is not None else None
            facts = cfm.build_error(code=code, stage=stage, exc_type=root_type(e), app=self.app_facts(),
                                    check_date=self.today.isoformat() if self.today else None,
                                    program=self.program, gpu=gpu, budget_hits=self.rec.budget,
                                    process=process_memory(), app_timings=self.timings)
            cfm.write(facts, self.job.out / CHECKFACTS, self.m.safety.assert_writable)
        except Exception as e2:
            _log(f"checkfacts.json not written after the error ({type(e2).__name__})")

    # ---------------------------------------------------------------- failures
    def stage_of(self, e: BaseException) -> str:
        if self.stage == "runner":
            for x in chain(e):
                mt = _STAGE_IN.search(str(x))
                if mt and _is_a(x, self.m.pipeline if self.m.has("pipeline") else None, "RunCrashed"):
                    return mt.group(1)
            return self.rec.stage or "runner"
        return self.stage

    def card_values(self, code: str, stage: str) -> Dict[str, str]:
        info = self.info
        vals = {"stage": stage}
        if info is not None:
            vals.update(name=str(getattr(info, "name", "") or ""), driver=str(getattr(info, "driver", "") or ""),
                        cc=_cc_text(getattr(info, "cc", "")))
            if code == "CARD_NOT_SUPPORTED":
                cc = getattr(info, "cc", None)
                vals["which"] = "old" if (isinstance(cc, tuple) and cc < CC_RANGE[0]) else "new"
        return vals

    def card_code(self, e: BaseException) -> Optional[str]:
        """The card error code of a CUDA error anywhere in e's chain (gpuengine.card_error_code), else None."""
        if not cuda_marked(e):
            return None
        code = None
        if self.m.has("gpuengine") or f"{APP_PKG}.gpuengine" in sys.modules:   # never imported just for this
            fn = getattr(self.m.gpuengine, "card_error_code", None)
            if callable(fn):
                try:
                    code = fn(e)
                except Exception:
                    code = None
        return code if code in CARD_CODES else "CARD_FAILED"

    def fail(self, e: BaseException) -> int:
        m = self.m
        hooks = m.hooks
        cancelled = _cls(hooks, "Cancelled")
        if cancelled is not None and any(isinstance(x, cancelled) for x in chain(e)):
            _log(f"cancelled in stage '{self.stage_of(e)}'")
            self.emit_final("cancelled")
            return EXIT_CANCELLED
        if not isinstance(e, Exception):                    # KeyboardInterrupt, SystemExit: not ours to map
            raise e
        stage = self.stage_of(e)
        code, values, exit_code, text = self.map_error(e, stage)
        if VERBOSE:
            _log(f"{code} (exit {exit_code}) in stage '{stage}'\n{frames_only(e)}")
        self.emit_final("error", code=code, values=values, text=redact(text))
        if exit_code in (EXIT_CRASH, EXIT_CARD):
            self.write_error_facts(code, stage, e)
        return exit_code

    def map_error(self, e: Exception, stage: str) -> Tuple[str, Dict[str, str], int, str]:
        """(code, values, exit code, text) of a failed check."""
        m = self.m
        for x in chain(e):
            if isinstance(x, Refusal):
                if x.exit_code == EXIT_CARD and x.code == "CARD_FAILED" and "stage" not in x.values:
                    x.values["stage"] = stage
                return x.code, dict(x.values), x.exit_code, x.text
        if isinstance(e, JobError):
            return BAD_ARGS, {}, EXIT_REFUSED, str(e)
        cardgate = m.cardgate if m.has("cardgate") else None
        gpuengine = m.gpuengine if m.has("gpuengine") else None
        for x in chain(e):
            if _is_a(x, cardgate, "CardMemoryShort"):
                free, need = getattr(x, "free_mb", None), getattr(x, "need_mb", None)
                return ("LOW_CARD_MEMORY", {"free": _gb(free), "need": _gb(need), "free_mb": str(free),
                                            "need_mb": str(need)}, EXIT_CARD, "not enough free card memory")
            if _is_a(x, gpuengine, "CardRefused"):
                code = str(getattr(x, "code", "") or "CARD_FAILED")
                code = code if code in CARD_CODES else "CARD_FAILED"
                vals = self.card_values(code, stage)
                vals.update({str(k): str(v) for k, v in (getattr(x, "values", None) or {}).items()})
                return code, vals, EXIT_CARD, f"the graphics card was refused ({code})"
        if _low_memory(e):                                  # before the DLL and CUDA checks (a DLL's path)
            return "LOW_MEMORY", {}, EXIT_CRASH, f"out of memory in stage '{stage}'"
        if _startup_failure(e):
            return "STARTUP_FAILED", {}, EXIT_STARTUP, f"{root_type(e)} while starting (stage '{stage}')"
        code = self.card_code(e)
        if code is not None:
            return code, self.card_values(code, stage), EXIT_CARD, f"graphics card error in stage '{stage}'"
        safety = m.safety if m.has("safety") else None
        ingest = m.ingest if m.has("ingest") else None
        pipeline = m.pipeline if m.has("pipeline") else None
        crashed = pipeline is not None and _is_a(e, pipeline, "RunCrashed")
        for x in chain(e):
            if _is_a(x, safety, "SafetyError") and "changed during the run" in str(x):
                return "INPUT_CHANGED", {}, EXIT_REFUSED, "the input changed during the check"
        if not crashed:
            for x in chain(e):
                if _is_a(x, ingest, "InputError"):
                    c, v = classify_refusal(x, "input")
                    return c, v, EXIT_REFUSED, str(x)
                if _is_a(x, safety, "SafetyError"):
                    c, v = classify_refusal(x, "safety")
                    return c, v, EXIT_REFUSED, str(x)
            if pipeline is not None and _is_a(e, pipeline, "RefusedError"):
                c, v = classify_refusal(e, "input")
                return c, v, EXIT_REFUSED, str(e)
        if _disk_full(e):
            return "DISK_FULL", {}, EXIT_CRASH, f"disk full in stage '{stage}'"
        return "CRASH", {"stage": stage}, EXIT_CRASH, f"{root_type(e)} in stage '{stage}'"

    def release(self) -> None:
        """engine.READER_FACTORY put back as it was; the card's cache emptied (the phase-1 reader is released by
        first_read itself; the process ends right after, and --cli runs one check per process too)."""
        if self.factory_set:
            with contextlib.suppress(Exception):
                self.m.engine.READER_FACTORY = self.prev_factory
        if self.card is not None:
            fn = getattr(self.card, "free", None)
            if callable(fn):
                with contextlib.suppress(Exception):
                    fn()
        d = self.ascii_dir                     # R2.1.2: the ASCII copies are removed at the job's end
        if d is not None:
            with contextlib.suppress(Exception):
                if d.is_dir():
                    shutil.rmtree(d)
            with contextlib.suppress(Exception):
                d.parent.rmdir()               # <temp>\<job>, when nothing else is in it


def run_job(job: Any, listener: Optional[Callable[..., Any]], *, mods: Optional[Modules] = None) -> int:
    """One check (R2.1.1). `job`: a Job, a job.json path or its dict; `listener`: what hooks.LISTENER calls
    (the protocol writer). Returns the worker's exit code; every outcome but a cancel sends an error or done
    event first, a cancel sends "cancelled". engine.READER_FACTORY and hooks.LISTENER are put back afterwards."""
    m = mods if mods is not None else Modules()
    hooks = m.hooks
    rec = Recorder(listener)
    prev_listener = getattr(hooks, "LISTENER", None)
    hooks.LISTENER = rec
    chk: Optional[_Check] = None
    try:
        try:
            chk = _Check(m, load_job(job), rec)
        except JobError as e:                  # as entry.py answers a bad job.json: BAD_ARGS, exit 2
            _log(f"{BAD_ARGS}: {redact(e)}")
            with contextlib.suppress(Exception):
                hooks.emit("error", code=BAD_ARGS, values={}, text=redact(e))
            return EXIT_REFUSED
        try:
            return chk.run()
        except BaseException as e:
            return chk.fail(e)
    finally:
        if chk is not None:
            chk.release()
        hooks.LISTENER = prev_listener
