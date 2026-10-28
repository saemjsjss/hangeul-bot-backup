"""Integration contracts between the build tasks (APP_SPEC rev 2, R2.7 "Integration"), pinned on fakes only.

    set HDC_TEST_NO_CARD=1
    <runner python> -I -B -m unittest discover -s packaging\\tests -p test_integration.py

Each test names the two tasks whose files must agree: the command lines H's harnesses and F's build give the app
against C's parser, H's job.json against C's validator, E's and T's card-architecture rules, H's trace checker
against A's guard, the module skip lists of F's spec and D's import smoke, and F's self-test part count. No card, no
torch, no PyInstaller, no frozen app; nothing is written. Every path and value here is made up.
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import ast  # noqa: E402
import os  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402

os.environ.setdefault("HDC_TEST_NO_CARD", "1")
HERE = Path(__file__).resolve().parent
PKG = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(1, str(PKG))

import build  # noqa: E402
import cardrun  # noqa: E402
import parity  # noqa: E402
import politeness  # noqa: E402
import trace_check  # noqa: E402

from src.verify.runner import safety  # noqa: E402
from src.verify.runner.app import entry, gpuengine, gpuinfo, protocol, selftest  # noqa: E402

EXE = Path(r"Q:\made-up\Hangeul Document Checker\Hangeul Document Checker.exe")
ROOT = Path(r"Q:\made-up\apptest")


class CommandLines(unittest.TestCase):
    """H (cardrun) and F (build.py) build argv lists; C (entry.parse) must accept them as meant."""

    def test_cli_cmd(self):
        cmd = cardrun.cli_cmd(EXE, r"Q:\made-up\in\CASE.pdf", "KLP", ROOT / "out", "2026-10-29", ROOT / "data",
                              [ROOT / "fakelock" / "auto_verify.lock"])
        o = entry.parse(cmd[1:])
        self.assertEqual((o.role, o.program, o.today), ("cli", "KLP", "2026-10-29"))
        self.assertEqual((o.out, o.data_dir), (str(ROOT / "out"), str(ROOT / "data")))
        self.assertEqual(o.lock_extra, [str(ROOT / "fakelock" / "auto_verify.lock")])
        self.assertEqual(o.path, r"Q:\made-up\in\CASE.pdf")

    def test_auto_cmd(self):
        cmd = cardrun.auto_cmd(EXE, r"Q:\made-up\in\CASE.pdf", "KLP", "2026-10-29", ROOT / "rep",
                               ROOT / "auto.json", ROOT / "data", ROOT / "trace")
        o = entry.parse(cmd[1:])
        self.assertEqual((o.role, o.report_root, o.auto_result, o.trace_dir),
                         ("auto", str(ROOT / "rep"), str(ROOT / "auto.json"), str(ROOT / "trace")))

    def test_build_check_commands(self):
        o = entry.parse(["--selftest-part", "imports", str(ROOT / "reports" / "imports.json")])
        self.assertEqual((o.role, o.part, o.part_result), ("selftest-part", "imports", str(ROOT / "reports" /
                                                                                         "imports.json")))
        self.assertIn(o.part, selftest.PART_FUNCS)            # D has the part F's check runs (test 8 g)
        self.assertNotIn("imports", selftest.PARTS)           # ... and it is not one of the nine
        o = entry.parse(["--data-dir", str(ROOT / "sd"), "--selftest", "--report-dir", str(ROOT / "st")])
        self.assertEqual((o.role, o.data_dir, o.report_dir), ("selftest", str(ROOT / "sd"), str(ROOT / "st")))
        self.assertEqual(build.SELFTEST_PARTS, len(selftest.PARTS))

    def test_politeness_job_passes_the_validator(self):
        case = parity.R8Case(label="made-up", desc="made up", dir=ROOT / "r8", input=r"Q:\made-up\in\CASE.pdf",
                             program="KLP", today="2026-10-29", case_id="CASE-0", mode="bundle")
        job = politeness.worker_job(case, ROOT / "out", ROOT / "data", [ROOT / "fakelock" / "auto_verify.lock"])
        checked = protocol.check_job(dict(job))
        self.assertEqual(checked["gpu"]["lock_paths"], [str(ROOT / "fakelock" / "auto_verify.lock")])


class CardRules(unittest.TestCase):
    """E (gpuinfo.arch_ok, before torch) and T (gpuengine.arch_supported, after torch) judge a card alike."""

    def test_arch_rules_agree(self):
        flags = list(gpuinfo.TORCH_ARCH_FLAGS)
        for major in range(5, 14):
            for minor in range(0, 10):
                cc = (major, minor)
                self.assertEqual(gpuinfo.arch_ok(cc, flags), gpuengine.arch_supported(cc, flags), cc)
        self.assertTrue(gpuinfo.arch_ok((8, 9), flags))       # an RTX 40 card runs sm_86 code
        self.assertFalse(gpuinfo.arch_ok((6, 1), flags))


class TraceAndGuard(unittest.TestCase):
    """H's trace checker fails exactly the socket events A's guard refuses as network contact (C's trace marks
    them refused=true); socket.__new__ / socket.gethostname are not network contact."""

    def test_network_socket_events(self):
        guard = {e for e in safety._NETWORK_EVENTS if e.startswith("socket.")}
        self.assertEqual(set(trace_check.NETWORK_SOCKET_EVENTS), guard)

    def test_refusal_names_the_guard(self):
        """D's self-test part "guard" counts a refusal only when its text names the guard (A's wording). Called on
        a made-up policy object: nothing is armed in this process."""
        from types import SimpleNamespace
        pol = SimpleNamespace(state=SimpleNamespace(blocked_total=0, blocked=[]))
        with self.assertRaises(PermissionError) as cm:
            safety._refuse(pol, "socket.connect", "made-up", "network access is disabled in the runner")
        self.assertIn("guard", str(cm.exception).lower())


class SkipLists(unittest.TestCase):
    """F's spec (hidden imports) and D's import smoke leave out the same DEV and test packages."""

    def test_spec_skip_equals_selftest_skip(self):
        tree = ast.parse((PKG / "hdc.spec").read_text(encoding="utf-8"))
        skip = None
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "SKIP" for t in node.targets):
                skip = ast.literal_eval(node.value)
        self.assertIsNotNone(skip)
        self.assertEqual(set(skip), set(selftest.IMPORT_SKIP))


if __name__ == "__main__":
    unittest.main()
