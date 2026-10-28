"""8 b static independence scan of the frozen app folder (APP_SPEC rev 1 §8 b, amended by rev 2 R2.6).

    <build python> -I -B packaging\\tests\\scan_bundle.py "<dist>\\Hangeul Document Checker"
                    [--allow packaging\\scan_allow.txt] [--stage-list <builds>\\<id>\\reports\\STAGE_LIST.txt]
                    [--json <report.json>]
    or, from build.py:  scan_bundle.main([...])  /  scan_bundle.scan(app_dir, allow, stage_list) -> Report

Reads (about 4.6 GB, chunk by chunk) every file of the folder raw; every member of ``base_library.zip``; every entry
of each exe's PyInstaller archive (``PyInstaller.archive.readers.CArchiveReader``, decompressed) and every module of
the embedded PYZ (or a separate ``.pyz``), decompressed and unmarshalled, so a hit names the module, its
``co_filename`` and the constant. It looks for (case-insensitive, UTF-8 and UTF-16LE) ``c:\\hangeul``,
``c:/hangeul``, ``c:\\\\hangeul``, ``jarvis``, ``private-runner`` and ``.venv``.

Pass:
  - zero hits except the lines of ``packaging\\scan_allow.txt`` (``module or file | string | reason``; a file is a
    path relative to the app folder with ``/``, ``*`` and ``?`` allowed; a hit is allowed when its module or file
    matches and its constant or context contains the string). For OUR code (``src.*``, the glue, the entry) the
    file may allow exactly ONE line: the card gate's default bot lock (R2.2);
  - the ``src.*`` modules in the PYZ equal ``STAGE_LIST.txt``;
  - no excluded module (R2.4.2), no onnxruntime, PySide6 or ``DirectML.dll``, no ``opencv_videoio_ffmpeg*``, no
    ONNX model, no ``torch\\include`` / ``torch\\share``, no ``.lib`` / ``.h`` / ``.hpp`` / ``.cuh`` files.
Advisory (reported, never failing): ``\\users\\``, ``telegram``, ``supabase``, ``token.json``; ``tests``/``test``
folders; allow lines that matched nothing.

Prints counts, module names and allow-list lines; a hit's constant is cut to 120 characters (the forbidden strings
are this PC's folders, never student data). Exit 0 pass, 1 fail, 2 could not run.
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse  # noqa: E402
import fnmatch  # noqa: E402
import io  # noqa: E402
import json  # noqa: E402
import marshal  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import types  # noqa: E402
import zipfile  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Set, Tuple  # noqa: E402

PKG = Path(__file__).resolve().parent.parent
FORBIDDEN = ("c:\\hangeul", "c:/hangeul", "c:\\\\hangeul", "jarvis", "private-runner", ".venv")
ADVISORY = ("\\users\\", "telegram", "supabase", "token.json")
OUR_TOP = ("src", "glue_numpy", "easyocr_vendor", "hdc_entry")
OUR_ALLOWED = ("src.verify.runner.app.cardgate", "c:\\hangeul\\bot\\data\\verification\\auto_verify.lock")
EXCLUDED_MODULES = (
    "onnxruntime", "onnx", "onnxscript", "tensorboard", "torch.utils.tensorboard", "triton",
    "easyocr.DBNet", "easyocr.detection_db", "easyocr.export", "easyocr.cli", "easyocr.scripts",
    "PySide6", "shiboken6", "PyQt5", "PyQt6", "matplotlib", "pandas", "IPython", "pytest",
    "pip", "venv", "ensurepip", "idlelib", "lib2to3", "pydoc_data",
    "src.verify.runner.devplaces", "src.verify.runner.tests", "src.verify.runner.selftest",
    "src.verify.runner.app.tests",
    "src.scraper.client", "src.sheets.passport_issue", "src.sheets.verified_docs", "src.verify.auto_verify",
    "pypdf", "googleapiclient", "google_auth_oauthlib", "httpx",
)
FORBIDDEN_FILES = ("directml.dll", "onnxruntime*.dll", "opencv_videoio_ffmpeg*", "*.onnx", "*.lib", "*.h", "*.hpp",
                   "*.cuh")
FORBIDDEN_DIRS = ("torch/include", "torch/share")
CHUNK = 16 * 2 ** 20
MAX_HITS_PER_KEY = 20
CTX = 60


# ---------------------------------------------------------------------------------------------- the byte search

def _patterns(words: Sequence[str]) -> List[Tuple[str, str, bytes]]:
    out = []
    for w in words:
        out.append((w, "utf-8", w.lower().encode("utf-8")))
        out.append((w, "utf-16le", w.lower().encode("utf-16-le")))
    return out


PATTERNS = _patterns(FORBIDDEN)
ADVISORY_PATTERNS = _patterns(ADVISORY)


def _context(data: bytes, i: int, n: int, enc: str) -> str:
    if enc == "utf-16le":
        a = max(0, i - 2 * CTX)
        a -= (i - a) % 2
        s = data[a:i + n + 2 * CTX].decode("utf-16-le", errors="replace")
    else:
        s = data[max(0, i - CTX):i + n + CTX].decode("latin-1")
    return "".join(ch if 32 <= ord(ch) < 127 else " " for ch in s)


def find_in_stream(fp: io.BufferedIOBase, patterns: Sequence[Tuple[str, str, bytes]], chunk: int = CHUNK,
                   cap: int = MAX_HITS_PER_KEY) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """{(word, encoding): {"count": n, "contexts": [...]}} over a stream read chunk by chunk (a match across a
    chunk boundary is found once)."""
    overlap = max(len(p) for _, _, p in patterns) - 1
    hits: Dict[Tuple[str, str], Dict[str, Any]] = {}
    tail = b""
    while True:
        buf = fp.read(chunk)
        if not buf:
            break
        data = tail + buf
        low = data.lower()
        for word, enc, pat in patterns:
            i = low.find(pat)
            while i != -1:
                if i + len(pat) > len(tail):
                    h = hits.setdefault((word, enc), {"count": 0, "contexts": []})
                    h["count"] += 1
                    if len(h["contexts"]) < cap:
                        h["contexts"].append(_context(data, i, len(pat), enc))
                i = low.find(pat, i + 1)
        tail = data[-overlap:] if overlap > 0 else b""
    return hits


def find_in_bytes(data: bytes, patterns: Sequence[Tuple[str, str, bytes]]) -> Dict[Tuple[str, str], Dict[str, Any]]:
    return find_in_stream(io.BytesIO(data), patterns, chunk=max(1, len(data)) if data else 1)


# ---------------------------------------------------------------------------------------------- code objects

def code_strings(code: types.CodeType) -> Iterator[Tuple[str, str]]:
    """(co_filename, constant) for every str/bytes constant of a code object and its nested ones, and the
    co_filename itself (constant "")."""
    seen: Set[int] = set()
    stack = [code]
    while stack:
        c = stack.pop()
        if id(c) in seen:
            continue
        seen.add(id(c))
        yield c.co_filename, ""
        todo = list(c.co_consts)
        while todo:
            k = todo.pop()
            if isinstance(k, types.CodeType):
                stack.append(k)
            elif isinstance(k, str):
                yield c.co_filename, k
            elif isinstance(k, bytes):
                yield c.co_filename, k.decode("latin-1")
            elif isinstance(k, (tuple, frozenset, list, set)):
                todo.extend(k)


@dataclass
class Hit:
    where: str           # "file", "base_library", "pkg", "pyz"
    key: str             # module name (dotted) or file path relative to the app folder ("/")
    word: str
    encoding: str
    count: int
    context: str         # the constant or the bytes around the first hit, cut
    co_filename: str = ""
    allowed_by: Optional[int] = None


def _cut(s: str, n: int = 120) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[:n - 3] + "..."


_USER_RE = re.compile(r"(?i)([\\/]users[\\/]{1,2})[^\\/\s\"']+")
_DEEP_RE = re.compile(r"(?i)((?:c:[\\/]{1,2}hangeul|private-runner)(?:[\\/]{1,2}[^\\/\"'|<>]+)?)[\\/][^\"'|<>]*")


def mask_paths(s: str) -> str:
    """For printing and the report file: a path keeps one folder after C:\\Hangeul (or private-runner) and the
    user name becomes <user>, so a hit can never show a student's folder."""
    return _DEEP_RE.sub(lambda m: m.group(1) + "\\...", _USER_RE.sub(lambda m: m.group(1) + "<user>", s))


def _hits_from_code(where: str, module: str, code: Any, raw: bytes) -> List[Hit]:
    """Hits of one module: by constant (naming co_filename) plus anything else in its marshal bytes."""
    out: List[Hit] = []
    found: Set[str] = set()
    if isinstance(code, types.CodeType):
        for fname, const in code_strings(code):
            text = (const or fname).lower()
            for word in FORBIDDEN:
                if word in text:
                    found.add(word)
                    out.append(Hit(where, module, word, "utf-8", text.count(word), _cut(const or fname),
                                   co_filename=_cut(fname)))
    for (word, enc), h in find_in_bytes(raw, PATTERNS).items():
        if word not in found:
            out.append(Hit(where, module, word, enc, h["count"], _cut(h["contexts"][0])))
    return _dedupe(out)


def _dedupe(hits: List[Hit]) -> List[Hit]:
    seen: Dict[Tuple[str, str, str, str], Hit] = {}
    for h in hits:
        k = (h.where, h.key, h.word, h.context)
        if k in seen:
            seen[k].count += h.count
        else:
            seen[k] = h
    return list(seen.values())


# ---------------------------------------------------------------------------------------------- archives

class ArchiveSource:
    """What the scan needs from PyInstaller's readers; tests pass a fake with the same two methods."""

    def pkg_entries(self, exe: Path) -> Optional[List[Tuple[str, str, bytes]]]:
        """[(name, typecode, decompressed bytes)] of the exe's CArchive; None when the exe has none."""
        from PyInstaller.archive.readers import CArchiveReader, NotAnArchiveError
        try:
            r = CArchiveReader(str(exe))
        except (NotAnArchiveError, ValueError, OSError):
            return None
        out = []
        for name, entry in r.toc.items():
            typecode = entry[-1]
            if typecode == "z":
                out.append((name, "z", b""))
                continue
            try:
                out.append((name, typecode, r.extract(name)))
            except Exception:
                out.append((name, typecode, b""))
        return out

    def pyz_modules(self, exe_or_pyz: Path, pkg_name: Optional[str]) -> List[Tuple[str, int, bytes]]:
        """[(module, typecode, decompressed marshal bytes)] of the PYZ embedded in ``exe_or_pyz`` (entry
        ``pkg_name``) or of a separate .pyz file (``pkg_name`` None)."""
        if pkg_name is not None:
            from PyInstaller.archive.readers import CArchiveReader
            pyz = CArchiveReader(str(exe_or_pyz)).open_embedded_archive(pkg_name)
        else:
            from PyInstaller.loader.pyimod01_archive import ZlibArchiveReader
            pyz = ZlibArchiveReader(str(exe_or_pyz))
        out = []
        for name, (typecode, _off, _len) in pyz.toc.items():
            try:
                raw = pyz.extract(name, raw=True) or b""
            except Exception:
                raw = b""
            out.append((name, int(typecode), raw))
        return out


def _unmarshal(raw: bytes) -> Any:
    try:
        return marshal.loads(raw)
    except Exception:
        return None


def module_of_member(member: str) -> str:
    m = member.replace("\\", "/")
    for ext in (".pyc", ".py"):
        if m.endswith(ext):
            m = m[:-len(ext)]
    parts = [p for p in m.split("/") if p]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


# ---------------------------------------------------------------------------------------------- allow list

@dataclass
class AllowLine:
    number: int
    key: str
    string: str
    reason: str
    used: int = 0

    def matches(self, h: Hit) -> bool:
        k = self.key.replace("\\", "/")
        key_ok = fnmatch.fnmatchcase(h.key.lower(), k.lower()) or h.key.lower() == k.lower()
        if not key_ok:
            return False
        s = self.string.lower()
        return s in h.context.lower() or s in h.co_filename.lower() or (s == h.word)


def load_allow(path: Optional[Path]) -> Tuple[List[AllowLine], List[str]]:
    """The allow lines and the format errors (a missing file is no lines and no error)."""
    lines: List[AllowLine] = []
    errors: List[str] = []
    if path is None or not Path(path).is_file():
        return lines, errors
    for n, raw in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        parts = [p.strip() for p in s.split("|")]
        if len(parts) < 3 or not parts[0] or not parts[1] or not parts[2]:
            errors.append(f"scan_allow.txt line {n}: needs 'module or file | string | reason'")
            continue
        lines.append(AllowLine(n, parts[0], parts[1], "|".join(parts[2:]).strip()))
    ours = [a for a in lines if is_ours(a.key)]
    for a in ours:
        if (a.key.lower(), a.string.lower()) != OUR_ALLOWED:
            errors.append(f"scan_allow.txt line {a.number}: our code may allow only the card gate's default lock "
                          f"({OUR_ALLOWED[0]} | {OUR_ALLOWED[1]})")
    if len(ours) > 1:
        errors.append(f"scan_allow.txt: {len(ours)} lines for our code; exactly one is allowed")
    return lines, errors


def is_ours(key: str) -> bool:
    k = key.replace("\\", "/").lower()
    top = re.split(r"[./]", k, maxsplit=1)[0]
    if k.startswith("_internal/"):
        top = re.split(r"[./]", k[len("_internal/"):], maxsplit=1)[0]
    return top in OUR_TOP


# ---------------------------------------------------------------------------------------------- stage list

def stage_modules(path: Path) -> Set[str]:
    """The src.* modules named by STAGE_LIST.txt. Task F's lines are tab-separated (stage path, module or "-",
    group, bytes, sha256): the module column is used. Other shapes: the first .py path with a ``src`` folder.
    '#' lines and other files are ignored."""
    mods: Set[str] = set()
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        cols = s.split("\t")
        if len(cols) >= 5 and cols[0].strip().lower().endswith(".py") and re.match(r"^[A-Za-z_][\w.]*$|^-$",
                                                                                    cols[1].strip()):
            m = cols[1].strip()
            if m == "src" or m.startswith("src."):
                mods.add(m)
            continue
        for tok in re.split(r"\s*(?:\||\t|->|<-)\s*|\s+", s):
            t = tok.strip().strip('"').replace("\\", "/")
            if not t.lower().endswith(".py"):
                continue
            parts = [p for p in t.split("/") if p]
            if "src" not in parts:
                continue
            parts = parts[parts.index("src"):]
            mods.add(module_of_member("/".join(parts)))
            break
    return mods


# ---------------------------------------------------------------------------------------------- the scan

@dataclass
class Report:
    files: int = 0
    bytes: int = 0
    hits: List[Hit] = field(default_factory=list)
    advisory: List[Hit] = field(default_factory=list)
    modules: Set[str] = field(default_factory=set)
    failures: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    allow_unused: List[int] = field(default_factory=list)

    @property
    def unallowed(self) -> List[Hit]:
        return [h for h in self.hits if h.allowed_by is None]

    @property
    def passed(self) -> bool:
        return not self.failures and not self.unallowed

    def to_json(self) -> Dict[str, Any]:
        def masked(h: Hit) -> Dict[str, Any]:
            d = dict(h.__dict__)
            d["context"] = mask_paths(h.context)
            d["co_filename"] = mask_paths(h.co_filename)
            return d
        return {"passed": self.passed, "files": self.files, "bytes": self.bytes,
                "failures": [mask_paths(f) for f in self.failures], "notes": [mask_paths(n) for n in self.notes],
                "allow_unused": self.allow_unused, "hits": [masked(h) for h in self.hits],
                "advisory": [masked(h) for h in self.advisory],
                "src_modules": sorted(m for m in self.modules if m == "src" or m.startswith("src."))}


def _rel(root: Path, p: Path) -> str:
    return p.relative_to(root).as_posix()


def _file_module(rel: str) -> Optional[str]:
    """A dotted module name for a .py/.pyc/.pyd under _internal (``torch/_C.cp312-win_amd64.pyd`` -> torch._C)."""
    r = rel[len("_internal/"):] if rel.startswith("_internal/") else rel
    name = r.rsplit("/", 1)[-1]
    if name.endswith(".pyd"):
        stem = name.split(".", 1)[0]
        return ".".join([p for p in r.split("/")[:-1]] + [stem])
    if name.endswith((".py", ".pyc")):
        return module_of_member(r)
    return None


def scan(app_dir: Path, allow_path: Optional[Path] = None, stage_list: Optional[Path] = None,
         source: Optional[ArchiveSource] = None, chunk: int = CHUNK,
         progress: Optional[Callable[[str], None]] = None) -> Report:
    root = Path(app_dir)
    rep = Report()
    if not root.is_dir():
        rep.failures.append("the app folder was not found")
        return rep
    allow, errors = load_allow(allow_path)
    rep.failures.extend(errors)
    if allow_path is not None and not Path(allow_path).is_file():
        rep.notes.append("scan_allow.txt not found: no hit is allowed")
    source = source or ArchiveSource()
    file_modules: Set[str] = set()
    top_dirs: Set[str] = set()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for d in dirnames:
            rel = _rel(root, Path(dirpath) / d)
            r = rel[len("_internal/"):] if rel.startswith("_internal/") else rel
            if "/" not in r:
                top_dirs.add(r)
            if any(r.lower() == f or r.lower().startswith(f + "/") for f in FORBIDDEN_DIRS):
                rep.failures.append(f"left-out folder present: {r}")
            if d.lower() in ("tests", "test"):
                rep.notes.append(f"advisory: a '{d}' folder: {rel}")
        for fn in sorted(filenames):
            p = Path(dirpath) / fn
            rel = _rel(root, p)
            rep.files += 1
            low = fn.lower()
            for pat in FORBIDDEN_FILES:
                if fnmatch.fnmatchcase(low, pat):
                    rep.failures.append(f"left-out file present: {rel}")
                    break
            m = _file_module(rel)
            if m:
                file_modules.add(m)
            try:
                size = p.stat().st_size
                rep.bytes += size
                with open(p, "rb") as fh:
                    found = find_in_stream(fh, PATTERNS + ADVISORY_PATTERNS, chunk=chunk)
            except OSError as exc:
                rep.failures.append(f"cannot read {rel}: {type(exc).__name__}")
                continue
            for (word, enc), h in found.items():
                hit = Hit("file", rel, word, enc, h["count"], _cut(h["contexts"][0]))
                (rep.advisory if word in ADVISORY else rep.hits).append(hit)
            if progress and size > 100 * 2 ** 20:
                progress(f"scanned {rel} ({size // 2 ** 20} MB)")
            if low == "base_library.zip":
                _scan_base_library(p, rep)
            elif low.endswith(".exe"):
                _scan_exe(p, rel, rep, source)
            elif low.endswith(".pyz"):
                _scan_pyz(p, None, rel, rep, source)
    rep.modules |= file_modules | top_dirs
    _check_modules(rep, stage_list)
    for h in rep.hits:
        for a in allow:
            if a.matches(h):
                h.allowed_by = a.number
                a.used += 1
                break
    rep.allow_unused = [a.number for a in allow if not a.used]
    return rep


def _scan_base_library(p: Path, rep: Report) -> None:
    try:
        with zipfile.ZipFile(p) as z:
            for info in z.infolist():
                data = z.read(info)
                mod = module_of_member(info.filename)
                if info.filename.endswith(".pyc"):
                    rep.modules.add(mod)
                    code = _unmarshal(data[16:])
                    rep.hits.extend(_hits_from_code("base_library", mod, code, data))
                else:
                    for (word, enc), h in find_in_bytes(data, PATTERNS).items():
                        rep.hits.append(Hit("base_library", info.filename, word, enc, h["count"],
                                            _cut(h["contexts"][0])))
    except (zipfile.BadZipFile, OSError) as exc:
        rep.failures.append(f"cannot read base_library.zip: {type(exc).__name__}")


def _scan_exe(p: Path, rel: str, rep: Report, source: ArchiveSource) -> None:
    try:
        entries = source.pkg_entries(p)
    except ImportError:
        rep.failures.append(f"{rel}: PyInstaller's archive reader is not importable (run in the build venv)")
        return
    except Exception as exc:
        rep.failures.append(f"{rel}: cannot read its archive: {type(exc).__name__}")
        return
    if entries is None:
        rep.notes.append(f"{rel}: no PyInstaller archive")
        return
    for name, typecode, data in entries:
        if typecode == "z":
            _scan_pyz(p, name, f"{rel}:{name}", rep, source)
            continue
        if typecode in ("s", "m", "M"):
            rep.hits.extend(_hits_from_code("pkg", name, _unmarshal(data), data))
        else:
            for (word, enc), h in find_in_bytes(data, PATTERNS).items():
                rep.hits.append(Hit("pkg", name, word, enc, h["count"], _cut(h["contexts"][0])))


def _scan_pyz(p: Path, pkg_name: Optional[str], label: str, rep: Report, source: ArchiveSource) -> None:
    try:
        mods = source.pyz_modules(p, pkg_name)
    except ImportError:
        rep.failures.append(f"{label}: PyInstaller's PYZ reader is not importable (run in the build venv)")
        return
    except Exception as exc:
        rep.failures.append(f"{label}: cannot read the PYZ: {type(exc).__name__}")
        return
    rep.notes.append(f"{label}: {len(mods)} PYZ entries")
    for name, typecode, raw in mods:
        rep.modules.add(name)
        code = _unmarshal(raw) if typecode in (0, 1) else None
        rep.hits.extend(_hits_from_code("pyz", name, code, raw))


def _present(mod: str, modules: Set[str]) -> bool:
    m = mod.lower()
    return any(x.lower() == m or x.lower().startswith(m + ".") for x in modules)


def _check_modules(rep: Report, stage_list: Optional[Path]) -> None:
    for mod in EXCLUDED_MODULES:
        if _present(mod, rep.modules):
            rep.failures.append(f"excluded module present: {mod}")
    ours = {m for m in rep.modules if m == "src" or m.startswith("src.")}
    if stage_list is None:
        rep.failures.append("STAGE_LIST.txt not given: the src.* module set cannot be checked")
        return
    if not Path(stage_list).is_file():
        rep.failures.append("STAGE_LIST.txt not found: the src.* module set cannot be checked")
        return
    staged = stage_modules(Path(stage_list))
    for m in sorted(staged - ours):
        rep.failures.append(f"staged but not in the bundle: {m}")
    for m in sorted(ours - staged):
        rep.failures.append(f"in the bundle but not staged: {m}")


def default_stage_list(app_dir: Path) -> Optional[Path]:
    """builds\\<id>\\dist\\Hangeul Document Checker -> builds\\<id>\\reports\\STAGE_LIST.txt"""
    p = Path(app_dir).resolve().parent.parent / "reports" / "STAGE_LIST.txt"
    return p if p.is_file() else None


def print_report(rep: Report, out: Callable[[str], None] = print) -> None:
    ours_bad = [h for h in rep.unallowed if is_ours(h.key)]
    out(f"8 b scan: {rep.files} files, {rep.bytes / 2 ** 30:.2f} GB | forbidden-string hits {len(rep.hits)}, "
        f"not allowed {len(rep.unallowed)} (ours {len(ours_bad)}) | advisory {len(rep.advisory)} | "
        f"src modules {len([m for m in rep.modules if m == 'src' or m.startswith('src.')])}")
    for h in rep.unallowed:
        out(f"  HIT {h.where} {h.key}: '{h.word}' ({h.encoding}) x{h.count}"
            + (f" in {mask_paths(h.co_filename)}" if h.co_filename else "") + f" :: {mask_paths(h.context)}")
    for h in rep.hits:
        if h.allowed_by is not None:
            out(f"  allowed (line {h.allowed_by}) {h.where} {h.key}: '{h.word}' x{h.count}")
    for f in rep.failures:
        out(f"  FAIL {mask_paths(f)}")
    for h in rep.advisory:
        out(f"  advisory {h.where} {h.key}: '{h.word}' ({h.encoding}) x{h.count}")
    for n in rep.allow_unused:
        out(f"  advisory: scan_allow.txt line {n} matched nothing")
    for n in rep.notes:
        out(f"  note {mask_paths(n)}")
    out(f"8 b {'PASS' if rep.passed else 'FAIL'}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="8 b static scan of the frozen app folder")
    ap.add_argument("app_dir")
    ap.add_argument("--allow", default=str(PKG / "scan_allow.txt"))
    ap.add_argument("--stage-list", default="")
    ap.add_argument("--json", default="")
    a = ap.parse_args(argv)
    app = Path(a.app_dir)
    stage = Path(a.stage_list) if a.stage_list else default_stage_list(app)
    try:
        rep = scan(app, Path(a.allow) if a.allow else None, stage, progress=lambda s: None)
    except Exception as exc:
        print(f"8 b: could not run: {type(exc).__name__}: {exc}")
        return 2
    print_report(rep)
    if a.json:
        Path(a.json).parent.mkdir(parents=True, exist_ok=True)
        Path(a.json).write_text(json.dumps(rep.to_json(), indent=1, ensure_ascii=True), encoding="utf-8")
    return 0 if rep.passed else 1


if __name__ == "__main__":
    sys.exit(main())
