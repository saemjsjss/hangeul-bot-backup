"""Shared data contract of the document-check test runner (RUNNER_SPEC section 4.1, revision 2).
Standard library only, no I/O, no personal data. Changed only by agreement of every task."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

RUNNER_VERSION = "0.2.4"
BASE_RULES_VERSION = "2026-10-21.3"      # the bot rules in the worktree's src/verify (commit 07b1f22)
RUNNER_RULES_VERSION = "2026-10-28.r2.4"   # + the owner's 21-27 Oct decisions (A1-A8), applied in the runner only
RUN_SCHEMA = "runner-run/2"
PAGE_TEXTS_SCHEMA = "runner-page-texts/1"   # gpuread.py writes it, engine.load_page_texts reads it (5.2.1)

# Page numbers shown to the reader. A run's pages are numbered in run order; the reader needs the page inside
# its input file (a bundle: the bundle page; folder mode: the page of that file). The pipeline sets the map for
# the run (set_page_numbering) and clears it at the end; with no map, run page i is page i + 1.
_PAGE_MAP: Optional[List[int]] = None
_PAGE_FILE: Optional[List[str]] = None


def set_page_numbering(pages: Optional[List[Any]]) -> None:
    """Map run page i to its input page (`file_page` of each PageRead) and its file; None clears the map."""
    global _PAGE_MAP, _PAGE_FILE
    _PAGE_MAP = None if pages is None else [int(getattr(p, "file_page", i)) for i, p in enumerate(pages)]
    _PAGE_FILE = None if pages is None else [str(getattr(p, "file", "") or "") for p in pages]


def page_no(run_page: int) -> int:
    """The 1-based input page of a 0-based run page (bundle page, or page of the folder file)."""
    m = _PAGE_MAP
    if m is not None and 0 <= run_page < len(m):
        return m[run_page] + 1
    return int(run_page) + 1


def page_file(run_page: Optional[int]) -> str:
    """The input file a 0-based run page belongs to ("" when unknown)."""
    f = _PAGE_FILE
    if f is not None and run_page is not None and 0 <= run_page < len(f):
        return f[run_page]
    return ""


def page_in(run_page: int, home_run_pages: Any = ()) -> str:
    """"page N", or "page N of <FILE>" when the page lies in another file than the home pages (folder mode: a
    finding filed on one file that names a page of another, review 2)."""
    f = page_file(run_page)
    homes = {page_file(p) for p in (home_run_pages or ())}
    return f"page {page_no(run_page)}" + (f" of {f}" if f and homes and f not in homes else "")


# Finding levels. NOTE counts as PASS; ERROR shows as FLAG ("could not be checked: ...").
PASS, NOTE, FLAG, FAIL, ERROR = "PASS", "NOTE", "FLAG", "FAIL", "ERROR"
PRESENT = "PRESENT"          # presence-only line: ID card / CV, income tax outside Master's
ORDER = "ORDER"              # a "fix these pages" finding; verdict.merge lifts it into a Notice
# Document-line and overall labels: the owner's words, plus the two the runner needs.
NOT_JUDGED = "NOT JUDGED"    # a unit a blocking ORDER notice touches: fix the pages, then check again
MISSING = "MISSING"          # a "X MISSING" line
STOP = "STOP"                # only the NID-person ambiguity (section 5.4)
OVERALL = (STOP, FAIL, FLAG, PASS)
RANK = {PASS: 0, NOTE: 0, PRESENT: 0, ORDER: 0, FLAG: 1, NOT_JUDGED: 1, ERROR: 2, FAIL: 3}
PROGRAMS = ("KLP", "EAP", "BACHELOR", "MASTER")
# the portal-shaped keys the bot's checkers read (== doc_verifier.PORTAL_KEYS_READ, tested)
PORTAL_KEYS = ("Full Name", "Father", "Mother", "DOB", "Gender", "Passport No", "Passport Expiry",
               "Sponsor", "SSC GPA", "HSC GPA", "Program")
# checks added from test 1 (A6 j): FLAG only, listed in the report as "new, keep?" (Q14)
NEW_CHECK_RULES = ("tin.web_address", "income_tax.tax_office")


@dataclass
class Finding:
    verdict: str                     # PASS | NOTE | FLAG | FAIL | ERROR | PRESENT | ORDER
    rule: str                        # rule id (RUNNER_SPEC section 6), e.g. "bank.holder_parent"
    message: str                     # owner-facing text; may carry the student's data (report only)
    source: str                      # "bot:check_bank", "bot:page_checks.qr_check", "runner:bank" ...
    pages: Tuple[int, ...] = ()      # 1-based pages of the input (bundle page, or page of the folder file)
    evidence: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SourceFile:
    path: Path                       # the input file (read-only)
    name: str
    is_image: bool
    n_pages: int
    size: int
    mtime: float
    key_by_name: Optional[str] = None   # folder mode: bot key from ingest.classify_name()


@dataclass
class Source:
    mode: str                        # "bundle" | "folder"
    root: Path                       # the bundle file or the folder
    files: List[SourceFile]
    case_id: str                     # "B-<sha1 of bundle bytes>[:16]" | "F-<sha1 of name|size|mtime list>[:16]"
    # folder mode, revision 2.3: files the runner cannot check, (file name, why): "not a PDF or image" (an .odg,
    # a .docx ...) or "could not be opened" (a broken or password-protected PDF or image). Listed in the report
    # as "Not checked (...)"; never a document, never a slot.
    not_checked: List[Tuple[str, str]] = field(default_factory=list)


@dataclass
class PageRead:
    index: int                       # 0-based position in the run's page list
    file: str                        # SourceFile.name
    file_page: int                   # 0-based page inside that file
    text: str                        # the bot's read_pages text (200 dpi; text layer when >= 40 chars)
    kind: str                        # "layer" | "ocr" | "image"
    chars: int
    sideways: bool
    seconds: float
    image_only: bool                 # no text layer and one embedded image covering >= 50% of the page
    engine: str = ""                 # "layer" | "gpu" (served from --page-texts) | "cpu" (runner light engine)


@dataclass
class PageType:
    index: int
    best: str                        # fine type (prototype labels, "photo" or "other")
    score: float
    scores: Dict[str, float]
    translation: bool                # letterhead translation cue (order study)
    level: Optional[str]             # order_split._LEVELS names: "degree" | "diploma" | "HSC" | "SSC" | None
    kind: str                        # academic: "apostille" | "certificate" | "transcript"; bank: "solvency" |
                                     # "statement"; else ""
    nid_numbers: List[str]
    birth_dates: List[str]           # ISO dates read after a birth label
    holder: List[str]                # name parts after a "Name" label (never Father's/Mother's/Husband's)


@dataclass
class DocUnit:
    uid: str                         # "D01", "D02", ... in page order
    key: str                         # bot key (rules.FILE_PATTERNS keys), "nid_unassigned" or "unrecognised"
    fine: str                        # finer type: "tin_certificate", "employment_certificate", "trade_license", ...
    title: str                       # report title: "FATHER NID", "TIN CERTIFICATE", "ACADEMIC HSC" ...
    pages: List[int]                 # 0-based PageRead indexes, in order
    role: str = ""                   # "father" | "mother" | "student" | ""
    role_how: str = ""               # "name" | "name-partial" | "position" | "file-name" | ""
    misplaced: bool = False
    level: str = ""                  # academic unit: "HSC" | "SSC" | "degree" | "diploma"; else ""
    sub: List[Tuple[int, int, str]] = field(default_factory=list)   # folder file holding several parts (0-based, incl.)
    path: Optional[Path] = None      # file the bot checkers read: cut sub-PDF, staged copy or photo crop
    source_file: str = ""            # folder mode: the input file name
    notes: List[Finding] = field(default_factory=list)              # split-level findings about this unit
    # folder mode, revision 2.3: "p4-6" when the unit is one of several documents cut out of its file (the
    # pages inside that file); "" when the unit is the whole file (or in a bundle)
    part: str = ""


@dataclass
class Notice:
    kind: str                        # "ORDER" | "HINT" | "NOTE" | "STOP"
    pages: Tuple[int, int]           # 1-based, inclusive
    message: str
    blocks: bool = False             # ORDER only: the units on these pages are NOT JUDGED until the pages are fixed
    units: Tuple[str, ...] = ()      # uids the notice touches


@dataclass
class SplitResult:
    units: List[DocUnit]
    notices: List[Notice]
    missing: List[str]               # slot ids (split.SLOTS) with no document
    stop: bool
    maybe: Dict[str, str] = field(default_factory=dict)   # slot id -> uid of a death certificate whose person is unknown


@dataclass
class NameRef:
    parts: Tuple[str, ...] = ()      # strict.words() of the name (titles dropped; initials, MD/MST kept)
    written: str = ""                # as printed, dots kept: the dot reference (A2); "" when only a code read it
    source: str = ""                 # "passport MRZ" | "passport MRZ re-read" | "passport printed" | "birth certificate" |
                                     # "passport field" | "father NID" | "mother NID" | ""
    unit: str = ""                   # uid of the document it was read from
    pages: Tuple[int, ...] = ()      # 0-based run pages it was read on
    confirmed: bool = False          # A1: a second independent read agrees (MRZ, another page or document, re-read)
    truncated: bool = False          # MRZ name field full (39 characters): the name may be cut
    # Birth certificate (A3): the words its label value goes on with after the name the other documents also
    # print, NOT confirmed by a second read (whole-page OCR prints Bangla text as Latin junk right there). A
    # document or holder that adds exactly these words is FLAG, never FAIL: the birth certificate may print them.
    tail: Tuple[str, ...] = ()


@dataclass
class Identity:
    student: NameRef                 # parts: MRZ letters; written: the passport's printed name (dot reference)
    father: NameRef                  # birth certificate (A3); else passport field; else the role-confirmed NID
    mother: NameRef
    father_nid: NameRef = field(default_factory=NameRef)   # the name on the father's role-confirmed NID (A7)
    mother_nid: NameRef = field(default_factory=NameRef)
    surname: str = ""
    given_names: str = ""
    name_trusted: bool = False       # MRZ line 1 well formed
    passport_no: str = ""
    passport_no_ok: bool = False     # MRZ check digit agrees
    printed_passport_no: str = ""
    dob: Optional[dt.date] = None
    dob_source: str = ""             # "MRZ" | "MRZ re-read" | "passport label" | "birth certificate" | ""
    dob_trusted: bool = False        # only an MRZ date trusted on doc_verifier._trusted terms
    expiry: Optional[dt.date] = None
    expiry_source: str = ""          # "MRZ" | "MRZ re-read" | "passport label" | "scan date" | ""
    expiry_trusted: bool = False     # MRZ (trusted) or the "Date of Expiry" label; a "scan date" never
    sex: str = ""
    name_verified: bool = False      # language.passport_name_verified(): every MRZ name word is printed
    mrz: Dict[str, Any] = field(default_factory=dict)   # the best MRZ read (doc_verifier.passport_mrz keys)
    notes: List[Finding] = field(default_factory=list)


@dataclass
class DerivedRecord:
    program: str
    today: dt.date
    identity: Identity

    def portal_view(self) -> Dict[str, str]:
        """What the bot's checkers get instead of the portal record (RUNNER_SPEC section 5.7).
        Every name, date, number, sponsor and GPA field is EMPTY on purpose. In the bot those
        fields drive loose name matches (name_in), self-confirming trust escapes (DOB, expiry)
        or tautologies (the passport number). The runner's strict rules use `identity`."""
        view = {k: "" for k in PORTAL_KEYS}
        view["Program"] = self.program
        return view


@dataclass
class MachineRead:
    """A name read by a code, not by OCR (A1: a machine read confirms letters, never dots)."""
    unit: str                        # uid of the document carrying the code
    person: str                      # "student" | "father" | "mother"
    name: str                        # as decoded
    source: str                      # "board QR" | "birth QR"
    page: int                        # 0-based run page


@dataclass
class BirthFacts:
    """What birth.read_birth_certificate() reads off one birth-certificate unit (RUNNER_SPEC 5.12)."""
    pages: Tuple[int, ...] = ()      # 0-based run pages of the unit; the lookup key is tuple(unit.pages)
    format: str = "unknown"          # "bangla_original" | "bilingual_qr" | "english_bdris" | "english_translation" |
                                     # "unknown"
    variant: str = ""                # "everify" | "manual_english" | ""
    page_kinds: List[str] = field(default_factory=list)      # birth.page_kind() per page, in page order
    english_pages: List[int] = field(default_factory=list)   # pages names and fields are read from (0-based run pages)
    has_translation: bool = False
    notarised: bool = False
    notary_pages: List[int] = field(default_factory=list)
    seal_only_pages: List[int] = field(default_factory=list) # a seal colour but no notary wording
    dob: Optional[dt.date] = None    # the certificate's date of birth (None when unread or unclear)
    dob_trusted: bool = False        # two independent readings agree (boxes or label, AND the "In words" line)
    dob_source: str = ""             # "boxes+words" | "label+words" | "boxes" | "label" | "words" | ""
    dob_readings: List[Tuple[str, str, int]] = field(default_factory=list)   # (source, ISO date, page)
    brn: str = ""                    # birth registration number as printed (17 digits)
    brn_confirmed: bool = False      # read alike twice (page text and zoomed re-read, or two pages)
    issue_date: Optional[dt.date] = None          # NEVER the date of birth
    registration_date: Optional[dt.date] = None   # NEVER the date of birth
    qr: Dict[str, Any] = field(default_factory=dict)   # decode result (host, path, key_len, key_bytes, kind ...), no images
    qr_status: str = "absent"        # "decoded" | "undecoded" | "absent"
    qr_name: str = ""                # a name the payload carries ("" for the opaque bdris key)
    qr_brn: str = ""                 # a BRN the payload or the CODE128 barcode carries
    qr_dob: Optional[dt.date] = None # a date of birth the payload carries
    notes: List[str] = field(default_factory=list)


@dataclass
class AffidavitInfo:
    present: bool = False
    units: List[str] = field(default_factory=list)                  # uids of affidavit documents
    groups: List[Tuple[str, List[str]]] = field(default_factory=list)   # (whose, same-person spellings AS WRITTEN)
    documents: List[str] = field(default_factory=list)              # kinds it names: nid, birth, academic ...


@dataclass
class DocResult:
    unit: DocUnit
    findings: List[Finding]
    verdict: str                     # PASS | FLAG | FAIL | PRESENT | NOT JUDGED
    reasons: List[str]               # FAIL, then ERROR, then FLAG messages (for the summary line)


@dataclass
class RunResult:
    schema: str                      # RUN_SCHEMA
    runner_version: str
    base_rules_version: str
    runner_rules_version: str
    case_id: str
    mode: str
    input: str
    program: str
    today: str
    reading: Dict[str, Any]          # {"pages_by_engine": {"gpu": n, "cpu": n, "layer": n}, "note": str, "page_texts": str}
    pages: List[PageRead]
    types: List[PageType]
    split: SplitResult
    identity: Optional[Identity]
    births: List[BirthFacts]
    docs: List[DocResult]
    cross: List[Finding]             # findings behind the NAMES and FINANCIAL CHAIN lines + bundle-level findings
    notices: List[Notice]            # split notices + lifted ORDER findings, in page order
    missing_lines: List[Tuple[str, str]]   # (MISSING | FLAG, text)
    overall: str                     # STOP | FAIL | FLAG | PASS
    headline: str
    timings: Dict[str, float]
    safety: Dict[str, Any]
    # folder mode, revision 2.3: Source.not_checked, (file name, why), one "Not checked (...)" summary line each
    not_checked: List[Tuple[str, str]] = field(default_factory=list)
