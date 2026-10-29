"""APP_SPEC rev 1 section 8 a (and rev 2 R2.1.6): the places seam, the hooks and the APP guard.

DEV checks run in this process: every value the seam took over is exactly today's. The APP checks run in CHILD
processes (the runner venv, -I -B) with HDC_MODE=app and every HDC_* folder under
...\\private-runner\\selftest\\places\\<run>, so not one write lands outside private-runner even if the guard failed;
the run folder is removed afterwards. The real bot folder is never probed. Made-up data only.
"""
from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import sys
import textwrap
import time
import types
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np

from src.verify.runner import botimport, devplaces, engine, hooks, places, safety
from src.verify.runner.model import PAGE_TEXTS_SCHEMA, PageRead, Source, SourceFile

PKG = "src.verify.runner"
PLACES_ROOT = botimport.PRIVATE / "selftest" / "places"
# today's DEV values (runner 0.2.7, 344b342), written out once more: the seam must not change any of them
TODAY_WORKTREE = Path(r"C:\Hangeul\JARVIS\prototype\page-types")
TODAY_PRIVATE = Path(r"C:\Hangeul\DOCCHECK\lab\private-runner")
TODAY_BOT = Path(r"C:\Hangeul\BOT")
TODAY_ONNX_LAB = Path(r"C:\Hangeul\DOCCHECK\lab\onnx")


def _rmtree(p: Path) -> None:
    """Remove a scratch folder under private-runner; retried (a virus scanner may hold a new file for a moment)."""
    for _ in range(6):
        shutil.rmtree(p, ignore_errors=True)
        if not Path(p).exists():
            return
        time.sleep(0.5)


def _run_folder() -> Path:
    base = PLACES_ROOT / f"run-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    assert base.resolve().is_relative_to(TODAY_PRIVATE)
    base.mkdir(parents=True, exist_ok=False)
    return base


def _app_env(base: Path, **extra: str) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("HDC_")}
    env.update(HDC_MODE="app", HDC_DATA_DIR=str(base / "data"), HDC_BUNDLE_DIR=str(base / "bundle"),
               HDC_CODE_ROOT=str(TODAY_WORKTREE), **extra)
    return env


def _child(code: str, base: Path, env: dict) -> dict:
    r = subprocess.run([sys.executable, "-I", "-B", "-c", textwrap.dedent(code), str(base)], cwd=str(base),
                       capture_output=True, text=True, timeout=300, env=env)
    if r.returncode != 0:
        raise AssertionError(f"child failed ({r.returncode}): {r.stderr[-3000:]}")
    return json.loads(r.stdout.strip().splitlines()[-1])


def _fake_bundle(base: Path) -> None:
    """A made-up bundle: dummy weight files and glue data (never loaded)."""
    model = base / "bundle" / "engine" / "easyocr" / "model"
    model.mkdir(parents=True)
    for name in ("craft_mlt_25k.pth", "english_g2.pth"):
        (model / name).write_bytes(b"made-up weights")
    (base / "bundle" / "engine" / "glue_data.json").write_text("{}", encoding="utf-8")


# =============================================================================================================== DEV
class TestDevPlaces(unittest.TestCase):
    def setUp(self):
        if places.mode() != places.DEV:
            self.skipTest("these checks are for DEV mode")

    def test_current_equals_devplaces(self):
        P = places.current()
        D = devplaces
        self.assertIs(places.current(), P)                                  # cached
        self.assertEqual(P.mode, places.DEV)
        self.assertEqual(P.write_root, D.PRIVATE)
        self.assertEqual(P.cache_root, D.PRIVATE / "runner-data")
        self.assertEqual(P.temp_dir, D.PRIVATE / "tmp")
        self.assertEqual(P.models_dir, D.ONNX_LAB / "models")
        self.assertEqual(P.glue_data, D.ONNX_LAB / "pkg" / "glue_data.json")
        self.assertEqual(P.code_root, D.WORKTREE)
        self.assertEqual(P.forbidden, (D.BOT, D.VERIFIED))
        self.assertEqual(P.no_install, (D.NUMPY_VENV,))
        self.assertEqual(P.sibling_root, D.HANGEUL)
        self.assertEqual(P.no_open_root, D.BOT)
        self.assertEqual((P.write_label, P.no_open_label), (D.WRITE_LABEL, D.NO_OPEN_LABEL))
        self.assertEqual(P.out_rule, places.OUT_UNDER_WRITE_ROOT)
        self.assertIsNone(P.bundle)
        with self.assertRaises(Exception):
            P.write_root = Path("x")                                        # frozen

    def test_values_are_todays(self):
        self.assertEqual(botimport.WORKTREE, TODAY_WORKTREE)
        self.assertEqual(botimport.PRIVATE, TODAY_PRIVATE)
        self.assertEqual(botimport.DEFAULT_CACHE_ROOT, TODAY_PRIVATE / "runner-data")
        self.assertEqual(safety.PRIVATE, TODAY_PRIVATE)
        self.assertEqual(safety.BOT, TODAY_BOT)
        self.assertEqual(safety.HANGEUL, Path(r"C:\Hangeul"))
        self.assertEqual(safety.FORBIDDEN, [TODAY_BOT, Path(r"C:\Hangeul\VERIFIED STUDENT DOCUMENTS")])
        self.assertEqual(safety.NO_INSTALL, [TODAY_ONNX_LAB / ".venv-pkg-numpy"])
        self.assertEqual(safety.TMP_DIR, TODAY_PRIVATE / "tmp")
        self.assertEqual(safety.WRITE_ROOT, TODAY_PRIVATE)
        self.assertEqual(safety.BOT_ROOT, TODAY_BOT)
        self.assertFalse(safety.APP_MODE)
        self.assertEqual(engine.MODELS, TODAY_ONNX_LAB / "models")
        self.assertEqual(engine.GLUE_DATA, TODAY_ONNX_LAB / "pkg" / "glue_data.json")
        self.assertIsNone(engine.READER_FACTORY)
        self.assertEqual(devplaces.BOT_VENV_NAME, ".venv")

    def test_messages_are_todays(self):
        self.assertEqual(safety.WRITE_LABEL, "private-runner")
        self.assertEqual(safety.NO_OPEN_LABEL, "C:\\Hangeul\\BOT")
        fake_bot = PLACES_ROOT / "fakebot-never-created"                    # the real bot folder is never probed
        pol = safety._Policy(write_roots=[safety._norm(TODAY_PRIVATE)], allowed_files=[], forbidden=[],
                             bot_root=safety._norm(fake_bot), state=safety.SafetyState(None, None, {}))
        self.assertEqual(safety._write_refusal(pol, safety._norm(Path(r"C:\Users\Public\x.txt"))),
                         "write outside private-runner")
        with self.assertRaises(PermissionError) as cm:
            safety._check_bot(pol, "open", fake_bot / "x.json")
        self.assertIn("nothing under C:\\Hangeul\\BOT may be opened here", str(cm.exception))
        for fn in (lambda: safety.check_input(TODAY_BOT / "made-up.pdf"),
                   lambda: safety.check_out_dir(TODAY_PRIVATE / "runs" / "X", TODAY_BOT / "made-up.pdf")):
            with self.assertRaises(safety.SafetyError) as cm:
                fn()
            self.assertEqual(str(cm.exception), "the input must not lie under C:\\Hangeul\\BOT (the runner never "
                                                "opens it)")

    def test_log_and_facts_labels(self):
        from src.verify.runner import pipeline, report
        base = _run_folder()
        try:
            rs = pipeline._Run(out=base, clock=pipeline._Clock())
            r = SimpleNamespace(reading={}, case_id="B-0123456789abcdef", mode="bundle", pages=[],
                                safety={"blocked": [], "bot_opens": 0})
            m = SimpleNamespace(safety=SimpleNamespace(assert_writable=lambda p: None))
            pipeline._write_log(m, rs, r)
            lines = (base / "log.txt").read_text(encoding="utf-8").splitlines()
            self.assertIn("safety: blocked 0, opens under C:\\Hangeul\\BOT 0", lines)
        finally:
            _rmtree(base)
        rr = SimpleNamespace(reading={}, runner_version="0", schema="s", base_rules_version="b",
                             runner_rules_version="r", case_id="B-0", input="", mode="bundle", program="KLP",
                             today="2026-10-28", timings={}, safety={"blocked": [], "bot_opens": 0}, identity=None,
                             docs=[], cross=[])
        facts = dict(report._facts(rr, None))
        self.assertEqual(facts["safety: opens under C:\\Hangeul\\BOT"], "0")

    def test_mode_rules(self):
        with mock.patch.dict(os.environ, {"HDC_MODE": "APP"}):
            self.assertEqual(places.mode(), places.APP)
        with mock.patch.dict(os.environ, {"HDC_MODE": ""}):
            self.assertEqual(places.mode(), places.DEV)
        with mock.patch.object(sys, "frozen", True, create=True):
            self.assertEqual(places.mode(), places.APP)
        with mock.patch.dict(os.environ, {"HDC_DATA_DIR": str(PLACES_ROOT / "d")}):
            self.assertEqual(places.app_data_dir(), PLACES_ROOT / "d")
        with mock.patch.dict(os.environ, {"HDC_DATA_DIR": "", "LOCALAPPDATA": r"C:\Users\Public\LocalMadeUp"}):
            self.assertEqual(places.app_data_dir(), Path(r"C:\Users\Public\LocalMadeUp\Hangeul Document Checker"))
        with mock.patch.dict(os.environ, {"HDC_BUNDLE_DIR": ""}):
            self.assertEqual(places.bundle_dir(), TODAY_WORKTREE)              # the folder holding src
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "_MEIPASS", r"C:\Users\Public\MadeUpApp\_internal", create=True), \
                mock.patch.dict(os.environ, {"HDC_BUNDLE_DIR": str(PLACES_ROOT), "HDC_CODE_ROOT": str(PLACES_ROOT)}):
            self.assertEqual(places.bundle_dir(), Path(r"C:\Users\Public\MadeUpApp\_internal"))   # env ignored
            self.assertEqual(places._code_root(places.bundle_dir()), Path(r"C:\Users\Public\MadeUpApp\_internal"))


# ============================================================================================================= hooks
class Recorder:
    def __init__(self, raise_on=None):
        self.events = []
        self.raise_on = raise_on

    def __call__(self, event, **data):
        self.events.append((event, data))
        if self.raise_on is not None and self.raise_on(event, data):
            raise hooks.Cancelled()


@contextlib.contextmanager
def listening(rec):
    old = hooks.LISTENER
    hooks.LISTENER = rec
    try:
        yield rec
    finally:
        hooks.LISTENER = old


class TestHooks(unittest.TestCase):
    def test_emit_without_listener_is_a_no_op(self):
        self.assertIsNone(hooks.LISTENER)
        self.assertIsNone(hooks.emit("stage", key="ocr", state="start"))

    def test_listener_gets_the_event_and_its_exceptions_pass(self):
        with listening(Recorder()) as rec:
            hooks.emit("page", done=1, of=2)
        self.assertEqual(rec.events, [("page", {"done": 1, "of": 2})])
        self.assertTrue(issubclass(hooks.Cancelled, BaseException))
        self.assertFalse(issubclass(hooks.Cancelled, Exception))
        for exc in (hooks.Cancelled, ValueError):
            def boom(event, **data):
                raise exc()
            with listening(boom):
                with self.assertRaises(exc):
                    hooks.emit("x")

    def test_clock_emits_start_and_end(self):
        from src.verify.runner import pipeline
        clock = pipeline._Clock()
        with mock.patch.object(pipeline, "VERBOSE", False), listening(Recorder()) as rec:
            with clock("typing"):
                pass
        self.assertEqual([(e, d["key"], d["state"]) for e, d in rec.events],
                         [("stage", "typing", "start"), ("stage", "typing", "end")])
        self.assertIsInstance(rec.events[1][1]["seconds"], float)
        ran = []
        with mock.patch.object(pipeline, "VERBOSE", False), \
                listening(Recorder(lambda e, d: d.get("state") == "start")):
            with self.assertRaises(hooks.Cancelled):
                with clock("ocr"):
                    ran.append(1)
        self.assertEqual(ran, [])

    def test_pagelog_events(self):
        dv = types.SimpleNamespace(_ocr_image=lambda arr, turned=None: "MADE UP TEXT")
        log = engine.PageLog(dv)
        a = np.zeros((4, 3, 3), dtype=np.uint8)
        with listening(Recorder()) as rec:
            self.assertEqual(dv._ocr_image(a), "MADE UP TEXT")
            self.assertEqual(dv._ocr_image(a.copy()), "MADE UP TEXT")             # the same pixels: memo
        self.assertEqual([(e, d.get("phase"), d.get("memo")) for e, d in rec.events],
                         [("ocr", "start", None), ("ocr", "end", False), ("ocr", "end", True)])
        self.assertEqual(len(log.calls), 2)
        with listening(Recorder(lambda e, d: d.get("phase") == "start")):
            with self.assertRaises(hooks.Cancelled):
                dv._ocr_image(np.ones((4, 3, 3), dtype=np.uint8))

    def test_file_events(self):
        import pymupdf
        base = _run_folder()
        try:
            pdf = base / "MADE UP.pdf"
            d = pymupdf.open()
            p = d.new_page()
            p.insert_text((50, 80), "A MADE-UP TEXT LAYER LINE THAT IS LONGER THAN FORTY CHARACTERS", fontsize=10)
            d.new_page()                                                         # page 2: no text -> OCR page
            d.save(str(pdf))
            d.close()
            eng = object.__new__(engine.Engine)                              # no bot, no reader
            eng._files, eng._pages_by_engine, eng._cache_hits = {}, engine.Counter(), 0
            eng._read_missing = lambda path, missing, kinds, layers: ({i: "MADE UP" for i in missing}, set(),
                                                                      {i: 0.0 for i in missing})
            given = engine.PageTexts(pages={("MADE UP.pdf", 1): ("MADE UP", "ocr", False, 0.5)}, complete=False,
                                     engine="made-up")
            with listening(Recorder()) as rec:
                eng.read_file(pdf, "MADE UP.pdf", 0, given)               # page 1 is a text-layer page
            self.assertEqual(rec.events, [("file", {"name": "MADE UP.pdf", "pages": 2, "ocr": 0})])
            given1 = engine.PageTexts(pages={("MADE UP.pdf", 0): ("LAYER", "layer", False, 0.0)}, complete=False,
                                      engine="made-up")
            with listening(Recorder()) as rec:
                eng.read_file(pdf, "MADE UP.pdf", 0, given1)
            self.assertEqual(rec.events, [("file", {"name": "MADE UP.pdf", "pages": 2, "ocr": 1})])
            gimg = engine.PageTexts(pages={("PHOTO.jpg", 0): ("", "image", False, 0.1)}, complete=True,
                                    engine="made-up")
            with listening(Recorder()) as rec:
                eng._read_image(base / "PHOTO.jpg", "PHOTO.jpg", 3, gimg)
            self.assertEqual(rec.events, [("file", {"name": "PHOTO.jpg", "pages": 1, "ocr": 0})])
        finally:
            _rmtree(base)

    def test_budget_events(self):
        from src.verify.runner import birth, tin
        white = np.full((240, 320, 3), 255, dtype=np.uint8)
        with listening(Recorder()) as rec:
            birth.decode_qr_image(white, budget_s=-1.0, use_zbar=False)
            tin._stage_e(white, budget=-1.0)
            with mock.patch.object(tin, "_detect_boxes", return_value=[(0, 0, 40, 40)]):
                tin._stage_b(white, budget=-1.0)
        got = [(e, d["where"], d["page"]) for e, d in rec.events]
        self.assertIn(("budget", "birth.qr", None), got)
        self.assertIn(("budget", "tin.stage_e", None), got)
        self.assertIn(("budget", "tin.stage_b", None), got)
        self.assertEqual({e for e, _w, _p in got}, {"budget"})

    def test_cancelled_passes_through_pipeline_run(self):
        """A listener's Cancelled at the 'ocr' stage leaves pipeline.run as Cancelled (not RunCrashed, no crash
        log), and Engine.case's finally still runs. Every stage module is a fake."""
        from src.verify.runner import pipeline
        base = _run_folder()
        seen = {"case_closed": False}

        def mod(name):
            return types.ModuleType(f"{PKG}.{name}")

        sm, im, em = mod("safety"), mod("ingest"), mod("engine")
        sm.SafetyError = type("SafetyError", (Exception,), {})
        sm.prepare = lambda out, cache_root, input_path: SimpleNamespace(blocked=[], bot_opens=0, snapshots={})
        sm.assert_writable = lambda p: None
        sm.finish = lambda state: {}
        im.InputError = type("InputError", (Exception,), {})
        im.open_input = lambda path: Source(mode="bundle", root=Path(path), case_id="B-0123456789abcdef",
                                            files=[SourceFile(path=Path(path), name=Path(path).name, is_image=False,
                                                              n_pages=1, size=1, mtime=0.0)])
        im.stage = lambda src, docs_dir: {}

        class Engine:
            def __init__(self, cache_root, threads=0):
                self.bot = SimpleNamespace(dv=SimpleNamespace(notarised=lambda *a: False))

            def set_today(self, today):
                pass

            @contextlib.contextmanager
            def case(self, case_id):
                try:
                    yield SimpleNamespace(cache={})
                finally:
                    seen["case_closed"] = True

            def read_source(self, src, staged, given=None):
                raise AssertionError("the ocr stage must not start")

        em.Engine = Engine
        try:
            inp = base / "MADE UP BUNDLE.pdf"
            inp.write_bytes(b"made-up")
            out = base / "out"
            import datetime as dt
            with mock.patch.dict(sys.modules, {f"{PKG}.safety": sm, f"{PKG}.ingest": im, f"{PKG}.engine": em}), \
                    mock.patch.object(pipeline, "VERBOSE", False), \
                    listening(Recorder(lambda e, d: e == "stage" and d == {"key": "ocr", "state": "start"})) as rec:
                with self.assertRaises(hooks.Cancelled):
                    pipeline.run(inp, "KLP", out, today=dt.date(2026, 10, 28), cache_root=base / "cache")
            self.assertTrue(seen["case_closed"])
            self.assertFalse((out / "run.json").exists())
            self.assertFalse((out / "log.txt").exists())
            keys = [(d["key"], d["state"]) for e, d in rec.events if e == "stage"]
            self.assertEqual(keys[:2], [("safety", "start"), ("safety", "end")])
            self.assertEqual(keys[-1], ("ocr", "start"))
        finally:
            _rmtree(base)


# =============================================================================================================== APP
_APP_CHILD = r'''
import json, os, socket, subprocess, sys
from pathlib import Path
base = Path(sys.argv[1])
data, bundle, inp, elsewhere = base / "data", base / "bundle", base / "input", base / "elsewhere"
res = {}
from src.verify.runner import places
P = places.current()
res["mode"] = P.mode
res["fields"] = {k: str(getattr(P, k)) for k in ("write_root", "cache_root", "temp_dir", "models_dir", "glue_data",
                                                  "code_root", "bundle")}
res["empty"] = [list(P.forbidden), list(P.no_install), P.sibling_root, P.no_open_root]
res["labels"] = [P.write_label, P.no_open_label, P.out_rule]
from src.verify.runner import botimport, safety, engine, pipeline, report, birth, tin, doccheck, ingest, hooks
from src.verify.runner import __main__ as CLI
from src.verify.runner.model import PAGE_TEXTS_SCHEMA, Source, SourceFile
res["devplaces_loaded"] = "src.verify.runner.devplaces" in sys.modules
res["botimport"] = [str(botimport.WORKTREE), str(botimport.PRIVATE), str(botimport.DEFAULT_CACHE_ROOT)]
res["safety"] = [safety.APP_MODE, safety.BOT, safety.HANGEUL, safety.FORBIDDEN, safety.NO_INSTALL,
                 str(safety.TMP_DIR), str(safety.WRITE_ROOT), safety.BOT_ROOT, safety.WRITE_LABEL, safety.NO_OPEN_LABEL]
res["engine"] = [str(engine.MODELS), str(engine.GLUE_DATA), engine.READER_FACTORY is None]
res["forbidden_rules"] = [safety.is_forbidden(Path(r"C:\Hangeul\SAMPLE STUDENT DOCUMENTS\x.pdf")),
                          safety.is_forbidden(Path(r"C:\Hangeul\BOT\x"))]
# botimport: a cache root outside DATA raises; DATA\cache loads the worktree's bot code (code_root)
try:
    botimport.load(elsewhere / "cache")
    res["load_outside"] = "ok"
except RuntimeError:
    res["load_outside"] = "RuntimeError"
ns = botimport.load(P.cache_root)
res["load_root"] = str(ns.cache_root)
res["bot_paths_under_data"] = all(Path(p).resolve().is_relative_to(data.resolve())
                                  for p in (ns.dv.REPORT_DIR, ns.TC.TEXT_DIR, ns.TC.IMAGES_DIR))
# check_out_dir: the APP rules (pure checks, nothing created)
def out_err(out, i=inp):
    try:
        safety.check_out_dir(Path(out), Path(i))
        return "ok"
    except safety.SafetyError as e:
        return str(e)
drive = os.environ.get("SystemDrive", "C:") + "\\"
res["out"] = {
    "reports": out_err(base / "reports" / "c1"), "under_data": out_err(data / "reports" / "c2"),
    "inside_input": out_err(inp / "out"), "input_inside": out_err(base), "drive_root": out_err(drive),
    "windir": out_err(Path(os.environ["WINDIR"]) / "x"), "app_folder": out_err(bundle / "x"),
    "relative": out_err(Path("reports") / "c1"), "data_itself": out_err(data),
    "share": out_err(Path(r"\\example-host\share\x")), "rel_input": out_err(base / "reports" / "c1", Path("in")),
}
if os.environ.get("ProgramData"):
    res["out"]["programdata"] = out_err(Path(os.environ["ProgramData"]) / "x")
# __main__.check_args: the APP out rule (BadArgs), --cache-root under DATA
def cli(out):
    try:
        CLI.check_args(CLI.build_parser().parse_args([str(inp), "--program", "KLP", "--out", str(out),
                                                       "--cache-root", str(P.cache_root)]))
        return "ok"
    except CLI.BadArgs as e:
        return str(e)
res["cli"] = [cli(base / "reports" / "c4"), cli(inp / "o")]
# load_page_texts: a file under DATA is accepted, one outside DATA refused
f = inp / "PASSPORT.pdf"
src = Source(mode="folder", root=inp, case_id="F-0123456789abcdef",
             files=[SourceFile(path=f, name=f.name, is_image=False, n_pages=1, size=f.stat().st_size,
                               mtime=f.stat().st_mtime)])
doc = {"schema": PAGE_TEXTS_SCHEMA, "engine": "made-up", "mode": "folder", "complete": True,
       "files": [{"name": f.name, "sha256": ingest.file_sha256(f), "n_pages": 1,
                  "pages": [{"page": 1, "text": "MADE UP TEXT", "kind": "ocr", "sideways": False, "seconds": 0.5}]}]}
inside = P.cache_root / "pagetexts" / "F-0123456789abcdef.json"
inside.parent.mkdir(parents=True, exist_ok=True)
inside.write_text(json.dumps(doc), encoding="utf-8")
outside = elsewhere / "pt.json"
outside.parent.mkdir(parents=True, exist_ok=True)
outside.write_text(json.dumps(doc), encoding="utf-8")
pt = engine.load_page_texts(inside, src)
res["pt_inside"] = [pt.complete, pt.get("PASSPORT.pdf", 0)[0]]
try:
    engine.load_page_texts(outside, src)
    res["pt_outside"] = "ok"
except ingest.InputError:
    res["pt_outside"] = "InputError"
# the guard: prepare(out, DATA\cache, input)
out1 = base / "reports" / "c1"
state = safety.prepare(out1, P.cache_root, inp)
def attempt(name, fn, probe=None):
    try:
        fn()
        res[name] = "ok"
    except PermissionError as e:
        res[name] = "PermissionError: " + str(e)[:160]
    except Exception as e:
        res[name] = type(e).__name__ + ": " + str(e)[:160]
    if probe is not None:
        res[name + "_exists"] = probe.exists()
attempt("w_data", lambda: (data / "w.txt").write_text("x"))
attempt("w_out", lambda: (out1 / "w.txt").write_text("x"))
attempt("w_elsewhere", lambda: (elsewhere / "x.txt").write_text("x"), elsewhere / "x.txt")
attempt("w_input", lambda: (inp / "new.txt").write_text("x"), inp / "new.txt")
attempt("net", lambda: socket.create_connection(("127.0.0.1", 9), timeout=2))
attempt("spawn", lambda: subprocess.run([sys.executable, "-c", "pass"], timeout=30))
attempt("aw_out", lambda: safety.assert_writable(out1 / "docs"))
attempt("aw_elsewhere", lambda: safety.assert_writable(elsewhere / "y"))
attempt("aw_relative", lambda: safety.assert_writable(Path("rel")))
res["safe_dir"] = [str(birth._safe_dir(out1 / "work")), birth._safe_dir(elsewhere)]
state2 = safety.prepare(out1, P.cache_root, inp)
res["same_state"] = state2 is state and safety.armed() is state
attempt("w_out_after_second", lambda: (out1 / "w2.txt").write_text("x"))
attempt("w_elsewhere_after_second", lambda: (elsewhere / "x2.txt").write_text("x"), elsewhere / "x2.txt")
attempt("prepare_other", lambda: safety.prepare(base / "reports" / "c3", P.cache_root, inp))
res["finish"] = safety.finish(state)["input_unchanged"]
res["tmp"] = os.environ.get("TMP")
print(json.dumps(res, default=lambda o: None if o is None else str(o)))
'''

_WINDOW_CHILD = r'''
import json, socket, sys
from pathlib import Path
base = Path(sys.argv[1])
data, reports, elsewhere = base / "data", base / "reports", base / "elsewhere"
res = {}
from src.verify.runner import safety
def attempt(name, fn, probe=None):
    try:
        fn()
        res[name] = "ok"
    except PermissionError as e:
        res[name] = "PermissionError"
    except Exception as e:
        res[name] = type(e).__name__ + ": " + str(e)[:160]
    if probe is not None:
        res[name + "_exists"] = probe.exists()
attempt("allow_unarmed", lambda: safety.allow_file(elsewhere / "one.pdf"))
attempt("arm_relative", lambda: safety.arm_window([Path("reports")]))
state = safety.arm_window([reports])
res["mode"] = state.mode
attempt("w_reports", lambda: (reports / "x.txt").write_text("x"))
attempt("w_data", lambda: (data / "settings.json").write_text("{}"))
attempt("w_elsewhere", lambda: (elsewhere / "two.pdf").write_bytes(b"x"), elsewhere / "two.pdf")
attempt("allow_relative", lambda: safety.allow_file(Path("one.pdf")))
res["allowed"] = safety.allow_file(elsewhere / "one.pdf") == safety._norm(elsewhere / "one.pdf")
attempt("w_allowed", lambda: (elsewhere / "one.pdf").write_bytes(b"x"))
attempt("w_not_allowed", lambda: (elsewhere / "three.pdf").write_bytes(b"x"), elsewhere / "three.pdf")
attempt("aw_allowed", lambda: safety.assert_writable(elsewhere / "one.pdf"))
attempt("aw_not_allowed", lambda: safety.assert_writable(elsewhere / "three.pdf"))
attempt("net", lambda: socket.create_connection(("127.0.0.1", 9), timeout=2))
print(json.dumps(res))
'''


class TestAppMode(unittest.TestCase):
    """HDC_MODE=app in a child; DATA, the bundle, the input and the report folders all under private-runner."""

    @classmethod
    def setUpClass(cls):
        cls.base = _run_folder()
        b = cls.base
        _fake_bundle(b)
        (b / "input").mkdir()
        (b / "input" / "PASSPORT.pdf").write_bytes(b"%PDF-1.4 made-up bytes, never parsed\n")
        (b / "elsewhere").mkdir()
        cls.r = _child(_APP_CHILD, b, _app_env(b))

    @classmethod
    def tearDownClass(cls):
        _rmtree(cls.base)

    def test_places_fields(self):
        b, r = self.base, self.r
        self.assertEqual(r["mode"], places.APP)
        self.assertEqual(r["fields"], {"write_root": str(b / "data"), "cache_root": str(b / "data" / "cache"),
                                       "temp_dir": str(b / "data" / "tmp"),
                                       "models_dir": str(b / "bundle" / "engine" / "easyocr" / "model"),
                                       "glue_data": str(b / "bundle" / "engine" / "glue_data.json"),
                                       "code_root": str(TODAY_WORKTREE), "bundle": str(b / "bundle")})
        self.assertEqual(r["empty"], [[], [], None, None])
        self.assertEqual(r["labels"], [places.APP_WRITE_LABEL, places.APP_NO_OPEN_LABEL, places.OUT_APP])
        self.assertFalse(r["devplaces_loaded"])
        self.assertEqual(r["botimport"], [str(TODAY_WORKTREE), str(b / "data"), str(b / "data" / "cache")])
        self.assertEqual(r["safety"], [True, None, None, [], [], str(b / "data" / "tmp"), str(b / "data"), None,
                                       places.APP_WRITE_LABEL, places.APP_NO_OPEN_LABEL])
        self.assertEqual(r["engine"], [str(b / "bundle" / "engine" / "easyocr" / "model"),
                                       str(b / "bundle" / "engine" / "glue_data.json"), True])
        self.assertEqual(r["forbidden_rules"], [False, False])        # no DEV folder rules in the app

    def test_botimport(self):
        self.assertEqual(self.r["load_outside"], "RuntimeError")
        self.assertEqual(Path(self.r["load_root"]), (self.base / "data" / "cache").resolve())
        self.assertTrue(self.r["bot_paths_under_data"])

    def test_check_out_dir_app_rules(self):
        o = self.r["out"]
        self.assertEqual(o["reports"], "ok")
        self.assertEqual(o["under_data"], "ok")
        self.assertEqual(o["inside_input"], safety.APP_OUT_INSIDE_INPUT)
        self.assertEqual(o["input_inside"], safety.APP_INPUT_INSIDE_OUT)
        self.assertEqual(o["drive_root"], safety.APP_OUT_ROOT)
        self.assertEqual(o["windir"], safety.APP_OUT_PROTECTED)
        self.assertEqual(o["app_folder"], safety.APP_OUT_PROTECTED)
        self.assertEqual(o["relative"], safety.APP_OUT_RELATIVE)
        self.assertEqual(o["data_itself"], safety.APP_OUT_DATA)
        self.assertEqual(o["share"], safety.APP_OUT_DEVICE)
        self.assertEqual(o["rel_input"], safety.APP_INPUT_RELATIVE)
        if "programdata" in o:
            self.assertEqual(o["programdata"], safety.APP_OUT_PROTECTED)
        self.assertEqual(self.r["cli"], ["ok", safety.APP_OUT_INSIDE_INPUT])
        self.assertFalse((self.base / "reports" / "c4").exists())     # check_args creates nothing

    def test_load_page_texts_under_data(self):
        self.assertEqual(self.r["pt_inside"], [True, "MADE UP TEXT"])
        self.assertEqual(self.r["pt_outside"], "InputError")

    def test_guard(self):
        r = self.r
        self.assertEqual(r["w_data"], "ok")
        self.assertEqual(r["w_out"], "ok")
        self.assertTrue(r["w_elsewhere"].startswith("PermissionError"))
        self.assertIn("write outside the app's data folder", r["w_elsewhere"])
        self.assertFalse(r["w_elsewhere_exists"])
        self.assertTrue(r["w_input"].startswith("PermissionError"))
        self.assertFalse(r["w_input_exists"])
        self.assertTrue(r["net"].startswith("PermissionError"))
        self.assertTrue(r["spawn"].startswith("PermissionError"))
        self.assertEqual(r["aw_out"], "ok")
        self.assertTrue(r["aw_elsewhere"].startswith("SafetyError"))
        self.assertTrue(r["aw_relative"].startswith("SafetyError"))
        self.assertEqual(r["safe_dir"], [str((self.base / "reports" / "c1" / "work").resolve()), None])
        self.assertEqual(Path(r["tmp"]), self.base / "data" / "tmp")
        self.assertTrue(r["finish"])

    def test_prepare_twice(self):
        r = self.r
        self.assertTrue(r["same_state"])
        self.assertEqual(r["w_out_after_second"], "ok")
        self.assertTrue(r["w_elsewhere_after_second"].startswith("PermissionError"))
        self.assertFalse(r["w_elsewhere_after_second_exists"])
        self.assertTrue(r["prepare_other"].startswith("SafetyError"))
        self.assertFalse((self.base / "reports" / "c3").exists())


class TestInDataFakeResolver(unittest.TestCase):
    """1.0.1 (APP_SPEC R2.10 I28): safety._in_data, the APP rule for a path that must lie in DATA, with a FAKE
    resolver standing for Windows' redirection of %LOCALAPPDATA% (an MSIX package, a junction, folder redirection).
    Pure: made-up drive X:, nothing is touched."""

    DATA = Path(r"X:\Users\Someone\AppData\Local\Hangeul Document Checker")
    REAL = Path(r"X:\Users\Someone\AppData\Local\Packages\Made.Up_0000\LocalCache\Local\Hangeul Document Checker")
    OUTSIDE = Path(r"X:\Elsewhere")

    def resolve(self, p: Path) -> Path:
        """DATA's name redirects to REAL; DATA\\cache\\jout is a made-up junction to X:\\Elsewhere; DATA\\cache\\jbot one
        to the DEV bot folder (a forbidden folder in this DEV process)."""
        s = os.path.normcase(os.path.abspath(str(p)))
        for name, target in ((self.DATA / "cache" / "jout", self.OUTSIDE / "target"),
                             (self.DATA / "cache" / "jbot", TODAY_BOT / "made-up"),
                             (self.DATA, self.REAL)):
            n = os.path.normcase(str(name))
            if s == n or s.startswith(n + "\\"):
                return Path(str(target) + str(p)[len(str(name)):])
        return Path(os.path.abspath(str(p)))

    def refused(self, p: Path) -> str:
        try:
            safety._in_data(p, "the cache root", self.DATA, self.resolve)
        except safety.SafetyError as e:
            return str(e)
        return "ok"

    def test_paths_under_a_redirected_data_folder_are_accepted(self):
        got = safety._in_data(self.DATA / "cache", "the cache root", self.DATA, self.resolve)
        self.assertEqual(got, self.REAL / "cache")                    # the real path, as before
        page_texts = self.DATA / "cache" / "pagetexts" / "F-0123456789abcdef.json"
        self.assertEqual(safety._in_data(page_texts, "--page-texts", self.DATA, self.resolve),
                         self.REAL / "cache" / "pagetexts" / "F-0123456789abcdef.json")
        # a caller that passes the real path already is accepted too
        self.assertEqual(safety._in_data(self.REAL / "tmp", "a written path", self.DATA, self.resolve),
                         self.REAL / "tmp")
        # 1.0.0's rule (the real path against DATA's NAME) refused exactly these: the regression of I27
        self.assertFalse(str(self.resolve(self.DATA / "cache")).lower().startswith(str(self.DATA).lower() + "\\"))

    def test_the_guard_keeps_its_refusals(self):
        for p in (self.OUTSIDE / "cache",                                   # outside DATA as written
                  self.DATA.parent / "Hangeul Document Checker2" / "cache",  # a sibling with a longer name
                  self.DATA / ".." / "Programs" / "x",                       # leaves DATA through ".."
                  self.DATA / "cache" / "jout" / "x.json",                   # a junction inside DATA that leads out
                  self.DATA / "cache" / "jbot" / "x.json"):                  # ... into a forbidden folder (DEV)
            why = self.refused(p)
            self.assertTrue(why.startswith("the cache root " + safety.APP_DATA_OUTSIDE), (p, why))
        self.assertTrue(safety.is_forbidden(TODAY_BOT / "made-up"))         # DEV keeps the bot rule
        self.assertIn("lies outside", self.refused(TODAY_BOT / "made-up"))

    def test_not_resolved_data_folder(self):
        """No redirection (the usual case): the same answers as the DEV rule had for PRIVATE."""
        plain = lambda p: Path(os.path.abspath(str(p)))                      # noqa: E731
        self.assertEqual(safety._in_data(self.DATA / "cache", "x", self.DATA, plain), self.DATA / "cache")
        with self.assertRaises(safety.SafetyError):
            safety._in_data(self.OUTSIDE, "x", self.DATA, plain)

    def test_dev_private_is_unchanged(self):
        """In DEV, _private still compares with private-runner as written and resolved (no _in_data)."""
        self.assertFalse(safety.APP_MODE)
        with self.assertRaises(safety.SafetyError) as cm:
            safety._private(self.DATA / "cache", "the cache root")
        self.assertIn("must lie under", str(cm.exception))
        self.assertNotIn(safety.APP_DATA_OUTSIDE, str(cm.exception))


_REDIRECT_CHILD = r'''
import json, os, sys
from pathlib import Path
base = Path(sys.argv[1])
data, real, inp, elsewhere = base / "data", base / "realdata", base / "input", base / "elsewhere"
res = {}
from src.verify.runner import places, safety, engine, ingest
from src.verify.runner.model import PAGE_TEXTS_SCHEMA, Source, SourceFile
P = places.current()
res["data_is_junction"] = os.path.isjunction(data)
res["write_root"] = str(P.write_root)
def try_private(p, what="the cache root"):
    try:
        return "ok:" + str(safety._private(Path(p), what))
    except safety.SafetyError as e:
        return "SafetyError: " + str(e)
# the paths the worker uses, before the guard is armed (1.0.0 refused the first three)
res["cache_root"] = try_private(P.cache_root)
res["tmp"] = try_private(P.temp_dir, "a written path")
res["store"] = try_private(P.cache_root / "pagetexts" / "F-0123456789abcdef.json", "--page-texts")
res["real_name"] = try_private(real / "cache")
res["outside"] = try_private(elsewhere / "cache")
res["inner_junction"] = try_private(data / "cache" / "jout" / "x.json")
# load_page_texts of the store under DATA (its name), read through the junction
f = inp / "PASSPORT.pdf"
src = Source(mode="folder", root=inp, case_id="F-0123456789abcdef",
             files=[SourceFile(path=f, name=f.name, is_image=False, n_pages=1, size=f.stat().st_size,
                               mtime=f.stat().st_mtime)])
doc = {"schema": PAGE_TEXTS_SCHEMA, "engine": "made-up", "mode": "folder", "complete": True,
       "files": [{"name": f.name, "sha256": ingest.file_sha256(f), "n_pages": 1,
                  "pages": [{"page": 1, "text": "MADE UP TEXT", "kind": "ocr", "sideways": False, "seconds": 0.5}]}]}
store = P.cache_root / "pagetexts" / "F-0123456789abcdef.json"
store.parent.mkdir(parents=True, exist_ok=True)
store.write_text(json.dumps(doc), encoding="utf-8")
res["store_on_disk"] = (real / "cache" / "pagetexts" / "F-0123456789abcdef.json").is_file()
pt = engine.load_page_texts(store, src)
res["pt"] = [pt.complete, pt.get("PASSPORT.pdf", 0)[0]]
# the guard, armed as the worker and the self-test's part "guard" arm it
out = base / "reports" / "c1"
try:
    state = safety.prepare(out, P.cache_root, inp)
    res["prepare"] = "ok"
except safety.SafetyError as e:
    res["prepare"] = "SafetyError: " + str(e)
    state = None
def attempt(name, fn, probe=None):
    try:
        fn()
        res[name] = "ok"
    except PermissionError as e:
        res[name] = "PermissionError: " + str(e)[:200]
    except Exception as e:
        res[name] = type(e).__name__ + ": " + str(e)[:200]
    if probe is not None:
        res[name + "_exists"] = probe.exists()
if state is not None:
    attempt("w_data", lambda: (data / "w.txt").write_text("x"))
    attempt("w_cache", lambda: (P.cache_root / "pagetexts" / "w.txt").write_text("x"))
    attempt("w_out", lambda: (out / "w.txt").write_text("x"))
    attempt("w_elsewhere", lambda: (elsewhere / "x.txt").write_text("x"), elsewhere / "x.txt")
    attempt("w_through_inner_junction", lambda: (data / "cache" / "jout" / "y.txt").write_text("x"),
            elsewhere / "y.txt")
    attempt("w_input", lambda: (inp / "new.txt").write_text("x"), inp / "new.txt")
    attempt("aw_cache", lambda: safety.assert_writable(P.cache_root / "docs"))
    attempt("aw_elsewhere", lambda: safety.assert_writable(elsewhere / "z"))
    res["finish"] = safety.finish(state)["input_unchanged"]
print(json.dumps(res))
'''


class TestAppRedirectedData(unittest.TestCase):
    """1.0.1 (APP_SPEC R2.10 I27/I28): DATA's name is a JUNCTION to another folder, as when %LOCALAPPDATA% resolves
    elsewhere (the Claude app's MSIX container redirects a new top-level folder; a junction or folder redirection on
    another PC). HDC_MODE=app in a child; every folder under private-runner. 1.0.0 refused the cache root here, and so
    every check (exit 2) and the self-test's part "guard"."""

    @classmethod
    def setUpClass(cls):
        import _winapi
        cls.base = _run_folder()
        b = cls.base
        cls.junctions = []                              # removed in reverse order
        try:
            _fake_bundle(b)
            (b / "input").mkdir()
            (b / "input" / "PASSPORT.pdf").write_bytes(b"%PDF-1.4 made-up bytes, never parsed\n")
            (b / "elsewhere").mkdir()
            (b / "realdata" / "cache").mkdir(parents=True)
            for target, link in ((b / "realdata", b / "data"),                     # DATA's name -> the real folder
                                 (b / "elsewhere", b / "realdata" / "cache" / "jout")):   # leads out of DATA
                _winapi.CreateJunction(str(target), str(link))
                cls.junctions.insert(0, link)
            cls.r = _child(_REDIRECT_CHILD, b, _app_env(b))
        except BaseException:
            cls.tearDownClass()                         # unittest skips tearDownClass when setUpClass fails
            raise

    @classmethod
    def tearDownClass(cls):
        for j in cls.junctions:                         # the junctions themselves, never their targets
            if os.path.lexists(j) and os.path.isjunction(j):
                os.rmdir(j)
        _rmtree(cls.base)

    def test_the_data_folder_paths_are_accepted(self):
        r, real = self.r, (self.base / "realdata").resolve()
        self.assertTrue(r["data_is_junction"])
        self.assertEqual(r["write_root"], str(self.base / "data"))
        self.assertEqual(r["cache_root"], "ok:" + str(real / "cache"))
        self.assertEqual(r["tmp"], "ok:" + str(real / "tmp"))
        self.assertEqual(r["store"], "ok:" + str(real / "cache" / "pagetexts" / "F-0123456789abcdef.json"))
        self.assertEqual(r["real_name"], "ok:" + str(real / "cache"))
        self.assertTrue(r["store_on_disk"])
        self.assertEqual(r["pt"], [True, "MADE UP TEXT"])

    def test_refusals_are_kept_and_say_data_folder(self):
        r = self.r
        for k in ("outside", "inner_junction"):
            self.assertTrue(r[k].startswith("SafetyError: the cache root " + safety.APP_DATA_OUTSIDE), (k, r[k]))

    def test_the_guard_arms_and_still_refuses(self):
        r = self.r
        self.assertEqual(r["prepare"], "ok")
        for k in ("w_data", "w_cache", "w_out", "aw_cache"):
            self.assertEqual(r[k], "ok", k)
        for k in ("w_elsewhere", "w_through_inner_junction", "w_input"):
            self.assertTrue(r[k].startswith("PermissionError"), (k, r[k]))
            self.assertFalse(r[k + "_exists"], k)
        self.assertIn("leaves the app's data folder", r["w_through_inner_junction"])
        self.assertTrue(r["aw_elsewhere"].startswith("SafetyError"))
        self.assertTrue(r["finish"])


class TestWindowGuard(unittest.TestCase):
    def test_arm_window_and_allow_file(self):
        base = _run_folder()
        try:
            _fake_bundle(base)
            for n in ("reports", "elsewhere"):
                (base / n).mkdir()
            r = _child(_WINDOW_CHILD, base, _app_env(base))
        finally:
            _rmtree(base)
        self.assertTrue(r["allow_unarmed"].startswith("SafetyError"))
        self.assertTrue(r["arm_relative"].startswith("SafetyError"))
        self.assertEqual(r["mode"], "window")
        self.assertEqual(r["w_reports"], "ok")
        self.assertEqual(r["w_data"], "ok")
        self.assertEqual(r["w_elsewhere"], "PermissionError")
        self.assertFalse(r["w_elsewhere_exists"])
        self.assertTrue(r["allow_relative"].startswith("SafetyError"))
        self.assertTrue(r["allowed"])
        self.assertEqual(r["w_allowed"], "ok")
        self.assertEqual(r["w_not_allowed"], "PermissionError")
        self.assertFalse(r["w_not_allowed_exists"])
        self.assertEqual(r["aw_allowed"], "ok")
        self.assertTrue(r["aw_not_allowed"].startswith("SafetyError"))
        self.assertEqual(r["net"], "PermissionError")


if __name__ == "__main__":
    unittest.main()
