"""The bank document: who holds the account, and the solvency-page rules (RUNNER_SPEC 5.9.2, owner
decision A7 and critique items 5, 10 and 15 of 27 Oct 2026).

Part 1 is the extractor section of the lab's bank_holder.py, copied verbatim (its lines 20-307) except for the
descriptor words added to _STOP_AFTER (review 2). The lab file's own description of it:

    Who holds the bank account - read from the solvency certificate (primary) and the statement header
    (secondary), with no portal data. The owner (27 Oct 2026): "for bank be extra careful picking original
    owners name". The holder is the person the certificate is ABOUT ("This is to certify that <HOLDER>,
    S/O <X> ..."), never: a name in a son-of / daughter-of / wife-of / father-of / care-of clause (S/O,
    D/O, W/O, F/O, C/O and their OCR spellings); the holder's own "Father's Name: / Mother's Name: /
    Spouse" fields; the student named in a purpose clause or a transaction narration; a nominee, a signing
    officer or a branch manager; an address that OCR put where the name should be. When the name cannot
    be read with confidence, NOTHING is returned (the caller FLAGs "check by eye"): a wrong name is worse
    than no name.

Part 2 is the runner's rules (check_bank_unit, dates_rows): the solvency certificate must be there, its
holder must be named on it and be the student's father or mother, letter for letter (A1; dots go to the
names dot rule), against the birth certificate OR that parent's role-confirmed NID, for EVERY joint name;
the dates, the USD figure and the seal are judged on the solvency page only. The minimum balance, the
opening balance and the one-page rule stay the bot's own rows (doccheck).

Nothing here reads a file, the portal or the network (the seal check reads the one-page PDF the caller
cuts). No personal data in this file.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

# --- vocabulary ------------------------------------------------------------------------
# Honorifics written before a name (kept in the returned name; name_tokens drops them).
_HON = r"(?:mr|mrs|ms|miss|md|mst|most|mohd|sk|alhaj|al-haj|haji|hazi|hajee|dr|engr|late|jms|mrjmrs)"
_LEAD_HON = re.compile(r"^(?:\s*(?:" + _HON + r")(?![a-z])\s*[.:,;_/|\\-]*\s*)+", re.I)

# "certify that" and its OCR spellings: certiry, certily, cerlily, ccrtit,v, eertify,
# certifii, certifu, certiff, certifj; "that" as thct / thot / thal / (hat / Ihal.
_THAT = r"(?:(?:that|thct|thot|thal|thaL|th[ae]t|\(hat|Ihal|Ihat|Lhal|Lhat|thz9|\{hxt)[tlL]?|tha\W)"
_CERTIFY = re.compile(
    r"(?:(?:\b(?:c[ce]r[tl][il1!][tflr][a-z,]{0,3}|e[ce]r[tl][il1][tflr][a-z,]{0,3}"
    r"|c[ce]n[tl][il1][tf][yiv]|certif[a-z]*)"
    r"|\bthis\s+[il1][s5u9]\s+\S{1,3}\s+\S{3,10})"
    r"\s*[,;:]?\s*" + _THAT +
    r"|pleasure\s+to\s+introduce)", re.I)

# What ends the holder's name in the certify clause.
_RELATION = (
    r"(?<![a-z])[sdwfcmh9]\s*[/\\|'IlJ]\s*[o09](?![a-z])"       # S/O D/O W/O F/O C/O M/O H/O + OCR
    r"|(?<![a-z])[sdwfc]\s+[o09]\s*[-.:]"                         # "S 0-", "S O."
    r"|\b(?:son|daughter|wife|husband|spouse|sonldaughter|guardian|nominee|brother|sister)\b"
    r"|\bfa[lt][a-z]{1,3}r(?:'|’|\$)?s?\b"                        # father / falher / fallar / fathcr
    r"|\bmo[lt]h[a-z]{0,2}r(?:'|’|\$)?s?\b"                       # mother / molher
    r"|\b[a-z]*[rt]h[ce]r'?s\b"                                   # "Irathcr's"
)
_STOP_AFTER = (
    r"\b(?:having|address|addrcss|addres|adress|residing|resident|residence|located|locatcd|present|"
    r"permanent|mailing|vill|village|holding|house|care|nid|national|passport|birth|dob|tin|"
    r"bearing|account|accounts|a/c|has|have|hus|is|are|was|been|bccn|becn|maintain\w*|holds?|"
    r"who|whose|valued|named|research|student|officer|teacher|businessman|proprietor|director|"
    r"chairman|employee|customer|client|in|at|of|ol|from|with|for|under|"
    # runner addition (review 2): descriptors printed after the holder's name ("<NAME> Freedom Fighter",
    # "<NAME> Retired Govt. employee") end it, so they never count as extra name words
    r"freedom|fighter|retired|retd|ex|former|govt|government|service|serviceholder|servant|farmer|doctor|"
    r"engineer|advocate|lawyer|banker|merchant|trader|contractor|employed|worker|pensioner|expatriate|"
    r"housewife|homemaker|unemployed|private|job|jobholder)\b"
)
_NAME_LABEL = re.compile(r"\b(?:name|nane|namc|nume|naine|anie|narne|namne|nanie)\b", re.I)
_END = re.compile(r"[,;(\[{<>\"“”]|(?<![A-Za-z])\d|\s[-–]\s|" + _RELATION + r"|" + _STOP_AFTER, re.I)

# Words that can never be part of a person's name here.
_BAD = {
    "the", "following", "customer", "customers", "person", "persons", "client", "account", "holder",
    "bank", "plc", "ltd", "limited", "branch", "this", "that", "certify", "concern", "whom", "savings",
    "saving", "deposit", "balance", "road", "ward", "union", "upazila", "thana", "district", "dist",
    "post", "office", "sadar", "bazar", "market", "city", "village", "vill", "house", "holding",
    "flat", "floor", "block", "sector", "lane", "area", "bangladesh", "dhaka", "certificate",
    "solvency", "statement", "name", "address", "type", "currency", "status", "number", "date",
    "period", "phone", "mobile", "regular", "special", "taka", "bdt", "usd", "total", "amount",
    "manager", "officer", "signature", "authorized", "authorised", "principal", "head", "nominee",
    "trade", "traders", "enterprise", "store", "company", "corporation", "agency", "industries",
    # a pronoun or adjective that OCR left in the name slot ("our valued client")
    "our", "his", "her", "their", "valued", "esteemed", "said", "above", "mentioned",
    # business names: the holder of such an account is a proprietor, which a person must check
    "enterprises", "brothers", "trading", "agro", "farm", "center", "centre", "shop", "mart",
    "bhandar", "bitan", "telecom", "electronics", "pharmacy", "hardware", "garments", "textile",
    "textiles", "motors", "builders", "construction", "foods", "poultry", "fisheries", "jewellers",
    "group", "international", "associates", "society", "samity", "foundation",
}
# A one-word "name" with a place ending is an address that OCR put in the name slot.
_PLACE_END = re.compile(r"(?:para|pur|ganj|gonj|nagar|gram|bari|bazar|kandi|danga|hati|khali|tola|tala|"
                        r"chor|char|ghat|hat)$", re.I)


@dataclass
class Holder:
    name: str                     # as printed (cleaned), honorifics kept
    source: str                   # "solvency:certify", "solvency:label", "statement:<layout>"
    page: Optional[int] = None
    ocr_suspect: bool = False     # a token carries digits/symbols: match it, but never FAIL on it
    notes: List[str] = field(default_factory=list)
    joint: List[str] = field(default_factory=list)   # "<A> and <B>": every holder; the caller FLAGs


# --- helpers ---------------------------------------------------------------------------
def _clean(raw: str) -> str:
    s = re.sub(r"[\"“”'`_|~*#^]+", " ", raw or "")
    s = re.sub(r"\s+", " ", s).strip(" .,:;-/\\")
    return s


def _core_tokens(name: str) -> List[str]:
    """The words of a name that are not honorifics (case kept)."""
    rest = _LEAD_HON.sub("", name)
    toks = [t for t in re.split(r"[\s.]+", rest) if t]
    return [t for t in toks if not re.fullmatch(_HON, t, re.I)]


def _valid(name: str) -> Optional[str]:
    """`name` when it can be a person's name, else None: 1-6 words besides honorifics, no
    bank/address/business word, not a lone place name, at least one vowel."""
    core = _core_tokens(name)
    if not core or len(core) > 6:
        return None
    if re.match(r"\W*m\s*/\s*s\b", name, re.I):          # "M/S <business>": a firm, not a person
        return None
    letters = [re.sub(r"[^A-Za-z]", "", t) for t in core]
    if any(len(t) < 2 for t in letters) and len(core) == 1:
        return None
    if sum(len(t) for t in letters) < 3:
        return None
    if any(t.lower() in _BAD or re.match(r"acc[o0]?u|acn?o$", t, re.I) for t in letters):
        return None
    if len(core) == 1 and _PLACE_END.search(letters[0]):
        return None
    if not any(re.search(r"[aeiouy]", t, re.I) for t in letters):
        return None
    return name


def _suspect(name: str) -> bool:
    """A token OCR visibly garbled: digits or symbols inside it, a capital inside a
    lower-case run ("BEGaM", "dN"), or a dot between capitals ("MOZ.I.IMDER")."""
    for t in _core_tokens(name):
        if re.search(r"[^A-Za-z.\-]", t) or re.search(r"[a-z][A-Z]|[A-Z]{2}[a-z][A-Z]", t):
            return True
    return bool(re.search(r"[A-Z]\.[A-Z]\.[A-Z]{2}", name))


def _cut_name(window: str) -> str:
    """The name at the start of `window`: everything up to the first terminator."""
    w = window.lstrip(" \t\n,;:.\"'“”-_")
    m = _LEAD_HON.match(w)
    head, rest = (w[:m.end()], w[m.end():]) if m else ("", w)
    end = _END.search(rest)
    cut = rest[:end.start()] if end else rest[:80]
    # "<NAME> Father's Name: ..." -> drop the word before a Name label
    lab = _NAME_LABEL.search(cut)
    if lab:
        cut = re.sub(r"\S+\s*$", "", cut[:lab.start()])
    # a period followed by a capitalised word that is not an initial ends the name
    cut = re.split(r"\.\s+(?=[A-Z][a-z]{2,}\s+(?:of|Name)\b)", cut)[0]
    name = _clean(head + cut)
    # a lone trailing letter is the start of a relation OCR broke ("<NAME> D", "<NAME>: S")
    name = re.sub(r"(?:[\s:;.]+[A-Za-z])+$", "", name)
    return _clean(name)


# --- solvency certificate -------------------------------------------------------------
_SOLV_CONTEXT = re.compile(r"solven|balance|maintain|account|a/c|deposit|saving|bank|financially", re.I)
_TABLE_LABELS = (
    re.compile(r"acc[o0]u\w*\s*holder\W{0,3}s?\s*na\w{1,3}\s*[:\-]?\s*(.{3,60}?)(?=\s+(?:acc[o0]u\w*|currency|type|a/c)\b)", re.I),
    re.compile(r"acc[o0]u\w*\s*title\s*[:\-]?\s*(.{3,60}?)(?=\s+(?:acc[o0]u\w*|currency|type|a/c|holder)\b)", re.I),
)
_NAMED_AGAIN = re.compile(r"(?:in\s+the\s+name\s+(?:&\s*style\s+)?of|named\s*:)\s*[\"“']?(.{3,60}?)(?=[\"”',:;]|\s+(?:bearing|with|has|at|in)\b)", re.I)


def _certify_holder(text: str) -> Optional[Holder]:
    for m in _CERTIFY.finditer(text or ""):
        around = text[max(0, m.start() - 600): m.end() + 600]
        if not _SOLV_CONTEXT.search(around):
            continue
        window = text[m.end(): m.end() + 160]
        if re.match(r"\W*(?:the\s+following|following|below)\b", window, re.I):
            return _label_holder(text, "solvency:table")
        # "certify that our valued client Mr. X" / "that the account holder Mr. X"
        window = re.sub(r"^\W*(?:our|the|a|an)\s+(?:valued\s+|esteemed\s+)?"
                        r"(?:client|customer|account\s*holder|depositor)s?\s*[,:\-]?\s*", "", window, flags=re.I)
        again = _NAMED_AGAIN.search(text[m.end():m.end() + 900])
        name = _cut_name(window)
        parts = [p for p in re.split(r"\s+(?:and|&)\s+|\s*&\s*", name) if p]
        if len(parts) > 1:                   # "Mr. A and Mrs. B": a joint account
            if all(_valid(p) for p in parts):
                h = Holder(parts[0], "solvency:certify", ocr_suspect=any(_suspect(p) for p in parts))
                h.joint = parts
                h.notes.append("joint account: " + " | ".join(parts))
                return h
            return None
        if _valid(name):
            h = Holder(name, "solvency:certify", ocr_suspect=_suspect(name))
            # the certificate names the holder twice ("in the name of X"): kept as a note for a
            # self-consistency check, never as the answer
            if again:
                h.notes.append("named again: " + _clean(again.group(1)))
            return h
        # the subject could not be read, but "maintaining an account in the name of X" still
        # says whose account it is
        if again and _valid(_clean(again.group(1))):
            n2 = _clean(again.group(1))
            return Holder(n2, "solvency:in-the-name-of", ocr_suspect=_suspect(n2))
        return None          # a certify clause whose name cannot be read: do not guess
    return None


def _label_holder(text: str, source: str) -> Optional[Holder]:
    for rx in _TABLE_LABELS:
        m = rx.search(text or "")
        if m:
            name = _clean(m.group(1))
            if _valid(name):
                return Holder(name, source, ocr_suspect=_suspect(name))
    return None


def holder_from_solvency(text: str) -> Optional[Holder]:
    """The account holder named in ONE solvency certificate's text, or None.

    1. "This is to certify that <HOLDER> ..." — the name runs up to the first relation
       (S/O, D/O, W/O, F/O, C/O, son/daughter/wife/father/mother of, Spouse, Father's Name),
       address/ID word (having, address, residing, located, of, NID ...), verb (has been,
       is, maintaining), comma, digit or bracket.
    2. "certify that the following customer ..." — the labelled field "Account Holder's
       Name" / "Account Title", only when the next label closes the value.
    Nothing else: a name found anywhere else on the page is never taken as the holder."""
    h = _certify_holder(text)
    if h is None and not _CERTIFY.search(text or ""):
        h = _label_holder(text, "solvency:label")
    return h


def is_solvency_page(page: str) -> bool:
    t = page or ""
    return bool(_CERTIFY.search(t) and _SOLV_CONTEXT.search(t)
                and not re.search(r"family\s+certificate|warish|heirship", t, re.I))


def holders_from_solvency_pages(pages: List[str]) -> List[Holder]:
    """One Holder per solvency certificate in a bank file (a file may carry two)."""
    out: List[Holder] = []
    for i, p in enumerate(pages or []):
        if not is_solvency_page(p):
            continue
        h = holder_from_solvency(p)
        if h:
            h.page = i
            out.append(h)
    return out


# --- statement ------------------------------------------------------------------------
_N = r"((?:[A-Za-z][A-Za-z.'`)]*[\s,]+){0,6}?[A-Za-z][A-Za-z.'`)]*)"
_NEXT = (r"(?:A\s*[/I|J]?\s*C\b|[NAJ][/I|J]{0,2}C\b|AIC\b|AJC\b|NJC\b|Alc\b|Account|Acc[o0]u|Address|Addrcss|Addross|"
         r"Customer|Cust\b|Type|Currency|Curency|Phone|Branch|Opening|Status|Period|Mobile|Product|Date|"
         r"STANDARD|Statement|Interest|\d)")
_STATEMENT_LAYOUTS = (
    ("label", re.compile(r"(?:A\s*[/I|J]?\s*C|NC|AIC|AJC|Account|Acc[o0]unt)\s*(?:Name|Namc|Nane|Title|Holder\W{0,3}s?\s*Name)"
                         r"\s*[:\-]?\s*" + _N + r"(?=\s+" + _NEXT + r")", re.I)),
    ("customer-name", re.compile(r"Customer\s*Name\s*[:\-]?\s*" + _N + r"(?=\s+" + _NEXT + r")", re.I)),
    ("name-ac", re.compile(r"\bNam[ec]\s*[:\-]?\s*" + _N +
                           r"(?=\s+(?:A\s*[/I|J]?\s*C\b|[NAJ][/I|J]{0,2}C\b|AIC\b|AJC\b|NJC\b|Customer\s*ID|Address|Addrcss))", re.I)),
    # Pubali: "A/C NUMBER: 1234567890123 <NAME> CUSTOMER ID" (not ONE Bank's "Account Number")
    ("number-name", re.compile(r"(?<!Account\s)(?<!Account)\bNUMBER\s*[:;.]?\s*[\dOoIl|tB]{6,}[\dOoIl|tB\s]*[:;]?\s*" + _N +
                               r"(?=\s+(?:C[UI]{1,2}ST\s?O\s?M|CUST|CURRENT|STATUS))", re.I)),
    ("account-statement", re.compile(r"ACCOUNT\s+STATEMENT\s+(?:Bank\s+)?" + _N +
                                     r"(?=\s+(?:Customer\s*.?\s*[I\[]?D|Account\s+Number))", re.I)),
    ("statement-of-account", re.compile(r"Statement\s+of\s+Account\s+" + _N + r"(?=\s+STANDARD\s+CHARTERED)", re.I)),
    ("opening-date", re.compile(r"Account\s+opening\s+date\s+\S+\s+" + _N + r"(?=\s+Status)", re.I)),
)


def holder_from_statement(text: str) -> Optional[Holder]:
    """The account name printed in a statement's header, or None. Only labelled layouts
    (A/C Name, Account Name/Title, Customer Name, Name ... A/C No, A/C NUMBER <n> <NAME>
    CUSTOMER ID, ACCOUNT STATEMENT <NAME> Customer ID); never a name from a transaction
    narration, a Father/Mother/Spouse field or the address block."""
    # Only the header: transaction narrations carry other people's names ("Acc Name <X>",
    # "TRANSFER TO BKASH <X>"), so the text stops at the first column heading of the table.
    head = (text or "")[:1500]
    cols = re.search(r"\b(?:narration|natration|particulars?|parlicular|description|descri|withdraw\w*|debits?|deblt)\b",
                     head, re.I)
    if cols:
        head = head[:cols.start()]
    for layout, rx in _STATEMENT_LAYOUTS:
        for m in rx.finditer(head):
            # "Father Name X / Mother Name Y / Spouse Z / Nominee / Joint Name": never the holder
            if re.search(r"(?:father|mother|spouse|nominee|joint|guardian|introducer)\W{0,3}s?\s*$",
                         head[max(0, m.start() - 16):m.start()], re.I):
                continue
            raw = m.group(1)
            name = re.split(r"\s+(?:Father|Mother|Spouse|Joint|Nominee)\b", raw, flags=re.I)[0]
            if "," in re.sub(r"^\s*(?:" + _HON + r")\s*[.,]", "", name, flags=re.I):
                continue                 # a comma inside: an address line, not a name
            name = _clean(name)
            if _valid(name):
                return Holder(name, "statement:" + layout, ocr_suspect=_suspect(name))
    return None


def holders_from_statement_pages(pages: List[str]) -> List[Holder]:
    out: List[Holder] = []
    for i, p in enumerate(pages or []):
        if is_solvency_page(p):
            continue
        h = holder_from_statement(p)
        if h:
            h.page = i
            out.append(h)
    return out


# =====================================================================================================
# Part 2 - the runner's rules (RUNNER_SPEC 5.9.2). Everything above this line is bank_holder.py, verbatim.
# =====================================================================================================
from pathlib import Path  # noqa: E402
from typing import Any, Callable, Dict, Sequence, Tuple  # noqa: E402

from . import botimport, strict  # noqa: E402
from .model import FAIL, FLAG, NOTE, PASS, DocUnit, Finding, Identity, NameRef, page_no  # noqa: E402

SOURCE = "runner:bank"
MIN_READ_CHARS = 300            # every bank page read this well and none is a certificate: it is missing
SEAL_PAGES_MAX = 2              # solvency pages whose seal colour is looked at (one-page cuts)
_SIGNATURE_WORDS = re.compile(r"(?i)authori[sz]ed\s+signat|signature|seal")
# USD on the solvency page: one of the bot's R.USD_WORDS printed as such (has_any's exact pass), or a USD
# mark. has_any's fuzzy pass is not used here: it accepts a multi-word phrase on its FIRST word alone, so
# the word "equivalent" ("equivalent to Taka ...") would pass "equivalent to usd".
_USD_MARK = re.compile(r"(?i)(?<![a-z])us[d0o](?![a-z])|dollar")
# What a strict.compare kind means, for the messages (never the word "letters" next to a number: A6 e).
KIND_TEXT = {"EQUAL": "the same", "DOTS": "only the dots differ", "OCR": "a look-alike character differs (I/l/1, O/0)",
             "ORDER": "the same words in another order", "SUBSET": "a word is missing or extra",
             "LETTERS": "one or two characters differ", "DIFFERENT": "a different name", "EMPTY": "not read"}


@dataclass
class BankOutcome:
    findings: List[Finding]
    holder: Optional[Holder]          # the solvency holder used for the chain (None when not read)
    holder_role: str                  # "father" | "mother" | "student" | "other" | ""
    solvency_pages: List[int]         # 0-based run pages that pass is_solvency_page
    solvency_missing: bool            # the bank pages read well (>= 300 characters each) and none is a certificate
    person_reads: List[Tuple[str, str, str, int]] = field(default_factory=list)   # (person, uid, written, page)


def _bot():
    """The bot code, as already loaded by the engine (any cache root), else loaded with the default root."""
    return botimport._loaded if botimport._loaded is not None else botimport.load()


def kind_text(kind: str) -> str:
    return KIND_TEXT.get(kind, kind.lower())


def ref_text(ref: Optional[NameRef]) -> str:
    """A reference name as text: as printed when it was (dots kept), else its words."""
    if ref is None:
        return ""
    return ref.written or " ".join(ref.parts or ())


def _p1(*run_pages: Optional[int]) -> Tuple[int, ...]:
    """The input pages (page of the file in folder mode) of run pages."""
    return tuple(sorted({page_no(p) for p in run_pages if p is not None and p >= 0}))


def names_is_stop(word: str) -> bool:
    """names.STOP membership (imported on use)."""
    from . import names as _names
    return _names.alpha(word).lower() in _names.STOP


def names_not_name_words(name: str, added: Sequence[str]) -> bool:
    """True when every word the holder's name adds to a reference is a word the name reader never takes into a
    name after a name: a names.STOP or names.AFTER_NAME word, a field label ("Program:"), or a word printed in
    another case than the name before it (names.case_breaks: a Title-case word after a capitalised name).
    Imported on use."""
    from . import names as _names

    def follows(w: str) -> bool:
        return names_is_stop(w) or _names.alpha(w).lower() in _names.AFTER_NAME
    if not added:
        return False
    toks = (name or "").split()
    if len(toks) != len(strict.words(name or "")) or len(toks) < len(added):   # glued words: letters only
        return all(follows(w) for w in added)
    head = toks[:len(toks) - len(added)]
    return all(follows(w) or _names.is_label_token(w) or (bool(head) and _names.case_breaks(head, w))
               for w in toks[len(head):])


def names_adds_tail(ref: str, variant: str, tail: Sequence[str]) -> bool:
    """names.adds_tail (imported on use: names imports the bot code)."""
    from . import names as _names
    return _names.adds_tail(ref, variant, tail)


def _parent_refs(ident: Optional[Identity]) -> List[Tuple[str, NameRef]]:
    """(person, reference) for the father's and the mother's two references (A7, critique 10): the
    canonical name (birth certificate; else passport field) and the name on that parent's role-confirmed
    NID. A reference with no letters is left out."""
    out: List[Tuple[str, NameRef]] = []
    if ident is None:
        return out
    for person in ("father", "mother"):
        for ref in (getattr(ident, person, None), getattr(ident, person + "_nid", None)):
            if ref is not None and strict.letters(ref_text(ref)):
                out.append((person, ref))
    return out


def _as_on(person: str, ref: NameRef) -> str:
    src = (ref.source or "").strip()
    if src.lower().endswith("nid"):
        return f"as on the {person}'s NID"
    if src:
        return f"as on the {src}"
    return f"as read for the {person}"


def _is_student(name: str, ident: Optional[Identity]) -> bool:
    """The holder IS the student: letter for letter against the MRZ words (a machine read: any order, no
    dots) or against the passport's printed name (letters only)."""
    if ident is None or ident.student is None:
        return False
    st = ident.student
    if st.parts and strict.compare(name, st.parts, any_order=True) == "EQUAL":
        return True
    return bool(st.written) and strict.compare(name, st.written, dots=False) == "EQUAL"


@dataclass
class _Verdict:
    verdict: str
    message: str
    role: str                         # father | mother | student | other | ""
    name: str
    page: Optional[int]               # 0-based run page of the holder read
    evidence: Dict[str, Any] = field(default_factory=dict)


def _judge_name(n: str, page: Optional[int], ident: Optional[Identity], parents: List[Tuple[str, NameRef]],
                covered: Callable[[str, str], bool], confirm: Callable[[int, str], str],
                other_reads: Sequence[Tuple[str, Optional[int]]], suspect: bool, joint: bool) -> _Verdict:
    """Rule 3 (bank.holder_parent) for one written holder name n read on run page `page`.
    other_reads: the other independent reads of this bank document (statement headers, another solvency
    certificate) as (name, run page) - they confirm n, or show the reference spelling instead."""
    pg = f"page {page_no(page)}" if page is not None else "the solvency page"
    for person, ref in parents:
        if strict.compare(n, ref_text(ref), dots=False) == "EQUAL":
            return _Verdict(PASS, f"account holder {n} is the student's {person} ({_as_on(person, ref)})",
                            person, n, page, {"reference": ref.source, "kind": "EQUAL"})
    if _is_student(n, ident):
        if joint:
            return _Verdict(FLAG, f"the student ({n}) is a holder of this joint account — the holder must be the "
                                  f"father or mother; owner decides (Q2), {pg}", "student", n, page,
                            {"kind": "student"})
        return _Verdict(FAIL, "the account is in the student's own name — the holder must be the father or mother",
                        "student", n, page, {"kind": "student"})
    if not parents:
        return _Verdict(FLAG, f"account holder {n} could not be compared: the father's and the mother's names are "
                              f"not known — check by eye, {pg}", "", n, page, {"kind": "EMPTY"})
    idx, kind = strict.best_match(n, [ref_text(r) for _, r in parents], dots=False)
    person, ref = parents[idx]
    rt = ref_text(ref)
    ev: Dict[str, Any] = {"kind": kind, "reference": ref.source, "reference_name": rt,
                          "reference_confirmed": bool(ref.confirmed), "ocr_suspect": suspect}
    if kind != "EMPTY" and covered(n, rt):
        return _Verdict(NOTE, f"account holder {n} is written differently from the {person}'s name {rt} "
                              f"({kind_text(kind)}); an affidavit covers it", person, n, page, ev)
    # A1 confirmation: the holder read AND the parent reference must both be confirmed, and no read of this
    # bank document may show the reference's letters instead.
    shows_ref = [p for o, p in other_reads if strict.compare(o, rt, dots=False) == "EQUAL"]
    agree = [p for o, p in other_reads if strict.compare(o, n, dots=False) == "EQUAL"]
    # The holder's name EXTENDS the reference at its end ("<A> <B> <C>" against "<A> <B>"): an extra word is a
    # difference like any other (A1, A7); the reference is the birth certificate's whole value (A3), so it is
    # not cut short. Confirmed and uncovered -> FAIL, as an added MD or a dropped word.
    hw, rw = strict.words(n), strict.words(rt)
    extends = kind == "SUBSET" and 0 < len(rw) < len(hw) and tuple(hw[:len(rw)]) == tuple(rw)
    ev["extends_reference"] = extends
    tail = tuple(getattr(ref, "tail", ()) or ())
    tail_hit = extends and names_adds_tail(rt, n, tail)
    # the holder adds other words where the birth certificate's read goes on with its unconfirmed tail: the
    # reference's end is not confirmed, so the difference is "check by eye" (A1, Q19), never FAIL (review 2)
    tail_other = extends and not tail_hit and bool(tail) and len(hw) - len(rw) <= len(tail)
    # an added word the name reader never takes as part of a name (names.STOP, e.g. MEMBER or FIGHTER; a field
    # label; a word printed in another case than the name) cannot show that the reference ends before it: the
    # reference was read up to that word. FLAG, as an unconfirmed tail; never FAIL (review 2).
    stop_hit = extends and not (tail_hit or tail_other) and names_not_name_words(n, hw[len(rw):])
    how, reason = "", ""
    if stop_hit:            # a stop word: the reference was read up to it
        reason = (f"the holder's name adds {' '.join(hw[len(rw):])}, a word the name reader stops at, so the "
                  f"{person}'s name on the {ref.source or 'reference'} may go on with it too")
    elif tail_hit:          # the birth certificate's read goes on with these words too, unconfirmed (NameRef.tail)
        reason = (f"the {ref.source or 'reference'}'s read goes on with {' '.join(tail)} too, but no second read "
                  f"shows it")
    elif tail_other:
        reason = (f"the holder's name adds {' '.join(hw[len(rw):])} where the {ref.source or 'reference'}'s read "
                  f"goes on with {' '.join(tail)}, which no second read shows")
    elif shows_ref:
        reason = f"another page of the bank document (page {page_no(shows_ref[0])}) reads {rt}"
    elif suspect:
        reason = "the read is garbled (digits or symbols inside a word)"
    elif kind != "EMPTY" and ref.confirmed:
        if agree:
            how = f"the same spelling on page {page_no(agree[0])}"
        elif page is not None:
            r = confirm(page, n)
            ev["reread"] = r
            if r == "confirmed":
                how = "confirmed by a closer read"
            elif r == "refuted":
                reason = "a closer read shows another spelling — probably OCR"
    ev["holder_confirmed"] = how
    tail_hit = tail_hit or tail_other or stop_hit
    v = strict.judge(kind, False, bool(how) and bool(ref.confirmed) and not shows_ref and not tail_hit)
    if v == FAIL and kind == "DIFFERENT":
        return _Verdict(FAIL, f"account holder {n} is not the father or the mother (closest: the {person}'s name "
                              f"{rt}; {how}); no affidavit covers it", "other", n, page, ev)
    if v == FAIL:
        return _Verdict(FAIL, f"account holder {n} is not written as the {person}'s name {rt} ({kind_text(kind)}; "
                              f"{how}) — not the father or the mother letter for letter, and no affidavit covers it",
                        "other", n, page, ev)
    tail = f"; {reason}" if reason else ""
    return _Verdict(FLAG, f"account holder {n} is not exactly the {person}'s name {rt} ({kind_text(kind)}) — check "
                          f"by eye, {pg}{tail}", "", n, page, ev)


def _usd_on(text: str, usd_words: Sequence[str]) -> bool:
    t = re.sub(r"\s+", " ", (text or "")).strip().lower()
    return any(w in t for w in usd_words) or bool(_USD_MARK.search(text or ""))


def check_bank_unit(unit: DocUnit, page_texts: List[str], page_index: List[int], ident: Identity,
                    covered: Callable[[str, str], bool], confirm: Callable[[int, str], str],
                    solvency_pdf: Callable[[int], Optional[Path]],
                    page_image: Optional[Callable[[int], Any]] = None) -> BankOutcome:
    """The bank document's rules, in order (RUNNER_SPEC 5.9.2): bank.solvency_present, bank.holder_read,
    bank.holder_parent (every holder, every joint name), bank.dates, bank.usd, bank.seal.
    page_texts: the unit's page texts; page_index: their 0-based run pages (Finding.pages are 1-based run
    pages); covered(a, b): names.covered with the case's affidavit; confirm(run page, written name): the
    shared targeted re-read; solvency_pdf(run page): a one-page PDF of that page (for the seal colour)."""
    texts = [t or "" for t in (page_texts or [])]
    idx = list(page_index) if page_index is not None else list(range(len(texts)))
    if len(idx) < len(texts):
        idx += list(range(len(idx), len(texts)))

    def run(k: Optional[int]) -> Optional[int]:
        return None if k is None else idx[k]

    covered = covered or (lambda a, b: False)
    confirm = confirm or (lambda i, v: "unclear")
    bot = _bot()
    uid = unit.uid if unit is not None else ""
    all_pages = _p1(*idx[:len(texts)])
    out: List[Finding] = []

    # 1. bank.solvency_present
    sol = [k for k, t in enumerate(texts) if is_solvency_page(t)]
    cert = sol or [k for k, t in enumerate(texts) if bot.dv._CERT_WORDS.search(t)]
    if not cert:
        if texts and all(len(t.strip()) >= MIN_READ_CHARS for t in texts):
            out.append(Finding(NOTE, "bank.solvency_present", "none of the bank pages is a solvency certificate — "
                                     "see the BANK SOLVENCY CERTIFICATE MISSING line", SOURCE, all_pages))
            return BankOutcome(out, None, "", [], True, [])
        out.append(Finding(FLAG, "bank.solvency_present", "no solvency certificate page could be read in the bank "
                                 "document — check by eye", SOURCE, all_pages))
        return BankOutcome(out, None, "", [], False, [])
    out.append(Finding(PASS, "bank.solvency_present", f"solvency certificate on page {page_no(run(cert[0]))}", SOURCE,
                       _p1(run(cert[0])), {"pages_by_wording": [run(k) for k in cert], "is_solvency_page": bool(sol)}))

    # 2. bank.holder_read
    hs = holders_from_solvency_pages(texts)
    if not hs:
        out.append(Finding(FLAG, "bank.holder_read", "the account holder's name could not be read on the solvency "
                                 "certificate — check by eye", SOURCE, _p1(*[run(k) for k in cert])))
    for h in hs:
        out.append(Finding(PASS, "bank.holder_read", f"account holder read on the solvency certificate: {h.name}"
                                 + (f" (joint: {' | '.join(h.joint)})" if h.joint else ""), SOURCE, _p1(run(h.page)),
                           {"source": h.source, "ocr_suspect": h.ocr_suspect, "notes": list(h.notes)}))

    # 3. bank.holder_parent - every holder, every joint name
    parents = _parent_refs(ident)
    stm = holders_from_statement_pages(texts)
    person_reads: List[Tuple[str, str, str, int]] = []
    chain_holder: Optional[Holder] = None
    chain_role = ""
    for h in hs:
        names = list(h.joint) if h.joint else [h.name]
        joint = len(names) > 1
        others = [(s.name, run(s.page)) for s in stm] + [(o.name, run(o.page)) for o in hs
                                                          if o is not h and o.page != h.page]
        verdicts = [_judge_name(n, run(h.page), ident, parents, covered, confirm, others, h.ocr_suspect, joint)
                    for n in names]
        for v in verdicts:
            if v.verdict == PASS and v.role in ("father", "mother"):
                person_reads.append((v.role, uid, v.name, v.page))
        parent_ok = [v for v in verdicts if v.verdict == PASS]
        if joint and parent_ok:
            # the holder rule passes when any joint holder is a parent (Q2); a student holder stays FLAG
            for v in verdicts:
                if v.verdict == PASS or v.role == "student":
                    out.append(Finding(v.verdict, "bank.holder_parent", v.message, SOURCE, _p1(v.page), v.evidence))
                else:
                    out.append(Finding(NOTE, "bank.holder_parent", f"joint holder {v.name} is not compared further "
                                             f"(the holder rule passes on {parent_ok[0].name})", SOURCE, _p1(v.page),
                                       v.evidence))
        else:
            for v in verdicts:
                out.append(Finding(v.verdict, "bank.holder_parent", v.message, SOURCE, _p1(v.page), v.evidence))
        if joint:
            out.append(Finding(FLAG, "bank.holder_parent", f"joint account (holders: {' | '.join(names)}) — owner "
                                     "decides (Q2)", SOURCE, _p1(run(h.page)), {"joint": names}))
        # the holder the chain uses: the parent the rule passed on, else this certificate's first holder
        pick = parent_ok[0] if parent_ok else None
        if pick is not None and (chain_holder is None or chain_role not in ("father", "mother")):
            chain_holder = Holder(pick.name, h.source, h.page, h.ocr_suspect, list(h.notes), list(h.joint))
            chain_role = pick.role
        elif chain_holder is None:
            worst = max(verdicts, key=lambda v: {PASS: 0, NOTE: 1, FLAG: 2, FAIL: 3}.get(v.verdict, 2))
            chain_holder = h
            chain_role = worst.role if worst.verdict in (FAIL, NOTE) else ""

    # 4. bank.dates
    out += dates_rows(texts, idx)

    # 5. bank.usd - the solvency page(s) only (critique 15)
    usd_words = list(bot.R.USD_WORDS)
    hit = [k for k in cert if _usd_on(texts[k], usd_words)]
    if hit:
        out.append(Finding(PASS, "bank.usd", f"solvency amount stated in USD (page {page_no(run(hit[0]))})", SOURCE,
                           _p1(run(hit[0]))))
    else:
        out.append(Finding(FLAG, "bank.usd", f"no USD equivalent found on the solvency certificate (page "
                                             f"{page_no(run(cert[0]))})", SOURCE, _p1(run(cert[0]))))

    # 6. bank.seal - the solvency page only (critique 15): a stamp (the seal colour), a handwritten signature (ink
    # strokes, rev 2.5) or signature/seal wording; the message says what was found
    out.append(seal_row(cert, texts, run, solvency_pdf, page_image))
    return BankOutcome(out, chain_holder, chain_role, [run(k) for k in sol], False, person_reads)


def seal_row(cert: List[int], texts: List[str], run: Callable[[Optional[int]], Optional[int]],
             solvency_pdf: Optional[Callable[[int], Optional[Path]]],
             page_image: Optional[Callable[[int], Any]] = None) -> Finding:
    """bank.seal on the solvency page(s) (critique 15, rev 2.5). Looks for a stamp (page_checks.seal_present on the
    one-page cut: a violet / red seal colour), a handwritten signature (ink_signatures on the page image) and the
    printed wording ("signature", "seal", "authorised signatory"). A round stamp's ring seen by its shape (ink_marks)
    is a stamp too, whatever its colour. Any of them: PASS (spec 5.9.2 "seal or signature"), the message saying what
    was found ("handwritten signature found, no stamp"); none: FLAG. A signature without a stamp is PASS by the
    spec's "seal or signature" (owner question listed)."""
    bot = _bot()
    errors: List[str] = []
    found: Dict[str, Any] = {}
    for k in cert[:SEAL_PAGES_MAX]:
        stamp, sigs, rings = False, [], []
        pdf = None
        try:
            pdf = solvency_pdf(run(k)) if solvency_pdf is not None else None
            stamp = bool(pdf is not None and bot.PC.seal_present(Path(pdf)))
        except bot.dv.CODE_FAULTS:
            raise
        except Exception as e:                          # an unreadable cut is no seal; said in the evidence
            errors.append(type(e).__name__)
        try:
            img = page_image(run(k)) if page_image is not None else _render_first(pdf)
            sigs, rings = ink_marks(img)
        except bot.dv.CODE_FAULTS:
            raise
        except Exception as e:
            errors.append(type(e).__name__)
        words = bool(_SIGNATURE_WORDS.search(texts[k]))
        if stamp or sigs or rings or words:
            found = {"page": k, "stamp": stamp, "signatures": sigs, "rings": rings, "wording": words}
            break
        if not found:
            found = {"page": cert[0], "stamp": False, "signatures": [], "rings": [], "wording": False}
    k = found.get("page", cert[0])
    pn = page_no(run(k))
    ev: Dict[str, Any] = {"stamp": found.get("stamp", False), "handwritten_signatures": len(found.get("signatures")
                                                                                            or []),
                          "signature_boxes": list(found.get("signatures") or [])[:6],
                          "stamp_rings": len(found.get("rings") or []),
                          "ring_boxes": list(found.get("rings") or [])[:4],
                          "wording": found.get("wording", False)}
    ev["how"] = ", ".join(w for w, on in (("seal colour", ev["stamp"]), ("stamp ring", ev["stamp_rings"]),
                                          ("ink signature", ev["handwritten_signatures"]),
                                          ("wording", ev["wording"])) if on)
    if errors:
        ev["errors"] = errors
    n_sig = ev["handwritten_signatures"]
    sig_txt = ("handwritten signature" if n_sig == 1 else f"{n_sig} handwritten signatures") if n_sig else ""
    stamp_txt = "stamp" if ev["stamp"] else ("round stamp (its ring's shape)" if ev["stamp_rings"] else "")
    if stamp_txt and n_sig:
        msg = f"{stamp_txt} and {sig_txt} found on the solvency certificate (page {pn})"
    elif stamp_txt:
        msg = f"{stamp_txt} found on the solvency certificate (page {pn})" + (
            "" if ev["wording"] else "; no handwritten signature seen")
    elif n_sig:
        msg = f"{sig_txt} found, no stamp, on the solvency certificate (page {pn})"
    elif ev["wording"]:
        msg = (f"signature / seal wording on the solvency certificate (page {pn}); no stamp or handwritten "
               "signature seen")
    else:
        return Finding(FLAG, "bank.seal", f"no seal or signature found on the solvency certificate (page {pn}) — "
                                          "neither a stamp nor a handwritten signature", SOURCE, _p1(run(k)), ev)
    return Finding(PASS, "bank.seal", msg, SOURCE, _p1(run(k)), ev)


# --- handwritten signatures (rev 2.5) --------------------------------------------------------------------------
# Owner folder 9: a solvency certificate signed by hand by two bank officers, with no stamp and no printed
# "signature" word, got a false "no seal or signature" FLAG (the seal check looks for a stamp's colour only).
# A handwritten signature is ink drawn in thin, curving, connected strokes: once the page's long straight lines are
# taken out and the strokes are joined, it is a blob far taller than the printed words around it, with little ink
# for its box (low fill), lying below the letterhead. Printed words, logos and filled blocks are not.
SIG_DPI = 150
SIG_MIN_H = 1.9                # x the page's median printed word height (joined strokes)
SIG_MIN_W = 2.5
SIG_FILL = (0.03, 0.26)        # ink pixels / box area: signatures 0.11-0.24 (owner folders), a logo or a filled block more
SIG_ASPECT = (0.9, 7.0)        # width / height
SIG_TOP = 0.25                 # the letterhead (logos) is above this share of the page
SIG_MAX_W = 0.45               # x page width
SIG_MIN_WORDS = 20             # printed words needed to measure the page's word height
SIG_MAX_FOUND = 4              # more blobs than this: a noisy scan, not signatures


def ink_signatures(img) -> List[Dict[str, Any]]:
    """Handwritten-signature-like ink blobs on one page image (RGB or BGR uint8, about 150 dpi): [{"box": page
    fractions, "fill", "height_x": its height / the printed word height, "stroke_x": its longest single pen stroke's
    height / the word height}]. Long straight lines (table rules, underlines, the signature line itself) are taken
    out first; blobs of one signature cut apart by a removed line are joined again (_merge_boxes)."""
    return ink_marks(img)[0]


def ink_marks(img) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """(handwritten signatures, round stamps' rings) on one page image (see ink_signatures). A ring is a stamp seen by
    its shape, whatever its ink colour (a black seal on a copy, which the seal-colour test cannot see)."""
    hw, rows = _sig_scan(img)
    out = [r for r in rows if not r["why"]]
    rings = [r for r in rows if r["why"] == "a stamp's ring"]
    if len(out) > SIG_MAX_FOUND:             # a noisy scan (a photo, a texture): nothing is trusted
        return [], []
    out = _merge_boxes(out, hw)
    for r in out + rings:
        r.pop("why", None)
        r.pop("px", None)
    return out, rings[:SIG_MAX_FOUND]


# The longest connected pen stroke (the blob's largest piece of undilated ink), measured on the owner folders' four
# solvency certificates at 150 dpi: the six signatures 1.5-4.5 word heights tall, 3.6-7.8 wide, their own fill
# 0.13-0.24; printed words and name stamps (bold, larger fonts) 0.5-1.5 tall but at most 2.4 wide, or a fill of 0.37+.
SIG_STROKE_H = 1.4             # x the word height (a printed word, ascender to descender, is ~1)
SIG_STROKE_W = 2.5
SIG_STROKE_FILL = 0.27         # that stroke's own ink / its box: a pen line is 0.05-0.25, a bold printed word more
SIG_RING_HOLE = 0.35           # a round stamp's ring: its inner hole / its box (a signature's loops are small)
SIG_MERGE_GAP = 1.0            # x the word height: boxes this close are one signature


def _sig_scan(img) -> Tuple[float, List[Dict[str, Any]]]:
    """(the printed word height in pixels, every large joined-ink blob below the letterhead with its measures and
    "why" it is not a signature ("" when it is one)). Debug and calibration use the rejected rows too."""
    import cv2
    import numpy as np
    if img is None or getattr(img, "ndim", 0) < 2:
        return 0.0, []
    grey = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY) if img.ndim == 3 else img
    H, W = grey.shape[:2]
    thr, _ = cv2.threshold(grey, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    ink0 = (grey < min(float(thr), 170.0)).astype(np.uint8)
    hk = cv2.getStructuringElement(cv2.MORPH_RECT, (max(15, W // 25), 1))
    vk = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(15, H // 25)))
    lines = cv2.morphologyEx(ink0, cv2.MORPH_OPEN, hk) | cv2.morphologyEx(ink0, cv2.MORPH_OPEN, vk)
    ink = ink0 & (1 - lines)
    # a pen stroke crossing a removed line (the signature line) is mended for the stroke measure: the line's pixels
    # right beside the stroke come back, the rest of the line does not
    mended = ink | (lines & cv2.dilate(ink, np.ones((3, 3), np.uint8), iterations=2))
    joined = cv2.dilate(ink, np.ones((3, 3), np.uint8), iterations=2)
    n, lab, st, _ = cv2.connectedComponentsWithStats(joined, 8)
    lab0 = None                                      # the page's own ink, lines kept: for a stamp's ring
    # printed words: 0.6-3% of the page's height (about 10-50 px on an A4 page at 150 dpi)
    hs = [int(st[i, 3]) for i in range(1, n) if max(6.0, H * 0.006) <= st[i, 3] <= H * 0.03 and st[i, 4] >= 30]
    if len(hs) < SIG_MIN_WORDS:              # no printed text to measure against: nothing is judged
        return 0.0, []
    hw = float(np.median(hs))
    rows: List[Dict[str, Any]] = []
    for i in range(1, n):
        x, y, w, h = (int(v) for v in st[i, :4])
        if h < 1.5 * hw or (y + h / 2) / H < SIG_TOP:
            continue
        why = ""
        if h < SIG_MIN_H * hw or w < SIG_MIN_W * hw:
            why = "small"
        elif w > SIG_MAX_W * W or h > 0.2 * H:
            why = "large"
        elif not (SIG_ASPECT[0] <= w / max(h, 1) <= SIG_ASPECT[1]):
            why = "aspect"
        mine = (lab[y:y + h, x:x + w] == i)
        fill = float((ink[y:y + h, x:x + w] * mine).sum()) / float(w * h)
        if not why and not (SIG_FILL[0] <= fill <= SIG_FILL[1]):
            why = "fill"
        # the longest single pen stroke: the largest connected piece of the blob's own (undilated, mended) ink
        part = mended[y:y + h, x:x + w] * mine
        m, plab, pst, _ = cv2.connectedComponentsWithStats(part.astype(np.uint8), 8)
        sx = sw = 0.0
        sfill, hole = 1.0, 0.0
        if m > 1:
            j = 1 + int(np.argmax(pst[1:, 4]))
            px0, py0, pw, ph = (int(v) for v in pst[j, :4])
            sx, sfill = ph / hw, float(pst[j, 4]) / max(float(pw * ph), 1.0)
            sw = pw / hw
            if not why and sx >= SIG_STROKE_H:
                # a stamp's ring (its top and bottom arcs can go with the removed lines): the page's own ink piece
                # under the stroke, lines kept; round with one large inner hole = a ring, not a signature
                if lab0 is None:
                    _, lab0, st0, _ = cv2.connectedComponentsWithStats(ink0, 8)
                ys, xs = np.nonzero(plab == j)
                ids = lab0[ys + y, xs + x]
                ids = ids[ids > 0]
                if ids.size:
                    k0 = int(np.bincount(ids).argmax())
                    qx, qy, qw, qh = (int(v) for v in st0[k0, :4])
                    if 0.75 <= qw / max(qh, 1) <= 1.35 and qh >= SIG_MIN_H * hw:
                        ring = (lab0[qy:qy + qh, qx:qx + qw] == k0).astype(np.uint8)
                        cnts, hier = cv2.findContours(ring, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
                        if hier is not None:
                            holes = [cv2.contourArea(c) for c, hh in zip(cnts, hier[0]) if hh[3] >= 0]
                            hole = max(holes) / float(qw * qh) if holes else 0.0
        if not why and (sx < SIG_STROKE_H or sw < SIG_STROKE_W):
            why = "no long stroke"
        elif not why and sfill > SIG_STROKE_FILL:
            why = "stroke too solid"
        elif not why and hole >= SIG_RING_HOLE:
            why = "a stamp's ring"
        rows.append({"box": [round(x / W, 3), round(y / H, 3), round((x + w) / W, 3), round((y + h) / H, 3)],
                     "fill": round(fill, 3), "height_x": round(h / hw, 1), "stroke_x": round(sx, 1),
                     "stroke_w": round(sw, 1), "stroke_fill": round(sfill, 2), "ring_hole": round(hole, 2), "why": why,
                     "px": (x, y, w, h, W, H)})
    return hw, rows


def _merge_boxes(rows: List[Dict[str, Any]], hw: float) -> List[Dict[str, Any]]:
    """Signature blobs closer than SIG_MERGE_GAP word heights are one signature (a pen stroke cut by the removed
    signature line, or a flourish apart from the name)."""
    items = [dict(r) for r in rows]
    gap = SIG_MERGE_GAP * hw
    changed = True
    while changed:
        changed = False
        for a in range(len(items)):
            for b in range(a + 1, len(items)):
                ax, ay, aw, ah, W, H = items[a]["px"]
                bx, by, bw, bh, _, _ = items[b]["px"]
                dx = max(0, max(ax, bx) - min(ax + aw, bx + bw))
                dy = max(0, max(ay, by) - min(ay + ah, by + bh))
                if dx <= gap and dy <= gap:
                    x0, y0 = min(ax, bx), min(ay, by)
                    x1, y1 = max(ax + aw, bx + bw), max(ay + ah, by + bh)
                    keep = items[a] if items[a]["height_x"] >= items[b]["height_x"] else items[b]
                    keep = dict(keep, px=(x0, y0, x1 - x0, y1 - y0, W, H),
                                box=[round(x0 / W, 3), round(y0 / H, 3), round(x1 / W, 3), round(y1 / H, 3)],
                                height_x=round((y1 - y0) / hw, 1))
                    items = [it for k, it in enumerate(items) if k not in (a, b)] + [keep]
                    changed = True
                    break
            if changed:
                break
    return sorted(items, key=lambda r: (r["box"][1], r["box"][0]))


def _render_first(pdf: Optional[Path], dpi: int = SIG_DPI):
    """Page 1 of a one-page PDF (the solvency cut) as an RGB array, or None."""
    if pdf is None:
        return None
    import numpy as np
    import pymupdf
    with pymupdf.open(str(pdf)) as doc:
        pix = doc[0].get_pixmap(dpi=dpi)
        a = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        return a[:, :, :3].copy()


def dates_rows(page_texts: List[str], page_index: Optional[Sequence[int]] = None) -> List[Finding]:
    """bank.dates: the solvency certificate's date (dv.document_date) against the statement's generation
    date (dv.statement_date), with the bot's own messages. The solvency page is the first is_solvency_page
    page wherever it sits (else the first page with the bot's certificate wording); the statement pages
    are the others. No solvency page: no row."""
    dv = _bot().dv
    texts = [t or "" for t in (page_texts or [])]
    idx = list(page_index) if page_index is not None else list(range(len(texts)))
    if len(idx) < len(texts):
        idx += list(range(len(idx), len(texts)))
    sol = [k for k, t in enumerate(texts) if is_solvency_page(t)] or \
        [k for k, t in enumerate(texts) if dv._CERT_WORDS.search(t)]
    if not sol:
        return []
    s = sol[0]
    rest = [k for k in range(len(texts)) if k not in sol]
    sd = dv.document_date(texts[s])
    st = dv.statement_date([texts[k] for k in rest])
    pages = _p1(idx[s], *([idx[rest[0]]] if rest else []))
    ev = {"solvency_page": page_no(idx[s]), "statement_pages": [page_no(idx[k]) for k in rest]}
    if sd and st:
        if sd == st:
            return [Finding(PASS, "bank.dates", f"solvency certificate and statement both dated {sd}", SOURCE, pages, ev)]
        return [Finding(FLAG, "bank.dates", f"the solvency certificate is dated {sd} but the statement was generated "
                                            f"on {st} — they must carry the SAME date", SOURCE, pages, ev)]
    if sd:
        return [Finding(NOTE, "bank.dates", f"solvency certificate dated {sd}; the statement's generation date could not "
                                            "be read from the scan — compare them by eye", SOURCE, pages, ev)]
    if st:
        return [Finding(NOTE, "bank.dates", f"statement generated {st}; no date could be read from the solvency "
                                            "certificate — compare them by eye", SOURCE, pages, ev)]
    return [Finding(NOTE, "bank.dates", "neither date could be read — check the solvency certificate and the statement "
                                        "carry the same date", SOURCE, pages, ev)]
