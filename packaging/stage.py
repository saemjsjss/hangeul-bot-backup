"""Stage and scrub the app's sources (APP_SPEC r1 6.1, amended by r2 R2.4.4). Build side only; never bundled.

PyInstaller freezes the staged copy, never the worktree. `run()` makes `builds\\<id>\\stage`:

1. The copy list is GENERATED: every `src\\verify\\runner\\*.py` except `devplaces.py` (so `closer.py` and `gpuread.py`
   are in), every `src\\verify\\runner\\app\\*.py`, the bot files of r1 section 5, the glue (`glue_numpy.py`,
   `easyocr_vendor.py`, `engine\\glue_data.json`) and the vendored pyzbar / openpyxl / et_xmlfile (tree hashes checked).
   Never `tests\\`, `selftest\\` or `app\\tests\\`. The full inventory goes to `reports\\STAGE_LIST.txt`.
2. The EasyOCR weights `craft_mlt_25k.pth` and `english_g2.pth` go to `engine\\easyocr\\model\\`; each MD5 must equal
   easyocr's own `md5sum` in `easyocr\\config.py` (read as text, never imported); sizes, MD5 and sha256 go to
   BUILD.json. `engine\\easyocr\\user_network\\.keep` (empty) is added. No ONNX model is copied.
3. The constant swaps (the two of `prototype_page_types.py` and the seven DEV path literals of `gpuread.py`), each old
   literal exactly once in the source, then the docstring scrub (every docstring of our code becomes an empty
   triple-quoted string with the same number of lines), then the AST check: the scrubbed file must equal its
   original with only those docstrings and those constants changed, line numbers kept.
4. The VC++ runtime DLLs from System32 (`vcrt\\`, file versions checked), `icon.ico`, `hdc_entry.py`, the self-test
   files (Task D's `make_selftest_pages.py`), `version_info.txt` and `BUILD.json` (versions read from the build
   env's `*.dist-info`, `torch\\version.py`, the version resource of `cudnn64_9.dll` and
   `torch._C._cuda_getArchFlags()` in a child run with -I -B; the child never touches `torch.cuda`).
5. The fast scan: the forbidden strings of r1 8 b have zero hits in the stage (code constants and names of every
   staged .py, compiled; the raw bytes of every other file), except the lines of `packaging\\scan_allow.txt`.

Also here: `run_low()`, the BELOW_NORMAL subprocess runner used by build.py and make_build_env.py.
"""
from __future__ import annotations

import ast
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
import tokenize
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple

PKG = Path(__file__).resolve().parent
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import make_icon  # noqa: E402
import manifest  # noqa: E402
import version as build_version  # noqa: E402

WORKTREE = PKG.parent
LAB = Path(r"C:\Hangeul\DOCCHECK\lab")
GLUE_DIR = LAB / "onnx" / "pkg"
VENDOR_DIR = LAB / "private-runner" / "engine" / "vendor"
SCAN_ALLOW = PKG / "scan_allow.txt"
SELFTEST_MAKER = PKG / "make_selftest_pages.py"

BOT_COMMIT = "07b1f22"
BOT_MODULES = ("src/verify/doc_verifier.py", "src/verify/page_checks.py", "src/verify/common.py",
               "src/verify/rules.py", "src/verify/text_cache.py", "src/verify/field_check.py",
               "src/scraper/ocr_validator.py")          # byte-identical to 07b1f22 (checked with git, read-only)
BOT_FILES = ("src/__init__.py", "src/verify/__init__.py", "src/verify/doc_verifier.py", "src/verify/page_checks.py",
             "src/verify/common.py", "src/verify/rules.py", "src/verify/text_cache.py", "src/verify/field_check.py",
             "src/verify/prototype_page_types.py", "src/scraper/__init__.py", "src/scraper/ocr_validator.py",
             "src/sheets/__init__.py", "src/sheets/progress_builder.py")
RUNNER_DIR = "src/verify/runner"
APP_DIR = "src/verify/runner/app"
RUNNER_NEVER = ("devplaces.py",)
GLUE_PY = ("glue_numpy.py", "easyocr_vendor.py")
GLUE_DATA = "glue_data.json"
VENDOR_PACKAGES = ("pyzbar", "openpyxl", "et_xmlfile")
# pyzbar has no entry in VENDOR.json (it was there before RUNNER_SPEC step 0); pinned from the vendored tree, 29 Oct
VENDOR_PINS: Dict[str, Dict[str, Any]] = {
    "pyzbar": {"files": 9, "tree_sha256": "aef8fb0a57121ca30518739aa70e261a14276e4427cbd32f51c1bec1fbe30168"},
}
WEIGHTS = {"craft_mlt_25k.pth": ("detection_models", "craft"),
           "english_g2.pth": ("recognition_models", "gen2", "english_g2")}
VCRT_MIN = {"vcruntime140.dll": (14, 40), "vcruntime140_1.dll": (14, 40), "msvcp140.dll": (14, 40),
            "msvcp140_1.dll": (14, 40), "msvcr120.dll": (12, 0)}
SELFTEST_FILES = ("selftest_pages.pdf", "selftest_expected.json", "selftest_page1.jpg")

EXPECTED_ENGINE = {"torch": "2.11.0+cu128", "torchvision": "0.26.0+cu128", "easyocr": "1.7.2"}
PACKAGE_DISTS = {"numpy": "numpy", "opencv": "opencv-python-headless", "pymupdf": "pymupdf", "pillow": "pillow",
                 "scipy": "scipy", "scikit-image": "scikit-image", "imageio": "imageio",
                 "pyinstaller": "pyinstaller", "pyinstaller-hooks-contrib": "pyinstaller-hooks-contrib"}
CUDA = "12.8"
CARD_FACTS = {"cuda_driver_min": 12080, "cap_gb": 3.0, "need_free_mb": 4096, "min_total_mb": 5800}
BASELINE = "r8 (runner 0.2.7, 344b342)"
SPEC = "RUNNER_SPEC r2.7 / APP_SPEC r2"
BUILD_SCHEMA = "hdc-build/2"

# r1 8 b: case-insensitive, UTF-8 and UTF-16LE. Zero hits outside scan_allow.txt.
FORBIDDEN = ("c:\\hangeul", "c:/hangeul", "c:\\\\hangeul", "jarvis", "private-runner", ".venv")


class StageError(Exception):
    """The stage stops; the message names the file and the rule, never page text or student data."""


# --------------------------------------------------------------------------------------------------------
# The constant swaps (r1 6.1 step 4; r2 R2.4.4 step 3)

@dataclass(frozen=True)
class Swap:
    path: str          # stage-relative posix path of the module
    name: str          # a short label for messages
    old: str           # source text; must occur exactly once
    new: str

    @property
    def old_value(self) -> str:
        return literal_of(self.old)

    @property
    def new_value(self) -> str:
        return literal_of(self.new)


PPT = "src/verify/prototype_page_types.py"
GPUREAD = "src/verify/runner/gpuread.py"
SWAPS: Tuple[Swap, ...] = (
    Swap(PPT, "DOCS_ROOT", r'DOCS_ROOT = Path(r"C:\Hangeul\VERIFIED STUDENT DOCUMENTS")',
         'DOCS_ROOT = Path("no-docs-root")'),
    Swap(PPT, "TEXT_DIR", r'TEXT_DIR = Path(r"C:\Hangeul\BOT\data\verification\text")',
         'TEXT_DIR = Path("no-text-dir")'),
    Swap(GPUREAD, "WORKTREE", r'WORKTREE = Path(r"C:\Hangeul\JARVIS\prototype\page-types")',
         'WORKTREE = Path("no-worktree")'),
    Swap(GPUREAD, "PRIVATE", r'PRIVATE = Path(r"C:\Hangeul\DOCCHECK\lab\private-runner")',
         'PRIVATE = Path("no-dev-root")'),
    Swap(GPUREAD, "GUARD", r'GUARD = Path(r"C:\Hangeul\DOCCHECK\lab\onnx\scripts\gpu_guard.py")',
         'GUARD = Path("no-dev-guard.py")'),
    Swap(GPUREAD, "check_out message",
         r'raise ValueError("the page-texts file must be under C:\\Hangeul\\DOCCHECK\\lab\\private-runner")',
         'raise ValueError("the page-texts file must be under the dev root")'),
    Swap(GPUREAD, "write_json message", 'raise PermissionError("writes go only under private-runner")',
         'raise PermissionError("writes go only under the dev root")'),
    Swap(GPUREAD, "page_texts help", 'help="the page-texts JSON to write (under private-runner)"',
         'help="the page-texts JSON to write (under the dev root)"'),
    Swap(GPUREAD, "--out help", 'help="--from-legacy: the page-texts JSON to write (under private-runner)"',
         'help="--from-legacy: the page-texts JSON to write (under the dev root)"'),
)


def literal_of(fragment: str) -> str:
    """The value of the ONE string literal in a source fragment (tokenized, then literal_eval)."""
    toks = [t.string for t in tokenize.generate_tokens(io.StringIO(fragment).readline) if t.type == tokenize.STRING]
    if len(toks) != 1:
        raise StageError(f"a swap fragment must hold exactly one string literal (found {len(toks)})")
    value = ast.literal_eval(toks[0])
    if not isinstance(value, str):
        raise StageError("a swap literal must be a text")
    return value


def swaps_for(rel: str, swaps: Sequence[Swap] = SWAPS) -> List[Swap]:
    return [s for s in swaps if s.path == rel]


def apply_swaps(rel: str, text: str, swaps: Sequence[Swap]) -> str:
    for s in swaps:
        n = text.count(s.old)
        if n != 1:
            raise StageError(f"{rel}: the literal to swap ({s.name}) occurs {n} times; it must occur exactly once")
        text = text.replace(s.old, s.new)
    return text


# --------------------------------------------------------------------------------------------------------
# The docstring scrub and the AST check (r1 6.1 steps 3 and 5)

_DOC_OWNERS = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def docstring_nodes(tree: ast.AST) -> List[ast.Constant]:
    out = []
    for node in ast.walk(tree):
        if isinstance(node, _DOC_OWNERS) and node.body:
            first = node.body[0]
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                out.append(first.value)
    return out


def split_lines(text: str) -> List[str]:
    """Lines with their endings ("\\r\\n" stays with its line). A lone "\\r" (an old Mac line end) is refused,
    because Python counts it as a line and this splitter would not."""
    if re.search(r"\r(?!\n)", text):
        raise StageError("a lone carriage return in a source file")
    return re.findall(r"[^\n]*\n|[^\n]+$", text)


def blank_docstrings(text: str) -> Tuple[str, int]:
    """Every docstring becomes \"\"\"<the same number of newlines>\"\"\"; everything else is kept byte for byte."""
    tree = ast.parse(text)
    nodes = docstring_nodes(tree)
    lines = split_lines(text)
    for node in sorted(nodes, key=lambda n: (n.lineno, n.col_offset), reverse=True):
        a, b = node.lineno - 1, node.end_lineno - 1
        prefix = lines[a].encode("utf-8")[:node.col_offset].decode("utf-8")
        suffix = lines[b].encode("utf-8")[node.end_col_offset:].decode("utf-8")
        nl = "\r\n" if lines[a].endswith("\r\n") else "\n"
        blank = '"""' + nl * (b - a) + '"""'
        new = split_lines(prefix + blank + suffix)
        if len(new) != b - a + 1:
            raise StageError("the docstring scrub changed a line count")
        lines[a:b + 1] = new
    return "".join(lines), len(nodes)


def expected_tree(original: str, swaps: Sequence[Swap]) -> ast.Module:
    """The original's AST with the listed constants swapped and every docstring blanked."""
    tree = ast.parse(original)
    docs = {id(n) for n in docstring_nodes(tree)}
    for s in swaps:
        old, new = s.old_value, s.new_value
        hits = [n for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
                and n.value == old and id(n) not in docs]
        if len(hits) != 1:
            raise StageError(f"AST check: the constant {s.name} occurs {len(hits)} times; it must occur exactly once")
        hits[0].value, hits[0].kind = new, None
    for n in docstring_nodes(tree):
        n.value, n.kind = "\n" * (n.end_lineno - n.lineno), None
    return tree


def ast_check(rel: str, original: str, staged: str, swaps: Sequence[Swap]) -> None:
    """r1 6.1 step 5: any difference beyond the docstrings and the listed constants stops the build. The line of
    every node must stay the same too (tracebacks keep their line numbers)."""
    want = expected_tree(original, swaps)
    try:
        got = ast.parse(staged)
    except SyntaxError as e:
        raise StageError(f"AST check: {rel} does not parse after the scrub (line {e.lineno})") from None
    if ast.dump(want) != ast.dump(got):
        raise StageError(f"AST check: {rel} differs from its original beyond the docstrings and the listed constants")
    if len(split_lines(original)) != len(split_lines(staged)):
        raise StageError(f"AST check: {rel} changed its number of lines")
    for a, b in zip(ast.walk(want), ast.walk(got)):
        if (getattr(a, "lineno", None), getattr(a, "end_lineno", None)) != \
                (getattr(b, "lineno", None), getattr(b, "end_lineno", None)):
            raise StageError(f"AST check: {rel} moved a statement to another line")


def scrub_file(rel: str, original: str, swaps: Sequence[Swap] = SWAPS) -> Tuple[str, int]:
    """The swaps of this file, the docstring scrub, then the AST check. Returns (staged text, docstrings blanked)."""
    mine = swaps_for(rel, swaps)
    swapped = apply_swaps(rel, original, mine)
    staged, n = blank_docstrings(swapped)
    ast_check(rel, original, staged, mine)
    return staged, n


# --------------------------------------------------------------------------------------------------------
# The fast scan (r1 6.1 step 6, with r2's scan_allow.txt)

@dataclass(frozen=True)
class Allow:
    where: str        # a module name ("src.verify.runner.app.cardgate") or a stage/dist-relative file path
    string: str       # lower case
    reason: str


def load_allow(path: Path) -> List[Allow]:
    """`module or file | string | reason` lines; blank lines and lines starting with # are skipped."""
    out: List[Allow] = []
    for i, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        parts = [p.strip() for p in s.split("|")]
        if len(parts) != 3 or not all(parts):
            raise StageError(f"scan_allow.txt line {i}: want 'module or file | string | reason'")
        out.append(Allow(parts[0].replace("\\", "/").lower(), parts[1].lower(), parts[2]))
    return out


def _allowed_for(where: str, allow: Sequence[Allow]) -> List[str]:
    w = where.replace("\\", "/").lower()
    return [a.string for a in allow if a.where == w]


def text_hits(s: str, allowed: Sequence[str] = ()) -> List[str]:
    low = s.lower()
    for a in allowed:
        low = low.replace(a, "")
    return [p for p in FORBIDDEN if p in low]


def bytes_hits(data: bytes, allowed: Sequence[str] = ()) -> List[str]:
    low = data.lower()
    for a in allowed:
        low = low.replace(a.encode("utf-8"), b"").replace(a.encode("utf-16-le"), b"")
    return [p for p in FORBIDDEN if p.encode("utf-8") in low or p.encode("utf-16-le") in low]


def code_strings(code: types.CodeType) -> Iterator[str]:
    """Every text a compiled module keeps: constants (nested code too), names, co_filename."""
    stack = [code]
    while stack:
        co = stack.pop()
        yield co.co_filename
        yield co.co_name
        yield getattr(co, "co_qualname", "")
        yield from co.co_names
        yield from co.co_varnames
        yield from co.co_freevars
        yield from co.co_cellvars
        consts = list(co.co_consts)
        while consts:
            c = consts.pop()
            if isinstance(c, types.CodeType):
                stack.append(c)
            elif isinstance(c, str):
                yield c
            elif isinstance(c, bytes):
                yield c.decode("latin-1")
            elif isinstance(c, (tuple, frozenset)):
                consts.extend(c)


def module_name(rel: str) -> Optional[str]:
    """The dotted module name of a staged .py ("vendor/openpyxl/x.py" -> "openpyxl.x"); None for other files."""
    if not rel.endswith(".py"):
        return None
    parts = rel[:-3].split("/")
    if parts[0] == "vendor":
        parts = parts[1:]
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) if parts else None


def scan_tree(root: Path, allow: Sequence[Allow]) -> List[Tuple[str, str]]:
    """(where, forbidden string) hits: .py files compiled (comments never reach the bundle), others raw."""
    hits: List[Tuple[str, str]] = []
    for rel, p in manifest.iter_files(root):
        mod = module_name(rel)
        if mod is not None:
            allowed = _allowed_for(mod, allow)
            try:
                code = compile(p.read_bytes(), rel, "exec", dont_inherit=True, optimize=0)
            except (SyntaxError, ValueError) as e:
                raise StageError(f"fast scan: {rel} does not compile ({type(e).__name__})") from None
            found = set()
            for s in code_strings(code):
                found.update(text_hits(s, allowed))
            hits += [(mod, h) for h in sorted(found)]
        else:
            data = p.read_bytes()
            hits += [(rel, h) for h in bytes_hits(data, _allowed_for(rel, allow))]
    return hits


# --------------------------------------------------------------------------------------------------------
# Hashes, vendor trees, versions

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def tree_sha256(root: Path) -> Tuple[int, str]:
    """VENDOR.json's tree hash: sha256 over sorted 'relpath\\0filesha256\\n' lines; __pycache__ excluded."""
    lines = []
    for dp, dns, fns in os.walk(root):
        dns[:] = sorted(d for d in dns if d != "__pycache__")
        for fn in fns:
            p = Path(dp) / fn
            lines.append(p.relative_to(root).as_posix() + "\0" + manifest.sha256_file(p) + "\n")
    lines.sort()
    return len(lines), hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()


def hashes_of(path: Path) -> Dict[str, Any]:
    md5, sha = hashlib.md5(), hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(1 << 20)
            if not block:
                break
            md5.update(block)
            sha.update(block)
    return {"size": Path(path).stat().st_size, "md5": md5.hexdigest(), "sha256": sha.hexdigest()}


def _norm_dist(name: str) -> str:
    return re.sub(r"[-_.]+", "_", name).lower()


def dist_version(name: str, site_dirs: Sequence[Path]) -> str:
    """The version of an installed distribution from its *.dist-info (the first site dir that has it wins, as on
    sys.path); the METADATA "Version:" line must agree with the folder name."""
    want = _norm_dist(name)
    for d in site_dirs:
        for info in sorted(Path(d).glob("*.dist-info")):
            stem = info.name[: -len(".dist-info")]
            dname, _, ver = stem.partition("-")
            if _norm_dist(dname) != want:
                continue
            meta = info / "METADATA"
            mver = None
            if meta.is_file():
                for line in meta.read_text(encoding="utf-8", errors="replace").splitlines():
                    if line.startswith("Version:"):
                        mver = line.split(":", 1)[1].strip()
                        break
                    if not line.strip():
                        break
            if mver is not None and mver != ver:
                raise StageError(f"{name}: dist-info folder and METADATA disagree on the version")
            return mver or ver
    raise StageError(f"{name} is not installed in the build env")


def find_in_sites(rel: str, site_dirs: Sequence[Path]) -> Path:
    for d in site_dirs:
        p = Path(d) / rel
        if p.exists():
            return p
    raise StageError(f"{rel} is not in the build env")


def _assigned_literals(path: Path, names: Sequence[str]) -> Dict[str, Any]:
    """Top-level `name = <literal>` (or annotated) values of a module, read as text, never imported."""
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    out: Dict[str, Any] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            target, value = node.targets[0].id, node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            target, value = node.target.id, node.value
        else:
            continue
        if target in names:
            try:
                out[target] = ast.literal_eval(value)
            except ValueError:
                raise StageError(f"{Path(path).name}: {target} is not a literal") from None
    missing = [n for n in names if n not in out]
    if missing:
        raise StageError(f"{Path(path).name}: {', '.join(missing)} not found")
    return out


def _ast_dict_get(node: ast.AST, key: str) -> ast.AST:
    if isinstance(node, ast.Dict):
        for k, v in zip(node.keys, node.values):
            if isinstance(k, ast.Constant) and k.value == key:
                return v
    raise StageError(f"easyocr config.py: no entry {key!r}")


def easyocr_md5s(config_py: Path) -> Dict[str, str]:
    """{weight file: md5} from easyocr's config.py: detection_models["craft"] and
    recognition_models["gen2"]["english_g2"]. Read as text: the syntax tree is walked down to the "filename" and
    "md5sum" leaves, which must be literals (other entries hold computed character lists and are never evaluated)."""
    tree = ast.parse(Path(config_py).read_text(encoding="utf-8"))
    tops: Dict[str, ast.AST] = {}
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id in ("detection_models", "recognition_models")):
            tops[node.targets[0].id] = node.value
    out = {}
    for fname, path in WEIGHTS.items():
        if path[0] not in tops:
            raise StageError(f"easyocr config.py: {path[0]} not found")
        entry = tops[path[0]]
        for key in path[1:]:
            entry = _ast_dict_get(entry, key)
        try:
            filename = ast.literal_eval(_ast_dict_get(entry, "filename"))
            md5 = ast.literal_eval(_ast_dict_get(entry, "md5sum"))
        except ValueError:
            raise StageError(f"easyocr config.py: the entry of {fname} is not literal") from None
        if filename != fname or not re.fullmatch(r"[0-9a-f]{32}", str(md5)):
            raise StageError(f"easyocr config.py: no md5sum for {fname}")
        out[fname] = md5
    return out


def model_versions(model_py: Path) -> Dict[str, str]:
    v = _assigned_literals(model_py, ("RUNNER_VERSION", "BASE_RULES_VERSION", "RUNNER_RULES_VERSION"))
    return {"runner_version": v["RUNNER_VERSION"], "base_rules_version": v["BASE_RULES_VERSION"],
            "runner_rules_version": v["RUNNER_RULES_VERSION"]}


def read_file_version(path: Path) -> str:
    """"a.b.c.d" from the VS_FIXEDFILEINFO of a PE file (version.dll; the file is mapped as data, never run)."""
    import ctypes
    from ctypes import wintypes
    ver = ctypes.WinDLL(os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "version.dll"),
                        use_last_error=True)
    ver.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    ver.GetFileVersionInfoSizeW.restype = wintypes.DWORD
    ver.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    ver.GetFileVersionInfoW.restype = wintypes.BOOL
    ver.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p),
                                   ctypes.POINTER(wintypes.UINT)]
    ver.VerQueryValueW.restype = wintypes.BOOL
    p = str(path)
    n = ver.GetFileVersionInfoSizeW(p, None)
    if not n:
        raise StageError(f"{Path(path).name}: no version resource")
    buf = ctypes.create_string_buffer(n)
    if not ver.GetFileVersionInfoW(p, 0, n, buf):
        raise StageError(f"{Path(path).name}: the version resource cannot be read")
    ptr, ln = ctypes.c_void_p(), wintypes.UINT()
    if not ver.VerQueryValueW(buf, "\\", ctypes.byref(ptr), ctypes.byref(ln)) or ln.value < 52:
        raise StageError(f"{Path(path).name}: no fixed file version")
    fixed = (wintypes.DWORD * 13).from_address(ptr.value)
    if fixed[0] != 0xFEEF04BD:
        raise StageError(f"{Path(path).name}: bad version signature")
    ms, ls = fixed[2], fixed[3]
    return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"


def version_tuple(v: str) -> Tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v))


# --------------------------------------------------------------------------------------------------------
# Child processes: BELOW_NORMAL, no window, the whole process tree ended when we are done

BELOW_NORMAL = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0x00004000)
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
_CARD_ENV_PREFIXES = ("PYTORCH_", "TORCH_", "CUBLAS_", "CUDNN_", "CUDA_")
# The app's test-only switches (R2.6 P0, r1 8 a): never passed on to a build step or a frozen-app run by accident
TEST_ONLY_VARS = ("HDC_TEST_NO_CARD", "HDC_MODE", "HDC_BUNDLE_DIR", "HDC_CODE_ROOT", "HDC_DATA_DIR")


def child_env(base: Optional[Mapping[str, str]] = None, *, no_card: bool = True,
              extra: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
    """The parent's environment without PYTHON*, CUDA_*, PYTORCH_*, TORCH_*, CUBLAS_*, CUDNN_*,
    NVIDIA_TF32_OVERRIDE and the app's test-only HDC_* switches; PYTHONDONTWRITEBYTECODE=1; with no_card,
    CUDA_VISIBLE_DEVICES=-1 (no card is visible, so nothing in the child can start CUDA on it). `extra` comes last."""
    env = dict(os.environ if base is None else base)
    for k in list(env):
        ku = k.upper()
        if (ku.startswith(_CARD_ENV_PREFIXES) or ku.startswith("PYTHON") or ku == "NVIDIA_TF32_OVERRIDE"
                or ku in TEST_ONLY_VARS):
            del env[k]
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if no_card:
        env["CUDA_VISIBLE_DEVICES"] = "-1"
    if extra:
        env.update(extra)
    return env


class _KillJob:
    """A Windows job object: every process in it runs BELOW_NORMAL and dies when the job is closed
    (JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE), so no grandchild is left behind. Without it, taskkill /T is the fallback."""

    def __init__(self) -> None:
        self.handle = None
        if os.name != "nt":
            return
        try:
            import ctypes
            from ctypes import wintypes

            class Basic(ctypes.Structure):
                _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                            ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                            ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                            ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                            ("SchedulingClass", wintypes.DWORD)]

            class IoCounters(ctypes.Structure):
                _fields_ = [(n, ctypes.c_uint64) for n in ("ReadOperationCount", "WriteOperationCount",
                                                            "OtherOperationCount", "ReadTransferCount",
                                                            "WriteTransferCount", "OtherTransferCount")]

            class Extended(ctypes.Structure):
                _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", IoCounters),
                            ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                            ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
            k32.CreateJobObjectW.restype = ctypes.c_void_p
            k32.SetInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
            k32.SetInformationJobObject.restype = wintypes.BOOL
            k32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            k32.AssignProcessToJobObject.restype = wintypes.BOOL
            k32.TerminateJobObject.argtypes = [ctypes.c_void_p, wintypes.UINT]
            k32.TerminateJobObject.restype = wintypes.BOOL
            k32.CloseHandle.argtypes = [ctypes.c_void_p]
            k32.CloseHandle.restype = wintypes.BOOL
            self._k32 = k32
            h = k32.CreateJobObjectW(None, None)
            if not h:
                return
            info = Extended()
            info.BasicLimitInformation.LimitFlags = 0x2000 | 0x20      # KILL_ON_JOB_CLOSE | PRIORITY_CLASS
            info.BasicLimitInformation.PriorityClass = BELOW_NORMAL
            if not k32.SetInformationJobObject(h, 9, ctypes.byref(info), ctypes.sizeof(info)):
                k32.CloseHandle(h)
                return
            self.handle = h
        except (OSError, AttributeError):
            self.handle = None

    def add(self, proc: subprocess.Popen) -> bool:
        if self.handle is None:
            return False
        return bool(self._k32.AssignProcessToJobObject(self.handle, int(proc._handle)))  # type: ignore[attr-defined]

    def kill(self, proc: subprocess.Popen) -> None:
        if self.handle is not None and self._k32.TerminateJobObject(self.handle, 1):
            return
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, timeout=60)
        else:
            proc.kill()

    def close(self) -> None:
        if self.handle is not None:
            self._k32.CloseHandle(self.handle)
            self.handle = None


@dataclass
class Ran:
    cmd: List[str]
    returncode: Optional[int]
    seconds: float
    timed_out: bool = False
    stdout: str = ""


def run_low(cmd: Sequence[str], *, env: Optional[Mapping[str, str]] = None, cwd: Optional[Path] = None,
            timeout: Optional[float] = None, log_path: Optional[Path] = None, capture: bool = False) -> Ran:
    """Runs one child at BELOW_NORMAL priority without a window, inside a kill-on-close job: on a timeout, and in
    any case when it returns, the child's whole process tree is ended (only processes this call started)."""
    cmd = [str(c) for c in cmd]
    flags = (BELOW_NORMAL | CREATE_NO_WINDOW) if os.name == "nt" else 0
    log = open(log_path, "a", encoding="utf-8", errors="replace") if log_path else None
    try:
        if capture:
            stdout, stderr = subprocess.PIPE, (log if log else subprocess.PIPE)
        else:
            stdout, stderr = (log, subprocess.STDOUT) if log else (subprocess.DEVNULL, subprocess.DEVNULL)
        job = _KillJob()
        t0 = time.monotonic()
        proc = subprocess.Popen(cmd, cwd=str(cwd) if cwd else None, env=dict(env) if env is not None else None,
                                stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, creationflags=flags,
                                text=True, encoding="utf-8", errors="replace")
        try:
            job.add(proc)
            try:
                out, _ = proc.communicate(timeout=timeout)
                timed_out = False
            except subprocess.TimeoutExpired:
                timed_out = True
                job.kill(proc)
                out, _ = proc.communicate()
            except BaseException:
                job.kill(proc)
                proc.communicate()
                raise
        finally:
            job.close()
        return Ran(cmd, None if timed_out else proc.returncode, time.monotonic() - t0, timed_out, out or "")
    finally:
        if log:
            log.close()


# --------------------------------------------------------------------------------------------------------
# The default readers (faked in the unit tests)

PROBE_CHILD = """\
import json, platform
import numpy
import torch
try:
    base = list(numpy.__cpu_baseline__)
except AttributeError:
    from numpy._core._multiarray_umath import __cpu_baseline__ as base
print(json.dumps({"python": platform.python_version(), "torch": torch.__version__, "numpy": numpy.__version__,
                  "arch_flags": torch._C._cuda_getArchFlags(), "numpy_cpu_baseline": list(base)}))
"""


def probe_build_python(python_exe: Path) -> Dict[str, Any]:
    """The build env's arch flags, numpy baseline and Python version, from a child run with -I -B and no card
    visible. Only `torch._C._cuda_getArchFlags()` is read (compiled-in text); `torch.cuda` is never touched."""
    r = run_low([str(python_exe), "-I", "-B", "-c", PROBE_CHILD], env=child_env(), timeout=600, capture=True)
    if r.timed_out or r.returncode != 0:
        raise StageError(f"the build env probe failed (exit {r.returncode}, timed out {r.timed_out})")
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise StageError("the build env probe printed no JSON") from None


def run_selftest_maker(python_exe: Path, maker: Path, out_dir: Path) -> None:
    """Task D's make_selftest_pages.py, run as `python -I -B make_selftest_pages.py --out <stage\\selftest>`."""
    if not Path(maker).is_file():
        raise StageError("packaging\\make_selftest_pages.py (Task D) is missing")
    r = run_low([str(python_exe), "-I", "-B", str(maker), "--out", str(out_dir)], env=child_env(), timeout=900,
                capture=True)
    if r.timed_out or r.returncode != 0:
        raise StageError(f"make_selftest_pages.py failed (exit {r.returncode}, timed out {r.timed_out})")


def git_bot_check(worktree: Path, run: Callable[..., Any] = subprocess.run) -> None:
    """S6: the 7 bot modules in the worktree are byte-identical to 07b1f22 (`git diff --quiet`, read-only)."""
    r = run(["git", "-C", str(worktree), "diff", "--quiet", BOT_COMMIT, "--", *BOT_MODULES], capture_output=True,
            timeout=120)
    if r.returncode == 1:
        raise StageError(f"the bot modules differ from {BOT_COMMIT}")
    if r.returncode != 0:
        raise StageError("git diff of the bot modules failed")


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# --------------------------------------------------------------------------------------------------------
# The stage

@dataclass
class Sources:
    worktree: Path = WORKTREE
    glue_dir: Path = GLUE_DIR
    vendor_dir: Path = VENDOR_DIR
    weights_dir: Path = field(default_factory=lambda: Path(os.environ.get("USERPROFILE", "~")) / ".EasyOCR" / "model")
    site_dirs: Tuple[Path, ...] = ()
    system32: Path = field(default_factory=lambda: Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32")
    python_exe: Path = field(default_factory=lambda: Path(sys.executable))
    selftest_maker: Path = SELFTEST_MAKER
    allow_file: Path = SCAN_ALLOW
    vendor_pins: Mapping[str, Mapping[str, Any]] = field(default_factory=lambda: dict(VENDOR_PINS))
    swaps: Tuple[Swap, ...] = SWAPS


@dataclass
class Tools:
    file_version: Callable[[Path], str] = read_file_version
    probe: Callable[[Path], Dict[str, Any]] = probe_build_python
    make_selftest: Callable[[Path, Path, Path], None] = run_selftest_maker
    git_run: Callable[..., Any] = subprocess.run
    now: Callable[[], str] = utc_now
    log: Callable[[str], None] = field(default=lambda s: print(s, flush=True))


@dataclass
class Entry:
    rel: str           # stage-relative posix path
    src: Optional[Path]
    group: str         # runner, app, bot, glue, vendor, weights, vcrt, selftest, generated


def copy_plan(src: Sources) -> List[Entry]:
    """The generated copy list (code, glue and vendor); weights, VC++ DLLs and generated files are added by run()."""
    wt = Path(src.worktree)
    plan: List[Entry] = []
    runner = wt / RUNNER_DIR
    if not runner.is_dir():
        raise StageError("src\\verify\\runner is missing in the worktree")
    for p in sorted(runner.glob("*.py")):
        if p.name not in RUNNER_NEVER:
            plan.append(Entry(f"{RUNNER_DIR}/{p.name}", p, "runner"))
    app = wt / APP_DIR
    if not (app / "__init__.py").is_file() or not (app / "entry.py").is_file():
        raise StageError("src\\verify\\runner\\app\\__init__.py or entry.py is missing")
    for p in sorted(app.glob("*.py")):
        plan.append(Entry(f"{APP_DIR}/{p.name}", p, "app"))
    for rel in BOT_FILES:
        p = wt / rel
        if not p.is_file():
            raise StageError(f"bot file missing: {rel}")
        plan.append(Entry(rel, p, "bot"))
    for name in GLUE_PY:
        p = Path(src.glue_dir) / name
        if not p.is_file():
            raise StageError(f"glue file missing: {name}")
        plan.append(Entry(name, p, "glue"))
    gd = Path(src.glue_dir) / GLUE_DATA
    if not gd.is_file():
        raise StageError(f"glue file missing: {GLUE_DATA}")
    plan.append(Entry(f"engine/{GLUE_DATA}", gd, "glue"))
    for pkg in VENDOR_PACKAGES:
        root = Path(src.vendor_dir) / pkg
        if not root.is_dir():
            raise StageError(f"vendored package missing: {pkg}")
        for dp, dns, fns in os.walk(root):
            dns[:] = sorted(d for d in dns if d != "__pycache__")
            for fn in sorted(fns):
                if fn.endswith((".pyc", ".pyo")):
                    continue
                p = Path(dp) / fn
                plan.append(Entry(f"vendor/{pkg}/{p.relative_to(root).as_posix()}", p, "vendor"))
    bad = [e.rel for e in plan if (e.rel.startswith("src/") and re.search(r"/(tests|selftest|__pycache__)/", e.rel))
           or e.rel.endswith("/devplaces.py") or e.rel.lower().endswith(".onnx")]
    if bad:
        raise StageError(f"the copy list holds files that are never staged: {bad[:3]}")
    return plan


def check_vendor(src: Sources) -> Dict[str, Dict[str, Any]]:
    """Each vendored package's tree hash equals VENDOR.json (or the pin of a package VENDOR.json does not list)."""
    vj = Path(src.vendor_dir) / "VENDOR.json"
    listed = json.loads(vj.read_text(encoding="utf-8")).get("packages", {}) if vj.is_file() else {}
    out = {}
    for pkg in VENDOR_PACKAGES:
        files, tree = tree_sha256(Path(src.vendor_dir) / pkg)
        want = listed.get(pkg) or src.vendor_pins.get(pkg)
        if not want:
            raise StageError(f"vendored {pkg}: no tree hash to check against")
        if tree != want.get("tree_sha256") or files != want.get("files", files):
            raise StageError(f"vendored {pkg}: the tree hash differs from VENDOR.json / the pin")
        out[pkg] = {"version": listed.get(pkg, {}).get("version"), "files": files, "tree_sha256": tree,
                    "checked_against": "VENDOR.json" if pkg in listed else "pin"}
    return out


HDC_ENTRY = "import sys\nfrom src.verify.runner.app.entry import main\nsys.exit(main())\n"


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def _copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)


def write_stage_list(path: Path, stage_dir: Path, groups: Mapping[str, str], build_id: str) -> int:
    """reports\\STAGE_LIST.txt: one tab-separated line per staged file: stage path, module (or -), group, bytes,
    sha256. `read_stage_list` / `src_modules` read it back."""
    rows = []
    for rel, p in manifest.iter_files(stage_dir):
        rows.append("\t".join((rel, module_name(rel) or "-", groups.get(rel, "generated"),
                               str(p.stat().st_size), manifest.sha256_file(p))))
    head = [f"# STAGE_LIST.txt - the staged files of build {build_id} (APP_SPEC r2 R2.4.4), {len(rows)} files",
            "# columns (tab-separated): stage path, module (- for a non-Python file), group, bytes, sha256"]
    _write_text(path, "\n".join(head + rows) + "\n")
    return len(rows)


def read_stage_list(path: Path) -> List[Dict[str, Any]]:
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        rel, mod, group, size, sha = line.split("\t")
        out.append({"path": rel, "module": None if mod == "-" else mod, "group": group, "bytes": int(size),
                    "sha256": sha})
    return out


def src_modules(path: Path) -> List[str]:
    """The src.* module names of a STAGE_LIST.txt (what the bundle's PYZ must hold, r2 8 b)."""
    return sorted(e["module"] for e in read_stage_list(path)
                  if e["module"] and (e["module"] == "src" or e["module"].startswith("src.")))


def run(src: Sources, stage_dir: Path, reports_dir: Path, build_id: str,
        tools: Optional[Tools] = None) -> Dict[str, Any]:
    """Makes the stage; returns the BUILD.json dict. Raises StageError at the first broken rule."""
    tools = tools or Tools()
    log = tools.log
    build_version.parse_build_id(build_id)
    stage_dir, reports_dir = Path(stage_dir), Path(reports_dir)
    if stage_dir.exists() and any(stage_dir.iterdir()):
        raise StageError("the stage folder exists and is not empty (a build id is never staged twice)")
    if not src.site_dirs:
        raise StageError("no build env site-packages given (hdc_build.pth)")
    stage_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)
    groups: Dict[str, str] = {}

    log("stage: bot modules against " + BOT_COMMIT)
    git_bot_check(Path(src.worktree), tools.git_run)

    log("stage: copy list, vendor trees")
    plan = copy_plan(src)
    vendor = check_vendor(src)
    scrubbed = docs = 0
    for e in plan:
        dst = stage_dir / e.rel
        groups[e.rel] = e.group
        if e.rel.endswith(".py") and e.group in ("runner", "app", "bot", "glue"):
            original = e.src.read_bytes().decode("utf-8")
            staged, n = scrub_file(e.rel, original, src.swaps)
            _write_text(dst, staged)
            scrubbed += 1
            docs += n
        else:
            _copy(e.src, dst)
    planned = {e.rel for e in plan}
    unused = [s.name for s in src.swaps if s.path not in planned]
    if unused:
        raise StageError(f"swaps for files that are not staged: {unused}")
    log(f"stage: {len(plan)} files copied; {scrubbed} modules scrubbed ({docs} docstrings), "
        f"{len(src.swaps)} constants swapped, AST checks passed")

    log("stage: EasyOCR weights")
    config = find_in_sites("easyocr/config.py", src.site_dirs)
    md5s = easyocr_md5s(config)
    models: Dict[str, Dict[str, Any]] = {}
    for fname in WEIGHTS:
        w = Path(src.weights_dir) / fname
        if not w.is_file():
            raise StageError(f"weight file missing: {fname}")
        dst = stage_dir / "engine" / "easyocr" / "model" / fname
        _copy(w, dst)
        h = hashes_of(dst)
        if h["md5"] != md5s[fname]:
            raise StageError(f"{fname}: MD5 differs from easyocr's md5sum (not the weights easyocr checks)")
        models[fname] = h
        groups[f"engine/easyocr/model/{fname}"] = "weights"
    keep = stage_dir / "engine" / "easyocr" / "user_network" / ".keep"
    _write_text(keep, "")
    groups["engine/easyocr/user_network/.keep"] = "weights"

    log("stage: VC++ runtime DLLs")
    vcrt: Dict[str, str] = {}
    for name, minimum in VCRT_MIN.items():
        p = Path(src.system32) / name
        if not p.is_file():
            raise StageError(f"{name} is missing in System32")
        v = tools.file_version(p)
        vt = version_tuple(v)
        if vt[:2] < minimum or (name == "msvcr120.dll" and vt[0] != 12):
            raise StageError(f"{name} {v} is older than {minimum[0]}.{minimum[1]}")
        _copy(p, stage_dir / "vcrt" / name)
        vcrt[name] = v
        groups[f"vcrt/{name}"] = "vcrt"

    log("stage: self-test files (make_selftest_pages.py)")
    tools.make_selftest(Path(src.python_exe), Path(src.selftest_maker), stage_dir / "selftest")
    for name in SELFTEST_FILES:
        if not (stage_dir / "selftest" / name).is_file():
            raise StageError(f"make_selftest_pages.py did not make selftest\\{name}")
    for rel, _ in manifest.iter_files(stage_dir / "selftest"):
        groups[f"selftest/{rel}"] = "selftest"

    log("stage: versions of the build env")
    engine = {k: dist_version(k, src.site_dirs) for k in EXPECTED_ENGINE}
    for k, want in EXPECTED_ENGINE.items():
        if engine[k] != want:
            raise StageError(f"the build env has {k} {engine[k]}, the tested engine is {want}")
    packages = {k: dist_version(d, src.site_dirs) for k, d in PACKAGE_DISTS.items()}
    tv = _assigned_literals(find_in_sites("torch/version.py", src.site_dirs), ("__version__", "cuda"))
    if tv["cuda"] != CUDA or tv["__version__"] != engine["torch"]:
        raise StageError(f"torch\\version.py says CUDA {tv['cuda']}, torch {tv['__version__']}")
    cudnn_fv = tools.file_version(find_in_sites("torch/lib/cudnn64_9.dll", src.site_dirs))
    probe = tools.probe(Path(src.python_exe))
    if probe.get("torch") != engine["torch"]:
        raise StageError("the build env's torch differs from its dist-info")
    flags = probe.get("arch_flags")
    arch = flags.split() if isinstance(flags, str) else [str(x) for x in (flags or [])]
    if not arch or not all(re.fullmatch(r"(sm|compute)_\d+[a-z]?", a) for a in arch):
        raise StageError("torch's arch flags are missing or unreadable")

    mv = model_versions(stage_dir / RUNNER_DIR / "model.py")
    build: Dict[str, Any] = {
        "schema": BUILD_SCHEMA,
        "app_version": build_version.APP_VERSION,
        "build_id": build_id,
        "file_version": build_version.file_version(build_id),
        "flavour": build_version.FLAVOUR,
        "flavour_label": build_version.FLAVOUR_LABEL,
        "built": tools.now(),
        **mv,
        "spec": SPEC,
        "baseline": BASELINE,
        "python": probe.get("python"),
        "torch": engine["torch"],
        "torchvision": engine["torchvision"],
        "easyocr": engine["easyocr"],
        "cuda": CUDA,
        "cudnn": ".".join(cudnn_fv.split(".")[:3]),
        "cudnn_file_version": cudnn_fv,
        "arch_flags": arch,
        **CARD_FACTS,
        "numpy_cpu_baseline": list(probe.get("numpy_cpu_baseline") or []),
        "packages": packages,
        "models": models,
        "vcrt": vcrt,
        "vendor": vendor,
        "bot_commit": BOT_COMMIT,
    }

    log("stage: BUILD.json, version_info.txt, icon.ico, hdc_entry.py")
    _write_text(stage_dir / "BUILD.json", json.dumps(build, indent=1, ensure_ascii=True) + "\n")
    _write_text(stage_dir / "version_info.txt",
                build_version.version_info_text(build_id, mv["runner_version"], mv["base_rules_version"],
                                                mv["runner_rules_version"]))
    make_icon.write_icon(stage_dir / "icon.ico")
    _write_text(stage_dir / "hdc_entry.py", HDC_ENTRY)

    log("stage: fast scan")
    structural = [rel for rel, _ in manifest.iter_files(stage_dir)
                  if rel.lower().endswith(".onnx") or "onnxruntime" in rel.lower() or "directml" in rel.lower()
                  or rel.endswith("/devplaces.py") or re.match(r"src/.*/(tests|selftest)/", rel)]
    if structural:
        raise StageError(f"the stage holds files that are never bundled: {structural[:3]}")
    hits = scan_tree(stage_dir, load_allow(src.allow_file))
    if hits:
        shown = "; ".join(f"{w}: {s}" for w, s in hits[:10])
        raise StageError(f"fast scan: {len(hits)} forbidden string hit(s): {shown}")

    n = write_stage_list(reports_dir / "STAGE_LIST.txt", stage_dir, groups, build_id)
    summary = {"build_id": build_id, "files": n, "scrubbed_modules": scrubbed, "docstrings_blanked": docs,
               "swaps": len(src.swaps), "scan_hits": 0, "ok": True}
    _write_text(reports_dir / "stage.json", json.dumps(summary, indent=1) + "\n")
    log(f"stage: done, {n} files, scan clean")
    return build
