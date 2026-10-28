"""APP_SPEC rev 1 section 1.3: the result code. Made-up RunResult fixtures (every name and number a placeholder);
the report outputs go to a temporary folder under the places temp folder (private-runner\\tmp in DEV), removed
afterwards, with safety's assert_writable patched out."""
from __future__ import annotations

import dataclasses
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.verify.runner import places
from src.verify.runner import report as REP
from src.verify.runner import resultcode as RC
from src.verify.runner.model import (FAIL, FLAG, MISSING, NOTE, ORDER, PASS, PRESENT, RUN_SCHEMA, DocResult, DocUnit,
                                     Finding, Notice, PageRead, RunResult, SplitResult)

GOLDEN = "964B-71C5"          # code(fixture()) for HDC-RC/1: an algorithm change must raise RC_SCHEMA


def F(v, rule, msg="", pages=()):
    return Finding(verdict=v, rule=rule, message=msg or f"{rule} said {v.lower()}", source="test", pages=tuple(pages))


def page(i, file="SAMPLE BUNDLE.pdf", file_page=None):
    return PageRead(index=i, file=file, file_page=i if file_page is None else file_page, text="MADE UP TEXT",
                    kind="ocr", chars=12, sideways=False, seconds=1.0, image_only=True, engine="gpu")


def unit(uid, key, pages, title, source_file=""):
    return DocUnit(uid=uid, key=key, fine=key, title=title, pages=list(pages), source_file=source_file)


def fixture(mode: str = "bundle") -> RunResult:
    files = ["PASSPORT.pdf", "FATHER NID.pdf", "ACADEMIC HSC.pdf", "ACADEMIC HSC.pdf", "ID CARD CV.pdf"]
    pages = [page(i, files[i], 0 if i != 3 else 1) if mode == "folder" else page(i) for i in range(5)]
    sf = files if mode == "folder" else [""] * 5
    docs = [DocResult(unit=unit("D01", "passport", [0], "PASSPORT", sf[0]),
                      findings=[F(PASS, "passport.validity"), F(NOTE, "identity.mrz_reread")], verdict=PASS,
                      reasons=[]),
            DocResult(unit=unit("D02", "father_nid", [1], "FATHER NID", sf[1]),
                      findings=[F(FLAG, "nid.number", "the number reads 000 here", pages=(2,)),
                                F(PASS, "nid.name")], verdict=FLAG, reasons=["the number reads 000 here"]),
            DocResult(unit=unit("D03", "academic", [2, 3], "ACADEMIC HSC", sf[2]),
                      findings=[F(FAIL, "bot.check_academic", "no e-Apostille", pages=(3,)),
                                F(FLAG, "page.bangla", "a page is in Bangla", pages=(4, 3)),
                                F(ORDER, "academic.apostille_order", "put it first", pages=(3,)),
                                F(PASS, "academic.gpa")],
                      verdict=FAIL, reasons=["no e-Apostille", "a page is in Bangla"]),
            DocResult(unit=unit("D04", "id_card_cv", [4], "ID CARD CV", sf[4]),
                      findings=[F(PRESENT, "doc.present_only", "present — not checked")], verdict=PRESENT,
                      reasons=[])]
    cross = [F(FAIL, "names.mother", "mother's name differs on ACADEMIC HSC (page 3)", pages=(3,)),
             F(PASS, "names.student", "the same on 3 documents"),
             F(FLAG, "financial.taxpayer_holder", "the bank holder could not be compared"),
             F(FLAG, "page.colour_bundle", "scanned in black and white")]
    notices = [Notice(kind=ORDER, pages=(3, 3), message="the e-Apostille is on page 3 — put it first",
                      units=("D03",)),
               Notice(kind=NOTE, pages=(1, 1), message="a made-up note")]
    missing = [(MISSING, "PHOTO MISSING"), (FLAG, "MOTHER NID (OR DEATH CERTIFICATE)  MISSING unless page 6 is it")]
    res = SplitResult(units=[d.unit for d in docs], notices=list(notices), missing=["photo"], stop=False)
    return RunResult(schema=RUN_SCHEMA, runner_version="0.2.7", base_rules_version="b", runner_rules_version="r",
                     case_id="B-0123456789abcdef" if mode == "bundle" else "F-0123456789abcdef", mode=mode,
                     input=r"C:\made\up\input.pdf", program="KLP", today="2026-10-28",
                     reading={"pages_by_engine": {"gpu": 5, "cpu": 0, "layer": 0}, "note": "a made-up note",
                              "page_texts": "", "engine_stats": {"ocr_calls": 0}},
                     pages=pages, types=[], split=res, identity=None, births=[], docs=docs, cross=cross,
                     notices=notices, missing_lines=missing, overall=FAIL, headline="FAIL — made up",
                     timings={"ocr": 1.25}, safety={"blocked": [], "bot_opens": 0})


class TestParts(unittest.TestCase):
    def test_norm(self):
        self.assertEqual(RC.norm("  A\u0301cademic \t hsc\n"), "\u00c1CADEMIC HSC")      # NFC, spaces, upper
        self.assertEqual(RC.norm(None), "")

    def test_reasons(self):
        fs = [F(FLAG, "b.rule", pages=(4, 3)), F(PASS, "a.ok"), F(FAIL, "z.rule", pages=(2,)),
              F("ERROR", "c.rule"), F(FLAG, "b.rule", "another wording", pages=(3, 4)), F(NOTE, "n"),
              F(ORDER, "o", pages=(1,)), F(PRESENT, "p")]
        self.assertEqual(RC.reasons(fs), [["FAIL", "z.rule", [2]], ["ERROR", "c.rule", []], ["FLAG", "b.rule", [3, 4]]])
        self.assertEqual(RC.VERDICT_LEVELS, (FAIL, "ERROR", FLAG))

    def test_where_key(self):
        self.assertEqual(RC.where_key(unit("D", "academic", [2, 3], "X"), "bundle"), [3, 4])
        self.assertEqual(RC.where_key(unit("D", "academic", [], "X"), "bundle"), [])
        self.assertEqual(RC.where_key(unit("D", "academic", [7], "X", "academic  hsc.pdf"), "folder"),
                         ["ACADEMIC HSC.PDF"])

    def test_canonical_shape(self):
        c = RC.canonical(fixture())
        self.assertEqual(c[:5], [RC.RC_SCHEMA, "2026-10-28", "KLP", "bundle", FAIL])
        self.assertEqual([d[0] for d in c[5]], ["PASSPORT", "FATHER NID", "ACADEMIC HSC", "ID CARD CV"])
        self.assertEqual(c[5][2], ["ACADEMIC HSC", [3, 4], FAIL, [["FAIL", "bot.check_academic", [3]],
                                                                  ["FLAG", "page.bangla", [3, 4]]]])
        self.assertEqual([x[:2] for x in c[6]], [["NAMES", FAIL], ["FINANCIAL CHAIN", FLAG], ["BUNDLE", FLAG]])
        self.assertEqual(c[7][1], [FLAG, "MOTHER NID (OR DEATH CERTIFICATE) MISSING UNLESS PAGE 6 IS IT"])
        self.assertEqual(c[8], [["NOTE", 1, 1, False], ["ORDER", 3, 3, False]])
        json.dumps(c, ensure_ascii=True)                                   # JSON-able as it is


class TestCode(unittest.TestCase):
    def test_format_and_golden(self):
        rc = RC.code(fixture())
        self.assertRegex(rc, r"^[0-9A-F]{4}-[0-9A-F]{4}$")
        self.assertRegex(rc, RC.CODE_RE)
        self.assertEqual(RC.RC_SCHEMA, "HDC-RC/1")
        self.assertEqual(rc, GOLDEN)

    def test_stable(self):
        r = fixture()
        self.assertEqual(RC.code(r), RC.code(r))
        self.assertEqual(RC.code(fixture()), RC.code(fixture()))           # two separately built fixtures
        self.assertEqual(RC.code(fixture("folder")), RC.code(fixture("folder")))

    def _same(self, change):
        r = fixture()
        change(r)
        self.assertEqual(RC.code(r), GOLDEN)

    def test_unchanged_by_what_stays_out(self):
        self._same(lambda r: setattr(r, "timings", {"ocr": 99.0, "names": 3.0}))
        self._same(lambda r: setattr(r, "input", r"C:\another\made\up\path.pdf"))
        self._same(lambda r: setattr(r, "case_id", "B-fedcba9876543210"))
        self._same(lambda r: setattr(r, "reading", {"pages_by_engine": {"cpu": 5}, "note": "other",
                                                    "engine_stats": {"ocr_calls": 42, "ocr_seconds": 7.5}}))
        self._same(lambda r: setattr(r, "runner_version", "9.9.9"))
        self._same(lambda r: setattr(r, "headline", "another headline"))
        self._same(lambda r: setattr(r, "safety", {"blocked": ["x"], "bot_opens": 1}))

        def messages(r):
            for d in r.docs:
                d.findings = [dataclasses.replace(f, message=f.message + " (another wording, 12345)")
                              for f in d.findings]
                d.reasons = [x + " reworded" for x in d.reasons]
            r.cross = [dataclasses.replace(f, message="reworded") for f in r.cross]
            r.notices = [dataclasses.replace(n, message="reworded") for n in r.notices]
        self._same(messages)

        def reorder(r):
            r.docs[2].findings = list(reversed(r.docs[2].findings))
            r.cross = list(reversed(r.cross))
            r.notices = list(reversed(r.notices))
        self._same(reorder)

    def test_unchanged_by_out_folder(self):
        r = fixture()
        a = REP.summary_text(r, Path(r"C:\made\up\out1")).splitlines()
        b = REP.summary_text(r, Path(r"C:\made\up\out2")).splitlines()
        self.assertEqual(a[2], b[2])
        self.assertEqual(a[2], f"RESULT CODE: {GOLDEN} (check date 2026-10-28)")

    def _differs(self, change):
        r = fixture()
        change(r)
        self.assertNotEqual(RC.code(r), GOLDEN)

    def test_changes_with_the_decisions(self):
        self._differs(lambda r: setattr(r.docs[1], "verdict", FAIL))                       # FLAG -> FAIL
        self._differs(lambda r: r.docs[0].findings.append(F(FLAG, "passport.new_rule")))   # a new FLAG reason
        self._differs(lambda r: r.missing_lines.append((MISSING, "BANK STATEMENT MISSING")))
        self._differs(lambda r: setattr(r, "notices", [dataclasses.replace(r.notices[0], blocks=True),
                                                       r.notices[1]]))                     # ORDER turns blocking
        self._differs(lambda r: setattr(r, "today", "2026-10-29"))
        self._differs(lambda r: setattr(r, "program", "EAP"))
        self._differs(lambda r: setattr(r, "overall", FLAG))
        self._differs(lambda r: setattr(r, "mode", "folder"))
        self._differs(lambda r: setattr(r.docs[2].findings[1], "pages", (5,)))             # a reason's page
        self._differs(lambda r: r.cross.append(F(FAIL, "names.father", pages=(2,))))

    def test_folder_mode_uses_file_names(self):
        a, b = fixture("folder"), fixture("folder")
        b.docs[1].unit.source_file = "FATHER NID 2.pdf"
        self.assertNotEqual(RC.code(a), RC.code(b))
        c = fixture("folder")
        c.docs[1].unit.source_file = "  father   nid.PDF "
        self.assertEqual(RC.code(a), RC.code(c))                                          # normalised


class TestInTheReport(unittest.TestCase):
    def setUp(self):
        tmp_root = places.current().temp_dir
        tmp_root.mkdir(parents=True, exist_ok=True)
        self.dir = Path(tempfile.mkdtemp(prefix="test_resultcode_", dir=tmp_root))
        self.p = mock.patch.object(REP, "_assert_writable", lambda p: None)
        self.p.start()

    def tearDown(self):
        self.p.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_summary_line_and_run_json(self):
        r = fixture()
        paths = REP.write_all(r, self.dir, {"names_rereads": [], "tin": {}})
        lines = paths["summary"].read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[2], f"RESULT CODE: {GOLDEN} (check date 2026-10-28)")
        self.assertTrue(lines[3].startswith("READING: "))
        d = json.loads(paths["json"].read_text(encoding="utf-8"))
        self.assertEqual(d["result_code"], GOLDEN)
        self.assertEqual(set(d) - {f.name for f in dataclasses.fields(RunResult)},
                         {"names_rereads", "tin", "result_code"})

    def test_a_partial_result_still_gets_a_summary(self):
        """Other tests build summaries from a SimpleNamespace without `today` or `split`: line 3 says so."""
        from types import SimpleNamespace
        r = fixture()
        ns = SimpleNamespace(overall=FLAG, case_id="F-0", program="KLP", mode="folder", pages=r.pages,
                             headline="FLAG — 1 FLAG", reading={}, notices=[], docs=r.docs[:2], missing_lines=[],
                             cross=[], types=[])
        lines = [t for _, t in REP.summary_lines(ns)]
        self.assertEqual(lines[2], "RESULT CODE: not available (check date -)")

    def test_not_available_never_breaks_the_report(self):
        r = fixture()
        with mock.patch.object(RC, "code", side_effect=RuntimeError("made-up")):
            paths = REP.write_all(r, self.dir)
        lines = paths["summary"].read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[2], "RESULT CODE: not available (check date 2026-10-28)")
        self.assertIsNone(json.loads(paths["json"].read_text(encoding="utf-8"))["result_code"])


if __name__ == "__main__":
    unittest.main()
