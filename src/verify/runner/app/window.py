"""The window of the app (APP_SPEC rev 1 section 3; rev 2 R2.1.4, R2.2, R2.3.2, R2.3.3).

Two parts:
- `WindowModel`: every decision of the window, headless (it never imports tkinter), so the unit tests drive it with
  fake events and fake helpers. It holds the inputs, the settings, the card state (probe at start and before every
  Check), the running check (events in, view state out), the waiting state, the result, the recent checks.
- `TkWindow`: binds Tk + ttk widgets to the model: it turns clicks into model calls and `model.view()` into widget
  state, and polls the running check every 100 ms on Tk's main thread.

`main(argv)` starts the window (or `--auto`, which checks one input with no clicks and writes a small JSON result
for the release tests). The other tasks' modules are reached only through `real_deps()` and the two handle adapters
(`ControllerHandle`, `SelftestHandle`), so the integration points sit in one place.

No personal data: messages carry values only; nothing is logged.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from . import messages as M
from . import recent as R
from . import settingsfile as S
from . import userdirs as U

TITLE = "Hangeul Document Checker"
PROGRAM_CHOICES: Tuple[Tuple[str, str], ...] = (("KLP", "KLP"), ("EAP", "EAP"), ("Bachelor's", "BACHELOR"),
                                                ("Master's", "MASTER"))
PROGRAM_LABEL = {code: label for label, code in PROGRAM_CHOICES}
CAP_GB = 3.0                     # R2.0 G5: the card-memory cap on every card
WARN_FREE_RAM_MB = 4608          # R2.6 P5: a check's worker may need about 4.5 GB
BOT_SUFFIX = "(the Telegram bot's document check)"
LOCK_ROW_LABEL = "Graphics card: wait while this file exists"
LOCK_OFF_TEXT = "Off — checks do not wait for another program's lock file"
DEFAULT_ARCH_FLAGS = ("sm_75", "sm_80", "sm_86", "sm_90", "sm_100", "sm_120")   # torch 2.11.0+cu128 (R2 facts)
# the report's FILL colours (report.FILL), per verdict
VERDICT_FILL = {"PASS": "C6EFCE", "NOTE": "DDEBF7", "PRESENT": "D9D9D9", "FLAG": "FFEB9C", "ERROR": "FFEB9C",
                "FAIL": "FFC7CE", "MISSING": "F4B084", "NOT JUDGED": "E4DFEC", "ORDER": "F8CBAD", "STOP": "FF9999",
                "HINT": "DDEBF7", "REREAD": "EDEDED", "NOT CHECKED": "EDEDED"}
BANNER_COLOURS = {M.ERROR: ("#FFC7CE", "#7A0010"), M.WAIT: ("#DDEBF7", "#0B2E59"), M.NOTE: ("#FFEB9C", "#5A4300"),
                  M.INFO: ("#EDEDED", "#202020"), M.CONFIRM: ("#FFEB9C", "#5A4300")}

# states
IDLE, RUNNING, WAITING, CANCELLING, FINISHING, SELFTEST = "idle", "running", "waiting", "cancelling", "finishing", \
    "selftest"
BUSY = (RUNNING, WAITING, CANCELLING, FINISHING, SELFTEST)


# ==================================================================================================== view
@dataclass
class Banner:
    kind: str
    title: str
    text: str
    code: str = ""
    detail: str = ""
    cancel: bool = False              # show a Cancel button next to it (the waiting banner)


@dataclass
class Row:
    title: str
    pages: str
    verdict: str
    reasons: str
    full: str                         # the whole summary line (shown in full when selected)
    cross: bool = False


@dataclass
class Result:
    overall: str
    headline: str
    code: str
    check_date: str
    rows: List[Row]
    fixes: List[str]                  # the yellow "Fix these pages" lines (one per ORDER notice)
    notes: List[str]
    stop: Optional[str]
    out: str
    label: str
    exit: int = 0


@dataclass
class View:
    title: str
    state: str
    input_text: str
    input_chosen: bool
    program_label: str
    program_labels: Tuple[str, ...]
    date_mode: str
    fixed_date: str
    report_root_text: str
    lock_label: str
    lock_text: str
    lock_is_default: bool
    lock_off: bool
    check_enabled: bool
    cancel_enabled: bool
    selftest_enabled: bool
    edit_enabled: bool
    progress_text: str
    progress_note: str
    progress_mode: str                # "off" | "determinate" | "indeterminate"
    progress_value: float             # 0..100 (determinate)
    progress_paused: bool
    banner: Optional[Banner]
    result: Optional[Result]
    result_buttons: bool
    recent_lines: List[str]
    footer_app: str
    footer_card: str


# ==================================================================================================== lines
_DOC_RE = re.compile(r"^(?P<title>[^()]+?) \((?P<where>.*)\): (?P<verdict>NOT JUDGED|PASS|FLAG|FAIL|ERROR|PRESENT|NOTE)"
                     r"(?: — (?P<reasons>.*))?$", re.S)
_CROSS_RE = re.compile(r"^(?P<title>NAMES|FINANCIAL CHAIN|BUNDLE): (?P<verdict>NOT JUDGED|PASS|FLAG|FAIL|ERROR|NOTE)"
                       r"(?: — (?P<reasons>.*))?$", re.S)
_ORDER_RE = re.compile(r"^ORDER: fix (?P<pages>pages? \d+(?:-\d+)?) — (?P<message>.*)$", re.S)
_STOP_RE = re.compile(r"^STOP: (?P<message>.*) \((?P<pages>pages? \d+(?:-\d+)?)\)$", re.S)
_HEADER_RE = re.compile(r"\| RESULT: (?:STOP|FAIL|FLAG|PASS|NOT JUDGED)\s*$")
_LABEL_RE = re.compile(r"^(?P<title>[^:]{1,60}): (?P<rest>.*)$", re.S)
_SKIP_PREFIXES = ("RESULT CODE", "READING:", "Report:", "ORDER: OK")


def parse_lines(lines: Iterable[Any], headline: str = "") -> Tuple[List[Row], List[Tuple[str, str]],
                                                                    Optional[str], List[str]]:
    """The done event's summary lines -> (rows, orders [(pages, message)], stop pages, notes). Header, reading,
    result-code and report lines are left out; NAMES / FINANCIAL CHAIN / BUNDLE rows come last."""
    rows: List[Row] = []
    cross: List[Row] = []
    orders: List[Tuple[str, str]] = []
    stop: Optional[str] = None
    notes: List[str] = []
    for item in lines or ():
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            label, text = str(item[0] or ""), str(item[1] or "")
        else:
            label, text = "", str(item or "")
        text = text.strip()
        if not text or text == headline or _HEADER_RE.search(text) or text.startswith(_SKIP_PREFIXES):
            continue
        m = _ORDER_RE.match(text)
        if m:
            orders.append((m.group("pages"), m.group("message")))
            continue
        m = _STOP_RE.match(text)
        if m:
            stop = m.group("pages")
            continue
        if text.startswith(("HINT: ", "NOTE: ")):
            notes.append(text)
            continue
        m = _CROSS_RE.match(text)
        if m:
            cross.append(Row(m.group("title"), "", m.group("verdict"), m.group("reasons") or "", text, True))
            continue
        m = _DOC_RE.match(text)
        if m:
            rows.append(Row(m.group("title"), m.group("where"), m.group("verdict"), m.group("reasons") or "", text))
            continue
        if label == "MISSING":
            rows.append(Row(text, "", "MISSING", "", text))
            continue
        if text.startswith("Not checked ("):
            m = _LABEL_RE.match(text)
            rows.append(Row(m.group("title") if m else text, "", label or "NOT CHECKED",
                            m.group("rest") if m else "", text))
            continue
        if label:                          # NEW CHECKS / ASSUMED / "PASS: A; B" / "X MISSING unless ..." lines
            m = _LABEL_RE.match(text)
            if m:
                rows.append(Row(m.group("title"), "", label, m.group("rest"), text))
            else:
                rows.append(Row(text, "", label, "", text))
            continue
        notes.append(text)
    return rows + cross, orders, stop, notes


def verdict_tag(verdict: str) -> str:
    return "v_" + re.sub(r"[^A-Z0-9]+", "_", str(verdict).upper()).strip("_")


# ==================================================================================================== deps
def _no_open(path: str) -> None:
    raise OSError("no opener")


def _no_probe() -> Any:
    return None


@dataclass
class Deps:
    """What the model needs from the outside world; `real_deps()` fills it, the tests pass fakes."""
    probe: Callable[[], Any] = _no_probe                               # gpuinfo.probe
    problems: Callable[[Any], List[Any]] = lambda info: []             # gpuinfo.problems(info, ARCH_FLAGS)
    need_free_mb: int = M.NEED_FREE_MB                                 # gpuinfo.NEED_FREE_MB
    default_lock: Callable[[], Optional[str]] = lambda: None           # cardgate.default_lock_setting
    lock_kind: Callable[[str], str] = lambda path: "other"             # cardgate.lock_kind
    start_check: Optional[Callable[[Dict[str, Any]], Any]] = None      # -> handle (drain, exit_code, cancel)
    start_selftest: Optional[Callable[[str], Any]] = None              # report root -> handle
    open_path: Callable[[str], None] = _no_open                        # os.startfile
    free_ram_mb: Callable[[], Optional[int]] = lambda: None
    allow_file: Callable[[str], None] = lambda path: None             # safety.allow_file (window guard)
    arm_roots: Callable[[List[str]], None] = lambda roots: None        # safety.arm_window
    housekeeping: Optional[Callable[[Path, int], Any]] = None         # userdirs.housekeeping
    integrity: Callable[[], Optional[str]] = lambda: None              # integrity_quick (MANIFEST sizes)
    cap_gb: float = CAP_GB                                             # version.CAP_GB
    default_report_root: Callable[[], Path] = U.default_report_root
    now: Callable[[], dt.datetime] = dt.datetime.now
    copy_file: Callable[[str, str], Any] = shutil.copyfile
    versions: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AutoSpec:
    input: str
    program: str
    today: Optional[str]
    report_root: Optional[str]
    result_path: Optional[str]


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def footer_app_line(versions: Mapping[str, Any]) -> str:
    if versions.get("footer"):
        return str(versions["footer"])                 # version.footer_text() (Task C)
    app = versions.get("app_version") or "1.0.0"
    build = versions.get("build_id") or "dev"
    base = versions.get("base_rules_version") or "?"
    runner = versions.get("runner_rules_version") or "?"
    return f"App {app} (GPU, build {build}) · Rules: bot {base} + runner {runner}"


# ==================================================================================================== model
class WindowModel:
    """The window's logic. Actions return short strings; `view()` gives everything the widgets show."""

    def __init__(self, data_dir: Path, deps: Optional[Deps] = None, *, settings: Optional[S.Settings] = None,
                 recent_items: Optional[List[R.RecentItem]] = None, auto: Optional[AutoSpec] = None,
                 persist: bool = True, lock_extra: Sequence[str] = (), trace_dir: Optional[str] = None):
        self.data_dir = Path(data_dir)
        self.d = deps or Deps()
        self.settings = settings if settings is not None else S.load(self.data_dir, self.d.default_lock)
        self.recent: List[R.RecentItem] = list(recent_items or [])
        self.auto = auto
        self.persist = persist and auto is None
        self.lock_extra = [p for p in (S.valid_abs_path(x) for x in lock_extra) if p]
        self.trace_dir = trace_dir
        self.report_root_override: Optional[str] = None
        self._default_root: Optional[str] = None
        # inputs
        self.input_path: Optional[str] = None
        self.program = self.settings.last_program
        self.date_mode = "today"
        self.fixed_date = ""
        # card
        self.card_info: Any = None
        self.card_problem: Optional[M.Message] = None
        self.card_note: Optional[M.Message] = None
        self.startup_problem: Optional[M.Message] = None    # MISSING_PART from the MANIFEST size check
        self.worker_card_problem: Optional[M.Message] = None   # a hard card code the worker reported
        self.footer_card = M.card_line(found=False)
        # check
        self.state = IDLE
        self.handle: Any = None
        self.job: Optional[Dict[str, Any]] = None
        self.case_folder: Optional[str] = None
        self.case_label = ""
        self.status: Optional[Banner] = None
        self.pending_confirm: Optional[M.Message] = None
        self.result: Optional[Result] = None
        self.housekeeping_counts: Any = None
        self.closing = False
        self.auto_done = False
        self.auto_status = 1
        self.selftest_handle: Any = None
        self._reset_run()

    # ------------------------------------------------------------------------------------------ run state
    def _reset_run(self) -> None:
        self.saw_hello = False
        self.terminal: Optional[str] = None            # "done" | "error" | "cancelled"
        self.terminal_code: Optional[str] = None
        self.exit_code: Optional[int] = None
        self.cancel_requested = False
        self.stage_key: Optional[str] = None
        self.pages_done = 0
        self.pages_of = 0
        self.page_seconds: List[float] = []
        self.firstread_pages = 0
        self.progress_text = ""
        self.progress_note = ""
        self.progress_mode = "off"
        self.progress_value = 0.0
        self.wait: Optional[Dict[str, Any]] = None
        self.waits = 0
        self.waited_s = 0.0
        self.budget_hits = 0
        self.check_date = ""
        self.done_event: Optional[Dict[str, Any]] = None

    # ------------------------------------------------------------------------------------------ start-up
    def startup(self) -> None:
        """Housekeeping, the first-start lock setting saved, the window guard's roots, the card probe; then the
        automatic check of --auto."""
        try:
            missing = self.d.integrity()
        except Exception:
            missing = None
        if missing:
            self.startup_problem = M.render(M.MISSING_PART, {"file": missing})
        hk = self.d.housekeeping
        if hk is not None:
            try:
                self.housekeeping_counts = hk(self.data_dir, int(self.settings.cache_days))
            except Exception:                            # housekeeping never stops the window
                self.housekeeping_counts = None
        self._save_settings()
        self._arm()
        self.probe_card()
        if self.auto is not None:
            self._auto_start()

    def _save_settings(self) -> None:
        if self.persist and self.settings.dirty:
            try:
                S.save(self.data_dir, self.settings)
            except OSError:
                pass

    def _arm(self) -> None:
        try:
            self.d.arm_roots([str(self.data_dir), str(self.report_root())])
        except Exception:
            pass

    # ------------------------------------------------------------------------------------------ card
    def probe_card(self) -> None:
        """gpuinfo.probe + problems: a hard problem goes to the banner and disables Check; too little free card
        memory is only a yellow note (a check waits for memory)."""
        try:
            info = self.d.probe()
        except Exception:
            info = None
        try:
            probs = list(self.d.problems(info)) if info is not None else []
        except Exception:
            probs = []
        self.card_info = info
        found = bool(_get(info, "nvml", False)) and int(_get(info, "count", 0) or 0) > 0
        self.footer_card = M.card_line(_get(info, "name"), _get(info, "total_mb"), _get(info, "driver"), found)
        base = self._info_values(info)
        hard: Optional[M.Message] = None
        low = False
        for p in probs:
            code = str(_get(p, "code", "") or "")
            pv = dict(_get(p, "values", {}) or {})
            families = {k.split("_")[0] for k in pv}             # the problem's own "total" beats the probe's total_mb
            vals = {k: v for k, v in base.items() if k.split("_")[0] not in families}
            vals.update(pv)
            if code == M.LOW_CARD_MEMORY:
                low = True
                continue
            if code == M.CARD_FAILED or not code:
                continue
            hard = M.render(code, vals)
            break
        if hard is None and not found:
            hard = M.render(M.NO_NVIDIA_CARD, base)
        if hard is None and self.worker_card_problem is not None:
            hard = self.worker_card_problem          # torch refused a card NVML accepted: no retry until a Self-test
        self.card_problem = hard
        free = _get(info, "free_mb")
        if hard is None and (low or (isinstance(free, (int, float)) and free < self.d.need_free_mb)):
            self.card_note = M.low_card_memory_note(free, self.d.need_free_mb)
        else:
            self.card_note = None

    @staticmethod
    def _info_values(info: Any) -> Dict[str, Any]:
        vals: Dict[str, Any] = {}
        for k in ("name", "driver", "total_mb", "free_mb"):
            v = _get(info, k)
            if v not in (None, ""):
                vals[k] = v
        cc = _get(info, "cc")
        if isinstance(cc, (tuple, list)) and len(cc) == 2:
            vals["cc"] = f"{cc[0]}.{cc[1]}"
        return vals

    # ------------------------------------------------------------------------------------------ inputs
    def report_root(self) -> str:
        if self.report_root_override:
            return self.report_root_override
        if self.settings.report_root:
            return self.settings.report_root
        if self._default_root is None:                     # the Known Folder call once per window
            try:
                self._default_root = str(self.d.default_report_root())
            except Exception:
                self._default_root = str(Path.home() / U.REPORTS_DIR_NAME)
        return self._default_root

    def choose_input(self, path: Optional[str]) -> str:
        if self.state in BUSY:
            return "busy"
        if not path:
            return "none"
        self.input_path = os.path.normpath(str(path))
        if self.status is not None and self.status.kind != M.WAIT:
            self.status = None
        return "ok"

    def set_program(self, value: str) -> str:
        if self.state in BUSY:
            return "busy"
        code = S.program_code(value)
        if code is None:
            return "bad"
        self.program = code
        S.set_program(self.settings, code)
        self._save_settings()
        return "ok"

    def set_date_mode(self, mode: str, fixed: Optional[str] = None) -> str:
        if mode not in ("today", "fixed"):
            return "bad"
        self.date_mode = mode
        if fixed is not None:
            self.fixed_date = str(fixed).strip()
        return "ok"

    def change_report_root(self, path: Optional[str]) -> str:
        if self.state in BUSY:
            return "busy"
        if not path:
            return "none"
        if not S.set_report_root(self.settings, path):
            self.status = self._banner(M.render(M.REPORT_NOT_WRITABLE))
            return "bad"
        self.report_root_override = None
        self._save_settings()
        self._arm()
        return "ok"

    def change_lock_path(self, path: Optional[str]) -> str:
        """Options: Change… (a file path, it need not exist yet) or Off (None)."""
        if self.state in BUSY:
            return "busy"
        if path == "":
            return "none"
        if not S.set_lock_path(self.settings, path):
            return "bad"
        self._save_settings()
        return "ok"

    def lock_paths(self) -> List[str]:
        out: List[str] = []
        for p in ([self.settings.gpu_lock_path] if self.settings.gpu_lock_path else []) + list(self.lock_extra):
            if p and os.path.normcase(p) not in {os.path.normcase(x) for x in out}:
                out.append(p)
        return out

    def _today_value(self) -> Tuple[bool, Optional[str]]:
        """(ok, the runner's `today`): (True, None) for "today"; (True, "YYYY-MM-DD") for a valid fixed date;
        (False, None) for a fixed date that is not one."""
        if self.date_mode != "fixed":
            return True, None
        s = self.fixed_date.strip()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
            return False, None
        try:
            return True, dt.date.fromisoformat(s).isoformat()
        except ValueError:
            return False, None

    # ------------------------------------------------------------------------------------------ check
    def _banner(self, msg: M.Message, detail: str = "", cancel: bool = False) -> Banner:
        return Banner(msg.kind, msg.title, msg.text, msg.code, detail, cancel)

    def _refuse(self, code: str, values: Optional[Mapping[str, Any]] = None) -> str:
        self.status = self._banner(M.render(code, values))
        if self.auto is not None:
            self._auto_finish("refused", code)
        return "refused"

    def check(self, confirmed: bool = False) -> str:
        """Check: probe the card, the window's fast pre-checks, the case folder, then the worker. Returns
        "started", "refused", "confirm" (low RAM: ask, then check(confirmed=True)), "busy" or "no_input"."""
        if self.state in BUSY:
            return "busy"
        self.pending_confirm = None
        if self.startup_problem is not None:
            self.status = None
            if self.auto is not None:
                self._auto_finish("refused", self.startup_problem.code)
            return "refused"
        if not self.input_path:
            self.status = self._banner(M.render(M.NO_INPUT))
            return "no_input"
        self.probe_card()
        if self.card_problem is not None:
            self.status = None
            if self.auto is not None:
                self._auto_finish("refused", self.card_problem.code)
            return "refused"
        ok, today = self._today_value()
        if not ok:
            return self._refuse(M.BAD_DATE)
        inp = self.input_path
        code = U.check_input(inp)
        if code:
            return self._refuse(code)
        root = self.report_root()
        code = U.check_report_root(root, inp)
        if code:
            return self._refuse(code)
        code = U.check_free_disk(root, U.input_bytes(inp))
        if code:
            return self._refuse(code)
        when = self.d.now()
        label = U.fit_label(root, U.input_stem(inp), self.program, when, U.input_names(inp))
        if label is None:
            return self._refuse(M.PATH_TOO_LONG)
        if not confirmed:
            try:
                free = self.d.free_ram_mb()
            except Exception:
                free = None
            if isinstance(free, (int, float)) and free < WARN_FREE_RAM_MB:
                self.pending_confirm = M.render(M.LOW_RAM_WARNING, {"free_mb": free, "need": M.RAM_NEED_GB})
                return "confirm"
        job_id = uuid.uuid4().hex
        try:
            folder = U.make_case_folder(root, label, self.program, when, job_id)
        except OSError:
            return self._refuse(M.REPORT_NOT_WRITABLE)
        self._reset_run()
        # ONE check date for the window and the worker: "today" is fixed here, at Check, and written into the job
        # (the worker would otherwise read its own clock seconds later, after midnight perhaps)
        self.check_date = today or when.date().isoformat()
        self.job = {
            "schema": "hdc-job/1", "job": job_id, "input": os.path.abspath(inp), "program": self.program,
            "out": str(folder), "today": self.check_date, "cache_root": str(self.data_dir / "cache"),
            "trace_dir": os.path.abspath(self.trace_dir) if self.trace_dir else None,
            "gpu": {"lock_paths": self.lock_paths(), "index": int(_get(self.card_info, "index", 0) or 0),
                    "cap_gb": float(self.d.cap_gb), "need_free_mb": int(self.d.need_free_mb)},
        }
        self.case_folder = str(folder)
        self.case_label = label
        self.result = None
        self.status = None
        if self.d.start_check is None:
            return self._start_failed()
        try:
            self.handle = self.d.start_check(dict(self.job))
        except Exception:
            return self._start_failed()
        self.state = RUNNING
        self.progress_text = M.PROGRESS_STARTING
        self.progress_mode = "indeterminate"
        return "started"

    def _start_failed(self) -> str:
        if self.case_folder and self.job:
            U.remove_case_folder_if_ours(self.case_folder, self.job["job"])
        self.handle = None
        self.state = IDLE
        return self._refuse(M.STARTUP_FAILED)

    def cancel(self) -> str:
        if self.state not in (RUNNING, WAITING):
            return "not_running"
        self.cancel_requested = True
        try:
            if self.handle is not None:
                self.handle.cancel()
        except Exception:
            pass
        self.state = CANCELLING
        self.progress_text = M.PROGRESS_CANCELLING
        self.progress_mode = "indeterminate"
        if self.status is not None and self.status.cancel:
            self.status = Banner(self.status.kind, self.status.title, self.status.text, self.status.code)
        return "cancelling"

    # ------------------------------------------------------------------------------------------ events
    def poll(self) -> bool:
        """Drain the running check's events and look whether it has finished (and the self-test). True when
        anything changed."""
        changed = False
        h = self.handle
        if h is not None:
            try:
                events = list(h.drain() or ())
            except Exception:
                events = []
            for ev in events:
                self.on_event(ev)
                changed = True
            try:
                fin = _finished(h)
            except Exception:
                fin = 1
            if fin is not None:
                self.on_finished(fin)
                changed = True
        st = self.selftest_handle
        if st is not None:
            try:
                code = _finished(st)
            except Exception:
                code = 1
            if code is not None:
                self._selftest_finished(code)
                changed = True
        return changed

    def on_event(self, ev: Mapping[str, Any]) -> None:
        if not isinstance(ev, Mapping):
            return
        kind = ev.get("ev")
        if self.terminal is not None and kind not in ("done", "error", "cancelled"):
            return
        fn = getattr(self, "_ev_" + str(kind), None) if isinstance(kind, str) and kind.isidentifier() else None
        if fn is not None:
            fn(ev)

    def _ev_hello(self, ev: Mapping[str, Any]) -> None:
        self.saw_hello = True

    def _ev_card(self, ev: Mapping[str, Any]) -> None:
        state = ev.get("state")
        if state == "found":
            self.footer_card = M.card_line(ev.get("name"), ev.get("total_mb"), ev.get("driver"), True)
        elif state == "ready" and self.state == RUNNING:
            self.progress_text = M.PROGRESS_CARD_READY

    def _ev_stage(self, ev: Mapping[str, Any]) -> None:
        key = str(ev.get("key") or "")
        st = ev.get("state")
        if st == "start":
            self.stage_key = key
            if self.state != RUNNING:
                return
            if key == "card":
                self.progress_text, self.progress_mode = M.PROGRESS_CARD, "indeterminate"
            elif key == "firstread":
                self.firstread_pages = 0
                self.progress_text, self.progress_mode = M.PROGRESS_FIRSTREAD, "indeterminate"
            else:
                self.progress_text, self.progress_mode = M.checking_progress(key), "indeterminate"
        elif st == "end" and key == "firstread" and self.firstread_pages == 0:
            self.progress_note = M.PROGRESS_SAVED

    def _ev_file(self, ev: Mapping[str, Any]) -> None:
        try:
            ocr = int(ev.get("ocr") or 0)
        except (TypeError, ValueError):
            ocr = 0
        if ocr and not self.pages_of:
            self.pages_of = ocr

    def seconds_per_page(self) -> float:
        if len(self.page_seconds) >= 2:
            return sum(self.page_seconds) / len(self.page_seconds)
        return float(self.settings.seconds_per_page_gpu)

    def _ev_page(self, ev: Mapping[str, Any]) -> None:
        try:
            done = int(ev.get("done") or 0)
            of = int(ev.get("of") or self.pages_of or 0)
        except (TypeError, ValueError):
            return
        secs = ev.get("seconds")
        if ev.get("where") == "card" and isinstance(secs, (int, float)) and not isinstance(secs, bool):
            self.page_seconds.append(float(secs))
        if self.stage_key == "firstread" or ev.get("where") == "card":
            self.firstread_pages += 1
        self.pages_done, self.pages_of = done, of
        if self.state != RUNNING:
            return
        left = max(0, of - done) * self.seconds_per_page() if of else None
        self.progress_text = M.reading_progress(done, of, left)
        self.progress_mode = "determinate" if of else "indeterminate"
        self.progress_value = (100.0 * min(done, of) / of) if of else 0.0

    def _ev_gpu_wait(self, ev: Mapping[str, Any]) -> None:
        st = ev.get("state")
        if st == "start":
            self.wait = {"why": ev.get("why") or "lock", "lock": ev.get("lock"), "waited_s": 0.0,
                         "free_mb": ev.get("free_mb"), "need_mb": ev.get("need_mb"), "where": ev.get("where")}
            if self.state == RUNNING:
                self.state = WAITING
        elif st == "tick":
            if self.wait is None:
                self.wait = {"why": ev.get("why") or "lock", "lock": ev.get("lock"), "waited_s": 0.0,
                             "free_mb": ev.get("free_mb"), "need_mb": ev.get("need_mb")}
                if self.state == RUNNING:
                    self.state = WAITING
            for k in ("waited_s", "free_mb", "need_mb", "lock"):
                if ev.get(k) is not None:
                    self.wait[k] = ev.get(k)
        elif st == "end":
            if self.wait is not None or self.state == WAITING:
                self.waits += 1
                w = ev.get("waited_s", (self.wait or {}).get("waited_s", 0))
                try:
                    self.waited_s += float(w or 0)
                except (TypeError, ValueError):
                    pass
            self.wait = None
            if self.state == WAITING:
                self.state = RUNNING
            if self.status is not None and self.status.kind == M.WAIT:
                self.status = None
            return
        else:
            return
        w = self.wait or {}
        msg = M.waiting(str(w.get("why")), w.get("lock"), w.get("waited_s"), w.get("free_mb"), w.get("need_mb"))
        self.status = self._banner(msg, cancel=self.state == WAITING)

    def _ev_budget(self, ev: Mapping[str, Any]) -> None:
        self.budget_hits += 1

    def _ev_done(self, ev: Mapping[str, Any]) -> None:
        self.terminal = "done"
        self.done_event = dict(ev)
        headline = str(ev.get("headline") or "")
        rows, orders, stop_pages, notes = parse_lines(ev.get("lines") or [], headline)
        blocks_default = any(r.verdict == "NOT JUDGED" for r in rows)
        fixes = []
        notices = ev.get("notices")
        if isinstance(notices, list) and notices:
            for n in notices:
                if isinstance(n, Mapping) and n.get("kind") == "ORDER":
                    pages = n.get("pages") or []
                    pw = _pages_word(pages)
                    fixes.append(M.order_text(pw, str(n.get("message") or ""), bool(n.get("blocks", True))))
        else:
            fixes = [M.order_text(p, msg, blocks_default) for p, msg in orders]
        gpu = ev.get("gpu") if isinstance(ev.get("gpu"), Mapping) else {}
        waited = gpu.get("waited_s", self.waited_s) if gpu else self.waited_s
        if (gpu.get("waits") if gpu else self.waits):
            notes.append(f"Waited {M.minutes(waited)} min for the graphics card.")
        cpu_calls = gpu.get("cpu_calls") if gpu else 0
        if isinstance(cpu_calls, int) and cpu_calls > 0:
            notes.append(f"{cpu_calls} large reading step(s) ran on the processor (too large for the graphics "
                         f"card's {float(self.d.cap_gb):.1f} GB cap).")
        # what can change a verdict against an idle PC: pages read smaller or left to the light reader (card
        # memory), and time-boxed QR / barcode steps that ran out of time (the done event's counts; the window's
        # own count of budget events when an older worker sends none)
        for note in (M.downscaled_note(gpu.get("downscaled") if gpu else 0),
                     M.skipped_note(gpu.get("skipped") if gpu else 0)):
            if note:
                notes.append(note)
        hits = ev.get("budget_hits")
        hits = hits if isinstance(hits, int) and not isinstance(hits, bool) else self.budget_hits
        if M.budget_note(hits):
            notes.append(M.budget_note(hits))
        if self.progress_note:
            notes.append(self.progress_note)
        date = ev.get("check_date")                    # the worker's check date (the one in the result code)
        if isinstance(date, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            self.check_date = date
        overall = str(ev.get("overall") or "")
        try:
            exit_ = int(ev.get("exit", 0) or 0)
        except (TypeError, ValueError):
            exit_ = 0
        stop = M.stop_text(stop_pages) if (stop_pages or overall == "STOP") else None
        self.result = Result(overall=overall, headline=headline, code=str(ev.get("code") or ""),
                             check_date=self.check_date, rows=rows, fixes=fixes, notes=notes, stop=stop,
                             out=self.case_folder or "", label=self.case_label, exit=exit_)
        self.status = self._banner(M.render(M.STOP, {"pages": stop_pages})) if stop else None
        self._finishing("Check finished")

    def _ev_error(self, ev: Mapping[str, Any]) -> None:
        code = str(ev.get("code") or M.CRASH)
        vals = dict(ev.get("values") or {}) if isinstance(ev.get("values"), Mapping) else {}
        if "stage" not in vals and self.stage_key:
            vals["stage"] = self.stage_key
        msg = M.render(code, vals)
        detail = M.safe_detail(ev.get("text")) if not M.known(code) or code == M.CRASH else ""
        self.terminal, self.terminal_code = "error", msg.code
        self.status = self._banner(msg, detail)
        if M.is_hard_card_code(code):
            self.card_problem = self.worker_card_problem = msg
            self.card_note = None
        self._finishing("Check stopped")

    def _ev_cancelled(self, ev: Mapping[str, Any]) -> None:
        self.terminal, self.terminal_code = "cancelled", M.CANCELLED
        self.status = self._banner(M.render(M.CANCELLED))
        self._finishing("Cancelled")

    def _finishing(self, text: str) -> None:
        self.wait = None
        self.state = FINISHING
        self.progress_text = text
        self.progress_mode = "off"

    def on_finished(self, outcome: Any) -> None:
        """The check has ended. `outcome` is Task C's controller Outcome (kind, code, exit, values, text, done,
        forced): its classification of the exit wins. A bare exit code (a simpler handle) is classified here."""
        if self.handle is None and self.state not in BUSY:
            return
        if isinstance(outcome, int) and not isinstance(outcome, bool):
            self.exit_code = int(outcome)
            if self.terminal is None:
                code = M.classify_exit(outcome, self.saw_hello)
                if self.cancel_requested or code == M.CANCELLED:
                    self._ev_cancelled({})
                else:
                    self._ev_error({"ev": "error", "code": code or M.CRASH, "values": {}})
        else:
            kind = str(_get(outcome, "kind") or "error")
            ex = _get(outcome, "exit")
            self.exit_code = int(ex) if isinstance(ex, int) and not isinstance(ex, bool) else None
            if kind == "done":
                done_ev = _get(outcome, "done")
                if self.terminal != "done" and isinstance(done_ev, Mapping):
                    self.terminal = None
                    self._ev_done(done_ev)
            elif kind == "cancelled":
                if self.terminal != "cancelled":
                    self._ev_cancelled({})
            else:
                code = str(_get(outcome, "code") or M.CRASH)
                vals = _get(outcome, "values")
                vals = dict(vals) if isinstance(vals, Mapping) else {}
                text = _get(outcome, "text") or ""
                if not text and vals.get("status"):
                    text = f"status {vals['status']}"
                if not (self.terminal == "error" and self.terminal_code == code):
                    self.terminal = None
                    self._ev_error({"ev": "error", "code": code, "values": vals, "text": text})
        self._wind_up()

    def on_exit(self, exit_code: int) -> None:
        """A bare exit code (kept for handles without an Outcome)."""
        self.on_finished(int(exit_code))

    def _wind_up(self) -> None:
        """After the end: the case folder tidied (C's controller does the same; both only act on this job's
        marker), recent.json and settings.json updated, the window ready again."""
        folder, job_id = self.case_folder, (self.job or {}).get("job")
        if folder and job_id:
            if self.terminal == "cancelled":
                U.remove_case_folder_if_ours(folder, job_id)
            elif self.terminal == "error" and _only_marker(folder, job_id):
                U.remove_case_folder_if_ours(folder, job_id)
            else:
                U.remove_marker(folder, job_id)
        if self.terminal == "done" and self.result is not None:
            self._remember_done()
        self.handle = None
        self.wait = None
        self.state = IDLE
        if self.terminal == "done" and self.result is not None and self.result.stop is None:
            self.progress_text = "Check finished"
        if self.auto is not None:
            st = self.terminal or "error"
            self._auto_finish(st, self.terminal_code)
        if self.closing:
            self.auto_done = True

    def _remember_done(self) -> None:
        r = self.result
        if r is None:
            return
        item = R.make(self.d.now(), self.input_path, self.program, r.overall or "FAIL", r.code, r.out)
        if r.overall in R.OVERALLS:
            self.recent = R.add(self.recent, item)
            if self.persist:
                try:
                    R.save(self.data_dir, self.recent)
                except OSError:
                    pass
        if S.add_page_seconds(self.settings, self.page_seconds):
            self._save_settings()

    # ------------------------------------------------------------------------------------------ self-test
    def selftest(self) -> str:
        if self.state in BUSY:
            return "busy"
        root = self.report_root()
        code = U.check_report_root(root, None)
        if code:
            self.status = self._banner(M.render(code))
            return "refused"
        if self.d.start_selftest is None:
            self.status = self._banner(M.render(M.STARTUP_FAILED))
            return "refused"
        try:
            self.selftest_handle = self.d.start_selftest(root)
        except Exception:
            self.selftest_handle = None
            self.status = self._banner(M.render(M.STARTUP_FAILED))
            return "refused"
        self.state = SELFTEST
        self.status = self._banner(M.render(M.SELFTEST_RUNNING))
        self.progress_text, self.progress_mode = M.PROGRESS_SELFTEST, "indeterminate"
        return "started"

    def _selftest_finished(self, code: Any) -> None:
        self.selftest_handle = None
        self.state = IDLE
        self.progress_text, self.progress_mode = "", "off"
        ok = code == 0
        self.status = self._banner(M.render(M.SELFTEST_OK if ok else M.SELFTEST_FAILED))
        if ok:
            self.worker_card_problem = None          # the self-test's card part read pages on this card
        self.probe_card()
        if self.closing:
            self.auto_done = True

    # ------------------------------------------------------------------------------------------ result actions
    def _out_file(self, name: str) -> Optional[Path]:
        if self.result is None or not self.result.out:
            return None
        return Path(self.result.out) / name

    def open_report_folder(self) -> str:
        out = self.result.out if self.result else ""
        if not out or not os.path.isdir(out):
            self.status = self._banner(M.render(M.FOLDER_GONE))
            return "missing"
        try:
            self.d.open_path(out)
        except OSError:
            self.status = self._banner(M.render(M.FOLDER_GONE))
            return "failed"
        return "ok"

    def open_excel(self) -> str:
        p = self._out_file("report.xlsx")
        if p is None or not p.is_file():
            self.status = self._banner(M.render(M.FILE_MISSING, {"file": "report.xlsx"}))
            return "missing"
        try:
            self.d.open_path(str(p))
            return "ok"
        except OSError:
            self.status = self._banner(M.render(M.NO_XLSX_PROGRAM))
            try:
                self.d.open_path(str(p.parent))
            except OSError:
                pass
            return "folder"

    def result_code_text(self) -> str:
        return self.result.code if self.result else ""

    def summary_text(self) -> Optional[str]:
        p = self._out_file("summary.txt")
        try:
            if p is None:
                raise OSError
            return p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            self.status = self._banner(M.render(M.FILE_MISSING, {"file": "summary.txt"}))
            return None

    def save_pdf_default(self) -> Tuple[str, str]:
        """(folder, file name) for the Save as PDF dialog: <label>.pdf in the case folder."""
        r = self.result
        return (r.out if r else "", (U.safe_label(r.label or "summary") if r else "summary") + ".pdf")

    def save_pdf(self, target: Optional[str]) -> str:
        if not target:
            return "none"
        src = self._out_file("summary.pdf")
        if src is None or not src.is_file():
            self.status = self._banner(M.render(M.FILE_MISSING, {"file": "summary.pdf"}))
            return "missing"
        if self.input_path and U.is_within(target, self.input_path):
            self.status = self._banner(M.render(M.SAVE_INSIDE_INPUT))
            return "refused"
        try:
            self.d.allow_file(str(target))
            self.d.copy_file(str(src), str(target))
        except Exception:
            self.status = self._banner(M.render(M.SAVE_FAILED))
            return "failed"
        self.status = self._banner(M.render(M.SAVED, {"file": os.path.basename(str(target))}))
        return "ok"

    def open_recent(self, index: int) -> str:
        if not (0 <= index < len(self.recent)):
            return "none"
        folder = self.recent[index].folder
        if not os.path.isdir(folder):
            self.status = self._banner(M.render(M.FOLDER_GONE))
            return "missing"
        try:
            self.d.open_path(folder)
        except OSError:
            self.status = self._banner(M.render(M.FOLDER_GONE))
            return "failed"
        return "ok"

    def dismiss_banner(self) -> None:
        if self.status is not None and self.status.kind != M.WAIT:
            self.status = None

    # ------------------------------------------------------------------------------------------ closing
    def request_close(self) -> str:
        """"close" when nothing runs, else "confirm" (ask, then close_now())."""
        return "confirm" if self.state in BUSY else "close"

    def close_now(self) -> str:
        """After the user agreed: cancel a running check; the window closes when its worker has ended."""
        self.closing = True
        if self.state in (RUNNING, WAITING):
            self.cancel()
            return "wait"
        if self.state in (CANCELLING, FINISHING):
            return "wait"
        if self.state == SELFTEST and self.selftest_handle is not None:
            try:
                self.selftest_handle.cancel()
            except Exception:
                pass
            return "wait"
        self.auto_done = True
        return "close"

    # ------------------------------------------------------------------------------------------ --auto
    def _auto_start(self) -> None:
        a = self.auto
        if a is None:
            return
        self.choose_input(a.input)
        code = S.program_code(a.program)
        if code:
            self.program = code
        if a.today:
            self.set_date_mode("fixed", a.today)
        if a.report_root:
            self.report_root_override = os.path.normpath(a.report_root)
            self._arm()
        r = self.check(confirmed=True)
        if r == "no_input":
            self._auto_finish("refused", M.NO_INPUT)

    def _auto_finish(self, state: str, code: Optional[str]) -> None:
        if self.auto is None or self.auto_done:
            return
        r = self.result
        data = {"schema": "hdc-auto/1", "state": state, "error": code if state != "done" else None,
                "overall": r.overall if (r and state == "done") else None,
                "code": r.code if (r and state == "done") else None,
                "check_date": (r.check_date if (r and state == "done") else self.check_date) or None,
                "exit": self.exit_code, "waits": self.waits, "waited_s": round(self.waited_s, 1),
                "pages_read_on_card": len(self.page_seconds)}
        path = self.auto.result_path
        if path:
            try:
                self.d.allow_file(str(path))
                tmp = str(path) + ".tmp"
                self.d.allow_file(tmp)
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump(data, fh, indent=1)
                os.replace(tmp, path)
            except Exception:
                pass
        self.auto_status = 0 if state == "done" else 1
        self.auto_done = True

    # ------------------------------------------------------------------------------------------ view
    def lock_row(self) -> Tuple[str, bool, bool]:
        """(text, is the bot's default, off) of the Options row."""
        p = self.settings.gpu_lock_path
        if not p:
            return LOCK_OFF_TEXT, False, True
        try:
            is_bot = self.d.lock_kind(p) == "bot"
        except Exception:
            is_bot = False
        return (f"{p} {BOT_SUFFIX}" if is_bot else p), is_bot, False

    def view(self) -> View:
        banner = self.status
        if banner is None and self.startup_problem is not None:
            banner = self._banner(self.startup_problem)
        if banner is None and self.card_problem is not None:
            banner = self._banner(self.card_problem)
        if banner is None and self.card_note is not None and self.state not in BUSY:
            banner = self._banner(self.card_note)
        lock_text, is_bot, off = self.lock_row()
        idle = self.state == IDLE
        r = self.result
        if self.input_path:
            input_text = os.path.basename(self.input_path.rstrip("\\/")) or self.input_path
        else:
            input_text = "(nothing chosen)"
        return View(
            title=TITLE, state=self.state,
            input_text=input_text,
            input_chosen=bool(self.input_path),
            program_label=PROGRAM_LABEL.get(self.program, self.program),
            program_labels=tuple(label for label, _ in PROGRAM_CHOICES),
            date_mode=self.date_mode, fixed_date=self.fixed_date,
            report_root_text=self.report_root(),
            lock_label=LOCK_ROW_LABEL, lock_text=lock_text, lock_is_default=is_bot, lock_off=off,
            check_enabled=(idle and bool(self.input_path) and self.card_problem is None
                           and self.startup_problem is None and not self.closing),
            cancel_enabled=self.state in (RUNNING, WAITING),
            selftest_enabled=idle and not self.closing,
            edit_enabled=idle,
            progress_text=self.progress_text, progress_note=self.progress_note,
            progress_mode=self.progress_mode if self.state in BUSY else "off",
            progress_value=self.progress_value,
            progress_paused=self.state == WAITING,
            banner=banner, result=r,
            result_buttons=bool(r is not None and r.out and idle),
            recent_lines=[R.display(i) for i in self.recent],
            footer_app=footer_app_line(self.d.versions), footer_card=self.footer_card)

    def exit_status(self) -> int:
        return self.auto_status if self.auto is not None else 0


def _pages_word(pages: Any) -> str:
    try:
        a, b = int(pages[0]), int(pages[1])
    except (TypeError, ValueError, IndexError):
        return "the pages"
    return f"page {a}" if a == b else f"pages {a}-{b}"


def _only_marker(folder: str, job_id: str) -> bool:
    try:
        names = os.listdir(folder)
    except OSError:
        return False
    return names == [U.MARKER_PREFIX + job_id]


# ==================================================================================================== handles
def _decode(ev: Any) -> Optional[Dict[str, Any]]:
    if isinstance(ev, Mapping):
        return dict(ev)
    if isinstance(ev, (bytes, str)):
        try:
            d = json.loads(ev)
        except ValueError:
            return None
        return d if isinstance(d, dict) else None
    return None


def _finished(h: Any) -> Any:
    """A handle's end: `finished()` (an Outcome, or an exit code), else `exit_code()`; None while it runs."""
    fn = getattr(h, "finished", None)
    if callable(fn):
        return fn()
    fn = getattr(h, "exit_code", None)
    return fn() if callable(fn) else None


def job_path(data_dir: Path, job_id: str) -> Path:
    """<DATA>\\jobs\\<job>\\job.json (rev 1 section 2; emptied by the start-up housekeeping)."""
    return Path(data_dir) / "jobs" / str(job_id) / "job.json"


def write_job(data_dir: Path, job: Mapping[str, Any], protocol_mod: Any = None) -> Path:
    """job.json through Task C's `protocol.write_job` (validated, atomic); a plain atomic write without it."""
    p = job_path(data_dir, job["job"])
    if protocol_mod is None:
        try:
            from . import protocol as protocol_mod       # Task C
        except ImportError:
            protocol_mod = None
    if protocol_mod is not None and hasattr(protocol_mod, "write_job"):
        return Path(protocol_mod.write_job(p, dict(job)))
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(dict(job), ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)
    return p


class ControllerHandle:
    """The model's handle (drain / finished / cancel) over Task C's `controller.CheckController`: the window
    writes job.json, `start(job_path, log_path=<DATA>\\logs\\worker-<job>.log)` starts the worker,
    `poll()` gives the events applied since the last call, `cancel()` asks the worker to stop (TerminateProcess
    after 10 s), and `outcome` (once `state` is "done") classifies the end."""

    def __init__(self, job: Mapping[str, Any], data_dir: Path, controller_mod: Any = None, protocol_mod: Any = None):
        if controller_mod is None:
            from . import controller as controller_mod    # Task C
        self.job_path = write_job(data_dir, job, protocol_mod)
        self.c = controller_mod.CheckController()
        log = Path(data_dir) / "logs" / f"worker-{job['job']}.log"
        self.c.start(self.job_path, log_path=log)

    def drain(self) -> List[Dict[str, Any]]:
        out = []
        for ev in self.c.poll() or ():
            d = _decode(ev)
            if d is not None:
                out.append(d)
        return out

    def finished(self) -> Any:
        if getattr(self.c, "state", None) == "done":
            return self.c.outcome
        return None

    def cancel(self) -> None:
        self.c.cancel()


class SelftestHandle:
    """The Self-test button: the same exe with --selftest --report-dir <root>, as its own child (no window)."""

    def __init__(self, report_root: str, spawn_mod: Any = None):
        import subprocess
        if spawn_mod is None:
            from . import spawn as spawn_mod               # Task C
        self.spawn = spawn_mod
        cmd = spawn_mod.self_command("--selftest", "--report-dir", str(report_root))
        self.p = spawn_mod.start_process(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)

    def drain(self) -> List[Dict[str, Any]]:
        return []

    def finished(self) -> Optional[int]:
        return self.p.poll()

    def cancel(self) -> None:
        """Only our own child (spawn.terminate = TerminateProcess on its handle)."""
        try:
            self.spawn.terminate(self.p, wait_s=3.0)
        except Exception:
            pass


# ==================================================================================================== real deps
def version_info(version_mod: Any = None) -> Dict[str, Any]:
    """Footer versions and ARCH_FLAGS from Task C's app/version.py (several spellings), with DEV defaults."""
    if version_mod is None:
        try:
            from . import version as version_mod     # Task C
        except ImportError:
            version_mod = None
    src: Dict[str, Any] = {}
    for getter in ("info", "build", "build_info"):
        fn = getattr(version_mod, getter, None)
        if callable(fn):
            try:
                v = fn()
                if isinstance(v, Mapping):
                    src.update(v)
                    break
            except Exception:
                pass
    b = getattr(version_mod, "BUILD", None)
    if isinstance(b, Mapping):
        for k, v in b.items():
            src.setdefault(k, v)

    def pick(*names: str) -> Any:
        for n in names:
            if version_mod is not None and getattr(version_mod, n, None) not in (None, ""):
                return getattr(version_mod, n)
            if src.get(n) not in (None, ""):
                return src[n]
        return None

    out = {"app_version": pick("APP_VERSION", "app_version", "VERSION"),
           "build_id": pick("BUILD_ID", "build_id"),
           "base_rules_version": pick("BASE_RULES_VERSION", "base_rules_version"),
           "runner_rules_version": pick("RUNNER_RULES_VERSION", "runner_rules_version"),
           "arch_flags": pick("ARCH_FLAGS", "arch_flags")}
    if not out["base_rules_version"] or not out["runner_rules_version"]:
        try:
            from .. import model as _model
            out["base_rules_version"] = out["base_rules_version"] or _model.BASE_RULES_VERSION
            out["runner_rules_version"] = out["runner_rules_version"] or _model.RUNNER_RULES_VERSION
        except ImportError:
            pass
    flags = out["arch_flags"]
    out["arch_flags"] = tuple(flags) if isinstance(flags, (list, tuple)) and flags else DEFAULT_ARCH_FLAGS
    footer = getattr(version_mod, "footer_text", None)
    if callable(footer):
        try:
            out["footer"] = str(footer())
        except Exception:
            pass
    for k in ("CAP_GB", "NEED_FREE_MB"):
        v = getattr(version_mod, k, None)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out[k.lower()] = v
    return out


def integrity_quick(app_folder: Optional[Path] = None) -> Optional[str]:
    """Rev 1 section 1.4: the size of every file of the app folder against _internal\\MANIFEST.json (Task F's
    "hdc-manifest/1": {"files": {"<relative/posix path>": {"size", "sha256"}}}). Returns the first missing or
    changed file, relative to the app folder; None when every size matches, when not frozen, or when there is no
    readable manifest (the Self-test's full sha256 check covers that)."""
    if app_folder is None:
        if not getattr(sys, "frozen", False):
            return None
        app_folder = Path(sys.executable).parent
    try:
        data = json.loads((Path(app_folder) / "_internal" / "MANIFEST.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    files = data.get("files") if isinstance(data, dict) else None
    if not isinstance(files, dict):
        return None
    for rel in sorted(files):
        info = files[rel]
        parts = [p for p in str(rel).split("/") if p]
        if not parts or any(p in (".", "..") or ":" in p or "\\" in p for p in parts):
            continue
        try:
            size = os.stat(os.path.join(str(app_folder), *parts)).st_size
        except OSError:
            return "\\".join(parts)
        want = info.get("size") if isinstance(info, dict) else None
        if isinstance(want, int) and not isinstance(want, bool) and size != want:
            return "\\".join(parts)
    return None


def free_ram_mb() -> Optional[int]:
    """Free physical memory (GlobalMemoryStatusEx), MB."""
    try:
        import ctypes
        from ctypes import wintypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

        st = MEMORYSTATUSEX()
        st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if not ctypes.WinDLL("kernel32").GlobalMemoryStatusEx(ctypes.byref(st)):
            return None
        return int(st.ullAvailPhys // (1024 * 1024))
    except Exception:
        return None


def real_deps(data_dir: Path) -> Deps:
    """The real helpers: Task E's gpuinfo and cardgate, Task C's controller / spawn / version, Task A's window
    guard. A module that is missing leaves the safe default (no card -> NO_NVIDIA_CARD; no guard call)."""
    v = version_info()
    flags = v["arch_flags"]
    d = Deps(versions=v, open_path=getattr(os, "startfile", _no_open), free_ram_mb=free_ram_mb,
             housekeeping=lambda data, days: U.housekeeping(data, days), integrity=integrity_quick)
    if isinstance(v.get("need_free_mb"), int):
        d.need_free_mb = int(v["need_free_mb"])
    if isinstance(v.get("cap_gb"), (int, float)):
        d.cap_gb = float(v["cap_gb"])
    try:
        from . import gpuinfo                          # Task E (ctypes only, never torch)
        d.probe = gpuinfo.probe
        d.problems = lambda info: gpuinfo.problems(info, flags)
        d.need_free_mb = int(getattr(gpuinfo, "NEED_FREE_MB", d.need_free_mb))
    except ImportError:
        pass
    try:
        from . import cardgate                         # Task E (standard library only)
        d.default_lock = cardgate.default_lock_setting
        d.lock_kind = cardgate.lock_kind
    except ImportError:
        pass
    try:
        from .. import safety                          # Task A: arm_window(roots), allow_file(path)
        arm = getattr(safety, "arm_window", None)
        allow = getattr(safety, "allow_file", None)
        if callable(arm):
            d.arm_roots = lambda roots: arm([Path(r) for r in roots])
        if callable(allow):
            d.allow_file = lambda p: allow(Path(p))
    except ImportError:
        pass
    d.start_check = lambda job: ControllerHandle(job, data_dir)
    d.start_selftest = lambda root: SelftestHandle(root)
    return d


# ==================================================================================================== Tk binding
class TkWindow:
    """Tk + ttk widgets bound to a WindowModel (rev 1 section 3.1 layout). Every widget change comes from
    `render(model.view())`; every click calls one model action."""

    POLL_MS = 100

    def __init__(self, root: Any, model: WindowModel, *, tk: Any, ttk: Any, filedialog: Any, messagebox: Any):
        self.root, self.m, self.tk, self.ttk, self.fd, self.mb = root, model, tk, ttk, filedialog, messagebox
        self._result_drawn = False
        self._result_obj: Optional[Result] = None          # held, so a new Result is never mistaken for it
        self._recent_shown: List[str] = []
        self._options_open = False
        self._rows: Dict[str, Row] = {}
        self._indeterminate = False
        root.title(TITLE)
        try:
            root.minsize(860, 620)
        except Exception:
            pass
        self._style()
        self._build()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.bind("<Return>", lambda e: self.on_check())
        root.bind("<Escape>", lambda e: self.on_cancel())

    # ------------------------------------------------------------------------------------------ build
    def _style(self) -> None:
        try:
            self.root.option_add("*Font", ("Segoe UI", 10))
            st = self.ttk.Style(self.root)
            st.configure(".", font=("Segoe UI", 10))
            st.configure("Overall.TLabel", font=("Segoe UI", 16, "bold"))
            st.configure("Small.TLabel", font=("Segoe UI", 9))
            st.configure("Fix.TLabel", background="#FFEB9C")
        except Exception:
            pass

    def _button(self, parent: Any, text: str, command: Callable[[], Any], **grid: Any) -> Any:
        b = self.ttk.Button(parent, text=text, command=command)
        b.grid(**grid)
        return b

    def _build(self) -> None:
        tk, ttk, root = self.tk, self.ttk, self.root
        root.columnconfigure(0, weight=1)
        root.rowconfigure(2, weight=1)
        top = ttk.Frame(root, padding=(10, 8, 10, 4))
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(4, weight=1)
        ttk.Label(top, text="1  What to check").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.b_pdf = self._button(top, "Choose PDF…", self.on_choose_pdf, row=0, column=1, padx=2)
        self.b_folder = self._button(top, "Choose folder…", self.on_choose_folder, row=0, column=2, padx=2)
        self.v_input = tk.StringVar(master=root, value="")
        ttk.Label(top, textvariable=self.v_input).grid(row=0, column=3, columnspan=2, sticky="w", padx=8)
        ttk.Label(top, text="2  Program").grid(row=1, column=0, sticky="w", pady=4)
        self.v_program = tk.StringVar(master=root, value="")
        self.c_program = ttk.Combobox(top, textvariable=self.v_program, state="readonly", width=14,
                                      values=[label for label, _ in PROGRAM_CHOICES])
        self.c_program.grid(row=1, column=1, columnspan=2, sticky="w", pady=4)
        self.c_program.bind("<<ComboboxSelected>>", lambda e: self.on_program())
        self.b_options = self._button(top, "▸ Options", self.on_toggle_options, row=2, column=0, sticky="w")
        # options
        self.f_options = ttk.Frame(top, padding=(16, 2, 0, 2))
        self.f_options.grid(row=3, column=0, columnspan=5, sticky="ew")
        self.f_options.columnconfigure(3, weight=1)
        ttk.Label(self.f_options, text="Check date").grid(row=0, column=0, sticky="w")
        self.v_date_mode = tk.StringVar(master=root, value="today")
        self.r_today = ttk.Radiobutton(self.f_options, text="today", value="today", variable=self.v_date_mode,
                                       command=self.on_date_mode)
        self.r_today.grid(row=0, column=1, sticky="w")
        self.r_fixed = ttk.Radiobutton(self.f_options, text="fixed", value="fixed", variable=self.v_date_mode,
                                       command=self.on_date_mode)
        self.r_fixed.grid(row=0, column=2, sticky="w")
        self.v_fixed = tk.StringVar(master=root, value="")
        self.e_fixed = ttk.Entry(self.f_options, textvariable=self.v_fixed, width=12)
        self.e_fixed.grid(row=0, column=3, sticky="w", padx=4)
        self.e_fixed.bind("<FocusOut>", lambda e: self.on_date_mode())
        ttk.Label(self.f_options, text="Report folder").grid(row=1, column=0, sticky="w")
        self.v_root = tk.StringVar(master=root, value="")
        ttk.Label(self.f_options, textvariable=self.v_root).grid(row=1, column=1, columnspan=3, sticky="w")
        self.b_root = self._button(self.f_options, "Change…", self.on_change_root, row=1, column=4, padx=2)
        ttk.Label(self.f_options, text=LOCK_ROW_LABEL).grid(row=2, column=0, columnspan=2, sticky="w")
        self.v_lock = tk.StringVar(master=root, value="")
        ttk.Label(self.f_options, textvariable=self.v_lock, wraplength=520).grid(row=2, column=2, columnspan=2,
                                                                                  sticky="w")
        self.b_lock = self._button(self.f_options, "Change…", self.on_change_lock, row=2, column=4, padx=2)
        self.b_lock_off = self._button(self.f_options, "Off", self.on_lock_off, row=2, column=5, padx=2)
        self.f_options.grid_remove()
        # check row
        row = ttk.Frame(top)
        row.grid(row=4, column=0, columnspan=5, sticky="ew", pady=(6, 2))
        row.columnconfigure(2, weight=1)
        self.b_check = self._button(row, "Check", self.on_check, row=0, column=0, padx=(0, 4))
        self.b_cancel = self._button(row, "Cancel", self.on_cancel, row=0, column=1, padx=(0, 8))
        self.v_progress = tk.StringVar(master=root, value="")
        ttk.Label(row, textvariable=self.v_progress).grid(row=0, column=2, sticky="w")
        self.p_bar = ttk.Progressbar(row, mode="determinate", length=200, maximum=100)
        self.p_bar.grid(row=0, column=3, sticky="e")
        self.v_note = tk.StringVar(master=root, value="")
        ttk.Label(row, textvariable=self.v_note, style="Small.TLabel").grid(row=1, column=2, columnspan=2,
                                                                              sticky="w")
        # banner
        self.f_banner = tk.Frame(root, bd=1, relief="solid", padx=10, pady=6)
        self.f_banner.grid(row=1, column=0, sticky="ew", padx=10, pady=4)
        self.f_banner.columnconfigure(0, weight=1)
        self.l_banner_title = tk.Label(self.f_banner, text="", font=("Segoe UI", 10, "bold"), anchor="w")
        self.l_banner_title.grid(row=0, column=0, sticky="w")
        self.l_banner_text = tk.Label(self.f_banner, text="", wraplength=760, justify="left", anchor="w")
        self.l_banner_text.grid(row=1, column=0, sticky="w")
        self.l_banner_detail = tk.Label(self.f_banner, text="", font=("Segoe UI", 8), wraplength=760,
                                        justify="left", anchor="w")
        self.l_banner_detail.grid(row=2, column=0, sticky="w")
        self.b_banner_cancel = ttk.Button(self.f_banner, text="Cancel", command=self.on_cancel)
        self.b_banner_cancel.grid(row=0, column=1, rowspan=2, sticky="e")
        self.f_banner.grid_remove()
        # result
        res = ttk.Frame(root, padding=(10, 4, 10, 4))
        res.grid(row=2, column=0, sticky="nsew")
        res.columnconfigure(0, weight=1)
        res.rowconfigure(2, weight=1)
        head = ttk.Frame(res)
        head.grid(row=0, column=0, sticky="ew")
        head.columnconfigure(0, weight=1)
        self.v_overall = tk.StringVar(master=root, value="")
        ttk.Label(head, textvariable=self.v_overall, style="Overall.TLabel").grid(row=0, column=0, sticky="w")
        self.v_code = tk.StringVar(master=root, value="")
        ttk.Label(head, textvariable=self.v_code).grid(row=0, column=1, sticky="e", padx=4)
        self.b_copy_code = self._button(head, "Copy", self.on_copy_code, row=0, column=2, sticky="e")
        self.f_fixes = ttk.Frame(res)
        self.f_fixes.grid(row=1, column=0, sticky="ew")
        self.f_fixes.columnconfigure(0, weight=1)
        self._fix_labels: List[Any] = []
        tf = ttk.Frame(res)
        tf.grid(row=2, column=0, sticky="nsew")
        tf.columnconfigure(0, weight=1)
        tf.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(tf, columns=("pages", "verdict", "reasons"), show="tree headings", height=10,
                                 selectmode="browse")
        self.tree.heading("#0", text="Document")
        self.tree.heading("pages", text="Pages")
        self.tree.heading("verdict", text="Verdict")
        self.tree.heading("reasons", text="Reasons")
        self.tree.column("#0", width=230, stretch=False)
        self.tree.column("pages", width=150, stretch=False)
        self.tree.column("verdict", width=100, stretch=False)
        self.tree.column("reasons", width=380, stretch=True)
        for verdict, fill in VERDICT_FILL.items():
            self.tree.tag_configure(verdict_tag(verdict), background="#" + fill)
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.on_select())
        self.t_detail = tk.Text(res, height=3, wrap="word", relief="flat")
        self.t_detail.grid(row=3, column=0, sticky="ew", pady=(4, 2))
        self.t_detail.configure(state="disabled")
        btns = ttk.Frame(res)
        btns.grid(row=4, column=0, sticky="w", pady=(2, 0))
        self.b_excel = self._button(btns, "Open Excel report", self.on_open_excel, row=0, column=0, padx=(0, 4))
        self.b_folder_open = self._button(btns, "Open report folder", self.on_open_folder, row=0, column=1, padx=4)
        self.b_save_pdf = self._button(btns, "Save as PDF…", self.on_save_pdf, row=0, column=2, padx=4)
        self.b_copy_summary = self._button(btns, "Copy summary", self.on_copy_summary, row=0, column=3, padx=4)
        # recent + footer
        bottom = ttk.Frame(root, padding=(10, 2, 10, 8))
        bottom.grid(row=3, column=0, sticky="ew")
        bottom.columnconfigure(1, weight=1)
        ttk.Label(bottom, text="Recent checks (double-click opens the folder)").grid(row=0, column=0, columnspan=3,
                                                                                    sticky="w")
        self.l_recent = tk.Listbox(bottom, height=4, activestyle="none")
        self.l_recent.grid(row=1, column=0, columnspan=3, sticky="ew")
        self.l_recent.bind("<Double-Button-1>", lambda e: self.on_open_recent())
        self.b_selftest = self._button(bottom, "Self-test", self.on_selftest, row=2, column=0, sticky="w",
                                       pady=(6, 0))
        self.v_footer_app = tk.StringVar(master=root, value="")
        ttk.Label(bottom, textvariable=self.v_footer_app, style="Small.TLabel").grid(row=2, column=1, sticky="e",
                                                                                      pady=(6, 0))
        self.v_footer_card = tk.StringVar(master=root, value="")
        ttk.Label(bottom, textvariable=self.v_footer_card, style="Small.TLabel").grid(row=3, column=1, sticky="e")

    # ------------------------------------------------------------------------------------------ render
    @staticmethod
    def _enable(widget: Any, on: bool) -> None:
        try:
            widget.state(["!disabled"] if on else ["disabled"])
        except Exception:
            try:
                widget.configure(state="normal" if on else "disabled")
            except Exception:
                pass

    def render(self, v: Optional[View] = None) -> None:
        v = v or self.m.view()
        self.v_input.set(v.input_text)
        if self.v_program.get() != v.program_label:
            self.v_program.set(v.program_label)
        if self.v_date_mode.get() != v.date_mode:
            self.v_date_mode.set(v.date_mode)
        self.v_root.set(v.report_root_text)
        self.v_lock.set(v.lock_text)
        for w in (self.b_pdf, self.b_folder, self.c_program, self.b_root, self.b_lock, self.r_today, self.r_fixed,
                  self.e_fixed):
            self._enable(w, v.edit_enabled)
        self._enable(self.b_lock_off, v.edit_enabled and not v.lock_off)
        self._enable(self.b_check, v.check_enabled)
        self._enable(self.b_cancel, v.cancel_enabled)
        self._enable(self.b_selftest, v.selftest_enabled)
        self.v_progress.set(v.progress_text)
        self.v_note.set(v.progress_note)
        self._progress(v)
        self._banner(v.banner)
        self._result(v.result, v.result_buttons)
        if v.recent_lines != self._recent_shown:
            self.l_recent.delete(0, "end")
            for line in v.recent_lines:
                self.l_recent.insert("end", line)
            self._recent_shown = list(v.recent_lines)
        self.v_footer_app.set(v.footer_app)
        self.v_footer_card.set(v.footer_card)

    def _progress(self, v: View) -> None:
        mode = v.progress_mode
        if mode == "indeterminate" and not v.progress_paused:
            if not self._indeterminate:
                self.p_bar.configure(mode="indeterminate")
                self.p_bar.start(15)
                self._indeterminate = True
            return
        if self._indeterminate:
            self.p_bar.stop()
            self._indeterminate = False
        self.p_bar.configure(mode="determinate")
        self.p_bar.configure(value=v.progress_value if mode == "determinate" else 0)

    def _banner(self, b: Optional[Banner]) -> None:
        if b is None:
            self.f_banner.grid_remove()
            return
        bg, fg = BANNER_COLOURS.get(b.kind, BANNER_COLOURS[M.INFO])
        self.f_banner.configure(background=bg)
        for w in (self.l_banner_title, self.l_banner_text, self.l_banner_detail):
            w.configure(background=bg, foreground=fg)
        self.l_banner_title.configure(text=b.title)
        self.l_banner_text.configure(text=b.text)
        self.l_banner_detail.configure(text=b.detail)
        if b.cancel:
            self.b_banner_cancel.grid()
        else:
            self.b_banner_cancel.grid_remove()
        self.f_banner.grid()

    def _result(self, r: Optional[Result], buttons: bool) -> None:
        for w in (self.b_excel, self.b_folder_open, self.b_save_pdf, self.b_copy_summary):
            self._enable(w, buttons)
        self._enable(self.b_copy_code, bool(r and r.code))
        if self._result_drawn and r is self._result_obj:
            return
        self._result_drawn, self._result_obj = True, r
        for item in self.tree.get_children(""):
            self.tree.delete(item)
        self._rows = {}
        for lab in self._fix_labels:
            lab.destroy()
        self._fix_labels = []
        self._detail("")
        if r is None:
            self.v_overall.set("")
            self.v_code.set("")
            return
        self.v_overall.set(r.headline or r.overall)
        self.v_code.set(f"Result code {r.code} (check date {r.check_date})" if r.code else "")
        for i, text in enumerate(list(r.fixes) + ([r.stop] if r.stop else []) + list(r.notes)):
            style = "Fix.TLabel" if i < len(r.fixes) + (1 if r.stop else 0) else "Small.TLabel"
            lab = self.ttk.Label(self.f_fixes, text=text, style=style, wraplength=800, justify="left")
            lab.grid(row=i, column=0, sticky="ew", pady=1)
            self._fix_labels.append(lab)
        for row in r.rows:
            iid = self.tree.insert("", "end", text=row.title, values=(row.pages, row.verdict, row.reasons),
                                   tags=(verdict_tag(row.verdict),))
            self._rows[str(iid)] = row

    def _detail(self, text: str) -> None:
        self.t_detail.configure(state="normal")
        self.t_detail.delete("1.0", "end")
        if text:
            self.t_detail.insert("1.0", text)
        self.t_detail.configure(state="disabled")

    # ------------------------------------------------------------------------------------------ actions
    def on_choose_pdf(self) -> None:
        p = self.fd.askopenfilename(parent=self.root, title="Choose the bundle PDF",
                                    filetypes=[("PDF files", "*.pdf")])
        if p:
            self.m.choose_input(p)
        self.render()

    def on_choose_folder(self) -> None:
        p = self.fd.askdirectory(parent=self.root, title="Choose the student's folder", mustexist=True)
        if p:
            self.m.choose_input(p)
        self.render()

    def on_program(self) -> None:
        self.m.set_program(self.v_program.get())
        self.render()

    def on_toggle_options(self) -> None:
        self._options_open = not self._options_open
        if self._options_open:
            self.f_options.grid()
            self.b_options.configure(text="▾ Options")
        else:
            self.f_options.grid_remove()
            self.b_options.configure(text="▸ Options")

    def on_date_mode(self) -> None:
        self.m.set_date_mode(self.v_date_mode.get(), self.v_fixed.get())
        self.render()

    def on_change_root(self) -> None:
        p = self.fd.askdirectory(parent=self.root, title="Report folder", mustexist=False)
        if p:
            self.m.change_report_root(os.path.normpath(p))
        self.render()

    def on_change_lock(self) -> None:
        cur = self.m.settings.gpu_lock_path or ""
        kw = dict(parent=self.root, title="The lock file to wait for", initialdir=os.path.dirname(cur) or None,
                  initialfile=os.path.basename(cur) or None)
        try:
            p = self.fd.asksaveasfilename(confirmoverwrite=False, **kw)
        except Exception:
            p = self.fd.asksaveasfilename(**kw)
        if p:
            self.m.change_lock_path(os.path.normpath(p))
        self.render()

    def on_lock_off(self) -> None:
        self.m.change_lock_path(None)
        self.render()

    def on_check(self) -> None:
        self.m.set_date_mode(self.v_date_mode.get(), self.v_fixed.get())
        r = self.m.check()
        if r == "confirm" and self.m.pending_confirm is not None:
            msg = self.m.pending_confirm
            if self.mb.askyesno(msg.title, msg.text + "\n\nContinue anyway?", parent=self.root):
                self.m.check(confirmed=True)
            else:
                self.m.pending_confirm = None
        self.render()

    def on_cancel(self) -> None:
        self.m.cancel()
        self.render()

    def on_select(self) -> None:
        sel = self.tree.selection()
        row = self._rows.get(str(sel[0])) if sel else None
        self._detail(row.full if row else "")

    def on_copy_code(self) -> None:
        code = self.m.result_code_text()
        if code:
            self.root.clipboard_clear()
            self.root.clipboard_append(code)

    def on_copy_summary(self) -> None:
        text = self.m.summary_text()
        if text:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
        self.render()

    def on_open_excel(self) -> None:
        self.m.open_excel()
        self.render()

    def on_open_folder(self) -> None:
        self.m.open_report_folder()
        self.render()

    def on_save_pdf(self) -> None:
        folder, name = self.m.save_pdf_default()
        p = self.fd.asksaveasfilename(parent=self.root, title="Save the summary as PDF", initialdir=folder or None,
                                      initialfile=name, defaultextension=".pdf", filetypes=[("PDF files", "*.pdf")])
        if p:
            self.m.save_pdf(os.path.normpath(p))
        self.render()

    def on_open_recent(self) -> None:
        sel = self.l_recent.curselection()
        if sel:
            self.m.open_recent(int(sel[0]))
            self.render()

    def on_selftest(self) -> None:
        self.m.selftest()
        self.render()

    def on_close(self) -> None:
        if self.m.request_close() == "confirm":
            if not self.mb.askyesno("A check is running", "Cancel it and close the window?", parent=self.root):
                return
        if self.m.close_now() == "close":
            self.root.destroy()
        else:
            self.render()

    # ------------------------------------------------------------------------------------------ loop
    def tick(self) -> None:
        """Every 100 ms on Tk's thread: poll the check, redraw on change. Never stops rescheduling itself, so a
        drawing error cannot leave a running check unwatched."""
        try:
            changed = self.m.poll()
        except Exception:
            changed = True
        if changed:
            try:
                self.render()
            except Exception:
                pass
        if self.m.auto_done:
            self.root.destroy()
            return
        self.root.after(self.POLL_MS, self.tick)


# ==================================================================================================== main
def _dpi_aware() -> None:
    try:
        import ctypes
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(1)
    except Exception:
        pass


@dataclass
class WindowArgs:
    """What the window needs from the command line (entry.py's Options carries the same names)."""
    role: str = "window"                      # "window" or "auto"
    path: Optional[str] = None                # the dropped PDF or folder; --auto: the input
    program: Optional[str] = None
    today: Optional[str] = None
    report_root: Optional[str] = None
    auto_result: Optional[str] = None
    trace_dir: Optional[str] = None
    data_dir: Optional[str] = None
    lock_extra: List[str] = field(default_factory=list)


def parse_args(argv: Sequence[str]) -> WindowArgs:
    """DEV only (entry.py parses the real command line): [PATH] or --auto INPUT --program P [--today D]
    [--report-root DIR] [--auto-result FILE] [--gpu-lock-extra PATH] [--trace-opens DIR] [--data-dir DIR]."""
    ap = argparse.ArgumentParser(prog="window", add_help=False)
    ap.add_argument("input", nargs="?", default=None)
    ap.add_argument("--auto", default=None)
    ap.add_argument("--program", default=None)
    ap.add_argument("--today", default=None)
    ap.add_argument("--report-root", default=None)
    ap.add_argument("--auto-result", default=None)
    ap.add_argument("--gpu-lock-extra", action="append", default=[])
    ap.add_argument("--trace-opens", default=None)
    ap.add_argument("--data-dir", default=None)
    a, _ = ap.parse_known_args(list(argv))
    return WindowArgs(role="auto" if a.auto else "window", path=a.auto or a.input, program=a.program,
                      today=a.today, report_root=a.report_root, auto_result=a.auto_result, trace_dir=a.trace_opens,
                      data_dir=a.data_dir, lock_extra=list(a.gpu_lock_extra))


def window_args(opts: Any) -> WindowArgs:
    """entry.Options (Task C: role, path, program, today, report_root, auto_result, trace_dir, data_dir,
    lock_extra), a list of strings (DEV), or None (sys.argv)."""
    if opts is None:
        return parse_args(sys.argv[1:])
    if isinstance(opts, (list, tuple)):
        return parse_args([str(x) for x in opts])
    return WindowArgs(role=str(getattr(opts, "role", "window") or "window"), path=getattr(opts, "path", None),
                      program=getattr(opts, "program", None), today=getattr(opts, "today", None),
                      report_root=getattr(opts, "report_root", None), auto_result=getattr(opts, "auto_result", None),
                      trace_dir=getattr(opts, "trace_dir", None), data_dir=getattr(opts, "data_dir", None),
                      lock_extra=list(getattr(opts, "lock_extra", None) or []))


def _data_dir(args: WindowArgs) -> Path:
    if args.data_dir:
        return Path(os.path.abspath(args.data_dir))
    try:
        from . import boot                             # Task C: HDC_DATA_DIR (set from --data-dir) or LOCALAPPDATA
        return Path(boot.data_dir())
    except ImportError:
        return U.data_dir()


def build_model(args: WindowArgs, deps: Optional[Deps] = None) -> WindowModel:
    """The model of one window: DATA, the real helpers, settings.json (first start: the default lock setting),
    recent.json, the --auto spec and the dropped input."""
    data = _data_dir(args)
    data.mkdir(parents=True, exist_ok=True)
    deps = deps or real_deps(data)
    auto = None
    if args.role == "auto":
        auto = AutoSpec(input=str(args.path or ""), program=args.program or "KLP", today=args.today,
                        report_root=args.report_root, result_path=args.auto_result)
    settings = S.load(data, deps.default_lock)
    model = WindowModel(data, deps, settings=settings, recent_items=R.load(data), auto=auto,
                        lock_extra=args.lock_extra, trace_dir=args.trace_dir)
    if args.path and auto is None:
        model.choose_input(args.path)
    return model


def main(opts: Any = None) -> int:
    """The window roles of entry.py (`main(opts)`, role "window" or "auto"). entry.py has already armed the trace,
    set DATA, checked the platform, preloaded the runtime and taken the single-instance mutex; the window runs
    the MANIFEST size check, the housekeeping, the window guard and the card probe (model.startup).
    --auto checks one input with no clicks, writes --auto-result and closes: 0 when the check finished."""
    args = window_args(opts)
    model = build_model(args)
    _dpi_aware()
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    root = tk.Tk()
    win = TkWindow(root, model, tk=tk, ttk=ttk, filedialog=filedialog, messagebox=messagebox)
    model.startup()
    win.render()
    if model.auto_done:
        root.destroy()
        return model.exit_status()
    root.after(TkWindow.POLL_MS, win.tick)
    root.mainloop()
    return model.exit_status()
