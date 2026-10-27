"""Task 4 tests for tin.py (RUNNER_SPEC 5.9.3; section 11 Task 4): the lab's 32 test_tin_qr.py checks ported
(the name-agreement cases rewritten to strict kinds, A1), plus the strict QR-vs-print judging, the decode on a
synthetic QR page, tin_findings, tin_page_types, web_hosts and tax_office.

Made-up names and numbers only ("Abcd Efgh", "123456789012"); "Md" is the abbreviation the owner's rules talk
about. Temporary files go under private-runner\\tmp. No personal data.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.verify.runner import botimport as B
from src.verify.runner import financial as F
from src.verify.runner import strict as S
from src.verify.runner import tin as Q
from src.verify.runner.model import FAIL, FLAG, NOTE, PASS, DocUnit, PageRead, PageType

TIN_QR = ("Taxpayer's Name : Md. Abcd Efgh\nDOB : 01/02/1970\nFather's Name : Late Ijkl Mnop\n"
          "TIN : 123456789012\nDate : 03/04/2019\nZone : 10, Dhaka\nCircle : Circle-205 (Salary)")
TIN_QR_GLUED = TIN_QR.replace("\nZone", "Zone")            # 25 of 205 real payloads look like this
RET_QR = "TIN: 123456789012, Reference No: 0000000000000"
PRINTED = ("Certificate TIN : 123456789012 This is to Certify that Md. Abcd Efgh is a Registered Taxpayer of "
           "National Board of Revenue under the jurisdiction of Taxes Circle-205 (Salary) Taxes Zone 10, Dhaka. "
           "Taxpayer's Particulars : 1) Name Md. Abcd Efgh 2) Father's Name Late Ijkl Mnop "
           "3) Mother's Name Qrst Uvwx 4) a) Current Address Road 1")


def tmp_dir() -> tempfile.TemporaryDirectory:
    root = B.PRIVATE / "tmp"
    root.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=root)


def qr_pdf(path: Path, payload: str, pages: int = 1, text: str = "") -> Path:
    """A synthetic PDF whose first page carries a QR of `payload` (cv2's encoder) and a little text."""
    import cv2
    import numpy as np
    import pymupdf
    m = cv2.QRCodeEncoder.create().encode(payload)
    m = cv2.resize(m, None, fx=8, fy=8, interpolation=cv2.INTER_NEAREST)
    m = cv2.copyMakeBorder(m, 32, 32, 32, 32, cv2.BORDER_CONSTANT, value=255)
    ok, png = cv2.imencode(".png", np.ascontiguousarray(m))
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 72), text or "made-up certificate page")
        if i == 0:
            page.insert_image(pymupdf.Rect(380, 60, 540, 220), stream=png.tobytes())
    doc.save(str(path))
    doc.close()
    return path


class TestPortedLab(unittest.TestCase):
    """The 32 checks of the lab's test_tin_qr.py (tin_qr.py and fin_names.py)."""

    def setUp(self):
        self.q = Q.parse_tin_qr(TIN_QR)

    # 1-5 the QR payload
    def test_01_qr_name(self):
        self.assertEqual(self.q["name"], "Md. Abcd Efgh")

    def test_02_qr_father(self):
        self.assertEqual(self.q["father"], "Late Ijkl Mnop")

    def test_03_qr_tin(self):
        self.assertEqual(self.q["tin"], "123456789012")

    def test_04_qr_zone(self):
        self.assertEqual(self.q["zone"], "10, Dhaka")

    def test_05_qr_circle(self):
        self.assertEqual(self.q["circle"], "Circle-205 (Salary)")

    def test_06_glued_date_zone(self):
        g = Q.parse_tin_qr(TIN_QR_GLUED)
        self.assertEqual((g["date"], g["zone"]), ("03/04/2019", "10, Dhaka"))

    def test_07_return_qr_is_not_a_tin_qr(self):
        self.assertIsNone(Q.parse_tin_qr(RET_QR))

    def test_08_return_qr_still_stops_the_ladder(self):
        # its parser is removed (critique 20); the payload still counts as meaningful (8+ characters)
        self.assertFalse(hasattr(Q, "parse_return_qr"))
        self.assertTrue(Q.meaningful([RET_QR]))

    # 9-13 the printed certificate
    def test_09_printed_tin(self):
        self.assertEqual(Q.printed_tin_fields(PRINTED).get("tin"), "123456789012")

    def test_10_printed_certify_name(self):
        self.assertEqual(Q.printed_tin_fields(PRINTED).get("certify_name"), "Md. Abcd Efgh")

    def test_11_printed_name(self):
        self.assertEqual(Q.printed_tin_fields(PRINTED).get("name"), "Md. Abcd Efgh")

    def test_12_printed_father(self):
        self.assertEqual(Q.printed_tin_fields(PRINTED).get("father"), "Late Ijkl Mnop")

    def test_12b_printed_father_before_a_stray_mark(self):      # integration, test 1: "... @ 3) Mother's Name"
        t = ("Certificate TIN: 123456789012 Taxpayer's Particulars 1) Name Abcd Efgh 2) Father's Name Ijkl Mnop @ "
             "3) Mother's Name Qrst Uvwx 4.a) Current Address Road 1")
        self.assertEqual(Q.printed_tin_fields(t).get("father"), "Ijkl Mnop")

    def test_13_compare(self):
        c = Q.compare(self.q, Q.printed_tin_fields(PRINTED), PRINTED)
        self.assertEqual((c["tin"], c["name"], c["father"]), ("EQUAL", "EQUAL", "EQUAL"))

    # 14-18 OCR noise is never a PASS (strict kinds)
    def test_14_ocr_letters(self):
        self.assertEqual(Q.compare({"name": "Abcd Efgh"}, {"name": "Abed Efgh"})["name"], "LETTERS")

    def test_15_ocr_digits(self):
        self.assertEqual(Q.tin_agreement("123456789012", "123456789072", ""), "OCR_DIGITS")  # pii-scan: placeholder

    def test_16_noise_is_flag(self):
        v = Q.verdicts(self.q, {"tin": "OCR_DIGITS", "name": "LETTERS"}, True)
        self.assertEqual([x[0] for x in v if x[2] in ("tin.qr_tin", "tin.qr_name")], [FLAG, FLAG])

    def test_17_altered_tin_is_fail(self):
        v = Q.verdicts(self.q, {"tin": "DIFFERENT", "name": "EQUAL"}, True)
        self.assertEqual([x[0] for x in v if x[2] == "tin.qr_tin"], [FAIL])

    def test_18_unread_qr_is_flag(self):
        self.assertEqual(Q.verdicts(None, None, True)[0][:3:2], (FLAG, "tin.qr"))

    # 19-21 strict names (A1): honorifics and initials count; nothing fuzzy
    def test_19_dropped_md_is_not_the_same(self):
        self.assertEqual(S.compare("MD. ABCD EFGH", "Abcd Efgh", dots=False), "SUBSET")

    def test_20_one_letter_off_is_not_the_same(self):
        self.assertNotEqual(S.compare("Abcd Efgh", "Abcd Efgk", dots=False), "EQUAL")

    def test_21_order_differs(self):
        self.assertEqual(S.compare("Abcd Efgh", "Efgh Abcd", dots=False), "ORDER")

    # 22-23 QR first, printed as the cross-check
    def test_22_tin_taxpayer_source_qr(self):
        tt = F.tin_taxpayer(PRINTED, [TIN_QR])
        self.assertEqual((tt["source"], tt["agree"]), ("QR", "EQUAL"))

    def test_23_tin_taxpayer_printed_fallback(self):
        self.assertEqual(F.tin_taxpayer(PRINTED, [])["source"], "PRINTED")

    # 24-29 employee: never the son-of clause, the parents' fields or the signatory
    EMP = [
        ("This is to certify that Mr. Abcd Efgh, son of Late Ijkl Mnop, has been working as Officer since 2019. "
         "Sincerely, Qrst Uvwx, Managing Director", "Mr. Abcd Efgh"),
        ("This is to certify that Md. Abcd Efgh (Employee ID # 123), S/O: Mr. Md. Ijkl Mnop and Qrst, has been "
         "working in our organization", "Md. Abcd Efgh"),
        ("This is to Certify that Mr: Abcd Efgh, Father Name Ijkl Mnop, Mother Name Qrst is working as Assistant "
         "Teacher", "Mr Abcd Efgh"),
        ("This is to certify Md Abcd Efgh Fathers Name: Late Ijkl Mnop, Mother Name: Mst Qrst; is working",
         "Md Abcd Efgh"),
        ("This is to Certify that Abcd Efgh is a Registered Taxpayer of National Board of Revenue. "
         "Bank company officer", ""),
        ("Certified True Copy Abcd Efgh Company", ""),
    ]

    def test_24_employee_0(self):
        self.assertEqual(F.employee_name(self.EMP[0][0]), self.EMP[0][1])

    def test_25_employee_1(self):
        self.assertEqual(F.employee_name(self.EMP[1][0]), self.EMP[1][1])

    def test_26_employee_2(self):
        self.assertEqual(F.employee_name(self.EMP[2][0]), self.EMP[2][1])

    def test_27_employee_3(self):
        self.assertEqual(F.employee_name(self.EMP[3][0]), self.EMP[3][1])

    def test_28_employee_4(self):
        self.assertEqual(F.employee_name(self.EMP[4][0]), self.EMP[4][1])

    def test_29_employee_5(self):
        self.assertEqual(F.employee_name(self.EMP[5][0]), self.EMP[5][1])

    # 30-32 the chain: PASS only on equal names, FAIL only on a confirmed uncovered difference
    TIN_U = DocUnit("D08", "trade_license", "tin_certificate", "TIN CERTIFICATE", [10])
    OWN_U = DocUnit("D09", "trade_license", "trade_license", "TRADE LICENSE", [11, 12])
    BANK_U = DocUnit("D07", "bank", "bank", "BANK STATEMENT", [8, 9])

    def chain(self, owner_text, holder_name, covered=None, confirm=None):
        from src.verify.runner.bank import Holder
        from src.verify.runner.model import Identity, NameRef
        h = Holder(holder_name, "solvency:certify", 0) if holder_name else None
        out = F.chain_findings({"name": "Abcd Efgh", "source": "QR", "qr_page": 10}, self.TIN_U, [self.OWN_U],
                               {"D09": owner_text}, h, self.BANK_U,
                               Identity(student=NameRef(), father=NameRef(), mother=NameRef()),
                               covered or (lambda a, b: False), confirm or (lambda i, v: "unclear"))
        return {f.rule: f.verdict for f in out.get("D08", [])}

    def test_30_findings_all_pass(self):
        got = self.chain(["Proprietor's Name: Abcd Efgh Address: Road 1", "Proprietor : Abcd Efgh"], "Abcd Efgh")
        self.assertEqual(got, {"financial.taxpayer_owner": PASS, "financial.taxpayer_holder": PASS})

    def test_31_findings_fail_and_flag(self):
        # the owner "Ijkl Mnop" is printed on two pages (confirmed): FAIL; the holder one letter off, unconfirmed: FLAG
        got = self.chain(["Proprietor's Name: Ijkl Mnop Address: Road 1", "Proprietor : Ijkl Mnop"], "Abcd Efgk")
        self.assertEqual(got, {"financial.taxpayer_owner": FAIL, "financial.taxpayer_holder": FLAG})

    def test_32_affidavit_covers_it(self):
        got = self.chain(["Proprietor's Name: Ijkl Mnop Address: Road 1", "Proprietor : Ijkl Mnop"], "Abcd Efgh",
                         covered=lambda a, b: {S.letters(a), S.letters(b)} == {"ABCDEFGH", "IJKLMNOP"})
        self.assertEqual(got["financial.taxpayer_owner"], NOTE)


class TestStrictQrName(unittest.TestCase):
    """critique 2: the QR-vs-print name check is strict; a confirmed difference is FAIL "may be altered"."""

    def variant(self, name="Md. Abcd Efgx", certify=None):
        certify = name if certify is None else certify
        return PRINTED.replace("Certify that Md. Abcd Efgh", f"Certify that {certify}") \
                      .replace("1) Name Md. Abcd Efgh", f"1) Name {name}")

    def run_check(self, text, page_texts=None, run_pages=None, confirm=None, pages=1):
        with tmp_dir() as d:
            pdf = qr_pdf(Path(d) / "TIN CERTIFICATE p11.pdf", TIN_QR, pages=pages)
            return Q.check_tin_certificate(pdf, text, ["tin"] * pages, confirm=confirm, page_texts=page_texts,
                                           run_pages=run_pages)

    def by_rule(self, res):
        return {f["rule"]: f["verdict"] for f in res["findings"]}

    def test_synthetic_qr_is_read_and_equal(self):
        res = self.run_check(PRINTED)
        self.assertEqual(res["qr"]["tin"], "123456789012")
        self.assertEqual(res["qr_page"], 0)
        self.assertEqual(self.by_rule(res), {"tin.qr": PASS, "tin.qr_tin": PASS, "tin.qr_name": PASS,
                                             "tin.qr_father": PASS})

    def test_md_dropped_in_print_never_passes(self):
        # "MD <A> <B>" (QR) vs "<A> <B>" (print), unconfirmed -> FLAG; confirmed by the re-read -> FAIL
        text = self.variant("Abcd Efgh")
        self.assertEqual(self.by_rule(self.run_check(text))["tin.qr_name"], FLAG)
        res = self.run_check(text, page_texts=[text], run_pages=[10], confirm=lambda i, v: "confirmed")
        self.assertEqual(self.by_rule(res)["tin.qr_name"], FAIL)
        self.assertIn("may be altered", [f for f in res["findings"] if f["rule"] == "tin.qr_name"][0]["message"])

    def test_confirmation_needs_run_pages(self):
        calls = []
        text = self.variant()
        res = self.run_check(text, confirm=lambda i, v: calls.append((i, v)) or "confirmed")
        self.assertEqual(self.by_rule(res)["tin.qr_name"], FLAG)
        self.assertEqual(calls, [])                                   # no page mapping: never asked
        res = self.run_check(text, page_texts=[text], run_pages=[10], confirm=lambda i, v: calls.append((i, v)) or "unclear")
        self.assertEqual(self.by_rule(res)["tin.qr_name"], FLAG)
        self.assertEqual(calls, [(10, "Md. Abcd Efgx")])              # asked on the RUN page

    def test_refuted_is_flag_probably_ocr(self):
        text = self.variant()
        res = self.run_check(text, page_texts=[text], run_pages=[10], confirm=lambda i, v: "refuted")
        row = [f for f in res["findings"] if f["rule"] == "tin.qr_name"][0]
        self.assertEqual(row["verdict"], FLAG)
        self.assertIn("probably OCR", row["message"])

    def test_two_pages_confirm(self):
        text = self.variant()
        res = self.run_check(text + "\n" + text, page_texts=[text, text], run_pages=[10, 11], pages=2)
        self.assertEqual(self.by_rule(res)["tin.qr_name"], FAIL)

    def test_mixed_reads_cannot_be_confirmed(self):
        # the certify line prints the QR's spelling, the "1) Name" row another: FLAG, never FAIL
        text = self.variant("Md. Abcd Efgx", certify="Md. Abcd Efgh")
        res = self.run_check(text, page_texts=[text], run_pages=[10], confirm=lambda i, v: "confirmed")
        self.assertTrue(res["compare"]["name_mixed"])
        self.assertEqual(self.by_rule(res)["tin.qr_name"], FLAG)

    def test_unreadable_file_is_error_not_crash(self):
        with tmp_dir() as d:
            res = Q.check_tin_certificate(Path(d) / "missing.pdf", PRINTED)
        self.assertEqual([f["verdict"] for f in res["findings"]], ["ERROR"])


class TestFindingsMapping(unittest.TestCase):
    UNIT = DocUnit("D08", "trade_license", "tin_certificate", "TIN CERTIFICATE", [10, 11])

    def pages(self, *texts):
        out = [PageRead(i, "bundle.pdf", i, "", "ocr", 0, False, 0.0, False) for i in range(10)]
        out += [PageRead(10 + k, "bundle.pdf", 10 + k, t, "ocr", len(t), False, 0.0, False) for k, t in enumerate(texts)]
        return out

    def result(self, **kw):
        res = {"findings": [{"verdict": PASS, "rule": "tin.qr", "message": "read", "page": 1},
                            {"verdict": FLAG, "rule": "tin.qr_name", "message": "differs", "page": 0}],
               "all_codes": [], "qr_page": 1, "page_types": ["tin", "tin"]}
        res.update(kw)
        return res

    def test_rules_and_run_pages(self):
        out = Q.tin_findings(self.result(), self.UNIT, self.pages("a", "b"))
        self.assertEqual([(f.rule, f.verdict, f.pages) for f in out], [("tin.qr", PASS, (12,)), ("tin.qr_name", FLAG, (11,))])
        self.assertTrue(all(f.source == "runner:tin" for f in out))

    def test_web_address_gov_pass(self):
        out = Q.tin_findings(self.result(), self.UNIT, self.pages("https://secure.incometax.gov.bd/TINHome 1/1", "x"))
        wa = [f for f in out if f.rule == "tin.web_address"]
        self.assertEqual([(f.verdict, f.pages) for f in wa], [(PASS, (11,))])

    def test_web_address_look_alike_flag(self):
        for host in ("https://incometax.gov.bd.sample-mirror.top/x", "www.sample-etin.top/print",
                     "https://secure-incometax-gov-bd.com/a"):
            out = Q.tin_findings(self.result(), self.UNIT, self.pages("TIN certificate", host))
            wa = [f for f in out if f.rule == "tin.web_address"]
            self.assertEqual([f.verdict for f in wa], [FLAG], host)
            self.assertIn("verify the TIN on the NBR site", wa[0].message)
            self.assertEqual(wa[0].pages, (12,))

    def test_web_address_in_qr_payload(self):
        out = Q.tin_findings(self.result(all_codes=["https://sample-tin.xyz/verify?id=1"]), self.UNIT,
                             self.pages("a", "b"))
        self.assertEqual([f.verdict for f in out if f.rule == "tin.web_address"], [FLAG])

    def test_no_host_no_row(self):
        out = Q.tin_findings(self.result(), self.UNIT, self.pages("no address", "none here either"))
        self.assertEqual([f for f in out if f.rule == "tin.web_address"], [])

    def test_only_tin_pages_are_searched(self):
        unit = DocUnit("D04", "trade_license", "trade_license", "TRADE LICENSE", [10, 11])
        out = Q.tin_findings(self.result(page_types=["", "tin"]), unit, self.pages("www.sample-site.top", "TIN page"))
        self.assertEqual([f for f in out if f.rule == "tin.web_address"], [])


class TestHelpers(unittest.TestCase):
    def test_web_hosts(self):
        self.assertEqual(Q.web_hosts("printed from https://secure.incometax.gov.bd/x and www.sample-tin.top/abc", []),
                         ["secure.incometax.gov.bd", "sample-tin.top"])
        self.assertEqual(Q.web_hosts("ocr: https://incometax.gov,bd/x", []), ["incometax.gov.bd"])
        self.assertEqual(Q.web_hosts("no web address, only e-mail sample@mail and 1.2.3", []), [])
        # OCR reads the slashes as I / l and glues the path onto the last label (test 1's footer)
        self.assertEqual(Q.web_hosts("Notarized https:Ilsample99.topluserlaccount No", []), ["sample99.top"])
        self.assertEqual(Q.web_hosts("https:Ilsecure.incometax.gov.bdlTINHome", []), ["secure.incometax.gov.bd"])
        self.assertEqual(Q.web_hosts("https:llsecure.incometax.gov.bdTINHome", []), ["secure.incometax.gov.bd"])
        self.assertEqual(Q.web_hosts("www.sample.comluser", []), ["sample.com"])
        self.assertTrue(Q.is_gov_host("secure.incometax.gov.bd"))
        self.assertFalse(Q.is_gov_host("incometax.gov.bd.sample.top"))
        self.assertFalse(Q.is_gov_host("samplegov.bd"))

    def test_tax_office(self):
        self.assertEqual(Q.tax_office("Taxes Circle-205 (Salary) Taxes Zone 10, Dhaka"), {"circle": "205", "zone": "10"})
        self.assertEqual(Q.tax_office("Circle : Circle-007 Zone No. 03"), {"circle": "7", "zone": "3"})
        self.assertEqual(Q.tax_office("nothing here"), {"circle": "", "zone": ""})
        self.assertEqual(Q.tax_office("Circle-2O5 then circle 31"), {"circle": "31", "zone": ""})   # a broken read is skipped

    def test_tin_page_types(self):
        types = [PageType(i, b, 1.0, {}, False, None, "", [], [], []) for i, b in
                 enumerate(["passport", "tin_certificate", "trade_license", "other"])]
        self.assertEqual(Q.tin_page_types(DocUnit("D1", "trade_license", "tin_certificate", "TIN", [2, 3]), types),
                         ["tin", "tin"])
        self.assertEqual(Q.tin_page_types(DocUnit("D2", "trade_license", "trade_license", "TL", [1, 2, 3]), types),
                         ["tin", "", ""])
        self.assertEqual(Q.tin_page_types(DocUnit("D3", "trade_license", "trade_license", "TL", [2, 3],
                                                  sub=[(1, 1, "tin_certificate")]), types), ["", "tin"])


if __name__ == "__main__":
    unittest.main()
