"""Unit tests of app/sysinfo.py (Task D): scrub, the CPU flags, the Windows facts, the SELFTEST.txt header lines and
the WARNINGS, with made-up facts and fake Windows calls. A few light real calls (memory, modules, a file version) run
on this PC. No card, no torch, no personal data: the user and PC names are made up.

    C:\\Hangeul\\DOCCHECK\\lab\\private-runner\\.venv-runner\\Scripts\\python.exe -I -B -m unittest ^
        src.verify.runner.app.tests.test_sysinfo
"""
from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("HDC_TEST_NO_CARD", "1")

from src.verify.runner.app import sysinfo as S  # noqa: E402

FAKE_ENV = {"USERNAME": "Xqz Yvw", "USERPROFILE": r"C:\Users\Xqz Yvw", "COMPUTERNAME": "MADEUP-PC7"}


class FakeK32:
    def __init__(self, present=(38, 39, 40, 41)):
        self.present = set(present)

    def IsProcessorFeaturePresent(self, code):
        return 1 if code in self.present else 0


def fake_reg(values):
    def reg(key, value):
        return values.get((key.split("\\")[-1], value))
    return reg


class TestScrub(unittest.TestCase):
    def test_quoted_and_bare_paths(self):
        s = S.scrub("FileNotFoundError: [Errno 2] No such file: 'C:\\Users\\Xqz Yvw\\AppData\\x y.json'",
                    environ=FAKE_ENV)
        self.assertEqual(s, "FileNotFoundError: [Errno 2] No such file: '<path>'")
        s = S.scrub("could not open D:\\Some Folder\\file.pdf", environ=FAKE_ENV)
        self.assertEqual(s, "could not open <path>")

    def test_unc_forward_slash_long_and_file_url(self):
        env = FAKE_ENV
        self.assertEqual(S.scrub(r"at \\server\share\doc.pdf", environ=env), "at <path>")
        self.assertEqual(S.scrub("at C:/Temp/x.txt", environ=env), "at <path>")
        self.assertEqual(S.scrub(r"at \\?\C:\very\long", environ=env), "at <path>")
        self.assertEqual(S.scrub("see file:///C:/x/y.pdf now", environ=env), "see <path> now")

    def test_user_and_pc_names(self):
        s = S.scrub("Xqz Yvw on MADEUP-PC7 (xqz yvw)", environ=FAKE_ENV)
        self.assertEqual(s, "<user> on <pc> (<user>)")

    def test_urls_and_relative_paths_stay(self):
        s = S.scrub("https://example.invalid/selftest/0001 and _internal\\torch\\lib\\c10.dll", environ=FAKE_ENV)
        self.assertEqual(s, "https://example.invalid/selftest/0001 and _internal\\torch\\lib\\c10.dll")

    def test_tokens_are_not_scrubbed_again(self):
        env = {"USERNAME": "path", "COMPUTERNAME": "user"}
        self.assertEqual(S.scrub("C:\\x path user", environ=env), "<path>")
        self.assertEqual(S.scrub("a path and a user", environ=env), "a <user> and a <pc>")

    def test_never_raises(self):
        class Bad:
            def __str__(self):
                raise ValueError("no")
        self.assertEqual(S.scrub(Bad(), environ=FAKE_ENV), "<unprintable>")

    def test_holds_private(self):
        self.assertEqual(S.holds_private("all clean here", FAKE_ENV), [])
        self.assertEqual(S.holds_private("at C:\\x\\y", FAKE_ENV), ["path"])
        self.assertEqual(S.holds_private("by xqz yvw on madeup-pc7", FAKE_ENV), ["user", "pc"])


class TestCpuAndWindows(unittest.TestCase):
    def test_cpu_flags(self):
        self.assertEqual(S.cpu_flags(FakeK32()), {"SSE4.2": True, "AVX": True, "AVX2": True, "AVX512F": True})
        self.assertEqual(S.cpu_flags(FakeK32(present=(38, 39))),
                         {"SSE4.2": True, "AVX": True, "AVX2": False, "AVX512F": False})
        self.assertEqual(S.cpu_flags(object()), {"SSE4.2": False, "AVX": False, "AVX2": False, "AVX512F": False})

    def test_merge_flags_with_numpy(self):
        win = {"SSE4.2": False, "AVX": False, "AVX2": False, "AVX512F": False}
        merged = S.merge_flags(win, {"SSE42": True, "AVX2": True, "AVX512F": False})
        self.assertEqual(merged, {"SSE4.2": True, "AVX": False, "AVX2": True, "AVX512F": False})

    def test_windows_info(self):
        reg = fake_reg({("CurrentVersion", "ProductName"): "Windows 10 Pro",
                        ("CurrentVersion", "DisplayVersion"): "22H2",
                        ("CurrentVersion", "CurrentBuildNumber"): "19045", ("CurrentVersion", "UBR"): 1234})
        w = S.windows_info(reg, k32=object())
        self.assertEqual((w["product"], w["display_version"], w["build"], w["ubr"]), ("Windows 10 Pro", "22H2", 19045, 1234))
        reg11 = fake_reg({("CurrentVersion", "ProductName"): "Windows 10 Home",
                          ("CurrentVersion", "DisplayVersion"): "23H2",
                          ("CurrentVersion", "CurrentBuildNumber"): "22631", ("CurrentVersion", "UBR"): 1})
        self.assertEqual(S.windows_info(reg11, k32=object())["product"], "Windows 11 Home")

    def test_smart_app_control_and_long_paths(self):
        self.assertEqual(S.smart_app_control(fake_reg({})), "not available")
        for v, want in ((0, "off"), (1, "on"), (2, "evaluation")):
            self.assertEqual(S.smart_app_control(fake_reg({("Policy", "VerifiedAndReputablePolicyState"): v})), want)
        self.assertIsNone(S.long_paths_enabled(fake_reg({})))
        self.assertTrue(S.long_paths_enabled(fake_reg({("FileSystem", "LongPathsEnabled"): 1})))
        self.assertFalse(S.long_paths_enabled(fake_reg({("FileSystem", "LongPathsEnabled"): 0})))
        self.assertEqual(S.cpu_name(fake_reg({("0", "ProcessorNameString"): "  Made-up  CPU 9000 "})),
                         "Made-up CPU 9000")

    def test_path_facts(self):
        self.assertEqual(S.path_facts("C:\\A b\\\u00dcx", True),
                         {"chars": 9, "spaces": True, "non_ascii": True, "overridden": True})
        self.assertEqual(S.path_facts("C:\\Ab", False)["spaces"], False)

    def test_short_version(self):
        self.assertEqual(S.short_version("14.50.35719.0"), "14.50")
        self.assertEqual(S.short_version("9.19.0.12", 3), "9.19.0")
        self.assertEqual(S.short_version(None), "?")


def made_up_facts(**over):
    f = {"windows": {"product": "Windows 10 Pro", "display_version": "22H2", "build": 19045, "ubr": 6456,
                     "arch": "x64"},
         "smart_app_control": "off", "long_paths": False,
         "cpu": {"name": "Made-up CPU 9000", "cores": 6, "threads": 12,
                 "flags": {"SSE4.2": True, "AVX": True, "AVX2": True, "AVX512F": True}},
         "ram": {"total_mb": 31949, "free_mb": 22938}, "disk_free_gb": 210.4,
         "data_path": {"chars": 38, "spaces": False, "non_ascii": False, "overridden": False},
         "card": {"nvml": True, "count": 1, "name": "NVIDIA GeForce RTX 0000", "total_mb": 8151, "free_mb": 6120},
         "card_line": "Graphics card: NVIDIA GeForce RTX 0000 | 8.0 GB total, 6.0 GB free | driver 000.00 (CUDA 12.9)"
                      " | compute 12.0 (this build: 7.5-12.0)"}
    f.update(over)
    return f


class TestLinesAndWarnings(unittest.TestCase):
    def test_lines(self):
        lines = S.lines(made_up_facts())
        self.assertEqual(lines, [
            "Windows: Windows 10 Pro 22H2, build 19045.6456, x64 | Smart App Control: off | long paths enabled: no",
            "CPU: Made-up CPU 9000 | 6 cores, 12 threads | SSE4.2 yes, AVX yes, AVX2 yes, AVX512F yes",
            "RAM: 31.2 GB total, 22.4 GB free | free disk (app data drive): 210 GB",
            "App data path: 38 characters, spaces: no, non-ASCII letters: no | overridden: no",
            "Graphics card: NVIDIA GeForce RTX 0000 | 8.0 GB total, 6.0 GB free | driver 000.00 (CUDA 12.9) | "
            "compute 12.0 (this build: 7.5-12.0)"])

    def test_lines_with_unknowns(self):
        f = made_up_facts(ram={"total_mb": None, "free_mb": None}, disk_free_gb=None, long_paths=None)
        f["cpu"]["cores"] = None
        lines = S.lines(f)
        self.assertIn("long paths enabled: ?", lines[0])
        self.assertIn("? cores, 12 threads", lines[1])
        self.assertEqual(lines[2], "RAM: ? GB total, ? GB free | free disk (app data drive): ? GB")

    def test_numpy_flags_fill_in(self):
        f = made_up_facts()
        f["cpu"]["flags"] = {"SSE4.2": False, "AVX": False, "AVX2": False, "AVX512F": False}
        self.assertIn("SSE4.2 yes, AVX yes, AVX2 yes, AVX512F no",
                      S.lines(f, {"SSE42": True, "AVX": True, "AVX2": True, "AVX512F": False})[1])
        self.assertNotIn("the processor has no AVX2: reading will be slower",
                         S.warnings(f, {"SSE42": True, "AVX": True, "AVX2": True}))

    def test_no_warnings_on_a_good_pc(self):
        self.assertEqual(S.warnings(made_up_facts()), [])

    def test_warnings(self):
        f = made_up_facts(ram={"total_mb": 7800, "free_mb": 2000}, smart_app_control="on",
                          data_path={"chars": 120, "spaces": True, "non_ascii": False, "overridden": False},
                          card={"nvml": True, "count": 1, "name": "NVIDIA GeForce RTX 0000 Laptop GPU",
                                "total_mb": 6144, "free_mb": 4000})
        f["windows"]["build"] = 17763
        f["cpu"]["flags"]["AVX2"] = False
        w = S.warnings(f)
        self.assertEqual(len(w), 8, w)
        self.assertIn("the processor has no AVX2: reading will be slower", w)
        self.assertTrue(any("7.6 GB of RAM" in x for x in w))
        self.assertTrue(any("2.0 GB of RAM free" in x for x in w))
        self.assertTrue(any("17763" in x and "not tested" in x for x in w))
        self.assertTrue(any("Smart App Control is on" in x for x in w))
        self.assertTrue(any("120 characters" in x for x in w))
        self.assertTrue(any("3.9 GB of the graphics card's memory is free now: a check may wait" in x for x in w))
        self.assertIn("a laptop graphics card with under 8 GB: large pages may be read on the processor", w)

    def test_no_card_no_card_warnings(self):
        f = made_up_facts(card={"nvml": False, "count": 0})
        self.assertEqual(S.warnings(f), [])

    def test_collect_with_fake_card(self):
        card = SimpleNamespace(nvml=True, count=1, name="NVIDIA GeForce RTX 0000", driver="000.00",
                               cuda_driver=12090, cc=(12, 0), total_mb=8151, free_mb=6120)
        seen = {}

        def line(info, arch):
            seen["arch"] = arch
            return "Graphics card: made-up line at C:\\Users\\Xqz Yvw\\x"

        with mock.patch.dict(os.environ, FAKE_ENV):
            f = S.collect("D:\\made up\\data", overridden=True, arch_flags=["sm_75"], card_probe=lambda: card,
                          card_line=line, reg=fake_reg({}), k32=object())
        self.assertEqual(seen["arch"], ["sm_75"])
        self.assertEqual(f["card_line"], "Graphics card: made-up line at <path>")
        self.assertEqual(f["card"]["cc"], [12, 0])
        self.assertEqual(f["data_path"], {"chars": 15, "spaces": True, "non_ascii": False, "overridden": True})
        self.assertNotIn("made up\\data", repr(f))

    def test_collect_card_probe_failure(self):
        def boom():
            raise RuntimeError("no")
        f = S.collect("D:\\x", card_probe=boom, reg=fake_reg({}), k32=object())
        self.assertEqual(f["card_line"], "Graphics card: not read")
        self.assertEqual(f["card"], {})


@unittest.skipUnless(sys.platform == "win32", "Windows calls")
class TestRealCallsOnThisPC(unittest.TestCase):
    """Light real calls (no card, nothing written)."""

    def test_memory_and_cpu(self):
        total, free = S.memory_mb()
        self.assertGreater(total, 1000)
        self.assertGreater(free, 0)
        cores, threads = S.cpu_counts()
        self.assertGreaterEqual(threads, cores)
        flags = S.cpu_flags()
        self.assertEqual(set(flags), {"SSE4.2", "AVX", "AVX2", "AVX512F"})
        self.assertIn(S.native_arch(), ("x64", "ARM64"))

    def test_modules_versions_and_peak(self):
        mods = S.loaded_modules()
        self.assertTrue(any(os.path.basename(m).lower() == "kernel32.dll" for m in mods))
        self.assertTrue(any(os.path.basename(m).lower().startswith("python3") for m in mods))
        k32 = next(m for m in mods if os.path.basename(m).lower() == "kernel32.dll")
        v = S.file_version(k32)
        self.assertRegex(v or "", r"^\d+\.\d+\.\d+\.\d+$")
        self.assertIsNone(S.file_version(os.path.join(os.path.dirname(k32), "no-such-file-here.dll")))
        self.assertGreater(S.peak_working_set_mb() or 0, 1)

    def test_windows_facts_read(self):
        w = S.windows_info()
        self.assertGreater(w["build"], 10000)
        self.assertIn(S.smart_app_control(), ("off", "on", "evaluation", "not available"))


if __name__ == "__main__":
    unittest.main()
