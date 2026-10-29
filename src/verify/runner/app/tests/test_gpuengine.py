"""Task T tests (APP_SPEC revision 2: R2.1.2, R2.1.3, R2.1.5): src.verify.runner.app.gpuengine.

Runner venv, HDC_TEST_NO_CARD=1, no torch, no easyocr, no graphics card: torch is fake_torch.FakeTorch, easyocr is
fake_torch.FakeEasyOCR, the bot's doc_verifier is FakeDV (its read surface, as gpuread's own tests fake it), the card
gate is FakeGate. gpuread.read_all is the REAL one (only its ingest is faked, for the sha256). glue_numpy is the real
lab module (numpy only). Every file is synthetic and written under
C:\\Hangeul\\DOCCHECK\\lab\\private-runner\\selftest\\app\\T; every text is made up. No personal data.

    <runner venv python> -I -B -m unittest src.verify.runner.app.tests.test_gpuengine
"""
from __future__ import annotations

import ast
import contextlib
import gc
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import weakref
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np
import pymupdf

from src.verify.runner import gpuread as GR
from src.verify.runner import model as M
from src.verify.runner.app import gpuengine as GE
from src.verify.runner.app.tests import fake_torch as FT

WORKTREE = Path(__file__).resolve().parents[5]
SELFTEST = Path(r"C:\Hangeul\DOCCHECK\lab\private-runner\selftest\app\T")
EXPORT = Path(r"C:\Hangeul\DOCCHECK\lab\onnx\scripts\export_onnx.py")
WORKER = Path(r"C:\Hangeul\DOCCHECK\lab\private-runner\gpu_sessions\gpu_worker.py")
GLUE_DATA = Path(r"C:\Hangeul\DOCCHECK\lab\onnx\pkg\glue_data.json")
GPUENGINE = Path(GE.__file__).resolve()
LAYER_TEXT = "This made-up page has a text layer of more than forty characters on it."
NON_ASCII = "nicht-ascii-\u00e4\u00f6\u00fc"          # a made-up folder name with non-ASCII letters
PICTURE = "bild-\u00e9.png"                             # a made-up picture name with a non-ASCII letter
CLEAN_ENV = (GE.NO_CARD_ENV,) + GE.NO_CACHING_ENV + GE.NOT_DEFAULT_ENV


# --------------------------------------------------------------------------------------------------- fakes
class Cancelled(BaseException):
    """hooks.Cancelled's stand-in (a BaseException, raised by the listener through the gate)."""


class FakeGate:
    """cardgate.CardGate's surface: wait_turn(where) (recorded; raises `exc` on the n-th call with `where` when
    asked), wait_room(where) (recorded in `rooms`; `on_call` sees it as "room:<where>"), on_pause and on_room (set
    by start_card)."""

    def __init__(self, raise_at=None, exc=Cancelled, on_call=None):
        self.calls = []
        self.rooms = []
        self.raise_at = raise_at
        self.exc = exc
        self.on_call = on_call
        self.on_pause = lambda: 0
        self.on_room = lambda: None

    def wait_room(self, where):
        self.rooms.append(where)
        if self.on_call is not None:
            self.on_call("room:" + where)

    def wait_turn(self, where):
        self.calls.append(where)
        if self.on_call is not None:
            self.on_call(where)
        if self.raise_at and where == self.raise_at[0] and self.calls.count(where) == self.raise_at[1]:
            raise self.exc("made-up cancel")


def page_array(page: int, shape=(40, 30)) -> np.ndarray:
    """A made-up rendered page: channel 0 holds the page number everywhere (it survives turning)."""
    a = np.zeros((shape[0], shape[1], 3), dtype=np.uint8)
    a[:, :, 0] = page
    return a


class FakePageReader:
    """The bot's EasyOCR reader on the card, made up. Arrays: the page number from channel 0; a portrait array is
    upright. `oom` = {page: n} makes the next n calls of that page run out of card memory (-1: every call);
    `sideways` pages read short when upright and long when turned; `fail` raises a plain error. Paths: must be ASCII
    and readable from the working folder; their bytes are kept."""

    device = "cuda:0"

    def __init__(self, log=None, oom=None, sideways=(), fail=None):
        self.log = log if log is not None else []
        self.oom = dict(oom or {})
        self.sideways = set(sideways)
        self.fail = fail
        self.path_bytes = []

    def readtext(self, image, detail=1, **kw):
        if detail != 0:
            raise AssertionError("the bot reads with detail=0")
        if isinstance(image, str):
            self.log.append(("path", image))
            if not image.isascii():
                raise AssertionError("a non-ASCII path reached the reader")
            self.path_bytes.append(Path(image).read_bytes())
            return ["MADE-UP", "PICTURE", "TEXT"]
        page = int(image[0, 0, 0])
        h, w = image.shape[:2]
        self.log.append((page, (h, w)))
        if page == self.fail:
            raise ValueError("made-up reader failure")
        n = self.oom.get(page, 0)
        if n == -1:
            raise FT.oom(f"page {page}")
        if n > 0:
            self.oom[page] = n - 1
            raise FT.oom(f"page {page}")
        if page in self.sideways and h > w:
            return ["X"]
        return [f"MADE-UP PAGE {page}"] + ["filler"] * 4


class FakeDV:
    """The bot's doc_verifier read surface. read_pages renders (made up) every page with < 40 text-layer characters
    and calls self._ocr_image, looked up at call time (gpuread wraps it); _ocr_image is the bot's shape: the upright
    read, then (when short) one turned read inside `except Exception`; read_document reads a picture by its path.
    Both reach the reader through _ocr_reader(), which returns _reader (the GatedReader)."""

    def __init__(self, shapes=None):
        self._reader = None
        self.shapes = dict(shapes or {})

    def _ocr_reader(self):
        if self._reader is None:
            raise AssertionError("no reader is set")
        return self._reader

    def _ocr_image(self, arr, turned=None):
        text = " ".join(self._ocr_reader().readtext(arr, detail=0))
        if len(text) >= 30:
            return text
        best = text
        try:
            t = " ".join(self._ocr_reader().readtext(np.ascontiguousarray(np.rot90(arr)), detail=0))
        except Exception:
            t = ""
        if len(t) > len(best):
            best = t
            if turned is not None:
                turned.append(True)
        return best

    def read_pages(self, path, max_pages=20, sideways=None):
        out = []
        with pymupdf.open(str(path)) as doc:
            for i, page in enumerate(doc):
                if i >= max_pages:
                    break
                t = page.get_text().strip()
                if len(t) < 40:
                    turned = []
                    t = self._ocr_image(page_array(i + 1, self.shapes.get(i + 1, (40, 30))), turned)
                    if turned and sideways is not None:
                        sideways.append(i + 1)
                out.append(t)
        return out

    def read_document(self, path, max_pages=20):
        return " ".join(self._ocr_reader().readtext(str(path), detail=0)), [(16, 16)]


def _sha256(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def make_pdf(path: Path, n: int, layer_pages=(1,)) -> Path:
    doc = pymupdf.open()
    for p in range(1, n + 1):
        page = doc.new_page(width=300, height=400)
        page.draw_rect(pymupdf.Rect(20, 20, 120, 80), color=(0, 0, 0))
        if p in layer_pages:
            page.insert_text((10, 200), LAYER_TEXT, fontsize=6)
    doc.save(str(path))
    doc.close()
    return path


def weights_dir(path: Path) -> Path:
    """A model folder holding two empty stand-ins for the .pth weights (the fake easyocr never reads them)."""
    path.mkdir(parents=True, exist_ok=True)
    for name in GE.WEIGHTS:
        (path / name).write_bytes(b"")
    return path


def make_png(path: Path, size: int = 16) -> Path:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, size, size), False)
    pix.set_rect(pix.irect, (200, 180, 160))
    pix.save(str(path))
    return path


def source(mode: str, paths) -> M.Source:
    files = []
    for p in paths:
        p = Path(p)
        img = p.suffix.lower() != ".pdf"
        n = 1
        if not img:
            with pymupdf.open(str(p)) as d:
                n = d.page_count
        st = p.stat()
        files.append(M.SourceFile(path=p, name=p.name, is_image=img, n_pages=n, size=st.st_size, mtime=st.st_mtime))
    key = "|".join(_sha256(f.path) for f in files)
    case = ("B-" if mode == "bundle" else "F-") + hashlib.sha1(key.encode()).hexdigest()[:16]
    return M.Source(mode=mode, root=Path(paths[0]).parent, files=files, case_id=case)


@contextlib.contextmanager
def card_env():
    """os.environ without HDC_TEST_NO_CARD and the switches start_card refuses (restored afterwards)."""
    with mock.patch.dict(os.environ, {}):
        for k in list(os.environ):
            if k.upper() in CLEAN_ENV:
                del os.environ[k]
        yield


def defs_in(tree: ast.AST, names) -> dict:
    return {n.name: ast.dump(n) for n in ast.walk(tree)
            if isinstance(n, (ast.ClassDef, ast.FunctionDef)) and n.name in names}


def func(tree: ast.AST, name: str) -> ast.FunctionDef:
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n
    raise AssertionError(f"{name} not found")


class _Dir(unittest.TestCase):
    def setUp(self):
        SELFTEST.mkdir(parents=True, exist_ok=True)
        self.dir = Path(tempfile.mkdtemp(prefix="t-", dir=str(SELFTEST)))
        self.addCleanup(shutil.rmtree, self.dir, True)


# ------------------------------------------------------------------------------------------- the first read
class FirstReadTests(_Dir):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(GR, "_ingest", lambda: SimpleNamespace(file_sha256=_sha256))
        p.start()
        self.addCleanup(p.stop)
        self.torch = FT.FakeTorch()
        self.card = GE.Card(self.torch)
        self.events = []
        self.ascii_dir = str(self.dir / "ascii")
        self.made = []

    def emit(self, ev, **kw):
        self.events.append((ev, kw))

    def store(self, src) -> Path:
        return self.dir / "cache" / "pagetexts" / f"{src.case_id}.json"

    def factory(self, **kw):
        log = kw.pop("log", None)

        def make():
            r = FakePageReader(log=log, **kw)
            self.made.append(weakref.ref(r))
            return r
        return make

    def run_fr(self, src, dv, gate, factory, **kw):
        return GE.first_read(self.card, SimpleNamespace(dv=dv), src, gate, self.store(src), self.emit,
                             ascii_dir=self.ascii_dir, reader_factory=factory, **kw)

    def load(self, src) -> dict:
        return json.loads(self.store(src).read_text(encoding="utf-8"))

    def page_events(self):
        return [kw for ev, kw in self.events if ev == "page"]

    def test_fresh_bundle_sideways_oom_retry_ladder_and_skip(self):
        pdf = make_pdf(self.dir / "bundle.pdf", 5, layer_pages=(1,))
        src = source("bundle", [pdf])
        dv = FakeDV(shapes={4: (2100, 30)})
        gate = FakeGate()
        log = []
        fr = self.run_fr(src, dv, gate, self.factory(log=log, oom={3: 1, 4: 2, 5: -1}, sideways={2}))
        d = self.load(src)
        self.assertEqual(d["schema"], M.PAGE_TEXTS_SCHEMA)
        self.assertEqual(d["engine"], GE.APP_ENGINE_LABEL)
        self.assertEqual((d["mode"], d["case_id"]), (src.mode, src.case_id))
        self.assertFalse(d["complete"])                                  # page 5 is left to the light reader
        e = d["files"][0]
        self.assertEqual((e["name"], e["sha256"], e["n_pages"]), (pdf.name, _sha256(pdf), 5))
        pages = {p["page"]: p for p in e["pages"]}
        self.assertEqual(sorted(pages), [1, 2, 3, 4])
        self.assertEqual(pages[1]["kind"], "layer")
        self.assertEqual(pages[1]["text"], LAYER_TEXT)
        self.assertTrue(pages[2]["sideways"])
        self.assertEqual(pages[3]["text"], "MADE-UP PAGE 3 filler filler filler filler")
        self.assertNotIn("downscaled", pages[3])                          # GatedReader's retry at full size
        self.assertEqual(pages[4]["downscaled"], 2000)                    # then gpuread's ladder
        self.assertEqual([s["page"] for s in e["skipped"]], [5])
        self.assertEqual(log.count((3, (40, 30))), 2)                    # the same full-size page twice
        self.assertIn((4, (2000, 29)), log)
        self.assertEqual(fr["pages"]["read"], 4)
        self.assertEqual((fr["skipped"], fr["downscaled"], fr["oom_retries"]), (1, 1, 3))
        self.assertEqual((fr["pending"], fr["reused"], fr["complete"], fr["model_loaded"]), (5, 0, False, True))
        self.assertEqual((fr["peak_alloc_gb"], fr["peak_reserved_gb"]), (2.41, 2.8))
        self.assertEqual(self.card.pauses, 3)                             # one pause per out-of-memory retry
        self.assertEqual(gate.calls.count("page"), 4)                     # checkpoint before every OCR page
        self.assertEqual(gate.calls.count("oom"), 3)
        self.assertEqual(gate.calls[0], "read")                           # the gate's turn before the model is made
        ev = self.page_events()
        self.assertEqual([x["done"] for x in ev], [2, 3, 4, 5])
        self.assertTrue(all(x["of"] == 5 and x["where"] == "card" for x in ev))
        self.assertTrue(all(isinstance(x["seconds"], float) for x in ev))
        self.assertIsNone(dv._reader)
        self.assertEqual(self.torch.cuda.calls[-1], "empty_cache")       # card.free() last, in the finally
        gc.collect()
        self.assertEqual(len(self.made), 1)
        self.assertIsNone(self.made[0]())                                 # the reader is gone
        self.assertEqual(GE.reading_note(fr), GE.NOTE_BASE + "; 1 page(s) left to the light reader (too large for "
                         "the cap); 1 page(s) read smaller (card memory)")

    def test_folder_with_a_non_ascii_picture(self):
        folder = self.dir / NON_ASCII
        folder.mkdir()
        pdf = make_pdf(folder / "a.pdf", 2, layer_pages=(1,))
        png = make_png(folder / PICTURE)
        src = source("folder", [pdf, png])
        reader = FakePageReader()
        fr = self.run_fr(src, FakeDV(), FakeGate(), lambda: reader)
        d = self.load(src)
        self.assertTrue(d["complete"])
        pic = d["files"][1]
        self.assertEqual(pic["pages"][0]["kind"], "image")
        self.assertEqual(pic["pages"][0]["text"], "MADE-UP PICTURE TEXT")
        paths = [x[1] for x in reader.log if x[0] == "path"]
        self.assertEqual(len(paths), 1)
        self.assertTrue(paths[0].isascii())
        self.assertEqual(reader.path_bytes, [png.read_bytes()])           # the same bytes, so the same decode
        self.assertTrue(Path(os.path.abspath(paths[0])).is_relative_to(Path(self.ascii_dir)))
        self.assertEqual(fr["pages"]["image"], 1)
        self.assertEqual([x["done"] for x in self.page_events()], [2, 3])

    def test_cancel_saves_then_resume_then_nothing_left(self):
        pdf = make_pdf(self.dir / "bundle.pdf", 4, layer_pages=(1,))
        src = source("bundle", [pdf])
        log1 = []
        dv = FakeDV()
        with self.assertRaises(Cancelled):                                # the 2nd checkpoint: before page 3
            self.run_fr(src, dv, FakeGate(raise_at=("page", 2)), self.factory(log=log1))
        self.assertIsNone(dv._reader)
        d = self.load(src)
        self.assertFalse(d["complete"])
        self.assertEqual([p["page"] for p in d["files"][0]["pages"]], [1, 2])
        self.assertEqual(log1, [(2, (40, 30))])
        gc.collect()
        self.assertIsNone(self.made[0]())
        # resume: only pages 3 and 4 are read
        self.events.clear()
        log2 = []
        fr = self.run_fr(src, dv, FakeGate(), self.factory(log=log2))
        self.assertEqual(log2, [(3, (40, 30)), (4, (40, 30))])
        self.assertEqual((fr["pending"], fr["reused"]), (2, 2))
        self.assertEqual([(x["done"], x["of"]) for x in self.page_events()], [(1, 2), (2, 2)])
        d = self.load(src)
        self.assertTrue(d["complete"])
        self.assertEqual([p["page"] for p in d["files"][0]["pages"]], [1, 2, 3, 4])
        self.assertEqual(d["engine"], GE.APP_ENGINE_LABEL)
        # nothing left: no reader is made, the file is only rebuilt
        self.events.clear()

        def no_model():
            raise AssertionError("no model may be loaded when nothing is pending")
        gate = FakeGate()
        fr = self.run_fr(src, dv, gate, no_model)
        self.assertEqual((fr["pending"], fr["model_loaded"], fr["complete"]), (0, False, True))
        self.assertEqual(self.page_events(), [])
        self.assertEqual(gate.calls, [])
        self.assertEqual(self.load(src)["files"], d["files"])

    def test_a_crash_saves_and_releases(self):
        pdf = make_pdf(self.dir / "bundle.pdf", 4, layer_pages=(1,))
        src = source("bundle", [pdf])
        dv = FakeDV()
        empties = self.torch.cuda.empties
        with self.assertRaises(ValueError):
            self.run_fr(src, dv, FakeGate(), self.factory(fail=3))
        self.assertIsNone(dv._reader)
        self.assertGreater(self.torch.cuda.empties, empties)
        d = self.load(src)
        self.assertEqual([p["page"] for p in d["files"][0]["pages"]], [1, 2])
        gc.collect()
        self.assertIsNone(self.made[0]())

    def test_a_reader_that_cannot_be_made_releases(self):
        src = source("bundle", [make_pdf(self.dir / "bundle.pdf", 2, layer_pages=(1,))])
        dv = FakeDV()
        dv._reader = "left over"

        def refuse():
            raise GE.CardRefused(GE.CARD_FAILED, {"stage": "firstread"})
        with self.assertRaises(GE.CardRefused):
            self.run_fr(src, dv, FakeGate(), refuse)
        self.assertIsNone(dv._reader)

    def test_the_default_reader_is_the_bots_with_the_bundles_folders(self):
        pdf = make_pdf(self.dir / "bundle.pdf", 2, layer_pages=(1, 2))   # pending, but no OCR page
        src = source("bundle", [pdf])
        eo = FT.FakeEasyOCR()
        card = GE.Card(self.torch, easyocr_loader=lambda: eo)
        model_dir = weights_dir(self.dir / "engine" / "easyocr" / "model")
        dv = FakeDV()
        fr = GE.first_read(card, SimpleNamespace(dv=dv), src, FakeGate(), self.store(src), self.emit,
                           model_dir=model_dir, ascii_dir=self.ascii_dir)
        self.assertEqual(eo.calls, [(["en"], {"gpu": True, "verbose": False,
                                              "model_storage_directory": str(model_dir),
                                              "user_network_directory": str(model_dir.parent / "user_network"),
                                              "download_enabled": False})])
        self.assertTrue(fr["complete"])
        self.assertIsNone(dv._reader)

    def test_model_dir_defaults_to_places(self):
        src = source("bundle", [make_pdf(self.dir / "bundle.pdf", 1, layer_pages=(1,))])
        eo = FT.FakeEasyOCR()
        card = GE.Card(self.torch, easyocr_loader=lambda: eo)
        model_dir = weights_dir(self.dir / "bundle" / "engine" / "easyocr" / "model")
        with mock.patch.object(GE, "_places", lambda: SimpleNamespace(models_dir=model_dir)):
            GE.first_read(card, SimpleNamespace(dv=FakeDV()), src, FakeGate(), self.store(src), self.emit)
        self.assertEqual(eo.calls[0][1]["model_storage_directory"], str(model_dir))

    def test_missing_weights_stop_before_easyocr(self):
        src = source("bundle", [make_pdf(self.dir / "bundle.pdf", 2, layer_pages=(1,))])
        eo = FT.FakeEasyOCR()
        card = GE.Card(self.torch, easyocr_loader=lambda: eo)
        model_dir = self.dir / "onnx" / "models"                         # a folder without the .pth weights
        model_dir.mkdir(parents=True)
        (model_dir / GE.WEIGHTS[0]).write_bytes(b"")
        dv = FakeDV()
        with self.assertRaises(FileNotFoundError) as cm:
            GE.first_read(card, SimpleNamespace(dv=dv), src, FakeGate(), self.store(src), self.emit,
                          model_dir=model_dir)
        self.assertIn(GE.WEIGHTS[1], str(cm.exception))
        self.assertNotIn(str(self.dir), str(cm.exception))               # names only, never paths
        self.assertEqual(eo.calls, [])
        self.assertFalse((model_dir.parent / "user_network").exists())
        self.assertIsNone(dv._reader)

    def test_the_store_is_written_atomically(self):
        src = source("bundle", [make_pdf(self.dir / "bundle.pdf", 2, layer_pages=(1,))])
        seen = []
        real = os.replace

        def spy(a, b):
            seen.append((Path(a).name, Path(b).name))
            return real(a, b)
        with mock.patch.object(GE.os, "replace", spy):
            self.run_fr(src, FakeDV(), FakeGate(), self.factory())
        name = self.store(src).name
        self.assertTrue(seen)
        self.assertTrue(all(x == (name + ".tmp", name) for x in seen))
        self.assertFalse(Path(str(self.store(src)) + ".tmp").exists())


    # -- G1 review finding 3: what an earlier check's page-texts file may give this check
    def pages_of(self, src) -> dict:
        return {p["page"]: p for p in self.load(src)["files"][0]["pages"]}

    def test_a_page_read_smaller_is_read_again_at_full_size(self):
        pdf = make_pdf(self.dir / "bundle.pdf", 4, layer_pages=(1,))
        src = source("bundle", [pdf])
        dv = FakeDV(shapes={4: (2100, 30)})
        fr = self.run_fr(src, dv, FakeGate(), self.factory(oom={4: 2}), build_id="b1")   # retry, then 2000 px
        self.assertEqual(fr["downscaled"], 1)
        self.assertEqual(self.pages_of(src)[4]["downscaled"], 2000)
        self.assertEqual(self.load(src)[GE.BUILD_KEY], "b1")
        log = []
        fr = self.run_fr(src, dv, FakeGate(), self.factory(log=log), build_id="b1")      # the card is free now
        self.assertEqual(log, [(4, (2100, 30))])                          # only page 4, at full size
        self.assertEqual((fr["pending"], fr["reused"], fr["reread_downscaled"], fr["downscaled"]), (1, 3, 1, 0))
        pages = self.pages_of(src)
        self.assertNotIn("downscaled", pages[4])
        self.assertEqual(pages[4]["text"], "MADE-UP PAGE 4 filler filler filler filler")
        self.assertTrue(self.load(src)["complete"])
        self.assertEqual(GE.reading_note(fr), GE.NOTE_BASE)

    def test_a_page_cut_down_again_keeps_its_mark(self):
        pdf = make_pdf(self.dir / "bundle.pdf", 3, layer_pages=(1,))
        src = source("bundle", [pdf])
        dv = FakeDV(shapes={3: (2100, 30)})
        self.run_fr(src, dv, FakeGate(), self.factory(oom={3: 2}), build_id="b1")
        log = []
        fr = self.run_fr(src, dv, FakeGate(), self.factory(log=log, oom={3: 2}), build_id="b1")  # still too large
        self.assertEqual(log, [(3, (2100, 30)), (3, (2100, 30)), (3, (2000, 29))])
        self.assertEqual((fr["reread_downscaled"], fr["downscaled"]), (1, 1))
        self.assertEqual(self.pages_of(src)[3]["downscaled"], 2000)        # the mark is never lost
        self.assertEqual(GE.reading_note(fr), GE.NOTE_BASE + "; 1 page(s) read smaller (card memory)")

    def test_a_skipped_picture_or_page_is_read_again(self):
        pdf = make_pdf(self.dir / "bundle.pdf", 2, layer_pages=(1,))
        src = source("bundle", [pdf])
        dv = FakeDV()
        fr = self.run_fr(src, dv, FakeGate(), self.factory(oom={2: -1}), build_id="b1")
        self.assertEqual(fr["skipped"], 1)
        log = []
        fr = self.run_fr(src, dv, FakeGate(), self.factory(log=log), build_id="b1")
        self.assertEqual(log, [(2, (40, 30))])
        self.assertEqual((fr["skipped"], fr["complete"]), (0, True))

    def test_another_build_or_engine_reads_every_page_again(self):
        pdf = make_pdf(self.dir / "bundle.pdf", 3, layer_pages=(1,))
        src = source("bundle", [pdf])
        dv = FakeDV()
        self.run_fr(src, dv, FakeGate(), self.factory(), build_id="b1")
        log = []
        fr = self.run_fr(src, dv, FakeGate(), self.factory(log=log), build_id="b2")
        self.assertEqual(log, [(2, (40, 30)), (3, (40, 30))])
        self.assertEqual((fr["pending"], fr["reused"], fr["stale_store"]), (3, 0, True))
        self.assertEqual(self.load(src)[GE.BUILD_KEY], "b2")
        d = self.load(src)                                                # gpuread's own engine label
        d["engine"] = GR.ENGINE_LABEL
        self.store(src).write_text(json.dumps(d), encoding="utf-8")
        log.clear()
        fr = self.run_fr(src, dv, FakeGate(), self.factory(log=log), build_id="b2")
        self.assertEqual((len(log), fr["stale_store"]), (2, True))
        log.clear()
        fr = self.run_fr(src, dv, FakeGate(), self.factory(log=log), build_id="b2")   # same build: nothing read
        self.assertEqual((log, fr["pending"], fr["stale_store"], fr["model_loaded"]), ([], 0, False, False))

    def test_the_default_build_id_is_this_builds(self):
        from src.verify.runner.app import version
        src = source("bundle", [make_pdf(self.dir / "bundle.pdf", 2, layer_pages=(1,))])
        self.run_fr(src, FakeDV(), FakeGate(), self.factory())
        self.assertEqual(GE.current_build_id(), str(version.BUILD_ID))
        self.assertEqual(self.load(src)[GE.BUILD_KEY], str(version.BUILD_ID))


class UsableDoneTests(unittest.TestCase):
    def test_rules(self):
        def page(n, **kw):
            return {"page": n, "text": "made-up", "kind": "ocr", "sideways": False, "seconds": 1.0, **kw}
        done = {"schema": GR.SCHEMA, "engine": GE.APP_ENGINE_LABEL, GE.BUILD_KEY: "b1", "case_id": "B-x",
                "files": [{"name": "a.pdf", "n_pages": 3,
                           "pages": [page(1), page(2, downscaled=2000), page(3, downscaled=1600)]},
                          {"name": "b.png", "n_pages": 1, "pages": [page(1)]}]}
        out, left = GE.usable_done(done, "b1")
        self.assertEqual([[p["page"] for p in e["pages"]] for e in out["files"]], [[1], [1]])
        self.assertEqual(left, {"stale": 0, "downscaled": 2})
        self.assertEqual((out["case_id"], out["engine"]), ("B-x", GE.APP_ENGINE_LABEL))
        self.assertEqual(len(done["files"][0]["pages"]), 3)                # the loaded dict is not changed
        self.assertEqual(GE.usable_done(done, "b2"), ({}, {"stale": 1, "downscaled": 0}))
        self.assertEqual(GE.usable_done({**done, "engine": GR.ENGINE_LABEL}, "b1"), ({}, {"stale": 1,
                                                                                          "downscaled": 0}))
        no_build = {k: v for k, v in done.items() if k != GE.BUILD_KEY}  # a file from before this rule
        self.assertEqual(GE.usable_done(no_build, "b1")[1]["stale"], 1)
        self.assertEqual(GE.usable_done({}, "b1"), ({}, {"stale": 0, "downscaled": 0}))


class GatedReaderTests(_Dir):
    def setUp(self):
        super().setUp()
        self.torch = FT.FakeTorch()
        self.card = GE.Card(self.torch)

    def test_gate_before_every_call_and_attributes_passed(self):
        gate = FakeGate()
        g = GE.GatedReader(FakePageReader(), gate, self.card, str(self.dir / "ascii"))
        g.readtext(page_array(1), detail=0)
        g.readtext(page_array(2), detail=0)
        self.assertEqual(gate.calls, ["read", "read"])
        self.assertEqual(g.device, "cuda:0")
        self.assertEqual((g.calls, g.oom_retries), (2, 0))

    def test_one_oom_is_retried_at_full_size(self):
        gate = FakeGate()
        log = []
        g = GE.GatedReader(FakePageReader(log=log, oom={7: 1}), gate, self.card, None)
        self.assertEqual(g.readtext(page_array(7), detail=0)[0], "MADE-UP PAGE 7")
        self.assertEqual(log, [(7, (40, 30)), (7, (40, 30))])
        self.assertEqual(gate.calls, ["read", "oom"])
        self.assertEqual((g.oom_retries, self.card.pauses, self.torch.cuda.syncs), (1, 1, 1))

    def test_room_on_the_card_before_the_retry(self):
        order = []
        gate = FakeGate(on_call=order.append)
        g = GE.GatedReader(FakePageReader(oom={7: 1}), gate, self.card, None)
        self.assertEqual(g.readtext(page_array(7), detail=0)[0], "MADE-UP PAGE 7")
        self.assertEqual(order, ["read", "oom", "room:oom"])              # pause, the bot's turn, room, retry
        self.assertEqual(gate.rooms, ["oom"])
        g = GE.GatedReader(FakePageReader(), gate, self.card, None)
        g.readtext(page_array(8), detail=0)
        self.assertEqual(gate.rooms, ["oom"])                             # no room check without an OOM

    def test_a_gate_without_wait_room(self):
        g = GE.GatedReader(FakePageReader(oom={7: 1}), SimpleNamespace(wait_turn=lambda where: None), self.card,
                           None)
        self.assertEqual(g.readtext(page_array(7), detail=0)[0], "MADE-UP PAGE 7")

    def test_a_second_oom_goes_on(self):
        g = GE.GatedReader(FakePageReader(oom={7: 2}), FakeGate(), self.card, None)
        with self.assertRaises(FT.OutOfMemoryError):
            g.readtext(page_array(7), detail=0)
        self.assertEqual(g.oom_retries, 1)

    def test_other_errors_and_cancel_are_not_retried(self):
        g = GE.GatedReader(FakePageReader(fail=7), FakeGate(), self.card, None)
        with self.assertRaises(ValueError):
            g.readtext(page_array(7), detail=0)
        self.assertEqual((g.oom_retries, self.card.pauses), (0, 0))
        log = []
        g = GE.GatedReader(FakePageReader(log=log), FakeGate(raise_at=("read", 1)), self.card, None)
        with self.assertRaises(Cancelled):
            g.readtext(page_array(7), detail=0)
        self.assertEqual(log, [])

    def test_a_non_ascii_path_is_copied_first(self):
        folder = self.dir / NON_ASCII
        folder.mkdir()
        png = make_png(folder / PICTURE)
        r = FakePageReader()
        GE.GatedReader(r, FakeGate(), self.card, str(self.dir / "ascii")).readtext(str(png), detail=0)
        self.assertEqual(r.path_bytes, [png.read_bytes()])

    def test_no_attribute_recursion_without_inner(self):
        g = GE.GatedReader.__new__(GE.GatedReader)
        with self.assertRaises(AttributeError):
            g.device


class MakeReaderTests(unittest.TestCase):
    def test_reader_arguments_and_device(self):
        eo = FT.FakeEasyOCR()
        r = GE.make_easyocr_reader(GE.Card(FT.FakeTorch()), "M", "U", easyocr_loader=lambda: eo)
        self.assertEqual(r.device, "cuda")
        self.assertEqual(eo.calls, [(["en"], {"gpu": True, "verbose": False, "model_storage_directory": "M",
                                              "user_network_directory": "U", "download_enabled": False})])

    def test_a_reader_off_the_card_is_refused(self):
        eo = FT.FakeEasyOCR(device="cpu")
        with self.assertRaises(GE.CardRefused) as cm:
            GE.make_easyocr_reader(GE.Card(FT.FakeTorch(), easyocr_loader=lambda: eo), "M", "U")
        self.assertEqual(cm.exception.code, GE.CARD_FAILED)

    def test_default_loaders_refuse_under_no_card(self):
        with mock.patch.dict(os.environ, {GE.NO_CARD_ENV: "1"}):
            for loader in (GE._import_torch, GE._import_easyocr):
                with self.assertRaises(GE.CardRefused):
                    loader()
        self.assertNotIn("torch", sys.modules)
        self.assertNotIn("easyocr", sys.modules)


# ------------------------------------------------------------------------------------------ non-ASCII paths
class AsciiPathTests(_Dir):
    def setUp(self):
        super().setUp()
        self.folder = self.dir / NON_ASCII
        self.folder.mkdir()
        self.png = make_png(self.folder / PICTURE)

    def test_copy_relative_to_the_working_folder(self):
        ascii_dir = self.dir / "ascii"
        with contextlib.chdir(self.dir):
            out = GE.ascii_path(str(self.png), str(ascii_dir))
            self.assertTrue(out.isascii())
            self.assertFalse(os.path.isabs(out))
            self.assertEqual(Path(out).read_bytes(), self.png.read_bytes())
            self.assertTrue(Path(out).name.startswith("img-"))
            self.assertEqual(Path(out).suffix, ".png")
            import cv2
            self.assertIsNotNone(cv2.imread(out, cv2.IMREAD_GRAYSCALE))
            self.assertEqual(GE.ascii_path(str(self.png), str(ascii_dir)), out)     # reused, not copied again
        self.assertEqual(len(list(ascii_dir.iterdir())), 1)

    def test_data_folder_with_non_ascii_letters(self):
        data = self.folder / "data"
        ascii_dir = data / "tmp" / "job" / "ascii"
        data.mkdir()
        with contextlib.chdir(data):
            out = GE.ascii_path(str(self.png), str(ascii_dir))
        self.assertTrue(out.isascii())
        self.assertEqual(Path(out).parts[:3], ("tmp", "job", "ascii"))
        self.assertEqual((data / out).read_bytes(), self.png.read_bytes())

    def test_no_ascii_path_possible(self):
        plain = self.dir / "plain"
        plain.mkdir()
        with contextlib.chdir(plain), self.assertRaises(RuntimeError):
            GE.ascii_path(str(self.png), str(self.folder / "copies"))

    def test_another_drive_gives_the_absolute_path(self):
        ascii_dir = self.dir / "ascii"
        with mock.patch.object(GE.os.path, "relpath", side_effect=ValueError("path is on mount 'D:'")):
            out = GE.ascii_path(str(self.png), str(ascii_dir))
        self.assertTrue(os.path.isabs(out) and out.isascii())
        self.assertEqual(Path(out).read_bytes(), self.png.read_bytes())

    def test_pass_through(self):
        a = page_array(1)
        self.assertIs(GE.ascii_path(a, str(self.dir)), a)
        self.assertEqual(GE.ascii_path("C:\\plain\\file.png", str(self.dir)), "C:\\plain\\file.png")
        missing = str(self.folder / "missing-\u00e9.png")
        self.assertEqual(GE.ascii_path(missing, str(self.dir)), missing)
        p = Path(self.png)
        self.assertIs(GE.ascii_path(p, str(self.dir)), p)
        self.assertFalse((self.dir / "ascii").exists())


# --------------------------------------------------------------------------------------------- the card start
class StartCardTests(unittest.TestCase):
    INFO = SimpleNamespace(name=FT.CARD_NAME, total_mb=8151, cc=(12, 0), index=0)

    def start(self, torch, gate=None, info=INFO, **kw):
        return GE.start_card(info, gate or FakeGate(), torch_loader=lambda: torch, **kw)

    def test_refused_before_torch_under_no_card(self):
        loader = mock.Mock()
        gate = FakeGate()
        with mock.patch.dict(os.environ, {GE.NO_CARD_ENV: "1"}), self.assertRaises(GE.CardRefused) as cm:
            GE.start_card(self.INFO, gate, torch_loader=loader)
        self.assertEqual(cm.exception.code, GE.NO_NVIDIA_CARD)
        loader.assert_not_called()
        self.assertEqual(gate.calls, [])

    def test_refused_when_the_cap_would_not_hold(self):
        for name in GE.NO_CACHING_ENV:
            loader = mock.Mock()
            with card_env(), mock.patch.dict(os.environ, {name: "1"}), self.assertRaises(GE.CardRefused) as cm:
                GE.start_card(self.INFO, FakeGate(), torch_loader=loader)
            self.assertEqual((cm.exception.code, cm.exception.values), (GE.CARD_FAILED, {"stage": "card"}))
            loader.assert_not_called()

    def test_refused_when_the_environment_is_not_torchs_default(self):
        for name in GE.NOT_DEFAULT_ENV:
            loader = mock.Mock()
            with card_env(), mock.patch.dict(os.environ, {name: "x"}), self.assertRaises(RuntimeError) as cm:
                GE.start_card(self.INFO, FakeGate(), torch_loader=loader)
            self.assertIn(name, str(cm.exception))
            loader.assert_not_called()

    def test_start_sets_the_cap_and_never_a_flag(self):
        torch = FT.FakeTorch()
        order = []
        gate = FakeGate(on_call=lambda where: order.append((where, len(torch.cuda.calls))))
        with card_env():
            card = self.start(torch, gate)
        self.assertEqual(order, [("start", 0)])                           # the gate's turn before CUDA starts
        fraction, device = torch.cuda.fraction
        self.assertAlmostEqual(fraction, 3.0 * FT.GIB / (8151 * FT.MIB) * 0.999)
        self.assertEqual(device, 0)
        self.assertEqual(torch.flag_sets, [])                             # torch's defaults, never set
        self.assertIs(gate.on_pause.__self__, card)
        self.assertIs(gate.on_room.__self__, card)
        self.assertEqual(gate.on_room(), 3072 - 180 + GE.ROOM_HEADROOM_MB)
        self.assertEqual(gate.on_pause(), 180)
        self.assertEqual(torch.cuda.calls[-2:], ["synchronize", "empty_cache"])
        self.assertEqual(card.notes, [])
        f = card.facts()
        self.assertEqual((f["torch"], f["cuda"], f["name"], f["cc"], f["cap_gb"], f["allocator_backend"]),
                         ("2.11.0+cu128", "12.8", FT.CARD_NAME, "12.0", 3.0, "native"))
        self.assertFalse(any(isinstance(v, str) and ("\\" in v or "/" in v) for v in f.values()))

    def test_cuda_not_available(self):
        with card_env(), self.assertRaises(GE.CardRefused) as cm:
            self.start(FT.FakeTorch(available=False))
        self.assertEqual(cm.exception.code, GE.CARD_FAILED)

    def test_card_generations(self):
        with card_env():
            for cc in ((7, 5), (8, 6), (8, 9), (9, 0), (10, 0), (12, 0)):
                self.start(FT.FakeTorch(cc=cc), info=None)                  # 8.9 runs sm_86 code
            for cc, which in (((6, 1), "old"), ((7, 0), "old"), ((11, 0), "new"), ((13, 0), "new")):
                with self.subTest(cc=cc), self.assertRaises(GE.CardRefused) as cm:
                    self.start(FT.FakeTorch(cc=cc), info=None)
                self.assertEqual(cm.exception.code, GE.CARD_NOT_SUPPORTED)
                self.assertEqual(cm.exception.values, {"name": FT.CARD_NAME, "which": which})

    def test_allocator_backend_must_be_native(self):
        with card_env(), self.assertRaises(GE.CardRefused) as cm:
            self.start(FT.FakeTorch(backend="cudaMallocAsync"))
        self.assertEqual(cm.exception.code, GE.CARD_FAILED)

    def test_flags_must_be_torchs_defaults(self):
        for flags in ({"cudnn_benchmark": True}, {"cudnn_allow_tf32": False}, {"matmul_allow_tf32": True},
                      {"cudnn_deterministic": True}):
            with card_env(), self.subTest(flags=flags), self.assertRaises(RuntimeError):
                self.start(FT.FakeTorch(flags=flags))
        with card_env(), self.assertRaises(RuntimeError):
            self.start(FT.FakeTorch(precision="high"))

    def test_a_mismatch_with_nvml_is_noted_not_fatal(self):
        info = SimpleNamespace(name="NVIDIA GeForce RTX 9999", total_mb=4000, cc=(8, 6), index=0)
        with card_env(), self.assertLogs(GE.LOG, "WARNING") as logs:
            card = self.start(FT.FakeTorch(), info=info)
        self.assertEqual(len(card.notes), 3)
        self.assertEqual(len(logs.records), 3)

    def test_card_pause_free_peaks(self):
        torch = FT.FakeTorch(reserved_mb=200)
        card = GE.Card(torch)
        self.assertEqual(card.pause(), 200)
        self.assertEqual(torch.cuda.calls, ["synchronize", "empty_cache"])
        card.free()
        self.assertEqual(torch.cuda.calls[-1], "empty_cache")
        self.assertEqual(card.peaks(), (2.41, 2.8))


# ----------------------------------------------------------------------------------------- the light reader
def det_fn(log=None, state=None, out=None):
    """The detector stand-in: [B, 3, H, W] -> zeros [B, H/2, W/2, 2]; `state["oom"]` card calls run out of memory."""
    def fn(arr, device):
        if log is not None:
            log.append(device)
        if state and device == "cuda" and state.get("oom", 0) > 0:
            state["oom"] -= 1
            raise FT.oom("made-up detection")
        if state and state.get("cuda_error") and device == "cuda":
            raise FT.AcceleratorError("CUDA error: an illegal memory access was encountered")
        if out is not None:
            return out(arr)
        b, _c, h, w = arr.shape
        return np.full((b, h // 2, w // 2, 2), 0.5 if device == "cpu" else 0.25, dtype=np.float32)
    return fn


def rec_fn(arr, device):
    b, _c, _h, w = arr.shape
    return np.zeros((b, max(1, w // 4), 97), dtype=np.float32)


class TorchSessionTests(unittest.TestCase):
    def setUp(self):
        self.torch = FT.FakeTorch()
        self.card = GE.Card(self.torch)
        self.gate = FakeGate()

    def session(self, kind="det", fn=None, **kw):
        return GE.TorchSession(kind, FT.FakeNet(fn or (det_fn() if kind == "det" else rec_fn)), self.card,
                               self.gate, **kw)

    def test_run_on_the_card(self):
        s = self.session()
        self.assertEqual((s.cpu_module.device, s.cuda_module.device), ("cpu", "cuda"))
        self.assertIsNot(s.cpu_module, s.cuda_module)
        out = s.run(None, {"image": np.zeros((1, 3, 64, 96), dtype=np.float32)})
        self.assertEqual(len(out), 1)
        self.assertEqual((out[0].shape, out[0].dtype), ((1, 32, 48, 2), np.float32))
        self.assertTrue((out[0] == 0.25).all())
        self.assertTrue(self.torch.from_numpy_args[-1]["in_inference"])
        self.assertEqual(self.gate.calls, ["det", "det"])                 # moving the weights, then the call
        st = s.stats()
        self.assertEqual((st["calls_card"], st["calls_cpu"], st["oom_retries"]), (1, 0, 0))
        r = self.session("rec")
        out = r.run(["logits"], {"image": np.zeros((2, 1, 64, 40), dtype=np.float32)})
        self.assertEqual(out[0].shape, (2, 10, 97))

    def test_inputs_onnx_runtime_would_refuse(self):
        log = []
        s = self.session(fn=det_fn(log))
        bad = [{"image": np.zeros((1, 3, 8, 8), dtype=np.float64)},
               {"image": np.zeros((3, 8, 8), dtype=np.float32)},
               {"image": np.zeros((1, 1, 8, 8), dtype=np.float32)},
               {"image": np.zeros((1, 3, 0, 8), dtype=np.float32)},
               {"image": [[0.0]]},
               {"input": np.zeros((1, 3, 8, 8), dtype=np.float32)},
               {"image": np.zeros((1, 3, 8, 8), dtype=np.float32), "other": 1}]
        for feeds in bad:
            with self.subTest(feeds=list(feeds)), self.assertRaises(ValueError):
                s.run(None, feeds)
        with self.assertRaises(ValueError):
            s.run(["logits"], {"image": np.zeros((1, 3, 8, 8), dtype=np.float32)})
        r = self.session("rec")
        for shape in ((1, 1, 32, 8), (1, 3, 64, 8)):
            with self.assertRaises(ValueError):
                r.run(None, {"image": np.zeros(shape, dtype=np.float32)})
        self.assertEqual(log, [])
        self.assertEqual(self.gate.calls, ["det", "rec"])                 # the gate is not asked for a refused call

    def test_read_only_and_strided_inputs_are_copied(self):
        s = self.session()
        x = np.zeros((1, 3, 8, 8), dtype=np.float32)
        x.setflags(write=False)
        s.run(None, {"image": x})
        y = np.zeros((1, 3, 8, 16), dtype=np.float32)[..., ::2]
        s.run(None, {"image": y})
        self.assertTrue(all(a["writeable"] and a["c_contiguous"] for a in self.torch.from_numpy_args))

    def test_one_oom_is_retried_on_the_card(self):
        log, state = [], {"oom": 1}
        s = self.session(fn=det_fn(log, state))
        out = s.run(None, {"image": np.zeros((1, 3, 8, 8), dtype=np.float32)})
        self.assertTrue((out[0] == 0.25).all())
        self.assertEqual(log, ["cuda", "cuda"])
        self.assertEqual(self.gate.calls, ["det", "det", "oom"])
        self.assertEqual(self.card.pauses, 1)
        st = s.stats()
        self.assertEqual((st["calls_card"], st["calls_cpu"], st["oom_retries"]), (1, 0, 1))

    def test_room_on_the_card_before_the_retry(self):
        order = []
        self.gate.on_call = order.append
        s = self.session(fn=det_fn(None, {"oom": 1}))
        s.run(None, {"image": np.zeros((1, 3, 8, 8), dtype=np.float32)})
        self.assertEqual(order, ["det", "det", "oom", "room:oom"])
        self.assertEqual(self.gate.rooms, ["oom"])

    def test_a_second_oom_runs_that_call_on_the_processor(self):
        log, state = [], {"oom": 2}
        s = self.session(fn=det_fn(log, state))
        with self.assertLogs(GE.LOG, "WARNING"):
            out = s.run(None, {"image": np.zeros((1, 3, 8, 8), dtype=np.float32)})
        self.assertTrue((out[0] == 0.5).all())                             # the processor module's answer
        self.assertEqual(log, ["cuda", "cuda", "cpu"])
        st = s.stats()
        self.assertEqual((st["calls_card"], st["calls_cpu"], st["cpu_reasons"]), (0, 1, {"oom": 1}))
        s.run(None, {"image": np.zeros((1, 3, 8, 8), dtype=np.float32)})   # the next call is on the card again
        self.assertEqual(log[-1], "cuda")

    def test_the_oom_retry_runs_the_detector_lean(self):
        """G2 (P5): the retry after an out-of-memory runs the detector through lean_detector_scores (the same
        numbers with less card memory at the peak: f9's and the EAP folder's 2560-pixel detections fit the cap),
        counted as calls_lean; the first attempt is the module itself."""
        log, state, lean = [], {"oom": 1}, []

        def fake_lean(torch, module, xt):
            lean.append((torch is self.torch, xt.device))
            return module(xt)
        s = self.session(fn=det_fn(log, state))
        with mock.patch.object(GE, "lean_detector_scores", fake_lean):
            out = s.run(None, {"image": np.zeros((1, 3, 8, 8), dtype=np.float32)})
            self.assertTrue((out[0] == 0.25).all())
            self.assertEqual(log, ["cuda", "cuda"])
            self.assertEqual(lean, [(True, "cuda")])
            st = s.stats()
            self.assertEqual((st["calls_card"], st["calls_lean"], st["calls_cpu"], st["oom_retries"]), (1, 1, 0, 1))
            s.run(None, {"image": np.zeros((1, 3, 8, 8), dtype=np.float32)})     # no OOM: the module itself
            self.assertEqual(len(lean), 1)
            r = self.session("rec", fn=lambda a, d: (state.update(oom=0), rec_fn(a, d))[1])
            r.run(None, {"image": np.zeros((1, 1, 64, 40), dtype=np.float32)})
            self.assertEqual(r.stats()["calls_lean"], 0)

    def test_a_lean_retry_that_runs_out_too_goes_to_the_processor_lean(self):
        log, state, lean = [], {"oom": 2}, []

        def fake_lean(torch, module, xt):
            lean.append(xt.device)
            return module(xt)
        s = self.session(fn=det_fn(log, state))
        with mock.patch.object(GE, "lean_detector_scores", fake_lean), self.assertLogs(GE.LOG, "WARNING"):
            out = s.run(None, {"image": np.zeros((1, 3, 8, 8), dtype=np.float32)})
        self.assertTrue((out[0] == 0.5).all())
        self.assertEqual(log, ["cuda", "cuda", "cpu"])
        self.assertEqual(lean, ["cuda", "cpu"])                 # the processor's detector pass is lean as well
        st = s.stats()
        self.assertEqual((st["calls_card"], st["calls_lean"], st["calls_cpu"]), (0, 0, 1))

    def test_lean_applies_only_to_a_detector_over_craft(self):
        self.assertIsNone(GE.lean_detector_scores(self.torch, FT.FakeNet(det_fn()), None))
        self.assertIsNone(GE.lean_detector_scores(self.torch, SimpleNamespace(net=FT.FakeNet(det_fn())), None))
        log, state = [], {"oom": 1}
        s = self.session(fn=det_fn(log, state))                 # a FakeNet: the retry runs the module itself
        s.run(None, {"image": np.zeros((1, 3, 8, 8), dtype=np.float32)})
        self.assertEqual((log, s.stats()["calls_lean"]), (["cuda", "cuda"], 0))

    def test_the_processor_can_be_turned_off(self):
        s = self.session(fn=det_fn(None, {"oom": 2}), allow_cpu=False)
        with self.assertRaises(GE.CardRefused) as cm:
            s.run(None, {"image": np.zeros((1, 3, 8, 8), dtype=np.float32)})
        self.assertEqual(cm.exception.code, GE.LOW_CARD_MEMORY)

    def test_other_cuda_errors_are_raised(self):
        log = []
        s = self.session(fn=det_fn(log, {"cuda_error": True}))
        with self.assertRaises(FT.AcceleratorError) as cm:
            s.run(None, {"image": np.zeros((1, 3, 8, 8), dtype=np.float32)})
        self.assertEqual(log, ["cuda"])
        self.assertEqual(GE.card_error_code(cm.exception), GE.CARD_FAILED)
        self.assertEqual(s.stats()["calls_cpu"], 0)

    def test_cancel_passes_through_the_gate(self):
        log = []
        s = self.session(fn=det_fn(log))
        self.gate.raise_at = ("det", 2)
        with self.assertRaises(Cancelled):
            s.run(None, {"image": np.zeros((1, 3, 8, 8), dtype=np.float32)})
        self.assertEqual(log, [])

    def test_outputs_are_checked(self):
        x = np.zeros((2, 3, 8, 8), dtype=np.float32)
        for out in (lambda a: np.zeros((2, 4, 4, 2), dtype=np.float64),
                    lambda a: np.zeros((1, 4, 4, 2), dtype=np.float32),
                    lambda a: np.zeros((2, 4, 4, 3), dtype=np.float32),
                    lambda a: np.zeros((2, 4, 4), dtype=np.float32)):
            s = self.session(fn=det_fn(out=out))
            with self.assertRaises(RuntimeError):
                s.run(None, {"image": x})
        r = self.session("rec", fn=lambda a, d: np.zeros((1, 5, 96), dtype=np.float32))
        with self.assertRaises(RuntimeError):
            r.run(None, {"image": np.zeros((1, 1, 64, 20), dtype=np.float32)})

    def test_kind(self):
        with self.assertRaises(ValueError):
            GE.TorchSession("cls", FT.FakeNet(rec_fn), self.card, self.gate)


class CardMemoryBetweenCallsTests(unittest.TestCase):
    """G1 review finding 1 (R2.1.5): after every call on the card torch's cache is given back above CACHE_KEEP_MB,
    so the app never sits on cached card memory between two light-reader calls (the runner's processor stretches
    last up to a minute, and the bot's check may start in one)."""

    def test_release_only_above_the_keep_size(self):
        torch = FT.FakeTorch(reserved_mb=GE.CACHE_KEEP_MB)
        card = GE.Card(torch)
        self.assertFalse(card.release())
        self.assertEqual(torch.cuda.calls, [])
        torch.cuda.reserved = (GE.CACHE_KEEP_MB + 1) * FT.MIB
        self.assertTrue(card.release())
        self.assertEqual(torch.cuda.calls, ["empty_cache"])               # no synchronize, no gc pause
        self.assertEqual((card.releases, card.pauses), (1, 0))

    def test_room(self):
        self.assertEqual(GE.Card(FT.FakeTorch(reserved_mb=180)).room_mb(), 3072 - 180 + GE.ROOM_HEADROOM_MB)
        self.assertEqual(GE.Card(FT.FakeTorch(reserved_mb=4000)).room_mb(), GE.ROOM_HEADROOM_MB)
        self.assertEqual(GE.Card(FT.FakeTorch(reserved_mb=0), cap_gb=3.0).room_mb(), 3072 + 512)

    def test_large_calls_leave_nothing_cached(self):
        torch = FT.FakeTorch(reserved_mb=110)
        torch.cuda.after_empty_mb = 110                                   # the weights stay
        card = GE.Card(torch)
        turns = []
        gate = FakeGate(on_call=lambda where: turns.append((where, torch.cuda.empties)))

        def fn(arr, device):
            if device == "cuda":
                torch.cuda.reserved = 2900 * FT.MIB                       # a page-size detection's activations
            b, _c, h, w = arr.shape
            return np.zeros((b, h // 2, w // 2, 2), dtype=np.float32)
        s = GE.TorchSession("det", FT.FakeNet(fn), card, gate)
        for _ in range(3):
            out = s.run(None, {"image": np.zeros((1, 3, 8, 8), dtype=np.float32)})
            self.assertEqual(out[0].shape, (1, 4, 4, 2))
            self.assertLessEqual(torch.cuda.memory_reserved(), GE.CACHE_KEEP_MB * FT.MIB)
        self.assertEqual(turns, [("det", 0), ("det", 0), ("det", 1), ("det", 2)])   # the turn, the call, release
        self.assertEqual((s.stats()["releases"], card.releases, torch.cuda.syncs), (3, 3, 0))

    def test_small_calls_keep_their_small_cache(self):
        torch = FT.FakeTorch(reserved_mb=180)
        card = GE.Card(torch)
        s = GE.TorchSession("rec", FT.FakeNet(rec_fn), card, FakeGate())
        s.run(None, {"image": np.zeros((2, 1, 64, 40), dtype=np.float32)})
        self.assertEqual((s.stats()["releases"], torch.cuda.empties), (0, 0))

    def test_the_light_reader_reports_its_releases(self):
        torch = FT.FakeTorch(reserved_mb=2900)
        torch.cuda.after_empty_mb = 2900                                  # stays high: every call releases
        card = GE.Card(torch)
        det = GE.TorchSession("det", FT.FakeNet(det_fn()), card, FakeGate())
        det.run(None, {"image": np.zeros((1, 3, 8, 8), dtype=np.float32)})
        reader = SimpleNamespace(det=det, rec=GE.TorchSession("rec", FT.FakeNet(rec_fn), card, FakeGate()))
        st = GE.card_light_reader_class().stats(reader)
        self.assertEqual((st["releases"], st["det"]["releases"], st["rec"]["releases"]), (1, 1, 0))


class RealGateTests(_Dir):
    """G1 review findings 1 and 2 across modules: the REAL card gate (a made-up lock file, a fake clock) with a Card
    from start_card (fake torch) and a TorchSession."""

    def test_a_lock_between_two_calls_then_room_then_the_call(self):
        from src.verify.runner.app import cardgate as CG
        lock = self.dir / "made-up.lock"
        clock = {"t": 1000.0}
        events, free_reads = [], iter([2500, 3600])

        def sleep(s):
            clock["t"] += s
            if lock.exists():
                lock.unlink()                                             # the made-up bot ends after one poll
        gate = CG.CardGate([str(lock)], lambda ev, **f: events.append((f.get("why"), f.get("state"))),
                           free_mb=lambda: next(free_reads), clock=lambda: clock["t"], sleep=sleep)
        torch = FT.FakeTorch(reserved_mb=110)
        torch.cuda.after_empty_mb = 110
        with card_env():
            card = GE.start_card(StartCardTests.INFO, gate, torch_loader=lambda: torch)

        def fn(arr, device):
            if device == "cuda":
                torch.cuda.reserved = 2900 * FT.MIB
            b, _c, h, w = arr.shape
            return np.zeros((b, h // 2, w // 2, 2), dtype=np.float32)
        s = GE.TorchSession("det", FT.FakeNet(fn), card, gate)
        x = {"image": np.zeros((1, 3, 8, 8), dtype=np.float32)}
        s.run(None, x)
        self.assertEqual(torch.cuda.memory_reserved(), 110 * FT.MIB)       # nothing cached after the call
        lock.write_bytes(b"")                                             # the made-up bot starts between calls
        clock["t"] += 1.0
        s.run(None, x)
        self.assertEqual(events[:2], [("lock", "start"), ("lock", "end")])
        self.assertEqual((events[2], events[-1]), (("memory", "start"), ("memory", "end")))
        st = gate.stats()
        self.assertEqual((st["room_checks"], st["room_short"], st["free_mb"]), (1, 0, 3600))
        self.assertEqual((s.stats()["calls_card"], s.stats()["releases"]), (2, 2))
        self.assertEqual(card.room_mb(), 3072 - 110 + GE.ROOM_HEADROOM_MB)


class _StubbornFlags:
    """A flag namespace that ignores every assignment (strict flags that do not take)."""

    def __init__(self, **values):
        object.__setattr__(self, "_v", dict(values))

    def __getattr__(self, name):
        return self.__dict__["_v"][name]

    def __setattr__(self, name, value):
        pass


class LightReaderTests(_Dir):
    def test_strict_fp32(self):
        torch = FT.FakeTorch()
        flags = GE.strict_fp32(torch, {})
        self.assertEqual(sorted(torch.flag_sets), sorted([("cudnn.deterministic", True), ("cudnn.benchmark", False),
                                                         ("cuda.matmul.allow_tf32", False),
                                                         ("cudnn.allow_tf32", False)]))
        self.assertEqual(flags["cudnn_deterministic"], True)
        self.assertEqual((flags["cublas_workspace_config"], flags["cuda_alloc_conf"]), ("", ""))
        torch.backends.cudnn = _StubbornFlags(deterministic=False, benchmark=False, allow_tf32=True)
        with self.assertRaises(RuntimeError):
            GE.strict_fp32(torch, {})

    def test_load_models_as_export_onnx(self):
        eo = FT.FakeEasyOCR()
        model_dir = self.dir / "engine" / "easyocr" / "model"
        det_m, rec_m, facts = GE.load_models(FT.FakeTorch(), eo, model_dir)
        self.assertEqual(eo.calls, [(["en"], {"gpu": False, "quantize": False, "verbose": False,
                                              "model_storage_directory": str(model_dir), "download_enabled": False,
                                              "user_network_directory": str(model_dir.parent / "user_network")})])
        reader = eo.readers[0]
        self.assertEqual((type(det_m).__name__, type(rec_m).__name__), ("DetectorOut", "RecognizerOut"))
        self.assertIs(det_m.net, reader.detector)
        self.assertIs(rec_m.net, reader.recognizer)
        self.assertEqual(type(reader.recognizer.AdaptiveAvgPool).__name__, "MeanPool")
        self.assertFalse(det_m.training or rec_m.training or reader.detector.training)
        self.assertEqual((det_m.device, rec_m.device), ("cpu", "cpu"))
        self.assertEqual(facts, {"detector": "FakeCraft", "recognizer": "FakeRecognizer", "num_class": 97,
                                 "characters": 96})
        y = rec_m(FT.Tensor(np.zeros((1, 1, 64, 40), dtype=np.float32)))
        self.assertEqual(y.shape, (1, 10, 97))
        self.assertEqual(reader.recognizer.pool_calls, 1)                 # through MeanPool

    def test_load_models_refuses_a_non_float32_tensor(self):
        with self.assertRaises(RuntimeError):
            GE.load_models(FT.FakeTorch(), FT.FakeEasyOCR(float64_param=True), self.dir)

    def test_light_reader_end_to_end(self):
        import glue_numpy
        torch = FT.FakeTorch()
        eo = FT.FakeEasyOCR(blob=(10, 20, 10, 60), word="HI")
        card = GE.Card(torch, easyocr_loader=lambda: eo)
        gate = FakeGate()
        reader = GE.light_reader(card, gate, GLUE_DATA, weights_dir(self.dir / "model"),
                                 ascii_dir=str(self.dir / "ascii"))
        self.assertIsInstance(reader, glue_numpy.LightReader)
        self.assertIs(type(reader), GE.CardLightReader)
        self.assertEqual(len(torch.flag_sets), 4)
        self.assertTrue(reader.flags["cudnn_deterministic"])
        self.assertEqual(reader.facts["num_class"], 97)
        self.assertIsInstance(reader.det, GE.TorchSession)
        self.assertEqual((reader.det.cuda_module.device, reader.rec.cuda_module.device), ("cuda", "cuda"))
        for name in ("rec", "det", "character", "lang_char"):                # what closer._inner and _Rec use
            self.assertTrue(hasattr(reader, name))
        img = np.full((100, 200, 3), 255, dtype=np.uint8)
        self.assertEqual(reader.readtext(img, detail=0), ["HI"])
        st = reader.stats()
        self.assertEqual((st["det"]["calls_card"], st["cpu_calls"]), (1, 0))
        self.assertGreaterEqual(st["rec"]["calls_card"], 1)
        self.assertEqual(st["card_calls"], st["det"]["calls_card"] + st["rec"]["calls_card"])
        self.assertIn("det", gate.calls)
        self.assertIn("rec", gate.calls)
        self.assertEqual(eo.calls[0][1]["gpu"], False)

    def test_light_reader_reads_a_non_ascii_path(self):
        import glue_numpy
        folder = self.dir / NON_ASCII
        folder.mkdir()
        png = make_png(folder / PICTURE, size=64)
        card = GE.Card(FT.FakeTorch(), easyocr_loader=lambda: FT.FakeEasyOCR())
        reader = GE.light_reader(card, FakeGate(), GLUE_DATA, weights_dir(self.dir / "model"),
                                 ascii_dir=str(self.dir / "a"))
        with mock.patch.object(glue_numpy, "reformat_input", wraps=glue_numpy.reformat_input) as spy:
            self.assertEqual(reader.readtext(str(png), detail=0), [])
        arg = spy.call_args[0][0]
        self.assertTrue(arg.isascii())
        _img, grey = glue_numpy.reformat_input(arg)                         # cv2.imread opens the ASCII copy
        self.assertIsNotNone(grey)
        self.assertEqual(grey.shape, (64, 64))

    def test_glue_data_must_fit_the_recogniser(self):
        class Narrow(FT.FakeEasyOCR):
            def Reader(self, lang_list, **kw):
                r = super().Reader(lang_list, **kw)
                r.recognizer.Prediction.out_features = 96
                return r
        card = GE.Card(FT.FakeTorch(), easyocr_loader=lambda: Narrow())
        with self.assertRaises(RuntimeError):
            GE.light_reader(card, FakeGate(), GLUE_DATA, weights_dir(self.dir / "model"))

    def test_missing_weights_stop_before_any_flag_or_load(self):
        torch = FT.FakeTorch()
        eo = FT.FakeEasyOCR()
        model_dir = self.dir / "models"
        model_dir.mkdir()
        with self.assertRaises(FileNotFoundError):
            GE.light_reader(GE.Card(torch, easyocr_loader=lambda: eo), FakeGate(), GLUE_DATA, model_dir)
        self.assertEqual((torch.flag_sets, eo.calls), ([], []))
        self.assertFalse((self.dir / "user_network").exists())


# ------------------------------------------------------------------------------------------ errors and notes
class ErrorCodeTests(unittest.TestCase):
    def test_codes(self):
        cases = [
            (FT.AcceleratorError("CUDA error: no kernel image is available for execution on the device"),
             GE.CARD_NOT_SUPPORTED),
            (RuntimeError("CUDA error: CUDA driver version is insufficient for CUDA runtime version"),
             GE.DRIVER_TOO_OLD),
            (RuntimeError("The NVIDIA driver on your system is too old (found version 11040)."), GE.DRIVER_TOO_OLD),
            (FT.AcceleratorError("CUDA error: an illegal memory access was encountered"), GE.CARD_FAILED),
            (RuntimeError("cuDNN error: CUDNN_STATUS_EXECUTION_FAILED"), GE.CARD_FAILED),
            (RuntimeError("CUBLAS_STATUS_ALLOC_FAILED when calling cublasCreate(handle)"), GE.CARD_FAILED),
            (FT.oom(), GE.LOW_CARD_MEMORY),
            (GE.CardRefused(GE.CARD_TOO_SMALL, {"name": "x", "total": "4.0"}), GE.CARD_TOO_SMALL),
            (ValueError("a made-up error"), None),
            (KeyError("page"), None),
        ]
        for e, code in cases:
            with self.subTest(e=type(e).__name__, code=code):
                self.assertEqual(GE.card_error_code(e), code)

    def test_the_chain_is_walked(self):
        try:
            try:
                raise FT.AcceleratorError("CUDA error: no kernel image is available for execution on the device")
            except RuntimeError as inner:
                raise RuntimeError("the page read failed") from inner
        except RuntimeError as e:
            self.assertEqual(GE.card_error_code(e), GE.CARD_NOT_SUPPORTED)
        try:
            try:
                raise RuntimeError("CUDA error: unspecified launch failure")
            except RuntimeError:
                raise ValueError("while reading")                          # implicit __context__
        except ValueError as e:
            self.assertEqual(GE.card_error_code(e), GE.CARD_FAILED)
        a, b = ValueError("a"), ValueError("b")
        a.__context__, b.__context__ = b, a                                # a cycle ends
        self.assertIsNone(GE.card_error_code(a))

    def test_card_memory_short(self):
        class CardMemoryShort(Exception):
            pass
        self.assertEqual(GE.card_error_code(CardMemoryShort("x")), GE.LOW_CARD_MEMORY)

    def test_is_oom_is_gpureads(self):
        for e in (FT.oom(), RuntimeError("CUDA out of memory."), RuntimeError("x"), ValueError("out of memory"),
                  MemoryError()):
            self.assertEqual(GE.is_oom(e), GR.is_oom(e))

    def test_card_refused(self):
        e = GE.CardRefused(GE.CARD_NOT_SUPPORTED, {"name": FT.CARD_NAME, "which": "old"})
        self.assertEqual((e.code, e.values, str(e)), (GE.CARD_NOT_SUPPORTED, {"name": FT.CARD_NAME, "which": "old"},
                                                      GE.CARD_NOT_SUPPORTED))

    def test_reading_note_counts_pages_read_smaller(self):
        self.assertEqual(GE.reading_note({"downscaled": 2}), GE.NOTE_BASE + "; 2 page(s) read smaller (card memory)")
        self.assertEqual(GE.reading_note({"skipped": 1, "downscaled": 2}),
                         GE.NOTE_BASE + "; 1 page(s) left to the light reader (too large for the cap)"
                         "; 2 page(s) read smaller (card memory)")
        big = GE.reading_note({"skipped": 12345, "downscaled": 67890})
        self.assertLessEqual(len(big), GE.NOTE_MAX)
        self.assertTrue(big.endswith("; 12345 page(s) left to the light reader; 67890 read smaller"))

    def test_reading_note(self):
        self.assertEqual(GE.reading_note({"skipped": 0}), GE.NOTE_BASE)
        self.assertEqual(GE.reading_note(None), GE.NOTE_BASE)
        n = GE.reading_note({"skipped": 12345})
        self.assertTrue(n.startswith(GE.NOTE_BASE) and "12345 page(s)" in n)
        self.assertLessEqual(len(n), 200)
        self.assertTrue(n.isascii())
        self.assertIn("3.0 GB cap", GE.NOTE_BASE)


# ----------------------------------------------------------------------------------- source parity and hygiene
class SourceParityTests(unittest.TestCase):
    """export_modules and load_models are export_onnx.py's own, node for node; strict_fp32 / strict_flags and
    load_models' float32 check and facts are gpu_worker.py's (each skipped when the lab file is absent)."""

    MINE = ast.parse(GPUENGINE.read_text(encoding="utf-8"))
    NAMES = ("MeanPool", "DetectorOut", "RecognizerOut", "load_reader")

    def theirs(self, path: Path) -> ast.AST:
        if not path.is_file():
            self.skipTest(f"{path.name} is not on this PC")
        return ast.parse(path.read_text(encoding="utf-8"))

    def test_export_modules_are_export_onnx_definitions(self):
        theirs = defs_in(self.theirs(EXPORT), self.NAMES)
        self.assertEqual(set(theirs), set(self.NAMES))
        mine = defs_in(func(self.MINE, "export_modules"), self.NAMES)
        for name in self.NAMES:
            with self.subTest(name):
                self.assertEqual(mine.get(name), theirs[name])
        args = [a.arg for a in func(self.MINE, "export_modules").args.args]
        self.assertEqual(args, ["torch", "easyocr", "MODEL_DIR"])        # load_reader's MODEL_DIR is the folder

    @staticmethod
    def build_steps(fn: ast.AST):
        keep = {"reader", "det", "rec", "det_m", "rec_m"}
        out = []
        for n in ast.walk(fn):
            if isinstance(n, ast.Assign) and len(n.targets) == 1:
                t = n.targets[0]
                if (isinstance(t, ast.Name) and t.id in keep) or (isinstance(t, ast.Attribute)
                                                                  and t.attr == "AdaptiveAvgPool"):
                    out.append((n.lineno, ast.dump(n)))
        return [d for _l, d in sorted(out)]

    def test_load_models_builds_as_export_onnx_main(self):
        theirs = self.build_steps(func(self.theirs(EXPORT), "main"))
        mine = self.build_steps(func(self.MINE, "load_models"))
        self.assertEqual(len(theirs), 6)
        self.assertEqual(mine, theirs)

    def test_strict_fp32_and_the_model_checks_are_gpu_workers(self):
        tree = self.theirs(WORKER)
        for name in ("strict_fp32", "strict_flags"):
            with self.subTest(name):
                self.assertEqual(ast.dump(func(self.MINE, name)), ast.dump(func(tree, name)))

        def tail(fn):
            return [ast.dump(n) for n in fn.body if isinstance(n, (ast.For, ast.Delete))
                    or (isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "facts")]
        self.assertEqual(tail(func(self.MINE, "load_models")), tail(func(tree, "load_models")))
        self.assertEqual(len(tail(func(tree, "load_models"))), 3)

    def test_on_card_pass_is_gpu_workers_nets(self):
        src = GPUENGINE.read_text(encoding="utf-8")
        for line in ("with torch.inference_mode():", "xt = torch.from_numpy(x).to(self.device)",
                     "out = y.contiguous().cpu().numpy()"):
            self.assertIn(line, src)


ONNX_PY = Path(r"C:\Hangeul\DOCCHECK\lab\onnx\.venv\Scripts\python.exe")
_LEAN_CHECK = r"""
import sys
sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])                      # -I: the worktree is not on the path by itself
import torch
from easyocr.craft import CRAFT
from src.verify.runner.app import gpuengine as GE
assert not torch.cuda.is_initialized()
torch.manual_seed(3)
net = CRAFT(pretrained=False).eval()
MeanPool, DetectorOut, RecognizerOut, load_reader = GE.export_modules(torch, None, "unused")
det = DetectorOut(net).eval()
res = []
for shape in ((1, 3, 96, 128), (1, 3, 100, 70), (2, 3, 64, 96)):
    x = torch.rand(shape) * 4 - 2
    with torch.inference_mode():
        a = det(x)
        b = GE.lean_detector_scores(torch, det, x)
    res.append(bool(a.shape == b.shape and torch.equal(a, b)))
class Other(torch.nn.Module):
    def forward(self, x):
        return x
res.append(GE.lean_detector_scores(torch, DetectorOut(Other()), x) is None)
res.append(not torch.cuda.is_initialized())
print(res)
"""


@unittest.skipUnless(ONNX_PY.is_file(), "lab\\onnx\\.venv (real torch and easyocr) is not on this PC")
class LeanDetectorRealTorchTests(unittest.TestCase):
    """lean_detector_scores against DetectorOut(CRAFT) itself, on the PROCESSOR with real torch and easyocr (a
    child of lab\\onnx\\.venv's Python, -I -B: read-only, no CUDA (CUDA_VISIBLE_DEVICES=-1), random weights, made-up
    inputs, odd sizes so the decoder's interpolations are not exact halvings): bit for bit equal."""

    def test_lean_is_the_same_forward(self):
        env = dict(os.environ, **{GE.NO_CARD_ENV: "1", "CUDA_VISIBLE_DEVICES": "-1", "PYTHONDONTWRITEBYTECODE": "1"})
        r = subprocess.run([str(ONNX_PY), "-I", "-B", "-c", _LEAN_CHECK, str(WORKTREE)], cwd=str(WORKTREE), env=env,
                           capture_output=True, text=True, timeout=600,
                           creationflags=0x00004000 if os.name == "nt" else 0)
        self.assertEqual(r.returncode, 0, r.stderr[-800:])
        self.assertEqual(r.stdout.strip().splitlines()[-1], "[True, True, True, True, True]")


class HygieneTests(unittest.TestCase):
    def test_import_loads_no_heavy_module(self):
        code = ("import sys; import src.verify.runner.app.gpuengine; "
                "print(sorted(m for m in ('torch', 'easyocr', 'glue_numpy', 'onnxruntime', 'cv2', 'pymupdf') "
                "if m in sys.modules))")
        env = dict(os.environ, **{GE.NO_CARD_ENV: "1"})
        r = subprocess.run([sys.executable, "-I", "-B", "-c", code], cwd=str(WORKTREE), env=env,
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr[-500:])
        self.assertEqual(r.stdout.strip(), "[]")

    def test_no_torch_here(self):
        self.assertNotIn("torch", sys.modules)
        self.assertNotIn("easyocr", sys.modules)
        self.assertNotIn("onnxruntime", GPUENGINE.read_text(encoding="utf-8").replace(
            "onnxruntime is never imported here", ""))

    def test_constants(self):
        self.assertEqual(GE.CAP_GB, GR.CAP_GB)
        self.assertEqual(GE.APP_ENGINE_LABEL, "bot EasyOCR on CUDA (app card gate, cap 3.0 GB)")
        self.assertNotEqual(GE.APP_ENGINE_LABEL, GR.ENGINE_LABEL)
        self.assertTrue(GE.APP_ENGINE_LABEL.isascii())


if __name__ == "__main__":
    unittest.main()
