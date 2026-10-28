"""language.py on MADE-UP certificate text (the lab self-test, ported; RUNNER_SPEC Task 5).

Every name is a placeholder ("SAMPLE", "MD TEST PERSON", "RIVER OTHER"); numbers are built at run time so
that no 10/13/17-digit run or passport-like token stands in the source. No personal data.
"""
from __future__ import annotations

import datetime as dt
import unittest

from src.verify.runner import language as L
from src.verify.runner.model import FAIL, FLAG, NOTE, PASS, PRESENT

TODAY = dt.date(2026, 10, 27)
PPNO = "A" + "0" + "1234567"                      # a made-up 9-character passport number
PP = {"surname": "SAMPLE", "given_names": "MD TEST PERSON", "passport_no": PPNO,
      "dob": dt.date(2004, 5, 6), "name_trusted": True, "name_verified": True}


def trf(family="SAMPLE", first="MD TEST PERSON", cid=PPNO, dob="06/05/2004", date="12/SEP/2025",
        bands=("6.5", "7.0", "6.0", "6.5"), overall="6.5", cefr="B2", number="25BD123456SAMM001A",
        cand="123456", centre="BD001", layout="interleaved", module="ACADEMIC"):
    if layout == "interleaved":
        res = (f"Test Results Listening {bands[0]} Reading {bands[1]} Writing {bands[2]} Speaking {bands[3]} "
               f"Overall Band Score {overall} CEFR Level {cefr} Administrator Comments")
    else:
        res = (f"Test Results Listening Reading Writing Speaking Overall CEFR {' '.join(bands)} Band {overall} "
               f"Level {cefr} Score Administrator Comments")
    return (f"IELTS Test Report Form {module} NOTE ... Centre Number {centre} Date {date} Candidate Number {cand} "
            f"Candidate Details Family Name {family} First Name(s) {first} Candidate ID {cid} Date of Birth {dob} "
            f"Sex (M/F) M Scheme Code Private Candidate {res} Validation stamp Administrator's Signature "
            f"Date 25/09/2025 Test Report Form Number {number} ielts.org/verify")


def verdicts(rep):
    return {c: v for v, c, _ in rep["findings"]}


TOPIK = ("TOPIK SCORE REPORT 1111-2222-3333-4444 Name Date of Birth (Male) 2004/05/06 MD TEST PERSON SAMPLE "
         "M BANGLADESH yyyy/mm/dd Registration No. " + "1234567" + "890123" + " Test No./Test Date Valid Until "
         "TOPIK I 105/2026/04/11 2028/05/28 Listening 75 / 100 140 / 200 Reading 65 / 100")
TOEFL = ("TOEFL iBT Test Taker Score Report Registration Number 0000 0000 0012 3456 Test Date: Sep 12, 2025 "
         "Date of Birth: 06/05/2004 Reading 25 Listening 26 Speaking 22 Writing 24 Total Score 97")


class TestPorted(unittest.TestCase):
    def test_overall_band_rounding(self):
        cases = [((6.5, 6.5, 5.0, 7.0), 6.5), ((6.0, 6.0, 6.0, 7.0), 6.5), ((6.5, 6.5, 6.5, 7.0), 6.5),
                 ((6.5, 6.5, 7.0, 7.0), 7.0), ((6.5, 7.0, 7.0, 7.0), 7.0), ((6.0, 6.0, 6.0, 6.5), 6.0),
                 ((6.0, 6.0, 6.5, 6.5), 6.5), ((5.5, 6.0, 6.0, 6.0), 6.0), ((4.0, 4.5, 5.0, 4.0), 4.5),
                 ((9.0, 9.0, 9.0, 8.5), 9.0), ((0.0, 0.0, 0.0, 0.5), 0.0)]
        for bands, want in cases:
            self.assertEqual(L.ielts_overall(*bands), want, bands)

    def test_clean_trf_passes(self):
        r = L.check_language(trf(), PP, "MASTER", TODAY)
        v = verdicts(r)
        self.assertEqual(r["test"], "IELTS")
        for check in ("name", "dob", "id", "arithmetic", "validity", "trf_name", "trf_year", "trf_centre",
                      "trf_candidate"):
            self.assertEqual(v[check], PASS, (check, r["findings"]))
        self.assertEqual(v["minimum"], NOTE)
        self.assertEqual(r["verdict"], PASS, r["findings"])
        self.assertEqual(verdicts(L.check_language(trf(layout="grouped"), PP, "MASTER", TODAY))["arithmetic"], PASS)

    def test_retake_note_keeps_validity_from_the_original_date(self):
        osr = trf().replace("Test Results", "This Test Report Form includes the result of a Speaking retake "
                                            "Test Results") + (
            " The Retake was taken at Centre Number BD001, as Candidate Number 654321 on Test Date 2 October 2025")
        r = L.check_language(osr, PP, "MASTER", TODAY)
        self.assertEqual([v for v, c, _ in r["findings"] if c == "retake"], [NOTE])

    def test_band_reading_and_module(self):
        r = L.check_language(trf(bands=("6.5", "6.0", "5.,5", "5.0"), overall="6.0", cefr="B2"), PP, "MASTER", TODAY)
        self.assertEqual(r["fields"]["bands"], [6.5, 6.0, 5.5, 5.0])
        self.assertEqual(verdicts(r)["arithmetic"], PASS)
        self.assertEqual(L.parse_ielts("Test Report Form | IELTS Academic NOTE ...")["module"], "A")
        self.assertEqual(L.parse_ielts("Test Report Form GENERAL TRAINING NOTE ... the ACADEMIC Reading")["module"], "G")
        self.assertEqual(verdicts(L.check_language(trf(overall="7.5", cefr="C1"), PP, "MASTER", TODAY))["arithmetic"],
                         FLAG)

    def test_expired_and_repaired_dates(self):
        r = L.check_language(trf(date="18/JAN/2024", number="23BD123456SAMM001A"), PP, "MASTER", TODAY)
        self.assertEqual(verdicts(r)["validity"], FAIL)
        self.assertEqual(verdicts(r)["trf_year"], PASS)
        r = L.check_language(trf(date="1OIJAN/2026", number="25BD123456SAMM001A"), PP, "MASTER", TODAY)
        self.assertEqual(r["fields"]["test_date"], dt.date(2026, 1, 10))
        self.assertTrue(r["fields"]["test_date_repaired"])

    def test_passport_name_verified(self):
        viz = "PASSPORT Surname SAMPLE Given Names MD TEST PERSON P<BGDSAMPLE<<MD<TEST<PERSON<<<<<<<<<<<<<<<<<<<<<<"
        self.assertTrue(L.passport_name_verified(viz, "SAMPLE", "MD TEST PERSON"))
        self.assertFalse(L.passport_name_verified(viz.replace("Given Names MD TEST PERSON", "Given Names MD TEST"),
                                                  "SAMPLE", "MD TEST PERSON"))

    def test_topik(self):
        r = L.check_language(TOPIK, PP, "KLP", TODAY)
        v = verdicts(r)
        self.assertEqual(r["test"], "TOPIK")
        for check in ("arithmetic", "validity", "name", "dob"):
            self.assertEqual(v[check], PASS, (check, r["findings"]))
        self.assertEqual(r["fields"]["level_from_total"], 2)

    def test_file_with_two_certificates(self):
        both = L.check_language_file(trf() + "\f" + TOPIK, PP, "KLP", TODAY)
        self.assertEqual([c["test"] for c in both["certificates"]], ["IELTS", "TOPIK"])

    def test_toefl(self):
        r = L.check_language(TOEFL, PP, "MASTER", TODAY)
        self.assertEqual(r["test"], "TOEFL")
        self.assertEqual(verdicts(r)["arithmetic"], PASS, r["findings"])
        toefl6 = TOEFL.replace("Reading 25", "Reading 4.5").replace("Listening 26", "Listening 5").replace(
            "Speaking 22", "Speaking 4").replace("Writing 24", "Writing 4.5").replace("Total Score 97", "Total Score 4.5")
        self.assertEqual(verdicts(L.check_language(toefl6, PP, "MASTER", TODAY))["arithmetic"], PASS)
        itp = ("TOEFL ITP Score Report Test Date: 12 SEP 2025 Listening Comprehension 52 Structure and Written "
               "Expression 55 Reading Comprehension 50 Total Score 523")
        r = L.check_language(itp, PP, "MASTER", TODAY)
        self.assertEqual(r["fields"]["variant"], "ITP")
        self.assertEqual(verdicts(r)["arithmetic"], PASS)
        self.assertEqual(verdicts(L.check_language(itp.replace("523", "530"), PP, "MASTER", TODAY))["arithmetic"], FLAG)

    def test_id_number_checks(self):
        nid = "12345" + "67890"
        why = [w for _, c, w in L.check_language(trf(cid=nid), PP, "KLP", TODAY)["findings"] if c == "id"][0]
        self.assertIn("NID", why)


class TestStrictNames(unittest.TestCase):
    """A1 / critique 14: one strict policy, the affidavit cover from names.covered."""

    def test_confirmed_different_name_fails_and_covered_is_note(self):
        other = trf(family="SAMPLE", first="RIVER OTHER", number="25BD123456SAMR001A")
        self.assertEqual(verdicts(L.check_language(other, PP, "MASTER", TODAY))["name"], FAIL)
        seen = []

        def covered(name):
            seen.append(name)
            return True
        self.assertEqual(verdicts(L.check_language(other, PP, "MASTER", TODAY, covered))["name"], NOTE)
        self.assertEqual(seen, ["SAMPLE RIVER OTHER"])            # the certificate's name AS WRITTEN
        # an affidavit that does not cover it does not help
        self.assertEqual(verdicts(L.check_language(other, PP, "MASTER", TODAY, lambda n: False))["name"], FAIL)

    def test_unconfirmed_passport_name_is_flag(self):
        other = trf(family="SAMPLE", first="RIVER OTHER", number="25BD123456SAMR001A")
        self.assertEqual(verdicts(L.check_language(other, dict(PP, name_verified=False), "MASTER", TODAY))["name"],
                         FLAG)

    def test_unconfirmed_certificate_name_is_flag(self):
        # a different later word while the TRF letters are the passport's: not confirmed -> FLAG, never PASS
        other2 = trf(family="SAMPLE", first="MD OTHER PERSON", number="25BD123456SAMM001A")
        self.assertEqual(verdicts(L.check_language(other2, PP, "MASTER", TODAY))["name"], FLAG)
        near = trf(first="MD TEST PERSN")                         # one letter off: FLAG, never PASS
        self.assertEqual(verdicts(L.check_language(near, PP, "MASTER", TODAY))["name"], FLAG)

    def test_dropped_md_is_never_pass(self):
        # "MD <A> <B>" vs "<A> <B>": a difference (A1); the TRF spells the certificate's name -> FAIL
        hon = trf(first="TEST PERSON", number="25BD123456SAMT001A")
        self.assertEqual(verdicts(L.check_language(hon, PP, "MASTER", TODAY))["name"], FAIL)
        # without the TRF's corroboration it is FLAG; it is never PASS
        hon2 = trf(first="TEST PERSON", number="25BD123456SAMM001A")
        self.assertEqual(verdicts(L.check_language(hon2, PP, "MASTER", TODAY))["name"], FLAG)
        topik = TOPIK.replace("MD TEST PERSON SAMPLE", "TEST PERSON SAMPLE")
        self.assertEqual(verdicts(L.check_language(topik, PP, "KLP", TODAY))["name"], FLAG)

    def test_word_order_against_the_mrz_is_equal(self):
        # the MRZ is a machine read (surname first, no dots): any word order, dots aside (RUNNER_SPEC 7)
        order = trf(first="TEST MD PERSON", number="25BD123456SAMT001A")
        self.assertEqual(verdicts(L.check_language(order, PP, "MASTER", TODAY))["name"], PASS)
        split = trf(family="SAMPLE MD", first="TEST PERSON", number="25BD123456SAMT001A")
        self.assertEqual(verdicts(L.check_language(split, PP, "MASTER", TODAY))["name"], PASS)
        dots = trf(first="MD. TEST PERSON")
        self.assertEqual(verdicts(L.check_language(dots, PP, "MASTER", TODAY))["name"], PASS)

    def test_stamp_beside_the_field_is_not_the_name(self):
        stamp = trf().replace("Family Name SAMPLE", "Family Name SAMPLE | NOTARY PUBLIC")
        self.assertEqual(verdicts(L.check_language(stamp, PP, "MASTER", TODAY))["name"], PASS)

    def test_untrusted_passport_name_not_compared(self):
        self.assertEqual(verdicts(L.check_language(trf(), dict(PP, name_trusted=False), "MASTER", TODAY))["name"],
                         FLAG)

    def test_trf_letters_disagree(self):
        self.assertEqual(verdicts(L.check_language(trf(number="25BD123456XYZM001A"), PP, "MASTER", TODAY))["trf_name"],
                         FLAG)


class TestDateOfBirth(unittest.TestCase):
    """Critique 12: the passport's date of birth reaches the check only when it is trusted."""

    def test_untrusted_dob_is_absent_and_never_fails(self):
        pp = dict(PP, dob=None)                       # identity.passport_for_language: dob None when untrusted
        r = L.check_language(trf(dob="01/01/1999"), pp, "MASTER", TODAY)
        self.assertEqual(verdicts(r)["dob"], FLAG)
        self.assertNotIn(FAIL, {v for v, c, _ in r["findings"] if c == "dob"})
        pp2 = dict(PP, dob_trusted=False)
        self.assertEqual(verdicts(L.check_language(trf(dob="01/01/1999"), pp2, "MASTER", TODAY))["dob"], FLAG)

    def test_trusted_dob_differs_fails(self):
        self.assertEqual(verdicts(L.check_language(trf(dob="01/01/1999"), PP, "MASTER", TODAY))["dob"], FAIL)


class TestExtrasRemoved(unittest.TestCase):
    """Critique 20: only IELTS, TOEFL and TOPIK are checked; no 60-day, General Training or authenticity rows."""

    def test_other_certificates_are_present_only(self):
        duo = "Duolingo English Test Overall 120 Test Date: Sep 12, 2025 certs.duolingo.com/" + "01234" + "56789abcdef"
        pte = "PTE Academic Score Report Overall Score 65 Test Date: 12 SEP 2025 Score Report Code ABCD12345"
        es = ("BRITISH COUNCIL EnglishScore This is to certify that MD TEST PERSON SAMPLE has achieved CEFR B2 Valid "
              "from 27 Sep 2025 Verified Use code 0a1b2c3d4e5f at englishscore com/verify")
        moi = "This is to certify that the medium of instruction was English"
        for text, test in ((duo, "DUOLINGO"), (pte, "PTE"), (es, "ENGLISHSCORE"), (moi, "MOI")):
            r = L.check_language(text, PP, "KLP", TODAY)
            self.assertEqual(r["test"], test)
            self.assertEqual(r["verdict"], PRESENT)
            self.assertEqual([(v, c) for v, c, _ in r["findings"]], [(PRESENT, "present_only")])
        self.assertEqual(L.check_language_file(duo, PP, "KLP", TODAY)["verdict"], PRESENT)

    def test_no_60_day_rows(self):
        # valid for only 30 more days: PASS, no "days left" FLAG; no administrator's-date row
        d = TODAY.replace(year=TODAY.year - 2) + dt.timedelta(days=30)
        date = f"{d.day:02d}/{L.MONTHS[d.month - 1]}/{d.year}"
        number = "%02dBD123456SAMM001A" % ((d.year if d.month >= 4 else d.year - 1) % 100)
        r = L.check_language(trf(date=date, number=number), PP, "MASTER", TODAY)
        self.assertEqual(verdicts(r)["validity"], PASS, r["findings"])
        self.assertNotIn("issue_date", verdicts(r))
        self.assertFalse(any("days left" in w or "60 days" in w for _, _, w in r["findings"]))
        osr = trf().replace("Test Results", "This Test Report Form includes the result of a Speaking retake "
                                            "Test Results") + (
            " The Retake was taken at Centre Number BD001, as Candidate Number 654321 on Test Date 2 December 2025")
        self.assertFalse(any("60 days" in w for _, _, w in L.check_language(osr, PP, "MASTER", TODAY)["findings"]))

    def test_no_general_training_or_authenticity_rows(self):
        r = L.check_language(trf(module="GENERAL TRAINING", number="25BD123456SAMM001G"), PP, "MASTER", TODAY)
        self.assertNotIn("module", verdicts(r))
        for text in (trf(), TOPIK, TOEFL):
            self.assertNotIn("authenticity", verdicts(L.check_language(text, PP, "MASTER", TODAY)))

    def test_rows_text_marks_column_gaps(self):
        items = [([[0, 0], [60, 0], [60, 10], [0, 10]], "Family Name", 0.9),
                 ([[70, 0], [130, 0], [130, 10], [70, 10]], "SAMPLE", 0.9),
                 ([[400, 1], [460, 1], [460, 11], [400, 11]], "STAMP", 0.9),
                 ([[0, 30], [60, 30], [60, 40], [0, 40]], "First Name", 0.9)]
        self.assertEqual(L.rows_text(items), "Family Name SAMPLE | STAMP\nFirst Name")


class TestPrintedNameAndCloserLook(unittest.TestCase):
    """Owner 28 Oct: the printed passport name when MRZ line 1 is not usable; the closer look for a difference."""

    def test_the_printed_name_is_used_when_mrz_line_1_is_not_usable(self):
        pp = dict(PP, name_trusted=False, printed_name="MD. EXAMPLE SAMPLE", printed_confirmed=True)
        rep = L.check_language(trf(family="SAMPLE", first="MD EXAMPLE"), pp, "EAP", TODAY)
        v = verdicts(rep)["name"]
        self.assertEqual(v, PASS)
        why = next(w for x, c, w in rep["findings"] if c == "name")
        self.assertIn("printed name", why)
        self.assertNotIn("not available", why)

    def test_a_misread_word_is_no_difference(self):
        pp = dict(PP, name_trusted=False, printed_name="MD. EXAMPLE SAMPLE", printed_confirmed=True,
                  closer=lambda cert_word, pp_word: ("same", "same"))
        rep = L.check_language(trf(family="SAMPLX", first="MD EXAMPLE"), pp, "EAP", TODAY)
        self.assertEqual(verdicts(rep)["name"], PASS)

    def test_a_confirmed_difference_fails_and_an_unclear_one_flags(self):
        for answer, want in ((("differs", "same"), FAIL), (("unclear", "same"), FLAG), (("differs", "unclear"), FLAG)):
            with self.subTest(answer=answer):
                pp = dict(PP, name_trusted=False, printed_name="MD. EXAMPLE SAMPLE", printed_confirmed=True,
                          closer=lambda cert_word, pp_word, a=answer: a)
                rep = L.check_language(trf(family="SAMPLX", first="MD EXAMPLE"), pp, "EAP", TODAY)
                self.assertEqual(verdicts(rep)["name"], want)

    def test_one_misread_word_and_one_real_difference(self):
        """Two words read differently: one is a misread (the look shows the passport's spelling), the other is
        printed differently and the passport prints its own: the name differs (FAIL), never an all-or-nothing
        FLAG."""
        def closer(cert_word, pp_word):
            return ("same", "same") if cert_word == "EXAMPIE" else ("differs", "same")
        pp = dict(PP, name_trusted=False, printed_name="MD. EXAMPLE SAMPLE", printed_confirmed=True, closer=closer)
        rep = L.check_language(trf(family="SAMPLX", first="MD EXAMPIE"), pp, "EAP", TODAY)
        self.assertEqual(verdicts(rep)["name"], FAIL)


if __name__ == "__main__":
    unittest.main()
