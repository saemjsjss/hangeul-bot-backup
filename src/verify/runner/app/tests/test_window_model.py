"""Task B: app/window.py, headless (APP_SPEC rev 1 section 3; rev 2 R2.1.4, R2.2, R2.3.2, R2.3.3).

The WindowModel is driven with fake card probes, fake check handles and made-up event streams; the Tk binding is
built on fake tk / ttk modules (no window is ever opened); one test runs Task C's real CheckController on a fake
worker process (no process is started). Made-up names, numbers and files only; no card, no torch, no real case.

Scratch files: C:\\Hangeul\\DOCCHECK\\lab\\private-runner\\selftest\\app\\B\\window-<pid>-<n> (removed).
Run: HDC_TEST_NO_CARD=1 <runner venv python> -I -B -m unittest src.verify.runner.app.tests.test_window_model
"""
from __future__ import annotations

import datetime as dt
import io
import json
import os
import shutil
import sys
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from src.verify.runner.app import messages as M
from src.verify.runner.app import recent as R
from src.verify.runner.app import settingsfile as S
from src.verify.runner.app import window as W

SCRATCH = Path(os.environ.get("HDC_TEST_SCRATCH", r"C:\Hangeul\DOCCHECK\lab\private-runner\selftest\app\B"))
WHEN = dt.datetime(2026, 10, 28, 14, 12)
LOCK = r"D:\Made Up\bot data\auto_verify.lock"
OTHER_LOCK = r"D:\Made Up\other\tool.lock"
CARD = "NVIDIA GeForce RTX 0000"

LINES = [
    ["FAIL", "B-0000000000000000 | KLP | bundle | 9 pages | RESULT: FAIL"],
    ["FAIL", "FAIL — 1 FAIL, 1 FLAG, 1 MISSING, 1 ORDER"],
    ["", "RESULT CODE: 3F2A-91C0 (check date 2026-10-28)"],
    ["", "READING: GPU pre-read 9 pages, CPU light engine 0, text layer 0"],
    ["ORDER", "ORDER: fix pages 4-5 — the HSC/SSC pages are swapped"],
    ["PASS", "PASSPORT (p1): PASS"],
    ["FAIL", "ACADEMIC HSC (p6-7): FAIL — no e-Apostille; made-up reason"],
    ["NOT JUDGED", "ACADEMIC SSC (p4-5): NOT JUDGED — fix the page order first"],
    ["PASS", "BANK STATEMENT (BANK STATEMENT (2).pdf p1-2): PASS"],
    ["MISSING", "PHOTO MISSING"],
    ["FLAG", "FATHER NID MISSING unless the death certificate on pages 8-9 is the father's — check by eye"],
    ["FLAG", "NAMES: FLAG — a made-up name difference"],
    ["FLAG", "NEW CHECKS (owner to confirm): tin.web_address on TIN CERTIFICATE (p9)"],
    ["", "Not checked (not a PDF or image): NOTES.docx"],
    ["NOTE", "NOTE: a made-up note"],
    ["", "Report: D:\\made up\\report.xlsx | JSON: D:\\made up\\run.json"],
]


def _rmtree(p: Path) -> None:
    for _ in range(6):
        shutil.rmtree(p, ignore_errors=True)
        if not p.exists():
            return
        time.sleep(0.3)


def card(**kw):
    base = dict(nvml=True, error="", count=1, index=0, name=CARD, driver="000.00", cuda_driver=12090, cc=(12, 0),
                total_mb=8151, free_mb=6120)
    base.update(kw)
    return SimpleNamespace(**base)


def done_event(**kw):
    ev = {"v": 1, "ev": "done", "exit": 0, "overall": "FAIL", "headline": LINES[1][1], "code": "3F2A-91C0",
          "out": "<case>", "lines": LINES, "peak_mb": 1000,
          "gpu": {"card": CARD, "peak_alloc_gb": 2.4, "peak_reserved_gb": 2.8, "cpu_calls": 0, "waits": 0,
                  "waited_s": 0.0}}
    ev.update(kw)
    return ev


def outcome(kind, code=None, exit=None, values=None, text="", done=None):
    return SimpleNamespace(kind=kind, code=code, exit=exit, values=values or {}, text=text, done=done,
                           forced=False, stop=False)


class _Handle:
    def __init__(self, job):
        self.job = job
        self.queue = []
        self.end = None
        self.cancelled = 0

    def push(self, *events):
        self.queue.extend(events)

    def drain(self):
        out, self.queue = self.queue, []
        return out

    def cancel(self):
        self.cancelled += 1


class FakeHandle(_Handle):
    """Like Task C's controller: `finished()` gives the Outcome once the worker has ended."""

    def finished(self):
        return self.end


class ExitHandle(_Handle):
    """A simpler handle that only knows the exit code."""

    def exit_code(self):
        return self.end


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = SCRATCH / f"window-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self.data = self.dir / "data"
        self.reports = self.dir / "reports"
        (self.dir / "input").mkdir(parents=True)
        self.pdf = self.dir / "input" / "SAMPLE BUNDLE.pdf"
        self.pdf.write_bytes(b"%PDF-1.4 made-up")
        self.info = card()
        self.problems = []
        self.free_ram = 16000
        self.handles = []
        self.selftests = []
        self.opened = []
        self.allowed = []
        self.armed = []
        self.hk = []
        self.missing = None
        self.handle_cls = FakeHandle
        self.open_error = None

    def tearDown(self):
        _rmtree(self.dir)

    def _start(self, job):
        h = self.handle_cls(job)
        self.handles.append(h)
        return h

    def _selftest(self, root):
        h = SimpleNamespace(code=None)
        h.finished = lambda: h.code
        h.cancel = lambda: None
        self.selftests.append(h)
        return h

    def _open(self, p):
        if self.open_error and p.endswith(self.open_error):
            raise OSError("no program")
        self.opened.append(p)

    def deps(self, **kw):
        d = W.Deps(probe=lambda: self.info, problems=lambda info: list(self.problems), need_free_mb=4096,
                   default_lock=lambda: LOCK, lock_kind=lambda p: "bot" if p == os.path.normpath(LOCK) else "other",
                   start_check=self._start, start_selftest=self._selftest, open_path=self._open,
                   free_ram_mb=lambda: self.free_ram, allow_file=self.allowed.append, arm_roots=self.armed.append,
                   housekeeping=lambda data, days: self.hk.append(days) or {"jobs": 0},
                   integrity=lambda: self.missing, default_report_root=lambda: self.reports, now=lambda: WHEN,
                   versions={"app_version": "1.0.0", "build_id": "20261029.1-abc1234",
                             "base_rules_version": "2026-10-21.3", "runner_rules_version": "2026-10-28.r2.6"})
        for k, v in kw.items():
            setattr(d, k, v)
        return d

    def model(self, auto=None, lock_extra=(), **kw):
        m = W.WindowModel(self.data, self.deps(**kw), auto=auto, lock_extra=lock_extra)
        m.startup()
        return m

    def started(self, m=None):
        m = m or self.model()
        m.choose_input(str(self.pdf))
        self.assertEqual(m.check(), "started")
        return m, self.handles[-1]

    def run_events(self, m, h, *events, end=None):
        h.push(*events)
        if end is not None:
            h.end = end
        m.poll()


# ================================================================================================ card probe
class CardProbe(Base):
    def test_hard_problems_disable_check_but_not_selftest(self):
        cases = [("NO_NVIDIA_CARD", {}, "No NVIDIA graphics card"),
                 ("DRIVER_TOO_OLD", {"driver": "000.00"}, "Graphics driver too old"),
                 ("CARD_NOT_SUPPORTED", {"name": CARD, "which": "new"}, "Graphics card not supported"),
                 ("CARD_TOO_SMALL", {"name": CARD, "total": "4.0"}, "Graphics card memory too small")]
        for code, vals, title in cases:
            self.problems = [SimpleNamespace(code=code, values=vals)]
            m = self.model()
            m.choose_input(str(self.pdf))
            v = m.view()
            self.assertEqual(v.banner.title, title, code)
            self.assertEqual(v.banner.kind, M.ERROR)
            self.assertFalse(v.check_enabled, code)
            self.assertTrue(v.selftest_enabled, code)
            self.assertEqual(m.check(), "refused")
            self.assertEqual(self.handles, [])
        self.assertIn("(NVIDIA GeForce RTX 0000) has 4.0 GB", m.view().banner.text)

    def test_first_problem_is_shown(self):
        self.problems = [SimpleNamespace(code="DRIVER_TOO_OLD", values={"driver": "000.00"}),
                         SimpleNamespace(code="CARD_TOO_SMALL", values={"name": CARD, "total": "4.0"})]
        self.assertEqual(self.model().view().banner.code, "DRIVER_TOO_OLD")

    def test_no_probe_answer_is_no_card(self):
        self.info = None
        m = self.model()
        self.assertEqual(m.view().banner.code, M.NO_NVIDIA_CARD)
        self.assertEqual(m.view().footer_card, "Graphics card: none found")

        def boom():
            raise OSError("made-up")
        m2 = self.model(probe=boom)
        self.assertEqual(m2.view().banner.code, M.NO_NVIDIA_CARD)

    def test_real_gpuinfo_without_a_card(self):
        """E's gpuinfo under HDC_TEST_NO_CARD gives NO_NVIDIA_CARD without loading nvml.dll."""
        try:
            from src.verify.runner.app import gpuinfo
        except ImportError:
            self.skipTest("gpuinfo.py not there yet")
        with mock.patch.dict(os.environ, {"HDC_TEST_NO_CARD": "1"}):
            m = self.model(probe=gpuinfo.probe, problems=lambda info: gpuinfo.problems(info, W.DEFAULT_ARCH_FLAGS))
        self.assertEqual(m.view().banner.code, M.NO_NVIDIA_CARD)
        self.assertFalse(m.view().check_enabled)
        real = gpuinfo.CardInfo(nvml=True, count=1, name=CARD, driver="000.00", cuda_driver=12000, cc=(6, 1),
                                total_mb=4096, free_mb=4000)
        probs = gpuinfo.problems(real, W.DEFAULT_ARCH_FLAGS)
        self.problems, self.info = probs, real
        m = self.model()
        self.assertEqual(m.view().banner.code, "DRIVER_TOO_OLD")
        self.problems = probs[1:]
        self.assertIn("is too old for the checker's reading engine", self.model().view().banner.text)
        self.problems = probs[2:]
        self.assertIn("has 4.0 GB of memory", self.model().view().banner.text)

    def test_low_free_memory_is_only_a_note(self):
        self.info = card(free_mb=3000)
        m = self.model()
        m.choose_input(str(self.pdf))
        v = m.view()
        self.assertEqual(v.banner.kind, M.NOTE)
        self.assertIn("2.9 GB", v.banner.text)
        self.assertTrue(v.check_enabled)
        self.problems = [SimpleNamespace(code="LOW_CARD_MEMORY", values={"free": "2.9"})]
        self.assertEqual(self.model().view().banner.kind, M.NOTE)

    def test_footer_card_line_and_versions(self):
        v = self.model().view()
        self.assertEqual(v.footer_card, "Graphics card: NVIDIA GeForce RTX 0000 (8.0 GB) · driver 000.00")
        self.assertEqual(v.footer_app, "App 1.0.0 (GPU, build 20261029.1-abc1234) · Rules: bot 2026-10-21.3 + "
                                       "runner 2026-10-28.r2.6")
        self.assertIsNone(v.banner)

    def test_probe_before_every_check(self):
        m = self.model()
        m.choose_input(str(self.pdf))
        self.problems = [SimpleNamespace(code="NO_NVIDIA_CARD", values={})]
        self.assertEqual(m.check(), "refused")
        self.assertEqual(m.view().banner.code, M.NO_NVIDIA_CARD)


# ================================================================================================ settings
class LockSetting(Base):
    def test_first_start_stores_the_default(self):
        m = self.model()
        stored = json.loads((self.data / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["gpu_lock_path"], os.path.normpath(LOCK))
        self.assertEqual(stored["seconds_per_page_gpu"], 3.0)
        text, is_bot, off = m.lock_row()
        self.assertTrue(is_bot)
        self.assertFalse(off)
        self.assertEqual(text, os.path.normpath(LOCK) + " (the Telegram bot's document check)")
        v = m.view()
        self.assertEqual(v.lock_label, "Graphics card: wait while this file exists")
        self.assertTrue(v.lock_is_default)
        self.assertEqual(self.hk, [30])
        self.assertEqual(self.armed[-1], [str(self.data), str(self.reports)])

    def test_change_and_off(self):
        m = self.model()
        self.assertEqual(m.change_lock_path(OTHER_LOCK), "ok")
        self.assertEqual(m.lock_row(), (os.path.normpath(OTHER_LOCK), False, False))
        self.assertEqual(m.change_lock_path("relative.lock"), "bad")
        self.assertEqual(m.change_lock_path(None), "ok")
        self.assertEqual(m.lock_row(), (W.LOCK_OFF_TEXT, False, True))
        self.assertIsNone(json.loads((self.data / "settings.json").read_text(encoding="utf-8"))["gpu_lock_path"])
        m2 = self.model()                                   # a later start keeps Off
        self.assertTrue(m2.view().lock_off)

    def test_job_lock_paths_and_extra_never_stored(self):
        m = self.model(lock_extra=[OTHER_LOCK, OTHER_LOCK.upper(), "relative.lock"])
        self.assertEqual(m.lock_paths(), [os.path.normpath(LOCK), os.path.normpath(OTHER_LOCK)])
        m.change_lock_path(None)
        self.assertEqual(m.lock_paths(), [os.path.normpath(OTHER_LOCK)])
        stored = json.loads((self.data / "settings.json").read_text(encoding="utf-8"))
        self.assertNotIn("tool.lock", json.dumps(stored))

    def test_program_and_report_root_remembered(self):
        m = self.model()
        self.assertEqual(m.set_program("Bachelor's"), "ok")
        self.assertEqual(m.view().program_label, "Bachelor's")
        self.assertEqual(m.set_program("PhD"), "bad")
        new_root = str(self.dir / "other reports")
        self.assertEqual(m.change_report_root(new_root), "ok")
        self.assertEqual(self.armed[-1], [str(self.data), new_root])
        stored = json.loads((self.data / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["last_program"], "BACHELOR")
        self.assertEqual(stored["report_root"], new_root)
        self.assertEqual(self.model().view().report_root_text, new_root)


# ================================================================================================ pre-checks
class PreChecks(Base):
    def test_refusals(self):
        m = self.model()
        self.assertEqual(m.check(), "no_input")
        self.assertEqual(m.view().banner.code, M.NO_INPUT)
        m.choose_input(str(self.dir / "absent.pdf"))
        self.assertEqual(m.check(), "refused")
        self.assertEqual(m.view().banner.code, M.NOT_PDF_OR_FOLDER)
        (self.dir / "empty").mkdir()
        m.choose_input(str(self.dir / "empty"))
        m.check()
        self.assertEqual(m.view().banner.code, M.EMPTY_FOLDER)
        m.choose_input(str(self.dir / "input"))
        m.change_report_root(str(self.dir / "input" / "reports"))
        m.check()
        self.assertEqual(m.view().banner.code, M.REPORT_INSIDE_INPUT)
        self.assertFalse((self.dir / "input" / "reports").exists())
        m.change_report_root("D:\\" + "r" * 160)
        m.check()
        self.assertEqual(m.view().banner.code, M.PATH_TOO_LONG)
        m.change_report_root(str(self.reports))
        m.set_date_mode("fixed", "2026-13-01")
        m.check()
        self.assertEqual(m.view().banner.code, M.BAD_DATE)
        m.set_date_mode("fixed", "28.10.2026")
        m.check()
        self.assertEqual(m.view().banner.code, M.BAD_DATE)
        m.set_date_mode("today")
        with mock.patch.object(W.U, "check_free_disk", return_value=M.DISK_FULL):
            m.check()
        self.assertEqual(m.view().banner.code, M.DISK_FULL)
        self.assertEqual(self.handles, [])
        self.assertFalse(self.reports.exists() and any(self.reports.iterdir()))

    def test_low_ram_asks_first(self):
        self.free_ram = 3000
        m = self.model()
        m.choose_input(str(self.pdf))
        self.assertEqual(m.check(), "confirm")
        self.assertEqual(m.pending_confirm.code, M.LOW_RAM_WARNING)
        self.assertEqual(self.handles, [])
        self.assertEqual(m.check(confirmed=True), "started")

    def test_choose_input_shows_the_base_name(self):
        m = self.model()
        self.assertEqual(m.view().input_text, "(nothing chosen)")
        self.assertFalse(m.view().check_enabled)
        m.choose_input(str(self.pdf))
        self.assertEqual(m.view().input_text, "SAMPLE BUNDLE.pdf")
        self.assertTrue(m.view().check_enabled)


# ================================================================================================ a check
class CheckStream(Base):
    def test_job_json_contract(self):
        m, h = self.started(self.model(lock_extra=[OTHER_LOCK]))
        job = h.job
        self.assertEqual(job["schema"], "hdc-job/1")
        self.assertRegex(job["job"], r"^[0-9a-f]{32}$")
        self.assertEqual(job["input"], str(self.pdf))
        self.assertEqual(job["program"], "KLP")
        self.assertIsNone(job["today"])
        self.assertEqual(job["cache_root"], str(self.data / "cache"))
        self.assertEqual(job["gpu"], {"lock_paths": [os.path.normpath(LOCK), os.path.normpath(OTHER_LOCK)],
                                      "index": 0, "cap_gb": 3.0, "need_free_mb": 4096})
        out = Path(job["out"])
        self.assertEqual(out.parent, self.reports)
        self.assertEqual(out.name, "SAMPLE BUNDLE KLP 2026-10-28 1412")
        self.assertTrue((out / (".hdc-run-" + job["job"])).is_file())
        try:
            from src.verify.runner.app import protocol
        except ImportError:
            return
        protocol.check_job(dict(job))                      # C's validator accepts the window's job

    def test_fixed_date_goes_into_the_job(self):
        m = self.model()
        m.set_date_mode("fixed", "2026-10-28")
        m, h = self.started(m)
        self.assertEqual(h.job["today"], "2026-10-28")

    def test_progress_wait_and_done(self):
        m, h = self.started()
        v = m.view()
        self.assertEqual(v.state, W.RUNNING)
        self.assertFalse(v.check_enabled)
        self.assertTrue(v.cancel_enabled)
        self.assertFalse(v.edit_enabled)
        self.assertEqual(v.progress_text, M.PROGRESS_STARTING)
        self.run_events(m, h, {"ev": "hello", "app": "1.0.0", "pid": 1},
                        {"ev": "stage", "key": "card", "state": "start"},
                        {"ev": "card", "state": "found", "name": "NVIDIA GeForce RTX 1111", "cc": "12.0",
                         "driver": "111.11", "total_mb": 12288, "free_mb": 9000})
        self.assertEqual(m.view().progress_text, M.PROGRESS_CARD)
        self.assertEqual(m.view().footer_card, "Graphics card: NVIDIA GeForce RTX 1111 (12.0 GB) · driver 111.11")
        self.run_events(m, h, {"ev": "card", "state": "ready", "torch": "2.11.0+cu128", "cuda": "12.8",
                               "seconds": 14.2})
        self.assertEqual(m.view().progress_text, M.PROGRESS_CARD_READY)
        self.run_events(m, h, {"ev": "stage", "key": "firstread", "state": "start"},
                        {"ev": "page", "done": 1, "of": 4, "seconds": 2.0, "where": "card"})
        v = m.view()
        self.assertEqual(v.progress_text, "Reading page 2 of 4 on the graphics card — about 9 s left")
        self.assertEqual(v.progress_mode, "determinate")
        self.assertEqual(v.progress_value, 25.0)
        self.run_events(m, h, {"ev": "page", "done": 2, "of": 4, "seconds": 4.0, "where": "card"})
        self.assertEqual(m.view().progress_text, "Reading page 3 of 4 on the graphics card — about 6 s left")
        # the bot's lock appears: waiting
        self.run_events(m, h, {"ev": "gpu_wait", "state": "start", "why": "lock", "lock": "bot", "where": "page",
                               "reserved_mb": 180})
        v = m.view()
        self.assertEqual(v.state, W.WAITING)
        self.assertTrue(v.progress_paused)
        self.assertTrue(v.cancel_enabled)
        self.assertTrue(v.banner.cancel)
        self.assertEqual(v.banner.kind, M.WAIT)
        self.assertIn("the Telegram bot is checking documents (waited 0 min)", v.banner.text)
        self.run_events(m, h, {"ev": "gpu_wait", "state": "tick", "why": "lock", "lock": "bot", "waited_s": 130.0})
        self.assertIn("(waited 2 min)", m.view().banner.text)
        self.run_events(m, h, {"ev": "gpu_wait", "state": "end", "why": "lock", "waited_s": 180.5})
        v = m.view()
        self.assertEqual(v.state, W.RUNNING)
        self.assertIsNone(v.banner)
        self.assertEqual(m.waits, 1)
        self.run_events(m, h, {"ev": "page", "done": 4, "of": 4, "seconds": 3.0, "where": "card"},
                        {"ev": "stage", "key": "firstread", "state": "end", "seconds": 9.0},
                        {"ev": "stage", "key": "names", "state": "start"},
                        {"ev": "budget", "where": "birth.qr", "page": 7})
        self.assertEqual(m.view().progress_text, "Checking documents… (comparing the names)")
        self.assertEqual(m.view().progress_mode, "indeterminate")
        self.assertEqual(m.view().progress_note, "")
        self.run_events(m, h, done_event(gpu={"card": CARD, "peak_alloc_gb": 2.4, "peak_reserved_gb": 2.8,
                                              "cpu_calls": 2, "waits": 1, "waited_s": 180.5}))
        v = m.view()
        self.assertEqual(v.state, W.FINISHING)
        self.assertFalse(v.check_enabled)
        self.assertEqual(v.result.code, "3F2A-91C0")
        self.assertEqual(v.result.check_date, "2026-10-28")
        self.run_events(m, h, end=outcome("done", exit=0, done=done_event()))
        v = m.view()
        self.assertEqual(v.state, W.IDLE)
        self.assertTrue(v.check_enabled)
        self.assertTrue(v.result_buttons)
        r = v.result
        self.assertEqual(r.overall, "FAIL")
        self.assertEqual(r.headline, LINES[1][1])
        self.assertEqual([row.title for row in r.rows],
                         ["PASSPORT", "ACADEMIC HSC", "ACADEMIC SSC", "BANK STATEMENT", "PHOTO MISSING",
                          LINES[10][1], "NEW CHECKS (owner to confirm)", "Not checked (not a PDF or image)", "NAMES"])
        self.assertEqual(len(r.fixes), 1)
        self.assertIn("Fix pages 4-5: the HSC/SSC pages are swapped.", r.fixes[0])
        self.assertIn("NOT JUDGED until you rescan", r.fixes[0])
        self.assertIn("Waited 3 min for the graphics card.", r.notes)
        self.assertTrue(any("2 large reading step(s) ran on the processor" in n for n in r.notes))
        out = Path(h.job["out"])
        self.assertTrue(out.is_dir())
        self.assertFalse((out / (".hdc-run-" + h.job["job"])).exists())
        rec = R.load(self.data)
        self.assertEqual(len(rec), 1)
        self.assertEqual(rec[0].input, "SAMPLE BUNDLE.pdf")
        self.assertEqual(rec[0].overall, "FAIL")
        self.assertEqual(rec[0].folder, str(out))
        self.assertEqual(v.recent_lines, ["28 Oct 14:12  SAMPLE BUNDLE.pdf  KLP  FAIL  3F2A-91C0"])
        stored = S.load(self.data)
        self.assertAlmostEqual(stored.seconds_per_page_gpu, 3.0)          # (2 + 4 + 3) / 3 pages on the card
        self.assertEqual(stored.pages_measured_gpu, 3)
        self.assertEqual(m.check(), "started")                           # ready for the next check

    def test_memory_and_other_lock_waits(self):
        m, h = self.started()
        self.run_events(m, h, {"ev": "hello"}, {"ev": "gpu_wait", "state": "start", "why": "memory",
                                                "free_mb": 2900, "need_mb": 4096})
        self.assertIn("2.8 GB free, 4.0 GB needed (waited 0 min, at most 15)", m.view().banner.text)
        self.run_events(m, h, {"ev": "gpu_wait", "state": "tick", "why": "memory", "free_mb": 3500,
                               "waited_s": 65})
        self.assertIn("3.4 GB free, 4.0 GB needed (waited 1 min", m.view().banner.text)
        self.run_events(m, h, {"ev": "gpu_wait", "state": "end", "why": "memory", "waited_s": 70})
        self.run_events(m, h, {"ev": "gpu_wait", "state": "tick", "why": "lock", "lock": "other", "waited_s": 4})
        self.assertEqual(m.view().state, W.WAITING)                     # a lost start: the tick opens the wait
        self.assertIn("another program's lock file", m.view().banner.text)

    def test_cancel_during_a_wait(self):
        m, h = self.started()
        self.run_events(m, h, {"ev": "hello"}, {"ev": "gpu_wait", "state": "start", "why": "lock", "lock": "bot"})
        self.assertEqual(m.cancel(), "cancelling")
        self.assertEqual(h.cancelled, 1)
        v = m.view()
        self.assertEqual(v.state, W.CANCELLING)
        self.assertFalse(v.cancel_enabled)
        self.assertEqual(v.progress_text, M.PROGRESS_CANCELLING)
        self.assertFalse(v.banner.cancel)
        self.assertEqual(m.cancel(), "not_running")
        self.run_events(m, h, {"ev": "cancelled"}, end=outcome("cancelled", "CANCELLED", 4))
        v = m.view()
        self.assertEqual(v.state, W.IDLE)
        self.assertEqual(v.banner.code, M.CANCELLED)
        self.assertEqual(v.banner.text, "Check cancelled. Nothing was saved.")
        self.assertFalse(Path(h.job["out"]).exists())                   # the folder this check made is gone
        self.assertEqual(R.load(self.data), [])

    def test_forced_cancel_without_event(self):
        self.handle_cls = ExitHandle
        m, h = self.started()
        self.run_events(m, h, {"ev": "hello"})
        m.cancel()
        self.run_events(m, h, end=1)                                    # TerminateProcess: no cancelled event
        self.assertEqual(m.view().banner.code, M.CANCELLED)

    def test_card_error_disables_check(self):
        m, h = self.started()
        self.run_events(m, h, {"ev": "hello"}, {"ev": "stage", "key": "card", "state": "start"},
                        {"ev": "error", "code": "DRIVER_TOO_OLD", "values": {"driver": "000.00"}, "text": "x"},
                        end=outcome("error", "DRIVER_TOO_OLD", 6, {"driver": "000.00", "stage": "card"}, "x"))
        v = m.view()
        self.assertEqual(v.banner.code, "DRIVER_TOO_OLD")
        self.assertIn("(version 000.00)", v.banner.text)
        self.assertFalse(v.check_enabled)
        self.assertTrue(v.selftest_enabled)
        self.assertFalse(Path(h.job["out"]).exists())                   # nothing but the marker: removed

    def test_worker_card_refusal_sticks_until_a_good_selftest(self):
        m, h = self.started()
        self.run_events(m, h, {"ev": "hello"},
                        {"ev": "error", "code": "CARD_NOT_SUPPORTED", "values": {"name": CARD, "which": "new"}},
                        end=outcome("error", "CARD_NOT_SUPPORTED", 6, {"name": CARD, "which": "new"}))
        self.assertEqual(m.check(), "refused")                          # NVML is fine, torch said no: no retry
        self.assertEqual(len(self.handles), 1)
        self.assertEqual(m.view().banner.code, "CARD_NOT_SUPPORTED")
        m.selftest()
        self.selftests[-1].code = 1
        m.poll()
        self.assertFalse(m.view().check_enabled)
        m.selftest()
        self.selftests[-1].code = 0
        m.poll()
        self.assertTrue(m.view().check_enabled)

    def test_card_failed_keeps_check_enabled_and_names_the_step(self):
        m, h = self.started()
        self.run_events(m, h, {"ev": "hello"}, {"ev": "stage", "key": "firstread", "state": "start"},
                        end=outcome("error", "CARD_FAILED", 6, {"stage": "firstread"}))
        v = m.view()
        self.assertEqual(v.banner.code, M.CARD_FAILED)
        self.assertIn("(step 'reading the pages')", v.banner.text)
        self.assertTrue(v.check_enabled)

    def test_crash_detail_is_scrubbed_and_folder_kept(self):
        m, h = self.started()
        out = Path(h.job["out"])
        (out / "log.txt").write_text("made-up", encoding="ascii")
        self.run_events(m, h, {"ev": "hello"}, {"ev": "stage", "key": "bank", "state": "start"},
                        {"ev": "error", "code": "CRASH", "text": r"ValueError at C:\Some Where\x.py"},
                        end=outcome("error", "CRASH", 1, {"stage": "bank"}, r"ValueError at C:\Some Where\x.py"))
        v = m.view()
        self.assertEqual(v.banner.code, M.CRASH)
        self.assertIn("step 'bank documents'", v.banner.text)
        self.assertNotIn("C:\\", v.banner.detail)
        self.assertIn("<path>", v.banner.detail)
        self.assertTrue(out.is_dir())
        self.assertFalse((out / (".hdc-run-" + h.job["job"])).exists())

    def test_outcome_without_events(self):
        m, h = self.started()
        self.run_events(m, h, end=outcome("error", "STARTUP_FAILED", None))
        self.assertEqual(m.view().banner.code, M.STARTUP_FAILED)
        self.assertEqual(m.view().state, W.IDLE)

    def test_bare_exit_codes(self):
        self.handle_cls = ExitHandle
        for code, hello, want in ((0xC000001D, False, M.CPU_UNSUPPORTED), (1, False, M.STARTUP_FAILED),
                                  (6, True, M.CARD_FAILED), (0xC0000005, True, M.CRASH)):
            m, h = self.started()
            self.run_events(m, h, *([{"ev": "hello"}] if hello else []), end=code)
            self.assertEqual(m.view().banner.code, want, code)
            self.assertEqual(m.view().state, W.IDLE)

    def test_saved_page_texts_note(self):
        m, h = self.started()
        self.run_events(m, h, {"ev": "hello"}, {"ev": "stage", "key": "firstread", "state": "start"},
                        {"ev": "stage", "key": "firstread", "state": "end", "seconds": 0.1},
                        {"ev": "stage", "key": "ocr", "state": "start"})
        v = m.view()
        self.assertEqual(v.progress_note, M.PROGRESS_SAVED)
        self.run_events(m, h, done_event(), end=outcome("done", exit=0, done=done_event()))
        self.assertIn(M.PROGRESS_SAVED, m.view().result.notes)
        self.assertEqual(S.load(self.data).pages_measured_gpu, 0)       # no page was read: the average stays

    def test_stop_result(self):
        stop_lines = [["STOP", "B-0 | KLP | bundle | 4 pages | RESULT: STOP"], ["STOP", "STOP — made-up"],
                      ["STOP", "STOP: made-up ambiguity (pages 2-3)"]]
        ev = done_event(exit=3, overall="STOP", headline="STOP — made-up", lines=stop_lines)
        m, h = self.started()
        self.run_events(m, h, {"ev": "hello"}, ev, end=outcome("done", exit=3, done=ev))
        v = m.view()
        self.assertEqual(v.result.overall, "STOP")
        self.assertIn("could not be told apart (pages 2-3)", v.result.stop)
        self.assertEqual(v.banner.code, M.STOP)
        self.assertEqual(R.load(self.data)[0].overall, "STOP")

    def test_start_failure_removes_the_new_folder(self):
        def boom(job):
            raise OSError("made-up")
        m = self.model(start_check=boom)
        m.choose_input(str(self.pdf))
        self.assertEqual(m.check(), "refused")
        self.assertEqual(m.view().banner.code, M.STARTUP_FAILED)
        self.assertEqual(m.view().state, W.IDLE)
        self.assertEqual(list(self.reports.iterdir()), [])

    def test_events_after_the_end_are_ignored(self):
        m, h = self.started()
        self.run_events(m, h, {"ev": "hello"}, {"ev": "cancelled"},
                        {"ev": "page", "done": 1, "of": 2, "seconds": 1.0, "where": "card"}, "junk", None, {"ev": 5})
        self.assertEqual(m.view().state, W.FINISHING)
        self.assertEqual(m.pages_done, 0)


# ================================================================================================ other actions
class Actions(Base):
    def finished(self):
        m, h = self.started()
        self.run_events(m, h, {"ev": "hello"}, done_event(), end=outcome("done", exit=0, done=done_event()))
        return m, Path(h.job["out"])

    def test_selftest(self):
        self.problems = [SimpleNamespace(code="NO_NVIDIA_CARD", values={})]
        m = self.model()
        self.assertEqual(m.selftest(), "started")
        v = m.view()
        self.assertEqual(v.state, W.SELFTEST)
        self.assertFalse(v.selftest_enabled)
        self.assertFalse(v.check_enabled)
        self.assertEqual(m.selftest(), "busy")
        self.problems = []
        self.selftests[-1].code = 0
        m.poll()
        v = m.view()
        self.assertEqual(v.state, W.IDLE)
        self.assertEqual(v.banner.code, M.SELFTEST_OK)
        self.assertIsNone(m.card_problem)                               # probed again after the self-test
        m.selftest()
        self.selftests[-1].code = 1
        m.poll()
        self.assertEqual(m.view().banner.code, M.SELFTEST_FAILED)

    def test_integrity_problem(self):
        self.missing = "_internal\\torch\\lib\\cudnn64_9.dll"
        m = self.model()
        m.choose_input(str(self.pdf))
        v = m.view()
        self.assertEqual(v.banner.code, M.MISSING_PART)
        self.assertTrue(v.banner.text.startswith("_internal\\torch\\lib\\cudnn64_9.dll is missing"))
        self.assertFalse(v.check_enabled)
        self.assertEqual(m.check(), "refused")
        self.assertTrue(v.selftest_enabled)

    def test_result_buttons(self):
        m, out = self.finished()
        self.assertEqual(m.open_excel(), "missing")
        self.assertEqual(m.view().banner.code, M.FILE_MISSING)
        (out / "report.xlsx").write_bytes(b"made-up")
        self.assertEqual(m.open_excel(), "ok")
        self.assertEqual(self.opened[-1], str(out / "report.xlsx"))
        self.open_error = "report.xlsx"
        self.assertEqual(m.open_excel(), "folder")
        self.assertEqual(m.view().banner.code, M.NO_XLSX_PROGRAM)
        self.assertEqual(self.opened[-1], str(out))
        self.assertEqual(m.open_report_folder(), "ok")
        self.assertIsNone(m.summary_text())
        (out / "summary.txt").write_text("made-up summary\n", encoding="utf-8")
        self.assertEqual(m.summary_text(), "made-up summary\n")
        self.assertEqual(m.result_code_text(), "3F2A-91C0")
        folder, name = m.save_pdf_default()
        self.assertEqual((folder, name), (str(out), "SAMPLE BUNDLE.pdf"))
        self.assertEqual(m.save_pdf(str(self.dir / "copy.pdf")), "missing")
        (out / "summary.pdf").write_bytes(b"%PDF-1.4 made-up summary")
        self.assertEqual(m.save_pdf(str(self.pdf)), "refused")           # never over the checked file
        self.assertEqual(m.view().banner.code, M.SAVE_INSIDE_INPUT)
        target = self.dir / "saved" / "copy.pdf"
        target.parent.mkdir()
        self.assertEqual(m.save_pdf(str(target)), "ok")
        self.assertEqual(target.read_bytes(), b"%PDF-1.4 made-up summary")
        self.assertEqual(self.allowed[-1], str(target))
        self.assertEqual(m.view().banner.text, "The summary was saved as copy.pdf.")
        self.assertEqual(m.save_pdf(str(self.dir / "absent dir" / "x.pdf")), "failed")

    def test_recent_open(self):
        m, out = self.finished()
        self.assertEqual(m.open_recent(0), "ok")
        self.assertEqual(self.opened[-1], str(out))
        self.assertEqual(m.open_recent(5), "none")
        _rmtree(out)
        self.assertEqual(m.open_recent(0), "missing")
        self.assertEqual(m.view().banner.code, M.FOLDER_GONE)

    def test_close(self):
        m = self.model()
        self.assertEqual(m.request_close(), "close")
        m2, h = self.started()
        self.assertEqual(m2.request_close(), "confirm")
        self.assertEqual(m2.close_now(), "wait")
        self.assertEqual(h.cancelled, 1)
        self.assertFalse(m2.auto_done)
        self.run_events(m2, h, {"ev": "cancelled"}, end=outcome("cancelled", "CANCELLED", 4))
        self.assertTrue(m2.auto_done)
        self.assertEqual(m.close_now(), "close")

    def test_busy_blocks_edits(self):
        m, h = self.started()
        self.assertEqual(m.choose_input(str(self.dir)), "busy")
        self.assertEqual(m.set_program("EAP"), "busy")
        self.assertEqual(m.change_lock_path(None), "busy")
        self.assertEqual(m.change_report_root(str(self.dir)), "busy")
        self.assertEqual(m.check(), "busy")


class Auto(Base):
    def test_auto_done_writes_a_result_without_paths(self):
        res = self.dir / "auto.json"
        spec = W.AutoSpec(input=str(self.pdf), program="Master's", today="2026-10-28",
                          report_root=str(self.dir / "auto reports"), result_path=str(res))
        m = self.model(auto=spec)
        h = self.handles[-1]
        self.assertEqual(h.job["program"], "MASTER")
        self.assertEqual(h.job["today"], "2026-10-28")
        self.assertEqual(Path(h.job["out"]).parent, self.dir / "auto reports")
        self.run_events(m, h, {"ev": "hello"}, {"ev": "page", "done": 1, "of": 1, "seconds": 2.0, "where": "card"},
                        done_event(), end=outcome("done", exit=0, done=done_event()))
        self.assertTrue(m.auto_done)
        self.assertEqual(m.exit_status(), 0)
        data = json.loads(res.read_text(encoding="utf-8"))
        self.assertEqual(data["state"], "done")
        self.assertEqual(data["code"], "3F2A-91C0")
        self.assertEqual(data["overall"], "FAIL")
        self.assertEqual(data["exit"], 0)
        self.assertEqual(data["pages_read_on_card"], 1)
        self.assertNotIn(":\\", res.read_text(encoding="utf-8"))
        self.assertIn(str(res), self.allowed)
        self.assertFalse((self.data / "settings.json").exists())        # --auto stores nothing
        self.assertFalse((self.data / "recent.json").exists())

    def test_auto_refused_by_the_card(self):
        res = self.dir / "auto.json"
        self.problems = [SimpleNamespace(code="CARD_TOO_SMALL", values={"name": CARD, "total": "4.0"})]
        m = self.model(auto=W.AutoSpec(str(self.pdf), "KLP", None, None, str(res)))
        self.assertTrue(m.auto_done)
        self.assertEqual(m.exit_status(), 1)
        data = json.loads(res.read_text(encoding="utf-8"))
        self.assertEqual((data["state"], data["error"]), ("refused", "CARD_TOO_SMALL"))

    def test_auto_refused_input_and_error(self):
        res = self.dir / "auto.json"
        m = self.model(auto=W.AutoSpec(str(self.dir / "absent.pdf"), "KLP", None, None, str(res)))
        self.assertEqual(json.loads(res.read_text(encoding="utf-8"))["error"], M.NOT_PDF_OR_FOLDER)
        self.assertEqual(m.exit_status(), 1)
        m2 = self.model(auto=W.AutoSpec(str(self.pdf), "KLP", None, None, str(res)))
        self.run_events(m2, self.handles[-1], {"ev": "hello"}, end=outcome("error", "CRASH", 1))
        data = json.loads(res.read_text(encoding="utf-8"))
        self.assertEqual((data["state"], data["error"], data["exit"]), ("error", "CRASH", 1))


# ================================================================================================ lines
class Lines(unittest.TestCase):
    def test_parse_lines(self):
        rows, orders, stop, notes = W.parse_lines(LINES, LINES[1][1])
        self.assertEqual(orders, [("pages 4-5", "the HSC/SSC pages are swapped")])
        self.assertIsNone(stop)
        self.assertEqual(notes, ["NOTE: a made-up note"])
        byt = {r.title: r for r in rows}
        self.assertEqual((byt["PASSPORT"].pages, byt["PASSPORT"].verdict), ("p1", "PASS"))
        self.assertEqual(byt["ACADEMIC HSC"].reasons, "no e-Apostille; made-up reason")
        self.assertEqual(byt["BANK STATEMENT"].pages, "BANK STATEMENT (2).pdf p1-2")
        self.assertEqual(byt["PHOTO MISSING"].verdict, "MISSING")
        self.assertEqual(byt[LINES[10][1]].verdict, "FLAG")
        self.assertEqual((byt["NAMES"].verdict, byt["NAMES"].cross), ("FLAG", True))
        self.assertEqual(rows[-1].title, "NAMES")
        self.assertEqual(byt["Not checked (not a PDF or image)"].verdict, "NOT CHECKED")
        self.assertEqual(byt["Not checked (not a PDF or image)"].reasons, "NOTES.docx")
        for r in rows:
            self.assertNotIn("RESULT", r.title)
            self.assertNotIn("Report", r.title)
        self.assertEqual(W.parse_lines([["PASS", "PASS: PASSPORT (p1); PHOTO (p2)"]])[0][0].reasons,
                         "PASSPORT (p1); PHOTO (p2)")
        self.assertEqual(W.parse_lines(["plain text line", None, 5])[3], ["plain text line", "5"])

    def test_verdict_tags_and_colours(self):
        self.assertEqual(W.verdict_tag("NOT JUDGED"), "v_NOT_JUDGED")
        for v in ("PASS", "FLAG", "FAIL", "MISSING", "NOT JUDGED", "PRESENT"):
            self.assertIn(v, W.VERDICT_FILL)
        try:
            from src.verify.runner import report
        except ImportError:
            return
        for k, v in report.FILL.items():                                # the report's FILL colours
            self.assertEqual(W.VERDICT_FILL[k], v, k)


# ================================================================================================ adapters
class Adapters(Base):
    def test_controller_handle_with_fakes(self):
        calls = {}

        class FakeController:
            def __init__(self):
                self.state, self.outcome, self.q = "checking", None, [{"ev": "hello"}, b'{"v":1,"ev":"x"}', "bad"]

            def start(self, path, log_path=None):
                calls["start"] = (Path(path), Path(log_path))

            def poll(self):
                out, self.q = self.q, []
                return out

            def cancel(self):
                calls["cancel"] = True

        written = {}
        proto = SimpleNamespace(write_job=lambda p, job: written.setdefault("p", Path(p)) and Path(p))
        job = {"job": "a" * 32}
        h = W.ControllerHandle(job, self.data, SimpleNamespace(CheckController=FakeController), proto)
        self.assertEqual(written["p"], self.data / "jobs" / ("a" * 32) / "job.json")
        self.assertEqual(calls["start"], (written["p"], self.data / "logs" / f"worker-{'a' * 32}.log"))
        self.assertEqual(h.drain(), [{"ev": "hello"}, {"v": 1, "ev": "x"}])
        self.assertIsNone(h.finished())
        h.c.state, h.c.outcome = "done", "OUT"
        self.assertEqual(h.finished(), "OUT")
        h.cancel()
        self.assertTrue(calls["cancel"])

    def test_with_the_real_controller(self):
        """Task C's CheckController (its own reader thread) on a fake worker process, through the model."""
        try:
            from src.verify.runner.app import controller, protocol
        except ImportError:
            self.skipTest("controller.py not there yet")

        def proc_for(events, rc):
            lines = b"".join(protocol.encode(e) for e in events)
            return SimpleNamespace(stdout=io.BytesIO(lines), stdin=io.BytesIO(), pid=1, returncode=rc,
                                   poll=lambda: rc, wait=lambda timeout=None: rc, terminate=lambda: None)

        def run(events, rc):
            procs = []
            ctrl = SimpleNamespace(CheckController=lambda: controller.CheckController(
                starter=lambda job_path, log, index: procs.append(proc_for(events, rc)) or procs[-1],
                terminate=lambda p: None))
            m = self.model(start_check=lambda job: W.ControllerHandle(job, self.data, ctrl, protocol))
            m.choose_input(str(self.pdf))
            self.assertEqual(m.check(), "started")
            t0 = time.monotonic()
            while m.state != W.IDLE and time.monotonic() - t0 < 10:
                m.poll()
                time.sleep(0.01)
            self.assertEqual(m.state, W.IDLE)
            return m

        ev_done = dict(protocol.done(0, "FAIL", LINES[1][1], "3F2A-91C0", "<case>", LINES))
        m = run([protocol.hello("1.0.0", 1), protocol.stage("card", "start"),
                 protocol.page(1, 2, 1.5, "card"), protocol.page(2, 2, 2.5, "card"), ev_done], 0)
        v = m.view()
        self.assertEqual(v.result.code, "3F2A-91C0")
        self.assertIsNone(v.banner)
        self.assertEqual(len(R.load(self.data)), 1)
        self.assertAlmostEqual(S.load(self.data).seconds_per_page_gpu, 2.0)
        jobs = list((self.data / "jobs").iterdir())
        self.assertEqual(len(jobs), 1)
        protocol.read_job(jobs[0] / "job.json")                          # C reads what the window wrote
        m2 = run([protocol.hello("1.0.0", 2), protocol.stage("card", "start"),
                  protocol.error("CARD_NOT_SUPPORTED", "made-up", {"name": CARD, "which": "old"})], 6)
        v2 = m2.view()
        self.assertEqual(v2.banner.code, "CARD_NOT_SUPPORTED")
        self.assertIn("is too old", v2.banner.text)
        self.assertFalse(v2.check_enabled)

    def test_selftest_handle_uses_spawn(self):
        calls = {}
        proc = SimpleNamespace(poll=lambda: None)
        sp = SimpleNamespace(self_command=lambda *a: ["app.exe", *a],
                             start_process=lambda cmd, **kw: calls.setdefault("cmd", (cmd, kw)) and proc,
                             terminate=lambda p, wait_s=0: calls.setdefault("term", p))
        h = W.SelftestHandle(r"D:\Made Up Reports", sp)
        self.assertEqual(calls["cmd"][0], ["app.exe", "--selftest", "--report-dir", r"D:\Made Up Reports"])
        self.assertIsNone(h.finished())
        h.cancel()
        self.assertIs(calls["term"], proc)

    def test_version_info(self):
        fake = SimpleNamespace(APP_VERSION="1.0.0", BUILD_ID="20261029.1-abc1234", BASE_RULES_VERSION="b",
                               RUNNER_RULES_VERSION="r", ARCH_FLAGS=("sm_75", "sm_120"), CAP_GB=3.0,
                               NEED_FREE_MB=4096, footer_text=lambda: "App 1.0.0 (made-up footer)")
        v = W.version_info(fake)
        self.assertEqual(v["arch_flags"], ("sm_75", "sm_120"))
        self.assertEqual(v["footer"], "App 1.0.0 (made-up footer)")
        self.assertEqual(W.footer_app_line(v), "App 1.0.0 (made-up footer)")
        self.assertEqual(v["need_free_mb"], 4096)
        bare = W.version_info(SimpleNamespace())
        self.assertEqual(bare["arch_flags"], W.DEFAULT_ARCH_FLAGS)
        self.assertTrue(bare["base_rules_version"])                      # from model.py
        try:
            from src.verify.runner.app import version
        except ImportError:
            return
        real = W.version_info(version)
        self.assertEqual(real["footer"], version.footer_text())
        self.assertEqual(real["arch_flags"], tuple(version.ARCH_FLAGS))

    def test_integrity_quick(self):
        app = self.dir / "app"
        (app / "_internal").mkdir(parents=True)
        (app / "Hangeul Document Checker.exe").write_bytes(b"x" * 10)
        (app / "_internal" / "a.dll").write_bytes(b"y" * 3)
        manifest = {"schema": "hdc-manifest/1",
                    "files": {"Hangeul Document Checker.exe": {"size": 10, "sha256": "0" * 64},
                              "_internal/a.dll": {"size": 3, "sha256": "0" * 64},
                              "../outside.txt": {"size": 1, "sha256": "0" * 64}}}
        (app / "_internal" / "MANIFEST.json").write_text(json.dumps(manifest), encoding="utf-8")
        self.assertIsNone(W.integrity_quick(app))
        (app / "_internal" / "a.dll").write_bytes(b"y" * 4)
        self.assertEqual(W.integrity_quick(app), "_internal\\a.dll")
        (app / "_internal" / "a.dll").unlink()
        self.assertEqual(W.integrity_quick(app), "_internal\\a.dll")
        (app / "_internal" / "MANIFEST.json").write_text("{junk", encoding="utf-8")
        self.assertIsNone(W.integrity_quick(app))
        self.assertIsNone(W.integrity_quick(self.dir / "absent"))
        if not getattr(sys, "frozen", False):
            self.assertIsNone(W.integrity_quick())

    def test_window_args_and_build_model(self):
        opts = SimpleNamespace(role="auto", path=str(self.pdf), program="EAP", today="2026-10-28",
                               report_root=str(self.reports), auto_result=str(self.dir / "a.json"),
                               trace_dir=None, data_dir=str(self.data), lock_extra=[OTHER_LOCK], argv=[])
        a = W.window_args(opts)
        self.assertEqual((a.role, a.program, a.lock_extra), ("auto", "EAP", [OTHER_LOCK]))
        b = W.window_args(["--auto", str(self.pdf), "--program", "KLP", "--gpu-lock-extra", OTHER_LOCK,
                           "--data-dir", str(self.data)])
        self.assertEqual((b.role, b.path, b.lock_extra), ("auto", str(self.pdf), [OTHER_LOCK]))
        c = W.window_args([str(self.pdf)])
        self.assertEqual((c.role, c.path), ("window", str(self.pdf)))
        m = W.build_model(c, self.deps())
        self.assertEqual(m.view().input_text, "SAMPLE BUNDLE.pdf")
        self.assertIsNone(m.auto)
        m2 = W.build_model(W.window_args(dict_to_opts(opts, data_dir=str(self.data))), self.deps())
        self.assertEqual(m2.auto.program, "EAP")
        self.assertFalse(m2.persist)


def dict_to_opts(ns, **kw):
    d = dict(vars(ns))
    d.update(kw)
    return SimpleNamespace(**d)


# ================================================================================================ Tk binding
class FakeVar:
    def __init__(self, master=None, value=""):
        self.v = value

    def get(self):
        return self.v

    def set(self, v):
        self.v = v


class FakeWidget:
    def __init__(self, master=None, **kw):
        self.kw = dict(kw)
        self.visible = None
        self.states = set()
        self.destroyed = False

    def grid(self, **kw):
        self.visible = True

    def grid_remove(self):
        self.visible = False

    def configure(self, **kw):
        self.kw.update(kw)

    config = configure

    def state(self, spec=None):
        for s in spec or ():
            if s.startswith("!"):
                self.states.discard(s[1:])
            else:
                self.states.add(s)
        return tuple(self.states)

    def destroy(self):
        self.destroyed = True

    def bind(self, *a, **k):
        pass

    def columnconfigure(self, *a, **k):
        pass

    rowconfigure = columnconfigure

    def set(self, *a):
        pass

    def start(self, *a):
        self.kw["running"] = True

    def stop(self):
        self.kw["running"] = False


class FakeTree(FakeWidget):
    def __init__(self, master=None, **kw):
        super().__init__(master, **kw)
        self.rows, self.sel = {}, ()

    def heading(self, *a, **k):
        pass

    def column(self, *a, **k):
        pass

    def tag_configure(self, tag, **kw):
        self.kw.setdefault("tags", {})[tag] = kw

    def insert(self, parent, index, text="", values=(), tags=()):
        iid = f"I{len(self.rows) + 1:03d}"
        self.rows[iid] = (text, tuple(values), tuple(tags))
        return iid

    def get_children(self, item=""):
        return tuple(self.rows)

    def delete(self, *items):
        for i in items:
            self.rows.pop(i, None)

    def selection(self):
        return self.sel

    def yview(self, *a):
        pass


class FakeListbox(FakeWidget):
    def __init__(self, master=None, **kw):
        super().__init__(master, **kw)
        self.items, self.cur = [], ()

    def insert(self, index, *elements):
        self.items.extend(elements)

    def delete(self, first, last=None):
        self.items = []

    def curselection(self):
        return self.cur


class FakeText(FakeWidget):
    text = ""

    def insert(self, index, chars):
        self.text += chars

    def delete(self, a, b=None):
        self.text = ""


class FakeRoot(FakeWidget):
    def __init__(self):
        super().__init__()
        self.after_calls, self.clip, self.protocols = [], "", {}

    def title(self, t):
        self.kw["title"] = t

    def minsize(self, *a):
        pass

    def option_add(self, *a):
        pass

    def protocol(self, name, fn):
        self.protocols[name] = fn

    def after(self, ms, fn):
        self.after_calls.append((ms, fn))

    def clipboard_clear(self):
        self.clip = ""

    def clipboard_append(self, s):
        self.clip += s


def fake_tk():
    tk = SimpleNamespace(StringVar=FakeVar, Frame=FakeWidget, Label=FakeWidget, Listbox=FakeListbox, Text=FakeText)
    ttk = SimpleNamespace(Frame=FakeWidget, Label=FakeWidget, Button=FakeWidget, Combobox=FakeWidget,
                          Radiobutton=FakeWidget, Entry=FakeWidget, Progressbar=FakeWidget, Treeview=FakeTree,
                          Scrollbar=FakeWidget, Style=FakeWidget)
    return tk, ttk


class TkBinding(Base):
    def window(self, m, answers=None, files=None):
        tk, ttk = fake_tk()
        self.fd_calls = []
        files = files or {}

        def ask(kind):
            def f(**kw):
                self.fd_calls.append((kind, kw))
                if kind == "save_lock" and "confirmoverwrite" in kw:
                    raise TypeError("an older Tk")
                return files.get(kind, "")
            return f
        fd = SimpleNamespace(askopenfilename=ask("pdf"), askdirectory=ask("dir"), asksaveasfilename=ask("save_lock"))
        mb = SimpleNamespace(askyesno=lambda *a, **k: (answers or {}).get("yes", True))
        root = FakeRoot()
        return W.TkWindow(root, m, tk=tk, ttk=ttk, filedialog=fd, messagebox=mb), root

    def test_render_states_and_clicks(self):
        m = self.model()
        win, root = self.window(m, files={"pdf": str(self.pdf), "save_lock": OTHER_LOCK})
        win.render()
        self.assertEqual(root.kw["title"], "Hangeul Document Checker")
        self.assertIn("disabled", win.b_check.states)
        self.assertFalse(win.f_banner.visible)
        self.assertEqual(win.v_footer_card.get(), "Graphics card: NVIDIA GeForce RTX 0000 (8.0 GB) · driver 000.00")
        win.b_pdf.kw["command"]()
        self.assertEqual(win.v_input.get(), "SAMPLE BUNDLE.pdf")
        self.assertNotIn("disabled", win.b_check.states)
        win.on_toggle_options()
        self.assertTrue(win.f_options.visible)
        self.assertEqual(win.b_options.kw["text"], "▾ Options")
        self.assertTrue(win.v_lock.get().endswith("(the Telegram bot's document check)"))
        win.b_lock.kw["command"]()                                     # Change…: falls back without the option
        self.assertEqual(win.v_lock.get(), os.path.normpath(OTHER_LOCK))
        win.b_lock_off.kw["command"]()
        self.assertEqual(win.v_lock.get(), W.LOCK_OFF_TEXT)
        self.assertIn("disabled", win.b_lock_off.states)
        win.v_program.set("Master's")
        win.on_program()
        self.assertEqual(m.program, "MASTER")
        win.b_check.kw["command"]()
        h = self.handles[-1]
        self.assertIn("disabled", win.b_check.states)
        self.assertNotIn("disabled", win.b_cancel.states)
        self.assertIn("disabled", win.b_pdf.states)
        h.push({"ev": "hello"}, {"ev": "page", "done": 1, "of": 3, "seconds": 2.0, "where": "card"})
        win.tick()
        self.assertEqual(win.v_progress.get(), "Reading page 2 of 3 on the graphics card — about 6 s left")
        self.assertEqual(win.p_bar.kw["value"], 100 / 3)
        h.push({"ev": "gpu_wait", "state": "start", "why": "lock", "lock": "bot"})
        win.tick()
        self.assertTrue(win.f_banner.visible)
        self.assertTrue(win.b_banner_cancel.visible)
        self.assertIn("Telegram bot", win.l_banner_text.kw["text"])
        h.push({"ev": "gpu_wait", "state": "end", "why": "lock", "waited_s": 5})
        win.tick()
        self.assertFalse(win.f_banner.visible)
        h.push(done_event())
        h.end = outcome("done", exit=0, done=done_event())
        win.tick()
        self.assertEqual(win.v_overall.get(), LINES[1][1])
        self.assertEqual(win.v_code.get(), "Result code 3F2A-91C0 (check date 2026-10-28)")
        self.assertEqual(len(win.tree.rows), 9)
        first = win.tree.rows["I001"]
        self.assertEqual(first, ("PASSPORT", ("p1", "PASS", ""), ("v_PASS",)))
        self.assertEqual(len(win._fix_labels), 2)                     # one fix line + one note
        win.tree.sel = ("I002",)
        win.on_select()
        self.assertEqual(win.t_detail.text, LINES[6][1])
        win.b_copy_code.kw["command"]()
        self.assertEqual(root.clip, "3F2A-91C0")
        self.assertEqual(win.l_recent.items, ["28 Oct 14:12  SAMPLE BUNDLE.pdf  Master's  FAIL  3F2A-91C0"])
        win.l_recent.cur = (0,)
        win.on_open_recent()
        self.assertEqual(self.opened[-1], h.job["out"])
        rows_before = dict(win.tree.rows)
        win.render()                                                   # the same result is not drawn again
        self.assertEqual(win.tree.rows, rows_before)
        self.assertTrue(root.after_calls)

    def test_close_and_auto_destroy(self):
        m = self.model()
        win, root = self.window(m, answers={"yes": False})
        m.choose_input(str(self.pdf))
        win.on_check()
        root.protocols["WM_DELETE_WINDOW"]()                           # "cancel and close?" -> no
        self.assertFalse(root.destroyed)
        self.assertEqual(self.handles[-1].cancelled, 0)
        win2, root2 = self.window(self.model())
        root2.protocols["WM_DELETE_WINDOW"]()
        self.assertTrue(root2.destroyed)

    def test_low_ram_question(self):
        self.free_ram = 1000
        m = self.model()
        m.choose_input(str(self.pdf))
        win, root = self.window(m, answers={"yes": False})
        win.on_check()
        self.assertEqual(self.handles, [])
        win.mb.askyesno = lambda *a, **k: True
        win.on_check()
        self.assertEqual(len(self.handles), 1)

    def test_error_banner_render(self):
        self.problems = [SimpleNamespace(code="NO_NVIDIA_CARD", values={})]
        m = self.model()
        win, root = self.window(m)
        win.render()
        self.assertTrue(win.f_banner.visible)
        self.assertEqual(win.l_banner_title.kw["text"], "No NVIDIA graphics card")
        self.assertEqual(win.f_banner.kw["background"], W.BANNER_COLOURS[M.ERROR][0])
        self.assertFalse(win.b_banner_cancel.visible)
        self.assertNotIn("disabled", win.b_selftest.states)

    def test_import_does_not_load_tk(self):
        self.assertFalse(hasattr(W, "tk"))
        src = Path(W.__file__).read_text(encoding="utf-8")
        head = src.split("class TkWindow")[0]
        self.assertNotIn("import tkinter", head)


if __name__ == "__main__":
    unittest.main()
