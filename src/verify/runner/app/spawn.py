"""Starting the app's own child processes (APP_SPEC rev 1 section 2, revision 2 R2.3.3).

Parent side (the window, the self-test parent):
- ``self_command(*args)``: the command that starts THIS app again with ``args`` (frozen: the exe itself; DEV: the
  current Python with ``-I -B -m src.verify.runner.app.entry``), plus ``--trace-opens DIR`` when this process is
  traced (test 8 c).
- ``child_env(index)``: the environment of a card role: ``gpuinfo.card_env(os.environ, index)`` (no PYTORCH_*,
  TORCH_*, CUBLAS_*, CUDNN_*, CUDA_* or NVIDIA_TF32_OVERRIDE; CUDA_DEVICE_ORDER=PCI_BUS_ID,
  CUDA_VISIBLE_DEVICES=<index>) plus OPENBLAS_NUM_THREADS=1 unless set, and PYMUPDF_MESSAGE=fd:2.
- ``start_process(cmd, ...)`` / ``start_worker(job_path, log_path, index)``: BELOW_NORMAL priority, no console
  window, stdin and stdout as pipes, stderr into the worker log. ``terminate(proc)`` ends only that child
  (TerminateProcess through its own handle).

Child side (``--worker`` and ``--cli``): ``claim_protocol_stdout()`` duplicates fd 1 for the protocol and points fd 1
at fd 2, so nothing else (a library's print, a native message) can write into the protocol pipe.

Standard library only; ``gpuinfo`` (Task E) is imported lazily. No personal data: nothing here logs paths.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, BinaryIO, Callable, Dict, List, Mapping, MutableMapping, Optional, Sequence

BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
CREATE_NO_WINDOW = 0x08000000
DEV_MODULE = "src.verify.runner.app.entry"
CARD_PREFIXES = ("PYTORCH_", "TORCH_", "CUBLAS_", "CUDNN_", "CUDA_")
CARD_NAMES = ("NVIDIA_TF32_OVERRIDE",)


def _frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def self_command(*args: Any, frozen: Optional[bool] = None, executable: Optional[str] = None,
                 trace_dir: Optional[str] = "auto") -> List[str]:
    """The argv that starts this app again with ``args``. ``trace_dir`` "auto" passes this process's trace
    folder on (``trace.armed_dir()``); None passes none."""
    frozen = _frozen() if frozen is None else frozen
    exe = executable or sys.executable
    cmd = [exe] if frozen else [exe, "-I", "-B", "-m", DEV_MODULE]
    if trace_dir == "auto":
        try:
            from . import trace
            trace_dir = trace.armed_dir()
        except Exception:
            trace_dir = None
    if trace_dir:
        cmd += ["--trace-opens", str(trace_dir)]
    cmd += [str(a) for a in args]
    return cmd


def is_card_var(key: str) -> bool:
    k = str(key).upper()
    return k.startswith(CARD_PREFIXES) or k in CARD_NAMES


def apply_env(target: MutableMapping[str, str], new: Mapping[str, str]) -> List[str]:
    """Makes ``target`` follow ``gpuinfo.card_env``'s answer ``new``: card variables that ``new`` lacks are
    deleted, then ``new`` is applied. A NON-card variable that ``new`` lacks is kept, so a reply that holds only
    the changes can never empty the environment. Returns the deleted names (names only)."""
    gone = sorted(k for k in list(target.keys()) if k not in new and is_card_var(k))
    for k in gone:
        del target[k]
    for k, v in new.items():
        if target.get(k) != v:
            target[k] = v
    return gone


def _gpuinfo() -> Any:
    from . import gpuinfo                                  # Task E
    return gpuinfo


def child_env(index: int = 0, base: Optional[Mapping[str, str]] = None, *, gpuinfo: Any = None) -> Dict[str, str]:
    """The full environment of a card role's child (the worker, ``--selftest-part card``)."""
    env: Dict[str, str] = dict(os.environ if base is None else base)
    gi = gpuinfo if gpuinfo is not None else _gpuinfo()
    apply_env(env, gi.card_env(dict(env), int(index)))
    env.setdefault("OPENBLAS_NUM_THREADS", "1")
    env["PYMUPDF_MESSAGE"] = "fd:2"
    return env


def start_process(cmd: Sequence[str], *, log_path: Optional[Any] = None, env: Optional[Mapping[str, str]] = None,
                  cwd: Optional[Any] = None, below_normal: bool = True, stdin: Any = subprocess.PIPE,
                  stdout: Any = subprocess.PIPE, popen: Callable[..., Any] = subprocess.Popen) -> Any:
    """Starts one child: BELOW_NORMAL priority (unless ``below_normal`` is False), no console window, binary
    pipes, stderr appended to ``log_path`` (or discarded). The parent's copy of the log handle is closed at once."""
    flags = CREATE_NO_WINDOW | (BELOW_NORMAL_PRIORITY_CLASS if below_normal else 0)
    log = None
    if log_path is not None:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        log = open(log_path, "ab")
    try:
        return popen([str(c) for c in cmd], stdin=stdin, stdout=stdout,
                     stderr=log if log is not None else subprocess.DEVNULL,
                     env=dict(env) if env is not None else None, cwd=str(cwd) if cwd is not None else None,
                     creationflags=flags, close_fds=True)
    finally:
        if log is not None:
            log.close()


def start_worker(job_path: Any, log_path: Any, index: int = 0, *, cwd: Optional[Any] = None,
                 popen: Callable[..., Any] = subprocess.Popen, gpuinfo: Any = None) -> Any:
    """The window's worker for one check: ``<app> --worker <job.json>`` with the card environment."""
    return start_process(self_command("--worker", os.path.abspath(str(job_path))), log_path=log_path,
                         env=child_env(index, gpuinfo=gpuinfo), cwd=cwd, popen=popen)


def terminate(proc: Any, wait_s: float = 5.0) -> Optional[int]:
    """Ends THIS child (Popen.terminate = TerminateProcess on its own handle); never any other process. Returns
    its exit code, or None if it did not end within ``wait_s``."""
    try:
        if proc.poll() is None:
            proc.terminate()
    except OSError:
        pass
    try:
        return proc.wait(timeout=wait_s)
    except subprocess.TimeoutExpired:
        return None


def priority_class(proc: Any) -> Optional[int]:
    """The Windows priority class of a child we started (tests; None when unknown)."""
    try:
        import ctypes
        handle = int(getattr(proc, "_handle"))
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetPriorityClass.argtypes = [ctypes.c_void_p]
        k32.GetPriorityClass.restype = ctypes.c_uint32
        v = int(k32.GetPriorityClass(ctypes.c_void_p(handle)))
        return v or None
    except Exception:
        return None


def lower_own_priority() -> bool:
    """BELOW_NORMAL for this process (``--cli`` runs like the window's worker). True when it worked."""
    try:
        import ctypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetCurrentProcess.restype = ctypes.c_void_p
        k32.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        k32.SetPriorityClass.restype = ctypes.c_int
        return bool(k32.SetPriorityClass(k32.GetCurrentProcess(), BELOW_NORMAL_PRIORITY_CLASS))
    except Exception:
        return False


# ------------------------------------------------------------------------------------------------ child side
def claim_protocol_stdout() -> Optional[BinaryIO]:
    """In the worker, before anything can print: duplicates fd 1 (the protocol pipe) and points fd 1 at fd 2 (the
    worker log), so ``print`` and native writes to stdout land in the log. Returns an unbuffered binary stream on
    the duplicate, or None when this process has no stdout. Sets PYMUPDF_MESSAGE=fd:2."""
    os.environ["PYMUPDF_MESSAGE"] = "fd:2"
    try:
        if sys.stdout is not None:
            sys.stdout.flush()
    except (OSError, ValueError):
        pass
    try:
        proto = os.dup(1)                                  # not inheritable (PEP 446): no grandchild gets the pipe
    except OSError:
        return None
    try:
        os.dup2(2, 1)
    except OSError:
        try:
            nul = os.open(os.devnull, os.O_WRONLY)
            os.dup2(nul, 1)
            os.close(nul)
        except OSError:
            pass
    _point_std_output_at_fd1()
    return os.fdopen(proto, "wb", buffering=0)


def _point_std_output_at_fd1() -> None:
    """Native code that writes through GetStdHandle(STD_OUTPUT_HANDLE) instead of the C runtime's fd 1 is sent to
    the same place as fd 1 now (the log)."""
    try:
        import ctypes
        import msvcrt
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.SetStdHandle.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
        k32.SetStdHandle.restype = ctypes.c_int
        k32.SetStdHandle(ctypes.c_uint32(-11 & 0xFFFFFFFF), ctypes.c_void_p(msvcrt.get_osfhandle(1)))
    except Exception:
        pass
