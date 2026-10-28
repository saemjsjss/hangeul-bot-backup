"""Task C: controller.py (APP_SPEC rev 1 sections 2 and 3.3, revision 2 R2.3.3): the states idle / checking /
waiting / done, gpu_wait driving "waiting", Cancel during a wait (stdin "cancel", then TerminateProcess of our own
child after the grace time), exit 6 mapped to the error event's card code, Windows status codes, and the case
folder. The workers are the scripted fake worker (raw protocol, or the real entry with a fake run_job) in the runner
venv with HDC_TEST_NO_CARD=1; every test ends its own children. Made-up data only."""
from __future__ import annotations

import io
import os
import time
import unittest
from pathlib import Path

from src.verify.runner.app import controller as C
from src.verify.runner.app import protocol as P
from src.verify.runner.app import spawn as S
from src.verify.runner.app.tests import fake_worker as F


def until(ctrl, cond, timeout=20.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        ctrl.poll()
        if cond():
            return True
        time.sleep(0.02)
    return False


# --------------------------------------------------------------------------------------------- classify
class Classify(unittest.TestCase):
    def test_table(self):
        done = {"ev": "done", "overall": "FAIL"}
        err = lambda code, **v: {"ev": "error", "code": code, "values": v, "text": "t"}
        rows = [
            (dict(exit_code=0, hello=True, done=done), ("done", None)),
            (dict(exit_code=3, hello=True, done={"overall": "STOP"}), ("done", None)),
            (dict(exit_code=0, hello=True), ("error", "CRASH")),
            (dict(exit_code=4, hello=True), ("cancelled", "CANCELLED")),
            (dict(exit_code=1, hello=True, cancelled=True), ("cancelled", "CANCELLED")),
            (dict(exit_code=1, hello=True, forced=True), ("cancelled", "CANCELLED")),
            (dict(exit_code=6, hello=True, error=err("DRIVER_TOO_OLD", driver="000.00")), ("error", "DRIVER_TOO_OLD")),
            (dict(exit_code=6, hello=True, error=err("LOW_CARD_MEMORY")), ("error", "LOW_CARD_MEMORY")),
            (dict(exit_code=6, hello=True, error=err("ENCRYPTED")), ("error", "CARD_FAILED")),
            (dict(exit_code=6, hello=True), ("error", "CARD_FAILED")),
            (dict(exit_code=2, hello=True, error=err("ENCRYPTED", file="x.pdf")), ("error", "ENCRYPTED")),
            (dict(exit_code=5, hello=True, error=err("WINDOWS_TOO_OLD")), ("error", "WINDOWS_TOO_OLD")),
            (dict(exit_code=5, hello=True), ("error", "STARTUP_FAILED")),
            (dict(exit_code=1, hello=True, error=err("CRASH", stage="names")), ("error", "CRASH")),
            (dict(exit_code=1, hello=False), ("error", "STARTUP_FAILED")),
            (dict(exit_code=0xC000001D, hello=False), ("error", "CPU_UNSUPPORTED")),
            (dict(exit_code=-1073741795, hello=True), ("error", "CPU_UNSUPPORTED")),   # signed (pii-scan: not a number)
            (dict(exit_code=0xC0000017, hello=True), ("error", "LOW_MEMORY")),
            (dict(exit_code=0xC0000135, hello=False), ("error", "MISSING_PART")),
            (dict(exit_code=0xC0000139, hello=False), ("error", "MISSING_PART")),
            (dict(exit_code=0xC0000005, hello=True), ("error", "CRASH")),
        ]
        for kw, want in rows:
            with self.subTest(kw=kw):
                o = C.classify(**kw)
                self.assertEqual((o.kind, o.code), want)

    def test_values(self):
        o = C.classify(6, hello=True, stage="firstread", error={"code": "CARD_FAILED", "values": {}})
        self.assertEqual(o.values, {"stage": "firstread"})
        o = C.classify(6, hello=True, stage="names", error={"code": "DRIVER_TOO_OLD", "values": {"driver": "1"}})
        self.assertEqual(o.values, {"stage": "names", "driver": "1"})
        o = C.classify(0xC0000005, hello=True, stage="ocr")
        self.assertEqual(o.values, {"stage": "ocr", "status": "0xC0000005"})
        self.assertTrue(C.classify(3, hello=True, done={"overall": "STOP"}).stop)
        self.assertFalse(C.classify(0, hello=True, done={"overall": "PASS"}).stop)
        self.assertTrue(C.classify(1, hello=True, forced=True).forced)
        self.assertEqual(C.CANCEL_GRACE_S, 10.0)


# --------------------------------------------------------------------------- the state machine (fake pipe)
class PipeProc:
    """A Popen stand-in: the test writes the worker's stdout through a real pipe and decides the exit."""

    def __init__(self):
        r, w = os.pipe()
        self.stdout = open(r, "rb")
        self._w = w
        self.stdin = io.BytesIO()
        self.returncode = None
        self.pid = 4242
        self.killed = 0

    def send(self, ev):
        os.write(self._w, P.encode(ev))

    def close_out(self):
        if self._w is not None:
            os.close(self._w)
            self._w = None

    def end(self, rc):
        self.close_out()
        self.returncode = rc

    def poll(self):
        return self.returncode

    def kill_for_test(self, proc):
        self.killed += 1
        self.end(1)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class StateMachine(unittest.TestCase):
    def setUp(self):
        self.dir = F.scratch("controller_states")
        self.case = os.path.join(self.dir, "reports", "case")
        os.makedirs(self.case)
        self.job = P.make_job(input=os.path.join(self.dir, "in.pdf"), program="KLP", out=self.case,
                              cache_root=os.path.join(self.dir, "data", "cache"))
        self.job_path = str(P.write_job(os.path.join(self.dir, "data", "jobs", "j", "job.json"), self.job))
        C.mark_case_dir(self.case, self.job["job"])
        self.proc = PipeProc()
        self.clock = Clock()
        self.ctrl = C.CheckController(starter=lambda j, l, i: self.proc, clock=self.clock,
                                      terminate=self.proc.kill_for_test)
        self.log = os.path.join(self.dir, "worker.log")

    def tearDown(self):
        self.proc.close_out()
        try:
            self.proc.stdout.close()
        except OSError:
            pass
        F.remove(self.dir)

    def test_waiting_follows_gpu_wait_and_done(self):
        c = self.ctrl
        self.assertEqual(c.state, C.IDLE)
        c.start(self.job_path, log_path=self.log)
        self.assertEqual(c.state, C.CHECKING)
        self.proc.send(P.hello("1.0.0", 1, "dev"))
        self.proc.send(P.card_found(F.CARD_NAME, "12.0", "000.00", 8151, 6120))
        self.proc.send(P.stage("firstread", "start"))
        self.proc.send(P.gpu_wait("start", "lock", lock="bot", where="page", reserved_mb=120))
        self.assertTrue(until(c, lambda: c.state == C.WAITING))
        self.assertEqual(c.progress.wait["lock"], "bot")
        self.assertEqual(c.progress.wait["where"], "page")
        self.assertEqual(c.progress.card["name"], F.CARD_NAME)
        self.proc.send(P.gpu_wait("tick", "lock", lock="bot", waited_s=42.0))
        self.assertTrue(until(c, lambda: c.progress.wait and c.progress.wait.get("waited_s") == 42.0))
        self.assertEqual(c.state, C.WAITING)
        self.proc.send(P.gpu_wait("end", "lock", waited_s=43.5))
        self.assertTrue(until(c, lambda: c.state == C.CHECKING))
        self.assertIsNone(c.progress.wait)
        self.assertEqual((c.progress.waits, c.progress.waited_s), (1, 43.5))
        self.proc.send(P.gpu_wait("start", "memory", free_mb=2900, need_mb=4096))
        self.assertTrue(until(c, lambda: c.state == C.WAITING))
        self.assertEqual(c.progress.wait["why"], "memory")
        self.proc.send(P.page(3, 14, 1.2, "card"))                        # a lost "end": the page ends the wait
        self.assertTrue(until(c, lambda: c.state == C.CHECKING))
        self.assertEqual((c.progress.page_done, c.progress.page_of, c.progress.page_where), (3, 14, "card"))
        self.proc.send(P.card_ready("2.11.0+cu128", "12.8", 9.5))
        self.proc.send(P.done(0, "PASS", "x", "0000-0000", "<case>", [], 1,
                              P.gpu_block(F.CARD_NAME, 2.4, 2.8, 0, 2, 43.5)))
        self.proc.end(0)
        self.assertTrue(until(c, lambda: c.state == C.DONE))
        o = c.outcome
        self.assertEqual((o.kind, o.code, o.exit), ("done", None, 0))
        self.assertEqual(o.done["gpu"]["waits"], 2)
        self.assertTrue(c.progress.card["ready"])
        self.assertEqual(c.progress.card["ready_seconds"], 9.5)
        self.assertTrue(os.path.isdir(self.case))                           # kept: the reports are there
        self.assertFalse(os.path.exists(os.path.join(self.case, C.case_marker(self.job["job"]))))
        c.reset()
        self.assertEqual(c.state, C.IDLE)

    def test_cancel_during_a_wait_escalates_after_the_grace_time(self):
        c = self.ctrl
        c.start(self.job_path, log_path=self.log)
        self.proc.send(P.hello("1.0.0", 1))
        self.proc.send(P.gpu_wait("start", "lock", lock="other", where="start", reserved_mb=0))
        self.assertTrue(until(c, lambda: c.state == C.WAITING))
        Path(self.case, "partial.txt").write_text("made-up", "ascii")
        self.assertTrue(c.cancel())
        self.assertTrue(c.cancelling)
        self.assertEqual(self.proc.stdin.getvalue(), b"cancel\n")
        self.assertTrue(c.cancel())                                         # a second press sends nothing more
        self.assertEqual(self.proc.stdin.getvalue(), b"cancel\n")
        self.clock.t += 9.9
        c.poll()
        self.assertEqual(self.proc.killed, 0)
        self.assertEqual(c.state, C.WAITING)
        self.clock.t += 0.2
        self.assertTrue(until(c, lambda: c.state == C.DONE, timeout=5))
        self.assertEqual(self.proc.killed, 1)
        o = c.outcome
        self.assertEqual((o.kind, o.code, o.forced), ("cancelled", "CANCELLED", True))
        self.assertFalse(os.path.exists(self.case))                         # this check created it: removed

    def test_exit_without_end_of_output(self):
        c = C.CheckController(starter=lambda j, l, i: self.proc, clock=self.clock, exit_wait_s=2.0)
        c.start(self.job_path, log_path=self.log)
        self.proc.send(P.hello("1.0.0", 1))
        self.proc.send(P.error("CARD_TOO_SMALL", "made-up", {"name": F.CARD_NAME, "total": "4.0"}))
        self.assertTrue(until(c, lambda: c.error is not None))
        self.proc.returncode = 6                                            # exited, pipe still open
        c.poll()
        self.assertEqual(c.state, C.CHECKING)
        self.clock.t += 2.0
        c.poll()
        self.assertEqual(c.state, C.DONE)
        self.assertEqual((c.outcome.code, c.outcome.values["total"]), ("CARD_TOO_SMALL", "4.0"))
        self.assertFalse(os.path.exists(self.case))                         # refused, nothing but the marker

    def test_start_failures(self):
        def boom(j, l, i):
            raise OSError("blocked")
        c = C.CheckController(starter=boom)
        c.start(self.job_path, log_path=self.log)
        self.assertEqual((c.state, c.outcome.code, c.outcome.exit), (C.DONE, "STARTUP_FAILED", None))
        c2 = C.CheckController(starter=boom)
        c2.start(os.path.join(self.dir, "missing.json"))
        self.assertEqual(c2.outcome.code, "STARTUP_FAILED")
        self.assertEqual(c2.outcome.values, {"why": "job"})
        self.assertFalse(c2.cancel())

    def test_start_while_running_is_refused(self):
        self.ctrl.start(self.job_path, log_path=self.log)
        with self.assertRaises(RuntimeError):
            self.ctrl.start(self.job_path, log_path=self.log)
        with self.assertRaises(RuntimeError):
            self.ctrl.reset()
        self.proc.end(1)
        self.ctrl.wait(5)

    def test_unknown_events_and_junk_are_ignored(self):
        c = self.ctrl
        c.start(self.job_path, log_path=self.log)
        self.proc.send(P.hello("1.0.0", 1))
        self.proc.send(P.make("future_event", x=1))
        os.write(self.proc._w, b"not json\n")
        self.proc.send(P.file_event("made-up.pdf", 3, 2))
        self.proc.send(P.budget("tin.b", 2))
        self.proc.send(P.stage("names", "start"))
        self.proc.send(P.done(3, "STOP", "x", "0000-0000", "<case>", [], 1))
        self.proc.end(3)
        self.assertTrue(until(c, lambda: c.state == C.DONE))
        self.assertEqual(c.bad_lines, 1)
        self.assertEqual(c.progress.files[0]["ocr"], 2)
        self.assertEqual(c.progress.budgets, [{"where": "tin.b", "page": 2}])
        self.assertEqual(c.progress.stage, "names")
        self.assertTrue(c.outcome.stop)


class CaseFolder(unittest.TestCase):
    def setUp(self):
        self.dir = F.scratch("controller_case")

    def tearDown(self):
        F.remove(self.dir)

    def test_only_a_marked_folder_is_removed(self):
        a = os.path.join(self.dir, "a")
        os.makedirs(a)
        Path(a, "report.xlsx").write_bytes(b"made-up")
        self.assertFalse(C.remove_case_dir_if_ours(a, "0" * 32))
        C.mark_case_dir(a, "1" * 32)
        self.assertFalse(C.remove_case_dir_if_ours(a, "0" * 32))           # another job's marker
        self.assertFalse(C.remove_case_dir_if_ours(a, "1" * 32, only_if_empty=True))
        self.assertTrue(os.path.isdir(a))
        self.assertTrue(C.remove_case_dir_if_ours(a, "1" * 32))
        self.assertFalse(os.path.exists(a))


# ---------------------------------------------------------------------------------------- real children
class RealWorkers(unittest.TestCase):
    def setUp(self):
        self.dir = F.scratch("controller_real")
        self.data = os.path.join(self.dir, "data")
        self.case = os.path.join(self.dir, "reports", "case")
        self.ctrls = []

    def tearDown(self):
        for c in self.ctrls:                                   # only the workers these tests started
            if c.running:
                c.shutdown(timeout=3)
            p = c._proc
            if p is not None and hasattr(p, "poll") and p.poll() is None:
                S.terminate(p)
        F.remove(self.dir)

    def job(self, **extra):
        os.makedirs(self.case, exist_ok=True)
        j = P.make_job(input=os.path.join(self.dir, "in", "MADE-UP.pdf"), program="KLP", out=self.case,
                       cache_root=os.path.join(self.data, "cache"), **extra)
        C.mark_case_dir(self.case, j["job"])
        return str(P.write_job(os.path.join(self.data, "jobs", j["job"], "job.json"), j)), j

    def raw(self, scenario, **kw):
        def starter(job_path, log_path, index):
            return S.start_process(F.fake_cmd("raw", scenario), log_path=log_path, env=F.child_env(self.data))
        c = C.CheckController(starter=starter, **kw)
        self.ctrls.append(c)
        path, j = self.job()
        c.start(path, log_path=os.path.join(self.data, "logs", f"{scenario}.log"))
        return c, j

    def via_entry(self, scenario, **kw):
        def starter(job_path, log_path, index):
            env = S.child_env(index, base=F.child_env(self.data), gpuinfo=F.make_fake_gpuinfo())
            return S.start_process(F.fake_cmd("entry", "--worker", str(job_path)), log_path=log_path, env=env)
        c = C.CheckController(starter=starter, **kw)
        self.ctrls.append(c)
        path, j = self.job(fake=scenario)
        c.start(path, log_path=os.path.join(self.data, "logs", f"entry-{scenario}.log"))
        return c, j

    def test_done(self):
        c, _ = self.raw("done")
        o = c.wait(30)
        self.assertEqual((o.kind, o.exit), ("done", 0))
        self.assertEqual(o.done["gpu"]["card"], F.CARD_NAME)
        self.assertEqual(c.progress.card["name"], F.CARD_NAME)
        self.assertEqual(c.hello["app"], "1.0.0")

    def test_exit_6_maps_to_the_error_events_card_code(self):
        for scenario, code, values in (("exit6_driver", "DRIVER_TOO_OLD", {"stage": "card", "driver": "000.00"}),
                                       ("exit6_unknown", "CARD_FAILED", {}),
                                       ("exit6_noerror", "CARD_FAILED", {"stage": "firstread"})):
            with self.subTest(scenario=scenario):
                c, _ = self.raw(scenario)
                o = c.wait(30)
                self.assertEqual((o.kind, o.code, o.exit), ("error", code, 6))
                self.assertEqual(o.values, values)
                self.assertFalse(os.path.exists(self.case))                 # refused: the empty case folder goes

    def test_cancel_during_a_wait(self):
        c, _ = self.raw("wait_cancel")
        self.assertTrue(until(c, lambda: c.state == C.WAITING))
        self.assertEqual(c.progress.wait["lock"], "bot")
        Path(self.case, "partial.txt").write_text("made-up", "ascii")
        t0 = time.monotonic()
        c.cancel()
        o = c.wait(15)
        self.assertLess(time.monotonic() - t0, 10.0)
        self.assertEqual((o.kind, o.code, o.exit, o.forced), ("cancelled", "CANCELLED", 4, False))
        self.assertTrue(c.cancelled_event)
        self.assertFalse(os.path.exists(self.case))
        self.assertIsNotNone(c._proc.poll())

    def test_a_worker_ignoring_cancel_is_terminated(self):
        c, _ = self.raw("wait_ignore_cancel", cancel_grace_s=1.0)
        self.assertTrue(until(c, lambda: c.state == C.WAITING))
        c.cancel()
        o = c.wait(20)
        self.assertEqual((o.kind, o.code, o.forced), ("cancelled", "CANCELLED", True))
        self.assertIsNotNone(c._proc.poll())                                  # our child is gone

    def test_no_hello_and_status_codes(self):
        for scenario, code in (("no_hello_exit1", "STARTUP_FAILED"), ("status_illegal", "CPU_UNSUPPORTED"),
                               ("status_other", "CRASH")):
            with self.subTest(scenario=scenario):
                c, _ = self.raw(scenario)
                o = c.wait(30)
                self.assertEqual((o.kind, o.code), ("error", code))

    def test_stray_output_does_not_reach_the_window(self):
        c, _ = self.raw("junk")
        o = c.wait(30)
        self.assertEqual(o.kind, "done")
        self.assertEqual(c.bad_lines, 1)                                      # the one deliberate junk line

    def test_memory_wait_then_done(self):
        c, _ = self.raw("wait_then_done")
        o = c.wait(30)
        self.assertEqual(o.kind, "done")
        self.assertEqual((c.progress.waits, round(c.progress.waited_s, 2)), (1, 0.2))
        self.assertEqual(c.state, C.DONE)

    # ---- the real entry (--worker) around a fake run_job
    def test_entry_worker_cancel_during_a_wait(self):
        c, _ = self.via_entry("wait_cancel")
        self.assertTrue(until(c, lambda: c.state == C.WAITING))
        self.assertEqual(c.progress.wait["where"], "start")
        t0 = time.monotonic()
        c.cancel()
        o = c.wait(15)
        self.assertLess(time.monotonic() - t0, 10.0)
        self.assertEqual((o.kind, o.exit, o.forced), ("cancelled", 4, False))
        self.assertTrue(c.cancelled_event)

    def test_entry_worker_done_and_card_error(self):
        c, _ = self.via_entry("done")
        o = c.wait(30)
        self.assertEqual((o.kind, o.exit), ("done", 0))
        self.assertEqual(c.progress.page_where, "runner")
        self.assertEqual(c.hello["build"], "dev")
        c2, _ = self.via_entry("card_error")
        o2 = c2.wait(30)
        self.assertEqual((o2.code, o2.exit, o2.values), ("DRIVER_TOO_OLD", 6, {"stage": "card", "driver": "000.00"}))


if __name__ == "__main__":
    unittest.main()
