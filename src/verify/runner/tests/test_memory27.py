"""Revision 2.7, memory (RUNNER_SPEC limit 15): the detector's ONNX session runs with the CPU memory arena off, and
the closer look's page search runs the detector on a page longer than closer.DETECT_LONG pixels as a copy scaled to
closer.DETECT_FIRST (then DETECT_FALLBACK when the line is not found), mapping its boxes back onto the page; a page up
to DETECT_LONG goes to the detector at its own size, as closer-3. MADE-UP text and synthetic pages only; no personal
data.
"""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

import numpy as np

from src.verify.runner import closer as C
from src.verify.runner import engine as E
from src.verify.runner.tests.test_closer import FONTS, _have_models

ROOT = Path(__file__).resolve().parents[4]          # the worktree (cwd of `unittest discover -t .`)
LINE_WORDS = ["TOLVIR", "WASEN", "SEVINA", "PORVIN", "ZELIRA", "PHATUN", "ROVEXA", "QELWOR", "TOSSAIN", "VONIMA"]
PAGE_SEARCH_PEAK_MB = 1600   # one page search on a made-up 400-dpi A4 page: peak committed memory of the process.
#                              Measured 28 Oct (step 1b, OPENBLAS_NUM_THREADS 1): 1.1 GB found at DETECT_FIRST (1.7 GB
#                              if the first search ran at 2048); before rev 2.7: 4.4-4.9 GB, 9.2 GB after a second search
PAGE_SEARCH_FALLBACK_PEAK_MB = 2300   # the same page searched at 1600 and then at 2048: measured 1.8 GB (a search
#                                       at the detector's own 2560 canvas: about 2.6 GB)


def a4_page(dpi: int = 400, sizes=(9, 10, 11, 12, 14), seed: int = 5):
    """A made-up A4 form at `dpi`: label + value lines of made-up words in a few sizes, a ruled table row.
    Returns (RGB uint8, [((x0, y0, x1, y1) of the value's ink, the value)]); every value is printed once."""
    import random
    from PIL import Image, ImageDraw, ImageFont
    rng = random.Random(seed)
    W, H = int(round(8.27 * dpi)), int(round(11.69 * dpi))
    im = Image.new("RGB", (W, H), (250, 250, 247))
    d = ImageDraw.Draw(im)
    y = int(0.8 * dpi)
    lines = []
    pairs = [(a, b) for a in LINE_WORDS for b in LINE_WORDS if a != b]
    rng.shuffle(pairs)
    while y < H - int(1.2 * dpi) and pairs:
        pt = rng.choice(sizes)
        f = ImageFont.truetype(str(FONTS / rng.choice(["arial.ttf", "times.ttf", "arialbd.ttf"])), int(pt * dpi / 72))
        label = "Name of the holder :"
        value = " ".join(pairs.pop())
        x = int(0.9 * dpi)
        d.text((x, y), label, font=f, fill=(20, 20, 30))
        vx = x + int(f.getlength(label + "    "))
        d.text((vx, y), value, font=f, fill=(20, 20, 30))
        l, t, r, b = d.textbbox((vx, y), value, font=f)
        lines.append(((float(l), float(t), float(r), float(b)), value))
        y += int(pt * dpi / 72 * 2.6)
    return np.array(im), lines


class _StubReader:
    """A reader whose detector returns given line boxes (in the pixels of the page it is handed): no model."""

    character = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    lang_char = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    rec = None
    det = None

    def __init__(self, full_lines, full_shape):
        self.full_lines = full_lines          # boxes in the page turned by k, at full resolution: {k: [...]}
        self.full_shape = full_shape          # {k: (h, w)} of the turned full page
        self.seen = []

    def detect(self, img, min_size=20, **_kw):
        h, w = img.shape[:2]
        k = next(k for k, (fh, fw) in self.full_shape.items() if abs(fh / h - fw / w) < 0.02 and
                 (fh >= fw) == (h >= w))
        fh, fw = self.full_shape[k]
        sx, sy = w / float(fw), h / float(fh)
        self.seen.append((k, (h, w), min_size))
        return [[int(round(x0 * sx)), int(round(x1 * sx)), int(round(y0 * sy)), int(round(y1 * sy))]
                for x0, y0, x1, y1 in self.full_lines[k]], []


class _Swap:
    """Use a reader (and DETECT_LONG / DETECT_FALLBACK) inside a test; the closer's reader, constants and memos are
    restored afterwards."""

    def __init__(self, reader, detect_long=None, fallback=None):
        self.reader, self.detect_long, self.fallback = reader, detect_long, fallback

    def __enter__(self):
        self.saved = (C._READER, C._REC, C.DETECT_LONG, C.DETECT_FALLBACK, dict(C._DET_MEMO), dict(C._LINE_MEMO),
                      C._HELD[0])
        C._READER, C._REC = self.reader, None
        if self.detect_long is not None:
            C.DETECT_LONG = self.detect_long
        if self.fallback is not None:
            C.DETECT_FALLBACK = self.fallback
        C._DET_MEMO.clear()
        C._LINE_MEMO.clear()
        return self

    def __exit__(self, *exc):
        C._READER, C._REC, C.DETECT_LONG, C.DETECT_FALLBACK = self.saved[:4]
        C._DET_MEMO.clear()
        C._DET_MEMO.update(self.saved[4])
        C._LINE_MEMO.clear()
        C._LINE_MEMO.update(self.saved[5])
        C._HELD[0] = self.saved[6]
        return False


# ============================================================================================ the mapping (no model)
class TestBoxesMappedBack(unittest.TestCase):
    def setUp(self):
        self.H, self.W = 4677, 3307                              # A4 at 400 dpi
        self.rgb = np.full((self.H, self.W, 3), 250, np.uint8)
        self.lines0 = [(300.0, 400.0 + 160 * i, 2600.0, 460.0 + 160 * i) for i in range(12)]
        # the same lines seen in the page turned a quarter turn (k = 1: np.rot90, counter-clockwise)
        self.lines1 = [(y0, self.W - x1, y1, self.W - x0) for x0, y0, x1, y1 in self.lines0]
        self.stub = _StubReader({0: self.lines0, 1: self.lines1},
                                {0: (self.H, self.W), 1: (self.W, self.H), 2: (self.H, self.W), 3: (self.W, self.H)})

    def test_scale_is_the_long_side_cap_and_never_enlarges(self):
        with _Swap(self.stub):
            self.assertAlmostEqual(C.detect_scale((4677, 3307, 3)), C.DETECT_FIRST / 4677.0)
            self.assertAlmostEqual(C.detect_scale((4677, 3307, 3), C.DETECT_FALLBACK), C.DETECT_FALLBACK / 4677.0)
            self.assertEqual(C.detect_scale((1200, 900, 3)), 1.0)
            self.assertEqual(C.detect_scale((2048, 1448, 3)), 1.0)
            self.assertEqual(C.detect_scale((1200, 900, 3), 2048), 1.0)
        with _Swap(self.stub, 0):
            self.assertEqual(C.detect_scale((4677, 3307, 3)), 1.0)

    def test_boxes_come_back_in_the_pages_own_pixels(self):
        with _Swap(self.stub):
            for k, want in ((0, self.lines0), (1, self.lines1)):
                got = C._boxes(self.rgb, k)
                self.assertEqual(len(got), len(want))
                tol = 1.0 / C.detect_scale(self.rgb.shape) + 1e-6       # one pixel of the scaled copy
                for g, w in zip(got, want):
                    for a, b in zip(g, w):
                        self.assertLessEqual(abs(a - b), tol, (k, g, w))
            h, w = self.stub.seen[0][1]
            self.assertEqual(max(h, w), C.DETECT_FIRST)                   # the detector saw the scaled copy
            self.assertEqual(self.stub.seen[0][2], 0)                     # min_size applied after mapping back

    def test_small_boxes_are_dropped_in_page_pixels_as_before(self):
        tiny = [(100.0, 100.0, 118.0, 110.0), (100.0, 300.0, 125.0, 310.0)]   # 18 px and 25 px long at the page
        stub = _StubReader({0: tiny}, {0: (self.H, self.W)})
        with _Swap(stub, 0):                                                    # unscaled: the old filter
            self.assertEqual(len(C._boxes(self.rgb, 0)), 1)

    def test_the_memo_is_keyed_by_the_scale(self):
        with _Swap(self.stub):
            C._boxes(self.rgb, 0)
            C._boxes(self.rgb, 0)
            self.assertEqual(len(self.stub.seen), 1)                      # memoised
            C._boxes(self.rgb, 0, 2000)
            self.assertEqual(len(self.stub.seen), 2)                      # another scale: detected again
            self.assertEqual(max(self.stub.seen[1][1]), 2000)
            other = self.rgb.copy()                                       # another page array: detected again
            C._boxes(other, 0)
            self.assertEqual(len(self.stub.seen), 3)

    def test_the_line_memo_is_keyed_by_the_box(self):
        calls = []

        class _Rec:
            def logp(self, gray, kind):
                calls.append(gray.shape)
                return np.zeros((4, 3), np.float32)
        with _Swap(self.stub, 1600):
            orig = C._rec
            C._rec = lambda: _Rec()
            try:
                C._box_logp(self.rgb, 0, 0, (300.0, 400.0, 2600.0, 460.0), "name")
                C._box_logp(self.rgb, 0, 0, (300.0, 400.0, 2600.0, 460.0), "name")
                C._box_logp(self.rgb, 0, 0, (300.0, 380.0, 2600.0, 470.0), "name")    # same index, other box
            finally:
                C._rec = orig
        self.assertEqual(len(calls), 2)


def _closer3_boxes(h_list):
    """closer-3's page-search boxes from the detector's horizontal list: easyocr's min_size 20 (LightReader.detect's
    default), then the 4-pixel floor, as (x0, y0, x1, y1) floats."""
    keep = [b for b in h_list if max(b[1] - b[0], b[3] - b[2]) > 20]
    return [(float(b[0]), float(b[2]), float(b[1]), float(b[3])) for b in keep if b[1] - b[0] > 4 and b[3] - b[2] > 4]


class TestScaleRule(unittest.TestCase):
    """Rev 2.7 (step 1b): a page up to DETECT_LONG pixels long is searched at its own size, its boxes bit for bit
    those of closer-3, and once only; a longer page is searched at DETECT_FIRST and, ONLY when the candidates' line
    is not found there, once more with the detector at DETECT_FALLBACK."""

    def test_the_constants(self):
        # measured on the real set (RUNNER_SPEC revision 2.7): a page up to 2048 px as closer-3; a longer page first
        # at 1600, then at 2048 only when its line is not found
        self.assertEqual((C.DETECT_LONG, C.DETECT_FIRST, C.DETECT_FALLBACK), (2048, 1600, 2048))
        self.assertEqual(C.first_long((2048, 1448, 3)), 0)
        self.assertEqual(C.first_long((2049, 1448, 3)), C.DETECT_FIRST)

    def test_a_page_up_to_detect_long_is_searched_at_its_own_size_as_closer_3(self):
        for H, W in ((2048, 1448), (1755, 1240), (1240, 1755)):
            rgb = np.full((H, W, 3), 250, np.uint8)
            lines = [(100.0, 100.0, 118.0, 110.0),          # 18 px long: min_size drops it
                     (100.0, 200.0, 400.0, 203.0),          # 3 px tall: the 4-pixel floor drops it
                     (100.0, 300.0, 104.0, 330.0),          # 4 px wide: the floor drops it
                     (120.0, 400.0, 900.0, 430.0), (950.0, 401.0, 1100.0, 431.0), (60.0, 520.0, 1000.0, 548.0)]
            stub = _StubReader({0: lines}, {0: (H, W)})
            with _Swap(stub):
                self.assertEqual(C.detect_scale(rgb.shape), 1.0)
                got = C._boxes(rgb, 0)
            self.assertEqual(stub.seen[0][1], (H, W))              # the detector got the page itself, not a copy
            raw = [[int(x0), int(x1), int(y0), int(y1)] for x0, y0, x1, y1 in lines]
            self.assertEqual(got, _closer3_boxes(raw))             # exactly closer-3's boxes
            self.assertEqual(len(got), 3)

    def test_a_longer_page_is_scaled_to_detect_long(self):
        rgb = np.full((4677, 3307, 3), 250, np.uint8)
        stub = _StubReader({0: [(300.0, 400.0, 2600.0, 460.0)]}, {0: (4677, 3307)})
        with _Swap(stub):
            C._boxes(rgb, 0)
            C._boxes(rgb, 0, C.DETECT_FALLBACK)
        self.assertEqual(max(stub.seen[0][1]), C.DETECT_FIRST)
        self.assertEqual(max(stub.seen[1][1]), C.DETECT_FALLBACK)

    def _calls(self, shape, found_at=(), fallback=None):
        calls = []

        def fake(rgb, labels, kind, cap):
            calls.append(cap)
            return [{"k": 0, "box": (0.0, 0.0, 10.0, 10.0), "cap": cap}] if cap in found_at else []
        orig = C._search
        C._search = fake
        try:
            with _Swap(None, fallback=fallback):
                hits = C._locate_all(np.zeros(shape, np.uint8), [[1, 2]], "name")
        finally:
            C._search = orig
        return calls, hits

    def test_the_fallback_runs_once_and_only_when_the_line_is_not_found_on_a_scaled_page(self):
        big, small = (4677, 3307, 3), (1755, 1240, 3)
        first, fb = C.DETECT_FIRST, C.DETECT_FALLBACK
        self.assertEqual(self._calls(big, found_at=(first,))[0], [first])         # found: no second search
        calls, hits = self._calls(big, found_at=(fb,))
        self.assertEqual(calls, [first, fb])                                      # not found: ONE more search
        self.assertEqual(hits[0]["cap"], fb)                                      # ... whose hits are used
        self.assertEqual(self._calls(big)[0], [first, fb])                        # never a third
        self.assertEqual(self._calls((2100, 1484, 3))[0], [first, fb])            # just over DETECT_LONG
        self.assertEqual(self._calls(small)[0], [0])                              # own size: closer-3, once
        self.assertEqual(self._calls((2048, 1448, 3))[0], [0])
        self.assertEqual(self._calls(big, fallback=0)[0], [first])                # no fallback set: none


# ============================================================================================ what holds memory
class TestHolders(unittest.TestCase):
    """Rev 2.7 (step 1b): what a run keeps between detections is bounded: the engine's render memo by MB, the closer's
    memos to one page, the confirmer's high-resolution images to the page looked at, and MuPDF's render store is
    emptied before each detection. Memos and caches only: no number changes."""

    def test_the_render_memo_is_capped_in_mb_and_keeps_the_newest(self):
        from collections import OrderedDict
        from src.verify.runner.model import PageRead

        class Eng:
            def __init__(self):
                self._images = OrderedDict()
                self.rendered = []

            def _path_of(self, page):
                return Path("made-up.pdf")

            def page_image(self, path, page, dpi=300):
                self.rendered.append((page, dpi))
                return np.zeros((1000, 1000, 3) if dpi < 600 else (2000, 2000, 3), np.uint8)   # 2.9 / 11.4 MB
        eng = Eng()
        pages = [PageRead(index=i, file="a.pdf", file_page=i, text="", kind="ocr", chars=0, sideways=False,
                          seconds=0.0, image_only=True, engine="cpu") for i in range(4)]
        saved = E.IMAGE_MEMO_MB
        try:
            E.IMAGE_MEMO_MB = 7
            for pg in pages[:3]:
                E.Engine.page_image_of(eng, pg, 300)
            self.assertEqual([k[1] for k in eng._images], [1, 2])               # 3 x 2.9 MB > 7 MB: the oldest out
            a = E.Engine.page_image_of(eng, pages[2], 300)
            self.assertIs(a, E.Engine.page_image_of(eng, pages[2], 300))       # memoised, the same array
            self.assertFalse(a.flags.writeable)
            E.Engine.page_image_of(eng, pages[3], 600)                          # one render over the cap: kept alone
            self.assertEqual([k[1] for k in eng._images], [3])
        finally:
            E.IMAGE_MEMO_MB = saved
        self.assertEqual(len(eng.rendered), 4)

    def test_the_render_store_is_emptied_before_every_read(self):
        calls = []

        class Inner:
            def readtext(self, image, *a, **k):
                calls.append("read")
                return []
        saved = E.free_render_store
        E.free_render_store = lambda: calls.append("free")
        try:
            r = E.CountingReader(Inner())
            r.readtext(np.zeros((8, 8, 3), np.uint8))
            r.readtext(np.zeros((8, 8, 3), np.uint8))
        finally:
            E.free_render_store = saved
        self.assertEqual(calls, ["free", "read", "free", "read"])
        E.free_render_store()                                                  # harmless whether pymupdf is loaded

    def test_the_render_store_is_given_back(self):
        if not _peak_api():
            self.skipTest("no Windows process-memory API")
        import pymupdf
        rng = np.random.default_rng(3)
        doc = pymupdf.open()
        from PIL import Image
        import io
        for _ in range(6):                                                    # six made-up "scans" (noise images)
            buf = io.BytesIO()
            Image.fromarray(rng.integers(0, 255, (1754, 1240, 3), dtype=np.uint8)).save(buf, "PNG")
            page = doc.new_page(width=595, height=842)
            page.insert_image(page.rect, stream=buf.getvalue())
        E.free_render_store()
        before = _private_mb()
        for page in doc:
            pix = page.get_pixmap(dpi=150)
            del pix
        filled = _private_mb()
        E.free_render_store()
        emptied = _private_mb()
        doc.close()
        self.assertGreater(filled - before, 15, (before, filled, emptied))  # MuPDF kept the decoded images
        self.assertLess(emptied - before, 0.4 * (filled - before) + 5, (before, filled, emptied))

    def test_the_closer_memos_hold_one_page(self):
        a = np.full((1755, 1240, 3), 250, np.uint8)
        b = np.full((1755, 1240, 3), 251, np.uint8)
        stub = _StubReader({0: [(100.0, 100.0, 900.0, 130.0)], 1: [(100.0, 100.0, 900.0, 130.0)]},
                           {0: (1755, 1240), 1: (1240, 1755)})
        with _Swap(stub):
            C._boxes(a, 0)
            C._boxes(a, 1)
            self.assertEqual({v[0] is a for v in C._DET_MEMO.values()}, {True})
            self.assertEqual(len(C._DET_MEMO), 2)
            C._boxes(b, 0)                                                     # another page: a is let go
            self.assertEqual(len(C._DET_MEMO), 1)
            self.assertIs(next(iter(C._DET_MEMO.values()))[0], b)
            self.assertIs(C._HELD[0], b)
            n = len(stub.seen)
            C._boxes(b, 0)
            self.assertEqual(len(stub.seen), n)                                # still memoised for the page in hand

    def test_the_confirmer_keeps_the_page_looked_at(self):
        from src.verify.runner import names as N
        self.assertEqual(N.HI_KEEP, 1)


def _private_mb() -> float:
    import ctypes.wintypes as wt

    class PMC(ctypes.Structure):
        _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD)] + [(n, ctypes.c_size_t) for n in (
            "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage",
            "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage",
            "PrivateUsage")]
    k32 = ctypes.WinDLL("kernel32")
    k32.GetCurrentProcess.restype = wt.HANDLE
    k32.K32GetProcessMemoryInfo.argtypes = [wt.HANDLE, ctypes.POINTER(PMC), wt.DWORD]
    c = PMC()
    c.cb = ctypes.sizeof(PMC)
    k32.K32GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(c), c.cb)
    return c.PrivateUsage / 2 ** 20


# ============================================================================================ the real models
@unittest.skipUnless(_have_models(), "the ONNX models or the Windows fonts are not here")
class TestDetectorSession(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reader = E.make_reader(2)

    def test_both_arenas_are_off(self):
        self.assertFalse(E.DET_ARENA)
        self.assertFalse(E.REC_ARENA)                                      # step 1b
        self.assertFalse(self.reader.det.get_session_options().enable_cpu_mem_arena)
        self.assertFalse(self.reader.rec.get_session_options().enable_cpu_mem_arena)
        self.assertEqual(self.reader.det.get_providers(), E.PROVIDERS)
        self.assertEqual(self.reader.rec.get_providers(), E.PROVIDERS)

    def test_recogniser_arena_off_reads_a_page_exactly_as_with_it_on(self):
        import onnxruntime as ort
        import glue_numpy
        img, _lines = a4_page(dpi=150, seed=4)
        rec_on = ort.InferenceSession(str(E.MODELS / "english_g2.onnx"), E.session_options(2, True),
                                      providers=E.PROVIDERS)
        self.assertTrue(rec_on.get_session_options().enable_cpu_mem_arena)
        other = glue_numpy.LightReader(self.reader.det, rec_on, E.GLUE_DATA)
        got, want = self.reader.readtext(img, detail=1), other.readtext(img, detail=1)
        self.assertTrue(got)
        self.assertEqual([(np.asarray(b).tolist(), t, c) for b, t, c in got],
                         [(np.asarray(b).tolist(), t, c) for b, t, c in want])

    def test_arena_off_gives_bit_identical_detector_outputs(self):
        import cv2
        import onnxruntime as ort
        img, _lines = a4_page(dpi=110)                                     # small: the arena-on copy stays small
        on = ort.InferenceSession(str(E.MODELS / "craft_mlt_25k.onnx"), E.session_options(2, True),
                                  providers=E.PROVIDERS)
        self.assertTrue(on.get_session_options().enable_cpu_mem_arena)
        import glue_numpy
        V = glue_numpy.V
        bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
        x, _r, _ = V.resize_aspect_ratio(bgr, 2560, interpolation=cv2.INTER_LINEAR, mag_ratio=1.0)
        x = np.array([np.transpose(V.normalizeMeanVariance(x), (2, 0, 1))])
        y_off = self.reader.det.run(None, {"image": x})[0]
        y_on = on.run(None, {"image": x})[0]
        self.assertTrue(np.array_equal(y_off, y_on))
        other = glue_numpy.LightReader(on, self.reader.rec, E.GLUE_DATA)
        self.assertEqual(self.reader.detect(bgr), other.detect(bgr))

    def test_the_page_search_finds_every_line_of_a_400_dpi_page(self):
        img, lines = a4_page(dpi=400)
        with _Swap(self.reader):                                           # the product's DETECT_LONG
            self.assertLess(C.detect_scale(img.shape), 0.45)
            boxes = C._boxes(img, 0)
        for (x0, y0, x1, y1), _value in lines:
            h = y1 - y0
            over = [b for b in boxes if min(b[3], y1) - max(b[1], y0) >= 0.5 * min(b[3] - b[1], h)
                    and min(b[2], x1) > max(b[0], x0)]
            self.assertTrue(over, f"no box on the line at y {y0:.0f}")
            # the joined line covers the value's ink, and its height is that of the line (not two lines)
            j = C._join_line(boxes, boxes.index(over[0]))
            self.assertLessEqual(j[0], x0 + 0.5 * h)
            self.assertGreaterEqual(j[2], x1 - 0.5 * h)
            self.assertLess(j[3] - j[1], 2.0 * h)

    def test_a_page_up_to_detect_long_gives_closer_3s_boxes_bit_for_bit(self):
        import cv2
        img, _lines = a4_page(dpi=170)                                     # 1988 pixels long
        with _Swap(self.reader):
            self.assertEqual(C.detect_scale(img.shape), 1.0)
            for k in (0, 1):
                got = C._boxes(img, k)
                h_list, _free = self.reader.detect(cv2.cvtColor(C._rot(img, k), cv2.COLOR_RGB2BGR))   # closer-3's call
                want = [(float(b[0]), float(b[2]), float(b[1]), float(b[3])) for b in h_list
                        if b[1] - b[0] > 4 and b[3] - b[2] > 4]
                self.assertEqual(got, want)
                self.assertTrue(got)

    def test_a_page_search_on_a_400_dpi_page_still_decides(self):
        img, lines = a4_page(dpi=400, seed=9)
        printed = lines[len(lines) // 2][1]
        other = printed[:-2] + ("X" if printed[-2] != "X" else "Y") + printed[-1]       # one letter changed
        with _Swap(self.reader):
            same = C.look(img, None, [printed, other], "name")
            differs = C.look(img, None, [other, printed], "name")
        self.assertEqual((same.decision, differs.decision), ("same", "differs"))
        self.assertEqual(same.detail.get("rotation"), 0)


def _peak_api() -> bool:
    if os.name != "nt":
        return False
    try:
        return bool(ctypes.WinDLL("kernel32").K32GetProcessMemoryInfo)
    except (OSError, AttributeError):
        return False


_CHILD = r"""
import os
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")       # as the runner's __main__ sets it (rev 2.7)
import ctypes, ctypes.wintypes as wt, json, sys
class PMC(ctypes.Structure):
    _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t), ("PrivateUsage", ctypes.c_size_t)]
k32 = ctypes.WinDLL("kernel32")
k32.GetCurrentProcess.restype = wt.HANDLE
k32.K32GetProcessMemoryInfo.argtypes = [wt.HANDLE, ctypes.POINTER(PMC), wt.DWORD]
def mem():
    c = PMC(); c.cb = ctypes.sizeof(PMC)
    k32.K32GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(c), c.cb)
    return c.PrivateUsage / 2 ** 20, c.PeakPagefileUsage / 2 ** 20
from src.verify.runner import closer as C, engine as E
from src.verify.runner.tests.test_memory27 import a4_page
C.bind(type("Eng", (), {"reader": E.make_reader(2)})())
img, lines = a4_page(dpi=400, seed=11)
ref = lines[3][1]
cands = [ref, ref[:-1] + ("Q" if ref[-1] != "Q" else "Z")]
if sys.argv[1] == "fallback":
    cands = ["QUORVAX ZENTHIL", "QUORVAX ZENTHIM"]           # printed nowhere: searched at 1600, then at 2048
before = mem()
res = C.look(img, None, cands, "name")
after = mem()
print(json.dumps({"before": before[0], "after": after[0], "peak": after[1], "decision": res.decision,
                  "detect_long": res.detail.get("detect_long"), "note": res.note[:40]}))
"""


@unittest.skipUnless(_have_models() and _peak_api(), "no models, or no Windows process-memory API")
class TestPageSearchMemory(unittest.TestCase):
    def _child(self, mode):
        env = {k: v for k, v in os.environ.items() if k != "OPENBLAS_NUM_THREADS"}
        r = subprocess.run([sys.executable, "-I", "-B", "-c", _CHILD, mode], cwd=str(ROOT), capture_output=True,
                           text=True, timeout=900, env=env)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        return json.loads(r.stdout.strip().splitlines()[-1])

    def test_one_page_search_stays_under_the_limit_and_gives_the_memory_back(self):
        m = self._child("found")
        self.assertEqual(m["detect_long"], 1600, m)                 # found at DETECT_FIRST: no second search
        self.assertLess(m["peak"], PAGE_SEARCH_PEAK_MB, m)
        self.assertLess(m["after"] - m["before"], 400, m)          # the detector's memory went back

    def test_a_search_with_the_fallback_stays_under_the_limit(self):
        m = self._child("fallback")
        self.assertTrue(m["note"].startswith("the text was not found"), m)    # 1600 and 2048 both searched
        self.assertLess(m["peak"], PAGE_SEARCH_FALLBACK_PEAK_MB, m)
        self.assertLess(m["after"] - m["before"], 400, m)


class TestBlasThreads(unittest.TestCase):
    """Rev 2.7: the runner sets OPENBLAS_NUM_THREADS=1 before numpy loads (numpy's OpenBLAS committed about 30 MB per
    thread at import, 363 MB on the owner's 12-thread PC); the runner's BLAS products are bit-identical with it."""

    _PRODUCTS = r"""
import hashlib, os, sys
import numpy as np
rng = np.random.default_rng(7)
h = hashlib.sha1()
for T in (8, 64, 333, 1500, 6000):                       # frames x classes @ classes x folded (closer._Rec.logp)
    for C, K in ((97, 38), (97, 40), (97, 12)):
        p = rng.random((T, C), dtype=np.float32)
        p /= p.sum(1, keepdims=True)
        m = (rng.random((C, K)) < 0.3).astype(np.float32)
        h.update(np.log(np.maximum(p @ m, 1e-12)).tobytes())
for n in (2, 3, 40):                                      # colour and box geometry norms
    h.update(np.float64(np.linalg.norm(rng.random(n))).tobytes())
M = rng.random((3, 3))
h.update(np.linalg.inv(M).tobytes() + np.matmul(M, (1.5, 2.5, 1)).tobytes())
print(h.hexdigest())
"""

    def _run(self, code, threads):
        env = {k: v for k, v in os.environ.items() if k != "OPENBLAS_NUM_THREADS"}
        if threads:
            env["OPENBLAS_NUM_THREADS"] = threads
        r = subprocess.run([sys.executable, "-I", "-B", "-c", code], cwd=str(ROOT), capture_output=True, text=True,
                           timeout=300, env=env)
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])
        return r.stdout.strip().splitlines()[-1]

    def test_the_runners_blas_products_do_not_depend_on_the_thread_count(self):
        self.assertEqual(self._run(self._PRODUCTS, ""), self._run(self._PRODUCTS, "1"))

    def test_the_runner_sets_one_blas_thread_before_numpy_loads(self):
        code = "; ".join(["import os, sys", "import src.verify.runner.__main__",
                          "assert 'numpy' not in sys.modules",                # numpy loads later, with the setting
                          "import numpy", "print(os.environ.get('OPENBLAS_NUM_THREADS'))"])
        self.assertEqual(self._run(code, ""), "1")
        self.assertEqual(self._run(code, "3"), "3")                          # an explicit setting is kept


if __name__ == "__main__":
    unittest.main()
