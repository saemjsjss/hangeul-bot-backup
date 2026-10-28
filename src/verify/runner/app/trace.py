"""The runtime trace for test 8 c (APP_SPEC rev 1 section 8 c, amended by revision 2 R2.6).

``--trace-opens DIR`` makes every process of the app (the window, the worker, the self-test children) call
``arm(DIR)`` FIRST, before boot. ``arm`` opens ``DIR\\trace-<pid>.jsonl`` and only then adds a permanent audit hook,
so the trace's own file never appears in it. Every traced event becomes one JSON line:

    {"t": 1111111111.125, "pid": 1234, "ev": "open", "args": ["<path>", "r", 32768]}
    {"t": ..., "pid": 1234, "ev": "socket.connect", "args": ["<socket>", ["127.0.0.1", 9]], "refused": true}

A ``socket.*`` line also carries ``refused``: true when the runner's guard (``safety``, armed in this process)
refuses that event (it refuses all of its network events before Windows is asked), false when no guard is armed
or the event is not one it refuses (``socket.__new__``, ``socket.gethostname``), null when ``safety`` is not loaded.

Traced audit events: ``open``, ``os.listdir``, ``os.scandir``, ``os.mkdir``, ``os.rmdir``, ``os.remove``,
``os.rename``, ``os.truncate``, ``os.chmod``, ``os.utime``, ``os.link``, ``os.symlink``, ``os.chdir``,
``os.startfile``, ``os.system``, ``os.spawn``, ``os.exec``, ``os.add_dll_directory``, ``ctypes.dlopen``,
``subprocess.Popen``, ``_winapi.CreateFile``, ``_winapi.CreateNamedPipe``, ``shutil.*``, ``socket.*`` and
``winreg.*`` (OpenKey, CreateKey, SetValue, DeleteKey ...).

Native file use raises no audit event, so these are wrapped as soon as their module is imported (a meta-path
finder patches the module right after it ran; a module imported before ``arm`` is patched at once):
``pymupdf.open`` (``Document.__init__``) and ``pymupdf.Document.save``, ``PIL.Image.open`` and ``Image.save``,
``cv2.imread`` and ``cv2.imwrite``, ``onnxruntime.InferenceSession`` (not bundled in the GPU build; kept for DEV).
A wrapper records the path argument (a stream is recorded as "<stream>") and then calls the original.

The trace holds paths (it is a private test output under apptest); it never holds file contents. The hook never
raises into the app: any failure inside it is counted and dropped. Children get ``--trace-opens`` from
``spawn.self_command``. Standard library only.
"""
from __future__ import annotations

import functools
import importlib.abc
import json
import os
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

TRACED_EXACT = frozenset({
    "open", "os.listdir", "os.scandir", "os.mkdir", "os.rmdir", "os.remove", "os.rename", "os.truncate",
    "os.chmod", "os.utime", "os.link", "os.symlink", "os.chdir", "os.startfile", "os.system", "os.spawn",
    "os.exec", "os.posix_spawn", "os.add_dll_directory", "ctypes.dlopen", "subprocess.Popen",
    "_winapi.CreateFile", "_winapi.CreateNamedPipe", "glob.glob",
})
TRACED_PREFIXES = ("shutil.", "socket.", "winreg.")

# module name -> the attributes to wrap ("Class.method" or "function"), and the event name each one records
NATIVE_TARGETS: Dict[str, Tuple[Tuple[str, str], ...]] = {
    "pymupdf": (("Document.__init__", "pymupdf.open"), ("Document.save", "pymupdf.Document.save")),
    "fitz": (("Document.__init__", "pymupdf.open"), ("Document.save", "pymupdf.Document.save")),
    "PIL.Image": (("open", "PIL.Image.open"), ("Image.save", "PIL.Image.save")),
    "cv2": (("imread", "cv2.imread"), ("imwrite", "cv2.imwrite")),
    "onnxruntime": (("InferenceSession.__init__", "onnxruntime.InferenceSession"),),
}

_WRAPPED_MARK = "__hdc_trace_wrapped__"
_lock = threading.Lock()
_local = threading.local()
_state: Dict[str, Any] = {"dir": None, "file": None, "path": None, "errors": 0, "lines": 0, "targets": None}


def wanted(event: str) -> bool:
    return event in TRACED_EXACT or event.startswith(TRACED_PREFIXES)


def _arg(x: Any, depth: int = 0) -> Any:
    """A JSON-safe, short form of one audit argument: paths and numbers as they are, containers shallowly,
    everything else as its type name (a socket, a handle object ...)."""
    if x is None or isinstance(x, (bool, int, float)):
        return x
    if isinstance(x, str):
        return x if len(x) <= 4096 else x[:4096] + "..."
    if isinstance(x, bytes):
        try:
            return os.fsdecode(x)[:4096]
        except Exception:
            return "<bytes>"
    if isinstance(x, os.PathLike):
        try:
            return os.fspath(x)
        except Exception:
            return "<path>"
    if isinstance(x, (list, tuple)) and depth < 2:
        return [_arg(v, depth + 1) for v in list(x)[:16]]
    if isinstance(x, dict) and depth < 1:
        return "<dict>"
    return f"<{type(x).__name__}>"


def socket_refused(event: str) -> Optional[bool]:
    """Whether the runner's guard refuses this socket event in this process: True when ``safety`` is armed here
    and the event is one of its network events (it refuses every one of them, before Windows is asked); False
    when the guard is not armed or does not refuse that event (``socket.__new__``, ``socket.gethostname`` ...);
    None when ``safety`` is not loaded. ``safety`` is never imported by the trace."""
    s = sys.modules.get("src.verify.runner.safety")
    if s is None:
        return None
    try:
        on = s.armed() is not None
        nets = getattr(s, "_NETWORK_EVENTS", frozenset())
    except Exception:
        return None
    return bool(on and event in nets)


def record(ev: str, args: Sequence[Any] = (), extra: Optional[Dict[str, Any]] = None) -> None:
    """Writes one trace line (also used by the native wrappers). A no-op when the trace is not armed."""
    f = _state["file"]
    if f is None or getattr(_local, "busy", False):
        return
    _local.busy = True
    try:
        obj = {"t": round(time.time(), 6), "pid": os.getpid(), "ev": ev, "args": [_arg(a) for a in args]}
        if extra:
            obj.update(extra)
        line = json.dumps(obj, ensure_ascii=True, separators=(",", ":"))
        with _lock:
            f.write(line + "\n")
            f.flush()
            _state["lines"] += 1
    except Exception:
        _state["errors"] += 1
    finally:
        _local.busy = False


def _hook(event: str, args: Tuple[Any, ...]) -> None:
    try:
        if _state["file"] is None or not wanted(event):
            return
        if event.startswith("socket."):
            record(event, args, {"refused": socket_refused(event)})
        else:
            record(event, args)
    except Exception:                                    # an audit hook must never raise into the app
        _state["errors"] += 1


# ------------------------------------------------------------------------------------------ native wrappers
def _path_of(args: Tuple[Any, ...], kwargs: Dict[str, Any], names: Sequence[str]) -> Any:
    for n in names:
        if n in kwargs:
            v = kwargs[n]
            break
    else:
        v = args[0] if args else None
    if v is None:
        return None
    if isinstance(v, (str, bytes, os.PathLike)):
        return v
    return "<stream>"


_ARG_NAMES = {
    "pymupdf.open": ("filename",), "pymupdf.Document.save": ("filename",),
    "PIL.Image.open": ("fp",), "PIL.Image.save": ("fp",),
    "cv2.imread": ("filename",), "cv2.imwrite": ("filename",),
    "onnxruntime.InferenceSession": ("path_or_bytes",),
}


def _wrap_callable(fn: Callable[..., Any], ev: str, method: bool) -> Callable[..., Any]:
    names = _ARG_NAMES.get(ev, ())

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            rest = args[1:] if method else args
            p = _path_of(rest, kwargs, names)
            if p is not None or ev == "pymupdf.open":
                record(ev, [p])
        except Exception:
            _state["errors"] += 1
        return fn(*args, **kwargs)

    setattr(wrapper, _WRAPPED_MARK, True)
    return wrapper


def patch_module(name: str, module: Any = None) -> List[str]:
    """Wraps the native file functions of one target module (idempotent). Returns the attributes that are wrapped
    now (newly or before); an attribute the module does not have (yet) is left out."""
    targets = (_state["targets"] or NATIVE_TARGETS).get(name, ())
    mod = module if module is not None else sys.modules.get(name)
    done: List[str] = []
    if mod is None:
        return done
    for attr, ev in targets:
        owner_name, _, meth = attr.rpartition(".")
        try:
            owner = getattr(mod, owner_name) if owner_name else mod
            fn = getattr(owner, meth)
        except AttributeError:                             # a module still running its own import
            continue
        try:
            if not getattr(fn, _WRAPPED_MARK, False):
                setattr(owner, meth, _wrap_callable(fn, ev, method=bool(owner_name)))
            done.append(attr)
        except Exception:
            _state["errors"] += 1
    return done


def patch_loaded() -> None:
    """Patches every target module that is in sys.modules and not patched yet (cheap after the first time)."""
    targets = _state["targets"] or NATIVE_TARGETS
    done = _state.setdefault("patched", set())
    for name in targets:
        if name in done:
            continue
        mod = sys.modules.get(name)
        if mod is None:
            continue
        if len(patch_module(name, mod)) == len(targets[name]):
            done.add(name)


class _PatchFinder(importlib.abc.MetaPathFinder):
    """Finds nothing itself: for a target module it asks the other finders, then makes the module's loader patch
    the module right after it ran. Every other import is untouched."""

    def __init__(self) -> None:
        self._busy = threading.local()

    def find_spec(self, fullname: str, path: Any = None, target: Any = None) -> Any:
        targets = _state["targets"] or NATIVE_TARGETS
        if fullname not in targets or getattr(self._busy, fullname.replace(".", "_"), False):
            return None
        flag = fullname.replace(".", "_")
        setattr(self._busy, flag, True)
        try:
            spec = None
            for finder in list(sys.meta_path):
                if finder is self or not hasattr(finder, "find_spec"):
                    continue
                try:
                    spec = finder.find_spec(fullname, path, target)
                except Exception:
                    spec = None
                if spec is not None:
                    break
        finally:
            setattr(self._busy, flag, False)
        if spec is None or spec.loader is None or isinstance(spec.loader, type):
            return spec
        loader = spec.loader
        inner = getattr(loader, "exec_module", None)
        if inner is None or getattr(inner, _WRAPPED_MARK, False):
            return spec

        def exec_module(module: Any, _inner: Callable[[Any], Any] = inner) -> None:
            _inner(module)
            patch_loaded()                                 # cv2 replaces itself in sys.modules while it runs

        setattr(exec_module, _WRAPPED_MARK, True)
        try:
            loader.exec_module = exec_module               # this spec's own loader object only
        except Exception:                                  # a loader without a __dict__: wrap it instead
            try:
                spec.loader = _LoaderProxy(loader, exec_module)
            except Exception:
                _state["errors"] += 1
        return spec


class _LoaderProxy:
    """A loader that runs ``exec_module`` through the patcher and passes everything else to the real loader."""

    def __init__(self, inner: Any, exec_module: Callable[[Any], None]):
        self._inner = inner
        self.exec_module = exec_module

    def create_module(self, spec: Any) -> Any:
        fn = getattr(self._inner, "create_module", None)
        return fn(spec) if fn is not None else None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


_finder: Optional[_PatchFinder] = None


def armed_dir() -> Optional[str]:
    """The trace folder when this process is traced, else None (spawn passes it to children)."""
    return _state["dir"]


def trace_file() -> Optional[str]:
    return _state["path"]


def stats() -> Dict[str, int]:
    return {"lines": int(_state["lines"]), "errors": int(_state["errors"])}


def arm(directory: Any, *, targets: Optional[Dict[str, Tuple[Tuple[str, str], ...]]] = None) -> str:
    """Opens DIR\\trace-<pid>.jsonl, then adds the audit hook (permanent for the process) and the import patcher.
    Idempotent: a second call returns the same file. ``directory`` must be absolute. ``targets`` replaces
    NATIVE_TARGETS (tests)."""
    global _finder
    if _state["file"] is not None:
        return _state["path"]
    d = os.fspath(directory)
    if not os.path.isabs(d):
        raise ValueError("the trace folder must be an absolute path")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"trace-{os.getpid()}.jsonl")
    f = open(path, "a", encoding="ascii", newline="\n")          # opened BEFORE the hook: never traced
    _state.update(dir=d, file=f, path=path, targets=dict(targets) if targets is not None else None)
    record("trace.armed", [sys.executable, bool(getattr(sys, "frozen", False))])
    sys.addaudithook(_hook)
    _finder = _PatchFinder()
    sys.meta_path.insert(0, _finder)
    patch_loaded()
    return path


def read(path: Any) -> List[Dict[str, Any]]:
    """The events of one trace file (bad lines skipped). For the tests and trace_check."""
    out: List[Dict[str, Any]] = []
    with open(path, "r", encoding="ascii", errors="replace") as f:
        for line in f:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if isinstance(d, dict) and "ev" in d:
                out.append(d)
    return out
