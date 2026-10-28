"""The closer look (owner 28 Oct 2026): before a name or a number that reads differently becomes a verdict, the
system looks at the page image again ITSELF and asks which of the KNOWN spellings the print supports.

  "make sure if the reader misreads one letter of a name before giving verdict it will take a closer look and give
   correct verdict. i want the final outcome of this as industry standard" (owner, 28 Oct)

This is verification of a known value, not a fresh guess (the way cheque and form readers verify a claimed
amount or name): the recogniser is not asked "what does this say?" but "how well does the image support each
candidate?".

  1. Find the text. `box` (pixels of `image`) when the caller knows it; else the page is searched: the CRAFT
     detector's line boxes, one recogniser pass per box, and a keyword score of every candidate (free text before
     and after it) picks the line; the boxes beside it on the same line join it (a label before, a further word
     after). A name a table cell wraps over two lines is tried as the two lines side by side too.
  2. Prepare the crop: a small one is enlarged 2-3x (Lanczos); printed rules through or under the text are painted
     out (an underline makes a 3 read as a 2); then views: four vertical margins (PADS) x five channels (grey, the
     maximum channel where coloured artwork fades, red, green, blue: the recogniser reads one line differently with
     more or less margin and with the artwork in or out), plus contrast-stretched, Otsu and adaptive binarised,
     sharpened, thicker and thinner strokes (bold / thin fonts) and two small skews.
  3. Score, do not re-guess: the english_g2 recogniser (the engine's CPU ONNX session; never DirectML) gives a
     per-time-step softmax; the CTC log-likelihood of EACH candidate is computed by the forward algorithm (forced
     alignment, every path summed). Letters are scored case-blind (upper and lower case of a letter are one class),
     a space or a label's colon is free (folded into the blank), '.' is its own class for kind "dots" (the
     recogniser's charset has it, so "MD." vs "MD" is scored directly), '<' is a separator for an MRZ line.
     The frames scored are those of the candidates' own region of the line (their keyword alignments, joined):
     a label before the name or a further field after it is not counted against any candidate, while a word ONE
     candidate adds (an MST, a BEGUM, a tail) lies in the region and is counted.
  4. Dots (kind "dots"): besides the CTC score, a geometric dot detector looks after the letter where a candidate
     has its dot: a small solid blob near the baseline, sized against the letter height, apart from the letters
     on either side (True / False / None = cannot tell). The recogniser drops printed dots (most of all one glued
     to the next word, "MD.NAME"): the dot's shape decides when the recogniser's lead is below DOTS_ALONE; the two
     agreeing decide; without a measured shape the recogniser alone may confirm a dot (lead >= DOTS_ALONE) but never
     "no dot". Look.dot is given only with a decision.
  5. Decide: per variant, scores are the CTC log-likelihood per character (one common divisor: the reference's
     length, so the order is the likelihood order); "same" (the image supports candidates[0]) or "differs" (it
     supports another candidate) only when the winner leads by >= MARGIN on average AND in most variants (MOST), at
     most DISSENT of the variants back another candidate by the margin, and its own score is that of readable text
     (>= MIN_SCORE); anything else is "unclear" (FLAG "check by eye").
     A REAL difference can come out "differs" or "unclear", never "same": "same" needs the reference to explain the
     print clearly better than the other spelling, in most variants.
     Every difference must hold by itself (segments(), differences_hold(); SEG_MIN nats): the places where the
     winner differs from another candidate come from a minimal edit alignment, and the winner is scored against
     itself with ONE place taken from the other candidate. Each place must favour the winner by SEG_MIN, else
     "unclear": a spelling that wins on the sum while one of its places reads the other way is not the print (a
     third spelling, e.g. a one-letter difference hidden behind an OCR variant that also adds a word; or a misread the
     recogniser shares, e.g. bold italic a / t read as u / l in every view). Letters a winner ADDS (a word, a tail)
     must also read as those letters by themselves (added_reads(), ADDED_MIN): artwork, a stamp or a Bangla line
     scores low. A wrapped line (line_under / the detector's box under the line) is tried as a continuation; when the
     line alone and the two lines support different spellings, "unclear".
  6. A second expert for letters: when the two best spellings differ in one small letter of another height (t / l,
     i / l, n / h, a / d, r / t ...), shape_check() measures that letter against the word's own ascender and x lines.
     The recogniser can read a small t as l in every view of a low-resolution print (owner folder 3): the shape and
     at least one view of the recogniser backing the other letter decide it; the shape against every view is
     "unclear". A single candidate is verified against the recogniser's own reading of its place.
  Calibration (synthetic made-up crops, 8 Windows fonts, 5 sizes, blur / JPEG / noise / tinted paper / artwork
  stripe; 179 letter looks): 169 correct, 9-10 unclear, 0-1 wrong, no false "same" at MARGIN 0.12 (any MARGIN from
  0.03 to 0.3 gives the same picture; DISSENT 0.1 trades 5 unclear for 1 wrong against 0.0). Dots: 30 / 40 correct,
  8 unclear, 2 wrong before DOTS_ALONE (both on bold serif initials whose dot the shape test could not measure).

CPU only (S5): the recogniser and detector are the engine's CPU sessions (engine.make_reader); the closer look must
work in the CPU-only app. No network, no files (the caller saves a crop when it wants one). No personal data.

Public: Look, look(), page_image(), MARGIN; bind(); available(); dot_at(); render helpers for tests live in the tests.
"""
from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

# ---------------------------------------------------------------------------------------------------------- tuning
MARGIN = 0.12            # mean per-character log-likelihood lead (nats per reference character); see calibrate notes
MIN_SCORE = -1.6         # the winner's mean per-character log-likelihood: below it the line is not that text
MOST = 0.85              # share of the image variants whose own winner (with the margin) must be the winner (0.6
#                          before A2: a bold italic name read one letter short in 19 of 28 views gave a false
#                          "differs" on owner folder 3)
LOCATE_MIN = -2.6        # keyword score per character a line needs to be taken as the candidates' line
REGION_MIN = -2.2        # keyword score per character a candidate needs to widen the scored region
PAD_FRAMES = 2           # frames kept around the candidates' region (onsets of the first and last letters)
SMALL_LINE = 48          # a text line lower than this (pixels) is enlarged before the variants
MODEL_H = 64             # the recogniser's input height
MAX_SIDE = 6000          # a crop is never wider than this after enlarging
SAME_LINE_GAP = 3.0      # line heights between two boxes that are still one line
NEIGHBOURS = 3           # boxes taken beside the best box on each side
TWO_LINES_GAIN = 0.25    # a wrapped cell's two lines side by side must read this much better (per character)
PADS = (0.06, 0.25, 0.5, 0.9)                         # vertical margins around the line, shares of its height
CHANNELS = ("gray", "dark", "red", "green", "blue")   # the views of a coloured print
PAD_X = 0.35             # horizontal margin, shares of the line height
DISSENT = 0.1            # share of the variants that may back another candidate with the margin (else unclear)
DOT_SIZE = (0.07, 0.42)  # a printed dot's width and height, as shares of the letter height
DOT_FILL = 0.42          # a dot is solid: its ink over its bounding box
DOTS_ALONE = 0.6         # without the dot's shape, the recogniser decides a dot only with this lead (it drops dots)
SEG_MIN = 2.0            # nats: each place where the winner differs from another candidate must favour it this much
ADDED_MIN = -1.6        # keyword score per letter an added word (a tail) must read with by itself
DIFFERS_MIN = -1.0       # rev 2.5: a "differs" needs the winner read this well (per character); a weak read of a
#                          script print is no confirmed difference (owner folder 9 SSC: a garbled OCR read of a
#                          script name won at -1.2 on a two-line view and gave a false FAIL)
EXTEND_GAP = 1.5         # line heights: ink beside a found line closer than this belongs to it (a missed word)
EXTEND_REACH = 14        # line heights looked at on either side for that ink
OCCUR_LEAD = 0.5         # nats per character: other lines matching this close to the best line are looked at too
OCCUR_MIN = -1.0         # ... and only clean matches (per character)
OCCUR_MAX = 3            # lines looked at when the page prints the candidates more than once
RUN_GAP = 2.5            # rev 2.6 (checker): line heights (letter to letter) within which a further word beside a
#                          single value belongs to it (an added MST, MD or BEGUM printed next to the name)
WORD_BREAK = 1.2         # line heights between two letters the recogniser reads with no space that still split words
VERSION = "closer-3"

# rev 2.6: words that end a value instead of adding to it, read beside a single value: a label, a connecting word of
# a certificate's sentence, a relation column. None of them is a name word; anything else read there is a further
# word of the value (an unknown word is never taken as a label).
LABEL_WORDS = frozenset((
    "NAME", "NAMES", "OF", "THE", "THAT", "THIS", "IS", "TO", "AND", "BY", "IN", "FOR", "AS", "SON", "DAUGHTER",
    "WIFE", "HUSBAND", "SO", "DO", "WO", "FATHER", "FATHERS", "MOTHER", "MOTHERS", "PARENT", "PARENTS",
    "GUARDIAN", "GUARDIANS", "STUDENT", "STUDENTS", "CANDIDATE", "CANDIDATES", "APPLICANT", "APPLICANTS",
    "EXAMINEE", "HOLDER", "HOLDERS", "ACCOUNT", "TITLE", "OWNER", "OWNERS", "PROPRIETOR", "LICENSEE", "TAXPAYER",
    "TAXPAYERS", "ASSESSEE", "EMPLOYEE", "BENEFICIARY", "DECEASED", "CERTIFY", "CERTIFIED", "CERTIFIES", "SELF",
    "BROTHER", "SISTER", "SPOUSE", "RELATION", "LATE",
    # rev 2.6 (takeover): the next field's label on a certificate, a card, a transcript or a bank / tax print (no
    # name word among them): without them a table row's next column read as a "further word" made every
    # single-spelling look unclear
    "DATE", "BIRTH", "DOB", "ROLL", "REG", "REGISTRATION", "NO", "NUMBER", "SERIAL", "SEX", "GENDER", "MALE",
    "FEMALE", "ADDRESS", "VILLAGE", "POST", "OFFICE", "DISTRICT", "UPAZILA", "THANA", "WARD", "NATIONALITY",
    "BANGLADESHI", "RELIGION", "OCCUPATION", "MOBILE", "PHONE", "SESSION", "GROUP", "INSTITUTION", "CENTRE",
    "CENTER", "BOARD", "YEAR", "GPA", "CGPA", "GRADE", "RESULT", "ID", "NID", "PASSPORT", "SIGNATURE", "PERMANENT",
    "PRESENT", "BLOOD", "AGE", "TYPE", "CLASS", "SUBJECT", "EXAMINATION", "SCHOOL", "COLLEGE", "UNIVERSITY",
    "ISSUE", "EXPIRY", "PLACE", "COUNTRY", "BANGLADESH", "TIN", "BRANCH", "BALANCE", "AMOUNT", "TAKA", "TK", "BDT"))
LEFT_TITLES = frozenset(("MR", "MRS", "MS", "MISS", "DR"))   # Q16 runner default: a leading title is dropped
# rev 2.6 (takeover): a passport MRZ line 1 before the surname: the document code and the issuing state ("P<" + three
# letters, read glued to the surname). identity._mrz_by_closer_look asks for the printed name in MRZ order alone;
# that header is no added word (an MRZ name field cannot carry one before the surname)
MRZ_HEAD = re.compile(r"^[PIACV][A-Z<][A-Z<]{3}$")

_NEG = -1.0e9


@dataclass
class Look:
    decision: str              # "same" (the image supports candidates[0]) | "differs" (it supports another) | "unclear"
    best: str                  # the candidate the image supports best
    scores: Dict[str, float]   # per candidate: mean per-character CTC log-likelihood over the variants
    margin: float              # best score minus the runner-up's
    variants: int              # image variants scored
    agree: int                 # variants whose best == best
    dot: Optional[bool]        # kind == "dots": a printed dot seen right after the word (None: not asked / unclear)
    note: str                  # short, masked-safe (no names)
    detail: Dict[str, Any] = field(default_factory=dict)   # box, rotation, per-variant winners (no text)


# ---------------------------------------------------------------------------------------------------------- reader
_READER: Any = None


def _inner(obj: Any) -> Any:
    """The LightReader inside an Engine, a CountingReader or a LightReader (anything with .rec and .det)."""
    seen = 0
    while obj is not None and seen < 4:
        if hasattr(obj, "rec") and hasattr(obj, "det") and hasattr(obj, "character"):
            return obj
        obj = getattr(obj, "reader", None) or getattr(obj, "inner", None)
        seen += 1
    return None


def available(engine: Any) -> bool:
    """True when `engine` carries the real recogniser (ONNX sessions), so a closer look can be taken."""
    return _inner(engine) is not None


def bind(engine: Any) -> bool:
    """Use this engine's CPU reader for every later look (no second model load). False when it has none."""
    global _READER, _REC
    r = _inner(engine)
    if r is None:
        return False
    if r is not _READER:
        _READER, _REC = r, None
    return True


def _reader() -> Any:
    global _READER
    if _READER is None:
        from .engine import make_reader            # CPU execution provider only (engine.PROVIDERS)
        _READER = make_reader()
    return _READER


class _Rec:
    """The recogniser's classes folded for scoring: 0 = blank (with the space and the free marks), 1-26 = A-Z
    (both cases), 27-36 = 0-9, then the kept marks of the kind ('.' for dots, '<' for digits)."""

    LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    DIGITS = "0123456789"

    def __init__(self, reader: Any) -> None:
        self.reader = reader
        chars = list(reader.character)
        lang = set(reader.lang_char)
        self.n = len(chars) + 1
        self.chars = chars
        self.ignore = np.array([i + 1 for i, c in enumerate(chars) if c not in lang], dtype=np.int64)
        self._fold: Dict[str, Tuple[np.ndarray, Dict[str, int]]] = {}

    def fold(self, kind: str) -> Tuple[np.ndarray, Dict[str, int]]:
        if kind in self._fold:
            return self._fold[kind]
        keep = {"dots": ".", "digits": "<"}.get(kind, "")
        index: Dict[str, int] = {}
        k = 1
        for ch in self.LETTERS + self.DIGITS + keep:
            index[ch] = k
            k += 1
        m = np.zeros((self.n, k), dtype=np.float32)
        m[0, 0] = 1.0
        for i, c in enumerate(self.chars):
            up = c.upper() if c.isalpha() and c.isascii() else c
            if kind == "dots" and c == ",":
                up = "."                                 # a printed period is often recognised as a comma
            m[i + 1, index.get(up, 0)] = 1.0
        self._fold[kind] = (m, index)
        return self._fold[kind]

    def probs(self, gray: np.ndarray) -> np.ndarray:
        """(T, classes) softmax of a grey line image (any size; resized to the model height as easyocr does)."""
        import cv2
        h, w = gray.shape[:2]
        ratio = max(w / float(max(h, 1)), 1.0)
        rw = max(int(math.ceil(MODEL_H * ratio)), 8)
        interp = cv2.INTER_AREA if h > MODEL_H else cv2.INTER_LINEAR
        img = cv2.resize(gray, (rw, MODEL_H), interpolation=interp)
        width = int(math.ceil(ratio)) * MODEL_H
        x = img.astype(np.float32) / np.float32(255.0)
        x = (x - np.float32(0.5)) / np.float32(0.5)
        pad = np.empty((1, 1, MODEL_H, max(width, rw)), dtype=np.float32)
        pad[0, 0, :, :rw] = x
        if pad.shape[3] > rw:
            pad[0, 0, :, rw:] = x[:, rw - 1:rw]
        logits = self.reader.rec.run(None, {"image": pad})[0][0].astype(np.float64)
        e = np.exp(logits - logits.max(axis=1, keepdims=True))
        p = e / e.sum(axis=1, keepdims=True)
        if self.ignore.size:
            p[:, self.ignore] = 0.0
        p /= np.maximum(p.sum(axis=1, keepdims=True), 1e-12)
        return p

    def logp(self, gray: np.ndarray, kind: str) -> np.ndarray:
        m, _ = self.fold(kind)
        return np.log(np.maximum(self.probs(gray) @ m, 1e-12))

    def both(self, gray: np.ndarray, kind: str) -> Tuple[np.ndarray, np.ndarray]:
        """(the unfolded softmax, the folded log-probabilities) of one line image, from one recogniser pass."""
        m, _ = self.fold(kind)
        p = self.probs(gray)
        return p, np.log(np.maximum(p @ m, 1e-12))


_REC: Optional[_Rec] = None


def _rec() -> _Rec:
    global _REC
    r = _reader()
    if _REC is None or _REC.reader is not r:
        _REC = _Rec(r)
    return _REC


# ---------------------------------------------------------------------------------------------------------- labels
def _ascii_upper(s: str) -> str:
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().upper()


def norm(candidate: str, kind: str = "name") -> str:
    """The symbols scored for a candidate: letters and digits; '.' too for kind "dots" (a run of dots is one, none
    before the first or after the last letter); '<' too for kind "digits"."""
    s = _ascii_upper(candidate)
    keep = {"dots": ".", "digits": "<"}.get(kind, "")
    out = "".join(ch for ch in s if ch.isalnum() or ch in keep)
    if kind == "dots":
        while ".." in out:
            out = out.replace("..", ".")
        out = out.strip(".")
    return out


def _encode(candidate: str, kind: str, index: Dict[str, int]) -> List[int]:
    return [index[ch] for ch in norm(candidate, kind) if ch in index]


# ---------------------------------------------------------------------------------------------------------- CTC
def _ext(label: Sequence[int]) -> Tuple[np.ndarray, np.ndarray]:
    ext = np.zeros(2 * len(label) + 1, dtype=np.int64)
    ext[1::2] = label
    skip = np.zeros(len(ext), dtype=bool)
    for s in range(2, len(ext)):
        skip[s] = ext[s] != 0 and ext[s] != ext[s - 2]
    return ext, skip


def ctc_logl(lp: np.ndarray, label: Sequence[int]) -> float:
    """log P(label | frames) under CTC (blank = class 0): the forward algorithm, every alignment summed."""
    T = lp.shape[0]
    if not len(label):
        return float(lp[:, 0].sum())
    ext, skip = _ext(label)
    S = len(ext)
    a = np.full(S, -np.inf)
    a[0] = lp[0, ext[0]]
    if S > 1:
        a[1] = lp[0, ext[1]]
    em = lp[:, ext]
    for t in range(1, T):
        b = a.copy()
        b[1:] = np.logaddexp(b[1:], a[:-1])
        two = np.full(S, -np.inf)
        two[2:] = np.where(skip[2:], a[:-2], -np.inf)
        b = np.logaddexp(b, two)
        a = b + em[t]
    end = np.logaddexp(a[-1], a[-2]) if S > 1 else a[-1]
    return float(end) if np.isfinite(end) else _NEG


def kws_span(lp: np.ndarray, label: Sequence[int]) -> Tuple[float, int, int]:
    """Keyword alignment with free text before and after (max over paths): (log score, first frame, last frame)."""
    T = lp.shape[0]
    if not len(label) or T == 0:
        return _NEG, 0, max(T - 1, 0)
    ext, skip = _ext(label)
    S = len(ext)
    em = lp[:, ext]
    v = np.full(S, -np.inf)
    st = np.full(S, -1, dtype=np.int64)
    best = (_NEG, 0, T - 1)
    for t in range(T):
        if t == 0:
            nv = np.full(S, -np.inf)
            nst = np.full(S, -1, dtype=np.int64)
        else:
            nv, nst = v.copy(), st.copy()
            one = np.full(S, -np.inf)
            one[1:] = v[:-1]
            take = one > nv
            nv = np.where(take, one, nv)
            nst[1:] = np.where(take[1:], st[:-1], nst[1:])
            two = np.full(S, -np.inf)
            two[2:] = np.where(skip[2:], v[:-2], -np.inf)
            take = two > nv
            nv = np.where(take, two, nv)
            nst[2:] = np.where(take[2:], st[:-2], nst[2:])
        # a fresh start at this frame on the first letter (free text before it costs nothing)
        if nv[1] < 0.0:
            nv[1], nst[1] = 0.0, t
        nv = nv + em[t]
        v, st = nv, nst
        e = max(v[-1], v[-2]) if S > 1 else v[-1]
        if e > best[0]:
            k = S - 1 if (S == 1 or v[-1] >= v[-2]) else S - 2
            best = (float(e), int(st[k]), t)
    return best


def viterbi_frames(lp: np.ndarray, label: Sequence[int]) -> List[int]:
    """For each label symbol, the frame where the best (strict) alignment emits it first."""
    T = lp.shape[0]
    if not len(label) or T == 0:
        return []
    ext, skip = _ext(label)
    S = len(ext)
    em = lp[:, ext]
    v = np.full(S, -np.inf)
    v[0] = em[0, 0]
    if S > 1:
        v[1] = em[0, 1]
    back = np.zeros((T, S), dtype=np.int8)
    for t in range(1, T):
        cand = np.stack([v,
                         np.concatenate([[-np.inf], v[:-1]]),
                         np.concatenate([[-np.inf, -np.inf], np.where(skip[2:], v[:-2], -np.inf)])])
        k = cand.argmax(axis=0)
        back[t] = k
        v = cand[k, np.arange(S)] + em[t]
    s = S - 1 if (S == 1 or v[-1] >= v[-2]) else S - 2
    path = [0] * T
    for t in range(T - 1, -1, -1):
        path[t] = s
        s -= int(back[t, s]) if t > 0 else 0
    first: Dict[int, int] = {}
    for t, s in enumerate(path):
        if s % 2 == 1 and s not in first:
            first[s] = t
    return [first.get(2 * i + 1, -1) for i in range(len(label))]


# ---------------------------------------------------------------------------------------------------------- images
def _rgb(image: Any) -> np.ndarray:
    a = np.asarray(image)
    if a.ndim == 2:
        a = np.repeat(a[:, :, None], 3, axis=2)
    elif a.ndim == 3 and a.shape[2] == 4:
        a = a[:, :, :3]
    elif a.ndim == 3 and a.shape[2] == 1:
        a = np.repeat(a, 3, axis=2)
    if a.dtype != np.uint8:
        a = a.astype(np.uint8)
    return np.ascontiguousarray(a)


def _gray(rgb: np.ndarray) -> np.ndarray:
    import cv2
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)


def _odd(n: float) -> int:
    k = max(3, int(n))
    return k if k % 2 else k + 1


def _channel(rgb: np.ndarray, ch: str) -> np.ndarray:
    if ch == "gray":
        return _gray(rgb)
    if ch == "dark":                                   # coloured artwork is light in every channel's maximum
        return np.ascontiguousarray(rgb.max(axis=2))
    return np.ascontiguousarray(rgb[:, :, {"red": 0, "green": 1, "blue": 2}[ch]])


def _processed(g: np.ndarray, dark: np.ndarray, text_h: float) -> List[Tuple[str, np.ndarray]]:
    """Processing variants of one grey line: contrast-stretched, Otsu and adaptive binarised, sharpened, thicker
    and thinner strokes, two small skews."""
    import cv2
    lo, hi = np.percentile(g, 2), np.percentile(g, 98)
    st = np.clip((g.astype(np.float32) - lo) * (255.0 / (hi - lo)), 0, 255).astype(np.uint8) if hi - lo >= 8 else g
    out = [("stretch", st)]
    _, otsu = cv2.threshold(cv2.GaussianBlur(dark, (3, 3), 0), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    out.append(("otsu", otsu))
    blk = _odd(max(15, 1.2 * text_h))
    out.append(("adaptive", cv2.adaptiveThreshold(g, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY,
                                                  blk, 12)))
    soft = cv2.GaussianBlur(st, (0, 0), max(0.8, text_h / 40.0))
    out.append(("sharpen", cv2.addWeighted(st, 1.7, soft, -0.7, 0)))
    k = max(2, int(round(text_h / 28.0)))
    ker = np.ones((k, k), np.uint8)
    out.append(("bold", cv2.erode(st, ker)))           # dark strokes grow (a thin font)
    out.append(("thin", cv2.dilate(st, ker)))          # dark strokes shrink (a bold font)
    h, w = st.shape
    for ang in (-1.5, 1.5):
        M = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), ang, 1.0)
        out.append((f"skew{ang:+.1f}", cv2.warpAffine(st, M, (w, h), flags=cv2.INTER_LINEAR,
                                                      borderMode=cv2.BORDER_REPLICATE)))
    return out


def clean_lines(rgb: np.ndarray, text_h: float) -> np.ndarray:
    """Rules printed through or under the text removed (painted with the paper's colour): an underline under a
    number turns a 3 into a 2 for the recogniser (owner folder 3, a TIN certificate). A horizontal ink run longer
    than 2.5 text heights, or a vertical one taller than 1.3 text heights, is never part of a letter."""
    import cv2
    if rgb.size == 0 or text_h < 4:
        return rgb
    g = rgb.max(axis=2) if rgb.ndim == 3 else rgb
    _, ink = cv2.threshold(cv2.GaussianBlur(g, (3, 3), 0), 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    H, W = ink.shape
    lines = np.zeros_like(ink)
    lh = int(2.5 * text_h)
    if W > lh >= 8:
        lines |= cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (lh, 1)))
    lv = int(1.3 * text_h)
    if H > lv >= 8:
        lines |= cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, lv)))
    if not lines.any():
        return rgb
    lines = cv2.dilate(lines, np.ones((3, 3), np.uint8))
    paper = rgb[ink == 0]
    colour = np.median(paper, axis=0).astype(np.uint8) if paper.size else np.array([255, 255, 255], np.uint8)
    out = rgb.copy()
    out[lines > 0] = colour
    return out


def _views(img: np.ndarray, box: Tuple[float, float, float, float], full: bool = True
           ) -> List[Tuple[str, np.ndarray, np.ndarray]]:
    """(name, grey view, the enlarged RGB crop it came from) of one text line: every vertical margin of PADS in
    every channel of CHANNELS (the recogniser reads a line differently with more or less margin and with coloured
    artwork in or out), plus the processing variants at the second margin. `full` False: a smaller set."""
    x0, y0, x1, y1 = box
    h = max(y1 - y0, 1.0)
    out: List[Tuple[str, np.ndarray, np.ndarray]] = []
    pads = PADS if full else PADS[1:3]
    chans = CHANNELS if full else CHANNELS[:3]
    for py in pads:
        c = _vcrop(img, y0, y1, x0, x1, py * h, PAD_X * h)
        if not c.size:
            continue
        big, f = _enlarge(c, h)
        big = clean_lines(big, h * f)
        for ch in chans:
            out.append((f"p{py:.2f}/{ch}", _channel(big, ch), big))
        if full and py == PADS[1]:
            for name, g in _processed(_channel(big, "gray"), _channel(big, "dark"), h * f):
                out.append((f"p{py:.2f}/{name}", g, big))
    return out


def _vcrop(rgb: np.ndarray, y0: float, y1: float, x0: float, x1: float, pad_y: float, pad_x: float) -> np.ndarray:
    H, W = rgb.shape[:2]
    ya, yb = int(max(0, y0 - pad_y)), int(min(H, y1 + pad_y))
    xa, xb = int(max(0, x0 - pad_x)), int(min(W, x1 + pad_x))
    if yb - ya < 4 or xb - xa < 4:
        return np.zeros((0, 0, 3), np.uint8)
    return rgb[ya:yb, xa:xb]


def _enlarge(rgb: np.ndarray, text_h: float) -> Tuple[np.ndarray, float]:
    import cv2
    if text_h >= SMALL_LINE or rgb.size == 0:
        return rgb, 1.0
    f = min(3.0, max(2.0, math.ceil(MODEL_H / max(text_h, 1.0))))
    if rgb.shape[1] * f > MAX_SIDE:
        f = max(1.0, MAX_SIDE / float(rgb.shape[1]))
    if f <= 1.0:
        return rgb, 1.0
    return cv2.resize(rgb, None, fx=f, fy=f, interpolation=cv2.INTER_LANCZOS4), f


# ---------------------------------------------------------------------------------------------------------- locate
_DET_MEMO: "Dict[Tuple[int, Tuple[int, ...], int], Tuple[Any, List[Tuple[float, float, float, float]]]]" = {}
_LINE_MEMO: Dict[Tuple[int, Tuple[int, ...], int, int, str], Tuple[Any, np.ndarray]] = {}
_MEMO_MAX = 4            # pages whose detector boxes are kept
_LINE_MEMO_MAX = 1500    # line read-outs kept (float32, a few pages' worth)


def _memo_put(memo: Dict, key: Any, val: Any, cap: int) -> None:
    memo[key] = val
    while len(memo) > cap:
        memo.pop(next(iter(memo)))


def _rot(rgb: np.ndarray, k: int) -> np.ndarray:
    return np.ascontiguousarray(np.rot90(rgb, k)) if k else rgb


def _boxes(rgb: np.ndarray, k: int) -> List[Tuple[float, float, float, float]]:
    """The detector's horizontal line boxes (x0, y0, x1, y1) of the page turned by k quarter turns (memoised)."""
    key = (id(rgb), tuple(rgb.shape), k)
    hit = _DET_MEMO.get(key)
    if hit is not None and hit[0] is rgb:
        return hit[1]
    img = _rot(rgb, k)
    reader = _rec().reader
    import cv2
    h_list, _free = reader.detect(cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
    boxes = [(float(b[0]), float(b[2]), float(b[1]), float(b[3])) for b in (h_list or [])
             if b[1] - b[0] > 4 and b[3] - b[2] > 4]
    _memo_put(_DET_MEMO, key, (rgb, boxes), _MEMO_MAX)
    return boxes


def _box_logp(rgb: np.ndarray, k: int, i: int, box: Tuple[float, float, float, float], kind: str) -> np.ndarray:
    key = (id(rgb), tuple(rgb.shape), k, i, kind)
    hit = _LINE_MEMO.get(key)
    if hit is not None and hit[0] is rgb:
        return hit[1]
    img = _rot(rgb, k)
    x0, y0, x1, y1 = box
    h = y1 - y0
    crop = _vcrop(img, y0, y1, x0, x1, 0.1 * h, 0.2 * h)
    crop, _ = _enlarge(crop, h)
    lp = _rec().logp(_gray(crop), kind).astype(np.float32) if crop.size else np.zeros((1, 1), np.float32)
    _memo_put(_LINE_MEMO, key, (rgb, lp), _LINE_MEMO_MAX)
    return lp


def _same_line(a: Tuple[float, ...], b: Tuple[float, ...]) -> bool:
    ov = min(a[3], b[3]) - max(a[1], b[1])
    return ov >= 0.5 * min(a[3] - a[1], b[3] - b[1])


def _join_line(boxes: List[Tuple[float, float, float, float]], i: int) -> Tuple[float, float, float, float]:
    """The best box and the boxes beside it on its line (a label before, a further word after)."""
    x0, y0, x1, y1 = boxes[i]
    h = y1 - y0
    row = sorted((b for b in boxes if _same_line(b, boxes[i])), key=lambda b: b[0])
    pos = row.index(boxes[i]) if boxes[i] in row else 0
    left, right = x0, x1
    lo, hi = y0, y1
    n = 0
    for b in reversed(row[:pos]):
        if n >= NEIGHBOURS or left - b[2] > SAME_LINE_GAP * h:
            break
        left, lo, hi, n = min(left, b[0]), min(lo, b[1]), max(hi, b[3]), n + 1
    n = 0
    for b in row[pos + 1:]:
        if n >= NEIGHBOURS or b[0] - right > SAME_LINE_GAP * h:
            break
        right, lo, hi, n = max(right, b[2]), min(lo, b[1]), max(hi, b[3]), n + 1
    return left, lo, right, hi


def _below(boxes: List[Tuple[float, float, float, float]], box: Tuple[float, float, float, float]
           ) -> Optional[Tuple[float, float, float, float]]:
    """The box right under `box` (the next line of a wrapped table cell), starting inside its width."""
    x0, y0, x1, y1 = box
    h = y1 - y0
    best = None
    for b in boxes:
        if b[1] <= y1 - 0.3 * h or b[1] - y1 > 1.2 * h:
            continue
        if not (x0 - 0.8 * h <= b[0] <= x0 + 0.6 * (x1 - x0)):
            continue
        if best is None or b[1] < best[1]:
            best = b
    return best


def _stitch(img: np.ndarray, line: Tuple[float, float, float, float], below: Tuple[float, float, float, float]
            ) -> Optional[np.ndarray]:
    """A wrapped table cell's two lines side by side (the second scaled to the first's height), with a margin of a
    quarter line above and below: the recogniser then reads the cell as one line."""
    import cv2
    x0, y0, x1, y1 = line
    h = max(y1 - y0, 1.0)
    c1 = _vcrop(img, y0, y1, x0, x1, 0.25 * h, PAD_X * h)
    bx0, by0, bx1, by1 = below
    c2 = _vcrop(img, by0, by1, bx0, bx1, 0.25 * (by1 - by0), PAD_X * h)
    if not c1.size or not c2.size:
        return None
    c2 = cv2.resize(c2, (max(4, int(c2.shape[1] * c1.shape[0] / float(c2.shape[0]))), c1.shape[0]),
                    interpolation=cv2.INTER_AREA)
    gap = np.empty((c1.shape[0], max(4, int(0.6 * h)), 3), dtype=np.uint8)
    gap[:] = np.median(c1.reshape(-1, 3), axis=0).astype(np.uint8)
    return np.ascontiguousarray(np.concatenate([c1, gap, c2], axis=1))


def _extend_ink(img: np.ndarray, box: Tuple[float, float, float, float], right_only: bool = False
                ) -> Tuple[float, float, float, float]:
    """The found line widened over ink the detector left out (rev 2.5, integrator: the detector misses a short word
    after a name, e.g. an italic or underlined surname; the look then saw the name one word short and "confirmed"
    an OCR read that had dropped that word): ink in the core of the line's band beside it, word gaps under
    EXTEND_GAP line heights, never across a vertical rule (a table border). Horizontal rules are no ink here."""
    import cv2
    x0, y0, x1, y1 = (float(v) for v in box)
    h = max(y1 - y0, 1.0)
    H, W = img.shape[:2]
    ya, yb = int(max(0, y0 + 0.2 * h)), int(min(H, y1 - 0.2 * h))       # the core: neighbours' descenders out
    reach = int(EXTEND_REACH * h)
    xa, xb = int(max(0, x0 - reach)), int(min(W, x1 + reach))
    if yb - ya < 3 or xb - xa < 8:
        return x0, y0, x1, y1
    ra, rb = int(max(0, y0 - 0.5 * h)), int(min(H, y1 + 0.5 * h))       # tall enough to see a border's run
    tall = img[ra:rb, xa:xb]
    g = tall.max(axis=2) if tall.ndim == 3 else tall
    if int(g.max()) - int(g.min()) < 30:
        return x0, y0, x1, y1
    _, ink = cv2.threshold(cv2.GaussianBlur(g, (3, 3), 0), 0, 1, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    lv = int(1.6 * h)
    vrules = np.zeros(ink.shape[1], dtype=bool)
    if ink.shape[0] > lv >= 8:
        vr = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, lv)))
        vrules = vr.any(axis=0)
    core = ink[ya - ra:yb - ra]
    lh = int(2.5 * h)
    if core.shape[1] > lh >= 8:
        core = core & (1 - cv2.morphologyEx(core, cv2.MORPH_OPEN,
                                            cv2.getStructuringElement(cv2.MORPH_RECT, (lh, 1))))
    cols = core.sum(axis=0) >= 2
    gap_max = EXTEND_GAP * h

    def walk(start: int, step: int) -> int:
        last, gap, x = start, 0, start + step
        while 0 <= x < len(cols):
            if vrules[x]:
                break
            if cols[x]:
                last, gap = x, 0
            else:
                gap += 1
                if gap > gap_max:
                    break
            x += step
        return last
    r = walk(int(min(len(cols) - 1, max(0, x1 - 1 - xa))), 1)
    nx1 = max(x1, float(xa + r + 1))
    nx0 = x0
    if not right_only:
        lft = walk(int(min(len(cols) - 1, max(0, x0 - xa))), -1)
        nx0 = min(x0, float(xa + lft))
    return nx0, y0, nx1, y1


def _locate_all(rgb: np.ndarray, labels: List[List[int]], kind: str) -> List[Dict[str, Any]]:
    """The lines of the page that match a candidate: the best one and (rev 2.5, integrator) every other line that
    matches about as well (OCCUR_LEAD, OCCUR_MIN; at most OCCUR_MAX): a page that prints the name twice (a table
    row and a footer; a card front and its machine-readable line) is looked at in each place, because a page-wide
    search cannot know which place the OCR read came from. Each hit: {"k", "box" (the joined line, widened over
    ink the detector missed), "cell" (the matched box, widened to the right inside its cell), "below" (the box
    under the cell: a wrapped cell's second line), "score"}."""
    best: Optional[Dict[str, Any]] = None
    scored: List[Tuple[float, int, int]] = []
    for k in (0, 1, 3, 2):
        boxes = _boxes(rgb, k)
        here: List[Tuple[float, int, int]] = []
        for i, b in enumerate(boxes):
            lp = _box_logp(rgb, k, i, b, kind)
            if lp.shape[1] < 2:
                continue
            top = _NEG
            for lab in labels:
                sc, _, _ = kws_span(lp, lab)
                top = max(top, sc / max(len(lab), 1))
            here.append((top, k, i))
            if best is None or top > best["score"]:
                best = {"k": k, "i": i, "score": top}
        scored.extend(here)
        if best is not None and best["score"] >= LOCATE_MIN:
            break
    if best is None or best["score"] < LOCATE_MIN:
        return []
    k = best["k"]
    boxes = _boxes(rgb, k)
    img = _rot(rgb, k)
    floor = max(OCCUR_MIN, best["score"] - OCCUR_LEAD)
    order = [best["i"]] + [i for s, kk, i in sorted(scored, key=lambda t: -t[0])
                           if kk == k and i != best["i"] and s >= floor]
    hits: List[Dict[str, Any]] = []
    for i in order:
        line = _join_line(boxes, i)
        if any(_same_line(line, h["box"]) and min(line[2], h["box"][2]) > max(line[0], h["box"][0]) for h in hits):
            continue                                       # the same line again (another box of it)
        try:
            wide = _extend_ink(img, line)
            cell = _extend_ink(img, boxes[i], right_only=True)
        except Exception:
            wide, cell = line, boxes[i]
        sc = next(s for s, kk, j in scored if kk == k and j == i)
        hits.append({"k": k, "box": wide, "cell": cell, "below": _below(boxes, boxes[i]), "score": sc})
        if len(hits) >= OCCUR_MAX:
            break
    return hits


def _locate(rgb: np.ndarray, labels: List[List[int]], kind: str) -> Optional[Dict[str, Any]]:
    """The line of the page that best matches any candidate: {"k", "box", "cell", "below", "score"} or None."""
    hits = _locate_all(rgb, labels, kind)
    return hits[0] if hits else None


# ---------------------------------------------------------------------------------------------------------- dots
def _components(binary_ink: np.ndarray) -> List[Tuple[int, int, int, int, int]]:
    import cv2
    n, _lab, stats, _ = cv2.connectedComponentsWithStats(binary_ink.astype(np.uint8), connectivity=8)
    return [tuple(int(v) for v in stats[i]) for i in range(1, n)]   # x, y, w, h, area


def dot_at(rgb_crop: np.ndarray, x_after: float, x_before: float) -> Optional[bool]:
    """Is a printed dot standing between the letter ending near x_after and the letter starting near x_before?
    True: a small solid blob near the baseline, apart from both letters; False: the gap is clean (no ink but
    specks); None: cannot tell (the letters touch, the gap holds other marks, the line cannot be measured)."""
    import cv2
    if rgb_crop.size == 0 or x_before <= x_after:
        return None
    g = rgb_crop.max(axis=2) if rgb_crop.ndim == 3 else rgb_crop
    _, ink = cv2.threshold(cv2.GaussianBlur(g, (3, 3), 0), 0, 1, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    comps = _components(ink)
    if not comps:
        return None
    H = float(rgb_crop.shape[0])
    tall = [c for c in comps if c[3] >= 0.25 * H and c[3] <= 0.95 * H]
    if len(tall) < 2:
        return None
    hs = sorted(c[3] for c in tall)
    letter_h = float(np.median(hs))
    bottoms = sorted(c[1] + c[3] for c in tall)
    baseline = float(np.median(bottoms))
    # the letters on either side of the gap
    left = [c for c in tall if c[0] <= x_after <= c[0] + c[2] or (c[0] + c[2] <= x_after + 0.3 * letter_h
                                                                     and c[0] + c[2] >= x_after - letter_h)]
    right = [c for c in tall if c[0] <= x_before <= c[0] + c[2] or (c[0] >= x_before - 0.3 * letter_h
                                                                       and c[0] <= x_before + letter_h)]
    if not left or not right:
        return None
    gl = max(c[0] + c[2] for c in left)
    gr = min(c[0] for c in right)
    if any(c in right for c in left) or gr - gl < 0.05 * letter_h:
        return None                                   # the letters touch: a dot cannot be told apart
    lo_s, hi_s = DOT_SIZE[0] * letter_h, DOT_SIZE[1] * letter_h
    speck = max(2.0, 0.05 * letter_h)
    dots, other = 0, 0
    # rev 2.5: the window reaches back under the left letter's box (up to 0.6 letter heights, never before the
    # letter's own place): a script t's crossbar or an N's swash runs on over the dot after it, and a window that
    # started at the box's right edge saw clean paper there ("no dot") although a dot was printed
    lo_x = min(gl - 0.1 * letter_h, max(float(x_after), gl - 0.6 * letter_h))
    for (x, y, w, h, area) in comps:
        cx = x + w / 2.0
        if not (lo_x <= cx <= gr + 0.1 * letter_h):
            continue
        if (x, y, w, h, area) in left or (x, y, w, h, area) in right:
            continue
        if w <= speck and h <= speck:
            continue
        bottom = y + h
        solid = area / float(max(w * h, 1))
        if (lo_s <= w <= hi_s and lo_s <= h <= hi_s and 0.45 <= w / float(max(h, 1)) <= 2.2 and solid >= DOT_FILL
                and abs(bottom - baseline) <= 0.22 * letter_h):
            dots += 1
        else:
            other += 1
    if dots >= 1 and other == 0:
        return True
    if dots == 0 and other == 0:
        # rev 2.5: "no dot" only when the gap is clean paper, not merely below the ink threshold: a blurred or
        # faint dot falls under Otsu's cut but still darkens the paper near the baseline (then: cannot tell)
        xa, xb = int(max(0, lo_x)), int(min(g.shape[1], gr + 0.1 * letter_h))
        ya, yb = int(max(0, baseline - 0.35 * letter_h)), int(min(g.shape[0], baseline + 0.1 * letter_h))
        if xb - xa >= 3 and yb - ya >= 3:
            ink_level = float(np.median(g[ink > 0])) if (ink > 0).any() else 0.0
            paper = float(np.median(g[ink == 0])) if (ink == 0).any() else 255.0
            _n, lab, stats, _c = cv2.connectedComponentsWithStats(ink.astype(np.uint8), connectivity=8)
            ids = [i for i in range(1, _n) if tuple(int(v) for v in stats[i]) in tall]
            letters = cv2.dilate(np.isin(lab, ids).astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
            near = cv2.blur(g, (3, 3))[ya:yb, xa:xb][~letters[ya:yb, xa:xb]]   # the paper beside the letters
            if paper - ink_level >= 20 and near.size and float(near.min()) < paper - 0.3 * (paper - ink_level):
                return None
        return False
    return None


def _dot_positions(cands: List[str]) -> List[int]:
    """Letter counts where some candidate has a dot and another has none."""
    pats = []
    for c in cands:
        n, p = 0, set()
        for ch in norm(c, "dots"):
            if ch == ".":
                p.add(n)
            else:
                n += 1
        pats.append(p)
    every = set().union(*pats) if pats else set()
    return sorted(q for q in every if not all(q in p for p in pats))


def _has_dot(c: str, pos: int) -> bool:
    n = 0
    s = norm(c, "dots")
    for i, ch in enumerate(s):
        if ch == ".":
            continue
        n += 1
        if n == pos:
            return i + 1 < len(s) and s[i + 1] == "."
    return False


# ---------------------------------------------------------------------------------------------------------- look
def _region(lp: np.ndarray, labels: List[List[int]]) -> Tuple[int, int]:
    """Frames scored: the union of the candidates' keyword alignments (those that read like text), padded up to
    the nearest letter of the context."""
    T = lp.shape[0]
    spans = []
    best = None
    for lab in labels:
        sc, a, b = kws_span(lp, lab)
        per = sc / max(len(lab), 1)
        if best is None or per > best[0]:
            best = (per, a, b)
        if per >= REGION_MIN:
            spans.append((a, b))
    if not spans and best is not None:
        spans = [(best[1], best[2])]
    if not spans:
        return 0, T - 1
    a = min(s[0] for s in spans)
    b = max(s[1] for s in spans)
    arg = lp.argmax(axis=1)
    lo = a
    for t in range(a - 1, max(-1, a - 1 - PAD_FRAMES), -1):
        if arg[t] != 0:
            break
        lo = t
    hi = b
    for t in range(b + 1, min(T, b + 1 + PAD_FRAMES)):
        if arg[t] != 0:
            break
        hi = t
    return lo, hi


def _greedy(lp: np.ndarray) -> List[int]:
    """The recogniser's own reading of the frames (best class per frame, repeats and blanks dropped)."""
    out, prev = [], -1
    for c in lp.argmax(axis=1):
        c = int(c)
        if c != prev and c != 0:
            out.append(c)
        prev = c
    return out


def line_frames(vname: str, g: np.ndarray, T: int) -> float:
    """The text line's height in recogniser frames for one view (`vname` "p<pad>/...": the crop holds the line plus
    that share of its height above and below)."""
    try:
        py = float(vname.split("/", 1)[0][1:])
    except (ValueError, IndexError):
        py = PADS[1]
    gh, gw = g.shape[:2]
    ratio = max(gw / float(max(gh, 1)), 1.0)
    width = int(math.ceil(ratio)) * MODEL_H
    return max(1.0, (MODEL_H / (1.0 + 2.0 * py)) * T / float(width))


def rec_tokens(P: np.ndarray, chars: Sequence[str]) -> List[Tuple[str, int, int]]:
    """The recogniser's own reading of a line letter by letter, from its unfolded softmax: [(character, first frame,
    last frame)] (best class per frame, repeats joined, blanks dropped; a space is kept)."""
    out: List[List[Any]] = []
    prev = 0
    for t, c in enumerate(P.argmax(axis=1)):
        c = int(c)
        if c == 0 or c > len(chars):
            prev = 0
            continue
        if c == prev and out:
            out[-1][2] = t
        else:
            out.append([chars[c - 1], t, t])
        prev = c
    return [(str(a), int(b), int(e)) for a, b, e in out]


def _splits(letters: str, words: frozenset) -> bool:
    """`letters` is two or more of `words` run together, one of them of three letters or more (a label read without
    its spaces: "SONOF", "NAMEOF"; two-letter pieces alone never make a label)."""
    n = len(letters)
    ok = [0] * (n + 1)          # 0 unreachable, 1 reached with two-letter pieces only, 2 with a longer piece too
    ok[0] = 1
    for i in range(2, n + 1):
        for j in range(max(0, i - 12), i - 1):
            if ok[j] and letters[j:i] in words:
                ok[i] = max(ok[i], 2 if (ok[j] == 2 or i - j >= 3) else 1)
    return ok[n] == 2


def is_label(letters: str, side: str) -> bool:
    """A word read beside a value that ends it (a label, a connecting word; before the value, a dropped title)."""
    pool = LABEL_WORDS | LEFT_TITLES if side == "before" else LABEL_WORDS
    return letters in pool or _splits(letters, pool)


def beside_words(toks: Sequence[Tuple[str, int, int]], a: int, b: int, lh: float, kind: str = "name"
                 ) -> List[Tuple[str, str]]:
    """Further words of a value the recogniser reads beside its region [a, b] (rev 2.6, checker): walking out from
    the region on each side, a word whose nearest letter lies within RUN_GAP line heights of the value belongs to it
    (an added MST before a name, a BEGUM after it), unless it is a label (a colon; a label or connecting word; before
    the value a dropped title), or for names a number; a wider gap ends the value. Punctuation alone is passed over.
    Returns [(side, the word's letters)] (at most one per side)."""
    found: List[Tuple[str, str]] = []
    for side in ("before", "after"):
        if side == "after":
            seq = [t for t in toks if t[1] > b]
        else:
            seq = [t for t in reversed(toks) if t[2] < a]
            head = _ascii_upper("".join(t[0] for t in toks if t[2] < a and not t[0].isspace()))
            if kind != "digits" and "<" in head and MRZ_HEAD.match(head):
                continue                                 # an MRZ line's document code and issuing state
        words: List[List[Tuple[str, int, int]]] = []
        cur: List[Tuple[str, int, int]] = []
        for t in seq:
            if t[0].isspace():
                if cur:
                    words.append(cur)
                cur = []
                continue
            if cur:
                gap = (t[1] - cur[-1][2]) if side == "after" else (cur[-1][1] - t[2])
                if gap > WORD_BREAK * lh:
                    words.append(cur)
                    cur = []
            cur.append(t)
        if cur:
            words.append(cur)
        last = b if side == "after" else a
        for w in words:
            near = w[0]
            dist = (near[1] - last) if side == "after" else (last - near[2])
            if dist > RUN_GAP * lh:
                break                                    # another field
            text = "".join(t[0] for t in (w if side == "after" else reversed(w)))
            last = w[-1][2] if side == "after" else w[-1][1]
            if ":" in text:
                break                                    # a label (before: the value's own; after: the next field's)
            letters = "".join(ch for ch in _ascii_upper(text) if ch.isalnum())
            if not letters:
                continue                                 # punctuation alone
            if kind != "digits" and letters.isdigit():
                break                                    # a number: another field
            if kind != "digits" and is_label(letters, side):
                break
            found.append((side, letters))
            break
    return found


def _ink_of(big: np.ndarray, xa: int, xb: int) -> Tuple[int, np.ndarray, float]:
    """(ink pixels, their mean colour, their mean darkness against the paper) between columns xa and xb of a line
    crop: a pixel is ink when it is 40 levels darker than the crop's paper (its 90th luminance percentile)."""
    xa, xb = max(0, int(xa)), min(big.shape[1], int(xb))
    if xb - xa < 2:
        return 0, np.zeros(3), 0.0
    lum = big.astype(np.float32).mean(axis=2)
    paper = float(np.percentile(lum, 90))
    part = big[:, xa:xb].astype(np.float32)
    dark = paper - lum[:, xa:xb]
    m = dark > 40.0
    n = int(m.sum())
    if n == 0:
        return 0, np.zeros(3), 0.0
    return n, part[m].mean(axis=0), float(dark[m].mean())


def far_tail(img: np.ndarray, box: Tuple[float, float, float, float], views: List[Tuple[str, np.ndarray, np.ndarray]],
             labels: List[List[int]], kind: str, rec: "_Rec") -> Optional[Tuple[float, str]]:
    """Rev 2.6: a word ANOTHER candidate adds AFTER the reference that lies more than RUN_GAP line heights from the
    reference's last letter AND is printed in another ink (a notary stamp's text beside a name, read by the page OCR
    as part of the value: owner EAP folder) is no part of the name's print: (x where the line is cut, why), else None.
    Both signals are needed: a far word in the name's own ink, or a near word in any ink, stays in the look."""
    if kind not in ("name", "dots") or len(labels) < 2 or not labels[0]:
        return None
    rep = next((v for v in views if v[0] == f"p{PADS[1]:.2f}/gray"), None)
    if rep is None:
        return None
    vname, g, big = rep
    lp = rec.logp(g, kind)
    T = lp.shape[0]
    lf = line_frames(vname, g, T)
    sc, ra, rb = kws_span(lp, labels[0])
    if sc / max(len(labels[0]), 1) < REGION_MIN:
        return None
    x0, y0, x1, y1 = box
    h = max(y1 - y0, 1.0)
    crop = _vcrop(img, y0, y1, x0, x1, PADS[1] * h, PAD_X * h)
    if not crop.size:
        return None
    gh, gw = g.shape[:2]
    width = int(math.ceil(max(gw / float(max(gh, 1)), 1.0))) * MODEL_H
    px_per_frame = (width / float(T)) * (gh / float(MODEL_H))          # pixels of the view (and of `big`)
    f = big.shape[1] / float(crop.shape[1])
    xa_img = max(0.0, x0 - PAD_X * h)
    best: Optional[float] = None
    for lab in labels[1:]:
        if not lab or len(lab) <= len(labels[0]):
            continue
        sgs = segments(labels[0], lab)
        if len(sgs) != 1:
            continue
        a0, a1, b0, b1 = sgs[0]
        if a0 != a1 or a0 != len(labels[0]) or b1 != len(lab) or b0 == 0:
            continue                                              # only letters ADDED after the whole reference
        if kws_span(lp, lab)[0] / len(lab) < REGION_MIN:
            continue
        fr = viterbi_frames(lp, lab)
        if len(fr) != len(lab):
            continue
        # takeover fix: the WHOLE added word lies far, from its first letter on (a near word whose last letters run
        # past RUN_GAP was cut in its middle and read as the reference: a false "same")
        if fr[b0] <= max(rb, fr[b0 - 1]) + RUN_GAP * lf:
            continue
        t_cut = max(max(rb, fr[b0 - 1]) + 1.2 * lf, fr[b0] - 0.8 * lf)
        n_n, c_n, d_n = _ink_of(big, ra * px_per_frame, rb * px_per_frame + 0.5 * lf * px_per_frame)
        n_f, c_f, d_f = _ink_of(big, t_cut * px_per_frame, (fr[-1] + 1.0 * lf) * px_per_frame)
        if n_n < 20:
            continue
        other = n_f < 10 or float(np.linalg.norm(c_n - c_f)) > 40.0 or d_f < 0.6 * d_n
        if not other:
            continue
        x_cut = xa_img + (t_cut * px_per_frame) / f
        if x_cut <= x0 + h or x_cut >= x1:
            continue
        best = x_cut if best is None else min(best, x_cut)
    if best is None:
        return None
    return best, "a word far after the name in another ink (a stamp?) is left out"


def tighten(img: np.ndarray, box: Sequence[float]) -> Tuple[float, float, float, float]:
    """The text line inside a caller's box: a box may be looser than the line (another engine's box, a fixed field
    box, a box at another resolution); the views' margins and the enlarging are measured from the line's own height.
    The band of ink rows nearest the box's middle with the most ink, and the ink columns inside it; the box as given
    when no clear band is found."""
    import cv2
    x0, y0, x1, y1 = (float(v) for v in box)
    H, W = img.shape[:2]
    ya, yb, xa, xb = int(max(0, y0)), int(min(H, y1)), int(max(0, x0)), int(min(W, x1))
    if yb - ya < 8 or xb - xa < 8:
        return x0, y0, x1, y1
    c = img[ya:yb, xa:xb]
    g = c.max(axis=2) if c.ndim == 3 else c
    if int(g.max()) - int(g.min()) < 30:
        return x0, y0, x1, y1
    _, ink = cv2.threshold(cv2.GaussianBlur(g, (3, 3), 0), 0, 1, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    rows = ink.mean(axis=1)
    thr = max(0.01, 0.12 * float(rows.max()))
    bands, start, gap = [], None, 0
    for y, v in enumerate(rows):
        if v >= thr:
            if start is None:
                start = y
            gap, end = 0, y
        elif start is not None:
            gap += 1
            if gap > max(2, (yb - ya) // 40):
                bands.append((start, end))
                start = None
    if start is not None:
        bands.append((start, end))
    if not bands:
        return x0, y0, x1, y1
    mid = (yb - ya) / 2.0
    best = max(bands, key=lambda b: float(rows[b[0]:b[1] + 1].sum()) *
               (1.5 - abs((b[0] + b[1]) / 2.0 - mid) / float(yb - ya)))
    r0, r1 = best
    if r1 - r0 < 6:
        return x0, y0, x1, y1
    # the band's thin rows on either side belong to it while they run on without a gap: a descender (q, g, y, p) or
    # a capital's top holds little ink, and a crop that cuts it reads a q as a or c
    low = max(0.002, 0.015 * float(rows.max()))
    bh = r1 - r0 + 1
    while r1 + 1 < len(rows) and rows[r1 + 1] >= low and (r1 + 1) - best[1] <= 0.45 * bh:
        r1 += 1
    while r0 - 1 >= 0 and rows[r0 - 1] >= low and best[0] - (r0 - 1) <= 0.35 * bh:
        r0 -= 1
    cols = np.flatnonzero(ink[r0:r1 + 1].any(axis=0))
    if cols.size == 0:
        return x0, y0, x1, y1
    return float(xa + cols[0]), float(ya + r0), float(xa + cols[-1] + 1), float(ya + r1 + 1)


def _unclear(cands: Sequence[str], note: str, kind: str, n: int = 0, **detail: Any) -> Look:
    return Look("unclear", cands[0] if cands else "", {c: _NEG for c in cands}, 0.0, n, 0,
                None, note, dict(detail))


# ---------------------------------------------------------------------------------------------------------- differences
def segments(a: Sequence[int], b: Sequence[int]) -> List[Tuple[int, int, int, int]]:
    """The places where two symbol strings differ, from a minimal edit alignment: [(a0, a1, b0, b1)] half-open
    spans (a[a0:a1] stands where b has b[b0:b1]); neighbouring edits are one place."""
    n, m = len(a), len(b)
    D = np.zeros((n + 1, m + 1), dtype=np.int32)
    D[:, 0] = np.arange(n + 1)
    D[0, :] = np.arange(m + 1)
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            D[i, j] = min(D[i - 1, j] + 1, D[i, j - 1] + 1, D[i - 1, j - 1] + (a[i - 1] != b[j - 1]))
    ops: List[Tuple[str, int, int]] = []
    i, j = n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and a[i - 1] == b[j - 1] and D[i, j] == D[i - 1, j - 1]:
            ops.append(("=", i - 1, j - 1))
            i, j = i - 1, j - 1
        elif i > 0 and j > 0 and D[i, j] == D[i - 1, j - 1] + 1:
            ops.append(("s", i - 1, j - 1))
            i, j = i - 1, j - 1
        elif i > 0 and D[i, j] == D[i - 1, j] + 1:
            ops.append(("d", i - 1, j))
            i -= 1
        else:
            ops.append(("i", i, j - 1))
            j -= 1
    ops.reverse()
    out: List[Tuple[int, int, int, int]] = []
    cur: Optional[List[int]] = None
    for op, p, q in ops:
        if op == "=":
            if cur is not None:
                out.append((cur[0], cur[1], cur[2], cur[3]))
                cur = None
            continue
        a0, b0 = p, q
        a1 = p + 1 if op in "sd" else p
        b1 = q + 1 if op in "si" else q
        if cur is None:
            cur = [a0, a1, b0, b1]
        else:
            cur[1], cur[3] = max(cur[1], a1), max(cur[3], b1)
    if cur is not None:
        out.append((cur[0], cur[1], cur[2], cur[3]))
    return out


def _hybrid(a: Sequence[int], b: Sequence[int], seg: Tuple[int, int, int, int]) -> List[int]:
    """`a` with ONE of its differences from `b` (seg) taken from `b`."""
    a0, a1, b0, b1 = seg
    return list(a[:a0]) + list(b[b0:b1]) + list(a[a1:])


# ---------------------------------------------------------------------------------------------------------- a wrapped line
def line_under(img: np.ndarray, box: Sequence[float]) -> Optional[Tuple[float, float, float, float]]:
    """The text line right under a line box (a table cell's second line), from the ink rows below it: its top within
    1.2 line heights of the box's bottom, its height 0.5-1.6 line heights, starting inside the box's width. None when
    no such line is there."""
    import cv2
    x0, y0, x1, y1 = (float(v) for v in box)
    h = max(y1 - y0, 1.0)
    H, W = img.shape[:2]
    xa, xb = int(max(0, x0 - 0.8 * h)), int(min(W, x1 + 0.5 * (x1 - x0)))
    ya, yb = int(min(H, y1 + 0.1 * h)), int(min(H, y1 + 2.9 * h))
    if yb - ya < 6 or xb - xa < 8:
        return None
    c = img[ya:yb, xa:xb]
    g = c.max(axis=2) if c.ndim == 3 else c
    if int(g.max()) - int(g.min()) < 30:
        return None
    _, ink = cv2.threshold(cv2.GaussianBlur(g, (3, 3), 0), 0, 1, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    # a printed rule (a table border, an underline) is no text line; a long rule between the two lines is a table
    # row's border: the line under it is the next row, not the cell's second line
    rules = np.zeros_like(ink)
    if ink.shape[1] >= 8:
        rules = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT,
                                                                                  (max(8, int(2.5 * h)), 1)))
        ink = ink & (1 - rules)
    rows = ink.mean(axis=1)
    if float(rows.max()) <= 0.0:
        return None
    on = rows >= max(0.01, 0.08 * float(rows.max()))
    ys = np.flatnonzero(on)
    if ys.size == 0:
        return None
    top = int(ys[0])
    bot = top
    gap = 0
    for y in range(top, len(on)):
        if on[y]:
            bot, gap = y, 0
        else:
            gap += 1
            if gap > max(2, int(0.15 * h)):
                break
    lh = bot - top + 1
    if top > 1.2 * h or not (0.5 * h <= lh <= 1.6 * h):
        return None
    wide = rules[:top].sum(axis=1) >= max(8, int(0.6 * (x1 - x0)))
    if bool(wide.any()) and int(np.flatnonzero(wide)[-1]) > int(0.4 * h):
        return None                              # a border between: the next row (an underline right under is no border)
    cols = np.flatnonzero(ink[top:bot + 1].any(axis=0))
    if cols.size == 0:
        return None
    bx0, bx1 = float(xa + cols[0]), float(xa + cols[-1] + 1)
    if not (x0 - 0.8 * h <= bx0 <= x0 + 0.6 * (x1 - x0)):
        return None
    return bx0, float(ya + top), bx1, float(ya + bot + 1)


# ---------------------------------------------------------------------------------------------------------- the look
def _score_views(views: List[Tuple[str, np.ndarray, np.ndarray]], lps: Dict[str, Optional[np.ndarray]],
                 labels: List[List[int]]) -> Tuple[List[str], List[List[float]], List[np.ndarray]]:
    """Per view: the candidates' CTC log-likelihoods (nats) over the candidates' own region, and that region's
    frames (kept to score further spellings)."""
    names, table, segs = [], [], []
    for vname, _g, _big in views:
        lp = lps.get(vname)
        if lp is None:
            continue
        a, b = _region(lp, [l for l in labels if l])
        seg = lp[a:b + 1]
        names.append(vname)
        table.append([max(ctc_logl(seg, l), _NEG) if l else _NEG for l in labels])
        segs.append(seg)
    return names, table, segs


def differences_hold(win: int, labels: List[List[int]], segs: List[np.ndarray], raw: np.ndarray,
                     others: Sequence[int], dot: Optional[int] = None) -> Tuple[float, str]:
    """Each place where the winner differs from another candidate must itself favour the winner: the winner against
    the winner with that ONE difference taken from the other candidate (nats, mean over the views). A spelling that
    wins on the sum while one of its differences reads the other way is not what the print shows (the print is a
    third spelling, or a misread the recogniser shares). Returns (the smallest lead, the kind of that place)."""
    worst, what = 99.0, ""
    floor = -60.0 * max(len(labels[0]), 1)
    for j in others:
        if j == win or not labels[j]:
            continue
        sgs = segments(labels[win], labels[j])
        for sg in sgs:
            a0, a1, b0, b1 = sg
            if dot is not None and set(labels[win][a0:a1]) | set(labels[j][b0:b1]) <= {dot}:
                continue                         # a dot alone: the dot's own shape judges it (_dot_check)
            hyb = _hybrid(labels[win], labels[j], sg)
            if len(sgs) == 1:
                lead = float(np.mean(raw[:, win] - raw[:, j]))
            else:
                lead = float(np.mean([raw[v, win] - max(ctc_logl(segs[v], hyb), floor) if hyb else 0.0
                                      for v in range(len(segs))]))
            if lead < worst:
                a0, a1, b0, b1 = sg
                what = "letters" if (a1 - a0) and (b1 - b0) else "added" if (a1 - a0) else "dropped"
                worst = lead
    return worst, what


def added_reads(win: int, labels: List[List[int]], segs: List[np.ndarray], others: Sequence[int]) -> float:
    """Letters (two or more) the winner has where another candidate has none (an added word, a tail) must read as
    those letters by themselves: their keyword score per letter (mean over the views; artwork, a stamp or a Bangla
    line read as Latin scores badly). 0.0 when the winner adds nothing."""
    worst = 0.0
    for j in others:
        if j == win or not labels[j]:
            continue
        for a0, a1, b0, b1 in segments(labels[win], labels[j]):
            ins = labels[win][a0:a1]
            if len(ins) < 2 or (b1 - b0) >= (a1 - a0):
                continue
            per = float(np.mean([kws_span(s, ins)[0] / len(ins) for s in segs]))
            worst = min(worst, per)
    return worst


def look(image: Any, box: Optional[Sequence[float]], candidates: Sequence[str], kind: str = "name") -> Look:
    """The closer look (see the module text). image: numpy (H, W[, 3]) of the page at a high resolution
    (page_image); box: (x0, y0, x1, y1) in its pixels around the text, or None to search the page; candidates:
    spellings, candidates[0] = the reference; kind: "name" | "digits" | "dots"."""
    cands = [str(c) for c in candidates if c is not None]
    if kind not in ("name", "digits", "dots"):
        raise ValueError("kind is name, digits or dots")
    try:
        rec = _rec()
    except Exception as exc:                                  # no recogniser: never a guess
        return _unclear(cands, f"no recogniser ({type(exc).__name__})", kind)
    _m, index = rec.fold(kind)
    labels = [_encode(c, kind, index) for c in cands]
    if not cands or not labels[0]:
        return _unclear(cands, "no candidate to look for", kind)
    keep = [0] + [i for i in range(1, len(cands)) if labels[i] and labels[i] not in labels[:i]]
    cands, labels = [cands[i] for i in keep], [labels[i] for i in keep]
    single = len(cands) == 1            # verify ONE value against the recogniser's own reading of its place
    rgb = _rgb(image)
    if box is not None:
        return _look_at(rgb, {"k": 0, "box": tuple(float(v) for v in box), "cell": None, "below": None},
                        cands, labels, kind, rec, index, single)
    try:
        hits = _locate_all(rgb, [l for l in labels if l], kind)
    except Exception as exc:
        return _unclear(cands, f"search failed ({type(exc).__name__})", kind)
    if not hits:
        return _unclear(cands, "the text was not found on the page", kind)
    looks = [_look_at(rgb, h, list(cands), [list(l) for l in labels], kind, rec, index, single) for h in hits]
    return looks[0] if len(looks) == 1 else _combine(looks, kind)


def _combine(looks: List[Look], kind: str) -> Look:
    """A page that prints the candidates in more than one place (rev 2.5): every place must give the same decision
    (and, for dots, the same dot); else "unclear". A page-wide search cannot tell which place the OCR read came
    from, and a page that prints the reference in one place and the other spelling in another is not settled by
    it (a real difference in a table row must never pass because the footer prints the reference)."""
    first = looks[0]
    decisions = [lk.decision for lk in looks]
    detail = dict(first.detail, places=len(looks), place_decisions=decisions,
                  place_boxes=[lk.detail.get("box") for lk in looks], place_best=[lk.best for lk in looks],
                  place_dots=[lk.dot for lk in looks])
    dots = {lk.dot for lk in looks}
    if len(set(decisions)) == 1 and decisions[0] != "unclear" and (kind != "dots" or len(dots) == 1):
        return Look(first.decision, first.best, first.scores, first.margin, first.variants, first.agree, first.dot,
                    f"{first.note} (the same in all {len(looks)} places the page prints it)", detail)
    if all(d == "unclear" for d in decisions):
        return Look("unclear", first.best, first.scores, first.margin, first.variants, first.agree, None,
                    first.note, detail)
    if "same" in decisions and "differs" in decisions:
        why = "unclear: the page prints the name in more than one place and they do not agree"
    else:
        why = "unclear: a place where the page prints the name could not be settled"
    return Look("unclear", first.best, first.scores, first.margin, first.variants, first.agree, None, why, detail)


def _look_at(rgb: np.ndarray, hit: Dict[str, Any], cands: List[str], labels: List[List[int]], kind: str,
             rec: "_Rec", index: Dict[str, int], single: bool) -> Look:
    """The look at one place: hit {"k" quarter turns, "box" the line, "cell" the matched box or None, "below" the
    line under the cell or None}."""
    k, box, below, cell = hit["k"], hit["box"], hit.get("below"), hit.get("cell")
    img = _rot(rgb, k)
    x0, y0, x1, y1 = tighten(img, box)
    h = max(y1 - y0, 1.0)
    if below is None:
        try:
            below = line_under(img, (x0, y0, x1, y1) if cell is None else tuple(cell))
        except Exception:
            below = None
    views_line = _views(img, (x0, y0, x1, y1))
    if not views_line:
        return _unclear(cands, "the box is empty", kind)
    cut_why = ""
    if not single:
        try:
            cut = far_tail(img, (x0, y0, x1, y1), views_line, labels, kind, rec)
        except Exception:                                     # the cut is an aid, never a reason to fail the look
            cut = None
        if cut is not None:
            x1, cut_why = cut[0], cut[1]
            views_line = _views(img, (x0, y0, x1, y1))
            if not views_line:
                return _unclear(cands, "the box is empty", kind)
    views_pair: List[Tuple[str, np.ndarray, np.ndarray]] = []
    if below is not None:
        # a wrapped cell: the line up to the end of the matched cell, then the line under it (rev 2.5: the whole
        # table row before the second line kept the two halves of the name apart)
        sx1 = x1 if cell is None else max(x0 + h, min(x1, float(cell[2]) + 0.2 * h))
        st = _stitch(img, (x0, y0, sx1, y1), below)
        if st is not None:
            views_pair = _views(st, (0.0, 0.25 * h, float(st.shape[1]), 1.25 * h), full=False)

    probs_line: Dict[str, np.ndarray] = {}

    def read(views: List[Tuple[str, np.ndarray, np.ndarray]], keep: bool = False
             ) -> Dict[str, Optional[np.ndarray]]:
        out: Dict[str, Optional[np.ndarray]] = {}
        for vname, g, _big in views:
            try:
                if keep:
                    probs_line[vname], out[vname] = rec.both(g, kind)
                else:
                    out[vname] = rec.logp(g, kind)
            except Exception:                                 # one variant failing never breaks the look
                out[vname] = None
        return out
    lps_line = read(views_line, keep=single)
    base: Dict[str, Any] = {"rotation": k * 90, "box": [round(v, 1) for v in (x0, y0, x1, y1)], "version": VERSION}
    if cut_why:
        base["cut"] = cut_why
    if single:
        # rev 2.6 (checker): the value is more than the reference's own letters. A word printed right before or
        # after it (an added MST, MD, BEGUM) lies outside the reference's region and was never scored: the
        # recogniser's reading beside the region, in each view, must hold no further word of the value (labels,
        # connecting words, numbers and other fields end the value); read in more than DISSENT of the views, the
        # look is "unclear" (never "same")
        seen = 0
        sides: Dict[str, int] = {}
        n_read = 0
        for vname, g, _big in views_line:
            lp, P = lps_line.get(vname), probs_line.get(vname)
            if lp is None or P is None:
                continue
            n_read += 1
            a, b = _region(lp, labels)
            extra = beside_words(rec_tokens(P, rec.chars), a, b, line_frames(vname, g, lp.shape[0]), kind)
            if extra:
                seen += 1
                for side, _w in extra:
                    sides[side] = sides.get(side, 0) + 1
        if n_read and seen > math.floor(DISSENT * n_read):
            return Look("unclear", cands[0], {cands[0]: 0.0}, 0.0, n_read, n_read - seen, None,
                        "unclear: the line reads a further word beside the value (an added word?)",
                        dict(base, single=True, beside=seen, beside_sides=sides))
        # the alternatives are what the recogniser itself reads at the value's place, in the views
        readings: Dict[Tuple[int, ...], int] = {}
        for _vname, lp in lps_line.items():
            if lp is None:
                continue
            a, b = _region(lp, labels)
            r = tuple(_greedy(lp[a:b + 1]))
            readings[r] = readings.get(r, 0) + 1
        n = sum(readings.values())
        alts = [r for r, _n in sorted(readings.items(), key=lambda kv: -kv[1]) if r and list(r) != labels[0]]
        if not alts:
            return Look("same" if n else "unclear", cands[0], {cands[0]: 0.0}, 9.99, n, n, None,
                        "the recogniser reads the value itself in every view" if n else "no view could be read",
                        dict(base, single=True))
        inv = {v: ch for ch, v in index.items()}
        for r in alts[:3]:
            labels.append(list(r))
            cands.append("".join(inv.get(c, "?") for c in r))
    lps_pair = read(views_pair) if views_pair else {}
    n_ref = max(len(labels[0]), 1)
    names_l, tab_l, segs_l = _score_views(views_line, lps_line, labels)
    if not tab_l:
        return _unclear(cands, "no variant could be scored", kind)
    arr_l = np.maximum(np.array(tab_l) / n_ref, -60.0)
    use = ("line", names_l, np.array(tab_l), segs_l, views_line)
    wrap = ""
    if views_pair:
        names_p, tab_p, segs_p = _score_views(views_pair, lps_pair, labels)
        if tab_p:
            arr_p = np.maximum(np.array(tab_p) / n_ref, -60.0)
            ml, mp = arr_l.mean(axis=0), arr_p.mean(axis=0)
            wl, wp = int(ml.argmax()), int(mp.argmax())
            if ml[wl] < MIN_SCORE <= mp[wp]:
                use, wrap = ("two-lines", names_p, np.array(tab_p), segs_p, views_pair), "the line reads on below"
            elif wl != wp and mp[wp] >= MIN_SCORE and mp[wp] > ml[wp] + TWO_LINES_GAIN:
                # the line alone and the line with the one under it support different spellings: a table cell
                # that wraps, or the next row's text; the look cannot tell which
                wrap = "conflict"
            elif wl == wp and mp[wp] > ml[wl] + TWO_LINES_GAIN:
                use, wrap = ("two-lines", names_p, np.array(tab_p), segs_p, views_pair), "the line reads on below"
    cname, _vnames, raw, segs, views = use
    raw = np.maximum(raw, -60.0 * n_ref)
    arr = raw / n_ref
    mean = arr.mean(axis=0)
    order = np.argsort(-mean)
    bi = int(order[0])
    second = float(mean[order[1]]) if len(order) > 1 else _NEG
    margin = float(mean[bi] - second)
    winners = arr.argmax(axis=1)
    agree = int(sum(1 for w in winners if int(w) == bi))
    strong, dissent = 0, 0
    for row, w in zip(arr, winners):
        w = int(w)
        rest = np.delete(row, w)
        lead = row[w] - rest.max() if rest.size else 99.0
        if w == bi and lead >= MARGIN:
            strong += 1
        elif w != bi and lead >= MARGIN:
            dissent += 1
    V = len(arr)
    scores = {c: round(float(mean[i]), 4) for i, c in enumerate(cands)}
    detail: Dict[str, Any] = dict(base, arrangement=cname, strong=strong, dissent=dissent,
                                  winners=[int(w) for w in winners], wrap=wrap,
                                  table=[[round(float(x), 3) for x in row] for row in arr])
    decision, why = "unclear", ""
    others = [j for j in range(len(labels)) if j != bi]
    if mean[bi] < MIN_SCORE:
        why = "the line does not read as any candidate"
    elif wrap == "conflict":
        why = "the name may go on in the line under it (a wrapped cell, or the next row's text)"
    elif margin < MARGIN:
        why = "the candidates score too close"
    elif strong < math.ceil(MOST * V) or dissent > math.floor(DISSENT * V):
        why = "the image variants disagree"
    else:
        decision = "same" if labels[bi] == labels[0] else "differs"
    # every place where the winner differs from another spelling must itself favour the winner (owner 28 Oct: a
    # real difference is never "same"; a misread the recogniser shares is no confirmed difference)
    seg_lead, seg_what = differences_hold(bi, labels, segs, raw, others, index.get(".") if kind == "dots" else None)
    added = added_reads(bi, labels, segs, others)
    detail.update(seg_lead=round(seg_lead, 3), seg_what=seg_what, added_read=round(added, 3), pre=decision)
    if decision != "unclear" and seg_lead < SEG_MIN:
        decision, why = "unclear", "one of the differences is not clear in the print"
    if decision != "unclear" and added < ADDED_MIN:
        decision, why = "unclear", "the added word does not read clearly as print (artwork, a stamp, another script)"
    # the letter-shape expert, when the two best spellings differ in one small letter of another shape
    detail["shape"] = ""
    if kind == "name" and len(order) > 1 and mean[bi] >= MIN_SCORE and cname == "line" and wrap != "conflict":
        ri = int(order[1])
        big = next((v[2] for v in views if v[0].startswith(f"p{PADS[1]:.2f}/")), views[0][2])
        try:
            sv, swhy = shape_check(big, cands[bi], cands[ri], rec)
        except Exception as exc:                              # the expert never breaks the look
            sv, swhy = None, f"shape check failed ({type(exc).__name__})"
        detail["shape"] = swhy
        if sv == 0 and decision == "unclear" and margin >= MARGIN and dissent == 0 and seg_lead > 0 \
                and added >= ADDED_MIN:
            decision, why = ("same" if labels[bi] == labels[0] else "differs"), ""
            detail["by"] = "letter shape + the recogniser"
        elif sv == 1:
            backers = sum(1 for row in arr if row[ri] - row[bi] >= MARGIN)
            if backers >= 1:
                # two independent judges for the runner-up: the letter's shape and views of the recogniser
                bi, margin = ri, float(mean[ri] - mean[bi])
                decision, why = ("same" if labels[bi] == labels[0] else "differs"), ""
                detail["by"] = "letter shape + %d recogniser view(s)" % backers
            else:
                decision, why = "unclear", "the letter's shape and the recogniser disagree"
    dot: Optional[bool] = None
    if kind == "dots":
        dot, dot_why = _dot_check(cname, views, cands, rec)
        if dot is None and cname == "two-lines" and views_line:
            # rev 2.5: the two-line reading read better, but the dot after an initial or a title lies on the first
            # line: its shape is measured there (the detector answers None when it cannot tell)
            dot, dot_why = _dot_check("line", views_line, cands, rec)
            dot_why = "the line alone: " + dot_why
        detail["dot_geometry"] = dot_why
        pos = _dot_positions(cands)
        if pos:
            best_has = _has_dot(cands[bi], pos[0])
            if dot is None:
                # the recogniser alone: a dot it sees with a clear lead; never "no dot" (it drops printed dots)
                if decision != "unclear" and (margin < DOTS_ALONE or not best_has):
                    decision, why = "unclear", ("the dot's shape could not be measured and the recogniser alone "
                                                "does not settle a dot")
            elif best_has != dot:
                # the recogniser drops (and now and then adds) a dot: the dot's own shape decides when the
                # recogniser's lead is small; else the two judges disagree
                alt = [int(i) for i in order if _has_dot(cands[int(i)], pos[0]) == dot
                       and mean[int(i)] >= MIN_SCORE
                       and all(_has_dot(cands[int(i)], p) == _has_dot(cands[bi], p) for p in pos[1:])]
                if margin < DOTS_ALONE and alt and mean[bi] >= MIN_SCORE and wrap != "conflict" \
                        and seg_lead >= SEG_MIN and added >= ADDED_MIN:
                    bi = alt[0]
                    margin = float(mean[bi] - max(mean[j] for j in range(len(mean)) if j != bi))
                    decision, why = ("same" if labels[bi] == labels[0] else "differs"), ""
                    detail["by"] = "the dot's shape (the recogniser's lead was small)"
                else:
                    decision, why = "unclear", "the dot's shape and the recogniser disagree"
            elif decision == "unclear" and margin > 0 and agree * 2 > V and mean[bi] >= MIN_SCORE \
                    and wrap != "conflict" and seg_lead >= SEG_MIN and added >= ADDED_MIN:
                decision, why = ("same" if labels[bi] == labels[0] else "differs"), ""
                detail["by"] = "the dot's shape + the recogniser"
        detail["dot_seen"] = dot
    if decision == "differs" and float(mean[bi]) < DIFFERS_MIN:
        # rev 2.5: a confirmed difference is a readable print of the other spelling, not a weak read of a script
        decision, why = "unclear", "the other spelling does not read clearly enough to confirm a difference"
    if decision == "unclear":
        dot = None                                            # Look.dot is given only with a decision
    if decision == "same":
        note = "the image supports the reference spelling"
    elif decision == "differs":
        note = "the image supports another spelling than the reference"
    else:
        note = f"unclear: {why}"
    return Look(decision, cands[bi], scores, round(margin, 4), V, agree, dot, note, detail)


def _letters_x(big: np.ndarray, text: str, rec: "_Rec") -> Optional[Tuple[List[float], List[bool]]]:
    """Where the best alignment of `text` (kind "name" letters) puts each letter in `big` (x in pixels), and whether
    the recogniser reads each one as a capital there. None when a letter cannot be aligned."""
    g = _gray(big)
    m, index = rec.fold("name")
    letters = norm(text, "name")
    lab = _encode(letters, "name", index)
    if not lab:
        return None
    p = rec.probs(g)
    lp = np.log(np.maximum(p @ m, 1e-12))
    a, b = _region(lp, [lab])
    frames = viterbi_frames(lp[a:b + 1], lab)
    if len(frames) != len(lab) or any(f < 0 for f in frames):
        return None
    T = lp.shape[0]
    ratio = max(g.shape[1] / float(max(g.shape[0], 1)), 1.0)
    rw = max(int(math.ceil(MODEL_H * ratio)), 8)
    width = max(int(math.ceil(ratio)) * MODEL_H, rw)
    px = width / float(T + 1) * (g.shape[1] / float(rw))
    xs = [(a + f + 0.5) * px for f in frames]
    up = []
    for ch, f in zip(letters, frames):
        t = a + f
        iu = rec.chars.index(ch) + 1 if ch in rec.chars else None
        il = rec.chars.index(ch.lower()) + 1 if ch.lower() in rec.chars else None
        pu = float(p[t, iu]) if iu else 0.0
        pl = float(p[t, il]) if il else 0.0
        up.append(pu >= pl if ch.isalpha() else True)
    return xs, up


def _dot_check(cname: str, views: List[Tuple[str, np.ndarray, np.ndarray]], cands: List[str], rec: _Rec
               ) -> Tuple[Optional[bool], str]:
    """The geometric dot test at the first position where the candidates disagree on a dot, on the middle-margin
    crop: the letters' places from the dotless alignment, the gap between them in pixels."""
    pos = _dot_positions(cands)
    if not pos or not views:
        return None, "no dot position to test"
    if cname == "two-lines":
        return None, "two-line crop"
    big = next((v[2] for v in views if v[0].startswith(f"p{PADS[1]:.2f}/")), views[0][2])
    p = pos[0]
    plain = [c for c in cands if not _has_dot(c, p)]
    base = plain[0] if plain else cands[0]
    if p <= 0 or p >= len(norm(base, "name")):
        return None, "dot at the end of the name"
    got = _letters_x(big, base, rec)
    if got is None:
        return None, "letters not aligned"
    xs, _up = got
    return dot_at(big, xs[p - 1], xs[p]), "gap after letter %d" % p


# The vertical extent of small letters: "full" reaches the ascender line, "half" (t) stops between it and the x-line,
# "x" stops at the x-line, "dot" is an x-height stem with a dot above it. Capitals reach the cap line ("full").
_V_FULL, _V_HALF, _V_X, _V_DOT = "full", "half", "x", "dot"


def _vclass(ch: str, upper: bool) -> Optional[str]:
    if not ch.isalpha():
        return None
    if upper:
        return _V_FULL
    c = ch.lower()
    if c in "bdfhkl":
        return _V_FULL
    if c == "t":
        return _V_HALF
    if c in "ij":
        return _V_DOT
    if c in "acemnorsuvwxzgpqy":
        return _V_X
    return None


def _ink_mid(big: np.ndarray) -> np.ndarray:
    """Ink at half way between the print's darkest level and its paper (no blur: a thin crossbar or a short stem
    keeps its height)."""
    g = _gray(big) if big.ndim == 3 else big
    text_lv, bg_lv = np.percentile(g, 3), np.percentile(g, 70)
    if bg_lv - text_lv < 25:
        return np.zeros(g.shape, dtype=bool)
    return g < (text_lv + bg_lv) / 2.0


def _runs(col: np.ndarray) -> List[Tuple[int, int]]:
    out, start = [], None
    for y, v in enumerate(col):
        if v and start is None:
            start = y
        elif not v and start is not None:
            out.append((start, y - 1))
            start = None
    if start is not None:
        out.append((start, len(col) - 1))
    return out


def shape_check(big: np.ndarray, a: str, b: str, rec: Optional["_Rec"] = None) -> Tuple[Optional[int], str]:
    """The letter-shape expert for two spellings that differ in ONE small letter whose vertical extent differs (t / l,
    i / l, n / h, a / d, r / t ...): the letter's stem is measured against the line's own ascender line and x-line
    (column profiles of the ink at the letters' aligned places). Returns (0 when the print has a's letter, 1 when
    b's, None when it cannot tell) and a short reason. A second, independent judge besides the recogniser: the
    recogniser can read a small t as l on a low-resolution print in every view (owner folder 3)."""
    rec = rec or _rec()
    la, lb = norm(a, "name"), norm(b, "name")
    if len(la) != len(lb) or len(la) < 3:
        return None, "not one substitution"
    diff = [i for i, (x, y) in enumerate(zip(la, lb)) if x != y]
    if len(diff) != 1:
        return None, "not one substitution"
    j = diff[0]
    ca, cb = _vclass(la[j], False), _vclass(lb[j], False)
    if ca is None or cb is None or ca == cb:
        return None, "the two letters have one shape class"
    got = _letters_x(big, a, rec)
    if got is None:
        return None, "letters not aligned"
    xs, up = got
    if up[j]:
        return None, "a capital at the letter"
    ink = _ink_mid(big)
    if not ink.any():
        return None, "no contrast"
    H, W = ink.shape
    tops = np.full(W, -1)
    bots = np.full(W, -1)
    for x in range(W):
        ys = np.flatnonzero(ink[:, x])
        if ys.size:
            tops[x], bots[x] = ys[0], ys[-1]
    have = tops >= 0
    if have.sum() < 10:
        return None, "no ink"
    steps = [xs[k + 1] - xs[k] for k in range(len(xs) - 1) if xs[k + 1] > xs[k]]
    pitch = float(np.median(steps)) if steps else 0.3 * H

    def window(k: int) -> Tuple[int, int]:
        lo = int(round(xs[k] - 0.2 * pitch))
        hi = int(round(xs[k] + 0.55 * pitch))
        if k + 1 < len(xs):
            hi = min(hi, int(round(xs[k + 1] - 0.15 * pitch)))
        if k > 0:
            lo = max(lo, int(round(xs[k - 1] + 0.6 * pitch)))
        return max(0, lo), min(W - 1, max(hi, lo))

    def top_of(k: int) -> Optional[float]:
        lo, hi = window(k)
        t = tops[lo:hi + 1]
        t = t[t >= 0]
        return float(t.min()) if t.size else None

    full_t, x_t, base = [], [], []
    for k in range(len(la)):
        if k == j:
            continue
        cl = _vclass(la[k], up[k])
        t = top_of(k)
        if t is None:
            continue
        if cl == _V_FULL:
            full_t.append(t)
        elif cl == _V_X and la[k].lower() not in "gpqy":
            x_t.append(t)
        lo, hi = window(k)
        bb = bots[lo:hi + 1]
        bb = bb[bb >= 0]
        if bb.size and (up[k] or la[k].lower() not in "gjpqy"):
            base.append(float(np.median(bb)))
    if len(full_t) < 1 or len(x_t) < 2:
        return None, "the word has too few known letters"
    asc, xl = float(np.median(full_t)), float(np.median(x_t))
    if xl - asc < 0.1 * H:
        return None, "the ascender and x lines are too close"
    lo, hi = window(j)
    if ca == _V_DOT or cb == _V_DOT:
        # i / j: a short run (the dot), a gap, then the stem from the x-line; l: one run from the ascender line
        dotted, solid = 0, 0
        for x in range(lo, hi + 1):
            rs = [r for r in _runs(ink[:, x]) if r[1] - r[0] >= 1]
            if not rs:
                continue
            if len(rs) >= 2 and rs[0][1] < xl - 0.05 * (xl - asc) and rs[1][0] >= xl - 0.25 * (xl - asc):
                dotted += 1
            elif rs[0][0] <= asc + 0.25 * (xl - asc) and rs[0][1] >= xl:
                solid += 1
        measured = _V_DOT if dotted >= 2 and dotted > solid else _V_FULL if solid >= 2 and solid > dotted else None
        r = None
    else:
        t = top_of(j)
        if t is None:
            return None, "the letter's glyph was not found"
        r = (t - asc) / (xl - asc)
        measured = _V_FULL if r < 0.15 else _V_HALF if 0.25 <= r <= 0.8 else _V_X if r > 0.88 else None
    if measured is None:
        return None, "the letter's height is between two shapes"
    if measured == ca:
        return 0, f"the letter's shape is '{ca}'"
    if measured == cb:
        return 1, f"the letter's shape is '{cb}'"
    return None, "the letter's shape fits neither"


# ---------------------------------------------------------------------------------------------------------- pages
def native_dpi(path: Any, page_no: int) -> Optional[float]:
    """The resolution of the largest image on a scanned PDF page (pixels per inch of the page), else None."""
    try:
        import pymupdf
        with pymupdf.open(str(path)) as doc:
            infos = doc[page_no].get_image_info()
            best = None
            for inf in infos:
                bb = pymupdf.Rect(inf["bbox"])
                if bb.width <= 1:
                    continue
                d = float(inf.get("width") or 0) / (bb.width / 72.0)
                area = bb.width * bb.height
                if best is None or area > best[0]:
                    best = (area, d)
            return best[1] if best else None
    except Exception:
        return None


def page_image(engine: Any, page: Any, dpi: int = 400) -> np.ndarray:
    """A high-resolution RGB render of a PageRead: a vector (text-layer) PDF page at `dpi`; a scanned PDF page at
    its scan's own resolution (150-600 dpi); a JPG / PNG file as it is. Binds the engine's reader for the looks."""
    bind(engine)
    kind = getattr(page, "kind", "")
    use = int(dpi)
    if kind in ("ocr",):
        path = None
        try:
            path = engine._path_of(page)
        except Exception:
            path = None
        nd = native_dpi(path, int(getattr(page, "file_page", 0))) if path is not None else None
        if nd:
            use = int(max(150, min(600, round(nd))))
    return engine.page_image_of(page, use)
