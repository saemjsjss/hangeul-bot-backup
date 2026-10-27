"""
The financial chain: taxpayer = bank holder = trade-licence owner / employee, letter for letter (RUNNER_SPEC
5.9.4; a port of the lab's fin_names.py; owner decisions A1, A6 j and A7, critique items 3, 9, 11 and 20).
No personal data here.

Owner rules (27 Oct 2026):
  "tin qr must be checked by system and with the qr result it must match whats on the actual tin and
   if its name is 100% matched by trade or employment"
  "tax payer name should match bank account holder and trade license/employee owner name"

Extractors, kept from the lab (each returns the raw name string as printed, "" when not found):
  tin_taxpayer(text, qr_payloads)  QR first (tin.parse_tin_qr), printed name as the cross-check
  trade_proprietor(text)           the trade licence's owner / proprietor / licensee
  owner_candidates(text)           every name after an owner label, "name" labels first
  employee_name(text)              the employee on an employment / salary certificate
  assessee_name(text)              the taxpayer on an income-tax return (kept for the report; no rule uses it)

Matching: strict.py only (A1). EQUAL is the only match; every other kind is judged by strict.judge -
a CONFIRMED uncovered difference is FAIL, any other difference FLAG "check by eye", never PASS.
  owner_slot(text, name)     OWNER only when the owner row's WHOLE value is strictly equal (critique 3)
  best_owner_match           the owner candidate closest to the taxpayer, by strict kind
  chain_findings             financial.taxpayer_read / taxpayer_print_only / taxpayer_owner /
                             taxpayer_employee / taxpayer_holder / holder_owner
  income_tax_findings        income_tax.tax_office (NEW, FLAG only, Q14)

Changed from the lab (the only changes): the bot's loose matchers and their lab copies (norm, the token
list, the 100%-token test, the disagreement kinds, the "clearly different" test, the name_in copy) are
gone; strict.py replaces them. `_take_name` keeps its "is there a real name word" guard as
`_has_name_word` (an extraction guard, never a comparison). The proposed-rule function is replaced by
chain_findings; financial.taxpayer_parent is removed (critique 20).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from . import strict
from .model import FAIL, FLAG, NOTE, PASS, DocUnit, Finding, Identity, NameRef, page_in, page_no

SOURCE = "runner:financial"
# Extraction guard only: _take_name returns a capture only when it holds a word of 3+ letters that is not a
# title or an honorific spelling (the lab's behaviour). Names are never COMPARED with these.
_GUARD_SKIP = frozenset({"md", "mst", "most", "mr", "mrs", "miss", "ms", "late", "sk", "mohd"})
_GUARD_SKIP_RE = re.compile(r"^(?:m[ou]h[a]?m+[aeo]?d|mos+[ao]m+[ae]t)$")


def _has_name_word(name: str) -> bool:
    """True when the captured value holds a real name word (not only titles, honorifics or initials)."""
    for w in re.split(r"[^a-z]+", re.sub(r"\s+", " ", name or "").strip().lower()):
        if len(w) >= 3 and w not in _GUARD_SKIP and not _GUARD_SKIP_RE.match(w):
            return True
    return False


def _edit(a: str, b: str) -> int:
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


# ---- capturing a name after a label -------------------------------------------------------
# Words that end a name: the next label, a relation, an address, an ID, a verb.
_STOP = set("""
father fathers father's husband husbands husband's mother mothers mother's spouse spouses spouse's wife
son daughter guardian relation name names of the and or
address addresses permanent present current village vill road holding house ward word post office upazila
thana district zila division union city corporation pourashava paurashava municipality area block sector
nid national passport birth tin bin e-tin etin id identification number no reg registration serial
mobile phone cell tel telephone email e-mail fax
business organization organisation institution company firm shop store trade trading traders enterprise
type nature category capital licence license licensee licen proprietor proprietorship owner owners owner's
assessee assesse assessee's taxpayer taxpayers taxpayer's
is was has have had being been working employed serving joined appointed posted holds holding bearing
designation position post grade dept department employee staff since from with in at as for on to
date dob dated issue issued valid validity fiscal financial year amount tk taka total
circle zone status individual previous gender male female religion nationality bangladeshi
tax vat fee managing director chairman partner ceo shareholder signature seal authorized authorised
ownership wealth institute establishment
""".split())
_RELATION_STOPS = re.compile(r"^(?:s/o|d/o|w/o|c/o|h/o)$", re.I)
_HONOR_KEEP = re.compile(r"^(?:md|mst|most|mr|mrs|ms|miss|late|sk|mohd|dr)\.?:?$", re.I)
# OCR-garbled label words ("Falher's", "Molher", "Husbend", "Adress"): a word within one letter of
# one of these is a label, never a name part.
_FUZZY_STOPS = ("father", "mother", "husband", "spouse", "address", "proprietor", "permanent", "present",
                "business", "organization", "designation", "national", "village", "holding", "nature")


def _is_stop(w: str) -> bool:
    core = w.strip(".:'\"-_").lower()
    letters = re.sub(r"[^a-z]", "", core)
    if core in _STOP or core.rstrip("'s$\"`") in _STOP or letters in _STOP:
        return True
    base = re.sub(r"(?:'s|s)$", "", letters)
    return len(base) >= 5 and any(abs(len(base) - len(s)) <= 1 and _edit(base, s) <= 1 for s in _FUZZY_STOPS)


def _take_name(s: str, max_words: int = 6) -> str:
    """The name at the start of `s`: words up to the first stop word, digit, bracket, comma or
    colon (a colon right after Md/Mst - "Md: NAME" - is part of how the name is written)."""
    words: List[str] = []
    for m in re.finditer(r"[^\s,;:()\[\]{}|<>]+:?|[,;:()\[\]{}|<>]", s):
        w = m.group(0)
        if w in ",;:()[]{}|<>":
            if words and not all(_HONOR_KEEP.match(x) for x in words):
                break                               # "Md: NAME", "Md , NAME": still the title
            continue
        if re.search(r"\d", w) or _RELATION_STOPS.match(w.rstrip(":")):
            break
        if not w.strip(".:'\"-_"):
            continue
        if _is_stop(w):
            break
        if not re.fullmatch(r"[A-Za-z][A-Za-z.'\-]*", w.strip(".:")):
            break
        words.append(w.strip(":"))
        if w.endswith(":") and not _HONOR_KEEP.match(w):
            break                                   # "NAME: next label"
        if len(words) >= max_words:
            break
    while words and _HONOR_KEEP.match(words[-1]):
        words.pop()                                 # "NAME MD" - the next field's honorific
    name = " ".join(words).strip(" .:-")
    return name if _has_name_word(name) else ""


# OCR writes ' as $, ", ` or drops it, and / as l, I, 1, [ or |: "Owner's/Proprietor'$",
# "Ownerlproprietor", "Owners/Proprietors", "IProprletor's", "Proprieror's", "Proprietor /Assesse"
_APOS = r"(?:\s?['\"`$’�]{1,2}\s?[s$]?|\s?s)?"      # �: a curly quote lost in the text layer
_SLASH = r"\s*[/\\\[{(|lI1]?\s*"
_PROP = r"[I|\[]?pr[o0]pr[a-z1]{2,5}"         # proprietor / proprletor / proprieror / propritor
_ASSESSEE = r"assess?e?e"
_NAMEW = r"n[ae]r?n?m?e"                    # name / narne / nane / neme
_OWNERW = r"(?:owner" + _APOS + _SLASH + r")?(?:" + _PROP + r"|owner)" + _APOS \
          + r"(?:\s*/\s*(?:" + _ASSESSEE + r"|company|managing\s+director|taxpayer" + _APOS + r"))?(?:\s*/?\s*managing\s+director)?"
_LABELS_NAMED = [                            # labels that hold the owner's name: tried first
    r"name\s+of\s+(?:the\s+)?(?:" + _OWNERW + r"|licen[sc]ee|licen[sc]e\s+holder|applicant)"
    r"(?:\s+of\s+(?:the\s+)?(?:business|firm|organi[sz]ations?|institut(?:e|ion)|establishment|company|concern|shop|trade))?",
    _OWNERW + r"\s*" + _NAMEW,
    r"(?:licen[sc]ee|licen[sc]e\s+holder)" + _APOS + r"\s*" + _NAMEW,
    r"owner\s+of\s+(?:the\s+)?(?:business|firm|organi[sz]ation|institution|company|concern|shop)"
    r"(?:\s+organi[sz]ation)?",
]
_LABELS_BARE = [                             # a bare word: also used in addresses ("Address of Owner")
    _OWNERW,
    r"licen[sc]ee",
]
_LB = r"(?<![a-hj-km-z])"                    # "IProprietor": a stray I or l before the label is OCR noise
_OWNER_LABELS = _LABELS_NAMED + _LABELS_BARE
OWNER_RE = re.compile(_LB + r"(?:" + "|".join(_OWNER_LABELS) + r")(?![a-z])", re.I)
NAMED_OWNER_RE = re.compile(_LB + r"(?:" + "|".join(_LABELS_NAMED) + r")(?![a-z])", re.I)
BARE_OWNER_RE = re.compile(_LB + r"(?:" + "|".join(_LABELS_BARE) + r")(?![a-z])", re.I)
RELATION_RE = re.compile(
    r"(?<![a-z])(?:father|husband|mother|spouse|guardian)" + _APOS + r"\s*(?:/\s*(?:father|husband)" + _APOS + r"\s*)?name"
    r"|name\s+of\s+(?:the\s+)?(?:father|husband|mother|spouse)(?:\s*/\s*husband)?"
    r"|(?<![a-z])(?:son|daughter|wife)\s+of(?![a-z])|(?<![a-z])[sdw]\s*/\s*o(?![a-z])", re.I)


def trade_proprietor(text: str) -> str:
    """The trade licence's owner: the first owner label followed by a name (not by an address,
    a business or a relation)."""
    c = owner_candidates(text)
    return c[0] if c else ""


def owner_candidates(text: str) -> List[str]:
    """Every name that follows an owner label, labels that say "name" first, in reading order."""
    out: List[str] = []
    t = re.sub(r"[ \t\u00a0]+", " ", text or "")
    for rx in (NAMED_OWNER_RE, BARE_OWNER_RE):          # "Proprietor's Name" before a bare "Proprietor"
        for m in rx.finditer(t):
            after = t[m.end(): m.end() + 120]
            after = re.sub(r"^\s*(?:\(?\s*(?:if\s+any|s)\s*\)?)?\s*(?:" + _NAMEW + r"\b)?\s*[:\-–_.|;]*\s*", "",
                           after, flags=re.I)
            name = _take_name(after)
            if name and name not in out:
                out.append(name)
    return out


_TAX_CTX = re.compile(r"registered\s+tax\s*payer|assess?ee\s+of|tax\s*payer\s+of|is\s+an?\s+assess?ee"
                      r"|\bis\s+an?\s+\S{0,14}\s*tax\s*payer|national\s+board\s+of|return\s+of\s+income", re.I)
# "This is to certify that", "This is t0 certify", "This is to certify Md ..." (no "that"), and a
# garbled verb between "This is to" and "that" ("This is to cerlfy that", "This is to <noise> that").
# A bare "Certified" with neither "to/is" before it nor "that" after it (a stamp, a heading) is not
# the subject sentence.
_CERTIFY = re.compile(r"(?P<pre>\b(?:to|t0|is)\s+)?(?<![a-z])[a-z]{0,2}er?t[il1]f(?:y|ied)\s+(?P<that>that\s+)?(?=\S)"
                      r"|\bthis\s+is\s+to\s+[a-z]{4,10}\s+(?P<that2>that\s+)(?=\S)", re.I)


def employee_name(text: str) -> str:
    """The employee on an employment / salary certificate: the subject of "This is to certify that
    [Mr./Mrs./Md.] <NAME>, [son of ... | S/O ... | Father Name ... | Employee ID ... | NID ...] has been
    working / is employed ...". The son-of / father / mother clause and the signatory are never taken.
    A TIN or tax-return sentence ("... is a registered taxpayer", "... is an assessee", "... National
    Board of Revenue") is skipped."""
    t = re.sub(r"[ \t\u00a0]+", " ", text or "")
    for m in _CERTIFY.finditer(t):
        if not (m.group("pre") or m.group("that") or m.group("that2")):
            continue
        sentence = t[m.end(): m.end() + 260]
        if _TAX_CTX.search(sentence[:160]):
            continue
        if not re.search(r"work|employ|serv|appoint|join|post|staff|salary|teacher|officer|designation|"
                         r"position|permanent|company|limited|ltd|bank|school|college|hospital|organi[sz]ation",
                         sentence, re.I):
            continue
        name = _take_name(re.sub(r"^\s*(?:the\s+bearer\s*,?\s*)", "", sentence, flags=re.I))
        if name:
            return name
    for pat in (r"name\s+of\s+(?:the\s+)?employee\s*[:\-|]*\s*", r"employee" + _APOS + r"\s*name\s*[:\-|]*\s*"):
        m = re.search(pat, t, re.I)
        if m:
            name = _take_name(t[m.end(): m.end() + 120])
            if name:
                return name
    return ""


_NOT_OWN_NAME = re.compile(r"(?:father|mother|husband|spouse|guardian|business|organi[sz]ation|bank|company|"
                           r"employer|nominee|son|daughter)\W{0,4}s?\W{0,3}$", re.I)


def assessee_name(text: str) -> str:
    """The taxpayer on an income-tax return / acknowledgement / certificate."""
    t = re.sub(r"[ \t\u00a0]+", " ", text or "")
    pats = [
        r"name\s+of\s+(?:the\s+)?(?:tax\s*payer|assess?ee)\s*[:\-|]*\s*",
        r"tax\s*payer" + _APOS + r"\s*name\s*[:\-|]*\s*",
        r"assess?ee\s*[:\-|]*\s*(?:\d\W*)?(?:\(?a\)?\W{0,2})?\s*name\s*[:\-|]*\s*",
    ]
    for pat in pats:
        for m in re.finditer(pat, t, re.I):
            name = _take_name(t[m.end(): m.end() + 120])
            if name:
                return name
    for m in re.finditer(r"certif(?:y|ied)\s+that\s+(.{3,80}?)\s+(?:is|iS|Is|\S{1,2})\s+(?:a\s+|an\s+|&\s+)?"
                         r"(?:registered\s+tax\s*payer|assess?ee|tax\s*payer)", t, re.I):
        name = _take_name(m.group(1) + " ,")
        if name:
            return name
    # a bare "Name : X" row of a return form, not "Father's Name" / "Mother's Name"
    for m in re.finditer(r"(?<![a-z])name\s*\|?\s*[:\-]\s*\|?\s*", t, re.I):
        if _NOT_OWN_NAME.search(t[max(0, m.start() - 16): m.start()]):
            continue
        name = _take_name(t[m.end(): m.end() + 120])
        if name:
            return name
    return ""


def tin_taxpayer(text: str, qr_payloads: Iterable[str] = ()) -> Dict[str, object]:
    """The TIN certificate's taxpayer. The QR (NBR's own data) is the primary source; the printed
    "certify that X is a Registered Taxpayer" / "1) Name X" is the cross-check.
    {'name': chosen, 'source': 'QR' | 'PRINTED' | '', 'qr': .., 'printed': .., 'agree': EQUAL/...}"""
    from . import tin as Q
    qr = None
    for p in qr_payloads:
        qr = Q.parse_tin_qr(p)
        if qr and qr.get("name"):
            break
    pf = Q.printed_tin_fields(text)
    printed = pf.get("name") or pf.get("certify_name") or ""
    qn = (qr or {}).get("name", "")
    out = {"qr": qn, "printed": printed, "qr_fields": qr or {}, "printed_fields": pf}
    if qn:
        out.update(name=qn, source="QR", agree=strict.compare(qn, printed, dots=False) if printed else "NOT_READ")
    elif printed:
        out.update(name=printed, source="PRINTED", agree="NO_QR")
    else:
        out.update(name="", source="", agree="NONE")
    return out


# =====================================================================================================
# The runner's rules (RUNNER_SPEC 5.9.4). Everything above this line is the lab's fin_names.py extractors.
# =====================================================================================================
KIND_TEXT = {"EQUAL": "the same", "DOTS": "only the dots differ", "OCR": "a look-alike character differs (I/l/1, O/0)",
             "ORDER": "the same words in another order", "SUBSET": "a word is missing or extra",
             "LETTERS": "one or two characters differ", "DIFFERENT": "a different name", "EMPTY": "not read"}
_TOKEN = re.compile(r"[^\s,;:()\[\]{}|<>]+:?|[,;:()\[\]{}|<>]")      # _take_name's tokens
_SLOT_RANK = {"ABSENT": 0, "OTHER": 1, "RELATION": 2, "PARTIAL": 3, "OWNER": 4}


def _tok_letters(w: str) -> str:
    """A token's letters (and OCR look-alike digits), upper case - nothing dropped (no title removal)."""
    s = unicodedata.normalize("NFKD", w or "").encode("ascii", "ignore").decode().upper()
    return re.sub(r"[^A-Z0-9|]", "", s)


def _name_token(w: str) -> bool:
    """Can this token be part of a name value? _take_name's own test: no digit, not a relation, not a stop
    word, letters (with . ' -) only."""
    if w in ",;:()[]{}|<>" or re.search(r"\d", w) or _RELATION_STOPS.match(w.rstrip(":")):
        return False
    if not w.strip(".:'\"-_") or _is_stop(w):
        return False
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z.'\-]*", w.strip(".:")))


def _whole_value(toks: List[Tuple[str, int, int]], i: int, j: int, floor: int, max_words: int = 6) -> str:
    """The whole value of the row holding the run toks[i..j]: name tokens extended to the left down to the
    label (absolute position `floor`) and to the right up to the next label, stop word, digit or mark."""
    a = i
    while a - 1 >= 0 and toks[a - 1][1] >= floor and _name_token(toks[a - 1][0]) \
            and not (toks[a - 1][0].endswith(":") and not _HONOR_KEEP.match(toks[a - 1][0])) \
            and (j - a + 2) <= max_words:
        a -= 1
    b = j
    if not (toks[b][0].endswith(":") and not _HONOR_KEEP.match(toks[b][0])):
        while b + 1 < len(toks) and _name_token(toks[b + 1][0]) and (b + 2 - a) <= max_words:
            b += 1
            if toks[b][0].endswith(":") and not _HONOR_KEEP.match(toks[b][0]):
                break                                 # "NAME: next label"
        while b > j and _HONOR_KEEP.match(toks[b][0]):
            b -= 1                                    # "NAME MD" - the next field's honorific
    return " ".join(t[0].strip(":") for t in toks[a:b + 1])


def owner_slot(text: str, name: str, window: int = 70) -> str:
    """Where `name` (the taxpayer) is printed in `text`, letter for letter as a run of words:
      OWNER    an owner / proprietor / licensee label is the nearest label before it AND that row's WHOLE
               value (from the label to the next label or stop word) is strictly EQUAL to the name
               (critique 3: a name merely contained in a longer owner value is not the owner);
      PARTIAL  it sits in an owner row whose whole value is not equal ("MD <A> <B>" for "<A> <B>"): the
               caller judges best_owner_match with strict.judge;
      RELATION a father / husband / mother / spouse / son-of label is the nearest label;
      OTHER    no label near;   ABSENT  the name's letters are not printed as a run of words."""
    target = strict.letters(name)
    if not target:
        return "ABSENT"
    t = text or ""
    toks = [(m.group(0), m.start(), m.end()) for m in _TOKEN.finditer(t)]
    span = len(strict.words(name)) + 3
    best = "ABSENT"
    for i in range(len(toks)):
        first = _tok_letters(toks[i][0])
        if not first or not target.startswith(first):
            continue
        acc, j = "", i
        for j in range(i, min(len(toks), i + span)):
            acc += _tok_letters(toks[j][0])
            if len(acc) >= len(target):
                break
        if acc != target:
            continue
        start = toks[i][1]
        before = t[max(0, start - window): start]
        base = max(0, start - window)
        o = [m.end() for m in OWNER_RE.finditer(before)]
        r = [m.end() for m in RELATION_RE.finditer(before)]
        if o and (not r or max(o) > max(r)):
            value = _whole_value(toks, i, j, base + max(o))
            if strict.compare(value, name, dots=False) == "EQUAL":
                return "OWNER"
            kind = "PARTIAL"
        else:
            kind = "RELATION" if r else "OTHER"
        if _SLOT_RANK[kind] > _SLOT_RANK[best]:
            best = kind
    return best


def best_owner_match(taxpayer: str, owners: List[str]) -> Tuple[str, str]:
    """(kind, owner) of the owner candidate closest to the taxpayer by strict.compare (letters, dots
    aside): EQUAL first, then the difference kinds in strict.KINDS order. ("EMPTY", "") for none."""
    i, kind = strict.best_match(taxpayer, list(owners or []), dots=False)
    return (kind, owners[i]) if i >= 0 else ("EMPTY", "")


def taxpayer_from_tin(result: Optional[Dict[str, Any]], tin_unit: Optional[DocUnit] = None) -> Dict[str, Any]:
    """tin_taxpayer()'s shape from a tin.check_tin_certificate result (the QR first, A6 i), with the run
    pages: qr_page / printed_page are 0-based RUN pages when tin_unit is given."""
    r = result or {}
    qr = r.get("qr") or {}
    pf = r.get("printed") or {}
    printed = pf.get("name") or pf.get("certify_name") or ""
    qn = qr.get("name", "")
    up = list(tin_unit.pages) if tin_unit is not None else []
    # a unit that holds a trade licence and then the TIN certificate: an unknown page is the first TIN page,
    # not the licence's
    first_tin = next((k for k, t in enumerate(r.get("page_types") or []) if t == "tin"), 0)

    def runp(k):
        k = first_tin if k is None else k
        return up[k] if 0 <= k < len(up) else (up[0] if up else None)

    name_row = next((f for f in (r.get("findings") or []) if f.get("rule") == "tin.qr_name"), {})
    out: Dict[str, Any] = {"qr": qn, "printed": printed, "qr_fields": qr, "printed_fields": pf,
                           "qr_page": runp(r.get("qr_page")), "printed_page": runp(name_row.get("page"))}
    if qn:
        out.update(name=qn, source="QR", agree=strict.compare(qn, printed, dots=False) if printed else "NOT_READ")
    elif printed:
        out.update(name=printed, source="PRINTED", agree="NO_QR")
    else:
        out.update(name="", source="", agree="NONE")
    return out


def _as_taxpayer(taxpayer: Any, tin_unit: Optional[DocUnit]) -> Dict[str, Any]:
    if not taxpayer:
        return {"name": "", "source": ""}
    if "source" in taxpayer:
        return dict(taxpayer)
    return taxpayer_from_tin(taxpayer, tin_unit)            # a check_tin_certificate result


@dataclass
class _Side:
    """One side of a chain comparison."""
    name: str
    what: str                                     # "taxpayer", "trade licence owner", "employee", "bank account holder"
    machine: bool = False                         # a QR read: its letters are confirmed (A1)
    pages: List[int] = field(default_factory=list)   # 0-based run pages printing this spelling (OCR)
    suspect: bool = False                         # OCR visibly garbled: never confirmed


def _pages_of(unit: Optional[DocUnit], text: Union[str, Sequence[str], None], name: str,
              extract: Callable[[str], List[str]]) -> List[int]:
    """Run pages of `unit` on which `extract(page text)` gives `name` letter for letter (per-page texts
    only; a joined text gives the unit's first page)."""
    if unit is None or not unit.pages:
        return []
    if isinstance(text, (list, tuple)):
        hits = [unit.pages[k] for k, pt in enumerate(text) if k < len(unit.pages)
                and any(strict.compare(name, c, dots=False) == "EQUAL" for c in extract(pt or ""))]
        return hits or [unit.pages[0]]
    return [unit.pages[0]]


def _joined(text: Union[str, Sequence[str], None]) -> str:
    if isinstance(text, (list, tuple)):
        return "\n".join(t or "" for t in text)
    return text or ""


def chain_findings(taxpayer: Dict[str, Any], tin_unit: Optional[DocUnit], owner_units: List[DocUnit],
                   owner_texts: Dict[str, Any], holder: Optional[Any], bank_unit: Optional[DocUnit],
                   ident: Identity, covered: Callable[[str, str], bool],
                   confirm: Callable[[int, str], str]) -> Dict[str, List[Finding]]:
    """The chain (A7): taxpayer = holder = owner/employee, every comparison
    strict.judge(strict.compare(a, b, dots=False), covered(a, b), confirmed) with confirmed = both sides
    confirmed (a QR name is a machine read; an OCR read needs a second page or `confirm`).
    taxpayer: tin_taxpayer()'s dict or a tin.check_tin_certificate result (the QR first); owner_texts:
    uid -> the unit's text (a string, or its page texts as a list - then the reading page is known);
    holder: bank.BankOutcome.holder (name, page = index in the bank unit's pages, ocr_suspect).
    Returns uid -> findings. Every finding goes to the TIN unit (else the first owner/employee unit, else
    the bank unit; with none of them uid "" = the cross line only); a finding about an owner/employee
    unit is echoed to that unit."""
    covered = covered or (lambda a, b: False)
    confirm = confirm or (lambda i, v: "unclear")
    tp = _as_taxpayer(taxpayer, tin_unit)
    t = tp.get("name") or ""
    primary = tin_unit or (owner_units[0] if owner_units else None) or bank_unit
    puid = primary.uid if primary is not None else ""
    out: Dict[str, List[Finding]] = {}
    asked: Dict[Tuple[int, str], str] = {}

    def put(f: Finding, *extra: Optional[DocUnit]) -> None:
        for uid in [puid] + [u.uid for u in extra if u is not None and u.uid != puid]:
            out.setdefault(uid, []).append(f)

    def ok(s: _Side) -> bool:
        if s.machine:
            return True
        if s.suspect or not strict.letters(s.name):
            return False
        if len(set(s.pages)) >= 2:
            return True
        for p in s.pages[:1]:
            key = (p, s.name)
            if key not in asked:
                asked[key] = confirm(p, s.name)
            return asked[key] == "confirmed"
        return False

    home = list(primary.pages) if primary is not None else []

    def judge(a: _Side, b: _Side, rule: str, extra: Optional[DocUnit] = None) -> Finding:
        kind = strict.compare(a.name, b.name, dots=False)
        pages = tuple(sorted({page_no(p) for p in a.pages + b.pages}))
        # folder mode: the two sides may lie in two files; run_pages lets the report name each page's file
        ev: Dict[str, Any] = {"kind": kind, "a": a.name, "b": b.name, "a_machine": a.machine, "b_machine": b.machine,
                              "run_pages": sorted(set(a.pages + b.pages))}
        if kind == "EQUAL":
            return Finding(PASS, rule, f"the {a.what} ({a.name}) is the {b.what}", SOURCE, pages, ev)
        if kind == "EMPTY":
            return Finding(FLAG, rule, f"the {b.what}'s name could not be read — compare it with the {a.what} by eye",
                           SOURCE, pages, ev)
        if covered(a.name, b.name):
            return Finding(NOTE, rule, f"the {a.what} ({a.name}) and the {b.what} ({b.name}) are written differently "
                                       f"({KIND_TEXT.get(kind, kind)}); an affidavit covers it", SOURCE, pages, ev)
        both = ok(a) and ok(b)                       # the machine side first: a re-read only when it can matter
        ev["confirmed"] = both
        ev["rereads"] = {f"{k[0]}": v for k, v in asked.items() if k[1] in (a.name, b.name)}
        if strict.judge(kind, False, both) == FAIL:
            return Finding(FAIL, rule, f"the {a.what} ({a.name}) is not the {b.what} ({b.name}): "
                                       f"{KIND_TEXT.get(kind, kind)}; no affidavit covers it", SOURCE, pages, ev)
        ocr_side = b if (a.machine or not b.machine) else a
        pg = f", {page_in(ocr_side.pages[0], home)}" if ocr_side.pages else ""
        why = ""
        if any(v == "refuted" for k, v in asked.items() if k[1] in (a.name, b.name)):
            why = "; a closer read shows another spelling — probably OCR"
        return Finding(FLAG, rule, f"the {a.what} ({a.name}) and the {b.what} ({b.name}) are not written the same "
                                   f"({KIND_TEXT.get(kind, kind)}) — check by eye{pg}{why}", SOURCE, pages, ev)

    tin_pages = list(tin_unit.pages) if tin_unit is not None else []
    if t:
        if tp.get("source") == "QR":
            tside = _Side(t, "taxpayer", machine=True, pages=[p for p in [tp.get("qr_page")] if p is not None])
        else:
            pp = tp.get("printed_page")
            tside = _Side(t, "taxpayer", pages=[pp] if pp is not None else tin_pages[:1])
    if tin_unit is not None and not t:
        put(Finding(FLAG, "financial.taxpayer_read", "the TIN certificate's taxpayer name could not be read (QR or "
                                                     "print)", SOURCE, tuple(page_no(p) for p in tin_pages)))
    if t and tp.get("source") == "PRINTED":
        put(Finding(FLAG, "financial.taxpayer_print_only", "the TIN QR could not be read: the taxpayer name is taken "
                                                           "from the print", SOURCE, tuple(page_no(p) for p in tside.pages)))
    hside = None
    if holder is not None and getattr(holder, "name", ""):
        hp = getattr(holder, "page", None)
        hrun = bank_unit.pages[hp] if (bank_unit is not None and hp is not None and 0 <= hp < len(bank_unit.pages)) \
            else (bank_unit.pages[0] if bank_unit is not None and bank_unit.pages else None)
        hside = _Side(holder.name, "bank account holder", pages=[hrun] if hrun is not None else [],
                      suspect=bool(getattr(holder, "ocr_suspect", False)))

    for u in owner_units or []:
        raw = owner_texts.get(u.uid, "") if owner_texts else ""
        text = _joined(raw)
        if u.fine == "employment_certificate":
            what, rule = "employee", "financial.taxpayer_employee"
            found = [employee_name(text)] if employee_name(text) else []
            none_msg = "no employee name found on the employment certificate"
            extract = lambda s: [employee_name(s)]                      # noqa: E731
        else:
            what, rule = "trade licence owner", "financial.taxpayer_owner"
            found = owner_candidates(text)
            none_msg = "no owner name found on the trade licence"
            extract = owner_candidates
        if t:
            if u.fine != "employment_certificate" and owner_slot(text, t) == "OWNER":
                put(Finding(PASS, rule, f"the taxpayer ({t}) is named in the trade licence's owner row", SOURCE,
                            tuple(page_no(p) for p in _pages_of(u, raw, t, owner_candidates))), u)
            elif not found:
                put(Finding(FLAG, rule, none_msg, SOURCE, tuple(page_no(p) for p in u.pages)), u)
            else:
                kind, who = best_owner_match(t, found)
                put(judge(tside, _Side(who, what, pages=_pages_of(u, raw, who, extract)), rule), u)
        elif hside is not None:
            # no taxpayer read: the holder against the owner / employee directly (critique 11)
            if not found:
                put(Finding(FLAG, "financial.holder_owner", none_msg, SOURCE, tuple(page_no(p) for p in u.pages)), u)
            else:
                kind, who = best_owner_match(hside.name, found)
                put(judge(hside, _Side(who, what, pages=_pages_of(u, raw, who, extract)),
                          "financial.holder_owner"), u)
    if t:
        if hside is None and bank_unit is None:
            # no bank document at all: its MISSING line carries it; nothing to flag here (integration, test 1 T5)
            put(Finding(NOTE, "financial.taxpayer_holder", "the bank holder could not be compared: there is no bank "
                                                           "solvency / statement (see its MISSING line)", SOURCE,
                        tuple(page_no(p) for p in tside.pages)))
        elif hside is None:
            put(Finding(FLAG, "financial.taxpayer_holder", "the bank holder could not be compared (no solvency holder "
                                                           "read)", SOURCE, tuple(page_no(p) for p in tside.pages)))
        else:
            put(judge(tside, hside, "financial.taxpayer_holder"))
    return out


def chain_person_reads(taxpayer: Dict[str, Any], tin_unit: Optional[DocUnit], owner_units: List[DocUnit],
                       owner_texts: Dict[str, Any], ident: Identity) -> List[Tuple[str, str, str, int]]:
    """(person, uid, written, run page) for the chain's OCR reads whose letters EQUAL a parent's reference
    (ident.father / father_nid, mother / mother_nid): the printed taxpayer name and the owner / employee
    names. For the names dot rule only (A2). A QR name is a machine read: it carries no dot evidence."""
    refs: List[Tuple[str, str]] = []
    for person in ("father", "mother"):
        for ref in (getattr(ident, person, None), getattr(ident, person + "_nid", None)):
            if ref is not None:
                txt = ref.written or " ".join(ref.parts or ())
                if strict.letters(txt):
                    refs.append((person, txt))

    def who(name: str) -> str:
        return next((p for p, r in refs if strict.compare(name, r, dots=False) == "EQUAL"), "")

    out: List[Tuple[str, str, str, int]] = []
    tp = _as_taxpayer(taxpayer, tin_unit)
    printed = tp.get("printed") or ""
    if printed and tin_unit is not None and who(printed):
        pp = tp.get("printed_page")
        out.append((who(printed), tin_unit.uid, printed, pp if pp is not None else tin_unit.pages[0]))
    for u in owner_units or []:
        raw = owner_texts.get(u.uid, "") if owner_texts else ""
        text = _joined(raw)
        if u.fine == "employment_certificate":
            names, extract = ([employee_name(text)] if employee_name(text) else []), (lambda s: [employee_name(s)])
        else:
            names, extract = owner_candidates(text), owner_candidates
        for n in names:
            p = who(n)
            if p and u.pages:
                out.append((p, u.uid, n, _pages_of(u, raw, n, extract)[0]))
    return out


def income_tax_findings(unit: DocUnit, text: str, tin_text: str, program: str) -> List[Finding]:
    """income_tax.tax_office (A6 j, NEW, FLAG only, Q14): the tax circle / zone the return (or its
    acknowledgement) names against the TIN certificate's. Both circles read and different, or both zones
    read and different -> FLAG; equal -> PASS; unread on either side -> no row. A FLAG for every program
    (a fraud signal), so a non-Master's income-tax line is PRESENT unless this fires."""
    from . import tin as Q
    a, b = Q.tax_office(text or ""), Q.tax_office(tin_text or "")
    both = [k for k in ("circle", "zone") if a.get(k) and b.get(k)]
    if not both:
        return []
    pages = tuple(page_no(p) for p in (unit.pages if unit is not None else []))
    ev = {"return": a, "tin": b, "compared": both, "new_check": True, "program": program}
    if any(int(a[k]) != int(b[k]) for k in both):
        return [Finding(FLAG, "income_tax.tax_office", f"the return names circle {a.get('circle') or '?'}, zone "
                                                       f"{a.get('zone') or '?'}; the TIN certificate circle "
                                                       f"{b.get('circle') or '?'}, zone {b.get('zone') or '?'} — check "
                                                       "the tax office seal and the return by eye", SOURCE, pages, ev)]
    return [Finding(PASS, "income_tax.tax_office", "same tax circle/zone as the TIN certificate", SOURCE, pages, ev)]
