"""The document check's orchestration (src/verify/auto_verify.py, src/verify/text_cache.py):
results.json kept safe, the 20-page text cache and the image-facts cache, the queue, read-error
retries, re-evaluation from the saved text (the roll-out of new rules, portal edits, the daily
re-check), the Telegram lines, the two reports and the command line.

The rules themselves are tested elsewhere (tests/test_verify_rules.py and the other
test_verify_* files). Here doc_verifier's verify_student, student_folders and portal_students
and field_check's check_student are small fakes that read every file through the patched
readers, so what the caches serve, miss or read again is visible:

  a line "PASS: x" / "FLAG: x" / "FAIL: x" / "ERROR: x"  is that finding
  "NEW-RULE <finding>" / "OLD-RULE <finding>"            only under the new / old rules
  "EXPIRES 2026-10-20"                                   FAIL once dv.TODAY is past that date
  "FATHER <name>"                                        FLAG unless the portal's Father matches
  a file starting "LOCKED"                               cannot be read (CheckError)

Every name and passport is synthetic (SAMPLE STUDENT n, A9990000n); every file is written under
tmp_path. A session guard (addendum A7) fails the run when anything under the live
C:\\Hangeul\\BOT\\data, the documents folders or the originals folder is written while the
tests run.

Run from the BOT folder:
    .venv\\Scripts\\python.exe -m pytest tests\\test_verify_orchestration.py -q
"""
import datetime as dt
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

import pytest

BOT_ROOT = Path(__file__).resolve().parent.parent
if str(BOT_ROOT) not in sys.path:
    sys.path.insert(0, str(BOT_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.config import settings  # noqa: E402
from src.verify import auto_verify as av  # noqa: E402
from src.verify import common as C  # noqa: E402
from src.verify import doc_verifier as dv  # noqa: E402
from src.verify import field_check as fc  # noqa: E402
from src.verify import page_checks as PC  # noqa: E402
from src.verify import text_cache as tc  # noqa: E402
from verify_fixtures import PASSPORTS, make_pdf  # noqa: E402
from verify_fixtures import student as make_student  # noqa: E402

P1, P2, P3, P4, P5 = PASSPORTS[:5]
VERSION = C.RULES_VERSION

# doc_verifier.PORTAL_KEYS_READ (plan §3.5), stubbed until WP1 is merged.
PORTAL_KEYS_READ = ("Full Name", "Father", "Mother", "DOB", "Gender", "Passport No", "Passport Expiry",
                    "Sponsor", "SSC GPA", "HSC GPA", "Program")


# --------------------------------------------------------------------------- live-data guard (A7)

def _norm(path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


_PROTECTED = sorted({_norm(p) for p in (Path(r"C:\Hangeul\BOT\data"), settings.docs_root(),
                                         settings.docs_originals_root(), settings.konyang_root())})
_GUARD: Dict[str, list] = {"extra": [], "writes": []}
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC
_PATH_EVENTS = {"os.rename": 2, "os.replace": 2, "shutil.copyfile": 2, "shutil.copytree": 2, "shutil.move": 2,
                "os.link": 2, "os.symlink": 2, "os.remove": 1, "os.rmdir": 1, "os.mkdir": 1,
                "os.truncate": 1, "os.utime": 1, "os.chmod": 1, "shutil.rmtree": 1}


def _guarded(path) -> Optional[str]:
    try:
        p = _norm(path)
    except Exception:
        return None
    for root in _PROTECTED + _GUARD["extra"]:
        if p == root or p.startswith(root.rstrip("\\/") + os.sep):
            return p
    return None


def _audit(event, args):
    """Every write this test process makes (open for writing, rename, replace, copy, delete,
    mkdir, utime) under a protected folder. Writes by the live bot itself, which runs beside the
    tests, are not this process's and are not seen."""
    try:
        if event == "open":
            path, mode, flags = args
            if path is None or isinstance(path, int):
                return
            writing = (any(c in mode for c in "wax+") if isinstance(mode, str)
                       else isinstance(flags, int) and bool(flags & _WRITE_FLAGS))
            hit = _guarded(path) if writing else None
            if hit:
                _GUARD["writes"].append(f"open({mode or flags}) {hit}")
        elif event in _PATH_EVENTS:
            for a in args[:_PATH_EVENTS[event]]:
                if isinstance(a, (str, bytes, os.PathLike)):
                    hit = _guarded(a)
                    if hit:
                        _GUARD["writes"].append(f"{event} {hit}")
    except Exception:
        pass


sys.addaudithook(_audit)        # installed at collection: it sees every test of the session


@pytest.fixture(scope="session", autouse=True)
def _live_data_untouched():
    """At the end of the session: nothing under the live data, documents or originals folders
    was created or changed by the tests (A7)."""
    yield
    writes = sorted(set(_GUARD["writes"]))
    assert not writes, ("the tests wrote under the live data / documents folders:\n   "
                        + "\n   ".join(writes[:40]))


def test_the_live_data_guard_sees_a_write_by_this_process(tmp_path):
    root = _norm(tmp_path / "live")
    _GUARD["extra"].append(root)
    try:
        (tmp_path / "live").mkdir()
        (tmp_path / "live" / "results.json").write_text("{}", encoding="utf-8")
        os.replace(tmp_path / "live" / "results.json", tmp_path / "live" / "results.json.bak")
        seen = [w for w in _GUARD["writes"] if root in w]
        assert any(w.startswith("os.mkdir") for w in seen)
        assert any(w.startswith("open(") and w.endswith("results.json") for w in seen)
        assert any(w.startswith("os.rename") for w in seen)              # os.replace audits as os.rename
        assert not [w for w in _GUARD["writes"] if _norm(tmp_path / "elsewhere") in w]
    finally:
        _GUARD["extra"].remove(root)
        _GUARD["writes"][:] = [w for w in _GUARD["writes"] if root not in w]


def test_the_guard_watches_the_live_folders():
    assert _norm(r"C:\Hangeul\BOT\data") in _PROTECTED
    assert _norm(settings.docs_root()) in _PROTECTED and _norm(settings.docs_originals_root()) in _PROTECTED


# --------------------------------------------------------------------------- isolation

_PATH_NAMES = ("REPORT_DIR", "STORE_PATH", "TEXT_DIR", "IMAGES_DIR", "LOCK_PATH", "DOC_REPORT", "FIELD_REPORT")


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    """Every test's check lives in tmp_path/verification; whatever a test rebinds (the CLI's
    --data-dir) is put back afterwards."""
    for name in _PATH_NAMES:
        monkeypatch.setattr(av, name, getattr(av, name))
    monkeypatch.setattr(tc, "TEXT_DIR", tc.TEXT_DIR)
    monkeypatch.setattr(tc, "IMAGES_DIR", tc.IMAGES_DIR)
    monkeypatch.setattr(av, "_last_written", None)
    monkeypatch.setattr(av, "_issue_memo", None)
    av.use_data_dir(tmp_path / "verification")
    av.REPORT_DIR.mkdir(parents=True)
    return av.REPORT_DIR


TITLES = {"passport": "01 Passport", "birth": "04 Birth Certificate", "father": "05 Father NID",
          "family": "06 Family Relationship Certificate", "academic": "07 Academic Certificate & Transcript",
          "bank": "08 Bank Solvency & Statement"}
_FINDING = re.compile(r"^(PASS|NOTE|FLAG|FAIL|ERROR): (.+)$")


class World:
    """Students on disk and on the portal, the fake rules, and what was read."""

    def __init__(self, tmp_path: Path):
        self.docs = tmp_path / "docs"
        self.students: Dict[str, Dict[str, str]] = {}
        self.folders: Dict[str, Path] = {}
        self.reads: List[str] = []              # files read through the real (fake) reader
        self.inspects: List[str] = []
        self.inspect_fails: set = set()
        self.explode: set = set()
        self.required = ["passport"]
        self.new_rules = False
        self.portal_down = False
        self.now = dt.datetime(2026, 10, 21, 10, 0)
        self.ticks = 0.0
        self.tick = 0.0
        self._mtime = 1_790_000_000

    # -- building ------------------------------------------------------------------
    def add(self, n: int, files: Dict[str, str], **over) -> str:
        pas = PASSPORTS[n - 1]
        row = make_student(Passport_No=pas, Full_Name=f"SAMPLE STUDENT {n}", **over)
        folder = self.docs / "KOREAN LANGUAGE PROGRAM (KLP)" / f"{row['Full Name']} ({pas})"
        folder.mkdir(parents=True, exist_ok=True)
        self.students[pas], self.folders[pas] = row, folder
        for name, text in files.items():
            self.write(pas, name, text)
        self._mtime += 10
        os.utime(folder, (self._mtime, self._mtime))        # pending's order: the order added
        return pas

    def write(self, pas: str, name: str, text: str) -> Path:
        p = self.folders[pas] / name
        p.write_text(text, encoding="utf-8")
        self._mtime += 10
        os.utime(p, (self._mtime, self._mtime))
        return p

    def set_now(self, when: dt.datetime) -> None:
        self.now = when
        dv.TODAY = when.date()

    def legacy(self) -> None:
        """results.json and the text caches as the code before this release left them: no
        rules version, portal fingerprint or evaluation time, rows without their document key,
        field fingerprints of the old kind, whole-file text kept under the old 6-page limit."""
        store = json.loads(av.STORE_PATH.read_text(encoding="utf-8"))
        for rec in store["documents"].values():
            for k in ("rules_version", "portal_fp", "evaluated_at", "first_checked", "max_pages", "engine", "folder"):
                rec.pop(k, None)
            for r in rec["rows"]:
                r.pop("key", None)
                r.pop("has_error", None)
        for rec in store["fields"].values():
            rec.pop("rules_version", None)
            rec.pop("evaluated_at", None)
            rec["fingerprint"] = "an older field fingerprint"
        store["meta"] = {}
        av.STORE_PATH.write_text(json.dumps(store, indent=1), encoding="utf-8")
        for f in av.TEXT_DIR.glob("*.json"):
            cache = json.loads(f.read_text(encoding="utf-8"))
            f.write_text(json.dumps({(k[:-3] + ":6" if k.endswith(":20") and not k.startswith("PAGES:") else k): v
                                     for k, v in cache.items()}), encoding="utf-8")

    def store(self) -> dict:
        return json.loads(av.STORE_PATH.read_text(encoding="utf-8"))

    # -- the fakes -----------------------------------------------------------------
    def read_document(self, path, max_pages=20):
        p = Path(path)
        self.reads.append(p.name)
        text = p.read_text(encoding="utf-8")
        if text.startswith("LOCKED"):
            raise C.CheckError(f"{p.name} is locked by another program")
        return text, [(595, 842)]

    def read_pages(self, path, max_pages=20, sideways=None):
        p = Path(path)
        self.reads.append(f"pages:{p.name}")
        return [p.read_text(encoding="utf-8")]

    def inspect(self, path):
        self.inspects.append(Path(path).name)
        if str(Path(path)) in self.inspect_fails:
            raise C.CheckError("the page could not be rendered")
        return {"saturation": 12.5, "qr": [], "qr_seen": False, "pages": 1, "digital": True}

    def rules(self, text: str, row: Dict[str, str]):
        out = []
        for line in text.splitlines():
            line = line.strip()
            for tag, wanted in (("NEW-RULE ", True), ("OLD-RULE ", False)):
                if line.startswith(tag):
                    line = line[len(tag):] if self.new_rules == wanted else ""
            m = _FINDING.match(line)
            if m:
                out.append((m.group(1), m.group(2)))
                continue
            m = re.match(r"^EXPIRES (\d{4}-\d{2}-\d{2})$", line)
            if m:
                until = dt.date.fromisoformat(m.group(1))
                out.append(("FAIL", f"expired on {until}") if dv.TODAY > until else ("PASS", f"valid until {until}"))
                continue
            m = re.match(r"^FATHER (.+)$", line)
            if m:
                same = m.group(1).strip().upper() == row.get("Father", "").upper()
                out.append(("PASS", "the father's name matches the portal") if same
                           else ("FLAG", "the father's name differs from the portal"))
        return out or [("PASS", "present")]

    def verify_student(self, folder, row, program):
        self.ticks += self.tick
        pas = row["Passport No"]
        if pas in self.explode:
            raise ValueError("the folder could not be listed")
        rows, present = [], set()
        for f in sorted(Path(folder).iterdir()):
            if not f.is_file() or f.name.startswith("."):
                continue
            key = f.stem.lower()
            present.add(key)
            title = TITLES.get(key, f.stem)
            try:
                text, _ = dv.read_document(f)
                PC.inspect(f)
            except C.CheckError as e:
                rows.append({"doc": title, "file": f.name, "key": key, "verdict": "ERROR", "has_error": True,
                             "detail": f"ERROR: could not check this file ({e}) — it is retried on the next pass"})
                continue
            findings = self.rules(text, row)
            r = {"doc": title, "file": f.name, "key": key, "verdict": C.row_verdict(findings),
                 "detail": " | ".join(f"{v}: {d}" for v, d in findings)}
            if C.has_error(findings):
                r["has_error"] = True
            rows.append(r)
        for key in self.required:
            if key not in present:
                rows.append({"doc": TITLES[key], "file": "", "key": key, "verdict": "MISSING",
                             "detail": "required by the guideline but not uploaded"})
        return rows

    def check_student(self, pas, folder, row):
        texts = []
        for f in sorted(Path(folder).iterdir()):
            if f.is_file() and not f.name.startswith("."):
                try:
                    texts.append(fc.read_document(f)[0])
                except Exception:
                    texts.append("")
        named = re.findall(r"^FATHER (.+)$", "\n".join(texts), re.M)
        father = row.get("Father", "")
        if not named:
            result = "NOT CONFIRMED"
        else:
            result = "MATCH" if any(n.strip().upper() == father.upper() for n in named) else "DIFFERS"
        return [{"field": "Father", "portal": father, "result": result, "detail": "from the fake field check"},
                {"field": "Full Name", "portal": row.get("Full Name", ""), "result": "MATCH", "detail": "found"}]

    def student_folders(self, portal=None):
        return {p: (f, f.name) for p, f in self.folders.items() if f.exists()}

    def portal_students(self, rows=None):
        if rows is not None:
            return {str(r["Passport No"]).upper(): dict(r) for r in rows if r.get("Passport No")}
        if self.portal_down:
            raise RuntimeError("the portal must not be read in this test")
        return {p: dict(s) for p, s in self.students.items()}

    def checked_portal_values(self, row):
        return {k: row.get(k, "") for k in ("Full Name", "Surname", "Given Name", "DOB", "Gender", "Father", "Mother",
                                            "SSC GPA", "SSC School", "HSC GPA", "Passport No", "Passport Expiry",
                                            "Program", "Intake")}


@pytest.fixture
def world(tmp_path, monkeypatch):
    w = World(tmp_path)
    monkeypatch.setattr(av, "preflight", lambda: "")
    monkeypatch.setattr(av, "_now", lambda: w.now)
    monkeypatch.setattr(av, "_clock", lambda: w.ticks)
    monkeypatch.setattr(dv, "TODAY", w.now.date())
    monkeypatch.setattr(dv, "PORTAL_KEYS_READ", PORTAL_KEYS_READ, raising=False)
    monkeypatch.setattr(fc, "checked_portal_values", w.checked_portal_values, raising=False)
    monkeypatch.setattr(dv, "program_of", lambda row: "KLP")
    monkeypatch.setattr(dv, "student_folders", w.student_folders)
    monkeypatch.setattr(dv, "portal_students", w.portal_students)
    monkeypatch.setattr(dv, "verify_student", w.verify_student)
    monkeypatch.setattr(fc, "check_student", w.check_student)
    monkeypatch.setattr(dv, "read_document", w.read_document)
    monkeypatch.setattr(fc, "read_document", w.read_document)
    monkeypatch.setattr(dv, "read_pages", w.read_pages)
    monkeypatch.setattr(PC, "inspect", w.inspect)
    monkeypatch.setattr(PC, "seal_present", lambda path: False)
    monkeypatch.setattr(PC, "page_codes", lambda path, max_pages=20: [[]])
    monkeypatch.setattr(dv, "_reader", None)
    from src.sheets import passport_issue
    monkeypatch.setattr(passport_issue, "load", lambda: {})
    return w


def report_line() -> str:
    return f"   Reports: DOCUMENT CHECK.xlsx / FIELD CHECK.xlsx in {av.REPORT_DIR}"


# --------------------------------------------------------------------------- results.json

STORE_ONE = {"documents": {P1: {"student": "SAMPLE STUDENT 1", "verdict": "PASS", "rows": []}},
             "fields": {}, "corrections": [], "meta": {}}


def test_a_missing_store_is_a_fresh_start():
    assert av.load_store() == {"documents": {}, "fields": {}, "corrections": [], "meta": {}}


def test_an_unreadable_store_goes_on_from_the_backup_and_is_kept_aside():
    av.STORE_PATH.write_text("{ not json", encoding="utf-8")
    av._bak_path().write_text(json.dumps(STORE_ONE), encoding="utf-8")
    store = av.load_store()
    assert store["documents"] == STORE_ONE["documents"]
    assert "unreadable" in store["_note"] and "results.json.bak" in store["_note"]
    (kept,) = av.REPORT_DIR.glob("results.corrupt-*.json")
    assert kept.read_text(encoding="utf-8") == "{ not json"
    av.save_store(store)
    saved = json.loads(av.STORE_PATH.read_text(encoding="utf-8"))
    assert "_note" not in saved and saved["documents"] == STORE_ONE["documents"]
    # the unreadable file never became the backup
    assert json.loads(av._bak_path().read_text(encoding="utf-8"))["documents"] == STORE_ONE["documents"]


def test_an_unreadable_store_without_a_backup_stops_and_changes_nothing():
    av.STORE_PATH.write_bytes(b"{ not json")
    with pytest.raises(av.StoreUnreadable) as e:
        av.load_store()
    assert "no results.json.bak" in str(e.value)
    assert av.STORE_PATH.read_bytes() == b"{ not json"
    assert [p.read_bytes() for p in av.REPORT_DIR.glob("results.corrupt-*.json")] == [b"{ not json"]


def test_a_missing_store_with_a_backup_is_restored_not_started_over():
    av._bak_path().write_text(json.dumps(STORE_ONE), encoding="utf-8")
    store = av.load_store()
    assert store["documents"] == STORE_ONE["documents"] and "missing" in store["_note"]


def test_each_save_keeps_the_previous_store_and_the_days_first(monkeypatch):
    monkeypatch.setattr(av, "_now", lambda: dt.datetime(2026, 10, 21, 9, 0))
    v = [{**STORE_ONE, "documents": {P1: {"verdict": verdict, "rows": []}}} for verdict in ("PASS", "REVIEW", "FAIL")]
    av.save_store(v[0])
    assert not av._bak_path().exists()
    av.save_store(v[1])
    daily = av.REPORT_DIR / "results-2026-10-21.json"
    assert json.loads(av._bak_path().read_text(encoding="utf-8"))["documents"][P1]["verdict"] == "PASS"
    assert json.loads(daily.read_text(encoding="utf-8"))["documents"][P1]["verdict"] == "PASS"
    av.save_store(v[2])
    assert json.loads(av._bak_path().read_text(encoding="utf-8"))["documents"][P1]["verdict"] == "REVIEW"
    assert json.loads(daily.read_text(encoding="utf-8"))["documents"][P1]["verdict"] == "PASS"
    assert json.loads(av.STORE_PATH.read_text(encoding="utf-8"))["documents"][P1]["verdict"] == "FAIL"


def test_only_seven_daily_copies_are_kept(monkeypatch):
    monkeypatch.setattr(av, "_now", lambda: dt.datetime(2026, 10, 21, 9, 0))
    for day in range(1, 10):
        (av.REPORT_DIR / f"results-2026-10-{day:02d}.json").write_text("{}", encoding="utf-8")
    av.STORE_PATH.write_text(json.dumps(STORE_ONE), encoding="utf-8")
    av.save_store(STORE_ONE)
    left = sorted(p.name for p in av.REPORT_DIR.glob("results-2026-*.json"))
    assert left == [f"results-2026-10-{d:02d}.json" for d in (4, 5, 6, 7, 8, 9, 21)]


def test_a_store_holding_students_is_never_replaced_by_an_empty_one():
    av.save_store(STORE_ONE)
    with pytest.raises(av.StoreShrinkRefused):
        av.save_store(av._empty_store())
    assert json.loads(av.STORE_PATH.read_text(encoding="utf-8"))["documents"]
    av.save_store(av._empty_store(), allow_shrink=True)
    assert json.loads(av.STORE_PATH.read_text(encoding="utf-8"))["documents"] == {}


# --------------------------------------------------------------------------- the lock

def test_the_lock_is_taken_once_and_cleared_when_its_process_is_gone():
    assert av._take_lock() and not av._take_lock()
    av._release_lock()
    assert not av.LOCK_PATH.exists()
    av.LOCK_PATH.write_text("99999999", encoding="utf-8")         # a process that is not running
    assert av._take_lock() and av.LOCK_PATH.read_text(encoding="utf-8") == str(os.getpid())
    av._release_lock()
    av.LOCK_PATH.write_text(str(os.getppid()), encoding="utf-8")  # a live process: left alone
    assert not av._take_lock()
    old = time.time() - av.LOCK_STALE_SECONDS - 60                 # ...unless it is hours old
    os.utime(av.LOCK_PATH, (old, old))
    assert av._take_lock()
    av._release_lock()


def test_a_pass_while_another_runs_is_busy_and_says_nothing(world):
    world.add(1, {"passport.pdf": "PASS: valid"})
    av.LOCK_PATH.write_text(str(os.getppid()), encoding="utf-8")
    r = av.run()
    assert r["busy"] and r["checked"] == [] and r["store"] is None
    assert av.summary_lines(r) == [] and world.reads == []
    assert av.LOCK_PATH.read_text(encoding="utf-8") == str(os.getppid())


# --------------------------------------------------------------------------- preflight

def test_preflight_names_what_is_missing(monkeypatch):
    import importlib.util
    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a: None if name == "easyocr" else real(name, *a))
    assert av.preflight() == "easyocr not installed"
    monkeypatch.setattr(importlib.util, "find_spec", real)
    monkeypatch.setattr(PC, "zbar_available", lambda: (False, "the zbar library was not found"), raising=False)
    assert av.preflight() == "the QR reader (pyzbar) is not working: the zbar library was not found"
    monkeypatch.setattr(PC, "zbar_available", lambda: (True, "pyzbar 0.1.9"), raising=False)
    assert av.preflight() == ""


def test_a_failed_preflight_stops_the_pass_before_anything(world, monkeypatch):
    world.add(1, {"passport.pdf": "PASS: valid"})
    monkeypatch.setattr(av, "preflight", lambda: "the QR reader (pyzbar) cannot load: ImportError: no zbar")
    r = av.run()
    assert r["stopped"] and r["store"] is None and r["checked"] == [] and not r["reports_due"]
    assert not av.STORE_PATH.exists() and world.reads == []
    assert av.summary_lines(r) == ["⚠️ Document check paused: the QR reader (pyzbar) cannot load: ImportError: "
                                   "no zbar — no student was checked"]


def test_an_unreadable_store_stops_the_pass_and_overwrites_nothing(world):
    world.add(1, {"passport.pdf": "PASS: valid"})
    av.STORE_PATH.write_bytes(b"{ broken")
    r = av.run()
    assert r["store_error"] and r["store"] is None and r["checked"] == []
    assert av.STORE_PATH.read_bytes() == b"{ broken" and world.reads == []
    assert av.summary_lines(r) == [f"⚠️ Document check stopped: {r['store_error']} — nothing was overwritten"]


def test_a_store_restored_from_the_backup_says_so_and_goes_on(world):
    world.add(1, {"passport.pdf": "PASS: valid"})
    av.run()
    av._bak_path().write_text(av.STORE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    av.STORE_PATH.write_text("{ broken", encoding="utf-8")
    world.add(2, {"passport.pdf": "PASS: valid"})
    r = av.run()
    assert "results.json.bak" in r["store_note"] and [e["passport"] for e in r["checked"]] == [P2]
    assert av.summary_lines(r)[0] == f"⚠️ {r['store_note']}"
    assert set(world.store()["documents"]) == {P1, P2}


def test_folders_or_portal_coming_back_nearly_empty_pause_the_pass(world):
    for n in range(1, 7):
        world.add(n, {"passport.pdf": "PASS: valid"})
    av.run(budget=0)
    for p in PASSPORTS[:5]:
        shutil.rmtree(world.folders[p])                     # the documents drive half gone
    before = av.STORE_PATH.read_bytes()
    r = av.run()
    assert "only 1 of the 6 checked students" in r["stopped"] and r["store"] is None
    assert av.STORE_PATH.read_bytes() == before


# --------------------------------------------------------------------------- fingerprints and the queue

def test_the_field_fingerprint_ignores_workflow_fields_and_counts_what_is_compared(world):
    pas = world.add(1, {"passport.pdf": "PASS: valid"})
    folder, row = world.folders[pas], world.students[pas]
    fp = av.field_fingerprint(folder, row)
    assert av.field_fingerprint(folder, {**row, "Current Stage": "Visa"}) == fp
    assert av.field_fingerprint(folder, {**row, "Father": "SAMPLE OTHER FATHER"}) != fp
    assert av.field_fingerprint(folder, {**row, "SSC School": "SAMPLE OTHER SCHOOL"}) != fp
    assert av.field_fingerprint(folder, row, {pas: "2021-01-01"}) != fp            # the issue-date cache
    assert av.portal_fp({**row, "Current Stage": "Visa"}) == av.portal_fp(row)
    assert av.portal_fp({**row, "Father": "SAMPLE OTHER FATHER"}) != av.portal_fp(row)
    assert av.portal_fp({**row, "SSC School": "SAMPLE OTHER SCHOOL"}) == av.portal_fp(row)


def test_pending_reads_new_students_first_then_changed_files_then_missing_text_then_retries(world):
    for n in range(1, 6):
        world.add(n, {"passport.pdf": "PASS: valid"})
    av.run(budget=0)
    store = world.store()
    store["documents"][P1]["needs_ocr"] = True                       # text never saved
    store["documents"][P2]["fingerprint"] = ""                       # a read error to retry
    store["documents"][P2]["retry"] = {"count": 1, "error": "x", "at": "2026-10-21T09:00:00"}
    av.STORE_PATH.write_text(json.dumps(store), encoding="utf-8")
    world.write(P3, "passport.pdf", "PASS: valid, scanned again")   # files changed
    world.students[P4]["Father"] = "SAMPLE OTHER FATHER"             # a portal edit only
    world.add(7, {"passport.pdf": "PASS: valid"})                    # new, added later
    world.add(6, {"passport.pdf": "PASS: valid"})
    folders, portal = world.student_folders(), world.portal_students()
    assert av.pending(av.load_store(), folders, portal) == [PASSPORTS[6], PASSPORTS[5], P3, P1, P2]


def test_a_current_stage_edit_changes_nothing_and_a_father_edit_is_re_evaluated_not_read(world):
    pas = world.add(1, {"passport.pdf": "PASS: valid", "father.pdf": "FATHER SAMPLE FATHER"})
    av.run()
    reads, evaluated = list(world.reads), world.store()["documents"][pas]["evaluated_at"]
    world.set_now(dt.datetime(2026, 10, 21, 11, 0))
    world.students[pas]["Current Stage"] = "Visa"
    r = av.run()
    assert r["checked"] == r["reevaluated"] == r["republish"] == [] and not r["waiting"]
    assert world.reads == reads and world.store()["documents"][pas]["evaluated_at"] == evaluated

    world.students[pas]["Father"] = "SAMPLE OTHER FATHER"
    r = av.run()
    assert world.reads == reads and r["checked"] == []               # from the saved text: no OCR
    assert r["republish"] == [pas]
    assert r["reevaluated"] == [{"passport": pas, "student": "SAMPLE STUDENT 1", "program": "KLP", "old": "PASS",
                                 "new": "REVIEW", "reason": "05 Father NID: the father's name differs from the portal",
                                 "why": "portal"}]
    assert av.summary_lines(r) == [
        "🔍 Documents checked: 1",
        "   • SAMPLE STUDENT 1 (KLP) — PASS -> REVIEW (05 Father NID: the father's name differs from the portal) "
        "[portal values changed]",
        report_line()]
    store = world.store()
    assert {r["field"]: r["result"] for r in store["fields"][pas]["rows"]}["Father"] == "DIFFERS"
    assert [(c["field"], c["was"], c["now"]) for c in store["corrections"]] == [
        ("Father", "SAMPLE FATHER", "SAMPLE OTHER FATHER")]
    assert store["documents"][pas]["evaluated_at"] == "2026-10-21T11:00:00"


# --------------------------------------------------------------------------- the text cache (B5, A4)

@pytest.fixture
def readers(monkeypatch):
    """Counting stand-ins for the real readers (the originals text_cache falls back to)."""
    calls: Dict[str, list] = {"doc": [], "pages": [], "inspect": [], "codes": []}
    answers: Dict[str, object] = {}

    def read_document(path, max_pages=20):
        calls["doc"].append((Path(path).name, max_pages))
        pages = min(tc.page_count(path) or 1, max_pages)               # one size per page read, as for real
        return answers.get("doc") or (f"TEXT OF {Path(path).name}", [(595, 842)] * pages)

    def read_pages(path, max_pages=20, sideways=None):
        calls["pages"].append((Path(path).name, max_pages))
        return list(answers["pages"] if "pages" in answers else ["PAGE ONE"])

    def inspect(path):
        calls["inspect"].append(Path(path).name)
        return dict(answers.get("inspect") or {"saturation": 9.5, "qr": ["https://example.org/x"], "pages": 1})

    def page_codes(path, max_pages=20):
        calls["codes"].append((Path(path).name, max_pages))
        if answers.get("codes_fail"):
            raise C.CheckError("pyzbar could not decode")
        return [["https://apostille.example/1"], []]

    for module, name, fn in ((dv, "read_document", read_document), (fc, "read_document", read_document),
                             (dv, "read_pages", read_pages), (PC, "inspect", inspect),
                             (PC, "page_codes", page_codes), (PC, "seal_present", lambda path: True)):
        monkeypatch.setattr(module, name, fn)
    return calls, answers


def _key(path: Path, n: int, pages: bool = False) -> str:
    st = path.stat()
    return f"{'PAGES:' if pages else ''}{path.name}:{st.st_size}:{int(st.st_mtime)}:{n}"


def _seed(pas: str, entries: Dict[str, object]) -> Path:
    path = av.TEXT_DIR / f"{pas}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries), encoding="utf-8")
    return path


def test_a_short_file_read_under_the_old_6_page_limit_serves_the_20_page_read(tmp_path, readers):
    calls, _ = readers
    two = make_pdf(tmp_path / "BIRTH.pdf", ["page one", "page two"])
    six = make_pdf(tmp_path / "BANK.pdf", [f"page {i}" for i in range(6)])
    ten = make_pdf(tmp_path / "ACADEMIC.pdf", [f"page {i}" for i in range(10)])
    cache = _seed(P1, {_key(two, 6): {"text": "OLD TWO", "sizes": [[595, 842], [595, 842]]},
                       _key(six, 6): {"text": "OLD SIX", "sizes": [[595, 842]] * 6},
                       _key(ten, 6): {"text": "OLD TEN", "sizes": [[595, 842]] * 6}})
    h = av.install_text_cache(P1, cache_only=True)
    try:
        assert dv.read_document(two) == ("OLD TWO", [(595, 842), (595, 842)])     # fewer pages than its limit
        assert dv.read_document(six)[0] == "OLD SIX"                              # every page (page count 6)
        with pytest.raises(C.NotCached):
            dv.read_document(ten)                                                 # 6 of 10 pages: not enough
    finally:
        av.save_text_cache(h)
    assert calls["doc"] == [] and h.misses == ["ACADEMIC.pdf"]
    saved = json.loads(cache.read_text(encoding="utf-8"))
    assert saved[_key(two, 20)] == {"text": "OLD TWO", "sizes": [[595, 842], [595, 842]]}
    assert _key(ten, 20) not in saved


def test_pages_read_one_by_one_serve_the_whole_file_and_smaller_page_reads(tmp_path, readers):
    calls, _ = readers
    three = make_pdf(tmp_path / "NID.pdf", ["one", "two", "three"])
    _seed(P1, {_key(three, 20, pages=True): {"pages": ["P1", "P2", "P3"], "sideways": [2]},
               "PAGES:BIG.pdf:1:1:20": {"pages": [f"p{i}" for i in range(12)], "sideways": [2, 9]}})
    big = tmp_path / "BIG.pdf"
    big.write_bytes(b"x")
    os.utime(big, (1, 1))
    h = av.install_text_cache(P1, cache_only=True)
    try:
        assert dv.read_document(three) == ("P1\nP2\nP3", [(595, 842)] * 3)
        turned: List[int] = []
        assert dv.read_pages(big, 8, turned) == [f"p{i}" for i in range(8)] and turned == [2]
        assert fc.read_document is dv.read_document
    finally:
        av.save_text_cache(h)
    assert calls["doc"] == calls["pages"] == [] and h.misses == []


def test_in_cache_only_mode_a_miss_is_not_read_but_recorded(tmp_path, readers):
    calls, _ = readers
    f = make_pdf(tmp_path / "PASSPORT.pdf", ["passport"])
    h = av.install_text_cache(P1, cache_only=True)
    try:
        with pytest.raises(C.NotCached) as e:
            dv.read_document(f)
        with pytest.raises(C.NotCached):
            dv.read_pages(f, 8)
    finally:
        av.save_text_cache(h)
    assert isinstance(e.value, C.CheckError) and "PASSPORT.pdf" in str(e.value)
    assert calls["doc"] == calls["pages"] == [] and h.misses == ["PASSPORT.pdf"]
    assert not (av.TEXT_DIR / f"{P1}.json").exists()


def test_a_normal_read_happens_once_and_is_kept_under_the_20_page_key(tmp_path, readers):
    calls, _ = readers
    f = make_pdf(tmp_path / "PASSPORT.pdf", ["passport", "visa page"])
    for _ in range(2):
        h = av.install_text_cache(P1)
        try:
            assert dv.read_document(f) == ("TEXT OF PASSPORT.pdf", [(595, 842)] * 2)
            assert dv.read_pages(f, 8) == ["PAGE ONE"]
        finally:
            av.save_text_cache(h)
    assert calls["doc"] == [("PASSPORT.pdf", 20)] and calls["pages"] == [("PASSPORT.pdf", 8)]
    saved = json.loads((av.TEXT_DIR / f"{P1}.json").read_text(encoding="utf-8"))
    assert set(saved) == {_key(f, 20), _key(f, 8, pages=True)}
    assert h.text_source == "cache"
    one = make_pdf(tmp_path / "PHOTO.pdf", ["photo"])                   # a one-page file read whole...
    h = av.install_text_cache(P1)
    try:
        dv.read_document(one)
    finally:
        av.save_text_cache(h)
    h = av.install_text_cache(P1, cache_only=True)
    try:
        assert dv.read_pages(one, 8) == ["TEXT OF PHOTO.pdf"]            # ...gives its only page too
    finally:
        av.save_text_cache(h)
    assert calls["pages"] == [("PASSPORT.pdf", 8)]
    from src.cloud import records                       # the key format Supabase reads is unchanged
    assert records._cache_entry(_key(f, 20)) == (False, "PASSPORT.pdf", f.stat().st_size, int(f.stat().st_mtime), 20)


def test_a_failed_open_is_never_kept_so_the_next_pass_reads_the_file_again(tmp_path, readers):
    calls, answers = readers
    f = make_pdf(tmp_path / "BANK.pdf", ["bank", "statement"])
    answers["doc"] = ("[unreadable: cannot open broken document]", [])
    answers["pages"] = []
    h = av.install_text_cache(P1)
    try:
        assert dv.read_document(f)[0].startswith("[unreadable:")
        assert dv.read_pages(f) == []
    finally:
        av.save_text_cache(h)
    assert not (av.TEXT_DIR / f"{P1}.json").exists()
    answers.clear()
    h = av.install_text_cache(P1)
    try:
        assert dv.read_document(f)[0] == "TEXT OF BANK.pdf"            # read fresh, not the failure
        assert dv.read_pages(f) == ["PAGE ONE"]
    finally:
        av.save_text_cache(h)
    assert len(calls["doc"]) == len(calls["pages"]) == 2
    _seed(P2, {_key(f, 20): {"text": "[unreadable: cached by older code]", "sizes": []}})
    h = av.install_text_cache(P2, cache_only=True)
    try:
        with pytest.raises(C.NotCached):
            dv.read_document(f)                                           # an old cached failure is a miss
    finally:
        av.save_text_cache(h)


def test_image_facts_are_kept_even_in_cache_only_mode_and_errors_never(tmp_path, readers):
    calls, answers = readers
    f = make_pdf(tmp_path / "ACADEMIC.pdf", ["academic"])
    for cache_only in (True, False):
        h = av.install_text_cache(P1, cache_only=cache_only)
        try:
            assert PC.inspect(f)["qr"] == ["https://example.org/x"]
            assert PC.page_codes(f) == [["https://apostille.example/1"], []]
            assert PC.seal_present(f) is True
        finally:
            av.save_text_cache(h)
    assert calls["inspect"] == ["ACADEMIC.pdf"] and calls["codes"] == [("ACADEMIC.pdf", 20)]
    images = json.loads((av.IMAGES_DIR / f"{P1}.json").read_text(encoding="utf-8"))
    st = f.stat()
    assert set(images) == {f"inspect:ACADEMIC.pdf:{st.st_size}:{int(st.st_mtime)}:",
                           f"page_codes:ACADEMIC.pdf:{st.st_size}:{int(st.st_mtime)}:20",
                           f"seal_present:ACADEMIC.pdf:{st.st_size}:{int(st.st_mtime)}:"}
    answers["codes_fail"] = True
    answers["inspect"] = {"saturation": None, "error": "RuntimeError: cannot render"}
    g = make_pdf(tmp_path / "BANK.pdf", ["bank"])
    for _ in range(2):
        h = av.install_text_cache(P1)
        try:
            with pytest.raises(C.CheckError):
                PC.page_codes(g)
            assert PC.inspect(g)["error"]
        finally:
            av.save_text_cache(h)
    assert len([c for c in calls["codes"] if c[0] == "BANK.pdf"]) == 2
    assert calls["inspect"].count("BANK.pdf") == 2


def test_uninstall_puts_the_real_readers_back(readers):
    before = (dv.read_document, fc.read_document, dv.read_pages, PC.inspect, PC.seal_present, PC.page_codes)
    h = av.install_text_cache(P1)
    assert dv.read_document is not before[0] and PC.page_codes is not before[5]
    inner = av.install_text_cache(P2)              # installed twice: the inner one reads the real reader
    assert dv.read_document._text_cache_original is before[0]
    av.save_text_cache(inner)
    av.save_text_cache(h)
    assert (dv.read_document, fc.read_document, dv.read_pages, PC.inspect, PC.seal_present, PC.page_codes) == before


def test_engine_info_says_what_read_the_files(monkeypatch):
    monkeypatch.setattr(dv, "_reader", None)
    info = tc.engine_info()
    assert set(info) == {"ocr", "device", "pymupdf", "pyzbar"}
    assert info["ocr"].startswith("easyocr ") and info["device"] == "not used"
    assert info["pyzbar"] in ("available", "missing") and info["pymupdf"]
    monkeypatch.setattr(dv, "_reader", type("Reader", (), {"device": "cuda"})())
    assert tc.engine_info()["device"] == "cuda"


# --------------------------------------------------------------------------- a pass: checks and messages

def test_a_first_check_is_announced_with_its_reason_and_recorded_in_full(world):
    a = world.add(1, {"passport.pdf": "FAIL: the passport expires in 5 months"})
    b = world.add(2, {"passport.pdf": "PASS: valid"})
    r = av.run()
    assert [(e["passport"], e["first"], e["previous"]) for e in r["checked"]] == [(a, True, None), (b, True, None)]
    assert av.summary_lines(r) == ["🔍 Documents checked: 2",
                                   "   • SAMPLE STUDENT 1 (KLP) — FAIL (01 Passport: the passport expires in 5 months)",
                                   "   • SAMPLE STUDENT 2 (KLP) — PASS",
                                   report_line()]
    rec = world.store()["documents"][a]
    assert rec["rules_version"] == VERSION and rec["max_pages"] == 20 and rec["folder"] == str(world.folders[a])
    assert rec["engine"]["text"] == "ocr" and rec["engine"]["device"] == "not used"
    assert rec["portal_fp"] == av.portal_fp(world.students[a]) and rec["fingerprint"] == av.doc_fingerprint(world.folders[a])
    assert rec["checked"] == rec["first_checked"] == rec["evaluated_at"] == "2026-10-21T10:00:00"
    assert world.store()["fields"][a]["rules_version"] == VERSION
    assert r["reports_due"] and r["republish"] == []
    assert av.finish_reports(r) == [] and av.DOC_REPORT.exists() and av.FIELD_REPORT.exists()


def test_an_unchanged_re_check_is_only_counted_and_a_change_shows_old_and_new(world):
    a = world.add(1, {"passport.pdf": "PASS: valid"})
    b = world.add(2, {"passport.pdf": "PASS: valid"})
    av.run()
    world.set_now(dt.datetime(2026, 10, 21, 12, 0))
    world.write(a, "passport.pdf", "PASS: valid, scanned again")
    world.write(b, "passport.pdf", "FAIL: the photo page is cut off")
    r = av.run()
    assert [(e["passport"], e["previous"], e["verdict"], e["first"]) for e in r["checked"]] == [
        (a, "PASS", "PASS", False), (b, "PASS", "FAIL", False)]
    assert av.summary_lines(r) == [
        "🔍 Documents checked: 1 (1 re-checked, verdict unchanged)",
        "   • SAMPLE STUDENT 2 (KLP) — PASS -> FAIL (01 Passport: the photo page is cut off)",
        report_line()]
    rec = world.store()["documents"][b]
    assert (rec["previous_verdict"], rec["verdict_changed_at"]) == ("PASS", "2026-10-21T12:00:00")
    assert rec["first_checked"] == "2026-10-21T10:00:00" and rec["checked"] == "2026-10-21T12:00:00"
    assert av.summary_lines(av.run()) == []                              # nothing new: nothing to say


def test_review_lines_carry_two_flags_and_incomplete_lines_the_missing_documents(world):
    world.required = ["passport", "family"]
    long_flag = ("the statement covers only 3 months of transactions " * 3).strip()
    world.add(1, {"passport.pdf": "PASS: valid",
                  "bank.pdf": f"FLAG: {long_flag}\nFLAG: a second flag on the same document",
                  "family.pdf": "FLAG: issued 4 months ago",
                  "father.pdf": "FLAG: a third document is not listed"})
    world.add(2, {"bank.pdf": "FAIL: the balance is below the minimum"})
    lines = av.summary_lines(av.run())
    assert lines[1] == (f"   • SAMPLE STUDENT 1 (KLP) — REVIEW (08 Bank Solvency & Statement: {long_flag[:89]}…; "
                        "06 Family Relationship Certificate: issued 4 months ago)")
    assert lines[2] == ("   • SAMPLE STUDENT 2 (KLP) — INCOMPLETE (missing: 01 Passport, "
                        "06 Family Relationship Certificate; plus 1 FAIL)")


def test_skipped_students_and_read_errors_are_listed(world):
    a = world.add(1, {"passport.pdf": "PASS: valid"})
    b = world.add(2, {"passport.pdf": "LOCKED by a scanner"})
    world.explode = {a}
    r = av.run()
    assert r["skipped"] == [f"SAMPLE STUDENT 1 ({a}): the folder could not be listed"]
    assert [(e["passport"], e["doc"], e["count"]) for e in r["errors"]] == [(b, "01 Passport", 1)]
    assert av.summary_lines(r) == [
        "   ⚠️ 1 student(s) hit a read error — retried on the next pass: SAMPLE STUDENT 2 "
        "(01 Passport: passport.pdf is locked by another program)",
        f"   ⚠️ 1 student(s) could not be checked: SAMPLE STUDENT 1 ({a}): the folder could not be listed",
        "   2 more waiting — they run on the next passes."]


def test_a_code_fault_stops_the_run(world):
    world.add(1, {"passport.pdf": "PASS: valid"})

    def broken(folder, row, program):
        raise NameError("name 'balance' is not defined")

    dv.verify_student = broken
    with pytest.raises(RuntimeError, match="code fault"):
        av.run()
    assert not av.LOCK_PATH.exists()


def test_a_failed_document_check_says_which_rule_failed():
    """Moved here from tests/test_jobs.py (addendum A10), with the INCOMPLETE line's new format."""
    rows = [{"doc": "Passport", "file": "p.jpg", "verdict": "PASS", "detail": "PASS: readable"},
            {"doc": "HSC certificate", "file": "h.pdf", "verdict": "FAIL",
             "detail": "PASS: readable | FAIL: GPA 4.50 differs from the portal's 5.00 | FLAG: faint stamp"}]
    result = {"checked": [{"student": "SAMPLE STUDENT ONE", "passport": P1, "program": "EAP",
                           "verdict": "FAIL", "differs": 0, "corrected": 0},
                          {"student": "NO BANK", "passport": P2, "program": "KLP",
                           "verdict": "INCOMPLETE", "differs": 0, "corrected": 0}],
              "store": {"documents": {P1: {"rows": rows}, P2: {"rows": [
                  {"doc": "Bank certificate", "file": "", "verdict": "MISSING",
                   "detail": "required by the guideline but not uploaded"}]}}}}
    lines = av.summary_lines(result)
    assert lines[0] == "🔍 Documents checked: 2"
    assert lines[1] == ("   • SAMPLE STUDENT ONE (EAP) — FAIL (HSC certificate: GPA 4.50 differs from "
                        "the portal's 5.00)")
    assert lines[2] == "   • NO BANK (KLP) — INCOMPLETE (missing: Bank certificate)"


# --------------------------------------------------------------------------- read errors (B14c, A3)

def test_a_read_error_keeps_the_old_verdict_and_the_third_in_a_row_is_kept(world):
    a = world.add(1, {"passport.pdf": "PASS: valid"})
    av.run()
    world.write(a, "passport.pdf", "LOCKED by a scanner")
    for n in (1, 2):
        r = av.run()
        rec = world.store()["documents"][a]
        assert r["checked"] == [] and [e["count"] for e in r["errors"]] == [n]
        assert rec["fingerprint"] == "" and rec["verdict"] == "PASS" and rec["retry"]["count"] == n
        assert rec["rows"][0]["detail"] == "PASS: valid"
        assert av.pending(world.store(), world.student_folders(), world.portal_students()) == [a]
    r = av.run()                                                         # the third pass in a row
    rec = world.store()["documents"][a]
    assert rec["verdict"] == "ERROR" and rec["fingerprint"] == av.doc_fingerprint(world.folders[a])
    assert rec["retry"]["final"] and r["errors"] == []
    assert av.summary_lines(r) == [
        "🔍 Documents checked: 1",
        "   • SAMPLE STUDENT 1 (KLP) — PASS -> ERROR (01 Passport: passport.pdf is locked by another program — "
        "could not be checked 3 passes in a row)",
        report_line()]
    assert av.run()["checked"] == []                                     # announced once, not retried for ever


def test_a_retry_that_reads_cleanly_drops_the_retry(world):
    a = world.add(1, {"passport.pdf": "PASS: valid"})
    av.run()
    world.write(a, "passport.pdf", "LOCKED")
    av.run()
    world.write(a, "passport.pdf", "PASS: valid again")
    r = av.run()
    rec = world.store()["documents"][a]
    assert "retry" not in rec and rec["fingerprint"] == av.doc_fingerprint(world.folders[a])
    assert [(e["previous"], e["verdict"]) for e in r["checked"]] == [("PASS", "PASS")]


def test_a_fail_row_with_an_error_finding_is_retried(world):
    a = world.add(1, {"passport.pdf": "FAIL: the passport expires in 5 months\nERROR: the QR reader failed"})
    r = av.run()
    rec = world.store()["documents"][a]
    assert rec["rows"][0]["verdict"] == "FAIL" and rec["rows"][0]["has_error"]
    assert rec["fingerprint"] == "" and rec["retry"]["count"] == 1 and "first_checked" not in rec
    assert r["checked"] == [] and [e["msg"] for e in r["errors"]] == ["the QR reader failed"]
    assert av.pending(world.store(), world.student_folders(), world.portal_students()) == [a]


# --------------------------------------------------------------------------- reports (B14a)

REPORT_STORE = {"documents": {P1: {"student": "SAMPLE STUDENT 1", "program": "KLP", "verdict": "ERROR",
                                   "checked": "2026-10-21T10:00:00", "evaluated_at": "2026-10-21T10:00:00",
                                   "rules_version": VERSION,
                                   "rows": [{"doc": "01 Passport", "file": "passport.pdf", "verdict": "ERROR",
                                             "detail": "ERROR: could not check this file (locked)"}]}},
                "fields": {P1: {"student": "SAMPLE STUDENT 1", "program": "KLP", "checked": "2026-10-21T10:00:00",
                                "rows": [{"field": "Father", "portal": "SAMPLE FATHER", "result": "NOT CONFIRMED",
                                          "detail": "not found"},
                                         {"field": "DOB", "portal": "2005-03-14", "result": "MATCH", "detail": "ok"}]}},
                "corrections": [], "meta": {}}


def test_an_open_report_is_saved_beside_it_with_a_note_and_nothing_raises(monkeypatch):
    real = os.replace

    def replace(src, dst):
        if Path(dst) == av.DOC_REPORT:
            raise PermissionError(13, "The process cannot access the file because it is being used")
        return real(src, dst)

    monkeypatch.setattr(os, "replace", replace)
    monkeypatch.setattr(av, "_now", lambda: dt.datetime(2026, 10, 21, 14, 5))
    earlier = av.REPORT_DIR / "DOCUMENT CHECK (updated 09-00).xlsx"
    earlier.write_bytes(b"an older copy")
    notes = av.finish_reports({"reports_due": True, "store": REPORT_STORE})
    assert notes == ["📄 DOCUMENT CHECK.xlsx was open in Excel — this pass saved 'DOCUMENT CHECK (updated 14-05).xlsx'; "
                     "close it and the next pass writes the usual file again."]
    assert (av.REPORT_DIR / "DOCUMENT CHECK (updated 14-05).xlsx").exists() and not earlier.exists()
    assert av.FIELD_REPORT.exists() and not av.DOC_REPORT.exists()
    assert not list(av.REPORT_DIR.glob("*.tmp.xlsx"))
    monkeypatch.setattr(os, "replace", real)                            # closed again
    assert av.finish_reports({"reports_due": True, "store": REPORT_STORE}) == []
    assert av.DOC_REPORT.exists() and not list(av.REPORT_DIR.glob("* (updated *).xlsx"))


def test_finish_reports_writes_nothing_unless_due_and_never_raises(monkeypatch):
    assert av.finish_reports({"reports_due": False, "store": REPORT_STORE}) == []
    assert av.finish_reports(None) == [] and av.finish_reports({"reports_due": True, "store": None}) == []
    assert not av.DOC_REPORT.exists()

    def broken(store, path=None):
        raise ValueError("a cell Excel refuses")

    monkeypatch.setattr(av, "write_field_report", broken)
    notes = av.finish_reports({"reports_due": True, "store": REPORT_STORE})
    assert notes == ["📄 FIELD CHECK.xlsx could not be written (ValueError: a cell Excel refuses) — the next pass tries again."]
    assert av.DOC_REPORT.exists()


def test_the_reports_show_the_rules_version_and_not_confirmed():
    from openpyxl import load_workbook
    av.finish_reports({"reports_due": True, "store": REPORT_STORE})
    ws = load_workbook(av.DOC_REPORT)["Summary"]
    head = [c.value for c in ws[1]]
    assert head[-2:] == ["Rules", "Evaluated"]
    assert [c.value for c in ws[2]][-2:] == [VERSION, "2026-10-21 10:00"]
    ws = load_workbook(av.FIELD_REPORT)["Summary"]
    head = [c.value for c in ws[1]]
    row = dict(zip(head, [c.value for c in ws[2]]))
    assert "Not confirmed" in head and row["Not confirmed"] == 1 and row["Checked"] == 2


def test_the_report_paths_are_read_when_writing(tmp_path, monkeypatch):
    other = tmp_path / "elsewhere"
    monkeypatch.setattr(av, "DOC_REPORT", other / "DOCUMENT CHECK.xlsx")
    monkeypatch.setattr(av, "FIELD_REPORT", other / "FIELD CHECK.xlsx")
    av.finish_reports({"reports_due": True, "store": REPORT_STORE})
    assert (other / "DOCUMENT CHECK.xlsx").exists() and (other / "FIELD CHECK.xlsx").exists()
    assert not list(av.REPORT_DIR.glob("*.xlsx"))


# --------------------------------------------------------------------------- the roll-out (B15, A1)

def _rollout_world(world):
    world.add(1, {"passport.pdf": "PASS: valid",
                  "bank.pdf": "PASS: balance above the minimum\n"
                              "NEW-RULE FLAG: the opening balance is above the preferred maximum"})
    world.add(2, {"passport.pdf": "PASS: valid",
                  "family.pdf": "PASS: issued last month\nOLD-RULE FAIL: the issue date was misread"})
    world.add(3, {"passport.pdf": "PASS: valid"})
    world.add(4, {"passport.pdf": "PASS: valid"})
    world.set_now(dt.datetime(2026, 10, 20, 10, 0))
    av.run(budget=0)
    world.legacy()
    world.new_rules = True
    world.set_now(dt.datetime(2026, 10, 21, 10, 0))


def test_a_roll_out_spans_passes_pauses_the_queue_and_is_announced_once(world):
    _rollout_world(world)
    (av.TEXT_DIR / f"{P4}.json").unlink()                                # P4's text was never saved
    world.add(5, {"passport.pdf": "PASS: valid"})                        # a new student waits
    reads = len(world.reads)
    world.tick = 10                                                      # each evaluation takes 10 s

    r1 = av.run(reeval_seconds=15)                                       # time for two students
    assert len(world.reads) == reads                                     # from the saved text only
    assert r1["rollout_lines"] == [] and r1["checked"] == [] and r1["waiting"] == 1
    assert av.summary_lines(r1) == []
    store = world.store()
    assert store["meta"]["rollout"]["done"] == [P1, P2] and "rules_version" not in store["meta"]
    assert store["documents"][P1]["rules_version"] == VERSION and "rules_version" not in store["documents"][P3]
    assert store["documents"][P1]["engine"]["text"] == "cache"

    r2 = av.run(reeval_seconds=15)
    assert r2["rollout_lines"] == [
        f"Rules unversioned -> {VERSION}: 3 student(s) re-evaluated from the saved text (no new OCR).",
        "Before: FAIL 1, REVIEW 0, INCOMPLETE 0, PASS 3",
        "Now: FAIL 0, REVIEW 1, INCOMPLETE 0, PASS 3",
        "2 verdict(s) changed:",
        "   • SAMPLE STUDENT 2 (KLP) — FAIL -> PASS (cleared: 06 Family Relationship Certificate: "
        "the issue date was misread)",
        "   • SAMPLE STUDENT 1 (KLP) — PASS -> REVIEW (08 Bank Solvency & Statement: the opening balance is above "
        "the preferred maximum)",
        "1 student(s) have documents whose text was never saved — they join the normal queue.",
        "Reports refreshed: DOCUMENT CHECK.xlsx / FIELD CHECK.xlsx"]
    # the queue went on in the same pass: the new student, then the one whose text was missing
    assert [(e["passport"], e["first"]) for e in r2["checked"]] == [(P5, True), (P4, False)]
    assert av.summary_lines(r2) == ["🔍 Documents checked: 1 (1 re-checked, verdict unchanged)",
                                   "   • SAMPLE STUDENT 5 (KLP) — PASS"]
    assert r2["reports_due"]
    meta = world.store()["meta"]
    assert meta["rules_version"] == VERSION and "rollout" not in meta
    assert meta["last_rollout"]["announced"] and meta["last_rollout"]["needs_ocr"] == [P4]
    assert (av.REPORT_DIR / f"rollout-{VERSION}.txt").read_text(encoding="utf-8").startswith(av.ROLLOUT_TITLE)

    r3 = av.run()
    assert r3["rollout_lines"] == [] and r3["reevaluated"] == [] and r3["checked"] == []


def test_a_roll_out_never_bumps_checked_for_rows_that_only_gained_their_key(world):
    _rollout_world(world)
    before = world.store()["documents"][P3]
    r = av.run()
    rec = world.store()["documents"][P3]
    assert rec["checked"] == before["checked"] == "2026-10-20T10:00:00"           # A16
    assert rec["evaluated_at"] == "2026-10-21T10:00:00" and rec["first_checked"] == before["checked"]
    assert P3 in r["republish"] and rec["rows"][0]["key"] == "passport"           # sent again once, with keys
    assert world.store()["documents"][P1]["checked"] == "2026-10-21T10:00:00"     # its rows changed


def test_records_the_saved_text_cannot_update_never_hold_the_queue(world):
    _rollout_world(world)
    (av.TEXT_DIR / f"{P2}.json").unlink()                                # needs_ocr
    (av.IMAGES_DIR / f"{P3}.json").unlink()
    world.inspect_fails = {str(world.folders[P3] / "passport.pdf")}       # a read error: kept as it was
    shutil.rmtree(world.folders[P4])                                     # no longer on this PC
    world.add(5, {"passport.pdf": "PASS: valid"})
    r = av.run()
    assert r["rollout_lines"][-3:] == [
        "1 student(s) have documents whose text was never saved — they join the normal queue.",
        "2 student(s) could not be re-evaluated (a read error, or no longer on the portal or this PC) — their "
        "verdicts stay as they were.",
        "Reports refreshed: DOCUMENT CHECK.xlsx / FIELD CHECK.xlsx"]
    assert [e["passport"] for e in r["checked"]] == [P5, P2]             # the queue resumed at once
    store = world.store()
    assert store["meta"]["rules_version"] == VERSION and store["meta"]["last_rollout"]["skipped"] == [P3, P4]
    assert "rules_version" not in store["documents"][P3] and store["documents"][P3]["reeval_error"]
    assert "rules_version" not in store["documents"][P4]
    r2 = av.run()
    assert r2["rollout_lines"] == [] and "rollout" not in world.store()["meta"]       # not restarted


def test_a_fresh_store_has_nothing_to_roll_out(world):
    world.add(1, {"passport.pdf": "PASS: valid"})
    r = av.run()
    assert r["rollout_lines"] == [] and world.store()["meta"]["rules_version"] == VERSION


# --------------------------------------------------------------------------- the daily re-check

def test_a_date_rule_that_changes_overnight_is_re_evaluated_after_seven(world):
    world.set_now(dt.datetime(2026, 10, 20, 10, 0))
    a = world.add(1, {"passport.pdf": "PASS: valid", "family.pdf": "EXPIRES 2026-10-20"})
    assert [e["verdict"] for e in av.run()["checked"]] == ["PASS"]
    reads = len(world.reads)
    world.set_now(dt.datetime(2026, 10, 21, 6, 30))
    r = av.run()
    assert r["reevaluated"] == [] and world.store()["meta"]["reevaluated_on"] == "2026-10-20"   # not before 07:00
    world.set_now(dt.datetime(2026, 10, 21, 8, 0))
    r = av.run()
    assert len(world.reads) == reads
    assert r["reevaluated"] == [{"passport": a, "student": "SAMPLE STUDENT 1", "program": "KLP", "old": "PASS",
                                 "new": "FAIL", "reason": "06 Family Relationship Certificate: expired on 2026-10-20",
                                 "why": "daily"}]
    assert av.summary_lines(r) == [
        "🔍 Documents checked: 1",
        "   • SAMPLE STUDENT 1 (KLP) — PASS -> FAIL (06 Family Relationship Certificate: expired on 2026-10-20) "
        "[re-checked: today's date]",
        report_line()]
    assert r["republish"] == [a] and world.store()["meta"]["reevaluated_on"] == "2026-10-21"
    world.set_now(dt.datetime(2026, 10, 21, 9, 0))
    assert av.run()["reevaluated"] == []                                 # once a day


def test_the_daily_re_check_stops_at_its_deadline_and_goes_on_next_pass(world):
    world.set_now(dt.datetime(2026, 10, 20, 10, 0))
    for n in (1, 2, 3):
        world.add(n, {"passport.pdf": "PASS: valid"})
    av.run(budget=0)
    world.set_now(dt.datetime(2026, 10, 21, 8, 0))
    world.tick = 10
    store = av.load_store()
    out = av.reevaluate(store, world.student_folders(), world.portal_students(), deadline=world.ticks + 15)
    assert out["evaluated"] == [P1, P2] and not out["done"]
    out = av.reevaluate(store, world.student_folders(), world.portal_students(), deadline=world.ticks + 15)
    assert out["evaluated"] == [P3] and out["done"]


# --------------------------------------------------------------------------- change reasons

def _rec(verdict, *rows):
    return {"verdict": verdict, "rows": [{"doc": d, "verdict": v, "detail": t} for d, v, t in rows]}


def test_change_reasons():
    nid_missing = _rec("INCOMPLETE", ("05 Father NID", "MISSING", "required by the guideline but not uploaded"))
    death = _rec("PASS", ("05 Father Death Certificate", "PASS",
                          "PASS: present — accepted in place of the father's NID (05 Father NID not needed)"))
    assert av.change_reason(nid_missing, death) == "05 Father NID: death certificate accepted"
    failed = _rec("FAIL", ("01 Passport", "FAIL", "PASS: readable | FAIL: expires in 5 months"))
    flagged = _rec("REVIEW", ("04 Birth Certificate", "FLAG", "FLAG: the DOB could not be read"))
    assert av.change_reason(failed, flagged) == "cleared: 01 Passport: expires in 5 months"
    assert av.change_reason(flagged, failed) == "01 Passport: expires in 5 months"
    assert av.change_reason(_rec("PASS"), flagged) == "04 Birth Certificate: the DOB could not be read"
    assert av.change_reason(flagged, _rec("PASS")) == "cleared: 04 Birth Certificate: the DOB could not be read"
    error = _rec("ERROR", ("07 Academic Certificate & Transcript", "ERROR",
                           "ERROR: could not check this file (pyzbar failed) — it is retried on the next pass"))
    assert av.change_reason(flagged, error) == "07 Academic Certificate & Transcript: pyzbar failed"
    assert av.change_reason(None, failed) == "01 Passport: expires in 5 months"


# --------------------------------------------------------------------------- the command line (B17)

@pytest.fixture
def sent(monkeypatch):
    """Telegram and the Supabase hand-over, recorded instead of done."""
    from src.cloud import sheet_hooks
    from src.sheets import auto_sync
    log: Dict[str, list] = {"notify": [], "refresh": [], "notice": []}
    accept = {"ok": True}

    def notify(lines, title=auto_sync.SYNC_TITLE):
        log["notify"].append((title, list(lines)))
        return accept["ok"]

    def refresh(result, sent=()):
        log["refresh"].append((result, list(sent)))

    monkeypatch.setattr(auto_sync, "notify", notify)
    monkeypatch.setattr(sheet_hooks, "after_verify_refresh", refresh, raising=False)
    monkeypatch.setattr(sheet_hooks, "after_verify_notice", lambda *a: log["notice"].append(a), raising=False)
    log["accept"] = accept
    return log


def _snapshot(folder: Path) -> Dict[str, bytes]:
    return {str(p.relative_to(folder)): p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file()}


def test_passport_alone_shows_the_stored_result_and_changes_nothing(world, capsys, sent):
    a = world.add(1, {"passport.pdf": "FLAG: the photo page is faint"})
    av.run()
    av.finish_reports({"reports_due": True, "store": av.load_store()})
    before = _snapshot(av.REPORT_DIR)
    assert av.main(["--passport", a.lower()]) == 0
    out = capsys.readouterr().out
    assert f"SAMPLE STUDENT 1 ({a}) — KLP — REVIEW" in out and "the photo page is faint" in out
    assert f"rules {VERSION}" in out
    assert _snapshot(av.REPORT_DIR) == before and sent["notify"] == []


def test_passport_force_re_runs_only_that_student(world, sent):
    a = world.add(1, {"passport.pdf": "PASS: valid\nNEW-RULE FLAG: the photo page is faint"})
    b = world.add(2, {"passport.pdf": "PASS: valid\nNEW-RULE FLAG: the photo page is faint"})
    av.run()
    store = world.store()
    others = (json.dumps(store["documents"][b]), json.dumps(store["fields"][b]))
    reads = len(world.reads)
    world.new_rules = True
    world.set_now(dt.datetime(2026, 10, 21, 11, 0))
    assert av.main(["--passport", a, "--force"]) == 0
    store = world.store()
    assert store["documents"][a]["verdict"] == "REVIEW" and store["documents"][a]["evaluated_at"] == "2026-10-21T11:00:00"
    assert (json.dumps(store["documents"][b]), json.dumps(store["fields"][b])) == others
    assert len(world.reads) == reads                                     # from the saved text
    assert av.DOC_REPORT.exists() and sent["notify"] == []


def test_recheck_re_evaluates_everyone_and_never_blanks_a_fingerprint(world, sent):
    for n in (1, 2, 3):
        world.add(n, {"passport.pdf": "PASS: valid"})
    av.run()
    before = world.store()
    world.set_now(dt.datetime(2026, 10, 21, 11, 0))
    assert av.main(["--recheck"]) == 0
    after = world.store()
    for p in (P1, P2, P3):
        assert after["documents"][p]["fingerprint"] == before["documents"][p]["fingerprint"] != ""
        assert after["fields"][p]["fingerprint"] == before["fields"][p]["fingerprint"] != ""
        assert after["documents"][p]["evaluated_at"] == "2026-10-21T11:00:00"
    assert sent["notify"] == []


def test_a_cli_roll_out_is_announced_only_once_telegram_accepts_it(world, sent):
    _rollout_world(world)
    assert av.main(["--recheck"]) == 0                                    # completed, not sent
    ro = world.store()["meta"]["rollout"]
    assert ro["complete"] and not ro["announced"] and sent["notify"] == []
    sent["accept"]["ok"] = False
    assert av.main(["--announce"]) == 0
    assert world.store()["meta"]["rollout"]["announced"] is None and sent["notice"] == []
    sent["accept"]["ok"] = True
    assert av.main(["--announce"]) == 0
    meta = world.store()["meta"]
    assert "rollout" not in meta and meta["last_rollout"]["announced"]
    title, lines = sent["notify"][-1]
    assert title == av.ROLLOUT_TITLE and lines == ro["lines"]
    ((n_title, n_lines, at),) = sent["notice"]                           # handed to Supabase (A19)
    assert (n_title, n_lines) == (av.ROLLOUT_TITLE, ro["lines"]) and at
    assert av.main(["--announce"]) == 0 and len(sent["notify"]) == 2      # nothing left to send


def test_recheck_announce_publish_sends_one_summary_and_hands_it_over(world, sent):
    _rollout_world(world)
    assert av.main(["--recheck", "--announce", "--publish"]) == 0
    assert [t for t, _ in sent["notify"]] == [av.ROLLOUT_TITLE]
    ((result, handed),) = sent["refresh"]
    assert set(result["republish"]) == {P1, P2, P3, P4}
    assert [(t, ls) for t, ls, _ in handed] == [(av.ROLLOUT_TITLE, sent["notify"][0][1])]
    assert sent["notice"] == [] and "rollout" not in world.store()["meta"]
    assert av.DOC_REPORT.exists()
    assert av.run()["rollout_lines"] == []                               # the scheduled pass sends nothing more


def test_a_roll_out_finished_from_the_console_is_handed_out_by_the_next_pass(world, sent):
    _rollout_world(world)
    av.main(["--recheck"])
    r = av.run()
    assert r["rollout_lines"] and r["rollout_lines"][0].startswith("Rules unversioned -> ")
    assert "rollout" not in world.store()["meta"] and av.run()["rollout_lines"] == []


def test_dry_run_refuses_live_data(world, tmp_path, sent, capsys):
    live = tmp_path / "BOT" / "data" / "verification"
    live.mkdir(parents=True)
    (live / av.DRY_RUN_MARKER).write_text("copy", encoding="utf-8")
    unmarked = tmp_path / "copy-without-marker"
    unmarked.mkdir()
    for argv, why in ((["--recheck", "--dry-run"], "needs --data-dir"),
                      (["--recheck", "--dry-run", "--data-dir", str(live)], "BOT\\data"),
                      (["--recheck", "--dry-run", "--data-dir", str(settings.verification_dir())], "own data"),
                      (["--recheck", "--dry-run", "--data-dir", str(unmarked)], av.DRY_RUN_MARKER)):
        assert av.main(argv) == 2
        assert why in capsys.readouterr().out
    assert list(live.iterdir()) == [live / av.DRY_RUN_MARKER] and list(unmarked.iterdir()) == []
    assert sent["notify"] == []


def test_a_dry_run_works_on_a_copy_with_the_export_and_sends_nothing(world, tmp_path, sent):
    _rollout_world(world)
    live = av.REPORT_DIR
    live_files = _snapshot(live)
    copy = tmp_path / "dryrun" / "verification"
    shutil.copytree(live, copy)
    (copy / av.DRY_RUN_MARKER).write_text("copy", encoding="utf-8")
    export = tmp_path / "dryrun" / "export.json"
    export.write_text(json.dumps(list(world.students.values())), encoding="utf-8")
    world.portal_down = True                                             # only the export may be read
    reads = len(world.reads)
    assert av.main(["--recheck", "--dry-run", "--data-dir", str(copy), "--export-file", str(export),
                    "--announce", "--publish"]) == 0
    assert sent["notify"] == sent["refresh"] == sent["notice"] == [] and len(world.reads) == reads
    summary = (copy / "rollout_summary.txt").read_text(encoding="utf-8")
    assert "Before: FAIL 1, REVIEW 0, INCOMPLETE 0, PASS 3" in summary
    assert "After:  FAIL 0, REVIEW 1, INCOMPLETE 0, PASS 3" in summary
    assert (f"   • SAMPLE STUDENT 1 ({P1}, KLP) — PASS -> REVIEW (08 Bank Solvency & Statement: the opening "
            "balance is above the preferred maximum) [rules]") in summary
    assert "0 ERROR row(s):" in summary and "0 birth-certificate FAIL(s)" in summary
    meta = json.loads((copy / "results.json").read_text(encoding="utf-8"))["meta"]
    assert meta["rollout"]["complete"] and not meta["rollout"]["announced"]
    assert _snapshot(live) == live_files                                 # the original untouched


def test_pending_and_rebuild_from_the_command_line(world, capsys, sent):
    world.add(1, {"passport.pdf": "PASS: valid"})
    av.run()
    world.add(2, {"passport.pdf": "PASS: valid"})
    assert av.main(["--pending"]) == 0
    out = capsys.readouterr().out
    assert "1 student(s) waiting to be checked" in out and f"{P2} (new)" in out
    assert not av.DOC_REPORT.exists()
    assert av.main(["--rebuild"]) == 0
    assert av.DOC_REPORT.exists() and av.FIELD_REPORT.exists() and not av.LOCK_PATH.exists()
    av.STORE_PATH.write_bytes(b"{ broken")
    assert av.main(["--passport", P1]) == 1 and av.main(["--pending"]) == 1
    assert "nothing was changed" in capsys.readouterr().out and av.STORE_PATH.read_bytes() == b"{ broken"
    assert not list(av.REPORT_DIR.glob("results.corrupt-*.json"))       # a look never copies anything


def test_a_running_check_makes_the_command_line_wait_or_give_up(world, monkeypatch, capsys):
    av.LOCK_PATH.write_text(str(os.getppid()), encoding="utf-8")
    assert av.main(["--recheck"]) == 1
    assert "already running" in capsys.readouterr().out
    slept = []

    def sleep(seconds):                                     # the other check ends while we wait
        slept.append(seconds)
        world.ticks += seconds
        av.LOCK_PATH.unlink()

    monkeypatch.setattr(av.time, "sleep", sleep)
    assert av.main(["--recheck", "--wait"]) == 0
    assert slept == [av.LOCK_POLL_SECONDS] and not av.LOCK_PATH.exists()


def test_recheck_all_sets_the_results_aside(world, sent):
    world.add(1, {"passport.pdf": "PASS: valid"})
    av.run()
    old = av.STORE_PATH.read_bytes()
    assert av.main(["--recheck-all", "--budget", "0"]) == 0
    (aside,) = av.REPORT_DIR.glob("results.cleared-*.json")
    assert aside.read_bytes() == old
    assert world.store()["documents"][P1]["first_checked"] == world.store()["documents"][P1]["checked"]
