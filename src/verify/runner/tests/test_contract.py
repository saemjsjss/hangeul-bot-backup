"""Contract tests (RUNNER_SPEC section 4.5): model.py, strict.py (owner decisions A1 and A2), botimport.py,
the banned loose-name helpers (S8) and the unchanged bot modules (S6).

Every name here is a made-up placeholder ("SAMPLE ONE", "X"); "MD" / "MST" are the abbreviations the
owner's rules talk about, not anybody's name. No personal data.

    <runner venv python> -I -B -m unittest discover -s <worktree>\\src\\verify\\runner\\tests -t <worktree>
"""
from __future__ import annotations

import ast
import dataclasses
import datetime as dt
import itertools
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from src.verify import runner as runner_pkg
from src.verify.runner import botimport as B
from src.verify.runner import model as M
from src.verify.runner import strict as S

RUNNER_DIR = Path(__file__).resolve().parents[1]
BOT_MODULES = ("src/verify/doc_verifier.py", "src/verify/page_checks.py", "src/verify/common.py",
               "src/verify/rules.py", "src/verify/text_cache.py", "src/verify/field_check.py",
               "src/scraper/ocr_validator.py")
BASE_COMMIT = "07b1f22"

# The made-up name pairs the kind table is checked on (RUNNER_SPEC 4.5). (a, b, any_order, dots, kind)
KIND_TABLE = [
    # EQUAL: case, spaces and other marks do not count; every letter and every dot does
    ("MD. SAMPLE ONE", "md.sample one", False, True, "EQUAL"),
    ("A.SAMPLE", "A. SAMPLE", False, True, "EQUAL"),                 # a glued initial with its dot (A6 e)
    ("SAMPLE ONE", ("ONE", "SAMPLE"), True, True, "EQUAL"),         # MRZ-style, surname first
    ("ABC DEF", "ABCDEF", False, True, "EQUAL"),                    # whitespace does not count
    ("SAMPLE-ONE, TWO", "SAMPLE ONE TWO", False, True, "EQUAL"),    # hyphens and commas are removed
    ("MR. SAMPLE ONE", "SAMPLE ONE", False, True, "EQUAL"),         # a leading title is dropped (Q16)
    ("SAMPLE ONE.", "SAMPLE ONE", False, True, "EQUAL"),            # a trailing dot is punctuation (Q16)
    # DOTS: same letters, the dots differ
    ("MD. SAMPLE", "MD SAMPLE", False, True, "DOTS"),
    ("ASAMPLE", "A. SAMPLE", False, True, "DOTS"),
    ("MD.SAMPLE ONE", "MD SAMPLE.ONE", False, True, "DOTS"),        # a dot in another place
    # dots=False compares letters only
    ("MD. SAMPLE", "MD SAMPLE", False, False, "EQUAL"),
    ("ASAMPLE", "A. SAMPLE", False, False, "EQUAL"),
    # OCR: only the look-alikes I/l/1/| and O/0 differ (a description, never a match)
    ("SAMPLE", "SAMPIE", False, True, "OCR"),
    ("SAMPLE", "SAMP1E", False, True, "OCR"),
    ("SAMPLE ONE", "SAMPLE 0NE", False, True, "OCR"),
    # ORDER: the same words in another order, outside a machine read
    ("SAMPLE ONE", "ONE SAMPLE", False, True, "ORDER"),
    # SUBSET: a dropped word, MD included (A1: no subset leniency)
    ("MD SAMPLE ONE", "SAMPLE ONE", False, True, "SUBSET"),
    ("MD SAMPLE ONE", "SAMPLE ONE", False, False, "SUBSET"),
    ("MST SAMPLE ONE", "SAMPLE ONE", False, True, "SUBSET"),
    ("SAMPLE ONE", "MD SAMPLE ONE", True, True, "SUBSET"),
    # LETTERS: a small letter difference
    ("SAMPLE", "SAMPELE", False, True, "LETTERS"),
    ("SAMPLE ONE", "SAMPLA ONE", False, True, "LETTERS"),
    # DIFFERENT: no honorific equivalence (A1), unrelated names
    ("MOHAMMAD SAMPLE", "MD SAMPLE", False, True, "DIFFERENT"),
    ("MOHAMMAD SAMPLE", "MD SAMPLE", True, False, "DIFFERENT"),
    ("SAMPLE ONE", "EXAMPLE TWO", False, True, "DIFFERENT"),
    # EMPTY: a side with no letters
    ("", "SAMPLE", False, True, "EMPTY"),
    ("SAMPLE", "...", False, True, "EMPTY"),
    ("-", "", False, True, "EMPTY"),
    ((), "SAMPLE", True, False, "EMPTY"),
]

NAMES = ["MD. SAMPLE ONE", "MD SAMPLE ONE", "SAMPLE ONE", "SAMPLE.ONE", "ONE SAMPLE", "SAMPIE ONE",
         "MST. SAMPLE", "MST SAMPLE", "A. SAMPLE", "A SAMPLE", "ASAMPLE", "SAMPLE ONE TWO", "MOHAMMAD SAMPLE",
         "md sample one", "  md   sample   one  ", "SAMPLE-ONE", "EXAMPLE TWO", "", "."]


class TestModel(unittest.TestCase):
    def test_versions(self):
        self.assertEqual(M.RUNNER_VERSION, "0.2.6")
        self.assertEqual(M.BASE_RULES_VERSION, "2026-10-21.3")
        self.assertEqual(M.RUNNER_RULES_VERSION, "2026-10-28.r2.6")
        self.assertEqual(M.RUN_SCHEMA, "runner-run/2")
        self.assertEqual(M.PAGE_TEXTS_SCHEMA, "runner-page-texts/1")
        self.assertEqual(runner_pkg.RUNNER_VERSION, M.RUNNER_VERSION)

    def test_labels(self):
        self.assertEqual(M.OVERALL, ("STOP", "FAIL", "FLAG", "PASS"))
        self.assertEqual(M.PROGRAMS, ("KLP", "EAP", "BACHELOR", "MASTER"))
        self.assertEqual(M.NOT_JUDGED, "NOT JUDGED")
        self.assertEqual(M.NEW_CHECK_RULES, ("tin.web_address", "income_tax.tax_office"))

    def test_rank_covers_every_level(self):
        for level in (M.PASS, M.NOTE, M.FLAG, M.FAIL, M.ERROR, M.PRESENT, M.ORDER, M.NOT_JUDGED):
            self.assertIn(level, M.RANK)
        self.assertEqual({M.RANK[x] for x in (M.PASS, M.NOTE, M.PRESENT, M.ORDER)}, {0})
        self.assertLess(M.RANK[M.PASS], M.RANK[M.FLAG])
        self.assertLess(M.RANK[M.FLAG], M.RANK[M.ERROR])
        self.assertLess(M.RANK[M.ERROR], M.RANK[M.FAIL])
        self.assertEqual(M.RANK[M.NOT_JUDGED], M.RANK[M.FLAG])

    def test_portal_view_is_empty_except_program(self):
        ident = M.Identity(student=M.NameRef(parts=("SAMPLE", "ONE"), written="SAMPLE ONE"),
                           father=M.NameRef(), mother=M.NameRef(), passport_no="PLACEHOLDER",
                           dob=dt.date(2000, 1, 1), dob_trusted=True)
        view = M.DerivedRecord(program="KLP", today=dt.date(2026, 1, 1), identity=ident).portal_view()
        self.assertEqual(tuple(view), M.PORTAL_KEYS)
        self.assertEqual(view["Program"], "KLP")
        self.assertEqual({k: v for k, v in view.items() if v}, {"Program": "KLP"})

    def test_defaults_are_independent(self):
        a, b = M.NameRef(), M.NameRef()
        self.assertEqual(a, b)
        i1 = M.Identity(student=a, father=a, mother=a)
        i2 = M.Identity(student=b, father=b, mother=b)
        i1.notes.append(M.Finding(M.FLAG, "x.y", "m", "runner:test"))
        self.assertEqual(i2.notes, [])
        self.assertEqual(i1.father_nid, M.NameRef())
        self.assertIsNot(i1.father_nid, i2.father_nid)
        self.assertEqual(M.BirthFacts().qr_status, "absent")
        self.assertEqual(M.BirthFacts().format, "unknown")
        self.assertEqual(M.SplitResult(units=[], notices=[], missing=[], stop=False).maybe, {})
        n = M.Notice(kind="ORDER", pages=(1, 2), message="m")
        self.assertFalse(n.blocks)
        self.assertEqual(n.units, ())
        self.assertEqual(M.PageRead(0, "f", 0, "", "ocr", 0, False, 0.0, False).engine, "")
        self.assertEqual(M.DocUnit(uid="D01", key="passport", fine="passport", title="PASSPORT",
                                   pages=[0]).level, "")

    def test_contract_fields(self):
        names = lambda c: [f.name for f in dataclasses.fields(c)]   # noqa: E731
        self.assertIn("written", names(M.NameRef))
        self.assertIn("pages", names(M.NameRef))
        for f in ("father_nid", "mother_nid", "name_trusted"):
            self.assertIn(f, names(M.Identity))
        for f in ("reading", "births", "notices", "missing_lines"):
            self.assertIn(f, names(M.RunResult))
        self.assertEqual(names(M.MachineRead), ["unit", "person", "name", "source", "page"])
        self.assertNotIn("deponent", names(M.AffidavitInfo))

    def test_model_and_strict_use_the_standard_library_only(self):
        allowed = {"__future__", "datetime", "dataclasses", "pathlib", "typing", "itertools", "re",
                   "unicodedata"}
        for mod in (M, S):
            tree = ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    roots = {a.name.split(".")[0] for a in node.names}
                elif isinstance(node, ast.ImportFrom):
                    roots = {(node.module or "").split(".")[0]}
                else:
                    continue
                self.assertTrue(roots <= allowed, (mod.__name__, roots))


class TestStrictForm(unittest.TestCase):
    def test_case_and_whitespace_do_not_count(self):
        self.assertEqual(S.form("md.  sample"), S.form("MD.SAMPLE"))
        self.assertEqual(S.form("md.  sample"), "MD.SAMPLE")
        self.assertEqual(S.form("  Md  Sample\tOne \n"), "MDSAMPLEONE")
        self.assertEqual(S.form(("SAMPLE", "ONE")), "SAMPLEONE")

    def test_dots(self):
        self.assertEqual(S.form("MD.. SAMPLE"), "MD.SAMPLE")             # a run of dots is one dot
        self.assertEqual(S.form("MD. . SAMPLE"), "MD.SAMPLE")
        self.assertEqual(S.form(".MD SAMPLE."), "MDSAMPLE")              # leading / trailing dot dropped
        self.assertEqual(S.form("MD. SAMPLE."), "MD.SAMPLE")
        self.assertEqual(S.form("A.B. SAMPLE"), "A.B.SAMPLE")            # inner dots all count

    def test_titles(self):
        self.assertEqual(S.form("LATE MD SAMPLE"), "MDSAMPLE")
        self.assertEqual(S.form("MR. SAMPLE ONE"), "SAMPLEONE")
        self.assertEqual(S.form("MRS SAMPLE ONE"), "SAMPLEONE")
        self.assertEqual(S.form("Late Mr. Sample"), "SAMPLE")
        self.assertEqual(S.form("MST SAMPLE"), "MSTSAMPLE")             # MST is kept
        self.assertEqual(S.form("MD SAMPLE"), "MDSAMPLE")               # MD is kept
        self.assertEqual(S.form("SAMPLE MR ONE"), "SAMPLEMRONE")        # a title only at the start
        self.assertEqual(S.words("DR. SAMPLE"), ("SAMPLE",))

    def test_marks_removed(self):
        self.assertEqual(S.form("SAMPLE-ONE, TWO"), "SAMPLEONETWO")
        self.assertEqual(S.form("SAMPLE (ONE)"), "SAMPLEONE")

    def test_letters_and_dot_pattern(self):
        self.assertEqual(S.letters("MD. SAMPLE ONE"), "MDSAMPLEONE")
        self.assertEqual(S.dot_pattern("MD. X"), (2,))
        self.assertEqual(S.dot_pattern("MD X"), ())
        self.assertEqual(S.dot_pattern("A.B. SAMPLE"), (1, 2))
        self.assertEqual(S.dot_pattern("MD.SAMPLE ONE"), (2,))
        self.assertEqual(S.dot_pattern("MD SAMPLE.ONE"), (8,))
        self.assertEqual(S.dot_pattern("SAMPLE."), ())

    def test_words(self):
        self.assertEqual(S.words("LATE MD. SAMPLE-ONE"), ("MD", "SAMPLE", "ONE"))
        self.assertEqual(S.words("A.SAMPLE"), ("A", "SAMPLE"))
        self.assertEqual(S.words(""), ())
        self.assertIs(S.parts, S.words)


class TestStrictCompare(unittest.TestCase):
    def test_kind_table(self):
        for a, b, any_order, dots, kind in KIND_TABLE:
            with self.subTest(a=a, b=b, any_order=any_order, dots=dots):
                self.assertEqual(S.compare(a, b, any_order=any_order, dots=dots), kind)

    def test_kinds_are_known(self):
        for a, b, any_order, dots, _ in KIND_TABLE:
            self.assertIn(S.compare(a, b, any_order=any_order, dots=dots), S.KINDS)
        self.assertEqual(S.KINDS[0], "EQUAL")
        self.assertEqual(S.OCR_KINDS, frozenset({"DOTS", "OCR"}))

    def test_equal_only_for_identical_form(self):
        """A1: EQUAL is the only match, and only when every letter and every dot agrees."""
        for a, b in itertools.product(NAMES, repeat=2):
            k = S.compare(a, b)
            if S.letters(a) and S.letters(b):
                self.assertEqual(k == "EQUAL", S.form(a) == S.form(b), (a, b, k))
            k_letters = S.compare(a, b, dots=False)
            if S.letters(a) and S.letters(b):
                self.assertEqual(k_letters == "EQUAL", S.letters(a) == S.letters(b), (a, b, k_letters))

    def test_symmetric_kind(self):
        for a, b in itertools.product(NAMES, repeat=2):
            self.assertEqual(S.compare(a, b), S.compare(b, a), (a, b))

    def test_one_letter_or_one_dot_is_a_difference(self):
        self.assertEqual(S.compare("SAMPLE ONE", "SAMPLE ONA"), "LETTERS")
        self.assertEqual(S.compare("SAMPLE ONE", "SAMPLE ON"), "LETTERS")
        self.assertEqual(S.compare("SAMPLE ONE", "SAMPLE ONEE"), "LETTERS")
        self.assertEqual(S.compare("MD. SAMPLE", "MD SAMPLE"), "DOTS")
        self.assertEqual(S.compare("MST. SAMPLE", "MST SAMPLE"), "DOTS")

    def test_no_subset_or_honorific_leniency(self):
        self.assertNotEqual(S.compare("MD SAMPLE ONE", "SAMPLE ONE", dots=False), "EQUAL")
        self.assertNotEqual(S.compare("MD SAMPLE ONE", "SAMPLE ONE", any_order=True, dots=False), "EQUAL")
        self.assertNotEqual(S.compare("MOHAMMAD SAMPLE", "MD SAMPLE", any_order=True, dots=False), "EQUAL")
        self.assertNotEqual(S.compare("MST SAMPLE", "SAMPLE"), "EQUAL")
        self.assertNotEqual(S.compare("SAMPLE ONE TWO", "SAMPLE TWO"), "EQUAL")
        self.assertEqual(S.judge(S.compare("MD SAMPLE ONE", "SAMPLE ONE"), covered=False, confirmed=True), "FAIL")
        self.assertEqual(S.judge(S.compare("MOHAMMAD SAMPLE", "MD SAMPLE"), covered=False, confirmed=True),
                         "FAIL")

    def test_any_order_ignores_dots_and_order_only(self):
        self.assertEqual(S.compare("MD. SAMPLE ONE", ("SAMPLE", "ONE", "MD"), any_order=True), "EQUAL")
        self.assertNotEqual(S.compare("SAMPLE ONE", ("ONE", "SAMPLA"), any_order=True), "EQUAL")
        self.assertEqual(S.compare("SAMPLE ONE", ("SAMPLA", "ONE"), any_order=True), "LETTERS")

    def test_best_match(self):
        cands = ["EXAMPLE TWO", "SAMPLE 0NE", "SAMPLE ONE"]
        self.assertEqual(S.best_match("SAMPLE ONE", cands), (2, "EQUAL"))
        self.assertEqual(S.best_match("SAMPLE ONE", cands[:2]), (1, "OCR"))
        self.assertEqual(S.best_match("MD. SAMPLE", ["MD SAMPLE"]), (0, "EQUAL"))   # dots=False by default
        self.assertEqual(S.best_match("MD. SAMPLE", ["MD SAMPLE"], dots=True), (0, "DOTS"))
        self.assertEqual(S.best_match("SAMPLE", []), (-1, "EMPTY"))


class TestStrictFindRun(unittest.TestCase):
    TEXT = "Name of holder: MD. SAMPLE ONE Father: SAMPLE TWO Address: ONE ROAD"

    def test_contiguous(self):
        self.assertTrue(S.find_run("MD SAMPLE ONE", self.TEXT))
        self.assertTrue(S.find_run("md.sample one", self.TEXT))          # case, dots and spaces aside
        self.assertTrue(S.find_run("SAMPLE TWO", self.TEXT))
        self.assertTrue(S.find_run("SAMPLE ONE", "NAME SAMPLEONE DATE"))  # glued in the text

    def test_not_contiguous_or_partial(self):
        self.assertFalse(S.find_run("SAMPLE ROAD", self.TEXT))
        self.assertFalse(S.find_run("SAMPLE ONE", "NAME SAMPLE TWO ONE DATE"))
        self.assertFalse(S.find_run("SAMPLE ONE", "NAME XSAMPLE ONE"))    # a longer word is not the name
        self.assertFalse(S.find_run("SAMPLE ONE", "NAME SAMPLE ONEX"))
        self.assertFalse(S.find_run("SAMPLE ONA", self.TEXT))             # one letter off
        self.assertFalse(S.find_run("", self.TEXT))

    def test_any_order(self):
        self.assertFalse(S.find_run("SAMPLE ONE", "NAME: ONE SAMPLE DATE"))
        self.assertTrue(S.find_run("SAMPLE ONE", "NAME: ONE SAMPLE DATE", any_order=True))
        self.assertTrue(S.find_run(("ONE", "SAMPLE"), "NAME: SAMPLE ONE DATE", any_order=True))
        self.assertFalse(S.find_run("SAMPLE ONE", "ONE TWO SAMPLE", any_order=True))


class TestStrictJudge(unittest.TestCase):
    def test_truth_table(self):
        for covered, confirmed in itertools.product((False, True), repeat=2):
            self.assertEqual(S.judge("EQUAL", covered, confirmed), "PASS")
            self.assertEqual(S.judge("EMPTY", covered, confirmed), "FLAG")
        for kind in ("OCR", "ORDER", "SUBSET", "LETTERS", "DIFFERENT"):
            with self.subTest(kind=kind):
                self.assertEqual(S.judge(kind, covered=True, confirmed=True), "NOTE")
                self.assertEqual(S.judge(kind, covered=True, confirmed=False), "NOTE")
                self.assertEqual(S.judge(kind, covered=False, confirmed=True), "FAIL")
                self.assertEqual(S.judge(kind, covered=False, confirmed=False), "FLAG")

    def test_dots_raise(self):
        with self.assertRaises(ValueError):
            S.judge("DOTS", covered=False, confirmed=True)

    def test_a_difference_is_never_pass(self):
        for kind in S.KINDS:
            if kind in ("EQUAL", "DOTS"):
                continue
            for covered, confirmed in itertools.product((False, True), repeat=2):
                self.assertNotEqual(S.judge(kind, covered, confirmed), "PASS", kind)


class TestDotRule(unittest.TestCase):
    """A2 (owner, 27 Oct). Dots are judged against the passport's printed name; the other documents'
    confirmed spellings decide whether the dots are mixed."""

    def test_owner_case_1_ref_dot_others_all_plain(self):
        out = S.dot_rule("MD. X", [("A", ["MD X"], True), ("B", ["MD X", "MD X"], True)])
        self.assertEqual(out, {"A": "PASS", "B": "PASS"})

    def test_owner_case_2_ref_dot_others_mixed(self):
        out = S.dot_rule("MD. X", [("A", ["MD. X"], True), ("B", ["MD X"], True)])
        self.assertEqual(out, {"A": "PASS", "B": "FAIL"})
        out = S.dot_rule("MD. X", [("A", ["MD. X"], True), ("B", ["MD X"], True), ("C", ["MD. X"], True)])
        self.assertEqual(out, {"A": "PASS", "B": "FAIL", "C": "PASS"})

    def test_owner_case_3_ref_plain_others_mixed(self):
        out = S.dot_rule("MD X", [("A", ["MD. X"], True), ("B", ["MD X"], True)])
        self.assertEqual(out, {"A": "FAIL", "B": "PASS"})

    def test_owner_case_4_ref_plain_others_all_plain(self):
        out = S.dot_rule("MD X", [("A", ["MD X"], True), ("B", ["MD X"], True)])
        self.assertEqual(out, {"A": "PASS", "B": "PASS"})

    def test_assumed_symmetric_case_q13(self):
        out = S.dot_rule("MD X", [("A", ["MD. X"], True), ("B", ["MD. X"], True)])
        self.assertEqual(out, {"A": "PASS", "B": "PASS"})

    def test_test1_case_unconfirmed_dropped_dot_is_flag_not_fail(self):
        # passport "MD."; document A read "MD." on two pages; document B read "MD" once and "MD." by the
        # zoomed re-read, so B's spelling is not confirmed (A6 b).
        out = S.dot_rule("MD. X", [("A", ["MD. X", "MD. X"], True), ("B", ["MD X", "MD. X"], False)])
        self.assertEqual(out, {"A": "PASS", "B": "FLAG"})

    def test_single_unconfirmed_read_is_flag(self):
        out = S.dot_rule("MD. X", [("A", ["MD. X"], True), ("B", ["MD X"], False)])
        self.assertEqual(out, {"A": "PASS", "B": "FLAG"})

    def test_nothing_confirmed(self):
        out = S.dot_rule("MD. X", [("A", ["MD. X"], False), ("B", ["MD X"], False)])
        self.assertEqual(out, {"A": "PASS", "B": "FLAG"})

    def test_no_reference_mixed_confirmed_is_flag(self):
        for ref in (None, "", "..."):
            out = S.dot_rule(ref, [("A", ["MD. X"], True), ("B", ["MD X"], True)])
            self.assertEqual(out, {"A": "FLAG", "B": "FLAG"}, ref)

    def test_one_document_or_none(self):
        self.assertEqual(S.dot_rule("MD. X", [("A", ["MD X"], True)]), {"A": "PASS"})
        self.assertEqual(S.dot_rule("MD. X", []), {})

    def test_dot_position_counts(self):
        out = S.dot_rule("MD.SAMPLE ONE", [("A", ["MD.SAMPLE ONE"], True), ("B", ["MD SAMPLE.ONE"], True)])
        self.assertEqual(out, {"A": "PASS", "B": "FAIL"})


class TestNoLooseNameHelpers(unittest.TestCase):
    """S8: the loose helpers of the bot and the lab are banned anywhere in the runner package. The
    patterns are written so that this file does not match itself; the one exempt line is this test's
    own `def` line, whose name the spec fixes."""
    BANNED = [r"name_in\(", r"name_tokens\(", r"\btokens\(", r"same_person\(", r"disagreement\(",
              r"name_agreement\("]
    MATCHER = "Sequence" + "Matcher"
    MARK = "# label-match only"
    OWN_DEF = "def test_no_name_in" + "(self):"

    def test_no_name_in(self):
        rx = re.compile("|".join(self.BANNED))
        files = sorted(RUNNER_DIR.rglob("*.py"))
        this = Path(__file__).resolve()
        self.assertIn(this, [f.resolve() for f in files])
        hits = []
        for f in files:
            for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                if f.resolve() == this and line.strip() == self.OWN_DEF:
                    continue
                if rx.search(line):
                    hits.append(f"{f.relative_to(RUNNER_DIR)}:{n} banned helper")
                if self.MATCHER in line and self.MARK not in line:
                    hits.append(f"{f.relative_to(RUNNER_DIR)}:{n} {self.MATCHER} without '{self.MARK}'")
        self.assertEqual(hits, [])

    def test_patterns_catch_what_they_should(self):
        rx = re.compile("|".join(self.BANNED))
        for fn in ("C.name_in", "x = name_tokens", "tokens", "same_person", "names.disagreement",
                   "name_agreement"):
            bad = fn + "(x)"
            self.assertTrue(rx.search(bad), bad)
        for ok in ("strict.words(s)", "my_tokens(s)", "name_input(x)"):
            self.assertFalse(rx.search(ok), ok)


class TestBotImport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Every test module in one `unittest discover` process shares the one load(); use the loaded root
        # when another test loaded first, else the default root under PRIVATE.
        cls.ns = B.load(B._loaded.cache_root if B._loaded is not None else B.DEFAULT_CACHE_ROOT)

    def test_paths_under_cache_root(self):
        ns = self.ns
        self.assertTrue(ns.cache_root.is_relative_to(B.PRIVATE))
        for p in (ns.dv.REPORT_DIR, ns.TC.TEXT_DIR, ns.TC.IMAGES_DIR, ns.PC.APOSTILLE_SUBJECTS, *ns.dv.DOCS_ROOTS):
            self.assertTrue(Path(p).resolve().is_relative_to(ns.cache_root), str(p))

    def test_modules_from_worktree(self):
        import src
        self.assertTrue(Path(src.__file__).resolve().is_relative_to(B.WORKTREE))
        for m in (self.ns.C, self.ns.dv, self.ns.FC, self.ns.PC, self.ns.P, self.ns.R, self.ns.TC, self.ns.OV):
            self.assertTrue(Path(m.__file__).resolve().is_relative_to(B.WORKTREE / "src"), m.__name__)
            self.assertFalse(Path(m.__file__).resolve().is_relative_to(Path(r"C:\Hangeul\BOT")), m.__name__)

    def test_config_is_the_stub(self):
        cfg = sys.modules["src.config"]
        self.assertTrue(getattr(cfg, "_RUNNER_STUB", False))
        self.assertEqual(cfg.settings.verification_dir(), self.ns.cache_root / "verification")
        self.assertEqual(cfg.settings.docs_root(), self.ns.cache_root / "no-docs-root")
        self.assertEqual(cfg.settings.konyang_root(), self.ns.cache_root / "no-docs-root")

    def test_idempotent_and_second_root_raises(self):
        self.assertIs(B.load(self.ns.cache_root), self.ns)
        with self.assertRaises(RuntimeError):
            B.load(self.ns.cache_root / "another-root")

    def test_root_outside_private_raises(self):
        saved = B._loaded
        try:
            B._loaded = None
            with self.assertRaises(RuntimeError):
                B.load(Path(r"C:\Hangeul\DOCCHECK\lab\not-private-runner"))
        finally:
            B._loaded = saved
        self.assertIs(B._loaded, self.ns)

    def test_portal_keys(self):
        self.assertEqual(tuple(M.PORTAL_KEYS), tuple(self.ns.dv.PORTAL_KEYS_READ))

    def test_no_heavy_modules(self):
        for mod in ("torch", "torchvision", "easyocr", "scipy", "skimage"):
            self.assertNotIn(mod, sys.modules, mod)


class TestBotUnchanged(unittest.TestCase):
    """S6: the worktree's bot modules stay byte-identical to commit 07b1f22."""

    def test_bot_unchanged(self):
        git = shutil.which("git")
        if git is None:
            self.skipTest("git not found")
        r = subprocess.run([git, "-C", str(B.WORKTREE), "diff", "--quiet", BASE_COMMIT, "--", *BOT_MODULES],
                           capture_output=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr.decode(errors="replace")[:300])


if __name__ == "__main__":
    unittest.main()
