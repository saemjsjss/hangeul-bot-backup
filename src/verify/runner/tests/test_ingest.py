"""Task 1: ingest.py (RUNNER_SPEC 5.1). Made-up file names and made-up PDF text only.

Scratch files go to C:\\Hangeul\\DOCCHECK\\lab\\private-runner\\tmp\\test_ingest-<pid>-<n> and are removed.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import unittest
import uuid
from pathlib import Path

from src.verify.runner import botimport, ingest, safety

SCRATCH = botimport.PRIVATE / "tmp"
MTIME = 1_790_000_000.25          # a fixed modification time (made-up)


def _rmtree(p: Path) -> None:
    """Remove a scratch folder under private-runner; retried, because a virus scanner may hold a file
    that was just written for a moment."""
    import time
    for _ in range(6):
        shutil.rmtree(p, ignore_errors=True)
        if not Path(p).exists():
            return
        time.sleep(0.5)


def _pdf(path: Path, lines, pages: int = 1) -> Path:
    import pymupdf
    d = pymupdf.open()
    for i in range(pages):
        p = d.new_page(width=595, height=842)
        y = 80
        for t in lines:
            p.insert_text((50, y), f"{t} {i + 1}", fontsize=11)
            y += 25
    d.save(str(path))
    d.close()
    os.utime(path, (MTIME, MTIME))
    return path


def _png(path: Path) -> Path:
    import numpy as np
    from PIL import Image
    a = np.full((60, 40, 3), 230, np.uint8)
    Image.fromarray(a).save(path)
    os.utime(path, (MTIME, MTIME))
    return path


class ClassifyName(unittest.TestCase):
    TABLE = [
        ("PASSPORT.pdf", "passport"),
        ("PASSPORT SIZE PHOTO.jpg", "photo"),
        ("FATHER NID.pdf", "father_nid"),
        ("MOTHER NID.pdf", "mother_nid"),
        ("STUDENT NID.pdf", "student_nid"),
        ("FATHER ID CARD.pdf", "father_nid"),
        ("MOTHER ID CARD.pdf", "mother_nid"),
        ("FATHER NATIONAL ID.pdf", "father_nid"),
        ("BIRTH CERTIFICATE.pdf", "birth_cert"),
        ("FAMILY CERTIFICATE.pdf", "family_cert"),
        ("family_cert_1234.pdf", "family_cert"),
        ("ACADEMIC CERTIFICATE HSC.pdf", "academic"),
        ("HSC.pdf", "academic"),
        ("SSC.pdf", "academic"),
        ("hsc_certificate.pdf", "academic"),
        ("SSC TRANSCRIPT.pdf", "academic"),
        ("APOSTILLE.pdf", "academic"),
        ("MARK SHEET.pdf", "academic"),
        ("BANK STATEMENT.pdf", "bank"),
        ("SOLVENCY CERTIFICATE.pdf", "bank"),
        ("TRADE LICENSE.pdf", "trade_license"),
        ("TRADE LICENCE.pdf", "trade_license"),
        ("TIN.pdf", "trade_license"),
        ("TIN CERTIFICATE.pdf", "trade_license"),
        ("E-TIN.pdf", "trade_license"),
        ("EMPLOYMENT CERTIFICATE.pdf", "trade_license"),
        ("SALARY CERTIFICATE.pdf", "trade_license"),
        ("INCOME TAX.pdf", "income_tax"),
        ("TAX RETURN.pdf", "income_tax"),
        ("RETURN ACKNOWLEDGEMENT.pdf", "income_tax"),
        ("AFFIDAVIT.pdf", "affidavit"),
        ("FATHER DEATH CERTIFICATE.pdf", "father_death_cert"),
        ("DEATH CERTIFICATE.pdf", "death_cert"),
        ("IELTS.pdf", "language_cert"),
        ("DUOLINGO.pdf", "language_cert"),
        ("PTE.pdf", "language_cert"),
        ("CV.pdf", "id_card_cv"),
        ("ID CARD CV.pdf", "id_card_cv"),
        ("RESUME.pdf", "id_card_cv"),
        ("STUDY PLAN.pdf", "personal_statement"),
        ("SOP.pdf", "personal_statement"),
        ("STATEMENT OF PURPOSE.pdf", "personal_statement"),
        ("RECOMMENDATION LETTER.pdf", "recommendation"),
        ("ECA.pdf", "eca"),
        # a short pattern inside a longer word is not a match (whole words only)
        ("CONTINUATION.pdf", None),
        ("DESTINATION.pdf", None),
        ("BECAUSE.pdf", None),
        ("PTERIN.pdf", None),
        ("NOC.pdf", None),
    ]

    def test_table(self):
        for name, key in self.TABLE:
            with self.subTest(name=name):
                self.assertEqual(ingest.classify_name(name), key)

    def test_bot_semantics_kept(self):
        # the bot's longest-pattern rule: a photo, not a passport; a father's death certificate
        self.assertEqual(ingest.classify_name("passport_size_photo.jpg"), "photo")
        self.assertEqual(ingest.classify_name("father_death_cert.pdf"), "father_death_cert")
        self.assertEqual(ingest.classify_name("bank_statement.pdf"), "bank")


class OpenInputAndStage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = SCRATCH / f"test_ingest-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        cls.folder = cls.base / "folder"
        cls.folder.mkdir(parents=True)
        _pdf(cls.folder / "PASSPORT.pdf", ["SAMPLE PASSPORT PAGE"])
        _pdf(cls.folder / "bank statement.pdf", ["SAMPLE BANK PLC", "STATEMENT OF ACCOUNT"], pages=3)
        _pdf(cls.folder / "CONTINUATION.pdf", ["SAMPLE EXTRA PAGE"])
        _png(cls.folder / "PASSPORT SIZE PHOTO.png")
        _pdf(cls.folder / ".hidden.pdf", ["SAMPLE"])
        _pdf(cls.folder / "~$lock.pdf", ["SAMPLE"])
        (cls.folder / "notes.txt").write_text("made-up notes", encoding="utf-8")
        (cls.folder / "sub").mkdir()
        _pdf(cls.folder / "sub" / "FATHER NID.pdf", ["SAMPLE"])
        cls.bundle = _pdf(cls.base / "BUNDLE.pdf", ["SAMPLE BUNDLE PAGE"], pages=4)

    @classmethod
    def tearDownClass(cls):
        _rmtree(cls.base)

    def test_folder(self):
        src = ingest.open_input(self.folder)
        self.assertEqual(src.mode, "folder")
        self.assertEqual([f.name for f in src.files],
                         ["bank statement.pdf", "CONTINUATION.pdf", "PASSPORT SIZE PHOTO.png", "PASSPORT.pdf"])
        by = {f.name: f for f in src.files}
        self.assertEqual(by["bank statement.pdf"].n_pages, 3)
        self.assertEqual(by["bank statement.pdf"].key_by_name, "bank")
        self.assertIsNone(by["CONTINUATION.pdf"].key_by_name)
        self.assertTrue(by["PASSPORT SIZE PHOTO.png"].is_image)
        self.assertEqual(by["PASSPORT SIZE PHOTO.png"].n_pages, 1)
        self.assertEqual(by["PASSPORT SIZE PHOTO.png"].key_by_name, "photo")
        joined = "\n".join(f"{f.name}|{f.size}|{int(f.mtime)}" for f in src.files)
        self.assertEqual(src.case_id, "F-" + hashlib.sha1(joined.encode("utf-8")).hexdigest()[:16])

    def test_case_id_stable(self):
        a, b = ingest.open_input(self.folder), ingest.open_input(self.folder)
        self.assertEqual(a.case_id, b.case_id)
        self.assertRegex(a.case_id, r"^F-[0-9a-f]{16}$")
        x, y = ingest.open_input(self.bundle), ingest.open_input(self.bundle)
        self.assertEqual(x.case_id, y.case_id)
        self.assertEqual(x.case_id, "B-" + hashlib.sha1(self.bundle.read_bytes()).hexdigest()[:16])

    def test_bundle(self):
        src = ingest.open_input(self.bundle)
        self.assertEqual(src.mode, "bundle")
        self.assertEqual(len(src.files), 1)
        self.assertEqual(src.files[0].n_pages, 4)
        self.assertIsNone(src.files[0].key_by_name)
        self.assertEqual(ingest.stage(src, self.base / "docs-bundle"), {})
        self.assertFalse((self.base / "docs-bundle").exists())

    def test_input_errors(self):
        bad = self.base / "broken.pdf"
        bad.write_bytes(b"this is not a PDF at all")
        empty = self.base / "empty"
        empty.mkdir(exist_ok=True)
        for p in (Path("relative\\BUNDLE.pdf"), self.base / "missing.pdf", self.folder / "PASSPORT SIZE PHOTO.png",
                  empty, bad):
            with self.subTest(p=p.name):
                with self.assertRaises(ingest.InputError):
                    ingest.open_input(p)

    def test_stage_keeps_name_size_mtime_and_input(self):
        src = ingest.open_input(self.folder)
        before = {f.name: ingest.file_sha256(f.path) for f in src.files}
        staged = ingest.stage(src, self.base / "run" / "docs")
        self.assertEqual(sorted(staged), sorted(f.name for f in src.files))
        for f in src.files:
            dst = staged[f.name]
            self.assertEqual(dst.name, f.name)
            st = dst.stat()
            self.assertEqual(st.st_size, f.size)
            self.assertEqual(int(st.st_mtime), int(f.mtime))
            self.assertEqual(ingest.file_sha256(dst), before[f.name])
        self.assertEqual({f.name: ingest.file_sha256(f.path) for f in src.files}, before)
        # a second stage into the same folder overwrites with the same copies
        again = ingest.stage(src, self.base / "run" / "docs")
        self.assertEqual(again, staged)

    def test_stage_refuses_outside_private(self):
        src = ingest.open_input(self.folder)
        outside = Path(r"C:\Users\Public\runner-stage-probe-never-created")
        with self.assertRaises(safety.SafetyError):
            ingest.stage(src, outside)
        self.assertFalse(outside.exists())

    def test_file_sha256(self):
        self.assertEqual(ingest.file_sha256(self.bundle), hashlib.sha256(self.bundle.read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main()
