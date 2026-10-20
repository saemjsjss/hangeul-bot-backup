"""Documents on this PC follow the portal's: verified_docs.run_local and the sync around it.

  replaced documents (B1)      the portal's ZIP names a document by its type (PASSPORT.jpg), so a
      replaced document comes back under the old one's name: the bytes decide. A changed one moves
      the old copy (and its original over 2 MB) to _replaced/<date>/, an equal one is left alone,
      nothing is ever deleted, and the marker ("v2\\n<docs>") is written only once all of it worked
  another extension (A6)       PASSPORT.jpg replaced by PASSPORT.pdf on a re-download
  the catch-up sweep (B1, A5)  students whose marker predates the comparison, 8 a run, oldest
      first, silent unless something was replaced; its circuit breaker and --confirm-replace
  Google Drive (B2)            a failed lookup never stops the downloads: the saved list is used
  the verified list (B19)      a list with too many rows lacking documents or a passport stops the
      downloads and touches no marker
  auto_sync                    the lines for all of it (exact strings), alerts at most once a day,
      and the document check's message before its reports (B14a)

Synthetic data only (SAMPLE names, passports A99900001..A99900009); the portal, Google Drive,
Telegram and the document check are fakes; every file lives in tmp_path.

Run from the BOT folder:
    .venv\\Scripts\\python.exe -m pytest tests\\test_verified_docs_replace.py -q
"""
import asyncio
import io
import json
import logging
import os
import sys
import zipfile
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

BOT_ROOT = Path(__file__).resolve().parent.parent
if str(BOT_ROOT) not in sys.path:
    sys.path.insert(0, str(BOT_ROOT))

from src.scraper import client as client_module  # noqa: E402
from src.sheets import auto_sync  # noqa: E402
from src.sheets import verified_docs as vd  # noqa: E402

PROG = "KOREAN LANGUAGE PROGRAM (KLP)"          # the portal's program, and its folder's name
WORDS = ["ZERO", "ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT", "NINE"]
TODAY = date.today().isoformat()


def zip_of(files):
    """A portal ZIP: {name: bytes}, each under docs/ as the portal zips them."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(f"docs/{name}", data)
    return buf.getvalue()


def corrupt_second_entry(data):
    """The same ZIP with one byte of its second entry flipped: reading that entry fails (bad CRC)."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        info = z.infolist()[1]
    start = info.header_offset + 30 + len(info.filename.encode()) + len(info.extra)
    out = bytearray(data)
    out[start] ^= 0xFF
    return bytes(out)


@pytest.fixture
def world(monkeypatch, tmp_path):
    """The portal (its verified list .students, the ZIPs by uid .zips, each ZIP fetched noted in
    .gets), the documents folder .root and the originals folder .originals in tmp_path. Google
    Drive is not asked unless a test says so (skip_drive_done=False)."""
    w = SimpleNamespace(students=[], zips={}, gets=[], root=tmp_path / "docs", originals=tmp_path / "originals",
                        drive_cache=tmp_path / "data" / "drive_complete_names.json")
    w.root.mkdir()

    async def get(url, params=None, timeout=None):
        w.gets.append(params["uid"])
        return SimpleNamespace(status_code=200, headers={"content-type": "application/zip"},
                               content=w.zips[params["uid"]])

    class Client:
        client = SimpleNamespace(get=get)
        base_url = "https://portal.test"

        async def login(self):
            return {"success": True}

        async def close(self):
            pass

    async def fetch(client):
        return [dict(s) for s in w.students]

    def drive():
        raise AssertionError("Google Drive is not asked in this test")

    monkeypatch.setattr(client_module, "admin_client", Client())
    monkeypatch.setattr(vd, "fetch_verified_students", fetch)
    monkeypatch.setattr(vd, "BACKUP_ROOT", w.originals)
    monkeypatch.setattr(vd, "DRIVE_NAMES_CACHE", w.drive_cache)
    monkeypatch.setattr(vd, "_drive_complete_names", drive)
    return w


def name_of(n):
    return f"SAMPLE STUDENT {WORDS[n % 10]}"


def add(w, n, zipped, docs=None, local=None, marker=None, originals=None):
    """Student n on the verified list (passport A9990000n), the portal's ZIP of `zipped`; on this
    PC the files `local`, the marker text `marker` (written as it is) and the originals over 2 MB
    `originals`. -> the student's folder."""
    passport = f"A9990000{(n - 1) % 9 + 1}"
    docs = f"passport_{n}_100.jpg" if docs is None else docs
    w.students.append({"uid": str(n), "name": name_of(n), "passport": passport, "program": PROG, "docs": docs})
    w.zips[str(n)] = zip_of(zipped) if isinstance(zipped, dict) else zipped
    folder = w.root / PROG / f"{name_of(n)} ({passport})"
    for name, data in (local or {}).items():
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(data)
    if marker is not None:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / vd.LOCAL_DONE_MARKER).write_text(marker, encoding="utf-8")
    for name, data in (originals or {}).items():
        (w.originals / PROG / folder.name).mkdir(parents=True, exist_ok=True)
        (w.originals / PROG / folder.name / name).write_bytes(data)
    return folder


def run(w, **kw):
    kw.setdefault("skip_drive_done", False)
    w.gets.clear()
    return asyncio.run(vd.run_local(w.root, **kw))


def files(folder):
    """{name: bytes} of the files directly in a folder (what the document check reads)."""
    return {f.name: f.read_bytes() for f in sorted(folder.iterdir()) if f.is_file() and f.name != vd.LOCAL_DONE_MARKER}


def set_aside(folder):
    """{name: bytes} of what went to folder/_replaced/<today>/ (the only day there)."""
    where = folder / vd.REPLACED_DIR
    if not where.exists():
        return {}
    assert [d.name for d in where.iterdir()] == [TODAY]
    return files(where / TODAY)


def marker(folder):
    return (folder / vd.LOCAL_DONE_MARKER).read_text(encoding="utf-8")


def originals_of(w, folder):
    return w.originals / PROG / folder.name


# --------------------------------------------------------------------------- the marker

def test_the_marker_records_its_format_and_the_portals_document_list(tmp_path):
    assert vd._read_marker(tmp_path) == ("", None)                       # never downloaded
    vd._write_marker(tmp_path, "academic_1_100.pdf|passport_1_100.jpg")
    assert (tmp_path / vd.LOCAL_DONE_MARKER).read_text(encoding="utf-8") == \
        "v2\nacademic_1_100.pdf|passport_1_100.jpg"
    assert vd._read_marker(tmp_path) == ("v2", "academic_1_100.pdf|passport_1_100.jpg")
    assert not (tmp_path / (vd.LOCAL_DONE_MARKER + ".part")).exists()
    for legacy, docs in (("passport_1_100.jpg", "passport_1_100.jpg"), ("ok", "ok"), ("", "")):
        (tmp_path / vd.LOCAL_DONE_MARKER).write_text(legacy, encoding="utf-8")
        assert vd._read_marker(tmp_path) == ("v1", docs)


# --------------------------------------------------------------------------- replaced documents (B1)

def test_a_changed_document_replaces_the_old_copy_which_is_kept_with_its_original(world):
    folder = add(world, 1, {"PASSPORT.jpg": b"new passport", "PHOTO.jpg": b"photo"},
                 docs="passport_1_200.jpg|photo_1_100.jpg", marker="v2\npassport_1_100.jpg|photo_1_100.jpg",
                 local={"PASSPORT.jpg": b"old passport, shrunk", "PHOTO.jpg": b"photo"},
                 originals={"PASSPORT.jpg": b"old passport, original"})
    result = run(world)
    assert result["redownloaded"] == [(PROG, "SAMPLE STUDENT ONE (A99900001)", 0, [], 1)]
    assert files(folder) == {"PASSPORT.jpg": b"new passport", "PHOTO.jpg": b"photo"}
    assert set_aside(folder) == {"PASSPORT.jpg": b"old passport, shrunk"}
    assert set_aside(originals_of(world, folder)) == {"PASSPORT.jpg": b"old passport, original"}
    assert files(originals_of(world, folder)) == {}                     # the new one is under 2 MB: no original
    assert marker(folder) == "v2\npassport_1_200.jpg|photo_1_100.jpg"
    assert [f.name for f in folder.iterdir() if f.name.endswith(".part")] == []

    # replaced again the same day: the earlier old copy is kept too, under a name of its own
    world.students[0]["docs"] = "passport_1_300.jpg|photo_1_100.jpg"
    world.zips["1"] = zip_of({"PASSPORT.jpg": b"newer passport", "PHOTO.jpg": b"photo"})
    assert run(world)["redownloaded"] == [(PROG, "SAMPLE STUDENT ONE (A99900001)", 0, [], 1)]
    assert set_aside(folder) == {"PASSPORT (2).jpg": b"new passport", "PASSPORT.jpg": b"old passport, shrunk"}
    assert files(folder)["PASSPORT.jpg"] == b"newer passport"


def test_a_redownload_whose_files_are_all_the_same_replaces_nothing_and_says_so(world):
    folder = add(world, 2, {"PASSPORT.jpg": b"passport", "ACADEMIC CERTIFICATE.pdf": b"academic"},
                 docs="academic_2_200.pdf|passport_2_100.jpg", marker="v2\nacademic_2_100.pdf|passport_2_100.jpg",
                 local={"PASSPORT.jpg": b"passport", "ACADEMIC CERTIFICATE.pdf": b"academic"})
    before = {f.name: f.stat().st_mtime_ns for f in folder.iterdir() if f.name != vd.LOCAL_DONE_MARKER}
    result = run(world)
    assert result["redownloaded"] == [(PROG, "SAMPLE STUDENT TWO (A99900002)", 0, [], 0)]
    assert {f.name: f.stat().st_mtime_ns for f in folder.iterdir() if f.name != vd.LOCAL_DONE_MARKER} == before
    assert not (folder / vd.REPLACED_DIR).exists()
    assert marker(folder) == "v2\nacademic_2_200.pdf|passport_2_100.jpg"
    assert auto_sync.doc_lines(result) == [
        "📁 1 student(s) changed their documents on the portal:",
        "   • SAMPLE STUDENT TWO (A99900002) — KOREAN LANGUAGE PROGRAM (KLP), re-downloaded: every file is the "
        "same as before (no document replaced)"]


def test_a_shrunk_copy_whose_original_is_unchanged_is_not_replaced(world):
    folder = add(world, 3, {"BANK CERTIFICATE.pdf": b"bank, original"}, docs="bank_3_200.pdf",
                 marker="v2\nbank_3_100.pdf", local={"BANK CERTIFICATE.pdf": b"bank, shrunk"},
                 originals={"BANK CERTIFICATE.pdf": b"bank, original"})
    assert run(world)["redownloaded"] == [(PROG, "SAMPLE STUDENT THREE (A99900003)", 0, [], 0)]
    assert files(folder) == {"BANK CERTIFICATE.pdf": b"bank, shrunk"}
    assert files(originals_of(world, folder)) == {"BANK CERTIFICATE.pdf": b"bank, original"}
    assert not (folder / vd.REPLACED_DIR).exists()


def test_a_zip_that_breaks_halfway_changes_nothing_and_leaves_the_marker(world):
    good = zip_of({"PASSPORT.jpg": b"new passport", "PHOTO.jpg": b"new photo"})
    folder = add(world, 4, corrupt_second_entry(good), docs="passport_4_200.jpg|photo_4_200.jpg",
                 marker="v2\npassport_4_100.jpg|photo_4_100.jpg",
                 local={"PASSPORT.jpg": b"old passport", "PHOTO.jpg": b"old photo"})
    result = run(world)
    assert result["redownloaded"] == [] and [n for n, _ in result["failed"]] == ["SAMPLE STUDENT FOUR (A99900004)"]
    assert files(folder) == {"PASSPORT.jpg": b"old passport", "PHOTO.jpg": b"old photo"}   # read first, then changed
    assert marker(folder) == "v2\npassport_4_100.jpg|photo_4_100.jpg"

    world.zips["4"] = good                                               # the next run does it all
    assert run(world)["redownloaded"] == [(PROG, "SAMPLE STUDENT FOUR (A99900004)", 0, [], 2)]
    assert marker(folder) == "v2\npassport_4_200.jpg|photo_4_200.jpg"


def test_a_failure_while_replacing_leaves_the_marker_and_the_next_run_finishes(world, monkeypatch):
    folder = add(world, 5, {"PASSPORT.jpg": b"new passport", "PHOTO.jpg": b"new photo"},
                 docs="passport_5_200.jpg|photo_5_200.jpg", marker="v2\npassport_5_100.jpg|photo_5_100.jpg",
                 local={"PASSPORT.jpg": b"old passport", "PHOTO.jpg": b"old photo"})
    real, calls = vd._set_aside, []

    def second_fails(path, base, day):
        calls.append(path.name)
        if len(calls) == 2:
            raise PermissionError("the file is open in another program")
        return real(path, base, day)

    monkeypatch.setattr(vd, "_set_aside", second_fails)
    result = run(world)
    assert result["redownloaded"] == [] and "open in another program" in result["failed"][0][1]
    assert marker(folder) == "v2\npassport_5_100.jpg|photo_5_100.jpg"   # not written: done again next run
    assert files(folder) == {"PASSPORT.jpg": b"new passport", "PHOTO.jpg": b"old photo"}

    monkeypatch.setattr(vd, "_set_aside", real)
    assert run(world)["redownloaded"] == [(PROG, "SAMPLE STUDENT FIVE (A99900005)", 0, [], 1)]
    assert files(folder) == {"PASSPORT.jpg": b"new passport", "PHOTO.jpg": b"new photo"}
    assert set_aside(folder) == {"PASSPORT.jpg": b"old passport", "PHOTO.jpg": b"old photo"}
    assert marker(folder) == "v2\npassport_5_200.jpg|photo_5_200.jpg"


def test_a_png_shrunk_to_jpg_is_compared_through_its_original(world):
    folder = add(world, 6, {"PHOTO.png": b"photo, original"}, docs="photo_6_200.png", marker="v2\nphoto_6_100.png",
                 local={"PHOTO.jpg": b"photo, shrunk"}, originals={"PHOTO.png": b"photo, original"})
    assert run(world)["redownloaded"] == [(PROG, "SAMPLE STUDENT SIX (A99900006)", 0, [], 0)]
    assert files(folder) == {"PHOTO.jpg": b"photo, shrunk"}              # the .jpg is that entry: kept (A6)

    world.students[0]["docs"] = "photo_6_300.png"
    world.zips["6"] = zip_of({"PHOTO.png": b"another photo"})
    assert run(world)["redownloaded"] == [(PROG, "SAMPLE STUDENT SIX (A99900006)", 0, [], 1)]
    assert files(folder) == {"PHOTO.png": b"another photo"}
    assert set_aside(folder) == {"PHOTO.jpg": b"photo, shrunk"}
    assert set_aside(originals_of(world, folder)) == {"PHOTO.png": b"photo, original"}


def test_a_document_uploaded_again_as_another_type_replaces_the_old_one(world):
    folder = add(world, 7, {"PASSPORT.pdf": b"passport pdf", "PHOTO.jpg": b"photo"},
                 docs="passport_7_200.pdf|photo_7_100.jpg", marker="v2\npassport_7_100.jpg|photo_7_100.jpg",
                 local={"PASSPORT.jpg": b"passport jpg", "PHOTO.jpg": b"photo", "CV.pdf": b"cv"})
    result = run(world)
    assert result["redownloaded"] == [(PROG, "SAMPLE STUDENT SEVEN (A99900007)", 0, [], 1)]   # replaced, not added
    assert files(folder) == {"CV.pdf": b"cv", "PASSPORT.pdf": b"passport pdf", "PHOTO.jpg": b"photo"}
    assert set_aside(folder) == {"PASSPORT.jpg": b"passport jpg"}
    assert auto_sync.doc_lines(result)[1] == ("   • SAMPLE STUDENT SEVEN (A99900007) — KOREAN LANGUAGE PROGRAM "
                                              "(KLP), 1 document(s) replaced (old copies kept in _replaced)")


def test_a_first_download_saves_every_file_and_writes_the_marker(world):
    folder = add(world, 8, {"PASSPORT.jpg": b"passport", "PHOTO.jpg": b"photo"}, docs="passport_8_100.jpg|photo_8_100.jpg")
    result = run(world)
    assert result["saved"] == [(PROG, "SAMPLE STUDENT EIGHT (A99900008)", 2, [], 0)]
    assert marker(folder) == "v2\npassport_8_100.jpg|photo_8_100.jpg"
    assert run(world)["saved"] == [] and world.gets == []               # nothing changed: not fetched again


def test_a_row_without_its_document_list_resets_no_marker(world):
    legacy = add(world, 1, {"PASSPORT.jpg": b"passport"}, docs="", marker="ok", local={"PASSPORT.jpg": b"passport"})
    current = add(world, 2, {"PASSPORT.jpg": b"passport"}, docs="", marker="v2\npassport_2_100.jpg",
                  local={"PASSPORT.jpg": b"passport"})
    result = run(world)
    assert world.gets == [] and result["redownloaded"] == result["refreshed"] == []
    assert marker(legacy) == "ok" and marker(current) == "v2\npassport_2_100.jpg"


# --------------------------------------------------------------------------- the catch-up sweep

def legacy_students(w, count, start=1):
    """Students downloaded before the comparison existed: the old marker (the document list
    itself), every file on this PC the same as the portal's; marker times oldest last."""
    folders = []
    for n in range(start, start + count):
        docs = f"passport_{n}_100.jpg"
        folder = add(w, n, {"PASSPORT.jpg": b"passport %d" % n}, docs=docs, marker=docs,
                     local={"PASSPORT.jpg": b"passport %d" % n})
        at = 1_790_000_000 - n * 100                                    # student 1 newest, the last one oldest
        os.utime(folder / vd.LOCAL_DONE_MARKER, (at, at))
        folders.append(folder)
    return folders


def test_the_sweep_compares_eight_old_markers_a_run_oldest_first_and_says_nothing(world):
    folders = legacy_students(world, 9)
    folders[0].joinpath(vd.LOCAL_DONE_MARKER).write_text("ok", encoding="utf-8")   # older still: no list at all
    os.utime(folders[0] / vd.LOCAL_DONE_MARKER, (1_700_000_000, 1_700_000_000))
    result = run(world)
    assert world.gets == ["1", "9", "8", "7", "6", "5", "4", "3"]            # by marker age, REFRESH_PER_RUN of them
    assert len(result["refreshed"]) == vd.REFRESH_PER_RUN == 8
    assert all(r[2:] == (0, [], 0) for r in result["refreshed"])
    assert auto_sync.doc_lines(result) == []                                 # silent: nothing was replaced
    assert [vd._read_marker(f)[0] for f in folders] == ["v2"] + ["v1"] + ["v2"] * 7
    assert marker(folders[0]) == "v2\npassport_1_100.jpg"
    assert all(not (f / vd.REPLACED_DIR).exists() for f in folders)

    assert run(world)["refreshed"] == [(PROG, "SAMPLE STUDENT TWO (A99900002)", 0, [], 0)]
    assert run(world)["refreshed"] == [] and world.gets == []               # all caught up


def test_the_sweep_saves_a_replacement_that_was_missed_and_says_so(world):
    folder = add(world, 1, {"PASSPORT.jpg": b"passport", "PHOTO.jpg": b"photo", "SSC.pdf": b"ssc, replaced"},
                 docs="passport_1_100.jpg|photo_1_100.jpg|ssc_1_300.pdf",
                 marker="passport_1_100.jpg|photo_1_100.jpg|ssc_1_300.pdf",
                 local={"PASSPORT.jpg": b"passport", "PHOTO.jpg": b"photo", "SSC.pdf": b"ssc, first upload"})
    result = run(world)
    assert result["refreshed"] == [(PROG, "SAMPLE STUDENT ONE (A99900001)", 0, [], 1)]
    assert files(folder)["SSC.pdf"] == b"ssc, replaced" and set_aside(folder) == {"SSC.pdf": b"ssc, first upload"}
    assert marker(folder) == "v2\npassport_1_100.jpg|photo_1_100.jpg|ssc_1_300.pdf"
    assert auto_sync.doc_lines(result) == [
        "📁 1 student(s) had documents replaced on the portal earlier that were missed — now saved:",
        "   • SAMPLE STUDENT ONE (A99900001) — KOREAN LANGUAGE PROGRAM (KLP), 1 document(s) replaced "
        "(old copies kept in _replaced)"]


def test_the_sweep_leaves_a_shrunk_copy_without_its_original_alone(world):
    folder = add(world, 2, {"PHOTO.png": b"photo", "PASSPORT.jpg": b"passport"},
                 docs="passport_2_100.jpg|photo_2_100.png", marker="passport_2_100.jpg|photo_2_100.png",
                 local={"PHOTO.jpg": b"photo, shrunk long ago", "PASSPORT.jpg": b"passport"})
    result = run(world)
    assert result["refreshed"] == [(PROG, "SAMPLE STUDENT TWO (A99900002)", 0, [], 0)]
    assert files(folder) == {"PASSPORT.jpg": b"passport", "PHOTO.jpg": b"photo, shrunk long ago"}
    assert vd._read_marker(folder)[0] == "v2"


def test_the_breaker_holds_back_a_student_with_more_than_half_the_files_changed(world):
    folder = add(world, 1, {"PASSPORT.jpg": b"p2", "PHOTO.jpg": b"f2", "SSC.pdf": b"ssc"},
                 docs="passport_1_100.jpg|photo_1_100.jpg|ssc_1_100.pdf",
                 marker="passport_1_100.jpg|photo_1_100.jpg|ssc_1_100.pdf",
                 local={"PASSPORT.jpg": b"p1", "PHOTO.jpg": b"f1", "SSC.pdf": b"ssc"})
    half = add(world, 2, {"PASSPORT.jpg": b"p2", "PHOTO.jpg": b"photo"}, docs="passport_2_100.jpg|photo_2_100.jpg",
               marker="passport_2_100.jpg|photo_2_100.jpg", local={"PASSPORT.jpg": b"p1", "PHOTO.jpg": b"photo"})
    result = run(world)
    assert result["held"] == [(PROG, "SAMPLE STUDENT ONE (A99900001)", 2, 3)]
    assert files(folder) == {"PASSPORT.jpg": b"p1", "PHOTO.jpg": b"f1", "SSC.pdf": b"ssc"}   # untouched
    assert marker(folder) == "passport_1_100.jpg|photo_1_100.jpg|ssc_1_100.pdf"           # tried again next run
    assert result["refreshed"] == [(PROG, "SAMPLE STUDENT TWO (A99900002)", 0, [], 1)]     # exactly half: replaced
    assert files(half)["PASSPORT.jpg"] == b"p2"

    state = {}
    alert = ["⚠️ The portal ZIP differs for 2 file(s) of 1 student(s) the catch-up check compared — check before "
             "replacing (nothing was replaced):",
             "   • SAMPLE STUDENT ONE (A99900001) — KOREAN LANGUAGE PROGRAM (KLP), 2 of 3 file(s) differ",
             "   To replace them once checked: python -m src.sheets.verified_docs --confirm-replace <passport number>"]
    assert auto_sync.doc_lines(result, state)[2:] == alert              # after the student who was replaced
    again = run(world)
    assert again["held"] == result["held"] and auto_sync.doc_lines(again, state) == []    # once a day
    state["daily_alerts"]["docs_held"]["day"] = "2026-01-01"
    assert auto_sync.doc_lines(again, state) == alert                                     # a new day: again


def test_the_breaker_holds_back_every_student_when_more_than_two_would_be_replaced(world):
    def one_changed(n):
        return add(world, n, {"PASSPORT.jpg": b"new %d" % n, "PHOTO.jpg": b"photo", "SSC.pdf": b"ssc"},
                   docs=f"passport_{n}_100.jpg|photo_{n}_100.jpg|ssc_{n}_100.pdf",
                   marker=f"passport_{n}_100.jpg|photo_{n}_100.jpg|ssc_{n}_100.pdf",
                   local={"PASSPORT.jpg": b"old %d" % n, "PHOTO.jpg": b"photo", "SSC.pdf": b"ssc"})

    folders = [one_changed(n) for n in (1, 2, 3)]
    result = run(world)
    assert sorted(result["held"]) == sorted((PROG, f"{name_of(n)} (A9990000{n})", 1, 3) for n in (1, 2, 3))
    assert result["refreshed"] == []
    assert all(files(f)["PASSPORT.jpg"].startswith(b"old") and vd._read_marker(f)[0] == "v1" for f in folders)

    world.students.pop()                                                 # two would be replaced: both are
    result = run(world)
    assert result["held"] == [] and sorted(r[4] for r in result["refreshed"]) == [1, 1]
    assert [files(f)["PASSPORT.jpg"] for f in folders[:2]] == [b"new 1", b"new 2"]


def test_confirm_replace_replaces_what_the_breaker_held_back_for_that_student_only(world):
    held = add(world, 1, {"PASSPORT.jpg": b"p2", "PHOTO.jpg": b"f2"}, docs="passport_1_100.jpg|photo_1_100.jpg",
               marker="passport_1_100.jpg|photo_1_100.jpg", local={"PASSPORT.jpg": b"p1", "PHOTO.jpg": b"f1"})
    current = add(world, 2, {"PASSPORT.jpg": b"portal copy"}, docs="passport_2_100.jpg",
                  marker="v2\npassport_2_100.jpg", local={"PASSPORT.jpg": b"local copy"})
    other = add(world, 3, {"PASSPORT.jpg": b"x2"}, docs="passport_3_100.jpg", marker="passport_3_100.jpg",
                local={"PASSPORT.jpg": b"x1"})
    assert run(world)["held"][0][1] == "SAMPLE STUDENT ONE (A99900001)"

    result = run(world, confirm_replace=[" a99900001", "A99900002"])
    assert sorted(world.gets) == ["1", "2"]                                  # only those two
    assert sorted(result["refreshed"]) == [(PROG, "SAMPLE STUDENT ONE (A99900001)", 0, [], 2),
                                           (PROG, "SAMPLE STUDENT TWO (A99900002)", 0, [], 1)]
    assert files(held) == {"PASSPORT.jpg": b"p2", "PHOTO.jpg": b"f2"} and marker(held).startswith("v2\n")
    assert files(current) == {"PASSPORT.jpg": b"portal copy"}
    assert set_aside(current) == {"PASSPORT.jpg": b"local copy"}
    assert files(other) == {"PASSPORT.jpg": b"x1"}


# --------------------------------------------------------------------------- Google Drive (B2)

def test_a_good_drive_lookup_is_saved_and_its_finished_students_skipped(world, monkeypatch):
    add(world, 1, {"PASSPORT.jpg": b"p"})
    add(world, 2, {"PASSPORT.jpg": b"p"})
    monkeypatch.setattr(vd, "_drive_complete_names", lambda: {"SAMPLE STUDENT ONE (A99900001)"})
    result = run(world, skip_drive_done=True)
    assert world.gets == ["2"] and "drive_lookup_error" not in result
    assert json.loads(world.drive_cache.read_text(encoding="utf-8"))["names"] == ["SAMPLE STUDENT ONE (A99900001)"]


def test_a_failed_drive_lookup_never_stops_the_downloads_and_uses_the_saved_list(world, monkeypatch, caplog):
    add(world, 1, {"PASSPORT.jpg": b"p"})
    add(world, 2, {"PASSPORT.jpg": b"p"})

    def expired():
        raise RuntimeError("invalid_grant: Token has been expired or revoked.")

    monkeypatch.setattr(vd, "_drive_complete_names", expired)
    with caplog.at_level(logging.WARNING, logger=vd.logger.name):
        result = run(world, skip_drive_done=True)                       # nothing saved yet: none skipped
    assert sorted(world.gets) == ["1", "2"] and "invalid_grant" in result["drive_lookup_error"]
    assert len([r for r in caplog.records if r.name == vd.logger.name]) == 1
    assert not world.drive_cache.exists()
    assert auto_sync.doc_lines(result)[0] == "📁 2 newly verified student(s) — documents saved:"   # not in Telegram

    world.drive_cache.parent.mkdir(parents=True, exist_ok=True)
    world.drive_cache.write_text(json.dumps({"names": ["SAMPLE STUDENT THREE (A99900003)"]}), encoding="utf-8")
    add(world, 3, {"PASSPORT.jpg": b"p"})
    add(world, 4, {"PASSPORT.jpg": b"p"})
    result = run(world, skip_drive_done=True)
    assert world.gets == ["4"] and result["drive_lookup_error"]          # the saved list was honoured


# --------------------------------------------------------------------------- the verified list (B19)

def rows(count, without_docs=0, without_passport=0):
    out = [{"uid": str(n), "name": name_of(n), "passport": f"A9990000{(n - 1) % 9 + 1}", "program": PROG,
            "docs": f"passport_{n}_100.jpg"} for n in range(1, count + 1)]
    for r in out[:without_docs]:
        r["docs"] = ""
    for r in out[len(out) - without_passport:] if without_passport else []:
        r["passport"] = ""
    return out


def test_the_list_looks_wrong_when_more_than_a_tenth_of_its_rows_lack_documents_or_a_passport():
    assert vd.layout_alert(rows(10, without_docs=1)) == ""                      # a tenth: still plausible
    assert vd.layout_alert(rows(10, without_docs=2)) == "2 of 10 row(s) show no document list and 0 no passport number"
    assert vd.layout_alert(rows(20, without_docs=1, without_passport=2)) == \
        "1 of 20 row(s) show no document list and 2 no passport number"
    assert vd.layout_alert(rows(9, without_docs=9)) == ""                       # too few rows to tell
    assert "SAMPLE" not in vd.layout_alert(rows(10, without_passport=5))        # counts only, never a name


def test_a_list_that_looks_wrong_downloads_nothing_and_touches_no_marker(world):
    world.students = rows(10, without_docs=3)
    for s in world.students:
        world.zips[s["uid"]] = zip_of({"PASSPORT.jpg": b"p"})
    folder = world.root / PROG / f"{name_of(1)} (A99900001)"
    folder.mkdir(parents=True)
    (folder / vd.LOCAL_DONE_MARKER).write_text("passport_1_100.jpg", encoding="utf-8")
    os.utime(folder / vd.LOCAL_DONE_MARKER, (1_790_000_000, 1_790_000_000))
    result = run(world)
    assert result["layout_alert"] == "3 of 10 row(s) show no document list and 0 no passport number"
    assert world.gets == [] and result["saved"] == result["redownloaded"] == result["refreshed"] == []
    assert len(result["students"]) == 10                                 # as read
    assert marker(folder) == "passport_1_100.jpg"
    assert (folder / vd.LOCAL_DONE_MARKER).stat().st_mtime == 1_790_000_000
    assert [p.name for p in (world.root / PROG).iterdir()] == [folder.name]


# --------------------------------------------------------------------------- auto_sync: the lines

def test_every_kind_of_document_line_in_its_exact_words():
    result = {"saved": [(PROG, "SAMPLE STUDENT ONE (A99900001)", 3, ["a", "b"], 0),
                        (PROG, "SAMPLE STUDENT TWO (A99900002)", 0, [], 0)],
              "redownloaded": [(PROG, "SAMPLE STUDENT THREE (A99900003)", 0, [], 2),
                               (PROG, "SAMPLE STUDENT FOUR (A99900004)", 2, [], 0),
                               (PROG, "SAMPLE STUDENT FIVE (A99900005)", 1, ["c"], 1),
                               (PROG, "SAMPLE STUDENT SIX (A99900006)", 0, [], 0)],
              "refreshed": [(PROG, "SAMPLE STUDENT SEVEN (A99900007)", 0, [], 0),
                            (PROG, "SAMPLE STUDENT EIGHT (A99900008)", 0, [], 3)],
              "held": [(PROG, "SAMPLE STUDENT NINE (A99900009)", 3, 4)],
              "failed": [("SAMPLE STUDENT ONE (A99900001)", "portal returned 502 text/html instead of a ZIP")]}
    k = "KOREAN LANGUAGE PROGRAM (KLP)"
    assert auto_sync.doc_lines(result) == [
        "📁 2 newly verified student(s) — documents saved:",
        f"   • SAMPLE STUDENT ONE (A99900001) — {k}, 3 file(s), 2 compressed to under 2 MB",
        f"   • SAMPLE STUDENT TWO (A99900002) — {k}, no new files (already on this PC)",
        "📁 4 student(s) changed their documents on the portal:",
        f"   • SAMPLE STUDENT THREE (A99900003) — {k}, 2 document(s) replaced (old copies kept in _replaced)",
        f"   • SAMPLE STUDENT FOUR (A99900004) — {k}, 2 new file(s)",
        f"   • SAMPLE STUDENT FIVE (A99900005) — {k}, 1 document(s) replaced (old copies kept in _replaced), "
        "1 new file(s), 1 compressed to under 2 MB",
        f"   • SAMPLE STUDENT SIX (A99900006) — {k}, re-downloaded: every file is the same as before "
        "(no document replaced)",
        "📁 1 student(s) had documents replaced on the portal earlier that were missed — now saved:",
        f"   • SAMPLE STUDENT EIGHT (A99900008) — {k}, 3 document(s) replaced (old copies kept in _replaced)",
        "⚠️ The portal ZIP differs for 3 file(s) of 1 student(s) the catch-up check compared — check before "
        "replacing (nothing was replaced):",
        f"   • SAMPLE STUDENT NINE (A99900009) — {k}, 3 of 4 file(s) differ",
        "   To replace them once checked: python -m src.sheets.verified_docs --confirm-replace <passport number>",
        "⚠️ Could not download documents for SAMPLE STUDENT ONE (A99900001): portal returned 502 text/html "
        "instead of a ZIP (will retry next run)"]
    assert auto_sync.doc_lines({"saved": [], "redownloaded": [], "failed": []}) == []


def test_a_list_that_looks_wrong_holds_back_the_documents_and_is_told_once_a_day(monkeypatch, tmp_path):
    async def run_local(root, limit=0, skip_drive_done=True):
        assert root == tmp_path
        return {"saved": [], "redownloaded": [], "refreshed": [], "held": [], "failed": [],
                "students": rows(10, without_docs=2), "layout_alert": "2 of 10 row(s) show no document list"}

    monkeypatch.setattr(vd, "run_local", run_local)
    monkeypatch.setattr(auto_sync, "DOCS_ROOT", tmp_path)
    state, cloud = {}, {}
    line = "⚠️ The verified-students list looks wrong: 2 of 10 row(s) show no document list — downloads paused, nothing reset"
    assert auto_sync.sync_docs(cloud, state) == [line]
    assert cloud["documents"] is None and str(cloud["docs_error"]) == "2 of 10 row(s) show no document list"
    assert auto_sync.sync_docs({}, state) == []                          # the same day: once
    state["daily_alerts"]["docs_layout"]["day"] = "2026-01-01"
    assert auto_sync.sync_docs({}, state) == [line]
    assert auto_sync._once_a_day(None, "docs_layout", line) == [line]    # no state: always


# --------------------------------------------------------------------------- auto_sync: the check's messages (B14a)

@pytest.fixture
def check(monkeypatch, tmp_path):
    """auto_sync.run_once over fakes: no sheet or document news, publishing on (the record handed
    over kept in .handed), Telegram a recorder (.events), and the document check a stub whose
    result (.result) carries its message lines (.result["lines"]); .fault makes it raise."""
    from src.cloud import sheet_hooks
    from src.verify import auto_verify as av
    w = SimpleNamespace(events=[], handed=[], fault=None, notes=[],
                        result={"lines": ["🔍 Documents checked: 1", "   • SAMPLE STUDENT ONE (KLP) — PASS"],
                                "rollout_lines": [], "reports_due": True})
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(auto_sync, "DATA_DIR", data)
    monkeypatch.setattr(auto_sync, "STATE_PATH", data / "sheet_state.json")
    monkeypatch.setattr(auto_sync, "LOCK_PATH", data / "auto_sync.lock")
    monkeypatch.setattr(auto_sync, "DOCS_ROOT", tmp_path / "docs")
    monkeypatch.setattr(vd, "BACKUP_ROOT", tmp_path / "originals")
    monkeypatch.setattr(vd, "DRIVE_NAMES_CACHE", data / "drive_complete_names.json")
    monkeypatch.setattr(auto_sync, "_fresh_portal_client", lambda: None)
    monkeypatch.setattr(auto_sync, "sync_sheets", lambda state: [])
    monkeypatch.setattr(auto_sync, "sync_docs", lambda cloud=None, state=None: [])

    def notify(lines, title=auto_sync.SYNC_TITLE):
        w.events.append(("sent", title, list(lines)))
        return True

    def run(budget=6, only=None, recheck=False):
        w.events.append(("checked",))
        if w.fault:
            raise w.fault
        return w.result

    def finish_reports(result):
        w.events.append(("reports", bool(result.get("reports_due"))))
        return list(w.notes)

    monkeypatch.setattr(auto_sync, "notify", notify)
    monkeypatch.setattr(av, "STORE_PATH", tmp_path / "verification" / "results.json")
    monkeypatch.setattr(av, "run", run)
    monkeypatch.setattr(av, "summary_lines", lambda result: list(result["lines"]))
    monkeypatch.setattr(av, "finish_reports", finish_reports, raising=False)
    monkeypatch.setattr(av, "ROLLOUT_TITLE", "🔍 Document check — rules updated", raising=False)
    monkeypatch.setattr(sheet_hooks, "on", lambda: True)
    monkeypatch.setattr(sheet_hooks, "store_readable", lambda path: True)
    monkeypatch.setattr(sheet_hooks, "after_sync", lambda cloud: w.handed.append(cloud))
    return w


def test_the_check_message_goes_out_before_the_reports_and_their_note_after(check):
    check.result["rollout_lines"] = ["Rules unversioned -> 2026-10-21.1: 1 student(s) re-evaluated"]
    check.notes = ["📄 DOCUMENT CHECK.xlsx was open in Excel — this pass saved 'DOCUMENT CHECK (updated 09-15).xlsx'"]
    lines = auto_sync.run_once()
    assert check.events == [
        ("checked",),
        ("sent", "🔍 Document check", ["🔍 Documents checked: 1", "   • SAMPLE STUDENT ONE (KLP) — PASS"]),
        ("sent", "🔍 Document check — rules updated", ["Rules unversioned -> 2026-10-21.1: 1 student(s) re-evaluated"]),
        ("reports", True),
        ("sent", "🔍 Document check", check.notes)]
    cloud, = check.handed
    assert [t for t, _, _ in cloud["sent"]] == ["🔍 Document check", "🔍 Document check — rules updated",
                                                "🔍 Document check"]
    assert cloud["verify"] is check.result
    assert lines == check.result["lines"] + check.result["rollout_lines"] + check.notes


def test_the_reports_are_written_even_when_nothing_is_announced_and_nothing_sent_when_off(check):
    check.result["lines"] = []
    auto_sync.run_once(send=False)
    assert check.events == [("checked",), ("reports", True)]


def test_a_check_that_could_not_run_is_told_from_the_third_run_in_a_row(check):
    check.fault = RuntimeError("the rules failed: a code fault")
    for _ in range(2):
        auto_sync.run_once()
    assert [e for e in check.events if e[0] != "checked"] == []         # silent twice
    assert all(isinstance(c["verify_error"], RuntimeError) for c in check.handed)
    auto_sync.run_once()
    assert check.events[-1] == ("sent", "🔍 Document check",
                                ["⚠️ Document check has failed 3 times in a row: the rules failed: a code fault"])
    check.fault, check.events[:] = None, []
    auto_sync.run_once()
    assert check.events[1] == ("sent", "🔍 Document check", check.result["lines"] + [
        "✅ Document check is working again (it had failed 3 times in a row)."])
    assert "verify" not in json.loads(auto_sync.STATE_PATH.read_text(encoding="utf-8"))["failures"]


@pytest.mark.parametrize("line", ["⚠️ Document check stopped: results.json could not be read — nothing was overwritten",
                                  "⚠️ Document check paused: pyzbar is missing — no student was checked"])
def test_a_stopped_or_paused_check_is_told_once_a_day(check, line):
    check.result.update(lines=[line], reports_due=False)
    auto_sync.run_once()
    auto_sync.run_once()
    assert [e for e in check.events if e[0] == "sent"] == [("sent", "🔍 Document check", [line])]
    state = json.loads(auto_sync.STATE_PATH.read_text(encoding="utf-8"))
    key, = state["daily_alerts"]
    state["daily_alerts"][key]["day"] = "2026-01-01"
    auto_sync.STATE_PATH.write_text(json.dumps(state), encoding="utf-8")
    auto_sync.run_once()
    assert [e for e in check.events if e[0] == "sent"] == [("sent", "🔍 Document check", [line])] * 2
