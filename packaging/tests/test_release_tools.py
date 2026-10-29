"""Unit tests of Task H's release tools (APP_SPEC rev 2, R2.6 / R2.7 P0), on FAKES only.

    set HDC_TEST_NO_CARD=1
    <runner python> -I -B -m unittest discover -s packaging\\tests -p test_release_tools.py

No card, no frozen app, no real case, no PyInstaller run: made-up dist trees and import tables, made-up r8/app
run.json and summary pairs, recorded event streams, a fake gpu_guard. The cardrun tests start tiny Python children
(a few seconds of sleep at most) to prove that a timeout ends the child AND what it started, and that nothing is left
behind. Files go only under private-runner\\selftest\\app\\H (each test's own temp folder, removed afterwards). All
names, texts and codes here are made up.
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import contextlib  # noqa: E402
import hashlib  # noqa: E402
import io  # noqa: E402
import json  # noqa: E402
import marshal  # noqa: E402
import os  # noqa: E402
import shutil  # noqa: E402
import tempfile  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
import unittest  # noqa: E402
import zipfile  # noqa: E402
from pathlib import Path  # noqa: E402
from unittest import mock  # noqa: E402

os.environ.setdefault("HDC_TEST_NO_CARD", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cardrun  # noqa: E402
import dll_check  # noqa: E402
import parity  # noqa: E402
import politeness  # noqa: E402
import scan_bundle  # noqa: E402
import timing  # noqa: E402
import trace_check  # noqa: E402

H_ROOT = Path(r"C:\Hangeul\DOCCHECK\lab\private-runner\selftest\app\H")
PY = sys.executable
NOWIN = 0x08000000


class TmpCase(unittest.TestCase):
    def setUp(self) -> None:
        H_ROOT.mkdir(parents=True, exist_ok=True)
        self.tmp = Path(tempfile.mkdtemp(prefix=self.id().rsplit(".", 1)[-1][:24] + "-", dir=H_ROOT))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)


# ================================================================================================== cardrun

class FakeGuard:
    """gpu_guard's surface: gpu_slot, Slot.checkpoint, SlotInterrupted, SlotExpired, DEV_LOCK."""

    class SlotInterrupted(Exception):
        pass

    class SlotExpired(TimeoutError):
        pass

    def __init__(self, folder: Path, interrupt_on=()) -> None:
        self.DEV_LOCK = folder / "gpu-dev.lock"
        self.calls = []
        self.released = False
        self.checkpoints = 0
        self.interrupt_on = set(interrupt_on)

    @contextlib.contextmanager
    def gpu_slot(self, what, need_free_gb=4.0, wait_s=900, max_s=90):
        self.calls.append({"what": what, "need_free_gb": need_free_gb, "wait_s": wait_s, "max_s": max_s})
        self.DEV_LOCK.write_text(f"{os.getpid()} {what}", encoding="utf-8")
        guard = self

        class Slot:
            def checkpoint(self) -> None:
                guard.checkpoints += 1
                if guard.checkpoints in guard.interrupt_on:
                    raise guard.SlotInterrupted("the bot's check started")
                os.utime(guard.DEV_LOCK)
        try:
            yield Slot()
        finally:
            self.DEV_LOCK.unlink(missing_ok=True)
            self.released = True


def child(code: str):
    return [PY, "-I", "-B", "-c", code]


EVENTS_CHILD = (
    "import json,time\n"
    "print(json.dumps({'v':1,'ev':'hello','app':'1.0.0','pid':0}), flush=True)\n"
    "for i in range(3):\n"
    "    print(json.dumps({'v':1,'ev':'page','done':i+1,'of':3,'seconds':0.1,'where':'card'}), flush=True)\n"
    "    time.sleep(0.05)\n"
    "print('a plain line', flush=True)\n"
    "import sys; sys.stderr.write('stage times\\n')\n"
    "print(json.dumps({'v':1,'ev':'done','exit':0,'code':'ABCD-1234'}), flush=True)\n")


class CardrunTests(TmpCase):
    def run_card(self, cmd, timeout=30.0, **kw):
        g = kw.pop("guard", None) or FakeGuard(self.tmp)
        return cardrun.run(cmd, timeout, guard=g, approval="Q-O1", **kw), g

    def test_refuses_without_q_o1(self):
        g = FakeGuard(self.tmp)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(cardrun.APPROVAL_ENV, None)
            with self.assertRaises(cardrun.NotApproved):
                cardrun.run(child("pass"), 10, guard=g)
        self.assertEqual(g.calls, [])
        self.assertIn("Q-O1", cardrun.__doc__)

    def test_real_guard_never_loaded_in_tests(self):
        self.assertTrue(os.environ.get("HDC_TEST_NO_CARD"))
        with self.assertRaises(RuntimeError):
            cardrun.load_guard()
        with self.assertRaises(RuntimeError):
            cardrun.run(child("pass"), 10, approval="Q-O1")

    def test_run_holds_the_slot_and_streams_events(self):
        seen = []
        res, g = self.run_card(child(EVENTS_CHILD), 30.0, on_line=lambda t, line, ctl: seen.append(line),
                               log_dir=self.tmp / "log", what="hdc test")
        self.assertEqual(res.returncode, 0)
        self.assertFalse(res.timed_out)
        evs = cardrun.events_of(res.lines)
        self.assertEqual([e["ev"] for _, e in evs], ["hello", "page", "page", "page", "done"])
        self.assertEqual(len(seen), 6)
        self.assertEqual(g.calls, [{"what": "hdc test", "need_free_gb": 4.0, "wait_s": 1800,
                                    "max_s": 30.0 + cardrun.SLOT_MARGIN_S}])
        self.assertTrue(g.released)
        self.assertFalse(g.DEV_LOCK.exists())
        for name in ("stdout.log", "stderr.log", "cardrun.json"):
            self.assertTrue((self.tmp / "log" / name).is_file(), name)
        stats = json.loads((self.tmp / "log" / "cardrun.json").read_text(encoding="utf-8"))
        self.assertEqual(stats["lines"], 6)
        self.assertNotIn("ABCD-1234", json.dumps(stats))
        self.assertGreater(res.stderr_bytes, 0)
        self.assertEqual(cardrun.done_event(evs)["code"], "ABCD-1234")

    def test_bot_interrupt_is_recorded_not_fatal_and_lock_kept_fresh(self):
        g = FakeGuard(self.tmp, interrupt_on={1, 2, 3})
        res, _ = self.run_card(child("import time; time.sleep(1.2)"), 30.0, guard=g, checkpoint_s=0.1)
        self.assertEqual(res.returncode, 0)
        self.assertGreaterEqual(res.interrupted, 2)
        self.assertGreaterEqual(res.lock_refreshed, 2)
        self.assertEqual(res.watcher_errors, [])
        self.assertTrue(g.released)

    def test_timeout_ends_the_child_and_what_it_started(self):
        code = ("import subprocess,sys,time,json\n"
                f"p=subprocess.Popen([sys.executable,'-I','-B','-c','import time; time.sleep(20)'],creationflags={NOWIN})\n"
                "print(json.dumps({'v':1,'ev':'hello','gpid':p.pid}), flush=True)\n"
                "time.sleep(20)\n")
        t0 = time.monotonic()
        res, g = self.run_card(child(code), 3.0)
        self.assertTrue(res.timed_out)
        self.assertLess(time.monotonic() - t0, 15.0)
        hello = cardrun.first(cardrun.events_of(res.lines), "hello")
        self.assertIsNotNone(hello)
        time.sleep(0.3)
        self.assertFalse(cardrun.pid_alive(int(hello[1]["gpid"])), "the grandchild survived")
        self.assertTrue(g.released)
        self.assertEqual(res.job_note, "")

    def test_processes_left_by_the_child_are_ended(self):
        code = ("import subprocess,sys,json\n"
                f"p=subprocess.Popen([sys.executable,'-I','-B','-c','import time; time.sleep(20)'],creationflags={NOWIN})\n"
                "print(json.dumps({'v':1,'ev':'hello','gpid':p.pid}), flush=True)\n")
        with mock.patch.object(cardrun, "KILL_GRACE_S", 0.5):
            res, _ = self.run_card(child(code), 30.0)
        self.assertEqual(res.returncode, 0)
        self.assertGreaterEqual(res.leftover_killed, 1)
        hello = cardrun.first(cardrun.events_of(res.lines), "hello")
        time.sleep(0.3)
        self.assertFalse(cardrun.pid_alive(int(hello[1]["gpid"])))

    def test_one_run_at_a_time(self):
        g = FakeGuard(self.tmp)
        out = {}
        th = threading.Thread(target=lambda: out.setdefault("r", cardrun.run(
            child("import time; time.sleep(1.5)"), 30, guard=g, approval="Q-O1")))
        th.start()
        for _ in range(100):
            if g.calls:
                break
            time.sleep(0.02)
        with self.assertRaises(cardrun.Busy):
            cardrun.run(child("pass"), 10, guard=FakeGuard(self.tmp), approval="Q-O1")
        th.join(30)
        self.assertEqual(out["r"].returncode, 0)

    def test_stdin_cancel_and_memory_counters(self):
        code = ("import sys,json\n"
                "print(json.dumps({'v':1,'ev':'hello'}), flush=True)\n"
                "line=sys.stdin.readline().strip()\n"
                "print(json.dumps({'v':1,'ev':'cancelled' if line=='cancel' else 'other'}), flush=True)\n"
                "sys.exit(4)\n")

        def on_line(t, line, ctl):
            ev = cardrun.parse_event(line)
            if ev and ev["ev"] == "hello":
                self.assertTrue(ctl.write("cancel\n"))
        res, _ = self.run_card(child(code), 30.0, stdin=True, on_line=on_line)
        self.assertEqual(res.returncode, 4)
        self.assertIsNotNone(cardrun.first(cardrun.events_of(res.lines), "cancelled"))
        self.assertGreater(res.peak_working_set or 0, 0)
        self.assertGreater(res.job_peak_process_commit or 0, 0)

    def test_on_tick_runs_and_callback_errors_never_kill_the_run(self):
        ticks = []

        def bad_line(t, line, ctl):
            raise ValueError("harness bug")
        res, _ = self.run_card(child("import time; print('x', flush=True); time.sleep(0.6)"), 30.0,
                               on_line=bad_line, on_tick=lambda t, ctl: ticks.append(t), tick_s=0.05)
        self.assertEqual(res.returncode, 0)
        self.assertTrue(ticks)
        self.assertEqual(len(res.callback_errors), 1)

    def test_helpers(self):
        exe = Path("C:/app/Hangeul Document Checker.exe")
        cmd = cardrun.cli_cmd(exe, "C:/in/x.pdf", "KLP", Path("C:/o"), "2026-01-02", Path("C:/d"), [Path("C:/l")])
        self.assertEqual(cmd[1:3], ["--cli", "C:/in/x.pdf"])
        self.assertEqual(cmd[cmd.index("--today") + 1], "2026-01-02")
        self.assertEqual(cmd[cmd.index("--gpu-lock-extra") + 1], str(Path("C:/l")))
        a = cardrun.auto_cmd(exe, "C:/in/x.pdf", "KLP", None, Path("C:/r"), Path("C:/r/a.json"), Path("C:/d"),
                             Path("C:/t"))
        self.assertIn("--trace-opens", a)
        self.assertNotIn("--today", a)
        env = cardrun.app_env(Path("C:/d"), {"HDC_TEST_NO_CARD": "1", "PATH": "x", "HDC_CARDRUN_APPROVAL": "Q-O1"})
        self.assertEqual(env, {"PATH": "x", "HDC_DATA_DIR": str(Path("C:/d"))})
        for bad in ("../x", "", "a/b", "x" * 70):
            with self.assertRaises(ValueError):
                cardrun.check_build_id(bad)
        self.assertEqual(cardrun.check_build_id("20261029.1-abc1234-dirty"), "20261029.1-abc1234-dirty")
        self.assertIsNone(cardrun.parse_event("not json"))
        self.assertIsNone(cardrun.parse_event('{"no": "ev"}'))
        self.assertEqual(cardrun.parse_event('{"v":1,"ev":"card"}')["ev"], "card")
        d1 = cardrun.new_run_dir(self.tmp, "p1")
        d2 = cardrun.new_run_dir(self.tmp, "p1")
        self.assertNotEqual(d1, d2)
        self.assertTrue(d1.is_dir() and d2.is_dir())
        self.assertEqual(cardrun.app_dir_for("20261029.1-abc", None),
                         cardrun.BUILDS / "20261029.1-abc" / "dist" / "Hangeul Document Checker")


# ================================================================================================== parity fixtures

CASE_ID = "B-00000000000000aa"
TEXTS = ["SAMPLE BOARD OF EXAMINATIONS page one", "This is to certify that SAMPLE STUDENT ONE", "Roll No 000123"]


def make_run(overall="FAIL", verdict2="FLAG", cross_rule="names.father", missing_text="PHOTO MISSING",
             note_msg="made-up note", look="same", best="CAND A", dot=None, code="ABCD-1234", case_id=CASE_ID):
    return {"schema": "runner-run/2", "case_id": case_id, "mode": "bundle", "input": "C:/made/up/SAMPLE.pdf",
            "program": "KLP", "today": "2026-01-02", "overall": overall,
            "docs": [{"unit": {"key": "passport", "pages": [0]}, "verdict": "PASS",
                      "findings": [{"verdict": "PASS", "rule": "passport.validity", "pages": [], "message": "m1"}]},
                     {"unit": {"key": "father_nid", "pages": [1]}, "verdict": verdict2,
                      "findings": [{"verdict": verdict2, "rule": "nid.sample", "pages": [2], "message": "m2"},
                                   {"verdict": "PASS", "rule": "nid.other", "pages": [2], "message": "m3"}]}],
            "cross": [{"verdict": "FAIL", "rule": cross_rule, "pages": [2], "message": "made-up"}],
            "missing_lines": [["MISSING", missing_text]],
            "notices": [{"kind": "NOTE", "pages": [1, 2], "message": note_msg, "blocks": False, "units": ["D01"]}],
            "names_rereads": [{"run_page": 1, "page": 2, "kind": "name", "how": "closer look", "result": look,
                               "best": best, "dot": dot, "candidates": ["CAND A", "CAND B"], "variant": "CAND B"},
                              {"run_page": 1, "page": 2, "variant": "CAND B", "result": "confirmed"}],
            "result_code": code}


def make_store(texts=TEXTS, complete=True, sideways=None):
    return {"schema": "runner-page-texts/1", "complete": complete, "case_id": CASE_ID, "mode": "bundle",
            "files": [{"name": "SAMPLE.pdf", "sha256": "0" * 64, "size": 10, "n_pages": len(texts),
                       "pages": [{"page": i, "text": t, "kind": "ocr", "sideways": bool(sideways and i in sideways),
                                  "seconds": 1.0 + i} for i, t in enumerate(texts)]}], "seconds_total": 30.0}


R8_SUMMARY = ["B-00000000000000aa | KLP | bundle | 3 pages | RESULT: FAIL", "FAIL: 1 FAIL, 1 FLAG",
              "READING: GPU pre-read 3 pages, CPU light engine 0, text layer 0", "ORDER: OK — the pages fit",
              "PASSPORT (p1): PASS", "FATHER NID (p2): FLAG — made-up reason", "PHOTO MISSING",
              "NAMES: FAIL — made-up name reason", "Report: C:\\r8\\report.xlsx | JSON: C:\\r8\\run.json"]


def app_summary(lines=R8_SUMMARY, code="ABCD-1234"):
    out = list(lines[:2]) + [f"RESULT CODE: {code} (check date 2026-01-02)",
                             "READING: GPU pre-read 3 pages, CPU light engine 0, text layer 0 — app GPU build"]
    out += list(lines[3:-1]) + ["Report: C:\\app\\case\\report.xlsx | JSON: C:\\app\\case\\run.json"]
    return out


def write_json(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj), encoding="utf-8")


def write_r8(runs: Path, label: str, case_id=CASE_ID, run=None) -> Path:
    d = runs / label
    write_json(d / "run.json", run or make_run(case_id=case_id))
    write_json(d / "page_texts.json", dict(make_store(), case_id=case_id))
    (d / "summary.txt").write_text("\n".join(R8_SUMMARY) + "\n", encoding="utf-8")
    (d / "run_case.log").write_text("2026-01-01 00:00:01 gpuread: exit 0 after 0.4 s\n"
                                    "2026-01-01 00:01:40 done; runner exit 0; input unchanged; total 100.0 s\n",
                                    encoding="utf-8")
    return d


def write_app_out(out: Path, data: Path, case_id=CASE_ID, run=None, summary=None, store=None) -> None:
    write_json(out / "run.json", run or make_run(case_id=case_id))
    (out / "summary.txt").write_text("\n".join(summary or app_summary()) + "\n", encoding="utf-8")
    write_json(data / "cache" / "pagetexts" / f"{case_id}.json", dict(store or make_store(), case_id=case_id))


class FakeRes:
    def __init__(self, lines, returncode=0, seconds=120.0, timed_out=False, interrupted=0, leftover=0):
        self.lines = [(float(i), ln) for i, ln in enumerate(lines)] if lines and not isinstance(lines[0], tuple) \
            else list(lines)
        self.returncode = returncode
        self.seconds = seconds
        self.timed_out = timed_out
        self.interrupted = interrupted
        self.leftover_killed = leftover

    def stats(self):
        return {"returncode": self.returncode, "seconds": self.seconds, "timed_out": self.timed_out,
                "peak_working_set_mb": 2000.0, "peak_commit_mb": 3000.0, "job_peak_process_commit_mb": 3100.0}


def ev(**kw) -> str:
    return json.dumps(dict({"v": 1}, **kw))


DONE_LINES = [ev(ev="hello"), ev(ev="card", state="ready", seconds=14.0),
              ev(ev="done", exit=0, code="ABCD-1234", lines=[["FAIL", "made-up line"]],
                 gpu={"cpu_calls": 0, "waits": 1, "waited_s": 12.5, "peak_alloc_gb": 2.4, "peak_reserved_gb": 2.8})]


class ParityTests(TmpCase):
    def test_page_texts_equal_and_text_only_change(self):
        rep = parity.compare_page_texts(make_store(), make_store())
        self.assertTrue(rep["equal"])
        self.assertEqual(rep["equal_pages"], 3)
        changed = make_store(texts=[TEXTS[0], TEXTS[1].replace("ONE", "0NE"), TEXTS[2]], sideways={2})
        rep = parity.compare_page_texts(make_store(), changed)
        self.assertFalse(rep["equal"])
        self.assertEqual([d["page"] for d in rep["differing"]], [2, 3])
        self.assertEqual(rep["differing"][0]["diff_chars"], 1)
        self.assertEqual(rep["differing"][1]["sideways"], [False, True])
        dumped = json.dumps(rep)
        for t in TEXTS + ["SAMPLE.pdf"]:
            self.assertNotIn(t, dumped)

    def test_page_texts_problems(self):
        self.assertIn("not found", parity.compare_page_texts(make_store(), None)["problems"][0])
        app = make_store(complete=False)
        app["files"][0]["sha256"] = "1" * 64
        rep = parity.compare_page_texts(make_store(), app)
        self.assertFalse(rep["equal"])
        self.assertIn("file 1: sha256 differs", rep["problems"])
        self.assertIn("the app's page-texts file is not complete", rep["problems"])

    def test_summary_ignores_reading_code_and_report_lines(self):
        rep = parity.compare_summary("\n".join(R8_SUMMARY), "\n".join(app_summary()))
        self.assertTrue(rep["equal"], rep)
        self.assertEqual(rep["lines_r8"], 7)

    def test_summary_verdict_change_names_the_line_not_the_text(self):
        lines = list(R8_SUMMARY)
        lines[5] = "FATHER NID (p2): FAIL — made-up reason"
        rep = parity.compare_summary("\n".join(R8_SUMMARY), "\n".join(app_summary(lines)))
        self.assertFalse(rep["equal"])
        self.assertEqual(len(rep["diffs"]), 1)
        self.assertIn("FATHER NID", rep["diffs"][0])
        self.assertIn("r8 FLAG, app FAIL", rep["diffs"][0])
        self.assertNotIn("made-up", rep["diffs"][0])

    def test_run_json_equal_and_changes(self):
        self.assertTrue(parity.compare_run(make_run(), make_run())["equal"])
        shuffled = make_run()
        shuffled["docs"][1]["findings"].reverse()
        shuffled["docs"][1]["findings"][0]["message"] = "other wording"
        self.assertTrue(parity.compare_run(make_run(), shuffled)["equal"], "order and wording are not decisions")
        cases = {"verdict": make_run(verdict2="FAIL"), "cross": make_run(cross_rule="names.mother"),
                 "missing": make_run(missing_text="BIRTH CERTIFICATE MISSING"), "notice": make_run(note_msg="x"),
                 "overall": make_run(overall="FLAG")}
        for what, app in cases.items():
            rep = parity.compare_run(make_run(), app)
            self.assertFalse(rep["equal"], what)
            dumped = json.dumps(rep)
            self.assertNotIn("BIRTH CERTIFICATE", dumped)
            self.assertNotIn("made-up", dumped)
        self.assertIn("doc 2 (father_nid): verdict r8 FLAG, app FAIL",
                      parity.compare_run(make_run(), cases["verdict"])["diffs"])
        self.assertIn("missing line 1: r8 MISSING, app MISSING (text differs)",
                      parity.compare_run(make_run(), cases["missing"])["diffs"])
        self.assertFalse(parity.compare_run(make_run(), None)["equal"])

    def test_looks(self):
        self.assertTrue(parity.compare_looks(make_run(), make_run())["equal"])
        for app, word in ((make_run(look="unclear"), "decision same vs unclear"),
                          (make_run(best="CAND B"), "best candidate differs"),
                          (make_run(dot=True), "dot None vs True")):
            rep = parity.compare_looks(make_run(), app)
            self.assertFalse(rep["equal"])
            self.assertIn(word, rep["diffs"][0])
            self.assertNotIn("CAND", json.dumps(rep))
        short = make_run()
        short["names_rereads"].pop()
        self.assertIn("looks: 2 in r8, 1 in the app", parity.compare_looks(make_run(), short)["diffs"])

    def _case(self, run_app=None, summary=None, store=None, returncode=0):
        runs = self.tmp / "runs"
        write_r8(runs, "test1_r8")
        case = parity.load_r8("test1_r8", runs)
        out, data = self.tmp / "app" / "out", self.tmp / "app" / "data"
        write_app_out(out, data, run=run_app, summary=summary, store=store)
        return parity.check_case(case, FakeRes(DONE_LINES, returncode=returncode), out, data)

    def test_check_case_equal_verdict_change_and_text_only_change(self):
        rep = self._case()
        self.assertTrue(rep["ok"], parity.detail_lines(rep))
        self.assertEqual(rep["code"], "ABCD-1234")
        self.assertEqual(rep["gpu"]["waits"], 1)
        self.assertIn("| OK", parity.case_line(rep))
        bad = self._case(run_app=make_run(verdict2="FAIL"))
        self.assertFalse(bad["ok"])
        self.assertTrue(any("MISS doc 2" in ln for ln in parity.detail_lines(bad)))
        text = self._case(store=make_store(texts=[TEXTS[0], TEXTS[1] + " X", TEXTS[2]]))
        self.assertTrue(text["ok"], "a page-text difference with equal verdicts does not fail (b)-(d)")
        self.assertFalse(text["page_texts"]["equal"])
        self.assertTrue(any("p2 (file 1 page 2)" in ln for ln in parity.detail_lines(text)))
        self.assertFalse(self._case(returncode=1)["ok"])

    def _fake_app(self):
        app = self.tmp / "app"
        app.mkdir(parents=True, exist_ok=True)
        (app / "Hangeul Document Checker.exe").write_bytes(b"MZ")
        return app

    def _fake_run_fn(self, verdict_change_for=None):
        calls = []

        def run_fn(cmd, timeout, *, what=None, env=None, log_dir=None, **kw):
            calls.append(cmd)
            data = Path(cmd[cmd.index("--data-dir") + 1])
            self.assertEqual(env.get("HDC_DATA_DIR"), str(data))
            self.assertNotIn("HDC_TEST_NO_CARD", env)
            if "--cli" in cmd:
                out = Path(cmd[cmd.index("--out") + 1])
                label = out.parent.name
                run = make_run(verdict2="FAIL") if label == verdict_change_for else make_run()
                write_app_out(out, data, run=run)
            else:
                reports = Path(cmd[cmd.index("--report-root") + 1])
                folder = reports / "SAMPLE KLP 2026-01-02 1200"
                write_app_out(folder, data)
                write_json(Path(cmd[cmd.index("--auto-result") + 1]), {"exit": 0, "code": "ABCD-1234",
                                                                       "out": str(folder)})
            return FakeRes(DONE_LINES)
        return run_fn, calls

    def _main(self, run_fn, *args):
        runs = self.tmp / "runs"
        for label, _ in parity.CASES:
            write_r8(runs, label)
        buf = io.StringIO()
        with mock.patch.object(cardrun, "APPTEST", self.tmp / "apptest"), \
                mock.patch.object(cardrun, "BUILDS", self.tmp / "builds"), contextlib.redirect_stdout(buf):
            rc = parity.main(["--app-dir", str(self._fake_app()), *args], run_fn=run_fn, runs=runs)
        return rc, buf.getvalue()

    def test_main_all_four_pass_and_this_pc_code(self):
        run_fn, calls = self._fake_run_fn()
        rc, out = self._main(run_fn)
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(calls), 5)
        self.assertIn("THIS_PC_CODE ABCD-1234", out)
        self.assertIn("= the --cli code", out)
        self.assertIn("P1 PASS", out)
        for t in TEXTS + ["made-up", "CAND", "C:\\"]:
            self.assertNotIn(t, out)
        latest = json.loads((self.tmp / "apptest" / "local" / "p1-latest.json").read_text(encoding="utf-8"))
        self.assertTrue(latest["passed"])
        for t in TEXTS + ["made-up", "CAND"]:
            self.assertNotIn(t, json.dumps(latest))

    def test_main_one_verdict_change_fails(self):
        run_fn, _ = self._fake_run_fn(verdict_change_for="owner_f9_r8")
        rc, out = self._main(run_fn)
        self.assertEqual(rc, 1)
        self.assertIn("MISS doc 2 (father_nid): verdict r8 FLAG, app FAIL", out)
        self.assertIn("P1 FAIL", out)

    def test_main_subset_is_not_a_build_pass(self):
        run_fn, _ = self._fake_run_fn()
        rc, out = self._main(run_fn, "--cases", "test1_r8", "--no-auto")
        self.assertEqual(rc, 1)
        self.assertIn("not all four were run", out)

    def test_main_input_stand_in_for_a_gone_input(self):
        """r8's own input folder is gone: --input LABEL=PATH checks a stand-in; the path is never printed."""
        stand_in = self.tmp / "zipsrc" / "made-up folder"
        stand_in.mkdir(parents=True)
        run_fn, calls = self._fake_run_fn()
        rc, out = self._main(run_fn, "--input", f"owner_f3_r8={stand_in}")
        self.assertEqual(rc, 0, out)
        cli_inputs = {Path(c[c.index("--out") + 1]).parent.name: c[c.index("--cli") + 1] for c in calls if "--cli" in c}
        self.assertEqual(cli_inputs["owner_f3_r8"], str(stand_in))
        self.assertNotEqual(cli_inputs["owner_f9_r8"], str(stand_in))
        self.assertIn("stand-in input for owner_f3_r8", out)
        self.assertNotIn("made-up folder", out)
        latest = json.loads((self.tmp / "apptest" / "local" / "p1-latest.json").read_text(encoding="utf-8"))
        self.assertEqual(latest["stand_in_inputs"], ["owner_f3_r8"])
        self.assertTrue(next(c for c in latest["cases"] if c["label"] == "owner_f3_r8")["stand_in_input"])
        self.assertNotIn("made-up folder", json.dumps(latest))
        for bad in (["--input", "owner_f3_r8"], ["--input", f"nope={stand_in}"],
                    ["--input", "owner_f3_r8=relative\\x"], ["--input", f"owner_f3_r8={stand_in / 'gone'}"]):
            rc, out = self._main(run_fn, *bad)
            self.assertEqual(rc, 2, (bad, out))
            self.assertIn("P1: cannot start: ValueError", out)

    def test_a_stand_in_with_another_case_id_fails_run_json(self):
        rep = self._case(run_app=make_run(case_id="F-00000000000000ff"))
        self.assertFalse(rep["ok"])
        self.assertIn("case id differs", rep["run"]["diffs"])

    def test_main_stops_cleanly_without_a_card_slot_or_approval(self):
        for exc in (TimeoutError("GPU not available"), cardrun.NotApproved("Q-O1"), cardrun.Busy("one")):
            def run_fn(cmd, timeout, **kw):
                raise exc
            rc, out = self._main(run_fn)
            self.assertEqual(rc, 2, out)
            self.assertIn(f"P1: stopped: {type(exc).__name__}", out)


# ================================================================================================== politeness

def wait_ev(state, **kw):
    return dict({"v": 1, "ev": "gpu_wait", "state": state, "why": "lock"}, **kw)


def page(n, of=14, where="card"):
    return {"v": 1, "ev": "page", "done": n, "of": of, "seconds": 1.0, "where": where}


DONE = {"v": 1, "ev": "done", "exit": 0, "code": "ABCD-1234"}


def s2_events(reserved=180, busy_at=None, end_at=74.0, bot=False):
    evs = [(0.5, {"v": 1, "ev": "hello"}), (5.0, {"v": 1, "ev": "card", "state": "ready"}),
           (6.0, {"v": 1, "ev": "stage", "key": "firstread", "state": "start"}),
           (10.0, page(1)), (11.0, page(2)), (12.0, page(3)),
           (12.6, wait_ev("start", lock="bot" if bot else "other", where="read", reserved_mb=reserved)),
           (14.6, wait_ev("tick", waited_s=2.0))]
    if busy_at is not None:
        evs.append((busy_at, page(4)))
    evs += [(end_at, wait_ev("end", waited_s=end_at - 12.6)), (end_at + 1, page(4)), (200.0, DONE)]
    return sorted(evs, key=lambda x: x[0])


MARKS2 = {"t_lock": 12.1, "t_ws": 12.6, "t_rm": 72.6, "t_cancel": None, "bot_seen": 0, "gave_up": False}


class FakeCtl:
    def __init__(self):
        self.t = 0.0
        self.written = []

    def elapsed(self):
        return self.t

    def write(self, text):
        self.written.append(text)
        return True


class PolitenessTests(TmpCase):
    refs = {"P1": "ABCD-1234", "scenario 6": "ABCD-1234"}

    def test_scenario_1(self):
        evs = [(0.5, {"v": 1, "ev": "hello"}), (1.2, wait_ev("start", lock="other", where="start", reserved_mb=0)),
               (3.2, wait_ev("tick", waited_s=2.0)), (61.6, wait_ev("end", waited_s=60.4)),
               (75.0, {"v": 1, "ev": "card", "state": "ready"}), (200.0, DONE)]
        marks = {"t_lock": 0.0, "t_ws": 1.2, "t_rm": 61.2, "t_cancel": None, "bot_seen": 0, "gave_up": False}
        o = politeness.judge(1, evs, marks, 0, "ABCD-1234", self.refs)
        self.assertTrue(o.passed, [(c.name, c.ok, c.detail) for c in o.checks])
        early = evs[:2] + [(30.0, {"v": 1, "ev": "card", "state": "ready"})] + evs[2:]
        o = politeness.judge(1, early, marks, 0, "ABCD-1234", self.refs)
        self.assertFalse(o.passed)
        self.assertIn("no card start while the lock was there", [c.name for c in o.checks if not c.ok])
        o = politeness.judge(1, evs, marks, 0, "EEEE-0000", self.refs)
        self.assertFalse(o.passed)

    def test_scenarios_2_and_3(self):
        o = politeness.judge(2, s2_events(), MARKS2, 0, "ABCD-1234", self.refs)
        self.assertTrue(o.passed, [(c.name, c.ok, c.detail) for c in o.checks])
        self.assertFalse(politeness.judge(2, s2_events(reserved=300), MARKS2, 0, "ABCD-1234", self.refs).passed)
        self.assertFalse(politeness.judge(3, s2_events(busy_at=30.0), MARKS2, 0, "ABCD-1234", self.refs).passed)
        late = politeness.judge(2, s2_events(end_at=90.0), MARKS2, 0, "ABCD-1234", self.refs)
        self.assertFalse(late.passed)
        self.assertEqual([late.status(c) for c in late.checks if not c.ok], ["FAIL"])
        bot = politeness.judge(2, s2_events(end_at=90.0), dict(MARKS2, bot_seen=3), 0, "ABCD-1234", self.refs)
        self.assertTrue(bot.passed, "a timing miss while the real bot ran is reported, not failed")
        self.assertEqual([bot.status(c) for c in bot.checks if not c.ok], ["bot active"])
        self.assertFalse(politeness.judge(2, s2_events(reserved=300), dict(MARKS2, bot_seen=3), 0, "ABCD-1234",
                                          self.refs).passed, "a non-timing miss fails even while the bot ran")

    def test_wait_already_running_for_the_bot_counts(self):
        evs = [(0.5, {"v": 1, "ev": "hello"}), (10.0, page(1)), (11.0, page(2)),
               (11.5, wait_ev("start", lock="bot", where="page", reserved_mb=150)),
               (40.0, wait_ev("tick", lock="bot", waited_s=28.5)), (73.0, wait_ev("end", waited_s=61.5)),
               (74.0, page(3)), (200.0, DONE)]
        marks = dict(MARKS2, t_lock=12.0, t_ws=12.0, t_rm=72.0)
        o = politeness.judge(3, evs, marks, 0, "ABCD-1234", self.refs)
        self.assertTrue(o.bot_active)
        self.assertTrue(o.passed, [(c.name, c.ok, c.detail) for c in o.checks])

    def test_scenario_4_cancel(self):
        evs = [(10.0, page(1)), (11.0, page(2)), (12.0, page(3)),
               (12.5, wait_ev("start", lock="other", where="read", reserved_mb=100)),
               (14.5, wait_ev("tick", waited_s=2.0)), (15.0, {"v": 1, "ev": "cancelled"})]
        marks = dict(MARKS2, t_lock=12.1, t_ws=12.5, t_rm=None, t_cancel=14.5)
        o = politeness.judge(4, evs, marks, 4, None, {}, seconds=15.5, leftover=0)
        self.assertTrue(o.passed, [(c.name, c.ok, c.detail) for c in o.checks])
        o = politeness.judge(4, evs[:-1], marks, 1, None, {}, seconds=15.5)
        self.assertFalse(o.passed)
        o = politeness.judge(4, evs, marks, 4, None, {}, seconds=15.5, leftover=1)
        self.assertFalse(o.passed)
        resume = [(1.0, {"v": 1, "ev": "stage", "key": "firstread", "state": "start"}), (3.0, page(1, of=9)),
                  (100.0, DONE)]
        self.assertTrue(all(c.ok for c in politeness.judge_resume(14, 5, resume, 0)))
        self.assertFalse(all(c.ok for c in politeness.judge_resume(14, 5, [(3.0, page(1, of=14))], 0)))
        self.assertFalse(all(c.ok for c in politeness.judge_resume(14, 2, [(3.0, page(1, of=12))], 0)))
        self.assertEqual(politeness.saved_pages(make_store()), 3)

    def test_scenarios_5_and_6(self):
        evs = [(0.5, {"v": 1, "ev": "hello"}), (100.0, DONE)]
        self.assertTrue(politeness.judge(5, evs, dict(MARKS2), 0, "ABCD-1234", self.refs).passed)
        waited = evs[:1] + [(1.0, wait_ev("start", lock="other", where="start"))] + evs[1:]
        self.assertFalse(politeness.judge(5, waited, dict(MARKS2), 0, "ABCD-1234", self.refs).passed)
        bot = evs[:1] + [(1.0, wait_ev("start", lock="bot", where="start"))] + evs[1:]
        o = politeness.judge(5, bot, dict(MARKS2), 0, "ABCD-1234", self.refs)
        self.assertTrue(o.passed and o.bot_active)
        self.assertTrue(politeness.judge(6, evs, {}, 0, "ABCD-1234", {"P1": "ABCD-1234"}).passed)
        self.assertFalse(politeness.judge(6, evs, {}, 0, "EEEE-0000", {"P1": "ABCD-1234"}).passed)
        self.assertTrue(politeness.judge(6, evs, {}, 0, "EEEE-0000", {"P1": None}).passed)

    # -- scenario 7 (G1 review finding 1): the lock during the runner's processor-only "bot" stage
    def s7_events(self, reserved=110):
        return [(0.5, {"v": 1, "ev": "hello"}), (5.0, {"v": 1, "ev": "card", "state": "ready"}),
                (40.0, {"v": 1, "ev": "stage", "key": "names", "state": "start"}),
                (49.0, {"v": 1, "ev": "stage", "key": "names", "state": "end", "seconds": 9.0}),
                (50.0, {"v": 1, "ev": "stage", "key": "bot", "state": "start"}),
                (80.0, {"v": 1, "ev": "stage", "key": "bot", "state": "end", "seconds": 30.0}),
                (80.1, {"v": 1, "ev": "stage", "key": "bank", "state": "start"}),
                (80.2, wait_ev("start", lock="other", where="stage", reserved_mb=reserved)),
                (82.2, wait_ev("tick", waited_s=2.0)), (110.5, wait_ev("end", waited_s=30.3)), (200.0, DONE)]

    MARKS7 = {"t_lock": 50.2, "t_ws": 80.2, "t_rm": 110.2, "t_cancel": None, "bot_seen": 0, "gave_up": False,
              "free_hold": [5600, 5550, None, 5580], "free_after": 6100}

    def test_scenario_7(self):
        o = politeness.judge(7, self.s7_events(), dict(self.MARKS7), 0, "ABCD-1234", self.refs)
        self.assertTrue(o.passed, [(c.name, c.ok, c.detail) for c in o.checks])
        self.assertEqual((o.facts["footprint_mb"], o.facts["free_hold_min"], o.facts["free_after"]), (550, 5550, 6100))
        cached = dict(self.MARKS7, free_hold=[3000, 2900, 2950])         # the old app: about 3 GB cached
        o = politeness.judge(7, self.s7_events(), cached, 0, "ABCD-1234", self.refs)
        self.assertFalse(o.passed)
        self.assertEqual([c.name for c in o.checks if not c.ok],
                         [f"the app held <= {politeness.APP_IDLE_MAX_MB} MB of card memory while the lock existed"])
        bot = politeness.judge(7, self.s7_events(), dict(cached, bot_seen=2), 0, "ABCD-1234", self.refs)
        self.assertTrue(bot.passed, "the real bot changes free memory: reported, not failed")
        unread = politeness.judge(7, self.s7_events(), dict(self.MARKS7, free_hold=[None], free_after=None), 0,
                                  "ABCD-1234", self.refs)
        self.assertFalse(unread.passed)
        self.assertIn("not measured", [c.detail for c in unread.checks if not c.ok])
        self.assertFalse(politeness.judge(7, self.s7_events(reserved=900), dict(self.MARKS7), 0, "ABCD-1234",
                                          self.refs).passed)
        self.assertFalse(politeness.judge(7, self.s7_events(), dict(self.MARKS7, t_lock=None), 0, "ABCD-1234",
                                          self.refs).passed)
        self.assertFalse(politeness.judge(7, self.s7_events(), dict(self.MARKS7), 0, "EEEE-0000", self.refs).passed)
        self.assertEqual(politeness.card_footprint({"free_hold": [True, 4000], "free_after": 4500}), 500)
        self.assertIsNone(politeness.card_footprint({"free_hold": [4000], "free_after": None}))

    def test_controller_bot_stage_samples_free_memory(self):
        lock = politeness.FakeLock(self.tmp / "fakelock" / "auto_verify.lock")
        reads = iter([5600, 5550, 5580, 5590, 6000, 6100, None])
        slept = []
        c = politeness.Controller(7, lock, hold_s=6.0, bot_active=lambda: False, free_fn=lambda: next(reads),
                                  sleep=slept.append)
        ctl = FakeCtl()
        c.before_start()
        c.on_line(1.0, json.dumps({"v": 1, "ev": "stage", "key": "names", "state": "start"}), ctl)
        self.assertFalse(lock.exists())
        ctl.t = 2.05
        c.on_line(2.0, json.dumps({"v": 1, "ev": "stage", "key": "bot", "state": "start"}), ctl)
        self.assertTrue(lock.exists())
        self.assertEqual(c.marks["t_lock"], 2.05)
        for t in (2.5, 3.0, 4.5, 6.5, 8.0):
            c.on_tick(t, ctl)
        self.assertTrue(lock.exists())
        c.on_tick(8.1, ctl)
        self.assertFalse(lock.exists())
        self.assertEqual((c.marks["t_rm"], c.marks["free_hold"]), (8.1, [5600, 5550, 5580]))
        c.finish()
        self.assertEqual(c.marks["free_after"], 6100)                      # the most of three reads after the end
        self.assertEqual(slept, [politeness.SETTLE_S] * 2)
        self.assertFalse(c.marks["gave_up"])
        self.assertEqual(politeness.ORDER, (6, 1, 2, 3, 7, 5, 4))

    def test_controller_page3_hold_and_removal(self):
        lock = politeness.FakeLock(self.tmp / "fakelock" / "auto_verify.lock")
        c = politeness.Controller(2, lock, hold_s=5.0, bot_active=lambda: False)
        ctl = FakeCtl()
        c.before_start()
        self.assertFalse(lock.exists())
        c.on_line(1.0, ev(ev="stage", key="firstread", state="start"), ctl)
        for i, t in enumerate((2.0, 3.0), start=1):
            c.on_line(t, json.dumps(page(i, where=None)), ctl)
        self.assertFalse(lock.exists())
        ctl.t = 4.05
        c.on_line(4.0, json.dumps(page(3, where=None)), ctl)
        self.assertTrue(lock.exists())
        self.assertEqual(c.marks["t_lock"], 4.05)
        c.on_line(4.5, json.dumps(wait_ev("start", lock="other", where="read", reserved_mb=10)), ctl)
        self.assertEqual(c.marks["t_ws"], 4.5)
        c.on_tick(9.4, ctl)
        self.assertTrue(lock.exists())
        c.on_tick(9.5, ctl)
        self.assertFalse(lock.exists())
        self.assertEqual(c.marks["t_rm"], 9.5)
        c.finish()
        self.assertFalse(c.marks["gave_up"])

    def test_controller_cancel_stale_and_giveup(self):
        lock = politeness.FakeLock(self.tmp / "fakelock" / "auto_verify.lock")
        c = politeness.Controller(4, lock, bot_active=lambda: False)
        ctl = FakeCtl()
        for i in (1, 2, 3):
            c.on_line(float(i), json.dumps(page(i)), ctl)
        c.on_line(3.5, json.dumps(wait_ev("start", lock="other", where="page", reserved_mb=10)), ctl)
        c.on_tick(4.0, ctl)
        self.assertEqual(ctl.written, [])
        c.on_tick(5.6, ctl)
        self.assertEqual(ctl.written, ["cancel\n"])
        self.assertEqual(c.marks["t_cancel"], 5.6)
        c.finish()
        self.assertFalse(lock.exists())
        stale = politeness.Controller(5, lock)
        stale.before_start()
        self.assertGreater(time.time() - lock.path.stat().st_mtime, 4.9 * 3600)
        stale.finish()
        self.assertFalse(lock.exists())
        first = politeness.Controller(1, lock, giveup_s=10.0, bot_active=lambda: True)
        first.before_start()
        self.assertTrue(lock.exists())
        first.on_tick(11.0, ctl)
        self.assertFalse(lock.exists())
        self.assertTrue(first.marks["gave_up"])
        self.assertEqual(first.marks["bot_seen"], 1)

    def test_worker_job(self):
        case = parity.R8Case("test1_r8", "", self.tmp, "C:/made/up/SAMPLE.pdf", "KLP", "2026-01-02", CASE_ID,
                             "bundle")
        job = politeness.worker_job(case, self.tmp / "out", self.tmp / "data", [self.tmp / "fake.lock"])
        self.assertEqual(job["schema"], "hdc-job/1")
        self.assertEqual(job["gpu"], {"lock_paths": [str(self.tmp / "fake.lock")], "index": 0, "cap_gb": 3.0,
                                      "need_free_mb": 4096})
        self.assertEqual(job["cache_root"], str(self.tmp / "data" / "cache"))
        self.assertEqual(len(job["job"]), 32)
        locks = politeness.default_locks(self.tmp / "fake.lock")
        self.assertEqual(locks[-1], self.tmp / "fake.lock")


# ================================================================================================== timing

class TimingTests(TmpCase):
    def test_stats_and_log(self):
        s = timing.stats([4.0, 2.0, 3.0, None])
        self.assertEqual((s["n"], s["mean"], s["median"], s["max"]), (3, 3.0, 3.0, 4.0))
        self.assertEqual(timing.stats([])["n"], 0)
        f = timing.parse_run_case_log("x gpuread: exit 0 after 0.4 s\ny done; runner exit 0; total 112.4 s\n")
        self.assertEqual(f, {"first_read_s": 0.4, "total_s": 112.4})

    def _events(self, p1=True):
        evs = [(1.0, {"v": 1, "ev": "hello"}),
               (15.0, {"v": 1, "ev": "card", "state": "ready", "seconds": 14.2, "torch_s": 6.1, "total_mb": 8151})]
        if p1:
            evs += [(16.0, {"v": 1, "ev": "stage", "key": "firstread", "state": "start"}),
                    (18.0, page(1, of=3)), (21.0, dict(page(2, of=3), seconds=3.0)),
                    (25.0, dict(page(3, of=3), seconds=4.0)),
                    (30.0, {"v": 1, "ev": "stage", "key": "firstread", "state": "end", "seconds": 14.0})]
        evs += [(130.0, {"v": 1, "ev": "done", "exit": 0, "gpu": {"cpu_calls": 0, "peak_alloc_gb": 2.4,
                                                                  "peak_reserved_gb": 2.8, "waits": 0,
                                                                  "waited_s": 0.0}})]
        return evs

    def test_summarize_and_targets(self):
        out = self.tmp / "out"
        write_json(out / "checkfacts.json", {"gpu": {"card_calls": 120, "first_read": {"model_load_s": 3.0}}})
        write_json(out / "run.json", {"timings": {"a": 1.5, "b": 2.5, "bot": 1.0},
                                      "reading": {"engine_stats": {"ocr_calls": 44, "ocr_seconds": 151.5,
                                                                   "cache_hits": 0}}})
        st = {"returncode": 0, "seconds": 131.0, "peak_working_set_mb": 3000.0, "peak_commit_mb": 4000.0,
              "job_peak_process_commit_mb": 4100.0}
        s = timing.summarize(self._events(), st, out)
        self.assertEqual(s["phase1"]["pages"], 3)
        self.assertEqual(s["phase2_s"], 100.0)
        self.assertEqual(s["start"]["card_ready"], {"seconds": 14.2, "torch_s": 6.1})
        self.assertEqual(s["calls"], {"card": 120, "processor": 0})
        self.assertEqual(s["memory"]["peak_commit_mb"], 4100.0)
        self.assertEqual(s["runner_timings_s"], 5.0)
        self.assertEqual(s["engine"], {"ocr_calls": 44, "ocr_s": 151.5, "cache_hits": 0, "bot_stage_s": 1.0})
        self.assertEqual(timing.engine_stats(None), {"ocr_calls": None, "ocr_s": None, "cache_hits": None,
                                                     "bot_stage_s": None})
        r8 = {"total_s": 100.0, "reused": True, "page_texts_s": 200.0}
        self.assertTrue(all(ok for _, ok, _ in timing.targets(s, r8, recheck=False)))
        # the orchestrator's P5 target: working set <= 3.5 GB fails; commit up to 6 GB passes (G1: 5.4 GB commit)
        committed = dict(s, memory={"peak_working_set_mb": 1900.0, "peak_commit_mb": 5500.0})
        self.assertTrue(all(ok for _, ok, _ in timing.targets(committed, r8, False)))
        heavy = dict(s, memory={"peak_working_set_mb": 3700.0, "peak_commit_mb": 4700.0})
        self.assertFalse(timing.targets(heavy, r8, False)[0][1])
        self.assertIn("working set", timing.targets(heavy, r8, False)[0][0])
        over = dict(s, memory={"peak_working_set_mb": 1900.0, "peak_commit_mb": 6200.0})
        self.assertFalse(timing.targets(over, r8, False)[-1][1])
        self.assertIn("commit <= 6.0 GB", timing.targets(over, r8, False)[-1][0])
        unmeasured = dict(s, memory={"peak_working_set_mb": None, "peak_commit_mb": None})
        self.assertFalse(timing.targets(unmeasured, r8, False)[0][1])
        hot = dict(s, card={"peak_alloc_gb": 2.9, "peak_reserved_gb": 3.1})
        self.assertFalse(timing.targets(hot, r8, False)[1][1])
        slow = dict(s, total_s=400.0)
        self.assertFalse(timing.targets(slow, r8, False)[2][1])
        rc = timing.targets(s, r8, recheck=True)
        self.assertFalse(rc[3][1], "a re-check that read pages on the card loaded the first-read model")
        self.assertTrue(rc[2][1])
        self.assertFalse(timing.targets(dict(s, total_s=170.0), r8, recheck=True)[2][1],
                         "the re-check is held to r8's run_case total + 60 s (no first-read allowance)")
        s2 = timing.summarize(self._events(p1=False), dict(st, seconds=120.0), out)
        self.assertEqual(s2["phase1"]["pages"], 0)
        rc2 = timing.targets(s2, r8, recheck=True)
        self.assertFalse(rc2[3][1], "a first-read model load of 3 s was reported")
        write_json(out / "checkfacts.json", {"gpu": {"card_calls": 120, "first_read": {"model_load_s": 0.0}}})
        s3 = timing.summarize(self._events(p1=False), dict(st, seconds=120.0), out)
        self.assertTrue(all(ok for _, ok, _ in timing.targets(s3, r8, recheck=True)))

    def test_main_with_fakes(self):
        runs = self.tmp / "runs"
        for label, _ in parity.CASES:
            write_r8(runs, label)
        app = self.tmp / "app"
        app.mkdir()
        (app / "Hangeul Document Checker.exe").write_bytes(b"MZ")
        seen = []

        def run_fn(cmd, timeout, *, what=None, env=None, log_dir=None, **kw):
            seen.append(Path(cmd[cmd.index("--data-dir") + 1]))
            res = FakeRes([json.dumps(e) for _, e in self._events(p1=len(seen) <= 4)], seconds=120.0)
            return res
        buf = io.StringIO()
        with mock.patch.object(cardrun, "APPTEST", self.tmp / "apptest"), contextlib.redirect_stdout(buf):
            rc = timing.main(["--app-dir", str(app)], run_fn=run_fn, runs=runs)
        self.assertEqual(len(seen), 5)
        self.assertEqual(seen[4], seen[3], "the re-check reuses CHECK_0001's DATA")
        self.assertEqual(rc, 0, buf.getvalue())
        self.assertIn("test1_r8 re-check", buf.getvalue())

    def test_main_input_stand_in(self):
        runs = self.tmp / "runs"
        for label, _ in parity.CASES:
            write_r8(runs, label)
        app = self.tmp / "app"
        app.mkdir()
        (app / "Hangeul Document Checker.exe").write_bytes(b"MZ")
        stand_in = self.tmp / "stand-in"
        stand_in.mkdir()
        inputs = []

        def run_fn(cmd, timeout, *, what=None, env=None, log_dir=None, **kw):
            inputs.append(cmd[cmd.index("--cli") + 1])
            return FakeRes([json.dumps(e) for _, e in self._events(p1=len(inputs) <= 4)], seconds=120.0)
        buf = io.StringIO()
        with mock.patch.object(cardrun, "APPTEST", self.tmp / "apptest"), contextlib.redirect_stdout(buf):
            rc = timing.main(["--app-dir", str(app), "--cases", "owner_f9_r8", "--input",
                              f"owner_f9_r8={stand_in}"], run_fn=run_fn, runs=runs)
        self.assertEqual(rc, 0, buf.getvalue())
        self.assertEqual(inputs, [str(stand_in)])
        self.assertNotIn(str(stand_in), buf.getvalue())


# ================================================================================================== 8 b scan

def code_bytes(src: str, filename: str) -> bytes:
    return marshal.dumps(compile(src, filename, "exec"))


class FakeArchives:
    def __init__(self, pyz):
        self.pyz = pyz

    def pkg_entries(self, exe):
        return [("hdc_entry", "s", code_bytes("import x\n", "hdc_entry.py")), ("PYZ.pyz", "z", b""),
                ("pyi-contents-directory _internal", "o", b"")]

    def pyz_modules(self, exe, name):
        return list(self.pyz)


GATE_SRC = 'BOT_LOCK_DEFAULT = r"C:\\Hangeul\\BOT\\data\\verification\\auto_verify.lock"\n'


def base_pyz(extra=()):
    mods = [("src", 1, code_bytes("", "src/__init__.py")),
            ("src.verify", 1, code_bytes("", "src/verify/__init__.py")),
            ("src.verify.runner", 1, code_bytes("", "src/verify/runner/__init__.py")),
            ("src.verify.runner.app", 1, code_bytes("", "src/verify/runner/app/__init__.py")),
            ("src.verify.runner.app.cardgate", 0, code_bytes(GATE_SRC, "src/verify/runner/app/cardgate.py")),
            ("src.verify.runner.engine", 0, code_bytes("def f():\n    return 'clean'\n", "src/verify/runner/engine.py")),
            ("numpy", 1, code_bytes("", "numpy/__init__.py"))]
    return mods + list(extra)


STAGE_LINES = ["# made-up stage list", "src\\__init__.py", "src/verify/__init__.py",
               "C:\\x\\stage\\src\\verify\\runner\\__init__.py\tsha", "src/verify/runner/app/__init__.py -> stage",
               "src/verify/runner/app/cardgate.py | 123", "src/verify/runner/engine.py", "glue_numpy.py",
               "engine/easyocr/model/craft_mlt_25k.pth"]
ALLOW = ["# module or file | string | reason",
         "src.verify.runner.app.cardgate | c:\\hangeul\\bot\\data\\verification\\auto_verify.lock | the default "
         "lock path (R2.2)",
         "_internal/torch/* | .venv | torch's own code mentions .venv (made-up line for the test)"]


class ScanBundleTests(TmpCase):
    def make_tree(self, allow=ALLOW, stage=STAGE_LINES):
        app = self.tmp / "dist" / "Hangeul Document Checker"
        (app / "_internal" / "torch" / "lib").mkdir(parents=True)
        (app / "Hangeul Document Checker.exe").write_bytes(b"MZ" + b"\0" * 200)
        (app / "_internal" / "torch" / "lib" / "clean.dll").write_bytes(bytes(range(256)) * 8)
        (app / "_internal" / "torch" / "utils.py").write_text("# path like .venv/lib\n", encoding="utf-8")
        with zipfile.ZipFile(app / "_internal" / "base_library.zip", "w") as z:
            z.writestr("encodings/aliases.pyc", b"\0" * 16 + code_bytes("X = 'ok'\n", "encodings/aliases.py"))
        (self.tmp / "scan_allow.txt").write_text("\n".join(allow) + "\n", encoding="utf-8")
        (self.tmp / "STAGE_LIST.txt").write_text("\n".join(stage) + "\n", encoding="utf-8")
        return app

    def scan(self, app, pyz=None, chunk=64):
        return scan_bundle.scan(app, self.tmp / "scan_allow.txt", self.tmp / "STAGE_LIST.txt",
                                source=FakeArchives(pyz or base_pyz()), chunk=chunk)

    def test_clean_tree_passes_with_the_one_allowed_line(self):
        rep = self.scan(self.make_tree())
        self.assertTrue(rep.passed, (rep.failures, [h.__dict__ for h in rep.unallowed]))
        gate = [h for h in rep.hits if h.key == "src.verify.runner.app.cardgate"]
        self.assertEqual(len(gate), 1)
        self.assertEqual(gate[0].allowed_by, 2)
        self.assertEqual(gate[0].co_filename, "src/verify/runner/app/cardgate.py")
        self.assertTrue(any(h.key == "_internal/torch/utils.py" and h.allowed_by == 3 for h in rep.hits))
        self.assertIn("src.verify.runner.engine", rep.modules)

    def test_utf16_hit_across_a_chunk_boundary_is_found_once(self):
        app = self.make_tree()
        blob = b"\x01" * 50 + "JaRvIs".encode("utf-16-le") + b"\x02" * 50
        (app / "_internal" / "some.dll").write_bytes(blob)
        rep = self.scan(app, chunk=64)
        bad = rep.unallowed
        self.assertEqual(len(bad), 1)
        self.assertEqual((bad[0].key, bad[0].word, bad[0].encoding, bad[0].count),
                         ("_internal/some.dll", "jarvis", "utf-16le", 1))
        self.assertFalse(rep.passed)
        hits = scan_bundle.find_in_stream(io.BytesIO(b"x" * 60 + b"PRIVATE-runner" + b"y" * 60),
                                          scan_bundle.PATTERNS, chunk=64)
        self.assertEqual(hits[("private-runner", "utf-8")]["count"], 1)

    def test_pyz_hit_names_module_and_is_masked_in_print(self):
        pyz = base_pyz([("src.verify.runner.leak", 0, code_bytes(
            'P = "C:\\\\Hangeul\\\\VERIFIED STUDENT DOCUMENTS\\\\SAMPLE\\\\NAME ONE.pdf"\n',
            "src/verify/runner/leak.py"))])
        stage = STAGE_LINES + ["src/verify/runner/leak.py"]
        rep = self.scan(self.make_tree(stage=stage), pyz=pyz)
        bad = [h for h in rep.unallowed if h.key == "src.verify.runner.leak"]
        self.assertEqual(len(bad), 1)
        self.assertEqual(bad[0].co_filename, "src/verify/runner/leak.py")
        lines = []
        scan_bundle.print_report(rep, lines.append)
        printed = "\n".join(lines) + json.dumps(rep.to_json())
        self.assertIn("VERIFIED STUDENT DOCUMENTS\\...", printed)
        self.assertNotIn("NAME ONE", printed)
        self.assertNotIn("SAMPLE\\", printed)

    def test_our_code_may_allow_only_the_lock(self):
        allow = ALLOW + ["src.verify.runner.engine | jarvis | not allowed"]
        rep = self.scan(self.make_tree(allow=allow))
        self.assertTrue(any("our code may allow only" in f for f in rep.failures))
        self.assertTrue(any("exactly one is allowed" in f for f in rep.failures))
        lines, errors = scan_bundle.load_allow(self._write("bad.txt", "x | y\n"))
        self.assertEqual(lines, [])
        self.assertEqual(len(errors), 1)

    def _write(self, name, text):
        p = self.tmp / name
        p.write_text(text, encoding="utf-8")
        return p

    def test_stage_list_must_equal_the_src_modules(self):
        stage = [s for s in STAGE_LINES if "engine.py" not in s] + ["src/verify/runner/extra.py"]
        app = self.make_tree(stage=stage)
        rep = self.scan(app)
        self.assertIn("in the bundle but not staged: src.verify.runner.engine", rep.failures)
        self.assertIn("staged but not in the bundle: src.verify.runner.extra", rep.failures)
        rep = scan_bundle.scan(app, self.tmp / "scan_allow.txt", None, source=FakeArchives(base_pyz()))
        self.assertTrue(any("STAGE_LIST.txt not given" in f for f in rep.failures))
        mods = scan_bundle.stage_modules(self.tmp / "STAGE_LIST.txt")
        self.assertNotIn("glue_numpy", mods)
        self.assertIn("src.verify.runner", mods)
        self.assertIn("src", mods)

    def test_left_out_parts_and_excluded_modules(self):
        app = self.make_tree()
        (app / "_internal" / "PySide6").mkdir()
        (app / "_internal" / "PySide6" / "QtCore.pyd").write_bytes(b"MZ")
        (app / "_internal" / "onnxruntime" / "capi").mkdir(parents=True)
        (app / "_internal" / "onnxruntime" / "capi" / "DirectML.dll").write_bytes(b"MZ")
        (app / "_internal" / "torch" / "include").mkdir()
        (app / "_internal" / "torch" / "include" / "x.h").write_text("int x;\n", encoding="utf-8")
        (app / "_internal" / "engine").mkdir()
        (app / "_internal" / "engine" / "craft_mlt_25k.onnx").write_bytes(b"\0")
        (app / "_internal" / "tests").mkdir()
        (app / "_internal" / "notes.txt").write_text("telegram and supabase", encoding="utf-8")
        pyz = base_pyz([("torch.utils.tensorboard", 1, code_bytes("", "t.py"))])
        rep = self.scan(app, pyz=pyz)
        f = "\n".join(rep.failures)
        for want in ("excluded module present: PySide6", "excluded module present: onnxruntime",
                     "excluded module present: torch.utils.tensorboard", "DirectML.dll", "torch/include",
                     "x.h", "craft_mlt_25k.onnx"):
            self.assertIn(want, f)
        self.assertEqual(sorted({h.word for h in rep.advisory}), ["supabase", "telegram"])
        self.assertTrue(any("'tests' folder" in n for n in rep.notes))

    def test_task_f_stage_list_format(self):
        sha = "0" * 64
        p = self._write("STAGE_F.txt", "\n".join([
            "# STAGE_LIST.txt - made-up", "# columns (tab-separated): stage path, module, group, bytes, sha256",
            f"src/__init__.py\tsrc\tbot\t10\t{sha}", f"src/verify/runner/engine.py\tsrc.verify.runner.engine\trunner\t10\t{sha}",
            f"vendor/openpyxl/x.py\topenpyxl.x\tvendor\t10\t{sha}", f"engine/glue_data.json\t-\tglue\t10\t{sha}"]) + "\n")
        self.assertEqual(scan_bundle.stage_modules(p), {"src", "src.verify.runner.engine"})

    def test_an_exe_without_pyinstaller_cookie_has_no_archive(self):
        """torch\\bin\\protoc.exe is not a PyInstaller exe: no archive (CArchiveReader would raise
        ArchiveReadError), a note, no failure. Found without importing PyInstaller."""
        app = self.make_tree()
        protoc = app / "_internal" / "torch" / "bin" / "protoc.exe"
        protoc.parent.mkdir(parents=True)
        protoc.write_bytes(b"MZ" + b"\0" * 300)
        self.assertIsNone(scan_bundle.ArchiveSource().pkg_entries(protoc))
        self.assertFalse(scan_bundle.has_pyi_cookie(protoc, chunk=64))
        cut = self.tmp / "cookie.bin"                      # the cookie across a chunk boundary is still found
        cut.write_bytes(b"\1" * 60 + scan_bundle.PYI_COOKIE + b"\2" * 60)
        self.assertTrue(scan_bundle.has_pyi_cookie(cut, chunk=64))

        class SomeHaveNone(FakeArchives):
            def __init__(self, pyz, none_for):
                super().__init__(pyz)
                self.none_for = none_for

            def pkg_entries(self, exe):
                return None if Path(exe).name in self.none_for else super().pkg_entries(exe)

        rep = scan_bundle.scan(app, self.tmp / "scan_allow.txt", self.tmp / "STAGE_LIST.txt",
                               source=SomeHaveNone(base_pyz(), {"protoc.exe"}), chunk=64)
        self.assertTrue(rep.passed, rep.failures)
        self.assertIn("_internal/torch/bin/protoc.exe: no PyInstaller archive (scanned raw only)", rep.notes)
        rep = scan_bundle.scan(app, self.tmp / "scan_allow.txt", self.tmp / "STAGE_LIST.txt",
                               source=SomeHaveNone(base_pyz(), {"protoc.exe", "Hangeul Document Checker.exe"}),
                               chunk=64)
        self.assertIn("Hangeul Document Checker.exe: the app's exe has no PyInstaller archive", rep.failures)
        self.assertFalse(rep.passed)

    def test_main_writes_json(self):
        app = self.make_tree()
        out = self.tmp / "r" / "scan.json"
        with mock.patch.object(scan_bundle, "ArchiveSource", lambda: FakeArchives(base_pyz())), \
                contextlib.redirect_stdout(io.StringIO()) as buf:
            rc = scan_bundle.main([str(app), "--allow", str(self.tmp / "scan_allow.txt"),
                                   "--stage-list", str(self.tmp / "STAGE_LIST.txt"), "--json", str(out)])
        self.assertEqual(rc, 0, buf.getvalue())
        self.assertTrue(json.loads(out.read_text(encoding="utf-8"))["passed"])


# ================================================================================================== 8 h DLL check

class FakePE:
    def __init__(self, table, versions):
        self.table = table
        self.versions = versions

    def _rel(self, path):
        return Path(path).as_posix().split("Hangeul Document Checker/", 1)[-1]

    def imports(self, path):
        return self.table.get(self._rel(path), ([], []))

    def version(self, path):
        return self.versions.get(self._rel(path))


BASELINE = ["# made-up baseline", "kernel32.dll | every Windows 10 x64", "api-ms-win-* | API sets of Windows",
            "nvml.dll | driver-provided, loaded at run time"]


F_BASELINE = ["# name | kind | reason (a made-up copy of Task F's format)",
              "api-ms-win-*.dll    | system | API set names", "kernel32.dll        | system | Win32 base API",
              "nvcuda.dll          | driver | NVIDIA driver, loaded at run time",
              "nvml.dll            | driver | NVIDIA driver, loaded at run time",
              "vcruntime140.dll    | bundle-only | VC++ runtime", "msvcp140.dll        | bundle-only | VC++ runtime",
              "msvcr120.dll        | bundle-only | VC++ 2013", "vcomp140.dll        | bundle-only | OpenMP runtime"]


class DllCheckTests(TmpCase):
    def make(self, table_extra=None, extra_files=(), baseline=BASELINE, env_extra=(), versions_extra=None):
        root = Path(tempfile.mkdtemp(prefix="d", dir=self.tmp))
        self.app = app = root / "dist" / "Hangeul Document Checker"
        files = ["Hangeul Document Checker.exe", "_internal/python312.dll", "_internal/vcruntime140.dll",
                 "_internal/vcruntime140_1.dll", "_internal/msvcp140.dll", "_internal/msvcp140_1.dll",
                 "_internal/pyzbar/msvcr120.dll", "_internal/pyzbar/libzbar-64.dll",
                 "_internal/torch/lib/torch_cuda.dll", "_internal/torch/lib/cudart64_12.dll",
                 "_internal/torch/lib/torch_python.dll", "_internal/torch/_C.cp312-win_amd64.pyd",
                 "_internal/numpy.libs/msvcp140-0123abcd.dll"] + list(extra_files)
        for f in files:
            (app / f).parent.mkdir(parents=True, exist_ok=True)
            (app / f).write_bytes(b"MZ")
        self.env = env = root / "envtorch"
        env.mkdir(exist_ok=True)
        for n in ["torch_cuda.dll", "cudart64_12.dll", "torch_python.dll"] + list(env_extra):
            (env / n).write_bytes(b"MZ")
        table = {"Hangeul Document Checker.exe": (["kernel32.dll", "python312.dll"], []),
                 "_internal/python312.dll": (["kernel32.dll", "vcruntime140.dll",
                                              "api-ms-win-crt-runtime-l1-1-0.dll"], []),
                 "_internal/pyzbar/libzbar-64.dll": (["msvcr120.dll"], []),
                 "_internal/torch/lib/torch_cuda.dll": (["cudart64_12.dll", "nvcuda.dll", "msvcp140.dll"],
                                                        ["directml.dll"]),
                 "_internal/torch/_C.cp312-win_amd64.pyd": (["torch_python.dll", "msvcp140-0123abcd.dll"], [])}
        table.update(table_extra or {})
        versions = {f: (14, 50, 35719, 0) for f in files if Path(f).name.lower().startswith(("vcruntime", "msvcp"))}
        versions["_internal/pyzbar/msvcr120.dll"] = (12, 0, 40660, 0)
        versions["_internal/numpy.libs/msvcp140-0123abcd.dll"] = (14, 29, 0, 0)
        versions.update(versions_extra or {})
        (self.tmp / "baseline.txt").write_text("\n".join(baseline) + "\n", encoding="utf-8")
        return dll_check.check(app, self.tmp / "baseline.txt", env, reader=FakePE(table, versions))

    def test_pass(self):
        rep = self.make()
        self.assertTrue(rep.passed, rep.failures)
        self.assertEqual(rep.driver, {"nvcuda.dll": 1})
        self.assertEqual(rep.delay_allowed, {"directml.dll": ["_internal/torch/lib/torch_cuda.dll"]})
        self.assertEqual(rep.vc_versions["_internal/numpy.libs/msvcp140-0123abcd.dll"], "14.29.0.0")

    def test_failures(self):
        checks = {
            "import not satisfied: foo.dll": dict(table_extra={"_internal/python312.dll": (["foo.dll"], [])}),
            "nvml.dll is bundled": dict(extra_files=["_internal/nvml.dll"]),
            "DLL base name twice: vcruntime140.dll": dict(extra_files=["_internal/cv2/vcruntime140.dll"]),
            "torch\\lib DLL not bundled: cufft64_11.dll": dict(env_extra=["cufft64_11.dll"]),
            "covers a VC++ runtime": dict(baseline=BASELINE + ["vcruntime140.dll | Windows has it"]),
            "_internal/msvcp140.dll: version 14.29.30133.0 < 14.40": dict(
                versions_extra={"_internal/msvcp140.dll": (14, 29, 30133, 0)}),
            "delay-load import not satisfied and not 'never called': bar.dll": dict(
                table_extra={"_internal/python312.dll": (["kernel32.dll"], ["bar.dll"])}),
        }
        for want, kw in checks.items():
            rep = self.make(**kw)
            self.assertFalse(rep.passed, want)
            self.assertTrue(any(want in f for f in rep.failures), (want, rep.failures))

    def test_missing_vc_copy_and_never_called_baseline(self):
        rep = self.make(baseline=BASELINE + ["bar.dll | never called: made-up delay-load"],
                        table_extra={"_internal/python312.dll": (["kernel32.dll"], ["bar.dll"])})
        self.assertTrue(rep.passed, rep.failures)
        (self.app / "_internal" / "msvcp140_1.dll").unlink()
        rep2 = dll_check.check(self.app, self.tmp / "baseline.txt", self.env, reader=FakePE({}, {}))
        self.assertTrue(any("VC++ runtime missing: _internal/msvcp140_1.dll" in f for f in rep2.failures))

    def test_task_f_baseline_format(self):
        rep = self.make(baseline=F_BASELINE)
        self.assertTrue(rep.passed, rep.failures)
        self.assertEqual(rep.driver, {"nvcuda.dll": 1})
        rep = self.make(baseline=F_BASELINE,
                        table_extra={"_internal/python312.dll": (["kernel32.dll", "vcomp140.dll"], [])})
        self.assertIn("import not satisfied: vcomp140.dll (by 1 file(s), e.g. _internal/python312.dll)",
                      rep.failures, "a bundle-only line never satisfies an import")
        lines, errors = dll_check.load_baseline(self.tmp / "baseline.txt")
        self.assertEqual(errors, [])
        kinds = {b.pattern: (b.driver, b.bundle_only, b.delay_only) for b in lines}
        self.assertEqual(kinds["nvml.dll"], (True, False, False))
        self.assertEqual(kinds["vcruntime140.dll"], (False, True, False))
        self.assertEqual(kinds["api-ms-win-*.dll"], (False, False, False))
        rep = self.make(baseline=F_BASELINE + ["nvrtc64_120_0.dll | driver | made-up driver line"],
                        extra_files=["_internal/torch/lib/nvrtc64_120_0.dll"], env_extra=["nvrtc64_120_0.dll"])
        self.assertIn("nvrtc64_120_0.dll is bundled; it is driver-provided and must come from Windows", rep.failures)

    def test_the_real_baseline_and_allow_files_load(self):
        """packaging\\dll_baseline.txt and scan_allow.txt as shipped: no format errors; imagehlp.dll (imported by
        torch\\lib\\libiomp5md.dll, found by the first frozen build) is a system line; the two reviewed third-party
        scan lines are there."""
        pkg = Path(dll_check.__file__).resolve().parent.parent
        lines, errors = dll_check.load_baseline(pkg / "dll_baseline.txt")
        self.assertEqual(errors, [])
        hit = dll_check._match("imagehlp.dll", lines, delay=False)
        self.assertIsNotNone(hit)
        self.assertFalse(hit.driver or hit.bundle_only or hit.delay_only)
        allow, errors = scan_bundle.load_allow(pkg / "scan_allow.txt")
        self.assertEqual(errors, [])
        keys = {(a.key, a.string) for a in allow}
        self.assertIn(("networkx.algorithms.dag", "jarvis"), keys)
        self.assertIn(("setuptools.command.sdist", ".venv"), keys)

    def test_real_pefile_reads_a_windows_dll(self):
        target = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "version.dll"
        try:
            import pefile  # noqa: F401
        except ImportError:
            self.skipTest("pefile is not importable here")
        if not target.is_file():
            self.skipTest("no version.dll")
        imp, _dly = dll_check.PEReader().imports(target)
        self.assertTrue(imp)
        self.assertTrue(all(n == n.lower() for n in imp))
        v = dll_check.PEReader().version(target)
        self.assertEqual(len(v), 4)
        self.assertGreaterEqual(v[0], 6)


# ================================================================================================== 8 c trace

class TraceCheckTests(TmpCase):
    def setUp(self) -> None:
        super().setUp()
        t = self.tmp
        self.install, self.data, self.report, self.trace = t / "app", t / "data", t / "rep", t / "trace"
        self.input = t / "in" / "SAMPLE.pdf"
        for d in (self.install / "_internal", self.data, self.report, self.trace, self.input.parent):
            d.mkdir(parents=True, exist_ok=True)
        self.env = {"WINDIR": r"C:\Windows", "TEMP": str(t / "systemp")}
        self.good = [
            {"event": "open", "args": [str(self.install / "_internal" / "x.pyd"), "rb", 0]},
            {"event": "open", "args": ["cache\\pagetexts\\B-1.json", "w", 577]},
            {"event": "open", "args": [str(self.input), "rb", 0]},
            {"event": "os.mkdir", "args": [str(self.report / "case"), 511, -1]},
            {"event": "os.rename", "args": [str(self.data / "a.tmp"), str(self.data / "a.json"), -1, -1]},
            {"event": "ctypes.dlopen", "args": ["libiconv.dll"]},
            {"event": "open", "args": [r"C:\Windows\Fonts\arial.ttf", "rb", 0]},
            {"event": "ctypes.dlopen", "args": [r"C:\Windows\System32\nvml.dll"]},
            {"event": "open", "args": ["NUL", "w", 0]},
            {"event": "winreg.OpenKey", "args": [2147483650, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",  # pii-scan: not a number
                                                 131097]},
            {"event": "socket.connect", "args": ["<socket>", ["127.0.0.1", 9]], "refused": True},
            # Task C's trace marks events the guard does not refuse with refused=false: no network contact
            {"ev": "socket.__new__", "args": ["<socket>", 2, 1, 0], "refused": False},
            {"ev": "socket.gethostname", "args": [], "refused": False},
            {"event": "subprocess.Popen", "args": [None, [str(self.install / "Hangeul Document Checker.exe"),
                                                          "--worker", "job.json"], None, None]},
            {"event": "open", "args": [str(self.trace / "trace-100.jsonl"), "a", 0]},
        ]

    def write(self, events, name="trace-100.jsonl"):
        (self.trace / name).write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")

    def check(self, **kw):
        return trace_check.run_check(self.trace, self.install, self.data, self.input, self.report,
                                     environ=self.env, **kw)

    def test_allowed_events_pass(self):
        self.write(self.good)
        r = self.check()
        self.assertTrue(r["passed"], r["findings"])
        self.assertEqual(r["events"], len(self.good))

    def test_each_violation(self):
        bad = {
            "bot": {"event": "open", "args": [r"C:\Hangeul\BOT\data\x.json", "rb", 0]},
            "write-install": {"event": "open", "args": [str(self.install / "_internal" / "new.txt"), "w", 0]},
            "socket": {"event": "socket.connect", "args": ["<socket>", ["10.0.0.1", 80]], "refused": False},
            "registry": {"event": "winreg.OpenKey", "args": [2147483649, r"Software\Made\Up", 1]},  # pii-scan: not a number
            "registry-write": {"event": "winreg.SetValue", "args": [1, "x", 1, "v"]},
            "temp": {"event": "open", "args": [str(self.tmp / "systemp" / "x.tmp"), "w", 0]},
            "outside": {"event": "open", "args": [r"C:\Users\Someone\Desktop\secret.txt", "rb", 0]},
            "process": {"event": "subprocess.Popen", "args": [r"C:\Windows\System32\cmd.exe", ["cmd"], None, None]},
            "onnx": {"event": "onnxruntime.InferenceSession", "args": ["x.onnx"]},
        }
        for kind, e in bad.items():
            self.write(self.good + [e])
            r = self.check()
            self.assertFalse(r["passed"], kind)
            self.assertEqual(list(r["by_kind"]), [kind], (kind, r["findings"]))
        self.write(self.good + [bad["outside"]])
        r = self.check()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            trace_check.print_result(r)
        self.assertNotIn("Someone", buf.getvalue() + json.dumps(r))
        self.assertNotIn("secret", buf.getvalue() + json.dumps(r))

    def test_manifest(self):
        files = {"Hangeul Document Checker.exe": b"MZ1", "_internal/BUILD.json": b"{}"}
        for rel, data in files.items():
            (self.install / rel).write_bytes(data)
        man = {"files": [{"path": rel, "size": len(d), "sha256": hashlib.sha256(d).hexdigest()}
                         for rel, d in files.items()]}
        (self.install / "_internal" / "MANIFEST.json").write_text(json.dumps(man), encoding="utf-8")
        self.write(self.good)
        mpath = self.install / "_internal" / "MANIFEST.json"
        self.assertTrue(self.check(manifest=mpath, hash_files=True)["passed"])
        (self.install / "_internal" / "BUILD.json").write_bytes(b"[]")
        self.assertTrue(self.check(manifest=mpath)["passed"], "same size: only --hash sees it")
        self.assertFalse(self.check(manifest=mpath, hash_files=True)["passed"])
        (self.install / "_internal" / "BUILD.json").write_bytes(b"{}")
        (self.install / "extra.txt").write_bytes(b"x")
        r = self.check(manifest=mpath)
        self.assertEqual(r["by_kind"], {"manifest": 1})

    def test_cli_check_and_run_with_fakes(self):
        self.write(self.good)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), mock.patch.dict(os.environ, self.env):
            rc = trace_check.main(["check", "--trace", str(self.trace), "--install", str(self.install), "--data",
                                   str(self.data), "--input", str(self.input), "--report", str(self.report)])
        self.assertEqual(rc, 0, buf.getvalue())
        self.assertTrue((self.tmp / "trace_check.json").is_file())
        runs = self.tmp / "runs"
        write_r8(runs, "test1_r8", run=dict(make_run(), input=str(self.input)))
        (self.install / "Hangeul Document Checker.exe").write_bytes(b"MZ")

        def run_fn(cmd, timeout, *, what=None, env=None, log_dir=None, **kw):
            trace = Path(cmd[cmd.index("--trace-opens") + 1])
            (trace / "trace-7.jsonl").write_text(json.dumps(
                {"event": "open", "args": [str(self.input), "rb", 0]}) + "\n", encoding="utf-8")
            return FakeRes(DONE_LINES)
        with contextlib.redirect_stdout(io.StringIO()) as out, mock.patch.object(parity, "RUNS", runs), \
                mock.patch.object(cardrun, "APPTEST", self.tmp / "apptest"):
            rc = trace_check.main(["run", "--app-dir", str(self.install)], run_fn=run_fn)
        self.assertEqual(rc, 0, out.getvalue())
        self.assertIn("8 c PASS", out.getvalue())


if __name__ == "__main__":
    unittest.main()
