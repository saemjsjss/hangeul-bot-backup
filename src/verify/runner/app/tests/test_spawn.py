"""Task C: spawn.py (APP_SPEC rev 1 section 2, revision 2 R2.3.3). The children are the scripted fake worker (the
runner venv, -I -B, HDC_TEST_NO_CARD=1); each test ends its own child. Made-up data only."""
from __future__ import annotations

import os
import subprocess
import time
import unittest

from src.verify.runner.app import protocol as P
from src.verify.runner.app import spawn as S
from src.verify.runner.app.tests import fake_worker as F


def read_events(proc, until=None, timeout=30.0):
    """Reads protocol lines from a child until an event named `until` (or EOF)."""
    out = []
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        line = proc.stdout.readline()
        if not line:
            break
        ev = P.decode(line)
        if ev is None:
            out.append({"ev": "<bad>"})
            continue
        out.append(ev)
        if until and ev["ev"] == until:
            break
    return out


class Commands(unittest.TestCase):
    def test_self_command_dev_and_frozen(self):
        self.assertEqual(S.self_command("--worker", r"C:\x\job.json", frozen=False, executable=r"C:\py.exe",
                                        trace_dir=None),
                         [r"C:\py.exe", "-I", "-B", "-m", "src.verify.runner.app.entry", "--worker",
                          r"C:\x\job.json"])
        self.assertEqual(S.self_command("--selftest-part", "card", r"C:\r.json", frozen=True,
                                        executable=r"C:\app\Hangeul Document Checker.exe", trace_dir=None),
                         [r"C:\app\Hangeul Document Checker.exe", "--selftest-part", "card", r"C:\r.json"])
        self.assertEqual(S.self_command("--selftest", frozen=True, executable="app.exe", trace_dir=r"C:\t"),
                         ["app.exe", "--trace-opens", r"C:\t", "--selftest"])

    def test_self_command_passes_this_process_trace_folder(self):
        from src.verify.runner.app import trace
        if trace.armed_dir() is not None:
            self.skipTest("this test process is traced")
        self.assertNotIn("--trace-opens", S.self_command("--selftest", frozen=True, executable="a.exe"))


class Environment(unittest.TestCase):
    BASE = {"PATH": r"C:\Windows", "SystemRoot": r"C:\Windows", "CUDA_PATH": r"C:\made\up\cuda",
            "CUDA_PATH_V12_8": r"C:\made\up", "cuda_cache_path": "x", "PYTORCH_CUDA_ALLOC_CONF": "x",
            "TORCH_HOME": "x", "CUBLAS_WORKSPACE_CONFIG": ":4096:8", "CUDNN_LOGINFO_DBG": "1",
            "NVIDIA_TF32_OVERRIDE": "0", "CUDA_VISIBLE_DEVICES": "3", "HDC_DATA_DIR": r"C:\d"}
    CARD_KEYS = ("CUDA_PATH", "CUDA_PATH_V12_8", "cuda_cache_path", "PYTORCH_CUDA_ALLOC_CONF", "TORCH_HOME",
                 "CUBLAS_WORKSPACE_CONFIG", "CUDNN_LOGINFO_DBG", "NVIDIA_TF32_OVERRIDE")

    def check(self, env, index):
        for k in self.CARD_KEYS:
            self.assertNotIn(k, env)
        self.assertEqual(env["CUDA_DEVICE_ORDER"], "PCI_BUS_ID")
        self.assertEqual(env["CUDA_VISIBLE_DEVICES"], str(index))
        self.assertEqual(env["PATH"], r"C:\Windows")
        self.assertEqual(env["HDC_DATA_DIR"], r"C:\d")

    def test_child_env_with_the_full_reply(self):
        gi = F.make_fake_gpuinfo()
        env = S.child_env(1, base=self.BASE, gpuinfo=gi)
        self.check(env, 1)
        self.assertEqual(env["OPENBLAS_NUM_THREADS"], "1")
        self.assertEqual(env["PYMUPDF_MESSAGE"], "fd:2")
        self.assertIn("card_env:1", gi.calls)
        self.assertEqual(self.BASE["CUDA_PATH"], r"C:\made\up\cuda")         # the base is not changed

    def test_openblas_setting_is_kept(self):
        env = S.child_env(0, base=dict(self.BASE, OPENBLAS_NUM_THREADS="4"), gpuinfo=F.make_fake_gpuinfo())
        self.assertEqual(env["OPENBLAS_NUM_THREADS"], "4")

    def test_apply_env_with_a_reply_of_changes_only(self):
        target = dict(self.BASE)
        gone = S.apply_env(target, {"CUDA_DEVICE_ORDER": "PCI_BUS_ID", "CUDA_VISIBLE_DEVICES": "0"})
        self.check(target, 0)
        self.assertEqual(sorted(gone), sorted(self.CARD_KEYS))

    def test_is_card_var(self):
        for k in ("CUDA_PATH", "cuda_path", "PyTorch_X", "TORCH_X", "CUBLAS_X", "CUDNN_X", "NVIDIA_TF32_OVERRIDE"):
            self.assertTrue(S.is_card_var(k), k)
        for k in ("PATH", "NVIDIA_OTHER", "XCUDA_PATH", "OPENBLAS_NUM_THREADS"):
            self.assertFalse(S.is_card_var(k), k)


class FakePopen:
    calls = []

    def __init__(self, cmd, **kw):
        FakePopen.calls.append((cmd, kw))
        self.pid = 1


class StartWorker(unittest.TestCase):
    def setUp(self):
        self.dir = F.scratch("spawn_fake")

    def tearDown(self):
        F.remove(self.dir)

    def test_start_worker_arguments(self):
        FakePopen.calls.clear()
        job = os.path.join(self.dir, "jobs", "j", "job.json")
        log = os.path.join(self.dir, "logs", "worker-j.log")
        S.start_worker(job, log, 2, popen=FakePopen, gpuinfo=F.make_fake_gpuinfo())
        cmd, kw = FakePopen.calls[-1]
        self.assertEqual(cmd[-2:], ["--worker", job])
        self.assertEqual(kw["creationflags"] & S.BELOW_NORMAL_PRIORITY_CLASS, S.BELOW_NORMAL_PRIORITY_CLASS)
        self.assertEqual(kw["creationflags"] & S.CREATE_NO_WINDOW, S.CREATE_NO_WINDOW)
        self.assertEqual((kw["stdin"], kw["stdout"]), (subprocess.PIPE, subprocess.PIPE))
        self.assertTrue(kw["close_fds"])
        self.assertEqual(kw["env"]["CUDA_VISIBLE_DEVICES"], "2")
        self.assertTrue(kw["stderr"].closed)                 # the parent's copy of the log is closed at once
        self.assertTrue(os.path.isfile(log))


class RealChildren(unittest.TestCase):
    def setUp(self):
        self.dir = F.scratch("spawn_real")
        self.data = os.path.join(self.dir, "data")
        self.procs = []

    def tearDown(self):
        for p in self.procs:                                 # only the children this test started
            if p.poll() is None:
                S.terminate(p)
            for s in (p.stdin, p.stdout):
                try:
                    if s:
                        s.close()
                except OSError:
                    pass
        F.remove(self.dir)

    def start(self, scenario, env=None):
        log = os.path.join(self.dir, f"{scenario}.log")
        p = S.start_process(F.fake_cmd("raw", scenario), log_path=log,
                            env=env if env is not None else F.child_env(self.data))
        self.procs.append(p)
        return p, log

    def test_card_environment_reaches_the_child(self):
        base = F.child_env(self.data, CUDA_PATH=r"C:\made\up\cuda", PYTORCH_FAKE_SETTING="1")
        env = S.child_env(0, base=base, gpuinfo=F.make_fake_gpuinfo())
        p, _ = self.start("print_env", env)
        evs = read_events(p, until="done")
        self.assertEqual(p.wait(30), 0)
        got = [e for e in evs if e["ev"] == "env"][0]
        self.assertEqual(got["CUDA_VISIBLE_DEVICES"], "0")
        self.assertEqual(got["CUDA_DEVICE_ORDER"], "PCI_BUS_ID")
        self.assertIsNone(got["CUDA_PATH"])
        self.assertIsNone(got["PYTORCH_FAKE_SETTING"])
        self.assertEqual(got["OPENBLAS_NUM_THREADS"], "1")
        self.assertEqual(got["PYMUPDF_MESSAGE"], "fd:2")

    def test_below_normal_priority_and_terminate(self):
        p, _ = self.start("wait_ignore_cancel")
        evs = read_events(p, until="gpu_wait")
        self.assertEqual(evs[-1]["ev"], "gpu_wait")
        self.assertEqual(S.priority_class(p), S.BELOW_NORMAL_PRIORITY_CLASS)
        rc = S.terminate(p)
        self.assertIsNotNone(rc)
        self.assertIsNotNone(p.poll())

    def test_stray_output_goes_to_the_log_not_the_pipe(self):
        p, log = self.start("junk")
        evs = read_events(p)
        self.assertEqual(p.wait(30), 0)
        self.assertEqual([e["ev"] for e in evs], ["hello", "<bad>", "done"])   # only the deliberate junk line
        with open(log, "rb") as f:
            text = f.read()
        self.assertIn(b"this goes to fd 1, which now points at the log", text)
        self.assertIn(b"a print after the claim", text)


if __name__ == "__main__":
    unittest.main()
