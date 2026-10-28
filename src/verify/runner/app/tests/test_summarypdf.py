"""Task W: app\\summarypdf.py (APP_SPEC rev 1 section 3.2; rev 2: "GPU" in the footer's version).

Real PyMuPDF (in memory, plus files written only under private-runner\\selftest\\app\\W, removed afterwards).
Made-up RunResult fixtures from test_checkfacts. No card, no torch."""
from __future__ import annotations

import os
import shutil
import unittest
from pathlib import Path

import pymupdf

from src.verify.runner.app import summarypdf as SP
from src.verify.runner.app.tests.test_checkfacts import TEST_ROOT, make_result
from src.verify.runner.model import FAIL, FLAG, MISSING, PASS, STOP

os.environ.setdefault("HDC_TEST_NO_CARD", "1")


def pdf_texts(data: bytes):
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        return [p.get_text() for p in doc], [tuple(p.rect)[2:] for p in doc]


class TestHtml(unittest.TestCase):
    def test_escaped_and_coloured(self):
        h = SP.line_html(FAIL, "ACADEMIC HSC (p3-4): FAIL — <b>&amp; FAIL again")
        self.assertIn("&lt;b&gt;&amp;amp;", h)
        self.assertEqual(h.count("<b style="), 1)                 # only the first FAIL
        self.assertIn(f'<b style="color:{SP.COLOURS[FAIL]}">FAIL</b>', h)

    def test_label_not_a_verdict_is_plain(self):
        self.assertEqual(SP.line_html("", "READING: x"), "<p>READING: x</p>")
        self.assertEqual(SP.coloured("HINT", "HINTED words"), "HINTED words")   # whole words only

    def test_multiword_label(self):
        h = SP.coloured("NOT JUDGED", "BANK (p5): NOT JUDGED")
        self.assertIn(">NOT JUDGED</b>", h)

    def test_version_text_says_gpu(self):
        self.assertEqual(SP.version_text("1.0.0", "20261029.1-abc1234", "0.2.7", "a", "b"),
                         "App 1.0.0 GPU, build 20261029.1-abc1234 \u00b7 rules bot a + runner b \u00b7 runner 0.2.7")
        self.assertIn("GPU (CUDA 12.8)", SP.version_text("1.0.0", flavour="GPU (CUDA 12.8)"))
        self.assertIn("GPU", SP.version_text("1.0.0", flavour="CUDA 12.8"))
        self.assertIn("GPU", SP.version_text("", flavour=""))


class TestPlan(unittest.TestCase):
    def test_greedy_pages(self):
        cap = {True: 2, False: 3}                       # the head takes one line's room on page 1

        def fits(a, b, first):
            return b - a <= cap[first]
        self.assertEqual(SP.plan_pages(8, fits), [(0, 2, False), (2, 5, False), (5, 8, False)])

    def test_oversize_line_is_shrunk_alone(self):
        def fits(a, b, first):
            return not (a <= 1 < b) and b - a <= 3       # line 1 never fits
        self.assertEqual(SP.plan_pages(4, fits), [(0, 1, False), (1, 2, True), (2, 4, False)])

    def test_no_lines(self):
        self.assertEqual(SP.plan_pages(0, lambda a, b, f: True), [(0, 0, False)])


class TestRender(unittest.TestCase):
    def test_one_page(self):
        lines = [(FAIL, "CASE | KLP | bundle | 5 pages | RESULT: FAIL"), ("", "READING: x"),
                 (PASS, "PASSPORT (p1): PASS"), (MISSING, "PHOTO MISSING"),
                 (FLAG, "NAMES: FLAG — dash \u2014 ellipsis \u2026 bengali \u0995 <b>&")]
        data = SP.render(lines, code="ABCD-1234", check_date="2026-10-28", program="KLP", overall=FAIL,
                         version=SP.version_text("1.0.0", "dev", "0.2.7", "a", "b"))
        self.assertTrue(data.startswith(b"%PDF"))
        texts, sizes = pdf_texts(data)
        self.assertEqual(len(texts), 1)
        self.assertEqual(sizes[0], SP.A4)
        t = texts[0]
        for s in ("ABCD-1234", "2026-10-28", "KLP", "App 1.0.0 GPU", "page 1 of 1", "PHOTO MISSING", "<b>&",
                  "\u2014", "\u2026", "\u0995", SP.DOC_TITLE):
            self.assertIn(s, t)

    def test_many_lines_break_into_pages(self):
        lines = [(PASS, f"LINE-{k:03d} (p{k}): PASS — a made-up reason that is long enough to wrap over the line "
                        f"width of an A4 page at nine and a half points") for k in range(150)]
        data = SP.render(lines, code="ABCD-1234", check_date="2026-10-28", program="EAP", overall=PASS,
                         version="App 1.0.0 GPU")
        texts, _ = pdf_texts(data)
        n = len(texts)
        self.assertGreaterEqual(n, 3)
        joined = "\n".join(texts)
        for k in range(150):
            self.assertEqual(joined.count(f"LINE-{k:03d} "), 1, k)        # every line once, none cut off
        for i, t in enumerate(texts, 1):
            self.assertIn(f"page {i} of {n}", t)
            self.assertIn("ABCD-1234", t)
            self.assertIn("App 1.0.0 GPU", t)

    def test_stop_overall_head(self):
        data = SP.render([(STOP, "STOP: made-up (pages 1-2)")], code="0000-0000", check_date="2026-10-28",
                         program="MASTER", overall=STOP, version="App 1.0.0 GPU")
        texts, _ = pdf_texts(data)
        self.assertIn("Result STOP", texts[0])


class TestWrite(unittest.TestCase):
    def setUp(self):
        self.dir = TEST_ROOT / "summarypdf"
        shutil.rmtree(self.dir, ignore_errors=True)
        self.dir.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_write_from_run_result(self):
        seen = []
        r = make_result()
        p = SP.write(r, self.dir / "summary.pdf", code="ABCD-1234", app_version="1.0.0", build_id="dev",
                     flavour="GPU (CUDA 12.8)", assert_writable=seen.append)
        self.assertEqual(seen, [p])
        data = p.read_bytes()
        texts, _ = pdf_texts(data)
        t = "\n".join(texts)
        self.assertIn("ABCD-1234", t)
        self.assertIn("App 1.0.0 GPU (CUDA 12.8), build dev", t)
        self.assertIn("ACADEMIC HSC", t)
        self.assertIn("PHOTO MISSING", t)
        self.assertNotIn("C:\\", t)                    # the summary lines are taken without the out folder
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            meta = doc.metadata
        self.assertEqual(meta.get("title"), "Hangeul Document Checker summary")
        self.assertNotIn("SAMPLE", " ".join(str(v) for v in meta.values()))

    def test_folder_mode(self):
        p = SP.write(make_result("folder"), self.dir / "summary.pdf", code="ABCD-1234", assert_writable=lambda x: None)
        self.assertTrue(p.read_bytes().startswith(b"%PDF"))

    def test_refused_write_writes_nothing(self):
        def refuse(p):
            raise PermissionError("refused")
        with self.assertRaises(PermissionError):
            SP.write(make_result(), self.dir / "summary.pdf", code="ABCD-1234", assert_writable=refuse)
        self.assertFalse((self.dir / "summary.pdf").exists())

    def test_summary_pairs_name_no_path(self):
        pairs = SP.summary_pairs(make_result())
        self.assertTrue(all(isinstance(a, str) and isinstance(b, str) for a, b in pairs))
        self.assertFalse(any("C:\\" in b for _, b in pairs))
        self.assertIn("Report: report.xlsx", pairs[-1][1])


if __name__ == "__main__":
    unittest.main()
