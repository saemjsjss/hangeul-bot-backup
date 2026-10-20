"""
Copy every document-verified student's files from the portal into Google Drive.

Source: students.php?source=direct&filter_docs=verified (all pages) — read-only GETs.
For each student the portal's own "download all" ZIP (download_docs.php?uid=N&zip=1) is
fetched, unzipped in memory, and each file is uploaded to

    ALL STUDENTS / VERIFIED STUDENT DOCUMENTS / <PROGRAM> / <FULL NAME> (<PASSPORT NO>) /

where <PROGRAM> is one of the four program folders (KLP, EAP, BACHELOR'S, MASTER'S).

Re-runnable: a student folder that finished completely is skipped; a half-finished one
only gets the files it is missing.  On this PC (run_local), a student whose portal documents
changed is fetched again and a changed document replaces the old copy, which is moved to
<student>/_replaced/<date>/ (and its over-2 MB original likewise under the originals folder).
Nothing is ever deleted.

CLI (run from the BOT folder):
  python -m src.sheets.verified_docs            # copy all verified students
  python -m src.sheets.verified_docs --list     # just list who would be copied
  python -m src.sheets.verified_docs --limit 2  # try the first 2 students only
  python -m src.sheets.verified_docs --local "..\\VERIFIED STUDENT DOCUMENTS"   # save on this PC
  python -m src.sheets.verified_docs --confirm-replace A99900001   # replace what the catch-up
                                                # check held back for that student (this PC)
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import logging
import mimetypes
import os
import re
import shutil
import time
import zipfile
from datetime import date
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from src.config import settings
from src.sheets.progress_builder import BOT_ROOT, PARENT_FOLDER_ID, PROGRAMS, _services, program_matches

logger = logging.getLogger(__name__)

LIST_PATH = "students.php?source=direct&filter_docs=verified"
TARGET_FOLDER_NAME = "VERIFIED STUDENT DOCUMENTS"
FOLDER_MIME = "application/vnd.google-apps.folder"


# --- portal side (read-only) -------------------------------------------------------
def _parse_rows(html: str) -> List[Dict[str, str]]:
    from bs4 import BeautifulSoup
    from src.scraper.parsers import decode_cf_emails
    soup = decode_cf_emails(BeautifulSoup(html, "html.parser"))
    out = []
    for a in soup.find_all("a", href=re.compile(r"download_docs\.php\?uid=\d+")):
        uid = re.search(r"uid=(\d+)", a["href"]).group(1)
        parts = a.find_parent("tr").get_text(" | ", strip=True).split(" | ")

        def after(label: str) -> str:
            if label in parts:
                i = parts.index(label) + 1
                return parts[i].strip() if i < len(parts) else ""
            return ""

        # The portal puts the upload time in every document's filename
        # (passport_492_1789796548.jpeg), so the set of names is a fingerprint of what
        # the student currently has uploaded.  Replace a document and the name changes:
        # run_local fetches the ZIP again, and the changed document replaces the old copy
        # (kept in the student's _replaced folder).
        row = a.find_parent("tr")
        docs = sorted({m.group(1).rsplit("/", 1)[-1]
                       for m in re.finditer(r"view_doc\.php\?f=([^\"&'\s]+)", str(row))})

        out.append({"uid": uid, "name": after("Full Name"), "passport": after("Passport No"),
                    "program": after("Program"), "docs": "|".join(docs)})
    return out


async def fetch_verified_students(client) -> List[Dict[str, str]]:
    """Every document-verified Direct student (LIST_PATH, every page, read by the client's one
    session path: read_student_pages). Raises PortalUnavailable when the list cannot be read whole
    (a refused login included), never an empty or partial list."""
    params = dict(p.split("=", 1) for p in LIST_PATH.partition("?")[2].split("&") if "=" in p)
    students: Dict[str, Dict[str, str]] = {}
    for html in await client.read_student_pages(params):
        for s in _parse_rows(html):
            students.setdefault(s["uid"], s)
    return list(students.values())


def program_folder(s: Dict[str, str]) -> str:
    """One of the four program folders (same names as the progress sheets)."""
    for cfg in PROGRAMS.values():
        if program_matches(s.get("program", ""), cfg["match_tokens"]):
            return cfg["name"]
    return "OTHER PROGRAMS"


def folder_name(s: Dict[str, str]) -> str:
    name = re.sub(r"\s+", " ", s["name"] or f"UID {s['uid']}").strip()
    return f"{name} ({s['passport']})" if s["passport"] else name


# --- Drive side --------------------------------------------------------------------
def _q(s: str) -> str:
    return s.replace("\\", "\\\\").replace("'", "\\'")


def _find_folder(drive, name: str, parent: str):
    found = drive.files().list(
        q=(f"name = '{_q(name)}' and '{parent}' in parents and mimeType = '{FOLDER_MIME}' "
           "and trashed = false"),
        fields="files(id, appProperties)", supportsAllDrives=True, includeItemsFromAllDrives=True,
    ).execute().get("files", [])
    return found[0] if found else None


def _find_or_create_folder(drive, name: str, parent: str):
    found = _find_folder(drive, name, parent)
    if found:
        return found
    return drive.files().create(
        body={"name": name, "parents": [parent], "mimeType": FOLDER_MIME},
        fields="id, appProperties", supportsAllDrives=True,
    ).execute()


def _existing_names(drive, folder_id: str) -> set:
    names, token = set(), None
    while True:
        resp = drive.files().list(
            q=f"'{folder_id}' in parents and trashed = false", fields="nextPageToken, files(name)",
            pageToken=token, supportsAllDrives=True, includeItemsFromAllDrives=True,
        ).execute()
        names.update(f["name"] for f in resp.get("files", []))
        token = resp.get("nextPageToken")
        if not token:
            return names


def _upload(drive, folder_id: str, filename: str, data: bytes) -> None:
    from googleapiclient.http import MediaIoBaseUpload
    mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mime, resumable=True)
    drive.files().create(body={"name": filename, "parents": [folder_id]}, media_body=media,
                         fields="id", supportsAllDrives=True).execute()


# --- main job ----------------------------------------------------------------------
async def run(limit: int = 0, list_only: bool = False) -> None:
    from src.scraper.client import admin_client as client
    try:
        await client.login()
        students = await fetch_verified_students(client)
        print(f"{len(students)} verified student(s) on the portal.")
        if limit:
            students = students[:limit]
        if list_only:
            for s in students:
                print(f"  {program_folder(s)} / {folder_name(s)}")
            return

        drive, _ = _services()
        target = _find_or_create_folder(drive, TARGET_FOLDER_NAME, PARENT_FOLDER_ID)
        print(f"Drive folder: https://drive.google.com/drive/folders/{target['id']}")

        prog_ids: Dict[str, str] = {}
        done = skipped = failed = 0
        for n, s in enumerate(students, 1):
            name = folder_name(s)
            try:
                prog = program_folder(s)
                if prog not in prog_ids:
                    prog_ids[prog] = _find_or_create_folder(drive, prog, target["id"])["id"]
                parent = prog_ids[prog]
                # a folder from an earlier run that sat directly in the target folder is
                # moved into its program folder instead of being copied again
                loose = _find_folder(drive, name, target["id"])
                if loose and not _find_folder(drive, name, parent):
                    drive.files().update(fileId=loose["id"], addParents=parent,
                                         removeParents=target["id"], supportsAllDrives=True).execute()
                folder = _find_or_create_folder(drive, name, parent)
                if (folder.get("appProperties") or {}).get("hangeul_docs_complete") == "1":
                    skipped += 1
                    continue
                resp = await client.client.get(
                    f"{client.base_url}/download_docs.php", params={"uid": s["uid"], "zip": 1},
                    timeout=300.0)
                if "zip" not in resp.headers.get("content-type", ""):
                    raise RuntimeError(f"portal returned {resp.status_code} "
                                       f"{resp.headers.get('content-type')} instead of a ZIP")
                have = _existing_names(drive, folder["id"])
                added = 0
                with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
                    for info in z.infolist():
                        fname = info.filename.rsplit("/", 1)[-1]
                        if info.is_dir() or not fname or fname in have:
                            continue
                        _upload(drive, folder["id"], fname, z.read(info))
                        added += 1
                drive.files().update(fileId=folder["id"],
                                     body={"appProperties": {"hangeul_docs_complete": "1"}},
                                     supportsAllDrives=True).execute()
                done += 1
                print(f"[{n}/{len(students)}] {name}: {added} file(s) uploaded")
            except Exception as e:
                failed += 1
                print(f"[{n}/{len(students)}] {name}: FAILED - {e}")
        print(f"\nFinished: {done} copied, {skipped} already complete, {failed} failed.")
        if failed:
            print("Run again to retry the failed ones (finished folders are skipped).")
    finally:
        await client.close()


async def _download_zip(client, uid: str) -> bytes:
    resp = await client.client.get(f"{client.base_url}/download_docs.php",
                                   params={"uid": uid, "zip": 1}, timeout=300.0)
    if "zip" not in resp.headers.get("content-type", ""):
        raise RuntimeError(f"portal returned {resp.status_code} "
                           f"{resp.headers.get('content-type')} instead of a ZIP")
    return resp.content


def _safe_path_part(s: str) -> str:
    """Windows can't have \\ / : * ? " < > | in names, or end them with a dot/space."""
    return re.sub(r'[\\/:*?"<>|]', "-", s).strip().rstrip(". ") or "UNNAMED"


def _drive_complete_names() -> set:
    """Student folders already finished in Drive (by an earlier Drive run)."""
    drive, _ = _services()
    names, token = set(), None
    while True:
        resp = drive.files().list(
            q=(f"mimeType = '{FOLDER_MIME}' and trashed = false and "
               "appProperties has { key='hangeul_docs_complete' and value='1' }"),
            fields="nextPageToken, files(name)", pageToken=token,
            supportsAllDrives=True, includeItemsFromAllDrives=True,
        ).execute()
        names.update(f["name"] for f in resp.get("files", []))
        token = resp.get("nextPageToken")
        if not token:
            return names


# The last list _drive_complete_names() read, for the runs when Google cannot be reached.
DRIVE_NAMES_CACHE = BOT_ROOT / "data" / "drive_complete_names.json"


def _drive_names(result: Dict) -> set:
    """_drive_complete_names(), failing open: Drive down or its login expired must never stop the
    downloads to this PC.  A good read is saved to DRIVE_NAMES_CACHE; a failed one logs one
    warning, uses the saved list (an empty one when there is none) and sets
    result["drive_lookup_error"] (not sent to Telegram)."""
    try:
        names = set(_drive_complete_names())
    except Exception as e:
        result["drive_lookup_error"] = f"{type(e).__name__}: {e}"
        try:
            saved = json.loads(DRIVE_NAMES_CACHE.read_text(encoding="utf-8"))
            names = set(saved.get("names", []) if isinstance(saved, dict) else saved)
        except Exception:
            names = set()
        logger.warning("Google Drive could not be asked which folders are finished (%s) — "
                       "using the list saved earlier (%d name(s))", result["drive_lookup_error"], len(names))
        return names
    try:
        DRIVE_NAMES_CACHE.parent.mkdir(parents=True, exist_ok=True)
        tmp = DRIVE_NAMES_CACHE.with_name(DRIVE_NAMES_CACHE.name + ".part")
        tmp.write_text(json.dumps({"saved_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "names": sorted(names)},
                                  ensure_ascii=False), encoding="utf-8")
        tmp.replace(DRIVE_NAMES_CACHE)
    except Exception as e:
        logger.warning("the Drive folder list could not be saved (%s)", e)
    return names


LOCAL_DONE_MARKER = ".download_complete"
# The marker records the portal's document list at the moment of the download: "v2\n<docs>".
# A marker without the "v2" line was written before documents were compared byte for byte
# (old copies may have been kept where the portal had replaced them), so its student is
# checked once more by the catch-up sweep.
MARKER_VERSION = "v2"
REPLACED_DIR = "_replaced"      # <student>/_replaced/<YYYY-MM-DD>/<old copy>
REFRESH_PER_RUN = 8             # catch-up sweep: students per run, oldest marker first
# The sweep's circuit breaker: a portal that zips the same document into other bytes would make
# every file look replaced, so nothing is replaced (and it is reported) when one student would
# have more than half of their files replaced, or more than SWEEP_MAX_STUDENTS students would.
SWEEP_MAX_STUDENTS = 2
# A student the breaker held back is not fetched again by the sweep the same day (their marker
# stays as it was, so they are compared again from the next day): "<student>/.sweep_held" holds
# that day. Without it, held students stay oldest in the queue and every run would download the
# same ZIPs and never reach anyone else.
SWEEP_HELD = ".sweep_held"
# The verified list is implausible (a changed page layout?) when, from LAYOUT_MIN_ROWS rows on,
# more than LAYOUT_MAX_BLANK of them show no document list or no passport number.
LAYOUT_MIN_ROWS = 10
LAYOUT_MAX_BLANK = 0.10

# Files above this are shrunk after download; the untouched original is copied to
# BACKUP_ROOT first (same sub-path).
MAX_FILE_BYTES = 2 * 1024 * 1024
_TARGET_BYTES = int(1.95 * 1024 * 1024)
BACKUP_ROOT = settings.docs_originals_root()
_A4 = (595, 842)


def _shrink_pdf(src: Path) -> Optional[bytes]:
    """Re-render each page as a JPEG on an A4-sized page, from ~150 dpi down, until the
    whole file fits.  (A4 page sizing also fixes scans saved with giant page sizes.)"""
    import pymupdf
    from PIL import Image
    doc = pymupdf.open(str(src))
    try:
        for long_px, quality in [(1754, 70), (1600, 65), (1400, 60), (1240, 55), (1100, 50), (950, 45)]:
            out = pymupdf.open()
            for p in doc:
                zoom = long_px / max(p.rect.width, p.rect.height)
                pix = p.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
                img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                buf = io.BytesIO()
                img.save(buf, "JPEG", quality=quality, optimize=True)
                w, h = _A4 if p.rect.height >= p.rect.width else _A4[::-1]
                s = min(w / p.rect.width, h / p.rect.height)
                page = out.new_page(width=p.rect.width * s, height=p.rect.height * s)
                page.insert_image(page.rect, stream=buf.getvalue())
            data = out.tobytes(deflate=True, garbage=4)
            out.close()
            if len(data) <= _TARGET_BYTES:
                return data
        return None
    finally:
        doc.close()


def _shrink_image(src: Path) -> Optional[bytes]:
    from PIL import Image
    with Image.open(src) as im:
        im = im.convert("RGB")
        for long_px in (2400, 2000, 1600, 1300):
            scaled = im.copy()
            scaled.thumbnail((long_px, long_px))
            for quality in (80, 70, 60, 50):
                buf = io.BytesIO()
                scaled.save(buf, "JPEG", quality=quality, optimize=True)
                if buf.tell() <= _TARGET_BYTES:
                    return buf.getvalue()
    return None


def shrink_large_files(folder: Path, root: Path) -> List[str]:
    """Bring every file over 2 MB in `folder` under 2 MB (original backed up first).
    Returns a line per file handled, for the run report."""
    import shutil
    report = []
    for f in sorted(folder.iterdir()):
        if not f.is_file() or f.name == LOCAL_DONE_MARKER or f.stat().st_size <= MAX_FILE_BYTES:
            continue
        before = f.stat().st_size
        ext = f.suffix.lower()
        try:
            if ext == ".pdf":
                data = _shrink_pdf(f)
            elif ext in (".jpg", ".jpeg", ".png"):
                data = _shrink_image(f)
            else:
                report.append(f"{f.name}: {before / 1048576:.1f} MB, type not supported - left as is")
                continue
        except Exception as e:
            report.append(f"{f.name}: could not compress ({e}) - left as is")
            continue
        if data is None:
            report.append(f"{f.name}: {before / 1048576:.1f} MB, could not get under 2 MB - left as is")
            continue
        backup = BACKUP_ROOT / f.relative_to(root)
        backup.parent.mkdir(parents=True, exist_ok=True)
        if not backup.exists():
            shutil.copy2(f, backup)
        dest = f.with_suffix(".jpg") if ext == ".png" else f  # shrunk images are JPEG
        tmp = dest.with_name(dest.name + ".part")
        tmp.write_bytes(data)
        tmp.replace(dest)
        if dest != f:
            f.unlink()
        report.append(f"{dest.name}: {before / 1048576:.1f} MB -> {len(data) / 1048576:.2f} MB")
    return report


# --- what a re-download changes (run_local) -------------------------------------------------
def _read_marker(folder: Path) -> Tuple[str, Optional[str]]:
    """A student folder's marker -> (version, docs): ("", None) when there is none, ("v2", the
    portal's document list) for the current format, ("v1", its text) for an older one ("" or
    "ok" when it predates the document list; "" also when it cannot be read)."""
    marker = folder / LOCAL_DONE_MARKER
    if not marker.exists():
        return "", None
    try:
        text = marker.read_text(encoding="utf-8")
    except Exception:
        return "v1", ""
    head, sep, rest = text.partition("\n")
    if sep and head.strip() == MARKER_VERSION:
        return MARKER_VERSION, rest.strip()
    return "v1", text.strip()


def _today() -> str:
    return date.today().isoformat()


def _held_today(folder: Path) -> bool:
    """Was this student held back by the sweep's circuit breaker today (SWEEP_HELD)?"""
    try:
        return (folder / SWEEP_HELD).read_text(encoding="utf-8").strip() == _today()
    except OSError:
        return False


def _note_held(folder: Path, held: bool) -> None:
    """Remember (or forget) that the breaker held this student back today. Never raises."""
    try:
        if held:
            (folder / SWEEP_HELD).write_text(_today(), encoding="utf-8")
        else:
            (folder / SWEEP_HELD).unlink(missing_ok=True)
    except OSError as e:
        logger.warning("could not note the catch-up hold for %s: %s", folder.name, e)


def _write_marker(folder: Path, docs: str) -> None:
    """Exactly "v2\\n<docs>" (bytes, so Windows writes no \\r), through .part and a rename."""
    tmp = folder / (LOCAL_DONE_MARKER + ".part")
    tmp.write_bytes(f"{MARKER_VERSION}\n{docs}".encode("utf-8"))
    tmp.replace(folder / LOCAL_DONE_MARKER)


def _passport_key(value) -> str:
    return re.sub(r"\s+", "", str(value or "")).upper()


def layout_alert(students: List[Dict[str, str]]) -> str:
    """Why the verified list looks wrong, in counts only (never a name) — "" when it looks right.
    From LAYOUT_MIN_ROWS rows on, more than LAYOUT_MAX_BLANK of them without a document list or a
    passport number means the page was not read as it should be (a changed layout): nothing may
    be downloaded or reset from it."""
    total = len(students)
    if total < LAYOUT_MIN_ROWS:
        return ""
    no_docs = sum(1 for s in students if not str(s.get("docs") or "").strip())
    no_passport = sum(1 for s in students if not str(s.get("passport") or "").strip())
    blank = sum(1 for s in students if not str(s.get("docs") or "").strip()
                or not str(s.get("passport") or "").strip())
    if blank <= total * LAYOUT_MAX_BLANK:
        return ""
    return (f"{no_docs} of {total} row(s) show no document list and {no_passport} no passport number")


def _set_aside(path: Path, base: Path, day: str) -> Path:
    """Move `path` to <base>/_replaced/<day>/ under its own name (" (2)", " (3)" … when that name
    is taken there).  Nothing is overwritten or deleted."""
    where = base / REPLACED_DIR / day
    where.mkdir(parents=True, exist_ok=True)
    target, n = where / path.name, 1
    while target.exists():
        n += 1
        target = where / f"{path.stem} ({n}){path.suffix}"
    shutil.move(str(path), str(target))
    return target


class _Plan:
    """What extracting one student's ZIP would change, worked out before anything is touched.
    steps: (name, new bytes or None, the local copy to set aside or None, its original under
    BACKUP_ROOT to set aside or None), in ZIP order; a step without bytes only sets a file aside.
    added / replaced count documents (ZIP entries): one whose old copy is set aside, under its own
    name or another extension's, is replaced; one that had no copy on this PC is added."""

    def __init__(self) -> None:
        self.files = 0                   # documents in the ZIP
        self.same = 0                    # equal to the copy on this PC: left alone
        self.ambiguous: List[str] = []   # sweep only: a shrunk copy without its original, left alone
        self.steps: List[Tuple[str, Optional[bytes], Optional[Path], Optional[Path]]] = []
        self.kind: Dict[str, str] = {}   # entry name (lower case) -> "new" | "same" | "kept" | "replaced"

    @property
    def added(self) -> int:
        return sum(1 for k in self.kind.values() if k == "new")

    @property
    def replaced(self) -> int:
        return sum(1 for k in self.kind.values() if k == "replaced")


def _plan(z: zipfile.ZipFile, folder: Path, root: Path, *, sweep: bool = False,
          stems: bool = False) -> _Plan:
    """Compare each ZIP entry with the copy on this PC: the original under BACKUP_ROOT when the
    copy was shrunk (a .png shrunk to .jpg included), else the copy itself.  `sweep`: a copy that
    looks shrunk but has no original to compare with is left alone, not replaced.  `stems` (a
    re-download because the portal's documents changed): a local file of an entry's name under
    another extension (PASSPORT.jpg when the ZIP has PASSPORT.pdf) is set aside too, except a .jpg
    whose original under BACKUP_ROOT has the entry's name (it is that entry, shrunk)."""
    rel = folder.relative_to(root)
    plan = _Plan()
    names: List[str] = []
    seen, matched = set(), set()
    for info in z.infolist():
        if info.is_dir() or not info.filename.rsplit("/", 1)[-1]:
            continue
        fname = _safe_path_part(info.filename.rsplit("/", 1)[-1])
        if fname.lower() in seen:
            continue                     # one name twice in the ZIP: the first one counts
        seen.add(fname.lower())
        names.append(fname)
        plan.files += 1
        data = z.read(info)
        dest = folder / fname
        if fname.lower().endswith(".png") and not dest.exists() and dest.with_suffix(".jpg").exists():
            dest = dest.with_suffix(".jpg")   # shrink_large_files saves a shrunk .png as .jpg
        backup = BACKUP_ROOT / rel / fname
        has_backup = backup.is_file()
        if not dest.exists():
            stale = backup if has_backup and backup.read_bytes() != data else None
            plan.steps.append((fname, data, None, stale))
            plan.kind[fname.lower()] = "new"
            continue
        matched.add(dest.name.lower())
        if (backup if has_backup else dest).read_bytes() == data:
            plan.same += 1
            plan.kind[fname.lower()] = "same"
            continue
        if sweep and not has_backup and (len(data) > MAX_FILE_BYTES or dest.name != fname):
            plan.ambiguous.append(fname)
            plan.kind[fname.lower()] = "kept"
            continue
        plan.steps.append((fname, data, dest, backup if has_backup else None))
        plan.kind[fname.lower()] = "replaced"
    if stems and folder.is_dir():
        by_stem: Dict[str, List[str]] = {}
        for name in names:
            by_stem.setdefault(Path(name).stem.lower(), []).append(name)
        for f in sorted(folder.iterdir()):
            low = f.name.lower()
            entries = by_stem.get(f.stem.lower())
            if (not f.is_file() or f.name.startswith(".") or low.endswith(".part") or low in seen
                    or low in matched or not entries):
                continue
            if f.suffix.lower() == ".jpg" and any((BACKUP_ROOT / rel / n).is_file() for n in entries):
                continue                 # that entry's shrunk copy: its original has the entry's name
            backup = BACKUP_ROOT / rel / f.name
            plan.steps.append((f.name, None, f, backup if backup.is_file() else None))
            # the document it was is replaced (the entry with no copy of its own name first)
            entry = next((n for n in entries if plan.kind[n.lower()] == "new"), entries[0])
            plan.kind[entry.lower()] = "replaced"
    return plan


def _apply(plan: _Plan, folder: Path, root: Path) -> Tuple[int, int]:
    """Carry out a plan -> (added, replaced).  Each old copy (and its original) is moved to
    _replaced/<today>/ just before the new one is written, through .part and a rename."""
    day = date.today().isoformat()
    originals = BACKUP_ROOT / folder.relative_to(root)
    folder.mkdir(parents=True, exist_ok=True)
    for fname, data, local, backup in plan.steps:
        if local is not None:
            _set_aside(local, folder, day)
        if backup is not None and backup.exists():
            _set_aside(backup, originals, day)
        if data is not None:
            dest = folder / fname
            tmp = dest.with_name(dest.name + ".part")
            tmp.write_bytes(data)
            tmp.replace(dest)
    return plan.added, plan.replaced


async def run_local(root: Path, limit: int = 0, skip_drive_done: bool = True,
                    confirm_replace: Iterable[str] = ()) -> Dict[str, list]:
    """Same as run(), but saves into a folder on this PC:
    <root>/<PROGRAM>/<FULL NAME> (<PASSPORT NO>)/<files>.  Files over 2 MB are shrunk.

    A student is fetched when they have no marker yet (a first download) or when the portal's
    document list is not the one their marker recorded (a re-download).  Each ZIP entry is compared
    byte for byte with the copy on this PC (_plan): an equal one is left alone; a changed one
    replaces the old copy, which goes to <student>/_replaced/<date>/.  The marker is written only
    once every entry and the shrinking succeeded, so a run that stops halfway is done again.
    A student whose marker is older than this (no "v2" line) is compared once more by the
    catch-up sweep, REFRESH_PER_RUN a run, oldest marker first, which replaces nothing when the
    circuit breaker trips (SWEEP_MAX_STUDENTS); such a student's marker is left as it was, and the
    sweep fetches them again from the next day (SWEEP_HELD).

    Returns {"saved": [(program, name, added, shrunk, replaced)] for first downloads,
    "redownloaded": [the same] for students fetched again because their portal files changed,
    "refreshed": [the same] for the sweep's students, "held": [(program, name, differing, files)]
    for the sweep's students the breaker held back, "failed": [(name, error)], "students": every
    verified student the list showed (fetch_verified_students: the whole list, whatever `limit`
    is)}, and "layout_alert" (then nothing was downloaded and no marker touched) or
    "drive_lookup_error" (the saved Drive list was used) when they apply.

    `confirm_replace`: these passports only, each compared even when their marker is current, and
    with no circuit breaker (python -m src.sheets.verified_docs --confirm-replace)."""
    from src.scraper.client import admin_client as client
    try:
        await client.login()
        students = await fetch_verified_students(client)
        listed = list(students)
        print(f"{len(students)} verified student(s) on the portal.")
        result: Dict[str, list] = {"saved": [], "redownloaded": [], "refreshed": [], "held": [],
                                   "failed": [], "students": listed}
        alert = layout_alert(listed)
        if alert:
            result["layout_alert"] = alert
            print(f"The verified-students list looks wrong ({alert}) - nothing downloaded, no marker touched.")
            return result
        confirm = {_passport_key(p) for p in confirm_replace if _passport_key(p)}
        if confirm:
            students = [s for s in students if _passport_key(s.get("passport")) in confirm]
            print(f"Only {len(students)} student(s) to compare with --confirm-replace.")
        if limit:
            students = students[:limit]
        in_drive = _drive_names(result) if skip_drive_done and not confirm else set()
        if in_drive:
            print(f"{len(in_drive)} already complete in Google Drive - not downloaded again.")
        print(f"Saving to: {root}")

        sweep: List[Tuple[float, int, Dict[str, str], Path]] = []
        done = skipped = drive_skipped = failed = 0
        for n, s in enumerate(students, 1):
            name = folder_name(s)
            if name in in_drive:
                drive_skipped += 1
                continue
            folder = root / _safe_path_part(program_folder(s)) / _safe_path_part(name)
            # Skip only when the portal still lists exactly the documents that were
            # downloaded.  The marker records the portal's own filenames, which carry
            # the upload time — so a student who replaces a rejected document is fetched
            # again instead of being trusted forever.
            version, was = _read_marker(folder)
            now = s.get("docs", "")
            again = bool(version)            # downloaded before: this is a re-download
            if again:
                if not now:                  # a row without its document list: nothing to go by
                    skipped += 1
                    continue
                current = was == now if version == MARKER_VERSION else was in ("", "ok", now)
                if current:
                    if version != MARKER_VERSION and not confirm and _held_today(folder):
                        skipped += 1                 # held back by the breaker today: tomorrow again
                    elif version != MARKER_VERSION or confirm:
                        try:
                            at = (folder / LOCAL_DONE_MARKER).stat().st_mtime
                        except OSError:
                            at = 0.0
                        sweep.append((at, n, s, folder))
                    else:
                        skipped += 1
                    continue
                print(f"[{n}/{len(students)}] {name}: documents changed on the portal — "
                      "downloading again")
            try:
                data = await _download_zip(client, s["uid"])
                folder.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(io.BytesIO(data)) as z:
                    plan = _plan(z, folder, root, stems=again)
                added, replaced = _apply(plan, folder, root)
                shrunk = shrink_large_files(folder, root)
                # record what the portal held at this moment, so a later upload is noticed
                _write_marker(folder, now)
                done += 1
                result["redownloaded" if again else "saved"].append(
                    (program_folder(s), name, added, shrunk, replaced))
                print(f"[{n}/{len(students)}] {name}: {added} {'new ' if again else ''}file(s) saved"
                      + (f", {replaced} replaced (old copies in {REPLACED_DIR})" if replaced else ""))
                for line in shrunk:
                    print(f"      compressed {line}")
            except Exception as e:
                failed += 1
                result["failed"].append((name, str(e)))
                print(f"[{n}/{len(students)}] {name}: FAILED - {e}")

        if sweep:
            failed += await _sweep(client, root, sweep, result, confirmed=bool(confirm))
        print(f"\nFinished: {done} saved, {skipped} already on this PC, "
              f"{len(result['refreshed'])} compared again (catch-up), {len(result['held'])} held back, "
              f"{drive_skipped} already in Drive, {failed} failed.")
        if failed:
            print("Run again to retry the failed ones (finished folders are skipped).")
        return result
    finally:
        await client.close()


async def _sweep(client, root: Path, queue: List[Tuple[float, int, Dict[str, str], Path]],
                 result: Dict[str, list], confirmed: bool = False) -> int:
    """The catch-up sweep: students whose marker predates the byte comparison (or, `confirmed`,
    the --confirm-replace students) are fetched again and compared, REFRESH_PER_RUN a run, oldest
    marker first.  Equal files are left alone and the marker is upgraded.  Replacing is held back,
    files and marker untouched (the student is compared again from the next day, SWEEP_HELD, so
    the run's places go to the others meanwhile), when a student would have more
    than half of their files replaced or more than SWEEP_MAX_STUDENTS students would — unless
    `confirmed`.  Adds to result["refreshed"] / result["held"] / result["failed"]; -> failures."""
    queue = sorted(queue, key=lambda c: (c[0], c[1]))
    if not confirmed:
        queue = queue[:REFRESH_PER_RUN]
    failed = 0
    waiting: List[Tuple[Dict[str, str], Path, _Plan]] = []     # would replace: decided at the end
    held: List[Tuple[Dict[str, str], Path, int, int]] = []
    would = 0                                                   # students with something to replace

    def finish(s, folder, plan):
        added, replaced = _apply(plan, folder, root)
        shrunk = shrink_large_files(folder, root)
        _write_marker(folder, s.get("docs", ""))
        _note_held(folder, False)
        result["refreshed"].append((program_folder(s), folder_name(s), added, shrunk, replaced))
        print(f"   catch-up {folder_name(s)}: {plan.same} file(s) the same, {added} added, {replaced} replaced"
              + (f", {len(plan.ambiguous)} shrunk copy(ies) without an original left alone"
                 if plan.ambiguous else ""))

    for _, _, s, folder in queue:
        try:
            data = await _download_zip(client, s["uid"])
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                # --confirm-replace: the portal's copy wins even over a shrunk copy without its original
                plan = _plan(z, folder, root, sweep=not confirmed)
            if plan.replaced and not confirmed:
                would += 1
                if plan.replaced * 2 > plan.files or would > SWEEP_MAX_STUDENTS:
                    held.append((s, folder, plan.replaced, plan.files))
                    continue
                waiting.append((s, folder, plan))
                continue
            finish(s, folder, plan)
        except Exception as e:
            failed += 1
            result["failed"].append((folder_name(s), str(e)))
            print(f"   catch-up {folder_name(s)}: FAILED - {e}")
    if would > SWEEP_MAX_STUDENTS:                 # too many at once: none is replaced
        held += [(s, folder, plan.replaced, plan.files) for s, folder, plan in waiting]
        waiting = []
    for s, folder, plan in waiting:
        try:
            finish(s, folder, plan)
        except Exception as e:
            failed += 1
            result["failed"].append((folder_name(s), str(e)))
            print(f"   catch-up {folder_name(s)}: FAILED - {e}")
    for s, folder, differ, files in held:
        _note_held(folder, True)                    # not fetched again until tomorrow
        result["held"].append((program_folder(s), folder_name(s), differ, files))
        print(f"   catch-up {folder_name(s)}: the portal ZIP differs for {differ} of {files} file(s) — "
              "held back, nothing replaced (python -m src.sheets.verified_docs --confirm-replace "
              f"{s.get('passport', '')} replaces them)")
    return failed


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s  %(message)s")
    ap = argparse.ArgumentParser(description="Copy verified students' portal documents to Drive or this PC.")
    ap.add_argument("--list", action="store_true", help="only list the students")
    ap.add_argument("--limit", type=int, default=0, help="only the first N students")
    ap.add_argument("--local", metavar="FOLDER", help="save on this PC instead of Drive, e.g. "
                    f"\"{settings.docs_root()}\"")
    ap.add_argument("--include-drive-done", action="store_true",
                    help="with --local: also download students already finished in Drive")
    ap.add_argument("--confirm-replace", metavar="PASSPORT", default="",
                    help="on this PC: compare these students' files with the portal's (comma "
                         "separated) and replace what differs, even when the catch-up check held "
                         "it back; --local defaults to the documents folder")
    args = ap.parse_args()
    confirm = [p for p in args.confirm_replace.split(",") if p.strip()]
    if confirm and not args.local:
        args.local = str(settings.docs_root())
    loop = asyncio.new_event_loop()
    try:
        if args.local and not args.list and confirm:
            # the scheduled sync writes the same folders: never at the same time as it, so this
            # takes its lock (and a sync that starts meanwhile skips its run)
            from src.sheets import auto_sync
            if auto_sync._lock_held(auto_sync.LOCK_PATH):
                print("A portal sync is running (or paused) right now — try again in a few minutes.")
                return
            auto_sync.LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
            auto_sync.LOCK_PATH.write_text(str(os.getpid()), encoding="utf-8")
            try:
                loop.run_until_complete(run_local(Path(args.local), limit=args.limit,
                                                  confirm_replace=confirm))
            finally:
                auto_sync.LOCK_PATH.unlink(missing_ok=True)
        elif args.local and not args.list:
            loop.run_until_complete(run_local(Path(args.local), limit=args.limit,
                                              skip_drive_done=not args.include_drive_done))
        else:
            loop.run_until_complete(run(limit=args.limit, list_only=args.list))
    finally:
        loop.close()


if __name__ == "__main__":
    main()
