"""Task B: app/recent.py (APP_SPEC rev 1 section 3.2: the last 15 checks). Made-up names and paths only.

Scratch files: C:\\Hangeul\\DOCCHECK\\lab\\private-runner\\selftest\\app\\B\\recent-<pid>-<n> (removed).
Run: HDC_TEST_NO_CARD=1 <runner venv python> -I -B -m unittest src.verify.runner.app.tests.test_recent
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import time
import unittest
import uuid
from pathlib import Path

from src.verify.runner.app import recent as R

SCRATCH = Path(os.environ.get("HDC_TEST_SCRATCH", r"C:\Hangeul\DOCCHECK\lab\private-runner\selftest\app\B"))
WHEN = dt.datetime(2026, 10, 28, 14, 12)


def _rmtree(p: Path) -> None:
    for _ in range(6):
        shutil.rmtree(p, ignore_errors=True)
        if not p.exists():
            return
        time.sleep(0.3)


def item(n: int, overall: str = "FAIL", code: str = "3F2A-91C0") -> R.RecentItem:
    return R.make(WHEN + dt.timedelta(minutes=n), rf"D:\Made Up\in\SAMPLE_{n:04d}.pdf", "KLP", overall, code,
                  rf"D:\Made Up\reports\SAMPLE_{n:04d} KLP 2026-10-28 1412")


class Recent(unittest.TestCase):
    def setUp(self):
        self.dir = SCRATCH / f"recent-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self.dir.mkdir(parents=True)

    def tearDown(self):
        _rmtree(self.dir)

    def test_make_keeps_the_base_name_only(self):
        it = item(1)
        self.assertEqual(it.input, "SAMPLE_0001.pdf")
        self.assertEqual(it.time, "2026-10-28T14:13")
        self.assertEqual(R.base_name("D:\\Made Up\\folder\\"), "folder")
        self.assertEqual(R.base_name("D:\\"), "")
        self.assertEqual(R.base_name(None), "")

    def test_display(self):
        self.assertEqual(R.display(item(0)), "28 Oct 14:12  SAMPLE_0000.pdf  KLP  FAIL  3F2A-91C0")
        b = R.make(WHEN, r"D:\x\SAMPLE FOLDER", "BACHELOR", "PASS", "", r"D:\x\out")
        self.assertEqual(R.display(b), "28 Oct 14:12  SAMPLE FOLDER  Bachelor's  PASS")

    def test_add_newest_first_dedupe_and_keep_15(self):
        items = []
        for n in range(20):
            items = R.add(items, item(n))
        self.assertEqual(len(items), R.KEEP)
        self.assertEqual(items[0].input, "SAMPLE_0019.pdf")
        self.assertEqual(items[-1].input, "SAMPLE_0005.pdf")
        again = R.make(WHEN, r"D:\Made Up\in\SAMPLE_0010.pdf", "KLP", "PASS", "0000-0000",
                       r"d:\made up\reports\SAMPLE_0010 KLP 2026-10-28 1412")       # same folder, other case
        items = R.add(items, again)
        self.assertEqual(len(items), R.KEEP)
        self.assertEqual(items[0].overall, "PASS")
        self.assertEqual(sum(1 for i in items if i.input == "SAMPLE_0010.pdf"), 1)

    def test_save_load_round_trip(self):
        items = [item(n) for n in range(3)]
        R.save(self.dir, items)
        self.assertFalse((self.dir / (R.FILE_NAME + ".tmp")).exists())
        self.assertEqual(R.load(self.dir), items)
        raw = json.loads((self.dir / R.FILE_NAME).read_text(encoding="utf-8"))
        self.assertEqual(raw["schema"], R.SCHEMA)
        self.assertEqual(set(raw["items"][0]), {"time", "input", "program", "overall", "code", "folder"})

    def test_bad_files_and_items(self):
        self.assertEqual(R.load(self.dir), [])                               # no file
        (self.dir / R.FILE_NAME).write_text("{junk", encoding="utf-8")
        self.assertEqual(R.load(self.dir), [])
        (self.dir / R.FILE_NAME).write_text(json.dumps({"schema": "other", "items": []}), encoding="utf-8")
        self.assertEqual(R.load(self.dir), [])
        good = {"time": "2026-10-28T14:12", "input": "SAMPLE.pdf", "program": "KLP", "overall": "FLAG",
                "code": "0A0B-0C0D", "folder": r"D:\Made Up\out"}
        bad = [dict(good, time="yesterday"), dict(good, program="PHD"), dict(good, overall="MAYBE"),
               dict(good, code="xyz"), dict(good, folder="relative\\out"), {"time": "2026-10-28T14:12"}, "x", None,
               dict(good, input=r"D:\secret\folder\SAMPLE.pdf")]
        (self.dir / R.FILE_NAME).write_text(json.dumps({"schema": R.SCHEMA, "items": bad + [good] * 20}),
                                            encoding="utf-8")
        got = R.load(self.dir)
        self.assertEqual(len(got), R.KEEP)
        self.assertEqual(got[0].input, "SAMPLE.pdf")              # the folder part of a stored input is dropped
        self.assertTrue(all(g.overall in R.OVERALLS for g in got))

    def test_remove(self):
        items = [item(n) for n in range(3)]
        self.assertEqual(len(R.remove(items, 1)), 2)
        self.assertEqual(len(R.remove(items, 9)), 3)


if __name__ == "__main__":
    unittest.main()
