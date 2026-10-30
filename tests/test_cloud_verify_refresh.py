"""The document check's re-evaluation as Supabase gets it (src/cloud/sheet_hooks.py and the
check's records in src/cloud/records.py).

What is pinned here:
  republish       the passports a re-evaluation changed (result["republish"]): their doc_verdict and
                  field_check, each passport its own complete scope and each once, never their
                  doc_page_text (their files did not change); the checked and catch-up passports
                  keep their OCR text
  a stopped check result["store_error"] (results.json unreadable: the check stopped) or
                  result["store_note"] (it went on from results.json.bak): no store-wide kind, and
                  why is a failed read; result["store"] None is no store
  the folder      the OCR text's file version comes from the folder the check used (rec["folder"]),
                  else from doc_verifier's choice with this run's portal rows, never from a lookup
                  without them
  --publish       after_verify_refresh hands over job portal_sync: every republished passport, the
                  complete doc_check, both reports and each notice Telegram accepted; it never raises
  doc_check       rules_version and max_pages in its data when the record has them (never
                  evaluated_at); without them every record's hash is what it was before
  the reports     DOCUMENT CHECK counts ERROR and FIELD CHECK NOT CONFIRMED, each only when there is one
Nothing reaches the network: Supabase is a fake, every file is in tmp_path, the students are SAMPLE
names with passports A99900001..A99900003.
"""
import hashlib
import json
import logging
import os
import sys
from pathlib import Path

import pytest

BOT_ROOT = Path(__file__).resolve().parent.parent
if str(BOT_ROOT) not in sys.path:
    sys.path.insert(0, str(BOT_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_cloud import cloud, cloud_warnings  # noqa: E402,F401  (cloud is a fixture)

from src.cloud import handoff, publish, records, sheet_hooks  # noqa: E402
from src.verify import auto_verify as av  # noqa: E402
from src.verify import doc_verifier as dv  # noqa: E402

P1, P2, P3 = "A99900001", "A99900002", "A99900003"
NAMES = {P1: "SAMPLE STUDENT ONE", P2: "SAMPLE STUDENT TWO", P3: "SAMPLE STUDENT THREE"}
DAY = "2026-10-20T09:10:00"
ROLLOUT_TITLE = "🔍 Document check — rules updated"
STOPPED = "results.json: unreadable before the document check (the check stopped; nothing was overwritten)"
RESTORED = "results.json: unreadable before the document check (the check went on from its backup)"
EMPTY = "results.json: unreadable before the document check (it started from an empty store)"


def doc_entry(name, verdict="REVIEW", **more):
    return {"fingerprint": "f1", "checked": DAY, "student": name, "program": "KLP", "verdict": verdict,
            "rows": [{"doc": "01 Passport", "file": "PASSPORT.jpg", "verdict": "PASS",
                      "detail": "PASS: valid until 2034-01-01"},
                     {"doc": "05 Father NID", "file": "FATHER NID.pdf", "verdict": "FLAG",
                      "detail": "FLAG: the father's name was not found on it"}], **more}


def field_entry(name, **more):
    return {"fingerprint": "g1", "checked": DAY, "student": name, "program": "KLP",
            "rows": [{"field": "DOB", "portal": "2000-01-01", "result": "MATCH", "detail": "found on 01 Passport"},
                     {"field": "Father", "portal": "SAMPLE FATHER", "result": "DIFFERS",
                      "detail": "the family certificate reads SAMPLE OTHER"}], **more}


def pinned_store():
    """A store as results.json held it before this release (no rules_version, max_pages, ERROR or
    NOT CONFIRMED): the records built from it must keep the hashes they had then."""
    return {"documents": {P1: doc_entry(NAMES[P1]), P2: doc_entry(NAMES[P2], "FAIL")},
            "fields": {P1: field_entry(NAMES[P1]), P2: field_entry(NAMES[P2])},
            "corrections": [{"noticed": "2026-10-20", "student": NAMES[P1], "field": "DOB", "was": "2000-01-02",
                             "now": "2000-01-01", "was_result": "DIFFERS", "now_result": "MATCH"}]}


PINNED_IDS = {P1: ("425", "HNG-2026-905")}
# sha256 of the joined content hashes, as records.py at 8317741 (and C1) built them from pinned_store()
PINNED_DOC_CHECKS = "1513f162f27454253315d558165d2ff75826c1c9d2f89b3401d821b1b2393ccc"
PINNED_DOCUMENT_REPORT = "2d89d5d02e6d8b63af5bb936c3c8bab3a04b2ce7450e723f49f7c8bd94865303"
PINNED_FIELD_REPORT = "21e67c8a8326885aec0254deeb88b9f4d92a4efa5bfb64a76716086467a4cd11"


def digest(recs):
    return hashlib.sha256("|".join(r["content_hash"] for r in recs).encode("utf-8")).hexdigest()


@pytest.fixture(autouse=True)
def text_dir(monkeypatch, tmp_path):
    """The OCR text caches (data/verification/text/<P>.json) in tmp_path."""
    path = tmp_path / "verification" / "text"
    path.mkdir(parents=True)
    monkeypatch.setattr(av, "TEXT_DIR", path)
    return path


def student_folder(tmp_path, program_dir, passport, size, mtime, name="PASSPORT.jpg"):
    """A downloaded student's folder with one scan of `size` bytes and `mtime`."""
    folder = tmp_path / "docs" / program_dir / f"{NAMES[passport]} ({passport})"
    folder.mkdir(parents=True)
    scan = folder / name
    scan.write_bytes(b"x" * size)
    os.utime(scan, (mtime, mtime))
    return folder


def write_text(passport, versions, name="PASSPORT.jpg"):
    """The passport's OCR text cache: one PAGES entry per (size, mtime, text) version of `name`."""
    cache = {f"PAGES:{name}:{size}:{mtime}:20": {"pages": [text], "sideways": []} for size, mtime, text in versions}
    (av.TEXT_DIR / f"{passport}.json").write_text(json.dumps(cache), encoding="utf-8")


def by_kind(batches):
    out = {}
    for b in batches:
        out.setdefault(b["kind"], []).append(b)
    return out


def scopes(batches, kind):
    """The passports of a kind's rows, in the order they come."""
    return list(dict.fromkeys(r["scope"] for b in batches if b["kind"] == kind for r in b["rows"]))


def handed(cloud_):
    """The one handoff file -> (its document, {kind: [batches]})."""
    assert len(cloud_.spawned) == 1, cloud_.spawned
    doc = json.loads(cloud_.spawned[0].read_text(encoding="utf-8"))
    return doc, by_kind(doc["batches"])


# --------------------------------------------------------------------------- republish

def test_republished_passports_send_their_verdicts_and_fields_but_not_their_text(cloud, tmp_path):
    store = {"documents": {}, "fields": {}, "corrections": []}
    for n, p in enumerate((P1, P2, P3)):
        folder = student_folder(tmp_path, "KLP", p, 100 + n, 1790000000)
        write_text(p, [(100 + n, 1790000000, f"PASSPORT OF {NAMES[p]}")])
        store["documents"][p] = doc_entry(NAMES[p], folder=str(folder))
        store["fields"][p] = field_entry(NAMES[p])
    # P1 was just checked (its files read), P2 re-evaluated from the saved text (its rows changed),
    # P3 never reached Supabase (a catch-up); a passport listed twice is sent once.
    result = {"checked": [{"student": NAMES[P1], "passport": P1, "program": "KLP", "verdict": "REVIEW"}],
              "republish": [P2, P1, P2], "store": store}
    batches, failed = sheet_hooks.verify_batches(result, True, ids={})
    assert failed == []
    assert scopes(batches, "doc_verdict") == [P1, P2, P3]               # checked, republished, catch-ups
    assert scopes(batches, "field_check") == [P1, P2, P3]
    assert scopes(batches, "doc_page_text") == [P1, P3]                 # P2's files did not change
    kinds = by_kind(batches)
    for kind in ("doc_verdict", "field_check"):
        b, = kinds[kind]
        assert (b["scope"], b["complete"]) == (None, True)              # each passport its own complete scope
        keys = [r["key"] for r in b["rows"]]
        assert len(keys) == len(set(keys)) == 6                         # 2 rows each of 3 passports, once
    check, = kinds["doc_check"]
    assert (check["scope"], check["complete"]) == ("all", True)
    assert sorted(r["key"] for r in check["rows"]) == [P1, P2, P3]


def test_a_republish_without_a_checked_list_or_a_store_entry_sends_what_there_is(cloud):
    store = {"documents": {P1: doc_entry(NAMES[P1])}, "fields": {P2: field_entry(NAMES[P2])}, "corrections": []}
    result = {"republish": [P1, P2, P3], "store": store}                 # no "checked"; P3 is not in the store
    batches, failed = sheet_hooks.verify_batches(result, True, ids={})
    assert failed == []
    assert scopes(batches, "doc_verdict") == [P1] and scopes(batches, "field_check") == [P2]
    assert scopes(batches, "doc_page_text") == []


# --------------------------------------------------------------------------- a stopped check

def test_a_check_stopped_on_an_unreadable_store_holds_back_every_store_wide_kind(cloud):
    result = {"checked": [], "republish": [], "waiting": 0, "skipped": [], "store": None,
              "store_error": "results.json could not be read and results.json.bak is no good"}
    batches, failed = sheet_hooks.verify_batches(result, True, ids={})
    assert failed == [STOPPED]
    assert not any(b["rows"] for b in batches)                          # nothing at all to send
    assert not {"doc_check", "field_correction", "report", "report_section"} & set(by_kind(batches))
    # auto_sync's own guess (results.json unreadable before the check) does not change the reason
    assert sheet_hooks.verify_batches(result, False, ids={})[1] == [STOPPED]


def test_a_check_that_went_on_from_the_backup_sends_its_passports_and_holds_back_the_store(cloud):
    store = {"documents": {P1: doc_entry(NAMES[P1]), P2: doc_entry(NAMES[P2])},
             "fields": {P1: field_entry(NAMES[P1])}, "corrections": []}
    result = {"checked": [{"passport": P1}], "republish": [P2], "store": store,
              "store_note": "results.json was unreadable; the check went on from results.json.bak"}
    batches, failed = sheet_hooks.verify_batches(result, True, ids={})
    assert failed == [RESTORED]
    assert scopes(batches, "doc_verdict") == [P1, P2]                   # no catch-up from a restored store
    assert not {"doc_check", "field_correction", "report", "report_section"} & set(by_kind(batches))


def test_an_unreadable_store_auto_sync_saw_before_the_check_is_still_its_own_reason(cloud):
    result = {"checked": [{"passport": P1}], "store": {"documents": {P1: doc_entry(NAMES[P1])}}}
    batches, failed = sheet_hooks.verify_batches(result, False, ids={})
    assert failed == [EMPTY]
    assert scopes(batches, "doc_verdict") == [P1] and "doc_check" not in by_kind(batches)


# --------------------------------------------------------------------------- the folder (A21)

@pytest.fixture
def two_folders(tmp_path, monkeypatch):
    """P1 downloaded twice, under two programs: the new copy (200 bytes, older mtime) and an old
    one (100 bytes, newer mtime), both versions in its OCR text cache. doc_verifier's folder lookup
    is recorded, and never runs without portal rows."""
    new = student_folder(tmp_path, "KLP", P1, 200, 1780000000)
    old = student_folder(tmp_path, "OLD PROGRAM", P1, 100, 1790000000)
    write_text(P1, [(200, 1780000000, "PASSPORT NEW COPY"), (100, 1790000000, "PASSPORT OLD COPY")])
    calls = []

    def student_folders(portal=None):
        calls.append(portal)
        assert portal, "never a folder lookup without this run's portal rows"
        return {P1: (new, new.name)}

    monkeypatch.setattr(dv, "student_folders", student_folders)
    monkeypatch.setattr(dv, "portal_students", lambda rows=None: {
        str(r.get("Passport No") or "").upper(): r for r in rows or [] if r.get("Passport No")})
    return {"new": new, "old": old, "calls": calls}


def page_text_sizes(batches):
    return [r["data"]["size"] for b in batches if b["kind"] == "doc_page_text" for r in b["rows"]]


def test_the_text_comes_from_the_folder_the_check_used(cloud, two_folders):
    store = {"documents": {P1: doc_entry(NAMES[P1], folder=str(two_folders["new"]))}}
    batches, _ = sheet_hooks.verify_batches({"checked": [{"passport": P1}], "store": store}, True, ids={})
    assert page_text_sizes(batches) == [200]
    assert two_folders["calls"] == []                                    # the record said which folder


def test_without_the_check_s_folder_the_run_s_portal_rows_choose_it(cloud, two_folders):
    store = {"documents": {P1: doc_entry(NAMES[P1])}}
    export = [{"Full Name": NAMES[P1], "Passport No": P1, "Program": "KLP"}]
    batches, _ = sheet_hooks.verify_batches({"checked": [{"passport": P1}], "store": store}, True, ids={},
                                            export=export)
    assert page_text_sizes(batches) == [200]
    assert [sorted(p) for p in two_folders["calls"]] == [[P1]]


def test_without_a_folder_or_portal_rows_no_folder_is_guessed(cloud, two_folders):
    store = {"documents": {P1: doc_entry(NAMES[P1])}}
    batches, _ = sheet_hooks.verify_batches({"checked": [{"passport": P1}], "store": store}, True, ids={})
    assert page_text_sizes(batches) == [100]                             # the newest version cached
    assert two_folders["calls"] == []


# --------------------------------------------------------------------------- auto_verify --publish

def test_after_verify_refresh_hands_over_the_republished_records_and_the_complete_check(cloud, tmp_path):
    before = {"documents": {p: doc_entry(NAMES[p]) for p in (P1, P2, P3)},
              "fields": {p: field_entry(NAMES[p]) for p in (P1, P2, P3)}, "corrections": []}
    first, _ = sheet_hooks.verify_batches({"checked": [], "store": before}, True, ids={})
    publish.publish_batches("portal_sync", first)                       # Supabase holds the old verdicts
    assert sorted(publish.known_scopes("doc_verdict")) == [P1, P2, P3]

    # The roll-out re-evaluated everyone from the saved text: P1's and P2's rows changed, P3's did not.
    after = json.loads(json.dumps(before))
    for p in (P1, P2, P3):
        after["documents"][p].update(rules_version="2026-10-21.1", max_pages=20, evaluated_at="2026-10-21T10:00:00")
    for p in (P1, P2):
        after["documents"][p].update(verdict="PASS", checked="2026-10-21T10:00:00",
                                     previous_verdict="REVIEW", verdict_changed_at="2026-10-21T10:00:00")
        after["documents"][p]["rows"][1].update(verdict="PASS", detail="PASS: the father's name is on it")
    lines = ["Rules unversioned -> 2026-10-21.1: 3 student(s) re-evaluated from the saved text (no new OCR).",
             "2 verdict(s) changed:"]
    result = {"checked": [], "reevaluated": [{"passport": P1, "old": "REVIEW", "new": "PASS"},
                                             {"passport": P2, "old": "REVIEW", "new": "PASS"}],
              "republish": [P1, P2], "rollout_lines": lines, "store": after, "waiting": 0, "skipped": []}

    path = sheet_hooks.after_verify_refresh(result, [(ROLLOUT_TITLE, lines, 1792560000.0)])
    assert path == cloud.spawned[0]
    doc, kinds = handed(cloud)
    assert (doc["job"], doc["failed_reads"]) == ("portal_sync", [])
    assert scopes(doc["batches"], "doc_verdict") == [P1, P2]             # every republished passport
    assert scopes(doc["batches"], "field_check") == [P1, P2]
    assert "doc_page_text" not in kinds                                  # no file was read again
    check, = kinds["doc_check"]
    assert (check["scope"], check["complete"]) == ("all", True)
    assert sorted(r["key"] for r in check["rows"]) == [P1, P2, P3]       # the complete check
    one = [r for r in check["rows"] if r["key"] == P1][0]
    assert (one["data"]["verdict"], one["data"]["rules_version"], one["data"]["max_pages"]) == ("PASS", "2026-10-21.1", 20)
    assert {b["scope"] for b in kinds["report"]} == {"document_check", "field_check"}
    notice, = kinds["notification"]
    assert notice["rows"][0]["content"] == ROLLOUT_TITLE + "\n\n" + "\n".join(lines)
    assert notice["rows"][0]["data"]["source"] == "auto_verify"

    assert publish.process_file(path) == 0
    run = list(cloud.fake.runs.values())[-1]                             # after the first publish's run
    assert (run["job"], run["status"]) == ("portal_sync", "ok")
    assert cloud.fake.records[("doc_check", P1)]["data"]["verdict"] == "PASS"
    assert cloud.fake.records[("doc_check", P3)]["data"]["verdict"] == "REVIEW"
    assert cloud.fake.records[("doc_verdict", f"{P2}|05 Father NID")]["data"]["verdict"] == "PASS"


def test_after_verify_refresh_never_raises_and_does_nothing_while_publishing_is_off(monkeypatch, caplog):
    from src.cloud import student_index
    store = {"documents": {P1: doc_entry(NAMES[P1]), P2: doc_entry(NAMES[P2])}, "fields": {}, "corrections": []}
    result = {"checked": [], "republish": [P1], "store": store}
    handed_over = []
    monkeypatch.setattr(handoff, "submit", lambda job, batches, failed=(): handed_over.append((job, batches, failed))
                        or Path("handoff.json"))
    monkeypatch.setattr(student_index, "load", lambda: {})
    monkeypatch.setattr(publish, "known_scopes", lambda kind: {P1, P2})  # Supabase has both: no catch-up

    monkeypatch.setattr(handoff, "enabled", lambda: False)
    assert sheet_hooks.after_verify_refresh(result) is None and handed_over == []

    monkeypatch.setattr(handoff, "enabled", lambda: True)
    assert sheet_hooks.after_verify_refresh(result) == Path("handoff.json")
    (job, batches, failed), = handed_over
    assert (job, failed) == ("portal_sync", [])
    assert scopes(batches, "doc_verdict") == [P1]
    assert [r["key"] for r in by_kind(batches)["doc_check"][0]["rows"]] == [P1, P2]

    def broken(*args, **kwargs):
        raise RuntimeError(f"bad record for {NAMES[P1]}")

    monkeypatch.setattr(sheet_hooks, "verify_batches", broken)
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="hangeul.cloud"):
        assert sheet_hooks.after_verify_refresh(result) is None
    assert [w.getMessage() for w in cloud_warnings(caplog)] == [
        "Supabase publish failed (portal_sync): the records could not be built (RuntimeError)"]
    assert NAMES[P1] not in caplog.text


def test_after_verify_notice_hands_over_the_announced_summary(monkeypatch):
    handed_over = []
    monkeypatch.setattr(handoff, "enabled", lambda: True)
    monkeypatch.setattr(handoff, "submit", lambda job, batches, failed=(): handed_over.append((job, batches, failed))
                        or Path("handoff.json"))
    lines = ["Rules unversioned -> 2026-10-21.1: 3 student(s) re-evaluated from the saved text (no new OCR)."]
    assert sheet_hooks.after_verify_notice(ROLLOUT_TITLE, lines, 1792560000.0) == Path("handoff.json")
    (job, batches, failed), = handed_over
    assert (job, failed) == ("portal_sync", [])
    notice, = batches
    assert (notice["kind"], notice["complete"]) == ("notification", False)
    assert notice["rows"][0]["content"] == f"{ROLLOUT_TITLE}\n\n{lines[0]}"


# --------------------------------------------------------------------------- records.py

def test_doc_check_keeps_the_rules_version_and_page_limit_and_old_hashes_hold():
    store = pinned_store()
    assert digest(records.doc_checks(store["documents"], store["fields"], PINNED_IDS)) == PINNED_DOC_CHECKS
    whole, sections = records.document_check_report(store)
    assert digest([whole] + sections) == PINNED_DOCUMENT_REPORT
    whole, sections = records.field_check_report(store)
    assert digest([whole] + sections) == PINNED_FIELD_REPORT

    versioned = pinned_store()
    versioned["documents"][P1].update(rules_version="2026-10-21.1", max_pages=20, evaluated_at="2026-10-21T07:05:00",
                                      engine={"ocr": "easyocr 1.7.1", "text": "cache"}, portal_fp="p1")
    one, two = records.doc_checks(versioned["documents"], versioned["fields"], PINNED_IDS)
    assert (one["data"]["rules_version"], one["data"]["max_pages"]) == ("2026-10-21.1", 20)
    assert not {"evaluated_at", "engine", "portal_fp", "fingerprint"} & set(one["data"])
    assert one["content"] == records.doc_checks(store["documents"], store["fields"], PINNED_IDS)[0]["content"]
    assert "rules_version" not in two["data"]
    assert two["content_hash"] == records.doc_checks(store["documents"], store["fields"], PINNED_IDS)[1]["content_hash"]

    # a daily re-evaluation that changed nothing moves only evaluated_at: nothing to send again
    again = json.loads(json.dumps(versioned))
    again["documents"][P1]["evaluated_at"] = "2026-10-22T07:05:00"
    assert records.doc_checks(again["documents"], again["fields"], PINNED_IDS)[0]["content_hash"] == one["content_hash"]


def test_the_reports_count_error_and_not_confirmed_only_when_there_is_one():
    store = pinned_store()
    whole, sections = records.document_check_report(store)
    assert "ERROR" not in whole["content"] and not any("ERROR" in s["content"] for s in sections)
    whole, sections = records.field_check_report(store)
    assert "NOT CONFIRMED" not in whole["content"] and not any("NOT CONFIRMED" in s["content"] for s in sections)

    store["documents"][P1]["verdict"] = "ERROR"
    store["documents"][P1]["rows"][0].update(verdict="ERROR", has_error=True,
                                             detail="ERROR: could not check this file (file locked) — it is retried on the next pass")
    store["fields"][P2]["rows"][1].update(result="NOT CONFIRMED", detail="not found on a readable document")
    whole, sections = records.document_check_report(store)
    assert whole["content"].split("\n")[0] == "DOCUMENT CHECK — 2 students: ERROR 1, FAIL 1"
    assert whole["data"]["facts"]["verdicts"] == {"ERROR": 1, "FAIL": 1}
    one, two = (s["content"].split("\n") for s in sections)
    assert one == [f"{NAMES[P1]} ({P1}, KLP): ERROR", f"MISSING 0, FAIL 0, FLAG 1, PASS 0, ERROR 1; checked {DAY}"]
    assert two == [f"{NAMES[P2]} ({P2}, KLP): FAIL", f"MISSING 0, FAIL 0, FLAG 1, PASS 1; checked {DAY}"]

    whole, sections = records.field_check_report(store)
    assert [s["data"]["heading"] for s in sections[:2]] == [
        f"{NAMES[P1]} ({P1}, KLP): MATCH 1, DIFFERS 1, UNREADABLE 0, NO DOCUMENT 0, BLANK 0",
        f"{NAMES[P2]} ({P2}, KLP): MATCH 1, DIFFERS 0, UNREADABLE 0, NO DOCUMENT 0, BLANK 0, NOT CONFIRMED 1"]
    check, = [r for r in records.doc_checks(store["documents"], store["fields"]) if r["key"] == P2]
    assert check["data"]["field_rows"] == {"MATCH": 1, "NOT CONFIRMED": 1}
    verdict, = [r for r in records.field_checks(P2, store["fields"][P2]) if r["key"] == f"{P2}|Father"]
    assert verdict["content"].endswith("— NOT CONFIRMED. not found on a readable document.")
