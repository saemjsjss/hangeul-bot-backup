"""Task 6: report.py (RUNNER_SPEC 5.11). A fixed RunResult fixture with made-up data; outputs go to a temporary
folder under C:\\Hangeul\\DOCCHECK\\lab\\private-runner\\tmp (removed afterwards). safety is patched out."""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.verify.runner import report as REP
from src.verify.runner import verdict as V
from src.verify.runner.botimport import PRIVATE
from src.verify.runner.model import (ERROR, FAIL, FLAG, MISSING, NOT_JUDGED, NOTE, ORDER, PASS, PRESENT, RUN_SCHEMA,
                                     STOP, BirthFacts, DocResult, DocUnit, Finding, Identity, NameRef, Notice,
                                     PageRead, PageType, RunResult, SplitResult)

TMP_ROOT = PRIVATE / "tmp"
DOC_LINE = re.compile(r"^[A-Z][A-Z0-9 /()]*? \((p\d+(-\d+)?(,\d+(-\d+)?)*|[^()]+)\): "
                      r"(PASS|FLAG|FAIL|PRESENT|NOT JUDGED)( — .+)?$")
HEAD1 = re.compile(r"^\S+ \| (KLP|EAP|BACHELOR|MASTER) \| (bundle|folder) \| \d+ pages \| RESULT: "
                   r"(PASS|FLAG|FAIL|STOP|NOT JUDGED)$")
HEADLINE = re.compile(r"^(STOP|FAIL|FLAG|PASS) — .+$")
READING = re.compile(r"^READING: GPU pre-read \d+ pages, CPU light engine \d+, text layer \d+( — .+)?$")
RESULT_CODE = re.compile(r"^RESULT CODE: [0-9A-F]{4}-[0-9A-F]{4} \(check date \d{4}-\d{2}-\d{2}\)$")
ORDER_LINE = re.compile(r"^ORDER: fix pages? \d+(-\d+)? — .+$")


def F(v, rule, msg="", pages=(), evidence=None):
    return Finding(verdict=v, rule=rule, message=msg or f"{rule} said {v.lower()}", source="test",
                   pages=tuple(pages), evidence=dict(evidence or {}))


def page(i, text, engine="gpu", file="SAMPLE BUNDLE.pdf", file_page=None):
    return PageRead(index=i, file=file, file_page=i if file_page is None else file_page, text=text, kind="ocr",
                    chars=len(text), sideways=False, seconds=1.5, image_only=True, engine=engine)


def ptype(i, best):
    return PageType(index=i, best=best, score=6.0, scores={best: 6.0, "other": 1.0}, translation=False, level=None,
                    kind="", nid_numbers=[], birth_dates=[], holder=[])


def unit(uid, key, pages, title, fine="", role="", path=None, source_file="", notes=()):
    return DocUnit(uid=uid, key=key, fine=fine or key, title=title, pages=list(pages), role=role,
                   role_how="name" if role else "", path=path, source_file=source_file, notes=list(notes))


def fixture(mode="bundle", notices=True, stop=False) -> RunResult:
    texts = ["PASSPORT SAMPLE ONE P<SMPSAMPLE", "NATIONAL ID SAMPLE FATHER", "=SUM(A1) HIGHER SECONDARY",
             "TAXPAYER IDENTIFICATION", "RETURN OF INCOME", "CURRICULUM VITAE\nline\x01two", "EXTRA PAGE"]
    kinds = ["passport", "nid", "academic_certificate", "tin_certificate", "income_tax", "id_card_cv", "other"]
    if mode == "folder":
        files = ["PASSPORT.pdf", "FATHER NID.pdf", "ACADEMIC CERTIFICATE HSC.pdf", "TIN CERTIFICATE.pdf",
                 "INCOME TAX.pdf", "ID CARD CV.pdf", "EXTRA.pdf"]
        pages = [page(i, t, "cpu", files[i], 0) for i, t in enumerate(texts)]
    else:
        files = [""] * 7
        pages = [page(i, t, "layer" if i == 4 else "gpu") for i, t in enumerate(texts)]
    types = [ptype(i, k) for i, k in enumerate(kinds)]
    us = [unit("D01", "passport", [0], "PASSPORT", path=Path(r"C:\x\PASSPORT p01.pdf"), source_file=files[0],
               notes=[F(NOTE, "identity.mrz_reread", "MRZ re-read decided the expiry")]),
          unit("D02", "father_nid", [1], "FATHER NID", role="father", source_file=files[1]),
          unit("D03", "academic", [2], "ACADEMIC HSC", source_file=files[2]),
          unit("D04", "trade_license", [3], "TIN CERTIFICATE", fine="tin_certificate", source_file=files[3]),
          unit("D05", "income_tax", [4], "INCOME TAX", source_file=files[4]),
          unit("D06", "id_card_cv", [5], "ID CARD CV", source_file=files[5]),
          unit("D07", "unrecognised", [6], "UNRECOGNISED", source_file=files[6],
               notes=[F(FLAG, "split.unrecognised", "page 7 not recognised")])]
    long = "x" * 400
    per = {"D01": [F(PASS, "passport.validity", "valid until a made-up date"), F(PASS, "passport.mrz")],
           "D02": [F(PASS, "nid.number", pages=(2,))],
           "D03": [F(FAIL, "bot.check_academic", "no e-Apostille with a scannable QR", pages=(3,)),
                   F(FAIL, "names.mother", "mother's name is SAMPLA here but SAMPLE on the birth certificate",
                     pages=(3,), evidence={"reads": ["SAMPLA"], "when": dt.date(2001, 2, 3)}),
                   F(ERROR, "page.qr", "reader broke"), F(FLAG, "page.bangla", long), F(FLAG, "other.one"),
                   F(ORDER, "academic.apostille_order", "the e-Apostille for HSC is on page 3 — put it first",
                     pages=(3,))],
           "D04": [F(FLAG, "tin.web_address", "printed from a made-up host, not .gov.bd", pages=(4,)),
                   F(PASS, "tin.qr_tin", "QR TIN equals the print")],
           "D05": [F(PRESENT, "doc.present_info", "present — information only for KLP"),
                   F(NOTE, "bot.check_income_tax", "tax paid below the guideline (information only)")],
           "D06": [F(PRESENT, "doc.present_only", "present — not checked (owner, 21 Oct)")]}
    ns = []
    if notices:
        ns = [Notice(kind="HINT", pages=(7, 7), message="PHOTO MISSING in its place; page 7 reads partly like it"),
              Notice(kind=NOTE, pages=(1, 1), message="a split note")]
    res = SplitResult(units=us, notices=ns, missing=["photo"], stop=stop)
    names = type("NR", (), {})()
    names.per_unit = {}
    names.summary = [F(FAIL, "names.mother", "mother's name differs on ACADEMIC HSC (page 3)"),
                     F(PASS, "names.student", "student's name is the same on 3 documents")]
    chain = [F(FLAG, "financial.taxpayer_holder", "the bank holder could not be compared")]
    bundle = [F(FLAG, "page.colour_bundle", "the whole bundle was scanned in black and white")]
    if stop:
        docs, cross = [], []
        notices_out = [Notice(kind=STOP, pages=(2, 2), message="whose NID is on page 2 could not be told")]
        res.notices = notices_out
        missing = []
    else:
        docs, cross, notices_out = V.merge(res, per, names=names, chain_summary=chain, bundle=bundle, program="KLP")
        missing = [(MISSING, "PHOTO MISSING"),
                   (FLAG, "MOTHER NID (OR DEATH CERTIFICATE) MISSING unless the death certificate on page 6 is "
                          "the mother's — check by eye")]
    overall, headline = V.overall(docs, missing, res, cross, notices_out)
    ident = Identity(student=NameRef(parts=("SAMPLE", "ONE"), written="SAMPLE ONE", source="passport MRZ",
                                     confirmed=True),
                     father=NameRef(parts=("SAMPLE", "FATHER"), source="passport field"),
                     mother=NameRef(), dob=dt.date(2000, 1, 2), dob_source="MRZ", dob_trusted=True,
                     mrz={"line2_ok": True})
    births = [BirthFacts(pages=(9,), format="english_bdris", dob=dt.date(2000, 1, 2),
                         issue_date=dt.date(2010, 5, 6), qr={"key_bytes": b"\x00\x01", "host": "example.invalid"})]
    by = {"gpu": sum(p.engine == "gpu" for p in pages), "cpu": sum(p.engine == "cpu" for p in pages),
          "layer": sum(p.engine == "layer" for p in pages)}
    return RunResult(schema=RUN_SCHEMA, runner_version="0.2.0", base_rules_version="b", runner_rules_version="r",
                     case_id="B-0123456789abcdef" if mode == "bundle" else "F-0123456789abcdef", mode=mode,
                     input=r"C:\made\up\input.pdf", program="KLP", today="2026-10-27",
                     reading={"pages_by_engine": by, "note": "a made-up note", "page_texts": "",
                              "engine_stats": {"ocr_calls": 0}},
                     pages=pages, types=types, split=res, identity=ident, births=births, docs=docs, cross=cross,
                     notices=notices_out, missing_lines=missing, overall=overall, headline=headline,
                     timings={"ocr": 1.25, "bot": 2.5}, safety={"blocked": [], "bot_opens": 0, "unchanged": True})


class _TmpCase(unittest.TestCase):
    def setUp(self):
        TMP_ROOT.mkdir(parents=True, exist_ok=True)
        self.dir = Path(tempfile.mkdtemp(prefix="test_report_", dir=TMP_ROOT))
        self.writes = []
        self.p = mock.patch.object(REP, "_assert_writable", side_effect=lambda p: self.writes.append(Path(p)))
        self.p.start()

    def tearDown(self):
        self.p.stop()
        shutil.rmtree(self.dir, ignore_errors=True)


class TestSummary(unittest.TestCase):
    def test_line_formats(self):
        r = fixture()
        lines = REP.summary_text(r, Path(r"C:\made\up\out")).splitlines()
        self.assertLessEqual(len(lines), REP.MAX_LINES)
        self.assertRegex(lines[0], HEAD1)
        self.assertTrue(lines[0].endswith("RESULT: FAIL"))
        self.assertRegex(lines[1], HEADLINE)
        self.assertEqual(lines[1], "FAIL — 1 FAIL, 5 FLAG, 1 MISSING, 1 ORDER")
        self.assertRegex(lines[2], RESULT_CODE)                          # APP_SPEC rev 1 section 1.3: line 3
        self.assertTrue(lines[2].endswith("(check date 2026-10-27)"))
        self.assertRegex(lines[3], READING)
        self.assertEqual(lines[3], "READING: GPU pre-read 6 pages, CPU light engine 0, text layer 1 — a made-up note")
        self.assertNotIn("ORDER: OK — the pages fit the fixed order", lines)     # an ORDER notice exists
        orders = [s for s in lines if s.startswith("ORDER:")]
        self.assertEqual(orders, ["ORDER: fix page 3 — the e-Apostille for HSC is on page 3 — put it first"])
        self.assertRegex(orders[0], ORDER_LINE)
        self.assertIn("HINT: PHOTO MISSING in its place; page 7 reads partly like it", lines)
        self.assertIn("NOTE: a split note", lines)
        docs = [s for s in lines if DOC_LINE.match(s)]
        self.assertEqual([s.split(" (")[0] for s in docs],
                         ["PASSPORT", "FATHER NID", "ACADEMIC HSC", "TIN CERTIFICATE", "INCOME TAX", "UNRECOGNISED"])
        self.assertIn("PASSPORT (p1): PASS", lines)
        self.assertIn("INCOME TAX (p5): PRESENT — present — information only for KLP", lines)
        hsc = [s for s in lines if s.startswith("ACADEMIC HSC")][0]
        self.assertTrue(hsc.startswith("ACADEMIC HSC (p3): FAIL — no e-Apostille with a scannable QR; mother's name"))
        self.assertTrue(hsc.endswith("(+2 more)"))                       # 2 FAIL + ERROR + 2 FLAG = 5 reasons
        self.assertIn("could not be checked: reader broke", hsc)
        for reason in hsc.split(" — ", 1)[1].split("; "):
            self.assertLessEqual(len(reason.replace(" (+2 more)", "")), REP.MAX_REASON)
        self.assertIn("PHOTO MISSING", lines)
        self.assertTrue(any(s.startswith("MOTHER NID (OR DEATH CERTIFICATE) MISSING unless") for s in lines))
        self.assertIn("NAMES: FAIL — mother's name differs on ACADEMIC HSC (page 3)", lines)
        self.assertIn("FINANCIAL CHAIN: FLAG — the bank holder could not be compared", lines)
        self.assertIn("BUNDLE: FLAG — the whole bundle was scanned in black and white", lines)
        self.assertIn("NEW CHECKS (owner to confirm): tin.web_address on TIN CERTIFICATE (p4)", lines)
        self.assertIn("Not checked (present): ID CARD CV (p6)", lines)
        self.assertFalse(any(s.startswith("ID CARD CV (") for s in lines))   # no document line for the CV
        self.assertEqual(lines[-1], r"Report: C:\made\up\out\report.xlsx | JSON: C:\made\up\out\run.json")

    def test_order_ok_only_without_notices(self):
        r = fixture(notices=False)
        r.notices = [n for n in r.notices if n.kind != ORDER]
        lines = REP.summary_text(r).splitlines()
        self.assertIn("ORDER: OK — the pages fit the fixed order", lines)
        r.notices.append(Notice(kind=ORDER, pages=(2, 3), message="moved", blocks=True))
        self.assertNotIn("ORDER: OK — the pages fit the fixed order", REP.summary_text(r).splitlines())

    def test_folder_mode_uses_file_names(self):
        r = fixture(mode="folder", notices=False)
        r.docs = list(reversed(r.docs))                                    # file-name order is no order
        lines = REP.summary_text(r).splitlines()
        self.assertEqual([s.split(" (")[0] for s in lines if DOC_LINE.match(s)],
                         ["PASSPORT", "FATHER NID", "ACADEMIC HSC", "TIN CERTIFICATE", "INCOME TAX",
                          "UNRECOGNISED"])                                    # the owner's fixed order
        self.assertIn("PASSPORT (PASSPORT.pdf): PASS", lines)
        self.assertIn("NEW CHECKS (owner to confirm): tin.web_address on TIN CERTIFICATE (TIN CERTIFICATE.pdf)",
                      lines)
        self.assertFalse(any(s.startswith("ORDER: OK") for s in lines))      # no fixed order in a folder
        self.assertRegex(lines[2], RESULT_CODE)
        self.assertRegex(lines[3], r"^READING: GPU pre-read 0 pages, CPU light engine 7, text layer 0")

    def test_stop_has_no_document_lines(self):
        r = fixture(stop=True)
        lines = REP.summary_text(r).splitlines()
        self.assertTrue(lines[0].endswith("RESULT: STOP"))
        self.assertIn("STOP: whose NID is on page 2 could not be told (page 2)", lines)
        self.assertFalse([s for s in lines if DOC_LINE.match(s)])
        self.assertFalse([s for s in lines if s.endswith("MISSING")])
        self.assertTrue(any(s.startswith("PAGE TYPES: p1 passport, p2 nid") for s in lines))

    def test_at_most_80_lines(self):
        r = fixture(notices=False)
        many = []
        for i in range(120):
            u = unit(f"X{i:03d}", "eca", [6], f"ECA {i}")
            many.append(DocResult(unit=u, findings=[], verdict=PASS, reasons=[]))
        r.docs = r.docs + many
        lines = REP.summary_text(r).splitlines()
        self.assertLessEqual(len(lines), REP.MAX_LINES)
        self.assertTrue(lines[-1].startswith("Report: "))
        self.assertTrue(any(s.startswith("PASS: PASSPORT (p1); FATHER NID (p2); ECA 0 (p7)") for s in lines))
        self.assertTrue(all(len(s) <= 210 for s in lines))
        r.notices = [Notice(kind=ORDER, pages=(i + 1, i + 1), message=f"made-up order break {i}") for i in range(150)]
        lines = REP.summary_text(r).splitlines()
        self.assertEqual(len(lines), REP.MAX_LINES)
        self.assertTrue(any(re.match(r"^\.\.\. \(\+\d+ more lines in report\.xlsx\)$", s) for s in lines))


class TestExcel(_TmpCase):
    def test_sheets_rows_and_colours(self):
        from openpyxl import load_workbook
        r = fixture()
        path = self.dir / "report.xlsx"
        REP.write_excel(r, path, {"names_rereads": [{"page": 2, "effect": "confirmed", "crop": Path("c.png")}]})
        self.assertIn(path, self.writes)
        wb = load_workbook(path)
        self.assertEqual(wb.sheetnames, ["Summary", "Documents", "Pages", "Findings"])
        pages = wb["Pages"]
        self.assertEqual(pages.max_row - 1, len(r.pages))
        header = [c.value for c in pages[1]]
        self.assertEqual(header[:5], ["page", "file", "file_page", "read", "engine"])
        self.assertEqual(header[-1], "text_start")
        row3 = {h: c.value for h, c in zip(header, pages[4])}
        self.assertEqual(row3["text_start"], "=SUM(A1) HIGHER SECONDARY")              # text, never a formula
        self.assertEqual(row3["doc_verdict"], FAIL)
        self.assertIn("bot.check_academic", row3["fails"])
        row6 = {h: c.value for h, c in zip(header, pages[7])}
        self.assertEqual(row6["text_start"], "CURRICULUM VITAE | linetwo")             # control character gone
        docs = wb["Documents"]
        dh = [c.value for c in docs[1]]
        self.assertEqual(dh, ["uid", "title", "key", "fine", "level", "pages", "cut_file", "role", "role_how",
                              "verdict", "FAIL reasons", "FLAG reasons", "notes", "pass_count"])
        verdicts = {docs.cell(row=i, column=1).value: docs.cell(row=i, column=10) for i in range(2, docs.max_row + 1)}
        self.assertEqual(verdicts["D03"].value, FAIL)
        self.assertEqual(verdicts["D03"].fill.fgColor.rgb[-6:], REP.FILL[FAIL])
        self.assertEqual(verdicts["D01"].fill.fgColor.rgb[-6:], REP.FILL[PASS])
        self.assertEqual(verdicts["D05"].fill.fgColor.rgb[-6:], REP.FILL[PRESENT])
        summ = wb["Summary"]
        first = summ.cell(row=2, column=1)
        self.assertEqual(first.value, FAIL)
        self.assertEqual(first.fill.fgColor.rgb[-6:], REP.FILL[FAIL])
        facts = {summ.cell(row=i, column=1).value: summ.cell(row=i, column=2).value
                 for i in range(1, summ.max_row + 1)}
        self.assertEqual(facts["safety: blocked events"], "0")
        self.assertEqual(facts["new checks fired"], "tin.web_address (TIN CERTIFICATE)")
        self.assertIn("MRZ; trusted=True", facts["identity: DOB"])
        fnd = wb["Findings"]
        verdict_col = [fnd.cell(row=i, column=4).value for i in range(2, fnd.max_row + 1)]
        for want in (FAIL, FLAG, ERROR, PASS, NOTE, PRESENT, ORDER, "HINT", MISSING, "REREAD"):
            self.assertIn(want, verdict_col)
        self.assertEqual(fnd.cell(row=1, column=8).value, "evidence")

    def test_stop_and_split_only_workbooks(self):
        from openpyxl import load_workbook
        r = fixture(stop=True)
        REP.write_excel(r, self.dir / "stop.xlsx")
        wb = load_workbook(self.dir / "stop.xlsx")
        self.assertEqual(wb.sheetnames, list(REP.SHEETS))
        self.assertEqual(wb["Pages"].max_row - 1, len(r.pages))
        self.assertEqual(wb["Pages"].cell(row=2, column=15).value, "D01")             # units from the split


class TestJson(_TmpCase):
    def test_round_trip(self):
        import numpy as np
        r = fixture()
        path = self.dir / "run.json"
        REP.write_json(r, path, {"names_rereads": [{"page": 1, "crop": Path("a.png")}],
                                 "tin": {"D04": {"qr": None, "image": np.zeros((2, 2)), "n": np.int64(3),
                                                 "codes": ("a", "b")}}})
        d = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(d["schema"], "runner-run/2")
        self.assertEqual(d["identity"]["dob"], "2000-01-02")
        self.assertEqual(d["births"][0]["issue_date"], "2010-05-06")
        self.assertEqual(d["births"][0]["qr"]["key_bytes"], "<2 bytes>")
        self.assertEqual(d["split"]["units"][0]["path"], r"C:\x\PASSPORT p01.pdf")
        self.assertEqual(d["docs"][0]["unit"]["path"], r"C:\x\PASSPORT p01.pdf")
        self.assertIsInstance(d["missing_lines"][0], list)
        self.assertEqual(d["notices"][0]["pages"], [1, 1])
        self.assertEqual(d["names_rereads"][0]["crop"], "a.png")
        self.assertEqual(d["tin"]["D04"], {"qr": None, "n": 3, "codes": ["a", "b"]})   # image array dropped
        self.assertEqual(d["pages"][2]["text"], r.pages[2].text)                        # full page text kept
        ev = [f for x in d["docs"] for f in x["findings"] if f["rule"] == "names.mother"][0]["evidence"]
        self.assertEqual(ev["when"], "2001-02-03")
        self.assertEqual(set(d) - {f.name for f in dataclasses.fields(RunResult)}, {"names_rereads", "tin"})

    def test_write_all(self):
        r = fixture()
        paths = REP.write_all(r, self.dir, {"names_rereads": [], "tin": {}})
        self.assertEqual({k: p.name for k, p in paths.items()},
                         {"summary": "summary.txt", "excel": "report.xlsx", "json": "run.json"})
        for p in paths.values():
            self.assertTrue(p.is_file())
            self.assertIn(p, self.writes)
        text = paths["summary"].read_text(encoding="utf-8")
        self.assertTrue(text.splitlines()[-1].startswith(f"Report: {self.dir}"))

    def test_to_jsonable(self):
        self.assertEqual(REP.to_jsonable({(1, 2): float("nan"), "s": {3, 1}, "t": dt.datetime(2026, 1, 2, 3, 4)}),
                         {"(1, 2)": None, "s": [1, 3], "t": "2026-01-02T03:04:00"})


class TestCasesReview(unittest.TestCase):
    def test_review_checklist_from_run_json(self):
        from src.verify.runner.selftest import cases
        r = fixture()
        d = REP.to_jsonable(dataclasses.asdict(r))
        d["names_rereads"] = [{"page": 2, "effect": "refuted", "crop": "c.png"}]
        text = cases.review_text(d, REP.summary_text(r).splitlines())
        self.assertIn("[ ] every FAIL (3):", text)                        # 2 unit FAILs + the NAMES cross FAIL
        self.assertIn("ACADEMIC HSC (p3) pages p3: bot.check_academic", text)
        self.assertIn("[ ] every MISSING line (2):", text)
        self.assertIn("[ ] every ORDER / STOP notice (1):", text)
        self.assertIn("    - ORDER page 3: the e-Apostille for HSC", text)
        self.assertIn("tin.web_address on TIN CERTIFICATE (p4)", text)
        self.assertIn("- DOB: MRZ; trusted=True", text)
        self.assertIn("page 3: refuted | crop c.png", text)
        self.assertTrue(text.splitlines()[0].startswith("REVIEW — B-0123456789abcdef | KLP | bundle | RESULT: FAIL"))


class TestPagesLabel(unittest.TestCase):
    def test_labels(self):
        self.assertEqual(REP.pages_label([4]), "p5")
        self.assertEqual(REP.pages_label([5, 6]), "p6-7")
        self.assertEqual(REP.pages_label([1, 2, 4]), "p2-3,5")
        self.assertEqual(REP.pages_label([11, 12, 13]), "p12-14")


if __name__ == "__main__":
    unittest.main()
