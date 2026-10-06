"""
Document rules for each program, taken from the Hangeul document guidelines
(KLP / EAP / BACHELOR / MASTER).

Each DOCS entry describes one required document: how its file is recognised, and the
checks that run on the text read out of it.  Checks are deliberately conservative — a
rule only fails when the evidence is clear; anything doubtful becomes a FLAG for a human.

Verdicts:  PASS (rule satisfied) · FLAG (needs a human eye) · FAIL (rule broken)
           MISSING (no such file uploaded)
"""
from __future__ import annotations

from typing import Dict, List

PROGRAMS = ["KLP", "EAP", "BACHELOR", "MASTER"]

# Minimum bank balance per program, in taka (guideline: Document 08).
MIN_BANK_TAKA = {"KLP": 1_800_000, "EAP": 1_800_000, "BACHELOR": 2_500_000, "MASTER": 2_500_000}

# Master's-only income-tax expectations (Document 9.1).
MIN_TAX_PAID_TAKA = 10_000
MIN_NET_WEALTH_TAKA = 5_000_000

PASSPORT_MIN_MONTHS = 12          # Document 01 — at least 1 year validity left
FAMILY_CERT_MAX_MONTHS = 3        # Document 06 — issued within the last 3 months
BANK_MIN_ACCOUNT_MONTHS = 6       # Document 08 — account at least 6 months old
BANK_MIN_STATEMENT_MONTHS = 6     # Document 08 — last 6 months of transactions
BANK_RELAXED_STATEMENT_MONTHS = 2  # allowed for "excellent accredited" universities
PHOTO_RATIO = 35 / 45             # Document 02 — 3.5 x 4.5 cm
PHOTO_FORMATS = (".jpg", ".jpeg")  # reviewer rule: photo must be JPG/JPEG

# Reviewer rule (bank): the statement's OPENING balance, not just any figure.
MIN_OPENING_BALANCE = {"KLP": 1_800_000, "EAP": 1_800_000,
                       "BACHELOR": 2_200_000, "MASTER": 2_200_000}
PREFERRED_OPENING_MAX = {"BACHELOR": 2_600_000, "MASTER": 2_600_000}

# A colour scan of a mostly-white document still shows coloured seals, stamps and ink,
# but a single pale government stamp on an otherwise white page can be well under 1% of
# the pixels.  0.5% was overtuned and called 36 genuine colour scans greyscale, so the
# bar is the presence of *any* meaningful colour rather than a share of the page.
MIN_COLOUR_PERCENT = 0.08

# Documents whose names must agree with each other and with the portal record.
IDENTITY_DOCS = ["passport", "student_nid", "birth_cert", "father_nid", "mother_nid",
                 "family_cert", "academic"]
# The only document the guideline requires in its online, QR-verifiable form.  Family
# certificates and academic certificates are issued by hand and usually have no QR at all.
QR_DOCS = ["birth_cert"]
# Bangla-only documents must be translated; these words show a translation is present.
TRANSLATION_WORDS = ["translated", "translation", "true copy", "english translation"]
BANGLA_RANGE = ("ঀ", "৿")

# file-name keywords -> document key.  The portal names files consistently
# (passport_*.pdf, family_cert_*.pdf …), so matching on the name is reliable.
FILE_PATTERNS: Dict[str, List[str]] = {
    "passport": ["passport"],
    "photo": ["passport size photo", "picture", "photo"],
    "student_nid": ["student nid", "student_nid", "nid card", "id card cv", "id_card"],
    "birth_cert": ["birth certificate", "birth_cert"],
    "father_nid": ["father nid", "father_nid"],
    "mother_nid": ["mother nid", "mother_nid"],
    "death_cert": ["death certificate", "death_cert"],
    "family_cert": ["family relationship", "family certificate", "family_cert"],
    "academic": ["academic certificate", "academic_cert", "transcript"],
    "bank": ["bank statement", "bank_statement", "solvency", "sanchay", "savings"],
    "trade_license": ["trade license", "trade_license", "tin", "employment certificate"],
    "income_tax": ["income tax", "tax certificate", "tax challan", "acknowledgement", "acknowledgment"],
    "affidavit": ["affidavit"],
    "personal_statement": ["personal statement", "study plan", "personal_statement"],
    "recommendation": ["recommendation"],
    "eca": ["extra curricular", "eca"],
    "language_cert": ["ielts", "topik", "toefl"],
}

# Which documents every program expects.  "required" drives the MISSING check;
# conditional ones are only expected when the student's portal record says so.
REQUIRED: Dict[str, List[str]] = {
    "KLP": ["passport", "photo", "birth_cert", "father_nid", "mother_nid", "family_cert",
            "academic", "bank", "trade_license"],
    "EAP": ["passport", "photo", "birth_cert", "father_nid", "mother_nid", "family_cert",
            "academic", "bank", "trade_license"],
    "BACHELOR": ["passport", "photo", "birth_cert", "father_nid", "mother_nid", "family_cert",
                 "academic", "bank", "trade_license"],
    "MASTER": ["passport", "photo", "birth_cert", "father_nid", "mother_nid", "family_cert",
               "academic", "bank", "trade_license", "income_tax"],
}
OPTIONAL = ["student_nid", "death_cert", "affidavit", "personal_statement", "recommendation", "eca",
            "language_cert"]

# Wording the checks look for in the OCR text (lower-cased, accents/spacing ignored).
NOTARY_WORDS = ["notar", "advocate", "attested", "authenticated", "affidavit", "judge court"]
# A translation must sit on a lawyer's pad OR any licensed/authorised pad.
# "ADV." is the usual abbreviation on notary stamps, and OCR often mangles the name.
LAWYER_PAD_WORDS = ["advocate", "adv.", "adv:", "adv ", "judge court", "bar association",
                    "notary public", "notarised by", "notarized by",
                    "translation centre", "translation center", "govt approved translation",
                    "government approved translation", "translation reg", "approved translation"]
APOSTILLE_WORDS = ["apostille", "e-apostille", "eapostille", "ministry of foreign affairs", "mofa"]
PROVISIONAL_WORDS = ["provisional"]
ONLINE_BIRTH_WORDS = ["bdris", "birth registration", "online", "qr"]
# How Bangladeshi banks label the balance a statement starts from.
OPENING_BALANCE_WORDS = ["opening balance", "previous balance", "initial balance",
                         "starting balance", "brought forward", "b/fwd", "b/f", "bf balance",
                         "balance b/f", "last balance"]
SOLVENCY_WORDS = ["solvency", "to whom it may concern", "certify"]
SEAL_WORDS = ["manager", "branch", "signature", "seal", "authorized signature"]
USD_WORDS = ["usd", "u.s. dollar", "us dollar", "equivalent to usd"]
UNION_ISSUER_WORDS = ["union parishad", "pourashava", "paurasava", "city corporation", "municipality",
                      "poura", "union porishod", "union porishad"]
REF_NO_WORDS = ["ref", "memo", "serial", "certificate no", "registration no"]
TRADE_RUNNING_WORDS = ["renewal", "valid", "fiscal year", "financial year", "validity"]

# Numbers that look like an NID but are not one.  Derived from the corpus: any number
# printed on many different students' documents belongs to the form, the notary or a
# phone line rather than to a person.  1301109623 appears on 49 students' NIDs.
BOILERPLATE_NUMBERS = {"1301109623"}

# Documents the applicant types and prints rather than photocopies from an original.
# "Clear colour scans only" is a rule about copies of issued originals, so it does not
# apply to these — they are black text on white by nature.
TYPED_DOCS = {"personal_statement", "recommendation", "eca"}

# Human-readable titles for the report
TITLES = {
    "passport": "01 Passport", "photo": "02 Photo", "student_nid": "03 Student NID",
    "birth_cert": "04 Birth Certificate", "father_nid": "05 Father NID", "mother_nid": "05 Mother NID",
    "death_cert": "05 Death Certificate", "family_cert": "06 Family Relationship Certificate",
    "academic": "07 Academic Certificate & Transcript", "bank": "08 Bank Solvency & Statement",
    "trade_license": "09 Financial (Trade Licence / TIN / Employment)",
    "income_tax": "09.1 Income Tax", "affidavit": "10 Affidavit",
    "personal_statement": "Personal Statement / Study Plan", "recommendation": "Recommendation Letter",
    "eca": "Extra Curricular (ECA)", "language_cert": "IELTS / TOPIK Certificate",
}

# --- Document 08: the order of the bank file -----------------------------------------
# Owner's rule: page 1 is the bank solvency certificate, pages 2 to the last are the statement.
# Each page is told apart by the wording it carries (regular expressions, lower-cased text).
# A solvency certificate can say "deposit" and "balance", and a statement page can carry the
# bank's "certify" footer, so neither single word decides: a page is a solvency certificate
# when it carries at least BANK_SOLVENCY_MIN of the solvency markers and (almost) none of the
# statement markers, and a statement page the other way round.  Calibrated on 162 stored bank
# files (420 pages): no page read as both.
BANK_SOLVENCY_MARKERS = [
    r"solvency", r"to whom it may concern", r"certif(?:y|ied) that", r"maintaining",
    r"\busd\b|us\s*dollar", r"equivalent", r"\bas on\b", r"in words",
    r"good (?:standing|reputation)", r"satisfactor",
]
BANK_STATEMENT_MARKERS = [
    r"statement of account|account statement|statement period|statement date|\bstatement\b",
    r"\bdebit\b|\bdr\.?\b|withdrawal", r"\bcredit\b|\bcr\.?\b|deposit", r"transaction",
    r"particulars", r"cheque|chq", r"narration|description",
    r"value date|txn date|tran(?:saction)? date",
    r"opening balance|closing balance|brought forward|balance b/?f",
    r"\bpage\s*\d+\s*(?:of|/)\s*\d+",
]
BANK_SOLVENCY_MIN = 2          # solvency markers a solvency-certificate page carries at least
BANK_STATEMENT_MIN = 3         # statement markers a statement page carries at least
BANK_STATEMENT_ROWS = 5        # dated rows that, with 2 markers, also make a statement page
# After page 1 a page only counts as a solvency certificate on strong evidence, so a statement's
# last page holding only the bank's "Certified that the above statement is correct" footer never
# reads as a solvency certificate in the wrong place: at least this many solvency markers, one of
# them a solvency certificate's own, and no word of "the above statement".
BANK_LATE_SOLVENCY_MIN = 3
BANK_SOLVENCY_CORE = r"solvency|to whom it may concern|maintaining|\busd\b|us\s*dollar|equivalent"
BANK_STATEMENT_REFERENCE = r"\b(?:above|this|the\s+said)\s+statement\b"

# --- Document 07: the qualifications each program's academic file must contain -------
# Each entry is a list of alternatives: ("HSC", "DIPLOMA") is an HSC or, instead, a Diploma.
REQUIRED_QUALIFICATIONS: Dict[str, List[tuple]] = {
    "KLP": [("SSC",), ("HSC", "DIPLOMA")],
    "EAP": [("SSC",), ("HSC", "DIPLOMA")],
    "BACHELOR": [("SSC",), ("HSC", "DIPLOMA")],
    "MASTER": [("SSC",), ("HSC", "DIPLOMA"), ("BACHELOR",)],
}
QUALIFICATION_NAMES = {"SSC": "SSC", "HSC": "HSC", "DIPLOMA": "Diploma",
                       "BACHELOR": "Bachelor's degree", "MASTER": "Master's degree"}
# How each qualification (or its equivalent) is named on the page, matched on whole words in
# lower-cased text.  "Higher Secondary (School) Certificate" is folded to "higher secondary"
# first, and "Junior School/Dakhil Certificate" removed, so neither reads as an SSC.  An SSC
# certificate says "Secondary SCHOOL Certificate"; "...secondary certificate" without
# "school" is the HSC title with "Higher" lost to the scan (seen 5 times in the stored text).
QUALIFICATION_WORDS: Dict[str, List[str]] = {
    "SSC": [r"secondary\s+school\s+(?:\S{1,15}\s+)?(?:cer[a-z]{2,}|exam)", r"\bschool\s+cer[a-z]{2,}\s+exam",
            r"certificate\s+of\s+secondary\s+education",
            r"\bdakhil\b", r"ordinary\s+level", r"\bi?gcse\b",
            r"\b(?:gce|cambridge|edexcel|international)\s+o[\s'-]{0,2}levels?\b"],
    "HSC": [r"higher\s+secondary",
            r"\balim\s+(?:exam|certif|transcript|result|\()", r"advanced\s+level",
            r"intermediate\s+certificate",
            r"\b(?:gce|cambridge|edexcel|international)\s+(?:as\s*(?:&|and)\s*)?a[\s'-]{0,2}levels?\b",
            r"\badvanced\s+(?:subsidiary\s+)?gce\b"],
    "DIPLOMA": [r"\bdiploma\b"],
    # A bare three-letter token ("BSC 0001234" as a serial, "msc"/"mss" as OCR noise) is not a
    # degree: the short forms only count dotted ("B.Sc") or titled ("BSc in", "BSc (Hons)").
    "BACHELOR": [r"\bbachelor", r"\bb\.\s?sc\b|\bbsc\s*(?:\(|in\b|hons|engineering)",
                 r"\bb\.\s?b\.\s?a\b|\bbba\s*(?:\(|in\b|hons)",
                 r"\bb\.\s?a\.?\s*\(?\s*(?:hons|honours|pass)", r"\bb\.\s?s\.\s?s\b|\bbss\s*(?:\(|in\b|hons)",
                 r"\bb\.?\s?com\b", r"\bhonou?rs\b|\bhons\b", r"\bmbbs\b",
                 r"\bb\.\s?d\.\s?s\b", r"\bb\.?\s?pharm", r"\bb\.\s?ed\b", r"\bb\.?\s?tech\b", r"\bdegree\s+pass\b"],
    "MASTER": [r"\bmaster'?s?\s+(?:of|in|degree)\b", r"\bm\.\s?sc\b|\bmsc\s*(?:\(|in\b)",
               r"\bm\.\s?b\.\s?a\b|\bmba\s*(?:\(|in\b)", r"\bm\.\s?a\.?\s*\(?\s*(?:in\b|final|prelim)",
               r"\bm\.\s?s\.\s?s\b|\bmss\s*(?:\(|in\b)", r"\bm\.\s?com\b"],
}
# A degree certificate or transcript often lists the holder's earlier SSC/HSC results, even
# under their full exam titles.  On a page that names a degree a school qualification therefore
# never counts: named by its title it only turns a missing one into a FLAG, never a FAIL; named
# by its short form alone ("HSC GPA 5.00") it is ignored.
QUALIFICATION_ABBREVIATIONS: Dict[str, str] = {"SSC": r"\bs\.?\s?s\.?\s?c\b", "HSC": r"\bh\.?\s?s\.?\s?c\b"}
# "...Secondary Certificate" with neither "School" nor "Higher" is the HSC title with "Higher"
# lost to the scan (5 stored pages) -- or the SSC title with "School" lost.  It counts as the HSC,
# but when the page shows no sign of the HSC (HSC_SIGNS) it may be the SSC, so a missing SSC
# is then a FLAG, never a FAIL.
HSC_UNSURE = r"\bsecondary\s+cer[a-z]{2,}"
HSC_SIGNS = r"\bcollege\b|\bhigher\b|\bintermediate\b(?!\s*(?:and|&)\s*secondary)"
# A notary signs as "Adv. <name>, LL.B (Hons), Dhaka Judge Court", and the officer who signs an
# e-Apostille or an attestation stamp prints a degree too: a degree abbreviation this close to
# those words is theirs, not the student's.  For the same reason a bare "LL.B" is not taken as the
# student's degree (it was the notary's or the signing officer's on every stored page that had
# it); a law degree still reads by "Bachelor of Laws", and "B. A." only counts as "B.A. (Hons)" /
# "B.A. Pass" (a bare "B. A." was an exam centre's name).
NOTARY_DEGREE = r"\b(?:ll\.?\s?[bm]|[bm]\.\s?a|hons|honou?rs)\b\.?"
NOTARY_NEAR = (r"\badv|advoca|\bnotar|\bcourt\b|bar\s+association|ministry|signing|attest|"
               r"\bseal\b|\bstamp|true\s+copy")
# Matched case-sensitively, so the English "a level" is not read as an A Level.
QUALIFICATION_WORDS_CASED: Dict[str, List[str]] = {
    "SSC": [r"\bO[\s'-]{0,2}Levels?\b"], "HSC": [r"\bA[\s'-]{0,2}Levels?\b"],
}
# A school board (SSC/HSC, Dakhil/Alim, Diploma) issues no degree: on its page a short degree word
# is a serial number, OCR noise, a headmaster's signature or an attestation stamp, never the
# student's degree.  Only a degree written out in full (DEGREE_IN_FULL) still makes it a degree
# page -- a degree transcript can name the board of its holder's HSC.
SCHOOL_BOARD = (r"intermediate\s*(?:and|&)\s*secondary|board\s+of\s+(?:intermediate|secondary)|"
                r"madrasah\s+education|technical\s+education\s+board")
DEGREE_IN_FULL = r"\bbachelor\b|\bmaster'?s?\s+(?:of|in|degree)\b|\bdegree\s+of\b"

# A page naming no qualification that still carries these words is a certificate or transcript
# the scan did not let us name, so a missing qualification there is a FLAG, never a FAIL.
ACADEMIC_PAGE_WORDS = ["examination", "board", "transcript", "gpa", "cgpa", "grade", "result",
                       "university", "college", "school", "semester", "marks", "subject", "roll",
                       "registration", "passed", "academic", "degree"]
# A page reads well enough to judge when it is this long and carries this many plain English words.
ACADEMIC_MIN_PAGE_CHARS = 150
ACADEMIC_MIN_ENGLISH_WORDS = 4
ACADEMIC_ENGLISH_WORDS = {"the", "and", "of", "this", "that", "is", "to", "in", "has", "his", "her", "date",
                          "name", "father", "mother", "board", "examination", "education", "university",
                          "bangladesh", "roll", "registration", "gpa", "grade", "result", "subject", "year",
                          "school", "college", "passed", "issued", "student", "group", "science",
                          "certificate", "certify", "transcript", "apostille", "document", "signature",
                          "seal", "held"}
