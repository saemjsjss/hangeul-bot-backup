"""The document check end to end, with every package's real code together (no fakes for
doc_verifier, page_checks, field_check, text_cache, auto_verify, auto_sync or sheet_hooks):
a first check reads a synthetic student's files and records them in full; a store left by the
code before this release is rolled out from the saved text alone (no OCR, no file read again);
auto_sync sends the lines before the reports are written; Supabase gets the records with the
rules version.

Every page carries a text layer and the OCR reader is a stub, so nothing is OCR'd. Every name
and passport is synthetic (SAMPLE ..., A9990000n) and every file is written under tmp_path.
"""
import json
import sys
from pathlib import Path

import pytest

BOT_ROOT = Path(__file__).resolve().parent.parent
if str(BOT_ROOT) not in sys.path:
    sys.path.insert(0, str(BOT_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.verify import auto_verify as av  # noqa: E402
from src.verify import common as C  # noqa: E402
from src.verify import doc_verifier as dv  # noqa: E402
from src.verify import field_check as fc  # noqa: E402
from src.verify import text_cache as tc  # noqa: E402
from verify_fixtures import FILLER, PASSPORTS, folder, make_image, student  # noqa: E402

KLP_DIR = "KOREAN LANGUAGE PROGRAM (KLP)"
P1 = PASSPORTS[0]
PATHS = ("REPORT_DIR", "STORE_PATH", "TEXT_DIR", "IMAGES_DIR", "LOCK_PATH", "DOC_REPORT", "FIELD_REPORT")


def page(*words: str) -> str:
    return " ".join(words) + " " + FILLER


class Reader:
    """The OCR reader's stand-in: says nothing, and counts what it was asked to read."""

    def __init__(self):
        self.calls = 0
        self.allowed = True

    def readtext(self, *args, **kwargs):
        assert self.allowed, "nothing may be OCR'd while the rules roll out"
        self.calls += 1
        return []


@pytest.fixture
def check(tmp_path, monkeypatch):
    for name in PATHS:
        monkeypatch.setattr(av, name, getattr(av, name))
    monkeypatch.setattr(tc, "TEXT_DIR", tc.TEXT_DIR)
    monkeypatch.setattr(tc, "IMAGES_DIR", tc.IMAGES_DIR)
    monkeypatch.setattr(av, "_last_written", None)
    monkeypatch.setattr(av, "_issue_memo", None)
    av.use_data_dir(tmp_path / "verification")
    av.REPORT_DIR.mkdir(parents=True)
    docs = tmp_path / "docs"
    monkeypatch.setattr(dv, "DOCS_ROOTS", [docs])
    reader = Reader()
    monkeypatch.setattr(dv, "_ocr_reader", lambda: reader)
    from src.sheets import passport_issue
    monkeypatch.setattr(passport_issue, "load", lambda: {})
    row = student(Passport_No=P1, Full_Name="SAMPLE STUDENT ONE")
    folder(docs, KLP_DIR, row["Full Name"], P1, {
        "PASSPORT.pdf": page("PASSPORT", "SAMPLE STUDENT ONE", P1),
        "PASSPORT SIZE PHOTO.jpg": lambda p: make_image(p),
        "BIRTH CERTIFICATE.pdf": page("BIRTH CERTIFICATE", "SAMPLE STUDENT ONE"),
        "FATHER DEATH CERTIFICATE.pdf": page("DEATH CERTIFICATE", "SAMPLE FATHER"),
        "MOTHER NID.pdf": page("NATIONAL ID CARD", "SAMPLE MOTHER", "5551234567"),
        "FAMILY RELATIONSHIP CERTIFICATE.pdf": page("FAMILY CERTIFICATE", "SAMPLE FATHER", "SAMPLE MOTHER"),
        "ACADEMIC CERTIFICATE.pdf": page("SECONDARY SCHOOL CERTIFICATE", "SAMPLE STUDENT ONE", "GPA 5.00"),
        "BANK STATEMENT.pdf": page("BANK SOLVENCY", "SAMPLE FATHER", "Balance: Tk 25,00,000.00"),
        "TRADE LICENSE.pdf": page("TRADE LICENSE", "SAMPLE FATHER"),
        "ID CARD CV.pdf": page("CURRICULUM VITAE", "SAMPLE OTHER PERSON"),
    })
    return {"row": row, "reader": reader}


def test_preflight_passes_with_this_checkouts_libraries():
    assert av.preflight() == ""


def test_a_first_check_then_a_roll_out_from_the_saved_text(check, monkeypatch):
    row, reader = check["row"], check["reader"]
    monkeypatch.setattr(av, "preflight", lambda: "")
    result = av.run(budget=5, export_rows=[row])
    assert not result.get("stopped") and not result.get("store_error")
    assert [e["passport"] for e in result["checked"]] == [P1] and result["reports_due"]

    store = json.loads(av.STORE_PATH.read_text(encoding="utf-8"))
    rec = store["documents"][P1]
    assert rec["rules_version"] == C.RULES_VERSION and rec["max_pages"] == 20
    assert rec["folder"] == str(dv.student_folders(dv.portal_students(rows=[row]))[P1][0])
    assert rec["fingerprint"] and not rec.get("retry")
    rows = json.loads(json.dumps(rec["rows"]))
    assert all(r.get("key") for r in rows) and not any(C.row_has_error(r) for r in rows)
    assert rec["verdict"] == C.student_verdict(rows) != C.INCOMPLETE
    by_key = {r["key"]: r for r in rows}
    assert "father_nid" not in by_key                                    # P1
    assert by_key["father_death_cert"]["detail"].startswith(
        "PASS: present — accepted in place of the father's NID (05 Father NID not needed)")
    assert by_key["id_card_cv"]["detail"] == "PASS: present — recorded as present only (not checked)"   # P3
    frows = store["fields"][P1]["rows"]
    assert frows and {r["result"] for r in frows} <= {"MATCH", "DIFFERS", fc.NOT_CONFIRMED, "UNREADABLE", "BLANK"}
    assert av.summary_lines(result)[0].startswith("🔍 Documents checked: 1")
    assert av.finish_reports(result) == [] and av.DOC_REPORT.exists() and av.FIELD_REPORT.exists()

    # The store as the code before this release left it: the roll-out re-evaluates it from the
    # saved text only, once, and says so once.
    for r in rec["rows"]:
        r.pop("key", None)
    for k in ("rules_version", "portal_fp", "evaluated_at", "first_checked", "max_pages", "engine", "folder"):
        rec.pop(k)
    store["meta"] = {}
    av.STORE_PATH.write_text(json.dumps(store), encoding="utf-8")
    reader.allowed = False
    again = av.run(budget=5, export_rows=[row])
    assert again["checked"] == [] and again["rollout_lines"]
    assert again["rollout_lines"][0].startswith(f"Rules unversioned -> {C.RULES_VERSION}: 1 student(s)")
    after = json.loads(av.STORE_PATH.read_text(encoding="utf-8"))
    assert after["meta"]["rules_version"] == C.RULES_VERSION
    assert after["documents"][P1]["rules_version"] == C.RULES_VERSION
    assert after["documents"][P1]["rows"] == rows                        # same rules, same rows
    assert av.run(budget=5, export_rows=[row])["rollout_lines"] == []    # never twice


def test_auto_sync_sends_the_lines_before_the_reports_and_supabase_gets_the_records(check, monkeypatch):
    from src.cloud import publish, sheet_hooks
    from src.sheets import auto_sync

    row = check["row"]
    monkeypatch.setattr(av, "preflight", lambda: "")
    monkeypatch.setattr(av, "DEFAULT_BUDGET", 5)
    monkeypatch.setattr(dv, "portal_students", lambda rows=None, _real=dv.portal_students: _real(rows=[row]))
    order = []
    monkeypatch.setattr(auto_sync, "notify", lambda lines, title=None: order.append(("sent", title)) or True)
    real_finish = av.finish_reports
    monkeypatch.setattr(av, "finish_reports", lambda result: order.append(("reports", None)) or real_finish(result))
    cloud = {"sent": []}
    lines = auto_sync._verify_step({}, cloud, True)
    assert lines and lines[0].startswith("🔍 Documents checked: 1")
    assert order == [("sent", auto_sync.CHECK_TITLE), ("reports", None)]
    assert cloud["store_ok"] is True and cloud["sent"][0][0] == auto_sync.CHECK_TITLE
    assert av.DOC_REPORT.exists()

    monkeypatch.setattr(publish, "known_scopes", lambda kind: set())
    batches, failed = sheet_hooks.verify_batches(cloud["verify"], cloud["store_ok"], ids={}, export=[row])
    assert failed == []
    kinds = [b[0] if isinstance(b, (list, tuple)) else b.get("kind") for b in batches if b]
    assert "doc_check" in json.dumps(batches, default=str)
    assert C.RULES_VERSION in json.dumps(batches, default=str)
    assert kinds


def test_the_real_fingerprints_ignore_workflow_fields_and_follow_compared_ones(check):
    row = check["row"]
    assert dv.PORTAL_KEYS_READ == ("Full Name", "Father", "Mother", "DOB", "Gender", "Passport No",
                                   "Passport Expiry", "Sponsor", "SSC GPA", "HSC GPA", "Program")
    d, _ = dv.student_folders(dv.portal_students(rows=[row]))[P1]
    base = av.field_fingerprint(d, row, issues={})
    moved = dict(row, **{"Current Stage": "Visa"})
    assert av.field_fingerprint(d, moved, issues={}) == base and av.portal_fp(moved) == av.portal_fp(row)
    edited = dict(row, Father="SAMPLE OTHER FATHER")
    assert av.field_fingerprint(d, edited, issues={}) != base and av.portal_fp(edited) != av.portal_fp(row)
