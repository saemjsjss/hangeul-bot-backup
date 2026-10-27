"""Task 1: engine.py (RUNNER_SPEC 5.2, 5.2.1 and Task 1 acceptance) on a SYNTHETIC bundle (made-up text
only; the generator is ported from the engine study's smoke_test.py):
  p1 typed solvency letter (text layer), p2 scanned statement with a violet stamp, p3 the next statement
  page scanned SIDEWAYS, p4 scanned trade licence with a QR code, p5 a small photo on a blank page.

It runs the real light engine (ONNX on the CPU, about a minute for the first read). Scratch files go to
C:\\Hangeul\\DOCCHECK\\lab\\private-runner\\tmp\\test_engine-<pid>-<n>; the case caches it makes under
runner-data\\verification are removed at the end.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import unittest
import uuid
from pathlib import Path

from src.verify.runner import botimport, engine, ingest
from src.verify.runner.model import PAGE_TEXTS_SCHEMA

SCRATCH = botimport.PRIVATE / "tmp"


def _rmtree(p: Path) -> None:
    """Remove a scratch folder under private-runner; retried, because a virus scanner may hold a file
    that was just written for a moment."""
    import time
    for _ in range(6):
        shutil.rmtree(p, ignore_errors=True)
        if not Path(p).exists():
            return
        time.sleep(0.5)

SOLVENCY = [(16, "SAMPLE BANK PLC"), (11, "Example Branch, Sample City"),
            (11, "Ref: SB/EX/2026/0001                                   Date: 20 October 2026"),
            (15, "SOLVENCY CERTIFICATE"), (11, "TO WHOM IT MAY CONCERN"),
            (11, "This is to certify that SAMPLE FATHER ONE has been maintaining a Savings"),
            (11, "Account No. 0000-1111-2222 with us. The balance in the account as on"),
            (11, "20 October 2026 is Tk 2,500,000.00 (Taka Twenty Five Lac only),"),
            (11, "equivalent to USD 20,500.00 at the rate of BDT 122 per USD."),
            (11, "We consider the account holder financially sound."),
            (11, "Branch Manager (Authorized Signature)")]
STATEMENT1 = [(15, "SAMPLE BANK PLC   STATEMENT OF ACCOUNT"), (11, "Account Name: SAMPLE FATHER ONE"),
              (11, "Account No: 0000-1111-2222    Branch: Example Branch"),
              (11, "Statement Period: 01 May 2026 to 20 October 2026"),
              (11, "Date          Description              Debit         Credit          Balance"),
              (11, "01/05/2026    Opening Balance                                          2,300,000.00"),
              (11, "15/05/2026    Cash Deposit                             50,000.00       2,350,000.00"),
              (11, "02/06/2026    Transfer Out             20,000.00                      2,330,000.00"),
              (11, "18/06/2026    Cash Deposit                             60,000.00       2,390,000.00"),
              (11, "05/07/2026    Interest                                  9,000.00       2,399,000.00"),
              (11, "21/07/2026    Cash Deposit                             40,000.00       2,439,000.00")]
STATEMENT2 = [(11, "Date          Description              Debit         Credit          Balance"),
              (11, "10/08/2026    Cash Deposit                             30,000.00       2,469,000.00"),
              (11, "28/08/2026    Utility Payment           4,000.00                       2,465,000.00"),
              (11, "14/09/2026    Cash Deposit                             25,000.00       2,490,000.00"),
              (11, "30/09/2026    Interest                                 10,000.00       2,500,000.00"),
              (11, "20/10/2026    Closing Balance                                          2,500,000.00"),
              (11, "This is a computer generated statement. Branch Manager, Example Branch")]
TRADE = [(16, "SAMPLE CITY CORPORATION"), (15, "TRADE LICENSE"), (11, "License No: TL-0000-0001"),
         (11, "Name of Business: SAMPLE TRADERS"), (11, "Name of Proprietor: SAMPLE FATHER ONE"),
         (11, "Nature of Business: General Trading"), (11, "Address: 1 Example Road, Sample City"),
         (11, "Fiscal Year: 2026-2027        Valid up to: 30 June 2027"),
         (11, "Licence Fee paid: Tk 2,000.00        Issued: 01 July 2026"),
         (11, "Licensing Officer, Sample City Corporation")]


def scan_image(lines, stamp=None, qr=None):
    """A page of made-up text rendered at 150 dpi, as a scanner would give it (BGR, then JPEG)."""
    import cv2
    import numpy as np
    import pymupdf
    d = pymupdf.open()
    p = d.new_page(width=595, height=842)
    y = 80
    for size, text in lines:
        p.insert_text((50, y), text, fontsize=size, fontname="helv")
        y += size * 2.3
    pix = p.get_pixmap(dpi=150)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3]
    img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR).copy()
    d.close()
    if stamp:
        cv2.circle(img, (img.shape[1] - 260, img.shape[0] - 300), 120, stamp, 10)
    if qr:
        q = cv2.QRCodeEncoder.create().encode(qr)
        q = cv2.resize(q, (q.shape[1] * 6, q.shape[0] * 6), interpolation=cv2.INTER_NEAREST)
        q = cv2.copyMakeBorder(q, 24, 24, 24, 24, cv2.BORDER_CONSTANT, value=255)
        h, w = q.shape
        img[-h - 120:-120, -w - 100:-100] = cv2.cvtColor(q, cv2.COLOR_GRAY2BGR)
    return img


def jpeg(img, q=85) -> bytes:
    import cv2
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, q])
    return buf.tobytes()


def build_bundle(path: Path) -> bytes:
    """The 5-page synthetic bundle; returns the photo's JPEG bytes."""
    import cv2
    import numpy as np
    import pymupdf
    doc = pymupdf.open()
    p = doc.new_page(width=595, height=842)                      # 1: typed, text layer
    y = 80
    for size, text in SOLVENCY:
        p.insert_text((50, y), text, fontsize=size, fontname="helv")
        y += size * 2.4
    p = doc.new_page(width=595, height=842)                      # 2: scan + violet stamp
    p.insert_image(p.rect, stream=jpeg(scan_image(STATEMENT1, stamp=(160, 0, 128))))
    side = cv2.rotate(scan_image(STATEMENT2), cv2.ROTATE_90_CLOCKWISE)
    p = doc.new_page(width=842, height=595)                      # 3: scanned sideways
    p.insert_image(p.rect, stream=jpeg(side))
    p = doc.new_page(width=595, height=842)                      # 4: scan + QR + red seal
    p.insert_image(p.rect, stream=jpeg(scan_image(TRADE, stamp=(0, 0, 200),
                                                  qr="https://example.test/trade-licence/verify?no=TL-0000-0001")))
    photo = np.full((531, 413, 3), 255, np.uint8)               # 5: a photo (3.5 x 4.5 cm at 300 dpi)
    cv2.ellipse(photo, (206, 220), (110, 140), 0, 0, 360, (90, 110, 140), -1)
    cv2.rectangle(photo, (60, 400), (352, 531), (60, 60, 60), -1)
    photo_jpg = jpeg(photo, 92)
    p = doc.new_page(width=595, height=842)
    p.insert_image(pymupdf.Rect(80, 80, 80 + 99.2, 80 + 127.6), stream=photo_jpg)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path), garbage=3)
    doc.close()
    return photo_jpg


def render_hashes(pdf: Path, dpi: int):
    import pymupdf
    with pymupdf.open(str(pdf)) as d:
        return [hashlib.sha1(p.get_pixmap(dpi=dpi).samples).hexdigest() for p in d]


def page_layers(pdf: Path):
    import pymupdf
    with pymupdf.open(str(pdf)) as d:
        return [p.get_text() for p in d]


class EngineSynthetic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tag = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
        cls.base = SCRATCH / f"test_engine-{tag}"
        cls.bundle = cls.base / "input" / "BUNDLE.pdf"
        cls.photo_jpg = build_bundle(cls.bundle)
        cls.src = ingest.open_input(cls.bundle)
        cls.eng = engine.Engine(botimport.DEFAULT_CACHE_ROOT)
        cls.cases = []
        cls.cid = cls.new_case("A")
        c0 = cls.eng.reader.calls
        with cls.eng.case(cls.cid):
            cls.pages = cls.eng.read_source(cls.src, {})
        cls.first_calls = cls.eng.reader.calls - c0

    @classmethod
    def new_case(cls, suffix: str) -> str:
        cid = f"TEST-ENGINE-{os.getpid()}-{uuid.uuid4().hex[:6]}-{suffix}"
        cls.cases.append(cid)
        return cid

    @classmethod
    def tearDownClass(cls):
        root = cls.eng.bot.cache_root / "verification"
        for cid in cls.cases:
            for sub in ("text", "images"):
                f = root / sub / f"{cid}.json"
                if f.exists():
                    f.unlink()
        _rmtree(cls.base)

    # ------------------------------------------------------------------ reading
    def test_pages(self):
        p = self.pages
        self.assertEqual(len(p), 5)
        self.assertEqual([x.index for x in p], [0, 1, 2, 3, 4])
        self.assertEqual([x.file_page for x in p], [0, 1, 2, 3, 4])
        self.assertEqual({x.file for x in p}, {"BUNDLE.pdf"})
        self.assertEqual([x.kind for x in p], ["layer", "ocr", "ocr", "ocr", "ocr"])
        self.assertEqual([x.engine for x in p], ["layer", "cpu", "cpu", "cpu", "cpu"])
        self.assertEqual([x.image_only for x in p], [False, True, True, True, False])
        self.assertIn("SOLVENCY CERTIFICATE", p[0].text)
        self.assertIn("STATEMENT", p[1].text.upper())
        self.assertTrue(p[2].sideways)
        self.assertEqual([x.chars for x in p], [len(x.text) for x in p])
        self.assertEqual(p[0].seconds, 0.0)
        self.assertTrue(all(x.seconds > 0 for x in p[1:]))
        self.assertGreater(self.first_calls, 0)
        self.assertGreaterEqual(self.eng.stats()["pages_by_engine"]["cpu"], 4)

    def test_second_run_same_case_zero_ocr(self):
        m, c0 = self.eng.log.mark(), self.eng.reader.calls
        with self.eng.case(self.cid):               # the cache was saved when the first case closed
            again = self.eng.read_source(self.src, {})
        self.assertEqual(self.eng.reader.calls - c0, 0)
        self.assertEqual(self.eng.log.since(m), [])
        self.assertEqual([x.text for x in again], [x.text for x in self.pages])
        self.assertEqual([x.sideways for x in again], [x.sideways for x in self.pages])

    def test_cpu_only(self):
        inner = self.eng.reader.inner
        self.assertEqual(inner.det.get_providers(), ["CPUExecutionProvider"])
        self.assertEqual(inner.rec.get_providers(), ["CPUExecutionProvider"])
        self.assertEqual(engine.heavy_modules(), [])
        self.assertIs(self.eng.bot.dv._reader, self.eng.reader)
        self.assertTrue(Path(self.eng.bot.dv.__file__).resolve().is_relative_to(botimport.WORKTREE / "src"))

    # ------------------------------------------------------------------ cutting and priming
    PARTS = [("BANK STATEMENT p01-03.pdf", 0, 2), ("TRADE LICENSE p04.pdf", 3, 3), ("PASSPORT SIZE PHOTO p05.jpg", 4, 4)]

    def test_cut_keeps_pixels_layer_and_mtime(self):
        out = self.base / "cut"
        files = self.eng.cut(self.bundle, self.PARTS, out)
        self.assertEqual([f.name for f in files], [n for n, _a, _b in self.PARTS])
        bmt = int(self.bundle.stat().st_mtime)
        self.assertTrue(all(int(f.stat().st_mtime) == bmt for f in files))
        for dpi in (150, 200):
            self.assertEqual(render_hashes(self.bundle, dpi)[:4],
                             render_hashes(files[0], dpi) + render_hashes(files[1], dpi))
        self.assertEqual(page_layers(self.bundle)[:4], page_layers(files[0]) + page_layers(files[1]))
        self.assertEqual(files[2].read_bytes(), self.photo_jpg)
        # cut again: the same size and mtime (the text-cache key); the bytes differ at most in the trailer /ID
        import re
        no_id = lambda b: re.sub(rb"/ID\s*\[<[0-9A-Fa-f]*>\s*<[0-9A-Fa-f]*>\]", b"", b)
        first = {f.name: f.read_bytes() for f in files}
        self.eng.cut(self.bundle, self.PARTS, out)
        for f in files:
            with self.subTest(f=f.name):
                self.assertEqual(len(first[f.name]), f.stat().st_size)
                self.assertEqual(no_id(first[f.name]), no_id(f.read_bytes()))
                self.assertEqual(int(f.stat().st_mtime), bmt)

    def test_cut_refuses_outside_private(self):
        from src.verify.runner import safety
        with self.assertRaises(safety.SafetyError):
            self.eng.cut(self.bundle, self.PARTS, Path(r"C:\Users\Public\runner-cut-probe-never-created"))
        self.assertFalse(Path(r"C:\Users\Public\runner-cut-probe-never-created").exists())
        with self.assertRaises(ValueError):
            self.eng.cut(self.bundle, [("..\\escape.pdf", 0, 0)], self.base / "cut-bad")

    def test_prime_zero_ocr(self):
        out = self.base / "cut-prime"
        files = self.eng.cut(self.bundle, self.PARTS, out)
        texts = [p.text for p in self.pages]
        cid = self.new_case("P")
        dv = self.eng.bot.dv
        with self.eng.case(cid):
            for f, (_n, a, b) in zip(files, self.PARTS):
                self.eng.prime(f, texts[a:b + 1])
            m, c0 = self.eng.log.mark(), self.eng.reader.calls
            got = [dv.read_document(f)[0] for f in files]
            pages = dv.read_pages(files[0])
        self.assertEqual(self.eng.reader.calls - c0, 0)
        self.assertEqual(self.eng.log.since(m), [])
        self.assertEqual(got, ["\n".join(texts[a:b + 1]) for _n, a, b in self.PARTS])
        self.assertEqual(pages, texts[0:3])

    def test_prime_needs_a_case(self):
        with self.assertRaises(RuntimeError):
            self.eng.prime(self.bundle, ["x"])

    def test_cut_page(self):
        out = self.base / "pages"
        one = self.eng.cut_page(self.pages[1], out)
        self.assertEqual(one.name, "BUNDLE p02.pdf")
        self.assertEqual(render_hashes(one, 200), [render_hashes(self.bundle, 200)[1]])
        self.assertEqual(int(one.stat().st_mtime), int(self.bundle.stat().st_mtime))

    # ------------------------------------------------------------------ pictures
    def test_page_image_of(self):
        img = self.eng.page_image_of(self.pages[0], 72)
        self.assertEqual(img.shape, (842, 595, 3))
        self.assertEqual(str(img.dtype), "uint8")
        self.assertIs(self.eng.page_image_of(self.pages[0], 72), img)
        self.assertFalse(img.flags.writeable)
        side = self.eng.page_image_of(self.pages[2], 72)
        self.assertEqual(side.shape, (595, 842, 3))

    def test_read_boxes_and_text(self):
        img = self.eng.page_image_of(self.pages[0], 150)[:400].copy()
        boxes = self.eng.read_boxes(img)
        self.assertTrue(boxes)
        for box, text, conf in boxes:
            self.assertEqual(len(box), 4)
            self.assertTrue(all(isinstance(v, int) for pt in box for v in pt))
            self.assertIsInstance(text, str)
            self.assertIsInstance(conf, float)
        self.assertIn("SAMPLE", " ".join(t for _b, t, _c in boxes).upper())
        self.assertIn("SAMPLE", self.eng.read_text(img).upper())

    def test_set_today(self):
        import datetime as dt
        old = self.eng.bot.dv.TODAY
        try:
            self.eng.set_today(dt.date(2026, 10, 27))
            self.assertEqual(self.eng.bot.dv.TODAY, dt.date(2026, 10, 27))
        finally:
            self.eng.bot.dv.TODAY = old

    def test_case_id_checked(self):
        with self.assertRaises(ValueError):
            with self.eng.case("..\\escape"):
                pass

    # ------------------------------------------------------------------ page texts (5.2.1)
    def _page_texts(self, name: str, pages, sha=None, n_pages=None, schema=PAGE_TEXTS_SCHEMA,
                    file_name="BUNDLE.pdf") -> Path:
        f = self.src.files[0]
        data = {"schema": schema, "complete": len(pages) == f.n_pages, "engine": "made-up GPU read",
                "mode": "bundle", "case_id": self.src.case_id,
                "files": [{"name": file_name, "sha256": sha or ingest.file_sha256(f.path), "size": f.size,
                           "n_pages": f.n_pages if n_pages is None else n_pages,
                           "pages": [{"page": i + 1, "text": t, "kind": k, "sideways": False, "seconds": 0.5}
                                     for i, t, k in pages]}],
                "slots": [], "seconds_total": 0.0}
        p = self.base / "texts" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data), encoding="utf-8")
        return p

    def test_page_texts_complete_zero_ocr(self):
        kinds = [x.kind for x in self.pages]
        rows = [(i, self.pages[i].text if kinds[i] == "layer" else f"PAGE {i + 1} made-up", kinds[i])
                for i in range(5)]
        given = engine.load_page_texts(self._page_texts("complete.json", rows), self.src)
        self.assertTrue(given.complete)
        self.assertEqual(len(given), 5)
        cid = self.new_case("G")
        m, c0 = self.eng.log.mark(), self.eng.reader.calls
        with self.eng.case(cid):
            got = self.eng.read_source(self.src, {}, given)
        self.assertEqual(self.eng.reader.calls - c0, 0)
        self.assertEqual(self.eng.log.since(m), [])
        self.assertEqual([x.engine for x in got], ["layer", "gpu", "gpu", "gpu", "gpu"])
        self.assertEqual([x.text for x in got], [t for _i, t, _k in rows])
        self.assertEqual([x.seconds for x in got], [0.5] * 5)
        self.assertEqual([x.image_only for x in got], [x.image_only for x in self.pages])

    def test_page_texts_partial_reads_only_missing(self):
        rows = [(0, self.pages[0].text, "layer"), (1, "PAGE 2 made-up", "ocr"), (2, "PAGE 3 made-up", "ocr")]
        given = engine.load_page_texts(self._page_texts("partial.json", rows), self.src)
        self.assertFalse(given.complete)
        cid = self.new_case("H")
        m = self.eng.log.mark()
        with self.eng.case(cid):
            got = self.eng.read_source(self.src, {}, given)
            calls = self.eng.log.since(m)
            m2 = self.eng.log.mark()
            again = self.eng.read_source(self.src, {}, given)        # the missing pages are in the case cache now
            calls2 = self.eng.log.since(m2)
        self.assertEqual(len(calls), 2)                               # pages 4 and 5 only
        self.assertEqual(calls2, [])
        self.assertEqual([x.engine for x in got], ["layer", "gpu", "gpu", "cpu", "cpu"])
        self.assertEqual([x.text for x in got[:3]], [t for _i, t, _k in rows])
        self.assertEqual([x.text for x in got[3:]], [x.text for x in self.pages[3:]])
        self.assertEqual([x.sideways for x in got[3:]], [x.sideways for x in self.pages[3:]])
        self.assertEqual([x.text for x in again], [x.text for x in got])

    def test_page_texts_refused(self):
        rows = [(0, "x", "layer")]
        cases = {
            "sha": self._page_texts("sha.json", rows, sha="0" * 64),
            "pages": self._page_texts("pages.json", rows, n_pages=6),
            "schema": self._page_texts("schema.json", rows, schema="runner-page-texts/0"),
            "name": self._page_texts("name.json", rows, file_name="OTHER.pdf"),
            "page_no": self._page_texts("page_no.json", [(7, "x", "ocr")]),
            "outside": Path(r"C:\Users\Public\page_texts_never_written.json"),
            "relative": Path("runs\\page_texts.json"),
            "missing": self.base / "texts" / "missing.json",
        }
        for k, p in cases.items():
            with self.subTest(k=k):
                with self.assertRaises(ingest.InputError):
                    engine.load_page_texts(p, self.src)


if __name__ == "__main__":
    unittest.main()
