"""cardrun: run the FROZEN app on THIS PC's graphics card under gpu_guard (APP_SPEC rev 2, R2.6, S11).

**NEEDS ORCHESTRATOR APPROVAL Q-O1** (APP_SPEC R2.9). gpu_guard's 90 s slots cannot hold a whole app check
(2-4 minutes), so this harness holds gpu_guard's dev lock for the WHOLE run instead. Until the orchestrator has
approved Q-O1, every call refuses: set ``HDC_CARDRUN_APPROVAL=Q-O1`` (or pass ``approval="Q-O1"``) only after
that approval.

What one ``run(cmd, timeout)`` does (R2.6 "Card runs on THIS PC"):
1. Loads ``C:\\Hangeul\\DOCCHECK\\lab\\onnx\\scripts\\gpu_guard.py`` BY PATH (reading the file is its only I/O;
   no bytecode is written) and waits for its conditions inside ``gpu_guard.gpu_slot``: the bot's check not running,
   the dev lock free, at least 4.0 GB of free card memory (up to ``wait_s`` = 1800 s).
2. Holds ``gpu_slot(what, need_free_gb=4.0, wait_s=1800, max_s=<timeout + margin>)`` for the whole run. A watcher
   thread calls ``slot.checkpoint()`` every 30 s to refresh the dev lock. A ``SlotInterrupted`` there (the bot's
   check started) is RECORDED and does not stop the run: the app's own card gate pauses by itself. While the bot
   runs, checkpoint() does not refresh the dev lock, so the watcher refreshes it itself (os.utime) when the lock
   file still names this process.
3. One run at a time (a process-wide lock; across processes the dev lock itself). It starts exactly one child (the
   given command) inside a Windows job object, so on a timeout it ends ONLY that child and the processes the child
   started (the window's worker, the self-test parts); it never touches any other process. The dev lock is released
   in ``finally`` (gpu_slot's own finally).

The app's own card gate enforces the bot's lock and the 3 GB cap INSIDE the run (R2.2); this harness only keeps
other development runs off the card.

The module also holds the small helpers the release harnesses share (parity.py, politeness.py, timing.py,
trace_check.py): the app's paths for a build id, the --cli / --auto commands, the protocol event parser.
Harnesses print case ids, counts, rule ids, page numbers and codes; never text, names or paths.
Development only: never bundled (packaging\\tests is outside src).
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import contextlib  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import subprocess  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
import types  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from datetime import datetime  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple  # noqa: E402

GUARD_PATH = Path(r"C:\Hangeul\DOCCHECK\lab\onnx\scripts\gpu_guard.py")
APPROVAL_ENV = "HDC_CARDRUN_APPROVAL"
APPROVAL_VALUE = "Q-O1"
NO_CARD_ENV = "HDC_TEST_NO_CARD"          # set in unit tests: the real gpu_guard is never loaded
NEED_FREE_GB = 4.0
WAIT_S = 1800
CHECKPOINT_S = 30.0
SLOT_MARGIN_S = 120.0                     # the slot outlives the run's own timeout by this much
KILL_GRACE_S = 10.0

PRIVATE = Path(r"C:\Hangeul\DOCCHECK\lab\private-runner")
BUILDS = Path(r"C:\Hangeul\DOCCHECK\lab\app-build\builds")
APPTEST = PRIVATE / "apptest"
APP_NAME = "Hangeul Document Checker"
APP_EXE = APP_NAME + ".exe"
BOT_LOCK = Path(r"C:\Hangeul\BOT\data\verification\auto_verify.lock")   # stat only, never opened
BOT_LOCK_STALE_S = 4 * 3600
BUILD_ID_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.\-]{0,63}$")

_BELOW_NORMAL = 0x00004000
_CREATE_NO_WINDOW = 0x08000000


class NotApproved(RuntimeError):
    """Q-O1 has not been approved for this run (no HDC_CARDRUN_APPROVAL=Q-O1)."""


class Busy(RuntimeError):
    """Another cardrun.run is active in this process: one run at a time."""


#: What a harness reports as "could not run" (exit 2) instead of a traceback: no approval, a run already active,
#: or gpu_guard found no free card within ``wait_s`` (its TimeoutError).
STOP_ERRORS = (NotApproved, Busy, TimeoutError)


# ---------------------------------------------------------------------------------------------------- the guard

def load_guard(path: Path = GUARD_PATH) -> types.ModuleType:
    """gpu_guard loaded by path, without writing bytecode next to it (lab\\onnx is read-only). Refused while
    HDC_TEST_NO_CARD is set: unit tests always inject a fake guard."""
    if os.environ.get(NO_CARD_ENV):
        raise RuntimeError("the graphics card is off for this test run (HDC_TEST_NO_CARD)")
    source = Path(path).read_text(encoding="utf-8")
    mod = types.ModuleType("cardrun_gpu_guard")
    mod.__file__ = str(path)
    sys.modules["cardrun_gpu_guard"] = mod
    exec(compile(source, str(path), "exec", dont_inherit=True), mod.__dict__)
    return mod


def approved(approval: Optional[str] = None) -> bool:
    return (approval if approval is not None else os.environ.get(APPROVAL_ENV, "")).strip() == APPROVAL_VALUE


def _refresh_own_dev_lock(guard: Any) -> bool:
    """os.utime of gpu_guard's dev lock, only when the file still names THIS process (gpu_slot writes
    "<pid> <what>"). Returns True when refreshed."""
    lock = getattr(guard, "DEV_LOCK", None)
    if lock is None:
        return False
    try:
        with open(lock, "rb") as fh:
            head = fh.read(64).split(b" ", 1)[0]
        if head != str(os.getpid()).encode("ascii"):
            return False
        os.utime(lock)
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------------------------- Windows job object

class _Job:
    """A job object with KILL_ON_JOB_CLOSE around the one child: a timeout ends the child and only the processes it
    started. Off (every call a no-op) where the API is missing or refuses; ``note`` says why."""

    def __init__(self) -> None:
        self.handle = None
        self.note = ""
        if os.name != "nt":
            self.note = "no job object (not Windows)"
            return
        try:
            import ctypes
            from ctypes import wintypes
            self._ct = ctypes
            k = ctypes.WinDLL("kernel32", use_last_error=True)
            k.CreateJobObjectW.restype = wintypes.HANDLE
            k.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
            k.SetInformationJobObject.restype = wintypes.BOOL
            k.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
            k.AssignProcessToJobObject.restype = wintypes.BOOL
            k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
            k.TerminateJobObject.restype = wintypes.BOOL
            k.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
            k.QueryInformationJobObject.restype = wintypes.BOOL
            k.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                                    ctypes.c_void_p]
            k.CloseHandle.restype = wintypes.BOOL
            k.CloseHandle.argtypes = [wintypes.HANDLE]
            self._k = k
            h = k.CreateJobObjectW(None, None)
            if not h:
                self.note = f"CreateJobObject failed ({ctypes.get_last_error()})"
                return
            info = _ext_limit_type()()
            info.BasicLimitInformation.LimitFlags = 0x2000          # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not k.SetInformationJobObject(h, 9, ctypes.byref(info), ctypes.sizeof(info)):
                self.note = f"SetInformationJobObject failed ({ctypes.get_last_error()})"
                k.CloseHandle(h)
                return
            self.handle = h
        except Exception as exc:                                       # no job: plain kill of the child only
            self.note = f"no job object ({type(exc).__name__})"
            self.handle = None

    def assign(self, proc: subprocess.Popen) -> bool:
        if not self.handle:
            return False
        handle = getattr(proc, "_handle", None)
        if handle is None or not self._k.AssignProcessToJobObject(self.handle, int(handle)):
            self.note = "the child could not be put in the job object; a timeout ends the child only"
            self.close()
            return False
        return True

    def active(self) -> Optional[int]:
        if not self.handle:
            return None
        acc = _accounting_type()()
        if not self._k.QueryInformationJobObject(self.handle, 1, self._ct.byref(acc), self._ct.sizeof(acc), None):
            return None
        return int(acc.ActiveProcesses)

    def peak_process_commit(self) -> Optional[int]:
        if not self.handle:
            return None
        info = _ext_limit_type()()
        if not self._k.QueryInformationJobObject(self.handle, 9, self._ct.byref(info), self._ct.sizeof(info), None):
            return None
        return int(info.PeakProcessMemoryUsed)

    def terminate(self, code: int = 1) -> bool:
        return bool(self.handle) and bool(self._k.TerminateJobObject(self.handle, code))

    def close(self) -> None:
        if self.handle:
            self._k.CloseHandle(self.handle)          # KILL_ON_JOB_CLOSE: nothing of ours outlives this
            self.handle = None


_TYPES: Dict[str, Any] = {}


def _ext_limit_type():
    if "ext" not in _TYPES:
        import ctypes
        from ctypes import wintypes

        class BasicLimit(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class IoCounters(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in ("ReadOperationCount", "WriteOperationCount",
                                                         "OtherOperationCount", "ReadTransferCount",
                                                         "WriteTransferCount", "OtherTransferCount")]

        class ExtLimit(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BasicLimit), ("IoInfo", IoCounters),
                        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]
        _TYPES["ext"] = ExtLimit
    return _TYPES["ext"]


def _accounting_type():
    if "acc" not in _TYPES:
        import ctypes
        from ctypes import wintypes

        class Accounting(ctypes.Structure):
            _fields_ = [("TotalUserTime", ctypes.c_int64), ("TotalKernelTime", ctypes.c_int64),
                        ("ThisPeriodTotalUserTime", ctypes.c_int64), ("ThisPeriodTotalKernelTime", ctypes.c_int64),
                        ("TotalPageFaultCount", wintypes.DWORD), ("TotalProcesses", wintypes.DWORD),
                        ("ActiveProcesses", wintypes.DWORD), ("TotalTerminatedProcesses", wintypes.DWORD)]
        _TYPES["acc"] = Accounting
    return _TYPES["acc"]


def process_memory(proc: subprocess.Popen) -> Tuple[Optional[int], Optional[int]]:
    """(peak working set, peak commit) in bytes of the child, by GetProcessMemoryInfo on its own handle (works
    after it exited while the handle is open); (None, None) where unavailable."""
    handle = getattr(proc, "_handle", None)
    if os.name != "nt" or handle is None:
        return None, None
    try:
        import ctypes
        from ctypes import wintypes
        if "pmc" not in _TYPES:
            class Pmc(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                            ("PeakPagefileUsage", ctypes.c_size_t), ("PrivateUsage", ctypes.c_size_t)]
            k = ctypes.WinDLL("kernel32", use_last_error=True)
            k.K32GetProcessMemoryInfo.restype = wintypes.BOOL
            k.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
            _TYPES["pmc"] = (Pmc, k)
        pmc_type, k = _TYPES["pmc"]
        pmc = pmc_type()
        pmc.cb = ctypes.sizeof(pmc)
        if not k.K32GetProcessMemoryInfo(int(handle), ctypes.byref(pmc), pmc.cb):
            return None, None
        return int(pmc.PeakWorkingSetSize), int(pmc.PeakPagefileUsage)
    except Exception:
        return None, None


def pid_alive(pid: int) -> bool:
    """True while a process with this id runs (OpenProcess + WaitForSingleObject(0)); used by the tests to prove
    that nothing of a run was left behind."""
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    import ctypes
    from ctypes import wintypes
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenProcess.restype = wintypes.HANDLE
    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.WaitForSingleObject.restype = wintypes.DWORD
    k.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    h = k.OpenProcess(0x00100000 | 0x1000, False, int(pid))      # SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    try:
        return k.WaitForSingleObject(h, 0) == 0x102                 # WAIT_TIMEOUT: still running
    finally:
        k.CloseHandle(h)


# ---------------------------------------------------------------------------------------------------- the run

@dataclass
class CardRunResult:
    """What one run gave. ``lines`` are the child's stdout lines with the seconds since its start (the app's
    protocol events; they can hold personal data, so harnesses never print them)."""
    args: List[str]
    returncode: Optional[int] = None
    lines: List[Tuple[float, str]] = field(default_factory=list)
    stderr_bytes: int = 0
    seconds: float = 0.0                       # child start -> exit
    slot_wait_s: float = 0.0                   # waiting inside gpu_slot before the start
    timed_out: bool = False
    interrupted: int = 0                       # SlotInterrupted seen by the watcher (the bot's check started)
    expired: int = 0                           # SlotExpired seen by the watcher
    checkpoints: int = 0
    lock_refreshed: int = 0                    # dev-lock refreshes done by the watcher itself
    watcher_errors: List[str] = field(default_factory=list)
    callback_errors: List[str] = field(default_factory=list)
    peak_working_set: Optional[int] = None     # bytes, the child process
    peak_commit: Optional[int] = None          # bytes, the child process
    job_peak_process_commit: Optional[int] = None   # bytes, the largest process of the job (child or its workers)
    leftover_killed: int = 0                   # processes of the job still alive after the child ended
    job_note: str = ""

    @property
    def stdout(self) -> str:
        return "\n".join(line for _, line in self.lines)

    def stats(self) -> Dict[str, Any]:
        """Counts and seconds only (no lines): safe for reports."""
        mb = (lambda b: None if b is None else round(b / 2 ** 20, 1))
        return {"returncode": self.returncode, "seconds": round(self.seconds, 2),
                "slot_wait_s": round(self.slot_wait_s, 2), "timed_out": self.timed_out,
                "interrupted": self.interrupted, "expired": self.expired, "checkpoints": self.checkpoints,
                "lock_refreshed": self.lock_refreshed, "watcher_errors": list(self.watcher_errors),
                "callback_errors": list(self.callback_errors), "lines": len(self.lines),
                "stderr_bytes": self.stderr_bytes, "peak_working_set_mb": mb(self.peak_working_set),
                "peak_commit_mb": mb(self.peak_commit),
                "job_peak_process_commit_mb": mb(self.job_peak_process_commit),
                "leftover_killed": self.leftover_killed, "job_note": self.job_note}


class Control:
    """Handed to the callbacks: write to the child's stdin (politeness' cancel), its pid, seconds since start."""

    def __init__(self, proc: subprocess.Popen, t0: float, clock: Callable[[], float]) -> None:
        self._proc = proc
        self._t0 = t0
        self._clock = clock
        self._lock = threading.Lock()
        self.pid = proc.pid

    def elapsed(self) -> float:
        return self._clock() - self._t0

    def write(self, text: str) -> bool:
        with self._lock:
            stream = self._proc.stdin
            if stream is None:
                return False
            try:
                stream.write(text.encode("ascii"))
                stream.flush()
                return True
            except (OSError, ValueError):
                return False


_ONE_RUN = threading.Lock()


def run(cmd: Sequence[str], timeout: float, *, what: Optional[str] = None, cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None, stdin: bool = False,
        on_line: Optional[Callable[[float, str, Control], None]] = None,
        on_tick: Optional[Callable[[float, Control], None]] = None, tick_s: float = 0.25,
        log_dir: Optional[Path] = None, guard: Any = None, approval: Optional[str] = None,
        need_free_gb: float = NEED_FREE_GB, wait_s: float = WAIT_S, checkpoint_s: float = CHECKPOINT_S,
        below_normal: bool = True, clock: Callable[[], float] = time.monotonic) -> CardRunResult:
    """Run ``cmd`` (the frozen app) while holding gpu_guard's slot; see the module docstring.

    ``on_line(t, line, ctl)`` gets every stdout line as it comes (t = seconds since the child started), from a
    reader thread; ``on_tick(t, ctl)`` is called about every ``tick_s`` from this thread while the child runs.
    Callback exceptions are recorded, never fatal. ``log_dir`` (private, under apptest) receives stdout.log,
    stderr.log and cardrun.json. ``guard`` replaces gpu_guard (unit tests)."""
    if not approved(approval):
        raise NotApproved("cardrun needs the orchestrator's approval Q-O1 (APP_SPEC R2.9): set "
                          f"{APPROVAL_ENV}={APPROVAL_VALUE} only after it was given")
    if not _ONE_RUN.acquire(blocking=False):
        raise Busy("cardrun: one run at a time")
    try:
        g = guard if guard is not None else load_guard()
        what = what or "hdc frozen app (cardrun)"
        result = CardRunResult(args=[str(c) for c in cmd])
        t_ask = clock()
        with g.gpu_slot(what, need_free_gb=need_free_gb, wait_s=wait_s,
                        max_s=float(timeout) + SLOT_MARGIN_S) as slot:
            result.slot_wait_s = clock() - t_ask
            _run_child(result, cmd, timeout, g, slot, cwd=cwd, env=env, stdin=stdin, on_line=on_line,
                       on_tick=on_tick, tick_s=tick_s, log_dir=log_dir, checkpoint_s=checkpoint_s,
                       below_normal=below_normal, clock=clock)
        if log_dir is not None:
            _write_json(Path(log_dir) / "cardrun.json", result.stats())
        return result
    finally:
        _ONE_RUN.release()


def _run_child(result: CardRunResult, cmd: Sequence[str], timeout: float, g: Any, slot: Any, *, cwd, env, stdin,
               on_line, on_tick, tick_s, log_dir, checkpoint_s, below_normal, clock) -> None:
    flags = 0
    if os.name == "nt":
        flags = _CREATE_NO_WINDOW | (_BELOW_NORMAL if below_normal else 0)
    err_fh = None
    out_fh = None
    if log_dir is not None:
        Path(log_dir).mkdir(parents=True, exist_ok=True)
        err_fh = open(Path(log_dir) / "stderr.log", "wb")
        out_fh = open(Path(log_dir) / "stdout.log", "w", encoding="utf-8", newline="\n")
    stop = threading.Event()
    job = _Job()
    proc = None
    try:
        proc = subprocess.Popen([str(c) for c in cmd], cwd=cwd, env=env,
                                stdin=subprocess.PIPE if stdin else subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags)
        t0 = clock()
        job.assign(proc)
        result.job_note = job.note
        ctl = Control(proc, t0, clock)

        def read_out() -> None:
            for raw in iter(proc.stdout.readline, b""):
                t = clock() - t0
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                result.lines.append((t, line))
                if out_fh is not None:
                    out_fh.write(line + "\n")
                if on_line is not None:
                    try:
                        on_line(t, line, ctl)
                    except Exception as exc:                  # a harness bug never kills the run
                        result.callback_errors.append(f"on_line: {type(exc).__name__}: {exc}"[:200])

        def read_err() -> None:
            for raw in iter(lambda: proc.stderr.read1(65536), b""):
                result.stderr_bytes += len(raw)
                if err_fh is not None:
                    err_fh.write(raw)

        def watch() -> None:
            while not stop.wait(checkpoint_s):
                result.checkpoints += 1
                try:
                    slot.checkpoint()
                except Exception as exc:
                    name = type(exc).__name__
                    if name == "SlotInterrupted" or isinstance(exc, getattr(g, "SlotInterrupted", ())):
                        result.interrupted += 1
                    elif name == "SlotExpired" or isinstance(exc, getattr(g, "SlotExpired", ())):
                        result.expired += 1
                    else:
                        result.watcher_errors.append(name)
                    if _refresh_own_dev_lock(g):
                        result.lock_refreshed += 1

        threads = [threading.Thread(target=read_out, name="cardrun-out", daemon=True),
                   threading.Thread(target=read_err, name="cardrun-err", daemon=True),
                   threading.Thread(target=watch, name="cardrun-watch", daemon=True)]
        for th in threads:
            th.start()
        while proc.poll() is None:
            now = clock() - t0
            if now > timeout:
                result.timed_out = True
                if not job.terminate(1):
                    with contextlib.suppress(OSError):
                        proc.kill()
                break
            if on_tick is not None:
                try:
                    on_tick(now, ctl)
                except Exception as exc:
                    result.callback_errors.append(f"on_tick: {type(exc).__name__}: {exc}"[:200])
            _sample(result, proc, job)
            time.sleep(tick_s)
        try:
            proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(OSError):
                proc.kill()
            proc.wait(timeout=60)
        result.seconds = clock() - t0
        result.returncode = proc.returncode
        _sample(result, proc, job)
        _end_leftovers(result, job)
        threads[0].join(timeout=30)
        threads[1].join(timeout=30)
    finally:
        stop.set()
        if proc is not None:
            if proc.poll() is None:                            # only on an error in this function
                if not job.terminate(1):
                    with contextlib.suppress(OSError):
                        proc.kill()
                with contextlib.suppress(Exception):
                    proc.wait(timeout=60)
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                if stream is not None:
                    with contextlib.suppress(OSError):
                        stream.close()
        job.close()
        for fh in (err_fh, out_fh):
            if fh is not None:
                fh.close()


def _sample(result: CardRunResult, proc: subprocess.Popen, job: _Job) -> None:
    ws, commit = process_memory(proc)
    if ws is not None:
        result.peak_working_set = max(ws, result.peak_working_set or 0)
    if commit is not None:
        result.peak_commit = max(commit, result.peak_commit or 0)
    jp = job.peak_process_commit()
    if jp is not None:
        result.job_peak_process_commit = max(jp, result.job_peak_process_commit or 0)


def _end_leftovers(result: CardRunResult, job: _Job) -> None:
    """Processes the child started that still run after it ended (in the job only): a grace, then ended."""
    deadline = time.monotonic() + KILL_GRACE_S
    n = job.active()
    while n and time.monotonic() < deadline:
        time.sleep(0.25)
        n = job.active()
    if n:
        result.leftover_killed = n
        job.terminate(1)


# ------------------------------------------------------------------------------------------ shared harness helpers

def check_build_id(build_id: str) -> str:
    if not BUILD_ID_RE.match(build_id or ""):
        raise ValueError("a build id is letters, digits, '.' and '-' only")
    return build_id


def app_dir_for(build_id: Optional[str] = None, app_dir: Optional[str] = None) -> Path:
    """The frozen app folder: --app-dir as given, else builds\\<id>\\dist\\Hangeul Document Checker."""
    if app_dir:
        return Path(app_dir)
    if not build_id:
        raise ValueError("give --build-id or --app-dir")
    return BUILDS / check_build_id(build_id) / "dist" / APP_NAME


def reports_dir_for(build_id: Optional[str]) -> Optional[Path]:
    return BUILDS / check_build_id(build_id) / "reports" if build_id else None


def apptest_dir(build_id: Optional[str], app_dir: Optional[str] = None) -> Path:
    """private-runner\\apptest\\<build-id> (or ...\\apptest\\local when only --app-dir is known)."""
    return APPTEST / (check_build_id(build_id) if build_id else "local")


def new_run_dir(base: Path, prefix: str, now: Optional[datetime] = None) -> Path:
    """A NEW folder base\\<prefix>-<YYYYmmdd-HHMMSS>[-n]; earlier runs are never deleted or reused."""
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    base.mkdir(parents=True, exist_ok=True)
    for n in range(0, 1000):
        d = base / (f"{prefix}-{stamp}" + (f"-{n}" if n else ""))
        try:
            d.mkdir()
            return d
        except FileExistsError:
            continue
    raise RuntimeError("no free run folder name")


def app_env(data_dir: Optional[Path] = None, base: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """The child's environment: this one, minus the unit-test switches, plus HDC_DATA_DIR."""
    env = dict(os.environ if base is None else base)
    for k in ("HDC_TEST_NO_CARD", "HDC_MODE", "HDC_BUNDLE_DIR", "HDC_CODE_ROOT", "HDC_DATA_DIR", APPROVAL_ENV):
        env.pop(k, None)
    if data_dir is not None:
        env["HDC_DATA_DIR"] = str(data_dir)
    return env


def cli_cmd(exe: Path, input_path: str, program: str, out: Path, today: Optional[str], data_dir: Path,
            extra_locks: Sequence[Path] = ()) -> List[str]:
    """R2.3.3: --cli <input> --program P --out DIR [--today D] --data-dir DIR [--gpu-lock-extra PATH]..."""
    cmd = [str(exe), "--cli", str(input_path), "--program", program, "--out", str(out)]
    if today:
        cmd += ["--today", today]
    cmd += ["--data-dir", str(data_dir)]
    for p in extra_locks:
        cmd += ["--gpu-lock-extra", str(p)]
    return cmd


def auto_cmd(exe: Path, input_path: str, program: str, today: Optional[str], report_root: Path, auto_result: Path,
             data_dir: Path, trace_dir: Optional[Path] = None) -> List[str]:
    """Rev 1 §1.6 step 3: the real window -> worker path in automatic mode."""
    cmd = [str(exe), "--auto", str(input_path), "--program", program]
    if today:
        cmd += ["--today", today]
    cmd += ["--report-root", str(report_root), "--auto-result", str(auto_result), "--data-dir", str(data_dir)]
    if trace_dir is not None:
        cmd += ["--trace-opens", str(trace_dir)]
    return cmd


def parse_event(line: str) -> Optional[Dict[str, Any]]:
    """One protocol line ({"v":1,"ev":...}) as a dict; None for anything else."""
    s = (line or "").strip()
    if not s.startswith("{"):
        return None
    try:
        obj = json.loads(s)
    except ValueError:
        return None
    if not isinstance(obj, dict) or "ev" not in obj:
        return None
    return obj


def events_of(lines: Sequence[Tuple[float, str]]) -> List[Tuple[float, Dict[str, Any]]]:
    out = []
    for t, line in lines:
        ev = parse_event(line)
        if ev is not None:
            out.append((t, ev))
    return out


def first(events: Sequence[Tuple[float, Dict[str, Any]]], ev: str, **match: Any) -> Optional[Tuple[float, dict]]:
    for t, e in events:
        if e.get("ev") == ev and all(e.get(k) == v for k, v in match.items()):
            return t, e
    return None


def done_event(events: Sequence[Tuple[float, Dict[str, Any]]]) -> Optional[Dict[str, Any]]:
    for _, e in reversed(list(events)):
        if e.get("ev") == "done":
            return e
    return None


def error_codes(events: Sequence[Tuple[float, Dict[str, Any]]]) -> List[str]:
    return [str(e.get("code")) for _, e in events if e.get("ev") == "error"]


def bot_lock_active(now: Optional[float] = None, path: Path = BOT_LOCK) -> bool:
    """The REAL bot lock, by os.stat only (never opened, listed or written)."""
    try:
        st = os.stat(path)
    except OSError:
        return False
    return ((now if now is not None else time.time()) - st.st_mtime) < BOT_LOCK_STALE_S


def read_json(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _write_json(path: Path, obj: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, indent=1, ensure_ascii=True, sort_keys=False)
    os.replace(tmp, path)


write_json = _write_json
