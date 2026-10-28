"""Task B: app/userdirs.py (APP_SPEC rev 1 sections 1.4, 3.2, 4.1): case labels, path lengths, the window's fast
pre-checks, the case folder and its marker, housekeeping. Made-up names and files only.

Scratch files: C:\\Hangeul\\DOCCHECK\\lab\\private-runner\\selftest\\app\\B\\userdirs-<pid>-<n> (removed).
Run: HDC_TEST_NO_CARD=1 <runner venv python> -I -B -m unittest src.verify.runner.app.tests.test_userdirs
"""
from __future__ import annotations

import datetime as dt
import os
import shutil
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace

from src.verify.runner.app import messages as M
from src.verify.runner.app import userdirs as U

SCRATCH = Path(os.environ.get("HDC_TEST_SCRATCH", r"C:\Hangeul\DOCCHECK\lab\private-runner\selftest\app\B"))
WHEN = dt.datetime(2026, 10, 28, 14, 12)
JOB = "0123456789abcdef0123456789abcdef"


def _rmtree(p: Path) -> None:
    for _ in range(6):
        shutil.rmtree(p, ignore_errors=True)
        if not p.exists():
            return
        time.sleep(0.3)


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = SCRATCH / f"userdirs-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self.dir.mkdir(parents=True)

    def tearDown(self):
        _rmtree(self.dir)

    def touch(self, rel: str, data: bytes = b"x", mtime: float = None) -> Path:
        p = self.dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        if mtime is not None:
            os.utime(p, (mtime, mtime))
        return p


class Labels(unittest.TestCase):
    def test_safe_label(self):
        self.assertEqual(U.safe_label('SAMPLE <one>: "two" / three\\four | five? six*'),
                         "SAMPLE one two three four five six")
        self.assertEqual(U.safe_label("  ..SAMPLE\tNAME..  "), "SAMPLE NAME")
        self.assertEqual(U.safe_label("x" * 60), "x" * 40)
        self.assertEqual(U.safe_label("a" * 39 + " b"), "a" * 39)            # the cut leaves no trailing space
        self.assertEqual(U.safe_label(""), "case")
        self.assertEqual(U.safe_label(" . "), "case")
        self.assertEqual(U.safe_label("con"), "_con")
        self.assertEqual(U.safe_label("NUL.sample"), "_NUL.sample")
        self.assertEqual(U.safe_label("COM1 bundle"), "COM1 bundle")             # only a device NAME is changed
        self.assertEqual(U.safe_label("নমুনা ফাইল"), "নমুনা ফাইল")              # non-ASCII letters stay
        self.assertEqual(U.safe_label("a\x00b\x1fc\ud800d"), "a b c d")
        self.assertEqual(U.safe_label("SAMPLE", 3), "SAM")

    def test_input_stem(self):
        self.assertEqual(U.input_stem(r"D:\x\SAMPLE BUNDLE.pdf"), "SAMPLE BUNDLE")
        self.assertEqual(U.input_stem(r"D:\x\SAMPLE FOLDER"), "SAMPLE FOLDER")
        self.assertEqual(U.input_stem(r"D:\x\SAMPLE.v2.PDF"), "SAMPLE.v2")
        self.assertEqual(U.input_stem("E:\\"), "drive E")

    def test_case_folder_name(self):
        self.assertEqual(U.case_folder_name("SAMPLE", "KLP", WHEN), "SAMPLE KLP 2026-10-28 1412")
        self.assertEqual(U.case_folder_name("SAMPLE", "KLP", WHEN, 2), "SAMPLE KLP 2026-10-28 1412 (2)")

    def test_longest_output_len(self):
        case = "D:\\" + "r" * 50
        n = U.longest_output_len(case)
        self.assertEqual(n, len(case) + len("\\docs\\parts\\") + U.LONGEST_UNIT_NAME)
        self.assertEqual(U.longest_output_len(case, ["y" * 120]), len(case) + len("\\docs\\") + 120)

    def test_fit_label_cuts_until_it_fits(self):
        stem = "SAMPLE STUDENT BUNDLE WITH A VERY LONG NAME"
        self.assertEqual(U.fit_label("D:\\Reports", stem, "KLP", WHEN), U.safe_label(stem))
        root = "D:\\" + "r" * 140                          # (143 <= 150) the case folder name must shrink
        label = U.fit_label(root, stem, "BACHELOR", WHEN)
        self.assertIsNotNone(label)
        self.assertLess(len(label), 40)
        name = U.case_folder_name(label, "BACHELOR", WHEN) + "x" * U.CLASH_SUFFIX_MAX
        self.assertLess(U.longest_output_len(root + "\\" + name), U.PATH_LIMIT)
        longer = U.safe_label(stem, len(label) + 1)
        name2 = U.case_folder_name(longer, "BACHELOR", WHEN) + "x" * U.CLASH_SUFFIX_MAX
        self.assertGreaterEqual(U.longest_output_len(root + "\\" + name2), U.PATH_LIMIT)
        self.assertIsNone(U.fit_label("D:\\" + "r" * 200, stem, "KLP", WHEN))
        self.assertIsNone(U.fit_label("D:\\Reports", stem, "KLP", WHEN, ["n" * 230]))   # folder mode copies

    def test_marker_name(self):
        self.assertEqual(U.marker_name(JOB), ".hdc-run-" + JOB)
        for bad in ("x", JOB.upper(), JOB + "0", "../" + JOB[3:]):
            with self.assertRaises(ValueError):
                U.marker_name(bad)

    def test_marker_matches_the_controller(self):
        try:
            from src.verify.runner.app import controller
        except ImportError:
            self.skipTest("controller.py not there yet")
        self.assertEqual(controller.case_marker(JOB), U.marker_name(JOB))


class InputChecks(Base):
    def test_files(self):
        self.assertEqual(U.check_input(""), M.NOT_PDF_OR_FOLDER)
        self.assertEqual(U.check_input(str(self.dir / "nothing.pdf")), M.NOT_PDF_OR_FOLDER)
        self.assertEqual(U.check_input(str(self.touch("notes.txt"))), M.NOT_PDF_OR_FOLDER)
        self.assertEqual(U.check_input(str(self.touch("photo.jpg"))), M.NOT_PDF_OR_FOLDER)   # a bundle is a PDF
        self.assertIsNone(U.check_input(str(self.touch("SAMPLE.PDF"))))
        self.assertEqual(U.check_input("D:\\" + "p" * 300 + ".pdf"), M.PATH_TOO_LONG)

    def test_folders(self):
        empty = self.dir / "empty"
        empty.mkdir()
        self.assertEqual(U.check_input(str(empty)), M.EMPTY_FOLDER)
        self.touch("others/notes.docx")
        self.assertEqual(U.check_input(str(self.dir / "others")), M.EMPTY_FOLDER)
        self.touch("hidden/.SAMPLE.pdf")
        self.touch("hidden/~$SAMPLE.pdf")
        self.assertEqual(U.check_input(str(self.dir / "hidden")), M.EMPTY_FOLDER)
        (self.dir / "parent" / "student one").mkdir(parents=True)
        (self.dir / "parent" / "student two").mkdir(parents=True)
        self.assertEqual(U.check_input(str(self.dir / "parent")), M.FOLDER_OF_FOLDERS)
        self.touch("good/PASSPORT.pdf")
        self.touch("good/PHOTO.JPG")
        (self.dir / "good" / "sub").mkdir()
        self.assertIsNone(U.check_input(str(self.dir / "good")))
        self.assertEqual(U.input_names(self.dir / "good"), ["PASSPORT.pdf", "PHOTO.JPG"])
        self.assertEqual(U.input_names(self.dir / "good" / "PASSPORT.pdf"), [])
        self.assertEqual(U.input_bytes(self.dir / "good"), 2)
        self.assertEqual(U.input_bytes(self.dir / "good" / "PASSPORT.pdf"), 1)

    def test_is_within(self):
        a = self.dir / "a"
        (a / "b").mkdir(parents=True)
        self.assertTrue(U.is_within(a / "b", a))
        self.assertTrue(U.is_within(a, a))
        self.assertTrue(U.is_within(str(a / "b").upper(), str(a).lower()))
        self.assertFalse(U.is_within(a, a / "b"))
        self.assertFalse(U.is_within(self.dir / "ab", a))
        self.assertFalse(U.is_within("Z:\\x", a))

    def test_report_root(self):
        inp = self.dir / "input"
        inp.mkdir()
        self.touch("input/PASSPORT.pdf")
        self.assertEqual(U.check_report_root("D:\\" + "r" * 150, inp), M.PATH_TOO_LONG)
        self.assertEqual(U.check_report_root(str(inp / "reports"), inp), M.REPORT_INSIDE_INPUT)
        self.assertEqual(U.check_report_root(str(inp), inp), M.REPORT_INSIDE_INPUT)
        self.assertEqual(U.check_report_root(str(self.dir), inp), M.INPUT_INSIDE_REPORT)
        self.assertEqual(U.check_report_root(str(self.dir), inp / "PASSPORT.pdf"), M.INPUT_INSIDE_REPORT)
        self.assertEqual(U.check_report_root("relative\\reports", inp), M.REPORT_NOT_WRITABLE)
        blocker = self.touch("blocker")                         # a file where the folder should be
        self.assertEqual(U.check_report_root(str(blocker / "reports"), inp), M.REPORT_NOT_WRITABLE)
        ok = self.dir / "reports" / "new"
        self.assertIsNone(U.check_report_root(str(ok), inp))
        self.assertTrue(ok.is_dir())
        self.assertEqual(list(ok.iterdir()), [])                 # the probe file is gone
        self.assertIsNone(U.check_report_root(str(self.dir / "never"), None, make=False))
        self.assertFalse((self.dir / "never").exists())

    def test_free_disk(self):
        du = lambda free: (lambda p: SimpleNamespace(free=free))
        need = 2 * 1000 + U.FREE_DISK_EXTRA
        self.assertIsNone(U.check_free_disk(self.dir, 1000, du(need)))
        self.assertEqual(U.check_free_disk(self.dir, 1000, du(need - 1)), M.DISK_FULL)

        def broken(p):
            raise OSError("made-up")
        self.assertIsNone(U.check_free_disk(self.dir, 1000, broken))
        self.assertIsNone(U.check_free_disk(self.dir, 1000))          # the real drive has room


class CaseFolders(Base):
    def test_make_and_clash(self):
        a = U.make_case_folder(self.dir / "reports", "SAMPLE", "KLP", WHEN, JOB)
        self.assertEqual(a.name, "SAMPLE KLP 2026-10-28 1412")
        self.assertTrue((a / U.marker_name(JOB)).is_file())
        job2 = "f" * 32
        b = U.make_case_folder(self.dir / "reports", "SAMPLE", "KLP", WHEN, job2)
        self.assertEqual(b.name, "SAMPLE KLP 2026-10-28 1412 (2)")
        self.assertTrue(U.remove_marker(a, JOB))
        self.assertFalse(U.remove_marker(a, JOB))
        self.assertTrue(a.is_dir())
        self.assertFalse(U.remove_case_folder_if_ours(a, JOB))        # no marker: never removed
        self.assertTrue(a.is_dir())
        (b / "log.txt").write_text("made-up", encoding="ascii")
        self.assertFalse(U.remove_case_folder_if_ours(b, JOB))        # another job's marker
        self.assertTrue(U.remove_case_folder_if_ours(b, job2))
        self.assertFalse(b.exists())

    def test_remove_tree_never_follows_a_junction(self):
        import _winapi
        outside = self.dir / "outside"
        outside.mkdir()
        keep = outside / "keep.txt"
        keep.write_text("made-up", encoding="ascii")
        case = U.make_case_folder(self.dir / "reports", "SAMPLE", "EAP", WHEN, JOB)
        (case / "work").mkdir()
        _winapi.CreateJunction(str(outside), str(case / "work" / "link"))
        self.assertTrue(U.remove_case_folder_if_ours(case, JOB))
        self.assertFalse(case.exists())
        self.assertTrue(keep.is_file())


class Housekeeping(Base):
    def test_cleans_only_what_it_should(self):
        now = time.time()
        old, new = now - 40 * 86400, now - 2 * 86400
        self.touch("jobs/" + JOB + "/job.json")
        self.touch("tmp/" + JOB + "/ascii/img-0001.jpg")
        self.touch("tmp/loose.tmp")
        self.touch("cache/pagetexts/B-0000.json", mtime=old)
        self.touch("cache/pagetexts/B-0001.json", mtime=new)
        self.touch("cache/verification/text/old.json", mtime=old)
        for i in range(23):
            self.touch(f"logs/worker-{i:032x}.log", mtime=now - 1000 + i)
        self.touch("logs/other.log", mtime=old)
        self.touch("settings.json")
        self.touch("recent.json")
        self.touch("selftest/x.txt", mtime=old)
        outside = self.dir.parent / (self.dir.name + "-outside")
        outside.mkdir()
        (outside / "keep.json").write_text("{}", encoding="ascii")
        os.utime(outside / "keep.json", (old, old))
        try:
            import _winapi
            _winapi.CreateJunction(str(outside), str(self.dir / "cache" / "linked"))
            r = U.housekeeping(self.dir, 30, now)
            self.assertTrue((outside / "keep.json").is_file())        # a junction is never followed
        finally:
            _rmtree(outside)
        self.assertEqual(list((self.dir / "jobs").iterdir()), [])
        self.assertEqual(list((self.dir / "tmp").iterdir()), [])
        self.assertFalse((self.dir / "cache/pagetexts/B-0000.json").exists())
        self.assertTrue((self.dir / "cache/pagetexts/B-0001.json").exists())
        self.assertFalse((self.dir / "cache/verification").exists())  # emptied folders go too
        logs = sorted(p.name for p in (self.dir / "logs").glob("worker-*.log"))
        self.assertEqual(len(logs), 20)
        self.assertNotIn(f"worker-{0:032x}.log", logs)
        self.assertIn(f"worker-{22:032x}.log", logs)
        self.assertTrue((self.dir / "logs/other.log").exists())
        for keep in ("settings.json", "recent.json", "selftest/x.txt"):
            self.assertTrue((self.dir / keep).exists(), keep)
        self.assertEqual(r["jobs"], 1)
        self.assertEqual(r["tmp"], 2)
        self.assertEqual(r["cache"], 2)
        self.assertEqual(r["logs"], 3)
        self.assertEqual(r["errors"], 0)

    def test_missing_folders_and_unsafe_roots(self):
        self.assertEqual(U.housekeeping(self.dir, 30)["errors"], 0)
        self.assertEqual(U.housekeeping("relative\\data", 30), {"jobs": 0, "tmp": 0, "cache": 0, "logs": 0,
                                                                "errors": 0})
        self.assertEqual(U.housekeeping(self.dir / "absent", 30)["jobs"], 0)
        self.assertEqual(U.housekeeping(Path(self.dir.anchor), 30)["jobs"], 0)


class Folders(unittest.TestCase):
    def test_data_dir(self):
        self.assertEqual(U.data_dir({"HDC_DATA_DIR": r"D:\made up\data"}), Path(r"D:\made up\data"))
        self.assertEqual(U.data_dir({"LOCALAPPDATA": r"D:\local"}), Path(r"D:\local\Hangeul Document Checker"))
        self.assertEqual(U.data_dir({}, finder=lambda g: Path(r"D:\kf")), Path(r"D:\kf\Hangeul Document Checker"))
        try:
            from src.verify.runner.app import boot
        except ImportError:
            return
        for env in ({"HDC_DATA_DIR": r"D:\made up\data"}, {"LOCALAPPDATA": r"D:\local"}):
            self.assertEqual(U.data_dir(env), boot.data_dir(env))           # the same DATA as C's boot

    def test_default_report_root(self):
        docs = Path(r"D:\Profile\Documents")
        self.assertEqual(U.default_report_root({}, docs, r"D:\Profile"), docs / "Hangeul Document Checks")
        synced = Path(r"D:\Profile\OneDrive - Made Up Org\Documents")
        self.assertEqual(U.default_report_root({}, synced, r"D:\Profile"),
                         Path(r"D:\Profile\Hangeul Document Checks"))
        env = {"OneDrive": r"D:\Profile\Cloud Files", "USERPROFILE": r"D:\Profile"}
        self.assertEqual(U.default_report_root(env, Path(r"D:\Profile\Cloud Files\Docs")),
                         Path(r"D:\Profile\Hangeul Document Checks"))
        self.assertFalse(U.is_synced(r"D:\Profile\Cloud Files Two\Docs", env))
        self.assertTrue(U.is_synced(r"d:\profile\cloud files", env))

    def test_known_folder(self):
        p = U.known_folder(U.FOLDERID_DOCUMENTS)            # read-only Windows call; nothing is printed
        self.assertIsNotNone(p)
        self.assertTrue(p.is_absolute())
        self.assertTrue(U.documents_dir().is_absolute())

        class NoShell:
            def __getattr__(self, name):
                raise AttributeError(name)
        self.assertIsNone(U.known_folder(U.FOLDERID_DOCUMENTS, shell32=NoShell(), ole32=NoShell()))
        self.assertEqual(U.documents_dir(lambda g: None), Path.home() / "Documents")


if __name__ == "__main__":
    unittest.main()
