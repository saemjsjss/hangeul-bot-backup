"""Task 6: verdict.py (RUNNER_SPEC 5.10). Made-up data only; the split module is a fake (sys.modules)."""
from __future__ import annotations

import re
import sys
import types
import unittest
from types import SimpleNamespace
from unittest import mock

from src.verify.runner import verdict as V
from src.verify.runner.model import (ERROR, FAIL, FLAG, MISSING, NOT_JUDGED, NOTE, ORDER, OVERALL, PASS, PRESENT,
                                     STOP, DocUnit, Finding, Notice, SplitResult)

SPLIT_NAME = "src.verify.runner.split"
HEADLINE = re.compile(r"^(STOP|FAIL|FLAG|PASS) — \d+ FAIL, \d+ FLAG, \d+ MISSING(, \d+ ORDER)?(, \d+ NOT JUDGED)?$")


def fake_split() -> types.ModuleType:
    m = types.ModuleType(SPLIT_NAME)
    m.SLOTS = ["passport", "photo", "father_id", "mother_id", "birth_cert", "family_cert", "academic", "bank",
               "owner_doc", "tin"]
    m.MISSING_TEXT = {"passport": "PASSPORT MISSING", "photo": "PHOTO MISSING",
                      "father_id": "FATHER NID (OR DEATH CERTIFICATE) MISSING",
                      "mother_id": "MOTHER NID (OR DEATH CERTIFICATE) MISSING",
                      "birth_cert": "BIRTH CERTIFICATE MISSING", "family_cert": "FAMILY CERTIFICATE MISSING",
                      "academic": "ACADEMIC CERTIFICATE MISSING", "bank": "BANK SOLVENCY / STATEMENT MISSING",
                      "owner_doc": "TRADE LICENSE / EMPLOYMENT CERTIFICATE MISSING",
                      "tin": "TIN CERTIFICATE MISSING", "income_tax": "INCOME TAX MISSING"}
    m.SOLVENCY_MISSING_TEXT = "BANK SOLVENCY CERTIFICATE MISSING"
    return m


def F(v: str, rule: str = "x.rule", msg: str = "", pages=()) -> Finding:
    return Finding(verdict=v, rule=rule, message=msg or f"{v.lower()} {rule}", source="test", pages=tuple(pages))


def U(uid: str, key: str, pages, title: str = "", notes=()) -> DocUnit:
    return DocUnit(uid=uid, key=key, fine=key, title=title or key.upper(), pages=list(pages), notes=list(notes))


def R(units, notices=(), missing=(), stop=False, maybe=None) -> SplitResult:
    return SplitResult(units=list(units), notices=list(notices), missing=list(missing), stop=stop,
                       maybe=dict(maybe or {}))


class TestDocVerdict(unittest.TestCase):
    def test_row_semantics(self):
        self.assertEqual(V.doc_verdict([]), PASS)
        self.assertEqual(V.doc_verdict([F(PASS), F(NOTE)]), PASS)                 # NOTE counts as PASS
        self.assertEqual(V.doc_verdict([F(PASS), F(ERROR)]), FLAG)                # ERROR shows as FLAG
        self.assertEqual(V.doc_verdict([F(FLAG), F(ERROR), F(FAIL)]), FAIL)
        self.assertEqual(V.doc_verdict([F(PRESENT), F(PASS), F(NOTE)]), PRESENT)  # PRESENT without FLAG/FAIL
        self.assertEqual(V.doc_verdict([F(PRESENT), F(FLAG)]), FLAG)
        self.assertEqual(V.doc_verdict([F(PRESENT), F(FAIL)]), FAIL)
        self.assertEqual(V.doc_verdict([F(ORDER)]), PASS)                         # lifted, never a level
        self.assertEqual(V.doc_verdict([F("SOMETHING")]), FLAG)                   # unknown: never a silent PASS
        self.assertEqual(V.doc_verdict([F(FAIL)], blocked=True), NOT_JUDGED)

    def test_reasons_order_and_error_prefix(self):
        fs = [F(FLAG, msg="flag one"), F(ERROR, msg="reader broke"), F(FAIL, msg="fail one"), F(PASS, msg="ok"),
              F(NOTE, msg="note"), F(FLAG, msg="flag one"), F(FAIL, msg="fail two")]
        self.assertEqual(V.reasons_of(fs), ["fail one", "fail two", "could not be checked: reader broke",
                                            "flag one"])


class TestMerge(unittest.TestCase):
    def setUp(self):
        self.u1 = U("D01", "passport", [0], notes=[F(NOTE, "identity.mrz_reread")])
        self.u2 = U("D02", "academic", [1, 2], "ACADEMIC HSC")
        self.u3 = U("D03", "academic", [3, 4], "ACADEMIC SSC")
        self.u4 = U("D04", "bank", [5])

    def test_stage_order_and_cross(self):
        m1 = {"D01": [F(PASS, "passport.mrz")], "D02": [F(FAIL, "bot.check_academic")]}
        m2 = {"D02": [F(FLAG, "bank.x")], "": [F(FLAG, "financial.taxpayer_read", "cross only")]}
        names = SimpleNamespace(per_unit={"D02": [F(FAIL, "names.mother")]},
                                summary=[F(FAIL, "names.mother", "mother summary")])
        bundle = [F(FLAG, "page.colour_bundle")]
        chain = [F(PASS, "financial.taxpayer_owner")]
        docs, cross, notices = V.merge(R([self.u4, self.u2, self.u1, self.u3]), m1, m2, names=names,
                                       chain_summary=chain, bundle=bundle, program="KLP")
        self.assertEqual([d.unit.uid for d in docs], ["D01", "D02", "D03", "D04"])     # page order
        d2 = docs[1]
        self.assertEqual([f.rule for f in d2.findings], ["bot.check_academic", "bank.x", "names.mother"])
        self.assertEqual(d2.verdict, FAIL)
        self.assertEqual(docs[0].findings[0].rule, "identity.mrz_reread")               # unit notes first
        self.assertEqual(docs[0].verdict, PASS)
        self.assertEqual([f.rule for f in cross], ["names.mother", "financial.taxpayer_owner", "page.colour_bundle",
                                                    "financial.taxpayer_read"])          # nothing dropped
        self.assertEqual(notices, [])

    def test_order_findings_lifted_non_blocking(self):
        m = {"D02": [F(ORDER, "academic.apostille_order", "put the e-Apostille first", pages=(2,)),
                     F(PASS, "page.colour")]}
        docs, _, notices = V.merge(R([self.u1, self.u2, self.u3]), m)
        self.assertEqual(len(notices), 1)
        n = notices[0]
        self.assertEqual((n.kind, n.pages, n.blocks, n.units), (ORDER, (2, 2), False, ("D02",)))
        d2 = [d for d in docs if d.unit.uid == "D02"][0]
        self.assertNotIn(ORDER, [f.verdict for f in d2.findings])
        self.assertEqual(d2.verdict, PASS)                                              # judged, not blocked

    def test_blocking_order_touches_named_units_only(self):
        blocking = Notice(kind=ORDER, pages=(2, 3), message="document out of its slot", blocks=True,
                          units=("D02",))
        soft = Notice(kind=ORDER, pages=(4, 5), message="transcript before certificate", blocks=False,
                      units=("D03",))
        m = {"D02": [F(FAIL, "bot.check_academic")], "D03": [F(FAIL, "bot.check_academic")]}
        docs, _, notices = V.merge(R([self.u1, self.u2, self.u3, self.u4], notices=[soft, blocking]), m)
        by = {d.unit.uid: d for d in docs}
        self.assertEqual(by["D02"].verdict, NOT_JUDGED)
        self.assertEqual(by["D02"].reasons, ["fix pages 2-3, then check again"])
        self.assertEqual(by["D03"].verdict, FAIL)                                       # non-blocking: judged
        self.assertEqual(by["D01"].verdict, PASS)
        self.assertEqual(by["D04"].verdict, PASS)
        self.assertEqual([n.pages for n in notices], [(2, 3), (4, 5)])                 # page order

    def test_blocking_order_by_pages_when_no_units(self):
        blocking = Notice(kind=ORDER, pages=(6, 6), message="unrecognised page in the middle", blocks=True)
        docs, _, _ = V.merge(R([self.u1, self.u4], notices=[blocking]))
        by = {d.unit.uid: d.verdict for d in docs}
        self.assertEqual(by, {"D01": PASS, "D04": NOT_JUDGED})


class TestMissing(unittest.TestCase):
    def run_missing(self, res, program, bank=()):
        with mock.patch.dict(sys.modules, {SPLIT_NAME: fake_split()}):
            return V.missing_lines(res, program, bank)

    def test_slot_order_solvency_and_death_certificate(self):
        dc = U("D05", "death_cert", [3, 4])
        res = R([dc], missing=["tin", "photo", "birth_cert", "income_tax"], maybe={"father_id": "D05"})
        bank = [SimpleNamespace(solvency_missing=False), SimpleNamespace(solvency_missing=True)]
        out = self.run_missing(res, "KLP", bank)
        self.assertEqual(out, [
            (MISSING, "PHOTO MISSING"), (MISSING, "BIRTH CERTIFICATE MISSING"), (MISSING, "TIN CERTIFICATE MISSING"),
            (MISSING, "BANK SOLVENCY CERTIFICATE MISSING"),
            (FLAG, "FATHER NID (OR DEATH CERTIFICATE) MISSING unless the death certificate on pages 4-5 is the "
                   "father's — check by eye")])

    def test_income_tax_only_for_master(self):
        res = R([], missing=["income_tax", "passport"])
        self.assertEqual(self.run_missing(res, "MASTER"), [(MISSING, "PASSPORT MISSING"),
                                                           (MISSING, "INCOME TAX MISSING")])
        self.assertEqual(self.run_missing(res, "EAP"), [(MISSING, "PASSPORT MISSING")])

    def test_nothing_missing(self):
        self.assertEqual(self.run_missing(R([]), "KLP", [SimpleNamespace(solvency_missing=False)]), [])


class TestOverall(unittest.TestCase):
    def docs(self, *verdicts):
        out = []
        for i, v in enumerate(verdicts):
            u = U(f"D{i + 1:02d}", "academic", [i])
            fs = [] if v in (PASS, NOT_JUDGED) else [F(v)]
            out.append(SimpleNamespace(unit=u, findings=fs, verdict=v, reasons=[]))
        return out

    def check(self, docs, missing=(), res=None, cross=(), notices=()):
        o, h = V.overall(docs, list(missing), res or R([]), list(cross), list(notices))
        self.assertIn(o, OVERALL)
        self.assertRegex(h, HEADLINE)
        self.assertTrue(h.startswith(o + " — "))
        return o, h

    def test_labels(self):
        self.assertEqual(self.check(self.docs(PASS, PASS))[0], PASS)
        self.assertEqual(self.check(self.docs(PASS, FLAG))[0], FLAG)
        self.assertEqual(self.check(self.docs(FAIL, FLAG))[0], FAIL)
        self.assertEqual(self.check(self.docs(PASS, NOT_JUDGED))[0], FLAG)

    def test_missing_alone_makes_flag_and_fail_outranks(self):
        miss = [(MISSING, "PHOTO MISSING"), (MISSING, "BIRTH CERTIFICATE MISSING")]
        o, h = self.check(self.docs(PASS, PASS), miss)
        self.assertEqual((o, V.MISSING_MAKES), (FLAG, FLAG))
        self.assertEqual(h, "FLAG — 0 FAIL, 0 FLAG, 2 MISSING")
        o, h = self.check(self.docs(PASS, FAIL, FAIL), miss)
        self.assertEqual(h, "FAIL — 2 FAIL, 0 FLAG, 2 MISSING")

    def test_present_never_changes_overall(self):
        self.assertEqual(self.check(self.docs(PASS, PRESENT, PRESENT))[0], PASS)
        self.assertEqual(self.check(self.docs(FLAG, PRESENT))[0], FLAG)

    def test_order_notice_makes_flag(self):
        n = Notice(kind=ORDER, pages=(3, 4), message="academic levels not newest first")
        o, h = self.check(self.docs(PASS), notices=[n])
        self.assertEqual(h, "FLAG — 0 FAIL, 0 FLAG, 0 MISSING, 1 ORDER")
        hint = Notice(kind="HINT", pages=(3, 3), message="page 3 reads partly like it")
        self.assertEqual(self.check(self.docs(PASS), notices=[hint])[0], PASS)

    def test_cross_findings(self):
        docs = self.docs(PASS)
        self.assertEqual(self.check(docs, cross=[F(FAIL, "financial.taxpayer_holder")])[1],
                         "FAIL — 1 FAIL, 0 FLAG, 0 MISSING")
        self.assertEqual(self.check(docs, cross=[F(ERROR, "page.colour_bundle")])[0], FLAG)
        f = F(FAIL, "names.mother")
        docs2 = self.docs(FAIL)
        docs2[0].findings = [f]
        self.assertEqual(self.check(docs2, cross=[f])[1], "FAIL — 1 FAIL, 0 FLAG, 0 MISSING")   # not twice

    def test_conditional_missing_line_counts_as_flag(self):
        o, h = self.check(self.docs(PASS), [(FLAG, "MOTHER NID (OR DEATH CERTIFICATE) MISSING unless ...")])
        self.assertEqual(h, "FLAG — 0 FAIL, 1 FLAG, 0 MISSING")

    def test_stop(self):
        n = Notice(kind=STOP, pages=(2, 5), message="whose NID is this")
        o, h = V.overall([], [], R([], notices=[n], stop=True), [], [n])
        self.assertEqual(o, STOP)
        self.assertTrue(h.startswith("STOP — 1 STOP notice"))

    def test_not_judged_counted(self):
        self.assertEqual(self.check(self.docs(PASS, NOT_JUDGED),
                                    notices=[Notice(ORDER, (2, 2), "x", True, ("D02",))])[1],
                         "FLAG — 0 FAIL, 0 FLAG, 0 MISSING, 1 ORDER, 1 NOT JUDGED")


class TestHelpers(unittest.TestCase):
    def test_chain_summary_worst_per_rule(self):
        a = {"D01": [F(PASS, "financial.taxpayer_owner"), F(FLAG, "financial.taxpayer_holder")],
             "D02": [F(PASS, "financial.taxpayer_owner"), F(FAIL, "financial.taxpayer_holder")]}
        b = {"D03": [F(PASS, "bank.holder_parent"), F(PASS, "bank.usd")]}
        out = V.chain_summary(a, b, prefixes=("financial.", "bank.holder"))
        self.assertEqual([(f.rule, f.verdict) for f in out], [("financial.taxpayer_owner", PASS),
                                                              ("financial.taxpayer_holder", FAIL),
                                                              ("bank.holder_parent", PASS)])

    def test_split_only(self):
        u = U("D01", "passport", [0], notes=[F(FLAG, "split.role_position")])
        docs, o, h = V.split_only(R([u]), [(MISSING, "PHOTO MISSING")], [])
        self.assertEqual((o, docs[0].verdict), (NOT_JUDGED, NOT_JUDGED))
        self.assertEqual(h, "NOT JUDGED — split only: 1 document, 1 MISSING, 0 ORDER")

    def test_page_words(self):
        self.assertEqual(V.pages_word(3, 3), "page 3")
        self.assertEqual(V.pages_word(3, 5), "pages 3-5")


if __name__ == "__main__":
    unittest.main()
