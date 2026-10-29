"""Task B: app/messages.py (APP_SPEC rev 1 section 3.3, rev 2 R2.3.2). Made-up values only; no file is written.

Run: HDC_TEST_NO_CARD=1 <runner venv python> -I -B -m unittest src.verify.runner.app.tests.test_messages
"""
from __future__ import annotations

import re
import unittest

from src.verify.runner.app import messages as M

DRIVE_PATH = re.compile(r"[A-Za-z]:\\|\\\\")


class CardMessages(unittest.TestCase):
    """R2.3.2: the six card codes, word for word, with values only."""

    def test_no_card(self):
        m = M.render(M.NO_NVIDIA_CARD)
        self.assertEqual(m.title, "No NVIDIA graphics card")
        self.assertEqual(m.text, "This checker reads documents on an NVIDIA graphics card, and none was found on this "
                                 "PC (or its driver is not installed). Use a PC with an NVIDIA GeForce RTX card (RTX "
                                 "20 series or newer, 6 GB or more) and its driver. Nothing was checked.")
        self.assertEqual(m.kind, M.ERROR)

    def test_driver_too_old(self):
        m = M.render(M.DRIVER_TOO_OLD, {"driver": "000.00"})
        self.assertEqual(m.title, "Graphics driver too old")
        self.assertEqual(m.text, "The NVIDIA driver on this PC (version 000.00) is too old for the checker. Install "
                                 "NVIDIA driver 570 or newer (it supports CUDA 12.8) from NVIDIA's website or the "
                                 "NVIDIA App, restart the PC and check again. Nothing was checked.")

    def test_not_supported_old_and_new(self):
        old = M.render(M.CARD_NOT_SUPPORTED, {"name": "NVIDIA GeForce GTX 0000", "which": "old"})
        self.assertEqual(old.text, "This PC's graphics card (NVIDIA GeForce GTX 0000) is too old for the checker's "
                                   "reading engine. It needs a GeForce RTX 20 series card or newer. Nothing was "
                                   "checked.")
        new = M.render(M.CARD_NOT_SUPPORTED, {"name": "NVIDIA GeForce RTX 0000", "which": "new"})
        self.assertEqual(new.text, "This PC's graphics card (NVIDIA GeForce RTX 0000) is newer than this version of "
                                   "the checker supports. A newer version of the checker is needed. Nothing was "
                                   "checked.")
        self.assertEqual(old.title, "Graphics card not supported")
        self.assertEqual(new.title, old.title)

    def test_not_supported_side_from_other_keys_and_cc(self):
        self.assertIn("is newer than", M.render(M.CARD_NOT_SUPPORTED, {"name": "X", "age": "new"}).text)
        self.assertIn("is newer than", M.render(M.CARD_NOT_SUPPORTED, {"name": "X", "cc": "13.0"}).text)
        self.assertIn("too old", M.render(M.CARD_NOT_SUPPORTED, {"name": "X", "cc": (6, 1)}).text)
        self.assertIn("too old", M.render(M.CARD_NOT_SUPPORTED, {}).text)
        self.assertNotIn("()", M.render(M.CARD_NOT_SUPPORTED, {}).text)
        self.assertNotIn("model not known", M.render(M.CARD_NOT_SUPPORTED, {}).text)

    def test_too_small_total_spellings(self):
        want = ("This PC's graphics card (NVIDIA GeForce RTX 0000) has 4.0 GB of memory; the checker needs a card "
                "with at least 6 GB. Nothing was checked.")
        for vals in ({"total": "4.0"}, {"total_gb": 4.0}, {"total_mb": 4096}, {"total": 4096}):
            vals = dict(vals, name="NVIDIA GeForce RTX 0000")
            self.assertEqual(M.render(M.CARD_TOO_SMALL, vals).text, want, vals)
        self.assertEqual(M.render(M.CARD_TOO_SMALL, {"name": "NVIDIA GeForce RTX 0000", "total": "4.0"}).title,
                         "Graphics card memory too small")

    def test_low_card_memory(self):
        m = M.render(M.LOW_CARD_MEMORY, {"free_mb": 2900, "need_mb": 4096})
        self.assertEqual(m.title, "Graphics card busy")
        self.assertEqual(m.text, "Only 2.8 GB of the graphics card's memory is free; the checker needs 4.0 GB. Close "
                                 "programs that use the graphics card (games, video or photo editors, other AI "
                                 "tools) and check again.")
        self.assertIn("needs 4.0 GB", M.render(M.LOW_CARD_MEMORY, {"free": "2.8"}).text)

    def test_card_failed_names_the_step(self):
        m = M.render(M.CARD_FAILED, {"stage": "firstread"})
        self.assertEqual(m.title, "Graphics card error")
        self.assertEqual(m.text, "The graphics card stopped working during the check (step 'reading the pages'). Your "
                                 "files were not changed. Restart the PC and check again; if it happens again, "
                                 "update the NVIDIA driver, run the Self-test and send SELFTEST.txt.")
        self.assertIn("step 'unknown'", M.render(M.CARD_FAILED).text)

    def test_hard_and_card_codes(self):
        self.assertEqual(M.HARD_CARD_CODES, ("NO_NVIDIA_CARD", "DRIVER_TOO_OLD", "CARD_NOT_SUPPORTED",
                                             "CARD_TOO_SMALL"))
        self.assertEqual(len(M.CARD_CODES), 6)
        for c in M.CARD_CODES:
            self.assertTrue(M.is_card_code(c))
            self.assertTrue(M.known(c))
        self.assertTrue(M.is_hard_card_code(M.CARD_TOO_SMALL))
        self.assertFalse(M.is_hard_card_code(M.LOW_CARD_MEMORY))
        self.assertFalse(M.is_hard_card_code(M.CARD_FAILED))

    def test_matches_other_tasks_codes(self):
        """C's protocol and E's gpuinfo name the same codes (skipped while those modules are absent)."""
        try:
            from src.verify.runner.app import protocol
        except ImportError:
            protocol = None
        if protocol is not None:
            self.assertEqual(tuple(protocol.CARD_CODES), M.CARD_CODES)
            for name in ("CRASH", "CANCELLED", "STARTUP_FAILED", "MISSING_PART", "CPU_UNSUPPORTED", "LOW_MEMORY",
                         "WINDOWS_TOO_OLD"):
                self.assertTrue(M.known(getattr(protocol, name)), name)
        try:
            from src.verify.runner.app import gpuinfo
        except ImportError:
            gpuinfo = None
        if gpuinfo is not None:
            for name in ("NO_NVIDIA_CARD", "DRIVER_TOO_OLD", "CARD_NOT_SUPPORTED", "CARD_TOO_SMALL"):
                self.assertEqual(getattr(gpuinfo, name), getattr(M, name))
            self.assertEqual(int(gpuinfo.NEED_FREE_MB), M.NEED_FREE_MB)


class WaitingAndLines(unittest.TestCase):
    def test_waiting_bot(self):
        m = M.waiting("lock", "bot", 125.0)
        self.assertEqual(m.title, "Waiting for the graphics card")
        self.assertEqual(m.kind, M.WAIT)
        self.assertEqual(m.text, "Waiting for the graphics card: the Telegram bot is checking documents (waited 2 "
                                 "min). The check goes on by itself when the bot is done.")

    def test_waiting_other(self):
        self.assertEqual(M.waiting("lock", "other", 0).text,
                         "Waiting for the graphics card: another program's lock file is present (waited 0 min). The "
                         "check goes on by itself.")
        self.assertIn("another program", M.waiting("lock", None, None).text)

    def test_waiting_memory(self):
        self.assertEqual(M.waiting("memory", None, 61, free_mb=2900, need_mb=4096).text,
                         "Waiting for the graphics card: 2.8 GB free, 4.0 GB needed (waited 1 min, at most 15). Close "
                         "programs that use the graphics card.")
        self.assertIn("? GB free, 4.0 GB needed", M.waiting("memory").text)

    def test_minutes(self):
        self.assertEqual([M.minutes(x) for x in (None, -5, 0, 59.9, 60, 3599, "x", float("nan"))],
                         [0, 0, 0, 0, 1, 59, 0, 0])

    def test_result_notes_of_g1_review_finding_4(self):
        for fn in (M.budget_note, M.downscaled_note, M.skipped_note):
            for n in (0, None, -1, "x", True and 0):
                self.assertEqual(fn(n), "", (fn.__name__, n))
        self.assertEqual(M.budget_note(2), "2 QR or barcode reading step(s) ran out of time, so a code may not have "
                         "been read. Check again when the PC is idle; the result may then differ.")
        self.assertEqual(M.downscaled_note(1), "1 page(s) were read at a smaller size because the graphics card ran "
                         "out of memory. Check again when the graphics card is free; the result may then differ.")
        self.assertTrue(M.skipped_note(3).startswith("3 page(s) were too large for the graphics card's memory"))
        for text in (M.budget_note(5), M.downscaled_note(5), M.skipped_note(5)):
            self.assertTrue(text.isascii())
            self.assertIsNone(DRIVE_PATH.search(text))

    def test_low_memory_note_is_yellow(self):
        n = M.low_card_memory_note(3000)
        self.assertEqual(n.kind, M.NOTE)
        self.assertIn("2.9 GB", n.text)
        self.assertIn("4.0 GB", n.text)
        self.assertIn("at most 15 min", n.text)

    def test_card_line(self):
        self.assertEqual(M.card_line("NVIDIA GeForce RTX 0000", 8151, "000.00"),
                         "Graphics card: NVIDIA GeForce RTX 0000 (8.0 GB) · driver 000.00")
        self.assertEqual(M.card_line(None, None, None, found=False), "Graphics card: none found")
        self.assertEqual(M.card_line("", 8151, "1", True), "Graphics card: none found")
        self.assertEqual(M.card_line("Card", None, None), "Graphics card: Card")

    def test_reading_progress(self):
        self.assertEqual(M.reading_progress(4, 12, 24.0),
                         "Reading page 5 of 12 on the graphics card — about 24 s left")
        self.assertEqual(M.reading_progress(0, 3, None), "Reading page 1 of 3 on the graphics card")
        self.assertEqual(M.reading_progress(3, 3, 0), "Read 3 of 3 pages on the graphics card")
        self.assertEqual(M.reading_progress(2, 0), "Reading page 3 on the graphics card")
        self.assertEqual(M.fmt_left(0.2), "about 1 s left")
        self.assertEqual(M.fmt_left(150), "about 2 min left")
        self.assertEqual(M.fmt_left(-1), "")
        self.assertEqual(M.fmt_left("x"), "")

    def test_stage_words(self):
        self.assertEqual(M.stage_words("card"), "starting the graphics card")
        self.assertEqual(M.stage_words("names"), "comparing the names")
        self.assertEqual(M.stage_words("newstage"), "newstage")
        self.assertEqual(M.checking_progress("bank"), "Checking documents… (bank documents)")

    def test_order_and_stop(self):
        t = M.order_text("pages 4-5", "the SSC/HSC pages are swapped.")
        self.assertEqual(t, "Some pages are not in the fixed order. Fix pages 4-5: the SSC/HSC pages are swapped. The "
                            "documents on these pages are NOT JUDGED until you rescan or reorder them and check "
                            "again.")
        t2 = M.order_text("page 3", "a page is out of place", blocks=False)
        self.assertTrue(t2.endswith("(the documents were still checked)"))
        self.assertEqual(M.stop_text("pages 2-3"),
                         "The father's and the mother's NID pages could not be told apart (pages 2-3). Put the "
                         "father's NID first, then the mother's, and check again.")


class Rev1Messages(unittest.TestCase):
    TITLES = {
        "STOP": "Check stopped", "NOT_PDF_OR_FOLDER": "Not a PDF", "EMPTY_FOLDER": "Empty folder",
        "FOLDER_OF_FOLDERS": "Wrong folder", "ENCRYPTED": "Password-protected PDF",
        "UNREADABLE_PDF": "PDF cannot be opened", "NO_PAGES": "Empty PDF", "UNREADABLE_IMAGE": "Picture cannot be opened",
        "PATH_TOO_LONG": "Path too long", "REPORT_INSIDE_INPUT": "Choose another report folder",
        "INPUT_INSIDE_REPORT": "Choose another report folder", "REPORT_NOT_WRITABLE": "Cannot save reports",
        "DISK_FULL": "Disk full", "LOW_MEMORY": "Not enough memory", "INPUT_CHANGED": "File changed",
        "CPU_UNSUPPORTED": "Processor too old", "WINDOWS_TOO_OLD": "Windows too old",
        "MISSING_PART": "A program file is missing", "STARTUP_FAILED": "Could not start the check",
        "CRASH": "The check stopped", "CANCELLED": "Cancelled",
    }

    def test_every_rev1_code_has_its_title(self):
        for code, title in self.TITLES.items():
            m = M.render(code)
            self.assertEqual(m.code, code)
            self.assertEqual(m.title, title, code)
            self.assertNotIn("{", m.text, code)

    def test_some_texts_word_for_word(self):
        self.assertEqual(M.render(M.EMPTY_FOLDER).text,
                         "This folder has no PDF, JPG or PNG files. Choose the folder that holds the student's "
                         "documents.")
        self.assertEqual(M.render(M.CANCELLED).text, "Check cancelled. Nothing was saved.")
        self.assertEqual(M.render(M.ENCRYPTED, {"file": "SAMPLE.pdf"}).text,
                         "SAMPLE.pdf is password-protected. Open it, save or print a copy without the password, and "
                         "check that copy.")
        self.assertEqual(M.render(M.CRASH, {"stage": "names"}).text,
                         "The check stopped unexpectedly in step 'comparing the names'. Your files were not changed. "
                         "Run the Self-test, then send SELFTEST.txt and checkfacts.json.")

    def test_values_are_never_paths(self):
        for p in (r"C:\Users\someone\Desktop\SAMPLE BUNDLE.pdf", r"\\server\share\x\SAMPLE BUNDLE.pdf",
                  "D:/scans/SAMPLE BUNDLE.pdf"):
            for code in (M.ENCRYPTED, M.UNREADABLE_PDF, M.NO_PAGES, M.UNREADABLE_IMAGE):
                t = M.render(code, {"file": p}).text
                self.assertNotRegex(t, DRIVE_PATH)
                self.assertTrue(t.startswith("SAMPLE BUNDLE.pdf "), t)
        t = M.render(M.CARD_TOO_SMALL, {"name": r"C:\x\NVIDIA Card", "total": "4.0"}).text
        self.assertIn("(NVIDIA Card)", t)
        self.assertNotRegex(M.render(M.DRIVER_TOO_OLD, {"driver": "C:\\driver\\000.00"}).text, DRIVE_PATH)

    def test_missing_part_keeps_a_relative_file(self):
        self.assertTrue(M.render(M.MISSING_PART, {"file": "_internal/torch/lib/cudnn64_9.dll"}).text.startswith(
            "_internal\\torch\\lib\\cudnn64_9.dll is missing or changed"))
        t = M.render(M.MISSING_PART, {"file": r"C:\Program Files\x\cudnn64_9.dll"}).text
        self.assertTrue(t.startswith("cudnn64_9.dll is missing"), t)

    def test_unknown_code(self):
        m = M.render("SOMETHING_NEW", {"x": 1})
        self.assertEqual(m.title, "The check stopped")
        self.assertIn("(SOMETHING_NEW)", m.text)
        bad = M.render("not a code; C:\\x")
        self.assertIn("(UNKNOWN)", bad.text)
        self.assertNotRegex(bad.text, DRIVE_PATH)

    def test_safe_helpers(self):
        self.assertEqual(M.safe_value(r"C:\a\b\SAMPLE.pdf"), "SAMPLE.pdf")
        self.assertEqual(M.safe_value("C:\\"), "")
        self.assertEqual(M.safe_value("SSC/HSC"), "SSC/HSC")
        self.assertEqual(M.safe_value("a\x00b\x07c"), "abc")
        self.assertEqual(len(M.safe_value("x" * 500)), 80)
        self.assertEqual(M.safe_text("see C:\\Users\\Some Name\\file.pdf now"), "see <path> <path> now")
        self.assertEqual(M.safe_text("the HSC/SSC order"), "the HSC/SSC order")
        self.assertEqual(M.safe_detail("failed at \\\\srv\\share\\x.pdf\nline2"), "failed at <path> line2")
        self.assertEqual(M.gb(8151), "8.0")
        self.assertEqual(M.gb(None), "?")
        self.assertEqual(M.gb(-1), "?")

    def test_window_notes(self):
        self.assertEqual(M.render(M.LOW_RAM_WARNING, {"free_mb": 3072, "need": "4.5"}).kind, M.CONFIRM)
        self.assertIn("3.0 GB", M.render(M.LOW_RAM_WARNING, {"free_mb": 3072, "need": "4.5"}).text)
        self.assertEqual(M.render(M.SELFTEST_OK).kind, M.INFO)
        self.assertEqual(M.render(M.SELFTEST_FAILED).kind, M.ERROR)
        self.assertEqual(M.render(M.SAVED, {"file": r"C:\x\SAMPLE.pdf"}).text, "The summary was saved as SAMPLE.pdf.")


class ExitClassification(unittest.TestCase):
    def test_table(self):
        cases = [
            (None, True, None),
            (0xC000001D, False, M.CPU_UNSUPPORTED), (-1073741795, True, M.CPU_UNSUPPORTED),  # pii-scan: not a number
            (0xC0000017, True, M.LOW_MEMORY),
            (0xC0000135, False, M.MISSING_PART), (0xC0000139, True, M.MISSING_PART),
            (1, False, M.STARTUP_FAILED), (0, False, M.STARTUP_FAILED),
            (0xC0000005, True, M.CRASH), (1, True, M.CRASH), (0, True, M.CRASH), (2, True, M.CRASH),
            (4, True, M.CANCELLED), (6, True, M.CARD_FAILED), ("x", True, M.CRASH),
        ]
        for code, hello, want in cases:
            self.assertEqual(M.classify_exit(code, hello), want, (code, hello))


if __name__ == "__main__":
    unittest.main()
