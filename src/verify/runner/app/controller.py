"""The window's check controller: one worker per check, its events, Cancel and the final outcome (APP_SPEC rev 1
sections 2, 3.2 and 3.3; revision 2 R2.3.2 and R2.3.3).

Headless and Tk-free: the window calls ``start(job_path)``, then ``poll()`` from ``root.after(100)`` (only the main
thread touches the state; a reader thread only fills a queue), shows ``state``, ``progress`` and finally ``outcome``.

States: ``idle`` -> ``checking`` <-> ``waiting`` -> ``done``.
- ``gpu_wait`` start / tick / end drive ``waiting`` (the waiting banner; the progress bar paused); a ``page`` event
  or a stage start also ends a wait whose "end" was lost.
- ``cancel()`` writes ``cancel`` on the worker's stdin; the worker's listener raises ``hooks.Cancelled`` at its next
  event (during a wait: the gate's next tick, within 2 s). If the worker is still alive ``CANCEL_GRACE_S`` (10 s)
  later, ``poll`` ends it with TerminateProcess: our own child only, never another process.
- The outcome maps the worker's exit code and its error event to a messages.py code: exit 6 carries the error
  event's card code (one of the six; anything else is CARD_FAILED); Windows status codes 0xC000001D, 0xC0000017,
  0xC0000135 / 0xC0000139 map to CPU_UNSUPPORTED, LOW_MEMORY and MISSING_PART, any other 0xC... to CRASH; a
  worker that never said hello is STARTUP_FAILED.
- The case folder: the window marks the folder it created with ``mark_case_dir`` (``.hdc-run-<job>``). After a
  cancel the controller removes that folder (only when the marker of THIS job is in it); after a refusal (exits 2,
  5, 6) it removes it when nothing but the marker is inside; otherwise it removes the marker only.

Standard library only. No personal data is logged: events stay in memory for the window.
"""
from __future__ import annotations

import queue
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import protocol

IDLE, CHECKING, WAITING, DONE = "idle", "checking", "waiting", "done"
CANCEL_GRACE_S = 10.0          # rev 1 section 2: TerminateProcess after 10 s
EXIT_WAIT_S = 2.0              # after the worker ended, how long its last lines may take to arrive
MARKER_PREFIX = ".hdc-run-"
NTSTATUS_CODES = {
    0xC000001D: protocol.CPU_UNSUPPORTED,       # illegal instruction
    0xC0000017: protocol.LOW_MEMORY,            # no memory
    0xC0000135: protocol.MISSING_PART,          # DLL not found
    0xC0000139: protocol.MISSING_PART,          # entry point not found
}
REFUSAL_EXITS = (protocol.EXIT_REFUSED, protocol.EXIT_PLATFORM, protocol.EXIT_CARD)


@dataclass
class Outcome:
    kind: str                                  # "done", "error" or "cancelled"
    code: Optional[str]                        # None when done; else a messages.py code
    exit: Optional[int]                        # the worker's exit code (None: it never started)
    values: Dict[str, Any] = field(default_factory=dict)
    text: str = ""                             # the worker's scrubbed error text
    done: Optional[Dict[str, Any]] = None      # the done event
    forced: bool = False                       # ended with TerminateProcess after Cancel
    stop: bool = False                         # done with STOP


@dataclass
class Progress:
    stage: Optional[str] = None                # the last stage started ("card", "firstread", "ocr", ...)
    stages: Dict[str, Dict[str, Any]] = field(default_factory=dict)   # key -> {"state", "seconds"}
    page_done: int = 0
    page_of: int = 0
    page_where: Optional[str] = None           # "card" (phase 1) or "runner"
    page_seconds: List[float] = field(default_factory=list)
    files: List[Dict[str, Any]] = field(default_factory=list)
    budgets: List[Dict[str, Any]] = field(default_factory=list)
    card: Dict[str, Any] = field(default_factory=dict)            # card found + ready fields
    wait: Optional[Dict[str, Any]] = None      # the current gpu_wait: why, lock, where, waited_s, free_mb, need_mb
    waits: int = 0
    waited_s: float = 0.0


def classify(exit_code: Optional[int], *, hello: bool, error: Optional[Dict[str, Any]] = None,
             done: Optional[Dict[str, Any]] = None, cancelled: bool = False, forced: bool = False,
             stage: Optional[str] = None) -> Outcome:
    """The outcome of one worker from its exit code and events (rev 1 section 3.3 "Windows exit codes", R2.3.3)."""
    base: Dict[str, Any] = {"stage": stage} if stage else {}
    if forced:
        return Outcome("cancelled", protocol.CANCELLED, exit_code, dict(base), forced=True)
    u = None if exit_code is None else int(exit_code) & 0xFFFFFFFF
    if u is not None and u >= 0xC0000000:
        vals = dict(base, status=f"0x{u:08X}")
        return Outcome("error", NTSTATUS_CODES.get(u, protocol.CRASH), exit_code, vals)
    if not hello:
        return Outcome("error", protocol.STARTUP_FAILED, exit_code, dict(base))
    if u in (protocol.EXIT_OK, protocol.EXIT_STOP) and done is not None:
        stop = u == protocol.EXIT_STOP or str(done.get("overall", "")).upper() == "STOP"
        return Outcome("done", None, exit_code, {}, "", done, stop=stop)
    if u == protocol.EXIT_CANCELLED or cancelled:
        return Outcome("cancelled", protocol.CANCELLED, exit_code, dict(base))
    err_code = str(error.get("code") or "") if error else ""
    err_vals = dict(error.get("values") or {}) if error and isinstance(error.get("values"), dict) else {}
    text = str(error.get("text") or "") if error else ""
    if u == protocol.EXIT_CARD:
        code = err_code if err_code in protocol.CARD_CODES else protocol.CARD_FAILED
        vals = {**base, **err_vals}
        return Outcome("error", code, exit_code, vals, text)
    if err_code:
        return Outcome("error", err_code, exit_code, {**base, **err_vals}, text)
    fallback = protocol.STARTUP_FAILED if u == protocol.EXIT_PLATFORM else protocol.CRASH
    return Outcome("error", fallback, exit_code, dict(base), text)


# ------------------------------------------------------------------------------------------ the case folder
def case_marker(job_id: str) -> str:
    return MARKER_PREFIX + str(job_id)


def mark_case_dir(case_dir: Any, job_id: str) -> Path:
    """Marks a case folder as created by this check (the window calls it right after creating the folder)."""
    p = Path(case_dir) / case_marker(job_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"")
    return p


def remove_case_dir_if_ours(case_dir: Any, job_id: str, *, only_if_empty: bool = False) -> bool:
    """Removes the case folder only when THIS job's marker is in it (``only_if_empty``: and nothing else).
    Returns True when the folder is gone."""
    d = Path(case_dir)
    marker = d / case_marker(job_id)
    try:
        if not marker.is_file():
            return False
        if only_if_empty and any(x.name != marker.name for x in d.iterdir()):
            return False
    except OSError:
        return False
    for _ in range(4):
        shutil.rmtree(d, ignore_errors=True)
        if not d.exists():
            return True
        time.sleep(0.25)                                 # a virus scanner may hold a file for a moment
    return not d.exists()


def remove_marker(case_dir: Any, job_id: str) -> None:
    try:
        (Path(case_dir) / case_marker(job_id)).unlink()
    except OSError:
        pass


# ----------------------------------------------------------------------------------------------- the reader
def _read_lines(stream: Any, q: "queue.Queue[Any]") -> None:
    skipping = False
    try:
        while True:
            raw = stream.readline(protocol.MAX_LINE + 1)
            if not raw:
                break
            if skipping:
                skipping = not raw.endswith(b"\n")
                continue
            if len(raw) > protocol.MAX_LINE and not raw.endswith(b"\n"):
                skipping = True
                q.put(("bad", None))
                continue
            ev = protocol.decode(raw)
            q.put(("ev", ev) if ev is not None else ("bad", None))
    except (OSError, ValueError):
        pass
    finally:
        q.put(("eof", None))


def _default_starter(job_path: Any, log_path: Any, index: int) -> Any:
    from . import spawn
    return spawn.start_worker(job_path, log_path, index)


def _default_log_path(job_id: str) -> Path:
    from . import boot
    return boot.data_dir() / "logs" / f"worker-{job_id}.log"


class CheckController:
    """One check at a time. ``starter(job_path, log_path, index) -> Popen-like`` (default ``spawn.start_worker``)."""

    def __init__(self, *, starter: Optional[Callable[[Any, Any, int], Any]] = None,
                 clock: Callable[[], float] = time.monotonic, cancel_grace_s: float = CANCEL_GRACE_S,
                 exit_wait_s: float = EXIT_WAIT_S, manage_case_dir: bool = True,
                 terminate: Optional[Callable[[Any], Any]] = None):
        self._starter = starter or _default_starter
        self._clock = clock
        self.cancel_grace_s = float(cancel_grace_s)
        self.exit_wait_s = float(exit_wait_s)
        self.manage_case_dir = manage_case_dir
        self._terminate = terminate
        self._reset_run()
        self.state = IDLE

    def _reset_run(self) -> None:
        self.job: Optional[Dict[str, Any]] = None
        self.outcome: Optional[Outcome] = None
        self.progress = Progress()
        self.hello: Optional[Dict[str, Any]] = None
        self.error: Optional[Dict[str, Any]] = None
        self.done_event: Optional[Dict[str, Any]] = None
        self.cancelled_event = False
        self.bad_lines = 0
        self.events = 0
        self._proc: Any = None
        self._q: "queue.Queue[Any]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._eof = False
        self._exit_seen_at: Optional[float] = None
        self._cancel_at: Optional[float] = None
        self._forced = False

    # ------------------------------------------------------------------------------------------- public
    @property
    def running(self) -> bool:
        return self.state in (CHECKING, WAITING)

    @property
    def cancelling(self) -> bool:
        return self._cancel_at is not None and self.running

    @property
    def pid(self) -> Optional[int]:
        return getattr(self._proc, "pid", None)

    def start(self, job_path: Any, *, log_path: Any = None) -> None:
        """Starts the worker of one check. A job.json that cannot be read, or a worker that cannot be started (an
        antivirus block), ends at once in ``done`` with STARTUP_FAILED. Raises RuntimeError while a check runs."""
        if self.running:
            raise RuntimeError("a check is running")
        self._reset_run()
        try:
            self.job = protocol.read_job(job_path)
        except protocol.JobError as e:
            self._finish_now(protocol.STARTUP_FAILED, {"why": "job"}, str(e))
            return
        log = log_path if log_path is not None else _default_log_path(self.job["job"])
        try:
            self._proc = self._starter(job_path, log, int(self.job["gpu"]["index"]))
        except Exception as e:
            self._finish_now(protocol.STARTUP_FAILED, {"why": type(e).__name__}, "")
            return
        self.state = CHECKING
        out = getattr(self._proc, "stdout", None)
        if out is not None:
            self._thread = threading.Thread(target=_read_lines, args=(out, self._q), name="hdc-worker-reader",
                                            daemon=True)
            self._thread.start()
        else:
            self._eof = True

    def cancel(self) -> bool:
        """Asks the worker to stop (``cancel`` on its stdin). True when the request was sent or is pending."""
        if not self.running or self._proc is None:
            return False
        if self._cancel_at is None:
            self._cancel_at = self._clock()
            stdin = getattr(self._proc, "stdin", None)
            try:
                if stdin is not None:
                    stdin.write(protocol.CANCEL_LINE)
                    stdin.flush()
            except (OSError, ValueError):
                pass                                     # the worker is ending anyway; poll() escalates if not
        return True

    def poll(self) -> List[Dict[str, Any]]:
        """Drains the worker's events, applies them, escalates a cancel after the grace time and detects the end.
        Returns the events applied by this call."""
        applied: List[Dict[str, Any]] = []
        if self._proc is None or self.state == DONE:
            return applied
        self._drain(applied)
        alive = self._proc.poll() is None
        if alive and self._cancel_at is not None and not self._forced:
            if self._clock() - self._cancel_at >= self.cancel_grace_s:
                self._forced = True
                self._kill()
                alive = self._proc.poll() is None
        if not alive:
            if self._exit_seen_at is None:
                self._exit_seen_at = self._clock()
            if self._eof or self._clock() - self._exit_seen_at >= self.exit_wait_s:
                self._drain(applied)
                self._finish(self._proc.returncode)
        return applied

    def wait(self, timeout: Optional[float] = None, interval: float = 0.05,
             sleep: Callable[[float], None] = time.sleep) -> Optional[Outcome]:
        """Polls until the check is done (or ``timeout`` seconds passed). For tests, --auto and the self-test."""
        end = None if timeout is None else time.monotonic() + timeout
        while self.state != DONE:
            self.poll()
            if self.state == DONE:
                break
            if end is not None and time.monotonic() >= end:
                break
            sleep(interval)
        return self.outcome

    def shutdown(self, timeout: Optional[float] = None) -> Optional[Outcome]:
        """The window is closing: cancel, wait up to the grace time, then end the worker."""
        if self.running:
            self.cancel()
            self.wait(self.cancel_grace_s + 3.0 if timeout is None else timeout)
            if self.running:
                self._forced = True
                self._kill()
                self.wait(self.exit_wait_s + 3.0)
        return self.outcome

    def reset(self) -> None:
        """Back to idle after a finished check (the window clears its result area)."""
        if self.running:
            raise RuntimeError("a check is running")
        self._reset_run()
        self.state = IDLE

    # ------------------------------------------------------------------------------------------ private
    def _kill(self) -> None:
        if self._terminate is not None:
            self._terminate(self._proc)
            return
        from . import spawn
        spawn.terminate(self._proc, wait_s=3.0)

    def _drain(self, applied: List[Dict[str, Any]]) -> None:
        while True:
            try:
                kind, ev = self._q.get_nowait()
            except queue.Empty:
                return
            if kind == "eof":
                self._eof = True
            elif kind == "bad":
                self.bad_lines += 1
            elif ev is not None:
                self.events += 1
                if self._apply(ev):
                    applied.append(ev)

    def _end_wait(self) -> None:
        if self.state == WAITING:
            self.state = CHECKING
        self.progress.wait = None

    def _apply(self, ev: Dict[str, Any]) -> bool:
        name = ev.get("ev")
        p = self.progress
        if name == "hello":
            self.hello = ev
            return True
        if name == "done":
            self.done_event = ev
            return True
        if name == "error":
            if self.error is None:
                self.error = ev
            return True
        if name == "cancelled":
            self.cancelled_event = True
            return True
        if not self.running:
            return False
        if name == "gpu_wait":
            st = ev.get("state")
            info = {k: v for k, v in ev.items() if k not in ("v", "ev", "state")}
            if st == "start" or (st == "tick" and self.state != WAITING):      # a tick also opens a lost start
                p.waits += 1
                p.wait = {"waited_s": 0.0, **info}
                self.state = WAITING
            elif st == "tick":
                p.wait = {**(p.wait or {}), **info}
            elif st == "end":
                try:
                    p.waited_s += float(ev.get("waited_s") or 0.0)
                except (TypeError, ValueError):
                    pass
                self._end_wait()
            return True
        if name == "card":
            fields = {k: v for k, v in ev.items() if k not in ("v", "ev", "state")}
            if ev.get("state") == "ready":
                fields = {("ready_seconds" if k == "seconds" else k): v for k, v in fields.items()}
                p.card["ready"] = True
            p.card.update(fields)
            return True
        if name == "stage":
            key = str(ev.get("key") or "")
            st = ev.get("state")
            p.stages[key] = {"state": st, "seconds": ev.get("seconds")}
            if st == "start":
                p.stage = key
                self._end_wait()
            return True
        if name == "page":
            try:
                p.page_done = int(ev.get("done") or 0)
                p.page_of = int(ev.get("of") or 0)
            except (TypeError, ValueError):
                return False
            p.page_where = ev.get("where")
            s = ev.get("seconds")
            if isinstance(s, (int, float)) and not isinstance(s, bool):
                p.page_seconds.append(float(s))
            self._end_wait()
            return True
        if name == "file":
            p.files.append({k: v for k, v in ev.items() if k not in ("v", "ev")})
            return True
        if name == "budget":
            p.budgets.append({k: v for k, v in ev.items() if k not in ("v", "ev")})
            return True
        return False                                     # an unknown event (a newer worker): ignored

    def _finish_now(self, code: str, values: Dict[str, Any], text: str) -> None:
        self.outcome = Outcome("error", code, None, values, text)
        self.state = DONE

    def _finish(self, rc: Optional[int]) -> None:
        self.outcome = classify(rc, hello=self.hello is not None, error=self.error, done=self.done_event,
                                cancelled=self.cancelled_event, forced=self._forced, stage=self.progress.stage)
        self.progress.wait = None
        self.state = DONE
        # stdout is closed only after the reader saw its end: closing a buffered pipe while the reader thread is
        # blocked in readline would wait for that read (a handle still held elsewhere would hang the window)
        names = ("stdin", "stdout") if self._eof else ("stdin",)
        for name in names:
            s = getattr(self._proc, name, None)
            try:
                if s is not None:
                    s.close()
            except (OSError, ValueError):
                pass
        if self._thread is not None and self._eof:
            self._thread.join(timeout=1.0)
        self._case_dir_after()

    def _case_dir_after(self) -> None:
        if not self.manage_case_dir or not self.job or self.outcome is None:
            return
        out, job_id = self.job.get("out"), self.job.get("job")
        if not out or not job_id:
            return
        o = self.outcome
        if o.kind == "cancelled":
            remove_case_dir_if_ours(out, job_id)
        elif o.kind == "error" and (o.exit in REFUSAL_EXITS or o.code == protocol.STARTUP_FAILED):
            if not remove_case_dir_if_ours(out, job_id, only_if_empty=True):
                remove_marker(out, job_id)
        else:
            remove_marker(out, job_id)
