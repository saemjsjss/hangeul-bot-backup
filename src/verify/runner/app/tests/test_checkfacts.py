"""Task W: app\\checkfacts.py (APP_SPEC rev 1 section 3.2, rev 2 R2.1.1) on made-up RunResult fixtures.

Every free-text field of the fixtures (messages, page texts, file names, the input path, the page-texts path)
carries a made-up marker; checkfacts.json must hold none of them, no path and no message. Made-up data only;
files are written only under private-runner\\selftest\\app\\W and removed afterwards. No card, no torch."""
from __future__ import annotations

import json
import os
import shutil
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.verify.runner.botimport import PRIVATE
from src.verify.runner.model import (BASE_RULES_VERSION, ERROR, FAIL, FLAG, MISSING, NOTE, ORDER, PASS, PRESENT,
                                     RUN_SCHEMA, RUNNER_RULES_VERSION, RUNNER_VERSION, STOP, DocResult, DocUnit,
                                     Finding, Notice, PageRead, RunResult, SplitResult)
from src.verify.runner.app import checkfacts as CF

os.environ.setdefault("HDC_TEST_NO_CARD", "1")
TEST_ROOT = PRIVATE / "selftest" / "app" / "W"

# made-up markers: none of these may reach checkfacts.json
NAME = "SAMPLE PERSON ONE"
OTHER = "SAMPLE PERSON TWO"
NUMBER = "000123"
MARKERS = (NAME, OTHER, "SAMPLE FATHER", NUMBER, "SAMPLE PAGE TEXT", "Users", "BUNDLE.pdf", "FILE.pdf",
           "PASSPORT.pdf", "FINANCIAL.pdf", "NOTES.docx", "x.json", "C:\\", "C:/", "check by eye", "out of order",
           "not decoded")


def F(v, rule, msg, pages=()):
    return Finding(verdict=v, rule=rule, message=msg, source="fake", pages=tuple(pages))


def _page(i, file, fp, engine, seconds, kind="ocr"):
    return PageRead(index=i, file=file, file_page=fp, text=f"SAMPLE PAGE TEXT {NAME} {NUMBER}", kind=kind,
                    chars=40, sideways=False, seconds=seconds, image_only=False, engine=engine)


def make_result(mode: str = "bundle", overall: str = FAIL, n_extra_docs: int = 0) -> RunResult:
    """A made-up RunResult. bundle: 5 pages of one file; folder: two files (1 + 4 pages)."""
    if mode == "folder":
        f1, f2 = f"{NAME} PASSPORT.pdf", f"{NAME} FINANCIAL.pdf"
        pages = [_page(0, f1, 0, "gpu", 1.2), _page(1, f2, 0, "gpu", 0.8), _page(2, f2, 1, "layer", 0.0, "layer"),
                 _page(3, f2, 2, "gpu", 1.6), _page(4, f2, 3, "cpu", 4.0)]
        src = {0: f1, 1: f2, 2: f2, 3: f2, 4: f2}
    else:
        f1 = f"{NAME} BUNDLE.pdf"
        pages = [_page(0, f1, 0, "gpu", 1.2), _page(1, f1, 1, "gpu", 0.8), _page(2, f1, 2, "layer", 0.0, "layer"),
                 _page(3, f1, 3, "gpu", 1.6), _page(4, f1, 4, "cpu", 4.0)]
        src = {}

    def unit(uid, key, title, ps, part=""):
        return DocUnit(uid=uid, key=key, fine="", title=title, pages=list(ps),
                       source_file=src.get(ps[0], "") if mode == "folder" else "", part=part)

    u1 = unit("D01", "passport", "PASSPORT", [0])
    u2 = unit("D02", "photo", "PHOTO", [1], part="p1" if mode == "folder" else "")
    u3 = unit("D03", "academic", "ACADEMIC HSC", [2, 3], part="p2-3" if mode == "folder" else "")
    u4 = unit("D04", "bank", "BANK SOLVENCY / STATEMENT", [4], part="p4" if mode == "folder" else "")
    d1 = DocResult(unit=u1, verdict=PASS, reasons=[],
                   findings=[F(PASS, "passport.mrz", f"MRZ of {NAME} read", [1])])
    d2 = DocResult(unit=u2, verdict=PASS, reasons=[], findings=[])
    fs3 = [F(FAIL, "academic.apostille", f"no e-Apostille for {NAME}", [3, 4]),
           F(FLAG, "academic.board_qr", f"QR {NUMBER} not decoded", [4]),
           F(FLAG, "academic.board_qr", f"QR {NUMBER} not decoded", [4]),        # a repeated reason: kept once
           F(NOTE, "academic.note", f"note about {NAME}", [3])]
    d3 = DocResult(unit=u3, verdict=FAIL, reasons=[f.message for f in fs3[:2]], findings=fs3)
    fs4 = [F(ERROR, "bank.balance", f"could not be checked: {NUMBER}", [5])]
    d4 = DocResult(unit=u4, verdict=FLAG, reasons=[fs4[0].message], findings=fs4)
    docs = [d1, d2, d3, d4]
    for k in range(n_extra_docs):                       # many lines for the PDF's page breaks
        u = unit(f"X{k:03d}", "unrecognised", f"EXTRA LINE {k:03d}", [4])
        docs.append(DocResult(unit=u, verdict=PASS, reasons=[], findings=[]))
    cross = [F(FLAG, "names.father_dots", "SAMPLE FATHER vs SAMPLE FATHER. — check by eye", [1, 3]),
             F(FAIL, "financial.holder", f"holder {OTHER}", [5])]
    notices = [Notice(kind=ORDER, pages=(3, 4), message=f"{NAME} pages out of order", blocks=True, units=("D03",)),
               Notice(kind=NOTE, pages=(2, 2), message="page 2 read turned", blocks=False)]
    missing = [(MISSING, "PHOTO MISSING"),
               (FLAG, "FATHER NID (OR DEATH CERTIFICATE) MISSING unless the death certificate on "
                      f"{NAME} FILE.pdf is the father's \u2014 check by eye"),
               (MISSING, f"SOMETHING ELSE {NAME}")]
    headline = (f"{overall} \u2014 1 FAIL, 1 FLAG, 2 MISSING, 1 ORDER" if overall != STOP
                else f"{STOP} \u2014 1 STOP notice; no document verdicts until the pages are fixed")
    return RunResult(
        schema=RUN_SCHEMA, runner_version=RUNNER_VERSION, base_rules_version=BASE_RULES_VERSION,
        runner_rules_version=RUNNER_RULES_VERSION, case_id="B-0123456789abcdef" if mode == "bundle" else
        "F-0123456789abcdef", mode=mode, input=f"C:\\Users\\{NAME}\\{NAME} BUNDLE.pdf", program="KLP",
        today="2026-10-28",
        reading={"pages_by_engine": {"gpu": 3, "cpu": 1, "layer": 1}, "note": "app GPU build",
                 "page_texts": f"C:\\Users\\{NAME}\\cache\\pagetexts\\x.json",
                 "engine_stats": {"ocr_calls": 7, "ocr_seconds": 4.0, "where": f"C:\\Users\\{NAME}\\y"}},
        pages=pages, types=[], split=SplitResult(units=[d.unit for d in docs], notices=notices, missing=[],
                                                  stop=overall == STOP),
        identity=None, births=[], docs=docs, cross=cross, notices=notices, missing_lines=missing, overall=overall,
        headline=headline, timings={"ingest": 0.1, "ocr": 3.2, "names": 1.5}, safety={"blocked": []},
        not_checked=[(f"{NAME} NOTES.docx", "not a PDF or image")] if mode == "folder" else [])


def fake_info():
    return SimpleNamespace(nvml=True, error="", count=1, index=0, name="NVIDIA GeForce RTX 0000", driver="000.00",
                           cuda_driver=12090, cc=(12, 0), total_mb=8151, free_mb=6120)


def assert_no_personal_data(tc: unittest.TestCase, text: str) -> None:
    for m in MARKERS:
        tc.assertNotIn(m, text, f"made-up marker {m!r} leaked")


class TestClean(unittest.TestCase):
    def test_keeps_numbers_and_safe_strings(self):
        self.assertEqual(CF.cleaned({"a": 1, "b": 2.123456, "c": "lock", "d": [1, "x"], "e": None, "f": True}),
                         {"a": 1, "b": 2.1235, "c": "lock", "d": [1, "x"], "e": None, "f": True})

    def test_drops_paths_long_strings_and_objects(self):
        got = CF.cleaned({"p": "C:\\Users\\x\\y.lock", "q": "\\\\server\\share", "r": "a" * 200,
                          "s": object(), "t": {"u": "D:/x"}, "C:\\k": 1, "v": "//host/x", "w": "line\nbreak"})
        self.assertEqual(got, {"t": {}})

    def test_float_nan_is_none(self):
        self.assertIsNone(CF.cleaned({"x": float("nan")})["x"])

    def test_safe_str(self):
        self.assertEqual(CF.safe_str("BANK SOLVENCY / STATEMENT"), "BANK SOLVENCY / STATEMENT")
        self.assertIsNone(CF.safe_str("C:\\x"))
        self.assertIsNone(CF.safe_str("/abs/path"))
        self.assertIsNone(CF.safe_str(5))


class TestBuild(unittest.TestCase):
    def setUp(self):
        self.r = make_result()
        self.facts = CF.build(self.r, result_code="ABCD-1234",
                              app={"version": "1.0.0", "build_id": "20261029.1-abc1234", "flavour": "gpu-cu128",
                                   "where": "C:\\Users\\x"},
                              gpu=CF.gpu_block(info=fake_info(), card={"torch": "2.11.0+cu128", "cuda": "12.8",
                                                                       "cudnn": "9.19.0"}, cap_gb=3.0),
                              budget_hits=[{"where": "birth.qr", "page": 7}, {"where": "C:\\x", "page": True}],
                              process={"peak_working_set_mb": 2100, "peak_commit_mb": 3000},
                              app_timings={"card": 14.2, "firstread": 30.5},
                              warnings=["summary.pdf not written: OSError", "C:\\Users\\x"])
        self.text = CF.to_text(self.facts)

    def test_head_fields(self):
        f = self.facts
        self.assertEqual(f["schema"], CF.SCHEMA)
        self.assertEqual((f["result_code"], f["check_date"], f["program"], f["mode"], f["overall"]),
                         ("ABCD-1234", "2026-10-28", "KLP", "bundle", FAIL))
        self.assertEqual(f["runner"]["version"], RUNNER_VERSION)
        self.assertEqual(f["app"], {"version": "1.0.0", "build_id": "20261029.1-abc1234", "flavour": "gpu-cu128"})
        self.assertEqual(f["headline_counts"], {"FAIL": 1, "FLAG": 1, "MISSING": 2, "ORDER": 1})
        self.assertEqual((f["pages"], f["files"]), (5, 1))
        self.assertEqual(f["pages_by_engine"], {"gpu": 3, "cpu": 1, "layer": 1})
        self.assertEqual(f["engine_stats"], {"ocr_calls": 7, "ocr_seconds": 4.0})
        self.assertEqual(f["timings_s"], {"ingest": 0.1, "ocr": 3.2, "names": 1.5, "card": 14.2, "firstread": 30.5})
        self.assertEqual(f["budget_hits"], [{"where": "birth.qr", "page": 7}, {"where": "?", "page": None}])
        self.assertEqual(f["process"], {"peak_working_set_mb": 2100, "peak_commit_mb": 3000})
        self.assertEqual(f["warnings"], ["summary.pdf not written: OSError"])

    def test_ocr_seconds(self):
        s = self.facts["ocr_seconds"]
        self.assertEqual(s["first_read"], {"n": 3, "total": 3.6, "mean": 1.2, "median": 1.2, "max": 1.6})
        self.assertEqual(s["light_reader"], {"n": 1, "total": 4.0, "mean": 4.0, "median": 4.0, "max": 4.0})

    def test_lines(self):
        lines = self.facts["lines"]
        self.assertEqual([x["title"] for x in lines], ["PASSPORT", "PHOTO", "ACADEMIC HSC",
                                                       "BANK SOLVENCY / STATEMENT"])
        self.assertEqual([x["verdict"] for x in lines], [PASS, PASS, FAIL, FLAG])
        self.assertEqual(lines[2]["where"], {"pages": [3, 4]})
        self.assertEqual(lines[2]["reasons"], [[FAIL, "academic.apostille", [3, 4]],
                                               [FLAG, "academic.board_qr", [4]]])
        self.assertEqual(lines[3]["reasons"], [[ERROR, "bank.balance", [5]]])
        self.assertEqual(lines[0]["reasons"], [])

    def test_cross_missing_notices(self):
        f = self.facts
        self.assertEqual(f["cross"], [{"line": "NAMES", "verdict": FLAG, "reasons": [[FLAG, "names.father_dots", [1, 3]]]},
                                      {"line": "FINANCIAL CHAIN", "verdict": FAIL,
                                       "reasons": [[FAIL, "financial.holder", [5]]]}])
        self.assertEqual(f["missing"], [[MISSING, "PHOTO MISSING"],
                                        [FLAG, "FATHER NID (OR DEATH CERTIFICATE) MISSING (conditional)"],
                                        [MISSING, "(other)"]])
        self.assertEqual(f["notices"], [{"kind": NOTE, "pages": [2, 2], "blocks": False},
                                        {"kind": ORDER, "pages": [3, 4], "blocks": True}])

    def test_gpu_block_present(self):
        g = self.facts["gpu"]
        self.assertEqual(g["card"]["name"], "NVIDIA GeForce RTX 0000")
        self.assertEqual(g["card"]["cc"], "12.0")
        self.assertEqual(g["engine"], {"torch": "2.11.0+cu128", "cuda": "12.8", "cudnn": "9.19.0", "cap_gb": 3.0})

    def test_no_personal_data(self):
        assert_no_personal_data(self, self.text)
        for word in ("e-Apostille", "holder SAMPLE", "MRZ of", "read turned", "app GPU build", "note about"):
            self.assertNotIn(word, self.text)          # messages and notes never enter
        self.assertTrue(self.text.isascii())

    def test_folder_mode_names_files_by_position(self):
        facts = CF.build(make_result("folder"), result_code="ABCD-1234", app={"version": "1.0.0"})
        text = CF.to_text(facts)
        assert_no_personal_data(self, text)
        lines = facts["lines"]
        self.assertEqual(lines[0]["where"], {"file": 1, "pages": [1, 1]})
        self.assertEqual(lines[2]["where"], {"file": 2, "pages": [2, 3]})
        self.assertEqual(facts["files"], 2)
        self.assertEqual(facts["not_checked"], {"files": 1, "reasons": {"not a PDF or image": 1}})
        self.assertIsNone(facts["gpu"])

    def test_known_missing_texts_come_from_split(self):
        known = CF.known_missing_texts()
        self.assertIn("PHOTO MISSING", known)
        self.assertIn("BANK SOLVENCY CERTIFICATE MISSING", known)

    def test_missing_entries_without_known_texts(self):
        self.assertEqual(CF.missing_entries([(MISSING, "PHOTO MISSING")], []), [[MISSING, "(other)"]])
        self.assertEqual(CF.missing_entries([("bad",)], ["X"]), [])


class TestGpuBlock(unittest.TestCase):
    def test_full_block(self):
        fr = {"pages": {"total": 14, "read": 13, "ocr": 12, "layer": 1, "image": 0, "skipped": 1},
              "seconds_total": 18.2, "model_load_s": 2.1, "downscaled": [{"page": 3}], "skipped": 1,
              "oom_retries": 2, "peak_alloc_gb": 2.41, "peak_reserved_gb": 2.8, "reread_downscaled": 1,
              "stale_store": False}
        sessions = {"det": {"calls_card": 10, "seconds_card": 3.5, "calls_cpu": 1, "seconds_cpu": 9.0,
                            "oom_retries": 3, "calls_lean": 2, "releases": 9},
                    "rec": {"card_calls": 200, "card_seconds": 1.5, "cpu_calls": 0, "ooms": 0}}
        gate = {"waits": 2, "waited_s": 181.5, "by_why": {"lock": 1, "memory": 1}, "lock": "C:\\Hangeul\\x.lock",
                "last_free_mb": 6000}
        b = CF.gpu_block(info=fake_info(), card={"torch": "2.11.0+cu128", "cuda": "12.8", "cudnn": "9.19.0",
                                                 "cap_gb": 3.0}, start_s=14.2, fr=fr, sessions=sessions,
                         light_load_s=1.1, gate=gate, peaks={"peak_allocated_gb": 2.6, "peak_reserved_gb": 2.9})
        self.assertEqual(b["card"]["total_mb"], 8151)
        self.assertEqual(b["first_read"]["pages"]["ocr"], 12)
        self.assertEqual((b["first_read"]["downscaled"], b["first_read"]["skipped"], b["first_read"]["oom_retries"]),
                         (1, 1, 2))
        self.assertEqual(b["light_reader"]["calls"]["det"], {"card_calls": 10, "card_s": 3.5, "cpu_calls": 1,
                                                             "cpu_s": 9.0, "oom_retries": 3, "lean_calls": 2,
                                                             "releases": 9})
        self.assertEqual(b["light_reader"]["calls"]["rec"]["releases"], 0)
        self.assertEqual(b["light_reader"]["calls"]["rec"]["lean_calls"], 0)
        self.assertEqual(b["light_reader"]["lean_calls"], 2)               # G2: the lean detector retries
        self.assertEqual((b["first_read"]["reread_downscaled"], b["first_read"]["stale_store"]), (1, False))
        self.assertEqual(b["light_reader"]["calls"]["rec"]["card_calls"], 200)
        self.assertEqual((b["cpu_calls"], b["oom_retries"]), (1, 5))
        self.assertEqual(b["model_load_s"], {"card_start": 14.2, "first_read": 2.1, "light_reader": 1.1})
        self.assertEqual(b["waits"], {"count": 2, "seconds": 181.5,
                                      "detail": {"by_why": {"lock": 1, "memory": 1}, "last_free_mb": 6000}})
        self.assertEqual(b["peaks"]["alloc_gb"], 2.6)
        self.assertEqual(b["peaks"]["reserved_gb"], 2.9)
        self.assertEqual(b["peaks"]["first_read_alloc_gb"], 2.41)
        self.assertNotIn("Hangeul", json.dumps(b))
        self.assertEqual(CF.done_gpu(b), {"card": "NVIDIA GeForce RTX 0000", "peak_alloc_gb": 2.6,
                                          "peak_reserved_gb": 2.9, "cpu_calls": 1, "waits": 2, "waited_s": 181.5,
                                          "downscaled": 1, "skipped": 1})

    def test_empty_block(self):
        b = CF.gpu_block()
        self.assertEqual(b["card"], {})
        self.assertEqual(b["cpu_calls"], 0)
        self.assertEqual(CF.done_gpu(b), {"card": None, "peak_alloc_gb": None, "peak_reserved_gb": None,
                                          "cpu_calls": 0, "waits": 0, "waited_s": 0.0, "downscaled": 0, "skipped": 0})
        self.assertEqual(CF.done_gpu(None)["waits"], 0)


class TestWrite(unittest.TestCase):
    def setUp(self):
        self.dir = TEST_ROOT / "checkfacts"
        shutil.rmtree(self.dir, ignore_errors=True)
        self.dir.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_write_asks_the_guard_first(self):
        seen = []
        facts = CF.build(make_result(), result_code="ABCD-1234", app={"version": "1.0.0"})
        p = CF.write(facts, self.dir / "checkfacts.json", assert_writable=seen.append)
        self.assertEqual(seen, [p])
        self.assertEqual(json.loads(p.read_text(encoding="ascii")), facts)

    def test_refused_write_writes_nothing(self):
        def refuse(p):
            raise PermissionError("refused")
        with self.assertRaises(PermissionError):
            CF.write({"schema": CF.SCHEMA}, self.dir / "checkfacts.json", assert_writable=refuse)
        self.assertFalse((self.dir / "checkfacts.json").exists())

    def test_build_error(self):
        f = CF.build_error(code="CARD_FAILED", stage="firstread", exc_type="RuntimeError", app={"version": "1.0.0"},
                           check_date="2026-10-28", program="KLP", gpu=CF.gpu_block(info=fake_info()),
                           budget_hits=[], process={"peak_working_set_mb": 10}, app_timings={"card": 1.0})
        self.assertEqual(f["error"], {"code": "CARD_FAILED", "stage": "firstread", "type": "RuntimeError"})
        self.assertEqual(f["gpu"]["card"]["name"], "NVIDIA GeForce RTX 0000")
        self.assertEqual(f["runner"]["version"], RUNNER_VERSION)


class TestReasons(unittest.TestCase):
    def test_only_reason_levels_sorted_unique(self):
        fs = [F(FLAG, "b.rule", "x", [2, 1]), F(FAIL, "a.rule", "y", [3]), F(PASS, "c", "z"), F(PRESENT, "d", "w"),
              F(FLAG, "b.rule", "again", [1, 2]), F(ERROR, "e.rule", "v")]
        self.assertEqual(CF.reasons(fs), [[FAIL, "a.rule", [3]], [ERROR, "e.rule", []], [FLAG, "b.rule", [1, 2]]])

    def test_stop_result(self):
        facts = CF.build(make_result(overall=STOP), result_code="ABCD-1234", app={})
        self.assertEqual(facts["overall"], STOP)
        self.assertEqual(facts["headline_counts"], {"STOP": 1})


if __name__ == "__main__":
    unittest.main()
