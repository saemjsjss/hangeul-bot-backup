"""P4 politeness (APP_SPEC rev 2, R2.6): the frozen app waits for a lock file, on THIS PC's card via cardrun.

    <runner python> -I -B packaging\\tests\\politeness.py --build-id <id> [--only 6,1,2,3,7,5,4] [--app-dir DIR]

CHECK_0001 (input, program and check date from ``runs\\test1_r8\\run.json``), each scenario with a FRESH DATA folder,
watching an extra lock ``private-runner\\apptest\\<build-id>\\fakelock\\auto_verify.lock`` (``--gpu-lock-extra``; the
app's default setting still watches the REAL bot lock). NEEDS ORCHESTRATOR APPROVAL Q-O1 (cardrun).

Scenarios (R2.6 P4) and what passes:
  (6) no lock: the reference. It finishes; its result code equals P1's THIS_PC_CODE (when P1 ran).
  (1) the lock is there before the start: a ``gpu_wait`` start with ``where`` "start" within 3 s, no ``card`` ready
      event while the lock is there (60 s); after its removal the check finishes.
  (2) the lock is made after the 3rd phase-1 page event, and (3) after the ``names`` stage starts (phase 2): a
      ``gpu_wait`` start within 3 s, ``reserved_mb`` <= 256, no page or stage event during the 60 s hold, resumed
      within 10 s of the removal; the check finishes.
  (4) ``--worker`` with stdin: the lock after the 3rd page, ``cancel`` written 2 s into the wait: a ``cancelled``
      event and exit 4 within 10 s, the worker gone (nothing left in cardrun's job); then a ``--cli`` run on the
      same DATA skips the pages the cancelled run had saved.
  (5) a stale lock (mtime 5 h back): no wait.
  (7) the lock is made when the runner's ``bot`` stage starts (phase 2; processor work for up to a minute, maybe no
      model call at all) and held 60 s. NVML free card memory is read every 2 s during the hold and again after the
      app ended: the app may hold at most APP_IDLE_MAX_MB of card memory while the lock exists (its CUDA context, the
      light reader's weights and at most 256 MB of torch cache; G1 review finding 1). A wait that starts in the
      hold has ``reserved_mb`` <= 256. The check finishes.
  (1), (2), (3), (5) and (7) give the same result code as (6) and P1.
When the REAL bot lock was active during a scenario (its lock seen by stat, a ``gpu_wait`` with lock "bot", or
cardrun's SlotInterrupted), the TIMING checks of that scenario are reported as "bot active", not failed.

Prints scenario numbers, counts, seconds and codes only. Exit 0 PASS, 1 FAIL, 2 could not run.
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))

import argparse  # noqa: E402
import os  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
import uuid  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple  # noqa: E402

import cardrun  # noqa: E402
import parity  # noqa: E402

WORKTREE = Path(__file__).resolve().parents[2]

HOLD_S = 60.0
START_WITHIN_S = 3.0
RESUME_WITHIN_S = 10.0
CANCEL_WITHIN_S = 10.0
CANCEL_AFTER_S = 2.0
RESERVED_MAX_MB = 256
NO_WAIT_GIVEUP_S = 90.0             # the lock is removed when no wait started by then (so the run can end)
STALE_AGE_S = 5 * 3600
BOT_SAMPLE_S = 2.0
DEFAULT_TIMEOUT_S = 3600.0
# scenario 7: the most card memory (MiB) the app may hold while a lock exists between two model calls: its CUDA
# context (about 0.3-0.5 GB, which torch does not count), the light reader's weights (about 0.1 GB) and at most
# 256 MB of torch cache. Before G1's fix it held up to the 3 GB cap through the runner's processor stretches.
APP_IDLE_MAX_MB = 1024
FREE_SAMPLES_MIN = 3
SETTLE_S = 0.5                      # after the app ended: free memory is read 3 times, this far apart (the max counts)
ORDER = (6, 1, 2, 3, 7, 5, 4)
TITLES = {1: "lock before the start", 2: "lock after the 3rd phase-1 page", 3: "lock after the names stage starts",
          4: "cancel during a wait (--worker)", 5: "stale lock (5 h old)", 6: "no lock (reference)",
          7: "lock during the bot stage (processor work)"}


def nvml_free_mb(index: int = 0) -> Optional[int]:
    """The card's free memory in MiB by NVML, through the app's own ``gpuinfo`` (ctypes only: no torch, no CUDA
    context), or None when it cannot be read."""
    try:
        if str(WORKTREE) not in sys.path:
            sys.path.insert(0, str(WORKTREE))
        from src.verify.runner.app import gpuinfo
        return gpuinfo.free_mb(index)
    except Exception:
        return None

Events = List[Tuple[float, Dict[str, Any]]]


# ------------------------------------------------------------------------------------------------ the fake lock

class FakeLock:
    """The test's own lock file (only ever under private-runner\\apptest). The app only stats it."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def make(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="ascii") as fh:
            fh.write("politeness test lock\n")

    def make_stale(self, age_s: float = STALE_AGE_S) -> None:
        self.make()
        t = time.time() - age_s
        os.utime(self.path, (t, t))

    def remove(self) -> bool:
        try:
            os.remove(self.path)
            return True
        except FileNotFoundError:
            return False

    def exists(self) -> bool:
        return self.path.exists()


# ------------------------------------------------------------------------------------------------ live control

class Controller:
    """Drives one scenario while the app runs: makes the lock at the trigger, removes it after the hold, writes
    ``cancel``; records the events with their times. ``on_line`` runs on cardrun's reader thread and ``on_tick``
    on its main loop; one lock guards the state."""

    def __init__(self, number: int, lock: FakeLock, *, hold_s: float = HOLD_S, cancel_after_s: float = CANCEL_AFTER_S,
                 giveup_s: float = NO_WAIT_GIVEUP_S, bot_active: Callable[[], bool] = cardrun.bot_lock_active,
                 bot_sample_s: float = BOT_SAMPLE_S, free_fn: Callable[[], Optional[int]] = nvml_free_mb,
                 settle_s: float = SETTLE_S, sleep: Callable[[float], None] = time.sleep) -> None:
        self.n = number
        self.lock = lock
        self.hold_s = hold_s
        self.cancel_after_s = cancel_after_s
        self.giveup_s = giveup_s
        self.bot_active = bot_active
        self.bot_sample_s = bot_sample_s
        self.free_fn = free_fn
        self.settle_s = settle_s
        self.sleep = sleep
        self.events: Events = []
        self.marks: Dict[str, Any] = {"t_lock": None, "t_ws": None, "t_rm": None, "t_cancel": None,
                                      "bot_seen": 0, "gave_up": False, "free_hold": [], "free_after": None}
        self._mu = threading.Lock()
        self._stage: Optional[str] = None
        self._phase1 = 0
        self._waiting = False
        self._last_bot = -1e9
        self._last_free = -1e9

    @property
    def trigger(self) -> Optional[str]:
        return {2: "page3", 3: "names", 4: "page3", 7: "bot"}.get(self.n)

    def before_start(self) -> None:
        if self.n == 1:
            self.lock.make()
            self.marks["t_lock"] = 0.0
        elif self.n == 5:
            self.lock.make_stale()

    def on_line(self, t: float, line: str, ctl: Any) -> None:
        ev = cardrun.parse_event(line)
        if ev is None:
            return
        with self._mu:
            self.events.append((t, ev))
            kind = ev.get("ev")
            if kind == "stage":
                self._stage = ev.get("key") if ev.get("state") == "start" else None
            if kind == "page" and (ev.get("where") == "card" or self._stage == "firstread"):
                self._phase1 += 1
            fire = ((self.trigger == "page3" and kind == "page" and self._phase1 == 3)
                    or (self.trigger in ("names", "bot") and kind == "stage" and ev.get("key") == self.trigger
                        and ev.get("state") == "start"))
            if fire and self.marks["t_lock"] is None:
                self.lock.make()
                self.marks["t_lock"] = ctl.elapsed() if ctl is not None else t
                if self._waiting:                  # already waiting (the real bot's lock): the hold starts now
                    self.marks["t_ws"] = self.marks["t_lock"]
            if kind == "gpu_wait" and ev.get("state") in ("start", "end"):
                self._waiting = ev.get("state") == "start"
            if (kind == "gpu_wait" and ev.get("state") == "start" and ev.get("why") == "lock"
                    and self.marks["t_lock"] is not None and self.marks["t_ws"] is None
                    and self.lock.exists()):
                self.marks["t_ws"] = t

    def on_tick(self, t: float, ctl: Any) -> None:
        with self._mu:
            if t - self._last_bot >= self.bot_sample_s:
                self._last_bot = t
                if self.bot_active():
                    self.marks["bot_seen"] += 1
            m = self.marks
            if self.n in (5, 6) or m["t_lock"] is None or m["t_rm"] is not None:
                return
            if self.n == 7:                        # free card memory during the hold; the hold counts from the lock
                if t - self._last_free >= self.bot_sample_s:
                    self._last_free = t
                    m["free_hold"].append(self.free_fn())
                if t >= m["t_lock"] + self.hold_s:
                    self.lock.remove()
                    m["t_rm"] = t
                return
            if self.n == 4:
                if m["t_ws"] is not None and m["t_cancel"] is None and t >= m["t_ws"] + self.cancel_after_s:
                    if ctl.write("cancel\n"):
                        m["t_cancel"] = t
                elif m["t_cancel"] is not None and t >= m["t_cancel"] + 3 * CANCEL_WITHIN_S:
                    self.lock.remove()             # the cancel did not end it: let the worker run to its end
                    m["t_rm"] = t
                elif m["t_ws"] is None and t >= m["t_lock"] + self.giveup_s:
                    self.lock.remove()
                    m["t_rm"] = t
                    m["gave_up"] = True
                return
            if m["t_ws"] is not None and t >= m["t_ws"] + self.hold_s:
                self.lock.remove()
                m["t_rm"] = t
            elif m["t_ws"] is None and t >= m["t_lock"] + self.giveup_s:
                self.lock.remove()
                m["t_rm"] = t
                m["gave_up"] = True

    def finish(self) -> None:
        """After the run (the app has ended): the lock removed; for scenario 7 free card memory read again (the
        most of three reads, SETTLE_S apart: the driver gives the ended process's memory back)."""
        with self._mu:
            if self.lock.remove() and self.marks["t_rm"] is None and self.n not in (4, 5):
                self.marks["gave_up"] = True
            if self.n == 7:
                reads = []
                for k in range(3):
                    if k:
                        self.sleep(self.settle_s)
                    reads.append(self.free_fn())
                got = [v for v in reads if isinstance(v, (int, float))]
                self.marks["free_after"] = max(got) if got else None


# ------------------------------------------------------------------------------------------------ judging

@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    timing: bool = False


@dataclass
class Outcome:
    number: int
    checks: List[Check] = field(default_factory=list)
    bot_active: bool = False
    code: Optional[str] = None
    facts: Dict[str, Any] = field(default_factory=dict)

    def status(self, c: Check) -> str:
        if c.ok:
            return "ok"
        return "bot active" if (c.timing and self.bot_active) else "FAIL"

    @property
    def passed(self) -> bool:
        return all(self.status(c) != "FAIL" for c in self.checks)


def _wait_start(events: Events, after: Optional[float]) -> Optional[Tuple[float, Dict[str, Any]]]:
    """The wait for a lock that covers ``after``: a gpu_wait start at or after it, or one already running then (the
    real bot's lock was there first; its start event then counts from ``after``)."""
    running: Optional[Dict[str, Any]] = None
    for t, e in events:
        if e.get("ev") != "gpu_wait":
            continue
        if after is not None and t < after - 1e-9:
            if e.get("state") == "start" and e.get("why") == "lock":
                running = e
            elif e.get("state") == "end":
                running = None
            continue
        if running is not None:
            return float(after), running
        if e.get("state") == "start" and e.get("why") == "lock":
            return t, e
    return (float(after), running) if (running is not None and after is not None) else None


def _first_after(events: Events, t0: float, pred: Callable[[Dict[str, Any]], bool]) -> Optional[float]:
    for t, e in events:
        if t > t0 and pred(e):
            return t
    return None


def _card_ready(e: Dict[str, Any]) -> bool:
    return e.get("ev") == "card" and e.get("state") == "ready"


def is_bot_active(events: Events, marks: Dict[str, Any], interrupted: int = 0) -> bool:
    return bool(marks.get("bot_seen")) or interrupted > 0 or any(
        e.get("ev") == "gpu_wait" and e.get("lock") == "bot" for _, e in events)


def _finish_checks(o: Outcome, events: Events, exit_code: Optional[int], refs: Dict[str, Optional[str]]) -> None:
    done = cardrun.done_event(events)
    o.checks.append(Check("the check finished", exit_code in parity.DONE_EXITS and done is not None,
                          f"exit {exit_code}"))
    for name, ref in refs.items():
        if ref:
            o.checks.append(Check(f"result code = {name}", o.code == ref, f"{o.code or '-'} vs {ref}"))


def judge(number: int, events: Events, marks: Dict[str, Any], exit_code: Optional[int], code: Optional[str],
          refs: Dict[str, Optional[str]], *, interrupted: int = 0, seconds: Optional[float] = None,
          leftover: int = 0, timed_out: bool = False) -> Outcome:
    """The R2.6 P4 pass rules for one recorded scenario (pure: events with harness times, the marks)."""
    o = Outcome(number, bot_active=is_bot_active(events, marks, interrupted), code=code)
    o.facts = {"events": len(events), "t_lock": marks.get("t_lock"), "t_ws": marks.get("t_ws"),
               "t_rm": marks.get("t_rm"), "t_cancel": marks.get("t_cancel"), "gave_up": marks.get("gave_up")}
    if timed_out:
        o.checks.append(Check("no timeout", False, "the run hit its timeout"))
    if number == 6:
        _finish_checks(o, events, exit_code, {k: v for k, v in refs.items() if k == "P1"})
        return o
    if number == 5:
        waits = [e for _, e in events if e.get("ev") == "gpu_wait" and e.get("state") == "start"
                 and e.get("why") == "lock" and e.get("lock") != "bot"]
        o.checks.append(Check("no wait for the stale lock", not waits, f"{len(waits)} wait(s)"))
        _finish_checks(o, events, exit_code, refs)
        return o
    t_lock = marks.get("t_lock")
    ws = _wait_start(events, t_lock)
    if number == 1:
        first_t = events[0][0] if events else None
        o.checks.append(Check("a wait at the start", ws is not None and ws[1].get("where") == "start",
                              "where " + str(ws[1].get("where")) if ws else "no wait"))
        if ws is not None:
            o.checks.append(Check(f"the wait started within {START_WITHIN_S:.0f} s", ws[0] - (first_t or 0.0)
                                  <= START_WITHIN_S, f"{ws[0] - (first_t or 0.0):.1f} s after the first event",
                                  timing=True))
            t_rm = marks.get("t_rm")
            early = [t for t, e in events if _card_ready(e) and (t_rm is None or t < t_rm)]
            o.checks.append(Check("no card start while the lock was there", not early,
                                  f"{len(early)} card-ready event(s) before the removal"))
            o.checks.append(Check(f"the lock was held {HOLD_S:.0f} s", t_rm is not None
                                  and t_rm - ws[0] >= HOLD_S - 0.5 and not marks.get("gave_up"),
                                  "" if t_rm is None else f"{t_rm - ws[0]:.1f} s", timing=True))
        _finish_checks(o, events, exit_code, refs)
        return o
    if number in (2, 3):
        o.checks.append(Check("the lock was made", t_lock is not None, "trigger not reached" if t_lock is None else ""))
        o.checks.append(Check("a wait started", ws is not None, "" if ws else "no gpu_wait start after the lock"))
        if ws is not None and t_lock is not None:
            o.checks.append(Check(f"the wait started within {START_WITHIN_S:.0f} s", ws[0] - t_lock <= START_WITHIN_S,
                                  f"{ws[0] - t_lock:.1f} s after the lock", timing=True))
            r = ws[1].get("reserved_mb")
            o.checks.append(Check(f"reserved_mb <= {RESERVED_MAX_MB}", isinstance(r, (int, float))
                                  and r <= RESERVED_MAX_MB, f"{r} MB"))
            t_rm = marks.get("t_rm")
            end = t_rm if t_rm is not None else float("inf")
            busy = [e.get("ev") for t, e in events if ws[0] < t < end and e.get("ev") in ("page", "stage")]
            o.checks.append(Check("no page or stage event during the hold", not busy, f"{len(busy)} event(s)"))
            if t_rm is not None:
                t_res = _first_after(events, t_rm, lambda e: (e.get("ev") == "gpu_wait" and e.get("state") == "end")
                                     or e.get("ev") in ("page", "stage", "done"))
                o.checks.append(Check(f"resumed within {RESUME_WITHIN_S:.0f} s of the removal", t_res is not None
                                      and t_res - t_rm <= RESUME_WITHIN_S,
                                      "not resumed" if t_res is None else f"{t_res - t_rm:.1f} s", timing=True))
        _finish_checks(o, events, exit_code, refs)
        return o
    if number == 7:
        hold = [v for v in marks.get("free_hold") or [] if isinstance(v, (int, float))]
        o.facts.update(free_hold_min=min(hold) if hold else None, free_after=marks.get("free_after"),
                       footprint_mb=card_footprint(marks))
        o.checks.append(Check("the lock was made", t_lock is not None, "the bot stage did not start" if t_lock
                              is None else ""))
        o.checks.append(Check(f"free card memory read >= {FREE_SAMPLES_MIN} times during the hold",
                              len(hold) >= FREE_SAMPLES_MIN, f"{len(hold)} read(s)"))
        fp = card_footprint(marks)
        o.checks.append(Check(f"the app held <= {APP_IDLE_MAX_MB} MB of card memory while the lock existed",
                              fp is not None and fp <= APP_IDLE_MAX_MB,
                              "not measured" if fp is None else f"{fp} MB", timing=True))
        if t_lock is not None:
            t_rm = marks.get("t_rm")
            end = t_rm if t_rm is not None else float("inf")
            big = [e.get("reserved_mb") for t, e in events if t_lock <= t < end and e.get("ev") == "gpu_wait"
                   and e.get("state") == "start" and e.get("why") == "lock"
                   and not (isinstance(e.get("reserved_mb"), (int, float)) and e["reserved_mb"] <= RESERVED_MAX_MB)]
            o.checks.append(Check(f"every wait in the hold had reserved_mb <= {RESERVED_MAX_MB}", not big,
                                  f"{len(big)} wait(s) above"))
        _finish_checks(o, events, exit_code, refs)
        return o
    if number == 4:
        t_cancel = marks.get("t_cancel")
        o.checks.append(Check("a wait started", ws is not None, "" if ws else "no gpu_wait start after the lock"))
        o.checks.append(Check("cancel was sent during the wait", t_cancel is not None))
        cancelled = cardrun.first(events, "cancelled")
        o.checks.append(Check("a cancelled event", cancelled is not None))
        o.checks.append(Check("exit 4", exit_code == 4, f"exit {exit_code}"))
        if cancelled is not None and t_cancel is not None:
            o.checks.append(Check(f"cancelled within {CANCEL_WITHIN_S:.0f} s", cancelled[0] - t_cancel
                                  <= CANCEL_WITHIN_S, f"{cancelled[0] - t_cancel:.1f} s", timing=True))
        if seconds is not None and t_cancel is not None:
            o.checks.append(Check(f"the worker ended within {CANCEL_WITHIN_S:.0f} s", seconds - t_cancel
                                  <= CANCEL_WITHIN_S, f"{seconds - t_cancel:.1f} s", timing=True))
        o.checks.append(Check("the worker process is gone", leftover == 0 and not timed_out,
                              f"{leftover} process(es) left in the job"))
        return o
    raise ValueError(f"unknown scenario {number}")


def card_footprint(marks: Dict[str, Any]) -> Optional[int]:
    """Scenario 7: the app's card memory while the lock existed, as NVML saw it: free memory after the app ended
    minus the least free memory during the hold (MiB). None when it was not read."""
    hold = [v for v in marks.get("free_hold") or [] if isinstance(v, (int, float)) and not isinstance(v, bool)]
    after = marks.get("free_after")
    if not hold or not isinstance(after, (int, float)) or isinstance(after, bool):
        return None
    return int(after - min(hold))


def phase1_of(events: Events) -> Optional[int]:
    """The 'of' (pages to read) of the first phase-1 page event."""
    stage = None
    for _, e in events:
        if e.get("ev") == "stage":
            stage = e.get("key") if e.get("state") == "start" else None
        if e.get("ev") == "page" and (e.get("where") == "card" or stage == "firstread"):
            of = e.get("of")
            return int(of) if isinstance(of, (int, float)) else None
    return None


def phase1_pages(events: Events) -> int:
    stage, n = None, 0
    for _, e in events:
        if e.get("ev") == "stage":
            stage = e.get("key") if e.get("state") == "start" else None
        if e.get("ev") == "page" and (e.get("where") == "card" or stage == "firstread"):
            n += 1
    return n


def saved_pages(store: Optional[Dict[str, Any]]) -> Optional[int]:
    if not isinstance(store, dict):
        return None
    return sum(len(f.get("pages") or []) for f in store.get("files") or [])


def judge_resume(total: Optional[int], saved: Optional[int], events: Events, exit_code: Optional[int]) -> List[Check]:
    """(4) second part: the next run on the same DATA reads only the pages the cancelled run did not save."""
    of = phase1_of(events)
    checks = [Check("the cancelled run saved >= 3 pages", saved is not None and saved >= 3, f"{saved} saved")]
    if total is not None and saved is not None:
        expect = total - saved
        ok = (of == expect) if expect > 0 else (of is None and phase1_pages(events) == 0)
        checks.append(Check("the next run skips the saved pages", ok, f"to read {of}, expected {expect}"))
    else:
        checks.append(Check("the next run skips the saved pages", False,
                            f"to read {of}; the page total or the saved count is unknown"))
    checks.append(Check("the next run finished", exit_code in parity.DONE_EXITS, f"exit {exit_code}"))
    return checks


# ------------------------------------------------------------------------------------------------ running

def worker_job(case: parity.R8Case, out: Path, data: Path, locks: Sequence[Path]) -> Dict[str, Any]:
    """job.json for --worker (rev 1 §2 + R2.3.3's gpu block)."""
    return {"schema": "hdc-job/1", "job": uuid.uuid4().hex, "input": case.input, "program": case.program,
            "out": str(out), "today": case.today, "cache_root": str(Path(data) / "cache"), "trace_dir": None,
            "gpu": {"lock_paths": [str(p) for p in locks], "index": 0, "cap_gb": 3.0, "need_free_mb": 4096}}


def default_locks(fake: Path) -> List[Path]:
    """What the window's default setting watches (the bot's lock when its folder exists) plus the fake one."""
    locks: List[Path] = []
    if cardrun.BOT_LOCK.parent.is_dir():
        locks.append(cardrun.BOT_LOCK)
    locks.append(Path(fake))
    return locks


def run_scenario(number: int, case: parity.R8Case, exe: Path, root: Path, fake: FakeLock, timeout: float,
                 run_fn: Callable[..., Any], data: Optional[Path] = None,
                 folder: str = "") -> Tuple[Controller, Any, Path, Path]:
    """One scenario run; ``number`` 0 is a plain --cli run with no lock (4's resume run, ``folder`` "s4-resume")."""
    d = root / (folder or f"s{number}")
    data = data or (d / "data")
    out = d / "out"
    data.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    ctl = Controller(number, fake)
    try:
        ctl.before_start()
        if number == 4:
            job = worker_job(case, out, data, default_locks(fake.path))
            job_path = data / "jobs" / job["job"] / "job.json"
            cardrun.write_json(job_path, job)
            cmd = [str(exe), "--worker", str(job_path)]
            res = run_fn(cmd, timeout, what="hdc P4 scenario 4", env=cardrun.app_env(data), log_dir=d, stdin=True,
                         on_line=ctl.on_line, on_tick=ctl.on_tick)
        else:
            cmd = cardrun.cli_cmd(exe, case.input, case.program, out, case.today, data, extra_locks=[fake.path])
            res = run_fn(cmd, timeout, what=f"hdc P4 scenario {number}", env=cardrun.app_env(data), log_dir=d,
                         on_line=ctl.on_line, on_tick=ctl.on_tick)
    finally:
        ctl.finish()
    return ctl, res, out, data


def code_of(out: Path, events: Events) -> Optional[str]:
    run = parity._read_json_or_none(Path(out) / "run.json")
    if isinstance(run, dict) and run.get("result_code"):
        return run["result_code"]
    return (cardrun.done_event(events) or {}).get("code")


def print_outcome(o: Outcome) -> None:
    head = f"scenario {o.number} ({TITLES.get(o.number, '?')}): {'PASS' if o.passed else 'FAIL'}"
    head += f" | code {o.code or '-'}" + (" | REAL bot lock active: timing checks not failed" if o.bot_active else "")
    print(head)
    for c in o.checks:
        print(f"  {o.status(c):10s} {c.name}" + (f" ({c.detail})" if c.detail else ""))


def main(argv: Optional[Sequence[str]] = None, run_fn: Optional[Callable[..., Any]] = None,
         runs: Path = parity.RUNS) -> int:
    ap = argparse.ArgumentParser(description="P4: the frozen app waits politely for a lock file (needs Q-O1)")
    ap.add_argument("--build-id", default="")
    ap.add_argument("--app-dir", default="")
    ap.add_argument("--only", default=",".join(map(str, ORDER)), help="scenario numbers, run in this order")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S)
    a = ap.parse_args(argv)
    try:
        app = cardrun.app_dir_for(a.build_id or None, a.app_dir or None)
        wanted = [int(x) for x in a.only.split(",") if x.strip()]
        if any(n not in TITLES for n in wanted):
            raise ValueError("scenarios are 1-7")
        case = parity.load_r8(parity.CHECK_CASE, runs)
    except (ValueError, OSError, KeyError) as exc:
        print(f"P4: cannot start: {type(exc).__name__}: {exc}")
        return 2
    exe = app / cardrun.APP_EXE
    if not exe.is_file():
        print("P4: cannot start: the app exe was not found in the app folder")
        return 2
    run_fn = run_fn or cardrun.run
    apptest = cardrun.apptest_dir(a.build_id or None)
    fake = FakeLock(apptest / "fakelock" / "auto_verify.lock")
    fake.remove()                                         # a leftover of an interrupted earlier P4 run
    root = cardrun.new_run_dir(apptest, "p4")
    p1 = parity._read_json_or_none(apptest / "p1-latest.json") or {}
    p1_code: Optional[str] = p1.get("this_pc_code")
    ref6: Optional[str] = None
    print(f"P4 politeness | build {a.build_id or '-'} | run folder {root.name} | P1 code {p1_code or '(none)'}")
    outcomes: List[Outcome] = []
    total_pages: Optional[int] = None
    for n in wanted:
        try:
            ctl, res, out, data = run_scenario(n, case, exe, root, fake, a.timeout, run_fn)
        except cardrun.STOP_ERRORS as exc:
            print(f"P4: stopped: {type(exc).__name__}: {exc}")
            return 2
        events = ctl.events
        code = code_of(out, events)
        refs: Dict[str, Optional[str]] = {"P1": p1_code}
        if n in (1, 2, 3, 5, 7):
            refs["scenario 6"] = ref6
        o = judge(n, events, ctl.marks, res.returncode, code, refs, interrupted=res.interrupted,
                  seconds=res.seconds, leftover=res.leftover_killed, timed_out=res.timed_out)
        o.facts["cardrun"] = res.stats()
        if n == 6:
            ref6 = code
            total_pages = phase1_of(events)
        if n == 4:
            store = parity._read_json_or_none(parity.find_store(data, case.case_id) if data.is_dir() else None)
            saved = saved_pages(store)
            try:
                ctl2, res2, out2, _ = run_scenario(0, case, exe, root, fake, a.timeout, run_fn, data=data,
                                                   folder="s4-resume")
            except cardrun.STOP_ERRORS as exc:
                print(f"P4: stopped before scenario 4's second run: {type(exc).__name__}: {exc}")
                return 2
            if total_pages is None:
                r8_store = parity._read_json_or_none(case.dir / "page_texts.json")
                total_pages = sum(1 for _, _, p in parity._pages(r8_store or {}) if p.get("kind") == "ocr") or None
            o.checks.extend(judge_resume(total_pages, saved, ctl2.events, res2.returncode))
            o.facts["resume"] = {"saved": saved, "total": total_pages, "to_read": phase1_of(ctl2.events),
                                 "cardrun": res2.stats(), "code": code_of(out2, ctl2.events)}
        outcomes.append(o)
        print_outcome(o)
    passed = all(o.passed for o in outcomes)
    print(f"P4 {'PASS' if passed else 'FAIL'} ({len(outcomes)} scenario(s))")
    report = {"schema": "hdc-p4/1", "build_id": a.build_id or None, "run_folder": root.name, "passed": passed,
              "scenarios": [{"number": o.number, "title": TITLES.get(o.number), "passed": o.passed,
                             "bot_active": o.bot_active, "code": o.code, "facts": o.facts,
                             "checks": [{"name": c.name, "status": o.status(c), "detail": c.detail,
                                         "timing": c.timing} for c in o.checks]} for o in outcomes]}
    cardrun.write_json(root / "politeness.json", report)
    rdir = cardrun.reports_dir_for(a.build_id or None)
    if rdir is not None and rdir.is_dir():
        cardrun.write_json(rdir / "politeness.json", report)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
