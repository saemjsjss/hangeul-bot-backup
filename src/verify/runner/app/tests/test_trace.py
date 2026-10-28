"""Task C: trace.py (APP_SPEC rev 1 section 8 c, revision 2 R2.6). The audit hook is permanent, so every armed
trace runs in a CHILD process (the runner venv, -I -B) that works only inside Task C's scratch folder and reports
its trace file. Made-up files only (a blank picture, an empty PDF page)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import unittest

from src.verify.runner.app import trace as T
from src.verify.runner.app.tests import fake_worker as F


def run_child(code: str, work: str) -> dict:
    env = F.child_env(os.path.join(work, "data"))
    r = subprocess.run([sys.executable, "-I", "-B", "-c", textwrap.dedent(code), work], capture_output=True,
                       text=True, timeout=180, env=env, cwd=work)
    if r.returncode != 0:
        raise AssertionError(f"child failed ({r.returncode}): {r.stderr[-3000:]}")
    return json.loads(r.stdout.strip().splitlines()[-1])


def events(path: str):
    return T.read(path)


def norm(p) -> str:
    return os.path.normcase(os.path.abspath(str(p)))


class Pure(unittest.TestCase):
    def test_wanted(self):
        for ev in ("open", "os.listdir", "os.scandir", "os.mkdir", "os.remove", "os.rename", "shutil.copyfile",
                   "shutil.rmtree", "ctypes.dlopen", "os.add_dll_directory", "winreg.OpenKey",
                   "winreg.OpenKey/result", "subprocess.Popen", "os.startfile", "socket.connect", "socket.__new__"):
            self.assertTrue(T.wanted(ev), ev)
        for ev in ("import", "exec", "compile", "object.__setattr__", "sys._getframe", "builtins.input"):
            self.assertFalse(T.wanted(ev), ev)

    def test_arg_forms(self):
        from pathlib import Path
        self.assertEqual(T._arg(Path(r"C:\made\up")), r"C:\made\up")
        self.assertEqual(T._arg(("127.0.0.1", 9)), ["127.0.0.1", 9])
        self.assertEqual(T._arg(object()), "<object>")
        self.assertEqual(T._arg(b"abc"), "abc")
        self.assertTrue(T._arg("x" * 5000).endswith("..."))

    def test_relative_folder_refused_before_anything_is_armed(self):
        with self.assertRaises(ValueError):
            T.arm("relative-trace-dir")
        self.assertIsNone(T.armed_dir())


class Armed(unittest.TestCase):
    def setUp(self):
        self.work = F.scratch("trace")

    def tearDown(self):
        F.remove(self.work)

    def test_audit_events_are_traced_and_the_trace_file_is_not(self):
        got = run_child("""
            import ctypes, json, os, shutil, socket, sys, winreg
            work = sys.argv[1]
            from src.verify.runner.app import spawn, trace
            path = trace.arm(os.path.join(work, "trace"))
            d = os.path.join(work, "files")
            os.mkdir(d)
            with open(os.path.join(d, "a.txt"), "w") as f:
                f.write("made-up")
            os.listdir(d)
            list(os.scandir(d))
            shutil.copyfile(os.path.join(d, "a.txt"), os.path.join(d, "b.txt"))
            os.rename(os.path.join(d, "b.txt"), os.path.join(d, "c.txt"))
            os.remove(os.path.join(d, "c.txt"))
            ctypes.WinDLL("kernel32")
            k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Software")
            winreg.CloseKey(k)
            s = socket.socket()
            s.close()
            print(json.dumps({"path": path, "dir": trace.armed_dir(), "stats": trace.stats(),
                              "cmd": spawn.self_command("--selftest", frozen=True, executable="a.exe")}))
        """, self.work)
        tdir = os.path.join(self.work, "trace")
        self.assertEqual(os.path.dirname(norm(got["path"])), norm(tdir))
        self.assertRegex(os.path.basename(got["path"]), r"^trace-\d+\.jsonl$")
        self.assertEqual(got["cmd"], ["a.exe", "--trace-opens", tdir, "--selftest"])
        self.assertEqual(got["stats"]["errors"], 0)
        evs = events(got["path"])
        names = [e["ev"] for e in evs]
        self.assertEqual(names[0], "trace.armed")
        for ev in ("open", "os.mkdir", "os.listdir", "os.scandir", "shutil.copyfile", "os.rename", "os.remove",
                   "ctypes.dlopen", "winreg.OpenKey", "socket.__new__"):
            self.assertIn(ev, names, ev)
        opened = [norm(e["args"][0]) for e in evs if e["ev"] == "open" and isinstance(e["args"][0], str)]
        self.assertIn(norm(os.path.join(self.work, "files", "a.txt")), opened)
        self.assertNotIn(norm(got["path"]), opened)                       # opened before the hook
        reg = [e for e in evs if e["ev"] == "winreg.OpenKey"][0]
        self.assertIn("Software", reg["args"])
        for e in evs:
            want = {"t", "pid", "ev", "args"} | ({"refused"} if e["ev"].startswith("socket.") else set())
            self.assertEqual(set(e), want)
        self.assertIsNone([e for e in evs if e["ev"] == "socket.__new__"][0]["refused"])   # no guard loaded

    def test_socket_lines_say_whether_the_guard_refused_them(self):
        got = run_child("""
            import json, os, sys, types
            work = sys.argv[1]
            from src.verify.runner.app import trace
            path = trace.arm(os.path.join(work, "trace"))
            fake = types.ModuleType("src.verify.runner.safety")
            state = {"on": None}
            fake.armed = lambda: state["on"]
            fake._NETWORK_EVENTS = frozenset({"socket.connect", "socket.getaddrinfo"})
            sys.modules["src.verify.runner.safety"] = fake
            sys.audit("socket.connect", None, ("127.0.0.1", 9))            # an audit event only: no network
            state["on"] = object()
            sys.audit("socket.connect", None, ("127.0.0.1", 9))
            sys.audit("socket.gethostname")
            print(json.dumps({"path": path}))
        """, self.work)
        socks = [(e["ev"], e["refused"]) for e in events(got["path"]) if e["ev"].startswith("socket.")]
        self.assertEqual(socks, [("socket.connect", False), ("socket.connect", True), ("socket.gethostname", False)])

    def test_native_file_use_is_wrapped_after_import(self):
        got = run_child("""
            import json, os, sys
            work = sys.argv[1]
            from src.verify.runner.app import trace
            path = trace.arm(os.path.join(work, "trace"))
            import numpy as np
            import PIL.Image
            import cv2
            try:
                import pymupdf
            except ImportError:
                import fitz as pymupdf
            png = os.path.join(work, "made-up.png")
            PIL.Image.new("L", (8, 8), 255).save(png)
            with PIL.Image.open(png) as im:
                im.load()
            cv2.imread(png)
            cv2.imwrite(os.path.join(work, "made-up2.png"), np.zeros((4, 4), np.uint8))
            doc = pymupdf.open()
            doc.new_page()
            pdf = os.path.join(work, "made-up.pdf")
            doc.save(pdf)
            doc.close()
            pymupdf.open(pdf).close()
            print(json.dumps({"path": path, "stats": trace.stats()}))
        """, self.work)
        evs = events(got["path"])
        by = {}
        for e in evs:
            by.setdefault(e["ev"], []).append(e["args"])
        png, pdf = norm(os.path.join(self.work, "made-up.png")), norm(os.path.join(self.work, "made-up.pdf"))
        self.assertIn(png, [norm(a[0]) for a in by.get("PIL.Image.save", [])])
        self.assertIn(png, [norm(a[0]) for a in by.get("PIL.Image.open", [])])
        self.assertIn(png, [norm(a[0]) for a in by.get("cv2.imread", [])])
        self.assertIn(norm(os.path.join(self.work, "made-up2.png")), [norm(a[0]) for a in by.get("cv2.imwrite", [])])
        self.assertIn(pdf, [norm(a[0]) for a in by.get("pymupdf.Document.save", [])])
        opened = [a[0] for a in by.get("pymupdf.open", [])]
        self.assertIn(None, opened)                                       # the new, empty document
        self.assertIn(pdf, [norm(a) for a in opened if a])
        self.assertEqual(got["stats"]["errors"], 0)

    def test_loader_without_a_dict_is_wrapped_by_a_proxy(self):
        got = run_child("""
            import importlib.abc, importlib.util, json, os, sys
            work = sys.argv[1]
            from src.verify.runner.app import trace

            class SlotLoader:                    # no __dict__: exec_module cannot be replaced on the instance
                __slots__ = ()
                def create_module(self, spec):
                    return None
                def exec_module(self, module):
                    module.read_native = lambda path, flag=0: ("read", path)

            class Finder(importlib.abc.MetaPathFinder):
                def find_spec(self, name, path=None, target=None):
                    if name == "hdc_made_up_native":
                        return importlib.util.spec_from_loader(name, SlotLoader())
                    return None

            sys.meta_path.append(Finder())
            path = trace.arm(os.path.join(work, "trace"),
                             targets={"hdc_made_up_native": (("read_native", "made_up.read"),)})
            import hdc_made_up_native as m
            r = m.read_native(os.path.join(work, "x.bin"))
            print(json.dumps({"path": path, "r": list(r), "proxy": type(m.__loader__).__name__,
                              "stats": trace.stats()}))
        """, self.work)
        self.assertEqual(got["r"][0], "read")
        self.assertEqual(got["proxy"], "_LoaderProxy")
        reads = [e["args"] for e in events(got["path"]) if e["ev"] == "made_up.read"]
        self.assertEqual([norm(a[0]) for a in reads], [norm(os.path.join(self.work, "x.bin"))])
        self.assertEqual(got["stats"]["errors"], 0)

    def test_module_imported_before_arm_is_patched_at_once(self):
        got = run_child("""
            import json, os, sys
            work = sys.argv[1]
            import PIL.Image
            from src.verify.runner.app import trace
            path = trace.arm(os.path.join(work, "trace"))
            again = trace.arm(os.path.join(work, "trace-other"))
            png = os.path.join(work, "early.png")
            PIL.Image.new("L", (4, 4), 0).save(png)
            print(json.dumps({"path": path, "again": again}))
        """, self.work)
        self.assertEqual(got["path"], got["again"])                       # idempotent
        evs = events(got["path"])
        saves = [norm(e["args"][0]) for e in evs if e["ev"] == "PIL.Image.save"]
        self.assertIn(norm(os.path.join(self.work, "early.png")), saves)
        self.assertFalse(os.path.exists(os.path.join(self.work, "trace-other")))


if __name__ == "__main__":
    unittest.main()
