"""Closer-look evaluation (owner rule 28 Oct, "closer look"; Agent C): the labelled set and the harness that
measures closer.look() the way a document-verification vendor measures a name / number check: FALSE PASS
(false accept: a real difference judged "same"), FALSE FAIL (false reject: an identical name judged
"differs"), MANUAL REVIEW ("unclear": the "check by eye" FLAG) and accuracy, with the MARGIN trade-off curve.

Data (private: the real set holds real names; under C:\\Hangeul\\DOCCHECK\\lab\\private-runner\\closer_eval):
  real\\spec.json      the real cases written by hand: source file and page, quarter turns, the strings to find
                       (or a fixed box), the reference, the OCR read(s), the printed spelling and the truth, each
                       verified by eye on the page image
  real\\cases.json     built by `build-real`: the box located with the CPU light engine and normalised to the
                       page image (0..1, so any render resolution maps it), the candidates
  real\\crops, sheets  400 dpi crops with the box drawn (the eye check)
  synth\\cases.json    built by `synth` (made-up names only, reproducible with --seed), synth\\img\\<id>.png
  results\\<run>\\      results_private.json (per case), report.txt (masked: ids, counts, rates)
  TARGETS.md           the targets this harness checks

A case: {"id", "set": real|synth, "kind": name|dots|digits, "category", "diff_type", "candidates" (reference
first), "truth": {"answer": same|differs, "printed", "dot"}, "source": {"file", "page", "rot90"} | {"png"},
"box": normalised [x0, y0, x1, y1] (real) | "box_px" (synth), "degradations", ...}.
  answer "same"    the image prints candidates[0] (a misread of an identical name, or a control)
  answer "differs" the image does not print candidates[0] (a real difference: letter, dot, word, order, digit)
  dot              kind "dots": whether a dot is printed right after the dotted word (Look.dot is scored on it)

Commands (runner venv, from C:\\Hangeul\\JARVIS\\prototype\\page-types):
  <py> -I -B -m src.verify.runner.selftest.closer_eval synth [--n 2400] [--seed 20261028]
  <py> -I -B -m src.verify.runner.selftest.closer_eval build-real [--ids R001,R002] [--no-sheets]
  <py> -I -B -m src.verify.runner.selftest.closer_eval run [--engine closer|ocr] [--set real|synth|all]
        [--limit N] [--sample N] [--ids ...] [--no-box | --production] [--candidates offered|runner]
        [--grid 0,0.05,...] [--margins 0.08,0.12,0.2] [--name RUN]
  <py> -I -B -m src.verify.runner.selftest.closer_eval report --name RUN [--set real|synth|all]
`run --engine closer` (default) imports src.verify.runner.closer (CPU ONNX engine) and calls look(image, box,
candidates, kind); `--engine ocr` is the plain-OCR baseline (one CPU read of the box, exact comparison), the
behaviour before the closer look. `--production` hands the closer what names.py / tin.py hand it (the page from
closer.page_image, not turned upright, box=None); `--candidates runner` offers only what the runner has (the
reference and the OCR reads, not the printed spelling the eye found). The MARGIN sweep re-derives decisions from
the closer's own scores (with its MOST and MIN_SCORE gates when it has them) and says how often that equals the
closer's own decision; `--margins` re-runs the closer with closer.MARGIN set to each value (the exact curve, one
run per value). Every run writes run_meta.json and a report naming the measured closer (VERSION, SHA-256 of
closer.py, its constants). Printed output: case ids, counts and rates only. Exit 0 when every target in TARGETS
is met, 1 otherwise, 2 on bad arguments or missing data.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import io
import json
import math
import random
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

PRIVATE = Path(r"C:\Hangeul\DOCCHECK\lab\private-runner")
ROOT = PRIVATE / "closer_eval"
REAL_DIR = ROOT / "real"
SYNTH_DIR = ROOT / "synth"
RESULTS_DIR = ROOT / "results"
FONTS_DIR = Path(r"C:\Windows\Fonts")
SEED = 20261028
RENDER_DPI = 400                   # the closer look's page_image default
LOCATE_DPI = 300                   # the box search (CPU light engine)

KINDS = ("name", "dots", "digits")
ANSWERS = ("same", "differs")
DECISIONS = ("same", "differs", "unclear")

# Targets (TARGETS.md): the real set allows no error at all; the synthetic set allows these rates.
TARGETS = {
    "real": {"false_pass": 0.0, "false_fail": 0.0, "review": None, "dot_wrong": 0.0},
    "synth": {"false_pass": 0.005, "false_fail": 0.01, "review": 0.05, "dot_wrong": 0.005},
}
DEFAULT_GRID = (0.0, 0.02, 0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.4, 0.5, 0.6, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0)
Z95 = 1.645                        # one-sided 95 % (Wilson upper bound of an error rate)


# =============================================================================================== text forms
def norm_letters(s: str) -> str:
    """Name letters only: case, spaces, dots, MRZ fillers and marks do not count (kind "name")."""
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def norm_dots(s: str) -> str:
    """Letters and dots (kind "dots"): case and spaces do not count, a dot does."""
    return re.sub(r"[^A-Z0-9.]", "", (s or "").upper())


def norm_digits(s: str) -> str:
    """Numbers (kind "digits"): letters, digits and the MRZ filler '<'; spaces and dashes do not count."""
    return re.sub(r"[^A-Z0-9<]", "", (s or "").upper())


NORM = {"name": norm_letters, "dots": norm_dots, "digits": norm_digits}


def same_form(kind: str, a: str, b: str) -> bool:
    return NORM[kind](a) == NORM[kind](b)


def dedupe(kind: str, items: Iterable[str]) -> List[str]:
    """Candidates in order, one per form of the kind (the first spelling of a form is kept)."""
    out, seen = [], set()
    for s in items:
        if not s or not NORM[kind](s):
            continue
        k = NORM[kind](s)
        if k not in seen:
            seen.add(k)
            out.append(s)
    return out


def edit_distance(a: str, b: str) -> int:
    """Levenshtein distance (box search only; never a verdict)."""
    if a == b:
        return 0
    if not a or not b:
        return len(a) + len(b)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def dot_after_first(s: str) -> Optional[bool]:
    """Whether the first word that a dot can follow (an initial or a short title such as MD / MST) carries a
    printed dot. None when the string has no such word."""
    m = re.match(r"\s*([A-Za-z]{1,4})(\.?)", s or "")
    if not m:
        return None
    return m.group(2) == "."


# =============================================================================================== cases
class CaseError(ValueError):
    pass


def validate_case(c: dict) -> None:
    """A case is consistent: kind and answer known, two or more candidates of distinct forms, the truth
    answer equal to whether the printed spelling has the reference's form, and a dot truth on a dots case."""
    cid = c.get("id", "?")
    kind = c.get("kind")
    if kind not in KINDS:
        raise CaseError(f"{cid}: kind {kind!r}")
    cands = c.get("candidates") or []
    if len(cands) < 2:
        raise CaseError(f"{cid}: fewer than two candidates")
    if len({NORM[kind](x) for x in cands}) != len(cands):
        raise CaseError(f"{cid}: two candidates of the same form")
    t = c.get("truth") or {}
    if t.get("answer") not in ANSWERS:
        raise CaseError(f"{cid}: truth answer {t.get('answer')!r}")
    printed = t.get("printed")
    if printed:
        want = "same" if same_form(kind, printed, cands[0]) else "differs"
        if want != t["answer"]:
            raise CaseError(f"{cid}: truth answer {t['answer']} but the printed spelling says {want}")
    if kind == "dots":
        if t.get("dot") not in (True, False):
            raise CaseError(f"{cid}: a dots case needs truth.dot")
        if printed and dot_after_first(printed) is not None and dot_after_first(printed) != t["dot"]:
            raise CaseError(f"{cid}: truth.dot disagrees with the printed spelling")
    if c.get("set") == "real":
        b = c.get("box")
        if b is not None and not (len(b) == 4 and 0 <= b[0] < b[2] <= 1 and 0 <= b[1] < b[3] <= 1):
            raise CaseError(f"{cid}: box must be normalised [x0, y0, x1, y1]")
    elif c.get("set") == "synth":
        b = c.get("box_px")
        if b is not None and not (len(b) == 4 and b[0] < b[2] and b[1] < b[3]):
            raise CaseError(f"{cid}: bad box_px")


def load_cases(which: str = "all", root: Path = ROOT) -> List[dict]:
    out: List[dict] = []
    for name, sub in (("real", "real"), ("synth", "synth")):
        if which not in ("all", name):
            continue
        p = Path(root) / sub / "cases.json"
        if not p.exists():
            continue
        data = json.loads(p.read_text(encoding="utf-8"))
        for c in data:
            validate_case(c)
        out.extend(data)
    return out


# =============================================================================================== scoring
@dataclass
class Row:
    """One case's outcome (no names: candidates are referred to by index)."""
    id: str
    set: str
    kind: str
    expected: str                     # same | differs
    decision: str                     # same | differs | unclear | error
    outcome: str                      # correct | false_pass | false_fail | review
    best_index: Optional[int]
    margin: Optional[float]
    scores: List[Optional[float]]     # per candidate index
    variants: int
    agree: int
    dot: Optional[bool]
    dot_truth: Optional[bool]
    printed_index: Optional[int]      # index of the printed spelling among the candidates (None: not offered)
    seconds: float
    groups: Dict[str, str] = field(default_factory=dict)
    note: str = ""

    def to_json(self) -> dict:
        return dict(self.__dict__)


def _get(look: Any, name: str, default=None):
    if isinstance(look, dict):
        return look.get(name, default)
    return getattr(look, name, default)


def outcome_of(expected: str, decision: str) -> str:
    if decision not in ("same", "differs"):
        return "review"
    if decision == expected:
        return "correct"
    return "false_pass" if expected == "differs" else "false_fail"


def case_groups(c: dict) -> Dict[str, str]:
    """The strata a case is reported in (one value per dimension; degradations are listed separately)."""
    g = {"set": c.get("set", ""), "kind": c.get("kind", ""), "category": c.get("category", "") or "-",
         "diff_type": c.get("diff_type", "") or "-", "origin": c.get("origin_group", "") or "-"}
    if c.get("set") == "synth":
        g["font"] = c.get("font_group", "") or "-"
        px = float(c.get("x_height_px") or 0)
        g["size"] = ("x-height < 8 px" if px < 8 else "x-height 8-12 px" if px < 12
                     else "x-height 12-20 px" if px < 20 else "x-height >= 20 px")
    return g


def score_case(c: dict, look: Any, seconds: float = 0.0, error: Optional[str] = None) -> Row:
    cands = list(c["candidates"])
    kind = c["kind"]
    expected = c["truth"]["answer"]
    printed = c["truth"].get("printed")
    pidx = next((i for i, x in enumerate(cands) if printed and same_form(kind, x, printed)), None)
    if error is not None or look is None:
        return Row(c["id"], c["set"], kind, expected, "error", "review", None, None, [None] * len(cands), 0, 0,
                   None, c["truth"].get("dot"), pidx, seconds, case_groups(c), (error or "no result")[:120])
    decision = _get(look, "decision", "unclear")
    if decision not in DECISIONS:
        decision = "unclear"
    best = _get(look, "best", None)
    sc = _get(look, "scores", None) or {}
    scores = [None if sc.get(x) is None else float(sc.get(x)) for x in cands]
    bidx = next((i for i, x in enumerate(cands) if best is not None and x == best), None)
    if bidx is None and best is not None:
        bidx = next((i for i, x in enumerate(cands) if same_form(kind, x, best)), None)
    margin = _get(look, "margin", None)
    return Row(c["id"], c["set"], kind, expected, decision, outcome_of(expected, decision), bidx,
               None if margin is None else float(margin), scores, int(_get(look, "variants", 0) or 0),
               int(_get(look, "agree", 0) or 0), _get(look, "dot", None), c["truth"].get("dot"), pidx, seconds,
               case_groups(c), str(_get(look, "note", "") or "")[:120])


def redecide(row: Row, threshold: float, min_agree: float = 0.0, min_score: Optional[float] = None) -> str:
    """The decision the closer look would give at MARGIN = threshold, from its own scores: the best-scoring
    candidate when its lead over the runner-up is at least the threshold, else unclear. The closer's other gates
    are applied when given: min_agree (that share of the image variants picked the winner) and min_score (the
    winner's own score at least this). An error stays unclear. The closer may have further gates (a dot
    check, a shape check), so this is an approximation; `run --margins` re-runs the closer itself."""
    if row.decision == "error":
        return "unclear"
    vals = [(s, i) for i, s in enumerate(row.scores) if s is not None and not math.isnan(s)]
    if len(vals) >= 2:
        vals.sort(reverse=True)
        best, margin, top = vals[0][1], vals[0][0] - vals[1][0], vals[0][0]
    else:
        best, margin, top = row.best_index, row.margin, None
    if best is None or margin is None:
        return "unclear"
    if margin < threshold:
        return "unclear"
    if min_agree > 0 and row.variants and row.agree / row.variants < min_agree:
        return "unclear"
    if min_score is not None and top is not None and top < min_score:
        return "unclear"
    return "same" if best == 0 else "differs"


def wilson_upper(k: int, n: int, z: float = Z95) -> Optional[float]:
    if n <= 0:
        return None
    p = k / n
    den = 1 + z * z / n
    centre = p + z * z / (2 * n)
    spread = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return min(1.0, (centre + spread) / den)


@dataclass
class Stats:
    n: int = 0
    n_same: int = 0
    n_differs: int = 0
    correct: int = 0
    false_pass: int = 0
    false_fail: int = 0
    review: int = 0
    errors: int = 0
    dots_n: int = 0
    dots_ok: int = 0
    dots_wrong: int = 0
    dots_none: int = 0
    identified: int = 0              # a "differs" decision whose best is the printed spelling (when offered)
    identified_of: int = 0
    seconds: float = 0.0

    def add(self, r: Row, decision: Optional[str] = None) -> None:
        d = r.decision if decision is None else decision
        o = outcome_of(r.expected, d)
        self.n += 1
        self.n_same += r.expected == "same"
        self.n_differs += r.expected == "differs"
        self.correct += o == "correct"
        self.false_pass += o == "false_pass"
        self.false_fail += o == "false_fail"
        self.review += o == "review"
        self.errors += r.decision == "error"
        self.seconds += r.seconds
        if r.kind == "dots" and r.dot_truth is not None and decision is None:
            self.dots_n += 1
            if r.dot is None:
                self.dots_none += 1
            elif r.dot == r.dot_truth:
                self.dots_ok += 1
            else:
                self.dots_wrong += 1
        if decision is None and r.expected == "differs" and d == "differs" and r.printed_index not in (None, 0):
            self.identified_of += 1
            self.identified += r.best_index == r.printed_index

    @property
    def false_pass_rate(self) -> Optional[float]:
        return self.false_pass / self.n_differs if self.n_differs else None

    @property
    def false_fail_rate(self) -> Optional[float]:
        return self.false_fail / self.n_same if self.n_same else None

    @property
    def review_rate(self) -> Optional[float]:
        return self.review / self.n if self.n else None

    @property
    def accuracy(self) -> Optional[float]:
        return self.correct / self.n if self.n else None

    @property
    def decided_accuracy(self) -> Optional[float]:
        d = self.n - self.review
        return self.correct / d if d else None

    def as_dict(self) -> dict:
        return {"n": self.n, "same": self.n_same, "differs": self.n_differs, "correct": self.correct,
                "false_pass": self.false_pass, "false_fail": self.false_fail, "review": self.review,
                "errors": self.errors, "false_pass_rate": self.false_pass_rate,
                "false_pass_upper95": wilson_upper(self.false_pass, self.n_differs),
                "false_fail_rate": self.false_fail_rate,
                "false_fail_upper95": wilson_upper(self.false_fail, self.n_same),
                "review_rate": self.review_rate, "accuracy": self.accuracy,
                "decided_accuracy": self.decided_accuracy, "dots": {"n": self.dots_n, "ok": self.dots_ok,
                                                                     "wrong": self.dots_wrong,
                                                                     "none": self.dots_none},
                "identified": [self.identified, self.identified_of], "seconds": round(self.seconds, 2)}


def summarize(rows: Sequence[Row], decision_of: Optional[Callable[[Row], str]] = None) -> Stats:
    s = Stats()
    for r in rows:
        s.add(r, None if decision_of is None else decision_of(r))
    return s


def breakdown(rows: Sequence[Row], dim: str) -> Dict[str, Stats]:
    out: Dict[str, Stats] = collections.OrderedDict()
    for r in rows:
        if dim == "degradation":
            keys = r.groups.get("degradations", "clean").split("+") if r.groups.get("degradations") else ["clean"]
        else:
            keys = [r.groups.get(dim, "-")]
        for k in keys:
            out.setdefault(k, Stats()).add(r)
    return dict(sorted(out.items()))


def sweep(rows: Sequence[Row], grid: Sequence[float] = DEFAULT_GRID, min_agree: float = 0.0,
          min_score: Optional[float] = None) -> List[dict]:
    """The trade-off curve: for each MARGIN threshold, the rates the closer look's own scores give."""
    out = []
    for t in grid:
        st = summarize(rows, lambda r, t=t: redecide(r, t, min_agree, min_score))
        d = st.as_dict()
        d["threshold"] = t
        d["min_agree"] = min_agree
        d["min_score"] = min_score
        out.append(d)
    return out


def consistency(rows: Sequence[Row], margin: Optional[float], min_agree: float = 0.0,
                min_score: Optional[float] = None) -> Tuple[int, int]:
    """(cases where the re-derived decision at the closer's own MARGIN equals the closer's decision, cases):
    how far the sweep can be trusted."""
    if margin is None:
        return 0, 0
    n = ok = 0
    for r in rows:
        if r.decision == "error":
            continue
        n += 1
        ok += redecide(r, margin, min_agree, min_score) == r.decision
    return ok, n


def meets(d: dict, target: dict) -> bool:
    """Whether a stats dict meets a target dict (a limit of None is no limit; a rate with no cases behind it
    is met: nothing could go wrong)."""
    for key, rate in (("false_pass", "false_pass_rate"), ("false_fail", "false_fail_rate"), ("review", "review_rate")):
        lim = target.get(key)
        if lim is None:
            continue
        v = d.get(rate)
        if v is not None and v > lim + 1e-12:
            return False
    return True


def recommend(real_curve: List[dict], synth_curve: List[dict], targets: dict = TARGETS) -> dict:
    """The smallest MARGIN at which the real set has no error and the synthetic set meets its error targets
    (the manual-review rate is reported against its target, not used to pick). Curves share the grid."""
    by_t = {}
    for d in real_curve:
        by_t.setdefault(d["threshold"], {})["real"] = d
    for d in synth_curve:
        by_t.setdefault(d["threshold"], {})["synth"] = d
    for t in sorted(by_t):
        r, s = by_t[t].get("real"), by_t[t].get("synth")
        ok_r = r is None or meets(r, {k: v for k, v in targets["real"].items() if k != "review"})
        ok_s = s is None or meets(s, {k: v for k, v in targets["synth"].items() if k != "review"})
        if ok_r and ok_s:
            rev_ok = s is None or meets(s, {"review": targets["synth"]["review"]})
            return {"threshold": t, "review_target_met": rev_ok,
                    "real": r, "synth": s}
    return {"threshold": None, "review_target_met": False, "real": None, "synth": None}


# =============================================================================================== report
def _pct(v: Optional[float], digits: int = 2) -> str:
    return "  n/a " if v is None else f"{100 * v:6.{digits}f}%"


def _why(note: str) -> str:
    """A closer note reduced to its reason (quotes, letters in quotes and numbers dropped: masked-safe even
    when a note names a letter)."""
    n = re.sub(r"(?<![A-Za-z])'[^']*'|\"[^\"]*\"", "'?'", note or "-")
    n = re.sub(r"\b[A-Z]{2,}\b", "?", n)
    n = re.sub(r"\d+(\.\d+)?", "#", n)
    return n[:90]


def _line(label: str, st: Stats) -> str:
    d = st.as_dict()
    return (f"  {label:28s} n {st.n:5d} (same {st.n_same:4d} / differs {st.n_differs:4d})  "
            f"FALSE PASS {st.false_pass:3d} {_pct(d['false_pass_rate'])} (<= {_pct(d['false_pass_upper95'])})  "
            f"FALSE FAIL {st.false_fail:3d} {_pct(d['false_fail_rate'])} (<= {_pct(d['false_fail_upper95'])})  "
            f"review {st.review:4d} {_pct(d['review_rate'])}  accuracy {_pct(d['accuracy'])}")


def format_report(rows: Sequence[Row], engine: str, grid: Sequence[float] = DEFAULT_GRID,
                  margin_used: Optional[float] = None, min_agree: float = 0.0, min_score: Optional[float] = None,
                  exact: Optional[List[dict]] = None, header: Sequence[str] = ()) -> Tuple[str, bool]:
    """The masked report (case ids, counts, rates) and whether every target is met by the engine's own
    decisions. exact: the `run --margins` re-runs ({"threshold", "real": stats dict, "synth": stats dict});
    header: extra first lines (mode, the closer's provenance; no names)."""
    lines: List[str] = []
    ok_all = True
    lines.append(f"closer_eval: engine {engine}; MARGIN in the closer {margin_used if margin_used is not None else 'n/a'}; "
                 f"{len(rows)} case(s)")
    lines.extend(f"  {h}" for h in header)
    lines.append("FALSE PASS = a real difference judged 'same' (rate over the 'differs' cases; <= : Wilson 95 % "
                 "upper bound); FALSE FAIL = an identical name judged 'differs' (rate over the 'same' cases); "
                 "review = 'unclear' or an error (rate over all cases)")
    curves = {}
    for which in ("real", "synth"):
        rs = [r for r in rows if r.set == which]
        if not rs:
            continue
        st = summarize(rs)
        lines.append("")
        lines.append(f"== {which.upper()} ==")
        lines.append(_line("all", st))
        dims = ["kind", "category", "diff_type", "origin", "degradation"] if which == "real" else \
            ["kind", "diff_type", "degradation", "font", "size"]
        for dim in dims:
            lines.append(f" by {'tag' if (dim == 'degradation' and which == 'real') else dim}:")
            for k, s in breakdown(rs, dim).items():
                lines.append(_line(k if k != "clean" or which == "synth" else "(no tag)", s))
        d = st.as_dict()
        if d["dots"]["n"]:
            dd = d["dots"]
            lines.append(f"  dot seen (Look.dot) on dots cases: {dd['ok']} right, {dd['wrong']} wrong, "
                         f"{dd['none']} not told, of {dd['n']}")
            wrong_ids = [r.id for r in rs if r.kind == "dots" and r.dot is not None and r.dot_truth is not None
                         and r.dot != r.dot_truth]
            if wrong_ids:
                lines.append(f"  wrong dot ids: {', '.join(wrong_ids[:40])}{' ...' if len(wrong_ids) > 40 else ''}")
        if d["identified"][1]:
            lines.append(f"  'differs' with the printed spelling offered: best = printed in "
                         f"{d['identified'][0]} of {d['identified'][1]}")
        fp = [r.id for r in rs if r.outcome == "false_pass"]
        ff = [r.id for r in rs if r.outcome == "false_fail"]
        rv = [r.id for r in rs if r.outcome == "review"]
        lines.append(f"  false PASS ids: {', '.join(fp[:60]) or 'none'}{' ...' if len(fp) > 60 else ''}")
        lines.append(f"  false FAIL ids: {', '.join(ff[:60]) or 'none'}{' ...' if len(ff) > 60 else ''}")
        lines.append(f"  review ids: {', '.join(rv[:40]) or 'none'}{' ...' if len(rv) > 40 else ''}")
        why = collections.Counter((r.outcome, _why(r.note)) for r in rs if r.outcome != "correct")
        if why:
            lines.append("  why (the closer's own note, masked; outcome: count):")
            for (o, n), k in sorted(why.items(), key=lambda t: (-t[1], t[0])):
                lines.append(f"    {o:10s} {k:4d}  {n}")
        tgt = TARGETS[which]
        for key, rate, label in (("false_pass", "false_pass_rate", "FALSE PASS"),
                                 ("false_fail", "false_fail_rate", "FALSE FAIL"),
                                 ("review", "review_rate", "manual review")):
            lim = tgt.get(key)
            if lim is None:
                continue
            v = d[rate]
            ok = v is None or v <= lim + 1e-12
            ok_all &= ok
            lines.append(f"  {'ok  ' if ok else 'MISS'} {which} {label} {_pct(v)} (target <= {_pct(lim)})")
        lim = tgt.get("dot_wrong")
        if lim is not None and d["dots"]["n"]:
            told = d["dots"]["ok"] + d["dots"]["wrong"]
            v = d["dots"]["wrong"] / told if told else None
            ok = v is None or v <= lim + 1e-12
            ok_all &= ok
            lines.append(f"  {'ok  ' if ok else 'MISS'} {which} wrong dot (Look.dot given and wrong) {_pct(v)} "
                         f"(target <= {_pct(lim)})")
        curves[which] = sweep(rs, grid, min_agree, min_score)
    lines.append("")
    gates = (f"; the closer's other gates applied: variant share >= {min_agree}" if min_agree else "") + \
            (f", winner score >= {min_score}" if min_score is not None else "")
    lines.append("== MARGIN sweep (decision re-derived from the closer's own scores: best candidate when its lead "
                 f">= MARGIN, else unclear{gates}) ==")
    ok_c, n_c = consistency(rows, margin_used, min_agree, min_score)
    if n_c:
        lines.append(f"  re-derived decision at the closer's own MARGIN = its decision in {ok_c} of {n_c} cases "
                     f"({_pct(ok_c / n_c, 1)}); below 100 % the closer has further gates: use `run --margins` "
                     f"for the exact curve")
    lines.append("  MARGIN | real: FP  FF  review   | synth: FALSE PASS  FALSE FAIL  review    accuracy")
    by_t = collections.OrderedDict()
    for which, cur in curves.items():
        for d in cur:
            by_t.setdefault(d["threshold"], {})[which] = d
    for t, dd in by_t.items():
        r, s = dd.get("real"), dd.get("synth")
        rtxt = (f"{r['false_pass']:3d} {r['false_fail']:3d} {_pct(r['review_rate'], 1)}" if r else "   -   -     -  ")
        stxt = (f"{_pct(s['false_pass_rate'])}  {_pct(s['false_fail_rate'])}  {_pct(s['review_rate'])}  "
                f"{_pct(s['accuracy'])}" if s else "-")
        lines.append(f"  {t:6.3f} | {rtxt} | {stxt}")
    rec = recommend(curves.get("real", []), curves.get("synth", []))
    approx = "" if (n_c and ok_c == n_c) else " in this re-derived (approximate) sweep"
    if rec["threshold"] is None:
        lines.append(f"  no MARGIN on the grid meets the error targets{approx} (real 0 / 0, synthetic FP <= 0.5 %, "
                     f"FF <= 1 %)")
    else:
        s = rec["synth"]
        lines.append(f"  smallest MARGIN meeting the error targets{approx}: {rec['threshold']}"
                     + (f" (synthetic review {_pct(s['review_rate'])}, target <= 5 %: "
                        f"{'met' if rec['review_target_met'] else 'MISSED'})" if s else ""))
    if approx:
        lines.append("  the re-derived curve lacks the closer's other gates (shape, dot, wrapped-cell and added-word "
                     "checks), so it can show errors the closer itself does not make (or the reverse): the targets are "
                     "judged on the closer's own decisions above; pick MARGIN from `run --margins` (EXACT sweep)")
    if exact:
        lines.append("")
        lines.append("== EXACT MARGIN sweep (the closer re-run with closer.MARGIN set to each value) ==")
        lines.append("  MARGIN | real: FP  FF  review   | synth: FALSE PASS  FALSE FAIL  review    accuracy")
        for e in exact:
            r, s = e.get("real"), e.get("synth")
            rtxt = (f"{r['false_pass']:3d} {r['false_fail']:3d} {_pct(r['review_rate'], 1)}" if r else "   -   -     -  ")
            stxt = (f"{_pct(s['false_pass_rate'])}  {_pct(s['false_fail_rate'])}  {_pct(s['review_rate'])}  "
                    f"{_pct(s['accuracy'])}" if s else "-")
            lines.append(f"  {e['threshold']:6.3f} | {rtxt} | {stxt}")
        rec2 = recommend([dict(e["real"], threshold=e["threshold"]) for e in exact if e.get("real")],
                         [dict(e["synth"], threshold=e["threshold"]) for e in exact if e.get("synth")])
        lines.append("  smallest re-run MARGIN meeting the error targets: "
                     + (str(rec2["threshold"]) if rec2["threshold"] is not None else "none"))
    lines.append("")
    lines.append("RESULT: " + ("every target met" if ok_all else "a target is missed (see MISS lines)"))
    return "\n".join(lines), ok_all


# =============================================================================================== images
def _rot(img, k: int):
    import numpy as np
    k = int(k or 0) % 4
    return np.ascontiguousarray(np.rot90(img, k)) if k else img


_RENDER_MEMO: "collections.OrderedDict[tuple, Any]" = collections.OrderedDict()


def render_source(src: dict, dpi: int = RENDER_DPI):
    """The page as an RGB uint8 array: a PDF page rendered at `dpi` (pymupdf), an image file at its own
    pixels; then turned by src["rot90"] quarter turns counter-clockwise (numpy.rot90). Memoised (4 pages)."""
    import numpy as np
    key = (src["file"], int(src.get("page", 0)), int(src.get("rot90", 0)), int(dpi))
    img = _RENDER_MEMO.get(key)
    if img is not None:
        _RENDER_MEMO.move_to_end(key)
        return img
    path = Path(src["file"])
    if path.suffix.lower() in (".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"):
        from PIL import Image
        with Image.open(path) as im:
            img = np.array(im.convert("RGB"))
    else:
        import pymupdf
        with pymupdf.open(str(path)) as doc:
            pix = doc[int(src.get("page", 0))].get_pixmap(dpi=int(dpi))
            a = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
            img = a[:, :, :3].copy()
    img = _rot(img, src.get("rot90", 0))
    img.flags.writeable = False
    _RENDER_MEMO[key] = img
    while len(_RENDER_MEMO) > 4:
        _RENDER_MEMO.popitem(last=False)
    return img


def box_pixels(box: Sequence[float], shape) -> Tuple[int, int, int, int]:
    h, w = shape[:2]
    x0, y0, x1, y1 = box
    return (max(0, int(math.floor(x0 * w))), max(0, int(math.floor(y0 * h))),
            min(w, int(math.ceil(x1 * w))), min(h, int(math.ceil(y1 * h))))


def case_image(c: dict, dpi: int = RENDER_DPI, root: Path = ROOT):
    """(image, box in its pixels or None) for a case."""
    import numpy as np
    if c["set"] == "synth":
        from PIL import Image
        with Image.open(Path(root) / "synth" / c["source"]["png"]) as im:
            img = np.array(im.convert("RGB"))
        b = c.get("box_px")
        return img, (tuple(int(v) for v in b) if b else None)
    img = render_source(c["source"], dpi)
    b = c.get("box")
    return img, (box_pixels(b, img.shape) if b else None)


# =============================================================================================== engines
def closer_engine():
    """(module, look function, MARGIN, gates) of src.verify.runner.closer: gates = {"min_agree": MOST,
    "min_score": MIN_SCORE} when the module has them. Raises ImportError when it does not exist."""
    from src.verify.runner import closer  # the module under test
    gates = {"min_agree": float(getattr(closer, "MOST", 0.0) or 0.0),
             "min_score": getattr(closer, "MIN_SCORE", None)}
    return closer, closer.look, getattr(closer, "MARGIN", None), gates


PROVENANCE_PARAMS = ("MARGIN", "MIN_SCORE", "MOST", "DISSENT", "DOTS_ALONE", "LOCATE_MIN", "REGION_MIN")


def provenance(module) -> dict:
    """Which closer look was measured: its VERSION, the first 12 hex digits of its source file's SHA-256 and
    modification time, and its decision constants (the closer is edited while it is evaluated, so every
    report names the exact one). {} for no module (the OCR baseline)."""
    if module is None:
        return {}
    out: Dict[str, Any] = {"version": getattr(module, "VERSION", None)}
    try:
        p = Path(module.__file__)
        out["sha256"] = hashlib.sha256(p.read_bytes()).hexdigest()[:12]
        out["mtime"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(p.stat().st_mtime))
    except Exception:
        out["sha256"] = None
    for k in PROVENANCE_PARAMS:
        v = getattr(module, k, None)
        if isinstance(v, (int, float)):
            out[k] = v
    return out


def provenance_line(prov: dict) -> str:
    if not prov:
        return "closer: none (plain-OCR baseline)"
    params = ", ".join(f"{k} {prov[k]}" for k in PROVENANCE_PARAMS if k in prov)
    return (f"closer: VERSION {prov.get('version')}, source sha256 {prov.get('sha256')} "
            f"(modified {prov.get('mtime')}); {params}")


def exact_sweep(module, cases: Sequence[dict], margins: Sequence[float], no_box: bool = False,
                dpi: int = RENDER_DPI, image_fn=None) -> List[dict]:
    """The closer re-run on the cases with module.MARGIN set to each value (restored afterwards)."""
    out = []
    old = getattr(module, "MARGIN", None)
    try:
        for t in margins:
            module.MARGIN = float(t)
            print(f"  re-run at MARGIN {t}", flush=True)
            rows = run_cases(cases, module.look, no_box=no_box, dpi=dpi, progress=False, image_fn=image_fn)
            e = {"threshold": float(t)}
            for which in ("real", "synth"):
                rs = [r for r in rows if r.set == which]
                if rs:
                    e[which] = summarize(rs).as_dict()
            out.append(e)
    finally:
        module.MARGIN = old
    return out


class OcrBaseline:
    """The behaviour before the closer look: one CPU light-engine read of the box, compared exactly with the
    candidates (kind's form). The read equal to candidates[0] -> same; equal to another -> differs; else
    unclear. Scores are 1 for a read equal to the candidate, 0 otherwise."""

    def __init__(self, reader=None):
        if reader is None:
            from src.verify.runner import engine as E
            reader = E.make_reader()
        self.reader = reader

    def read(self, img, box) -> str:
        crop = img if box is None else img[box[1]:box[3], box[0]:box[2]]
        res = self.reader.readtext(crop)
        items = []
        for b, text, _conf in res:
            xs = [p[0] for p in b]
            ys = [p[1] for p in b]
            items.append((min(ys), min(xs), text))
        items.sort(key=lambda t: (round(t[0] / 20), t[1]))
        return " ".join(t[2] for t in items)

    def __call__(self, image, box, candidates, kind="name"):
        text = self.read(image, box)
        form = NORM[kind](text)
        scores = {c: (1.0 if NORM[kind](c) == form else 0.0) for c in candidates}
        hits = [c for c in candidates if scores[c] == 1.0]
        if hits:
            best = hits[0]
            decision = "same" if best == candidates[0] else "differs"
            margin = 1.0
        else:
            best, decision, margin = candidates[0], "unclear", 0.0
        dot = dot_after_first(text) if kind == "dots" else None
        return {"decision": decision, "best": best, "scores": scores, "margin": margin, "variants": 1,
                "agree": 1 if hits else 0, "dot": dot, "note": "plain OCR read"}


def production_image(c: dict, module, dpi: int = RENDER_DPI, root: Path = ROOT):
    """The page as the runner hands it to the closer (names.py, tin.py): closer.page_image(engine, PageRead) —
    a vector page at `dpi`, a scanned page at its own resolution, an image file as it is — NOT turned upright
    (the closer searches the page and its quarter turns itself); no box. A synthetic case: its image."""
    if c["set"] == "synth":
        return case_image(c, dpi, root)[0], None
    import pymupdf  # noqa: F401  (render_source needs it for PDFs)
    from src.verify.runner.model import PageRead
    src = dict(c["source"], rot90=0)
    path = Path(src["file"])
    image_file = path.suffix.lower() in (".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp")
    kind = "image"
    if not image_file:
        from src.verify.runner import engine as E
        kind = E.page_kinds(path)[int(src["page"])]

    class _Adapter:                      # the two engine calls page_image makes; no reader (look binds its own)
        def _path_of(self, page):
            return path

        def page_image_of(self, page, use_dpi=RENDER_DPI):
            return render_source(src, int(use_dpi))

    page = PageRead(index=0, file=path.name, file_page=int(src["page"]), text="", kind=kind, chars=0,
                    sideways=False, seconds=0.0, image_only=kind != "layer", engine="cpu")
    return module.page_image(_Adapter(), page, dpi), None


def run_cases(cases: Sequence[dict], look: Callable, no_box: bool = False, dpi: int = RENDER_DPI,
              root: Path = ROOT, progress: bool = True,
              image_fn: Optional[Callable[[dict], Tuple[Any, Any]]] = None,
              partial: Optional[Path] = None, every: int = 0) -> List[Row]:
    """Calls look(image, box, candidates, kind) on every case. image_fn(case) -> (image, box) replaces the
    default (case_image: upright render, the stored box). partial: a file the rows so far are written to every
    `every` cases (default: 100, 10 when there are fewer than 300 cases), so a long run can be read before
    it ends (`report --partial`)."""
    every = every or (10 if len(cases) < 300 else 100)
    rows: List[Row] = []
    t_all = time.time()
    for i, c in enumerate(cases, 1):
        t0 = time.time()
        err = None
        res = None
        try:
            img, box = image_fn(c) if image_fn is not None else case_image(c, dpi, root)
            res = look(img, None if no_box else box, list(c["candidates"]), kind=c["kind"])
        except Exception as exc:                       # an error is a manual review, counted apart
            err = f"{type(exc).__name__}"
        r = score_case(c, res, time.time() - t0, err)
        r.groups["degradations"] = "+".join(c.get("degradations") or []) or ""
        rows.append(r)
        if progress and (i % every == 0 or i == len(cases)):
            el = time.time() - t_all
            print(f"  {i}/{len(cases)} cases, {el:.0f} s ({el / i:.2f} s per case)", flush=True)
        if partial is not None and (i % every == 0 or i == len(cases)):
            tmp = Path(partial).with_suffix(".tmp")
            tmp.write_text(json.dumps([x.to_json() for x in rows], indent=0), encoding="utf-8")
            tmp.replace(partial)
    return rows


# =============================================================================================== synthetic set
_ON = ["B", "D", "F", "G", "H", "J", "K", "L", "M", "N", "P", "R", "S", "T", "V", "Z", "SH", "KH", "CH", "TH",
       "R", "N", "L", "M", "S", "T"]
_VO = ["A", "E", "I", "O", "U", "A", "I", "E", "O", "AI", "OU"]
_CO = ["", "", "", "", "N", "M", "L", "R", "S", "T", "K", "N", "R"]
_TITLES_DOT = ["MD", "MST", "M", "A", "S", "K", "N", "R"]            # words a printed dot can follow
_ADD_WORDS = ["MD", "MST", "BEGUM", "KHATUN"]                        # the owner's added / dropped words
_LOWER_PAIRS = [("t", "l"), ("a", "o"), ("e", "c"), ("n", "m"), ("rn", "m"), ("i", "l"), ("h", "b"), ("u", "v"),
                ("c", "o"), ("f", "t"), ("r", "n"), ("cl", "d"), ("ri", "n"), ("e", "o"), ("u", "a")]
_UPPER_PAIRS = [("O", "D"), ("O", "Q"), ("E", "F"), ("P", "R"), ("C", "G"), ("M", "N"), ("U", "V"), ("I", "L"),
                ("I", "T"), ("H", "N"), ("B", "R"), ("K", "X"), ("A", "R"), ("U", "O"), ("E", "B")]
_DIGIT_PAIRS = [("0", "8"), ("1", "7"), ("3", "8"), ("5", "6"), ("6", "8"), ("2", "7"), ("4", "9"), ("0", "6"),
                ("0", "O"), ("1", "I"), ("5", "S"), ("8", "B"), ("9", "8"), ("3", "5")]
FONTS = [  # (file, group, weight)
    ("arial.ttf", "sans", 3), ("arialbd.ttf", "sans-bold", 2), ("ariali.ttf", "sans-italic", 1),
    ("calibri.ttf", "sans", 2), ("verdana.ttf", "sans", 1), ("tahoma.ttf", "sans", 1), ("segoeui.ttf", "sans", 1),
    ("times.ttf", "serif", 3), ("timesbd.ttf", "serif-bold", 2), ("timesi.ttf", "serif-italic", 1),
    ("georgia.ttf", "serif", 1), ("BOOKOS.TTF", "serif", 1), ("pala.ttf", "serif", 1),
    ("cour.ttf", "mono", 2), ("consola.ttf", "mono", 1),
    ("MTCORSVA.TTF", "script", 2), ("segoesc.ttf", "script", 1), ("ITCEDSCR.TTF", "script", 1),
    ("LHANDW.TTF", "script", 1),
]
LABELS = {"name": ["Name :", "Name of Student :", "Father's Name :", "Mother's Name :", "Account Name :",
                   "Name of License Holder :", "Taxpayer's Name :"],
          "digits": ["NID No :", "TIN :", "Birth Registration No :", "Passport No :", "No :", "Personal No :"]}
DEGRADATIONS = ("blur", "jpeg", "salt", "low_contrast", "texture", "stamp", "rotate")


def _word(rng: random.Random, lo: int = 4, hi: int = 8) -> str:
    for _ in range(200):
        n = rng.choice((2, 2, 3))
        w = "".join(rng.choice(_ON) + rng.choice(_VO) + (rng.choice(_CO) if k == n - 1 else "") for k in range(n))
        if lo <= len(w) <= hi:
            return w
    return "TAMUR"[:hi]


def _name(rng: random.Random, words: int, banned: frozenset) -> List[str]:
    out = []
    while len(out) < words:
        w = _word(rng)
        if w not in banned and w not in out:
            out.append(w)
    return out


def _style(words: List[str], case: str) -> List[str]:
    if case == "upper":
        return [w.upper() for w in words]
    return [w[:1].upper() + w[1:].lower() for w in words]


def _confusable(rng: random.Random, text: str, upper: bool) -> Optional[str]:
    pairs = _UPPER_PAIRS if upper else _LOWER_PAIRS
    opts = []
    for a, b in pairs:
        for x, y in ((a, b), (b, a)):
            start = 0
            while True:
                k = text.find(x, start)
                if k < 0:
                    break
                if k > 0 or upper:                      # keep a Title-case capital as it is
                    opts.append((k, x, y))
                start = k + 1
    rng.shuffle(opts)
    for k, x, y in opts:
        cand = text[:k] + y + text[k + len(x):]
        if norm_letters(cand) != norm_letters(text):
            return cand
    return None


def _mutate_name(rng: random.Random, words: List[str], kind: str, upper: bool,
                 banned: frozenset) -> Tuple[Optional[List[str]], str]:
    """(the other spelling's words, diff_type) or (None, "") when this mutation does not apply."""
    w = list(words)
    titled = {i for i, x in enumerate(w) if x.upper() in ("MD", "MST")}     # titles keep their letters
    if kind == "confusable":
        idx = [i for i, x in enumerate(w) if len(x) > 2 and i not in titled]
        rng.shuffle(idx)
        for i in idx:
            v = _confusable(rng, w[i], upper)
            if v:
                w[i] = v
                return w, "one_letter_confusable"
        return None, ""
    if kind == "letter":
        idx = [i for i, x in enumerate(w) if len(x) > 3]
        if not idx:
            return None, ""
        i = rng.choice(idx)
        k = rng.randrange(1, len(w[i]))
        pool = "BDFGKPWXYZ" if upper else "bdfgkpwxyz"
        ch = rng.choice([c for c in pool if c.lower() != w[i][k].lower()])
        w[i] = w[i][:k] + ch + w[i][k + 1:]
        return w, "one_letter_other"
    if kind == "drop_letter":
        idx = [i for i, x in enumerate(w) if len(x) > 4]
        if not idx:
            return None, ""
        i = rng.choice(idx)
        k = rng.randrange(1, len(w[i]) - 1)
        w[i] = w[i][:k] + w[i][k + 1:]
        return w, "dropped_letter"
    if kind == "add_letter":
        idx = [i for i, x in enumerate(w) if len(x) > 2 and i not in titled]
        if not idx:
            return None, ""
        i = rng.choice(idx)
        k = rng.randrange(1, len(w[i]))
        v = rng.choice("AEIOU" if upper else "aeiou")
        w[i] = w[i][:k] + v + w[i][k:]
        return w, "added_letter"
    if kind == "added_word":
        word = rng.choice(_ADD_WORDS + [_word(rng)])
        word = word if upper else (word[:1] + word[1:].lower())
        if word.upper() in ("MD", "MST"):
            w.insert(0, word)
        else:
            w.append(word)
        return w, "added_word"
    if kind == "order":
        if len(w) < 2:
            return None, ""
        i = rng.randrange(len(w) - 1)
        w[i], w[i + 1] = w[i + 1], w[i]
        if norm_letters(" ".join(w)) == norm_letters(" ".join(words)):
            return None, ""
        return w, "order"
    raise ValueError(kind)


def _digits_value(rng: random.Random) -> Tuple[str, str, str]:
    """(printed number, label, font hint): a made-up number of an NID / TIN / BRN / passport / MRZ shape."""
    shape = rng.choice(("nid10", "nid13", "tin", "brn", "passport", "mrz", "apostille"))
    d = lambda n: "".join(rng.choice("0123456789") for _ in range(n))
    if shape == "nid10":
        s = d(10)
        return (f"{s[:3]} {s[3:6]} {s[6:]}" if rng.random() < 0.5 else s), "NID No :", ""
    if shape == "nid13":
        return d(13), "NID No :", ""
    if shape == "tin":
        return d(12), "TIN :", ""
    if shape == "brn":
        return d(17), "Birth Registration No :", ""
    if shape == "passport":
        return rng.choice("ABE") + d(8), "Passport No :", ""
    if shape == "mrz":
        return "I<BGD" + d(9) + "<" + d(2), "", "mono"
    return d(12), "No :", ""


def _letter_swap(a: str, b: str) -> bool:
    """Whether two spellings of a number differ by a letter / digit swap (0/O, 1/I, 5/S, 8/B): an OCR misread
    shape, never a real other number."""
    return any(x != y and (x.isalpha() or y.isalpha()) for x, y in zip(a, b))


def _mutate_digits(rng: random.Random, s: str, digits_only: bool = False) -> Optional[str]:
    """One confusable character of a number changed (digits_only: digit to digit only)."""
    opts = []
    for a, b in _DIGIT_PAIRS:
        if digits_only and not (a.isdigit() and b.isdigit()):
            continue
        for x, y in ((a, b), (b, a)):
            for k, ch in enumerate(s):
                if ch == x and not (s.startswith("I<BGD") and k < 5):
                    opts.append((k, y))
    if not opts:
        return None
    k, y = rng.choice(opts)
    return s[:k] + y + s[k + 1:]


def real_name_parts() -> frozenset:
    """Upper-case name parts of 4+ letters that real people carry (pii_scan's portal and document reads),
    in memory only, so that no made-up word equals one. Empty when they cannot be read."""
    try:
        from src.verify.runner.selftest import pii_scan
        return frozenset(pii_scan.portal_name_parts() | pii_scan.run_name_parts())
    except Exception:
        return frozenset()


def synth_specs(n: int, seed: int = SEED, banned: frozenset = frozenset()) -> List[dict]:
    """The synthetic case specs (no images): n // 2 pairs, each a 'same' and a 'differs' case on the same
    candidates, font, size, resolution and degradation set. Kinds: 62.5 % name, 20.8 % dots, 16.7 % digits."""
    specs: List[dict] = []
    pairs = max(1, n // 2)
    fonts = [(f, g) for f, g, wgt in FONTS for _ in range(wgt) if (FONTS_DIR / f).exists()]
    if not fonts:
        raise RuntimeError("no Windows fonts found")
    for p in range(pairs):
        rng = random.Random(f"{seed}:{p}")
        r = p / pairs
        kind = "name" if r < 0.625 else "dots" if r < 0.833 else "digits"
        font, group = rng.choice(fonts)
        if kind == "digits":
            font, group = rng.choice([x for x in fonts if x[1].startswith(("sans", "serif", "mono"))])
        pt = rng.choice((8, 9, 10, 10, 11, 12, 12, 13, 14))
        dpi = rng.choice((150, 200, 200, 300, 300))
        upper = rng.random() < 0.6
        if group.startswith("script"):
            upper = False                  # documents print script names in Title case; all-caps script is unreal
        if rng.random() < 0.25:
            degr: List[str] = []
        else:
            degr = sorted(set(rng.sample(DEGRADATIONS, rng.choice((1, 1, 2)))))
            if "jpeg" not in degr and rng.random() < 0.2:
                degr.append("jpeg")
        label_in_box = rng.random() < 0.08
        if kind == "name":
            words = _name(rng, rng.choice((2, 2, 3)), banned)
            if rng.random() < 0.25:
                words.insert(0, rng.choice(("MD", "MST")))
            mut = rng.choices(("confusable", "letter", "drop_letter", "add_letter", "added_word", "order"),
                              weights=(50, 8, 7, 7, 18, 10))[0]
            other, dtype = None, ""
            for m in (mut, "confusable", "letter"):
                other, dtype = _mutate_name(rng, _style(words, "upper" if upper else "title"), m, upper, banned)
                if other:
                    break
            a = " ".join(_style(words, "upper" if upper else "title"))
            b = " ".join(other)
            label = rng.choice(LABELS["name"])
        elif kind == "dots":
            words = _name(rng, rng.choice((1, 2, 2)), banned)
            title = rng.choice(_TITLES_DOT)
            t = title if upper else (title[:1] + title[1:].lower())
            rest = " ".join(_style(words, "upper" if upper else "title"))
            nospace = rng.random() < 0.2
            dotted = f"{t}.{rest}" if nospace else f"{t}. {rest}"
            plain = f"{t} {rest}"
            a, b = (dotted, plain) if rng.random() < 0.5 else (plain, dotted)
            dtype = "dot_nospace" if nospace else "dot"
            label = rng.choice(LABELS["name"])
        else:
            a, label, hint = _digits_value(rng)
            b = None
            for _ in range(20):
                b = _mutate_digits(rng, a)
                if b and norm_digits(b) != norm_digits(a):
                    break
            if hint == "mono":
                font, group = rng.choice([x for x in fonts if x[1] == "mono"] or fonts)
            dtype = "one_digit"
        if not b or NORM[kind](a) == NORM[kind](b):
            continue
        ref, other = (a, b) if rng.random() < 0.5 else (b, a)
        extra: List[str] = []
        if kind == "digits" and _letter_swap(a, b):
            # A letter in a number is an OCR misread, never a printed number: the reference is the true number,
            # the letter spelling is offered as the OCR read, and the 'differs' member prints another real
            # number (one digit changed), offered third.
            c3 = None
            for _ in range(20):
                c3 = _mutate_digits(rng, a, digits_only=True)
                if c3 and norm_digits(c3) not in (norm_digits(a), norm_digits(b)):
                    break
                c3 = None
            if not c3:
                continue
            ref, other, extra = a, c3, [b]
            dtype = "letter_misread"
        if kind == "name" and dtype in ("added_word",) and ref == b:
            dtype = "dropped_word"
        ink = rng.choice(((20, 20, 20), (10, 10, 60), (35, 30, 30), (10, 20, 90)))
        for member, printed, answer in ((0, ref, "same"), (1, other, "differs")):
            cid = f"S{p:05d}{'ab'[member]}"
            spec = {
                "id": cid, "set": "synth", "kind": kind, "category": "synthetic", "diff_type": dtype,
                "origin_group": "synth", "candidates": [ref, *extra, other],
                "truth": {"answer": answer, "printed": printed,
                          "dot": (dot_after_first(printed) if kind == "dots" else None)},
                "font": font, "font_group": group, "pt": pt, "dpi": dpi, "case_style": "upper" if upper else "title",
                "label": label, "label_in_box": label_in_box, "degradations": list(degr) + (["label_in_box"] if label_in_box else []),
                "ink": list(ink), "render_seed": f"{seed}:{p}:{member}", "source": {"png": f"img/{cid}.png"},
            }
            specs.append(spec)
    return specs


def _texture(rng, w: int, h: int):
    import numpy as np
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    f1, f2, f3 = rng.uniform(0.03, 0.12), rng.uniform(0.01, 0.05), rng.uniform(2, 6)
    v = 0.5 + 0.5 * np.sin(xx * f1 + f3 * np.sin(yy * f2) + rng.uniform(0, 6))
    v2 = 0.5 + 0.5 * np.sin(yy * f1 * 0.7 - f3 * np.cos(xx * f2) + rng.uniform(0, 6))
    base = np.array(rng.choice(((214, 236, 214), (236, 220, 228), (214, 226, 242), (240, 234, 210))), np.float32)
    amp = rng.uniform(18, 40)
    img = base[None, None, :] + (amp * (v * v2))[:, :, None] - amp / 2
    return np.clip(img, 0, 255).astype(np.uint8)


def _stamp(draw_img, rng, cx: float, cy: float, r: float):
    from PIL import Image, ImageDraw
    layer = Image.new("RGBA", draw_img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    col = rng.choice(((110, 40, 150), (40, 60, 170), (190, 40, 50)))
    a = int(rng.uniform(90, 170))
    width = max(2, int(r * 0.06))
    d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=col + (a,), width=width)
    d.ellipse((cx - r * 0.72, cy - r * 0.72, cx + r * 0.72, cy + r * 0.72), outline=col + (a,), width=max(1, width // 2))
    for k in range(18):
        ang = 2 * math.pi * k / 18
        x0, y0 = cx + math.cos(ang) * r * 0.78, cy + math.sin(ang) * r * 0.78
        x1, y1 = cx + math.cos(ang) * r * 0.94, cy + math.sin(ang) * r * 0.94
        d.line((x0, y0, x1, y1), fill=col + (a,), width=max(1, width // 2))
    d.line((cx - r * 0.5, cy, cx + r * 0.5, cy), fill=col + (a,), width=width)
    d.line((cx - r * 0.35, cy - r * 0.25, cx + r * 0.35, cy - r * 0.25), fill=col + (a,), width=max(1, width // 2))
    base = draw_img.convert("RGBA")
    return Image.alpha_composite(base, layer).convert("RGB")


def render_synth(spec: dict, fonts_dir: Path = FONTS_DIR):
    """(RGB uint8 array, box_px [x0, y0, x1, y1], x-height in pixels) of a synthetic spec; deterministic."""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
    rng = random.Random(spec["render_seed"])
    nrng = np.random.default_rng(int(hashlib.sha256(spec["render_seed"].encode()).hexdigest()[:8], 16))
    px = max(8, int(round(spec["pt"] * spec["dpi"] / 72.0)))
    fpath = str(Path(fonts_dir) / spec["font"])
    font = ImageFont.truetype(fpath, px)
    lfont = ImageFont.truetype(str(Path(fonts_dir) / ("arial.ttf" if spec["font_group"].startswith("script")
                                                      else spec["font"])), px)
    printed = spec["truth"]["printed"]
    label = spec.get("label") or ""
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    lb = probe.textbbox((0, 0), label, font=lfont) if label else (0, 0, 0, 0)
    nb = probe.textbbox((0, 0), printed, font=font)
    xb = probe.textbbox((0, 0), "x", font=font)
    x_height = xb[3] - xb[1]
    margin = int(px * rng.uniform(1.2, 2.2))
    gap = int(px * rng.uniform(0.6, 1.2)) if label else 0
    line_h = int(px * 1.7)
    extra_above, extra_below = rng.random() < 0.5, rng.random() < 0.5
    w = margin * 2 + (lb[2] - lb[0]) + gap + (nb[2] - nb[0]) + int(px * rng.uniform(0.5, 3.0))
    h = margin * 2 + line_h * (1 + extra_above + extra_below)
    degr = set(spec.get("degradations") or [])
    if "texture" in degr:
        img = Image.fromarray(_texture(rng, w, h))
    elif "low_contrast" in degr:
        g = int(rng.uniform(196, 228))
        img = Image.new("RGB", (w, h), (g, g, g - 4))
    else:
        img = Image.new("RGB", (w, h), (255, 255, 255) if rng.random() < 0.7 else (250, 248, 240))
    d = ImageDraw.Draw(img)
    ink = tuple(spec.get("ink") or (20, 20, 20))
    if "low_contrast" in degr:
        g = int(rng.uniform(95, 150))
        ink = (g, g, g)
    y = margin + (line_h if extra_above else 0)
    x = margin
    other = ("Date of Birth : 01/01/2000", "Roll : 000000  Reg : 1111111111", "Address : Sample Road, Sample Town",
             "Issue Date : 01/01/2030")
    if extra_above:
        d.text((margin, y - line_h), rng.choice(other), font=lfont, fill=ink)
    if extra_below:
        d.text((margin, y + line_h), rng.choice(other), font=lfont, fill=ink)
    if label:
        d.text((x, y), label, font=lfont, fill=ink)
    nx = x + (lb[2] - lb[0]) + gap
    d.text((nx, y), printed, font=font, fill=ink)
    tb = d.textbbox((nx, y), printed, font=font)
    th = max(1, tb[3] - tb[1])
    box = [tb[0] - 0.35 * th, tb[1] - 0.25 * th, tb[2] + 0.6 * th, tb[3] + 0.25 * th]
    if spec.get("label_in_box") and label:
        box[0] = x - 0.2 * th
    if "stamp" in degr:
        cx = rng.uniform(tb[0], tb[2])
        cy = rng.uniform(tb[1] - th * 0.5, tb[3] + th * 0.5)
        img = _stamp(img, rng, cx, cy, th * rng.uniform(1.4, 2.6))
    if "rotate" in degr:
        ang = rng.choice((-1, 1)) * rng.uniform(0.8, 3.0)
        bgc = img.getpixel((1, 1))
        W0, H0 = img.size
        img = img.rotate(ang, resample=Image.BICUBIC, expand=True, fillcolor=bgc)
        W1, H1 = img.size
        rad = math.radians(ang)
        cx0, cy0, cx1, cy1 = W0 / 2, H0 / 2, W1 / 2, H1 / 2
        pts = []
        for (px_, py_) in ((box[0], box[1]), (box[2], box[1]), (box[0], box[3]), (box[2], box[3])):
            dx, dy = px_ - cx0, py_ - cy0
            pts.append((cx1 + dx * math.cos(rad) + dy * math.sin(rad), cy1 - dx * math.sin(rad) + dy * math.cos(rad)))
        box = [min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts)]
    if "blur" in degr:
        img = img.filter(ImageFilter.GaussianBlur(radius=rng.uniform(0.5, 1.3) * spec["dpi"] / 200.0))
    arr = np.array(img)
    if "salt" in degr:
        frac = rng.uniform(0.003, 0.015)
        m = nrng.random(arr.shape[:2])
        arr[m < frac / 2] = 0
        arr[(m >= frac / 2) & (m < frac)] = 255
    if "jpeg" in degr:
        buf = io.BytesIO()
        Image.fromarray(arr).save(buf, format="JPEG", quality=int(rng.uniform(20, 40)))
        buf.seek(0)
        with Image.open(buf) as im:
            arr = np.array(im.convert("RGB"))
    H, W = arr.shape[:2]
    bx = [max(0, int(math.floor(box[0]))), max(0, int(math.floor(box[1]))),
          min(W, int(math.ceil(box[2]))), min(H, int(math.ceil(box[3])))]
    return arr, bx, x_height


def make_synth(n: int, seed: int = SEED, out: Path = SYNTH_DIR, write_images: bool = True,
               banned: Optional[frozenset] = None) -> List[dict]:
    from PIL import Image
    out = Path(out)
    if not out.resolve().is_relative_to(PRIVATE):
        raise RuntimeError("the synthetic set is written under private-runner only")
    banned = real_name_parts() if banned is None else banned
    specs = synth_specs(n, seed, banned)
    (out / "img").mkdir(parents=True, exist_ok=True)
    for i, s in enumerate(specs, 1):
        arr, box, xh = render_synth(s)
        s["box_px"] = box
        s["x_height_px"] = xh
        s["size_px"] = [int(arr.shape[1]), int(arr.shape[0])]
        validate_case(s)
        if write_images:
            Image.fromarray(arr).save(out / s["source"]["png"], optimize=False, compress_level=6)
        if i % 500 == 0:
            print(f"  {i}/{len(specs)} synthetic images", flush=True)
    (out / "cases.json").write_text(json.dumps(specs, indent=0, ensure_ascii=False), encoding="utf-8")
    meta = {"seed": seed, "n": len(specs), "banned_words": len(banned), "generated": time.strftime("%Y-%m-%d %H:%M"),
            "fonts": sorted({s["font"] for s in specs}), "kinds": dict(collections.Counter(s["kind"] for s in specs)),
            "diff_types": dict(collections.Counter(s["diff_type"] for s in specs)),
            "degradations": dict(collections.Counter(d for s in specs for d in (s["degradations"] or ["clean"])))}
    (out / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    return specs


# =============================================================================================== real set
def _words_of(reader, img) -> List[Tuple[int, int, int, int, str, float]]:
    """Word boxes of a page (CPU light engine, little horizontal merging): (x0, y0, x1, y1, text, conf)."""
    out = []
    for b, text, conf in reader.readtext(img, width_ths=0.05):
        xs = [p[0] for p in b]
        ys = [p[1] for p in b]
        out.append((int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys)), str(text), float(conf)))
    return out


def _lines(words) -> List[List[tuple]]:
    """Words grouped into text lines (y-centre within half a word height), each line left to right."""
    lines: List[List[tuple]] = []
    for w in sorted(words, key=lambda t: (t[1] + t[3]) / 2):
        cy, hh = (w[1] + w[3]) / 2, max(1, w[3] - w[1])
        for ln in lines:
            lcy = sum((t[1] + t[3]) / 2 for t in ln) / len(ln)
            lh = sum(t[3] - t[1] for t in ln) / len(ln)
            if abs(cy - lcy) <= 0.5 * max(hh, lh):
                ln.append(w)
                break
        else:
            lines.append([w])
    for ln in lines:
        ln.sort(key=lambda t: t[0])
    return lines


def locate(words, targets: Sequence[str], region: Optional[Sequence[float]] = None,
           shape=None) -> Tuple[Optional[Tuple[int, int, int, int]], float]:
    """The contiguous run of words on one line whose letters are nearest any target (box, relative
    distance). Region (normalised) restricts the words considered."""
    if region is not None and shape is not None:
        rx0, ry0, rx1, ry1 = box_pixels(region, shape)
        words = [w for w in words if w[0] >= rx0 - 5 and w[2] <= rx1 + 5 and w[1] >= ry0 - 5 and w[3] <= ry1 + 5]
    best, best_d = None, 9.0
    tforms = [norm_letters(t) for t in targets if norm_letters(t)]
    for ln in _lines(words):
        for i in range(len(ln)):
            if not norm_letters(ln[i][4]):             # a run starts and ends on a word with letters
                continue
            acc = ""
            for j in range(i, min(len(ln), i + 8)):
                part = norm_letters(ln[j][4])
                acc += part
                if not part:
                    continue
                for tf in tforms:
                    d = edit_distance(acc, tf) / max(len(tf), 1)
                    if d < best_d - 1e-9:
                        seg = ln[i:j + 1]
                        best_d = d
                        best = (min(t[0] for t in seg), min(t[1] for t in seg), max(t[2] for t in seg),
                                max(t[3] for t in seg))
    return best, best_d


def pad_box(b: Tuple[int, int, int, int], shape, right_h: float = 0.6, lines: int = 1) -> List[float]:
    """A located word box padded as the closer look expects (a trailing dot inside) and normalised. right_h:
    the right padding in line heights (a case whose OCR read words after the name takes in where it read
    them); lines: the text lines the box spans (a wrapped cell), so the padding is per line."""
    h, w = shape[:2]
    x0, y0, x1, y1 = b
    th = max(1, (y1 - y0) / max(1, lines))
    x0, x1 = x0 - 0.35 * th, x1 + right_h * th
    y0, y1 = y0 - 0.25 * th, y1 + 0.25 * th
    return [round(max(0.0, x0 / w), 5), round(max(0.0, y0 / h), 5), round(min(1.0, x1 / w), 5),
            round(min(1.0, y1 / h), 5)]


def build_real(spec_path: Path = REAL_DIR / "spec.json", out_dir: Path = REAL_DIR,
               ids: Optional[Sequence[str]] = None, sheets: bool = True, locate_dpi: int = LOCATE_DPI) -> List[dict]:
    """real\\spec.json -> real\\cases.json (+ crops and contact sheets for the eye check). A spec entry:
    {"id", "file", "page" (0-based), "rot90" (quarter turns counter-clockwise, or "auto"), "find": [strings]
    (each located; their union is the box) | "box": normalised, "region": normalised search area,
    "kind", "reference", "ocr": [reads], "printed", "answer", "dot", "category", "diff_type", "origin",
    "origin_group", "verified"}. Prints ids and match distances only."""
    from PIL import Image
    out_dir = Path(out_dir)
    if not out_dir.resolve().is_relative_to(PRIVATE):
        raise RuntimeError("the real set is written under private-runner only")
    specs = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    old = {}
    cp = out_dir / "cases.json"
    if cp.exists():
        old = {c["id"]: c for c in json.loads(cp.read_text(encoding="utf-8"))}
    cache_p = out_dir / "words_cache.json"
    cache = json.loads(cache_p.read_text(encoding="utf-8")) if cache_p.exists() else {}
    reader = None
    built = []
    for s in specs:
        if ids and s["id"] not in ids:
            if s["id"] in old:
                built.append(old[s["id"]])
            continue
        kind = s["kind"]
        rot = s.get("rot90", 0)
        src = {"file": s["file"], "page": int(s.get("page", 0)), "rot90": 0}
        if rot == "auto":
            raise CaseError(f"{s['id']}: give rot90 explicitly")
        src["rot90"] = int(rot)
        box = s.get("box")
        dist = 0.0
        if box is None:
            stem = f"{src['file']}|{src['page']}|{src['rot90']}|"
            have = [k for k in cache if k.startswith(stem)]          # a page read at any resolution serves
            dpi_used = int(have[0].rsplit("|", 1)[1]) if have and f"{stem}{locate_dpi}" not in cache else locate_dpi
            img = render_source(src, dpi_used)
            key = f"{stem}{dpi_used}"
            if key not in cache:
                if reader is None:
                    from src.verify.runner import engine as E
                    reader = E.make_reader()
                cache[key] = [list(w) for w in _words_of(reader, img)]
                cache_p.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
            words = [tuple(w) for w in cache[key]]
            boxes = []
            for t in s["find"]:
                b, d = locate(words, [t], s.get("region"), img.shape)
                if b is None:
                    raise CaseError(f"{s['id']}: nothing found")
                boxes.append(b)
                dist = max(dist, d)
            ub = (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes),
                  max(b[3] for b in boxes))
            box = pad_box(ub, img.shape, float(s.get("pad_right_h", 0.6)), len(s["find"]))
        cands = s.get("candidates") or dedupe(kind, [s["reference"], *(s.get("ocr") or []), s["printed"]])
        c = {"id": s["id"], "set": "real", "kind": kind, "category": s.get("category", ""),
             "diff_type": s.get("diff_type", ""), "origin": s.get("origin", ""),
             "origin_group": s.get("origin_group", ""), "source": src, "box": box, "candidates": cands,
             "reference": s["reference"], "ocr": s.get("ocr") or [], "printed_offered_by_ocr":
                 any(same_form(kind, o, s["printed"]) for o in (s.get("ocr") or [])) or
                 same_form(kind, s["reference"], s["printed"]),
             "truth": {"answer": s["answer"], "printed": s["printed"], "dot": s.get("dot")},
             "degradations": s.get("tags") or [], "verified": s.get("verified", ""), "locate_distance": round(dist, 3)}
        validate_case(c)
        built.append(c)
        print(f"  {c['id']}: box located (distance {dist:.2f})", flush=True)
    built.sort(key=lambda c: c["id"])
    cp.write_text(json.dumps(built, indent=1, ensure_ascii=False), encoding="utf-8")
    if sheets:
        make_sheets(built if not ids else [c for c in built if c["id"] in ids], out_dir,
                    prefix="sheet" if not ids else f"sheet_{sorted(ids)[0]}")
    return built


def make_sheets(cases: Sequence[dict], out_dir: Path = REAL_DIR, per_sheet: int = 8, prefix: str = "sheet") -> None:
    """Private eye-check images: each case's crop at 400 dpi with context and the box in red, the case id,
    the printed spelling and candidates written above it; per_sheet crops per sheet."""
    from PIL import Image, ImageDraw, ImageFont
    crops_dir, sheets_dir = Path(out_dir) / "crops", Path(out_dir) / "sheets"
    crops_dir.mkdir(parents=True, exist_ok=True)
    sheets_dir.mkdir(parents=True, exist_ok=True)
    font = ImageFont.truetype(str(FONTS_DIR / "arial.ttf"), 22)
    tiles = []
    for c in cases:
        img, box = case_image(c)
        x0, y0, x1, y1 = box
        bh = y1 - y0
        cx0, cy0 = max(0, x0 - 2 * bh), max(0, y0 - int(1.2 * bh))
        cx1, cy1 = min(img.shape[1], x1 + 2 * bh), min(img.shape[0], y1 + int(1.2 * bh))
        crop = Image.fromarray(img[cy0:cy1, cx0:cx1].copy())
        dr = ImageDraw.Draw(crop)
        dr.rectangle((x0 - cx0, y0 - cy0, x1 - cx0, y1 - cy0), outline=(230, 0, 0), width=2)
        crop.save(crops_dir / f"{c['id']}.png")
        scale = min(1.0, 1500 / max(1, crop.width))
        if scale < 1:
            crop = crop.resize((int(crop.width * scale), int(crop.height * scale)))
        cap = (f"{c['id']} {c['kind']} truth={c['truth']['answer']} dot={c['truth'].get('dot')} | printed: "
               f"{c['truth']['printed']} | cands: {' / '.join(c['candidates'])}")
        tiles.append((cap, crop))
    for k in range(0, len(tiles), per_sheet):
        part = tiles[k:k + per_sheet]
        W = max(max(t[1].width for t in part), 900)
        H = sum(t[1].height + 34 for t in part)
        sheet = Image.new("RGB", (W, H), (255, 255, 255))
        dr = ImageDraw.Draw(sheet)
        y = 0
        for cap, crop in part:
            dr.text((4, y + 4), cap, font=font, fill=(0, 0, 160))
            sheet.paste(crop, (0, y + 30))
            y += crop.height + 34
        sheet.save(sheets_dir / f"{prefix}_{k // per_sheet:03d}.png")


# =============================================================================================== CLI
def _parse(argv):
    ap = argparse.ArgumentParser(prog="closer_eval", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("synth", help="build the synthetic set")
    s.add_argument("--n", type=int, default=2400)
    s.add_argument("--seed", type=int, default=SEED)
    b = sub.add_parser("build-real", help="locate the boxes of real\\spec.json, write real\\cases.json")
    b.add_argument("--ids", default="")
    b.add_argument("--no-sheets", action="store_true")
    b.add_argument("--locate-dpi", type=int, default=LOCATE_DPI)
    p = sub.add_parser("report", help="re-print a finished run's report from its results (no re-run)")
    p.add_argument("--name", required=True)
    p.add_argument("--partial", action="store_true", help="the rows a running run has written so far")
    p.add_argument("--set", choices=("real", "synth", "all"), default="all")
    p.add_argument("--grid", default="")
    r = sub.add_parser("run", help="evaluate an engine on the cases")
    r.add_argument("--engine", choices=("closer", "ocr"), default="closer")
    r.add_argument("--set", choices=("real", "synth", "all"), default="all")
    r.add_argument("--limit", type=int, default=0)
    r.add_argument("--sample", type=int, default=0, help="a stratified random sample of N synthetic cases")
    r.add_argument("--ids", default="")
    r.add_argument("--no-box", action="store_true", help="pass box=None (the closer look searches the page)")
    r.add_argument("--production", action="store_true",
                   help="as the runner calls it: closer.page_image of the page, not turned upright, box=None")
    r.add_argument("--candidates", choices=("offered", "runner"), default="offered",
                   help="offered: the case's candidates (reference, OCR reads, printed spelling); runner: only "
                        "what the runner has (reference + OCR reads; the printed spelling only when no OCR read "
                        "was recorded)")
    r.add_argument("--grid", default="")
    r.add_argument("--margins", default="", help="re-run the closer with closer.MARGIN set to each value "
                                                 "(exact sweep; costs one run per value)")
    r.add_argument("--dpi", type=int, default=RENDER_DPI)
    r.add_argument("--name", default="")
    r.add_argument("--seed", type=int, default=SEED)
    return ap.parse_args(argv)


def _sample(cases: List[dict], n: int, seed: int) -> List[dict]:
    """Every real case, plus n synthetic cases drawn by pair (both members kept), stratified by kind."""
    real = [c for c in cases if c["set"] == "real"]
    syn = [c for c in cases if c["set"] == "synth"]
    if n <= 0 or n >= len(syn):
        return cases
    pairs = collections.defaultdict(list)
    for c in syn:
        pairs[c["id"][:-1]].append(c)
    by_kind = collections.defaultdict(list)
    for k, v in pairs.items():
        by_kind[v[0]["kind"]].append(k)
    rng = random.Random(seed)
    keep = []
    total_pairs = len(pairs)
    for kind, keys in sorted(by_kind.items()):
        keys.sort()
        take = max(1, round(n / 2 * len(keys) / total_pairs))
        keep += rng.sample(keys, min(take, len(keys)))
    keep = set(keep)
    return real + [c for c in syn if c["id"][:-1] in keep]


def rows_of(results: Sequence[dict]) -> List[Row]:
    """Rows back from a results_private.json."""
    names = set(Row.__dataclass_fields__)
    return [Row(**{k: v for k, v in d.items() if k in names}) for d in results]


def report_again(name: str, which: str = "all", grid: Sequence[float] = DEFAULT_GRID,
                 results_dir: Path = RESULTS_DIR, partial: bool = False) -> int:
    """Re-prints (and re-writes report.txt of) a finished run from results_private.json and run_meta.json,
    restricted to one set when asked; partial: from results_partial.json of a run still going (printed only).
    2 when the run is missing."""
    out = Path(results_dir) / name
    rp = out / ("results_partial.json" if partial else "results_private.json")
    if not rp.exists():
        print(f"closer_eval: no results under results\\{name}", file=sys.stderr)
        return 2
    rows = [r for r in rows_of(json.loads(rp.read_text(encoding="utf-8"))) if which in ("all", r.set)]
    try:                                   # real tags refined after the run are reported (labels are not changed)
        cur = {c["id"]: c for c in load_cases("real")} if any(r.set == "real" for r in rows) else {}
    except (CaseError, ValueError):
        cur = {}
    for r in rows:
        c = cur.get(r.id)
        if c is not None and c["kind"] == r.kind and c["truth"]["answer"] == r.expected:
            r.groups = dict(case_groups(c), degradations="+".join(c.get("degradations") or []))
    mp = out / "run_meta.json"
    meta = json.loads(mp.read_text(encoding="utf-8")) if mp.exists() else {}
    prov = meta.get("provenance") or {}
    header = [f"mode {meta.get('mode', '?')}; candidates {meta.get('candidates', '?')}", provenance_line(prov)]
    if meta.get("seconds") is not None:
        header.append(f"run {meta['seconds']} s ({meta.get('started')} - {meta.get('finished')})")
    exact = None
    ep = out / "exact_sweep.json"
    if ep.exists():
        exact = json.loads(ep.read_text(encoding="utf-8"))
    text, ok = format_report(rows, meta.get("engine", "?"), grid, prov.get("MARGIN"),
                             float(prov.get("MOST", 0.0) or 0.0), prov.get("MIN_SCORE"), exact, header=header)
    if partial:
        text = text.replace("closer_eval:", f"closer_eval (PARTIAL: {len(rows)} case(s) so far):", 1)
    elif which == "all":
        (out / "report.txt").write_text(text, encoding="utf-8")
    print(text)
    return 0 if ok else 1


def runner_candidates(c: dict) -> dict:
    """The case with only the candidates the runner itself has (production): the reference and the OCR
    read(s), not the printed spelling the eye found. A real case whose OCR read was not recorded (or that would
    keep fewer than two candidates) keeps its offered candidates; a synthetic case is unchanged (its second
    candidate plays the OCR read)."""
    if c.get("set") != "real":
        return c
    cands = dedupe(c["kind"], [c.get("reference") or c["candidates"][0], *(c.get("ocr") or [])])
    if len(cands) < 2 or NORM[c["kind"]](cands[0]) != NORM[c["kind"]](c["candidates"][0]):
        return dict(c, candidates_mode="offered (no OCR read recorded)")
    return dict(c, candidates=cands, candidates_mode="runner")


def main(argv=None) -> int:
    args = _parse(sys.argv[1:] if argv is None else argv)
    if args.cmd == "synth":
        t0 = time.time()
        specs = make_synth(args.n, args.seed)
        kinds = collections.Counter(s["kind"] for s in specs)
        ans = collections.Counter(s["truth"]["answer"] for s in specs)
        print(f"synthetic set: {len(specs)} cases ({dict(kinds)}; {dict(ans)}), seed {args.seed}, "
              f"{time.time() - t0:.0f} s")
        return 0
    if args.cmd == "build-real":
        ids = [x.strip() for x in args.ids.split(",") if x.strip()] or None
        built = build_real(ids=ids, sheets=not args.no_sheets, locate_dpi=args.locate_dpi)
        cnt = collections.Counter((c["kind"], c["truth"]["answer"]) for c in built)
        print(f"real set: {len(built)} cases; " + ", ".join(f"{k[0]}/{k[1]} {v}" for k, v in sorted(cnt.items())))
        return 0
    if args.cmd == "report":
        return report_again(args.name, args.set,
                            tuple(float(x) for x in args.grid.split(",") if x.strip()) or DEFAULT_GRID,
                            partial=args.partial)
    try:
        cases = load_cases(args.set)
    except (CaseError, ValueError) as exc:
        print(f"closer_eval: bad case data ({exc})", file=sys.stderr)
        return 2
    if not cases:
        print("closer_eval: no cases (run `synth` and `build-real` first)", file=sys.stderr)
        return 2
    if args.ids:
        want = {x.strip() for x in args.ids.split(",") if x.strip()}
        cases = [c for c in cases if c["id"] in want]
    if args.sample:
        cases = _sample(cases, args.sample, args.seed)
    if args.limit:
        cases = cases[: args.limit]
    if args.candidates == "runner":
        cases = [runner_candidates(c) for c in cases]
        kept = sum(1 for c in cases if c.get("candidates_mode", "").startswith("offered"))
        if kept:
            print(f"closer_eval: {kept} real case(s) keep the offered candidates (no OCR read recorded)", flush=True)
    margin_used = None
    module = None
    gates = {"min_agree": 0.0, "min_score": None}
    if args.engine == "closer":
        try:
            module, look, margin_used, gates = closer_engine()
        except ImportError as exc:
            print(f"closer_eval: src.verify.runner.closer cannot be imported ({type(exc).__name__}); "
                  f"use --engine ocr for the baseline", file=sys.stderr)
            return 2
    else:
        look = OcrBaseline()
    grid = tuple(float(x) for x in args.grid.split(",") if x.strip()) or DEFAULT_GRID
    margins = [float(x) for x in args.margins.split(",") if x.strip()]
    name = args.name or f"{args.engine}_{time.strftime('%Y%m%d_%H%M%S')}"
    out = RESULTS_DIR / name
    out.mkdir(parents=True, exist_ok=True)
    image_fn, no_box, mode = None, args.no_box, "box (upright render, the stored box)"
    if args.production:
        if module is None:
            print("closer_eval: --production needs --engine closer", file=sys.stderr)
            return 2
        image_fn = lambda c: production_image(c, module, args.dpi)  # noqa: E731
        no_box, mode = True, "production (closer.page_image, not turned, box=None)"
    elif args.no_box:
        mode = "no box (upright render, box=None)"
    prov = provenance(module)
    print(f"closer_eval: {len(cases)} case(s), engine {args.engine}, mode {mode}, results under results\\{name}",
          flush=True)
    print(f"  {provenance_line(prov)}", flush=True)
    t0 = time.time()
    meta = {"name": name, "engine": args.engine, "mode": mode, "set": args.set, "cases": len(cases),
            "candidates": args.candidates, "provenance": prov, "dpi": args.dpi,
            "started": time.strftime("%Y-%m-%d %H:%M:%S"), "argv": list(sys.argv[1:] if argv is None else argv)}
    (out / "run_meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    rows = run_cases(cases, look, no_box=no_box, dpi=args.dpi, image_fn=image_fn,
                     partial=out / "results_partial.json")
    exact = exact_sweep(module, cases, margins, no_box, args.dpi, image_fn) if (margins and module is not None) else None
    meta.update(finished=time.strftime("%Y-%m-%d %H:%M:%S"), seconds=round(time.time() - t0, 1))
    (out / "run_meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    (out / "results_private.json").write_text(json.dumps([r.to_json() for r in rows], indent=0), encoding="utf-8")
    if exact:
        (out / "exact_sweep.json").write_text(json.dumps(exact, indent=1), encoding="utf-8")
    text, ok = format_report(rows, args.engine, grid, margin_used, gates["min_agree"], gates["min_score"], exact,
                             header=[f"mode {mode}; candidates {args.candidates}", provenance_line(prov),
                                     f"run {meta['seconds']} s ({meta['started']} - {meta['finished']})"])
    (out / "report.txt").write_text(text, encoding="utf-8")
    print(text)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
