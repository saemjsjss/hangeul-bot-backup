"""8 h DLL closure of the frozen app folder (APP_SPEC rev 1 §8 h, amended by rev 2 R2.6 and R2.4.7).

    <build python> -I -B packaging\\tests\\dll_check.py "<dist>\\Hangeul Document Checker"
                    [--baseline packaging\\dll_baseline.txt] [--torch-lib <build env>\\torch\\lib] [--json out.json]
    or, from build.py:  dll_check.main([...])  /  dll_check.check(app_dir, baseline, torch_lib) -> Report

For every ``.dll``, ``.pyd`` and ``.exe`` of the folder it reads the import tables with pefile (``fast_load`` and
then only the import, delay-import and, for the VC++ runtimes, resource directories; nothing else is parsed).
Pass:
  - every imported DLL is in the bundle (any folder of it) or on ``dll_baseline.txt`` (Task F's file: lines
    ``name | kind | reason`` with kind ``system``, ``driver`` or ``bundle-only``; ``*`` and ``?`` allowed, e.g.
    ``api-ms-win-*``). A ``bundle-only`` line never satisfies an import: it documents a DLL that must be bundled;
  - ``nvcuda.dll`` and ``nvml.dll`` count as "driver-provided, loaded at run time" and must NOT be in the bundle;
  - delay-loaded imports are listed separately and may be missing only when on the "never called" list
    (``directml.dll``, or a baseline line whose reason starts with "never called" / "delay-only");
  - no ``.dll`` base name twice (case-insensitive; a 774 MB DLL collected twice would double the size);
  - the set of ``torch\\lib\\*.dll`` base names equals the build environment's ``torch\\lib``;
  - the VC++ rule: ``vcruntime140*``, ``msvcp140*`` and ``msvcr120`` are never satisfied by the baseline; they are in
    the bundle at file version >= 14.40 (12.0 for msvcr120); ``_internal`` holds vcruntime140, vcruntime140_1,
    msvcp140, msvcp140_1 and ``_internal\\pyzbar\\msvcr120.dll``. Hashed wheel copies (``msvcp140-<hash>.dll``) only
    have to be present.
Advisory: a DLL found only in a folder that is neither the importer's, ``_internal`` nor a known DLL folder
(``torch\\lib``, ``*.libs``); ``.pyd`` base names twice. DLL names are not personal data. Exit 0 / 1 / 2.
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse  # noqa: E402
import fnmatch  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple  # noqa: E402

PKG = Path(__file__).resolve().parent.parent
TORCH_LIB = Path(r"C:\Hangeul\DOCCHECK\lab\onnx\.venv\Lib\site-packages\torch\lib")
DRIVER_PROVIDED = ("nvcuda.dll", "nvml.dll")
NEVER_CALLED = ("directml.dll",)
VC_RE = re.compile(r"^(vcruntime140(_\d+)?|msvcp140(_[a-z0-9_]+)?|msvcr120)\.dll$", re.I)
VC_ANY_RE = re.compile(r"^(vcruntime140|msvcp140|msvcr120)", re.I)
VC_MIN = {"msvcr120.dll": (12, 0)}
VC_MIN_DEFAULT = (14, 40)
VC_REQUIRED = ("_internal/vcruntime140.dll", "_internal/vcruntime140_1.dll", "_internal/msvcp140.dll",
               "_internal/msvcp140_1.dll", "_internal/pyzbar/msvcr120.dll")
KNOWN_DLL_DIRS = ("_internal/torch/lib",)
PE_SUFFIXES = (".dll", ".pyd", ".exe")


# ---------------------------------------------------------------------------------------------- reading PE files

class PEReader:
    """pefile with fast_load; tests pass a fake with the same two methods."""

    def imports(self, path: Path) -> Tuple[List[str], List[str]]:
        """(imported DLL names, delay-loaded DLL names), lower case."""
        import pefile
        pe = pefile.PE(str(path), fast_load=True)
        try:
            d = pefile.DIRECTORY_ENTRY
            pe.parse_data_directories(directories=[d["IMAGE_DIRECTORY_ENTRY_IMPORT"],
                                                   d["IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT"]],
                                      import_dllnames_only=True)
            imp = [e.dll.decode("ascii", "replace").lower() for e in getattr(pe, "DIRECTORY_ENTRY_IMPORT", [])]
            dly = [e.dll.decode("ascii", "replace").lower() for e in getattr(pe, "DIRECTORY_ENTRY_DELAY_IMPORT", [])]
            return imp, dly
        finally:
            pe.close()

    def version(self, path: Path) -> Optional[Tuple[int, int, int, int]]:
        """The file version from the version resource; None when there is none."""
        import pefile
        pe = pefile.PE(str(path), fast_load=True)
        try:
            pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"]])
            infos = getattr(pe, "VS_FIXEDFILEINFO", None)
            if not infos:
                return None
            f = infos[0]
            return (f.FileVersionMS >> 16, f.FileVersionMS & 0xFFFF, f.FileVersionLS >> 16, f.FileVersionLS & 0xFFFF)
        finally:
            pe.close()


# ---------------------------------------------------------------------------------------------- the baseline

KINDS = ("system", "driver", "bundle-only", "never-called", "delay-only")


@dataclass
class BaseLine:
    pattern: str
    reason: str
    number: int
    delay_only: bool = False
    driver: bool = False
    bundle_only: bool = False


def load_baseline(path: Optional[Path]) -> Tuple[List[BaseLine], List[str]]:
    """Lines ``name | kind | reason`` (kind: system, driver, bundle-only, never-called / delay-only), or
    ``name | reason`` / ``name  reason``; '#' lines are comments. never-called / delay-only (or a reason starting
    with "never called") allows a delay-loaded import only; driver (or "driver-provided") marks a driver DLL;
    bundle-only never satisfies an import."""
    lines: List[BaseLine] = []
    errors: List[str] = []
    if path is None or not Path(path).is_file():
        errors.append("dll_baseline.txt not found")
        return lines, errors
    for n, raw in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        kind = ""
        if "|" in s:
            parts = [x.strip() for x in s.split("|")]
            if len(parts) >= 3 and parts[1].lower() in KINDS:
                name, kind, reason = parts[0], parts[1].lower(), "|".join(parts[2:]).strip()
            else:
                name, reason = parts[0], "|".join(parts[1:]).strip()
        else:
            parts = s.split(None, 1)
            name, reason = parts[0], (parts[1] if len(parts) > 1 else "")
            reason = reason.lstrip("#").strip()
        name = name.lower()
        if not reason:
            errors.append(f"dll_baseline.txt line {n}: no reason")
        low = reason.lower()
        lines.append(BaseLine(name, reason, n,
                              delay_only=kind in ("never-called", "delay-only")
                              or low.startswith(("never called", "delay-only")),
                              driver=kind == "driver" or "driver-provided" in low,
                              bundle_only=kind == "bundle-only"))
    return lines, errors


def _match(name: str, lines: Sequence[BaseLine], delay: bool) -> Optional[BaseLine]:
    for b in lines:
        if b.bundle_only:
            continue
        if (not b.delay_only or delay) and fnmatch.fnmatchcase(name, b.pattern):
            return b
    return None


# ---------------------------------------------------------------------------------------------- the check

@dataclass
class Report:
    binaries: int = 0
    imports: int = 0
    failures: List[str] = field(default_factory=list)
    advisory: List[str] = field(default_factory=list)
    missing: Dict[str, List[str]] = field(default_factory=dict)          # dll -> importers (relative)
    delay_missing: Dict[str, List[str]] = field(default_factory=dict)
    delay_allowed: Dict[str, List[str]] = field(default_factory=dict)
    driver: Dict[str, int] = field(default_factory=dict)
    baseline_used: Dict[str, int] = field(default_factory=dict)
    vc_versions: Dict[str, str] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return not self.failures

    def to_json(self) -> Dict[str, Any]:
        return {"passed": self.passed, "binaries": self.binaries, "imports": self.imports,
                "failures": self.failures, "advisory": self.advisory, "missing": self.missing,
                "delay_missing": self.delay_missing, "delay_allowed": self.delay_allowed, "driver": self.driver,
                "baseline_used": self.baseline_used, "vc_versions": self.vc_versions}


def _fmt_v(v: Optional[Tuple[int, ...]]) -> str:
    return "none" if v is None else ".".join(map(str, v))


def check(app_dir: Path, baseline: Optional[Path] = None, torch_lib: Optional[Path] = TORCH_LIB,
          reader: Optional[PEReader] = None, progress: Optional[Callable[[str], None]] = None) -> Report:
    root = Path(app_dir)
    rep = Report()
    if not root.is_dir():
        rep.failures.append("the app folder was not found")
        return rep
    reader = reader or PEReader()
    base, errors = load_baseline(baseline)
    rep.failures.extend(errors)
    by_name: Dict[str, List[str]] = {}
    pe_files: List[Tuple[str, Path]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for fn in sorted(filenames):
            p = Path(dirpath) / fn
            rel = p.relative_to(root).as_posix()
            low = fn.lower()
            if low.endswith(".dll"):
                by_name.setdefault(low, []).append(rel)
            if low.endswith(PE_SUFFIXES):
                pe_files.append((rel, p))
    for name, where in sorted(by_name.items()):
        if len(where) > 1:
            rep.failures.append(f"DLL base name twice: {name} ({len(where)} copies: {', '.join(where)})")
    pyd: Dict[str, int] = {}
    for rel, _ in pe_files:
        if rel.lower().endswith(".pyd"):
            k = rel.rsplit("/", 1)[-1].lower()
            pyd[k] = pyd.get(k, 0) + 1
    for k, n in sorted(pyd.items()):
        if n > 1:
            rep.advisory.append(f".pyd base name {n} times: {k}")
    drivers = set(DRIVER_PROVIDED) | {b.pattern for b in base if b.driver and not set(b.pattern) & set("*?[")}
    for d in sorted(drivers):
        if d in by_name:
            rep.failures.append(f"{d} is bundled; it is driver-provided and must come from Windows")
    for b in base:
        if b.bundle_only:
            continue
        if VC_ANY_RE.match(b.pattern) or any(fnmatch.fnmatchcase(v, b.pattern) for v in
                                             ("vcruntime140.dll", "msvcp140.dll", "msvcr120.dll")):
            rep.failures.append(f"dll_baseline.txt line {b.number} covers a VC++ runtime ({b.pattern}); "
                                "those must be bundled")
    for req in VC_REQUIRED:
        if not (root / req).is_file():
            rep.failures.append(f"VC++ runtime missing: {req}")
    vc_seen: Set[str] = set()
    for rel, p in pe_files:
        rep.binaries += 1
        try:
            imp, dly = reader.imports(p)
        except Exception as exc:
            rep.failures.append(f"cannot read the import table of {rel}: {type(exc).__name__}")
            continue
        here = rel.rsplit("/", 1)[0] if "/" in rel else ""
        for name, delay in [(n, False) for n in imp] + [(n, True) for n in dly]:
            rep.imports += 1
            name = name.lower()
            if VC_RE.match(name):
                vc_seen.add(name)
            if name in by_name:
                where = by_name[name]
                dirs = {w.rsplit("/", 1)[0] if "/" in w else "" for w in where}
                if not (here in dirs or "_internal" in dirs or dirs & set(KNOWN_DLL_DIRS)
                        or any(d.endswith(".libs") for d in dirs)):
                    rep.advisory.append(f"{rel} imports {name}, found only in {', '.join(sorted(dirs))}")
                continue
            if name in drivers:
                rep.driver[name] = rep.driver.get(name, 0) + 1
                continue
            if VC_ANY_RE.match(name):
                (rep.delay_missing if delay else rep.missing).setdefault(name, []).append(rel)
                continue
            b = _match(name, base, delay)
            if b is not None:
                rep.baseline_used[b.pattern] = rep.baseline_used.get(b.pattern, 0) + 1
                continue
            if delay and name in NEVER_CALLED:
                rep.delay_allowed.setdefault(name, []).append(rel)
                continue
            (rep.delay_missing if delay else rep.missing).setdefault(name, []).append(rel)
        if progress and rep.binaries % 500 == 0:
            progress(f"{rep.binaries} binaries read")
    for name, who in sorted(rep.missing.items()):
        rep.failures.append(f"import not satisfied: {name} (by {len(who)} file(s), e.g. {who[0]})")
    for name, who in sorted(rep.delay_missing.items()):
        rep.failures.append(f"delay-load import not satisfied and not 'never called': {name} "
                            f"(by {len(who)} file(s), e.g. {who[0]})")
    _vc_versions(root, by_name, vc_seen, reader, rep)
    _torch_lib(root, torch_lib, rep)
    return rep


def _vc_versions(root: Path, by_name: Dict[str, List[str]], seen: Set[str], reader: PEReader, rep: Report) -> None:
    names = set(seen) | {r.rsplit("/", 1)[-1] for r in VC_REQUIRED}
    for name in sorted(names):
        for rel in by_name.get(name, []):
            try:
                v = reader.version(root / rel)
            except Exception as exc:
                rep.failures.append(f"cannot read the version of {rel}: {type(exc).__name__}")
                continue
            rep.vc_versions[rel] = _fmt_v(v)
            need = VC_MIN.get(name, VC_MIN_DEFAULT)
            if v is None or tuple(v[:2]) < need:
                rep.failures.append(f"{rel}: version {_fmt_v(v)} < {'.'.join(map(str, need))}")
    for name, where in by_name.items():                         # hashed wheel copies: present, version noted
        if VC_ANY_RE.match(name) and not VC_RE.match(name):
            for rel in where:
                try:
                    rep.vc_versions[rel] = _fmt_v(reader.version(root / rel))
                except Exception:
                    rep.vc_versions[rel] = "unreadable"


def _torch_lib(root: Path, torch_lib: Optional[Path], rep: Report) -> None:
    if torch_lib is None:
        return
    if not Path(torch_lib).is_dir():
        rep.failures.append("the build environment's torch\\lib was not found")
        return
    want = {p.name.lower() for p in Path(torch_lib).iterdir() if p.name.lower().endswith(".dll")}
    have_dir = root / "_internal" / "torch" / "lib"
    have = {p.name.lower() for p in have_dir.iterdir() if p.name.lower().endswith(".dll")} if have_dir.is_dir() else set()
    for n in sorted(want - have):
        rep.failures.append(f"torch\\lib DLL not bundled: {n}")
    for n in sorted(have - want):
        rep.failures.append(f"torch\\lib DLL not in the build environment: {n}")


def print_report(rep: Report, out: Callable[[str], None] = print) -> None:
    out(f"8 h DLL closure: {rep.binaries} binaries, {rep.imports} imports | missing {len(rep.missing)} | "
        f"delay-load missing {len(rep.delay_missing)} | never-called {sorted(rep.delay_allowed)} | "
        f"driver-provided {rep.driver} | baseline names used {len(rep.baseline_used)}")
    for rel, v in sorted(rep.vc_versions.items()):
        out(f"  VC++ {rel} {v}")
    for f in rep.failures:
        out(f"  FAIL {f}")
    for a in rep.advisory:
        out(f"  advisory {a}")
    out(f"8 h {'PASS' if rep.passed else 'FAIL'}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="8 h DLL closure of the frozen app folder")
    ap.add_argument("app_dir")
    ap.add_argument("--baseline", default=str(PKG / "dll_baseline.txt"))
    ap.add_argument("--torch-lib", default=str(TORCH_LIB))
    ap.add_argument("--json", default="")
    a = ap.parse_args(argv)
    try:
        rep = check(Path(a.app_dir), Path(a.baseline), Path(a.torch_lib) if a.torch_lib else None)
    except ImportError as exc:
        print(f"8 h: could not run (pefile not importable: run in the build venv): {exc}")
        return 2
    except Exception as exc:
        print(f"8 h: could not run: {type(exc).__name__}: {exc}")
        return 2
    print_report(rep)
    if a.json:
        Path(a.json).parent.mkdir(parents=True, exist_ok=True)
        Path(a.json).write_text(json.dumps(rep.to_json(), indent=1, ensure_ascii=True), encoding="utf-8")
    return 0 if rep.passed else 1


if __name__ == "__main__":
    sys.exit(main())
