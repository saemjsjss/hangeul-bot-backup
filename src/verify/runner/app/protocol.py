"""The window <-> worker protocol, version 1 (APP_SPEC rev 1 section 2, with revision 2 R2.3.3).

One ASCII JSON line per event on the worker's stdout (the protocol stream is a duplicate of fd 1; fd 1 itself points
at fd 2, see ``spawn.claim_protocol_stdout``). The window sends one line on the worker's stdin: ``cancel``.

    {"v":1,"ev":"hello","app":"1.0.0","build":"<id>","pid":1234}
    {"v":1,"ev":"stage","key":"ocr","state":"start"}                       (also "card", "firstread"; "end" + seconds)
    {"v":1,"ev":"file","name":"<file>","pages":14,"ocr":12}
    {"v":1,"ev":"page","done":3,"of":14,"seconds":1.2,"where":"card"}       (phase 1; "runner" for the runner's pages)
    {"v":1,"ev":"budget","where":"birth.qr","page":7}
    {"v":1,"ev":"card","state":"found","name":"<card>","cc":"12.0","driver":"000.00","total_mb":8151,"free_mb":6120}
    {"v":1,"ev":"card","state":"ready","torch":"2.11.0+cu128","cuda":"12.8","seconds":14.2}
    {"v":1,"ev":"gpu_wait","state":"start","why":"lock","lock":"bot","where":"page","reserved_mb":180}
    {"v":1,"ev":"gpu_wait","state":"tick","why":"lock","lock":"bot","waited_s":42.0}
    {"v":1,"ev":"gpu_wait","state":"end","why":"lock","waited_s":180.5}
    {"v":1,"ev":"gpu_wait","state":"start","why":"memory","free_mb":2900,"need_mb":4096}
    {"v":1,"ev":"done","exit":0,"overall":"FAIL","headline":"...","code":"3F2A-91C0","out":"...",
     "lines":[["FAIL","..."]],"peak_mb":1310,"gpu":{"card":"<card>","peak_alloc_gb":2.41,"peak_reserved_gb":2.8,
                                                    "cpu_calls":0,"waits":1,"waited_s":180.5}}
    {"v":1,"ev":"error","code":"DRIVER_TOO_OLD","values":{"driver":"000.00"},"text":"<scrubbed>"}
    {"v":1,"ev":"cancelled"}

Also here: the worker exit codes, the card error codes, job.json (schema hdc-job/1 with the revision 2 "gpu" block),
and ``WorkerListener``, the worker-side listener that writes events and raises ``hooks.Cancelled`` once the window
asked to cancel. Standard library only: importing this module never loads numpy, torch or the runner. No personal
data: events are built by the worker and travel only through the pipe; nothing here logs them.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import re
import threading
import uuid
from pathlib import Path, PurePath
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

VERSION = 1
CANCEL_LINE = b"cancel\n"            # the one line the window writes on the worker's stdin
MAX_LINE = 4 * 1024 * 1024           # a longer line is not an event (and is dropped by the reader)

# ------------------------------------------------------------------------------------------------ exit codes
EXIT_OK = 0           # done (whatever the verdict)
EXIT_CRASH = 1        # crash; an error event was sent (the runner wrote log.txt and run.json {"error": ...})
EXIT_REFUSED = 2      # refused (bad input, unsafe path, bad arguments); an error event was sent; nothing written
EXIT_STOP = 3         # done with STOP
EXIT_CANCELLED = 4    # cancelled (a "cancelled" event was sent)
EXIT_PLATFORM = 5     # platform or start-up failure (WINDOWS_TOO_OLD, CPU_UNSUPPORTED, MISSING_PART ...)
EXIT_CARD = 6         # a card problem (one of CARD_CODES); an error event was sent first (R2.3.3)

# ------------------------------------------------------------------------------- error codes (messages.py keys)
NO_NVIDIA_CARD = "NO_NVIDIA_CARD"
DRIVER_TOO_OLD = "DRIVER_TOO_OLD"
CARD_NOT_SUPPORTED = "CARD_NOT_SUPPORTED"
CARD_TOO_SMALL = "CARD_TOO_SMALL"
LOW_CARD_MEMORY = "LOW_CARD_MEMORY"
CARD_FAILED = "CARD_FAILED"
CARD_CODES = (NO_NVIDIA_CARD, DRIVER_TOO_OLD, CARD_NOT_SUPPORTED, CARD_TOO_SMALL, LOW_CARD_MEMORY, CARD_FAILED)

CRASH = "CRASH"
CANCELLED = "CANCELLED"
STARTUP_FAILED = "STARTUP_FAILED"
MISSING_PART = "MISSING_PART"
CPU_UNSUPPORTED = "CPU_UNSUPPORTED"
LOW_MEMORY = "LOW_MEMORY"
WINDOWS_TOO_OLD = "WINDOWS_TOO_OLD"
BAD_ARGS = "BAD_ARGS"                # the --cli / --worker command line or job.json was wrong (tests only)

# ------------------------------------------------------------------------------------------------ the events
EVENTS = ("hello", "stage", "file", "page", "budget", "card", "gpu_wait", "done", "error", "cancelled")
TERMINAL = ("done", "error", "cancelled")
WAIT_STATES = ("start", "tick", "end")
WAIT_WHY = ("lock", "memory")
LOCK_KINDS = ("bot", "other")
CARD_STATES = ("found", "ready")
STAGE_STATES = ("start", "end")
GPU_BLOCK_KEYS = ("card", "peak_alloc_gb", "peak_reserved_gb", "cpu_calls", "waits", "waited_s")


class ProtocolError(ValueError):
    """An event or job.json that breaks the protocol."""


def _jsonable(x: Any, depth: int = 0) -> Any:
    """A JSON-safe copy: paths become strings, tuples lists, non-finite floats None, numpy scalars Python numbers
    (through ``item()``), unknown objects their type name. Never raises."""
    if depth > 12:
        return None
    if x is None or isinstance(x, (bool, str)):
        return x
    if isinstance(x, int):
        return int(x)
    if isinstance(x, float):
        return x if math.isfinite(x) else None
    if isinstance(x, PurePath):
        return str(x)
    if isinstance(x, bytes):
        return x.decode("utf-8", "replace")
    if isinstance(x, Mapping):
        return {str(k): _jsonable(v, depth + 1) for k, v in x.items()}
    if isinstance(x, (list, tuple, set, frozenset)):
        return [_jsonable(v, depth + 1) for v in x]
    item = getattr(x, "item", None)                      # numpy scalars
    if callable(item):
        try:
            return _jsonable(item(), depth + 1)
        except Exception:
            pass
    if isinstance(x, os.PathLike):
        try:
            return os.fspath(x)
        except Exception:
            pass
    return f"<{type(x).__name__}>"


def make(ev: str, /, **fields: Any) -> Dict[str, Any]:
    """One event: {"v": 1, "ev": ev, **fields} with JSON-safe values. "v" and "ev" are reserved."""
    if not isinstance(ev, str) or not ev:
        raise ProtocolError("an event needs a name")
    out: Dict[str, Any] = {"v": VERSION, "ev": ev}
    for k, v in fields.items():
        if k in ("v", "ev"):
            continue
        out[k] = _jsonable(v)
    return out


def encode(event: Mapping[str, Any]) -> bytes:
    """The event as one ASCII JSON line ending in b"\\n" (compact; "v" and "ev" first)."""
    if not isinstance(event, Mapping) or not isinstance(event.get("ev"), str):
        raise ProtocolError("not an event")
    body = {"v": VERSION, "ev": event["ev"]}
    for k, v in event.items():
        if k not in ("v", "ev"):
            body[str(k)] = _jsonable(v)
    line = json.dumps(body, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
    return line.encode("ascii") + b"\n"


def decode(line: Any) -> Optional[Dict[str, Any]]:
    """One line (bytes or str) as an event dict, or None when it is not a v1 event (junk, a stray print, another
    version, a line over MAX_LINE). Unknown event names are kept (the controller ignores them)."""
    try:
        if isinstance(line, (bytes, bytearray)):
            if len(line) > MAX_LINE:
                return None
            text = bytes(line).decode("ascii")
        else:
            text = str(line)
            if len(text) > MAX_LINE:
                return None
        text = text.strip()
        if not text.startswith("{"):
            return None
        obj = json.loads(text)
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None
    if not isinstance(obj, dict) or obj.get("v") != VERSION or not isinstance(obj.get("ev"), str) or not obj["ev"]:
        return None
    return obj


# --------------------------------------------------------------------------------------- event constructors
def hello(app: str, pid: int, build: Optional[str] = None) -> Dict[str, Any]:
    ev = make("hello", app=app, pid=int(pid))
    if build is not None:
        ev["build"] = str(build)
    return ev


def stage(key: str, state: str, seconds: Optional[float] = None) -> Dict[str, Any]:
    ev = make("stage", key=key, state=state)
    if seconds is not None:
        ev["seconds"] = _jsonable(float(seconds))
    return ev


def file_event(name: str, pages: int, ocr: int) -> Dict[str, Any]:
    return make("file", name=name, pages=int(pages), ocr=int(ocr))


def page(done: int, of: int, seconds: Optional[float] = None, where: Optional[str] = None) -> Dict[str, Any]:
    ev = make("page", done=int(done), of=int(of))
    if seconds is not None:
        ev["seconds"] = _jsonable(float(seconds))
    if where is not None:
        ev["where"] = str(where)
    return ev


def budget(where: str, page_no: Optional[int] = None) -> Dict[str, Any]:
    return make("budget", where=where, page=page_no)


def card_found(name: str, cc: str, driver: str, total_mb: int, free_mb: int) -> Dict[str, Any]:
    return make("card", state="found", name=name, cc=cc, driver=driver, total_mb=int(total_mb),
                free_mb=int(free_mb))


def card_ready(torch: str, cuda: str, seconds: float) -> Dict[str, Any]:
    return make("card", state="ready", torch=torch, cuda=cuda, seconds=float(seconds))


def gpu_wait(state: str, why: str, **fields: Any) -> Dict[str, Any]:
    return make("gpu_wait", state=state, why=why, **fields)


def gpu_block(card: str, peak_alloc_gb: float, peak_reserved_gb: float, cpu_calls: int, waits: int,
              waited_s: float) -> Dict[str, Any]:
    """The "gpu" block of the done event (R2.3.3): counts and peaks only, no path, no text."""
    return {"card": str(card), "peak_alloc_gb": _jsonable(float(peak_alloc_gb)),
            "peak_reserved_gb": _jsonable(float(peak_reserved_gb)), "cpu_calls": int(cpu_calls),
            "waits": int(waits), "waited_s": _jsonable(float(waited_s))}


def done(exit: int, overall: str, headline: str, code: str, out: str, lines: Sequence[Sequence[str]],
         peak_mb: Optional[int] = None, gpu: Optional[Mapping[str, Any]] = None, **extra: Any) -> Dict[str, Any]:
    return make("done", exit=int(exit), overall=overall, headline=headline, code=code, out=out,
                lines=[list(x) for x in lines], peak_mb=peak_mb, gpu=dict(gpu) if gpu is not None else None,
                **extra)


def error(code: str, text: str = "", values: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    return make("error", code=code, values=dict(values or {}), text=str(text or ""))


def cancelled() -> Dict[str, Any]:
    return make("cancelled")


# ------------------------------------------------------------------------------------------------ validation
def _num(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _int(x: Any) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def validate(event: Mapping[str, Any]) -> List[str]:
    """The protocol problems of one event (empty when it is a well-formed v1 event). Unknown event names are
    allowed (forward compatible); the known ones must carry their fields."""
    p: List[str] = []
    if not isinstance(event, Mapping):
        return ["not a mapping"]
    if event.get("v") != VERSION:
        p.append("v must be 1")
    ev = event.get("ev")
    if not isinstance(ev, str) or not ev:
        return p + ["ev missing"]

    def need(key: str, ok: Callable[[Any], bool], what: str) -> None:
        if key not in event or not ok(event[key]):
            p.append(f"{ev}.{key} must be {what}")

    if ev == "hello":
        need("app", lambda x: isinstance(x, str), "a string")
        need("pid", _int, "an integer")
    elif ev == "stage":
        need("key", lambda x: isinstance(x, str) and x, "a stage key")
        need("state", lambda x: x in STAGE_STATES, "start or end")
    elif ev == "file":
        need("pages", _int, "an integer")
        need("ocr", _int, "an integer")
    elif ev == "page":
        need("done", _int, "an integer")
        need("of", _int, "an integer")
    elif ev == "card":
        need("state", lambda x: x in CARD_STATES, "found or ready")
        if event.get("state") == "found":
            need("name", lambda x: isinstance(x, str), "a string")
            need("total_mb", _int, "an integer")
            need("free_mb", _int, "an integer")
        elif event.get("state") == "ready":
            need("torch", lambda x: isinstance(x, str), "a string")
            need("cuda", lambda x: isinstance(x, str), "a string")
    elif ev == "gpu_wait":
        need("state", lambda x: x in WAIT_STATES, "start, tick or end")
        need("why", lambda x: x in WAIT_WHY, "lock or memory")
        if event.get("why") == "lock" and event.get("state") in ("start", "tick"):
            need("lock", lambda x: x in LOCK_KINDS, "bot or other")
        if event.get("why") == "memory" and event.get("state") == "start":
            need("need_mb", _int, "an integer")
        if event.get("state") in ("tick", "end"):
            need("waited_s", _num, "a number")
    elif ev == "done":
        need("exit", _int, "an integer")
        need("overall", lambda x: isinstance(x, str), "a string")
        need("lines", lambda x: isinstance(x, list), "a list")
        g = event.get("gpu")
        if g is not None:
            if not isinstance(g, Mapping):
                p.append("done.gpu must be an object")
            else:
                for k in GPU_BLOCK_KEYS:
                    if k not in g:
                        p.append(f"done.gpu.{k} missing")
    elif ev == "error":
        need("code", lambda x: isinstance(x, str) and x, "a code")
        if "values" in event and not isinstance(event["values"], Mapping):
            p.append("error.values must be an object")
    return p


# ------------------------------------------------------------------------------------------------ job.json
JOB_SCHEMA = "hdc-job/1"
_JOB_RE = re.compile(r"^[0-9a-f]{32}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class JobError(ValueError):
    """job.json is missing, unreadable or breaks the schema (the message names the field, never a path)."""


def new_job_id() -> str:
    return uuid.uuid4().hex


def _cap_defaults() -> Tuple[float, int]:
    try:
        from . import version
        return float(version.CAP_GB), int(version.NEED_FREE_MB)
    except Exception:                                    # version.py never fails; this is belt and braces
        return 3.0, 4096


def make_job(*, input: Any, program: str, out: Any, cache_root: Any, today: Any = None,
             trace_dir: Any = None, lock_paths: Iterable[Any] = (), index: int = 0,
             cap_gb: Optional[float] = None, need_free_mb: Optional[int] = None,
             job: Optional[str] = None, **extra: Any) -> Dict[str, Any]:
    """A job.json dict (rev 1 section 2 + R2.3.3). ``lock_paths`` drops empty entries and duplicates (normcase)."""
    cap_default, need_default = _cap_defaults()
    locks: List[str] = []
    seen = set()
    for lp in lock_paths or ():
        if lp is None or str(lp).strip() == "":
            continue
        s = os.path.abspath(str(lp))
        key = os.path.normcase(s)
        if key not in seen:
            seen.add(key)
            locks.append(s)
    if isinstance(today, (dt.date, dt.datetime)):
        today = today.strftime("%Y-%m-%d")
    d: Dict[str, Any] = {
        "schema": JOB_SCHEMA,
        "job": job or new_job_id(),
        "input": str(input),
        "program": str(program),
        "out": str(out),
        "today": None if today in (None, "") else str(today),
        "cache_root": str(cache_root),
        "trace_dir": None if trace_dir in (None, "") else str(trace_dir),
        "gpu": {"lock_paths": locks, "index": int(index),
                "cap_gb": float(cap_default if cap_gb is None else cap_gb),
                "need_free_mb": int(need_default if need_free_mb is None else need_free_mb)},
    }
    for k, v in extra.items():
        if k not in d:
            d[k] = _jsonable(v)
    check_job(d)
    return d


def _abs(x: Any) -> bool:
    return isinstance(x, str) and x != "" and os.path.isabs(x)


def check_job(d: Any) -> Dict[str, Any]:
    """Validates a job dict in place and fills a missing "gpu" block with the defaults (a rev 1 job.json). Raises
    JobError naming the first bad field. Extra keys are kept (forward compatible)."""
    if not isinstance(d, dict):
        raise JobError("job.json must hold an object")
    if d.get("schema") != JOB_SCHEMA:
        raise JobError("job.json schema must be " + JOB_SCHEMA)
    if not isinstance(d.get("job"), str) or not _JOB_RE.match(d["job"]):
        raise JobError("job.json job must be 32 lower-case hex digits")
    for key in ("input", "out", "cache_root"):
        if not _abs(d.get(key)):
            raise JobError(f"job.json {key} must be an absolute path")
    if not isinstance(d.get("program"), str) or not d["program"].strip():
        raise JobError("job.json program is missing")
    t = d.get("today")
    if t is not None:
        if not isinstance(t, str) or not _DATE_RE.match(t):
            raise JobError("job.json today must be YYYY-MM-DD or null")
        try:
            dt.date.fromisoformat(t)
        except ValueError:
            raise JobError("job.json today must be YYYY-MM-DD or null") from None
    tr = d.get("trace_dir")
    if tr is not None and not _abs(tr):
        raise JobError("job.json trace_dir must be an absolute path or null")
    cap_default, need_default = _cap_defaults()
    g = d.get("gpu")
    if g is None:
        g = d["gpu"] = {"lock_paths": [], "index": 0, "cap_gb": cap_default, "need_free_mb": need_default}
    if not isinstance(g, dict):
        raise JobError("job.json gpu must be an object")
    g.setdefault("lock_paths", [])
    g.setdefault("index", 0)
    g.setdefault("cap_gb", cap_default)
    g.setdefault("need_free_mb", need_default)
    if not isinstance(g["lock_paths"], list) or not all(_abs(x) for x in g["lock_paths"]):
        raise JobError("job.json gpu.lock_paths must be a list of absolute paths")
    if not _int(g["index"]) or g["index"] < 0:
        raise JobError("job.json gpu.index must be an integer >= 0")
    if not _num(g["cap_gb"]) or not g["cap_gb"] > 0:
        raise JobError("job.json gpu.cap_gb must be a number > 0")
    if not _int(g["need_free_mb"]) or g["need_free_mb"] <= 0:
        raise JobError("job.json gpu.need_free_mb must be an integer > 0")
    return d


def write_job(path: Any, job: Mapping[str, Any]) -> Path:
    """Writes job.json atomically (``<path>.tmp`` then ``os.replace``) after validating it."""
    d = check_job(dict(job))
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)
    return p


def read_job(path: Any) -> Dict[str, Any]:
    """job.json, validated (``check_job``). Raises JobError."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
    except FileNotFoundError:
        raise JobError("job.json does not exist") from None
    except (OSError, ValueError) as e:
        raise JobError(f"job.json cannot be read ({type(e).__name__})") from None
    return check_job(d)


# ---------------------------------------------------------------------------------------- the worker's side
class Writer:
    """Writes events to the protocol stream (a binary file object). Thread-safe; one ``flush`` per event. After a
    write error (the window is gone) ``broken`` is True and further events are dropped."""

    def __init__(self, stream: Any):
        self._stream = stream
        self._lock = threading.Lock()
        self.broken = stream is None
        self.sent = 0

    def send(self, event: Mapping[str, Any]) -> bool:
        data = encode(event)
        with self._lock:
            if self.broken:
                return False
            try:
                self._stream.write(data)
                self._stream.flush()
            except (OSError, ValueError):
                self.broken = True
                return False
            self.sent += 1
            return True

    def close(self) -> None:
        with self._lock:
            try:
                if self._stream is not None:
                    self._stream.flush()
            except (OSError, ValueError):
                pass


class CancelFlag:
    """Set by the stdin watcher (``watch_stdin``) when the window writes ``cancel``."""

    def __init__(self) -> None:
        self._ev = threading.Event()
        self.why: Optional[str] = None

    def set(self, why: str = "cancel") -> None:
        if self.why is None:
            self.why = why
        self._ev.set()

    def is_set(self) -> bool:
        return self._ev.is_set()

    def wait(self, timeout: Optional[float] = None) -> bool:
        return self._ev.wait(timeout)


class _PrivateStdin:
    """Windows: the worker's stdin pipe read through a PRIVATE duplicate of its handle with kernel32's ReadFile, after
    fd 0 and the process's standard input were pointed at NUL.

    Why (G2, found by P1's --auto run): the C runtime's ``_read`` holds fd 0's lock for as long as it waits, and the
    window writes nothing until Cancel. Any other thread that then takes fd 0's lock waits with it: ``import numpy``
    does (seen in a child with a blocked stdin read; the frozen worker hung right after numpy's DLLs loaded, before
    its first log line, so the window -> worker path never got past the start). With the pipe moved off fd 0 the
    runtime and every library see NUL, and the watcher's ReadFile holds no lock of the runtime."""

    def __init__(self, handle: int, kernel32: Any) -> None:
        self._h = handle
        self._k = kernel32

    def read(self, n: int) -> bytes:
        import ctypes
        from ctypes import wintypes
        buf = ctypes.create_string_buffer(int(n))
        got = wintypes.DWORD(0)
        if not self._k.ReadFile(wintypes.HANDLE(self._h), buf, wintypes.DWORD(int(n)), ctypes.byref(got), None):
            return b""                                     # ERROR_BROKEN_PIPE: the window closed it = end of input
        return buf.raw[:got.value]

    def close(self) -> None:
        if self._h:
            self._k.CloseHandle(self._h)
            self._h = 0


def private_stdin() -> Optional[Any]:
    """Windows only: a reader over a private duplicate of fd 0's handle (see ``_PrivateStdin``), with fd 0, sys.stdin
    and STD_INPUT_HANDLE moved to NUL; None elsewhere or when there is no usable stdin handle (nothing is changed
    then)."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        import msvcrt
        from ctypes import wintypes
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.GetCurrentProcess.restype = wintypes.HANDLE
        k.DuplicateHandle.argtypes = [wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE,
                                      ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.DuplicateHandle.restype = wintypes.BOOL
        k.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                               ctypes.c_void_p]
        k.ReadFile.restype = wintypes.BOOL
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        k.SetStdHandle.argtypes = [wintypes.DWORD, wintypes.HANDLE]
        k.SetStdHandle.restype = wintypes.BOOL
        h0 = msvcrt.get_osfhandle(0)
        if h0 in (-1, -2, 0):
            return None
        me = k.GetCurrentProcess()
        dup = wintypes.HANDLE()
        if not k.DuplicateHandle(me, wintypes.HANDLE(h0), me, ctypes.byref(dup), 0, False, 0x2):   # SAME_ACCESS
            return None
        nul = os.open(os.devnull, os.O_RDONLY | getattr(os, "O_BINARY", 0))
        try:
            os.dup2(nul, 0)                                # closes fd 0's pipe handle; fd 0 is NUL from now on
        finally:
            os.close(nul)
        k.SetStdHandle(wintypes.DWORD(-10 & 0xFFFFFFFF), wintypes.HANDLE(msvcrt.get_osfhandle(0)))
        return _PrivateStdin(int(dup.value), k)
    except (OSError, ValueError, AttributeError, ImportError):
        return None


def watch_stdin(flag: CancelFlag, stream: Any = None, name: str = "hdc-stdin") -> Optional[threading.Thread]:
    """A daemon thread that reads the worker's stdin line by line and sets ``flag`` on ``cancel``. End of input
    is not a cancel (a --cli run may have no stdin). Returns None when there is no stdin. Without ``stream`` on
    Windows the pipe is read through ``private_stdin()`` (never through fd 0: see ``_PrivateStdin``)."""
    if stream is None:
        stream = private_stdin()
    if stream is None:
        if os.name == "nt":
            return None                                    # never a blocking read of fd 0 on Windows
        try:
            stream = open(0, "rb", buffering=0, closefd=False)
        except (OSError, ValueError):
            return None

    def run() -> None:
        buf = b""
        try:
            while True:
                chunk = stream.read(512)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if line.strip().lower() == b"cancel":
                        flag.set("cancel")
                if len(buf) > 4096:
                    buf = buf[-4096:]
        except (OSError, ValueError):
            return
        if buf.strip().lower() == b"cancel":            # "cancel" without a newline, then end of input
            flag.set("cancel")

    t = threading.Thread(target=run, name=name, daemon=True)
    t.start()
    return t


def _hooks_cancelled() -> type:
    from .. import hooks                                   # Task A: class Cancelled(BaseException)
    return hooks.Cancelled


class WorkerListener:
    """The worker's event listener: ``hooks.LISTENER`` and the ``listener`` given to ``worker.run_job``.

    Called as ``listener(event, **fields)`` (the hooks contract), ``listener(event, fields_dict)`` or
    ``listener(event_dict)``. It translates the runner's own hook events into protocol events, writes them, and
    raises ``hooks.Cancelled`` once ``cancel`` is set, at the next event that may stop the check: never on an
    "end" event (a stage's ``finally``, an OCR call's end, a wait's end), never on hello / done / error /
    cancelled. A broken protocol pipe (the window is gone) sets ``cancel`` too, when ``cancel_on_broken``.

    Translation: "ocr" phase "end" (not a memo hit) -> "page" (done / of counted over the "file" events,
    where="runner"); "ocr" phase "start" is dropped; every other event passes through unchanged.
    """

    def __init__(self, writer: Writer, cancel: Optional[CancelFlag] = None, *,
                 cancelled_exc: Optional[type] = None, cancel_on_broken: bool = True):
        self.writer = writer
        self.cancel = cancel if cancel is not None else CancelFlag()
        self._exc = cancelled_exc
        self.cancel_on_broken = cancel_on_broken
        self.ocr_of = 0
        self.ocr_done = 0
        self.counts: Dict[str, int] = {}
        self.terminal: Optional[str] = None

    # the hooks contract: LISTENER(event, **data)
    def __call__(self, event: Any, data: Optional[Mapping[str, Any]] = None, /, **fields: Any) -> None:
        if isinstance(event, Mapping):
            fields = {**{k: v for k, v in event.items() if k not in ("v", "ev")}, **fields}
            name = event.get("ev")
        else:
            name = event
            if isinstance(data, Mapping):
                fields = {**data, **fields}
        if not isinstance(name, str) or not name:
            return
        for out in self._translate(name, fields):
            self.send(out)
        self._maybe_cancel(name, fields)

    def send(self, event: Mapping[str, Any]) -> bool:
        ev = event.get("ev")
        self.counts[ev] = self.counts.get(ev, 0) + 1
        if ev in TERMINAL and self.terminal is None:
            self.terminal = ev
        ok = self.writer.send(event)
        if not ok and self.cancel_on_broken and self.writer.broken:
            self.cancel.set("broken pipe")
        return ok

    def _translate(self, name: str, fields: Dict[str, Any]) -> List[Dict[str, Any]]:
        if name == "ocr":
            if fields.get("phase") != "end" or fields.get("memo"):
                return []
            self.ocr_done += 1
            return [page(self.ocr_done, max(self.ocr_of, self.ocr_done), fields.get("seconds"), "runner")]
        if name == "file":
            try:
                self.ocr_of += int(fields.get("ocr") or 0)
            except (TypeError, ValueError):
                pass
        return [make(name, **fields)]

    def _maybe_cancel(self, name: str, fields: Mapping[str, Any]) -> None:
        if not self.cancel.is_set():
            return
        if name in TERMINAL or name == "hello":
            return
        if fields.get("state") == "end" or fields.get("phase") == "end":
            return
        exc = self._exc or _hooks_cancelled()
        raise exc()
