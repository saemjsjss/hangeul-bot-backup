"""Task C: boot.py and entry.py (APP_SPEC rev 1 section 6.2, revision 2 R2.3.3). Pure checks run here with
injected environments; everything that changes os.environ, the working folder or stdout runs in a CHILD process (the
runner venv, -I -B, HDC_TEST_NO_CARD=1) through the fake worker, with DATA under Task C's scratch folder. A fake
gpuinfo and a fake cardgate stand in for Task E's modules; no card, no torch, no real case."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
import uuid
from pathlib import Path

from src.verify.runner.app import boot as B
from src.verify.runner.app import entry as E
from src.verify.runner.app import protocol as P
from src.verify.runner.app.tests import fake_worker as F


# ------------------------------------------------------------------------------------------------- boot
class DataDir(unittest.TestCase):
    def test_override_and_default(self):
        self.assertEqual(B.data_dir({"HDC_DATA_DIR": r"C:\made\up\data"}), Path(r"C:\made\up\data"))
        self.assertEqual(B.data_dir({"LOCALAPPDATA": r"C:\Users\Someone\AppData\Local"}),
                         Path(r"C:\Users\Someone\AppData\Local\Hangeul Document Checker"))

    def test_card_roles(self):
        self.assertTrue(B.is_card_role("worker"))
        self.assertTrue(B.is_card_role("cli"))
        self.assertTrue(B.is_card_role("selftest-part", "card"))
        for role, part in (("selftest-part", "runtime"), ("selftest", None), ("window", None), ("auto", None)):
            self.assertFalse(B.is_card_role(role, part), (role, part))


class CardEnv(unittest.TestCase):
    BASE = {"PATH": r"C:\Windows", "CUDA_PATH": r"C:\made\up", "PYTORCH_CUDA_ALLOC_CONF": "x", "Torch_Home": "y",
            "NVIDIA_TF32_OVERRIDE": "0", "CUDA_VISIBLE_DEVICES": "5"}

    def check(self, env, index):
        for k in ("CUDA_PATH", "PYTORCH_CUDA_ALLOC_CONF", "Torch_Home", "NVIDIA_TF32_OVERRIDE"):
            self.assertNotIn(k, env)
        self.assertEqual(env["CUDA_VISIBLE_DEVICES"], str(index))
        self.assertEqual(env["CUDA_DEVICE_ORDER"], "PCI_BUS_ID")
        self.assertEqual(env["OPENBLAS_NUM_THREADS"], "1")
        self.assertEqual(env["PYMUPDF_MESSAGE"], "fd:2")
        self.assertEqual(env["PATH"], r"C:\Windows")

    def test_apply_card_env_with_given_index(self):
        env = dict(self.BASE)
        gi = F.make_fake_gpuinfo()
        info = B.prepare_card_env(2, environ=env, gpuinfo=gi)
        self.check(env, 2)
        self.assertEqual(info["index"], 2)
        self.assertEqual(info["removed"], sorted(["CUDA_PATH", "CUDA_VISIBLE_DEVICES", "NVIDIA_TF32_OVERRIDE",
                                                  "PYTORCH_CUDA_ALLOC_CONF", "Torch_Home"]))
        self.assertEqual(gi.calls, ["apply_card_env:2"])               # no NVML probe when the index is given

    def test_index_from_nvml_and_no_card(self):
        env = dict(self.BASE)
        gi = F.make_fake_gpuinfo(index=1, nvml=True)
        self.assertEqual(B.prepare_card_env(None, environ=env, gpuinfo=gi)["index"], 1)
        self.assertEqual(gi.calls[0], "probe")
        env2 = dict(self.BASE)
        self.assertEqual(B.prepare_card_env(None, environ=env2, gpuinfo=F.make_fake_gpuinfo(nvml=False))["index"],
                         0)
        self.check(env2, 0)

    def test_card_env_only_contract(self):
        env = dict(self.BASE, OPENBLAS_NUM_THREADS="8")
        B.prepare_card_env(0, environ=env, gpuinfo=F.make_fake_gpuinfo(with_apply=False))
        self.assertEqual(env["OPENBLAS_NUM_THREADS"], "8")                  # an explicit setting is kept
        env["OPENBLAS_NUM_THREADS"] = "1"
        self.check(env, 0)


class Isolate(unittest.TestCase):
    def setUp(self):
        self.dir = F.scratch("boot_isolate")

    def tearDown(self):
        F.remove(self.dir)

    def test_frozen_drops_paths_outside_the_bundle(self):
        mei = os.path.join(self.dir, "bundle", "_internal")
        paths = [os.path.join(mei, "base_library.zip"), mei, r"C:\elsewhere\site-packages", "",
                 mei + "-twin"]
        env = {}
        seen = []
        data = os.path.join(self.dir, "data \u00e9")
        r = B.isolate(frozen=True, meipass=mei, data=data, environ=env, path_list=paths, chdir=seen.append)
        self.assertEqual(paths, [os.path.join(mei, "base_library.zip"), mei])
        self.assertEqual(r, {"dropped": 3, "data_ascii": False})
        self.assertEqual(seen, [data])
        self.assertEqual(env["TMP"], os.path.join(data, "tmp"))
        self.assertEqual(env["TEMP"], os.path.join(data, "tmp"))
        self.assertTrue(os.path.isdir(os.path.join(data, "tmp")))

    def test_dev_keeps_paths_and_pins_the_empty_entry(self):
        paths = ["", r"C:\venv\site-packages"]
        data = os.path.join(self.dir, "data")
        r = B.isolate(frozen=False, data=data, environ={}, path_list=paths, chdir=lambda p: None)
        self.assertEqual(paths, [os.getcwd(), r"C:\venv\site-packages"])
        self.assertEqual(r["dropped"], 0)
        self.assertTrue(r["data_ascii"])


class Platform(unittest.TestCase):
    def test_windows_build_and_bits(self):
        self.assertTrue(B.check_platform(build=19045, is64=True, cpu={}).ok)
        self.assertTrue(B.check_platform(build=17763, is64=True, cpu={}).ok)
        r = B.check_platform(build=17762, is64=True, cpu={})
        self.assertEqual((r.ok, r.code), (False, "WINDOWS_TOO_OLD"))
        self.assertEqual(B.check_platform(build=22631, is64=False, cpu={}).code, "WINDOWS_TOO_OLD")

    def test_this_pc(self):
        r = B.check_platform()
        self.assertTrue(r.ok)
        self.assertGreaterEqual(r.windows_build, 17763)
        self.assertEqual(set(r.cpu), {"sse42", "avx", "avx2", "avx512f"})

    def test_cpu_flags_forms(self):
        f = B.cpu_flags({"SSE4.2": "yes", "AVX2": False, "avx512f": "no", "unknown": True})
        self.assertEqual((f["sse42"], f["avx2"], f["avx512f"]), (True, False, False))
        self.assertIsNotNone(f["avx"])                                       # filled from Windows
        g = B.cpu_flags(["SSE4.2", "AVX"])
        self.assertEqual(g, {"sse42": True, "avx": True, "avx2": False, "avx512f": False})


class Preload(unittest.TestCase):
    def setUp(self):
        self.mei = F.scratch("boot_preload")

    def tearDown(self):
        F.remove(self.mei)

    def test_dev_loads_nothing(self):
        self.assertEqual(B.preload_runtime(frozen=False), {})

    def test_frozen_loads_by_full_path(self):
        for n in ("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll"):
            Path(self.mei, n).write_bytes(b"made-up")
        os.makedirs(os.path.join(self.mei, "pyzbar"))
        Path(self.mei, "pyzbar", "msvcr120.dll").write_bytes(b"made-up")
        loaded = []

        def loader(p):
            if p.endswith("msvcr120.dll"):
                raise OSError("made-up")
            loaded.append(p)
            return object()

        r = B.preload_runtime(frozen=True, meipass=self.mei, loader=loader)
        self.assertEqual(r, {"vcruntime140.dll": "bundled", "vcruntime140_1.dll": "bundled",
                             "msvcp140.dll": "bundled", "msvcp140_1.dll": "missing",
                             "msvcr120.dll": "failed (OSError)"})
        self.assertEqual(loaded, [os.path.join(self.mei, n) for n in
                                  ("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll")])
        self.assertEqual(B.missing_runtime(r), "msvcp140_1.dll")
        self.assertIsNone(B.missing_runtime({"a.dll": "bundled"}))


class SingleInstance(unittest.TestCase):
    def test_second_holder_is_refused(self):
        name = "Local\\HDC-unit-test-" + uuid.uuid4().hex
        h1 = B.take_single_instance(name)
        self.assertIsNotNone(h1)
        try:
            self.assertIsNone(B.take_single_instance(name))
        finally:
            B.release_single_instance(h1)
        h3 = B.take_single_instance(name)
        self.assertIsNotNone(h3)
        B.release_single_instance(h3)


# ------------------------------------------------------------------------------------------------ entry
class Parse(unittest.TestCase):
    def test_window(self):
        o = E.parse([])
        self.assertEqual((o.role, o.path), ("window", None))
        o = E.parse([r"C:\made\up\CHECK.pdf", r"C:\second.pdf"])
        self.assertEqual((o.role, o.path), ("window", r"C:\made\up\CHECK.pdf"))

    def test_cli(self):
        o = E.parse(["--cli", r"C:\in\x.pdf", "--program", "klp", "--out", r"C:\out\c", "--today", "2026-10-28",
                     "--gpu-lock-extra", r"C:\l\a.lock", "--gpu-lock-extra=C:\\l\\b.lock", "--data-dir", r"C:\d",
                     "--trace-opens", r"C:\t"])
        self.assertEqual((o.role, o.path, o.program, o.out, o.today), ("cli", r"C:\in\x.pdf", "KLP", r"C:\out\c",
                                                                       "2026-10-28"))
        self.assertEqual(o.lock_extra, [r"C:\l\a.lock", r"C:\l\b.lock"])
        self.assertEqual((o.data_dir, o.trace_dir), (r"C:\d", r"C:\t"))

    def test_other_roles(self):
        self.assertEqual(E.parse(["--worker", r"C:\j\job.json"]).job, r"C:\j\job.json")
        o = E.parse(["--selftest", "--report-dir", r"C:\r"])
        self.assertEqual((o.role, o.report_dir), ("selftest", r"C:\r"))
        o = E.parse(["--selftest-part", "card", r"C:\r\card.json"])
        self.assertEqual((o.role, o.part, o.part_result), ("selftest-part", "card", r"C:\r\card.json"))
        o = E.parse(["--selftest-part", "imports"])
        self.assertEqual((o.part, o.part_result), ("imports", None))
        o = E.parse(["--auto", r"C:\in\x.pdf", "--program", "Bachelor's", "--today", "2026-10-28",
                     "--report-root", r"C:\r", "--auto-result", r"C:\r\auto.json"])
        self.assertEqual((o.role, o.program, o.report_root, o.auto_result),
                         ("auto", "BACHELOR", r"C:\r", r"C:\r\auto.json"))

    def test_bad(self):
        bad = [["--cli", r"C:\x.pdf", "--program", "KLP"], ["--cli", r"C:\x.pdf", "--out", r"C:\o"],
               ["--cli", r"C:\x.pdf", "--program", "PHD", "--out", r"C:\o"],
               ["--cli", r"C:\x.pdf", "--program", "KLP", "--out", "relative"],
               ["--cli", r"C:\x.pdf", "--program", "KLP", "--out", r"C:\o", "--today", "28/10/2026"],
               ["--worker"], ["--worker", "job.json"], ["--worker", r"C:\j.json", "--cli", r"C:\x.pdf"],
               ["--selftest", "extra"], ["--selftest-part"], ["--nonsense"], ["--program"],
               ["--out", r"C:\o", "--out", r"C:\p"], ["--selftest", "--gpu-lock-extra", r"C:\l"],
               ["--data-dir", "rel"]]
        for argv in bad:
            with self.subTest(argv=argv):
                with self.assertRaises(E.BadArgs):
                    E.parse(argv)

    def test_programs(self):
        for raw, want in (("klp", "KLP"), ("EAP", "EAP"), ("Master's", "MASTER"), ("bachelor\u2019s", "BACHELOR"),
                          ("MASTER", "MASTER")):
            self.assertEqual(E.normalise_program(raw), want)


class CliLocks(unittest.TestCase):
    def setUp(self):
        self.data = Path(F.scratch("boot_locks"))

    def tearDown(self):
        F.remove(str(self.data))

    def test_settings_default_and_extras(self):
        extra = str(self.data / "fake" / "auto_verify.lock")
        self.assertEqual(E.lock_paths_for_cli(self.data, [extra], default_setting=None), [extra])
        self.assertEqual(E.lock_paths_for_cli(self.data, [], default_setting=r"C:\made\up\x.lock"),
                         [r"C:\made\up\x.lock"])
        (self.data / "settings.json").write_text(json.dumps({"gpu_lock_path": r"C:\made\up\y.lock"}), "utf-8")
        self.assertEqual(E.lock_paths_for_cli(self.data, [extra], default_setting=r"C:\made\up\x.lock"),
                         [r"C:\made\up\y.lock", extra])
        (self.data / "settings.json").write_text(json.dumps({"gpu_lock_path": None}), "utf-8")   # turned off
        self.assertEqual(E.lock_paths_for_cli(self.data, [], default_setting=r"C:\made\up\x.lock"), [])


class CliChild(unittest.TestCase):
    """``--cli`` through the real entry in a child: the card environment, the protocol channel, boot, and the
    (fake) worker's run_job in that same process."""

    def setUp(self):
        self.dir = F.scratch("boot_cli")
        self.data = os.path.join(self.dir, "data")
        self.input = os.path.join(self.dir, "in", "MADE-UP.pdf")
        os.makedirs(os.path.dirname(self.input))
        Path(self.input).write_bytes(b"%PDF-1.4 made-up")

    def tearDown(self):
        F.remove(self.dir)

    def run_cli(self, scenario, *extra, env_extra=None, argv=None):
        env = F.child_env(self.data, FAKE_SCENARIO=scenario, CUDA_PATH=r"C:\made\up\cuda", PYTORCH_FAKE_SETTING="1",
                          **(env_extra or {}))
        env.pop("OPENBLAS_NUM_THREADS", None)
        args = argv if argv is not None else [
            "--cli", self.input, "--program", "eap", "--out", os.path.join(self.dir, "out", "case"),
            "--today", "2026-10-28", "--data-dir", self.data, *extra]
        r = subprocess.run(F.fake_cmd("entry", *args), capture_output=True, timeout=120, env=env,
                           stdin=subprocess.DEVNULL, cwd=self.dir)
        lines = r.stdout.splitlines()
        evs = [P.decode(x) for x in lines]
        self.assertNotIn(None, evs, f"a stdout line is not an event: {lines}")
        return r, evs

    def test_done_stream(self):
        r, evs = self.run_cli("done")
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        names = [e["ev"] for e in evs]
        self.assertEqual(names[0], "hello")
        self.assertEqual(names[-1], "done")
        self.assertNotIn("ocr", names)
        for e in evs:
            self.assertEqual(P.validate(e), [], e)
        pages = [(e["done"], e["of"], e["where"]) for e in evs if e["ev"] == "page"]
        self.assertEqual(pages, [(1, 2, "card"), (2, 2, "card"), (1, 1, "runner")])
        self.assertEqual(evs[-1]["gpu"]["card"], F.CARD_NAME)
        self.assertEqual([e["state"] for e in evs if e["ev"] == "card"], ["found", "ready"])
        self.assertIn(b"a stray print that must land in the log", r.stderr)
        self.assertIn(b"boot: role cli", r.stderr)

    def test_job_environment_and_working_folder(self):
        extra = os.path.join(self.dir, "fakelock", "auto_verify.lock")
        r, evs = self.run_cli("echo_job", "--gpu-lock-extra", extra)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        info = [e for e in evs if e["ev"] == "jobinfo"][0]
        self.assertEqual(info["program"], "EAP")
        self.assertEqual(info["today"], "2026-10-28")
        self.assertEqual(info["gpu"], {"lock_paths": [extra], "index": 0, "cap_gb": 3.0, "need_free_mb": 4096})
        self.assertEqual(info["cache_tail"], "cache")
        self.assertTrue(info["cwd_is_data"])
        self.assertTrue(info["tmp_is_data"])
        self.assertEqual((info["visible"], info["order"], info["openblas"], info["leftover"]),
                         ("0", "PCI_BUS_ID", "1", None))

    def test_settings_lock_is_watched(self):
        os.makedirs(self.data, exist_ok=True)
        lock = os.path.join(self.dir, "set", "auto_verify.lock")
        Path(self.data, "settings.json").write_text(json.dumps({"gpu_lock_path": lock}), "utf-8")
        r, evs = self.run_cli("echo_job")
        info = [e for e in evs if e["ev"] == "jobinfo"][0]
        self.assertEqual(info["gpu"]["lock_paths"], [lock])

    def test_card_error_exit_6(self):
        r, evs = self.run_cli("card_error")
        self.assertEqual(r.returncode, 6)
        self.assertEqual(evs[-1]["ev"], "error")
        self.assertEqual((evs[-1]["code"], evs[-1]["values"]), ("DRIVER_TOO_OLD", {"driver": "000.00"}))

    def test_crash_inside_run_job(self):
        r, evs = self.run_cli("raise")
        self.assertEqual(r.returncode, 1)
        self.assertEqual((evs[-1]["ev"], evs[-1]["code"]), ("error", "CRASH"))
        self.assertNotIn("made-up failure", evs[-1]["text"])               # the type only, never the message

    def test_run_job_without_a_final_event(self):
        r, evs = self.run_cli("no_terminal")
        self.assertEqual(r.returncode, 1)
        self.assertEqual((evs[-1]["ev"], evs[-1]["code"]), ("error", "CRASH"))

    def test_bad_arguments_give_one_line(self):
        r, evs = self.run_cli("done", argv=["--cli", self.input, "--out", os.path.join(self.dir, "o")])
        self.assertEqual(r.returncode, 2)
        self.assertEqual(len(evs), 1)
        self.assertEqual((evs[0]["ev"], evs[0]["code"]), ("error", "BAD_ARGS"))

    def test_worker_with_a_bad_job(self):
        r, evs = self.run_cli("done", argv=["--worker", os.path.join(self.dir, "no-such-job.json"),
                                            "--data-dir", self.data])
        self.assertEqual(r.returncode, 2)
        self.assertEqual([e["ev"] for e in evs], ["hello", "error"])
        self.assertEqual(evs[-1]["code"], "BAD_ARGS")
        self.assertNotIn(self.dir, evs[-1]["text"])


_DISPATCH = r"""
import json, os, sys, types
from src.verify.runner.app.tests import fake_worker as F
F.install_fakes()
out = os.environ["DISPATCH_OUT"]

def record(**kw):
    kw.update(visible=os.environ.get("CUDA_VISIBLE_DEVICES"), cuda_path=os.environ.get("CUDA_PATH"),
              cwd=os.getcwd(), heavy=sorted(m for m in ("numpy", "torch", "cv2") if m in sys.modules))
    with open(out, "w", encoding="utf-8") as f:
        json.dump(kw, f)

st = types.ModuleType("fake_selftest")
st.run_part = lambda name, result: record(call="run_part", name=name, result=result) or 0
st.main = lambda report_dir=None: record(call="main", report_dir=report_dir) or 0
win = types.ModuleType("fake_window")
win.main = lambda opts: record(call="window", role=opts.role, program=opts.program, path=opts.path,
                               locks=opts.lock_extra) or 0
sys.modules["fake_selftest"], sys.modules["fake_window"] = st, win
from src.verify.runner.app import boot, entry
entry.SELFTEST_MODULE, entry.WINDOW_MODULE = "fake_selftest", "fake_window"
boot.MUTEX_NAME = os.environ["DISPATCH_MUTEX"]
held = boot.take_single_instance() if os.environ.get("DISPATCH_HOLD") else None
sys.exit(entry.main(sys.argv[1:]))
"""


class Dispatch(unittest.TestCase):
    """The other roles through the real entry in a child, with fake selftest / window modules."""

    def setUp(self):
        self.dir = F.scratch("boot_dispatch")
        self.data = os.path.join(self.dir, "data")
        self.out = os.path.join(self.dir, "dispatch.json")

    def tearDown(self):
        F.remove(self.dir)

    def run_entry(self, *argv, hold=False):
        env = F.child_env(self.data, CUDA_PATH=r"C:\made\up\cuda", DISPATCH_OUT=self.out,
                          DISPATCH_MUTEX="Local\\HDC-unit-test-" + uuid.uuid4().hex)
        if hold:
            env["DISPATCH_HOLD"] = "1"
        r = subprocess.run([sys.executable, "-I", "-B", "-c", _DISPATCH, *argv, "--data-dir", self.data],
                           capture_output=True, text=True, timeout=120, env=env, stdin=subprocess.DEVNULL,
                           cwd=self.dir)
        got = None
        if os.path.exists(self.out):
            with open(self.out, encoding="utf-8") as f:
                got = json.load(f)
            os.remove(self.out)
        return r, got

    def test_selftest_part_card_gets_the_card_environment(self):
        res = os.path.join(self.dir, "card.json")
        r, got = self.run_entry("--selftest-part", "card", res)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        self.assertEqual((got["call"], got["name"], got["result"]), ("run_part", "card", res))
        self.assertEqual((got["visible"], got["cuda_path"]), ("0", None))
        self.assertEqual(os.path.normcase(got["cwd"]), os.path.normcase(self.data))
        self.assertEqual(got["heavy"], [])

    def test_other_parts_keep_the_environment(self):
        r, got = self.run_entry("--selftest-part", "runtime", os.path.join(self.dir, "runtime.json"))
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        self.assertEqual(got["cuda_path"], r"C:\made\up\cuda")
        r, got = self.run_entry("--selftest", "--report-dir", os.path.join(self.dir, "reports"))
        self.assertEqual((got["call"], got["report_dir"]), ("main", os.path.join(self.dir, "reports")))

    def test_window_roles_and_the_single_instance(self):
        r, got = self.run_entry("--auto", os.path.join(self.dir, "in.pdf"), "--program", "master's",
                                "--gpu-lock-extra", os.path.join(self.dir, "x.lock"))
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        self.assertEqual((got["call"], got["role"], got["program"]), ("window", "auto", "MASTER"))
        self.assertEqual(got["locks"], [os.path.join(self.dir, "x.lock")])
        self.assertEqual(got["heavy"], [])
        r, got = self.run_entry("--auto", os.path.join(self.dir, "in.pdf"), "--program", "KLP", hold=True)
        self.assertEqual(r.returncode, 1)                                   # already open: no box in --auto
        self.assertIsNone(got)


class ImportWeight(unittest.TestCase):
    def test_entry_imports_no_numpy_or_torch(self):
        code = ("import sys\nfrom src.verify.runner.app import entry, controller\n"
                "print(sorted(m for m in ('numpy', 'torch', 'cv2', 'pymupdf', 'fitz', 'PIL') if m in sys.modules))")
        r = subprocess.run([sys.executable, "-I", "-B", "-c", code], capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        self.assertEqual(r.stdout.strip(), "[]")


if __name__ == "__main__":
    unittest.main()
