"""Task B: app/settingsfile.py (APP_SPEC rev 1 section 3.2, rev 2 R2.2 / R2.3.2). Made-up paths only.

Scratch files: C:\\Hangeul\\DOCCHECK\\lab\\private-runner\\selftest\\app\\B\\settings-<pid>-<n> (removed).
Run: HDC_TEST_NO_CARD=1 <runner venv python> -I -B -m unittest src.verify.runner.app.tests.test_settingsfile
"""
from __future__ import annotations

import json
import os
import shutil
import time
import unittest
import uuid
from pathlib import Path

from src.verify.runner.app import settingsfile as S

SCRATCH = Path(os.environ.get("HDC_TEST_SCRATCH", r"C:\Hangeul\DOCCHECK\lab\private-runner\selftest\app\B"))
LOCK_A = r"D:\Made Up\locks\sample.lock"
LOCK_B = r"E:\other\made-up.lock"


def _rmtree(p: Path) -> None:
    for _ in range(6):
        shutil.rmtree(p, ignore_errors=True)
        if not p.exists():
            return
        time.sleep(0.3)


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = SCRATCH / f"settings-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self.dir.mkdir(parents=True)

    def tearDown(self):
        _rmtree(self.dir)

    def write(self, data):
        (self.dir / S.FILE_NAME).write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")

    def stored(self):
        return json.loads((self.dir / S.FILE_NAME).read_text(encoding="utf-8"))


class Defaults(Base):
    def test_first_start_takes_the_default_lock(self):
        s = S.load(self.dir, lambda: LOCK_A)
        self.assertTrue(s.first_start)
        self.assertTrue(s.dirty)
        self.assertFalse(s.recovered)
        self.assertEqual(s.gpu_lock_path, os.path.normpath(LOCK_A))
        self.assertEqual(s.last_program, "KLP")
        self.assertEqual(s.seconds_per_page_gpu, 3.0)
        self.assertEqual(s.cache_days, 30)
        self.assertIsNone(s.report_root)

    def test_first_start_without_the_bot_folder(self):
        self.assertIsNone(S.load(self.dir, lambda: None).gpu_lock_path)
        self.assertIsNone(S.load(self.dir, None).gpu_lock_path)

    def test_a_failing_default_is_off(self):
        def boom():
            raise OSError("made-up")
        self.assertIsNone(S.load(self.dir, boom).gpu_lock_path)

    def test_a_relative_default_is_refused(self):
        self.assertIsNone(S.load(self.dir, lambda: "relative\\x.lock").gpu_lock_path)

    def test_save_then_load(self):
        s = S.load(self.dir, lambda: LOCK_A)
        s.report_root = r"D:\Made Up Reports"
        s.last_program = "MASTER"
        S.save(self.dir, s)
        self.assertFalse(s.dirty)
        self.assertFalse((self.dir / (S.FILE_NAME + ".tmp")).exists())
        raw = self.stored()
        self.assertEqual(raw["schema"], S.SCHEMA)
        self.assertEqual(raw["gpu_lock_path"], os.path.normpath(LOCK_A))
        self.assertEqual(raw["seconds_per_page_gpu"], 3.0)
        t = S.load(self.dir, lambda: LOCK_B)               # not the first start any more: the default is not asked
        self.assertFalse(t.first_start)
        self.assertFalse(t.dirty)
        self.assertEqual(t.gpu_lock_path, os.path.normpath(LOCK_A))
        self.assertEqual(t.report_root, r"D:\Made Up Reports")
        self.assertEqual(t.last_program, "MASTER")

    def test_off_stays_off(self):
        s = S.load(self.dir, lambda: LOCK_A)
        self.assertTrue(S.set_lock_path(s, None))
        S.save(self.dir, s)
        self.assertIsNone(self.stored()["gpu_lock_path"])
        self.assertIn("gpu_lock_path", self.stored())
        t = S.load(self.dir, lambda: LOCK_A)
        self.assertIsNone(t.gpu_lock_path)
        self.assertFalse(t.first_start)

    def test_change_lock(self):
        s = S.load(self.dir, lambda: None)
        self.assertTrue(S.set_lock_path(s, LOCK_B))
        self.assertEqual(s.gpu_lock_path, os.path.normpath(LOCK_B))
        self.assertFalse(S.set_lock_path(s, "not absolute.lock"))
        self.assertEqual(s.gpu_lock_path, os.path.normpath(LOCK_B))
        self.assertTrue(S.set_lock_path(s, "\\\\server\\share\\made-up.lock"))

    def test_entry_reads_the_same_key(self):
        """C's --cli path reads "gpu_lock_path" from this file (skipped while entry.py is absent)."""
        try:
            from src.verify.runner.app import entry
        except ImportError:
            self.skipTest("entry.py not there yet")
        s = S.load(self.dir, lambda: LOCK_A)
        S.save(self.dir, s)
        self.assertEqual([os.path.normcase(p) for p in entry.lock_paths_for_cli(self.dir, [], default_setting=None)],
                         [os.path.normcase(os.path.normpath(LOCK_A))])
        S.set_lock_path(s, None)
        S.save(self.dir, s)
        self.assertEqual(entry.lock_paths_for_cli(self.dir, [], default_setting=LOCK_B), [])


class Migration(Base):
    def test_rev1_file(self):
        self.write({"report_root": r"D:\Made Up Reports", "last_program": "Master's", "seconds_per_page": 6.3,
                    "cache_days": 45, "future_key": {"a": 1}})
        s = S.load(self.dir, lambda: LOCK_A)
        self.assertEqual(s.migrated_from, "none")
        self.assertTrue(s.first_start)
        self.assertTrue(s.dirty)
        self.assertEqual(s.report_root, r"D:\Made Up Reports")
        self.assertEqual(s.last_program, "MASTER")
        self.assertEqual(s.cache_days, 45)
        self.assertEqual(s.seconds_per_page_gpu, 3.0)          # never the CPU number
        self.assertEqual(s.gpu_lock_path, os.path.normpath(LOCK_A))
        S.save(self.dir, s)
        raw = self.stored()
        self.assertNotIn("seconds_per_page", raw)
        self.assertEqual(raw["future_key"], {"a": 1})
        self.assertEqual(raw["schema"], S.SCHEMA)

    def test_rev1_schema_named(self):
        self.write({"schema": "hdc-settings/1", "last_program": "EAP"})
        s = S.load(self.dir, lambda: None)
        self.assertEqual(s.migrated_from, "hdc-settings/1")
        self.assertEqual(s.last_program, "EAP")

    def test_damaged_file(self):
        for junk in ("{not json", "[1, 2]", "null", ""):
            self.write(junk)
            s = S.load(self.dir, lambda: LOCK_A)
            self.assertTrue(s.recovered, junk)
            self.assertTrue(s.first_start, junk)
            self.assertEqual(s.gpu_lock_path, os.path.normpath(LOCK_A), junk)
            self.assertEqual(s.last_program, "KLP")

    def test_bad_values_fall_back(self):
        self.write({"schema": S.SCHEMA, "report_root": "relative\\reports", "last_program": "PHD",
                    "seconds_per_page_gpu": -2, "pages_measured_gpu": 7, "cache_days": True,
                    "gpu_lock_path": "not\\absolute.lock"})
        s = S.load(self.dir, lambda: LOCK_B)
        self.assertIsNone(s.report_root)
        self.assertEqual(s.last_program, "KLP")
        self.assertEqual(s.seconds_per_page_gpu, 3.0)
        self.assertEqual(s.pages_measured_gpu, 0)
        self.assertEqual(s.cache_days, 30)
        self.assertEqual(s.gpu_lock_path, os.path.normpath(LOCK_B))     # a damaged value: the default again
        self.assertTrue(s.dirty)
        for bad in (float("inf"), "3.0", None, 10_000):
            self.write({"schema": S.SCHEMA, "seconds_per_page_gpu": bad, "gpu_lock_path": None})
            self.assertEqual(S.load(self.dir).seconds_per_page_gpu, 3.0, bad)


class Updates(unittest.TestCase):
    def test_programs(self):
        for v, want in (("KLP", "KLP"), ("eap", "EAP"), ("Bachelor's", "BACHELOR"), ("BACHELORS", "BACHELOR"),
                        ("Master's", "MASTER"), ("master", "MASTER"), ("PhD", None), (None, None), (3, None)):
            self.assertEqual(S.program_code(v), want, v)
        s = S.Settings()
        self.assertTrue(S.set_program(s, "Master's"))
        self.assertEqual(s.last_program, "MASTER")
        self.assertFalse(S.set_program(s, "PhD"))

    def test_page_average(self):
        s = S.Settings()
        self.assertFalse(S.add_page_seconds(s, []))
        self.assertFalse(S.add_page_seconds(s, [-1, float("nan"), "x"]))
        self.assertTrue(S.add_page_seconds(s, [1.0, 2.0, 3.0]))
        self.assertAlmostEqual(s.seconds_per_page_gpu, 2.0)          # nothing measured before: this PC's pages
        self.assertEqual(s.pages_measured_gpu, 3)
        S.add_page_seconds(s, [5.0])
        self.assertAlmostEqual(s.seconds_per_page_gpu, (2.0 * 3 + 5.0) / 4)
        s2 = S.Settings(seconds_per_page_gpu=1.0, pages_measured_gpu=S.AVERAGE_CAP)
        S.add_page_seconds(s2, [11.0] * S.AVERAGE_CAP)
        self.assertAlmostEqual(s2.seconds_per_page_gpu, 6.0)
        self.assertEqual(s2.pages_measured_gpu, S.AVERAGE_CAP)

    def test_report_root(self):
        s = S.Settings()
        self.assertTrue(S.set_report_root(s, r"D:\Made Up"))
        self.assertEqual(s.report_root, r"D:\Made Up")
        self.assertFalse(S.set_report_root(s, "relative"))
        self.assertTrue(S.set_report_root(s, None))
        self.assertIsNone(s.report_root)

    def test_valid_abs_path(self):
        self.assertEqual(S.valid_abs_path("D:/a/b.lock"), os.path.normpath("D:/a/b.lock"))
        for bad in ("", "a.lock", "\\a.lock", "D:a.lock", "D:\\a\x00b", 5, None, "D:\\" + "x" * 2000):
            self.assertIsNone(S.valid_abs_path(bad), bad)


if __name__ == "__main__":
    unittest.main()
