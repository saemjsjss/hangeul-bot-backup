"""The closer-look evaluation harness (selftest/closer_eval.py) with a STUB closer look (closer.Look's shape),
made-up names and numbers only. Synthetic images are rendered into
C:\\Hangeul\\DOCCHECK\\lab\\private-runner\\tmp\\test_closer_eval-<pid>-<n> and removed at the end.
"""
from __future__ import annotations

import json
import os
import shutil
import unittest
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

from src.verify.runner import botimport
from src.verify.runner.selftest import closer_eval as CE

SCRATCH = botimport.PRIVATE / "tmp"


@dataclass
class Look:                                       # the shape of closer.Look (shared contract)
    decision: str
    best: str
    scores: Dict[str, float] = field(default_factory=dict)
    margin: float = 0.0
    variants: int = 1
    agree: int = 1
    dot: Optional[bool] = None
    note: str = ""


def _case(cid="X1", kind="name", cands=("ZOREK VAMUL", "ZOREK VAMLL"), answer="same", printed=None, dot=None,
          set_="synth"):
    c = {"id": cid, "set": set_, "kind": kind, "candidates": list(cands),
         "truth": {"answer": answer, "printed": printed if printed is not None else
                   (cands[0] if answer == "same" else cands[1]), "dot": dot},
         "category": "synthetic", "diff_type": "one_letter_confusable", "degradations": []}
    if set_ == "synth":
        c["box_px"] = [0, 0, 10, 10]
    else:
        c["box"] = [0.1, 0.1, 0.2, 0.2]
    return c


def _look(decision, cands, scores, dot=None):
    best = max(scores, key=scores.get) if scores else cands[0]
    vals = sorted(scores.values(), reverse=True)
    return Look(decision, best, dict(scores), (vals[0] - vals[1]) if len(vals) > 1 else 0.0, 4, 4, dot, "stub")


class TestForms(unittest.TestCase):
    def test_norms(self):
        self.assertEqual(CE.norm_letters("Zorek. vamul<<"), "ZOREKVAMUL")
        self.assertEqual(CE.norm_dots("Md. Zorek"), CE.norm_dots("MD.ZOREK"))
        self.assertNotEqual(CE.norm_dots("Md. Zorek"), CE.norm_dots("MD ZOREK"))
        self.assertEqual(CE.norm_digits("123 456-7890"), "1234567890")
        self.assertTrue(CE.same_form("name", "Zorek Vamul", "ZOREK VAMUL"))
        self.assertFalse(CE.same_form("name", "ZOREK VAMUL", "VAMUL ZOREK"))

    def test_dot_after_first(self):
        self.assertTrue(CE.dot_after_first("Md. Zorek"))
        self.assertTrue(CE.dot_after_first("M.Zorek Vamul"))
        self.assertFalse(CE.dot_after_first("MD ZOREK"))
        self.assertFalse(CE.dot_after_first("M Zorek"))

    def test_dedupe_keeps_first_spelling_of_a_form(self):
        self.assertEqual(CE.dedupe("name", ["ZOREK", "Zorek", "Zorek."]), ["ZOREK"])
        self.assertEqual(CE.dedupe("dots", ["MD ZOREK", "Md. Zorek", "MD. ZOREK"]), ["MD ZOREK", "Md. Zorek"])

    def test_edit_distance(self):
        self.assertEqual(CE.edit_distance("ZOREK", "ZOREK"), 0)
        self.assertEqual(CE.edit_distance("ZOREK", "ZORAK"), 1)
        self.assertEqual(CE.edit_distance("", "ABC"), 3)


class TestValidate(unittest.TestCase):
    def test_good_cases(self):
        CE.validate_case(_case())
        CE.validate_case(_case(answer="differs"))
        CE.validate_case(_case(kind="dots", cands=("MD ZOREK", "MD. ZOREK"), answer="differs", dot=True))
        CE.validate_case(_case(kind="digits", cands=("1234567890", "1234567898"), set_="real"))  # pii-scan: placeholder

    def test_bad_cases(self):
        bad = [
            _case(cands=("ZOREK VAMUL",)),                                         # one candidate
            _case(cands=("ZOREK VAMUL", "Zorek Vamul")),                           # same form twice
            _case(answer="same", printed="ZOREK VAMLL"),                           # truth contradicts print
            _case(kind="dots", cands=("MD ZOREK", "MD. ZOREK"), answer="differs"),  # no dot truth
            _case(kind="dots", cands=("MD ZOREK", "MD. ZOREK"), answer="differs", dot=False),
            dict(_case(), kind="letters"),
            dict(_case(set_="real"), box=[0.5, 0.1, 0.2, 0.2]),
        ]
        for c in bad:
            with self.assertRaises(CE.CaseError):
                CE.validate_case(c)


class TestScoring(unittest.TestCase):
    def test_outcomes(self):
        self.assertEqual(CE.outcome_of("same", "same"), "correct")
        self.assertEqual(CE.outcome_of("differs", "same"), "false_pass")
        self.assertEqual(CE.outcome_of("same", "differs"), "false_fail")
        self.assertEqual(CE.outcome_of("same", "unclear"), "review")
        self.assertEqual(CE.outcome_of("differs", "error"), "review")

    def test_score_case_and_rates(self):
        cands = ("ZOREK VAMUL", "ZOREK VAMLL")
        rows = []
        # 4 same cases: 3 right, 1 false FAIL; 4 differs cases: 2 right, 1 false PASS, 1 unclear
        for i, dec in enumerate(("same", "same", "same", "differs")):
            c = _case(f"A{i}", cands=cands, answer="same")
            sc = {cands[0]: -0.1, cands[1]: -0.9} if dec == "same" else {cands[0]: -0.9, cands[1]: -0.1}
            rows.append(CE.score_case(c, _look(dec, cands, sc)))
        for i, dec in enumerate(("differs", "differs", "same", "unclear")):
            c = _case(f"B{i}", cands=cands, answer="differs")
            sc = {cands[0]: -0.9, cands[1]: -0.1} if dec != "same" else {cands[0]: -0.1, cands[1]: -0.9}
            if dec == "unclear":
                sc = {cands[0]: -0.5, cands[1]: -0.48}
            rows.append(CE.score_case(c, _look(dec, cands, sc)))
        st = CE.summarize(rows)
        self.assertEqual((st.n, st.n_same, st.n_differs), (8, 4, 4))
        self.assertEqual((st.correct, st.false_pass, st.false_fail, st.review), (5, 1, 1, 1))
        self.assertAlmostEqual(st.false_pass_rate, 0.25)
        self.assertAlmostEqual(st.false_fail_rate, 0.25)
        self.assertAlmostEqual(st.review_rate, 0.125)
        self.assertAlmostEqual(st.accuracy, 5 / 8)
        self.assertEqual(rows[0].scores, [-0.1, -0.9])
        self.assertEqual(rows[4].best_index, 1)
        self.assertEqual(rows[4].printed_index, 1)
        # the printed spelling was offered and picked on both right "differs" decisions
        self.assertEqual(st.as_dict()["identified"], [2, 2])

    def test_error_and_missing_result_are_reviews(self):
        r = CE.score_case(_case(), None, error="RuntimeError")
        self.assertEqual((r.decision, r.outcome), ("error", "review"))
        r2 = CE.score_case(_case(), {"decision": "maybe", "best": "ZOREK VAMUL"})
        self.assertEqual(r2.decision, "unclear")

    def test_dot_field_is_scored(self):
        cands = ("MD ZOREK", "MD. ZOREK")
        c = _case(kind="dots", cands=cands, answer="differs", dot=True)
        rows = [CE.score_case(c, _look("differs", cands, {cands[0]: -1.0, cands[1]: -0.1}, dot=True)),
                CE.score_case(c, _look("differs", cands, {cands[0]: -1.0, cands[1]: -0.1}, dot=False)),
                CE.score_case(c, _look("unclear", cands, {cands[0]: -1.0, cands[1]: -0.9}, dot=None))]
        d = CE.summarize(rows).as_dict()["dots"]
        self.assertEqual((d["n"], d["ok"], d["wrong"], d["none"]), (3, 1, 1, 1))

    def test_wilson_upper(self):
        self.assertIsNone(CE.wilson_upper(0, 0))
        u = CE.wilson_upper(0, 1000)
        self.assertGreater(u, 0.0)
        self.assertLess(u, 0.005)                     # 0 of 1000 bounds the rate under 0.5 %
        self.assertGreater(CE.wilson_upper(5, 100), 0.05)


class TestSweep(unittest.TestCase):
    def _rows(self):
        cands = ("ZOREK VAMUL", "ZOREK VAMLL")
        rows = []
        # same cases with leads 0.05, 0.3, 0.8 for the reference; differs cases with leads 0.05, 0.3, 0.8
        for k, lead in enumerate((0.05, 0.3, 0.8)):
            rows.append(CE.score_case(_case(f"S{k}", cands=cands, answer="same"),
                                      _look("same", cands, {cands[0]: -0.1, cands[1]: -0.1 - lead})))
            # a differs case whose scores wrongly favour the reference by a small lead (a would-be false PASS)
            rows.append(CE.score_case(_case(f"D{k}", cands=cands, answer="differs"),
                                      _look("same" if k == 0 else "differs", cands,
                                            {cands[0]: -0.1, cands[1]: -0.15} if k == 0 else
                                            {cands[0]: -0.1 - lead, cands[1]: -0.1})))
        return rows

    def test_redecide(self):
        rows = self._rows()
        self.assertEqual(CE.redecide(rows[0], 0.0), "same")
        self.assertEqual(CE.redecide(rows[0], 0.1), "unclear")
        self.assertEqual(CE.redecide(rows[3], 0.2), "differs")
        err = CE.score_case(_case(), None, error="X")
        self.assertEqual(CE.redecide(err, 0.0), "unclear")

    def test_min_agree(self):
        cands = ("ZOREK VAMUL", "ZOREK VAMLL")
        r = CE.score_case(_case(), Look("same", cands[0], {cands[0]: -0.1, cands[1]: -0.9}, 0.8, 4, 2))
        self.assertEqual(CE.redecide(r, 0.1), "same")
        self.assertEqual(CE.redecide(r, 0.1, min_agree=1.0), "unclear")

    def test_min_score_gate_and_consistency(self):
        cands = ("ZOREK VAMUL", "ZOREK VAMLL")
        weak = CE.score_case(_case(), Look("unclear", cands[0], {cands[0]: -2.0, cands[1]: -2.9}, 0.9, 4, 4))
        self.assertEqual(CE.redecide(weak, 0.1), "same")
        self.assertEqual(CE.redecide(weak, 0.1, min_score=-1.6), "unclear")
        self.assertEqual(CE.consistency([weak], 0.1), (0, 1))
        self.assertEqual(CE.consistency([weak], 0.1, min_score=-1.6), (1, 1))
        self.assertEqual(CE.consistency([weak], None), (0, 0))

    def test_exact_sweep_reruns_the_module_and_restores_margin(self):
        class Stub:                                     # a closer module whose look obeys its MARGIN
            MARGIN = 0.12

        def look(image, box, candidates, kind="name"):
            lead = 0.3
            return Look("same" if lead >= Stub.MARGIN else "unclear", candidates[0],
                        {candidates[0]: -0.1, candidates[1]: -0.1 - lead}, lead, 2, 2)
        Stub.look = staticmethod(look)
        cases = [_case("E1"), _case("E2", answer="differs")]
        orig = CE.case_image
        CE.case_image = lambda c, dpi=400, root=None: (__import__("numpy").zeros((20, 40, 3), "uint8"), (0, 0, 40, 20))
        try:
            ex = CE.exact_sweep(Stub, cases, (0.1, 0.5))
        finally:
            CE.case_image = orig
        self.assertEqual(Stub.MARGIN, 0.12)
        self.assertEqual([e["threshold"] for e in ex], [0.1, 0.5])
        self.assertEqual(ex[0]["synth"]["false_pass"], 1)      # E2 differs but the stub says same
        self.assertEqual(ex[1]["synth"]["review"], 2)
        text, _ = CE.format_report([], "stub", (0.0,), 0.12, exact=ex)
        self.assertIn("EXACT MARGIN sweep", text)

    def test_sweep_trade_off_and_recommendation(self):
        rows = self._rows()
        curve = CE.sweep(rows, (0.0, 0.1, 0.5, 1.0))
        reviews = [d["review"] for d in curve]
        self.assertEqual(reviews, sorted(reviews))         # more review as MARGIN grows
        self.assertEqual(curve[0]["false_pass"], 1)        # the small wrong lead passes at 0
        self.assertEqual(curve[1]["false_pass"], 0)        # and goes to review at 0.1
        rec = CE.recommend([], curve, {"real": {"false_pass": 0.0, "false_fail": 0.0, "review": None},
                                       "synth": {"false_pass": 0.0, "false_fail": 0.0, "review": 0.5}})
        self.assertEqual(rec["threshold"], 0.1)
        none = CE.recommend([], curve[:1], {"real": {"false_pass": 0.0, "false_fail": 0.0, "review": None},
                                            "synth": {"false_pass": 0.0, "false_fail": 0.0, "review": 0.5}})
        self.assertIsNone(none["threshold"])

    def test_report_is_masked(self):
        rows = self._rows()
        for r in rows[:2]:
            r.set = "real"
        text, ok = CE.format_report(rows, "stub", (0.0, 0.1))
        for word in ("ZOREK", "VAMUL", "VAMLL"):
            self.assertNotIn(word, text.upper())
        self.assertIn("FALSE PASS", text)
        self.assertIn("MARGIN sweep", text)
        self.assertFalse(ok)                                # one false PASS in the synthetic part
        self.assertIn("D0", text)                           # named by its id


class TestSynthetic(unittest.TestCase):
    def test_specs_are_reproducible_and_balanced(self):
        a = CE.synth_specs(60, seed=7)
        b = CE.synth_specs(60, seed=7)
        self.assertEqual(a, b)
        self.assertNotEqual(a, CE.synth_specs(60, seed=8))
        ans = [s["truth"]["answer"] for s in a]
        self.assertEqual(ans.count("same"), ans.count("differs"))
        self.assertEqual({s["kind"] for s in CE.synth_specs(240, seed=7)}, set(CE.KINDS))
        pairs = {}
        for s in a:
            pairs.setdefault(s["id"][:-1], []).append(s)
        for members in pairs.values():
            self.assertEqual(len(members), 2)
            self.assertEqual(members[0]["candidates"], members[1]["candidates"])
            self.assertEqual({m["truth"]["answer"] for m in members}, {"same", "differs"})
            self.assertEqual(members[0]["font"], members[1]["font"])

    def test_banned_words_are_never_drawn(self):
        def shared_words(specs):
            # the words both candidates carry are the drawn ones (a mutation changes one candidate only)
            out = set()
            for s in specs:
                if s["kind"] in ("name", "dots"):
                    a, b = (set(x.upper().replace(".", " ").split()) for x in s["candidates"])
                    out |= {w for w in a & b if len(w) >= 4}
            return out
        made = shared_words(CE.synth_specs(200, seed=11))
        self.assertTrue(made)
        banned = frozenset(sorted(made)[:15])
        self.assertFalse(shared_words(CE.synth_specs(200, seed=11, banned=banned)) & banned)

    def test_render_is_deterministic_and_box_inside(self):
        spec = [s for s in CE.synth_specs(40, seed=3) if s["degradations"]][0]
        a1, b1, xh = CE.render_synth(spec)
        a2, b2, _ = CE.render_synth(spec)
        self.assertTrue((a1 == a2).all())
        self.assertEqual(b1, b2)
        h, w = a1.shape[:2]
        self.assertTrue(0 <= b1[0] < b1[2] <= w and 0 <= b1[1] < b1[3] <= h)
        self.assertGreater(xh, 0)


class TestHarnessEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = SCRATCH / f"test_closer_eval-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        CE.make_synth(16, seed=5, out=cls.root / "synth", banned=frozenset())
        cls.cases = CE.load_cases("synth", root=cls.root)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def test_files_written(self):
        self.assertEqual(len(self.cases), 16)
        self.assertTrue((self.root / "synth" / "meta.json").exists())
        for c in self.cases:
            self.assertTrue((self.root / "synth" / c["source"]["png"]).exists())

    def test_oracle_stub_is_perfect_and_gets_the_contract_arguments(self):
        seen = []
        truth = {c["id"]: c for c in self.cases}
        order = iter(self.cases)

        def oracle(image, box, candidates, kind="name"):
            c = next(order)
            seen.append((image.shape, box, list(candidates), kind))
            self.assertEqual(image.ndim, 3)
            self.assertTrue(all(isinstance(v, int) for v in box))
            want = truth[c["id"]]["truth"]["answer"]
            best = candidates[0] if want == "same" else candidates[1]
            scores = {x: (-0.1 if x == best else -1.0) for x in candidates}
            return Look(want, best, scores, 0.9, 3, 3, truth[c["id"]]["truth"].get("dot"), "oracle")

        rows = CE.run_cases(self.cases, oracle, root=self.root, progress=False)
        st = CE.summarize(rows)
        self.assertEqual(st.correct, len(self.cases))
        self.assertEqual(st.false_pass + st.false_fail + st.review, 0)
        self.assertEqual([s[3] for s in seen], [c["kind"] for c in self.cases])
        self.assertEqual([s[2] for s in seen], [c["candidates"] for c in self.cases])

    def test_always_same_stub_passes_every_difference(self):
        def lazy(image, box, candidates, kind="name"):
            return Look("same", candidates[0], {candidates[0]: -0.1, candidates[1]: -0.2}, 0.1, 1, 1)

        rows = CE.run_cases(self.cases, lazy, root=self.root, progress=False)
        st = CE.summarize(rows)
        self.assertEqual(st.false_pass, st.n_differs)
        self.assertEqual(st.false_fail, 0)
        self.assertAlmostEqual(st.false_pass_rate, 1.0)

    def test_crash_is_counted_as_review(self):
        def boom(image, box, candidates, kind="name"):
            raise RuntimeError("stub crash")

        rows = CE.run_cases(self.cases[:2], boom, root=self.root, progress=False)
        self.assertEqual([r.decision for r in rows], ["error", "error"])
        self.assertEqual(CE.summarize(rows).review, 2)

    def test_no_box_mode(self):
        boxes = []

        def rec(image, box, candidates, kind="name"):
            boxes.append(box)
            return Look("unclear", candidates[0], {}, 0.0, 0, 0)

        CE.run_cases(self.cases[:3], rec, no_box=True, root=self.root, progress=False)
        self.assertEqual(boxes, [None, None, None])

    def test_partial_results_are_written_while_running(self):
        seen = []
        part = self.root / "run_p" / "results_partial.json"
        part.parent.mkdir(parents=True, exist_ok=True)

        def rec(image, box, candidates, kind="name"):
            if part.exists():
                seen.append(len(json.loads(part.read_text(encoding="utf-8"))))
            return Look("unclear", candidates[0], {}, 0.0, 0, 0)

        CE.run_cases(self.cases[:5], rec, root=self.root, progress=False, partial=part, every=2)
        self.assertEqual(seen, [2, 2, 4])                    # written after cases 2 and 4, read by 3, 4, 5
        self.assertEqual(len(json.loads(part.read_text(encoding="utf-8"))), 5)
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            CE.report_again("run_p", results_dir=self.root, partial=True)
        self.assertIn("PARTIAL: 5 case(s)", buf.getvalue())
        self.assertFalse((self.root / "run_p" / "report.txt").exists())

    def test_load_cases_rejects_a_bad_label(self):
        bad_root = self.root / "bad"
        (bad_root / "synth").mkdir(parents=True, exist_ok=True)
        c = dict(self.cases[0])
        c["truth"] = dict(c["truth"], answer="differs" if c["truth"]["answer"] == "same" else "same")
        (bad_root / "synth" / "cases.json").write_text(json.dumps([c]), encoding="utf-8")
        with self.assertRaises(CE.CaseError):
            CE.load_cases("synth", root=bad_root)


class TestProductionImage(unittest.TestCase):
    def test_page_image_gets_the_page_unturned_and_no_box(self):
        import pymupdf
        root = SCRATCH / f"test_closer_eval_pi-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        root.mkdir(parents=True, exist_ok=True)
        try:
            pdf = root / "made_up.pdf"
            doc = pymupdf.open()
            page = doc.new_page()                                   # portrait
            page.insert_text((72, 72), "ZOREK VAMUL", fontsize=12)  # little text: an "ocr" page
            doc.save(str(pdf))
            doc.close()
            seen = {}

            class Stub:
                @staticmethod
                def page_image(engine, page, dpi=400):
                    seen.update(path=engine._path_of(page), kind=page.kind, file_page=page.file_page, dpi=dpi)
                    return engine.page_image_of(page, 50)

            c = _case(set_="real")
            c["source"] = {"file": str(pdf), "page": 0, "rot90": 1}   # upright needs a turn: production ignores it
            img, box = CE.production_image(c, Stub)
            self.assertIsNone(box)
            self.assertEqual(Path(seen["path"]), pdf)
            self.assertEqual((seen["kind"], seen["file_page"], seen["dpi"]), ("ocr", 0, CE.RENDER_DPI))
            self.assertGreater(img.shape[0], img.shape[1])           # still portrait: not turned
        finally:
            CE._RENDER_MEMO.clear()
            shutil.rmtree(root, ignore_errors=True)


class TestReportExtras(unittest.TestCase):
    def test_provenance_names_the_measured_closer(self):
        import types
        root = SCRATCH / f"test_closer_eval_pv-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        root.mkdir(parents=True, exist_ok=True)
        try:
            src = root / "closer_stub.py"
            src.write_text("VERSION = 'stub-1'\n", encoding="utf-8")
            mod = types.SimpleNamespace(__file__=str(src), VERSION="stub-1", MARGIN=0.3, MOST=0.6, MIN_SCORE=-1.6,
                                        DISSENT="not a number")
            p = CE.provenance(mod)
            self.assertEqual((p["version"], p["MARGIN"], p["MOST"], p["MIN_SCORE"]), ("stub-1", 0.3, 0.6, -1.6))
            self.assertEqual(len(p["sha256"]), 12)
            self.assertNotIn("DISSENT", p)
            line = CE.provenance_line(p)
            self.assertIn("stub-1", line)
            self.assertIn(p["sha256"], line)
            src.write_text("VERSION = 'stub-2'\n", encoding="utf-8")
            self.assertNotEqual(CE.provenance(mod)["sha256"], p["sha256"])      # an edit is seen
            self.assertEqual(CE.provenance(None), {})
            self.assertIn("baseline", CE.provenance_line({}))
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_wrong_dot_on_a_real_case_misses_the_target(self):
        cands = ("MD ZOREK", "MD. ZOREK")
        c = _case("R9", kind="dots", cands=cands, answer="differs", dot=True, set_="real")
        right = CE.score_case(c, _look("differs", cands, {cands[0]: -1.0, cands[1]: -0.1}, dot=True))
        text, ok = CE.format_report([right], "stub", (0.0,), header=["mode test"])
        self.assertTrue(ok)
        self.assertIn("ok   real wrong dot", text)
        self.assertIn("  mode test", text)
        wrong = CE.score_case(c, _look("differs", cands, {cands[0]: -1.0, cands[1]: -0.1}, dot=False))
        text, ok = CE.format_report([wrong], "stub", (0.0,))
        self.assertFalse(ok)                                  # the decision is right but the dot is wrong
        self.assertIn("MISS real wrong dot", text)
        self.assertIn("wrong dot ids: R9", text)

    def test_real_report_has_a_tag_breakdown(self):
        cands = ("ZOREK VAMUL", "ZOREK VAMLL")
        c = _case("R7", cands=cands, set_="real")
        r = CE.score_case(c, _look("same", cands, {cands[0]: -0.1, cands[1]: -0.9}))
        r.groups["degradations"] = "script+board_paper"
        r2 = CE.score_case(_case("R8", cands=cands, set_="real"), _look("same", cands, {cands[0]: -0.1, cands[1]: -0.9}))
        r2.groups["degradations"] = ""
        text, _ = CE.format_report([r, r2], "stub", (0.0,))
        self.assertIn(" by tag:", text)
        self.assertIn("board_paper", text)
        self.assertIn("(no tag)", text)

    def test_runner_candidates_drop_the_eye_spelling(self):
        c = _case("R5", cands=("ZOREK VAMUL", "ZOREK VAMLL", "ZOREK VAMOL"), answer="differs",
                  printed="ZOREK VAMOL", set_="real")
        c.update(reference="ZOREK VAMUL", ocr=["Zorek Vamll"])
        got = CE.runner_candidates(c)
        self.assertEqual(got["candidates"], ["ZOREK VAMUL", "Zorek Vamll"])
        self.assertEqual(got["candidates_mode"], "runner")
        CE.validate_case(got)                               # the truth still holds: the reference is not printed
        self.assertEqual(c["candidates"][2], "ZOREK VAMOL")  # the case itself is not changed
        no_read = dict(c, ocr=[])
        self.assertEqual(CE.runner_candidates(no_read)["candidates"], c["candidates"])
        self.assertTrue(CE.runner_candidates(no_read)["candidates_mode"].startswith("offered"))
        syn = _case("S1")
        self.assertIs(CE.runner_candidates(syn), syn)

    def test_report_again_from_saved_results(self):
        cands = ("ZOREK VAMUL", "ZOREK VAMLL")
        rows = [CE.score_case(_case("S1", cands=cands), _look("same", cands, {cands[0]: -0.1, cands[1]: -0.9})),
                CE.score_case(_case("S2", cands=cands, answer="differs"),
                              _look("same", cands, {cands[0]: -0.1, cands[1]: -0.9}))]
        root = SCRATCH / f"test_closer_eval_ra-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        (root / "run1").mkdir(parents=True, exist_ok=True)
        try:
            (root / "run1" / "results_private.json").write_text(json.dumps([r.to_json() for r in rows]),
                                                                 encoding="utf-8")
            (root / "run1" / "run_meta.json").write_text(json.dumps(
                {"engine": "stub", "mode": "box", "candidates": "offered",
                 "provenance": {"version": "stub-1", "sha256": "abc", "MARGIN": 0.12}}), encoding="utf-8")
            self.assertEqual(CE.rows_of([r.to_json() for r in rows])[1].outcome, "false_pass")
            import contextlib
            import io
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(CE.report_again("run1", results_dir=root), 1)   # the false PASS misses a target
            text = (root / "run1" / "report.txt").read_text(encoding="utf-8")
            self.assertIn("VERSION stub-1", text)
            self.assertIn("false PASS ids: S2", text)
            self.assertNotIn("ZOREK", text.upper())
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(CE.report_again("missing", results_dir=root), 2)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_why_lines_are_masked(self):
        self.assertEqual(CE._why("the letter's shape is 'ZOREK' at 0.42"), "the letter's shape is '?' at #")
        self.assertEqual(CE._why("read ZOREK VAMUL"), "read ? ?")
        cands = ("ZOREK VAMUL", "ZOREK VAMLL")
        r = CE.score_case(_case("S3", cands=cands), Look("unclear", cands[0], {}, 0.0, 2, 1, None,
                                                         "unclear: the shape is 'ZOREK'"))
        text, _ = CE.format_report([r], "stub", (0.0,))
        self.assertIn("why (the closer's own note", text)
        self.assertNotIn("ZOREK", text)

    def test_a_printed_number_never_carries_a_misread_letter(self):
        specs = [s for s in CE.synth_specs(2400, seed=7) if s["kind"] == "digits"]
        misread = [s for s in specs if s["diff_type"] == "letter_misread"]
        self.assertTrue(misread)
        for s in specs:
            ref, printed = s["candidates"][0], s["truth"]["printed"]
            self.assertFalse(CE._letter_swap(ref, printed), s["id"])     # printed vs reference: digits only
        for s in misread:
            self.assertEqual(len(s["candidates"]), 3)
            self.assertTrue(CE._letter_swap(s["candidates"][0], s["candidates"][1]))
            want = s["candidates"][0] if s["truth"]["answer"] == "same" else s["candidates"][2]
            self.assertEqual(s["truth"]["printed"], want)

    def test_script_fonts_print_title_case(self):
        specs = CE.synth_specs(400, seed=7)
        script = [s for s in specs if s["font_group"].startswith("script") and s["kind"] in ("name", "dots")]
        self.assertTrue(script)
        self.assertEqual({s["case_style"] for s in script}, {"title"})
        self.assertIn("upper", {s["case_style"] for s in specs if not s["font_group"].startswith("script")})


class TestRealHelpers(unittest.TestCase):
    def test_locate_picks_the_nearest_run_on_one_line(self):
        words = [(10, 100, 60, 120, "Name", 0.9), (70, 100, 90, 120, ":", 0.9), (100, 100, 160, 120, "Zorek", 0.9),
                 (170, 100, 230, 120, "Vamll", 0.8), (10, 200, 60, 220, "Zorek", 0.9), (70, 300, 130, 320, "Qadim", 0.9)]
        box, d = CE.locate(words, ["Zorek Vamul"])
        self.assertEqual(box, (100, 100, 230, 120))
        self.assertAlmostEqual(d, 1 / 10)
        box2, _ = CE.locate(words, ["Zorek"], region=[0.0, 0.3, 1.0, 1.0], shape=(400, 400, 3))
        self.assertEqual(box2, (10, 200, 60, 220))

    def test_pad_box_and_pixels(self):
        b = CE.pad_box((100, 100, 200, 120), (400, 1000, 3))
        self.assertLess(b[0], 0.1)
        self.assertGreater(b[2], 0.2)
        px = CE.box_pixels(b, (400, 1000, 3))
        self.assertTrue(px[0] < 100 and px[2] > 200 and px[1] < 100 and px[3] > 120)

    def test_sample_keeps_real_and_whole_pairs(self):
        specs = CE.synth_specs(200, seed=9)
        real = [_case("R1", set_="real")]
        got = CE._sample(real + specs, 40, 1)
        self.assertIn("R1", [c["id"] for c in got])
        syn = [c for c in got if c["set"] == "synth"]
        ids = {c["id"][:-1] for c in syn}
        self.assertEqual(len(syn), 2 * len(ids))
        self.assertLess(len(syn), len(specs))


if __name__ == "__main__":
    unittest.main()
