"""
Document rules for each program, taken from the Hangeul document guidelines
(KLP / EAP / BACHELOR / MASTER).

Each DOCS entry describes one required document: how its file is recognised, and the
checks that run on the text read out of it.  Checks are deliberately conservative — a
rule only fails when the evidence is clear; anything doubtful becomes a FLAG for a human.

Verdicts:  PASS (rule satisfied) · FLAG (needs a human eye) · FAIL (rule broken)
           MISSING (no such file uploaded) · ERROR (the file could not be checked; retried)
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

# file-name keywords -> document key.  The portal's ZIP names each file by a descriptive
# title ("FATHER NID.pdf", "FAMILY RELATIONSHIP CERTIFICATE.pdf"); a file saved under the
# portal's own key ("family_cert_1234.pdf") is recognised too, because classify() also
# reads the name with "_" as a space.  The longest matching pattern wins.
FILE_PATTERNS: Dict[str, List[str]] = {
    "passport": ["passport"],
    "photo": ["passport size photo", "picture", "photo"],
    "student_nid": ["student nid", "student_nid", "nid card"],
    # Not "id card" on its own: "FATHER ID CARD.pdf" is a parent's NID, not the student's CV.
    "id_card_cv": ["id card cv", "id_card_cv", "id_card"],
    "birth_cert": ["birth certificate", "birth_cert", "birth cert"],
    "father_nid": ["father nid", "father_nid"],
    "mother_nid": ["mother nid", "mother_nid"],
    "father_death_cert": ["father death certificate", "father death cert", "father_death_cert"],
    "mother_death_cert": ["mother death certificate", "mother death cert", "mother_death_cert"],
    "death_cert": ["death certificate", "death_cert", "death cert"],
    "family_cert": ["family relationship", "family certificate", "family_cert", "family cert"],
    "academic": ["academic certificate", "academic_cert", "academic cert", "transcript"],
    "bank": ["bank statement", "bank_statement", "solvency", "sanchay", "savings"],
    "trade_license": ["trade license", "trade_license", "tin", "employment certificate"],
    "income_tax": ["income tax", "income_tax", "tax certificate", "tax challan", "acknowledgement",
                   "acknowledgment"],
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
OPTIONAL = ["student_nid", "id_card_cv", "death_cert", "father_death_cert", "mother_death_cert",
            "affidavit", "personal_statement", "recommendation", "eca", "language_cert"]

# A required document that another one stands in for: a deceased parent has no NID to
# give, and their death certificate is uploaded instead.  With the alternative present the
# required one is not reported MISSING.
ALTERNATIVES: Dict[str, List[str]] = {"father_nid": ["father_death_cert"],
                                      "mother_nid": ["mother_death_cert"]}
# Documents recorded as present and never read or checked (the portal's "ID Card / CV"
# slot holds anything from a CV to a school ID).
PRESENCE_ONLY = {"id_card_cv"}

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
    "id_card_cv": "03 ID Card / CV", "father_death_cert": "05 Father Death Certificate",
    "mother_death_cert": "05 Mother Death Certificate",
}

# --- labels the amount, date and number rules read beside -----------------------------
# The figure a statement or solvency certificate calls its balance.  The opening balance
# (OPENING_BALANCE_WORDS) has its own rule and is never taken for it.
BALANCE_LABELS = ["closing balance", "current balance", "available balance", "ledger balance",
                  "total balance", "balance", "solvency", "amount of tk", "sum of tk",
                  "amount of taka", "sum of taka"]
# The amount an income-tax certificate or challan says was paid.
TAX_PAID_LABELS = ["total tax paid", "amount of tax paid", "tax paid", "paid tax", "tax payment",
                   "tax deposited", "paid amount"]
# A figure written right after one of these is an account, customer or phone number, not
# money (each is matched as a whole word: "paid" holds no "id", "nominee" no "no").
ID_NUMBER_LABELS = ["a/c", "account", "customer", "cif", "id", "no", "number", "routing",
                    "mobile", "phone", "tin", "nid"]
# Where a certificate prints the date of birth, and the labels that end that field (so the
# registration or issue date after an unreadable birth date is not taken for it).
DOB_LABELS = ["date of birth", "birth date", "d.o.b", "dob"]
DOB_STOP_LABELS = ["date of", "registration", "issue", "issued"]
# A trade licence's fiscal year is printed beside one of these.
FISCAL_LABELS = ["fiscal", "financial year", "renewal", "validity", "valid up to"]
