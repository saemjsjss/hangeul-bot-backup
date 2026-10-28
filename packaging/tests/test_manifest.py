"""Unit tests of packaging\\manifest.py and packaging\\build.py (Task F; APP_SPEC r1 1.1, r2 R2.4.6, R2.5, R2.6).
Made-up app folders and build trees under C:\\Hangeul\\DOCCHECK\\lab\\private-runner\\selftest\\app\\F. Every child
process is faked: no PyInstaller, no frozen app, no card, no ISCC. Task H's cardrun and Task G's build_installer are
replaced by made-up modules loaded through build.load_module.

    python -I -B -m unittest discover -s <worktree>\\packaging\\tests -p test_manifest.py
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest import mock

PKG = Path(__file__).resolve().parents[1]
for p in (str(PKG), str(Path(__file__).resolve().parent)):
    if p not in sys.path:
        sys.path.insert(0, p)

import build  # noqa: E402
import make_icon  # noqa: E402
import manifest  # noqa: E402
import stage  # noqa: E402
import test_stage as fakes  # noqa: E402  (the made-up source world; its TestCases are not collected here)

ROOT = Path(r"C:\Hangeul\DOCCHECK\lab\private-runner\selftest\app\F")
BID = "20261029.1-abc1234"
APP = "Hangeul Document Checker"


def fresh(name: str) -> Path:
    d = ROOT / "mb" / name
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    return d


def put(path: Path, data: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, bytes):
        path.write_bytes(data)
    else:
        path.write_text(data, encoding="utf-8")
    return path


def app_folder(root: Path) -> Path:
    app = root / APP
    put(app / f"{APP}.exe", b"MZ made-up exe")
    put(app / "_internal" / "BUILD.json", '{"build_id": "x"}')
    put(app / "_internal" / "torch" / "lib" / "c10.dll", b"MZ" + b"\0" * 300)
    put(app / "_internal" / "engine" / "easyocr" / "user_network" / ".keep", b"")
    put(app / "_internal" / "selftest" / "selftest_pages.pdf", b"%PDF made-up")
    return app


class ManifestTests(unittest.TestCase):

    def setUp(self) -> None:
        self.app = app_folder(fresh(self._testMethodName))

    def test_write_lists_every_file_but_itself(self) -> None:
        d = manifest.write(self.app, build_id=BID, app_version="1.0.0")
        on_disk = json.loads((self.app / "_internal" / "MANIFEST.json").read_text(encoding="utf-8"))
        self.assertEqual(d, on_disk)
        self.assertEqual(d["schema"], "hdc-manifest/1")
        self.assertEqual(d["build_id"], BID)
        self.assertEqual(sorted(d["files"]), sorted([f"{APP}.exe", "_internal/BUILD.json", "_internal/torch/lib/c10.dll",
                                                     "_internal/engine/easyocr/user_network/.keep",
                                                     "_internal/selftest/selftest_pages.pdf"]))
        self.assertNotIn("_internal/MANIFEST.json", d["files"])
        c10 = self.app / "_internal" / "torch" / "lib" / "c10.dll"
        self.assertEqual(d["files"]["_internal/torch/lib/c10.dll"],
                         {"size": 302, "sha256": hashlib.sha256(c10.read_bytes()).hexdigest()})
        self.assertEqual(d["count"], 5)
        self.assertEqual(d["bytes"], sum(v["size"] for v in d["files"].values()))
        self.assertEqual(manifest.build(self.app, build_id=BID, app_version="1.0.0"), d)
        self.assertEqual(manifest.verify(self.app, manifest.load(self.app / manifest.REL_PATH)), [])

    def test_verify_finds_each_kind_of_change(self) -> None:
        m = manifest.write(self.app, build_id=BID, app_version="1.0.0")
        (self.app / "_internal" / "selftest" / "selftest_pages.pdf").unlink()
        put(self.app / "_internal" / "BUILD.json", '{"build_id": "y"}')                  # same size, other bytes
        put(self.app / "_internal" / "torch" / "lib" / "c10.dll", b"MZ")                # other size
        put(self.app / "_internal" / "new.dll", b"MZ")
        problems = manifest.verify(self.app, m)
        self.assertEqual(problems, ["changed _internal/BUILD.json", "missing _internal/selftest/selftest_pages.pdf",
                                    "size _internal/torch/lib/c10.dll", "extra _internal/new.dll"])
        self.assertEqual(manifest.verify(self.app, m, full=False, allow_extra=True),
                         ["missing _internal/selftest/selftest_pages.pdf", "size _internal/torch/lib/c10.dll"])

    def test_cli_and_load(self) -> None:
        self.assertEqual(manifest.main(["write", str(self.app), "--build-id", BID]), 0)
        self.assertEqual(manifest.main(["verify", str(self.app)]), 0)
        put(self.app / "_internal" / "BUILD.json", '{"build_id": "z"}')
        self.assertEqual(manifest.main(["verify", str(self.app), "--sizes-only"]), 0)
        self.assertEqual(manifest.main(["verify", str(self.app)]), 1)
        bad = put(self.app.parent / "bad.json", '{"files": []}')
        with self.assertRaises(ValueError):
            manifest.load(bad)

    def test_a_junction_inside_is_refused(self) -> None:
        try:
            import _winapi
            _winapi.CreateJunction(str(self.app.parent), str(self.app / "_internal" / "loop"))
        except (ImportError, OSError, AttributeError):
            self.skipTest("junctions are not available here")
        try:
            with self.assertRaisesRegex(ValueError, "link or junction"):
                manifest.build(self.app, build_id=BID, app_version="1.0.0")
        finally:
            os.rmdir(self.app / "_internal" / "loop")                # removes the junction only, never its target


# ---------------------------------------------------------------------------------------------------------------

SELFTEST_TXT = """HANGEUL DOCUMENT CHECKER - SELF-TEST
App 1.0.0, build 20261029.1-abc1234 | rules: bot 2000-01-01.1 + runner 2000-01-02.r1 | runner 0.0.9
Run: 2026-10-29 01:00 | overall: {overall} ({ok} of 9 parts)
Graphics card: NVIDIA GeForce RTX 0000 | 8.0 GB total, 6.0 GB free | driver 000.00 (CUDA 12.9) | compute 12.0 (this build: 7.5-12.0)
Reading engine: torch 2.11.0+cu128, CUDA 12.8, cuDNN 9.19.0 | cap 3.0 GB
PARTS
  integrity  OK    312 files, 0 missing, 0 changed
  card       {card}    reader on cuda:0; CER 0.4% / 0.9% / 0.6% / 0.4%; {code}(build PC, same card model: none)
WARNINGS
  (none)
"""

FAKE_CARDRUN = '''
import json, os
from pathlib import Path

def _run(cmd, timeout, kw):
    if RAISE:
        raise RuntimeError("cardrun needs the orchestrator's approval Q-O1")
    rd = Path(cmd[cmd.index("--report-dir") + 1])
    dd = Path(cmd[cmd.index("--data-dir") + 1])
    rd.mkdir(parents=True, exist_ok=True)
    if TEXT:
        (rd / "SELFTEST 2026-10-29 0100.txt").write_text(TEXT, encoding="utf-8")
    if SUMMARY is not None:
        (dd / "selftest").mkdir(parents=True, exist_ok=True)
        (dd / "selftest" / "selftest.json").write_text(json.dumps(SUMMARY), encoding="utf-8")
    (rd / "cardrun_call.json").write_text(json.dumps({"cmd": cmd, "timeout": timeout, "kw": kw,
                                                      "data": os.environ.get("HDC_DATA_DIR"),
                                                      "no_card": os.environ.get("HDC_TEST_NO_CARD"),
                                                      "bundle": os.environ.get("HDC_BUNDLE_DIR")}), encoding="utf-8")
    return RESULT
'''
RUN_PLAIN = "\ndef run(cmd, timeout):\n    return _run(cmd, timeout, {})\n"
RUN_KW = ("\ndef run(cmd, timeout, *, what=None, log_dir=None, env=None):\n"
          "    return _run(cmd, timeout, {'what': what, 'log_dir': str(log_dir)})\n")


def summary(ok: bool = True, ok_parts: int = 9, build_id: str = BID) -> Dict[str, Any]:
    """A made-up selftest.json in Task D's hdc-selftest/1 shape."""
    names = ("integrity", "runtime", "core", "pdf", "card", "qr", "excel", "tk", "guard")
    return {"schema": "hdc-selftest/1", "run": "2026-10-29 01:00",
            "app": {"app_version": "1.0.0", "build_id": build_id},
            "ok": ok, "ok_parts": ok_parts,
            "parts": [{"part": n, "status": "OK", "code": None, "seconds": 1.0} for n in names],
            "card": {"card_name": "NVIDIA GeForce RTX 0000", "torch": "2.11.0+cu128", "cuda": "12.8",
                     "cudnn": "9.19.0", "read_code": "0A1B-2C3D"},
            "nvml": {"name": "NVIDIA GeForce RTX 0000", "driver": "000.00", "cc": [12, 0]}, "warnings": []}

FAKE_INSTALLER = '''
import json
from pathlib import Path

def main(argv):
    Path(OUT).write_text(json.dumps(list(argv)), encoding="utf-8")
    return 0
'''


class FakeRunner:
    """Stands in for stage.run_low; per-command exit codes by a key word in the command."""

    def __init__(self, codes: Optional[Dict[str, int]] = None, on_freeze: Any = None) -> None:
        self.calls: List[Dict[str, Any]] = []
        self.codes = codes or {}
        self.on_freeze = on_freeze

    def __call__(self, cmd: List[str], **kw: Any) -> stage.Ran:
        cmd = [str(c) for c in cmd]
        self.calls.append({"cmd": cmd, **kw})
        if "PyInstaller" in cmd and self.on_freeze:
            self.on_freeze(cmd)
        rc = 0
        for word, code in self.codes.items():
            if any(word in c for c in cmd):
                rc = code
        if "--selftest-part" in cmd and cmd[cmd.index("--selftest-part") + 1] == "imports":
            # like the frozen child (selftest.run_part): it writes its part result, exit 0 once written
            status = "FAIL" if self.codes.get("imports-fail") else "OK"
            Path(cmd[-1]).write_text(json.dumps({"schema": "hdc-selftest-part/1", "part": "imports",
                                                 "status": status, "detail": "made up"}), encoding="ascii")
        return stage.Ran(cmd, rc, 1.5)


class BuildTests(unittest.TestCase):

    def setUp(self) -> None:
        self.root = fresh(self._testMethodName)
        self.pkg = self.root / "pkg"
        self.logs: List[str] = []
        self.runner = FakeRunner()
        self.ctx = build.Ctx(builds=self.root / "builds", pkg=self.pkg, python=Path(r"C:\made-up\bpy\python.exe"),
                             run=self.runner, log=self.logs.append)

    # --- helpers
    def frozen(self, bid: str = BID) -> build.Layout:
        L = self.ctx.layout(bid)
        for d in (L.stage, L.work, L.dist, L.installer, L.reports):
            d.mkdir(parents=True)
        put(L.stage / "BUILD.json", json.dumps({"build_id": bid, "app_version": "1.0.0"}))
        app_folder(L.dist)
        shutil.copyfile(L.stage / "BUILD.json", L.internal / "BUILD.json")
        put(L.reports / "stage.json", '{"ok": true}')
        put(L.reports / "freeze.json", '{"ok": true}')
        return L

    def tools(self, result: Any = 0, overall: str = "OK", ok: int = 9, card: str = "OK",
              code: str = "READ CODE 51C2-0A7E ", summary: Optional[Dict[str, Any]] = None, kw: bool = False,
              raise_: bool = False, text: bool = True) -> None:
        put(self.pkg / "tests" / "scan_bundle.py", "raise SystemExit(0)\n")
        put(self.pkg / "tests" / "dll_check.py", "raise SystemExit(0)\n")
        txt = SELFTEST_TXT.format(overall=overall, ok=ok, card=card, code=code) if text else ""
        put(self.pkg / "tests" / "cardrun.py", f"TEXT = {txt!r}\nRESULT = {result!r}\nSUMMARY = {summary!r}\n"
                                               f"RAISE = {raise_!r}\n" + FAKE_CARDRUN + (RUN_KW if kw else RUN_PLAIN))

    # --- tests
    def test_layout_and_ids(self) -> None:
        L = self.ctx.layout(BID)
        self.assertEqual(L.root, self.root / "builds" / BID)
        self.assertEqual(L.exe, L.root / "dist" / APP / f"{APP}.exe")
        self.assertEqual(L.internal, L.root / "dist" / APP / "_internal")
        with self.assertRaises(ValueError):
            self.ctx.layout("..\\x")
        self.assertEqual(build.main(["freeze", "--build-id", "nope"], ctx=self.ctx), 2)
        self.assertEqual(build.main(["dance"], ctx=self.ctx), 2)
        self.assertEqual(build.BUILDS, Path(r"C:\Hangeul\DOCCHECK\lab\app-build\builds"))
        self.assertEqual(build.PYI_CONFIG, Path(r"C:\Hangeul\DOCCHECK\lab\app-build\pyi-config"))

    def test_build_python_and_pth(self) -> None:
        with self.assertRaisesRegex(build.BuildError, "build env's python"):
            build.require_build_python(Path(sys.executable))
        build.require_build_python(build.BPY)
        pth = put(self.root / "hdc_build.pth", "C:\\a\\site-packages\nC:\\b\\site-packages\n")
        self.assertEqual(build.site_dirs_from_pth(pth), [Path("C:\\a\\site-packages"), Path("C:\\b\\site-packages")])
        put(pth, "C:\\a\n")
        with self.assertRaisesRegex(build.BuildError, "exactly two"):
            build.site_dirs_from_pth(pth)

    def test_stage_command(self) -> None:
        f = fakes.Fake("mb_stage_world")
        git = lambda cmd, **kw: subprocess.CompletedProcess(  # noqa: E731
            cmd, 0, "abc1234\n" if "rev-parse" in cmd else " M x\n", "")
        self.ctx.sources, self.ctx.stage_tools = f.sources(), f.tools()
        self.ctx.git_run, self.ctx.today = git, "20261029"
        with mock.patch.object(make_icon, "write_icon", lambda p, sizes=(): put(p, b"\0\0\1\0")):
            bid = build.cmd_stage(self.ctx)
            self.assertEqual(bid, "20261029.1-abc1234-dirty")
            L = self.ctx.layout(bid)
            for d in (L.stage, L.work, L.dist, L.installer, L.reports):
                self.assertTrue(d.is_dir())
            self.assertTrue(json.loads((L.reports / "stage.json").read_text(encoding="utf-8"))["ok"])
            self.assertEqual(json.loads((L.stage / "BUILD.json").read_text(encoding="utf-8"))["build_id"], bid)
            with self.assertRaisesRegex(build.BuildError, "staged once"):
                build.cmd_stage(self.ctx, bid)
            f2 = fakes.Fake("mb_stage_world2", git_rc=1)
            self.ctx.sources, self.ctx.stage_tools = f2.sources(), f2.tools()
            with self.assertRaisesRegex(build.BuildError, "stage: the bot modules differ"):
                build.cmd_stage(self.ctx)
            failed = self.ctx.layout("20261029.2-abc1234-dirty")
            self.assertFalse(json.loads((failed.reports / "stage.json").read_text(encoding="utf-8"))["ok"])

    def test_freeze_command(self) -> None:
        L = self.ctx.layout(BID)
        with self.assertRaisesRegex(build.BuildError, "run build.py stage"):
            build.cmd_freeze(self.ctx, BID)
        for d in (L.stage, L.reports):
            d.mkdir(parents=True)
        put(L.stage / "BUILD.json", json.dumps({"build_id": BID}))
        put(L.reports / "stage.json", '{"ok": true}')

        def make_dist(cmd: List[str]) -> None:
            app_folder(L.dist)
            shutil.copyfile(L.stage / "BUILD.json", L.internal / "BUILD.json")

        self.runner.on_freeze = make_dist
        build.cmd_freeze(self.ctx, BID)
        c = self.runner.calls[0]
        self.assertEqual(c["cmd"], [str(self.ctx.python), "-I", "-B", "-m", "PyInstaller", "--noconfirm", "--clean",
                                    "--distpath", str(L.dist), "--workpath", str(L.work), str(build.SPEC_FILE)])
        env = c["env"]
        self.assertEqual(env["HDC_STAGE"], str(L.stage))
        self.assertEqual(env["HDC_REPORTS"], str(L.reports))
        self.assertEqual(env["PYINSTALLER_CONFIG_DIR"], str(build.PYI_CONFIG))
        self.assertEqual(env["PYTHONDONTWRITEBYTECODE"], "1")
        self.assertEqual(env["CUDA_VISIBLE_DEVICES"], "-1")
        self.assertEqual(c["log_path"], L.reports / "freeze.log")
        self.assertEqual(c["cwd"], L.root)
        self.assertTrue(json.loads((L.reports / "freeze.json").read_text(encoding="utf-8"))["ok"])
        with self.assertRaisesRegex(build.BuildError, "frozen once"):
            build.cmd_freeze(self.ctx, BID)

    def test_freeze_failure(self) -> None:
        L = self.ctx.layout(BID)
        for d in (L.stage, L.reports):
            d.mkdir(parents=True)
        put(L.stage / "BUILD.json", "{}")
        put(L.reports / "stage.json", '{"ok": true}')
        self.runner.codes = {"PyInstaller": 1}
        with self.assertRaisesRegex(build.BuildError, "freeze failed"):
            build.cmd_freeze(self.ctx, BID)
        self.assertFalse(json.loads((L.reports / "freeze.json").read_text(encoding="utf-8"))["ok"])

    def test_check_command(self) -> None:
        L = self.frozen()
        self.tools()
        saved = os.environ.get("HDC_DATA_DIR")
        with mock.patch.dict(os.environ, {"HDC_TEST_NO_CARD": "1", "HDC_BUNDLE_DIR": "C:\\made-up\\bundle"}):
            rep = build.cmd_check(self.ctx, BID)
            self.assertEqual(os.environ.get("HDC_TEST_NO_CARD"), "1")              # restored afterwards
            self.assertEqual(os.environ.get("HDC_BUNDLE_DIR"), "C:\\made-up\\bundle")
        self.assertEqual(os.environ.get("HDC_DATA_DIR"), saved)
        self.assertTrue(rep["ok"])
        self.assertEqual(list(rep["steps"]), ["dist", "imports", "manifest", "scan_bundle", "dll_check", "selftest",
                                              "build_read"])
        smoke, scan, dll = self.runner.calls
        self.assertEqual(smoke["cmd"], [str(L.exe), "--selftest-part", "imports", str(L.reports / "imports.json")])
        self.assertEqual(smoke["env"]["HDC_DATA_DIR"], str(L.reports / "smoke-data"))
        self.assertEqual(smoke["env"]["CUDA_VISIBLE_DEVICES"], "-1")
        self.assertEqual(scan["cmd"], [str(self.ctx.python), "-I", "-B", str(self.pkg / "tests" / "scan_bundle.py"),
                                       str(L.app), "--stage-list", str(L.reports / "STAGE_LIST.txt"),
                                       "--json", str(L.reports / "scan_bundle.json")])
        self.assertEqual(dll["cmd"][3:], [str(self.pkg / "tests" / "dll_check.py"), str(L.app),
                                          "--json", str(L.reports / "dll_check.json")])
        call = json.loads((L.reports / "selftest" / "cardrun_call.json").read_text(encoding="utf-8"))
        self.assertEqual(call["cmd"], [str(L.exe), "--data-dir", str(L.reports / "selftest-data"), "--selftest",
                                       "--report-dir", str(L.reports / "selftest")])
        self.assertEqual(call["data"], str(L.reports / "selftest-data"))
        self.assertIsNone(call["no_card"])               # test-only switches never reach the frozen app
        self.assertIsNone(call["bundle"])
        self.assertNotIn("HDC_TEST_NO_CARD", smoke["env"])
        self.assertNotIn("HDC_BUNDLE_DIR", smoke["env"])
        self.assertEqual(call["timeout"], build.SELFTEST_TIMEOUT_S)
        self.assertEqual(call["kw"], {})
        br = json.loads((L.internal / "selftest" / "build_read.json").read_text(encoding="utf-8"))
        self.assertEqual(br, {"schema": "hdc-build-read/1", "read_code": "51C2-0A7E",
                              "card": "NVIDIA GeForce RTX 0000", "build_id": BID})
        m = manifest.load(L.internal / "MANIFEST.json")
        self.assertIn("_internal/selftest/build_read.json", m["files"])
        self.assertEqual(manifest.verify(L.app, m), [])
        saved_check = json.loads((L.reports / "check.json").read_text(encoding="utf-8"))
        self.assertTrue(saved_check["ok"])
        self.assertEqual(saved_check["steps"]["selftest"]["read_code"], "51C2-0A7E")

    def test_check_needs_the_imports_part_ok(self) -> None:
        """Integration: run_part exits 0 once its result is written, so the smoke is judged by imports.json."""
        L = self.frozen()
        self.tools()
        self.runner.codes = {"imports-fail": 1}          # the fake child writes status FAIL and exits 0
        with self.assertRaisesRegex(build.BuildError, "before the self-test"):
            build.cmd_check(self.ctx, BID)
        st = json.loads((L.reports / "check.json").read_text(encoding="utf-8"))["steps"]["imports"]
        self.assertEqual((st["ok"], st["status"]), (False, "FAIL"))

    def test_check_prefers_task_d_selftest_json(self) -> None:
        L = self.frozen()
        self.tools(summary=summary(), kw=True, text=False)
        rep = build.cmd_check(self.ctx, BID)
        self.assertEqual(rep["steps"]["selftest"]["source"], "selftest.json")
        br = json.loads((L.internal / "selftest" / "build_read.json").read_text(encoding="utf-8"))
        self.assertEqual(br, {"schema": "hdc-build-read/1", "read_code": "0A1B-2C3D",
                              "card": "NVIDIA GeForce RTX 0000", "cc": [12, 0], "driver": "000.00",
                              "torch": "2.11.0+cu128", "cuda": "12.8", "cudnn": "9.19.0", "build_id": BID})
        call = json.loads((L.reports / "selftest" / "cardrun_call.json").read_text(encoding="utf-8"))
        self.assertEqual(call["kw"], {"what": "build.py check: frozen --selftest", "log_dir": str(L.reports / "cardrun")})

    def test_check_refuses_a_selftest_of_another_build_or_short_of_9(self) -> None:
        self.frozen()
        self.tools(summary=summary(build_id="20261028.7-abc1234"), text=False)
        with self.assertRaisesRegex(build.BuildError, "9 of 9"):
            build.cmd_check(self.ctx, BID)
        b2 = "20261029.2-abc1234"
        self.frozen(b2)
        self.tools(summary=summary(ok=False, ok_parts=8, build_id=b2), text=False)
        with self.assertRaisesRegex(build.BuildError, "9 of 9"):
            build.cmd_check(self.ctx, b2)

    def test_check_records_a_refused_cardrun(self) -> None:
        self.frozen()
        self.tools(raise_=True)
        saved = os.environ.get("HDC_DATA_DIR")
        with self.assertRaisesRegex(build.BuildError, "could not run through cardrun"):
            build.cmd_check(self.ctx, BID)
        self.assertEqual(os.environ.get("HDC_DATA_DIR"), saved)
        steps = json.loads((self.ctx.layout(BID).reports / "check.json").read_text(encoding="utf-8"))["steps"]
        self.assertIn("Q-O1", steps["selftest"]["error"])
        self.assertFalse(steps["selftest"]["ok"])

    def test_check_stops_before_the_card_when_a_scan_fails(self) -> None:
        self.frozen()
        self.tools()
        self.runner.codes = {"scan_bundle": 1}
        with self.assertRaisesRegex(build.BuildError, "before the self-test"):
            build.cmd_check(self.ctx, BID)
        L = self.ctx.layout(BID)
        self.assertFalse((L.reports / "selftest" / "cardrun_call.json").exists())
        steps = json.loads((L.reports / "check.json").read_text(encoding="utf-8"))["steps"]
        self.assertFalse(steps["scan_bundle"]["ok"])
        self.assertTrue(steps["dll_check"]["ok"])
        self.assertNotIn("selftest", steps)

    def test_check_needs_9_of_9_and_a_read_code(self) -> None:
        self.frozen()
        self.tools(code="")
        with self.assertRaisesRegex(build.BuildError, "READ CODE"):
            build.cmd_check(self.ctx, BID)
        b2 = "20261029.2-abc1234"
        self.frozen(b2)
        self.tools(result=1, overall="FAIL", ok=8, card="FAIL")
        with self.assertRaisesRegex(build.BuildError, "9 of 9"):
            build.cmd_check(self.ctx, b2)
        self.assertFalse((self.ctx.layout(b2).internal / "selftest" / "build_read.json").exists())

    def test_check_needs_a_frozen_build_and_h_tools(self) -> None:
        with self.assertRaisesRegex(build.BuildError, "not frozen"):
            build.cmd_check(self.ctx, BID)
        self.frozen()
        with self.assertRaisesRegex(build.BuildError, "before the self-test"):
            build.cmd_check(self.ctx, BID)
        steps = json.loads((self.ctx.layout(BID).reports / "check.json").read_text(encoding="utf-8"))["steps"]
        self.assertIn("scan_bundle.py is missing", steps["scan_bundle"]["error"])

    def test_installer_command(self) -> None:
        L = self.frozen()
        out = self.root / "installer_argv.json"
        put(self.pkg / "installer" / "build_installer.py", f"OUT = {str(out)!r}\n" + FAKE_INSTALLER)
        for date, code in (("29-10-2026", "51C2-0A7E"), ("2026-10-29", "51c2-0a7e")):
            with self.assertRaises(build.BuildError):
                build.cmd_installer(self.ctx, BID, date, code)
        with self.assertRaisesRegex(build.BuildError, "check has not passed"):
            build.cmd_installer(self.ctx, BID, "2026-10-29", "51C2-0A7E")
        manifest.write(L.app, build_id=BID, app_version="1.0.0")
        put(L.reports / "check.json", json.dumps({"build_id": BID, "ok": True}))
        put(L.internal / "torch" / "lib" / "c10.dll", b"MZ changed after the check")
        with self.assertRaisesRegex(build.BuildError, "no longer matches MANIFEST"):
            build.cmd_installer(self.ctx, BID, "2026-10-29", "51C2-0A7E")
        manifest.write(L.app, build_id=BID, app_version="1.0.0")
        self.assertEqual(build.main(["installer", "--build-id", BID, "--check-date", "2026-10-29",
                                     "--this-pc-code", "51C2-0A7E"], ctx=self.ctx), 0)
        self.assertEqual(json.loads(out.read_text(encoding="utf-8")),
                         ["--build-id", BID, "--check-date", "2026-10-29", "--this-pc-code", "51C2-0A7E"])

    def test_helpers(self) -> None:
        self.assertEqual(build._rc(0), 0)
        self.assertEqual(build._rc(subprocess.CompletedProcess([], 3)), 3)
        self.assertEqual(build._rc({"exit": 4}), 4)
        self.assertIsNone(build._rc(True))
        self.assertIsNone(build._rc(None))
        p = build.parse_selftest(SELFTEST_TXT.format(overall="OK", ok=9, card="OK", code="READ CODE 51C2-0A7E "))
        self.assertEqual(p, {"overall": "OK", "ok_parts": 9, "parts": 9, "read_code": "51C2-0A7E",
                             "card": "NVIDIA GeForce RTX 0000"})
        self.assertEqual(build.main(["check", "--build-id", BID], ctx=self.ctx), 1)
        self.assertIn("not frozen", self.logs[-1])


if __name__ == "__main__":
    unittest.main()
